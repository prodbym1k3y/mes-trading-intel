"""Phase 2 Quantitative Strategies for MES Intel System.

Contains ten microstructure and quantitative strategies:
  1. TWAPDeviationStrategy
  2. MicrostructureAlpha
  3. TickMomentumStrategy
  4. CumulativeDeltaDivergence
  5. LiquiditySweepStrategy
  6. OpeningRangeBreakout
  7. VWAPBandsMeanReversion
  8. MarketInternalsComposite
  9. AuctionMarketTheory
 10. IcebergDetectionStrategy
"""
from __future__ import annotations

import math
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from .base import Strategy, StrategyResult


# ======================================================================
# 1. TWAP Deviation Strategy
# ======================================================================

class TWAPDeviationStrategy(Strategy):
    """Detects when price deviates from a TWAP execution pattern.

    Institutional TWAP orders slice large orders evenly across time.
    When price has been tracking a TWAP trajectory and then deviates,
    it suggests the institutional flow is ending and a reversion or
    continuation inflection is likely.
    """
    name = "twap_deviation"
    description = "Detect TWAP execution patterns and deviation signals"

    def __init__(self, window: int = 30, deviation_sigma: float = 1.5,
                 min_r2: float = 0.85):
        self.window = window
        self.deviation_sigma = deviation_sigma
        self.min_r2 = min_r2

    def required_data(self) -> list[str]:
        return ["prices", "volumes"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        volumes = market_data.get("volumes", [])

        if len(prices) < self.window + 5:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "insufficient data"})

        window_prices = np.array(prices[-self.window:], dtype=float)
        window_volumes = np.array(volumes[-self.window:], dtype=float) if len(volumes) >= self.window else None
        x = np.arange(len(window_prices))

        # Fit linear TWAP trajectory over the window
        coeffs = np.polyfit(x, window_prices, 1)
        fitted = np.polyval(coeffs, x)
        residuals = window_prices - fitted
        residual_std = float(np.std(residuals)) + 1e-9

        # R-squared of linear fit (how TWAP-like the execution was)
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((window_prices - np.mean(window_prices)) ** 2)) + 1e-9
        r2 = 1.0 - ss_res / ss_tot

        # Check most recent bars for deviation from TWAP line
        tail = 5
        tail_residuals = residuals[-tail:]
        avg_tail_deviation = float(np.mean(tail_residuals))
        deviation_z = avg_tail_deviation / residual_std

        # Volume acceleration in tail vs body (institutions ending => volume drops)
        vol_signal = 0.0
        if window_volumes is not None and len(window_volumes) >= self.window:
            body_avg_vol = float(np.mean(window_volumes[:-tail]))
            tail_avg_vol = float(np.mean(window_volumes[-tail:]))
            if body_avg_vol > 0:
                vol_signal = (tail_avg_vol - body_avg_vol) / body_avg_vol

        if r2 < self.min_r2:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT",
                                  meta={"reason": f"no TWAP pattern (R2={r2:.2f})", "r2": r2})

        # TWAP detected; now check deviation
        if abs(deviation_z) < self.deviation_sigma:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT",
                                  meta={"reason": "TWAP in progress, no deviation yet",
                                        "r2": r2, "deviation_z": deviation_z})

        # Deviation found: signal reversal of the TWAP direction
        twap_direction = float(coeffs[0])  # slope
        # Price deviated above TWAP with volume declining => TWAP buy ending => short
        # Price deviated below TWAP with volume declining => TWAP sell ending => long
        if deviation_z > 0 and vol_signal < -0.15:
            score = -0.5 * min(abs(deviation_z) / 3.0, 1.0)
        elif deviation_z < 0 and vol_signal < -0.15:
            score = 0.5 * min(abs(deviation_z) / 3.0, 1.0)
        else:
            # Deviation with volume => breakout continuation
            score = float(np.sign(deviation_z)) * 0.4 * min(abs(deviation_z) / 3.0, 1.0)

        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(min(r2 * min(abs(deviation_z) / 2.0, 1.0), 1.0))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=float(prices[-1]),
            meta={"r2": r2, "deviation_z": deviation_z, "twap_slope": twap_direction,
                  "vol_change": vol_signal},
        )


# ======================================================================
# 2. Microstructure Alpha — Bid/Ask Spread Analysis
# ======================================================================

