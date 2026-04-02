"""Order flow data structures for volume profile, delta profile, value area, and footprint charts.

Core data model:
  - Each trade tick has: price, size, aggressor side (bid/ask)
  - Ticks aggregate into PriceLevels (bid vol, ask vol, delta at each price)
  - PriceLevels form a VolumeProfile (POC, VAH, VAL, value area)
  - Time-bucketed PriceLevels form FootprintBars (footprint chart data)
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class Tick:
    """A single trade tick from Rithmic."""
    timestamp: float
    price: float
    size: int
    aggressor: str  # "BID" (sell) or "ASK" (buy)

    @property
    def is_buy(self) -> bool:
        return self.aggressor == "ASK"

    @property
    def is_sell(self) -> bool:
        return self.aggressor == "BID"


@dataclass
class PriceLevel:
    """Aggregated volume data at a single price level."""
    price: float
    bid_volume: int = 0   # volume traded at bid (selling pressure)
    ask_volume: int = 0   # volume traded at ask (buying pressure)

    @property
    def total_volume(self) -> int:
        return self.bid_volume + self.ask_volume

    @property
    def delta(self) -> int:
        """Positive = buying pressure, negative = selling pressure."""
        return self.ask_volume - self.bid_volume

    @property
    def delta_pct(self) -> float:
        total = self.total_volume
        if total == 0:
            return 0.0
        return self.delta / total

    def add_tick(self, tick: Tick):
        if tick.is_buy:
            self.ask_volume += tick.size
        else:
            self.bid_volume += tick.size


class VolumeProfile:
    """Volume profile with POC, value area (VAH/VAL), and delta profile.

    Maintains a dict of PriceLevel objects keyed by price.
    Tick size for MES is 0.25 points.
    """

    def __init__(self, tick_size: float = 0.25):
        self.tick_size = tick_size
        self.levels: dict[float, PriceLevel] = {}
        self.cumulative_delta: int = 0
        self._tick_count: int = 0
        self._start_time: float = time.time()

    def _round_price(self, price: float) -> float:
        """Round price to nearest tick."""
        return round(round(price / self.tick_size) * self.tick_size, 2)

    def add_tick(self, tick: Tick):
        """Add a trade tick to the profile."""
        price = self._round_price(tick.price)
        if price not in self.levels:
            self.levels[price] = PriceLevel(price=price)
        self.levels[price].add_tick(tick)
        self.cumulative_delta += tick.size if tick.is_buy else -tick.size
        self._tick_count += 1

    def add_trade(self, price: float, size: int, is_buy: bool):
        """Convenience method to add a trade without creating a Tick object."""
        price = self._round_price(price)
        if price not in self.levels:
            self.levels[price] = PriceLevel(price=price)
        if is_buy:
            self.levels[price].ask_volume += size
            self.cumulative_delta += size
        else:
            self.levels[price].bid_volume += size
            self.cumulative_delta -= size
        self._tick_count += 1

    @property
    def total_volume(self) -> int:
        return sum(lv.total_volume for lv in self.levels.values())

    @property
    def total_delta(self) -> int:
        return sum(lv.delta for lv in self.levels.values())

    @property
    def poc(self) -> Optional[float]:
        """Point of Control — price with highest volume."""
        if not self.levels:
            return None
        return max(self.levels.values(), key=lambda lv: lv.total_volume).price

    def value_area(self, pct: float = 0.40) -> tuple[Optional[float], Optional[float]]:
        """Calculate Value Area High and Value Area Low.

        The value area contains `pct` (default 40%) of total volume,
        centered around the POC. Returns (VAL, VAH).
        """
        if not self.levels:
            return None, None

        sorted_levels = sorted(self.levels.values(), key=lambda lv: lv.price)
        total_vol = self.total_volume
        target_vol = total_vol * pct

        # Find POC index
        poc_price = self.poc
        poc_idx = next(i for i, lv in enumerate(sorted_levels) if lv.price == poc_price)

        accumulated = sorted_levels[poc_idx].total_volume
        low_idx = poc_idx
        high_idx = poc_idx

        while accumulated < target_vol and (low_idx > 0 or high_idx < len(sorted_levels) - 1):
            # Look at the next level above and below, add the one with more volume
            vol_below = sorted_levels[low_idx - 1].total_volume if low_idx > 0 else -1
            vol_above = sorted_levels[high_idx + 1].total_volume if high_idx < len(sorted_levels) - 1 else -1

            if vol_below < 0 and vol_above < 0:
                break

            if vol_above >= vol_below:
                high_idx += 1
                accumulated += sorted_levels[high_idx].total_volume
            else:
                low_idx -= 1
                accumulated += sorted_levels[low_idx].total_volume

        return sorted_levels[low_idx].price, sorted_levels[high_idx].price

    @property
    def val(self) -> Optional[float]:
        """Value Area Low."""
        return self.value_area()[0]

    @property
    def vah(self) -> Optional[float]:
        """Value Area High."""
        return self.value_area()[1]

    def delta_profile(self) -> list[tuple[float, int]]:
        """Returns list of (price, delta) sorted by price."""
        return sorted(
            [(lv.price, lv.delta) for lv in self.levels.values()],
            key=lambda x: x[0]
        )

    def sorted_levels(self) -> list[PriceLevel]:
        """All price levels sorted by price ascending."""
        return sorted(self.levels.values(), key=lambda lv: lv.price)

    def top_volume_levels(self, n: int = 5) -> list[PriceLevel]:
        """Top N price levels by volume (high volume nodes)."""
        return sorted(self.levels.values(), key=lambda lv: -lv.total_volume)[:n]

    def low_volume_nodes(self, threshold_pct: float = 0.1) -> list[PriceLevel]:
        """Price levels with volume below threshold % of POC volume."""
        if not self.levels:
            return []
        poc_vol = max(lv.total_volume for lv in self.levels.values())
        threshold = poc_vol * threshold_pct
        return sorted(
            [lv for lv in self.levels.values() if lv.total_volume < threshold],
            key=lambda lv: lv.price
        )

    def to_dict(self) -> dict:
        """Serialize for storage/transport."""
        return {
            "tick_size": self.tick_size,
            "cumulative_delta": self.cumulative_delta,
            "poc": self.poc,
            "val": self.val,
            "vah": self.vah,
            "total_volume": self.total_volume,
            "total_delta": self.total_delta,
            "levels": {
                str(lv.price): {
                    "bid_vol": lv.bid_volume,
                    "ask_vol": lv.ask_volume,
                    "delta": lv.delta,
                    "total": lv.total_volume,
                }
                for lv in self.sorted_levels()
            },
        }

    def reset(self):
        """Clear all data."""
        self.levels.clear()
        self.cumulative_delta = 0
        self._tick_count = 0
        self._start_time = time.time()


class FootprintBar:
    """A single footprint bar — time-bucketed order flow data.

    Each bar covers a time period (e.g., 1 minute) and contains a VolumeProfile
    plus OHLC data.
    """

    def __init__(self, start_time: float, duration_sec: float = 60.0, tick_size: float = 0.25):
        self.start_time = start_time
        self.duration_sec = duration_sec
        self.end_time = start_time + duration_sec
        self.profile = VolumeProfile(tick_size=tick_size)

        self.open: Optional[float] = None
        self.high: Optional[float] = None
        self.low: Optional[float] = None
        self.close: Optional[float] = None

    def add_tick(self, tick: Tick):
        """Add a tick to this footprint bar."""
        self.profile.add_tick(tick)
        price = tick.price
        if self.open is None:
            self.open = price
        self.close = price
        if self.high is None or price > self.high:
            self.high = price
        if self.low is None or price < self.low:
            self.low = price

    @property
    def is_complete(self) -> bool:
        return time.time() >= self.end_time

    @property
    def volume(self) -> int:
        return self.profile.total_volume

    @property
    def delta(self) -> int:
        return self.profile.total_delta

    @property
    def is_bullish(self) -> bool:
        return self.close is not None and self.open is not None and self.close >= self.open

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time,
            "duration": self.duration_sec,
            "ohlc": [self.open, self.high, self.low, self.close],
            "volume": self.volume,
            "delta": self.delta,
            "profile": self.profile.to_dict(),
        }


class FootprintChart:
    """Manages a series of FootprintBars for rendering footprint charts."""

    def __init__(self, bar_duration_sec: float = 60.0, tick_size: float = 0.25, max_bars: int = 500):
        self.bar_duration = bar_duration_sec
        self.tick_size = tick_size
        self.max_bars = max_bars
        self.bars: list[FootprintBar] = []
        self._current_bar: Optional[FootprintBar] = None

    def add_tick(self, tick: Tick):
        """Route a tick to the appropriate bar, creating new bars as needed."""
        if self._current_bar is None or tick.timestamp >= self._current_bar.end_time:
            # Start a new bar
            bar_start = tick.timestamp - (tick.timestamp % self.bar_duration)
            self._current_bar = FootprintBar(
                start_time=bar_start,
                duration_sec=self.bar_duration,
                tick_size=self.tick_size,
            )
            self.bars.append(self._current_bar)
            if len(self.bars) > self.max_bars:
                self.bars = self.bars[-self.max_bars:]

        self._current_bar.add_tick(tick)

    @property
    def session_profile(self) -> VolumeProfile:
        """Aggregate profile across all bars (full session)."""
        session = VolumeProfile(tick_size=self.tick_size)
        for bar in self.bars:
            for lv in bar.profile.levels.values():
                price = lv.price
                if price not in session.levels:
                    session.levels[price] = PriceLevel(price=price)
                session.levels[price].bid_volume += lv.bid_volume
                session.levels[price].ask_volume += lv.ask_volume
            session.cumulative_delta += bar.profile.cumulative_delta
        return session

    @property
    def current_bar(self) -> Optional[FootprintBar]:
        return self._current_bar

    def recent_bars(self, n: int = 20) -> list[FootprintBar]:
        return self.bars[-n:]

    def cumulative_delta_series(self) -> list[tuple[float, int]]:
        """Returns (timestamp, cumulative_delta) for charting."""
        running = 0
        series = []
        for bar in self.bars:
            running += bar.delta
            series.append((bar.start_time, running))
        return series


class DeltaDivergenceDetector:
    """Detects divergences between price and cumulative delta.

    A bullish divergence: price makes lower low but delta makes higher low.
    A bearish divergence: price makes higher high but delta makes lower high.
    """

    def __init__(self, lookback: int = 10):
        self.lookback = lookback

    def check(self, bars: list[FootprintBar]) -> Optional[str]:
        """Returns 'bullish_divergence', 'bearish_divergence', or None."""
        if len(bars) < self.lookback:
            return None

        recent = bars[-self.lookback:]
        prices = [b.close for b in recent if b.close is not None]
        deltas = []
        running = 0
        for b in recent:
            running += b.delta
            deltas.append(running)

        if len(prices) < 3 or len(deltas) < 3:
            return None

        # Check last 3 swing points (simplified)
        price_trend = prices[-1] - prices[0]
        delta_trend = deltas[-1] - deltas[0]

        # Bullish: price falling but delta rising
        if price_trend < 0 and delta_trend > 0:
            return "bullish_divergence"
        # Bearish: price rising but delta falling
        if price_trend > 0 and delta_trend < 0:
            return "bearish_divergence"

        return None
