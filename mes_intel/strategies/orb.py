"""Opening Range Breakout (ORB) Strategy.

Trades breakouts of the first 30-minute session range on MES futures.
Handles volume confirmation, failed-breakout fade signals, time-of-day
filtering, and Fibonacci extension targets.
"""
from __future__ import annotations

import statistics
from datetime import datetime, time, timezone
from typing import Optional

import numpy as np

from .base import Strategy, StrategyResult


class ORBStrategy(Strategy):
    """Opening Range Breakout — first 30-minute high/low as reference."""

    name = "orb"
    description = "ORB: breakout/fade of the 30-min opening range with volume confirmation"

    # CME / ET session open
    SESSION_OPEN_ET = time(9, 30)
    SESSION_CUTOFF_ET = time(11, 30)  # only trade ORBs in first 2 hours

    def __init__(
        self,
        volume_confirmation_ratio: float = 1.5,
        fib_extension: float = 1.618,
        failed_reversal_ticks: float = 2.0,   # points back inside OR = failed breakout
        min_or_range: float = 2.0,            # minimum OR width in points to be tradable
        score_decay_rate: float = 0.3,        # confidence decay per hour past 11:30 ET
    ):
        self.volume_confirmation_ratio = volume_confirmation_ratio
        self.fib_extension = fib_extension
        self.failed_reversal_ticks = failed_reversal_ticks
        self.min_or_range = min_or_range
        self.score_decay_rate = score_decay_rate

    def required_data(self) -> list[str]:
        return [
            "price", "opening_range_high", "opening_range_low",
            "volume", "volume_history", "price_history",
            "session_high", "session_low", "open",
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_avg_volume(self, volume_history: list[float]) -> float:
        """Estimate average bar volume from history."""
        if not volume_history:
            return 1.0
        tail = volume_history[-60:] if len(volume_history) >= 60 else volume_history
        return statistics.mean(tail) if tail else 1.0

    def _time_confidence_factor(self, now_et_hour_frac: float) -> float:
        """
        Returns 1.0 at open (9:30), linearly decays to ~0.4 by 11:30,
        and continues decaying beyond that window.

        now_et_hour_frac: fractional hours since midnight ET, e.g. 10.5 = 10:30
        """
        open_hf = 9.5   # 9:30 ET
        cutoff_hf = 11.5  # 11:30 ET

        if now_et_hour_frac < open_hf:
            return 0.0   # market not open yet
        if now_et_hour_frac <= cutoff_hf:
            # linear from 1.0 down to 0.6 within the prime window
            progress = (now_et_hour_frac - open_hf) / (cutoff_hf - open_hf)
            return 1.0 - 0.4 * progress
        else:
            # past 11:30 — further decay
            hours_past = now_et_hour_frac - cutoff_hf
            return max(0.1, 0.6 - self.score_decay_rate * hours_past)

    def _gap_type_factor(self, open_price: float, orh: float, orl: float, pdh: float, pdl: float, pdc: float) -> float:
        """
        Gap-and-go opens tend to produce stronger ORBs.
        Returns a confidence multiplier: 1.0 to 1.3.
        """
        gap_up = open_price > pdc + 2.0
        gap_down = open_price < pdc - 2.0
        gap_through_pdh = open_price > pdh
        gap_through_pdl = open_price < pdl

        if gap_through_pdh or gap_through_pdl:
            return 1.3   # powerful gap — high follow-through probability
        if gap_up or gap_down:
            return 1.15  # moderate gap
        return 1.0       # flat open — ORB still valid but less momentum bias

    def _detect_failed_breakout(
        self,
        price: float,
        price_history: list[float],
        orh: float,
        orl: float,
    ) -> Optional[str]:
        """
        Checks if price broke out then reversed back inside the opening range.

        Returns 'FAILED_LONG' if price broke above ORH then came back inside,
                'FAILED_SHORT' if price broke below ORL then came back inside,
                None otherwise.

        Uses last 3 bars of price_history to identify the two-bar reversal pattern.
        """
        if len(price_history) < 3:
            return None

        recent = price_history[-3:]

        # Check for failed upside breakout: previous bar was above ORH, now price back below
        prev_bar_above = any(p > orh for p in recent[:-1])
        now_inside = price < orh - self.failed_reversal_ticks

        if prev_bar_above and now_inside and price > orl:
            return "FAILED_LONG"

        # Check for failed downside breakout
        prev_bar_below = any(p < orl for p in recent[:-1])
        now_inside_low = price > orl + self.failed_reversal_ticks

        if prev_bar_below and now_inside_low and price < orh:
            return "FAILED_SHORT"

        return None

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        orh: Optional[float] = market_data.get("opening_range_high")
        orl: Optional[float] = market_data.get("opening_range_low")

        if orh is None or orl is None or price == 0.0:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "opening range not yet established"},
            )

        or_range = orh - orl
        or_mid = (orh + orl) / 2.0

        if or_range < self.min_or_range:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": f"OR range too narrow ({or_range:.2f} pts)"},
            )

        volume: float = market_data.get("volume", 0.0)
        volume_history: list[float] = market_data.get("volume_history", [])
        price_history: list[float] = market_data.get("price_history", [])
        open_price: float = market_data.get("open", price)
        pdh: float = market_data.get("prior_day_high", orh + 10)
        pdl: float = market_data.get("prior_day_low", orl - 10)
        pdc: float = market_data.get("prior_day_close", open_price)

        avg_volume = self._session_avg_volume(volume_history)
        volume_ratio = volume / avg_volume if avg_volume > 0 else 1.0
        volume_confirmed = volume_ratio >= self.volume_confirmation_ratio

        # --- Time-of-day factor ---
        # Try to pull from market_data or use current UTC-5 (approximate ET)
        now_ts: Optional[float] = market_data.get("timestamp")
        if now_ts is not None:
            dt_utc = datetime.fromtimestamp(now_ts, tz=timezone.utc)
            et_hour_frac = (dt_utc.hour - 5) + dt_utc.minute / 60.0  # crude ET offset
        else:
            # Estimate from session_high/low progression as proxy for time
            # If we have no clock, use a mid-session default factor
            et_hour_frac = 10.5  # assume mid-prime-window
        time_factor = self._time_confidence_factor(et_hour_frac)

        # --- Gap type factor ---
        gap_factor = self._gap_type_factor(open_price, orh, orl, pdh, pdl, pdc)

        # --- Failed breakout detection ---
        failed = self._detect_failed_breakout(price, price_history, orh, orl)

        # ----------------------------------------------------------------
        # Signal generation
        # ----------------------------------------------------------------
        score = 0.0
        confidence = 0.0
        direction = "FLAT"
        entry_price: Optional[float] = None
        stop_price: Optional[float] = None
        target_price: Optional[float] = None
        meta_parts: dict = {
            "or_range": round(or_range, 2),
            "or_high": round(orh, 2),
            "or_low": round(orl, 2),
            "volume_ratio": round(volume_ratio, 2),
            "volume_confirmed": volume_confirmed,
            "time_factor": round(time_factor, 2),
            "gap_factor": round(gap_factor, 2),
            "failed_breakout": failed,
        }

        if time_factor < 0.05:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={**meta_parts, "reason": "outside tradeable session window"},
            )

        # --- Case 1: Failed breakout fade ---
        if failed == "FAILED_LONG":
            # Price broke above ORH then reversed — fade the move SHORT
            score = -0.65
            # Confidence: higher if volume was large on the failed break (exhaustion)
            vol_conf_bonus = 0.15 if volume_confirmed else 0.0
            confidence = min((0.50 + vol_conf_bonus) * time_factor, 1.0)
            direction = "SHORT"
            entry_price = price
            stop_price = orh + 2.0          # stop above the false breakout level
            target_price = orl              # target is bottom of OR
            meta_parts["reason"] = "failed upside ORB — fade SHORT"

        elif failed == "FAILED_SHORT":
            score = 0.65
            vol_conf_bonus = 0.15 if volume_confirmed else 0.0
            confidence = min((0.50 + vol_conf_bonus) * time_factor, 1.0)
            direction = "LONG"
            entry_price = price
            stop_price = orl - 2.0
            target_price = orh
            meta_parts["reason"] = "failed downside ORB — fade LONG"

        # --- Case 2: Valid breakout above ORH ---
        elif price > orh:
            raw_score = 0.55
            if volume_confirmed:
                raw_score += 0.25
            raw_score = min(raw_score * gap_factor, 1.0)
            score = raw_score
            confidence = min(raw_score * time_factor, 1.0)
            direction = "LONG"
            entry_price = price            # enter at breakout close or retest
            stop_price = or_mid            # stop at OR midpoint
            extension = or_range * self.fib_extension
            target_price = orl + extension  # OR low + full extension (= ORH + (range × 0.618) approx)
            meta_parts["reason"] = (
                f"ORH breakout {'with' if volume_confirmed else 'without'} volume"
            )

        # --- Case 3: Valid breakdown below ORL ---
        elif price < orl:
            raw_score = 0.55
            if volume_confirmed:
                raw_score += 0.25
            raw_score = min(raw_score * gap_factor, 1.0)
            score = -raw_score
            confidence = min(raw_score * time_factor, 1.0)
            direction = "SHORT"
            entry_price = price
            stop_price = or_mid
            extension = or_range * self.fib_extension
            target_price = orh - extension  # ORH minus full extension
            meta_parts["reason"] = (
                f"ORL breakdown {'with' if volume_confirmed else 'without'} volume"
            )

        # --- Case 4: Price inside OR — no trade ---
        else:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={**meta_parts, "reason": "price inside opening range — no breakout"},
            )

        # Clamp
        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(np.clip(confidence, 0.0, 1.0))

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta=meta_parts,
        )
