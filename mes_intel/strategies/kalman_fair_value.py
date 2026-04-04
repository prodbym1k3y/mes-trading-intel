"""Kalman Fair Value Strategy — adaptive fair value estimation with mean reversion.

Uses a Kalman filter to estimate the "true" fair value of MES by fusing:
- Price observations (noisy)
- VWAP (anchor)
- Volume-weighted center of mass
- Cross-asset implied fair value

When price deviates significantly from Kalman-estimated fair value, it generates
mean reversion signals. The filter adapts its noise parameters based on regime.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class KalmanFairValueStrategy(Strategy):
    """Kalman-filtered fair value estimation with mean reversion signals."""

    name = "kalman_fair_value"
    description = "Adaptive Kalman fair value: fuses price, VWAP, volume center, cross-asset for mean reversion"

    # Kalman parameters (will adapt based on regime)
    PROCESS_NOISE_BASE = 0.10     # Q: how much fair value can move per step
    MEASUREMENT_NOISE_BASE = 1.0  # R: how noisy price observations are
    DEVIATION_THRESHOLD = 2.0     # points: minimum deviation to signal
    STRONG_DEVIATION = 4.0        # points: strong mean reversion signal
    LOOKBACK = 30                 # bars for initialization

    def required_data(self) -> list[str]:
        return [
            "price", "price_history", "vwap",
            "volume_profile", "volume_history",
            "regime",
        ]

    # ------------------------------------------------------------------
    # Kalman filter
    # ------------------------------------------------------------------

    def _run_kalman(self, prices: list[float], vwap: float,
                     regime: str) -> tuple[float, float, list[float]]:
        """Run 1D Kalman filter on price observations.

        State: estimated fair value
        Observation: price (with noise)

        Returns (fair_value_estimate, uncertainty, fair_value_history).
        """
        if not prices:
            return 0.0, float("inf"), []

        # Adapt noise parameters to regime
        if regime in ("trending", "TRENDING_UP", "TRENDING_DOWN"):
            Q = self.PROCESS_NOISE_BASE * 2.0   # fair value moves faster in trends
            R = self.MEASUREMENT_NOISE_BASE * 0.5  # trust price more in trends
        elif regime in ("volatile", "VOLATILE"):
            Q = self.PROCESS_NOISE_BASE * 1.5
            R = self.MEASUREMENT_NOISE_BASE * 2.0  # price is noisier
        elif regime in ("quiet", "QUIET"):
            Q = self.PROCESS_NOISE_BASE * 0.5
            R = self.MEASUREMENT_NOISE_BASE * 0.7
        else:  # ranging, unknown
            Q = self.PROCESS_NOISE_BASE
            R = self.MEASUREMENT_NOISE_BASE

        # Initialize state with VWAP if available, else first price
        x = vwap if vwap > 0 else prices[0]  # state estimate
        P = R * 2.0  # initial uncertainty

        fv_history = []

        for z in prices:
            # Predict
            x_pred = x         # fair value doesn't drift (random walk model)
            P_pred = P + Q

            # Update (Kalman gain)
            K = P_pred / (P_pred + R)
            x = x_pred + K * (z - x_pred)
            P = (1.0 - K) * P_pred

            fv_history.append(x)

        return x, P, fv_history

    # ------------------------------------------------------------------
    # Volume-weighted center of mass
    # ------------------------------------------------------------------

    def _volume_center(self, volume_profile) -> Optional[float]:
        """Compute volume-weighted price center from the profile.

        This is a more robust "fair value" anchor than VWAP because it
        accounts for where actual trading activity is concentrated.
        """
        if volume_profile is None:
            return None

        levels = volume_profile.sorted_levels()
        if not levels:
            return None

        total_vol = sum(l.total_volume for l in levels)
        if total_vol == 0:
            return None

        vw_price = sum(l.price * l.total_volume for l in levels) / total_vol
        return vw_price

    # ------------------------------------------------------------------
    # Fair value confidence band
    # ------------------------------------------------------------------

    def _confidence_band(self, fv_history: list[float], prices: list[float]) -> float:
        """Compute the standard deviation of price around fair value.

        Used to normalize deviation signals — a 2pt deviation in a quiet
        market is more significant than in a volatile one.
        """
        if len(fv_history) < 5 or len(prices) < 5:
            return 2.0  # default

        n = min(len(fv_history), len(prices))
        residuals = [prices[-n + i] - fv_history[-n + i] for i in range(n)]
        if len(residuals) < 3:
            return 2.0

        return max(0.5, statistics.stdev(residuals))

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        price_history = market_data.get("price_history", [])
        vwap = market_data.get("vwap", 0.0)
        volume_profile = market_data.get("volume_profile")
        regime = market_data.get("regime", "unknown")

        if not price or len(price_history) < 10:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient price history"},
            )

        # Run Kalman filter
        fair_value, uncertainty, fv_history = self._run_kalman(
            price_history[-self.LOOKBACK:], vwap, regime
        )

        # Volume-weighted center as secondary anchor
        vol_center = self._volume_center(volume_profile)

        # Fuse Kalman FV with volume center (if available)
        if vol_center is not None and abs(vol_center - fair_value) < 10:
            # Weighted average: 70% Kalman, 30% volume center
            fused_fv = fair_value * 0.7 + vol_center * 0.3
        else:
            fused_fv = fair_value

        # Deviation from fused fair value
        deviation = price - fused_fv
        band = self._confidence_band(fv_history, price_history[-self.LOOKBACK:])
        z_score = deviation / band if band > 0 else 0.0

        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []

        notes.append(f"Fair value={fused_fv:.2f} (Kalman={fair_value:.2f}, VWAP={vwap:.2f})")
        if vol_center is not None:
            notes.append(f"Volume center={vol_center:.2f}")

        # --- Deviation-based signals ---
        abs_dev = abs(deviation)

        if abs_dev >= self.STRONG_DEVIATION or abs(z_score) >= 2.5:
            # Strong mean reversion signal
            mean_rev = -1.0 if deviation > 0 else 1.0
            score = mean_rev * min(0.65, 0.35 + abs(z_score) * 0.1)
            signals.append(("strong_deviation", score, 0.40))
            notes.append(f"Strong deviation: {deviation:+.2f}pts (z={z_score:+.2f}) — mean reversion")

        elif abs_dev >= self.DEVIATION_THRESHOLD or abs(z_score) >= 1.5:
            mean_rev = -1.0 if deviation > 0 else 1.0
            score = mean_rev * min(0.40, 0.20 + abs(z_score) * 0.08)
            signals.append(("deviation", score, 0.30))
            notes.append(f"Deviation: {deviation:+.2f}pts (z={z_score:+.2f}) — mild mean reversion")

        elif abs(z_score) < 0.5:
            notes.append(f"At fair value (dev={deviation:+.2f}pts, z={z_score:+.2f})")

        # --- Fair value trend (is FV itself moving?) ---
        if len(fv_history) >= 10:
            fv_slope = (fv_history[-1] - fv_history[-10]) / 10.0
            if abs(fv_slope) > 0.05:
                fv_dir = 1.0 if fv_slope > 0 else -1.0
                signals.append(("fv_trend", fv_dir * min(0.25, abs(fv_slope) * 2), 0.25))
                notes.append(f"Fair value trending {'up' if fv_slope > 0 else 'down'} ({fv_slope:+.3f}/bar)")

        # --- Regime modifier ---
        if regime in ("trending", "TRENDING_UP", "TRENDING_DOWN"):
            # In trends, reduce mean reversion confidence
            for i, (name, s, w) in enumerate(signals):
                if "deviation" in name:
                    signals[i] = (name, s * 0.6, w)
            notes.append("Trending regime — mean reversion dampened")
        elif regime in ("ranging", "RANGING"):
            # In ranges, boost mean reversion
            for i, (name, s, w) in enumerate(signals):
                if "deviation" in name:
                    signals[i] = (name, s * 1.3, w)
            notes.append("Ranging regime — mean reversion boosted")

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={
                    "fair_value": round(fused_fv, 2),
                    "deviation": round(deviation, 2),
                    "z_score": round(z_score, 2),
                    "uncertainty": round(uncertainty, 4),
                    "notes": notes,
                },
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))

        confidence = min(1.0, 0.15 + 0.20 * abs(z_score) + 0.25 * abs(score))
        # Higher uncertainty = lower confidence
        if uncertainty > 2.0:
            confidence *= 0.7

        if abs(score) < 0.10:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        entry_price = price if direction != "FLAT" else None
        # Targets: move back toward fair value
        target_price = round(fused_fv, 2) if direction != "FLAT" else None
        stop_dist = max(2.0, band * 1.5)
        stop_price = round(price - stop_dist, 2) if direction == "LONG" else (
            round(price + stop_dist, 2) if direction == "SHORT" else None)

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "fair_value": round(fused_fv, 2),
                "kalman_fv": round(fair_value, 2),
                "volume_center": round(vol_center, 2) if vol_center else None,
                "vwap": round(vwap, 2),
                "deviation": round(deviation, 2),
                "z_score": round(z_score, 2),
                "band": round(band, 3),
                "uncertainty": round(uncertainty, 4),
                "regime": regime,
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
