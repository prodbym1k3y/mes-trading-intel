"""Momentum Strategy — Kalman Filter + ADX + Rate of Change.

Uses a Kalman filter for trend estimation, ADX for trend strength,
and rate of change for momentum confirmation.
"""
from __future__ import annotations

import numpy as np
from .base import Strategy, StrategyResult


def kalman_filter(prices: np.ndarray, q: float = 0.01, r: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    """Simple 1D Kalman filter on price series.

    Returns (filtered_prices, velocities).
    """
    n = len(prices)
    # State: [price, velocity]
    x = np.array([prices[0], 0.0])
    P = np.eye(2) * 1.0

    F = np.array([[1, 1], [0, 1]])  # state transition
    H = np.array([[1, 0]])          # observation
    Q = np.array([[q, 0], [0, q]])  # process noise
    R = np.array([[r]])             # measurement noise

    filtered = np.zeros(n)
    velocities = np.zeros(n)

    for i in range(n):
        # Predict
        x = F @ x
        P = F @ P @ F.T + Q

        # Update
        z = prices[i]
        y = z - H @ x
        S = H @ P @ H.T + R
        K = P @ H.T @ np.linalg.inv(S)
        x = x + (K @ y).flatten()
        P = (np.eye(2) - K @ H) @ P

        filtered[i] = x[0]
        velocities[i] = x[1]

    return filtered, velocities


def compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Compute ADX (Average Directional Index)."""
    n = len(closes)
    if n < period + 1:
        return 0.0

    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(np.abs(highs[1:] - closes[:-1]),
                               np.abs(lows[1:] - closes[:-1])))

    plus_dm = np.where((highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]),
                       np.maximum(highs[1:] - highs[:-1], 0), 0)
    minus_dm = np.where((lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]),
                        np.maximum(lows[:-1] - lows[1:], 0), 0)

    # Smoothed with EMA
    alpha = 1.0 / period
    atr = _ema(tr, alpha)
    plus_di = 100 * _ema(plus_dm, alpha) / np.maximum(atr, 1e-10)
    minus_di = 100 * _ema(minus_dm, alpha) / np.maximum(atr, 1e-10)

    dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    adx = _ema(dx, alpha)

    return float(adx[-1]) if len(adx) > 0 else 0.0


def _ema(data: np.ndarray, alpha: float) -> np.ndarray:
    """Exponential moving average."""
    result = np.zeros_like(data, dtype=float)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


class MomentumStrategy(Strategy):
    name = "momentum"
    description = "Kalman filter trend + ADX strength + ROC"

    def __init__(self, roc_period: int = 10, adx_threshold: float = 25.0,
                 kalman_q: float = 0.01, kalman_r: float = 0.1):
        self.roc_period = roc_period
        self.adx_threshold = adx_threshold
        self.kalman_q = kalman_q
        self.kalman_r = kalman_r

    def required_data(self) -> list[str]:
        return ["prices", "highs", "lows"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = np.array(market_data.get("prices", []))
        highs = np.array(market_data.get("highs", []))
        lows = np.array(market_data.get("lows", []))

        if len(prices) < 30:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "insufficient data"})

        # Kalman filter
        filtered, velocities = kalman_filter(prices, self.kalman_q, self.kalman_r)
        current_velocity = velocities[-1]

        # Rate of change
        roc = (prices[-1] - prices[-self.roc_period]) / prices[-self.roc_period] * 100
        roc_normalized = np.clip(roc / 2.0, -1.0, 1.0)  # normalize to [-1, 1]

        # ADX
        adx = compute_adx(highs, lows, prices)
        trend_strength = min(adx / 50.0, 1.0)  # normalize 0-1

        # Kalman velocity signal
        velocity_signal = np.clip(current_velocity / (np.std(velocities) + 1e-10), -1.0, 1.0)

        # Combine: velocity direction + ROC confirmation + ADX strength
        raw_score = 0.5 * velocity_signal + 0.3 * roc_normalized + 0.2 * np.sign(roc_normalized) * trend_strength
        score = float(np.clip(raw_score, -1.0, 1.0))

        # Confidence: high when ADX is strong and velocity/ROC agree
        agreement = 1.0 if np.sign(velocity_signal) == np.sign(roc_normalized) else 0.3
        confidence = float(trend_strength * agreement)

        # Only signal if ADX shows trending market
        if adx < self.adx_threshold:
            direction = "FLAT"
            confidence *= 0.3
        elif score > 0.2:
            direction = "LONG"
        elif score < -0.2:
            direction = "SHORT"
        else:
            direction = "FLAT"

        # Stops based on ATR
        atr = np.mean(highs[-14:] - lows[-14:])
        entry = float(prices[-1])
        if direction == "LONG":
            stop = entry - atr * 1.5
            target = entry + atr * 2.0
        elif direction == "SHORT":
            stop = entry + atr * 1.5
            target = entry - atr * 2.0
        else:
            stop = None
            target = None

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            entry_price=entry,
            stop_price=float(stop) if stop else None,
            target_price=float(target) if target else None,
            meta={
                "adx": float(adx),
                "roc": float(roc),
                "kalman_velocity": float(current_velocity),
                "trend_strength": float(trend_strength),
                "atr": float(atr),
            },
        )
