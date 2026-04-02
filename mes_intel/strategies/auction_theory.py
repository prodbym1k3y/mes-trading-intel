"""Auction Theory Strategy — Market Profile auction framework.

Models price as an auction process: two-timeframe (balance) vs one-timeframe
(imbalance) markets, initiative vs responsive participants, and failed auction signals.

Reference levels: Value Area High/Low, Prior Day H/L/C, Weekly H/L.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .base import Strategy, StrategyResult


@dataclass
class AuctionContext:
    """Classifies current price action in Market Profile auction terms."""
    market_mode: str          # 'TWO_TIMEFRAME' (balance) or 'ONE_TIMEFRAME' (imbalance)
    position: str             # 'INSIDE_VA', 'ABOVE_VA', 'BELOW_VA'
    initiative: Optional[str] # 'BUYING' or 'SELLING' — initiative participant detected
    responsive: Optional[str] # 'BUYING' or 'SELLING' — responsive participant detected
    at_reference: Optional[str]  # which reference level is being tested, if any
    excess: bool              # excess wick / termination signal detected


class AuctionTheoryStrategy(Strategy):
    """Market Profile auction theory: balance/imbalance, initiative/responsive."""

    name = "auction_theory"
    description = "Market Profile auction theory: value area balance, initiative/responsive participants"

    def __init__(
        self,
        reference_tolerance: float = 1.5,   # points within which price "tests" a reference
        excess_wick_ratio: float = 0.35,     # wick / total range ratio to call "excess"
        failed_auction_bars: int = 2,        # price spends ≤ N bars at reference = failed
        min_bars_outside: int = 3,           # bars outside VA to confirm one-timeframe
        volume_reference_ratio: float = 1.5, # volume at reference vs avg to confirm responsive
    ):
        self.reference_tolerance = reference_tolerance
        self.excess_wick_ratio = excess_wick_ratio
        self.failed_auction_bars = failed_auction_bars
        self.min_bars_outside = min_bars_outside
        self.volume_reference_ratio = volume_reference_ratio

    def required_data(self) -> list[str]:
        return [
            "price", "high", "low", "open",
            "vah", "val", "poc",
            "prior_day_high", "prior_day_low", "prior_day_close",
            "weekly_high", "weekly_low",
            "price_history", "volume_history", "delta_history",
            "session_high", "session_low",
            "delta", "volume",
        ]

    # ------------------------------------------------------------------
    # Reference level map
    # ------------------------------------------------------------------

    def _get_reference_levels(self, md: dict) -> dict[str, float]:
        """Collect all reference levels that are present in market_data."""
        levels: dict[str, float] = {}
        for key, label in [
            ("vah", "VAH"), ("val", "VAL"), ("poc", "POC"),
            ("prior_day_high", "PDH"), ("prior_day_low", "PDL"), ("prior_day_close", "PDC"),
            ("weekly_high", "WKH"), ("weekly_low", "WKL"),
            ("opening_range_high", "ORH"), ("opening_range_low", "ORL"),
        ]:
            v = md.get(key)
            if v is not None:
                levels[label] = float(v)
        return levels

    def _nearest_reference(
        self,
        price: float,
        references: dict[str, float],
    ) -> tuple[Optional[str], float]:
        """Return (label, distance) for the closest reference level."""
        best_label: Optional[str] = None
        best_dist = float("inf")
        for label, level in references.items():
            d = abs(price - level)
            if d < best_dist:
                best_dist = d
                best_label = label
        return best_label, best_dist

    # ------------------------------------------------------------------
    # Market mode classification
    # ------------------------------------------------------------------

    def _classify_market_mode(
        self,
        price: float,
        price_history: list[float],
        vah: float,
        val: float,
        regime: str,
    ) -> str:
        """
        TWO_TIMEFRAME (balance): price oscillates within value area.
        ONE_TIMEFRAME (imbalance): price moving directionally outside VA.
        """
        # Quick regime check
        if regime in ("TRENDING_UP", "TRENDING_DOWN"):
            return "ONE_TIMEFRAME"

        if not price_history:
            return "TWO_TIMEFRAME" if val <= price <= vah else "ONE_TIMEFRAME"

        recent = price_history[-self.min_bars_outside:]
        if val <= price <= vah:
            # Price in VA — check if it's been oscillating (TWO_TIMEFRAME)
            va_bounces = sum(1 for p in recent if val <= p <= vah)
            if va_bounces >= len(recent) * 0.6:
                return "TWO_TIMEFRAME"

        # Check how many recent bars are outside the VA
        outside_count = sum(1 for p in recent if p < val or p > vah)
        if outside_count >= self.min_bars_outside * 0.7:
            return "ONE_TIMEFRAME"

        return "TWO_TIMEFRAME"

    # ------------------------------------------------------------------
    # Initiative / responsive detection
    # ------------------------------------------------------------------

    def _detect_initiative(
        self,
        price: float,
        price_history: list[float],
        references: dict[str, float],
    ) -> Optional[str]:
        """
        Initiative buying: price pushes above PDH or weekly_high — new price discovery.
        Initiative selling: price pushes below PDL or weekly_low.

        Returns 'BUYING', 'SELLING', or None.
        """
        pdh = references.get("PDH")
        pdl = references.get("PDL")
        wkh = references.get("WKH")
        wkl = references.get("WKL")

        initiative_up_levels = [l for l in [pdh, wkh] if l is not None]
        initiative_dn_levels = [l for l in [pdl, wkl] if l is not None]

        for level in initiative_up_levels:
            if price > level + self.reference_tolerance * 0.5:
                # Verify it's a recent breakout (wasn't above yesterday)
                if price_history:
                    prev_max = max(price_history[-10:]) if len(price_history) >= 10 else max(price_history)
                    if prev_max <= level + self.reference_tolerance:
                        return "BUYING"
                else:
                    return "BUYING"

        for level in initiative_dn_levels:
            if price < level - self.reference_tolerance * 0.5:
                if price_history:
                    prev_min = min(price_history[-10:]) if len(price_history) >= 10 else min(price_history)
                    if prev_min >= level - self.reference_tolerance:
                        return "SELLING"
                else:
                    return "SELLING"

        return None

    def _detect_responsive(
        self,
        price: float,
        vah: float,
        val: float,
        references: dict[str, float],
        volume: float,
        volume_history: list[float],
        delta: float,
    ) -> Optional[str]:
        """
        Responsive buying: at VAL, PDL, or PDC — value buyers stepping in.
        Responsive selling: at VAH, PDH, or PDC — value sellers stepping in.

        Returns 'BUYING', 'SELLING', or None.
        """
        avg_vol = float(np.mean(volume_history[-20:])) if len(volume_history) >= 20 else (
            float(np.mean(volume_history)) if volume_history else 0.0
        )
        vol_spike = volume > avg_vol * self.volume_reference_ratio if avg_vol > 0 else False

        # Responsive buying levels: VAL, PDL, PDC (if below price)
        buying_levels = [val]
        for key in ("PDL", "PDC"):
            v = references.get(key)
            if v is not None and v <= price + self.reference_tolerance:
                buying_levels.append(v)

        for level in buying_levels:
            if abs(price - level) <= self.reference_tolerance:
                if delta > 0 or vol_spike:
                    return "BUYING"

        # Responsive selling levels: VAH, PDH, PDC (if above price)
        selling_levels = [vah]
        for key in ("PDH", "PDC"):
            v = references.get(key)
            if v is not None and v >= price - self.reference_tolerance:
                selling_levels.append(v)

        for level in selling_levels:
            if abs(price - level) <= self.reference_tolerance:
                if delta < 0 or vol_spike:
                    return "SELLING"

        return None

    # ------------------------------------------------------------------
    # Failed auction detection
    # ------------------------------------------------------------------

    def _detect_failed_auction(
        self,
        price: float,
        price_history: list[float],
        references: dict[str, float],
    ) -> Optional[tuple[str, str]]:
        """
        Failed auction: price reaches a reference, spends ≤ N bars, returns.
        This is a strong signal in the direction AWAY from the failed attempt.

        Returns (reference_label, 'FAILED_UP') or (reference_label, 'FAILED_DOWN') or None.
        """
        if len(price_history) < self.failed_auction_bars + 2:
            return None

        for label, level in references.items():
            # Look in recent history for a probe of this level
            tail = price_history[-(self.failed_auction_bars + 3):]

            at_level = [abs(p - level) <= self.reference_tolerance * 1.5 for p in tail]
            touches = sum(at_level)

            if 0 < touches <= self.failed_auction_bars:
                # Was it above the level before, came down, touched, and now back below?
                first_touch_idx = next(i for i, t in enumerate(at_level) if t)
                if first_touch_idx < len(tail) - 1:
                    after = tail[first_touch_idx + 1 :]
                    if not after:
                        continue
                    # All "after" bars moved away from level
                    if all(abs(p - level) > self.reference_tolerance for p in after):
                        # Determine direction of the failed probe
                        before = tail[:first_touch_idx]
                        if before:
                            approach_from_below = float(np.mean(before)) < level
                            if approach_from_below and price < level - self.reference_tolerance:
                                return (label, "FAILED_UP")
                            elif not approach_from_below and price > level + self.reference_tolerance:
                                return (label, "FAILED_DOWN")

        return None

    # ------------------------------------------------------------------
    # Excess detection
    # ------------------------------------------------------------------

    def _detect_excess(
        self,
        high: float,
        low: float,
        session_high: float,
        session_low: float,
        price: float,
    ) -> bool:
        """
        Excess: extreme wick at session high or low after a one-timeframe move.
        The current bar's wick at the extreme = rejection of that price.
        """
        bar_range = high - low
        if bar_range <= 0:
            return False

        # Upper wick at session high
        if high >= session_high and abs(high - session_high) <= 1.0:
            upper_wick = high - price  # price = close approximation
            if upper_wick / bar_range >= self.excess_wick_ratio:
                return True

        # Lower wick at session low
        if low <= session_low and abs(low - session_low) <= 1.0:
            lower_wick = price - low
            if lower_wick / bar_range >= self.excess_wick_ratio:
                return True

        return False

    # ------------------------------------------------------------------
    # Score from position relative to value area + reference levels
    # ------------------------------------------------------------------

    def _position_score(
        self,
        price: float,
        vah: float,
        val: float,
        poc: float,
        references: dict[str, float],
        market_mode: str,
    ) -> float:
        """
        Computes a directional score purely from price position relative to
        value area and key reference levels, before any initiative/responsive overlay.
        """
        va_range = vah - val
        if va_range <= 0:
            return 0.0

        if val <= price <= vah:
            # Inside value area — slight bias toward POC
            rel_to_poc = (price - poc) / (va_range / 2.0 + 1e-9)
            return float(np.clip(-rel_to_poc * 0.25, -0.3, 0.3))

        if price > vah:
            # Above value area
            dist_above = (price - vah) / va_range
            if market_mode == "ONE_TIMEFRAME":
                # Trend continuation — positive score
                return min(0.3 + dist_above * 0.2, 0.65)
            else:
                # Balance — fade back to value
                return max(-0.3 - dist_above * 0.15, -0.55)

        if price < val:
            dist_below = (val - price) / va_range
            if market_mode == "ONE_TIMEFRAME":
                return -(min(0.3 + dist_below * 0.2, 0.65))
            else:
                return min(0.3 + dist_below * 0.15, 0.55)

        return 0.0

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        high: float = market_data.get("high", price)
        low: float = market_data.get("low", price)
        session_high: float = market_data.get("session_high", high)
        session_low: float = market_data.get("session_low", low)
        vah: Optional[float] = market_data.get("vah")
        val: Optional[float] = market_data.get("val")
        poc: Optional[float] = market_data.get("poc")
        price_history: list[float] = market_data.get("price_history", [])
        volume_history: list[float] = market_data.get("volume_history", [])
        delta_history: list[float] = market_data.get("delta_history", [])
        volume: float = market_data.get("volume", 0.0)
        delta: float = market_data.get("delta", 0.0)
        regime: str = market_data.get("regime", "UNKNOWN")

        if price == 0.0 or vah is None or val is None or poc is None:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "missing value area data"},
            )

        references = self._get_reference_levels(market_data)
        market_mode = self._classify_market_mode(price, price_history, vah, val, regime)

        initiative = self._detect_initiative(price, price_history, references)
        responsive = self._detect_responsive(price, vah, val, references, volume, volume_history, delta)
        failed = self._detect_failed_auction(price, price_history, references)
        excess = self._detect_excess(high, low, session_high, session_low, price)

        # ----------------------------------------------------------------
        # Base score from position
        # ----------------------------------------------------------------
        pos_score = self._position_score(price, vah, val, poc, references, market_mode)
        score = pos_score
        confidence = 0.25  # baseline

        adjustment_log: list[str] = [f"mode={market_mode}"]

        # ----------------------------------------------------------------
        # Initiative participant overlay
        # ----------------------------------------------------------------
        if initiative == "BUYING":
            score = min(score + 0.35, 1.0)
            confidence = min(confidence + 0.25, 1.0)
            adjustment_log.append("initiative buying — join trend LONG")
        elif initiative == "SELLING":
            score = max(score - 0.35, -1.0)
            confidence = min(confidence + 0.25, 1.0)
            adjustment_log.append("initiative selling — join trend SHORT")

        # ----------------------------------------------------------------
        # Responsive participant overlay
        # ----------------------------------------------------------------
        if responsive == "BUYING":
            score = min(score + 0.30, 1.0)
            confidence = min(confidence + 0.20, 1.0)
            adjustment_log.append("responsive buying at value — LONG")
        elif responsive == "SELLING":
            score = max(score - 0.30, -1.0)
            confidence = min(confidence + 0.20, 1.0)
            adjustment_log.append("responsive selling at high — SHORT")

        # ----------------------------------------------------------------
        # Failed auction overlay (strong signal)
        # ----------------------------------------------------------------
        if failed is not None:
            failed_label, failed_dir = failed
            if failed_dir == "FAILED_UP":
                # Failed probe above — bearish
                score = max(score - 0.45, -1.0)
                confidence = min(confidence + 0.30, 1.0)
                adjustment_log.append(f"failed auction UP at {failed_label} — SHORT")
            elif failed_dir == "FAILED_DOWN":
                score = min(score + 0.45, 1.0)
                confidence = min(confidence + 0.30, 1.0)
                adjustment_log.append(f"failed auction DOWN at {failed_label} — LONG")

        # ----------------------------------------------------------------
        # Excess / termination signal
        # ----------------------------------------------------------------
        if excess:
            # Excess wick at extreme = reversal likely
            if high >= session_high - 1.0:
                score = max(score - 0.30, -1.0)
                confidence = min(confidence + 0.15, 1.0)
                adjustment_log.append("excess at session high — termination SHORT")
            elif low <= session_low + 1.0:
                score = min(score + 0.30, 1.0)
                confidence = min(confidence + 0.15, 1.0)
                adjustment_log.append("excess at session low — termination LONG")

        # ----------------------------------------------------------------
        # Conflicting signals reduce confidence
        # ----------------------------------------------------------------
        if initiative and responsive and initiative != responsive:
            confidence *= 0.6
            adjustment_log.append("CONFLICT: initiative vs responsive")

        # ----------------------------------------------------------------
        # Delta confirmation at reference
        # ----------------------------------------------------------------
        avg_vol = float(np.mean(volume_history[-20:])) if len(volume_history) >= 20 else (
            float(np.mean(volume_history)) if volume_history else 0.0
        )
        if avg_vol > 0 and volume > avg_vol * self.volume_reference_ratio:
            near_label, near_dist = self._nearest_reference(price, references)
            if near_dist <= self.reference_tolerance:
                delta_sign = np.sign(delta)
                score_sign = np.sign(score)
                if delta_sign == score_sign:
                    confidence = min(confidence + 0.10, 1.0)
                    adjustment_log.append(f"delta confirms signal at {near_label}")

        # ----------------------------------------------------------------
        # Direction and levels
        # ----------------------------------------------------------------
        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(np.clip(confidence, 0.0, 1.0))

        if abs(score) < 0.15 or confidence < 0.15:
            direction = "FLAT"
            entry_price = stop_price = target_price = None
        elif score > 0:
            direction = "LONG"
            entry_price = price
            stop_price = val - self.reference_tolerance      # below value area
            target_price = vah if price < vah else references.get("PDH", vah + 10)
        else:
            direction = "SHORT"
            entry_price = price
            stop_price = vah + self.reference_tolerance
            target_price = val if price > val else references.get("PDL", val - 10)

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "market_mode": market_mode,
                "vah": vah, "val": val, "poc": poc,
                "initiative": initiative,
                "responsive": responsive,
                "failed_auction": f"{failed[0]}:{failed[1]}" if failed else None,
                "excess": excess,
                "position_score": round(pos_score, 3),
                "adjustments": adjustment_log,
                "references": {k: round(v, 2) for k, v in references.items()},
            },
        )