class MicrostructureAlpha(Strategy):
    """Bid/ask spread widening and narrowing as a directional signal.

    Spread widening signals uncertainty and potential reversal.
    Spread narrowing with directional volume signals conviction.
    Persistent spread asymmetry (more width on one side) signals
    informed flow.
    """
    name = "microstructure_alpha"
    description = "Bid/ask spread dynamics and market maker behavior"

    def __init__(self, spread_lookback: int = 50, z_threshold: float = 1.5):
        self.spread_lookback = spread_lookback
        self.z_threshold = z_threshold

    def required_data(self) -> list[str]:
        return ["bid_prices", "ask_prices", "prices", "volumes"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        bids = market_data.get("bid_prices", [])
        asks = market_data.get("ask_prices", [])
        prices = market_data.get("prices", [])
        volumes = market_data.get("volumes", [])

        min_len = min(len(bids), len(asks))
        if min_len < self.spread_lookback:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "insufficient BBO data"})

        bids_arr = np.array(bids[-self.spread_lookback:], dtype=float)
        asks_arr = np.array(asks[-self.spread_lookback:], dtype=float)
        spreads = asks_arr - bids_arr

        mean_spread = float(np.mean(spreads))
        std_spread = float(np.std(spreads)) + 1e-9

        # Recent spread (last 5 ticks) vs historical
        recent_spread = float(np.mean(spreads[-5:]))
        spread_z = (recent_spread - mean_spread) / std_spread

        # Spread change momentum
        spread_delta = float(np.mean(spreads[-5:]) - np.mean(spreads[-15:-5])) if len(spreads) >= 15 else 0.0

        # Mid-price momentum for context
        mids = (bids_arr + asks_arr) / 2.0
        mid_change = float(mids[-1] - mids[-10]) if len(mids) >= 10 else 0.0

        # Weighted mid vs actual mid (asymmetry)
        # If bid size > ask size, weighted mid is higher => buying pressure
        bid_sizes = market_data.get("bid_sizes", [])
        ask_sizes = market_data.get("ask_sizes", [])
        microprice_signal = 0.0
        if bid_sizes and ask_sizes and len(bid_sizes) >= 5 and len(ask_sizes) >= 5:
            recent_bid_sz = np.array(bid_sizes[-5:], dtype=float)
            recent_ask_sz = np.array(ask_sizes[-5:], dtype=float)
            total = recent_bid_sz + recent_ask_sz + 1e-9
            microprice_weights = recent_bid_sz / total  # high = more bid size = bullish
            microprice_signal = float(np.mean(microprice_weights) - 0.5) * 2.0  # -1 to +1

        score = 0.0
        reasons = []

        # Spread widening = uncertainty / potential reversal
        if spread_z > self.z_threshold:
            # Wide spread — fade the move
            score -= np.sign(mid_change) * 0.3 * min(spread_z / 3.0, 1.0)
            reasons.append(f"spread widening z={spread_z:.2f}")

        # Spread narrowing with trend = conviction
        if spread_z < -self.z_threshold and abs(mid_change) > 0.5:
            score += np.sign(mid_change) * 0.4
            reasons.append(f"spread narrowing with momentum z={spread_z:.2f}")

        # Microprice signal
        if abs(microprice_signal) > 0.2:
            score += microprice_signal * 0.3
            reasons.append(f"microprice bias={microprice_signal:.2f}")

        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(min(0.3 + abs(spread_z) * 0.1 + abs(microprice_signal) * 0.2, 1.0))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=float(prices[-1]) if prices else None,
            meta={"spread_z": spread_z, "microprice_signal": microprice_signal,
                  "mean_spread": mean_spread, "reasons": reasons},
        )


# ======================================================================
# 3. Tick Momentum Strategy
# ======================================================================

class TickMomentumStrategy(Strategy):
    """Tick chart momentum using uptick/downtick ratio.

    Tracks the ratio of price upticks to downticks over a lookback
    window. Extreme readings indicate directional momentum; ratio
    divergence from price warns of exhaustion.
    """
    name = "tick_momentum"
    description = "Uptick/downtick ratio momentum and divergence"

    def __init__(self, lookback: int = 50, extreme_ratio: float = 0.7,
                 divergence_lookback: int = 20):
        self.lookback = lookback
        self.extreme_ratio = extreme_ratio
        self.divergence_lookback = divergence_lookback

    def required_data(self) -> list[str]:
        return ["prices"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        if len(prices) < self.lookback + 1:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "insufficient tick data"})

        price_arr = np.array(prices[-(self.lookback + 1):], dtype=float)
        diffs = np.diff(price_arr)

        upticks = int(np.sum(diffs > 0))
        downticks = int(np.sum(diffs < 0))
        total_ticks = upticks + downticks
        if total_ticks == 0:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "no price movement"})

        uptick_ratio = upticks / total_ticks
        # Normalize to -1..+1: 0.5 = neutral, 1.0 = all upticks, 0.0 = all downticks
        ratio_score = (uptick_ratio - 0.5) * 2.0

        # Tick intensity (average absolute move size)
        avg_tick_size = float(np.mean(np.abs(diffs[diffs != 0]))) if np.any(diffs != 0) else 0.0

        # Check for divergence: ratio vs price direction over recent window
        divergence = 0.0
        if len(prices) > self.divergence_lookback + 1:
            recent_prices = np.array(prices[-self.divergence_lookback:], dtype=float)
            recent_diffs = np.diff(recent_prices)
            recent_upticks = int(np.sum(recent_diffs > 0))
            recent_total = int(np.sum(recent_diffs != 0))
            if recent_total > 0:
                recent_ratio = recent_upticks / recent_total
                recent_ratio_score = (recent_ratio - 0.5) * 2.0
                price_change = float(recent_prices[-1] - recent_prices[0])
                price_dir = np.sign(price_change) if abs(price_change) > 0.25 else 0.0
                # Divergence: price going one way, tick ratio going the other
                if price_dir != 0 and np.sign(recent_ratio_score) != price_dir:
                    divergence = -price_dir * abs(recent_ratio_score) * 0.5

        # Momentum signal
        score = ratio_score * 0.5
        # Add divergence (fades the move)
        score += divergence
        # Extreme readings boost confidence
        is_extreme = abs(uptick_ratio - 0.5) > (self.extreme_ratio - 0.5)

        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(min(0.3 + abs(ratio_score) * 0.3 + (0.2 if is_extreme else 0.0), 1.0))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=float(prices[-1]),
            meta={"uptick_ratio": uptick_ratio, "ratio_score": ratio_score,
                  "divergence": divergence, "avg_tick_size": avg_tick_size,
                  "is_extreme": is_extreme},
        )


# ======================================================================
# 4. Cumulative Delta Divergence (Advanced)
# ======================================================================

