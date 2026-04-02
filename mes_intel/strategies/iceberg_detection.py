"""Iceberg Order Detection Strategy.

Identifies hidden institutional orders by detecting repeated prints at the
same price level with consistent lot sizes and price absorption (price not
moving despite repeated hits).

Defensive icebergs hold a price floor — signal to go LONG above them.
Offensive icebergs cap price — signal to go SHORT below them.
"""
from __future__ import annotations

import statistics
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .base import Strategy, StrategyResult


@dataclass
class IcebergSignal:
    """A detected iceberg order at a specific price level."""
    price: float
    side: str                      # 'BUY' or 'SELL'
    trade_count: int               # number of times level was hit
    avg_lot_size: float            # average print size
    lot_size_cv: float             # coefficient of variation of lot sizes (lower = more consistent)
    total_visible_volume: float    # sum of visible prints
    estimated_hidden_size: float   # extrapolated total hidden order
    absorption_strength: float     # 0.0 to 1.0 — how well price was held
    window_seconds: float          # time window over which signal was observed
    confidence: float

    def __repr__(self) -> str:
        return (
            f"Iceberg({self.side}@{self.price:.2f} "
            f"count={self.trade_count} avg_lot={self.avg_lot_size:.0f} "
            f"hidden≈{self.estimated_hidden_size:.0f} abs={self.absorption_strength:.2f})"
        )


