"""VPIN Strategy — Volume-Synchronized Probability of Informed Trading.

VPIN is a real-time toxicity metric that measures the probability of informed
(institutional) trading in the order flow. Developed by Easley, López de Prado,
and O'Hara (2012). High VPIN precedes volatility events and flash crashes.

This implementation uses the bulk volume classification (BVC) method to estimate
buy/sell volume from tick data, then computes VPIN over rolling volume buckets.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class VPINStrategy(Strategy):
    """Volume-Synchronized Probability of Informed Trading."""

    name = "vpin"
    description = "VPIN toxicity metric: probability of informed trading, flash crash predictor"

    # VPIN parameters
    BUCKET_SIZE = 200        # volume per bucket (trades)
    NUM_BUCKETS = 50         # rolling window of buckets for VPIN calculation
    HIGH_VPIN = 0.70         # threshold for "high toxicity" warning
    EXTREME_VPIN = 0.85      # threshold for extreme toxicity (flash crash risk)
    LOW_VPIN = 0.30          # below this = clean, low-toxicity flow

    def required_data(self) -> list[str]:
        return ["price", "price_history", "volume_history", "delta_history", "volumes"]

    # ------------------------------------------------------------------
    # Bulk Volume Classification (BVC)
    # ------------------------------------------------------------------

    def _bulk_volume_classify(self, prices: list[float],
                               volumes: list[float]) -> list[tuple[float, float]]:
        """Classify each bar's volume into buy/sell using BVC.

        BVC uses price change normalized by its standard deviation with a
        normal CDF to estimate the buy fraction. This avoids needing actual
        tick-by-tick trade classification.

        Returns list of (buy_volume, sell_volume) per bar.
        """
        if len(prices) < 3 or len(volumes) < 3:
            return []

        n = min(len(prices), len(volumes))
        prices = prices[-n:]
        volumes = volumes[-n:]

        # Price changes
        dp = [prices[i] - prices[i - 1] for i in range(1, n)]
        if not dp:
            return []

        sigma = statistics.stdev(dp) if len(dp) > 2 else max(abs(d) for d in dp) or 0.25

        result = []
        for i, d in enumerate(dp):
            v = volumes[i + 1]  # volume of the bar
            if v <= 0:
                result.append((0.0, 0.0))
                continue

            # Standard normal CDF approximation
            z = d / sigma if sigma > 0 else 0.0
            buy_pct = self._norm_cdf(z)
            buy_vol = v * buy_pct
            sell_vol = v * (1.0 - buy_pct)
            result.append((buy_vol, sell_vol))

        return result

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Approximation of the standard normal CDF."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    # ------------------------------------------------------------------
    # VPIN computation
    # ------------------------------------------------------------------

    def _compute_vpin(self, buy_sell_volumes: list[tuple[float, float]]) -> tuple[float, list[float]]:
        """Compute VPIN from classified buy/sell volume bars.

        Groups trades into equal-volume buckets, then computes the order imbalance
        (|buy - sell| / total) averaged over a rolling window of buckets.

        Returns (current_vpin, vpin_history).
        """
        if not buy_sell_volumes:
            return 0.0, []

        # Aggregate into volume buckets
        buckets: list[tuple[float, float]] = []  # (bucket_buy, bucket_sell)
        accum_buy = 0.0
        accum_sell = 0.0
        accum_vol = 0.0

        for buy_v, sell_v in buy_sell_volumes:
            remaining_buy = buy_v
            remaining_sell = sell_v
            remaining_total = buy_v + sell_v

            while remaining_total > 0:
                space = self.BUCKET_SIZE - accum_vol
                if remaining_total <= space:
                    accum_buy += remaining_buy
                    accum_sell += remaining_sell
                    accum_vol += remaining_total
                    remaining_total = 0
                else:
                    # Fill the current bucket
                    frac = space / remaining_total if remaining_total > 0 else 0
                    accum_buy += remaining_buy * frac
                    accum_sell += remaining_sell * frac
                    accum_vol = self.BUCKET_SIZE

                    buckets.append((accum_buy, accum_sell))
                    accum_buy = 0.0
                    accum_sell = 0.0
                    accum_vol = 0.0

                    remaining_buy *= (1 - frac)
                    remaining_sell *= (1 - frac)
                    remaining_total = remaining_buy + remaining_sell

        # Compute VPIN over rolling bucket windows
        if len(buckets) < 3:
            return 0.0, []

        vpin_series = []
        window = min(self.NUM_BUCKETS, len(buckets))

        for end in range(window, len(buckets) + 1):
            window_buckets = buckets[end - window:end]
            imbalances = []
            total_volume = 0.0

            for b, s in window_buckets:
                bucket_total = b + s
                if bucket_total > 0:
                    imbalances.append(abs(b - s))
                    total_volume += bucket_total

            if total_volume > 0:
                vpin_val = sum(imbalances) / total_volume
            else:
                vpin_val = 0.0
            vpin_series.append(vpin_val)

        current_vpin = vpin_series[-1] if vpin_series else 0.0
        return current_vpin, vpin_series

    # ------------------------------------------------------------------
    # VPIN trend (rising = increasing toxicity)
    # ------------------------------------------------------------------

    def _vpin_trend(self, vpin_series: list[float]) -> float:
        """Compute the trend of VPIN: rising = increasing informed flow.

        Returns slope normalized to [-1, 1].
        """
        if len(vpin_series) < 5:
            return 0.0

        recent = vpin_series[-10:]
        n = len(recent)
        x_mean = (n - 1) / 2.0
        y_mean = statistics.mean(recent)
        num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0.0

        return max(-1.0, min(1.0, slope * 20.0))

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        price_history = market_data.get("price_history", [])
        volume_history = market_data.get("volume_history", [])
        delta_history = market_data.get("delta_history", [])
        volumes = market_data.get("volumes", volume_history)

        if not price or len(price_history) < 10 or len(volumes) < 10:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient data for VPIN"},
            )

        # BVC classification
        buy_sell = self._bulk_volume_classify(price_history, volumes)
        if len(buy_sell) < 5:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient classified volume"},
            )

        # Compute VPIN
        vpin, vpin_series = self._compute_vpin(buy_sell)
        vpin_trend = self._vpin_trend(vpin_series)

        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []

        # --- VPIN level signals ---

        if vpin >= self.EXTREME_VPIN:
            # Extreme toxicity — potential flash crash / volatility event
            # In negative gamma regime, this is especially dangerous
            signals.append(("extreme_vpin", -0.50, 0.35))
            notes.append(f"EXTREME VPIN={vpin:.3f} — high probability of informed trading, flash crash risk")
        elif vpin >= self.HIGH_VPIN:
            # High toxicity — informed flow detected, caution
            signals.append(("high_vpin", -0.25, 0.25))
            notes.append(f"High VPIN={vpin:.3f} — elevated informed trading probability")
        elif vpin <= self.LOW_VPIN:
            # Clean flow — low toxicity, safe to follow momentum
            notes.append(f"Low VPIN={vpin:.3f} — clean flow, low toxicity")

        # --- VPIN trend signals ---
        if vpin_trend > 0.3:
            signals.append(("vpin_rising", -0.20, 0.20))
            notes.append(f"VPIN rising (trend={vpin_trend:.2f}) — toxicity increasing")
        elif vpin_trend < -0.3:
            signals.append(("vpin_falling", 0.10, 0.15))
            notes.append(f"VPIN falling (trend={vpin_trend:.2f}) — toxicity normalizing")

        # --- Delta direction at high VPIN ---
        if vpin >= self.HIGH_VPIN and delta_history:
            recent_delta = sum(delta_history[-5:])
            if recent_delta > 0:
                # Informed buying at high VPIN = genuine bullish conviction
                signals.append(("informed_buying", 0.35, 0.25))
                notes.append("Informed buying detected at high VPIN")
            elif recent_delta < 0:
                # Informed selling at high VPIN = genuine bearish conviction
                signals.append(("informed_selling", -0.35, 0.25))
                notes.append("Informed selling detected at high VPIN")

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"vpin": round(vpin, 4), "vpin_trend": round(vpin_trend, 3),
                       "notes": notes},
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))

        confidence = min(1.0, 0.20 + 0.30 * vpin + 0.20 * abs(score))

        if abs(score) < 0.10:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=price if direction != "FLAT" else None,
            meta={
                "vpin": round(vpin, 4),
                "vpin_trend": round(vpin_trend, 3),
                "vpin_level": ("extreme" if vpin >= self.EXTREME_VPIN
                               else "high" if vpin >= self.HIGH_VPIN
                               else "low" if vpin <= self.LOW_VPIN
                               else "normal"),
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