class CumulativeDeltaDivergence(Strategy):
    """Advanced cumulative delta divergence with multi-timeframe analysis.

    Goes beyond simple delta/price divergence by incorporating:
    - Rate of change of delta vs rate of change of price
    - Delta acceleration (2nd derivative)
    - Volume-weighted delta normalization
    - Swing-based divergence (pivot highs/lows)
    """
    name = "cum_delta_divergence"
    description = "Multi-timeframe cumulative delta divergence with acceleration"

    def __init__(self, short_window: int = 10, long_window: int = 30,
                 divergence_threshold: float = 0.3):
        self.short_window = short_window
        self.long_window = long_window
        self.divergence_threshold = divergence_threshold

    def required_data(self) -> list[str]:
        return ["prices", "volumes", "cumulative_delta", "recent_deltas", "footprint_bars"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        recent_deltas = market_data.get("recent_deltas", [])
        bars = market_data.get("footprint_bars", [])

        if len(prices) < self.long_window or len(recent_deltas) < self.long_window:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "insufficient data"})

        price_arr = np.array(prices[-self.long_window:], dtype=float)
        delta_arr = np.array(recent_deltas[-self.long_window:], dtype=float)
        cum_delta = np.cumsum(delta_arr)

        # Normalize both to z-scores for comparison
        def z_normalize(arr):
            std = float(np.std(arr))
            if std < 1e-9:
                return arr - np.mean(arr)
            return (arr - np.mean(arr)) / std

        price_z = z_normalize(price_arr)
        delta_z = z_normalize(cum_delta)

        # 1. Rate of change divergence (short window)
        short_price_roc = float(price_arr[-1] - price_arr[-self.short_window])
        short_delta_roc = float(cum_delta[-1] - cum_delta[-self.short_window])
        price_std = float(np.std(price_arr)) + 1e-9
        delta_std = float(np.std(cum_delta)) + 1e-9
        price_roc_z = short_price_roc / price_std
        delta_roc_z = short_delta_roc / delta_std

        roc_divergence = 0.0
        if abs(price_roc_z) > 0.5 and np.sign(price_roc_z) != np.sign(delta_roc_z):
            roc_divergence = -np.sign(price_roc_z) * min(abs(price_roc_z - delta_roc_z) / 3.0, 1.0)

        # 2. Delta acceleration (2nd derivative)
        if len(delta_arr) >= 5:
            delta_vel = np.diff(cum_delta)
            delta_accel = np.diff(delta_vel)
            recent_accel = float(np.mean(delta_accel[-3:]))
            accel_norm = recent_accel / (float(np.std(delta_accel)) + 1e-9)
        else:
            accel_norm = 0.0

        # 3. Swing-based divergence: compare last two price highs/lows with delta
        swing_div = 0.0
        if len(price_arr) >= 20:
            half = len(price_arr) // 2
            first_half = price_arr[:half]
            second_half = price_arr[half:]
            first_delta = cum_delta[:half]
            second_delta = cum_delta[half:]

            # Higher price high with lower delta high = bearish divergence
            if np.max(second_half) > np.max(first_half) and np.max(second_delta) < np.max(first_delta):
                swing_div = -0.5
            # Lower price low with higher delta low = bullish divergence
            elif np.min(second_half) < np.min(first_half) and np.min(second_delta) > np.min(first_delta):
                swing_div = 0.5

        # 4. Volume-weighted delta trend
        vol_weighted = 0.0
        if bars and len(bars) >= self.short_window:
            recent_bars = bars[-self.short_window:]
            total_vol = sum(b.volume for b in recent_bars) + 1
            vol_weighted = sum(b.delta * b.volume for b in recent_bars) / total_vol
            vol_weighted = float(np.clip(vol_weighted / 500.0, -1.0, 1.0))

        # Combine signals
        score = (roc_divergence * 0.35 +
                 swing_div * 0.30 +
                 float(np.clip(accel_norm * 0.1, -0.2, 0.2)) +
                 vol_weighted * 0.15)
        score = float(np.clip(score, -1.0, 1.0))

        # Confidence from agreement
        signals = [roc_divergence, swing_div, accel_norm * 0.1, vol_weighted]
        agreement = sum(1 for s in signals if np.sign(s) == np.sign(score) and abs(s) > 0.05)
        confidence = float(min(0.2 + agreement * 0.15 + abs(score) * 0.3, 1.0))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=float(prices[-1]),
            meta={"roc_divergence": roc_divergence, "swing_divergence": swing_div,
                  "delta_accel": accel_norm, "vol_weighted_delta": vol_weighted},
        )


# ======================================================================
# 5. Liquidity Sweep Strategy
# ======================================================================

