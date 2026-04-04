"""Advanced Volume Profile Strategy — naked POC, profile shape, poor highs/lows, POC migration.

Goes beyond basic auction theory by analyzing the *structure* of the volume profile
itself: unfinished business (naked POCs), distribution shape (P/b/D), poor highs/lows
that invite re-test, and POC migration speed as a trend quality metric.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class VolumeProfileAdvancedStrategy(Strategy):
    """Advanced volume profile structural analysis."""

    name = "volume_profile_advanced"
    description = "Naked POC, profile shape (P/b/D), poor highs/lows, POC migration speed"

    # Tuneable thresholds
    NAKED_POC_TOLERANCE = 2.0     # points — POC considered "tested" if price came within this
    POOR_EXTREME_VOLUME_PCT = 0.25  # volume at extreme vs POC volume — below this = "poor"
    MIGRATION_LOOKBACK = 10       # bars to measure POC migration
    SHAPE_SKEW_THRESHOLD = 0.3    # |skew| > this to classify P-shape or b-shape

    def required_data(self) -> list[str]:
        return [
            "price", "volume_profile", "price_history",
            "session_high", "session_low",
            "poc", "vah", "val",
        ]

    # ------------------------------------------------------------------
    # Profile shape classification
    # ------------------------------------------------------------------

    def _classify_shape(self, volume_profile) -> tuple[str, float]:
        """Classify the volume profile distribution shape.

        Returns (shape, skew):
          'P' — high-volume node at top (long liquidation / short covering)
          'b' — high-volume node at bottom (accumulation / long building)
          'D' — normal/bell — balanced two-way trade
          'B' — bimodal — two high-volume nodes (double distribution)
        """
        if volume_profile is None:
            return "D", 0.0

        levels = volume_profile.sorted_levels()
        if len(levels) < 5:
            return "D", 0.0

        prices = [l.price for l in levels]
        volumes = [l.total_volume for l in levels]
        total_vol = sum(volumes)
        if total_vol == 0:
            return "D", 0.0

        # Volume-weighted mean and skew
        vw_mean = sum(p * v for p, v in zip(prices, volumes)) / total_vol
        price_range = prices[-1] - prices[0]
        if price_range == 0:
            return "D", 0.0

        # Normalize position of volume-weighted center: 0 = bottom, 1 = top
        center_pct = (vw_mean - prices[0]) / price_range

        # Check for bimodal (two peaks separated by a valley)
        poc_vol = max(volumes)
        half = len(volumes) // 2
        upper_max = max(volumes[half:])
        lower_max = max(volumes[:half])
        valley_min = min(volumes[max(1, half - 2):min(len(volumes) - 1, half + 3)])

        if (min(upper_max, lower_max) > poc_vol * 0.6 and
                valley_min < poc_vol * 0.35):
            return "B", center_pct - 0.5

        # Skew-based classification
        skew = center_pct - 0.5  # positive = volume skewed high, negative = skewed low
        if skew > self.SHAPE_SKEW_THRESHOLD:
            return "P", skew
        elif skew < -self.SHAPE_SKEW_THRESHOLD:
            return "b", skew
        else:
            return "D", skew

    # ------------------------------------------------------------------
    # Poor highs / poor lows
    # ------------------------------------------------------------------

    def _detect_poor_extremes(self, volume_profile, session_high: float,
                               session_low: float) -> tuple[bool, bool]:
        """Detect poor highs and poor lows.

        A 'poor' extreme has relatively high volume — meaning the market did NOT
        reject that price efficiently. It invites a re-test.
        """
        if volume_profile is None:
            return False, False

        levels = volume_profile.sorted_levels()
        if not levels:
            return False, False

        poc_vol = max(l.total_volume for l in levels)
        if poc_vol == 0:
            return False, False

        # Check volume at session extremes
        poor_high = False
        poor_low = False

        for level in levels:
            vol_ratio = level.total_volume / poc_vol
            if abs(level.price - session_high) <= 1.0:
                if vol_ratio > self.POOR_EXTREME_VOLUME_PCT:
                    poor_high = True
            if abs(level.price - session_low) <= 1.0:
                if vol_ratio > self.POOR_EXTREME_VOLUME_PCT:
                    poor_low = True

        return poor_high, poor_low

    # ------------------------------------------------------------------
    # Naked POC detection
    # ------------------------------------------------------------------

    def _find_naked_pocs(self, price: float, price_history: list[float],
                          poc: float) -> tuple[bool, float]:
        """Check if prior POC levels remain untested ('naked').

        Returns (is_near_naked_poc, distance_to_nearest).
        A naked POC acts as a magnet — price tends to return to it.
        """
        # Simple version: check if current POC has been tested from the other side
        if not price_history or poc == 0:
            return False, 0.0

        # Was price recently on the opposite side of POC and hasn't crossed back?
        recent = price_history[-20:]
        was_above = any(p > poc + self.NAKED_POC_TOLERANCE for p in recent[:10])
        was_below = any(p < poc - self.NAKED_POC_TOLERANCE for p in recent[:10])

        dist = abs(price - poc)

        if price > poc and was_below and dist > self.NAKED_POC_TOLERANCE:
            return True, dist
        if price < poc and was_above and dist > self.NAKED_POC_TOLERANCE:
            return True, dist

        return False, dist

    # ------------------------------------------------------------------
    # POC migration speed
    # ------------------------------------------------------------------

    def _poc_migration(self, price_history: list[float], volume_profile) -> float:
        """Estimate POC migration speed/direction.

        Fast migration = strong trend. Slow/no migration = balance.
        Returns normalized speed [-1, 1]: positive = POC migrating up.
        """
        if volume_profile is None or len(price_history) < self.MIGRATION_LOOKBACK:
            return 0.0

        # Use VWAP-like center of recent price windows as POC proxy
        half = self.MIGRATION_LOOKBACK // 2
        early = price_history[-self.MIGRATION_LOOKBACK:-half]
        late = price_history[-half:]

        if not early or not late:
            return 0.0

        early_center = statistics.mean(early)
        late_center = statistics.mean(late)

        price_range = max(price_history[-self.MIGRATION_LOOKBACK:]) - min(price_history[-self.MIGRATION_LOOKBACK:])
        if price_range == 0:
            return 0.0

        migration = (late_center - early_center) / price_range
        return max(-1.0, min(1.0, migration * 2.0))

    # ------------------------------------------------------------------
    # HVN / LVN proximity
    # ------------------------------------------------------------------

    def _hvn_lvn_score(self, price: float, volume_profile) -> tuple[float, str]:
        """Score based on proximity to High Volume Nodes vs Low Volume Nodes.

        At HVN: mean reversion zone, price tends to consolidate.
        At LVN: price tends to move through quickly — breakout/breakdown zone.

        Returns (score, description).
        """
        if volume_profile is None:
            return 0.0, "no_profile"

        hvns = volume_profile.top_volume_levels(5)
        lvns = volume_profile.low_volume_nodes(0.15)

        nearest_hvn_dist = float("inf")
        nearest_lvn_dist = float("inf")

        for level in hvns:
            d = abs(price - level.price)
            if d < nearest_hvn_dist:
                nearest_hvn_dist = d

        for level in lvns:
            d = abs(price - level.price)
            if d < nearest_lvn_dist:
                nearest_lvn_dist = d

        if nearest_hvn_dist < 1.5:
            return -0.15, f"at_HVN (consolidation zone, dist={nearest_hvn_dist:.1f})"
        elif nearest_lvn_dist < 1.5:
            return 0.0, f"at_LVN (fast-move zone, dist={nearest_lvn_dist:.1f})"
        else:
            return 0.0, "between_nodes"

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        volume_profile = market_data.get("volume_profile")
        price_history = market_data.get("price_history", [])
        session_high = market_data.get("session_high", price)
        session_low = market_data.get("session_low", price)
        poc = market_data.get("poc", 0.0)
        vah = market_data.get("vah")
        val = market_data.get("val")

        if price == 0.0 or volume_profile is None:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no volume profile data"},
            )

        signals: list[tuple[str, float, float]] = []  # (name, score, weight)
        notes: list[str] = []

        # 1. Profile shape
        shape, skew = self._classify_shape(volume_profile)
        if shape == "P":
            signals.append(("p_shape", -0.35, 0.20))
            notes.append(f"P-shape (volume at top, skew={skew:.2f}) — bearish liquidation")
        elif shape == "b":
            signals.append(("b_shape", 0.35, 0.20))
            notes.append(f"b-shape (volume at bottom, skew={skew:.2f}) — bullish accumulation")
        elif shape == "B":
            signals.append(("bimodal", 0.0, 0.10))
            notes.append("B-shape (double distribution) — range bound, wait for breakout")
        else:
            notes.append("D-shape (normal distribution) — balanced")

        # 2. Poor highs / poor lows
        poor_high, poor_low = self._detect_poor_extremes(
            volume_profile, session_high, session_low
        )
        if poor_high:
            signals.append(("poor_high", 0.25, 0.20))
            notes.append("Poor high — unfinished business above, likely retest")
        if poor_low:
            signals.append(("poor_low", -0.25, 0.20))
            notes.append("Poor low — unfinished business below, likely retest")

        # 3. Naked POC magnet
        near_naked, naked_dist = self._find_naked_pocs(price, price_history, poc)
        if near_naked and naked_dist > 0:
            direction_to_poc = 1.0 if poc > price else -1.0
            magnitude = min(0.30, 0.10 + naked_dist / 20.0)
            signals.append(("naked_poc", direction_to_poc * magnitude, 0.20))
            notes.append(f"Naked POC @ {poc:.2f} ({naked_dist:.1f}pts away) — magnet")

        # 4. POC migration speed
        migration = self._poc_migration(price_history, volume_profile)
        if abs(migration) > 0.3:
            signals.append(("poc_migration", migration * 0.30, 0.15))
            direction_str = "up" if migration > 0 else "down"
            notes.append(f"POC migrating {direction_str} (speed={migration:.2f}) — trend quality")

        # 5. HVN / LVN proximity
        hvn_lvn_score, hvn_lvn_desc = self._hvn_lvn_score(price, volume_profile)
        if abs(hvn_lvn_score) > 0:
            signals.append(("hvn_lvn", hvn_lvn_score, 0.10))
            notes.append(hvn_lvn_desc)

        # 6. Value area width (volatility proxy)
        if vah is not None and val is not None and vah > val:
            va_width = vah - val
            if va_width < 3.0:
                notes.append(f"Tight VA ({va_width:.1f}pts) — compression, expect expansion")
            elif va_width > 12.0:
                notes.append(f"Wide VA ({va_width:.1f}pts) — high participation, watch for balance")

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"shape": shape, "notes": notes},
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))

        # Confidence: number of confirming signals + magnitude
        confirming = sum(1 for _, s, _ in signals if s * score > 0)
        confidence = min(1.0, 0.20 + 0.15 * confirming + 0.3 * abs(score))

        if abs(score) < 0.10:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        entry_price = price if direction != "FLAT" else None
        stop_price = None
        target_price = None
        if direction == "LONG" and val is not None:
            stop_price = round(val - 1.5, 2)
            target_price = round(vah, 2) if vah and price < vah else round(price + 6, 2)
        elif direction == "SHORT" and vah is not None:
            stop_price = round(vah + 1.5, 2)
            target_price = round(val, 2) if val and price > val else round(price - 6, 2)

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "shape": shape,
                "skew": round(skew, 3),
                "poor_high": poor_high,
                "poor_low": poor_low,
                "near_naked_poc": near_naked,
                "poc_migration_speed": round(migration, 3),
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
