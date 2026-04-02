"""Advanced Order Flow Engine — imbalance, footprint signals, DOM, institutional patterns.

Extends the base orderflow module with:
  - Diagonal imbalance detection (ATAS-style bid@level vs ask@level+1)
  - Stacked imbalance detection (3+ consecutive imbalance levels)
  - Finished/unfinished auction detection
  - Exhaustion detection (volume spike, no price move)
  - Absorption signals from footprint data
  - Initiative vs responsive activity classification
  - POC migration tracking
  - Single print / excess detection
  - Multi-timeframe order flow aggregation
  - Large trade speed analysis
  - Volume delta rate of change
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from ..event_bus import EventBus, Event, EventType
from ..orderflow import VolumeProfile, FootprintBar, FootprintChart, PriceLevel, Tick

log = logging.getLogger(__name__)


# ─── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class Imbalance:
    """A detected bid/ask imbalance at a price level."""
    price: float
    imbalance_type: str  # "buy_imbalance" or "sell_imbalance"
    ratio: float         # imbalance ratio (e.g. 3.5x)
    bid_vol: int
    ask_vol: int
    is_diagonal: bool = False  # True if comparing bid@level vs ask@level+1


@dataclass
class StackedImbalance:
    """3+ consecutive imbalance levels in same direction."""
    start_price: float
    end_price: float
    direction: str  # "BULLISH" (buy imbalances stacked) or "BEARISH"
    count: int      # number of consecutive levels
    avg_ratio: float
    imbalances: list[Imbalance] = field(default_factory=list)


@dataclass
class FootprintSignal:
    """Auto-generated signal from footprint analysis."""
    timestamp: float
    price: float
    signal_type: str  # absorption, exhaustion, unfinished_auction, stacked_imbalance, etc.
    direction: str    # BULLISH or BEARISH
    confidence: float
    description: str
    components: list[str] = field(default_factory=list)  # what triggered it


@dataclass
class DOMLevel:
    """A single Level 2 order book level."""
    price: float
    bid_size: int = 0
    ask_size: int = 0
    bid_count: int = 0
    ask_count: int = 0


@dataclass
class DOMSnapshot:
    """Level 2 / Depth of Market snapshot."""
    timestamp: float
    levels: list[DOMLevel] = field(default_factory=list)
    best_bid: float = 0.0
    best_ask: float = 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def total_bid_depth(self) -> int:
        return sum(lv.bid_size for lv in self.levels)

    @property
    def total_ask_depth(self) -> int:
        return sum(lv.ask_size for lv in self.levels)

    @property
    def book_imbalance(self) -> float:
        """Ratio of bid depth to ask depth. >1 = more bids (bullish), <1 = more asks."""
        ask = self.total_ask_depth
        if ask == 0:
            return 10.0
        return self.total_bid_depth / ask


# ─── Advanced Order Flow Engine ───────────────────────────────────────────────


class AdvancedOrderFlowEngine:
    """Advanced order flow analysis with imbalance, footprint signals, and DOM tracking."""

    def __init__(self, bus: EventBus, tick_size: float = 0.25,
                 imbalance_ratio: float = 3.0,
                 stacked_min_count: int = 3,
                 exhaustion_vol_multiplier: float = 2.5,
                 exhaustion_price_ticks: int = 2):
        self.bus = bus
        self.tick_size = tick_size
        self.imbalance_ratio = imbalance_ratio
        self.stacked_min_count = stacked_min_count
        self.exhaustion_vol_multiplier = exhaustion_vol_multiplier
        self.exhaustion_price_ticks = exhaustion_price_ticks

        # Multi-timeframe footprints
        self.footprint_1m = FootprintChart(bar_duration_sec=60, tick_size=tick_size)
        self.footprint_5m = FootprintChart(bar_duration_sec=300, tick_size=tick_size)
        self.footprint_15m = FootprintChart(bar_duration_sec=900, tick_size=tick_size)

        # POC migration tracking
        self._poc_history: deque[tuple[float, float]] = deque(maxlen=500)  # (timestamp, poc_price)

        # Trade speed tracking
        self._trade_timestamps: deque[float] = deque(maxlen=10000)
        self._trade_speeds: deque[float] = deque(maxlen=100)  # trades per second snapshots

        # Volume delta rate of change
        self._delta_history: deque[tuple[float, int]] = deque(maxlen=500)  # (timestamp, cum_delta)

        # DOM history for pull/stack detection
        self._dom_history: deque[DOMSnapshot] = deque(maxlen=100)

        # Aggressive vs passive tracking
        self._aggressive_volume: int = 0  # market orders
        self._passive_volume: int = 0     # limit order fills (estimated)

        # Session cumulative delta
        self.session_cumulative_delta: int = 0
        self._session_start_delta: int = 0

        log.info("Advanced Order Flow Engine initialized")

    def _round_price(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 2)

    def process_tick(self, tick: Tick):
        """Process tick through all timeframe footprints and tracking."""
        # Multi-timeframe
        self.footprint_1m.add_tick(tick)
        self.footprint_5m.add_tick(tick)
        self.footprint_15m.add_tick(tick)

        # Track trade speed
        self._trade_timestamps.append(tick.timestamp)

        # Track cumulative delta
        delta_change = tick.size if tick.is_buy else -tick.size
        self.session_cumulative_delta += delta_change

        # Aggressive volume tracking (market orders hitting the tape)
        self._aggressive_volume += tick.size

        # Periodically snapshot delta for rate of change
        if len(self._trade_timestamps) % 50 == 0:
            self._delta_history.append((tick.timestamp, self.session_cumulative_delta))

        # Track POC migration on bar completion
        bar_1m = self.footprint_1m.current_bar
        if bar_1m and bar_1m.is_complete:
            session_prof = self.footprint_1m.session_profile
            poc = session_prof.poc
            if poc is not None:
                self._poc_history.append((tick.timestamp, poc))

    def process_dom_update(self, dom: DOMSnapshot):
        """Process a Level 2 / DOM update."""
        self._dom_history.append(dom)

        # Passive volume estimation from DOM changes
        if len(self._dom_history) >= 2:
            prev = self._dom_history[-2]
            # Volume that disappeared from the book = filled limit orders (passive)
            for plv in prev.levels:
                for clv in dom.levels:
                    if abs(plv.price - clv.price) < self.tick_size * 0.5:
                        bid_fill = max(0, plv.bid_size - clv.bid_size)
                        ask_fill = max(0, plv.ask_size - clv.ask_size)
                        self._passive_volume += bid_fill + ask_fill

        self.bus.publish(Event(
            type=EventType.DOM_UPDATE,
            source="advanced_orderflow",
            data={
                "best_bid": dom.best_bid,
                "best_ask": dom.best_ask,
                "spread": dom.spread,
                "book_imbalance": dom.book_imbalance,
                "bid_depth": dom.total_bid_depth,
                "ask_depth": dom.total_ask_depth,
            },
        ))

    # ─── Imbalance Detection ─────────────────────────────────────────────

    def detect_imbalances(self, profile: VolumeProfile) -> list[Imbalance]:
        """Detect bid/ask imbalances in a volume profile."""
        imbalances = []
        sorted_levels = profile.sorted_levels()

        for lv in sorted_levels:
            if lv.total_volume < 10:
                continue

            # Standard imbalance at level
            if lv.ask_volume > 0 and lv.bid_volume > 0:
                buy_ratio = lv.ask_volume / lv.bid_volume
                sell_ratio = lv.bid_volume / lv.ask_volume

                if buy_ratio >= self.imbalance_ratio:
                    imbalances.append(Imbalance(
                        price=lv.price, imbalance_type="buy_imbalance",
                        ratio=buy_ratio, bid_vol=lv.bid_volume,
                        ask_vol=lv.ask_volume,
                    ))
                elif sell_ratio >= self.imbalance_ratio:
                    imbalances.append(Imbalance(
                        price=lv.price, imbalance_type="sell_imbalance",
                        ratio=sell_ratio, bid_vol=lv.bid_volume,
                        ask_vol=lv.ask_volume,
                    ))

        return imbalances

    def detect_diagonal_imbalances(self, profile: VolumeProfile) -> list[Imbalance]:
        """ATAS-style diagonal imbalance: compare bid@level vs ask@level+1."""
        imbalances = []
        sorted_levels = profile.sorted_levels()

        for i in range(len(sorted_levels) - 1):
            lower = sorted_levels[i]
            upper = sorted_levels[i + 1]

            # Check if prices are adjacent
            if abs(upper.price - lower.price - self.tick_size) > 0.01:
                continue

            # Buy imbalance: ask volume at lower level vs bid volume at upper level
            if lower.bid_volume > 0 and upper.ask_volume > 0:
                ratio = upper.ask_volume / lower.bid_volume
                if ratio >= self.imbalance_ratio:
                    imbalances.append(Imbalance(
                        price=lower.price, imbalance_type="buy_imbalance",
                        ratio=ratio, bid_vol=lower.bid_volume,
                        ask_vol=upper.ask_volume, is_diagonal=True,
                    ))

            if upper.ask_volume > 0 and lower.bid_volume > 0:
                ratio = lower.bid_volume / upper.ask_volume
                if ratio >= self.imbalance_ratio:
                    imbalances.append(Imbalance(
                        price=upper.price, imbalance_type="sell_imbalance",
                        ratio=ratio, bid_vol=lower.bid_volume,
                        ask_vol=upper.ask_volume, is_diagonal=True,
                    ))

        return imbalances

    def detect_stacked_imbalances(self, profile: VolumeProfile) -> list[StackedImbalance]:
        """Detect 3+ consecutive imbalance levels in same direction."""
        all_imbalances = self.detect_imbalances(profile)
        all_imbalances.sort(key=lambda x: x.price)

        stacked = []
        current_run: list[Imbalance] = []
        current_type = ""

        for imb in all_imbalances:
            if not current_run:
                current_run = [imb]
                current_type = imb.imbalance_type
                continue

            # Check if adjacent and same type
            prev_price = current_run[-1].price
            is_adjacent = abs(imb.price - prev_price - self.tick_size) < 0.01
            is_same_type = imb.imbalance_type == current_type

            if is_adjacent and is_same_type:
                current_run.append(imb)
            else:
                if len(current_run) >= self.stacked_min_count:
                    stacked.append(self._build_stacked(current_run))
                current_run = [imb]
                current_type = imb.imbalance_type

        if len(current_run) >= self.stacked_min_count:
            stacked.append(self._build_stacked(current_run))

        return stacked

    def _build_stacked(self, imbalances: list[Imbalance]) -> StackedImbalance:
        direction = "BULLISH" if imbalances[0].imbalance_type == "buy_imbalance" else "BEARISH"
        avg_ratio = sum(i.ratio for i in imbalances) / len(imbalances)
        return StackedImbalance(
            start_price=imbalances[0].price,
            end_price=imbalances[-1].price,
            direction=direction,
            count=len(imbalances),
            avg_ratio=avg_ratio,
            imbalances=imbalances,
        )

    # ─── Footprint Signal Detection ──────────────────────────────────────

    def detect_unfinished_auctions(self, bar: FootprintBar) -> list[FootprintSignal]:
        """Detect levels where one side has 0 volume (unfinished business)."""
        signals = []
        for lv in bar.profile.levels.values():
            if lv.total_volume < 5:
                continue
            if lv.bid_volume == 0 and lv.ask_volume > 20:
                signals.append(FootprintSignal(
                    timestamp=bar.start_time, price=lv.price,
                    signal_type="unfinished_auction",
                    direction="BEARISH",  # no sellers tested = price likely returns down
                    confidence=0.6,
                    description=f"Unfinished auction at {lv.price:.2f} — 0 bid volume, price likely revisits",
                    components=["unfinished_auction"],
                ))
            elif lv.ask_volume == 0 and lv.bid_volume > 20:
                signals.append(FootprintSignal(
                    timestamp=bar.start_time, price=lv.price,
                    signal_type="unfinished_auction",
                    direction="BULLISH",
                    confidence=0.6,
                    description=f"Unfinished auction at {lv.price:.2f} — 0 ask volume, price likely revisits",
                    components=["unfinished_auction"],
                ))
        return signals

    def detect_exhaustion(self, bars: list[FootprintBar]) -> list[FootprintSignal]:
        """Detect volume spikes with no price movement (trapped traders)."""
        if len(bars) < 3:
            return []

        signals = []
        avg_vol = sum(b.volume for b in bars[:-1]) / max(len(bars) - 1, 1)

        last_bar = bars[-1]
        if last_bar.volume < avg_vol * self.exhaustion_vol_multiplier:
            return []

        # High volume but small range
        if last_bar.high is not None and last_bar.low is not None:
            bar_range = last_bar.high - last_bar.low
            if bar_range <= self.tick_size * self.exhaustion_price_ticks:
                direction = "BEARISH" if last_bar.delta > 0 else "BULLISH"
                signals.append(FootprintSignal(
                    timestamp=last_bar.start_time,
                    price=last_bar.close or last_bar.open or 0,
                    signal_type="exhaustion",
                    direction=direction,
                    confidence=min(0.85, 0.5 + (last_bar.volume / avg_vol - 1) * 0.1),
                    description=(
                        f"EXHAUSTION at {last_bar.close:.2f} — volume {last_bar.volume} "
                        f"({last_bar.volume / avg_vol:.1f}x avg) but range only "
                        f"{bar_range:.2f}. Trapped {'buyers' if last_bar.delta > 0 else 'sellers'}."
                    ),
                    components=["exhaustion", "volume_spike", "small_range"],
                ))

        return signals

    def detect_absorption_footprint(self, bar: FootprintBar) -> list[FootprintSignal]:
        """Detect absorption from footprint — large volume on one side but price doesn't move."""
        signals = []
        if bar.high is None or bar.low is None or bar.close is None or bar.open is None:
            return []

        bar_range = bar.high - bar.low
        if bar_range > self.tick_size * 4:
            return []  # price moved too much

        # Check each level for heavy one-sided volume
        for lv in bar.profile.levels.values():
            if lv.total_volume < 50:
                continue

            # Heavy selling but price at or above level → buyers absorbing
            if lv.bid_volume > lv.ask_volume * 2 and bar.close >= lv.price:
                signals.append(FootprintSignal(
                    timestamp=bar.start_time, price=lv.price,
                    signal_type="absorption",
                    direction="BULLISH",
                    confidence=min(0.85, 0.5 + lv.bid_volume / (lv.total_volume * 2)),
                    description=(
                        f"ABSORPTION at {lv.price:.2f} — {lv.bid_volume} sell vol absorbed, "
                        f"price holding. Bullish."
                    ),
                    components=["absorption", "heavy_selling", "price_holds"],
                ))

            # Heavy buying but price at or below level → sellers absorbing
            elif lv.ask_volume > lv.bid_volume * 2 and bar.close <= lv.price:
                signals.append(FootprintSignal(
                    timestamp=bar.start_time, price=lv.price,
                    signal_type="absorption",
                    direction="BEARISH",
                    confidence=min(0.85, 0.5 + lv.ask_volume / (lv.total_volume * 2)),
                    description=(
                        f"ABSORPTION at {lv.price:.2f} — {lv.ask_volume} buy vol absorbed, "
                        f"price holding. Bearish."
                    ),
                    components=["absorption", "heavy_buying", "price_holds"],
                ))

        return signals

    def detect_single_prints(self, profile: VolumeProfile) -> list[float]:
        """Detect single print areas — very low volume (price moved through fast).
        These are likely revisit levels."""
        if not profile.levels:
            return []

        max_vol = max(lv.total_volume for lv in profile.levels.values())
        threshold = max_vol * 0.05  # less than 5% of max volume

        return sorted([
            lv.price for lv in profile.levels.values()
            if 0 < lv.total_volume < threshold
        ])

    def detect_excess(self, bar: FootprintBar) -> list[FootprintSignal]:
        """Detect excess — high volume rejection tails at extremes."""
        signals = []
        if bar.high is None or bar.low is None or bar.open is None or bar.close is None:
            return []

        sorted_levels = bar.profile.sorted_levels()
        if len(sorted_levels) < 3:
            return []

        body_high = max(bar.open, bar.close)
        body_low = min(bar.open, bar.close)

        # Check upper tail (excess at top = bearish rejection)
        upper_tail_levels = [lv for lv in sorted_levels if lv.price > body_high]
        upper_vol = sum(lv.total_volume for lv in upper_tail_levels)
        if upper_vol > bar.volume * 0.3 and len(upper_tail_levels) >= 2:
            signals.append(FootprintSignal(
                timestamp=bar.start_time, price=bar.high,
                signal_type="excess",
                direction="BEARISH",
                confidence=min(0.8, 0.5 + upper_vol / bar.volume * 0.3),
                description=f"EXCESS at {bar.high:.2f} — high volume rejection tail (bearish)",
                components=["excess", "rejection_tail"],
            ))

        # Check lower tail (excess at bottom = bullish rejection)
        lower_tail_levels = [lv for lv in sorted_levels if lv.price < body_low]
        lower_vol = sum(lv.total_volume for lv in lower_tail_levels)
        if lower_vol > bar.volume * 0.3 and len(lower_tail_levels) >= 2:
            signals.append(FootprintSignal(
                timestamp=bar.start_time, price=bar.low,
                signal_type="excess",
                direction="BULLISH",
                confidence=min(0.8, 0.5 + lower_vol / bar.volume * 0.3),
                description=f"EXCESS at {bar.low:.2f} — high volume rejection tail (bullish)",
                components=["excess", "rejection_tail"],
            ))

        return signals

    def classify_initiative_responsive(self, bar: FootprintBar,
                                       val: float, vah: float) -> str:
        """Classify bar activity as initiative or responsive.

        Initiative: trading beyond value area (new business, directional)
        Responsive: trading back into value area (returning to value)
        """
        if bar.close is None:
            return "unknown"

        if bar.close > vah:
            return "initiative_buy"
        elif bar.close < val:
            return "initiative_sell"
        elif val <= bar.close <= vah:
            return "responsive"
        return "unknown"

    # ─── POC Migration ───────────────────────────────────────────────────

    def get_poc_migration(self) -> list[tuple[float, float]]:
        """Returns (timestamp, poc_price) history for charting POC drift."""
        return list(self._poc_history)

    def poc_direction(self) -> str:
        """Which direction is the POC migrating?"""
        if len(self._poc_history) < 5:
            return "unknown"
        recent = [p for _, p in list(self._poc_history)[-10:]]
        if recent[-1] > recent[0] + self.tick_size * 2:
            return "rising"
        elif recent[-1] < recent[0] - self.tick_size * 2:
            return "falling"
        return "stable"

    # ─── Trade Speed / Delta ROC ────────────────────────────────────────

    def trade_speed(self, window_sec: float = 5.0) -> float:
        """Trades per second in the last window_sec."""
        if not self._trade_timestamps:
            return 0.0
        now = self._trade_timestamps[-1]
        count = sum(1 for ts in self._trade_timestamps if now - ts <= window_sec)
        return count / window_sec

    def delta_rate_of_change(self) -> float:
        """Rate of change of cumulative delta (acceleration/deceleration)."""
        if len(self._delta_history) < 2:
            return 0.0
        recent = list(self._delta_history)[-10:]
        if len(recent) < 2:
            return 0.0
        dt = recent[-1][0] - recent[0][0]
        if dt <= 0:
            return 0.0
        dd = recent[-1][1] - recent[0][1]
        return dd / dt

    def aggressive_passive_ratio(self) -> float:
        """Ratio of aggressive to passive volume."""
        if self._passive_volume == 0:
            return 10.0
        return self._aggressive_volume / self._passive_volume

    # ─── Pull/Stack Detection (DOM) ──────────────────────────────────────

    def detect_pull_stack(self) -> Optional[dict]:
        """Detect when large orders are being pulled (spoofing) or stacked."""
        if len(self._dom_history) < 3:
            return None

        prev = self._dom_history[-2]
        curr = self._dom_history[-1]

        # Check for significant bid pulling (spoofing indicator)
        bid_change = curr.total_bid_depth - prev.total_bid_depth
        ask_change = curr.total_ask_depth - prev.total_ask_depth

        result = None
        # Large bid pull = bearish (fake support pulled)
        if bid_change < -prev.total_bid_depth * 0.3:
            result = {
                "type": "bid_pull",
                "direction": "BEARISH",
                "magnitude": abs(bid_change),
                "description": f"BID PULL detected — {abs(bid_change)} contracts pulled from bid side",
            }
        # Large ask pull = bullish (fake resistance pulled)
        elif ask_change < -prev.total_ask_depth * 0.3:
            result = {
                "type": "ask_pull",
                "direction": "BULLISH",
                "magnitude": abs(ask_change),
                "description": f"ASK PULL detected — {abs(ask_change)} contracts pulled from ask side",
            }
        # Large bid stack = bullish (or bait)
        elif bid_change > prev.total_bid_depth * 0.3:
            result = {
                "type": "bid_stack",
                "direction": "BULLISH",
                "magnitude": bid_change,
                "description": f"BID STACK detected — {bid_change} contracts added to bid side",
            }
        # Large ask stack = bearish (or bait)
        elif ask_change > prev.total_ask_depth * 0.3:
            result = {
                "type": "ask_stack",
                "direction": "BEARISH",
                "magnitude": ask_change,
                "description": f"ASK STACK detected — {ask_change} contracts added to ask side",
            }

        return result

    # ─── Cluster Analysis (Multi-Signal Convergence) ─────────────────────

    def cluster_analysis(self, bar: FootprintBar, profile: VolumeProfile) -> list[FootprintSignal]:
        """Detect when multiple footprint signals converge at the same level.

        Convergence of absorption + imbalance + exhaustion = very high confidence signal.
        """
        signals = []

        # Gather all signals for this bar
        absorption_sigs = self.detect_absorption_footprint(bar)
        unfinished_sigs = self.detect_unfinished_auctions(bar)
        excess_sigs = self.detect_excess(bar)
        stacked = self.detect_stacked_imbalances(bar.profile)

        # Index by price (rounded)
        price_signals: dict[float, list[str]] = defaultdict(list)

        for sig in absorption_sigs:
            price_signals[self._round_price(sig.price)].append("absorption")
        for sig in unfinished_sigs:
            price_signals[self._round_price(sig.price)].append("unfinished_auction")
        for sig in excess_sigs:
            price_signals[self._round_price(sig.price)].append("excess")
        for si in stacked:
            for p in [si.start_price, si.end_price]:
                price_signals[self._round_price(p)].append("stacked_imbalance")

        # Build cluster signals where 2+ signals converge
        for price, components in price_signals.items():
            if len(components) < 2:
                continue

            # Determine direction from component analysis
            bullish_count = sum(1 for s in absorption_sigs + excess_sigs
                                if self._round_price(s.price) == price and s.direction == "BULLISH")
            bearish_count = sum(1 for s in absorption_sigs + excess_sigs
                                if self._round_price(s.price) == price and s.direction == "BEARISH")

            direction = "BULLISH" if bullish_count >= bearish_count else "BEARISH"
            confidence = min(0.95, 0.5 + len(components) * 0.15)

            signals.append(FootprintSignal(
                timestamp=bar.start_time, price=price,
                signal_type="cluster",
                direction=direction,
                confidence=confidence,
                description=(
                    f"CLUSTER at {price:.2f} — {', '.join(components)} converging. "
                    f"{direction} with {confidence:.0%} confidence."
                ),
                components=components,
            ))

        return signals

    # ─── Multi-Timeframe Summary ────────────────────────────────────────

    def get_multi_timeframe_summary(self) -> dict:
        """Aggregate order flow across 1m, 5m, 15m for bigger picture."""
        def _summarize(fp: FootprintChart) -> dict:
            bars = fp.recent_bars(20)
            if not bars:
                return {"delta": 0, "volume": 0, "direction": "FLAT"}
            total_delta = sum(b.delta for b in bars)
            total_vol = sum(b.volume for b in bars)
            direction = "BULLISH" if total_delta > 0 else "BEARISH" if total_delta < 0 else "FLAT"
            return {"delta": total_delta, "volume": total_vol, "direction": direction}

        return {
            "1m": _summarize(self.footprint_1m),
            "5m": _summarize(self.footprint_5m),
            "15m": _summarize(self.footprint_15m),
            "poc_direction": self.poc_direction(),
            "delta_roc": self.delta_rate_of_change(),
            "trade_speed": self.trade_speed(),
            "aggressive_passive_ratio": self.aggressive_passive_ratio(),
        }

    def reset_session(self):
        """Reset for new session."""
        self.footprint_1m = FootprintChart(bar_duration_sec=60, tick_size=self.tick_size)
        self.footprint_5m = FootprintChart(bar_duration_sec=300, tick_size=self.tick_size)
        self.footprint_15m = FootprintChart(bar_duration_sec=900, tick_size=self.tick_size)
        self._poc_history.clear()
        self._trade_timestamps.clear()
        self._delta_history.clear()
        self._dom_history.clear()
        self._aggressive_volume = 0
        self._passive_volume = 0
        self.session_cumulative_delta = 0
        log.info("Advanced Order Flow Engine session reset")