class LiquiditySweepStrategy(Strategy):
    """Detects liquidity sweeps (stop hunts).

    Identifies when price spikes through a recent high/low (where stops
    cluster), then reverses, suggesting a sweep of liquidity. Classic
    smart money concept: run the stops, then reverse.
    """
    name = "liquidity_sweep"
    description = "Stop hunt / liquidity sweep detection with reversal signal"

    def __init__(self, swing_lookback: int = 20, sweep_threshold_ticks: int = 4,
                 reversal_bars: int = 3, min_wick_ratio: float = 0.6):
        self.swing_lookback = swing_lookback
        self.sweep_threshold = sweep_threshold_ticks * 0.25  # MES tick = 0.25
        self.reversal_bars = reversal_bars
        self.min_wick_ratio = min_wick_ratio

    def required_data(self) -> list[str]:
        return ["prices", "highs", "lows", "volumes", "footprint_bars"]

    def _find_swing_highs_lows(self, highs, lows, lookback):
        """Find significant swing highs and lows."""
        swing_highs = []
        swing_lows = []
        for i in range(lookback, len(highs) - 1):
            window_h = highs[i - lookback:i]
            if highs[i] >= max(window_h):
                swing_highs.append((i, highs[i]))
            window_l = lows[i - lookback:i]
            if lows[i] <= min(window_l):
                swing_lows.append((i, lows[i]))
        return swing_highs, swing_lows

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        highs = market_data.get("highs", [])
        lows = market_data.get("lows", [])
        bars = market_data.get("footprint_bars", [])

        if len(prices) < self.swing_lookback + 5 or len(highs) < self.swing_lookback + 5:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "insufficient data"})

        highs_arr = list(map(float, highs))
        lows_arr = list(map(float, lows))
        current_price = float(prices[-1])

        swing_highs, swing_lows = self._find_swing_highs_lows(
            highs_arr, lows_arr, min(self.swing_lookback, len(highs_arr) // 3)
        )

        sweep_signal = 0.0
        sweep_details = []

        # Check for high sweep (price spiked above swing high then reversed)
        if len(bars) >= self.reversal_bars + 1:
            recent_bars = bars[-(self.reversal_bars + 1):]
            spike_bar = recent_bars[0]
            reversal_bars = recent_bars[1:]

            for _, sh_price in swing_highs[-5:]:  # check recent swing highs
                if spike_bar.high is not None and spike_bar.close is not None:
                    # Spike above swing high
                    if spike_bar.high > sh_price + self.sweep_threshold:
                        # But closed back below (wick)
                        bar_range = spike_bar.high - spike_bar.low if spike_bar.low else 0
                        if bar_range > 0:
                            upper_wick = spike_bar.high - max(spike_bar.open or spike_bar.close, spike_bar.close)
                            wick_ratio = upper_wick / bar_range
                            # Reversal confirmation
                            reversed_down = all(
                                b.close is not None and b.close < sh_price
                                for b in reversal_bars if b.close is not None
                            )
                            if wick_ratio >= self.min_wick_ratio and reversed_down:
                                sweep_signal = -0.7  # bearish sweep => short after reversal
                                sweep_details.append(f"high_sweep at {sh_price:.2f}")

            for _, sl_price in swing_lows[-5:]:  # check recent swing lows
                if spike_bar.low is not None and spike_bar.close is not None:
                    if spike_bar.low < sl_price - self.sweep_threshold:
                        bar_range = spike_bar.high - spike_bar.low if spike_bar.high else 0
                        if bar_range > 0:
                            lower_wick = min(spike_bar.open or spike_bar.close, spike_bar.close) - spike_bar.low
                            wick_ratio = lower_wick / bar_range
                            reversed_up = all(
                                b.close is not None and b.close > sl_price
                                for b in reversal_bars if b.close is not None
                            )
                            if wick_ratio >= self.min_wick_ratio and reversed_up:
                                sweep_signal = 0.7  # bullish sweep => long after reversal
                                sweep_details.append(f"low_sweep at {sl_price:.2f}")

        # Volume confirmation: sweep bar should have elevated volume
        vol_confirm = 1.0
        if bars and len(bars) >= 10:
            avg_vol = float(np.mean([b.volume for b in bars[-10:]]))
            sweep_bar_vol = bars[-(self.reversal_bars + 1)].volume if len(bars) > self.reversal_bars else 0
            if avg_vol > 0 and sweep_bar_vol > avg_vol * 1.5:
                vol_confirm = 1.2
            elif avg_vol > 0 and sweep_bar_vol < avg_vol * 0.5:
                vol_confirm = 0.5

        score = float(np.clip(sweep_signal * vol_confirm, -1.0, 1.0))
        confidence = float(min(abs(score) * 0.8 + (0.1 if vol_confirm > 1.0 else 0.0), 1.0)) if score != 0 else 0.0

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=float(prices[-1]) if direction != "FLAT" else None,
            meta={"sweep_details": sweep_details, "vol_confirm": vol_confirm,
                  "swing_highs": [s[1] for s in swing_highs[-3:]],
                  "swing_lows": [s[1] for s in swing_lows[-3:]]},
        )


# ======================================================================
# 6. Opening Range Breakout
# ======================================================================

