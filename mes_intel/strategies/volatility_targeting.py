"""Volatility Targeting Strategy — risk normalization and Sharpe optimization.

Estimates realized volatility (rolling std of returns) and generates signals
based on vol regime transitions. High vol → defensive (reduce risk, tighter stops).
Low vol → offensive (wider stops, trend-following). This teaches drawdown control
and Sharpe optimization rather than directional prediction.

Key concepts:
- Rolling realized vol (5/10/20 bar windows)
- Vol regime classification (low/normal/high/extreme)
- Vol compression → expansion breakout signals
- Risk-adjusted return scoring (return / vol = Sharpe proxy)
- Drawdown control: scale conviction inversely with vol
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class VolatilityTargetingStrategy(Strategy):
    """Volatility targeting: risk normalization, vol regime, Sharpe optimization."""

    name = "vol_targeting"
    description = "Realized vol targeting, vol compression/expansion, risk-adjusted returns, drawdown control"

    TARGET_VOL = 0.001     # target vol per bar (~0.1% per 5-second bar)
    MIN_BARS = 25
    VOL_WINDOWS = [5, 10, 20]

    # Vol regime thresholds (as multiples of long-term average)
    VOL_LOW = 0.6
    VOL_HIGH = 1.5
    VOL_EXTREME = 2.5

    # Compression detection
    COMPRESSION_RATIO = 0.5  # short vol / long vol < this = compression

    def required_data(self) -> list[str]:
        return ["price", "price_history", "volume_history", "delta_history", "regime"]

    # ------------------------------------------------------------------
    # Rolling volatility
    # ------------------------------------------------------------------

    def _rolling_vol(self, prices: list[float]) -> dict[int, float]:
        """Compute rolling realized volatility for each window.

        Uses log returns for better statistical properties.
        Returns dict of {window: vol}.
        """
        if len(prices) < 5:
            return {}

        log_returns = [math.log(prices[i] / prices[i - 1])
                       for i in range(1, len(prices))
                       if prices[i - 1] > 0 and prices[i] > 0]

        if len(log_returns) < 5:
            return {}

        result = {}
        for w in self.VOL_WINDOWS:
            if len(log_returns) >= w:
                window_returns = log_returns[-w:]
                result[w] = statistics.stdev(window_returns) if len(window_returns) > 1 else 0.0

        return result

    # ------------------------------------------------------------------
    # Vol regime classification
    # ------------------------------------------------------------------

    def _classify_vol_regime(self, vols: dict[int, float]) -> tuple[str, float]:
        """Classify the current volatility regime.

        Returns (regime, vol_ratio) where vol_ratio = current / long-term.
        """
        if not vols:
            return "unknown", 1.0

        # Use shortest window as "current", longest as "baseline"
        sorted_windows = sorted(vols.keys())
        current_vol = vols[sorted_windows[0]]
        baseline_vol = vols[sorted_windows[-1]] if len(sorted_windows) > 1 else current_vol

        if baseline_vol == 0:
            return "unknown", 1.0

        ratio = current_vol / baseline_vol

        if ratio >= self.VOL_EXTREME:
            return "extreme", ratio
        elif ratio >= self.VOL_HIGH:
            return "high", ratio
        elif ratio <= self.VOL_LOW:
            return "low", ratio
        else:
            return "normal", ratio

    # ------------------------------------------------------------------
    # Vol compression / expansion detection
    # ------------------------------------------------------------------

    def _detect_vol_dynamics(self, vols: dict[int, float],
                              prices: list[float]) -> tuple[str, float, str]:
        """Detect vol compression (about to expand) or expansion (already moving).

        Compression: short-term vol << long-term vol → coiling, expect breakout.
        Expansion: short-term vol >> long-term vol → move in progress.

        Returns (dynamic, ratio, description).
        """
        sorted_windows = sorted(vols.keys())
        if len(sorted_windows) < 2:
            return "neutral", 1.0, ""

        short_vol = vols[sorted_windows[0]]
        long_vol = vols[sorted_windows[-1]]

        if long_vol == 0:
            return "neutral", 1.0, ""

        ratio = short_vol / long_vol

        if ratio < self.COMPRESSION_RATIO:
            # Compression: volatility contracting → expect expansion
            return "compression", ratio, f"Vol compression ({ratio:.2f}x) — expect breakout"
        elif ratio > 1.0 / self.COMPRESSION_RATIO:
            # Expansion: volatility expanding → move in progress
            return "expansion", ratio, f"Vol expansion ({ratio:.2f}x) — move in progress"
        else:
            return "neutral", ratio, ""

    # ------------------------------------------------------------------
    # Risk-adjusted return (rolling Sharpe proxy)
    # ------------------------------------------------------------------

    def _rolling_sharpe(self, prices: list[float], window: int = 20) -> float:
        """Compute rolling Sharpe ratio (return / vol) as quality metric.

        High Sharpe = clean trend (good to follow).
        Low Sharpe = choppy/noisy (reduce conviction).
        """
        if len(prices) < window + 1:
            return 0.0

        log_returns = [math.log(prices[i] / prices[i - 1])
                       for i in range(len(prices) - window, len(prices))
                       if prices[i - 1] > 0 and prices[i] > 0]

        if len(log_returns) < 5:
            return 0.0

        avg_ret = statistics.mean(log_returns)
        std_ret = statistics.stdev(log_returns) if len(log_returns) > 1 else 1e-9

        if std_ret == 0:
            return 0.0

        return avg_ret / std_ret

    # ------------------------------------------------------------------
    # Vol-adjusted position scale
    # ------------------------------------------------------------------

    def _vol_position_scale(self, current_vol: float) -> float:
        """Inverse vol position scaling: reduce size in high vol, increase in low vol.

        target_vol / current_vol = scale factor, capped at [0.3, 2.0].
        """
        if current_vol <= 0:
            return 1.0

        scale = self.TARGET_VOL / current_vol
        return max(0.3, min(2.0, scale))

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
                meta={"reason": f"need {self.MIN_BARS} bars"},
            )

        # Compute rolling vol
        vols = self._rolling_vol(price_history)
        if not vols:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "cannot compute volatility"},
            )

        # Vol regime
        vol_regime, vol_ratio = self._classify_vol_regime(vols)

        # Vol dynamics (compression/expansion)
        dynamic, dyn_ratio, dyn_desc = self._detect_vol_dynamics(vols, price_history)

        # Rolling Sharpe
        sharpe = self._rolling_sharpe(price_history, 20)

        # Position scale
        current_vol = vols[min(vols.keys())]
        pos_scale = self._vol_position_scale(current_vol)

        # --- Build signal ---
        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []

        notes.append(f"Vol regime: {vol_regime} (ratio={vol_ratio:.2f})")
        notes.append(f"Sharpe(20): {sharpe:.3f}, pos_scale: {pos_scale:.2f}x")

        # 1. Vol compression → breakout anticipation
        if dynamic == "compression":
            # Don't know direction yet — use delta and recent momentum
            if delta_history and len(delta_history) >= 5:
                net_delta = sum(delta_history[-5:])
                if net_delta > 0:
                    signals.append(("vol_compression_long", 0.30, 0.25))
                    notes.append(f"Vol compression + positive delta → bullish breakout")
                elif net_delta < 0:
                    signals.append(("vol_compression_short", -0.30, 0.25))
                    notes.append(f"Vol compression + negative delta → bearish breakout")
            else:
                notes.append(dyn_desc)

        # 2. Vol expansion → follow the move
        elif dynamic == "expansion":
            recent_move = price_history[-1] - price_history[-5] if len(price_history) >= 5 else 0
            if recent_move > 0:
                signals.append(("vol_expansion_long", 0.25, 0.20))
                notes.append("Vol expansion + upward move → follow long")
            elif recent_move < 0:
                signals.append(("vol_expansion_short", -0.25, 0.20))
                notes.append("Vol expansion + downward move → follow short")

        # 3. Extreme vol → defensive
        if vol_regime == "extreme":
            # In extreme vol, lean short (fear/selling) unless delta says otherwise
            signals.append(("extreme_vol", -0.20, 0.20))
            notes.append(f"EXTREME vol — defensive mode, scale={pos_scale:.2f}x")

        # 4. High Sharpe → follow the trend
        if abs(sharpe) > 1.0:
            sharpe_dir = 1.0 if sharpe > 0 else -1.0
            signals.append(("high_sharpe", sharpe_dir * 0.35, 0.25))
            notes.append(f"High rolling Sharpe ({sharpe:.2f}) — clean trend, follow")

        elif abs(sharpe) < 0.3 and abs(sharpe) > 0:
            notes.append(f"Low Sharpe ({sharpe:.2f}) — choppy, reduce conviction")

        # 5. Vol regime transition signals
        if vol_regime == "low" and dynamic != "compression":
            # Low vol without compression = calm market, slightly bullish (risk-on)
            signals.append(("low_vol_risk_on", 0.15, 0.15))
            notes.append("Low vol environment — risk-on bias")

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={
                    "vol_regime": vol_regime, "vol_ratio": round(vol_ratio, 3),
                    "dynamic": dynamic, "sharpe": round(sharpe, 3),
                    "position_scale": round(pos_scale, 3),
                    "vols": {f"{w}b": round(v, 6) for w, v in sorted(vols.items())},
                    "notes": notes,
                },
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0

        # Apply vol-adjusted position scaling to score
        score *= min(pos_scale, 1.5)  # cap scaling impact on score
        score = max(-1.0, min(1.0, score))

        confidence = min(1.0, 0.15 + 0.20 * abs(sharpe) + 0.15 * abs(score))
        # Reduce confidence in extreme vol
        if vol_regime == "extreme":
            confidence *= 0.6
        elif vol_regime == "high":
            confidence *= 0.8

        if abs(score) < 0.08:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        entry_price = price if direction != "FLAT" else None
        # Vol-adjusted stops: wider in high vol, tighter in low vol
        base_stop = max(1.5, current_vol * price * 15)  # ~1.5 sigma
        stop_price = round(price - base_stop, 2) if direction == "LONG" else (
            round(price + base_stop, 2) if direction == "SHORT" else None)
        target_price = round(price + base_stop * 2.0, 2) if direction == "LONG" else (
            round(price - base_stop * 2.0, 2) if direction == "SHORT" else None)

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "vol_regime": vol_regime,
                "vol_ratio": round(vol_ratio, 3),
                "dynamic": dynamic,
                "sharpe": round(sharpe, 3),
                "position_scale": round(pos_scale, 3),
                "vols": {f"{w}b": round(v, 6) for w, v in sorted(vols.items())},
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
