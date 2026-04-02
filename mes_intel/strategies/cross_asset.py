"""Cross-Asset Correlation Strategy.

Reads pre-fetched cross-asset data (VIX, DXY, yields, NQ, Russell, HYG, etc.)
and produces a composite directional signal for MES.
"""
from __future__ import annotations

import time
import numpy as np
from .base import Strategy, StrategyResult


# Per-asset weights (must sum to ~1.0)
_ASSET_WEIGHTS: dict[str, float] = {
    'VIX':      0.30,
    'NQ FUTS':  0.25,
    'DXY':      0.20,
    '10Y YIELD':0.15,
    'RUSSELL':  0.05,
    'HY BONDS': 0.05,
    # Remaining assets share any leftover weight implicitly through composite_signal
}

_MAX_DATA_AGE = 900  # 15 minutes — beyond this, confidence collapses


class CrossAssetStrategy(Strategy):
    """Cross-asset correlation signals for MES futures."""

    name = "cross_asset"
    description = "Cross-asset correlation signals (VIX, DXY, yields, NQ divergence)"

    def required_data(self) -> list[str]:
        return ['prices', 'cross_asset']

    def evaluate(self, market_data: dict) -> StrategyResult:
        data = market_data.get('cross_asset', {})

        if not data:
            return StrategyResult(
                name=self.name,
                score=0.0,
                confidence=0.0,
                direction='FLAT',
                meta={'reason': 'no cross_asset data'},
            )

        assets = data.get('assets', {})
        gex = data.get('gex', {})
        data_age = data.get('age_sec', 9999)

        # ----------------------------------------------------------------
        # Age-based confidence penalty
        # ----------------------------------------------------------------
        if data_age > _MAX_DATA_AGE:
            age_factor = 0.2
        elif data_age > 300:
            age_factor = max(0.5, 1.0 - (data_age - 300) / (_MAX_DATA_AGE - 300) * 0.5)
        else:
            age_factor = 1.0

        # ----------------------------------------------------------------
        # Individual signal computation
        # ----------------------------------------------------------------
        signals: list[tuple[str, float, float, str]] = []  # (name, score, weight, detail)

        # --- VIX regime signal (weight 0.30) ---
        vix_data = assets.get('VIX', {})
        vix_price = vix_data.get('price', 18.0)
        vix_chg = vix_data.get('change_pct', 0.0)

        vix_regime_score = _vix_regime_score(vix_price)
        vix_trend_score = _vix_trend_score(vix_chg)
        vix_score = vix_regime_score * 0.4 + vix_trend_score * 0.6
        vix_detail = f"price={vix_price:.1f} chg={vix_chg:+.1f}%"
        signals.append(('VIX', vix_score, _ASSET_WEIGHTS['VIX'], vix_detail))

        # --- NQ divergence signal (weight 0.25) ---
        nq_data = assets.get('NQ FUTS', {})
        nq_chg = nq_data.get('change_pct', 0.0)
        nq_score = _nq_signal_score(nq_chg)
        nq_detail = f"chg={nq_chg:+.2f}%"
        signals.append(('NQ FUTS', nq_score, _ASSET_WEIGHTS['NQ FUTS'], nq_detail))

        # --- DXY signal (weight 0.20) ---
        dxy_data = assets.get('DXY', {})
        dxy_chg = dxy_data.get('change_pct', 0.0)
        dxy_score = _dxy_signal_score(dxy_chg)
        dxy_detail = f"chg={dxy_chg:+.2f}%"
        signals.append(('DXY', dxy_score, _ASSET_WEIGHTS['DXY'], dxy_detail))

        # --- 10Y Yield signal (weight 0.15) ---
        yield_data = assets.get('10Y YIELD', {})
        yield_price = yield_data.get('price', 4.0)
        yield_chg = yield_data.get('change_pct', 0.0)
        # Approximate bps change
        bps_change = yield_chg * yield_price * 10 if yield_price > 0 else 0.0
        yield_score = _yield_signal_score(bps_change)
        yield_detail = f"~{bps_change:+.1f}bps"
        signals.append(('10Y YIELD', yield_score, _ASSET_WEIGHTS['10Y YIELD'], yield_detail))

        # --- Russell risk-on/off signal (weight 0.05) ---
        rty_data = assets.get('RUSSELL', {})
        rty_chg = rty_data.get('change_pct', 0.0)
        rty_score = _breadth_signal_score(rty_chg)
        rty_detail = f"chg={rty_chg:+.2f}%"
        signals.append(('RUSSELL', rty_score, _ASSET_WEIGHTS['RUSSELL'], rty_detail))

        # --- HYG credit stress signal (weight 0.05) ---
        hyg_data = assets.get('HY BONDS', {})
        hyg_chg = hyg_data.get('change_pct', 0.0)
        hyg_score = _credit_signal_score(hyg_chg)
        hyg_detail = f"chg={hyg_chg:+.2f}%"
        signals.append(('HY BONDS', hyg_score, _ASSET_WEIGHTS['HY BONDS'], hyg_detail))

        # ----------------------------------------------------------------
        # Weighted score
        # ----------------------------------------------------------------
        total_weight = sum(w for _, _, w, _ in signals)
        if total_weight > 0:
            weighted_score = sum(s * w for _, s, w, _ in signals) / total_weight
        else:
            weighted_score = 0.0

        score = float(np.clip(weighted_score, -1.0, 1.0))

        # ----------------------------------------------------------------
        # Confidence: agreement ratio × data freshness
        # ----------------------------------------------------------------
        signs = [s for _, s, _, _ in signals if abs(s) > 0.05]
        if signs:
            positive = sum(1 for s in signs if s > 0)
            negative = sum(1 for s in signs if s < 0)
            agreement_ratio = max(positive, negative) / len(signs)
        else:
            agreement_ratio = 0.3

        # Boost confidence if score is strong
        magnitude_boost = min(abs(score) * 0.5, 0.3)
        confidence = float(np.clip((agreement_ratio * 0.7 + magnitude_boost) * age_factor, 0.0, 1.0))

        # ----------------------------------------------------------------
        # Direction
        # ----------------------------------------------------------------
        if score > 0.15:
            direction = 'LONG'
        elif score < -0.15:
            direction = 'SHORT'
        else:
            direction = 'FLAT'

        # ----------------------------------------------------------------
        # VIX regime labels for meta
        # ----------------------------------------------------------------
        if vix_price < 15:
            vix_regime_str = 'low'
        elif vix_price < 20:
            vix_regime_str = 'normal'
        elif vix_price < 25:
            vix_regime_str = 'elevated'
        else:
            vix_regime_str = 'fear'

        meta = {
            'signals': [
                {
                    'asset': name,
                    'score': round(s, 3),
                    'weight': round(w, 3),
                    'contribution': round(s * w / max(total_weight, 1e-10), 4),
                    'detail': detail,
                }
                for name, s, w, detail in signals
            ],
            'vix_regime': vix_regime_str,
            'vix_price': vix_price,
            'vix_change_pct': vix_chg,
            'nq_change_pct': nq_chg,
            'dxy_change_pct': dxy_chg,
            'yield_bps_change': round(bps_change, 1),
            'agreement_ratio': round(agreement_ratio, 3),
            'age_factor': round(age_factor, 3),
            'data_age_sec': data_age,
            'dealer_positioning': gex.get('dealer_positioning', 'unknown'),
            'composite_signal_raw': data.get('composite_signal', 0.0),
        }

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            meta=meta,
        )