class OpeningRangeBreakout(Strategy):
    """Opening Range Breakout (ORB) strategy.

    Defines the opening range as the high/low of the first N minutes
    of the session. Signals on breakout with volume confirmation.
    Filters: range must be meaningful, breakout must have volume,
    failed breakouts are counter-traded.
    """
    name = "orb"
    description = "Opening range breakout with volume confirmation"

    def __init__(self, min_range_pts: float = 2.0, max_range_pts: float = 15.0,
                 volume_multiplier: float = 1.3):
        self.min_range_pts = min_range_pts
        self.max_range_pts = max_range_pts
        self.volume_multiplier = volume_multiplier

    def required_data(self) -> list[str]:
        return ["prices", "volumes", "opening_range_high", "opening_range_low"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        volumes = market_data.get("volumes", [])
        or_high = market_data.get("opening_range_high")
        or_low = market_data.get("opening_range_low")

        if not prices or or_high is None or or_low is None:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "no OR data"})

        or_high = float(or_high)
        or_low = float(or_low)
        or_range = or_high - or_low
        current = float(prices[-1])

        if or_range < self.min_range_pts:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": f"OR too tight ({or_range:.1f}pts)"})
        if or_range > self.max_range_pts:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": f"OR too wide ({or_range:.1f}pts)"})

        # Volume confirmation
        avg_vol = float(np.mean(volumes[-30:])) if len(volumes) >= 30 else 0
        recent_vol = float(np.mean(volumes[-5:])) if len(volumes) >= 5 else 0
        vol_confirmed = avg_vol > 0 and recent_vol > avg_vol * self.volume_multiplier

        # Check breakout state
        above_or = current > or_high
        below_or = current < or_low
        inside_or = not above_or and not below_or

        # Track if we had a breakout that failed (price went through then came back)
        recent_high = max(map(float, prices[-10:])) if len(prices) >= 10 else current
        recent_low = min(map(float, prices[-10:])) if len(prices) >= 10 else current
        failed_high_break = recent_high > or_high and current < or_high
        failed_low_break = recent_low < or_low and current > or_low

        score = 0.0
        reasons = []

        if above_or and vol_confirmed:
            extension = (current - or_high) / or_range
            score = min(0.3 + extension * 0.3, 0.8)
            reasons.append(f"ORB high breakout +{extension:.1f}R, vol confirmed")
        elif above_or and not vol_confirmed:
            score = 0.15
            reasons.append("ORB high breakout, weak volume")
        elif below_or and vol_confirmed:
            extension = (or_low - current) / or_range
            score = max(-0.3 - extension * 0.3, -0.8)
            reasons.append(f"ORB low breakout +{extension:.1f}R, vol confirmed")
        elif below_or and not vol_confirmed:
            score = -0.15
            reasons.append("ORB low breakout, weak volume")
        elif failed_high_break:
            score = -0.4
            reasons.append("Failed ORB high breakout — fade")
        elif failed_low_break:
            score = 0.4
            reasons.append("Failed ORB low breakout — fade")
        elif inside_or:
            reasons.append("inside opening range, no signal")

        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(min(abs(score) * 0.7 + (0.2 if vol_confirmed else 0.0), 1.0))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        entry = current if direction != "FLAT" else None
        stop = or_low - 1.0 if direction == "LONG" else (or_high + 1.0 if direction == "SHORT" else None)
        target = (or_high + or_range if direction == "LONG" else
                  or_low - or_range if direction == "SHORT" else None)

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=entry, stop_price=stop, target_price=target,
            meta={"or_high": or_high, "or_low": or_low, "or_range": or_range,
                  "vol_confirmed": vol_confirmed, "reasons": reasons},
        )


# ======================================================================
# 7. VWAP Bands Mean Reversion
# ======================================================================

class VWAPBandsMeanReversion(Strategy):
    """Mean reversion to VWAP using standard deviation bands.

    Computes 1-sigma and 2-sigma bands around VWAP. Signals when
    price extends to 2-sigma (fade back to VWAP) or bounces off
    1-sigma (trend continuation). Volume and delta confirm signals.
    """
    name = "vwap_bands"
    description = "VWAP standard deviation band mean reversion"

    def __init__(self, band_1_sigma: float = 1.0, band_2_sigma: float = 2.0,
                 lookback: int = 50):
        self.band_1_sigma = band_1_sigma
        self.band_2_sigma = band_2_sigma
        self.lookback = lookback

    def required_data(self) -> list[str]:
        return ["prices", "volumes", "vwap"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        volumes = market_data.get("volumes", [])
        vwap = market_data.get("vwap")

        if vwap is None or len(prices) < self.lookback:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "no VWAP or insufficient data"})

        vwap = float(vwap)
        price_arr = np.array(prices[-self.lookback:], dtype=float)
        current = float(prices[-1])

        # Compute VWAP deviation standard deviation
        deviations = price_arr - vwap
        std_dev = float(np.std(deviations)) + 1e-9

        # Current Z-score relative to VWAP
        z_score = (current - vwap) / std_dev

        # Band levels
        upper_1 = vwap + std_dev * self.band_1_sigma
        lower_1 = vwap - std_dev * self.band_1_sigma
        upper_2 = vwap + std_dev * self.band_2_sigma
        lower_2 = vwap - std_dev * self.band_2_sigma

        # Recent momentum for band touch classification
        recent_momentum = float(price_arr[-1] - price_arr[-5]) if len(price_arr) >= 5 else 0.0
        momentum_dir = np.sign(recent_momentum)

        # Volume trend (rising volume on extension = trend, falling = mean reversion setup)
        vol_trend = 0.0
        if len(volumes) >= 20:
            vol_arr = np.array(volumes[-20:], dtype=float)
            first_half = float(np.mean(vol_arr[:10]))
            second_half = float(np.mean(vol_arr[10:]))
            if first_half > 0:
                vol_trend = (second_half - first_half) / first_half

        score = 0.0
        reasons = []

        if z_score >= self.band_2_sigma:
            # At or beyond 2-sigma upper: strong mean reversion short
            score = -0.6 * min(z_score / 3.0, 1.0)
            if vol_trend < 0:  # declining volume confirms exhaustion
                score *= 1.3
                reasons.append("2σ+ upper band with declining volume")
            else:
                reasons.append("2σ+ upper band")
        elif z_score <= -self.band_2_sigma:
            # At or beyond 2-sigma lower: strong mean reversion long
            score = 0.6 * min(abs(z_score) / 3.0, 1.0)
            if vol_trend < 0:
                score *= 1.3
                reasons.append("2σ+ lower band with declining volume")
            else:
                reasons.append("2σ+ lower band")
        elif z_score >= self.band_1_sigma:
            # Between 1-sigma and 2-sigma upper
            if momentum_dir < 0:  # turning down from 1σ = mean reversion
                score = -0.3
                reasons.append("rejected at 1σ upper band")
            else:
                score = 0.15  # still trending up, mild continuation
                reasons.append("holding above 1σ upper, mild trend")
        elif z_score <= -self.band_1_sigma:
            if momentum_dir > 0:
                score = 0.3
                reasons.append("rejected at 1σ lower band")
            else:
                score = -0.15
                reasons.append("holding below 1σ lower, mild trend")
        else:
            # Inside 1σ bands — no strong signal
            reasons.append(f"inside 1σ bands (z={z_score:.2f})")

        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(min(0.2 + abs(z_score) * 0.15 + (0.15 if abs(vol_trend) > 0.2 else 0.0), 1.0))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        entry = current if direction != "FLAT" else None
        stop = (upper_2 + 1.0 if direction == "SHORT" else
                lower_2 - 1.0 if direction == "LONG" else None)
        target = vwap if direction != "FLAT" else None

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=entry, stop_price=stop, target_price=target,
            meta={"vwap": vwap, "z_score": z_score, "std_dev": std_dev,
                  "upper_1": upper_1, "lower_1": lower_1,
                  "upper_2": upper_2, "lower_2": lower_2,
                  "vol_trend": vol_trend, "reasons": reasons},
        )


