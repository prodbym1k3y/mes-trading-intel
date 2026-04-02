"""Microstructure Strategy — bid/ask spread and quote dynamics analysis."""
from __future__ import annotations

import statistics
from typing import List

from .base import Strategy, StrategyResult


class MicrostructureStrategy(Strategy):
    """Analyzes market microstructure: spread dynamics, quote imbalance,
    tick quality, and toxic (informed) order flow.

    Informed traders cause systematic price impact. Detecting their footprint
    early — via spread narrowing, consistent quote imbalance, and high price
    impact per unit volume — gives an edge on the direction of the next move.
    """

    name = "microstructure"
    description = "Bid/ask spread and quote dynamics / toxic flow detector"

    # --- tuneable parameters ------------------------------------------------
    SPREAD_WINDOW = 20      # bars for rolling avg spread
    IMPACT_WINDOW = 10      # bars for price-impact calculation
    IMBALANCE_THRESHOLD = 0.15   # minimum quote imbalance to register
    TOXICITY_THRESHOLD = 0.30    # min toxicity score to boost confidence

    def required_data(self) -> list[str]:
        return [
            "price", "bid", "ask", "spread",
            "buy_volume", "sell_volume",
            "ticks", "price_history", "volume_history",
        ]

    # --- helpers ------------------------------------------------------------

    def _spread_zscore(self, spread: float, price_history: List[float],
                       bid: float, ask: float) -> float:
        """Z-score of current spread vs rolling history.

        Approximates historical half-spreads from consecutive price differences
        when we only have one current spread value.
        Returns positive = wider than avg (risk-off), negative = tighter (confidence).
        """
        if len(price_history) < self.SPREAD_WINDOW + 1:
            return 0.0
        # Proxy: use abs(price[i] - price[i-1]) as a tick-level spread estimate
        window = price_history[-(self.SPREAD_WINDOW + 1):]
        approx_spreads = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
        if not approx_spreads:
            return 0.0
        mean_s = statistics.mean(approx_spreads)
        stdev_s = statistics.pstdev(approx_spreads)
        if stdev_s == 0:
            return 0.0
        return (spread - mean_s) / stdev_s

    def _quote_imbalance(self, buy_volume: float, sell_volume: float) -> float:
        """Normalized quote/volume imbalance in [-1, +1].

        +1 = all buying, -1 = all selling.
        """
        total = buy_volume + sell_volume
        if total == 0:
            return 0.0
        return (buy_volume - sell_volume) / total

    def _tick_quality(self, ticks) -> float:
        """Ratio of ticks that actually move price vs total ticks.

        High quality = price is being actively pushed; low quality = noise.
        Returns value in [0, 1].
        """
        if not ticks or len(ticks) < 2:
            return 0.5
        moving = 0
        try:
            for i in range(1, len(ticks)):
                if ticks[i].price != ticks[i - 1].price:
                    moving += 1
        except AttributeError:
            return 0.5
        return moving / (len(ticks) - 1)

    def _price_impact(self, price_history: List[float],
                      volume_history: List[float]) -> float:
        """Average absolute price change per unit volume (last N bars).

        High impact on low volume = informed / toxic flow.
        Returns value in points-per-unit-volume, normalized to [0, 1].
        """
        n = self.IMPACT_WINDOW
        prices = price_history[-n - 1:]
        vols = volume_history[-n:]
        if len(prices) < 2 or len(vols) < 1:
            return 0.0
        impacts = []
        for i in range(min(len(prices) - 1, len(vols))):
            v = vols[i]
            if v > 0:
                impacts.append(abs(prices[i + 1] - prices[i]) / v)
        if not impacts:
            return 0.0
        raw = statistics.mean(impacts)
        # Normalize: 0 impact = 0, ~0.01 pts/contract = 1.0
        return min(raw / 0.01, 1.0)

    def _flow_direction(self, ticks) -> float:
        """Measure directional persistence of aggressive ticks.

        Returns [-1, +1]: positive = buy-side aggression, negative = sell-side.
        """
        if not ticks:
            return 0.0
        buy_hits = 0
        sell_hits = 0
        try:
            for tick in ticks[-30:]:
                if tick.aggressor:
                    buy_hits += 1
                else:
                    sell_hits += 1
        except AttributeError:
            return 0.0
        total = buy_hits + sell_hits
        if total == 0:
            return 0.0
        return (buy_hits - sell_hits) / total

    def _adverse_selection_score(self, ticks) -> float:
        """Detect repeated same-side aggression = directional intent.

        Count runs of consecutive same-aggressor ticks; longer runs = higher score.
        """
        if not ticks or len(ticks) < 3:
            return 0.0
        try:
            max_run = 1
            current_run = 1
            for i in range(1, len(ticks[-20:])):
                if ticks[i].aggressor == ticks[i - 1].aggressor:
                    current_run += 1
                    max_run = max(max_run, current_run)
                else:
                    current_run = 1
        except AttributeError:
            return 0.0
        # Normalize: run of 5+ = score 1.0
        return min((max_run - 1) / 5.0, 1.0)

    # --- main ---------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        bid: float = market_data.get("bid", price)
        ask: float = market_data.get("ask", price)
        spread: float = market_data.get("spread", ask - bid)
        buy_volume: float = market_data.get("buy_volume", 0.0)
        sell_volume: float = market_data.get("sell_volume", 0.0)
        ticks = market_data.get("ticks", [])
        price_history: List[float] = market_data.get("price_history", [])
        volume_history: List[float] = market_data.get("volume_history", [])

        if price == 0 or len(price_history) < self.SPREAD_WINDOW:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient_data"}
            )

        spread_z = self._spread_zscore(spread, price_history, bid, ask)
        imbalance = self._quote_imbalance(buy_volume, sell_volume)
        tick_qual = self._tick_quality(ticks)
        impact = self._price_impact(price_history, volume_history)
        flow_dir = self._flow_direction(ticks)
        adv_sel = self._adverse_selection_score(ticks)

        # Toxicity: high impact + consistent aggression = informed flow
        toxicity = 0.5 * impact + 0.5 * adv_sel

        # Spread regime: negative z-score (tight spread) boosts confidence
        spread_confidence_boost = max(0.0, -spread_z / 3.0)   # z=-3 → +1.0 boost

        # Primary directional signal: follow informed flow direction
        # If imbalance and flow_dir agree, signal is stronger
        if abs(imbalance) >= self.IMBALANCE_THRESHOLD or abs(flow_dir) >= 0.2:
            raw_dir = 0.6 * flow_dir + 0.4 * imbalance
        else:
            raw_dir = 0.0

        # Toxic flow amplifies the signal
        if toxicity >= self.TOXICITY_THRESHOLD:
            raw_dir *= (1.0 + 0.5 * toxicity)

        score = max(-1.0, min(1.0, raw_dir))

        # Confidence
        confidence = (
            0.25 * tick_qual
            + 0.25 * toxicity
            + 0.20 * min(abs(imbalance) / 0.5, 1.0)
            + 0.15 * min(abs(flow_dir) / 0.5, 1.0)
            + 0.15 * max(0.0, min(1.0, spread_confidence_boost))
        )
        confidence = max(0.0, min(1.0, confidence))

        if score > 0.15:
            direction = "LONG"
        elif score < -0.15:
            direction = "SHORT"
        else:
            direction = "FLAT"

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=price if direction != "FLAT" else None,
            meta={
                "spread_zscore": round(spread_z, 4),
                "quote_imbalance": round(imbalance, 4),
                "tick_quality": round(tick_qual, 4),
                "price_impact": round(impact, 4),
                "flow_direction": round(flow_dir, 4),
                "adverse_selection": round(adv_sel, 4),
                "toxicity": round(toxicity, 4),
            },
        )
