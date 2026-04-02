"""Big Trades Indicator — ATAS-style large trade detection with absorption/breakout signals.

Detects unusually large trades hitting the tape in real-time. Uses dynamic
thresholds based on rolling average trade size. Tracks absorption (large trades
hitting a level but price holds) vs breakout (wave of aggressive orders sweeping
through a level).

Feeds into the Signal Engine as a high-weight confluence input.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from ..event_bus import EventBus, Event, EventType
from ..orderflow import Tick

log = logging.getLogger(__name__)


@dataclass
class BigTrade:
    """A single detected big trade."""
    timestamp: float
    price: float
    size: int
    is_buy: bool
    multiplier: float  # how many x the rolling average

    @property
    def side(self) -> str:
        return "BUY" if self.is_buy else "SELL"


@dataclass
class PriceLevelBigTrades:
    """Aggregated big trade activity at a single price level."""
    price: float
    big_buys: int = 0
    big_sells: int = 0
    big_buy_volume: int = 0
    big_sell_volume: int = 0
    last_activity: float = 0.0
    trades: list[BigTrade] = field(default_factory=list)

    @property
    def big_delta(self) -> int:
        return self.big_buy_volume - self.big_sell_volume

    @property
    def total_big_volume(self) -> int:
        return self.big_buy_volume + self.big_sell_volume

    @property
    def big_count(self) -> int:
        return self.big_buys + self.big_sells


@dataclass
class AbsorptionSignal:
    """Signal when big trades are absorbed at a level."""
    timestamp: float
    price: float
    direction: str  # "BULLISH" (sells absorbed) or "BEARISH" (buys absorbed)
    volume_absorbed: int
    duration_sec: float
    confidence: float
    description: str


@dataclass
class BreakoutSignal:
    """Signal when big trades sweep through a level."""
    timestamp: float
    price: float
    direction: str  # "BULLISH" (buys sweeping up) or "BEARISH" (sells sweeping down)
    volume_swept: int
    velocity: float  # big trades per second
    confidence: float
    description: str


class BigTradesEngine:
    """Detects and tracks unusually large trades in real-time.

    Dynamic threshold: trades > multiplier × rolling_avg_size are flagged.
    Tracks per-level big trade activity for absorption/breakout detection.
    """

    def __init__(self, bus: EventBus,
                 threshold_multiplier: float = 3.0,
                 rolling_window: int = 5000,
                 absorption_window_sec: float = 30.0,
                 breakout_window_sec: float = 15.0,
                 tick_size: float = 0.25,
                 time_decay_sec: float = 300.0):
        self.bus = bus
        self.threshold_multiplier = threshold_multiplier
        self.rolling_window = rolling_window
        self.absorption_window_sec = absorption_window_sec
        self.breakout_window_sec = breakout_window_sec
        self.tick_size = tick_size
        self.time_decay_sec = time_decay_sec

        # Rolling trade sizes for dynamic threshold
        self._recent_sizes: deque[int] = deque(maxlen=rolling_window)
        self._rolling_sum: int = 0

        # Big trade tracking
        self._big_trades: deque[BigTrade] = deque(maxlen=10000)
        self._level_activity: dict[float, PriceLevelBigTrades] = defaultdict(
            lambda: PriceLevelBigTrades(price=0.0)
        )
        self._recent_big_trades: deque[BigTrade] = deque(maxlen=500)

        # Session stats
        self.session_big_buys: int = 0
        self.session_big_sells: int = 0
        self.session_big_buy_volume: int = 0
        self.session_big_sell_volume: int = 0

        # Price tracking for absorption detection
        self._price_at_big_trade: dict[float, list[tuple[float, float]]] = defaultdict(list)
        self._last_price: float = 0.0

        log.info("Big Trades Engine initialized (threshold=%.1fx, window=%d)",
                 threshold_multiplier, rolling_window)

    def _round_price(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 2)

    @property
    def rolling_avg_size(self) -> float:
        if not self._recent_sizes:
            return 1.0
        return self._rolling_sum / len(self._recent_sizes)

    @property
    def big_trade_threshold(self) -> float:
        return self.rolling_avg_size * self.threshold_multiplier

    @property
    def session_big_delta(self) -> int:
        return self.session_big_buy_volume - self.session_big_sell_volume

    @property
    def institutional_participation_rate(self) -> float:
        total = self.session_big_buy_volume + self.session_big_sell_volume
        if not self._recent_sizes:
            return 0.0
        total_volume = self._rolling_sum
        return total / max(total_volume, 1)

    def process_tick(self, tick: Tick) -> Optional[BigTrade]:
        """Process a trade tick. Returns BigTrade if this tick is a big trade."""
        size = tick.size
        price = self._round_price(tick.price)
        self._last_price = price

        # Update rolling average
        if len(self._recent_sizes) == self.rolling_window:
            self._rolling_sum -= self._recent_sizes[0]
        self._recent_sizes.append(size)
        self._rolling_sum += size

        # Check if big trade
        threshold = self.big_trade_threshold
        if size < threshold or len(self._recent_sizes) < 100:
            return None

        multiplier = size / max(self.rolling_avg_size, 1)
        big = BigTrade(
            timestamp=tick.timestamp,
            price=price,
            size=size,
            is_buy=tick.is_buy,
            multiplier=multiplier,
        )

        self._record_big_trade(big)
        self._check_signals(big)
        return big

    def _record_big_trade(self, big: BigTrade):
        """Record a big trade in all tracking structures."""
        self._big_trades.append(big)
        self._recent_big_trades.append(big)

        price = self._round_price(big.price)
        level = self._level_activity[price]
        level.price = price
        level.last_activity = big.timestamp
        level.trades.append(big)

        if big.is_buy:
            level.big_buys += 1
            level.big_buy_volume += big.size
            self.session_big_buys += 1
            self.session_big_buy_volume += big.size
        else:
            level.big_sells += 1
            level.big_sell_volume += big.size
            self.session_big_sells += 1
            self.session_big_sell_volume += big.size

        # Track price at time of big trade for absorption detection
        self._price_at_big_trade[price].append((big.timestamp, self._last_price))

        # Publish big trade event
        self.bus.publish(Event(
            type=EventType.BIG_TRADE_ALERT,
            source="big_trades",
            data={
                "price": big.price,
                "size": big.size,
                "side": big.side,
                "multiplier": big.multiplier,
                "timestamp": big.timestamp,
                "level_big_delta": level.big_delta,
                "level_big_count": level.big_count,
                "session_big_delta": self.session_big_delta,
            },
            priority=5 if big.multiplier > 5 else 2,
        ))

    def _check_signals(self, big: BigTrade):
        """Check for absorption or breakout patterns after a big trade."""
        now = big.timestamp
        price = self._round_price(big.price)

        # --- Absorption Detection ---
        self._check_absorption(price, now)

        # --- Breakout Detection ---
        self._check_breakout(now)

    def _check_absorption(self, price: float, now: float):
        """Check if big trades at a level are being absorbed (price holds)."""
        level = self._level_activity.get(price)
        if not level or level.big_count < 3:
            return

        # Get recent big trades at this level within absorption window
        recent = [t for t in level.trades
                  if now - t.timestamp <= self.absorption_window_sec]

        if len(recent) < 3:
            return

        # Check if sells are being absorbed (bullish)
        sell_vol = sum(t.size for t in recent if not t.is_buy)
        buy_vol = sum(t.size for t in recent if t.is_buy)
        first_ts = recent[0].timestamp
        duration = now - first_ts

        # Price didn't drop despite heavy selling → absorption by resting buy orders
        price_move = self._last_price - price
        if sell_vol > buy_vol * 1.5 and sell_vol > self.big_trade_threshold * 3:
            if abs(price_move) <= self.tick_size * 4:  # price held within 1 point
                confidence = min(0.95, 0.5 + (sell_vol / (sell_vol + buy_vol)) * 0.3
                                 + (len(recent) / 10) * 0.2)
                sig = AbsorptionSignal(
                    timestamp=now, price=price, direction="BULLISH",
                    volume_absorbed=sell_vol, duration_sec=duration,
                    confidence=confidence,
                    description=(
                        f"ABSORPTION at {price:.2f} — {sell_vol} contracts sold "
                        f"in {duration:.0f}s, price holding. Buyers absorbing."
                    ),
                )
                self._publish_absorption(sig)
                return

        # Price didn't rise despite heavy buying → absorption by resting sell orders
        if buy_vol > sell_vol * 1.5 and buy_vol > self.big_trade_threshold * 3:
            if abs(price_move) <= self.tick_size * 4:
                confidence = min(0.95, 0.5 + (buy_vol / (sell_vol + buy_vol)) * 0.3
                                 + (len(recent) / 10) * 0.2)
                sig = AbsorptionSignal(
                    timestamp=now, price=price, direction="BEARISH",
                    volume_absorbed=buy_vol, duration_sec=duration,
                    confidence=confidence,
                    description=(
                        f"ABSORPTION at {price:.2f} — {buy_vol} contracts bought "
                        f"in {duration:.0f}s, price holding. Sellers absorbing."
                    ),
                )
                self._publish_absorption(sig)

    def _check_breakout(self, now: float):
        """Check for burst of big trades in one direction (breakout/momentum)."""
        recent = [t for t in self._recent_big_trades
                  if now - t.timestamp <= self.breakout_window_sec]

        if len(recent) < 3:
            return

        buy_vol = sum(t.size for t in recent if t.is_buy)
        sell_vol = sum(t.size for t in recent if not t.is_buy)
        total = buy_vol + sell_vol
        duration = now - recent[0].timestamp
        velocity = len(recent) / max(duration, 0.1)

        # Bullish breakout — overwhelming buy pressure
        if buy_vol > sell_vol * 2.5 and total > self.big_trade_threshold * 5:
            confidence = min(0.95, 0.5 + (buy_vol / total) * 0.3 + min(velocity / 5, 0.2))
            prices = [t.price for t in recent if t.is_buy]
            sweep_price = max(prices) if prices else self._last_price
            sig = BreakoutSignal(
                timestamp=now, price=sweep_price, direction="BULLISH",
                volume_swept=buy_vol, velocity=velocity,
                confidence=confidence,
                description=(
                    f"BREAKOUT CONFIRMED at {sweep_price:.2f} — {buy_vol} aggressive "
                    f"buy contracts in {duration:.0f}s, sweeping through resistance"
                ),
            )
            self._publish_breakout(sig)
            return

        # Bearish breakout — overwhelming sell pressure
        if sell_vol > buy_vol * 2.5 and total > self.big_trade_threshold * 5:
            confidence = min(0.95, 0.5 + (sell_vol / total) * 0.3 + min(velocity / 5, 0.2))
            prices = [t.price for t in recent if not t.is_buy]
            sweep_price = min(prices) if prices else self._last_price
            sig = BreakoutSignal(
                timestamp=now, price=sweep_price, direction="BEARISH",
                volume_swept=sell_vol, velocity=velocity,
                confidence=confidence,
                description=(
                    f"BREAKOUT CONFIRMED at {sweep_price:.2f} — {sell_vol} aggressive "
                    f"sell contracts in {duration:.0f}s, sweeping through support"
                ),
            )
            self._publish_breakout(sig)

    def _publish_absorption(self, sig: AbsorptionSignal):
        log.info("BIG TRADES: %s", sig.description)
        self.bus.publish(Event(
            type=EventType.BIG_TRADE_ALERT,
            source="big_trades",
            data={
                "signal_type": "absorption",
                "direction": sig.direction,
                "price": sig.price,
                "volume": sig.volume_absorbed,
                "duration": sig.duration_sec,
                "confidence": sig.confidence,
                "description": sig.description,
            },
            priority=8,
        ))

    def _publish_breakout(self, sig: BreakoutSignal):
        log.info("BIG TRADES: %s", sig.description)
        self.bus.publish(Event(
            type=EventType.BIG_TRADE_ALERT,
            source="big_trades",
            data={
                "signal_type": "breakout",
                "direction": sig.direction,
                "price": sig.price,
                "volume": sig.volume_swept,
                "velocity": sig.velocity,
                "confidence": sig.confidence,
                "description": sig.description,
            },
            priority=8,
        ))

    # --- Query Methods ---

    def get_big_trades_at_level(self, price: float) -> PriceLevelBigTrades:
        price = self._round_price(price)
        return self._level_activity.get(price, PriceLevelBigTrades(price=price))

    def get_hottest_levels(self, n: int = 10) -> list[PriceLevelBigTrades]:
        """Top N price levels by big trade activity."""
        levels = [lv for lv in self._level_activity.values() if lv.big_count > 0]
        return sorted(levels, key=lambda lv: -lv.total_big_volume)[:n]

    def get_recent_big_trades(self, n: int = 50) -> list[BigTrade]:
        return list(self._recent_big_trades)[-n:]

    def get_session_stats(self) -> dict:
        return {
            "big_buys": self.session_big_buys,
            "big_sells": self.session_big_sells,
            "big_buy_volume": self.session_big_buy_volume,
            "big_sell_volume": self.session_big_sell_volume,
            "big_delta": self.session_big_delta,
            "threshold": self.big_trade_threshold,
            "rolling_avg": self.rolling_avg_size,
            "participation_rate": self.institutional_participation_rate,
            "hottest_levels": [
                {"price": lv.price, "big_delta": lv.big_delta,
                 "big_volume": lv.total_big_volume, "count": lv.big_count}
                for lv in self.get_hottest_levels(5)
            ],
        }

    def get_big_trade_heatmap(self) -> list[dict]:
        """Returns list of {price, big_buy_vol, big_sell_vol, big_delta} for heatmap."""
        result = []
        for price in sorted(self._level_activity.keys()):
            lv = self._level_activity[price]
            if lv.big_count > 0:
                result.append({
                    "price": lv.price,
                    "big_buy_vol": lv.big_buy_volume,
                    "big_sell_vol": lv.big_sell_volume,
                    "big_delta": lv.big_delta,
                    "count": lv.big_count,
                })
        return result

    def reset_session(self):
        """Reset for new session."""
        self._big_trades.clear()
        self._recent_big_trades.clear()
        self._level_activity.clear()
        self._price_at_big_trade.clear()
        self.session_big_buys = 0
        self.session_big_sells = 0
        self.session_big_buy_volume = 0
        self.session_big_sell_volume = 0
        log.info("Big Trades Engine session reset")