# ======================================================================
# 8. Market Internals Composite
# ======================================================================

class MarketInternalsComposite(Strategy):
    """Composite of TICK, ADD, and VOLD market internals.

    TICK: NYSE tick index (upticking vs downticking stocks)
    ADD: Advance/decline difference
    VOLD: Up volume minus down volume

    Extreme readings, divergences, and trend in internals provide
    broad market context for MES/ES trading.
    """
    name = "market_internals"
    description = "TICK + ADD + VOLD composite market breadth signal"

    def __init__(self, tick_extreme: int = 800, add_extreme: int = 1500,
                 lookback: int = 20):
        self.tick_extreme = tick_extreme
        self.add_extreme = add_extreme
        self.lookback = lookback

    def required_data(self) -> list[str]:
        return ["tick_values", "add_values", "vold_values", "prices"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        tick_vals = market_data.get("tick_values", [])
        add_vals = market_data.get("add_values", [])
        vold_vals = market_data.get("vold_values", [])
        prices = market_data.get("prices", [])

        available = sum(1 for v in [tick_vals, add_vals, vold_vals] if len(v) >= 5)
        if available == 0:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "no internals data"})

        sub_scores = []
        details = {}

        # --- TICK analysis ---
        if len(tick_vals) >= 5:
            tick_arr = np.array(tick_vals[-self.lookback:], dtype=float) if len(tick_vals) >= self.lookback else np.array(tick_vals, dtype=float)
            current_tick = float(tick_arr[-1])
            avg_tick = float(np.mean(tick_arr))
            tick_trend = float(np.polyfit(range(len(tick_arr)), tick_arr, 1)[0]) if len(tick_arr) >= 5 else 0.0

            # Normalize current TICK
            tick_score = np.clip(current_tick / self.tick_extreme, -1.0, 1.0)
            # Trend adds conviction
            tick_score = float(tick_score * 0.7 + np.clip(tick_trend / 50.0, -0.3, 0.3))
            sub_scores.append(("TICK", tick_score))
            details["tick_current"] = current_tick
            details["tick_avg"] = avg_tick
            details["tick_trend"] = tick_trend

        # --- ADD analysis ---
        if len(add_vals) >= 5:
            add_arr = np.array(add_vals[-self.lookback:], dtype=float) if len(add_vals) >= self.lookback else np.array(add_vals, dtype=float)
            current_add = float(add_arr[-1])
            add_trend = float(np.polyfit(range(len(add_arr)), add_arr, 1)[0]) if len(add_arr) >= 5 else 0.0

            add_score = float(np.clip(current_add / self.add_extreme, -1.0, 1.0))
            add_score = float(add_score * 0.7 + np.clip(add_trend / 100.0, -0.3, 0.3))
            sub_scores.append(("ADD", add_score))
            details["add_current"] = current_add
            details["add_trend"] = add_trend

        # --- VOLD analysis ---
        if len(vold_vals) >= 5:
            vold_arr = np.array(vold_vals[-self.lookback:], dtype=float) if len(vold_vals) >= self.lookback else np.array(vold_vals, dtype=float)
            current_vold = float(vold_arr[-1])
            vold_std = float(np.std(vold_arr)) + 1e-9
            vold_z = (current_vold - float(np.mean(vold_arr))) / vold_std

            vold_score = float(np.clip(vold_z / 2.0, -1.0, 1.0))
            sub_scores.append(("VOLD", vold_score))
            details["vold_current"] = current_vold
            details["vold_z"] = vold_z

        # Combine sub-scores with equal weight
        total_score = float(np.mean([s[1] for s in sub_scores]))
        total_score = float(np.clip(total_score, -1.0, 1.0))

        # Agreement boosts confidence
        signs = [np.sign(s[1]) for s in sub_scores if abs(s[1]) > 0.1]
        if signs:
            dominant = np.sign(total_score)
            agreement = sum(1 for s in signs if s == dominant) / len(signs)
        else:
            agreement = 0.0

        confidence = float(min(0.2 + agreement * 0.3 + abs(total_score) * 0.3, 1.0))

        # Price divergence check: internals up but price down = bullish divergence
        if prices and len(prices) >= 10:
            price_change = float(prices[-1]) - float(prices[-10])
            if total_score > 0.3 and price_change < -1.0:
                details["divergence"] = "bullish (internals up, price down)"
                confidence = min(confidence + 0.1, 1.0)
            elif total_score < -0.3 and price_change > 1.0:
                details["divergence"] = "bearish (internals down, price up)"
                confidence = min(confidence + 0.1, 1.0)

        direction = "LONG" if total_score > 0.15 else ("SHORT" if total_score < -0.15 else "FLAT")

        details["sub_scores"] = [(name, float(sc)) for name, sc in sub_scores]

        return StrategyResult(
            name=self.name, score=total_score, confidence=confidence, direction=direction,
            entry_price=float(prices[-1]) if prices else None,
            meta=details,
        )


