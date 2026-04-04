"""Order Flow Imbalance Strategy — multi-level imbalance scoring.

Analyzes order flow at multiple levels simultaneously to detect institutional
footprints. Individual level imbalances are noise, but when multiple adjacent
levels all show the same directional pressure, it's institutional.

Key patterns:
- Stacked imbalance: 3+ consecutive levels of directional pressure
- Volume cluster: abnormal volume concentration at specific levels
- Aggressive vs passive flow: who is crossing the spread
- Zero-print levels: prices skipped entirely = fast institutional move
"""
from __future__ import annotations

import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class OrderFlowImbalanceStrategy(Strategy):
    """Multi-level order flow imbalance scoring with institutional detection."""

    name = "orderflow_imbalance"
    description = "Multi-level imbalance scoring, stacked imbalance, volume clusters, zero-prints"

    # Thresholds
    IMBALANCE_RATIO = 3.0      # bid/ask ratio for "imbalanced" level
    STACKED_MIN_LEVELS = 3     # minimum consecutive imbalanced levels
    VOLUME_CLUSTER_ZSCORE = 2.0  # z-score for volume concentration
    ZERO_PRINT_MIN_LEVELS = 2   # minimum skipped levels for zero-print signal

    def required_data(self) -> list[str]:
        return [
            "price", "volume_profile", "footprint_bars",
            "buy_volume", "sell_volume",
            "price_history", "delta_history",
        ]

    # ------------------------------------------------------------------
    # Multi-level imbalance analysis
    # ------------------------------------------------------------------

    def _analyze_level_imbalances(self, volume_profile) -> tuple[int, int, list[str]]:
        """Analyze bid/ask imbalance at every price level.

        Returns (buy_imbalanced_levels, sell_imbalanced_levels, descriptions).
        """
        if volume_profile is None:
            return 0, 0, []

        levels = volume_profile.sorted_levels()
        if not levels:
            return 0, 0, []

        buy_imbalanced = 0
        sell_imbalanced = 0
        notes = []

        for level in levels:
            if level.bid_volume == 0 and level.ask_volume > 0:
                buy_imbalanced += 1
            elif level.ask_volume == 0 and level.bid_volume > 0:
                sell_imbalanced += 1
            elif level.bid_volume > 0 and level.ask_volume > 0:
                ratio = level.ask_volume / level.bid_volume
                if ratio >= self.IMBALANCE_RATIO:
                    buy_imbalanced += 1
                elif 1.0 / ratio >= self.IMBALANCE_RATIO:
                    sell_imbalanced += 1

        total = len(levels)
        if buy_imbalanced > total * 0.4:
            notes.append(f"Strong buy imbalance ({buy_imbalanced}/{total} levels)")
        elif sell_imbalanced > total * 0.4:
            notes.append(f"Strong sell imbalance ({sell_imbalanced}/{total} levels)")

        return buy_imbalanced, sell_imbalanced, notes

    # ------------------------------------------------------------------
    # Stacked imbalance detection
    # ------------------------------------------------------------------

    def _detect_stacked_imbalance(self, volume_profile) -> tuple[float, str]:
        """Detect stacked imbalances: 3+ consecutive levels with same-direction pressure.

        This is the strongest order flow signal — it indicates aggressive
        institutional buying or selling across multiple price levels simultaneously.

        Returns (score [-1, 1], description).
        """
        if volume_profile is None:
            return 0.0, ""

        levels = volume_profile.sorted_levels()
        if len(levels) < self.STACKED_MIN_LEVELS:
            return 0.0, ""

        best_buy_stack = 0
        best_sell_stack = 0
        current_buy_stack = 0
        current_sell_stack = 0

        for level in levels:
            if level.total_volume == 0:
                current_buy_stack = 0
                current_sell_stack = 0
                continue

            delta_pct = level.delta / level.total_volume if level.total_volume > 0 else 0

            if delta_pct > 0.3:  # >65% ask = aggressive buying
                current_buy_stack += 1
                current_sell_stack = 0
                best_buy_stack = max(best_buy_stack, current_buy_stack)
            elif delta_pct < -0.3:  # >65% bid = aggressive selling
                current_sell_stack += 1
                current_buy_stack = 0
                best_sell_stack = max(best_sell_stack, current_sell_stack)
            else:
                current_buy_stack = 0
                current_sell_stack = 0

        if best_buy_stack >= self.STACKED_MIN_LEVELS:
            score = min(0.65, 0.25 + best_buy_stack * 0.10)
            return score, f"Stacked buy imbalance ({best_buy_stack} levels)"
        elif best_sell_stack >= self.STACKED_MIN_LEVELS:
            score = -min(0.65, 0.25 + best_sell_stack * 0.10)
            return score, f"Stacked sell imbalance ({best_sell_stack} levels)"

        return 0.0, ""

    # ------------------------------------------------------------------
    # Volume cluster detection
    # ------------------------------------------------------------------

    def _detect_volume_clusters(self, volume_profile, price: float) -> tuple[float, str]:
        """Detect abnormal volume concentration at specific levels.

        Volume clusters near current price indicate institutional interest.
        Above price = resistance. Below price = support.

        Returns (score [-1, 1], description).
        """
        if volume_profile is None:
            return 0.0, ""

        levels = volume_profile.sorted_levels()
        if len(levels) < 5:
            return 0.0, ""

        volumes = [l.total_volume for l in levels]
        avg_vol = statistics.mean(volumes)
        std_vol = statistics.stdev(volumes) if len(volumes) > 2 else avg_vol

        if std_vol == 0:
            return 0.0, ""

        # Find clusters (z-score > threshold) near price
        clusters_above = []
        clusters_below = []

        for level in levels:
            z = (level.total_volume - avg_vol) / std_vol
            if z >= self.VOLUME_CLUSTER_ZSCORE:
                dist = level.price - price
                if dist > 0:
                    clusters_above.append((level.price, z, level.delta))
                else:
                    clusters_below.append((level.price, z, level.delta))

        score = 0.0
        desc_parts = []

        if clusters_above and not clusters_below:
            # Only resistance above — bearish
            strongest = max(clusters_above, key=lambda x: x[1])
            score = -0.20
            desc_parts.append(f"Volume cluster resistance @ {strongest[0]:.2f} (z={strongest[1]:.1f})")
        elif clusters_below and not clusters_above:
            # Only support below — bullish
            strongest = max(clusters_below, key=lambda x: x[1])
            score = 0.20
            desc_parts.append(f"Volume cluster support @ {strongest[0]:.2f} (z={strongest[1]:.1f})")
        elif clusters_above and clusters_below:
            # Both — trapped range
            desc_parts.append("Volume clusters above AND below — range-bound")

        desc = "; ".join(desc_parts) if desc_parts else ""
        return score, desc

    # ------------------------------------------------------------------
    # Zero-print (skipped price) detection
    # ------------------------------------------------------------------

    def _detect_zero_prints(self, volume_profile, price: float) -> tuple[float, str]:
        """Detect zero-print levels: prices that were skipped entirely.

        When price moves through a level so fast that no trades print there,
        it indicates an aggressive institutional order that swept through.
        These levels often get revisited later.

        Returns (score [-1, 1], description).
        """
        if volume_profile is None:
            return 0.0, ""

        levels = volume_profile.sorted_levels()
        if len(levels) < 5:
            return 0.0, ""

        prices_with_volume = {l.price for l in levels if l.total_volume > 0}
        all_prices = {l.price for l in levels}

        # Find gaps (zero-print levels)
        zero_prints_above = []
        zero_prints_below = []

        sorted_prices = sorted(all_prices)
        for i in range(len(sorted_prices) - 1):
            gap_ticks = int((sorted_prices[i + 1] - sorted_prices[i]) / 0.25)
            if gap_ticks > 1:
                # There are skipped tick levels between these two
                gap_center = (sorted_prices[i] + sorted_prices[i + 1]) / 2.0
                if gap_center > price:
                    zero_prints_above.append((gap_center, gap_ticks))
                else:
                    zero_prints_below.append((gap_center, gap_ticks))

        if not zero_prints_above and not zero_prints_below:
            return 0.0, ""

        score = 0.0
        desc = ""

        if zero_prints_above and len(zero_prints_above) >= self.ZERO_PRINT_MIN_LEVELS:
            # Zero prints above = aggressive buying swept through → bullish
            # But these levels may get revisited (magnet)
            score = 0.15
            desc = f"Zero-print levels above ({len(zero_prints_above)}x) — aggressive buying swept through"

        if zero_prints_below and len(zero_prints_below) >= self.ZERO_PRINT_MIN_LEVELS:
            # Zero prints below = aggressive selling swept through → bearish
            score = -0.15
            desc = f"Zero-print levels below ({len(zero_prints_below)}x) — aggressive selling swept through"

        return score, desc

    # ------------------------------------------------------------------
    # Aggressive vs passive flow
    # ------------------------------------------------------------------

    def _aggressive_passive_ratio(self, buy_volume: float,
                                    sell_volume: float,
                                    delta_history: list[float]) -> tuple[float, str]:
        """Compute the aggressive/passive flow ratio.

        Aggressive flow = trades that cross the spread (market orders).
        In MES, buy_volume at ask = aggressive buying, sell_volume at bid = aggressive selling.

        Returns (score [-1, 1], description).
        """
        total = buy_volume + sell_volume
        if total == 0:
            return 0.0, ""

        buy_pct = buy_volume / total
        sell_pct = sell_volume / total

        # Strong imbalance in aggressive flow
        if buy_pct > 0.65:
            score = min(0.40, (buy_pct - 0.5) * 2.0)
            # Check if delta confirms
            if delta_history and sum(delta_history[-3:]) > 0:
                score = min(0.55, score + 0.15)
            return score, f"Aggressive buying {buy_pct:.0%} (delta-confirmed)" if delta_history and sum(delta_history[-3:]) > 0 else f"Aggressive buying {buy_pct:.0%}"

        elif sell_pct > 0.65:
            score = -min(0.40, (sell_pct - 0.5) * 2.0)
            if delta_history and sum(delta_history[-3:]) < 0:
                score = max(-0.55, score - 0.15)
            return score, f"Aggressive selling {sell_pct:.0%} (delta-confirmed)" if delta_history and sum(delta_history[-3:]) < 0 else f"Aggressive selling {sell_pct:.0%}"

        return 0.0, ""

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        volume_profile = market_data.get("volume_profile")
        footprint_bars = market_data.get("footprint_bars", [])
        buy_volume = market_data.get("buy_volume", 0.0)
        sell_volume = market_data.get("sell_volume", 0.0)
        price_history = market_data.get("price_history", [])
        delta_history = market_data.get("delta_history", [])

        if not price:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no price data"},
            )

        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []

        # 1. Multi-level imbalance
        buy_imb, sell_imb, imb_notes = self._analyze_level_imbalances(volume_profile)
        notes.extend(imb_notes)
        if buy_imb > sell_imb and buy_imb > 3:
            signals.append(("level_imbalance", 0.25, 0.15))
        elif sell_imb > buy_imb and sell_imb > 3:
            signals.append(("level_imbalance", -0.25, 0.15))

        # 2. Stacked imbalance (strongest signal)
        stacked_score, stacked_desc = self._detect_stacked_imbalance(volume_profile)
        if abs(stacked_score) > 0:
            signals.append(("stacked_imbalance", stacked_score, 0.30))
            notes.append(stacked_desc)

        # 3. Volume clusters
        cluster_score, cluster_desc = self._detect_volume_clusters(volume_profile, price)
        if abs(cluster_score) > 0:
            signals.append(("volume_cluster", cluster_score, 0.15))
            notes.append(cluster_desc)

        # 4. Zero-print levels
        zero_score, zero_desc = self._detect_zero_prints(volume_profile, price)
        if abs(zero_score) > 0:
            signals.append(("zero_prints", zero_score, 0.15))
            notes.append(zero_desc)

        # 5. Aggressive vs passive flow
        agg_score, agg_desc = self._aggressive_passive_ratio(
            buy_volume, sell_volume, delta_history
        )
        if abs(agg_score) > 0:
            signals.append(("aggressive_flow", agg_score, 0.25))
            notes.append(agg_desc)

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"buy_imbalanced": buy_imb, "sell_imbalanced": sell_imb,
                       "notes": notes},
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))

        agreeing = sum(1 for _, s, _ in signals if s * score > 0)
        confidence = min(1.0, 0.15 + 0.15 * agreeing + 0.30 * abs(score))

        # Stacked imbalance is high-conviction
        if abs(stacked_score) >= 0.25:
            confidence = min(1.0, confidence + 0.15)

        if abs(score) < 0.10:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        entry_price = price if direction != "FLAT" else None
        atr_est = (max(price_history[-10:]) - min(price_history[-10:])) if len(price_history) >= 10 else 4.0
        stop_dist = max(2.0, atr_est * 0.5)
        target_dist = stop_dist * 1.5

        stop_price = round(price - stop_dist, 2) if direction == "LONG" else (
            round(price + stop_dist, 2) if direction == "SHORT" else None)
        target_price = round(price + target_dist, 2) if direction == "LONG" else (
            round(price - target_dist, 2) if direction == "SHORT" else None)

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            meta={
                "buy_imbalanced_levels": buy_imb,
                "sell_imbalanced_levels": sell_imb,
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
