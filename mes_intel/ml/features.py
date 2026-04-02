"""Feature engineering for MES Intel ML pipeline.

Computes ~60 features from raw market_data dicts and trade history covering:
price action, volatility, momentum, order flow, market structure, volume,
strategy signals, time (sin/cos encoded), and regime.

Expected market_data dict keys (all optional, default 0):
    close, open, high, low, volume, vwap, tick_count
    poc, vah, val                           – volume profile prices
    cum_delta_5m, cum_delta_15m             – cumulative delta windows
    delta_per_bar, buy_volume, sell_volume  – bar-level order flow
    absorption_events                       – int count
    large_trade_count                       – int count
    naked_poc_count                         – int
    profile_shape                           – 0=unknown,1=p,2=b,3=D,4=balanced
    regime                                  – 0=unknown,1=up,2=down,3=range
    regime_duration                         – bars in current regime
    strategy_scores                         – dict: name -> float score
    timestamp                               – unix float (for time features)
    history                                 – list of prior bar dicts (optional)
"""
from __future__ import annotations

import logging
import math
import time
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_CLIP_STD = 5.0
_RTH_OPEN_MIN = 9 * 60 + 30   # 09:30 in minutes-from-midnight
_RTH_CLOSE_MIN = 16 * 60       # 16:00

STRATEGY_KEYS: List[str] = [
    "momentum",
    "mean_reversion",
    "stat_arb",
    "order_flow",
    "gex_model",
    "hmm_regime",
    "ml_scorer",
]


# ── tiny safe-math helpers ────────────────────────────────────────────────────

def _safe(val, default: float = 0.0) -> float:
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _pct(a: float, b: float) -> float:
    """Return (a - b) / (|b| + eps)."""
    return _safe((a - b) / (abs(b) + 1e-9))


def _sin_cos(value: float, period: float) -> Tuple[float, float]:
    angle = 2.0 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


# ── rolling indicators ────────────────────────────────────────────────────────

def _returns(closes: np.ndarray, n: int) -> float:
    if len(closes) <= n:
        return 0.0
    return _safe((closes[-1] - closes[-n - 1]) / (closes[-n - 1] + 1e-9))


def _log_return(closes: np.ndarray, n: int) -> float:
    if len(closes) <= n or closes[-n - 1] <= 0:
        return 0.0
    return _safe(math.log(closes[-1] / closes[-n - 1]))


def _rolling_std(arr: np.ndarray, n: int) -> float:
    if len(arr) < max(n, 2):
        return 0.0
    return _safe(float(np.std(arr[-n:])))


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         period: int = 14) -> float:
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return 0.0
    h, l, c = highs[-n:], lows[-n:], closes[-n:]
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]),
                               np.abs(l[1:] - c[:-1])))
    window = min(period, len(tr))
    return _safe(float(np.mean(tr[-window:])))


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = np.diff(closes[-(period + 1):])
    gains = float(np.mean(np.where(delta > 0, delta, 0.0)))
    losses = float(np.mean(np.where(delta < 0, -delta, 0.0)))
    if losses < 1e-9:
        return 100.0
    rs = gains / losses
    return _safe(100.0 - 100.0 / (1.0 + rs))


def _linear_slope(arr: np.ndarray, n: int) -> float:
    """Slope of OLS line through last n values."""
    if len(arr) < 2:
        return 0.0
    window = arr[-min(n, len(arr)):]
    if len(window) < 2:
        return 0.0
    x = np.arange(len(window), dtype=np.float64)
    coeffs = np.polyfit(x, window.astype(np.float64), 1)
    return _safe(float(coeffs[0]))


# ── feature name catalogue ────────────────────────────────────────────────────

def _build_feature_names() -> List[str]:
    names: List[str] = []

    # Price features (7 groups)
    for n in (1, 5, 10, 20):
        names.append(f"return_{n}b")
    for n in (1, 5, 10, 20):
        names.append(f"log_return_{n}b")
    names += ["price_vs_vwap", "price_vs_poc", "price_position_in_range"]

    # Volatility (7)
    for n in (5, 10, 20):
        names.append(f"rolling_std_{n}")
    names += ["atr_14", "realized_vol", "vol_ratio"]

    # Momentum (5)
    names += ["rsi_14", "rsi_7", "roc_5", "roc_10", "momentum_divergence"]

    # Order flow (7)
    names += [
        "cum_delta_5m", "cum_delta_15m", "delta_per_bar",
        "buy_vol_ratio", "sell_vol_ratio", "delta_trend",
        "absorption_events_count",
    ]

    # Market structure (4)
    names += ["va_position", "poc_distance", "naked_poc_count", "profile_shape"]

    # Volume (4)
    names += ["volume_vs_avg", "volume_trend", "tick_count", "large_trade_count"]

    # Strategy signals (8 = 7 strategies + agreement count)
    for key in STRATEGY_KEYS:
        names.append(f"strat_{key}")
    names.append("strategy_agreement_count")

    # Time (6)
    names += [
        "hour_sin", "hour_cos",
        "dow_sin", "dow_cos",
        "minutes_to_close", "minutes_from_open",
    ]

    # Regime (2)
    names += ["regime", "regime_duration"]

    return names


