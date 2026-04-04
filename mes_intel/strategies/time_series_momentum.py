"""Time-Series Momentum Strategy — systematic trend following with position scaling.

Measures rolling returns across multiple lookback windows (5, 10, 20, 60 bars),
allocates based on direction and persistence, and scales position size by
signal strength. Learns trend persistence, regime shifts, and position scaling
rather than trying to predict price.

Key concepts:
- Multi-timeframe momentum vector (short/medium/long alignment)
- Momentum persistence: how long has the trend been running?
- Breakpoint detection: when does momentum flip?
- Position scaling by conviction (momentum strength * alignment)
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class TimeSeriesMomentumStrategy(Strategy):
    """Multi-timeframe time-series momentum with adaptive position scaling."""

    name = "ts_momentum"
    description = "Rolling returns across 5/10/20/60 bars, trend persistence, breakpoint detection, position scaling"

    # Lookback windows for momentum measurement
    WINDOWS = [5, 10, 20, 60]
    MIN_BARS = 60

    # Momentum thresholds
    WEAK_MOMENTUM = 0.0005      # 0.05% return — noise floor
    STRONG_MOMENTUM = 0.003     # 0.3% return — strong trend
    BREAKPOINT_SENSITIVITY = 3  # bars of opposing returns to flag breakpoint

    def required_data(self) -> list[str]:
        return ["price", "price_history", "volume_history", "delta_history", "regime"]

    # ------------------------------------------------------------------
    # Rolling returns
    # ------------------------------------------------------------------

    def _rolling_returns(self, prices: list[float]) -> dict[int, float]:
        """Compute rolling returns for each lookback window.

        Returns dict of {window: return_pct} where return is (P_now / P_lookback - 1).
        """
        result = {}
        current = prices[-1]
        for w in self.WINDOWS:
            if len(prices) > w:
                past = prices[-(w + 1)]
                if past > 0:
                    result[w] = (current / past) - 1.0
        return result

    # ------------------------------------------------------------------
    # Momentum alignment score
    # ------------------------------------------------------------------

    def _alignment_score(self, returns: dict[int, float]) -> tuple[float, int, int]:
        """Score how aligned the momentum vectors are across timeframes.

        Returns (alignment [-1,1], num_bullish, num_bearish).
        Full alignment = all timeframes agree → high conviction.
        Mixed = some up, some down → low conviction.
        """
        if not returns:
            return 0.0, 0, 0

        bullish = sum(1 for r in returns.values() if r > self.WEAK_MOMENTUM)
        bearish = sum(1 for r in returns.values() if r < -self.WEAK_MOMENTUM)
        total = len(returns)

        if total == 0:
            return 0.0, 0, 0

        if bullish == total:
            return 1.0, bullish, bearish
        elif bearish == total:
            return -1.0, bullish, bearish
        else:
            net = (bullish - bearish) / total
            return net, bullish, bearish

    # ------------------------------------------------------------------
    # Momentum persistence
    # ------------------------------------------------------------------

    def _momentum_persistence(self, prices: list[float]) -> tuple[int, str]:
        """Count how many consecutive bars the current trend has persisted.

        Returns (streak_length, direction).
        Long streaks = mature trend (either follow or watch for exhaustion).
        """
        if len(prices) < 3:
            return 0, "FLAT"

        streak = 0
        direction = "FLAT"

        for i in range(len(prices) - 1, 0, -1):
            diff = prices[i] - prices[i - 1]
            if diff > 0:
                if direction == "FLAT":
                    direction = "UP"
                if direction == "UP":
                    streak += 1
                else:
                    break
            elif diff < 0:
                if direction == "FLAT":
                    direction = "DOWN"
                if direction == "DOWN":
                    streak += 1
                else:
                    break
            else:
                break

        return streak, direction

    # ------------------------------------------------------------------
    # Breakpoint detection
    # ------------------------------------------------------------------

    def _detect_breakpoint(self, prices: list[float],
                            returns: dict[int, float]) -> tuple[bool, str]:
        """Detect if momentum is breaking (trend reversal starting).

        A breakpoint occurs when short-term momentum flips against longer-term.
        This is the early warning of a regime shift.

        Returns (is_breaking, description).
        """
        if len(returns) < 2:
            return False, ""

        sorted_windows = sorted(returns.keys())
        if len(sorted_windows) < 2:
            return False, ""

        short_ret = returns[sorted_windows[0]]
        long_ret = returns[sorted_windows[-1]]

        # Short-term flipped against long-term
        if (short_ret > self.WEAK_MOMENTUM and long_ret < -self.WEAK_MOMENTUM):
            return True, f"Bullish breakpoint: short-term ({sorted_windows[0]}b) up while long-term ({sorted_windows[-1]}b) still down"
        elif (short_ret < -self.WEAK_MOMENTUM and long_ret > self.WEAK_MOMENTUM):
            return True, f"Bearish breakpoint: short-term ({sorted_windows[0]}b) down while long-term ({sorted_windows[-1]}b) still up"

        # Check for recent flip in the shortest window
        if len(prices) > self.BREAKPOINT_SENSITIVITY + 2:
            recent = prices[-self.BREAKPOINT_SENSITIVITY:]
            prior = prices[-(self.BREAKPOINT_SENSITIVITY + 5):-self.BREAKPOINT_SENSITIVITY]

            if prior and recent:
                prior_dir = recent[0] - prior[0]
                recent_dir = recent[-1] - recent[0]
                if prior_dir * recent_dir < 0 and abs(recent_dir) > abs(prior_dir) * 0.5:
                    return True, "Momentum direction flip detected in recent bars"

        return False, ""

    # ------------------------------------------------------------------
    # Position scaling by conviction
    # ------------------------------------------------------------------

    def _position_scale(self, alignment: float, returns: dict[int, float],
                         streak: int) -> float:
        """Scale position size by conviction: alignment * magnitude * persistence.

        Returns scale factor [0, 1] where:
        - 0 = no position (conflicting signals)
        - 1 = maximum position (full alignment + strong returns + long streak)
        """
        if not returns:
            return 0.0

        # Magnitude: average absolute return across windows
        avg_magnitude = statistics.mean(abs(r) for r in returns.values())
        mag_score = min(1.0, avg_magnitude / self.STRONG_MOMENTUM)

        # Alignment
        align_score = abs(alignment)

        # Persistence: caps out around 10+ bars
        persist_score = min(1.0, streak / 10.0) if streak > 0 else 0.0

        # Combined: multiplicative — all three must be present
        scale = (align_score * 0.40 + mag_score * 0.35 + persist_score * 0.25)
        return min(1.0, max(0.0, scale))

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        price_history = market_data.get("price_history", [])
        delta_history = market_data.get("delta_history", [])
        regime = market_data.get("regime", "unknown")

        if not price or len(price_history) < self.MIN_BARS:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": f"need {self.MIN_BARS} bars, have {len(price_history)}"},
            )

        # Multi-timeframe returns
        returns = self._rolling_returns(price_history)
        if not returns:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no valid returns computed"},
            )

        # Alignment across timeframes
        alignment, n_bull, n_bear = self._alignment_score(returns)

        # Trend persistence
        streak, streak_dir = self._momentum_persistence(price_history[-30:])

        # Breakpoint detection
        breaking, break_desc = self._detect_breakpoint(price_history, returns)

        # Position scaling
        position_scale = self._position_scale(alignment, returns, streak)

        # --- Build signal ---
        notes: list[str] = []

        # Weighted return: emphasize short-term more for entry timing
        weights = {5: 0.35, 10: 0.25, 20: 0.25, 60: 0.15}
        weighted_ret = sum(returns.get(w, 0) * weights.get(w, 0.2) for w in returns)

        if weighted_ret > self.WEAK_MOMENTUM:
            direction = "LONG"
            score = min(0.80, weighted_ret / self.STRONG_MOMENTUM * 0.5 + alignment * 0.3)
        elif weighted_ret < -self.WEAK_MOMENTUM:
            direction = "SHORT"
            score = max(-0.80, weighted_ret / self.STRONG_MOMENTUM * 0.5 + alignment * 0.3)
        else:
            direction = "FLAT"
            score = 0.0

        # Scale score by position sizing factor
        score *= position_scale

        # Breakpoint override: reduce conviction if momentum is breaking
        if breaking:
            score *= 0.4
            position_scale *= 0.4
            notes.append(break_desc)

        # Delta confirmation
        if delta_history and len(delta_history) >= 5:
            net_delta = sum(delta_history[-5:])
            if (score > 0 and net_delta > 0) or (score < 0 and net_delta < 0):
                score *= 1.15
                notes.append("Delta confirms momentum direction")
            elif (score > 0 and net_delta < 0) or (score < 0 and net_delta > 0):
                score *= 0.85
                notes.append("Delta diverges from momentum")

        score = max(-1.0, min(1.0, score))

        # Confidence
        confidence = min(1.0, 0.15 + position_scale * 0.5 + abs(alignment) * 0.2)
        if breaking:
            confidence *= 0.5

        # Regime adjustment
        if regime in ("trending", "TRENDING_UP", "TRENDING_DOWN"):
            confidence = min(1.0, confidence * 1.2)
            notes.append("Trending regime — momentum boosted")
        elif regime in ("ranging", "RANGING"):
            confidence *= 0.6
            notes.append("Ranging regime — momentum dampened")

        if abs(score) < 0.08:
            direction = "FLAT"

        # Return details
        ret_summary = {f"{w}b": f"{r*100:+.3f}%" for w, r in sorted(returns.items())}
        notes.insert(0, f"Returns: {ret_summary}")
        notes.insert(1, f"Alignment: {alignment:+.2f} ({n_bull} bull / {n_bear} bear)")
        notes.insert(2, f"Streak: {streak} bars {streak_dir}, scale: {position_scale:.2f}")

        entry_price = price if direction != "FLAT" else None
        atr = max(price_history[-20:]) - min(price_history[-20:]) if len(price_history) >= 20 else 5.0
        stop_dist = max(2.0, atr * 0.35)
        target_dist = stop_dist * (2.5 if abs(alignment) > 0.7 else 1.5)

        stop_price = round(price - stop_dist, 2) if direction == "LONG" else (
            round(price + stop_dist, 2) if direction == "SHORT" else None)
        target_price = round(price + target_dist, 2) if direction == "LONG" else (
            round(price - target_dist, 2) if direction == "SHORT" else None)

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "returns": {w: round(r, 6) for w, r in sorted(returns.items())},
                "alignment": round(alignment, 3),
                "streak": streak,
                "streak_dir": streak_dir,
                "position_scale": round(position_scale, 3),
                "breaking": breaking,
                "notes": notes,
            },
        )
