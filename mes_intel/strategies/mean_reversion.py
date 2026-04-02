"""Mean Reversion Strategy — VWAP + Z-Score.

Identifies overbought/oversold conditions relative to VWAP using Z-score
of price deviation. Signals when price deviates significantly and shows
signs of reverting.
"""
from __future__ import annotations

import numpy as np
from .base import Strategy, StrategyResult


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    description = "VWAP mean reversion with Z-score"

    def __init__(self, zscore_entry: float = 2.0, zscore_exit: float = 0.5,
                 lookback: int = 100, min_volume: int = 50):
        self.zscore_entry = zscore_entry
        self.zscore_exit = zscore_exit
        self.lookback = lookback
        self.min_volume = min_volume

    def required_data(self) -> list[str]:
        return ["prices", "volumes", "highs", "lows"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = np.array(market_data.get("prices", []))
        volumes = np.array(market_data.get("volumes", []))

        if len(prices) < self.lookback:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "insufficient data"})

        prices = prices[-self.lookback:]
        volumes = volumes[-self.lookback:]

        # Calculate VWAP
        typical_prices = prices  # using close prices; could use (H+L+C)/3
        if "highs" in market_data and "lows" in market_data:
            highs = np.array(market_data["highs"][-self.lookback:])
            lows = np.array(market_data["lows"][-self.lookback:])
            typical_prices = (highs + lows + prices) / 3.0

        cum_vol = np.cumsum(volumes)
        cum_tp_vol = np.cumsum(typical_prices * volumes)
        vwap = np.where(cum_vol > 0, cum_tp_vol / cum_vol, typical_prices)

        # Z-score of current price vs VWAP
        current_price = prices[-1]
        current_vwap = vwap[-1]
        deviations = prices - vwap
        std_dev = np.std(deviations)

        if std_dev < 1e-8:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "no volatility"})

        zscore = (current_price - current_vwap) / std_dev

        # Score: negative z-score = price below VWAP = long signal
        score = np.clip(-zscore / self.zscore_entry, -1.0, 1.0)

        # Confidence based on z-score magnitude
        confidence = min(abs(zscore) / (self.zscore_entry * 1.5), 1.0)

        # Direction
        if zscore <= -self.zscore_entry:
            direction = "LONG"
        elif zscore >= self.zscore_entry:
            direction = "SHORT"
        else:
            direction = "FLAT"

        # Entry/stop/target
        entry = current_price
        if direction == "LONG":
            stop = current_price - std_dev * 1.5
            target = current_vwap  # revert to VWAP
        elif direction == "SHORT":
            stop = current_price + std_dev * 1.5
            target = current_vwap
        else:
            stop = None
            target = None

        return StrategyResult(
            name=self.name,
            score=float(score),
            confidence=float(confidence),
            direction=direction,
            entry_price=float(entry),
            stop_price=float(stop) if stop else None,
            target_price=float(target) if target else None,
            meta={
                "zscore": float(zscore),
                "vwap": float(current_vwap),
                "std_dev": float(std_dev),
                "deviation": float(current_price - current_vwap),
            },
        )
