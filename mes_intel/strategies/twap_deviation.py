"""TWAP Deviation Strategy — detects institutional TWAP execution patterns."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import List

from .base import Strategy, StrategyResult


class TWAPDeviationStrategy(Strategy):
    """Detects institutional TWAP execution by measuring price deviation from
    time-weighted average price and identifying regular-interval order clustering.

    Institutions executing large TWAP orders distribute fills evenly over time.
    When price strays far from TWAP, the algorithm is accumulating there, making
    a mean-reversion back toward TWAP a high-probability trade.
    """

    name = "twap_deviation"
    description = "Institutional TWAP execution pattern detector"

    # --- tuneable parameters ------------------------------------------------
    TWAP_BARS = 20          # bars used to calculate TWAP
    DEV_THRESHOLD = 0.15    # % deviation that suggests TWAP activity
    ATR_BARS = 14           # bars for ATR calculation
    ATR_MULTIPLIER = 2.0    # stop distance in ATR units
    BUCKET_SECONDS = 30     # bucket width for regularity scoring

    def required_data(self) -> list[str]:
        return ["price", "price_history", "volume_history", "ticks", "spread", "vwap"]

    # --- helpers ------------------------------------------------------------

    def _twap(self, price_history: List[float]) -> float:
        window = price_history[-self.TWAP_BARS:]
        return statistics.mean(window) if window else price_history[-1]

    def _atr(self, price_history: List[float]) -> float:
        """Approximate ATR using consecutive price differences."""
        window = price_history[-(self.ATR_BARS + 1):]
        if len(window) < 2:
            return 1.0
        ranges = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
        return statistics.mean(ranges) if ranges else 1.0

    def _tick_regularity_score(self, ticks) -> float:
        """Score 0-1: how evenly distributed ticks are across 30s time buckets.

        A score near 1.0 means ticks arrive at a machine-regular cadence —
        a hallmark of algorithmic TWAP child orders.
        """
        if not ticks or len(ticks) < 5:
            return 0.0

        bucket_counts: dict[int, int] = defaultdict(int)
        try:
            t0 = ticks[0].timestamp
            for tick in ticks:
                bucket = int((tick.timestamp - t0) / self.BUCKET_SECONDS)
                bucket_counts[bucket] += 1
        except AttributeError:
            return 0.0

        counts = list(bucket_counts.values())
        if len(counts) < 2:
            return 0.0

        mean_c = statistics.mean(counts)
        if mean_c == 0:
            return 0.0
        stdev_c = statistics.pstdev(counts)
        cv = stdev_c / mean_c          # coefficient of variation
        # Low CV (< 0.3) → very regular → high score
        return max(0.0, min(1.0, 1.0 - cv / 0.5))

    def _volume_consistency_score(self, volume_history: List[float]) -> float:
        """Score 0-1: how consistent recent volumes are (low CV = consistent)."""
        window = volume_history[-self.TWAP_BARS:]
        if len(window) < 2:
            return 0.0
        mean_v = statistics.mean(window)
        if mean_v == 0:
            return 0.0
        cv = statistics.pstdev(window) / mean_v
        return max(0.0, min(1.0, 1.0 - cv / 0.5))

    def _spread_score(self, spread: float, price: float) -> float:
        """Score 0-1: tighter spread relative to price = more liquid = higher confidence."""
        if price == 0:
            return 0.0
        spread_bps = (spread / price) * 10_000
        # MES typical spread ~0.25 pts ≈ 1–2 bps; generous upper bound = 5 bps
        return max(0.0, min(1.0, 1.0 - spread_bps / 5.0))

    # --- main ---------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        price_history: List[float] = market_data.get("price_history", [])
        volume_history: List[float] = market_data.get("volume_history", [])
        ticks = market_data.get("ticks", [])
        spread: float = market_data.get("spread", 0.25)

        # Guard: need enough history
        if len(price_history) < self.TWAP_BARS or price == 0:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient_history"}
            )

        twap = self._twap(price_history)
        deviation_pct = (price - twap) / twap * 100.0 if twap else 0.0
        abs_dev = abs(deviation_pct)

        # No signal if deviation is too small
        if abs_dev < self.DEV_THRESHOLD:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"twap": round(twap, 2), "deviation_pct": round(deviation_pct, 4),
                      "reason": "deviation_too_small"}
            )

        # Score magnitude: how far from TWAP (capped at 0.5%)
        deviation_score = min(abs_dev / 0.50, 1.0)

        # Sub-scores
        tick_reg = self._tick_regularity_score(ticks)
        vol_con = self._volume_consistency_score(volume_history)
        spread_sc = self._spread_score(spread, price)

        # Direction: if price is BELOW twap, institution is buying below → we go LONG
        # (price will revert up toward TWAP)
        direction = "LONG" if deviation_pct < 0 else "SHORT"
        sign = 1.0 if direction == "LONG" else -1.0

        # Composite score
        score = sign * deviation_score * (0.5 + 0.3 * tick_reg + 0.2 * vol_con)
        score = max(-1.0, min(1.0, score))

        # Confidence
        confidence = (
            0.4 * deviation_score
            + 0.3 * tick_reg
            + 0.2 * vol_con
            + 0.1 * spread_sc
        )
        confidence = max(0.0, min(1.0, confidence))

        # Risk levels
        atr = self._atr(price_history)
        if direction == "LONG":
            stop_price = price - self.ATR_MULTIPLIER * atr
            target_price = twap
        else:
            stop_price = price + self.ATR_MULTIPLIER * atr
            target_price = twap

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=price,
            stop_price=round(stop_price, 2),
            target_price=round(target_price, 2),
            meta={
                "twap": round(twap, 2),
                "deviation_pct": round(deviation_pct, 4),
                "atr": round(atr, 4),
                "tick_regularity": round(tick_reg, 4),
                "volume_consistency": round(vol_con, 4),
                "spread_score": round(spread_sc, 4),
            },
        )
