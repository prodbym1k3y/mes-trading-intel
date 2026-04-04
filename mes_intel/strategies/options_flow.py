"""Options Flow Strategy — dealer hedging pressure, skew, term structure.

Analyzes the *dynamics* of options positioning to derive directional signals:
- Dealer hedging pressure: when dealers are short gamma, their delta-hedging
  amplifies moves. When long gamma, they dampen moves (pin to strikes).
- Skew analysis: put skew steepening = fear, call skew = speculation.
- GEX gradient: rate of change in gamma exposure = hedging urgency.
- Charm/Vanna flows: time decay and vol sensitivity of dealer positions.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from .base import Strategy, StrategyResult


class OptionsFlowStrategy(Strategy):
    """Options flow dynamics: hedging pressure, skew, GEX gradient, charm/vanna."""

    name = "options_flow"
    description = "Dealer hedging pressure, put/call skew dynamics, GEX gradient, charm/vanna flows"

    # Thresholds
    MAX_DATA_AGE = 1800  # 30 min staleness limit
    NEGATIVE_GEX_THRESHOLD = -500_000_000   # -$500M net GEX = short gamma
    EXTREME_PCR_HIGH = 1.4    # extreme fear
    EXTREME_PCR_LOW = 0.65    # extreme complacency
    PIN_DISTANCE_PCT = 0.002  # 0.2% from strike = pinning zone

    def required_data(self) -> list[str]:
        return ["price", "prices", "options_data"]

    # ------------------------------------------------------------------
    # Dealer hedging pressure model
    # ------------------------------------------------------------------

    def _dealer_hedging_pressure(self, spy_price: float, options: dict,
                                   price_history: list[float]) -> tuple[float, float, str]:
        """Model dealer delta-hedging flow.

        When dealers are short gamma, they must buy as price rises and sell as
        price falls (pro-cyclical hedging) → amplifies moves.
        When dealers are long gamma, they sell rips and buy dips → dampens moves.

        Returns (pressure_score [-1,1], confidence, description).
        """
        net_gex = float(options.get('net_gex', 0))
        dealer_pos = options.get('dealer_positioning', '')
        flip_price = options.get('flip_price')

        if not price_history or len(price_history) < 3:
            return 0.0, 0.0, "insufficient price history"

        # Recent price direction
        recent_move = price_history[-1] - price_history[-3] if len(price_history) >= 3 else 0
        moving_up = recent_move > 0.5
        moving_down = recent_move < -0.5

        # Short gamma regime: hedging amplifies the current direction
        if dealer_pos == 'short_gamma' or net_gex < self.NEGATIVE_GEX_THRESHOLD:
            if moving_up:
                return 0.35, 0.6, f"Short gamma + rally — dealer buy-hedging amplifies (+GEX={net_gex/1e9:.1f}B)"
            elif moving_down:
                return -0.40, 0.65, f"Short gamma + selloff — dealer sell-hedging amplifies (GEX={net_gex/1e9:.1f}B)"
            else:
                return 0.0, 0.3, f"Short gamma, no directional move yet (GEX={net_gex/1e9:.1f}B)"

        # Long gamma: hedging dampens — lean against the move
        if dealer_pos == 'long_gamma' or net_gex > 1_000_000_000:
            if moving_up:
                return -0.20, 0.5, f"Long gamma + rally — dealer selling rips (GEX=+{net_gex/1e9:.1f}B)"
            elif moving_down:
                return 0.20, 0.5, f"Long gamma + dip — dealer buying dips (GEX=+{net_gex/1e9:.1f}B)"
            else:
                return 0.0, 0.3, f"Long gamma, pinning (GEX=+{net_gex/1e9:.1f}B)"

        return 0.0, 0.0, "neutral gamma"

    # ------------------------------------------------------------------
    # Put/call skew dynamics
    # ------------------------------------------------------------------

    def _pcr_dynamics(self, pcr: float, price_history: list[float]) -> tuple[float, str]:
        """Analyze put/call ratio for contrarian and momentum signals.

        Extreme PCR readings are contrarian. But PCR *trending* with price
        can be a confirmation signal.

        Returns (score [-1,1], description).
        """
        if pcr <= 0:
            return 0.0, ""

        score = 0.0
        desc = ""

        if pcr >= self.EXTREME_PCR_HIGH:
            # Extreme fear — contrarian bullish
            intensity = min(1.0, (pcr - self.EXTREME_PCR_HIGH) / 0.5)
            score = 0.30 + 0.20 * intensity
            desc = f"PCR={pcr:.2f} extreme fear — contrarian bullish"

        elif pcr <= self.EXTREME_PCR_LOW:
            # Extreme complacency — contrarian bearish
            intensity = min(1.0, (self.EXTREME_PCR_LOW - pcr) / 0.3)
            score = -(0.30 + 0.20 * intensity)
            desc = f"PCR={pcr:.2f} extreme complacency — contrarian bearish"

        elif pcr > 1.1:
            score = 0.15
            desc = f"PCR={pcr:.2f} elevated (mild contrarian bullish)"
        elif pcr < 0.8:
            score = -0.15
            desc = f"PCR={pcr:.2f} low (mild contrarian bearish)"

        return score, desc

    # ------------------------------------------------------------------
    # Strike pinning detection
    # ------------------------------------------------------------------

    def _strike_pinning(self, spy_price: float, options: dict) -> tuple[float, str]:
        """Detect if price is being pinned to a major options strike.

        When net GEX is very positive and price is near a round strike, dealers'
        hedging creates a "gravitational" pull that pins price to that level.

        Returns (score [-1,1], description).
        """
        net_gex = float(options.get('net_gex', 0))
        max_pain = options.get('max_pain')

        if net_gex < 500_000_000:
            return 0.0, ""  # only in positive gamma regimes

        # Check proximity to round SPY strikes ($1 increments)
        nearest_strike = round(spy_price)
        dist_to_strike = abs(spy_price - nearest_strike)
        dist_pct = dist_to_strike / spy_price

        if dist_pct < self.PIN_DISTANCE_PCT:
            # Very close to strike — expect pinning (mean reversion)
            direction = -1.0 if spy_price > nearest_strike else 1.0
            score = direction * 0.20
            return score, f"Strike pinning @ {nearest_strike} (dist={dist_to_strike:.2f}, GEX=+{net_gex/1e9:.1f}B)"

        # Check max pain proximity
        if max_pain is not None and max_pain > 0:
            mp_dist = spy_price - max_pain
            mp_pct = abs(mp_dist) / spy_price
            if mp_pct < 0.005:  # within 0.5%
                direction = -1.0 if mp_dist > 0 else 1.0
                score = direction * 0.15
                return score, f"Near max pain {max_pain:.1f} (dist={mp_dist:+.1f})"

        return 0.0, ""

    # ------------------------------------------------------------------
    # Charm flow (theta-driven delta changes)
    # ------------------------------------------------------------------

    def _charm_flow(self, spy_price: float, options: dict) -> tuple[float, str]:
        """Estimate charm (time decay) impact on dealer hedging.

        As options decay toward expiration, their delta changes. For calls that
        are OTM, delta decreases (dealers must sell to re-hedge). For puts that
        are OTM, negative delta decreases in magnitude (dealers must buy).

        This creates predictable flows, especially into the close.
        """
        call_wall = options.get('call_wall')
        put_wall = options.get('put_wall')

        if call_wall is None or put_wall is None:
            return 0.0, ""

        # Price position relative to the call/put walls
        if call_wall <= 0 or put_wall <= 0:
            return 0.0, ""

        call_dist = call_wall - spy_price
        put_dist = spy_price - put_wall
        total_dist = call_dist + put_dist

        if total_dist <= 0:
            return 0.0, ""

        # Charm pull: closer to call wall = more call theta decay = more dealer selling
        # Closer to put wall = more put theta decay = more dealer buying
        call_proximity = 1.0 - (call_dist / total_dist)  # 0 = at put wall, 1 = at call wall
        put_proximity = 1.0 - call_proximity

        # Net charm flow: positive = toward call wall (bullish), negative = toward put wall
        charm_bias = put_proximity - call_proximity
        if abs(charm_bias) < 0.2:
            return 0.0, ""

        score = charm_bias * 0.20
        desc = f"Charm flow {'bullish' if charm_bias > 0 else 'bearish'} (bias={charm_bias:.2f})"
        return score, desc

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        options = market_data.get("options_data", {})
        price = market_data.get("price", prices[-1] if prices else 0.0)

        if not price or not options:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no options data"},
            )

        # Data freshness check
        fetched_at = options.get('_fetched_at', 0.0)
        data_age = time.time() - fetched_at if fetched_at > 0 else options.get('age_sec', self.MAX_DATA_AGE)
        if data_age > self.MAX_DATA_AGE:
            age_factor = 0.3
        else:
            age_factor = max(0.5, 1.0 - data_age / self.MAX_DATA_AGE * 0.5)

        spy_price = price / 10.0  # MES ≈ 10x SPY
        price_history = [p / 10.0 for p in prices[-20:]] if prices else []
        pcr = float(options.get('put_call_ratio', 1.0))

        signals: list[tuple[str, float, float]] = []
        notes: list[str] = []

        # 1. Dealer hedging pressure
        hedge_score, hedge_conf, hedge_desc = self._dealer_hedging_pressure(
            spy_price, options, price_history
        )
        if abs(hedge_score) > 0.05:
            signals.append(("dealer_hedging", hedge_score, 0.30))
            notes.append(hedge_desc)

        # 2. PCR dynamics
        pcr_score, pcr_desc = self._pcr_dynamics(pcr, price_history)
        if abs(pcr_score) > 0.05:
            signals.append(("pcr_dynamics", pcr_score, 0.20))
            notes.append(pcr_desc)

        # 3. Strike pinning
        pin_score, pin_desc = self._strike_pinning(spy_price, options)
        if abs(pin_score) > 0.05:
            signals.append(("strike_pinning", pin_score, 0.20))
            notes.append(pin_desc)

        # 4. Charm flow
        charm_score, charm_desc = self._charm_flow(spy_price, options)
        if abs(charm_score) > 0.05:
            signals.append(("charm_flow", charm_score, 0.15))
            notes.append(charm_desc)

        # --- Aggregate ---
        if not signals:
            return StrategyResult(
                name=self.name, score=0.0, confidence=0.0, direction="FLAT",
                meta={"reason": "no actionable options flow signals",
                       "pcr": pcr, "data_age": round(data_age)},
            )

        total_weight = sum(w for _, _, w in signals)
        score = sum(s * w for _, s, w in signals) / total_weight if total_weight > 0 else 0.0
        score = max(-1.0, min(1.0, score))
        score *= age_factor  # Degrade with stale data

        agreeing = sum(1 for _, s, _ in signals if s * score > 0)
        confidence = min(1.0, (0.15 + 0.15 * agreeing + 0.30 * abs(score)) * age_factor)

        if abs(score) < 0.10:
            direction = "FLAT"
        elif score > 0:
            direction = "LONG"
        else:
            direction = "SHORT"

        return StrategyResult(
            name=self.name,
            score=round(score, 4),
            confidence=round(confidence, 4),
            direction=direction,
            entry_price=price if direction != "FLAT" else None,
            meta={
                "pcr": pcr,
                "net_gex": options.get('net_gex'),
                "dealer_positioning": options.get('dealer_positioning'),
                "gamma_regime": options.get('dealer_positioning', 'unknown'),
                "data_age_sec": round(data_age),
                "age_factor": round(age_factor, 3),
                "signal_breakdown": [
                    {"name": n, "score": round(s, 3), "weight": round(w, 3)}
                    for n, s, w in signals
                ],
                "notes": notes,
            },
        )