# ======================================================================
# 9. Auction Market Theory
# ======================================================================

class AuctionMarketTheory(Strategy):
    """Balance/imbalance detection using auction market theory.

    Tracks value area migration (rotating vs trending), POC stability,
    excess (tails/spikes), and initiative vs responsive activity to
    determine market state and direction.
    """
    name = "auction_market"
    description = "Value area migration, POC analysis, and balance/imbalance detection"

    def __init__(self, balance_threshold: float = 3.0, excess_ticks: int = 4):
        self.balance_threshold = balance_threshold
        self.excess_min = excess_ticks * 0.25  # in points

    def required_data(self) -> list[str]:
        return ["prices", "volume_profile", "footprint_bars",
                "prior_session_val", "prior_session_vah", "prior_session_poc"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        prices = market_data.get("prices", [])
        profile = market_data.get("volume_profile")
        bars = market_data.get("footprint_bars", [])

        if not prices or profile is None:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "missing profile data"})

        current = float(prices[-1])
        poc = profile.poc
        val, vah = profile.value_area()

        if poc is None or val is None or vah is None:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "empty volume profile"})

        va_range = vah - val
        signals = []
        details = {"poc": poc, "val": val, "vah": vah, "va_range": va_range}

        # --- 1. Value area position (responsive vs initiative) ---
        prior_val = market_data.get("prior_session_val")
        prior_vah = market_data.get("prior_session_vah")
        prior_poc = market_data.get("prior_session_poc")

        if prior_val is not None and prior_vah is not None:
            prior_val = float(prior_val)
            prior_vah = float(prior_vah)

            # Value area migration direction
            va_migration = ((val + vah) / 2.0) - ((prior_val + prior_vah) / 2.0)
            details["va_migration"] = va_migration

            if abs(va_migration) > self.balance_threshold:
                # Trending: value migrating
                migration_score = float(np.clip(va_migration / 10.0, -0.5, 0.5))
                signals.append(("va_migration", migration_score, 0.6))

            # Open drive: opening outside prior value area
            if current > prior_vah:
                signals.append(("above_prior_va", 0.3, 0.4))
            elif current < prior_val:
                signals.append(("below_prior_va", -0.3, 0.4))

        # --- 2. Current position relative to today's value area ---
        if current > vah:
            # Above value area: initiative buying if sustained, else responsive sell
            dist_above = (current - vah) / (va_range + 1e-9)
            if dist_above > 0.5:
                signals.append(("extended_above_va", 0.3, 0.3))  # trend continuation
            else:
                signals.append(("above_va_responsive", -0.2, 0.3))  # potential fade
        elif current < val:
            dist_below = (val - current) / (va_range + 1e-9)
            if dist_below > 0.5:
                signals.append(("extended_below_va", -0.3, 0.3))
            else:
                signals.append(("below_va_responsive", 0.2, 0.3))

        # --- 3. POC migration ---
        if prior_poc is not None:
            prior_poc = float(prior_poc)
            poc_shift = poc - prior_poc
            if abs(poc_shift) > self.balance_threshold:
                poc_signal = float(np.clip(poc_shift / 8.0, -0.4, 0.4))
                signals.append(("poc_migration", poc_signal, 0.5))
                details["poc_shift"] = poc_shift

        # --- 4. Excess detection (single prints / tails at extremes) ---
        if bars and len(bars) >= 5:
            # Check for single-print excess at session highs/lows
            sorted_levels = profile.sorted_levels()
            if sorted_levels:
                # Low volume at extremes = excess (market found price too high/low)
                poc_vol = max(lv.total_volume for lv in sorted_levels)
                top_levels = sorted_levels[-3:]
                bottom_levels = sorted_levels[:3]

                top_avg_vol = float(np.mean([lv.total_volume for lv in top_levels]))
                bottom_avg_vol = float(np.mean([lv.total_volume for lv in bottom_levels]))

                # Excess at top (thin volume = rejection) => bearish
                if poc_vol > 0 and top_avg_vol < poc_vol * 0.1:
                    signals.append(("excess_top", -0.3, 0.4))
                    details["excess_top"] = True
                # Excess at bottom => bullish
                if poc_vol > 0 and bottom_avg_vol < poc_vol * 0.1:
                    signals.append(("excess_bottom", 0.3, 0.4))
                    details["excess_bottom"] = True

        # --- 5. Balance vs imbalance (VA width) ---
        if va_range < self.balance_threshold * 2:
            details["market_state"] = "balanced (tight VA)"
        else:
            details["market_state"] = "imbalanced (wide VA)"

        # Combine
        if not signals:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "balanced, no signal", **details})

        total_weight = sum(s[2] for s in signals)
        score = sum(s[1] * s[2] for s in signals) / total_weight
        score = float(np.clip(score, -1.0, 1.0))

        dirs = [np.sign(s[1]) for s in signals if abs(s[1]) > 0.05]
        agreement = sum(1 for d in dirs if d == np.sign(score)) / max(len(dirs), 1) if dirs else 0.0
        confidence = float(min(agreement * 0.4 + abs(score) * 0.4, 1.0))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        details["signals"] = [(s[0], float(s[1]), float(s[2])) for s in signals]

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=current if direction != "FLAT" else None,
            meta=details,
        )


