"""VWAP Standard Deviation Bands Strategy.

Mean-reversion when price is at ±2σ in range regimes.
Trend-following breakout when price closes outside ±2σ with volume in trending regimes.
Detects band squeezes and VWAP reclaim patterns.
"""
from __future__ import annotations

import statistics
from typing import Optional

import numpy as np

from .base import Strategy, StrategyResult


class VWAPBandsStrategy(Strategy):
    """VWAP ± 1σ / 2σ / 3σ band mean reversion and breakout signals."""

    name = "vwap_bands"
    description = "VWAP standard deviation bands: mean reversion (range) + breakout (trend)"

    def __init__(
        self,
        reversion_band: float = 2.0,       # σ level to trigger mean reversion
        breakout_band: float = 2.0,         # σ level to trigger trend follow
        volume_breakout_ratio: float = 1.4, # volume multiple vs avg needed for breakout
        squeeze_lookback: int = 20,         # bars to compare for squeeze detection
        squeeze_threshold: float = 0.3,     # band width must shrink by this fraction
        reclaim_lookback: int = 5,          # bars to look for VWAP reclaim pattern
    ):
        self.reversion_band = reversion_band
        self.breakout_band = breakout_band
        self.volume_breakout_ratio = volume_breakout_ratio
        self.squeeze_lookback = squeeze_lookback
        self.squeeze_threshold = squeeze_threshold
        self.reclaim_lookback = reclaim_lookback

    def required_data(self) -> list[str]:
        return ["price", "vwap", "price_history", "volume_history", "volume", "regime"]

    # ------------------------------------------------------------------
    # Band computation
    # ------------------------------------------------------------------

    def _compute_bands(
        self, price_history: list[float], volume_history: list[float], vwap: float
    ) -> dict[str, float]:
        """
        Compute anchored VWAP standard deviation bands from session data.

        Returns dict with keys: sigma, band_1up, band_1dn, band_2up, band_2dn,
        band_3up, band_3dn, band_width (= 2 * 2σ).
        """
        n = min(len(price_history), len(volume_history))
        if n < 5:
            # Fallback: rough estimate using price stdev
            if len(price_history) >= 5:
                sigma = statistics.stdev(price_history[-30:]) if len(price_history) >= 30 else statistics.stdev(price_history)
            else:
                sigma = 5.0  # default

            return {
                "sigma": sigma,
                "band_1up": vwap + sigma,
                "band_1dn": vwap - sigma,
                "band_2up": vwap + 2 * sigma,
                "band_2dn": vwap - 2 * sigma,
                "band_3up": vwap + 3 * sigma,
                "band_3dn": vwap - 3 * sigma,
                "band_width": 4 * sigma,
            }

        prices = np.array(price_history[-n:], dtype=float)
        volumes = np.array(volume_history[-n:], dtype=float)

        # Volume-weighted variance around VWAP
        total_vol = volumes.sum()
        if total_vol <= 0:
            total_vol = 1.0
        vw_var = float(np.sum(volumes * (prices - vwap) ** 2) / total_vol)
        sigma = float(np.sqrt(max(vw_var, 0.01)))

        return {
            "sigma": sigma,
            "band_1up": vwap + sigma,
            "band_1dn": vwap - sigma,
            "band_2up": vwap + 2 * sigma,
            "band_2dn": vwap - 2 * sigma,
            "band_3up": vwap + 3 * sigma,
            "band_3dn": vwap - 3 * sigma,
            "band_width": 4 * sigma,   # 2σ envelope width
        }

    def _distance_in_sigma(self, price: float, vwap: float, sigma: float) -> float:
        """Signed distance from VWAP in units of sigma. Positive = above VWAP."""
        if sigma <= 0:
            return 0.0
        return (price - vwap) / sigma

    def _detect_squeeze(self, price_history: list[float], volume_history: list[float], vwap: float, current_sigma: float) -> bool:
        """
        Squeeze: current band width is significantly narrower than N bars ago.
        Uses the lookback window sigma vs the most recent sigma.
        """
        n = self.squeeze_lookback
        if len(price_history) < n + 5 or len(volume_history) < n + 5:
            return False

        ph_old = price_history[-(n + 5):-5]
        vh_old = volume_history[-(n + 5):-5]

        old_bands = self._compute_bands(ph_old, vh_old, vwap)
        old_sigma = old_bands["sigma"]

        if old_sigma <= 0:
            return False

        # Squeeze if current bands are significantly tighter
        ratio = current_sigma / old_sigma
        return ratio < (1.0 - self.squeeze_threshold)

    def _detect_vwap_reclaim(
        self, price_history: list[float], vwap: float
    ) -> Optional[str]:
        """
        VWAP reclaim: price was on one side of VWAP and crosses back.

        Returns 'RECLAIM_LONG' if price dipped below VWAP then reclaimed it,
                'RECLAIM_SHORT' if price rose above VWAP then lost it,
                None if no pattern.
        """
        n = self.reclaim_lookback
        if len(price_history) < n + 1:
            return None

        recent = price_history[-(n + 1):]
        current = recent[-1]
        prior = recent[:-1]

        was_below = any(p < vwap for p in prior)
        was_above = any(p > vwap for p in prior)

        if was_below and current > vwap:
            return "RECLAIM_LONG"
        if was_above and current < vwap:
            return "RECLAIM_SHORT"
        return None

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        vwap: Optional[float] = market_data.get("vwap")
        price_history: list[float] = market_data.get("price_history", [])
        volume_history: list[float] = market_data.get("volume_history", [])
        volume: float = market_data.get("volume", 0.0)
        regime: str = market_data.get("regime", "UNKNOWN")

        if vwap is None or price == 0.0 or len(price_history) < 10:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient data for VWAP bands"},
            )

        bands = self._compute_bands(price_history, volume_history, vwap)
        sigma = bands["sigma"]
        dist_sigma = self._distance_in_sigma(price, vwap, sigma)

        # Average volume for breakout confirmation
        avg_vol = float(np.mean(volume_history[-30:])) if len(volume_history) >= 30 else (
            float(np.mean(volume_history)) if volume_history else 1.0
        )
        vol_ratio = volume / avg_vol if avg_vol > 0 else 1.0
        volume_confirmed = vol_ratio >= self.volume_breakout_ratio

        squeeze = self._detect_squeeze(price_history, volume_history, vwap, sigma)
        reclaim = self._detect_vwap_reclaim(price_history, vwap)

        score = 0.0
        confidence = 0.0
        direction = "FLAT"
        entry_price: Optional[float] = None
        stop_price: Optional[float] = None
        target_price: Optional[float] = None
        signals: list[str] = []

        abs_dist = abs(dist_sigma)

        # ----------------------------------------------------------------
        # Regime-aware signal logic
        # ----------------------------------------------------------------

        if regime in ("RANGE", "UNKNOWN"):
            # --- Mean reversion signals ---
            if dist_sigma >= self.reversion_band:
                # Price at or above +2σ — fade SHORT toward VWAP
                # Score magnitude scales with how far past 2σ
                excess = abs_dist - self.reversion_band
                raw_score = -(0.5 + min(excess * 0.15, 0.4))
                score = raw_score
                confidence = min(0.45 + excess * 0.1, 0.85)
                direction = "SHORT"
                entry_price = price
                stop_price = bands["band_3up"]        # invalidated if price reaches 3σ
                target_price = vwap                   # mean reversion target
                signals.append(f"fade +{abs_dist:.1f}σ — reversion SHORT")

            elif dist_sigma <= -self.reversion_band:
                excess = abs_dist - self.reversion_band
                raw_score = 0.5 + min(excess * 0.15, 0.4)
                score = raw_score
                confidence = min(0.45 + excess * 0.1, 0.85)
                direction = "LONG"
                entry_price = price
                stop_price = bands["band_3dn"]
                target_price = vwap
                signals.append(f"fade -{abs_dist:.1f}σ — reversion LONG")

            elif abs_dist >= 1.0:
                # Between 1σ and 2σ — weak fade signal
                raw_score = -(dist_sigma / self.reversion_band) * 0.35
                score = float(np.clip(raw_score, -1.0, 1.0))
                direction = "SHORT" if dist_sigma > 0 else "LONG"
                confidence = 0.25
                entry_price = price
                target_price = vwap
                stop_price = (bands["band_2up"] if dist_sigma > 0 else bands["band_2dn"])
                signals.append(f"mild fade at {dist_sigma:+.1f}σ")

        if regime in ("TRENDING_UP", "TRENDING_DOWN"):
            # --- Breakout / trend-following signals ---
            if dist_sigma >= self.breakout_band and volume_confirmed:
                score = min(0.55 + (abs_dist - self.breakout_band) * 0.1, 0.90)
                confidence = min(0.5 + (abs_dist - self.breakout_band) * 0.08, 0.85)
                direction = "LONG"
                entry_price = price
                stop_price = bands["band_1up"]          # retrace back inside 1σ = stop
                target_price = bands["band_3up"]
                signals.append(f"trending breakout +{abs_dist:.1f}σ with volume")

            elif dist_sigma <= -self.breakout_band and volume_confirmed:
                score = -(min(0.55 + (abs_dist - self.breakout_band) * 0.1, 0.90))
                confidence = min(0.5 + (abs_dist - self.breakout_band) * 0.08, 0.85)
                direction = "SHORT"
                entry_price = price
                stop_price = bands["band_1dn"]
                target_price = bands["band_3dn"]
                signals.append(f"trending breakdown -{abs_dist:.1f}σ with volume")

            elif abs_dist >= self.breakout_band and not volume_confirmed:
                # No volume on breakout — suspect, reduce score
                sign = 1.0 if dist_sigma > 0 else -1.0
                score = sign * 0.25
                confidence = 0.25
                direction = "LONG" if sign > 0 else "SHORT"
                signals.append(f"breakout {dist_sigma:+.1f}σ — LOW VOLUME")

        # --- VWAP reclaim overlay (always applies) ---
        if reclaim == "RECLAIM_LONG":
            reclaim_boost = 0.25
            score = float(np.clip(score + reclaim_boost, -1.0, 1.0))
            confidence = min(confidence + 0.15, 1.0)
            if direction == "FLAT":
                direction = "LONG"
                entry_price = price
                stop_price = vwap - sigma * 0.5
                target_price = bands["band_1up"]
            signals.append("VWAP reclaim LONG")

        elif reclaim == "RECLAIM_SHORT":
            reclaim_boost = -0.25
            score = float(np.clip(score + reclaim_boost, -1.0, 1.0))
            confidence = min(confidence + 0.15, 1.0)
            if direction == "FLAT":
                direction = "SHORT"
                entry_price = price
                stop_price = vwap + sigma * 0.5
                target_price = bands["band_1dn"]
            signals.append("VWAP reclaim SHORT")

        # --- Squeeze flag: lower confidence (expect breakout, not reversion) ---
        if squeeze:
            confidence *= 0.7
            signals.append("band squeeze — breakout imminent")

        # If score too small, stay flat
        if abs(score) < 0.1:
            direction = "FLAT"
            entry_price = None
            stop_price = None
            target_price = None

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
            meta={
                "vwap": round(vwap, 2),
                "sigma": round(sigma, 3),
                "dist_sigma": round(dist_sigma, 3),
                "band_1up": round(bands["band_1up"], 2),
                "band_1dn": round(bands["band_1dn"], 2),
                "band_2up": round(bands["band_2up"], 2),
                "band_2dn": round(bands["band_2dn"], 2),
                "band_3up": round(bands["band_3up"], 2),
                "band_3dn": round(bands["band_3dn"], 2),
                "vol_ratio": round(vol_ratio, 2),
                "volume_confirmed": volume_confirmed,
                "squeeze": squeeze,
                "reclaim": reclaim,
                "regime": regime,
                "signals": signals,
            },
        )
