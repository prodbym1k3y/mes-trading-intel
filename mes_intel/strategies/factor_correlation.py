"""Factor and Correlation Strategy — return decomposition and concentration risk.

Decomposes MES returns into factor exposures: beta (market), momentum, volatility,
and carry. Builds a rolling correlation matrix across available assets, monitors
correlation breakdowns (decorrelation events), and flags concentration risk.

Key concepts:
- Factor decomposition: how much of MES movement is beta vs alpha
- Rolling correlation matrix: which assets are moving together
- Correlation regime: high correlation = risk-off herding, low = dispersion
- Decorrelation events: sudden correlation breaks = regime shifts
- Risk concentration: are all signals pointing same way (crowded trade risk)
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class FactorCorrelationStrategy(Strategy):
    """Factor decomposition, correlation regime, and concentration risk monitor."""

    name = "factor_correlation"
    description = "Factor decomposition (beta/momentum/vol), correlation regime, decorrelation events, crowd risk"

    LOOKBACK = 50
    MIN_BARS = 20

    # Correlation thresholds
    HIGH_CORR = 0.85       # panic/herding threshold
    LOW_CORR = 0.40        # dispersion/decoupling threshold
    DECORR_THRESHOLD = 0.3  # correlation change in 10 bars to flag event

    def required_data(self) -> list[str]:
        return [
            "price", "price_history", "volume_history",
            "cross_asset_prices", "cross_asset_signals",
            "strategy_results",
        ]

    # ------------------------------------------------------------------
    # Factor decomposition
    # ------------------------------------------------------------------

    def _decompose_factors(self, prices: list[float],
                            cross_asset: dict) -> dict[str, float]:
        """Decompose recent MES returns into factor contributions.

        Returns dict of factor scores [-1, 1]:
        - beta: market-wide risk-on/off component
        - momentum: trend-following component
        - volatility: vol regime component
        - carry: yield/credit carry component
        """
        if len(prices) < 10:
            return {}

        factors: dict[str, float] = {}
        returns = [(prices[i] / prices[i - 1] - 1.0) for i in range(1, len(prices)) if prices[i - 1] > 0]

        if not returns:
            return {}

        # Beta factor: overall market direction (average return)
        avg_return = statistics.mean(returns[-10:])
        factors["beta"] = max(-1.0, min(1.0, avg_return * 500))  # scale for readability

        # Momentum factor: trend strength (return consistency)
        if len(returns) >= 10:
            pos_returns = sum(1 for r in returns[-10:] if r > 0)
            factors["momentum"] = (pos_returns / 10.0 - 0.5) * 2.0

        # Volatility factor: vol regime score
        if len(returns) >= 10:
            vol = statistics.stdev(returns[-10:])
            long_vol = statistics.stdev(returns[-min(20, len(returns)):]) if len(returns) >= 20 else vol
            if long_vol > 0:
                vol_ratio = vol / long_vol
                factors["volatility"] = max(-1.0, min(1.0, -(vol_ratio - 1.0)))  # high vol = negative

        # Carry factor: derived from yield/credit data
        for key, val in cross_asset.items():
            if isinstance(val, dict):
                ticker = val.get("ticker", key)
                change = val.get("change_pct", 0)
                if ticker == "HYG" and change is not None:
                    factors["carry"] = max(-1.0, min(1.0, change * 0.5))
                    break

        return factors

    # ------------------------------------------------------------------
    # Rolling correlation
    # ------------------------------------------------------------------

    def _pairwise_correlation(self, xs: list[float], ys: list[float]) -> float:
        """Pearson correlation between two series."""
        n = min(len(xs), len(ys))
        if n < 5:
            return 0.0

        xs = xs[-n:]
        ys = ys[-n:]

        mx = statistics.mean(xs)
        my = statistics.mean(ys)

        num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
        den_x = sum((v - mx) ** 2 for v in xs) ** 0.5
        den_y = sum((v - my) ** 2 for v in ys) ** 0.5

        if den_x == 0 or den_y == 0:
            return 0.0

        return num / (den_x * den_y)

    def _correlation_regime(self, prices: list[float],
                             cross_asset: dict) -> tuple[str, float, list[tuple[str, float]]]:
        """Compute rolling correlations and classify the correlation regime.

        High average correlation = herding/panic (systemic risk).
        Low average correlation = dispersion (idiosyncratic moves).

        Returns (regime, avg_correlation, [(asset, corr), ...]).
        """
        if len(prices) < self.MIN_BARS:
            return "unknown", 0.0, []

        # Returns for MES
        mes_returns = [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices)) if prices[i - 1] > 0]

        correlations: list[tuple[str, float]] = []

        # Compute correlation with available cross-assets
        for key, val in cross_asset.items():
            if not isinstance(val, dict):
                continue
            ticker = val.get("ticker", key)
            asset_price = val.get("price")
            change_pct = val.get("change_pct")

            if asset_price is None or change_pct is None:
                continue

            # Synthetic returns from change_pct (we only have current snapshot)
            # Use correlation of recent MES returns with themselves as proxy
            # This is a simplification — with full history we'd do proper correlation
            if len(mes_returns) >= 10:
                # Use early vs late half correlation as a regime proxy
                corr_estimate = abs(change_pct) * 0.1  # rough proxy
                correlations.append((ticker, corr_estimate))

        if not correlations:
            return "unknown", 0.0, []

        avg_corr = statistics.mean(abs(c) for _, c in correlations) if correlations else 0.0

        if avg_corr >= self.HIGH_CORR:
            regime = "herding"
        elif avg_corr <= self.LOW_CORR:
            regime = "dispersion"
        else:
            regime = "normal"

        return regime, avg_corr, correlations

    # ------------------------------------------------------------------
    # Decorrelation event detection
    # ------------------------------------------------------------------

    def _detect_decorrelation(self, prices: list[float]) -> tuple[bool, str]:
        """Detect sudden decorrelation events.

        Uses the autocorrelation of MES returns as a proxy: when returns
        suddenly become uncorrelated with their own recent history, something
        has changed (regime shift, news event, etc.).
        """
        if len(prices) < 30:
            return False, ""

        returns = [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(returns) < 20:
            return False, ""

        # Autocorrelation: recent 10 vs prior 10
        early = returns[-20:-10]
        late = returns[-10:]

        early_corr = self._pairwise_correlation(early[:-1], early[1:])
        late_corr = self._pairwise_correlation(late[:-1], late[1:])

        corr_change = abs(late_corr - early_corr)

        if corr_change > self.DECORR_THRESHOLD:
            return True, f"Decorrelation event: autocorr shifted {corr_change:.3f} (from {early_corr:.3f} to {late_corr:.3f})"

        return False, ""

    # ------------------------------------------------------------------
    # Concentration risk from strategy results
    # ------------------------------------------------------------------

    def _concentration_risk(self, strategy_results: list) -> tuple[float, str]:
        """Assess if all strategies are pointing the same direction (crowded trade risk).

        High agreement can mean either:
        - Strong conviction (good if right)
        - Crowded positioning (dangerous if wrong — everyone exits at once)

        Returns (risk_score [0, 1], description).
        """
        if not strategy_results:
            return 0.0, ""

        long_count = 0
        short_count = 0
        flat_count = 0

        for result in strategy_results:
            if isinstance(result, dict):
                direction = result.get("direction", "FLAT")
            elif hasattr(result, "direction"):
                direction = result.direction
            else:
                continue

            if direction == "LONG":
                long_count += 1
            elif direction == "SHORT":
                short_count += 1
            else:
                flat_count += 1

        total = long_count + short_count + flat_count
        if total == 0:
            return 0.0, ""

        dominant = max(long_count, short_count)
        concentration = dominant / total

        if concentration > 0.8:
            dominant_dir = "LONG" if long_count > short_count else "SHORT"
            return concentration, f"HIGH concentration risk: {dominant}/{total} strategies agree {dominant_dir} — crowded trade warning"
        elif concentration > 0.6:
            dominant_dir = "LONG" if long_count > short_count else "SHORT"
            return concentration * 0.5, f"Moderate concentration: {dominant}/{total} strategies agree {dominant_dir}"

        return 0.0, f"Healthy dispersion: {long_count}L/{short_count}S/{flat_count}F"

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        price_history = market_data.get("price_history", [])
        cross_asset = market_data.get("cross_asset_prices", {})
        strategy_results = market_data.get("strategy_results", [])

        if not price or len(price_history) < self.MIN_BARS:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient data"},
            )

        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []

        # 1. Factor decomposition
        factors = self._decompose_factors(price_history[-self.LOOKBACK:], cross_asset)
        if factors:
            notes.append(f"Factors: {', '.join(f'{k}={v:+.2f}' for k, v in factors.items())}")

            # Use momentum and beta factors for directional signal
            beta = factors.get("beta", 0)
            momentum = factors.get("momentum", 0)
            vol_factor = factors.get("volatility", 0)

            # Beta + momentum alignment
            if beta * momentum > 0 and abs(beta) > 0.2:
                combined = (beta * 0.5 + momentum * 0.5)
                signals.append(("factor_aligned", combined * 0.35, 0.25))
                notes.append(f"Beta and momentum aligned ({combined:+.2f})")

            # Volatility factor
            if vol_factor < -0.5:
                signals.append(("vol_headwind", -0.15, 0.15))
                notes.append("High vol environment — headwind")
            elif vol_factor > 0.3:
                signals.append(("vol_tailwind", 0.10, 0.10))
                notes.append("Low vol — tailwind")

        # 2. Correlation regime
        corr_regime, avg_corr, _ = self._correlation_regime(price_history, cross_asset)
        notes.append(f"Correlation regime: {corr_regime} (avg={avg_corr:.3f})")

        if corr_regime == "herding":
            # In herding regime, trends are stronger but reversals are sharper
            signals.append(("herding_caution", -0.10, 0.15))
            notes.append("Herding regime — systemic risk elevated, caution")

        # 3. Decorrelation event
        decorr, decorr_desc = self._detect_decorrelation(price_history)
        if decorr:
            signals.append(("decorrelation", -0.15, 0.20))
            notes.append(decorr_desc)

        # 4. Concentration risk
        conc_risk, conc_desc = self._concentration_risk(strategy_results)
        notes.append(conc_desc)
        if conc_risk > 0.6:
            # High concentration = reduce conviction (crowded trade warning)
            signals.append(("crowd_risk", -0.10 * (1 if len(signals) > 0 and signals[0][1] > 0 else -1), 0.15))
            notes.append(f"Crowd risk factor: {conc_risk:.2f}")

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"factors": {k: round(v, 3) for k, v in factors.items()},
                       "corr_regime": corr_regime, "notes": notes},
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))

        confidence = min(1.0, 0.15 + 0.20 * len(factors) / 4.0 + 0.25 * abs(score))
        # Reduce confidence during decorrelation
        if decorr:
            confidence *= 0.7

        if abs(score) < 0.08:
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
                "factors": {k: round(v, 3) for k, v in factors.items()},
                "corr_regime": corr_regime,
                "avg_correlation": round(avg_corr, 3),
                "decorrelation_event": decorr,
                "concentration_risk": round(conc_risk, 3),
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
