"""Delta Flow Strategy — advanced cumulative delta analysis.

Goes beyond delta divergence to analyze delta *dynamics*: acceleration/deceleration,
absorption (big delta with no price movement), exhaustion waves, and delta momentum
relative to volume. These are the patterns professional order flow traders watch.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional

from .base import Strategy, StrategyResult


class DeltaFlowStrategy(Strategy):
    """Advanced delta dynamics: acceleration, absorption, exhaustion, momentum."""

    name = "delta_flow"
    description = "Delta acceleration, absorption, exhaustion waves, delta momentum vs volume"

    LOOKBACK = 20
    MIN_BARS = 8
    ABSORPTION_PRICE_THRESHOLD = 0.5   # max price move (pts) to qualify as absorption
    ABSORPTION_DELTA_THRESHOLD = 500   # min delta magnitude for absorption
    EXHAUSTION_DECEL_THRESHOLD = 0.4   # deceleration ratio to flag exhaustion

    def required_data(self) -> list[str]:
        return [
            "price", "price_history", "delta_history",
            "volume_history", "session_delta",
            "footprint_bars",
        ]

    # ------------------------------------------------------------------
    # Delta acceleration / deceleration
    # ------------------------------------------------------------------

    def _delta_acceleration(self, delta_history: list[float]) -> tuple[float, float]:
        """Compute delta velocity (1st derivative) and acceleration (2nd derivative).

        Returns (velocity, acceleration) normalized to [-1, 1].
        Positive acceleration with positive velocity = strengthening buying.
        Negative acceleration with positive velocity = buying weakening (exhaustion).
        """
        if len(delta_history) < 6:
            return 0.0, 0.0

        recent = delta_history[-self.LOOKBACK:]
        n = len(recent)

        # Velocity: slope of delta over recent window
        velocities = [recent[i] - recent[i - 1] for i in range(1, n)]
        if not velocities:
            return 0.0, 0.0
        velocity = statistics.mean(velocities[-5:])

        # Acceleration: change in velocity
        if len(velocities) < 4:
            return velocity, 0.0

        accels = [velocities[i] - velocities[i - 1] for i in range(1, len(velocities))]
        acceleration = statistics.mean(accels[-3:])

        # Normalize by recent delta range
        delta_range = max(recent) - min(recent)
        if delta_range == 0:
            return 0.0, 0.0

        norm_vel = max(-1.0, min(1.0, velocity / (delta_range * 0.2)))
        norm_accel = max(-1.0, min(1.0, acceleration / (delta_range * 0.1)))

        return norm_vel, norm_accel

    # ------------------------------------------------------------------
    # Absorption detection
    # ------------------------------------------------------------------

    def _detect_absorption(self, price_history: list[float],
                            delta_history: list[float]) -> tuple[bool, str]:
        """Detect absorption: large delta but minimal price movement.

        Absorption means one side is aggressively trading but the other side is
        absorbing it without giving ground. This is a *reversal* precursor.

        Returns (detected, description).
        """
        if len(price_history) < 5 or len(delta_history) < 5:
            return False, ""

        recent_prices = price_history[-5:]
        recent_deltas = delta_history[-5:]

        price_move = abs(recent_prices[-1] - recent_prices[0])
        total_abs_delta = sum(abs(d) for d in recent_deltas)
        avg_abs_delta = total_abs_delta / len(recent_deltas)

        if price_move < self.ABSORPTION_PRICE_THRESHOLD and avg_abs_delta > self.ABSORPTION_DELTA_THRESHOLD:
            net_delta = sum(recent_deltas)
            if net_delta > 0:
                return True, f"Buy absorption (delta={net_delta:+.0f}, price_move={price_move:.2f}pts) — sellers absorbing"
            else:
                return True, f"Sell absorption (delta={net_delta:+.0f}, price_move={price_move:.2f}pts) — buyers absorbing"

        return False, ""

    # ------------------------------------------------------------------
    # Exhaustion wave detection
    # ------------------------------------------------------------------

    def _detect_exhaustion(self, delta_history: list[float],
                            price_history: list[float]) -> tuple[float, str]:
        """Detect exhaustion waves: delta decelerating while price extends.

        This is the classic "last gasp" — aggressive buying/selling that's losing
        steam. The final push often marks the extreme.

        Returns (exhaustion_score [0, 1], description).
        """
        if len(delta_history) < 8 or len(price_history) < 8:
            return 0.0, ""

        # Compare last 3 bars' delta magnitude vs previous 5
        recent_delta = [abs(d) for d in delta_history[-3:]]
        prior_delta = [abs(d) for d in delta_history[-8:-3]]

        recent_avg = statistics.mean(recent_delta) if recent_delta else 0
        prior_avg = statistics.mean(prior_delta) if prior_delta else 0

        if prior_avg == 0:
            return 0.0, ""

        decel_ratio = 1.0 - (recent_avg / prior_avg)

        # Price must still be extending (making new highs/lows)
        recent_prices = price_history[-3:]
        prior_prices = price_history[-8:-3]

        price_extending_up = max(recent_prices) > max(prior_prices)
        price_extending_down = min(recent_prices) < min(prior_prices)
        net_delta_sign = sum(delta_history[-3:])

        if decel_ratio > self.EXHAUSTION_DECEL_THRESHOLD:
            if price_extending_up and net_delta_sign > 0:
                score = min(1.0, decel_ratio)
                return score, f"Buy exhaustion (decel={decel_ratio:.2f}) — buyers losing steam at highs"
            elif price_extending_down and net_delta_sign < 0:
                score = min(1.0, decel_ratio)
                return score, f"Sell exhaustion (decel={decel_ratio:.2f}) — sellers losing steam at lows"

        return 0.0, ""

    # ------------------------------------------------------------------
    # Delta momentum (delta / volume ratio)
    # ------------------------------------------------------------------

    def _delta_momentum(self, delta_history: list[float],
                         volume_history: list[float]) -> float:
        """Delta-to-volume ratio: how much of volume is directional.

        High ratio = aggressive directional flow.
        Low ratio = passive/balanced flow.

        Returns [-1, 1] where magnitude = conviction.
        """
        if len(delta_history) < 3 or len(volume_history) < 3:
            return 0.0

        n = min(len(delta_history), len(volume_history), 5)
        deltas = delta_history[-n:]
        volumes = volume_history[-n:]

        ratios = []
        for d, v in zip(deltas, volumes):
            if v > 0:
                ratios.append(d / v)

        if not ratios:
            return 0.0

        avg_ratio = statistics.mean(ratios)
        return max(-1.0, min(1.0, avg_ratio * 3.0))

    # ------------------------------------------------------------------
    # Stacked imbalance from footprint bars
    # ------------------------------------------------------------------

    def _stacked_imbalance(self, footprint_bars) -> tuple[float, str]:
        """Detect stacked imbalances in recent footprint bars.

        3+ consecutive price levels where bid >> ask (or vice versa) = aggressive
        institutional flow. Very strong directional signal.

        Returns (score [-1, 1], description).
        """
        if not footprint_bars:
            return 0.0, ""

        # Use the most recent bar's profile
        recent_bars = footprint_bars[-3:] if len(footprint_bars) >= 3 else footprint_bars

        total_stacked_buy = 0
        total_stacked_sell = 0

        for bar in recent_bars:
            if not hasattr(bar, 'profile') or bar.profile is None:
                continue

            levels = bar.profile.sorted_levels()
            if len(levels) < 3:
                continue

            # Look for 3+ consecutive levels with strong imbalance
            buy_streak = 0
            sell_streak = 0

            for level in levels:
                if level.total_volume == 0:
                    buy_streak = 0
                    sell_streak = 0
                    continue

                ratio = level.delta / level.total_volume if level.total_volume > 0 else 0

                if ratio > 0.4:  # >70% ask volume
                    buy_streak += 1
                    sell_streak = 0
                    if buy_streak >= 3:
                        total_stacked_buy += 1
                elif ratio < -0.4:  # >70% bid volume
                    sell_streak += 1
                    buy_streak = 0
                    if sell_streak >= 3:
                        total_stacked_sell += 1
                else:
                    buy_streak = 0
                    sell_streak = 0

        if total_stacked_buy > total_stacked_sell and total_stacked_buy > 0:
            score = min(0.5, total_stacked_buy * 0.15)
            return score, f"Stacked buy imbalance ({total_stacked_buy}x)"
        elif total_stacked_sell > total_stacked_buy and total_stacked_sell > 0:
            score = -min(0.5, total_stacked_sell * 0.15)
            return score, f"Stacked sell imbalance ({total_stacked_sell}x)"

        return 0.0, ""

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        price = market_data.get("price", 0.0)
        price_history = market_data.get("price_history", [])
        delta_history = market_data.get("delta_history", [])
        volume_history = market_data.get("volume_history", [])
        footprint_bars = market_data.get("footprint_bars", [])

        if not price or len(delta_history) < self.MIN_BARS:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "insufficient delta data"},
            )

        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []

        # 1. Delta acceleration/deceleration
        velocity, acceleration = self._delta_acceleration(delta_history)

        if abs(velocity) > 0.3:
            # Strong velocity + aligned acceleration = trend continuation
            if velocity * acceleration > 0 and abs(acceleration) > 0.2:
                signals.append(("delta_accel", velocity * 0.40, 0.25))
                notes.append(f"Delta accelerating {'up' if velocity > 0 else 'down'} (v={velocity:.2f}, a={acceleration:.2f})")
            # Strong velocity + opposing acceleration = potential exhaustion
            elif velocity * acceleration < 0 and abs(acceleration) > 0.3:
                signals.append(("delta_decel", -velocity * 0.25, 0.20))
                notes.append(f"Delta decelerating (v={velocity:.2f}, a={acceleration:.2f}) — momentum fading")

        # 2. Absorption
        absorbed, absorb_desc = self._detect_absorption(price_history, delta_history)
        if absorbed:
            # Absorption is contrarian — the absorbing side will prevail
            net_delta = sum(delta_history[-5:])
            if net_delta > 0:
                signals.append(("absorption", -0.40, 0.25))
            else:
                signals.append(("absorption", 0.40, 0.25))
            notes.append(absorb_desc)

        # 3. Exhaustion wave
        exhaust_score, exhaust_desc = self._detect_exhaustion(delta_history, price_history)
        if exhaust_score > 0.3:
            # Exhaustion is contrarian
            net_delta = sum(delta_history[-3:])
            contrarian = -1.0 if net_delta > 0 else 1.0
            signals.append(("exhaustion", contrarian * exhaust_score * 0.45, 0.25))
            notes.append(exhaust_desc)

        # 4. Delta momentum (delta/volume ratio)
        momentum = self._delta_momentum(delta_history, volume_history)
        if abs(momentum) > 0.3:
            signals.append(("delta_momentum", momentum * 0.30, 0.15))
            notes.append(f"Delta momentum={momentum:.2f} ({'aggressive buying' if momentum > 0 else 'aggressive selling'})")

        # 5. Stacked imbalance
        stacked_score, stacked_desc = self._stacked_imbalance(footprint_bars)
        if abs(stacked_score) > 0:
            signals.append(("stacked_imbalance", stacked_score, 0.20))
            notes.append(stacked_desc)

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"velocity": round(velocity, 3), "acceleration": round(acceleration, 3)},
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))

        # Confidence: agreement + signal count
        agreeing = sum(1 for _, s, _ in signals if s * score > 0)
        confidence = min(1.0, 0.15 + 0.20 * agreeing + 0.25 * abs(score))

        # Absorption and exhaustion are high-confidence patterns
        if absorbed or exhaust_score > 0.4:
            confidence = min(1.0, confidence + 0.15)

        if abs(score) < 0.10:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        entry_price = price if direction != "FLAT" else None
        atr_est = (max(price_history[-10:]) - min(price_history[-10:])) if len(price_history) >= 10 else 4.0
        stop_dist = max(2.0, atr_est * 0.6)
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
                "velocity": round(velocity, 3),
                "acceleration": round(acceleration, 3),
                "absorbed": absorbed,
                "exhaustion_score": round(exhaust_score, 3),
                "delta_momentum": round(momentum, 3),
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
