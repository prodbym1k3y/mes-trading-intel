"""Delta Divergence Strategy — cumulative delta vs price divergence."""
from __future__ import annotations

import statistics
from typing import List, Tuple

from .base import Strategy, StrategyResult


class DeltaDivergenceStrategy(Strategy):
    """Tracks cumulative delta versus price to detect divergence patterns.

    When price and delta are aligned the trend is healthy. When they diverge
    — price making a new extreme but delta failing to confirm — smart money
    is absorbing the move rather than driving it. That divergence tends to
    resolve in the direction the delta is "pointing."
    """

    name = "delta_divergence"
    description = "Cumulative delta vs price divergence and exhaustion detector"

    # --- tuneable parameters ------------------------------------------------
    LOOKBACK = 20           # bars for divergence analysis
    MIN_BARS = 10           # minimum bars required
    CORR_DIVERGE_THRESH = -0.3   # negative correlation = divergence confirmed
    SLOPE_WINDOW = 5        # bars for slope calculation
    TARGET_MULTIPLIER = 1.5

    def required_data(self) -> list[str]:
        return [
            "price", "price_history", "delta_history",
            "session_delta", "session_high", "session_low",
            "high", "low",
        ]

    # --- helpers ------------------------------------------------------------

    def _linear_slope(self, series: List[float]) -> float:
        """Least-squares slope of a series. Positive = rising, negative = falling."""
        n = len(series)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2.0
        y_mean = statistics.mean(series)
        num = sum((i - x_mean) * (series[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return num / den if den != 0 else 0.0

    def _pearson_correlation(self, xs: List[float], ys: List[float]) -> float:
        """Pearson correlation between two equal-length lists."""
        n = len(xs)
        if n < 3:
            return 0.0
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
        den_x = sum((v - mx) ** 2 for v in xs) ** 0.5
        den_y = sum((v - my) ** 2 for v in ys) ** 0.5
        if den_x == 0 or den_y == 0:
            return 0.0
        return num / (den_x * den_y)

    def _find_swing_points(self, series: List[float]) -> Tuple[float, float, int, int]:
        """Return (min_val, max_val, min_idx, max_idx) in series."""
        min_val = min(series)
        max_val = max(series)
        min_idx = series.index(min_val)
        max_idx = series.index(max_val)
        return min_val, max_val, min_idx, max_idx

    def _detect_divergence(
        self,
        prices: List[float],
        deltas: List[float],
    ) -> Tuple[str, float]:
        """Classify divergence type and return (type, magnitude).

        Types:
          'bullish'  — price lower low, delta higher low
          'bearish'  — price higher high, delta lower high
          'none'     — no divergence
        """
        if len(prices) < 4 or len(deltas) < 4:
            return "none", 0.0

        half = len(prices) // 2
        p_early, p_late = prices[:half], prices[half:]
        d_early, d_late = deltas[:half], deltas[half:]

        p_min_early, p_max_early = min(p_early), max(p_early)
        p_min_late, p_max_late = min(p_late), max(p_late)
        d_min_early, d_max_early = min(d_early), max(d_early)
        d_min_late, d_max_late = min(d_late), max(d_late)

        price_range = max(prices) - min(prices)
        delta_range = max(deltas) - min(deltas)
        if price_range == 0 or delta_range == 0:
            return "none", 0.0

        # Bullish divergence: price lower low but delta higher low
        if p_min_late < p_min_early and d_min_late > d_min_early:
            price_diff = (p_min_early - p_min_late) / price_range
            delta_diff = (d_min_late - d_min_early) / delta_range
            magnitude = (price_diff + delta_diff) / 2.0
            return "bullish", min(magnitude, 1.0)

        # Bearish divergence: price higher high but delta lower high
        if p_max_late > p_max_early and d_max_late < d_max_early:
            price_diff = (p_max_late - p_max_early) / price_range
            delta_diff = (d_max_early - d_max_late) / delta_range
            magnitude = (price_diff + delta_diff) / 2.0
            return "bearish", min(magnitude, 1.0)

        return "none", 0.0

    def _delta_exhaustion(
        self,
        delta_history: List[float],
        session_delta: float,
        price: float,
        session_high: float,
        session_low: float,
    ) -> Tuple[float, str]:
        """Detect delta exhaustion: delta at session extreme but price not following.

        Returns (exhaustion_score [0,1], direction 'LONG'/'SHORT'/'FLAT').
        """
        if not delta_history:
            return 0.0, "FLAT"

        session_delta_range = max(delta_history) - min(delta_history)
        if session_delta_range == 0:
            return 0.0, "FLAT"

        # Normalize delta position within session range
        delta_pct = (session_delta - min(delta_history)) / session_delta_range

        price_range = session_high - session_low
        if price_range == 0:
            return 0.0, "FLAT"
        price_pct = (price - session_low) / price_range

        # Delta at extreme high but price not at high → bearish exhaustion
        if delta_pct > 0.85 and price_pct < 0.65:
            exhaustion = min((delta_pct - 0.85) / 0.15 + (0.65 - price_pct) / 0.35, 1.0)
            return exhaustion, "SHORT"

        # Delta at extreme low but price not at low → bullish exhaustion
        if delta_pct < 0.15 and price_pct > 0.35:
            exhaustion = min((0.15 - delta_pct) / 0.15 + (price_pct - 0.35) / 0.65, 1.0)
            return exhaustion, "LONG"

        return 0.0, "FLAT"

    def _swing_stop(self, prices: List[float], direction: str) -> float:
        """Last significant swing high (for SHORT) or swing low (for LONG)."""
        window = prices[-self.LOOKBACK:] if len(prices) >= self.LOOKBACK else prices
        if direction == "SHORT":
            return max(window)
        return min(window)

    # --- main ---------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        price_history: List[float] = market_data.get("price_history", [])
        delta_history: List[float] = market_data.get("delta_history", [])
        session_delta: float = market_data.get("session_delta", 0.0)
        session_high: float = market_data.get("session_high", price)
        session_low: float = market_data.get("session_low", price)
        high: float = market_data.get("high", price)
        low: float = market_data.get("low", price)

        if len(price_history) < self.MIN_BARS or len(delta_history) < self.MIN_BARS:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient_history"}
            )

        # Trim to lookback window
        prices = price_history[-self.LOOKBACK:]
        deltas = delta_history[-self.LOOKBACK:]

        # --- divergence detection -------------------------------------------
        div_type, div_magnitude = self._detect_divergence(prices, deltas)

        # --- correlation (negative = diverging) -----------------------------
        correlation = self._pearson_correlation(
            [p - prices[0] for p in prices],
            [d - deltas[0] for d in deltas],
        )

        # --- slope comparison -----------------------------------------------
        p_slope = self._linear_slope(prices[-self.SLOPE_WINDOW:])
        d_slope = self._linear_slope(deltas[-self.SLOPE_WINDOW:])
        # Normalize slopes to comparable scale
        p_scale = max(abs(p_slope), 1e-9)
        d_scale = max(abs(d_slope), 1e-9)
        slope_agree = (p_slope * d_slope) > 0   # same sign = aligned

        # --- delta exhaustion -----------------------------------------------
        exhaustion, exhaust_dir = self._delta_exhaustion(
            delta_history, session_delta, price, session_high, session_low
        )

        # --- build signal ---------------------------------------------------
        direction = "FLAT"
        score = 0.0
        div_score = 0.0

        if div_type == "bullish":
            direction = "LONG"
            div_score = div_magnitude
        elif div_type == "bearish":
            direction = "SHORT"
            div_score = -div_magnitude
        elif not slope_agree and exhaust_dir != "FLAT":
            # Fallback: trend continuation when aligned
            direction = exhaust_dir
            div_score = exhaustion * 0.5

        # Correlation contribution: negative correlation amplifies divergence signal
        corr_factor = max(0.0, -correlation) if div_type != "none" else 0.0

        score = div_score * (1.0 + 0.5 * corr_factor)

        # Trend continuation bonus when slope + delta agree
        if div_type == "none" and slope_agree and abs(d_slope) > 0:
            continuation_dir = "LONG" if d_slope > 0 else "SHORT"
            if direction == "FLAT":
                direction = continuation_dir
                score = 0.25 * (1.0 if d_slope > 0 else -1.0)

        score = max(-1.0, min(1.0, score))

        # --- confidence -----------------------------------------------------
        persistence_score = min(self.LOOKBACK / 20.0, 1.0)  # more bars = more confident
        confidence = (
            0.40 * min(abs(div_score), 1.0)
            + 0.25 * corr_factor
            + 0.20 * exhaustion
            + 0.15 * persistence_score
        )
        confidence = max(0.0, min(1.0, confidence))

        # --- risk levels ----------------------------------------------------
        stop_price = None
        target_price = None
        if direction != "FLAT":
            stop_price = self._swing_stop(prices, direction)
            div_pts = abs(max(prices) - min(prices))
            if direction == "LONG":
                target_price = price + div_pts * self.TARGET_MULTIPLIER
            else:
                target_price = price - div_pts * self.TARGET_MULTIPLIER

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=price if direction != "FLAT" else None,
            stop_price=round(stop_price, 2) if stop_price is not None else None,
            target_price=round(target_price, 2) if target_price is not None else None,
            meta={
                "divergence_type": div_type,
                "divergence_magnitude": round(div_magnitude, 4),
                "correlation": round(correlation, 4),
                "price_slope": round(p_slope, 6),
                "delta_slope": round(d_slope, 6),
                "slopes_aligned": slope_agree,
                "exhaustion_score": round(exhaustion, 4),
                "exhaustion_dir": exhaust_dir,
            },
        )
