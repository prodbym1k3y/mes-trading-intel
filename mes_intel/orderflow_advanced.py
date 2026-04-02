"""Advanced Order Flow Engine — Phase 2.

Institutional-grade order flow analysis for MES futures:
  - Big trade detection with absorption/breakout classification
  - Institutional execution pattern recognition (TWAP, VWAP, iceberg, sweep, accumulation/distribution)
  - DOM / Level 2 order book imbalance analysis with spoof detection
  - Cumulative delta tracking with divergence alerts
  - Trade speed / HFT burst detection and flow classification
  - Aggressive vs passive flow separation
  - Multi-timeframe order flow aggregation and alignment scoring
  - AdvancedOrderFlowEngine orchestrator with EventBus integration
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from .orderflow import Tick, PriceLevel, VolumeProfile, FootprintBar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BigTradeEvent:
    """A trade significantly larger than the rolling average."""
    price: float
    size: int
    avg_size: float
    multiplier: float
    side: str                 # 'BUY' or 'SELL'
    classification: str       # 'ABSORPTION' or 'BREAKOUT'
    timestamp: float


@dataclass
class InstitutionalSignal:
    """A detected institutional execution pattern."""
    pattern_type: str         # 'TWAP'/'VWAP_EXEC'/'ICEBERG'/'SWEEP'/'ACCUMULATION'/'DISTRIBUTION'
    confidence: float         # 0.0 – 1.0
    side: str                 # 'BUY', 'SELL', or 'NEUTRAL'
    price_range: float        # price span covered
    estimated_size: int       # estimated total order size
    timestamp: float = field(default_factory=time.time)


@dataclass
class DOMImbalance:
    """Snapshot of DOM (depth of market) imbalance."""
    ratio: float              # bid_vol / ask_vol
    dominant_side: str        # 'BID' or 'ASK'
    imbalance_pct: float      # 0–100
    stacked_bid_levels: int   # consecutive bid levels with large size
    stacked_ask_levels: int


@dataclass
class SpoofingAlert:
    """A suspected spoofing event detected in DOM data."""
    alert_type: str           # 'PULL' or 'STACK'
    side: str                 # 'BID' or 'ASK'
    price: float
    size: int
    confidence: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class CDDivergenceAlert:
    """Cumulative delta divergence vs price."""
    divergence_type: str      # 'BULLISH' or 'BEARISH'
    price_extreme: float
    delta_extreme: int
    bars_span: int
    confidence: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class FlowClassification:
    """Trade speed / algo pattern classification."""
    is_algo: bool
    algo_confidence: float
    pattern: str              # 'HFT_BURST'/'STEADY_ALGO'/'HUMAN'/'MIXED'
    trades_per_sec: float


@dataclass
class FlowSummary:
    """Aggressive vs passive flow breakdown."""
    aggressive_buy_vol: int
    aggressive_sell_vol: int
    passive_buy_vol: int
    passive_sell_vol: int
    aggression_ratio: float   # aggressive / total
    dominant_flow: str        # 'AGGRESSIVE_BUY'/'AGGRESSIVE_SELL'/'PASSIVE'/'NEUTRAL'


@dataclass
class OrderFlowSignal:
    """Composite order flow signal produced by AdvancedOrderFlowEngine."""
    direction: float          # -1.0 (bearish) to +1.0 (bullish)
    confidence: float         # 0.0 – 1.0
    components: Dict[str, Any] = field(default_factory=dict)
    alerts: List[Any] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# 1. BigTradesDetector
# ---------------------------------------------------------------------------

class BigTradesDetector:
    """Detect trades significantly larger than the rolling average size.

    A big trade is classified as:
      - ABSORPTION: trade against the current micro-trend; price tends to
        revert, meaning resting liquidity absorbed the aggression.
      - BREAKOUT: trade in the direction of the current micro-trend; price
        continues through the level.
    """

    def __init__(self, window: int = 50, multiplier: float = 3.0):
        self.window = window
        self.multiplier = multiplier

        self._sizes: deque[int] = deque(maxlen=window)
        self._prices: deque[float] = deque(maxlen=20)
        self._big_trades: deque[BigTradeEvent] = deque(maxlen=100)

    # ------------------------------------------------------------------
    def update(self, tick: Tick) -> Optional[BigTradeEvent]:
        """Process a tick; return BigTradeEvent if the trade qualifies."""
        self._sizes.append(tick.size)
        self._prices.append(tick.price)

        if len(self._sizes) < 10:
            return None

        avg = float(np.mean(self._sizes))
        if avg == 0:
            return None

        ratio = tick.size / avg
        if ratio < self.multiplier:
            return None

        side = "BUY" if tick.is_buy else "SELL"
        classification = self._classify(tick)

        event = BigTradeEvent(
            price=tick.price,
            size=tick.size,
            avg_size=round(avg, 2),
            multiplier=round(ratio, 2),
            side=side,
            classification=classification,
            timestamp=tick.timestamp,
        )
        self._big_trades.append(event)
        return event

    def _classify(self, tick: Tick) -> str:
        """Classify as ABSORPTION or BREAKOUT based on price action context."""
        if len(self._prices) < 5:
            return "BREAKOUT"

        recent = list(self._prices)[:-1]  # exclude current tick
        if tick.is_buy:
            # Absorption: price was rising but this big buy might stall it
            # Heuristic: if we're near recent high, likely breakout; else absorption
            recent_high = max(recent[-5:]) if len(recent) >= 5 else recent[-1]
            if tick.price >= recent_high:
                return "BREAKOUT"
            return "ABSORPTION"
        else:
            recent_low = min(recent[-5:]) if len(recent) >= 5 else recent[-1]
            if tick.price <= recent_low:
                return "BREAKOUT"
            return "ABSORPTION"

    @property
    def recent_big_trades(self) -> List[BigTradeEvent]:
        return list(self._big_trades)

    def reset(self):
        self._sizes.clear()
        self._prices.clear()
        self._big_trades.clear()


# ---------------------------------------------------------------------------
# 2. InstitutionalFlowDetector
# ---------------------------------------------------------------------------

class InstitutionalFlowDetector:
    """Detect institutional execution patterns in the tick stream.

    Patterns:
      TWAP       — equal-sized orders at regular time intervals
      VWAP_EXEC  — order sizes proportional to market volume
      ICEBERG    — repeated same-size fills at same price (3+ hits)
      SWEEP      — 5+ aggressive orders across multiple levels in < 2 s
      ACCUMULATION/DISTRIBUTION — consistent directional bias over time
    """

    _SWEEP_WINDOW_SEC = 2.0
    _SWEEP_MIN_COUNT = 5

    def __init__(self):
        self._ticks: deque[Tick] = deque(maxlen=2000)
        self._signals: deque[InstitutionalSignal] = deque(maxlen=200)

        # TWAP / VWAP tracking
        self._last_ts: float = 0.0
        self._intervals: deque[float] = deque(maxlen=50)
        self._recent_sizes: deque[int] = deque(maxlen=50)

        # Iceberg: (price_rounded, size) → list of timestamps
        self._iceberg_hits: defaultdict = defaultdict(list)

        # Accumulation / distribution
        self._cum_buy: int = 0
        self._cum_sell: int = 0
        self._delta_history: deque[tuple] = deque(maxlen=500)

        self._last_analysis: float = 0.0
        self._analysis_interval: float = 3.0  # seconds

    # ------------------------------------------------------------------
    def update(self, tick: Tick) -> Optional[InstitutionalSignal]:
        """Process a tick; return InstitutionalSignal if pattern detected."""
        now = tick.timestamp
        self._ticks.append(tick)
        self._recent_sizes.append(tick.size)

        if self._last_ts > 0:
            iv = now - self._last_ts
            if iv > 0:
                self._intervals.append(iv)
        self._last_ts = now

        # Iceberg tracking
        rounded_price = round(round(tick.price / 0.25) * 0.25, 2)
        key = (rounded_price, tick.size)
        self._iceberg_hits[key].append(now)

        # Accumulation
        if tick.is_buy:
            self._cum_buy += tick.size
        else:
            self._cum_sell += tick.size
        self._delta_history.append((now, self._cum_buy - self._cum_sell))

        # Periodic analysis
        if now - self._last_analysis < self._analysis_interval:
            return None
        self._last_analysis = now
        return self._run_analysis(now)

    def _run_analysis(self, now: float) -> Optional[InstitutionalSignal]:
        sig = (self._check_twap(now)
               or self._check_iceberg(now)
               or self._check_sweep(now)
               or self._check_accum_distrib(now))
        if sig:
            self._signals.append(sig)
        return sig

    def _check_twap(self, now: float) -> Optional[InstitutionalSignal]:
        if len(self._intervals) < 10 or len(self._recent_sizes) < 10:
            return None
        intervals = list(self._intervals)[-10:]
        sizes = list(self._recent_sizes)[-10:]

        avg_iv = float(np.mean(intervals))
        avg_sz = float(np.mean(sizes))
        if avg_iv == 0 or avg_sz == 0:
            return None

        cv_iv = float(np.std(intervals)) / avg_iv
        cv_sz = float(np.std(sizes)) / avg_sz

        # TWAP: size CV < 5% (within 5%) and interval CV < 20%
        if cv_sz > 0.05 or cv_iv > 0.20:
            return None

        recent = [t for t in self._ticks if t.timestamp >= now - 30]
        buy_vol = sum(t.size for t in recent if t.is_buy)
        sell_vol = sum(t.size for t in recent if t.is_sell)
        side = "BUY" if buy_vol > sell_vol else "SELL" if sell_vol > buy_vol else "NEUTRAL"
        confidence = max(0.0, min(1.0, (0.05 - cv_sz) / 0.05 * 0.5
                                  + (0.20 - cv_iv) / 0.20 * 0.5))
        prices = [t.price for t in recent]
        price_range = max(prices) - min(prices) if prices else 0.0
        est_size = sum(t.size for t in recent)
        return InstitutionalSignal(
            pattern_type="TWAP",
            confidence=round(confidence, 3),
            side=side,
            price_range=round(price_range, 2),
            estimated_size=est_size,
            timestamp=now,
        )

    def _check_iceberg(self, now: float) -> Optional[InstitutionalSignal]:
        cutoff = now - 60.0
        best: Optional[InstitutionalSignal] = None
        best_conf = 0.0

        for (price, size), ts_list in list(self._iceberg_hits.items()):
            # Prune old timestamps
            fresh = [t for t in ts_list if t >= cutoff]
            self._iceberg_hits[(price, size)] = fresh
            if len(fresh) < 3 or size < 1:
                continue

            confidence = min(1.0, len(fresh) / 10.0)
            if confidence <= best_conf:
                continue

            recent_at = [t for t in self._ticks
                         if abs(t.price - price) < 0.125 and t.timestamp >= cutoff]
            buy_vol = sum(t.size for t in recent_at if t.is_buy)
            sell_vol = sum(t.size for t in recent_at if t.is_sell)
            side = "BUY" if buy_vol >= sell_vol else "SELL"

            best_conf = confidence
            best = InstitutionalSignal(
                pattern_type="ICEBERG",
                confidence=round(confidence, 3),
                side=side,
                price_range=0.0,
                estimated_size=len(fresh) * size,
                timestamp=now,
            )
        return best

    def _check_sweep(self, now: float) -> Optional[InstitutionalSignal]:
        window = [t for t in self._ticks
                  if t.timestamp >= now - self._SWEEP_WINDOW_SEC]
        if len(window) < self._SWEEP_MIN_COUNT:
            return None

        buy_agg = [t for t in window if t.is_buy]
        sell_agg = [t for t in window if t.is_sell]

        for agg_ticks, side in [(buy_agg, "BUY"), (sell_agg, "SELL")]:
            if len(agg_ticks) < self._SWEEP_MIN_COUNT:
                continue
            prices = sorted({round(round(t.price / 0.25) * 0.25, 2) for t in agg_ticks})
            if len(prices) < 2:
                continue
            price_range = prices[-1] - prices[0]
            est_size = sum(t.size for t in agg_ticks)
            time_span = (max(t.timestamp for t in agg_ticks)
                         - min(t.timestamp for t in agg_ticks))
            confidence = min(1.0, len(agg_ticks) / 15.0 + price_range / 2.0)
            _ = time_span  # used in classification context
            return InstitutionalSignal(
                pattern_type="SWEEP",
                confidence=round(confidence, 3),
                side=side,
                price_range=round(price_range, 2),
                estimated_size=est_size,
                timestamp=now,
            )
        return None

    def _check_accum_distrib(self, now: float) -> Optional[InstitutionalSignal]:
        if len(self._delta_history) < 30:
            return None
        cutoff = now - 120.0
        window = [(ts, d) for ts, d in self._delta_history if ts >= cutoff]
        if len(window) < 20:
            return None

        deltas = [d for _, d in window]
        delta_change = deltas[-1] - deltas[0]
        total_vol = sum(t.size for t in self._ticks if t.timestamp >= cutoff)
        if total_vol == 0:
            return None

        bias = delta_change / total_vol  # -1 to +1 roughly
        if abs(bias) < 0.2:
            return None

        side = "BUY" if bias > 0 else "SELL"
        pattern = "ACCUMULATION" if bias > 0 else "DISTRIBUTION"
        confidence = min(1.0, abs(bias))

        prices = [t.price for t in self._ticks if t.timestamp >= cutoff]
        price_range = max(prices) - min(prices) if prices else 0.0

        return InstitutionalSignal(
            pattern_type=pattern,
            confidence=round(confidence, 3),
            side=side,
            price_range=round(price_range, 2),
            estimated_size=abs(delta_change),
            timestamp=now,
        )

    @property
    def recent_signals(self) -> List[InstitutionalSignal]:
        return list(self._signals)

    def reset(self):
        self._ticks.clear()
        self._signals.clear()
        self._intervals.clear()
        self._recent_sizes.clear()
        self._iceberg_hits.clear()
        self._cum_buy = 0
        self._cum_sell = 0
        self._delta_history.clear()
        self._last_ts = 0.0
        self._last_analysis = 0.0


# ---------------------------------------------------------------------------
# 3. DOMImbalanceAnalyzer
# ---------------------------------------------------------------------------

class DOMImbalanceAnalyzer:
    """Analyze DOM (depth of market) snapshots for imbalance and spoof detection.

    Snapshots are lists of PriceLevel objects.  A DOM snapshot deque is
    maintained internally; callers push new snapshots via update_snapshot().
    """

    _LARGE_ORDER_PCT = 0.15   # order is "large" if > 15% of side total
    _PULL_WINDOW_SEC = 2.0
    _STACK_MIN_LEVELS = 3

    def __init__(self, max_snapshots: int = 60):
        self._snapshots: deque[tuple[float, List[PriceLevel], List[PriceLevel]]] = deque(
            maxlen=max_snapshots
        )

    # ------------------------------------------------------------------
    def update_snapshot(self, bids: List[PriceLevel], asks: List[PriceLevel],
                        timestamp: Optional[float] = None):
        """Record a new DOM snapshot (bids and asks as PriceLevel lists)."""
        ts = timestamp if timestamp is not None else time.time()
        self._snapshots.append((ts, list(bids), list(asks)))

    def calculate_imbalance(self, bids: List[PriceLevel],
                            asks: List[PriceLevel]) -> DOMImbalance:
        """Compute bid/ask imbalance from a single snapshot."""
        bid_vol = sum(lv.bid_volume for lv in bids) if bids else 0
        ask_vol = sum(lv.ask_volume for lv in asks) if asks else 0

        total = bid_vol + ask_vol
        if total == 0:
            return DOMImbalance(ratio=1.0, dominant_side="NEUTRAL",
                                imbalance_pct=0.0,
                                stacked_bid_levels=0, stacked_ask_levels=0)

        ratio = bid_vol / ask_vol if ask_vol > 0 else float("inf")
        dominant_side = "BID" if bid_vol >= ask_vol else "ASK"
        imbalance_pct = abs(bid_vol - ask_vol) / total * 100.0

        # Stacked levels: consecutive levels each exceeding avg volume
        stacked_bids = self._count_stacked(bids, "bid")
        stacked_asks = self._count_stacked(asks, "ask")

        return DOMImbalance(
            ratio=round(ratio, 3),
            dominant_side=dominant_side,
            imbalance_pct=round(imbalance_pct, 2),
            stacked_bid_levels=stacked_bids,
            stacked_ask_levels=stacked_asks,
        )

    def _count_stacked(self, levels: List[PriceLevel], side: str) -> int:
        """Count consecutive levels with above-average volume (stacking)."""
        if not levels:
            return 0
        vols = [lv.bid_volume if side == "bid" else lv.ask_volume for lv in levels]
        avg = sum(vols) / len(vols) if vols else 0
        if avg == 0:
            return 0
        threshold = avg * 1.5
        count = 0
        streak = 0
        for v in vols:
            if v >= threshold:
                streak += 1
                count = max(count, streak)
            else:
                streak = 0
        return count

    def detect_spoofing(self,
                        dom_snapshots: Optional[deque] = None
                        ) -> List[SpoofingAlert]:
        """Scan the snapshot history for pull and stack patterns.

        Returns a list of SpoofingAlert objects.  If dom_snapshots is
        provided it is used instead of the internal deque.
        """
        snaps = dom_snapshots if dom_snapshots is not None else self._snapshots
        alerts: List[SpoofingAlert] = []

        if len(snaps) < 2:
            return alerts

        snap_list = list(snaps)
        alerts.extend(self._detect_pulls(snap_list))
        alerts.extend(self._detect_stacks(snap_list))
        return alerts

    def _detect_pulls(self, snaps: list) -> List[SpoofingAlert]:
        """Pull detection: large order appears then disappears within 2 s."""
        alerts: List[SpoofingAlert] = []
        if len(snaps) < 2:
            return alerts

        ts_prev, bids_prev, asks_prev = snaps[-2]
        ts_curr, bids_curr, asks_curr = snaps[-1]

        if ts_curr - ts_prev > self._PULL_WINDOW_SEC:
            return alerts

        def large_orders(levels: List[PriceLevel], side: str):
            total = sum(lv.bid_volume if side == "bid" else lv.ask_volume
                        for lv in levels)
            threshold = total * self._LARGE_ORDER_PCT
            result = {}
            for lv in levels:
                vol = lv.bid_volume if side == "bid" else lv.ask_volume
                if vol >= threshold:
                    result[lv.price] = vol
            return result

        for side, prev_lvs, curr_lvs, dom_side in [
            ("bid", bids_prev, bids_curr, "BID"),
            ("ask", asks_prev, asks_curr, "ASK"),
        ]:
            prev_large = large_orders(prev_lvs, side)
            curr_large = large_orders(curr_lvs, side)

            for price, size in prev_large.items():
                if price not in curr_large:
                    # Order appeared and was pulled
                    confidence = min(1.0, size / max(1, sum(
                        lv.bid_volume if side == "bid" else lv.ask_volume
                        for lv in prev_lvs
                    )) * 5)
                    alerts.append(SpoofingAlert(
                        alert_type="PULL",
                        side=dom_side,
                        price=price,
                        size=size,
                        confidence=round(confidence, 3),
                        timestamp=ts_curr,
                    ))
        return alerts

    def _detect_stacks(self, snaps: list) -> List[SpoofingAlert]:
        """Stack detection: multiple large orders suddenly appearing on same side."""
        alerts: List[SpoofingAlert] = []
        if len(snaps) < 2:
            return alerts

        ts_prev, bids_prev, asks_prev = snaps[-2]
        ts_curr, bids_curr, asks_curr = snaps[-1]

        for side, prev_lvs, curr_lvs, dom_side in [
            ("bid", bids_prev, bids_curr, "BID"),
            ("ask", asks_prev, asks_curr, "ASK"),
        ]:
            prev_total = sum(lv.bid_volume if side == "bid" else lv.ask_volume
                             for lv in prev_lvs) or 1
            curr_total = sum(lv.bid_volume if side == "bid" else lv.ask_volume
                             for lv in curr_lvs) or 1
            threshold_curr = curr_total * self._LARGE_ORDER_PCT

            new_large_levels = []
            prev_prices = {lv.price: (lv.bid_volume if side == "bid" else lv.ask_volume)
                           for lv in prev_lvs}
            for lv in curr_lvs:
                vol = lv.bid_volume if side == "bid" else lv.ask_volume
                prev_vol = prev_prices.get(lv.price, 0)
                if vol >= threshold_curr and vol > prev_vol * 2:
                    new_large_levels.append((lv.price, vol))

            if len(new_large_levels) >= self._STACK_MIN_LEVELS:
                total_stacked = sum(v for _, v in new_large_levels)
                confidence = min(1.0, len(new_large_levels) / 6.0)
                best_price = new_large_levels[0][0]
                alerts.append(SpoofingAlert(
                    alert_type="STACK",
                    side=dom_side,
                    price=best_price,
                    size=total_stacked,
                    confidence=round(confidence, 3),
                    timestamp=ts_curr,
                ))
        return alerts

    def reset(self):
        self._snapshots.clear()


# ---------------------------------------------------------------------------
# 4. CumulativeDeltaAnalyzer
# ---------------------------------------------------------------------------

class CumulativeDeltaAnalyzer:
    """Track cumulative delta (buy_vol − sell_vol) and detect divergences.

    Also maintains session, hourly, and 5-minute rolling delta buckets.
    """

    _DIVERGENCE_LOOKBACK = 5   # swing points to look back for divergence

    def __init__(self):
        # Running cumulative delta
        self._cum_delta: int = 0
        self._tick_count: int = 0

        # Bar-level history: each entry is (timestamp, price, cum_delta)
        self._bar_history: deque[tuple] = deque(maxlen=200)
        self._last_bar_ts: float = 0.0
        self._bar_interval: float = 60.0  # 1-minute bars

        # Price and delta swings
        self._price_highs: deque[tuple] = deque(maxlen=50)
        self._price_lows: deque[tuple] = deque(maxlen=50)
        self._delta_at_highs: deque[int] = deque(maxlen=50)
        self._delta_at_lows: deque[int] = deque(maxlen=50)
        self._recent_prices: deque[tuple] = deque(maxlen=100)

        # Time-bucketed deltas
        self._session_delta: int = 0
        self._hourly_ticks: deque[tuple] = deque(maxlen=50000)
        self._5min_ticks: deque[tuple] = deque(maxlen=10000)

        self._alerts: deque[CDDivergenceAlert] = deque(maxlen=100)

    # ------------------------------------------------------------------
    def update(self, tick: Tick) -> Optional[CDDivergenceAlert]:
        """Process a tick; return CDDivergenceAlert if divergence detected."""
        now = tick.timestamp
        delta_change = tick.size if tick.is_buy else -tick.size
        self._cum_delta += delta_change
        self._tick_count += 1

        # Session / hourly / 5-min buckets
        self._session_delta += delta_change
        self._hourly_ticks.append((now, delta_change))
        self._5min_ticks.append((now, delta_change))

        # Expire old entries
        self._hourly_ticks = deque(
            [(ts, d) for ts, d in self._hourly_ticks if ts >= now - 3600],
            maxlen=50000,
        )
        self._5min_ticks = deque(
            [(ts, d) for ts, d in self._5min_ticks if ts >= now - 300],
            maxlen=10000,
        )

        # Track price for swing point detection
        self._recent_prices.append((now, tick.price))
        self._track_swings(now, tick.price)

        # Close bar and check for divergence
        if now - self._last_bar_ts >= self._bar_interval:
            self._bar_history.append((now, tick.price, self._cum_delta))
            self._last_bar_ts = now
            return self._check_divergence(now)

        return None

    def _track_swings(self, now: float, price: float):
        """Identify local swing highs/lows from recent prices."""
        if len(self._recent_prices) < 9:
            return
        window = [p for _, p in list(self._recent_prices)[-9:]]
        mid = window[4]
        if mid == max(window):
            self._price_highs.append((now, mid))
            self._delta_at_highs.append(self._cum_delta)
        if mid == min(window):
            self._price_lows.append((now, mid))
            self._delta_at_lows.append(self._cum_delta)

    def _check_divergence(self, now: float) -> Optional[CDDivergenceAlert]:
        n = self._DIVERGENCE_LOOKBACK

        # Bearish divergence: price higher high, delta lower high
        if (len(self._price_highs) >= 2 and len(self._delta_at_highs) >= 2):
            ph = list(self._price_highs)
            dh = list(self._delta_at_highs)
            if ph[-1][1] > ph[-2][1] and dh[-1] < dh[-2]:
                bars_span = min(n, len(self._bar_history))
                conf = min(1.0, (ph[-1][1] - ph[-2][1]) / max(0.25, ph[-2][1]) * 10)
                alert = CDDivergenceAlert(
                    divergence_type="BEARISH",
                    price_extreme=ph[-1][1],
                    delta_extreme=dh[-1],
                    bars_span=bars_span,
                    confidence=round(conf, 3),
                    timestamp=now,
                )
                self._alerts.append(alert)
                return alert

        # Bullish divergence: price lower low, delta higher low
        if (len(self._price_lows) >= 2 and len(self._delta_at_lows) >= 2):
            pl = list(self._price_lows)
            dl = list(self._delta_at_lows)
            if pl[-1][1] < pl[-2][1] and dl[-1] > dl[-2]:
                bars_span = min(n, len(self._bar_history))
                conf = min(1.0, (pl[-2][1] - pl[-1][1]) / max(0.25, pl[-2][1]) * 10)
                alert = CDDivergenceAlert(
                    divergence_type="BULLISH",
                    price_extreme=pl[-1][1],
                    delta_extreme=dl[-1],
                    bars_span=bars_span,
                    confidence=round(conf, 3),
                    timestamp=now,
                )
                self._alerts.append(alert)
                return alert

        return None

    # ------------------------------------------------------------------
    @property
    def cumulative_delta(self) -> int:
        return self._cum_delta

    @property
    def session_delta(self) -> int:
        return self._session_delta

    @property
    def hourly_delta(self) -> int:
        return sum(d for _, d in self._hourly_ticks)

    @property
    def five_min_delta(self) -> int:
        return sum(d for _, d in self._5min_ticks)

    @property
    def alerts(self) -> List[CDDivergenceAlert]:
        return list(self._alerts)

    def reset(self):
        self._cum_delta = 0
        self._tick_count = 0
        self._bar_history.clear()
        self._price_highs.clear()
        self._price_lows.clear()
        self._delta_at_highs.clear()
        self._delta_at_lows.clear()
        self._recent_prices.clear()
        self._session_delta = 0
        self._hourly_ticks.clear()
        self._5min_ticks.clear()
        self._alerts.clear()
        self._last_bar_ts = 0.0


# ---------------------------------------------------------------------------
# 5. TradeSpeedAnalyzer
# ---------------------------------------------------------------------------

class TradeSpeedAnalyzer:
    """Detect algorithmic trading patterns from trade arrival speed.

    A burst is defined as > 10 trades in < 500 ms, which strongly suggests
    algorithmic / HFT activity.  Regular-interval detection uses low variance
    in inter-trade timing as a signal.
    """

    _BURST_COUNT = 10
    _BURST_WINDOW_MS = 500.0

    def __init__(self):
        self._timestamps: deque[float] = deque(maxlen=5000)
        self._intervals: deque[float] = deque(maxlen=1000)  # ms
        self._last_ts: float = 0.0

    # ------------------------------------------------------------------
    def add_tick(self, tick: Tick):
        """Record tick timestamp."""
        now = tick.timestamp
        self._timestamps.append(now)
        if self._last_ts > 0:
            iv_ms = (now - self._last_ts) * 1000.0
            if iv_ms > 0:
                self._intervals.append(iv_ms)
        self._last_ts = now

    def classify_flow(self, recent_ticks: List[Tick]) -> FlowClassification:
        """Classify the flow pattern for the given tick list."""
        if not recent_ticks:
            return FlowClassification(
                is_algo=False, algo_confidence=0.0,
                pattern="HUMAN", trades_per_sec=0.0
            )

        if len(recent_ticks) == 1:
            return FlowClassification(
                is_algo=False, algo_confidence=0.0,
                pattern="HUMAN", trades_per_sec=0.0
            )

        times = [t.timestamp for t in recent_ticks]
        span_sec = times[-1] - times[0]
        tps = len(recent_ticks) / span_sec if span_sec > 0 else 0.0

        # Burst detection: >=10 trades in any 500 ms window
        is_burst = False
        for i in range(len(times)):
            window_end = times[i] + self._BURST_WINDOW_MS / 1000.0
            count_in_window = sum(1 for t in times[i:] if t <= window_end)
            if count_in_window >= self._BURST_COUNT:
                is_burst = True
                break

        # Interval variance: low CV = regular = algo
        if len(times) >= 3:
            ivs = np.diff(times) * 1000.0  # ms
            avg_iv = float(np.mean(ivs))
            cv_iv = float(np.std(ivs)) / avg_iv if avg_iv > 0 else 1.0
        else:
            cv_iv = 1.0

        is_regular = cv_iv < 0.25

        # Classify
        if is_burst:
            pattern = "HFT_BURST"
            algo_confidence = min(1.0, 0.7 + (tps / 100.0) * 0.3)
        elif is_regular and tps > 5:
            pattern = "STEADY_ALGO"
            algo_confidence = max(0.0, min(1.0, 1.0 - cv_iv))
        elif is_regular and tps <= 5:
            pattern = "MIXED"
            algo_confidence = 0.4
        else:
            pattern = "HUMAN"
            algo_confidence = max(0.0, 0.3 - cv_iv * 0.1)

        is_algo = algo_confidence >= 0.5

        return FlowClassification(
            is_algo=is_algo,
            algo_confidence=round(algo_confidence, 3),
            pattern=pattern,
            trades_per_sec=round(tps, 2),
        )

    @property
    def current_speed(self) -> float:
        """Trades per second over the last 5 seconds."""
        if not self._timestamps:
            return 0.0
        now = self._timestamps[-1]
        cutoff = now - 5.0
        count = sum(1 for ts in self._timestamps if ts >= cutoff)
        return round(count / 5.0, 2)

    def reset(self):
        self._timestamps.clear()
        self._intervals.clear()
        self._last_ts = 0.0


# ---------------------------------------------------------------------------
# 6. AggressivePassiveClassifier
# ---------------------------------------------------------------------------

class AggressivePassiveClassifier:
    """Separate aggressive (market orders) from passive (limit orders) flow.

    Uses the Tick.aggressor field and price context to classify each trade:
      Aggressive buy:  tick.aggressor == True (ASK), price ≥ recent ask
      Passive buy:     tick.aggressor == False (BID) or price ≤ recent bid
    """

    def __init__(self):
        self._ticks: deque[Tick] = deque(maxlen=5000)
        self._ask: Optional[float] = None
        self._bid: Optional[float] = None

    # ------------------------------------------------------------------
    def update_nbbo(self, bid: float, ask: float):
        """Update best bid / ask for classification."""
        self._bid = bid
        self._ask = ask

    def add_tick(self, tick: Tick):
        self._ticks.append(tick)

    def _classify_tick(self, tick: Tick) -> tuple[str, str]:
        """Return (aggression_type, side): type is 'AGGRESSIVE' or 'PASSIVE'."""
        if tick.is_buy:
            if tick.aggressor == "ASK":
                return "AGGRESSIVE", "BUY"
            return "PASSIVE", "BUY"
        else:
            if tick.aggressor == "BID":
                return "AGGRESSIVE", "SELL"
            return "PASSIVE", "SELL"

    def get_flow_summary(self, window_ticks: int = 100) -> FlowSummary:
        """Summarize flow over the last window_ticks ticks."""
        window = list(self._ticks)[-window_ticks:]

        agg_buy = agg_sell = pas_buy = pas_sell = 0
        for tick in window:
            aggression, side = self._classify_tick(tick)
            if aggression == "AGGRESSIVE" and side == "BUY":
                agg_buy += tick.size
            elif aggression == "AGGRESSIVE" and side == "SELL":
                agg_sell += tick.size
            elif aggression == "PASSIVE" and side == "BUY":
                pas_buy += tick.size
            else:
                pas_sell += tick.size

        total = agg_buy + agg_sell + pas_buy + pas_sell
        aggression_ratio = (agg_buy + agg_sell) / total if total > 0 else 0.0

        # Dominant flow
        candidates = {
            "AGGRESSIVE_BUY": agg_buy,
            "AGGRESSIVE_SELL": agg_sell,
            "PASSIVE_BUY": pas_buy,
            "PASSIVE_SELL": pas_sell,
        }
        dominant_flow = max(candidates, key=candidates.get) if total > 0 else "NEUTRAL"
        if candidates[dominant_flow] == 0:
            dominant_flow = "NEUTRAL"

        return FlowSummary(
            aggressive_buy_vol=agg_buy,
            aggressive_sell_vol=agg_sell,
            passive_buy_vol=pas_buy,
            passive_sell_vol=pas_sell,
            aggression_ratio=round(aggression_ratio, 4),
            dominant_flow=dominant_flow,
        )

    def reset(self):
        self._ticks.clear()


# ---------------------------------------------------------------------------
# 7. MultiTimeframeOrderFlow
# ---------------------------------------------------------------------------

class MultiTimeframeOrderFlow:
    """Aggregate order flow across 1m, 5m, and 15m timeframes.

    Maintains rolling delta, volume, buy_vol, sell_vol, and a per-bar delta
    history (last 20 bars) for each timeframe.

    Alignment score:
      +1.0  all 3 bullish
      +0.67 2 bullish, 1 other
      +0.33 1 bullish, 1 bearish, 1 neutral  (or similar)
       0.0  mixed / neutral
      -0.33 1 bearish dominant
      -0.67 2 bearish, 1 other
      -1.0  all 3 bearish
    """

    TIMEFRAMES = {60: "1m", 300: "5m", 900: "15m"}

    def __init__(self):
        # Rolling tick buckets per timeframe
        self._tf_ticks: Dict[int, deque] = {
            tf: deque(maxlen=100000) for tf in self.TIMEFRAMES
        }
        # Bar-level delta history
        self._tf_bar_delta: Dict[int, deque] = {
            tf: deque(maxlen=20) for tf in self.TIMEFRAMES
        }
        self._tf_bar_start: Dict[int, float] = {tf: 0.0 for tf in self.TIMEFRAMES}
        self._tf_bar_buy: Dict[int, int] = {tf: 0 for tf in self.TIMEFRAMES}
        self._tf_bar_sell: Dict[int, int] = {tf: 0 for tf in self.TIMEFRAMES}

    # ------------------------------------------------------------------
    def update(self, tick: Tick):
        """Route tick into all timeframe buckets."""
        now = tick.timestamp
        for tf in self.TIMEFRAMES:
            self._tf_ticks[tf].append(tick)

            # Expire old ticks
            cutoff = now - tf
            while self._tf_ticks[tf] and self._tf_ticks[tf][0].timestamp < cutoff:
                self._tf_ticks[tf].popleft()

            # Bar tracking
            if self._tf_bar_start[tf] == 0.0:
                self._tf_bar_start[tf] = now - (now % tf)

            bar_end = self._tf_bar_start[tf] + tf
            if now >= bar_end:
                # Close bar, record delta
                bar_delta = self._tf_bar_buy[tf] - self._tf_bar_sell[tf]
                self._tf_bar_delta[tf].append(bar_delta)
                # Start new bar
                self._tf_bar_start[tf] = now - (now % tf)
                self._tf_bar_buy[tf] = 0
                self._tf_bar_sell[tf] = 0

            if tick.is_buy:
                self._tf_bar_buy[tf] += tick.size
            else:
                self._tf_bar_sell[tf] += tick.size

    def _tf_summary(self, tf: int) -> Dict[str, Any]:
        ticks = list(self._tf_ticks[tf])
        buy_vol = sum(t.size for t in ticks if t.is_buy)
        sell_vol = sum(t.size for t in ticks if t.is_sell)
        delta = buy_vol - sell_vol
        total = buy_vol + sell_vol
        delta_pct = delta / total if total > 0 else 0.0
        bar_deltas = list(self._tf_bar_delta[tf])
        return {
            "delta": delta,
            "volume": total,
            "buy_vol": buy_vol,
            "sell_vol": sell_vol,
            "delta_pct": round(delta_pct, 4),
            "delta_per_bar": bar_deltas,
            "label": self.TIMEFRAMES[tf],
        }

    def get_mtf_summary(self) -> Dict[str, Any]:
        """Return summary for all 3 timeframes plus alignment score."""
        result: Dict[str, Any] = {}
        directions: List[float] = []

        for tf, label in self.TIMEFRAMES.items():
            tf_data = self._tf_summary(tf)
            result[label] = tf_data
            dp = tf_data["delta_pct"]
            if dp > 0.05:
                directions.append(1.0)
            elif dp < -0.05:
                directions.append(-1.0)
            else:
                directions.append(0.0)

        bullish = directions.count(1.0)
        bearish = directions.count(-1.0)

        if bullish == 3:
            alignment_score = 1.0
        elif bullish == 2:
            alignment_score = 0.67
        elif bearish == 3:
            alignment_score = -1.0
        elif bearish == 2:
            alignment_score = -0.67
        elif bullish == 1 and bearish == 0:
            alignment_score = 0.33
        elif bearish == 1 and bullish == 0:
            alignment_score = -0.33
        else:
            alignment_score = 0.0

        result["alignment_score"] = alignment_score
        return result

    def reset(self):
        for tf in self.TIMEFRAMES:
            self._tf_ticks[tf].clear()
            self._tf_bar_delta[tf].clear()
            self._tf_bar_start[tf] = 0.0
            self._tf_bar_buy[tf] = 0
            self._tf_bar_sell[tf] = 0


# ---------------------------------------------------------------------------
# 8. AdvancedOrderFlowEngine  (main orchestrator)
# ---------------------------------------------------------------------------

class AdvancedOrderFlowEngine:
    """Orchestrates all advanced order flow detectors into a single interface.

    Usage:
        engine = AdvancedOrderFlowEngine(event_bus=bus)
        engine.process_tick(tick)
        signal = engine.get_composite_signal()

    If an EventBus is provided the engine subscribes to PRICE_UPDATE events
    and publishes ORDER_FLOW_UPDATE events after each tick.
    """

    SOURCE = "AdvancedOrderFlowEngine"

    def __init__(self, event_bus=None, tick_size: float = 0.25):
        self.big_trades = BigTradesDetector()
        self.institutional = InstitutionalFlowDetector()
        self.dom = DOMImbalanceAnalyzer()
        self.cum_delta = CumulativeDeltaAnalyzer()
        self.trade_speed = TradeSpeedAnalyzer()
        self.flow_classifier = AggressivePassiveClassifier()
        self.mtf = MultiTimeframeOrderFlow()

        self._event_bus = event_bus
        self._tick_size = tick_size

        self._recent_ticks: deque[Tick] = deque(maxlen=500)
        self._pending_alerts: List[Any] = []
        self._last_big_trade: Optional[BigTradeEvent] = None
        self._last_inst_signal: Optional[InstitutionalSignal] = None
        self._last_cd_alert: Optional[CDDivergenceAlert] = None

        if event_bus is not None:
            self._subscribe(event_bus)

    # ------------------------------------------------------------------
    def _subscribe(self, bus):
        """Subscribe to EventBus events."""
        try:
            from .event_bus import EventType
            bus.subscribe(EventType.PRICE_UPDATE, self._on_price_update)
        except Exception as exc:
            logger.warning("Could not subscribe to EventBus: %s", exc)

    def _on_price_update(self, event):
        """Handle PRICE_UPDATE events from the EventBus."""
        data = event.data
        try:
            tick = Tick(
                timestamp=data.get("timestamp", time.time()),
                price=float(data["price"]),
                size=int(data.get("size", 1)),
                aggressor=data.get("aggressor", "ASK"),
            )
            self.process_tick(tick)
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("Skipped malformed PRICE_UPDATE: %s", exc)

    # ------------------------------------------------------------------
    def process_tick(self, tick: Tick):
        """Feed a tick to all detectors and publish results."""
        self._recent_ticks.append(tick)
        self._pending_alerts.clear()

        # BigTradesDetector
        bt_event = self.big_trades.update(tick)
        if bt_event is not None:
            self._last_big_trade = bt_event
            self._pending_alerts.append(bt_event)

        # InstitutionalFlowDetector
        inst_sig = self.institutional.update(tick)
        if inst_sig is not None:
            self._last_inst_signal = inst_sig
            self._pending_alerts.append(inst_sig)

        # CumulativeDeltaAnalyzer
        cd_alert = self.cum_delta.update(tick)
        if cd_alert is not None:
            self._last_cd_alert = cd_alert
            self._pending_alerts.append(cd_alert)

        # TradeSpeedAnalyzer
        self.trade_speed.add_tick(tick)

        # AggressivePassiveClassifier
        self.flow_classifier.add_tick(tick)

        # MultiTimeframeOrderFlow
        self.mtf.update(tick)

        # Publish to EventBus if available
        if self._event_bus is not None:
            self._publish_update()

    def _publish_update(self):
        try:
            from .event_bus import Event, EventType
            signal = self.get_composite_signal()
            evt = Event(
                type=EventType.ORDER_FLOW_UPDATE,
                data={
                    "direction": signal.direction,
                    "confidence": signal.confidence,
                    "components": signal.components,
                    "alert_count": len(signal.alerts),
                },
                source=self.SOURCE,
            )
            self._event_bus.publish(evt)

            # Publish big trade alert if one fired this tick
            if self._last_big_trade is not None and self._pending_alerts:
                for alert in self._pending_alerts:
                    if isinstance(alert, BigTradeEvent):
                        big_evt = Event(
                            type=EventType.BIG_TRADE_ALERT,
                            data={
                                "price": alert.price,
                                "size": alert.size,
                                "side": alert.side,
                                "classification": alert.classification,
                                "multiplier": alert.multiplier,
                            },
                            source=self.SOURCE,
                        )
                        self._event_bus.publish(big_evt)

            # Publish institutional flow signal
            for alert in self._pending_alerts:
                if isinstance(alert, InstitutionalSignal):
                    inst_evt = Event(
                        type=EventType.INSTITUTIONAL_FLOW,
                        data={
                            "pattern_type": alert.pattern_type,
                            "confidence": alert.confidence,
                            "side": alert.side,
                            "estimated_size": alert.estimated_size,
                        },
                        source=self.SOURCE,
                    )
                    self._event_bus.publish(inst_evt)

        except Exception as exc:
            logger.debug("EventBus publish error: %s", exc)

    # ------------------------------------------------------------------
    def get_composite_signal(self) -> OrderFlowSignal:
        """Combine all detector outputs into a single directional signal.

        Returns OrderFlowSignal with direction in [-1, +1] and confidence
        in [0, 1].
        """
        components: Dict[str, Any] = {}
        scores: List[float] = []
        weights: List[float] = []

        # --- Cumulative delta ---
        cd = self.cum_delta.cumulative_delta
        session_vol = sum(
            t.size for t in self._recent_ticks
        ) or 1
        cd_norm = max(-1.0, min(1.0, cd / (session_vol * 0.5 + 1)))
        scores.append(cd_norm)
        weights.append(0.30)
        components["cum_delta"] = cd
        components["cd_norm"] = round(cd_norm, 4)

        # --- MTF alignment ---
        mtf = self.mtf.get_mtf_summary()
        alignment_score = mtf.get("alignment_score", 0.0)
        scores.append(alignment_score)
        weights.append(0.25)
        components["mtf_alignment"] = alignment_score
        components["mtf"] = {k: v for k, v in mtf.items() if k != "alignment_score"}

        # --- Aggressive flow ---
        flow_sum = self.flow_classifier.get_flow_summary(window_ticks=200)
        agg_net = flow_sum.aggressive_buy_vol - flow_sum.aggressive_sell_vol
        agg_total = flow_sum.aggressive_buy_vol + flow_sum.aggressive_sell_vol + 1
        agg_score = max(-1.0, min(1.0, agg_net / agg_total))
        scores.append(agg_score)
        weights.append(0.20)
        components["aggressive_buy_vol"] = flow_sum.aggressive_buy_vol
        components["aggressive_sell_vol"] = flow_sum.aggressive_sell_vol
        components["aggression_ratio"] = flow_sum.aggression_ratio
        components["dominant_flow"] = flow_sum.dominant_flow

        # --- Trade speed / algo detection ---
        recent_list = list(self._recent_ticks)[-50:]
        flow_class = self.trade_speed.classify_flow(recent_list)
        # Algo activity itself is directionally neutral but reduces confidence
        algo_penalty = flow_class.algo_confidence * 0.15
        components["flow_pattern"] = flow_class.pattern
        components["algo_confidence"] = flow_class.algo_confidence
        components["trades_per_sec"] = flow_class.trades_per_sec

        # --- Big trades ---
        big_buy = sum(
            1 for bt in self.big_trades.recent_big_trades
            if bt.side == "BUY" and (time.time() - bt.timestamp) < 60
        )
        big_sell = sum(
            1 for bt in self.big_trades.recent_big_trades
            if bt.side == "SELL" and (time.time() - bt.timestamp) < 60
        )
        big_net = big_buy - big_sell
        big_total = big_buy + big_sell
        if big_total > 0:
            big_score = max(-1.0, min(1.0, big_net / big_total))
            scores.append(big_score)
            weights.append(0.15)
        components["big_buy_count"] = big_buy
        components["big_sell_count"] = big_sell

        # --- Institutional signals ---
        recent_inst = self.institutional.recent_signals[-5:] if self.institutional.recent_signals else []
        inst_score = 0.0
        if recent_inst:
            for sig in recent_inst:
                direction_val = 1.0 if sig.side == "BUY" else -1.0 if sig.side == "SELL" else 0.0
                inst_score += direction_val * sig.confidence
            inst_score = max(-1.0, min(1.0, inst_score / len(recent_inst)))
            scores.append(inst_score)
            weights.append(0.10)
        components["inst_score"] = round(inst_score, 4)

        # --- Weighted direction ---
        if not scores:
            direction = 0.0
            raw_confidence = 0.0
        else:
            total_weight = sum(weights[:len(scores)])
            direction = sum(s * w for s, w in zip(scores, weights)) / max(total_weight, 1e-9)
            direction = max(-1.0, min(1.0, direction))
            # Confidence: agreement among components
            agreement = sum(1 for s in scores if s * direction > 0) / len(scores)
            raw_confidence = agreement * abs(direction)

        confidence = max(0.0, min(1.0, raw_confidence - algo_penalty))

        # --- Collect active alerts ---
        alerts = list(self._pending_alerts) + list(self.dom.detect_spoofing())

        return OrderFlowSignal(
            direction=round(direction, 4),
            confidence=round(confidence, 4),
            components=components,
            alerts=alerts,
        )

    # ------------------------------------------------------------------
    def update_dom(self, bids: List[PriceLevel], asks: List[PriceLevel],
                   timestamp: Optional[float] = None):
        """Forward DOM snapshot to the DOMImbalanceAnalyzer."""
        self.dom.update_snapshot(bids, asks, timestamp)

    def reset(self):
        """Reset all detectors to a clean state."""
        self.big_trades.reset()
        self.institutional.reset()
        self.dom.reset()
        self.cum_delta.reset()
        self.trade_speed.reset()
        self.flow_classifier.reset()
        self.mtf.reset()
        self._recent_ticks.clear()
        self._pending_alerts.clear()
        self._last_big_trade = None
        self._last_inst_signal = None
        self._last_cd_alert = None
