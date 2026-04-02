"""GEX Fair Value Model — Gamma Exposure & Dealer Positioning.

Uses gamma exposure levels to identify dealer hedging flows and
fair value ranges. GEX data sourced from external APIs or local cache.
"""
from __future__ import annotations

import json
import time
import numpy as np
from pathlib import Path
from .base import Strategy, StrategyResult


class GEXModelStrategy(Strategy):
    name = "gex_model"
    description = "Gamma exposure fair value & dealer positioning"

    def __init__(self, gex_data_path: str = "", flip_zone_width: float = 2.0):
        self.gex_data_path = gex_data_path or str(
            Path(__file__).parent.parent.parent / "gex_levels.json"
        )
        self.flip_zone_width = flip_zone_width

    def required_data(self) -> list[str]:
        return ["prices"]

    def _load_gex(self) -> dict | None:
        """Load GEX levels from cached JSON file."""
        path = Path(self.gex_data_path)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            # Check freshness (< 4 hours)
            if "timestamp" in data:
                age = time.time() - data["timestamp"]
                if age > 14400:
                    return None
            return data
        except (json.JSONDecodeError, KeyError):
            return None

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        gex_override = market_data.get("gex")

        if not prices:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "no price data"})

        current_price = float(prices[-1])

        # Load GEX data
        gex = gex_override or self._load_gex()
        if not gex:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "no GEX data available"})

        # Extract key GEX levels
        gex_flip = gex.get("flip_price") or gex.get("zero_gamma")
        major_pos_gamma = gex.get("major_pos_gamma") or gex.get("call_wall")
        major_neg_gamma = gex.get("major_neg_gamma") or gex.get("put_wall")
        hv_node = gex.get("hv_node") or gex.get("max_gamma")
        net_gex = gex.get("net_gex", 0)

        signals = []

        # 1. Position relative to GEX flip
        if gex_flip:
            gex_flip = float(gex_flip)
            dist_from_flip = current_price - gex_flip
            flip_zone = self.flip_zone_width

            if abs(dist_from_flip) < flip_zone:
                # Near the flip → high vol regime, no strong signal
                signals.append(("near_flip", 0.0, 0.3))
            elif dist_from_flip > 0:
                # Above flip = positive gamma territory → mean reverting, sell rips
                signals.append(("pos_gamma_territory", -0.3, 0.5))
            else:
                # Below flip = negative gamma territory → trending, momentum
                signals.append(("neg_gamma_territory", 0.3, 0.5))

        # 2. Proximity to call/put walls (magnets)
        if major_pos_gamma:
            call_wall = float(major_pos_gamma)
            dist_to_call = (call_wall - current_price) / current_price * 100
            if 0 < dist_to_call < 0.5:
                signals.append(("near_call_wall", 1.0, 0.6))  # magnet pull up
            elif dist_to_call < 0:
                signals.append(("above_call_wall", -0.5, 0.4))

        if major_neg_gamma:
            put_wall = float(major_neg_gamma)
            dist_to_put = (current_price - put_wall) / current_price * 100
            if 0 < dist_to_put < 0.5:
                signals.append(("near_put_wall", -1.0, 0.6))  # magnet pull down
            elif dist_to_put < 0:
                signals.append(("below_put_wall", 0.5, 0.4))

        # 3. Net GEX regime
        if net_gex > 0:
            # Positive GEX → dealers sell rips, buy dips → mean reversion
            signals.append(("pos_gex_regime", 0.0, 0.2))  # neutral, but informs regime
        elif net_gex < 0:
            # Negative GEX → dealers amplify moves → momentum/trend
            signals.append(("neg_gex_regime", 0.0, 0.2))

        if not signals:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "no actionable GEX signals"})

        total_weight = sum(abs(s[2]) for s in signals)
        weighted_score = sum(s[1] * s[2] for s in signals) / max(total_weight, 1e-10)
        score = float(np.clip(weighted_score, -1.0, 1.0))

        confidence = float(min(total_weight / 2.0, 1.0))

        if score > 0.2:
            direction = "LONG"
        elif score < -0.2:
            direction = "SHORT"
        else:
            direction = "FLAT"

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            entry_price=current_price,
            meta={
                "signals": [(s[0], float(s[1]), float(s[2])) for s in signals],
                "gex_flip": gex_flip,
                "call_wall": major_pos_gamma,
                "put_wall": major_neg_gamma,
                "net_gex": net_gex,
                "gex_regime": "positive" if net_gex > 0 else "negative",
            },
        )
