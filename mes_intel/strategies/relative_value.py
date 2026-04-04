"""Relative Value / Spread Strategy — multi-pair mean reversion.

Models relationships between correlated assets (MES vs NQ, MES vs Russell,
equity vs bonds, equity vs VIX). Tracks spreads vs historical mean and trades
deviations using z-scores with dynamic entry/exit bands.

Exploits mean reversion and mispricing independent of market direction.
When MES is cheap relative to NQ (z < -2), buy MES. When expensive (z > 2), sell.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


# Spread pair definitions: (name, asset_key, expected_correlation, description)
SPREAD_PAIRS = [
    ("MES_NQ", "nq_price", 0.92, "MES vs NQ (Nasdaq futures)"),
    ("MES_RTY", "rty_price", 0.75, "MES vs Russell 2000"),
    ("EQ_BONDS", "tlt_price", -0.50, "Equity vs Long Bonds (TLT)"),
    ("EQ_VIX", "vix_price", -0.85, "Equity vs VIX (inverse)"),
    ("EQ_CREDIT", "hyg_price", 0.65, "Equity vs HY Credit (HYG)"),
    ("EQ_GOLD", "gold_price", -0.30, "Equity vs Gold"),
]


class RelativeValueStrategy(Strategy):
    """Multi-pair relative value spread trading with z-score bands."""

    name = "relative_value"
    description = "Multi-pair spreads (MES vs NQ/RTY/TLT/VIX/HYG/Gold), z-score entry/exit bands"

    # Z-score thresholds
    ENTRY_Z = 2.0          # enter at |z| >= 2.0
    STRONG_Z = 2.5         # strong signal at |z| >= 2.5
    EXIT_Z = 0.5           # exit when |z| <= 0.5 (back to fair value)
    LOOKBACK = 100         # bars for spread mean/std computation
    MIN_POINTS = 20        # minimum data points per pair

    def required_data(self) -> list[str]:
        return ["price", "price_history", "cross_asset_prices", "cross_asset_signals"]

    # ------------------------------------------------------------------
    # Spread computation
    # ------------------------------------------------------------------

    def _compute_spread(self, mes_prices: list[float],
                         other_prices: list[float],
                         correlation: float) -> tuple[float, float, float, float]:
        """Compute the spread between MES and another asset.

        Uses ratio-based spread (log ratio) for stability.
        Returns (current_z, spread_mean, spread_std, half_life).
        """
        n = min(len(mes_prices), len(other_prices))
        if n < self.MIN_POINTS:
            return 0.0, 0.0, 0.0, 0.0

        mes = mes_prices[-n:]
        other = other_prices[-n:]

        # Log ratio spread
        spreads = []
        for m, o in zip(mes, other):
            if m > 0 and o > 0:
                spreads.append(math.log(m / o))

        if len(spreads) < self.MIN_POINTS:
            return 0.0, 0.0, 0.0, 0.0

        mean = statistics.mean(spreads)
        std = statistics.stdev(spreads) if len(spreads) > 2 else 1e-9

        if std == 0:
            return 0.0, mean, 0.0, 0.0

        current_spread = spreads[-1]
        z = (current_spread - mean) / std

        # Half-life of mean reversion (Ornstein-Uhlenbeck estimate)
        half_life = self._estimate_half_life(spreads)

        return z, mean, std, half_life

    def _estimate_half_life(self, spreads: list[float]) -> float:
        """Estimate half-life of mean reversion using AR(1) regression.

        y(t) - y(t-1) = alpha + beta * y(t-1) + epsilon
        half_life = -log(2) / beta
        """
        if len(spreads) < 10:
            return float("inf")

        y = spreads[1:]
        x = spreads[:-1]
        dy = [y[i] - x[i] for i in range(len(y))]

        n = len(dy)
        x_mean = statistics.mean(x)
        dy_mean = statistics.mean(dy)

        num = sum((x[i] - x_mean) * (dy[i] - dy_mean) for i in range(n))
        den = sum((x[i] - x_mean) ** 2 for i in range(n))

        if den == 0:
            return float("inf")

        beta = num / den

        if beta >= 0:
            return float("inf")  # not mean-reverting

        return -math.log(2) / beta

    # ------------------------------------------------------------------
    # Extract cross-asset prices
    # ------------------------------------------------------------------

    def _get_asset_price(self, cross_asset_prices: dict,
                          asset_key: str) -> Optional[float]:
        """Extract a current price from cross-asset data."""
        # Map our keys to cross_asset_feed ticker names
        key_map = {
            "nq_price": "NQ=F",
            "rty_price": "RTY=F",
            "tlt_price": "TLT",
            "vix_price": "^VIX",
            "hyg_price": "HYG",
            "gold_price": "GC=F",
        }

        ticker = key_map.get(asset_key, asset_key)

        # Try direct lookup
        if ticker in cross_asset_prices:
            data = cross_asset_prices[ticker]
            if isinstance(data, dict):
                return data.get("price")
            return float(data)

        # Try by asset name
        for key, val in cross_asset_prices.items():
            if isinstance(val, dict) and val.get("ticker") == ticker:
                return val.get("price")

        return None

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        price_history = market_data.get("price_history", [])
        cross_asset = market_data.get("cross_asset_prices", {})
        cross_signals = market_data.get("cross_asset_signals", {})

        if not price or len(price_history) < self.MIN_POINTS:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient data"},
            )

        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []
        pair_results: list[dict] = []

        for pair_name, asset_key, expected_corr, desc in SPREAD_PAIRS:
            asset_price = self._get_asset_price(cross_asset, asset_key)
            if asset_price is None or asset_price <= 0:
                continue

            # Synthetic history: use current ratio applied to MES history
            # (approximate since we don't have full cross-asset price history)
            ratio = price / asset_price if asset_price > 0 else 1.0
            synthetic_other = [p / ratio for p in price_history[-self.LOOKBACK:]]

            z, spread_mean, spread_std, half_life = self._compute_spread(
                price_history[-self.LOOKBACK:], synthetic_other, expected_corr
            )

            pair_data = {
                "pair": pair_name,
                "description": desc,
                "z_score": round(z, 3),
                "half_life": round(half_life, 1) if half_life < 1000 else "inf",
                "asset_price": round(asset_price, 2),
            }
            pair_results.append(pair_data)

            # Signal from this pair
            if abs(z) >= self.STRONG_Z:
                # Strong mean reversion signal
                direction_score = -0.30 if z > 0 else 0.30  # fade the extreme
                weight = 0.25 if abs(expected_corr) > 0.7 else 0.15
                signals.append((f"{pair_name}_strong", direction_score, weight))
                notes.append(f"{pair_name}: z={z:+.2f} STRONG — MES {'expensive' if z > 0 else 'cheap'} vs {desc}")

            elif abs(z) >= self.ENTRY_Z:
                direction_score = -0.20 if z > 0 else 0.20
                weight = 0.20 if abs(expected_corr) > 0.7 else 0.12
                signals.append((f"{pair_name}_entry", direction_score, weight))
                notes.append(f"{pair_name}: z={z:+.2f} — MES {'expensive' if z > 0 else 'cheap'} vs {desc}")

            elif abs(z) <= self.EXIT_Z:
                notes.append(f"{pair_name}: z={z:+.2f} — at fair value")

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"pairs": pair_results, "notes": notes or ["no actionable spread dislocations"]},
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))

        # Confidence: more pairs confirming = higher confidence
        agreeing = sum(1 for _, s, _ in signals if s * score > 0)
        total_signals = len(signals)
        agreement_ratio = agreeing / total_signals if total_signals > 0 else 0

        confidence = min(1.0, 0.15 + 0.25 * agreement_ratio + 0.30 * abs(score))

        if abs(score) < 0.08:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        entry_price = price if direction != "FLAT" else None
        atr = max(price_history[-20:]) - min(price_history[-20:]) if len(price_history) >= 20 else 5.0
        stop_price = round(price - atr * 0.5, 2) if direction == "LONG" else (
            round(price + atr * 0.5, 2) if direction == "SHORT" else None)
        target_price = round(price + atr * 0.7, 2) if direction == "LONG" else (
            round(price - atr * 0.7, 2) if direction == "SHORT" else None)

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "pairs": pair_results,
                "agreement": f"{agreeing}/{total_signals}",
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