# ======================================================================
# 10. Iceberg Detection Strategy
# ======================================================================

class IcebergDetectionStrategy(Strategy):
    """Detects iceberg orders — large hidden orders that refill at the same price.

    Identifies repeated fills at the same price level across multiple
    bars/ticks, suggesting a hidden large order. The direction of the
    iceberg (bid vs ask side) reveals institutional intent.
    """
    name = "iceberg_detection"
    description = "Detect hidden iceberg orders via repeated same-price fills"

    def __init__(self, min_repeats: int = 3, volume_threshold_ratio: float = 2.0,
                 price_tolerance: float = 0.25, lookback_bars: int = 15):
        self.min_repeats = min_repeats
        self.volume_threshold_ratio = volume_threshold_ratio
        self.price_tolerance = price_tolerance
        self.lookback_bars = lookback_bars

    def required_data(self) -> list[str]:
        return ["prices", "footprint_bars", "volume_profile"]

    def evaluate(self, market_data: dict) -> StrategyResult:
        bars = market_data.get("footprint_bars", [])
        prices = market_data.get("prices", [])
        profile = market_data.get("volume_profile")

        if len(bars) < self.lookback_bars or not prices:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "insufficient bar data"})

        recent_bars = bars[-self.lookback_bars:]

        # Build a map of price -> list of (bar_index, volume, delta) across recent bars
        price_fills: dict[float, list[tuple[int, int, int]]] = {}
        for idx, bar in enumerate(recent_bars):
            for price_level in bar.profile.sorted_levels():
                rounded = round(price_level.price / self.price_tolerance) * self.price_tolerance
                if rounded not in price_fills:
                    price_fills[rounded] = []
                price_fills[rounded].append((
                    idx,
                    price_level.total_volume,
                    price_level.delta,
                ))

        # Compute average volume per level per bar for baseline
        all_level_vols = []
        for bar in recent_bars:
            for lv in bar.profile.sorted_levels():
                all_level_vols.append(lv.total_volume)
        avg_level_vol = float(np.mean(all_level_vols)) if all_level_vols else 1.0

        # Find iceberg candidates: same price with repeated high-volume fills
        icebergs = []
        for price, fills in price_fills.items():
            if len(fills) < self.min_repeats:
                continue

            # Check for consistently high volume at this price across bars
            fill_vols = [f[1] for f in fills]
            fill_deltas = [f[2] for f in fills]
            avg_fill_vol = float(np.mean(fill_vols))

            if avg_fill_vol < avg_level_vol * self.volume_threshold_ratio:
                continue

            # Consistency: standard deviation of fill volumes should be relatively low
            # (icebergs refill with similar size)
            vol_cv = float(np.std(fill_vols)) / (avg_fill_vol + 1e-9)
            if vol_cv > 1.0:
                continue  # too variable, probably not an iceberg

            # Determine iceberg side from net delta
            net_delta = sum(fill_deltas)
            side = "bid" if net_delta < 0 else "ask"
            strength = float(len(fills)) / self.lookback_bars  # persistence ratio

            icebergs.append({
                "price": price,
                "repeats": len(fills),
                "avg_volume": avg_fill_vol,
                "net_delta": net_delta,
                "side": side,
                "vol_cv": vol_cv,
                "strength": strength,
            })

        if not icebergs:
            return StrategyResult(name=self.name, score=0.0, confidence=0.0,
                                  direction="FLAT", meta={"reason": "no icebergs detected"})

        # Find the strongest iceberg
        strongest = max(icebergs, key=lambda x: x["strength"] * x["avg_volume"])
        current_price = float(prices[-1])

        # Signal: iceberg on bid side (aggressive selling absorbed by hidden bid) = bullish
        #         iceberg on ask side (aggressive buying absorbed by hidden ask) = bearish
        if strongest["side"] == "bid":
            # Hidden buyer: price held at this level despite selling => bullish
            score = 0.5 * strongest["strength"]
            if current_price <= strongest["price"] + self.price_tolerance:
                score += 0.2  # price still at iceberg level
        else:
            # Hidden seller: price held despite buying => bearish
            score = -0.5 * strongest["strength"]
            if current_price >= strongest["price"] - self.price_tolerance:
                score -= 0.2

        score = float(np.clip(score, -1.0, 1.0))
        confidence = float(min(
            0.3 + strongest["strength"] * 0.3 +
            min(strongest["avg_volume"] / (avg_level_vol * 5.0), 0.2) +
            (0.1 if strongest["vol_cv"] < 0.3 else 0.0),
            1.0
        ))

        direction = "LONG" if score > 0.15 else ("SHORT" if score < -0.15 else "FLAT")

        entry = current_price if direction != "FLAT" else None
        # Stop beyond the iceberg level, target away from it
        if direction == "LONG":
            stop = strongest["price"] - 2.0
            target = current_price + abs(current_price - strongest["price"]) + 4.0
        elif direction == "SHORT":
            stop = strongest["price"] + 2.0
            target = current_price - abs(current_price - strongest["price"]) - 4.0
        else:
            stop = target = None

        return StrategyResult(
            name=self.name, score=score, confidence=confidence, direction=direction,
            entry_price=entry, stop_price=stop, target_price=target,
            meta={"strongest_iceberg": strongest, "all_icebergs": icebergs,
                  "avg_level_vol": avg_level_vol},
        )
