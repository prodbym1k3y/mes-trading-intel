"""Order Flow Imbalance Strategy — Volume Delta Analysis.

Uses bid/ask volume imbalance, cumulative delta trends, and absorption
detection to identify institutional order flow.
"""
from __future__ import annotations

import numpy as np
from .base import Strategy, StrategyResult
from ..orderflow import VolumeProfile, FootprintBar


class OrderFlowStrategy(Strategy):
    name = "order_flow"
    description = "Volume delta imbalance + absorption detection"

    def __init__(self, imbalance_threshold: float = 0.3, absorption_ratio: float = 2.0,
                 delta_divergence_bars: int = 10):
        self.imbalance_threshold = imbalance_threshold
        self.absorption_ratio = absorption_ratio
        self.delta_divergence_bars = delta_divergence_bars

    def required_data(self) -> list[str]:
        return ["volume_profile", "footprint_bars", "prices"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        profile: VolumeProfile | None = market_data.get("volume_profile")
        bars: list[FootprintBar] = market_data.get("footprint_bars", [])
        prices = market_data.get("prices", [])

        if profile is None or len(bars) < 5:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "insufficient order flow data"})

        signals = []

        # 1. Current delta imbalance at POC
        poc = profile.poc
        if poc and poc in profile.levels:
            poc_level = profile.levels[poc]
            if poc_level.total_volume > 0:
                imbalance = poc_level.delta / poc_level.total_volume
                if abs(imbalance) > self.imbalance_threshold:
                    signals.append(("poc_imbalance", np.sign(imbalance), abs(imbalance)))

        # 2. Recent bar delta trend
        recent_deltas = [b.delta for b in bars[-self.delta_divergence_bars:]]
        if len(recent_deltas) >= 3:
            delta_trend = np.polyfit(range(len(recent_deltas)), recent_deltas, 1)[0]
            delta_trend_norm = np.clip(delta_trend / (np.std(recent_deltas) + 1), -1, 1)
            signals.append(("delta_trend", float(delta_trend_norm), min(abs(float(delta_trend_norm)), 1.0)))

        # 3. Cumulative delta vs price divergence
        if len(bars) >= self.delta_divergence_bars and len(prices) >= self.delta_divergence_bars:
            recent_prices = prices[-self.delta_divergence_bars:]
            cum_deltas = np.cumsum(recent_deltas)

            price_change = recent_prices[-1] - recent_prices[0] if len(recent_prices) > 1 else 0
            delta_change = cum_deltas[-1] - cum_deltas[0] if len(cum_deltas) > 1 else 0

            # Divergence: price up but delta down (bearish) or vice versa
            if price_change > 0 and delta_change < 0:
                signals.append(("divergence", -1.0, 0.7))
            elif price_change < 0 and delta_change > 0:
                signals.append(("divergence", 1.0, 0.7))

        # 4. Absorption detection (large volume at a level with small price movement)
        if len(bars) >= 2:
            last_bar = bars[-1]
            if last_bar.high and last_bar.low:
                price_range = last_bar.high - last_bar.low
                if price_range > 0:
                    vol_per_tick = last_bar.volume / (price_range / 0.25)
                    prev_avg_vol = np.mean([b.volume for b in bars[-6:-1]])
                    if prev_avg_vol > 0 and last_bar.volume > prev_avg_vol * self.absorption_ratio:
                        # High volume, small range = absorption
                        absorption_dir = 1.0 if last_bar.delta > 0 else -1.0
                        signals.append(("absorption", absorption_dir, 0.6))

        # 5. Value area position
        val, vah = profile.value_area()
        current_price = float(prices[-1]) if prices else None
        if val and vah and current_price:
            if current_price < val:
                signals.append(("below_value", 1.0, 0.4))  # below value = potential long
            elif current_price > vah:
                signals.append(("above_value", -1.0, 0.4))  # above value = potential short

        # Combine signals
        if not signals:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                                  meta={"reason": "no order flow signals"})

        total_weight = sum(s[2] for s in signals)
        weighted_score = sum(s[1] * s[2] for s in signals) / total_weight
        score = float(np.clip(weighted_score, -1.0, 1.0))

        # Agreement boosts confidence
        directions = [np.sign(s[1]) for s in signals if abs(s[1]) > 0.1]
        agreement = sum(d == np.sign(score) for d in directions) / max(len(directions), 1)
        confidence = float(min(agreement * np.mean([s[2] for s in signals]), 1.0))

        if abs(score) > 0.3 and score > 0:
            direction = "LONG"
        elif abs(score) > 0.3 and score < 0:
            direction = "SHORT"
        else:
            direction = "FLAT"

        entry = float(prices[-1]) if prices else None

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=confidence,
            direction=direction,
            entry_price=entry,
            meta={
                "signals": [(s[0], float(s[1]), float(s[2])) for s in signals],
                "poc": poc,
                "val": val,
                "vah": vah,
                "cumulative_delta": profile.cumulative_delta,
                "total_volume": profile.total_volume,
            },
        )