_FEATURE_NAMES: List[str] = _build_feature_names()


# ── FeatureEngine ─────────────────────────────────────────────────────────────

class FeatureEngine:
    """Engineers features from raw market_data dicts and trade history.

    Usage::

        engine = FeatureEngine(feature_window=20)

        # Single bar
        row = engine.compute_features(bar_dict, trade_history)   # np.ndarray

        # Full matrix from history list
        df = engine.compute_feature_matrix(bar_list, trade_history)
    """

    def __init__(self, feature_window: int = 20):
        self.feature_window = feature_window

    # ── public ────────────────────────────────────────────────────────────────

    def get_feature_names(self) -> List[str]:
        return list(_FEATURE_NAMES)

    def compute_features(self, market_data: dict, trade_history: list) -> np.ndarray:
        """Compute features for a single latest bar.

        If market_data contains a 'history' key (list of prior dicts), that
        window is used directly; otherwise market_data is treated as the sole bar.
        """
        window: list = market_data.get("history", [market_data])
        return self._extract_row(window, trade_history)

    def compute_feature_matrix(
        self, market_data_history: list, trade_history: list
    ) -> pd.DataFrame:
        """Build a feature matrix from a list of bar dicts (chronological).

        Each bar's features are computed from the sliding window ending at
        that bar; bars without enough history produce zero-padded rows.
        """
        rows = []
        for i, bar in enumerate(market_data_history):
            if "history" in bar:
                window = bar["history"]
            else:
                start = max(0, i - self.feature_window + 1)
                window = market_data_history[start: i + 1]
            rows.append(self._extract_row(window, trade_history))

        df = pd.DataFrame(rows, columns=_FEATURE_NAMES)
        return self._clean(df)

    # ── internal ──────────────────────────────────────────────────────────────

    def _extract_row(self, window: list, trade_history: list) -> np.ndarray:
        if not window:
            return np.zeros(len(_FEATURE_NAMES), dtype=np.float32)

        latest = window[-1]

        # Build price arrays
        closes = np.array([_safe(b.get("close", 0)) for b in window], dtype=np.float64)
        highs  = np.array([_safe(b.get("high",  _safe(b.get("close", 0)))) for b in window], dtype=np.float64)
        lows   = np.array([_safe(b.get("low",   _safe(b.get("close", 0)))) for b in window], dtype=np.float64)
        vols   = np.array([_safe(b.get("volume", 0)) for b in window], dtype=np.float64)
        deltas = np.array([_safe(b.get("delta_per_bar", 0)) for b in window], dtype=np.float64)

        price = closes[-1] if len(closes) else 0.0
        vwap  = _safe(latest.get("vwap", price))
        poc   = _safe(latest.get("poc",  price))
        vah   = _safe(latest.get("vah",  price))
        val   = _safe(latest.get("val",  price))
        price_range = (vah - val) if (vah - val) > 1e-9 else 1.0

        feats: List[float] = []

        # ── Price returns ─────────────────────────────────────────────────────
        for n in (1, 5, 10, 20):
            feats.append(_returns(closes, n))
        for n in (1, 5, 10, 20):
            feats.append(_log_return(closes, n))

        feats.append(_pct(price, vwap))
        feats.append(_pct(price, poc))
        feats.append(_safe((price - val) / price_range))

        # ── Volatility ────────────────────────────────────────────────────────
        for n in (5, 10, 20):
            feats.append(_rolling_std(closes, n))

        feats.append(_atr(highs, lows, closes, 14))

        if len(closes) >= 5:
            valid = closes[closes > 0]
            if len(valid) >= 2:
                lr = np.diff(np.log(valid[-20:]))
                realized = _safe(float(np.std(lr)) * math.sqrt(252 * 390))
            else:
                realized = 0.0
        else:
            realized = 0.0
        feats.append(realized)

        std5  = _rolling_std(closes, 5)
        std20 = _rolling_std(closes, 20)
        feats.append(_safe(std5 / (std20 + 1e-9)))

        # ── Momentum ──────────────────────────────────────────────────────────
        feats.append(_rsi(closes, 14))
        feats.append(_rsi(closes, 7))
        feats.append(_returns(closes, 5))   # rate_of_change(5)
        feats.append(_returns(closes, 10))  # rate_of_change(10)

        price_trend_5 = _returns(closes, 5)
        delta_trend_5 = _returns(deltas, 5)
        if price_trend_5 > 0.001 and delta_trend_5 < -0.001:
            div = -1.0   # bearish divergence
        elif price_trend_5 < -0.001 and delta_trend_5 > 0.001:
            div = 1.0    # bullish divergence
        else:
            div = 0.0
        feats.append(div)

        # ── Order flow ────────────────────────────────────────────────────────
        feats.append(_safe(latest.get("cum_delta_5m",  0)))
        feats.append(_safe(latest.get("cum_delta_15m", 0)))
        feats.append(_safe(latest.get("delta_per_bar", 0)))

        buy_v  = _safe(latest.get("buy_volume",  0))
        sell_v = _safe(latest.get("sell_volume", 0))
        tot_v  = buy_v + sell_v
        feats.append(_safe(buy_v  / (tot_v + 1e-9)))
        feats.append(_safe(sell_v / (tot_v + 1e-9)))
        feats.append(_linear_slope(deltas, 5))
        feats.append(_safe(latest.get("absorption_events", 0)))

        # ── Market structure ──────────────────────────────────────────────────
        if vah > val:
            mid = (vah + val) / 2.0
            half = price_range / 2.0
            if price >= vah:
                va_pos = 1.0
            elif price <= val:
                va_pos = -1.0
            else:
                va_pos = _safe((price - mid) / (half + 1e-9))
        else:
            va_pos = 0.0
        feats.append(va_pos)
        feats.append(_pct(price, poc))
        feats.append(_safe(latest.get("naked_poc_count", 0)))
        feats.append(_safe(latest.get("profile_shape",   0)))

        # ── Volume ────────────────────────────────────────────────────────────
        avg_vol = float(np.mean(vols)) if len(vols) > 0 else 1.0
        cur_vol = vols[-1] if len(vols) > 0 else 0.0
        feats.append(_safe(cur_vol / (avg_vol + 1e-9)))
        feats.append(_linear_slope(vols, 5))
        feats.append(_safe(latest.get("tick_count",       0)))
        feats.append(_safe(latest.get("large_trade_count", 0)))

        # ── Strategy signals ──────────────────────────────────────────────────
        strat: dict = latest.get("strategy_scores", {})
        agreement = 0
        for key in STRATEGY_KEYS:
            score = _safe(strat.get(key, 0.0))
            feats.append(score)
            if abs(score) >= 0.5:
                agreement += 1
        feats.append(float(agreement))

        # ── Time (sin/cos encoded) ────────────────────────────────────────────
        import datetime as _dt
        ts = _safe(latest.get("timestamp", time.time()))
        dt = _dt.datetime.fromtimestamp(ts)
        hour_frac = dt.hour + dt.minute / 60.0
        h_sin, h_cos = _sin_cos(hour_frac, 24.0)
        feats.append(h_sin)
        feats.append(h_cos)

        dow = float(dt.weekday())  # 0=Monday
        d_sin, d_cos = _sin_cos(dow, 5.0)
        feats.append(d_sin)
        feats.append(d_cos)

        cur_min = dt.hour * 60 + dt.minute
        feats.append(_safe(max(0.0, float(_RTH_CLOSE_MIN - cur_min))))
        feats.append(_safe(max(0.0, float(cur_min - _RTH_OPEN_MIN))))

        # ── Regime ────────────────────────────────────────────────────────────
        feats.append(_safe(latest.get("regime",          0)))
        feats.append(_safe(latest.get("regime_duration", 0)))

        assert len(feats) == len(_FEATURE_NAMES), (
            f"Feature count mismatch: got {len(feats)}, expected {len(_FEATURE_NAMES)}"
        )
        return np.array(feats, dtype=np.float32)

    @staticmethod
    def _clean(df: pd.DataFrame) -> pd.DataFrame:
        """Replace NaN with 0; clip each column at ±5 std from mean."""
        df = df.fillna(0.0)
        for col in df.columns:
            std = df[col].std()
            if std > 0:
                mean = df[col].mean()
                df[col] = df[col].clip(
                    lower=mean - _CLIP_STD * std,
                    upper=mean + _CLIP_STD * std,
                )
        return df