class IcebergDetectionStrategy(Strategy):
    """Hidden institutional order detection via repeated-print analysis."""

    name = "iceberg_detection"
    description = "Detects iceberg orders from repeated same-price prints with size consistency"

    # MES tick size
    TICK_SIZE = 0.25

    def __init__(
        self,
        min_repeat_count: int = 5,         # minimum times a level must be hit
        window_seconds: float = 60.0,      # look-back window in seconds
        size_cv_threshold: float = 0.20,   # max coefficient of variation for "consistent" sizes
        price_tolerance_ticks: int = 0,    # ticks of tolerance for "same level" (0 = exact)
        absorption_range_ticks: float = 4, # max price movement (in ticks) to call absorption
        min_hidden_multiplier: float = 3.0,# estimated hidden = visible × this factor
        stop_ticks: int = 4,               # stop distance from iceberg level in ticks
    ):
        self.min_repeat_count = min_repeat_count
        self.window_seconds = window_seconds
        self.size_cv_threshold = size_cv_threshold
        self.price_tolerance = price_tolerance_ticks * self.TICK_SIZE
        self.absorption_range_ticks = absorption_range_ticks
        self.min_hidden_multiplier = min_hidden_multiplier
        self.stop_ticks = stop_ticks

    def required_data(self) -> list[str]:
        return ["price", "ticks", "bid", "ask", "vah", "val"]

    # ------------------------------------------------------------------
    # Tick windowing
    # ------------------------------------------------------------------

    def _get_recent_ticks(self, ticks: list, window_seconds: float) -> list:
        """Return ticks within the last window_seconds. Ticks must have .timestamp."""
        if not ticks:
            return []

        # Use the most recent tick's timestamp as reference
        try:
            latest_ts = ticks[-1].timestamp
            cutoff = latest_ts - window_seconds
            return [t for t in ticks if t.timestamp >= cutoff]
        except AttributeError:
            # No timestamp attribute — use all ticks (fallback)
            return ticks[-200:]  # cap to last 200 ticks

    # ------------------------------------------------------------------
    # Iceberg detection core
    # ------------------------------------------------------------------

    def _group_ticks_by_level(
        self, ticks: list
    ) -> dict[float, list]:
        """
        Group ticks by price level, applying price_tolerance clustering.

        Returns dict: representative_price → list of ticks at that level.
        """
        if self.price_tolerance == 0:
            # Exact grouping
            groups: dict[float, list] = defaultdict(list)
            for t in ticks:
                groups[t.price].append(t)
            return dict(groups)

        # Tolerance clustering: merge prices within price_tolerance
        sorted_prices = sorted(set(t.price for t in ticks))
        price_map: dict[float, float] = {}  # raw_price → representative
        clusters: list[list[float]] = []
        current_cluster: list[float] = []

        for p in sorted_prices:
            if not current_cluster or p - current_cluster[0] <= self.price_tolerance:
                current_cluster.append(p)
            else:
                clusters.append(current_cluster)
                current_cluster = [p]
        if current_cluster:
            clusters.append(current_cluster)

        for cluster in clusters:
            rep = float(np.mean(cluster))
            for p in cluster:
                price_map[p] = rep

        grouped: dict[float, list] = defaultdict(list)
        for t in ticks:
            grouped[price_map.get(t.price, t.price)].append(t)
        return dict(grouped)

    def _analyze_level(
        self,
        level_price: float,
        level_ticks: list,
        all_ticks: list,
        current_price: float,
    ) -> Optional[IcebergSignal]:
        """
        Determine if a price level shows iceberg characteristics.

        Returns IcebergSignal if criteria met, else None.
        """
        if len(level_ticks) < self.min_repeat_count:
            return None

        # --- Extract attributes ---
        try:
            sizes = [float(t.size) for t in level_ticks]
            aggressors = [bool(t.aggressor) for t in level_ticks]
        except AttributeError:
            return None

        if not sizes:
            return None

        # --- Lot size consistency ---
        avg_size = statistics.mean(sizes)
        if avg_size <= 0:
            return None
        try:
            std_size = statistics.stdev(sizes) if len(sizes) > 1 else 0.0
        except statistics.StatisticsError:
            std_size = 0.0
        cv = std_size / avg_size  # coefficient of variation

        if cv > self.size_cv_threshold:
            return None  # size too variable — not iceberg-like

        total_visible = sum(sizes)

        # --- Determine side ---
        # Buy iceberg: aggressor sellers hitting a passive buy order repeatedly
        # In ATAS/footprint: aggressor=True means trade hit the ask (buyer)
        # A defensive iceberg at support = passive buyer absorbing aggressive sellers
        # aggressor=False at level = passive buyer sitting → buy iceberg
        buy_count = sum(1 for a in aggressors if not a)   # passive = absorbed selling
        sell_count = sum(1 for a in aggressors if a)       # aggressive = absorbed buying

        if buy_count >= sell_count:
            side = "BUY"   # repeated passive buys = buy iceberg floor
        else:
            side = "SELL"  # repeated passive sells = sell iceberg ceiling

        # --- Absorption strength ---
        # Price range during the level's activity window
        try:
            level_prices = [t.price for t in level_ticks]
            price_range_during = max(level_prices) - min(level_prices)
        except (TypeError, ValueError):
            price_range_during = 0.0

        max_range_pts = self.absorption_range_ticks * self.TICK_SIZE
        if max_range_pts > 0:
            absorption_strength = float(np.clip(1.0 - price_range_during / max_range_pts, 0.0, 1.0))
        else:
            absorption_strength = 1.0

        # Additional absorption check: current price still near level
        dist_from_level = abs(current_price - level_price)
        still_at_level = dist_from_level <= self.absorption_range_ticks * self.TICK_SIZE * 1.5

        if not still_at_level:
            # Price has moved away — iceberg may have been exhausted
            absorption_strength *= 0.5

        # --- Estimate hidden size ---
        # Replenishment rate: total visible / count ≈ hidden refill per cycle
        # Simple heuristic: total hidden ≈ visible × multiplier
        estimated_hidden = total_visible * self.min_hidden_multiplier

        # --- Confidence ---
        repeat_score = min(len(level_ticks) / 15.0, 1.0)    # more hits = higher conf
        cv_score = 1.0 - cv / self.size_cv_threshold          # lower cv = higher conf
        confidence = float(np.clip(
            0.3 * repeat_score + 0.4 * absorption_strength + 0.3 * cv_score,
            0.0, 1.0
        ))

        # --- Window ---
        try:
            ts_list = [t.timestamp for t in level_ticks]
            window = max(ts_list) - min(ts_list)
        except (AttributeError, TypeError):
            window = 0.0

        return IcebergSignal(
            price=level_price,
            side=side,
            trade_count=len(level_ticks),
            avg_lot_size=avg_size,
            lot_size_cv=cv,
            total_visible_volume=total_visible,
            estimated_hidden_size=estimated_hidden,
            absorption_strength=absorption_strength,
            window_seconds=window,
            confidence=confidence,
        )

    def _scan_for_icebergs(
        self, ticks: list, current_price: float
    ) -> list[IcebergSignal]:
        """Run iceberg detection across all price levels in the tick window."""
        recent = self._get_recent_ticks(ticks, self.window_seconds)
        if not recent:
            return []

        grouped = self._group_ticks_by_level(recent)
        signals: list[IcebergSignal] = []

        for level_price, level_ticks in grouped.items():
            sig = self._analyze_level(level_price, level_ticks, recent, current_price)
            if sig is not None:
                signals.append(sig)

        # Sort by confidence descending
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals

    # ------------------------------------------------------------------
    # Level selection and trade logic
    # ------------------------------------------------------------------

    def _select_best_iceberg(
        self,
        icebergs: list[IcebergSignal],
        current_price: float,
        vah: float,
        val: float,
    ) -> Optional[IcebergSignal]:
        """
        Pick the most actionable iceberg signal.

        Prefers:
        - Buy icebergs near/below current price (floor signal)
        - Sell icebergs near/above current price (ceiling signal)
        - Highest confidence
        """
        buy_icebergs = [s for s in icebergs if s.side == "BUY" and s.price <= current_price + 2.0]
        sell_icebergs = [s for s in icebergs if s.side == "SELL" and s.price >= current_price - 2.0]

        best_buy = buy_icebergs[0] if buy_icebergs else None
        best_sell = sell_icebergs[0] if sell_icebergs else None

        if best_buy and best_sell:
            return best_buy if best_buy.confidence >= best_sell.confidence else best_sell
        return best_buy or best_sell

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price: float = market_data.get("price", 0.0)
        ticks: list = market_data.get("ticks", [])
        bid: float = market_data.get("bid", price)
        ask: float = market_data.get("ask", price)
        vah: float = market_data.get("vah", price + 10)
        val: float = market_data.get("val", price - 10)

        if price == 0.0 or not ticks:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no tick data available"},
            )

        icebergs = self._scan_for_icebergs(ticks, price)

        if not icebergs:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no iceberg patterns detected", "ticks_scanned": len(ticks)},
            )

        best = self._select_best_iceberg(icebergs, price, vah, val)

        if best is None:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "icebergs found but none actionable", "count": len(icebergs)},
            )

        stop_pts = self.stop_ticks * self.TICK_SIZE

        if best.side == "BUY":
            # Defensive iceberg = price floor — go LONG above it
            direction = "LONG"
            score = min(0.5 + best.confidence * 0.5, 1.0)
            entry_price = ask                              # enter at current ask
            stop_price = best.price - stop_pts            # stop below iceberg
            # Target: opposite side of value area or next major level
            target_price = vah if price < vah else price + (vah - val) * 0.618
        else:
            # Offensive iceberg = ceiling — go SHORT below it
            direction = "SHORT"
            score = -(min(0.5 + best.confidence * 0.5, 1.0))
            entry_price = bid
            stop_price = best.price + stop_pts
            target_price = val if price > val else price - (vah - val) * 0.618

        return StrategyResult(
            name=self.name,
            score=score,
            confidence=best.confidence,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "iceberg_price": round(best.price, 2),
                "iceberg_side": best.side,
                "trade_count": best.trade_count,
                "avg_lot_size": round(best.avg_lot_size, 1),
                "lot_size_cv": round(best.lot_size_cv, 3),
                "total_visible_volume": round(best.total_visible_volume, 0),
                "estimated_hidden_size": round(best.estimated_hidden_size, 0),
                "absorption_strength": round(best.absorption_strength, 3),
                "window_seconds": round(best.window_seconds, 1),
                "total_icebergs_found": len(icebergs),
                "all_icebergs": [
                    {
                        "price": round(s.price, 2),
                        "side": s.side,
                        "count": s.trade_count,
                        "confidence": round(s.confidence, 3),
                    }
                    for s in icebergs[:5]  # top 5 for meta
                ],
            },
        )
