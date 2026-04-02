"""Confluence Zone Detector Strategy.

Finds price levels where multiple support/resistance triggers stack,
then generates signals based on proximity and absorption/breakthrough
detection at those zones.

Triggers: VWAP, POC, VAH/VAL, prior day H/L/C, weekly H/L, round numbers,
GEX level, dark pool levels, fair value gaps, opening range H/L.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .base import Strategy, StrategyResult


@dataclass
class ConfluenceLevel:
    """A price level where multiple support/resistance factors stack."""
    price: float
    score: int                               # count of confluences
    factors: List[str]                       # e.g. ['VWAP', 'PDH', 'ROUND_NUMBER', 'POC']
    direction: str                           # 'SUPPORT', 'RESISTANCE', 'BOTH'
    absorption_detected: bool = False
    breakthrough_detected: bool = False
    entry_direction: str = "FLAT"           # LONG, SHORT, FLAT
    stop_price: float = 0.0
    target_price: float = 0.0
    confidence: float = 0.0

    def __repr__(self) -> str:
        flags = []
        if self.absorption_detected:
            flags.append("ABS")
        if self.breakthrough_detected:
            flags.append("BRK")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        return (
            f"ConfluenceLevel({self.price:.2f} {self.direction} "
            f"score={self.score} factors={self.factors}{flag_str})"
        )


class ConfluenceZoneDetector(Strategy):
    """Multi-factor confluence zone detector with absorption/breakthrough signals."""

    name = "confluence"
    description = "Stacked S/R confluence detector: VWAP, POC, PDH/L, round numbers, GEX, dark pool, FVGs, OR"

    # Price tolerance for "same level": 0.5 points (2 MES ticks)
    LEVEL_TOLERANCE = 0.5

    # Proximity to current price to be considered "nearby" for signal generation
    SIGNAL_RADIUS = 2.0

    def __init__(
        self,
        level_tolerance: float = 0.5,
        signal_radius: float = 2.0,
        round_number_tolerance: float = 0.25,
        absorption_volume_ratio: float = 1.8,
        absorption_range_max_pts: float = 1.5,
        breakthrough_min_pts: float = 1.0,
        breakthrough_volume_ratio: float = 1.4,
    ):
        """
        Args:
            level_tolerance: Points within which two factors merge into the same level.
            signal_radius: Distance (points) within which a level influences the signal.
            round_number_tolerance: Distance to round number to qualify.
            absorption_volume_ratio: Volume vs session avg for absorption detection.
            absorption_range_max_pts: Max bar range to qualify as absorption.
            breakthrough_min_pts: Bar must close this many points past level to be a breakthrough.
            breakthrough_volume_ratio: Volume multiple needed for a valid breakthrough.
        """
        self.level_tolerance = level_tolerance
        self.signal_radius = signal_radius
        self.round_number_tolerance = round_number_tolerance
        self.absorption_volume_ratio = absorption_volume_ratio
        self.absorption_range_max_pts = absorption_range_max_pts
        self.breakthrough_min_pts = breakthrough_min_pts
        self.breakthrough_volume_ratio = breakthrough_volume_ratio

    def required_data(self) -> list[str]:
        return [
            "price", "vwap", "poc", "vah", "val",
            "prior_day_high", "prior_day_low", "prior_day_close",
            "weekly_high", "weekly_low",
            "price_history", "volume_history", "volume",
            "gex_level", "dark_pool_levels", "fair_value_gaps",
            "opening_range_high", "opening_range_low",
        ]

    # ------------------------------------------------------------------
    # Factor extraction
    # ------------------------------------------------------------------

    def _extract_factors(self, md: dict, current_price: float) -> list[tuple[float, str]]:
        """
        Pull all candidate S/R levels with their factor labels.

        Returns list of (price, factor_label) tuples.
        """
        factors: list[tuple[float, str]] = []

        # 1. VWAP
        vwap = md.get("vwap")
        if vwap is not None:
            factors.append((float(vwap), "VWAP"))

        # 2. POC
        poc = md.get("poc")
        if poc is not None:
            factors.append((float(poc), "POC"))

        # 3. VAH
        vah = md.get("vah")
        if vah is not None:
            factors.append((float(vah), "VAH"))

        # 4. VAL
        val = md.get("val")
        if val is not None:
            factors.append((float(val), "VAL"))

        # 5. Prior Day High (PDH)
        pdh = md.get("prior_day_high")
        if pdh is not None:
            factors.append((float(pdh), "PDH"))

        # 6. Prior Day Low (PDL)
        pdl = md.get("prior_day_low")
        if pdl is not None:
            factors.append((float(pdl), "PDL"))

        # 7. Prior Day Close (PDC)
        pdc = md.get("prior_day_close")
        if pdc is not None:
            factors.append((float(pdc), "PDC"))

        # 8. Weekly High
        wkh = md.get("weekly_high")
        if wkh is not None:
            factors.append((float(wkh), "WEEKLY_HIGH"))

        # 9. Weekly Low
        wkl = md.get("weekly_low")
        if wkl is not None:
            factors.append((float(wkl), "WEEKLY_LOW"))

        # 10. Round Numbers — whole numbers and .5 levels near current price
        # Check integers and half-integers within a 20-point window
        base = math.floor(current_price)
        for offset_half in range(-40, 42):   # ±20 points in 0.5 steps
            candidate = (base + offset_half * 0.5)
            if abs(candidate - current_price) > 20:
                continue
            # Is it a whole number?
            if candidate == math.floor(candidate):
                label = "ROUND_NUMBER"
            elif (candidate * 2) == math.floor(candidate * 2):
                label = "HALF_NUMBER"
            else:
                continue
            if abs(candidate - current_price) <= self.round_number_tolerance:
                factors.append((float(candidate), label))

        # 11. GEX Level
        gex = md.get("gex_level")
        if gex is not None and float(gex) != 0.0:
            factors.append((float(gex), "GEX_LEVEL"))

        # 12. Dark Pool Levels
        dark_pool_levels: list = md.get("dark_pool_levels", [])
        for dp_level in dark_pool_levels:
            factors.append((float(dp_level), "DARK_POOL"))

        # 13. Fair Value Gaps — price inside a FVG zone counts
        fvgs: list[dict] = md.get("fair_value_gaps", [])
        for gap in fvgs:
            top = gap.get("top")
            bottom = gap.get("bottom")
            if top is None or bottom is None:
                continue
            mid = (float(top) + float(bottom)) / 2.0
            # Use midpoint as the level; also add edges
            factors.append((mid, "FVG_MID"))

        # 14. Opening Range High (ORH)
        orh = md.get("opening_range_high")
        if orh is not None:
            factors.append((float(orh), "ORH"))

        # 15. Opening Range Low (ORL)
        orl = md.get("opening_range_low")
        if orl is not None:
            factors.append((float(orl), "ORL"))

        return factors

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def _cluster_factors(
        self,
        factors: list[tuple[float, str]],
        current_price: float,
    ) -> list[ConfluenceLevel]:
        """
        Merge factors within level_tolerance of each other into ConfluenceLevels.
        """
        if not factors:
            return []

        # Sort by price
        sorted_factors = sorted(factors, key=lambda x: x[0])
        used = [False] * len(sorted_factors)
        levels: list[ConfluenceLevel] = []

        for i, (price_i, factor_i) in enumerate(sorted_factors):
            if used[i]:
                continue

            cluster_prices = [price_i]
            cluster_factors: list[str] = [factor_i]
            used[i] = True

            for j in range(i + 1, len(sorted_factors)):
                if used[j]:
                    continue
                if sorted_factors[j][0] - price_i <= self.level_tolerance:
                    cluster_prices.append(sorted_factors[j][0])
                    cluster_factors.append(sorted_factors[j][1])
                    used[j] = True
                else:
                    break

            # Representative price = weighted mean (equal weights here)
            rep_price = float(np.mean(cluster_prices))

            # Direction based on position vs current price
            if rep_price < current_price - self.level_tolerance:
                direction = "SUPPORT"
            elif rep_price > current_price + self.level_tolerance:
                direction = "RESISTANCE"
            else:
                direction = "BOTH"  # price is right at the level

            level = ConfluenceLevel(
                price=rep_price,
                score=len(cluster_factors),
                factors=cluster_factors,
                direction=direction,
            )
            levels.append(level)

        return levels

    # ------------------------------------------------------------------
    # Absorption and breakthrough detection
    # ------------------------------------------------------------------

    def _check_absorption_breakthrough(
        self,
        level: ConfluenceLevel,
        price_history: list[float],
        volume_history: list[float],
        current_price: float,
        current_volume: float,
    ) -> None:
        """
        Modifies level in-place to set absorption_detected / breakthrough_detected.

        Uses price_history and volume_history (most recent bars last).
        """
        if len(price_history) < 3:
            return

        avg_vol = float(np.mean(volume_history[-20:])) if len(volume_history) >= 20 else (
            float(np.mean(volume_history)) if volume_history else 0.0
        )
        if avg_vol <= 0:
            return

        # Look at the last 5 "bars" of price history
        n = min(5, len(price_history))
        recent_prices = price_history[-n:]
        recent_vols = volume_history[-n:] if len(volume_history) >= n else (
            volume_history + [current_volume] * (n - len(volume_history))
        )

        tolerance = self.level_tolerance * 2  # slightly wider for detection

        # Check if any recent bars touched the level
        touched = any(abs(p - level.price) <= tolerance for p in recent_prices)

        if not touched:
            return

        # Find bars that touched the level
        touch_prices = [p for p in recent_prices if abs(p - level.price) <= tolerance]
        touch_vols_idx = [i for i, p in enumerate(recent_prices) if abs(p - level.price) <= tolerance]
        touch_vols = [recent_vols[i] for i in touch_vols_idx if i < len(recent_vols)]

        # Volume spike at touch
        vol_spike = any(v > avg_vol * self.absorption_volume_ratio for v in touch_vols)

        # Absorption: price touched the level with high volume, did NOT close through it
        price_range_at_touch = max(touch_prices) - min(touch_prices) if len(touch_prices) > 1 else 0.0
        small_range = price_range_at_touch <= self.absorption_range_max_pts

        if vol_spike and small_range:
            # Verify price has NOT moved through the level
            last_price = recent_prices[-1]
            if level.direction == "SUPPORT" and last_price >= level.price - tolerance:
                level.absorption_detected = True
            elif level.direction == "RESISTANCE" and last_price <= level.price + tolerance:
                level.absorption_detected = True
            elif level.direction == "BOTH":
                level.absorption_detected = True

        # Breakthrough: last price closes beyond level with volume
        if not level.absorption_detected:
            last_price = recent_prices[-1]
            last_vol = recent_vols[-1] if recent_vols else 0.0
            vol_confirmed = last_vol > avg_vol * self.breakthrough_volume_ratio

            if vol_confirmed:
                if level.direction == "RESISTANCE" and last_price > level.price + self.breakthrough_min_pts:
                    level.breakthrough_detected = True
                elif level.direction == "SUPPORT" and last_price < level.price - self.breakthrough_min_pts:
                    level.breakthrough_detected = True

    # ------------------------------------------------------------------
    # Signal assignment per level
    # ------------------------------------------------------------------

    def _assign_level_signal(
        self,
        level: ConfluenceLevel,
        current_price: float,
        vah: Optional[float],
        val: Optional[float],
    ) -> None:
        """
        Set entry_direction, stop_price, target_price, confidence on level in-place.
        """
        tol = self.level_tolerance

        # Base confidence from score
        if level.score >= 5:
            base_conf = 0.75
        elif level.score >= 3:
            base_conf = 0.55
        else:
            base_conf = 0.30

        # Absorption at support → LONG
        if level.absorption_detected and level.direction == "SUPPORT":
            level.entry_direction = "LONG"
            level.stop_price = level.price - 4 * 0.25    # 4 ticks below support
            level.target_price = vah if vah else level.price + (level.score * 2.0)
            level.confidence = min(base_conf + 0.15, 1.0)
            return

        # Absorption at resistance → SHORT
        if level.absorption_detected and level.direction == "RESISTANCE":
            level.entry_direction = "SHORT"
            level.stop_price = level.price + 4 * 0.25
            level.target_price = val if val else level.price - (level.score * 2.0)
            level.confidence = min(base_conf + 0.15, 1.0)
            return

        # Breakthrough of resistance → LONG continuation
        if level.breakthrough_detected and level.direction == "RESISTANCE":
            level.entry_direction = "LONG"
            level.stop_price = level.price - tol         # back below the broken level = stop
            level.target_price = level.price + (level.score * 2.5)
            level.confidence = min(base_conf + 0.10, 1.0)
            return

        # Breakthrough of support → SHORT continuation
        if level.breakthrough_detected and level.direction == "SUPPORT":
            level.entry_direction = "SHORT"
            level.stop_price = level.price + tol
            level.target_price = level.price - (level.score * 2.5)
            level.confidence = min(base_conf + 0.10, 1.0)
            return

        # No absorption/breakthrough — directional bias from approach direction
        dist = current_price - level.price  # positive = price above level

        if level.direction == "RESISTANCE":
            # Price approaching from below = potential short at resistance
            if dist > 0:  # price above level already — skip
                level.entry_direction = "FLAT"
            else:
                level.entry_direction = "SHORT"
                level.stop_price = level.price + 4 * 0.25
                level.target_price = val if val else level.price - (level.score * 2.0)
                level.confidence = base_conf * 0.8

        elif level.direction == "SUPPORT":
            if dist < 0:  # price below level — skip
                level.entry_direction = "FLAT"
            else:
                level.entry_direction = "LONG"
                level.stop_price = level.price - 4 * 0.25
                level.target_price = vah if vah else level.price + (level.score * 2.0)
                level.confidence = base_conf * 0.8

        else:  # BOTH — price at the level
            # Signal based on approach direction from recent prices
            level.entry_direction = "FLAT"
            level.confidence = base_conf * 0.5

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_all_levels(self, market_data: dict) -> List[ConfluenceLevel]:
        """Return all confluence levels found in the current market context.

        Levels are sorted by price (ascending).
        """
        price: float = market_data.get("price", 0.0)
        if price == 0.0:
            return []

        raw_factors = self._extract_factors(market_data, price)
        levels = self._cluster_factors(raw_factors, price)

        price_history: list[float] = market_data.get("price_history", [])
        volume_history: list[float] = market_data.get("volume_history", [])
        volume: float = market_data.get("volume", 0.0)
        vah: Optional[float] = market_data.get("vah")
        val: Optional[float] = market_data.get("val")

        for level in levels:
            self._check_absorption_breakthrough(level, price_history, volume_history, price, volume)
            self._assign_level_signal(level, price, vah, val)

        return sorted(levels, key=lambda l: l.price)

    def get_nearby_levels(
        self,
        price: float,
        market_data: dict,
        radius: float = 5.0,
    ) -> List[ConfluenceLevel]:
        """Return all levels within radius points of price, sorted by distance."""
        all_levels = self.get_all_levels(market_data)
        nearby = [l for l in all_levels if abs(l.price - price) <= radius]
        return sorted(nearby, key=lambda l: abs(l.price - price))

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)

        if price == 0.0:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no price data"},
            )

        # Get all levels, then filter to those nearby
        nearby_levels = self.get_nearby_levels(price, market_data, radius=self.signal_radius)

        if not nearby_levels:
            # Widen radius to find informational context
            all_levels = self.get_all_levels(market_data)
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={
                    "reason": f"no confluence levels within {self.signal_radius} pts",
                    "all_level_count": len(all_levels),
                    "all_levels": [
                        {"price": round(l.price, 2), "score": l.score, "factors": l.factors}
                        for l in all_levels[:10]
                    ],
                },
            )

        # Pick the best level (highest score, then highest confidence)
        best = max(nearby_levels, key=lambda l: (l.score, l.confidence))

        # If multiple levels exist nearby, see if they agree on direction
        actionable = [l for l in nearby_levels if l.entry_direction != "FLAT"]
        if len(actionable) > 1:
            directions = [l.entry_direction for l in actionable]
            if directions.count("LONG") > directions.count("SHORT"):
                consensus = "LONG"
            elif directions.count("SHORT") > directions.count("LONG"):
                consensus = "SHORT"
            else:
                consensus = best.entry_direction
        else:
            consensus = best.entry_direction

        # Build final score from the best level
        if best.score >= 5:
            base_score_mag = 0.80
        elif best.score >= 3:
            base_score_mag = 0.55
        elif best.score >= 2:
            base_score_mag = 0.35
        else:
            base_score_mag = 0.15

        if best.absorption_detected:
            base_score_mag = min(base_score_mag + 0.15, 1.0)
        if best.breakthrough_detected:
            base_score_mag = min(base_score_mag + 0.10, 1.0)

        if consensus == "LONG":
            score = base_score_mag
            direction = "LONG"
        elif consensus == "SHORT":
            score = -base_score_mag
            direction = "SHORT"
        else:
            score = 0.0
            direction = "FLAT"

        confidence = best.confidence if direction != "FLAT" else 0.0
        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(np.clip(confidence, 0.0, 1.0))

        # Entry / stop / target come from the best level
        entry_price: Optional[float] = price if direction != "FLAT" else None
        stop_price: Optional[float] = best.stop_price if direction != "FLAT" and best.stop_price != 0.0 else None
        target_price: Optional[float] = best.target_price if direction != "FLAT" and best.target_price != 0.0 else None

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "best_level_price": round(best.price, 2),
                "best_level_score": best.score,
                "best_level_factors": best.factors,
                "best_level_direction": best.direction,
                "absorption": best.absorption_detected,
                "breakthrough": best.breakthrough_detected,
                "consensus": consensus,
                "nearby_count": len(nearby_levels),
                "nearby_levels": [
                    {
                        "price": round(l.price, 2),
                        "score": l.score,
                        "factors": l.factors,
                        "direction": l.direction,
                        "entry_dir": l.entry_direction,
                        "absorption": l.absorption_detected,
                        "breakthrough": l.breakthrough_detected,
                        "confidence": round(l.confidence, 3),
                    }
                    for l in nearby_levels
                ],
            },
        )
