"""Market Internals Strategy — NYSE TICK / ADD / VOLD composite analysis.

Uses real market internals data when available in market_data, or derives
proxy values from order flow data (buy_volume, sell_volume, delta_history,
session_delta) when real internals are not present.

Composite z-score approach: normalize each internal to a z-score, weight
and combine, then map to a -1.0 to +1.0 signal score.
"""
from __future__ import annotations

import statistics
from typing import Optional

import numpy as np

from .base import Strategy, StrategyResult


class MarketInternalsStrategy(Strategy):
    """Composite NYSE market internals: TICK, ADD, VOLD.

    Real internals should be injected into market_data under keys:
      - 'nyse_tick'   : current NYSE TICK reading (int)
      - 'nyse_tick_history' : List[float], last N readings
      - 'nyse_add'    : current ADVANCE/DECLINE difference
      - 'nyse_add_history'  : List[float]
      - 'nyse_vold'   : current VOLUME ratio (adv vol / dec vol, or diff)
      - 'nyse_vold_history' : List[float]

    If any are absent the strategy builds proxies from:
      - buy_volume / sell_volume → TICK proxy
      - session_delta, delta_history → ADD / VOLD proxies
    """

    name = "market_internals"
    description = "NYSE TICK/ADD/VOLD composite breadth analysis with proxy fallback"

    # Extreme TICK thresholds (traditional NYSE TICK scale)
    TICK_EXTREME_HIGH = 1000.0
    TICK_EXTREME_LOW = -1000.0

    # Weights for composite
    WEIGHT_TICK = 0.45
    WEIGHT_ADD = 0.30
    WEIGHT_VOLD = 0.25

    def __init__(
        self,
        ma_period: int = 5,                    # bars for internals moving average
        z_score_lookback: int = 40,            # lookback for z-score normalization
        signal_threshold: float = 0.5,         # |z-score| to produce LONG/SHORT signal
        extreme_tick_threshold: float = 0.80,  # normalized |TICK| for extreme reading flag
        divergence_lookback: int = 10,         # bars to check TICK vs price divergence
    ):
        self.ma_period = ma_period
        self.z_score_lookback = z_score_lookback
        self.signal_threshold = signal_threshold
        self.extreme_tick_threshold = extreme_tick_threshold
        self.divergence_lookback = divergence_lookback

    def required_data(self) -> list[str]:
        return [
            "price", "price_history",
            "buy_volume", "sell_volume",
            "delta_history", "session_delta",
        ]

    # ------------------------------------------------------------------
    # Z-score helper
    # ------------------------------------------------------------------

    def _z_score(self, series: list[float], current: float) -> float:
        """Compute z-score of current value against series, clamped to ±3."""
        if len(series) < 3:
            return 0.0
        mu = statistics.mean(series)
        try:
            sd = statistics.stdev(series)
        except statistics.StatisticsError:
            sd = 0.0
        if sd < 1e-9:
            return 0.0
        return float(np.clip((current - mu) / sd, -3.0, 3.0))

    def _normalize_z(self, z: float, clamp: float = 3.0) -> float:
        """Map z-score to -1..+1 range."""
        return float(np.clip(z / clamp, -1.0, 1.0))

    # ------------------------------------------------------------------
    # Proxy builders
    # ------------------------------------------------------------------

    def _build_tick_proxy(
        self,
        buy_volume: float,
        sell_volume: float,
        volume_history: list[float],
        buy_vol_history: Optional[list[float]] = None,
        sell_vol_history: Optional[list[float]] = None,
    ) -> tuple[float, list[float]]:
        """
        Proxy for NYSE TICK from buy/sell volume imbalance.

        Maps buy/(buy+sell) to a [-1000, +1000] pseudo-TICK scale.
        Returns (current_tick_proxy, history_list).
        """
        total = buy_volume + sell_volume
        if total <= 0:
            return 0.0, []

        imbalance = (buy_volume - sell_volume) / total  # -1 to +1
        tick_proxy = imbalance * 1000.0

        # Build history if raw buy/sell histories available
        history: list[float] = []
        if buy_vol_history and sell_vol_history:
            n = min(len(buy_vol_history), len(sell_vol_history))
            for bv, sv in zip(buy_vol_history[-n:], sell_vol_history[-n:]):
                t = bv + sv
                if t > 0:
                    history.append(((bv - sv) / t) * 1000.0)

        return tick_proxy, history

    def _build_add_proxy(
        self,
        session_delta: float,
        delta_history: list[float],
        ma_period: int = 10,
    ) -> tuple[float, list[float]]:
        """
        Proxy for NYSE ADD from cumulative session delta vs its moving average.

        Returns (current_add_proxy, rolling_add_history).
        """
        if not delta_history:
            return 0.0, []

        # Cumulative delta series
        cum_delta = list(np.cumsum(delta_history))

        # ADD proxy = current cum_delta minus its N-bar moving average
        lookback = min(len(cum_delta), max(ma_period, 5))
        ma = float(np.mean(cum_delta[-lookback:]))
        current_add = session_delta - ma

        # Rolling ADD proxy history using the cum_delta series
        history: list[float] = []
        for i in range(len(cum_delta)):
            start = max(0, i - ma_period + 1)
            window_ma = float(np.mean(cum_delta[start : i + 1]))
            history.append(cum_delta[i] - window_ma)

        return current_add, history

    def _build_vold_proxy(
        self, delta_history: list[float], lookback: int = 20
    ) -> tuple[float, list[float]]:
        """
        Proxy for NYSE VOLD from delta trend strength.

        Uses the slope of delta_history as the VOLD proxy — positive slope
        (increasing delta) = advancing volume dominating.

        Returns (current_vold_proxy, rolling_vold_history).
        """
        if len(delta_history) < 5:
            return 0.0, []

        def slope_segment(segment: list[float]) -> float:
            if len(segment) < 2:
                return 0.0
            x = np.arange(len(segment), dtype=float)
            coeffs = np.polyfit(x, segment, 1)
            return float(coeffs[0])

        history: list[float] = []
        for i in range(5, len(delta_history) + 1):
            window = delta_history[max(0, i - lookback):i]
            history.append(slope_segment(window))

        current_vold = history[-1] if history else 0.0
        return current_vold, history

    # ------------------------------------------------------------------
    # Divergence detection
    # ------------------------------------------------------------------

    def _detect_tick_divergence(
        self,
        tick_history: list[float],
        price_history: list[float],
        n: int,
    ) -> Optional[str]:
        """
        Returns 'TICK_BEARISH_DIV' if price trending up but TICK MA trending down,
                'TICK_BULLISH_DIV' if price trending down but TICK MA trending up,
                None otherwise.
        """
        if len(tick_history) < n or len(price_history) < n:
            return None

        tick_seg = np.array(tick_history[-n:], dtype=float)
        price_seg = np.array(price_history[-n:], dtype=float)
        x = np.arange(n, dtype=float)

        tick_slope = float(np.polyfit(x, tick_seg, 1)[0])
        price_slope = float(np.polyfit(x, price_seg, 1)[0])

        if price_slope > 0.01 and tick_slope < -0.01:
            return "TICK_BEARISH_DIV"
        if price_slope < -0.01 and tick_slope > 0.01:
            return "TICK_BULLISH_DIV"
        return None

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        price_history: list[float] = market_data.get("price_history", [])
        buy_volume: float = market_data.get("buy_volume", 0.0)
        sell_volume: float = market_data.get("sell_volume", 0.0)
        delta_history: list[float] = market_data.get("delta_history", [])
        session_delta: float = market_data.get("session_delta", 0.0)
        volume_history: list[float] = market_data.get("volume_history", [])

        if price == 0.0:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no price data"},
            )

        # ----------------------------------------------------------------
        # Acquire TICK, ADD, VOLD — real data preferred, proxy fallback
        # ----------------------------------------------------------------
        using_proxy = False

        # --- TICK ---
        tick_real: Optional[float] = market_data.get("nyse_tick")
        tick_history_real: list[float] = market_data.get("nyse_tick_history", [])
        if tick_real is not None:
            tick_current = tick_real
            tick_history = tick_history_real
        else:
            using_proxy = True
            tick_current, tick_history = self._build_tick_proxy(
                buy_volume, sell_volume, volume_history,
                market_data.get("buy_volume_history"),
                market_data.get("sell_volume_history"),
            )

        # --- ADD ---
        add_real: Optional[float] = market_data.get("nyse_add")
        add_history_real: list[float] = market_data.get("nyse_add_history", [])
        if add_real is not None:
            add_current = add_real
            add_history = add_history_real
        else:
            using_proxy = True
            add_current, add_history = self._build_add_proxy(session_delta, delta_history, self.ma_period)

        # --- VOLD ---
        vold_real: Optional[float] = market_data.get("nyse_vold")
        vold_history_real: list[float] = market_data.get("nyse_vold_history", [])
        if vold_real is not None:
            vold_current = vold_real
            vold_history = vold_history_real
        else:
            using_proxy = True
            vold_current, vold_history = self._build_vold_proxy(delta_history)

        # ----------------------------------------------------------------
        # Normalize each to z-score → -1..+1
        # ----------------------------------------------------------------
        tick_z = self._z_score(tick_history[-self.z_score_lookback:], tick_current) if tick_history else 0.0
        add_z = self._z_score(add_history[-self.z_score_lookback:], add_current) if add_history else 0.0
        vold_z = self._z_score(vold_history[-self.z_score_lookback:], vold_current) if vold_history else 0.0

        tick_norm = self._normalize_z(tick_z)
        add_norm = self._normalize_z(add_z)
        vold_norm = self._normalize_z(vold_z)

        # ----------------------------------------------------------------
        # Composite weighted score
        # ----------------------------------------------------------------
        composite = (
            self.WEIGHT_TICK * tick_norm
            + self.WEIGHT_ADD * add_norm
            + self.WEIGHT_VOLD * vold_norm
        )

        # ----------------------------------------------------------------
        # 5-bar moving average of composite (trend of internals)
        # ----------------------------------------------------------------
        # Reconstruct rough composite history from sub-histories
        n_hist = min(len(tick_history), len(add_history), len(vold_history), self.z_score_lookback)
        if n_hist >= self.ma_period:
            tick_h = tick_history[-n_hist:]
            add_h = add_history[-n_hist:]
            vold_h = vold_history[-n_hist:]

            composite_history: list[float] = []
            for i in range(len(tick_h)):
                tz = self._z_score(tick_h[:i], tick_h[i]) if i > 2 else 0.0
                az = self._z_score(add_h[:i], add_h[i]) if i > 2 else 0.0
                vz = self._z_score(vold_h[:i], vold_h[i]) if i > 2 else 0.0
                composite_history.append(
                    self.WEIGHT_TICK * self._normalize_z(tz)
                    + self.WEIGHT_ADD * self._normalize_z(az)
                    + self.WEIGHT_VOLD * self._normalize_z(vz)
                )

            composite_ma = float(np.mean(composite_history[-self.ma_period:])) if composite_history else composite
        else:
            composite_ma = composite

        # ----------------------------------------------------------------
        # Extreme TICK check
        # ----------------------------------------------------------------
        tick_normalized_abs = abs(tick_current) / self.TICK_EXTREME_HIGH
        extreme_tick = tick_normalized_abs >= self.extreme_tick_threshold
        extreme_direction: Optional[str] = None
        if extreme_tick:
            extreme_direction = "LONG" if tick_current > 0 else "SHORT"

        # ----------------------------------------------------------------
        # Divergence check
        # ----------------------------------------------------------------
        divergence = self._detect_tick_divergence(tick_history, price_history, self.divergence_lookback)

        # ----------------------------------------------------------------
        # Breadth confidence: how many internals agree with composite direction?
        # ----------------------------------------------------------------
        comp_sign = np.sign(composite)
        agreement_count = sum(
            1 for v in [tick_norm, add_norm, vold_norm]
            if np.sign(v) == comp_sign
        )
        breadth_confidence = agreement_count / 3.0   # 0.33, 0.67, or 1.0

        # ----------------------------------------------------------------
        # Final signal
        # ----------------------------------------------------------------
        abs_composite = abs(composite)

        if abs_composite >= self.signal_threshold:
            direction = "LONG" if composite > 0 else "SHORT"
            # Base confidence from composite strength × breadth agreement
            base_conf = min(abs_composite * 0.8, 0.75) * breadth_confidence
            # Boost if extreme TICK agrees
            if extreme_tick and extreme_direction == direction:
                base_conf = min(base_conf + 0.15, 1.0)
            # Reduce if divergence warns against signal
            if divergence == "TICK_BEARISH_DIV" and direction == "LONG":
                base_conf *= 0.6
            elif divergence == "TICK_BULLISH_DIV" and direction == "SHORT":
                base_conf *= 0.6
            # Proxy reduces confidence
            if using_proxy:
                base_conf *= 0.75
            confidence = float(np.clip(base_conf, 0.0, 1.0))
            score = float(np.clip(composite, -1.0, 1.0))
        else:
            direction = "FLAT"
            score = float(np.clip(composite, -1.0, 1.0))
            confidence = 0.1 * breadth_confidence

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            entry_price=price if direction != "FLAT" else None,
            meta={
                "tick": round(tick_current, 1),
                "add": round(add_current, 2),
                "vold": round(vold_current, 4),
                "tick_norm": round(tick_norm, 3),
                "add_norm": round(add_norm, 3),
                "vold_norm": round(vold_norm, 3),
                "composite": round(composite, 4),
                "composite_ma": round(composite_ma, 4),
                "breadth_agreement": f"{agreement_count}/3",
                "extreme_tick": extreme_tick,
                "extreme_direction": extreme_direction,
                "divergence": divergence,
                "using_proxy": using_proxy,
            },
        )