# ---------------------------------------------------------------------------
# Signal score helpers — all return float in [-1, 1]
# ---------------------------------------------------------------------------

def _vix_regime_score(vix: float) -> float:
    """Bearish score based on absolute VIX level."""
    if vix < 15:
        return 0.0      # low vol, neutral
    elif vix < 20:
        return -0.2     # slight bearish
    elif vix < 25:
        return -0.6     # bearish
    else:
        return -1.0     # strong bearish / fear


def _vix_trend_score(vix_chg_pct: float) -> float:
    """Bearish score based on VIX daily % change."""
    if vix_chg_pct > 10:
        return -1.0
    elif vix_chg_pct > 5:
        return -0.7
    elif vix_chg_pct > 2:
        return -0.3
    elif vix_chg_pct < -5:
        return 0.7
    elif vix_chg_pct < -2:
        return 0.3
    else:
        return 0.0


def _nq_signal_score(nq_chg_pct: float) -> float:
    """NQ leading/lagging indicator for MES."""
    if nq_chg_pct > 1.0:
        return 0.8
    elif nq_chg_pct > 0.5:
        return 0.5
    elif nq_chg_pct > 0.1:
        return 0.2
    elif nq_chg_pct < -1.0:
        return -0.8
    elif nq_chg_pct < -0.5:
        return -0.5
    elif nq_chg_pct < -0.1:
        return -0.2
    else:
        return 0.0


def _dxy_signal_score(dxy_chg_pct: float) -> float:
    """DXY inverse relationship to MES/SPY."""
    if dxy_chg_pct > 1.0:
        return -0.8
    elif dxy_chg_pct > 0.5:
        return -0.4
    elif dxy_chg_pct < -1.0:
        return 0.8
    elif dxy_chg_pct < -0.5:
        return 0.4
    else:
        return 0.0


def _yield_signal_score(bps_change: float) -> float:
    """10Y yield bps change → MES signal."""
    if bps_change > 10:
        return -0.8     # strong bearish
    elif bps_change > 5:
        return -0.4     # mild bearish
    elif bps_change < -5:
        return 0.3      # mild bullish (flight from risk less, equities ok)
    else:
        return 0.0


def _breadth_signal_score(rty_chg_pct: float) -> float:
    """Russell 2000 breadth/risk signal."""
    if rty_chg_pct > 1.0:
        return 0.8
    elif rty_chg_pct > 0.5:
        return 0.5
    elif rty_chg_pct < -1.0:
        return -0.8
    elif rty_chg_pct < -0.5:
        return -0.5
    else:
        return 0.0


def _credit_signal_score(hyg_chg_pct: float) -> float:
    """HYG credit stress → MES signal."""
    if hyg_chg_pct < -0.5:
        return -0.7
    elif hyg_chg_pct < -0.2:
        return -0.3
    elif hyg_chg_pct > 0.2:
        return 0.3
    else:
        return 0.0
