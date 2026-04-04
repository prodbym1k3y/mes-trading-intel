"""Macro Regime Allocation Strategy — regime-dependent signal weighting.

Defines macro regimes using cross-asset signals: rates environment (yields, DXY),
risk appetite (VIX, credit spreads, HYG), growth (commodity momentum, equity breadth),
and liquidity (yield curve slope, credit stress). Adjusts MES signal bias and
confidence based on which macro regime is active.

Regimes:
- RISK_ON: low VIX, tight credit, strong breadth → bullish bias
- RISK_OFF: high VIX, wide credit, weak breadth → bearish bias
- REFLATION: rising yields + rising commodities → neutral-bullish (inflation trades)
- DEFLATION: falling yields + falling commodities → bearish (flight to safety)
- TIGHTENING: rising DXY + rising yields → headwind for equities
- EASING: falling DXY + falling yields → tailwind for equities
"""
from __future__ import annotations

import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class MacroRegimeStrategy(Strategy):
    """Macro regime allocation: rates, risk appetite, growth, liquidity signals."""

    name = "macro_regime"
    description = "Macro regime (risk-on/off, reflation, tightening) from VIX, yields, DXY, credit, commodities"

    def required_data(self) -> list[str]:
        return ["price", "cross_asset_prices", "cross_asset_signals", "cross_asset_composite"]

    # ------------------------------------------------------------------
    # Extract macro indicators
    # ------------------------------------------------------------------

    def _extract_indicators(self, cross_asset: dict,
                             cross_signals: dict) -> dict[str, float]:
        """Extract and normalize macro indicators from cross-asset data.

        Returns dict with indicator scores normalized to [-1, 1].
        """
        indicators: dict[str, float] = {}

        # Helper to extract price/change from cross_asset dict
        def get_asset(ticker: str) -> tuple[Optional[float], Optional[float]]:
            for key, val in cross_asset.items():
                if isinstance(val, dict):
                    if val.get("ticker") == ticker or key == ticker:
                        return val.get("price"), val.get("change_pct", 0)
            return None, None

        def get_signal(ticker: str) -> Optional[float]:
            for key, val in cross_signals.items():
                if isinstance(val, dict):
                    if val.get("ticker") == ticker or key == ticker:
                        return val.get("signal")
            if ticker in cross_signals:
                v = cross_signals[ticker]
                return v.get("signal") if isinstance(v, dict) else float(v)
            return None

        # VIX: high = fear, low = complacency
        vix_price, vix_change = get_asset("^VIX")
        if vix_price is not None:
            if vix_price > 30:
                indicators["fear"] = -0.80
            elif vix_price > 25:
                indicators["fear"] = -0.50
            elif vix_price > 20:
                indicators["fear"] = -0.20
            elif vix_price < 14:
                indicators["fear"] = 0.40  # complacent = positive risk
            else:
                indicators["fear"] = 0.10

            if vix_change is not None:
                if vix_change > 10:
                    indicators["vix_spike"] = -0.70
                elif vix_change > 5:
                    indicators["vix_spike"] = -0.30
                elif vix_change < -5:
                    indicators["vix_spike"] = 0.30

        # 10Y Yield: rising = tightening, falling = easing
        tnx_price, tnx_change = get_asset("^TNX")
        if tnx_change is not None:
            if tnx_change > 3:
                indicators["rates_pressure"] = -0.40  # rapid rise = headwind
            elif tnx_change > 1:
                indicators["rates_pressure"] = -0.15
            elif tnx_change < -3:
                indicators["rates_pressure"] = 0.30  # falling yields = tailwind
            elif tnx_change < -1:
                indicators["rates_pressure"] = 0.15

        # DXY: strong dollar = headwind for equities
        dxy_price, dxy_change = get_asset("DX-Y.NYB")
        if dxy_change is not None:
            if dxy_change > 0.5:
                indicators["dollar_pressure"] = -0.30
            elif dxy_change < -0.5:
                indicators["dollar_pressure"] = 0.25

        # HYG: credit stress (falling HYG = widening spreads = risk-off)
        hyg_price, hyg_change = get_asset("HYG")
        if hyg_change is not None:
            if hyg_change < -0.5:
                indicators["credit_stress"] = -0.50
            elif hyg_change < -0.2:
                indicators["credit_stress"] = -0.20
            elif hyg_change > 0.3:
                indicators["credit_stress"] = 0.25

        # Gold: rising = risk-off / inflation hedge
        gold_price, gold_change = get_asset("GC=F")
        if gold_change is not None:
            if gold_change > 1.0:
                indicators["safe_haven"] = -0.25  # flight to safety
            elif gold_change < -0.5:
                indicators["safe_haven"] = 0.15  # risk appetite

        # Oil: rising = growth/inflation signal
        oil_price, oil_change = get_asset("CL=F")
        if oil_change is not None:
            if oil_change > 2.0:
                indicators["commodity_momentum"] = 0.15  # growth signal
            elif oil_change < -3.0:
                indicators["commodity_momentum"] = -0.25  # demand destruction

        # Russell 2000: breadth indicator
        rty_price, rty_change = get_asset("RTY=F")
        if rty_change is not None:
            if rty_change > 1.0:
                indicators["breadth"] = 0.30
            elif rty_change < -1.0:
                indicators["breadth"] = -0.30

        # TLT: bond rally = flight to safety
        tlt_price, tlt_change = get_asset("TLT")
        if tlt_change is not None:
            if tlt_change > 0.5:
                indicators["bond_bid"] = -0.20  # flight to safety
            elif tlt_change < -0.5:
                indicators["bond_bid"] = 0.15  # risk-on rotation

        # Bitcoin: risk appetite proxy
        btc_price, btc_change = get_asset("BTC-USD")
        if btc_change is not None:
            if btc_change > 3.0:
                indicators["risk_appetite"] = 0.20
            elif btc_change < -3.0:
                indicators["risk_appetite"] = -0.20

        # Composite signal (pre-computed by cross_asset_feed)
        composite = None
        if isinstance(cross_signals, dict):
            for key, val in cross_signals.items():
                if isinstance(val, dict) and "composite_signal" in val:
                    composite = val["composite_signal"]
                    break

        if composite is not None:
            indicators["composite"] = float(composite) * 0.5

        return indicators

    # ------------------------------------------------------------------
    # Regime classification
    # ------------------------------------------------------------------

    def _classify_regime(self, indicators: dict[str, float]) -> tuple[str, float]:
        """Classify the current macro regime from indicator scores.

        Returns (regime_name, conviction).
        """
        if not indicators:
            return "UNKNOWN", 0.0

        fear = indicators.get("fear", 0)
        rates = indicators.get("rates_pressure", 0)
        dollar = indicators.get("dollar_pressure", 0)
        credit = indicators.get("credit_stress", 0)
        breadth = indicators.get("breadth", 0)
        safe_haven = indicators.get("safe_haven", 0)
        commodity = indicators.get("commodity_momentum", 0)

        # Risk-off: high fear + credit stress + safe haven bid
        risk_off_score = abs(min(fear, 0)) + abs(min(credit, 0)) + abs(min(safe_haven, 0))
        # Risk-on: low fear + strong breadth + positive credit
        risk_on_score = max(fear, 0) + max(breadth, 0) + max(credit, 0)
        # Tightening: rising rates + rising dollar
        tightening_score = abs(min(rates, 0)) + abs(min(dollar, 0))
        # Easing: falling rates + falling dollar
        easing_score = max(rates, 0) + max(dollar, 0)
        # Reflation: rising commodities + rising yields
        reflation_score = max(commodity, 0) + abs(min(rates, 0)) * 0.5

        scores = {
            "RISK_OFF": risk_off_score,
            "RISK_ON": risk_on_score,
            "TIGHTENING": tightening_score,
            "EASING": easing_score,
            "REFLATION": reflation_score,
        }

        # Winner takes all
        best_regime = max(scores, key=scores.get)
        best_score = scores[best_regime]

        # But needs minimum conviction
        if best_score < 0.3:
            return "NEUTRAL", 0.0

        conviction = min(1.0, best_score / 1.5)
        return best_regime, conviction

    # ------------------------------------------------------------------
    # Regime → MES bias
    # ------------------------------------------------------------------

    def _regime_bias(self, regime: str, conviction: float) -> tuple[float, str]:
        """Convert macro regime to MES directional bias.

        Returns (bias [-1, 1], description).
        """
        biases = {
            "RISK_ON": (0.35, "Risk-on regime — bullish MES bias"),
            "RISK_OFF": (-0.45, "Risk-off regime — bearish MES bias"),
            "EASING": (0.30, "Easing regime — tailwind for equities"),
            "TIGHTENING": (-0.25, "Tightening regime — headwind for equities"),
            "REFLATION": (0.10, "Reflation regime — mixed equity impact"),
            "NEUTRAL": (0.0, "Neutral macro regime — no bias"),
            "UNKNOWN": (0.0, "Insufficient macro data"),
        }

        base_bias, desc = biases.get(regime, (0.0, "Unknown regime"))
        return base_bias * conviction, desc

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        cross_asset = market_data.get("cross_asset_prices", {})
        cross_signals = market_data.get("cross_asset_signals", {})

        if not price or not cross_asset:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no cross-asset data"},
            )

        # Extract and score macro indicators
        indicators = self._extract_indicators(cross_asset, cross_signals)

        if not indicators:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "could not extract macro indicators"},
            )

        # Classify regime
        regime, conviction = self._classify_regime(indicators)

        # Convert to MES bias
        bias, regime_desc = self._regime_bias(regime, conviction)

        # Aggregate individual indicator scores
        weighted_sum = sum(indicators.values())
        n_indicators = len(indicators)
        avg_indicator = weighted_sum / n_indicators if n_indicators > 0 else 0

        # Combined score: regime bias + average indicator signal
        score = bias * 0.6 + avg_indicator * 0.4
        score = max(-1.0, min(1.0, score))

        # Confidence: more indicators = more confident
        data_quality = min(1.0, n_indicators / 6.0)  # want at least 6 indicators
        confidence = min(1.0, 0.10 + conviction * 0.40 + data_quality * 0.20 + abs(score) * 0.20)

        notes = [
            regime_desc,
            f"Regime: {regime} (conviction={conviction:.2f})",
            f"Indicators: {n_indicators} active, avg={avg_indicator:+.3f}",
        ]

        # Add individual indicator details
        for name, val in sorted(indicators.items(), key=lambda x: abs(x[1]), reverse=True):
            notes.append(f"  {name}: {val:+.3f}")

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
                "regime": regime,
                "conviction": round(conviction, 3),
                "indicators": {k: round(v, 4) for k, v in indicators.items()},
                "n_indicators": n_indicators,
                "notes": notes,
            },
        )
