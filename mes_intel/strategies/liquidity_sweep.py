"""Liquidity Sweep Strategy — detects stop hunts and trades the reversal."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .base import Strategy, StrategyResult


@dataclass
class SwingLevel:
    price: float
    bar_index: int      # index in price_history
    kind: str           # 'high' or 'low'


class LiquiditySweepStrategy(Strategy):
    """Identifies liquidity pools (swing highs/lows), detects when price
    sweeps through them, then fades the move back inside.

    The logic is that retail stop orders cluster at visible swing levels.
    Institutions sweep these levels to fill their own orders at better prices,
    then reverse hard. Trading with the reversal — after the sweep is
    confirmed — captures the imbalance left behind.
    """

    name = "liquidity_sweep"
    description = "Stop hunt / liquidity sweep detector with reversal signal"

    # --- tuneable parameters ------------------------------------------------
    SWING_LOOKBACK = 50         # bars to find swing highs/lows
    SWING_STRENGTH = 3          # bars each side to confirm a swing
    MES_TICK = 0.25             # 1 MES tick = 0.25 index points
    MIN_SWEEP_TICKS = 1         # price must pierce level by this many ticks
    MAX_REVERSAL_BARS = 3       # sweep must close back inside within this many bars
    VOLUME_SPIKE_RATIO = 1.5    # sweep bar volume must be > ratio * avg
    FAILED_AUCTION_BARS = 2     # max bars beyond level for "failed auction"
    ATR_BARS = 14

    def required_data(self) -> list[str]:
        return [
            "price", "high", "low", "open",
            "price_history", "volume_history", "delta_history",
            "volume", "delta",
            "session_high", "session_low",
        ]

    # --- helpers ------------------------------------------------------------

    def _find_swings(self, price_history: List[float]) -> Tuple[List[SwingLevel], List[SwingLevel]]:
        """Find swing highs and lows in the lookback window.

        A swing high at index i: price[i] is the highest among i±SWING_STRENGTH bars.
        """
        window = price_history[-self.SWING_LOOKBACK:]
        s = self.SWING_STRENGTH
        highs: List[SwingLevel] = []
        lows: List[SwingLevel] = []

        for i in range(s, len(window) - s):
            left = window[i - s: i]
            right = window[i + 1: i + s + 1]
            val = window[i]
            if all(val > v for v in left) and all(val > v for v in right):
                highs.append(SwingLevel(price=val, bar_index=i, kind="high"))
            elif all(val < v for v in left) and all(val < v for v in right):
                lows.append(SwingLevel(price=val, bar_index=i, kind="low"))

        return highs, lows

    def _avg_volume(self, volume_history: List[float]) -> float:
        window = volume_history[-self.ATR_BARS:]
        return statistics.mean(window) if window else 1.0

    def _atr(self, price_history: List[float]) -> float:
        window = price_history[-(self.ATR_BARS + 1):]
        if len(window) < 2:
            return 1.0
        ranges = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
        return statistics.mean(ranges) if ranges else 1.0

    def _detect_high_sweep(
        self,
        highs: List[SwingLevel],
        price_history: List[float],
        volume_history: List[float],
        delta_history: List[float],
        current_high: float,
        current_close: float,
        current_volume: float,
        current_delta: float,
    ) -> Optional[Tuple[SwingLevel, float]]:
        """Check if current bar sweeps a swing high and then closes back below.

        Returns (swept_level, confidence) or None.
        """
        if not highs:
            return None

        avg_vol = self._avg_volume(volume_history)

        for level in reversed(highs):  # most recent first
            sweep_price = level.price + self.MIN_SWEEP_TICKS * self.MES_TICK
            # Must pierce the level
            if current_high < sweep_price:
                continue
            # Must close back below the level (reversal)
            if current_close >= level.price:
                continue

            # Volume spike check
            vol_ratio = current_volume / avg_vol if avg_vol > 0 else 0.0
            if vol_ratio < self.VOLUME_SPIKE_RATIO:
                continue

            # Delta reversal: on a high sweep we expect selling (negative delta bar)
            # after an initial buy. Current bar delta should be negative or reversing.
            delta_reversal = 1.0 if current_delta < 0 else 0.3

            # Confidence components
            vol_score = min((vol_ratio - self.VOLUME_SPIKE_RATIO) / 2.0 + 0.5, 1.0)
            sweep_depth = (current_high - level.price) / (self.MES_TICK * 4)
            sweep_speed_score = min(sweep_depth, 1.0)

            confidence = (
                0.40 * vol_score
                + 0.35 * delta_reversal
                + 0.25 * sweep_speed_score
            )
            return level, confidence

        return None

    def _detect_low_sweep(
        self,
        lows: List[SwingLevel],
        price_history: List[float],
        volume_history: List[float],
        delta_history: List[float],
        current_low: float,
        current_close: float,
        current_volume: float,
        current_delta: float,
    ) -> Optional[Tuple[SwingLevel, float]]:
        """Check if current bar sweeps a swing low and then closes back above."""
        if not lows:
            return None

        avg_vol = self._avg_volume(volume_history)

        for level in reversed(lows):
            sweep_price = level.price - self.MIN_SWEEP_TICKS * self.MES_TICK
            if current_low > sweep_price:
                continue
            if current_close <= level.price:
                continue

            vol_ratio = current_volume / avg_vol if avg_vol > 0 else 0.0
            if vol_ratio < self.VOLUME_SPIKE_RATIO:
                continue

            # On low sweep expect buying (positive delta reversal)
            delta_reversal = 1.0 if current_delta > 0 else 0.3

            vol_score = min((vol_ratio - self.VOLUME_SPIKE_RATIO) / 2.0 + 0.5, 1.0)
            sweep_depth = (level.price - current_low) / (self.MES_TICK * 4)
            sweep_speed_score = min(sweep_depth, 1.0)

            confidence = (
                0.40 * vol_score
                + 0.35 * delta_reversal
                + 0.25 * sweep_speed_score
            )
            return level, confidence

        return None

    def _opposite_swing(
        self,
        direction: str,
        highs: List[SwingLevel],
        lows: List[SwingLevel],
    ) -> Optional[float]:
        """Return the nearest swing level on the opposite side as a target."""
        if direction == "LONG" and highs:
            return highs[-1].price
        if direction == "SHORT" and lows:
            return lows[-1].price
        return None

    # --- main ---------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        high: float = market_data.get("high", price)
        low: float = market_data.get("low", price)
        open_: float = market_data.get("open", price)
        price_history: List[float] = market_data.get("price_history", [])
        volume_history: List[float] = market_data.get("volume_history", [])
        delta_history: List[float] = market_data.get("delta_history", [])
        current_volume: float = market_data.get("volume", 0.0)
        current_delta: float = market_data.get("delta", 0.0)

        if len(price_history) < self.SWING_LOOKBACK:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient_history"}
            )

        swing_highs, swing_lows = self._find_swings(price_history)

        # --- check for high sweep (SHORT setup) -----------------------------
        high_result = self._detect_high_sweep(
            swing_highs, price_history, volume_history, delta_history,
            high, price, current_volume, current_delta,
        )

        # --- check for low sweep (LONG setup) --------------------------------
        low_result = self._detect_low_sweep(
            swing_lows, price_history, volume_history, delta_history,
            low, price, current_volume, current_delta,
        )

        # --- resolve if both trigger simultaneously (pick higher confidence) -
        sweep_level: Optional[SwingLevel] = None
        direction = "FLAT"
        raw_confidence = 0.0

        if high_result and low_result:
            if high_result[1] >= low_result[1]:
                sweep_level, raw_confidence = high_result
                direction = "SHORT"
            else:
                sweep_level, raw_confidence = low_result
                direction = "LONG"
        elif high_result:
            sweep_level, raw_confidence = high_result
            direction = "SHORT"
        elif low_result:
            sweep_level, raw_confidence = low_result
            direction = "LONG"

        if direction == "FLAT" or sweep_level is None:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={
                    "swing_highs_found": len(swing_highs),
                    "swing_lows_found": len(swing_lows),
                    "reason": "no_sweep_detected",
                }
            )

        score = raw_confidence if direction == "LONG" else -raw_confidence
        score = max(-1.0, min(1.0, score))

        # --- risk levels ----------------------------------------------------
        atr = self._atr(price_history)

        if direction == "LONG":
            # Stop just below the sweep extreme
            stop_price = low - self.MES_TICK
            target_level = self._opposite_swing("LONG", swing_highs, swing_lows)
            target_price = target_level if target_level else price + 2.0 * atr
        else:
            stop_price = high + self.MES_TICK
            target_level = self._opposite_swing("SHORT", swing_highs, swing_lows)
            target_price = target_level if target_level else price - 2.0 * atr

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(raw_confidence, 4),
            direction=direction,
            entry_price=price,
            stop_price=round(stop_price, 2),
            target_price=round(target_price, 2),
            meta={
                "swept_level": round(sweep_level.price, 2),
                "sweep_kind": sweep_level.kind,
                "swing_highs_found": len(swing_highs),
                "swing_lows_found": len(swing_lows),
                "avg_volume": round(self._avg_volume(volume_history), 1),
                "bar_volume": round(current_volume, 1),
                "bar_delta": round(current_delta, 1),
                "atr": round(atr, 4),
            },
        )
