"""Hurst Exponent Regime Strategy — trend persistence vs mean reversion classifier.

The Hurst exponent H measures the long-term memory of a time series:
- H > 0.5: persistent (trending) — momentum strategies work
- H = 0.5: random walk — no edge
- H < 0.5: anti-persistent (mean reverting) — fade moves

This strategy computes a rolling Hurst exponent and uses it as both a regime
classifier AND a directional signal modifier. Combined with recent price action,
it tells you whether to follow or fade the current move.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

import numpy as np

from .base import Strategy, StrategyResult


class HurstRegimeStrategy(Strategy):
    """Hurst exponent regime: trend persistence vs mean reversion classifier."""

    name = "hurst_regime"
    description = "Rolling Hurst exponent: trend persistence (H>0.5) vs mean reversion (H<0.5) regime"

    MIN_BARS = 30
    HURST_TRENDING = 0.60     # H above this = trending regime
    HURST_MEAN_REV = 0.40     # H below this = mean-reverting regime
    LOOKBACK = 100            # bars for Hurst calculation

    def required_data(self) -> list[str]:
        return ["price", "price_history", "delta_history", "vwap"]

    # ------------------------------------------------------------------
    # Hurst exponent (R/S method)
    # ------------------------------------------------------------------

    def _compute_hurst(self, prices: list[float]) -> float:
        """Compute Hurst exponent using the rescaled range (R/S) method.

        Uses multiple sub-series lengths to compute the slope of
        log(R/S) vs log(n), which gives H.
        """
        n = len(prices)
        if n < 20:
            return 0.5  # default to random walk

        # Log returns
        returns = [math.log(prices[i] / prices[i - 1])
                   for i in range(1, n) if prices[i - 1] > 0 and prices[i] > 0]

        if len(returns) < 15:
            return 0.5

        # R/S analysis over multiple chunk sizes
        max_chunk = len(returns) // 2
        chunk_sizes = []
        rs_values = []

        size = 8
        while size <= max_chunk:
            chunk_sizes.append(size)
            rs_list = []

            num_chunks = len(returns) // size
            for i in range(num_chunks):
                chunk = returns[i * size:(i + 1) * size]
                mean_r = statistics.mean(chunk)

                # Cumulative deviation from mean
                cum_dev = []
                running = 0.0
                for r in chunk:
                    running += r - mean_r
                    cum_dev.append(running)

                R = max(cum_dev) - min(cum_dev)
                S = statistics.stdev(chunk) if len(chunk) > 1 else 1e-10

                if S > 0:
                    rs_list.append(R / S)

            if rs_list:
                rs_values.append(statistics.mean(rs_list))

            size = int(size * 1.5)

        if len(chunk_sizes) < 3:
            return 0.5

        # Log-log regression to find H
        log_n = [math.log(s) for s in chunk_sizes]
        log_rs = [math.log(rs) for rs in rs_values]

        n_pts = len(log_n)
        x_mean = statistics.mean(log_n)
        y_mean = statistics.mean(log_rs)

        num = sum((log_n[i] - x_mean) * (log_rs[i] - y_mean) for i in range(n_pts))
        den = sum((log_n[i] - x_mean) ** 2 for i in range(n_pts))

        H = num / den if den != 0 else 0.5
        return max(0.0, min(1.0, H))

    # ------------------------------------------------------------------
    # Rolling Hurst for trend detection
    # ------------------------------------------------------------------

    def _rolling_hurst(self, prices: list[float], window: int = 50) -> list[float]:
        """Compute rolling Hurst exponent over a sliding window."""
        if len(prices) < window:
            return [self._compute_hurst(prices)]

        hurst_series = []
        for end in range(window, len(prices) + 1):
            h = self._compute_hurst(prices[end - window:end])
            hurst_series.append(h)

        return hurst_series

    # ------------------------------------------------------------------
    # Regime-aware directional signal
    # ------------------------------------------------------------------

    def _directional_signal(self, hurst: float, prices: list[float],
                             delta_history: list[float],
                             vwap: float) -> tuple[float, float, str]:
        """Generate directional signal based on Hurst regime + price action.

        In trending regime (H > 0.6): follow momentum
        In mean-reverting regime (H < 0.4): fade extremes
        In random walk (0.4-0.6): low confidence, use other signals

        Returns (score, confidence, description).
        """
        if len(prices) < 10:
            return 0.0, 0.0, "insufficient data"

        price = prices[-1]

        # Recent momentum
        short_ma = statistics.mean(prices[-5:])
        long_ma = statistics.mean(prices[-20:]) if len(prices) >= 20 else statistics.mean(prices)
        momentum = (short_ma - long_ma) / max(abs(long_ma), 1e-9)

        # Delta confirmation
        delta_bias = 0.0
        if delta_history and len(delta_history) >= 5:
            recent_delta = sum(delta_history[-5:])
            delta_bias = 1.0 if recent_delta > 0 else (-1.0 if recent_delta < 0 else 0.0)

        # VWAP deviation
        vwap_dev = (price - vwap) / max(abs(vwap), 1e-9) if vwap > 0 else 0.0

        if hurst >= self.HURST_TRENDING:
            # Trending: follow momentum
            h_strength = (hurst - 0.5) * 4.0  # 0 at H=0.5, ~2 at H=1.0
            direction = 1.0 if momentum > 0 else -1.0

            score = direction * min(0.60, 0.25 + h_strength * 0.15)

            # Delta confirmation boosts confidence
            conf = min(0.80, 0.35 + h_strength * 0.15)
            if delta_bias * direction > 0:
                conf = min(0.90, conf + 0.15)

            desc = (f"Trending regime (H={hurst:.3f}): follow "
                    f"{'bullish' if direction > 0 else 'bearish'} momentum")
            return score, conf, desc

        elif hurst <= self.HURST_MEAN_REV:
            # Mean-reverting: fade moves away from VWAP
            h_strength = (0.5 - hurst) * 4.0

            # Fade the deviation from VWAP
            if abs(vwap_dev) > 0.001:
                direction = -1.0 if vwap_dev > 0 else 1.0  # fade back to VWAP
                score = direction * min(0.55, 0.20 + h_strength * 0.15 + abs(vwap_dev) * 10)
            else:
                score = 0.0
                direction = 0.0

            conf = min(0.75, 0.30 + h_strength * 0.15)
            desc = (f"Mean-reverting regime (H={hurst:.3f}): fade "
                    f"{'overbought' if vwap_dev > 0 else 'oversold'} "
                    f"(VWAP dev={vwap_dev*100:.2f}%)")
            return score, conf, desc

        else:
            # Random walk zone — low confidence
            return 0.0, 0.10, f"Random walk regime (H={hurst:.3f}): no directional edge"

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        price_history = market_data.get("price_history", [])
        delta_history = market_data.get("delta_history", [])
        vwap = market_data.get("vwap", 0.0)

        if not price or len(price_history) < self.MIN_BARS:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient data for Hurst calculation"},
            )

        # Compute current Hurst
        analysis_window = price_history[-self.LOOKBACK:]
        hurst = self._compute_hurst(analysis_window)

        # Rolling Hurst for trend in the exponent itself
        hurst_series = self._rolling_hurst(price_history, window=50)
        hurst_trend = 0.0
        if len(hurst_series) >= 5:
            hurst_trend = hurst_series[-1] - hurst_series[-5]

        # Directional signal
        score, confidence, desc = self._directional_signal(
            hurst, analysis_window, delta_history, vwap
        )

        # Hurst trend modifier: Hurst increasing = strengthening trend regime
        notes = [desc]
        if hurst_trend > 0.05:
            notes.append(f"Hurst increasing ({hurst_trend:+.3f}) — trend strengthening")
            if score != 0:
                confidence = min(1.0, confidence + 0.10)
        elif hurst_trend < -0.05:
            notes.append(f"Hurst decreasing ({hurst_trend:+.3f}) — regime transitioning")

        # Classify regime
        if hurst >= self.HURST_TRENDING:
            regime = "trending"
        elif hurst <= self.HURST_MEAN_REV:
            regime = "mean_reverting"
        else:
            regime = "random_walk"

        if abs(score) < 0.10:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        entry_price = price if direction != "FLAT" else None
        stop_price = None
        target_price = None

        if direction != "FLAT":
            atr_est = max(price_history[-20:]) - min(price_history[-20:]) if len(price_history) >= 20 else 5.0
            stop_dist = max(2.0, atr_est * 0.4)

            if regime == "trending":
                target_dist = stop_dist * 2.0  # wider targets in trends
            else:
                target_dist = stop_dist * 1.2  # tighter in mean reversion

            if direction == "LONG":
                stop_price = round(price - stop_dist, 2)
                target_price = round(price + target_dist, 2)
            else:
                stop_price = round(price + stop_dist, 2)
                target_price = round(price - target_dist, 2)

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "hurst": round(hurst, 4),
                "hurst_trend": round(hurst_trend, 4),
                "regime": regime,
                "vwap": round(vwap, 2) if vwap else None,
                "notes": notes,
            },
        )
