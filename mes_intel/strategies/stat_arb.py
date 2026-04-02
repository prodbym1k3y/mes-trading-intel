"""Statistical Arbitrage Strategy — ES vs SPY spread.

Monitors the spread between ES (or MES) futures and SPY ETF to detect
temporary dislocations. The fair value spread is modeled and deviations
beyond a threshold generate signals.
"""
from __future__ import annotations

import numpy as np
from .base import Strategy, StrategyResult


class StatArbStrategy(Strategy):
    name = "stat_arb"
    description = "ES/MES vs SPY spread mean reversion"

    def __init__(self, zscore_threshold: float = 2.0, lookback: int = 60,
                 half_life_max: int = 30):
        self.zscore_threshold = zscore_threshold
        self.lookback = lookback
        self.half_life_max = half_life_max

    def required_data(self) -> list[str]:
        return ["prices", "spy_prices"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        es_prices = np.array(market_data.get("prices", []))
        spy_prices = np.array(market_data.get("spy_prices", []))

        min_len = min(len(es_prices), len(spy_prices))
        if min_len < self.lookback:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "insufficient data"})

        es = es_prices[-self.lookback:]
        spy = spy_prices[-self.lookback:]

        # Compute spread (ES - SPY * multiplier)
        # ES ≈ SPY * 10 for full-size, MES ≈ SPY * 2 approximately
        # Use OLS to find the hedge ratio
        spy_mean = np.mean(spy)
        es_mean = np.mean(es)
        hedge_ratio = es_mean / spy_mean if spy_mean != 0 else 10.0

        spread = es - spy * hedge_ratio
        spread_mean = np.mean(spread)
        spread_std = np.std(spread)

        if spread_std < 1e-8:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "no spread variance"})

        current_spread = spread[-1]
        zscore = (current_spread - spread_mean) / spread_std

        # Half-life of mean reversion (Ornstein-Uhlenbeck)
        spread_lag = spread[:-1]
        spread_diff = np.diff(spread)
        if len(spread_lag) > 0 and np.std(spread_lag) > 1e-10:
            beta = np.sum(spread_lag * spread_diff) / np.sum(spread_lag ** 2)
            half_life = -np.log(2) / beta if beta < 0 else float("inf")
        else:
            half_life = float("inf")

        # Only trade if half-life is reasonable (spread actually mean-reverts)
        reverts = 0 < half_life < self.half_life_max

        # Score
        score = float(np.clip(-zscore / self.zscore_threshold, -1.0, 1.0))

        # Confidence
        confidence = min(abs(zscore) / (self.zscore_threshold * 1.5), 1.0)
        if not reverts:
            confidence *= 0.2  # low confidence if spread doesn't mean-revert

        # Direction (trade ES side)
        if zscore <= -self.zscore_threshold and reverts:
            direction = "LONG"   # spread below mean → ES undervalued
        elif zscore >= self.zscore_threshold and reverts:
            direction = "SHORT"  # spread above mean → ES overvalued
        else:
            direction = "FLAT"

        entry = float(es[-1])
        stop = entry + float(spread_std * 2 * (-1 if direction == "LONG" else 1)) if direction != "FLAT" else None
        target = entry + float(spread_mean - current_spread) if direction != "FLAT" else None

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=float(confidence),
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            meta={
                "zscore": float(zscore),
                "spread": float(current_spread),
                "spread_mean": float(spread_mean),
                "spread_std": float(spread_std),
                "half_life": float(half_life) if half_life != float("inf") else -1,
                "hedge_ratio": float(hedge_ratio),
                "mean_reverts": reverts,
            },
        )
