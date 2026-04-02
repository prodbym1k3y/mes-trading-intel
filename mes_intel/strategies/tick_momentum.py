"""Tick Momentum Strategy — tick-by-tick momentum analysis."""
from __future__ import annotations

import statistics
from typing import List

from .base import Strategy, StrategyResult


class TickMomentumStrategy(Strategy):
    """Analyzes raw tick flow for momentum, exhaustion, and divergence signals.

    Tick data captures the microstructure of price discovery before it shows
    up in bars. A sustained uptick ratio above 0.7 in a short window is
    significantly more predictive than a single bar close.
    """

    name = "tick_momentum"
    description = "Tick-by-tick momentum, velocity, exhaustion, and divergence detector"

    # --- tuneable parameters ------------------------------------------------
    TICK_WINDOW = 20        # ticks for primary momentum calculation
    BURST_WINDOW = 20       # ticks for momentum burst detection
    BURST_THRESHOLD = 0.70  # % same-direction to call a burst
    LONG_THRESHOLD = 0.60   # uptick ratio for LONG signal
    SHORT_THRESHOLD = 0.40  # uptick ratio for SHORT signal
    VELOCITY_WINDOW = 10    # ticks for velocity calculation
    WILLIAMS_R_THRESHOLD = -80.0   # oversold
    WILLIAMS_R_OB = -20.0          # overbought

    def required_data(self) -> list[str]:
        return ["price", "ticks", "session_high", "session_low", "price_history"]

    # --- helpers ------------------------------------------------------------

    def _uptick_ratio(self, ticks, window: int) -> float:
        """Fraction of ticks in the last `window` that were upticks (price > prev)."""
        recent = ticks[-window:] if len(ticks) >= window else ticks
        if len(recent) < 2:
            return 0.5
        up = 0
        try:
            for i in range(1, len(recent)):
                if recent[i].price > recent[i - 1].price:
                    up += 1
        except AttributeError:
            return 0.5
        return up / (len(recent) - 1)

    def _tick_velocity(self, ticks) -> float:
        """Absolute price change per tick over recent window (normalized to [0,1]).

        High velocity after a prolonged trend can indicate momentum; declining
        velocity at extremes suggests exhaustion.
        """
        recent = ticks[-self.VELOCITY_WINDOW:] if len(ticks) >= self.VELOCITY_WINDOW else ticks
        if len(recent) < 2:
            return 0.0
        try:
            total_move = abs(recent[-1].price - recent[0].price)
        except AttributeError:
            return 0.0
        avg_move_per_tick = total_move / (len(recent) - 1)
        # Normalize: 0.25 pts/tick ≈ 1 MES tick = velocity score 1.0
        return min(avg_move_per_tick / 0.25, 1.0)

    def _tick_clustering(self, ticks) -> tuple[float, str]:
        """Return (cluster_score, direction): longest run of same-direction ticks.

        score in [0, 1], direction = 'up' or 'down'.
        """
        if not ticks or len(ticks) < 3:
            return 0.0, "flat"
        max_up_run = max_dn_run = cur_up = cur_dn = 0
        try:
            for i in range(1, len(ticks)):
                if ticks[i].price > ticks[i - 1].price:
                    cur_up += 1
                    cur_dn = 0
                elif ticks[i].price < ticks[i - 1].price:
                    cur_dn += 1
                    cur_up = 0
                max_up_run = max(max_up_run, cur_up)
                max_dn_run = max(max_dn_run, cur_dn)
        except AttributeError:
            return 0.0, "flat"

        if max_up_run >= max_dn_run:
            return min(max_up_run / 8.0, 1.0), "up"
        else:
            return min(max_dn_run / 8.0, 1.0), "down"

    def _exhaustion_score(self, ticks) -> float:
        """Detect slowing velocity after a strong move: early = 0, exhausted = 1."""
        if len(ticks) < self.VELOCITY_WINDOW * 2:
            return 0.0
        half = self.VELOCITY_WINDOW
        try:
            early_move = abs(ticks[-2 * half].price - ticks[-half].price)
            late_move = abs(ticks[-half].price - ticks[-1].price)
        except (AttributeError, IndexError):
            return 0.0
        if early_move == 0:
            return 0.0
        ratio = late_move / early_move   # < 1 = slowing = exhaustion
        return max(0.0, min(1.0, 1.0 - ratio))

    def _momentum_burst(self, ticks) -> tuple[bool, str]:
        """True if >BURST_THRESHOLD of last BURST_WINDOW ticks are same direction."""
        recent = ticks[-self.BURST_WINDOW:] if len(ticks) >= self.BURST_WINDOW else ticks
        if len(recent) < self.BURST_WINDOW // 2:
            return False, "flat"
        ratio = self._uptick_ratio(recent, len(recent))
        if ratio >= self.BURST_THRESHOLD:
            return True, "up"
        if ratio <= (1.0 - self.BURST_THRESHOLD):
            return True, "down"
        return False, "flat"

    def _tick_divergence(self, ticks, price_history: List[float]) -> float:
        """Detect tick divergence: price making new highs but uptick ratio falling.

        Returns [-1, +1]: +1 = bullish divergence (tick ratio rising on lower price),
        -1 = bearish divergence.
        """
        if len(price_history) < 10 or len(ticks) < self.TICK_WINDOW * 2:
            return 0.0

        mid = len(ticks) // 2
        ratio_early = self._uptick_ratio(ticks[:mid], min(mid, self.TICK_WINDOW))
        ratio_late = self._uptick_ratio(ticks[mid:], min(len(ticks) - mid, self.TICK_WINDOW))

        price_early = statistics.mean(price_history[-20:-10]) if len(price_history) >= 20 else price_history[0]
        price_late = statistics.mean(price_history[-10:])

        price_rising = price_late > price_early
        ratio_rising = ratio_late > ratio_early

        if price_rising and not ratio_rising:
            return -1.0 * min(abs(ratio_late - ratio_early) / 0.2, 1.0)  # bearish div
        if not price_rising and ratio_rising:
            return +1.0 * min(abs(ratio_late - ratio_early) / 0.2, 1.0)  # bullish div
        return 0.0

    def _williams_r(self, price: float, session_high: float, session_low: float) -> float:
        """Williams %R: 0 to -100. -100 = session low, 0 = session high."""
        rng = session_high - session_low
        if rng == 0:
            return -50.0
        return ((session_high - price) / rng) * -100.0

    # --- main ---------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        ticks = market_data.get("ticks", [])
        session_high: float = market_data.get("session_high", price)
        session_low: float = market_data.get("session_low", price)
        price_history: List[float] = market_data.get("price_history", [price])

        if not ticks or len(ticks) < 5:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient_ticks"}
            )

        uptick_ratio = self._uptick_ratio(ticks, self.TICK_WINDOW)
        velocity = self._tick_velocity(ticks)
        cluster_score, cluster_dir = self._tick_clustering(ticks)
        exhaustion = self._exhaustion_score(ticks)
        burst, burst_dir = self._momentum_burst(ticks)
        divergence = self._tick_divergence(ticks, price_history)
        wr = self._williams_r(price, session_high, session_low)

        # --- direction from uptick ratio ------------------------------------
        if uptick_ratio > self.LONG_THRESHOLD:
            raw_direction = "LONG"
            base_score = (uptick_ratio - 0.5) * 2.0     # maps [0.5,1.0] → [0,1]
        elif uptick_ratio < self.SHORT_THRESHOLD:
            raw_direction = "SHORT"
            base_score = -(0.5 - uptick_ratio) * 2.0    # maps [0,0.5] → [-1,0]
        else:
            raw_direction = "FLAT"
            base_score = 0.0

        # --- adjustments ----------------------------------------------------
        # Exhaustion reverses direction if strong enough
        if exhaustion > 0.7:
            base_score *= (1.0 - exhaustion)

        # Divergence adds counter-signal
        base_score += divergence * 0.3

        # Burst amplifies
        if burst:
            if burst_dir == "up":
                base_score = max(base_score, base_score * 1.2)
            else:
                base_score = min(base_score, base_score * 1.2)

        score = max(-1.0, min(1.0, base_score))

        # --- confidence -----------------------------------------------------
        deviation_from_neutral = abs(uptick_ratio - 0.5)
        confidence = (
            0.40 * min(deviation_from_neutral / 0.3, 1.0)
            + 0.25 * velocity
            + 0.15 * cluster_score
            + 0.10 * (1.0 - exhaustion)
            + 0.10 * (1.0 if burst else 0.0)
        )
        confidence = max(0.0, min(1.0, confidence))

        # Reclassify direction after adjustments
        if score > 0.15:
            direction = "LONG"
        elif score < -0.15:
            direction = "SHORT"
        else:
            direction = "FLAT"

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=price if direction != "FLAT" else None,
            meta={
                "uptick_ratio": round(uptick_ratio, 4),
                "tick_velocity": round(velocity, 4),
                "cluster_score": round(cluster_score, 4),
                "cluster_dir": cluster_dir,
                "exhaustion": round(exhaustion, 4),
                "momentum_burst": burst,
                "burst_dir": burst_dir,
                "divergence": round(divergence, 4),
                "williams_r": round(wr, 2),
            },
        )
