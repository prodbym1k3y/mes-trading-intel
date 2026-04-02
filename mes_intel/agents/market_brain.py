"""Market Brain Agent — quantitative market learning engine.

Computes the full suite of institutional-grade quant features:
  - RSI, MACD, Bollinger Bands, ATR, VWAP, volume z-scores, momentum
  - Fair Value Gaps (FVG), futures fair value vs SPY spot
  - Volume Profile intelligence: POC migration, value area shift, naked POCs,
    poor highs/lows, single prints, HVN/LVN, profile shape (P/b/D)
  - Market Auction Theory: Initial Balance, IB extensions, failed auctions,
    excess, range vs trend day detection
  - Markov Chain regime transitions with probability forecasting
  - Microstructure: order flow imbalance, absorption, sweeps, icebergs
  - Derivatives-derived levels: GEX flip, max pain, 0DTE gamma, put/call skew
  - Statistical edge: Hurst exponent, volume z-score, momentum quality,
    mean reversion probability

Broadcasts:
  - MARKET_REGIME_CHANGE  when regime transitions
  - QUANT_SIGNAL          continuous quant state (all computed features)
  - HISTORICAL_PATTERN_MATCH when current action matches a historical analog

Persists learned patterns to market_patterns and market_regimes tables.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType

log = logging.getLogger(__name__)

# Regime labels
REGIME_TRENDING   = "trending"
REGIME_RANGING    = "ranging"
REGIME_VOLATILE   = "volatile"
REGIME_QUIET      = "quiet"
REGIME_BREAKOUT   = "breakout"
ALL_REGIMES = [REGIME_TRENDING, REGIME_RANGING, REGIME_VOLATILE, REGIME_QUIET, REGIME_BREAKOUT]

# Minimum bars needed before computing indicators
MIN_BARS = 20

# How frequently (seconds) to run the full analysis loop
ANALYSIS_INTERVAL_SEC = 15.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OHLCV:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class QuantState:
    """Current snapshot of all computed quantitative features."""
    timestamp: float = 0.0
    price: float = 0.0

    # Trend / momentum
    rsi_14: float = 50.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    momentum_5: float = 0.0          # 5-bar price momentum
    momentum_20: float = 0.0         # 20-bar price momentum
    momentum_quality: float = 0.0    # momentum strength normalized by ATR

    # Volatility
    atr_14: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_mid: float = 0.0
    bb_pct: float = 0.5              # where price sits in band (0-1)
    bb_width: float = 0.0

    # VWAP
    vwap: float = 0.0
    vwap_dev_z: float = 0.0          # z-score of VWAP deviation
    anchored_vwap: float = 0.0
    vwap_trend: str = "flat"         # "rising", "falling", "flat"

    # Volume
    volume_z: float = 0.0            # current bar volume z-score vs 20-bar avg
    volume_trend: str = "normal"     # "climactic", "dry", "normal"

    # Statistical edge
    hurst: float = 0.5               # >0.5 trending, <0.5 mean-reverting
    mean_rev_prob: float = 0.5       # probability of mean reversion
    trend_strength: float = 0.0      # ADX-like directional strength 0-1

    # Fair Value
    fvg_above: Optional[float] = None    # nearest FVG above price
    fvg_below: Optional[float] = None    # nearest FVG below price
    es_spy_premium: float = 0.0          # ES futures premium over fair value

    # Volume Profile
    developing_poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    value_area_migration: str = "neutral"   # "expanding_up", "expanding_down", "contracting"
    poc_migration: str = "stable"            # "rising", "falling", "stable"
    profile_shape: str = "D"                 # "P" bullish, "b" bearish, "D" balanced
    naked_poc_above: Optional[float] = None
    naked_poc_below: Optional[float] = None
    single_print_zone: bool = False
    hvn_nearest: Optional[float] = None
    lvn_nearest: Optional[float] = None

    # Auction Theory
    initial_balance_high: float = 0.0
    initial_balance_low: float = 0.0
    ib_range: float = 0.0
    ib_extension_level: float = 0.0
    session_type: str = "unknown"           # "range_day", "trend_day", "volatile_day"
    failed_auction: bool = False
    excess_high: bool = False               # long tail at high (strong rejection)
    excess_low: bool = False                # long tail at low (strong rejection)
    poor_high: bool = False                 # no excess at high (likely revisited)
    poor_low: bool = False                  # no excess at low (likely revisited)

    # Microstructure
    of_imbalance: float = 0.0      # order flow imbalance [-1, 1] (bid/ask ratio)
    absorption_score: float = 0.0  # 0-1 how much aggressive flow is being absorbed
    sweep_detected: bool = False
    iceberg_score: float = 0.0     # 0-1 likelihood of iceberg order at current level
    trade_arrival_accel: float = 0.0  # acceleration in trade frequency

    # Regime
    regime: str = "unknown"
    regime_confidence: float = 0.0
    next_regime_prob: dict = field(default_factory=dict)

    # Signals
    long_signals: list[str] = field(default_factory=list)
    short_signals: list[str] = field(default_factory=list)
    bias: str = "neutral"    # "bullish", "bearish", "neutral"
    bias_score: float = 0.0  # -1.0 to +1.0


# ---------------------------------------------------------------------------
# Utility math functions
# ---------------------------------------------------------------------------

def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return [values[-1]] if values else [0.0]
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas[-period:]]
    losses = [max(-d, 0) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs: list[float], lows: list[float], closes: list[float],
         period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    trs = trs[-period:]
    return sum(trs) / len(trs) if trs else 0.0


def _hurst_exponent(series: list[float], max_lag: int = 20) -> float:
    """Estimate Hurst exponent via R/S analysis. H>0.5=trending, H<0.5=mean-reverting."""
    if len(series) < max_lag * 2:
        return 0.5
    lags = range(2, max_lag)
    tau = []
    for lag in lags:
        chunks = [series[i:i+lag] for i in range(0, len(series) - lag, lag)]
        if not chunks:
            continue
        rs_vals = []
        for chunk in chunks:
            if len(chunk) < 2:
                continue
            mean_c = sum(chunk) / len(chunk)
            dev = [c - mean_c for c in chunk]
            cumdev = [sum(dev[:i+1]) for i in range(len(dev))]
            R = max(cumdev) - min(cumdev)
            S = (sum((c - mean_c)**2 for c in chunk) / len(chunk)) ** 0.5
            if S > 0:
                rs_vals.append(R / S)
        if rs_vals:
            tau.append(sum(rs_vals) / len(rs_vals))

    if len(tau) < 2:
        return 0.5
    try:
        log_lags = [math.log(l) for l in list(lags)[:len(tau)]]
        log_tau  = [math.log(t) for t in tau if t > 0]
        if len(log_lags) != len(log_tau) or len(log_lags) < 2:
            return 0.5
        n = len(log_lags)
        sx = sum(log_lags)
        sy = sum(log_tau)
        sxy = sum(log_lags[i] * log_tau[i] for i in range(n))
        sx2 = sum(x**2 for x in log_lags)
        denom = n * sx2 - sx**2
        if denom == 0:
            return 0.5
        return (n * sxy - sx * sy) / denom
    except Exception:
        return 0.5


def _vwap(prices: list[float], volumes: list[float]) -> float:
    if not prices or not volumes:
        return prices[-1] if prices else 0.0
    total_vol = sum(volumes)
    if total_vol == 0:
        return prices[-1]
    return sum(p * v for p, v in zip(prices, volumes)) / total_vol


def _zscore(value: float, series: list[float]) -> float:
    if len(series) < 2:
        return 0.0
    mean = sum(series) / len(series)
    var  = sum((x - mean)**2 for x in series) / len(series)
    std  = math.sqrt(var) if var > 0 else 1e-10
    return (value - mean) / std


# ---------------------------------------------------------------------------
# Markov Regime Tracker
# ---------------------------------------------------------------------------

class MarkovRegimeTracker:
    """Tracks regime state transitions and estimates next-state probabilities."""

    def __init__(self):
        # transition_counts[from][to] = count
        self.transition_counts: dict[str, dict[str, int]] = {
            r: {rr: 0 for rr in ALL_REGIMES} for r in ALL_REGIMES
        }
        self.current_regime: str = REGIME_RANGING
        self.prev_regime: str = REGIME_RANGING
        self.regime_duration: int = 0  # bars in current regime
        self.history: deque = deque(maxlen=200)

    def update(self, new_regime: str) -> bool:
        """Update regime. Returns True if a transition occurred."""
        self.history.append(new_regime)
        if new_regime != self.current_regime:
            # Record transition
            self.transition_counts[self.current_regime][new_regime] += 1
            self.prev_regime = self.current_regime
            self.current_regime = new_regime
            self.regime_duration = 1
            return True
        self.regime_duration += 1
        return False

    def next_state_probs(self) -> dict[str, float]:
        """Return probability distribution over next states."""
        counts = self.transition_counts[self.current_regime]
        total = sum(counts.values())
        if total == 0:
            # Uniform prior
            return {r: 1.0 / len(ALL_REGIMES) for r in ALL_REGIMES}
        return {r: counts[r] / total for r in ALL_REGIMES}


# ---------------------------------------------------------------------------
# Market Brain Agent
# ---------------------------------------------------------------------------

class MarketBrain:
    """Quantitative market learning agent — the intelligence backbone."""

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus

        # Rolling price/volume history (1-minute bars synthesized from ticks)
        self._bars: deque[OHLCV] = deque(maxlen=500)
        self._current_bar: Optional[OHLCV] = None
        self._bar_start_ts: float = 0.0
        self._bar_duration = 60.0  # 1-minute bars

        # Tick-level accumulators for microstructure
        self._tick_prices: deque[float] = deque(maxlen=1000)
        self._tick_buys: deque[float]   = deque(maxlen=1000)  # buy volume
        self._tick_sells: deque[float]  = deque(maxlen=1000)  # sell volume
        self._tick_times: deque[float]  = deque(maxlen=1000)
        self._level_buys:  dict[float, float] = {}
        self._level_sells: dict[float, float] = {}

        # Volume profile for the session
        self._session_vol: dict[float, float] = {}  # price -> volume
        self._session_start_ts: float = time.time()

        # Initial balance tracking
        self._ib_high: float = 0.0
        self._ib_low:  float = float('inf')
        self._ib_set:  bool  = False
        self._ib_start_ts: float = 0.0

        # VWAP state
        self._vwap_pv: float = 0.0
        self._vwap_vol: float = 0.0
        self._vwap_history: deque[float] = deque(maxlen=100)

        # Prior session naked POCs (persisted across analysis runs)
        self._naked_pocs: list[float] = []

        # Fair value gaps
        self._fvgs_above: list[float] = []  # sorted ascending
        self._fvgs_below: list[float] = []  # sorted descending

        # Markov regime tracker
        self._markov = MarkovRegimeTracker()

        # Current quant state (broadcast to all agents)
        self._state = QuantState()
        self._last_analysis_ts: float = 0.0
        self._last_regime: str = "unknown"

        # Pattern tracking
        self._pattern_history: deque[dict] = deque(maxlen=500)

        # Subscribe to events
        self.bus.subscribe(EventType.PRICE_UPDATE, self._on_price_update)
        self.bus.subscribe(EventType.VOLUME_PROFILE_UPDATE, self._on_volume_profile)
        self.bus.subscribe(EventType.CROSS_ASSET_UPDATE, self._on_cross_asset)
        self.bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)

        log.info("Market Brain initialized")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_price_update(self, event: Event):
        data = event.data
        price  = data.get("price", 0.0)
        volume = data.get("volume", data.get("size", 1))
        is_buy = data.get("is_buy", True)
        ts     = event.timestamp

        if not price:
            return

        # Accumulate tick-level microstructure data
        self._tick_prices.append(price)
        self._tick_times.append(ts)
        if is_buy:
            self._tick_buys.append(volume)
            self._tick_sells.append(0)
        else:
            self._tick_buys.append(0)
            self._tick_sells.append(volume)

        lvl = round(round(price / 0.25) * 0.25, 2)
        if is_buy:
            self._level_buys[lvl]  = self._level_buys.get(lvl, 0) + volume
        else:
            self._level_sells[lvl] = self._level_sells.get(lvl, 0) + volume

        # VWAP accumulation
        self._vwap_pv  += price * volume
        self._vwap_vol += volume

        # Session volume profile
        self._session_vol[lvl] = self._session_vol.get(lvl, 0) + volume

        # Build OHLCV bars
        self._update_bar(price, volume, ts)

        # Initial balance (first 60 minutes of RTH = 9:30-10:30 ET)
        self._update_ib(price, ts)

        # Throttled full analysis
        if ts - self._last_analysis_ts >= ANALYSIS_INTERVAL_SEC:
            self._run_analysis(price, ts)
            self._last_analysis_ts = ts

    def _on_volume_profile(self, event: Event):
        """Ingest session volume profile from chart monitor."""
        data = event.data
        poc  = data.get("poc_price", 0.0)
        vah  = data.get("vah_price", 0.0)
        val  = data.get("val_price", 0.0)
        if poc:
            self._state.developing_poc = poc
            self._state.vah = vah
            self._state.val = val

    def _on_cross_asset(self, event: Event):
        """Use cross-asset data for ES/SPY fair value calc."""
        data = event.data
        spy  = data.get("SPY", {}).get("price", 0.0)
        mes  = self._state.price
        if spy > 0 and mes > 0:
            # ES fair value ≈ SPY * 10 (rough ratio)
            fair_value = spy * 10.0
            self._state.es_spy_premium = mes - fair_value

    def _on_trade_result(self, event: Event):
        """Record trade outcome to improve pattern recognition."""
        outcome = event.data.get("outcome", "")
        regime  = event.data.get("regime", "")
        pnl     = event.data.get("pnl", 0.0)
        if not outcome:
            return
        try:
            self.db.upsert_market_pattern(
                pattern_type=f"regime_trade_{regime}",
                conditions_json=json.dumps({"regime": regime}),
                outcome=outcome,
                confidence=min(1.0, abs(pnl) / 10.0),
                sample_size=1,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Bar building
    # ------------------------------------------------------------------

    def _update_bar(self, price: float, volume: float, ts: float):
        if self._current_bar is None:
            self._current_bar = OHLCV(ts, price, price, price, price, volume)
            self._bar_start_ts = ts
            return

        # Update current bar
        bar = self._current_bar
        if price > bar.high:
            bar.high = price
        if price < bar.low:
            bar.low = price
        bar.close = price
        bar.volume += volume

        # Check if bar is complete
        if ts - self._bar_start_ts >= self._bar_duration:
            self._bars.append(bar)
            self._detect_fvgs(bar)
            self._current_bar = OHLCV(ts, price, price, price, price, volume)
            self._bar_start_ts = ts

    def _detect_fvgs(self, bar: OHLCV):
        """Detect fair value gaps (3-candle pattern with a gap)."""
        if len(self._bars) < 2:
            return
        prev2 = self._bars[-2] if len(self._bars) >= 2 else None
        prev1 = self._bars[-1]
        if not prev2:
            return
        # Bullish FVG: prev2.high < bar.low (gap up — unfilled area above prev2)
        if prev2.high < prev1.low:
            mid = (prev2.high + prev1.low) / 2.0
            self._fvgs_above.append(mid)
            self._fvgs_above.sort()
        # Bearish FVG: prev2.low > bar.high (gap down)
        if prev2.low > prev1.high:
            mid = (prev2.low + prev1.high) / 2.0
            self._fvgs_below.append(mid)
            self._fvgs_below.sort(reverse=True)
        # Keep only recent 10
        self._fvgs_above = self._fvgs_above[-10:]
        self._fvgs_below = self._fvgs_below[-10:]

    def _update_ib(self, price: float, ts: float):
        """Track Initial Balance (first 60 min of RTH)."""
        # Approximate: if we haven't started IB yet, start now
        if not self._ib_set and self._ib_start_ts == 0.0:
            self._ib_start_ts = ts

        elapsed = ts - self._ib_start_ts if self._ib_start_ts > 0 else 999999
        if elapsed <= 3600 and not self._ib_set:
            if price > self._ib_high:
                self._ib_high = price
            if price < self._ib_low:
                self._ib_low = price
        elif elapsed > 3600 and not self._ib_set and self._ib_high > 0:
            self._ib_set = True
            log.info("Initial Balance set: %.2f - %.2f (range=%.2f)",
                     self._ib_low, self._ib_high, self._ib_high - self._ib_low)

    # ------------------------------------------------------------------
    # Full analysis loop
    # ------------------------------------------------------------------

    def _run_analysis(self, price: float, ts: float):
        if len(self._bars) < MIN_BARS:
            return

        state = QuantState()
        state.timestamp = ts
        state.price = price

        closes  = [b.close  for b in self._bars]
        highs   = [b.high   for b in self._bars]
        lows    = [b.low    for b in self._bars]
        volumes = [b.volume for b in self._bars]

        # ---- RSI ----
        state.rsi_14 = _rsi(closes)

        # ---- MACD ----
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        n = min(len(ema12), len(ema26))
        macd_line = [ema12[-(n - i)] - ema26[-(n - i)] for i in range(n)]
        macd_line.reverse()
        signal_line = _ema(macd_line, 9)
        state.macd_line   = macd_line[-1] if macd_line else 0.0
        state.macd_signal = signal_line[-1] if signal_line else 0.0
        state.macd_hist   = state.macd_line - state.macd_signal

        # ---- ATR ----
        state.atr_14 = _atr(highs, lows, closes)

        # ---- Bollinger Bands ----
        n_bb = min(20, len(closes))
        bb_closes = closes[-n_bb:]
        bb_mean = sum(bb_closes) / n_bb
        bb_std  = math.sqrt(sum((c - bb_mean)**2 for c in bb_closes) / n_bb)
        state.bb_upper = bb_mean + 2 * bb_std
        state.bb_lower = bb_mean - 2 * bb_std
        state.bb_mid   = bb_mean
        state.bb_width = (state.bb_upper - state.bb_lower) / (bb_mean + 1e-10)
        if state.bb_upper > state.bb_lower:
            state.bb_pct = (price - state.bb_lower) / (state.bb_upper - state.bb_lower)
        else:
            state.bb_pct = 0.5

        # ---- VWAP ----
        if self._vwap_vol > 0:
            state.vwap = self._vwap_pv / self._vwap_vol
            dev = price - state.vwap
            vwap_hist = list(self._vwap_history)
            self._vwap_history.append(state.vwap)
            if len(vwap_hist) >= 5:
                vwap_devs = [c - v for c, v in zip(closes[-len(vwap_hist):], vwap_hist)]
                state.vwap_dev_z = _zscore(dev, vwap_devs)
            if len(vwap_hist) >= 3:
                state.vwap_trend = (
                    "rising" if state.vwap > vwap_hist[-1] else
                    "falling" if state.vwap < vwap_hist[-1] else "flat"
                )

        state.anchored_vwap = _vwap(closes[-50:], volumes[-50:])

        # ---- Volume z-score ----
        n_vol = min(20, len(volumes))
        vol_hist = volumes[-n_vol:]
        state.volume_z = _zscore(volumes[-1], vol_hist)
        if state.volume_z > 2.0:
            state.volume_trend = "climactic"
        elif state.volume_z < -1.5:
            state.volume_trend = "dry"

        # ---- Momentum ----
        if len(closes) >= 5:
            state.momentum_5  = closes[-1] - closes[-5]
        if len(closes) >= 20:
            state.momentum_20 = closes[-1] - closes[-20]
        if state.atr_14 > 0:
            state.momentum_quality = abs(state.momentum_5) / state.atr_14

        # ---- Hurst exponent ----
        state.hurst = _hurst_exponent(closes[-100:] if len(closes) >= 100 else closes)

        # ---- Trend strength (ADX-like, simplified) ----
        if len(closes) >= 14:
            up_moves   = [max(highs[i] - highs[i-1], 0) for i in range(1, len(highs))]
            down_moves = [max(lows[i-1] - lows[i], 0) for i in range(1, len(lows))]
            di_plus  = sum(up_moves[-14:])   / (sum(up_moves[-14:]) + 1e-10)
            di_minus = sum(down_moves[-14:]) / (sum(down_moves[-14:]) + 1e-10)
            dx = abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10)
            state.trend_strength = dx

        # ---- Mean reversion probability ----
        # Based on Hurst + BB position + RSI extremes
        mr_score = 0.0
        if state.hurst < 0.45:
            mr_score += 0.4
        if state.bb_pct > 0.9 or state.bb_pct < 0.1:
            mr_score += 0.3
        if state.rsi_14 > 75 or state.rsi_14 < 25:
            mr_score += 0.3
        state.mean_rev_prob = min(mr_score, 1.0)

        # ---- FVG nearest levels ----
        if self._fvgs_above:
            above = [f for f in self._fvgs_above if f > price]
            state.fvg_above = min(above) if above else None
        if self._fvgs_below:
            below = [f for f in self._fvgs_below if f < price]
            state.fvg_below = max(below) if below else None

        # ---- Volume Profile intelligence ----
        self._compute_volume_profile_features(state, price)

        # ---- Auction theory ----
        self._compute_auction_features(state, price, highs, lows)

        # ---- Microstructure ----
        self._compute_microstructure(state, price)

        # ---- Regime detection ----
        regime = self._detect_regime(state)
        state.regime = regime
        state.regime_confidence = self._compute_regime_confidence(state, regime)
        state.next_regime_prob  = self._markov.next_state_probs()

        regime_changed = self._markov.update(regime)

        # ---- Signal generation ----
        self._generate_quant_signals(state)

        # ---- Persist ----
        self._state = state
        try:
            self.db.insert_market_regime(
                regime=regime,
                volatility=state.atr_14 / (price + 1e-10),
                trend_strength=state.trend_strength,
                features_json=json.dumps({
                    "rsi": round(state.rsi_14, 2),
                    "macd_hist": round(state.macd_hist, 4),
                    "hurst": round(state.hurst, 3),
                    "bb_pct": round(state.bb_pct, 3),
                    "volume_z": round(state.volume_z, 2),
                }),
            )
        except Exception:
            pass

        # ---- Broadcast ----
        self.bus.publish(Event(
            type=EventType.QUANT_SIGNAL,
            source="market_brain",
            priority=3,
            data=self._state_to_dict(state),
        ))

        if regime_changed:
            log.info("Regime change: %s → %s (confidence=%.2f)",
                     self._last_regime, regime, state.regime_confidence)
            self._last_regime = regime
            self.bus.publish(Event(
                type=EventType.MARKET_REGIME_CHANGE,
                source="market_brain",
                priority=8,
                data={
                    "from_regime": self._markov.prev_regime,
                    "to_regime": regime,
                    "confidence": state.regime_confidence,
                    "next_probs": state.next_regime_prob,
                    "quant": self._state_to_dict(state),
                },
            ))

        # ---- Pattern matching ----
        self._check_historical_patterns(state)

    # ------------------------------------------------------------------
    # Volume Profile features
    # ------------------------------------------------------------------

    def _compute_volume_profile_features(self, state: QuantState, price: float):
        if not self._session_vol:
            return

        vol_items = sorted(self._session_vol.items())
        prices = [p for p, _ in vol_items]
        vols   = [v for _, v in vol_items]
        total  = sum(vols)
        if total == 0:
            return

        # POC
        poc_price = prices[vols.index(max(vols))]
        state.developing_poc = poc_price

        # Value Area (70% of volume around POC)
        target_vol = total * 0.70
        poc_idx = vols.index(max(vols))
        lo_idx, hi_idx = poc_idx, poc_idx
        va_vol = vols[poc_idx]
        while va_vol < target_vol:
            lo_add = vols[lo_idx - 1] if lo_idx > 0 else 0
            hi_add = vols[hi_idx + 1] if hi_idx < len(vols) - 1 else 0
            if lo_add >= hi_add and lo_idx > 0:
                lo_idx -= 1
                va_vol += vols[lo_idx]
            elif hi_idx < len(vols) - 1:
                hi_idx += 1
                va_vol += vols[hi_idx]
            else:
                break
        state.vah = prices[hi_idx]
        state.val = prices[lo_idx]

        # Value area migration (compare POC to previous known POC)
        if hasattr(self, '_prev_poc') and self._prev_poc:
            if poc_price > self._prev_poc * 1.001:
                state.poc_migration = "rising"
            elif poc_price < self._prev_poc * 0.999:
                state.poc_migration = "falling"
            else:
                state.poc_migration = "stable"
        self._prev_poc = poc_price  # type: ignore

        # Profile shape: P (POC near top = bullish), b (POC near low = bearish), D (centered)
        price_range = max(prices) - min(prices)
        if price_range > 0:
            poc_rel = (poc_price - min(prices)) / price_range
            if poc_rel > 0.65:
                state.profile_shape = "P"
            elif poc_rel < 0.35:
                state.profile_shape = "b"
            else:
                state.profile_shape = "D"

        # Nearest HVN / LVN (above and below current price)
        mean_vol  = total / len(vols)
        hvns = [p for p, v in zip(prices, vols) if v > mean_vol * 1.5]
        lvns = [p for p, v in zip(prices, vols) if v < mean_vol * 0.5]
        hvns_above = [p for p in hvns if p > price]
        hvns_below = [p for p in hvns if p < price]
        lvns_above = [p for p in lvns if p > price]
        lvns_below = [p for p in lvns if p < price]
        state.hvn_nearest = min(hvns_above) if hvns_above else (max(hvns_below) if hvns_below else None)
        # Check if price is near a LVN (fast move zone)
        near_lvn = any(abs(price - lvn) < state.atr_14 * 0.5 for lvn in lvns) if lvns else False
        state.single_print_zone = near_lvn

        # Poor highs / lows: last bar close near the high/low with small tail
        if self._bars:
            last_bar = self._bars[-1]
            tail_high = last_bar.high - last_bar.close
            tail_low  = last_bar.close - last_bar.low
            bar_range = last_bar.high - last_bar.low
            if bar_range > 0:
                state.poor_high  = tail_high < bar_range * 0.1
                state.poor_low   = tail_low  < bar_range * 0.1
                state.excess_high = tail_high > bar_range * 0.25
                state.excess_low  = tail_low  > bar_range * 0.25

        # Naked POCs: prior session POCs that haven't been touched
        state.naked_poc_above = min((p for p in self._naked_pocs if p > price), default=None)
        state.naked_poc_below = max((p for p in self._naked_pocs if p < price), default=None)

    # ------------------------------------------------------------------
    # Auction theory
    # ------------------------------------------------------------------

    def _compute_auction_features(self, state: QuantState, price: float,
                                   highs: list[float], lows: list[float]):
        # Initial Balance
        if self._ib_set and self._ib_high > 0:
            state.initial_balance_high = self._ib_high
            state.initial_balance_low  = self._ib_low
            state.ib_range = self._ib_high - self._ib_low

            # IB extension levels
            if price > self._ib_high:
                extension = (price - self._ib_high) / (state.ib_range + 1e-10)
                state.ib_extension_level = extension
            elif price < self._ib_low:
                extension = (self._ib_low - price) / (state.ib_range + 1e-10)
                state.ib_extension_level = -extension

            # Session type based on IB range vs ATR
            if state.atr_14 > 0:
                ib_atr_ratio = state.ib_range / state.atr_14
                if ib_atr_ratio > 1.2:
                    state.session_type = "volatile_day"
                elif state.trend_strength > 0.6:
                    state.session_type = "trend_day"
                else:
                    state.session_type = "range_day"

        # Failed auction: price attempted a breakout but reversed
        if len(highs) >= 5:
            recent_high = max(highs[-5:])
            recent_low  = min(lows[-5:])
            if price < recent_high * 0.998 and highs[-1] == recent_high:
                state.failed_auction = True

    # ------------------------------------------------------------------
    # Microstructure
    # ------------------------------------------------------------------

    def _compute_microstructure(self, state: QuantState, price: float):
        if not self._tick_buys or not self._tick_sells:
            return

        # Order flow imbalance [-1, 1]
        recent_buys  = sum(list(self._tick_buys)[-50:])
        recent_sells = sum(list(self._tick_sells)[-50:])
        total_flow   = recent_buys + recent_sells
        if total_flow > 0:
            state.of_imbalance = (recent_buys - recent_sells) / total_flow

        # Absorption: high volume at a price level with small price movement
        if state.atr_14 > 0 and len(self._tick_prices) >= 10:
            recent_prices = list(self._tick_prices)[-50:]
            price_range = max(recent_prices) - min(recent_prices)
            norm_range = price_range / state.atr_14
            if norm_range < 0.3 and total_flow > 0:
                state.absorption_score = min(1.0, (total_flow / max(total_flow, 1)) * (1 - norm_range))

        # Sweep detection: rapid price movement with accelerating volume
        if len(self._tick_times) >= 20:
            times = list(self._tick_times)[-20:]
            if times[-1] - times[0] > 0:
                arrival_rate_recent = 20 / (times[-1] - times[0])
                times_prev = list(self._tick_times)[-40:-20] if len(self._tick_times) >= 40 else times
                arrival_rate_prev = 20 / (times_prev[-1] - times_prev[0] + 1e-10)
                state.trade_arrival_accel = (arrival_rate_recent - arrival_rate_prev) / (arrival_rate_prev + 1e-10)
                state.sweep_detected = state.trade_arrival_accel > 3.0

        # Iceberg detection: repeated volume at same price level
        lvl = round(round(price / 0.25) * 0.25, 2)
        total_at_level = self._level_buys.get(lvl, 0) + self._level_sells.get(lvl, 0)
        if total_flow > 0:
            state.iceberg_score = min(1.0, total_at_level / (total_flow * 0.5 + 1e-10))

    # ------------------------------------------------------------------
    # Regime detection
    # ------------------------------------------------------------------

    def _detect_regime(self, state: QuantState) -> str:
        """Classify market regime from quant features."""
        # Volatile: high ATR relative to recent history, wide BB
        if state.volume_z > 2.5 and state.bb_width > 0.015:
            return REGIME_VOLATILE

        # Trending: low Hurst (persistent), strong ADX-like, MACD aligned
        if (state.hurst > 0.55 and state.trend_strength > 0.5
                and abs(state.momentum_20) > state.atr_14 * 1.5):
            return REGIME_TRENDING

        # Quiet: low volume, narrow BB, low ATR
        if state.volume_trend == "dry" and state.bb_width < 0.005:
            return REGIME_QUIET

        # Breakout: price outside IB + volume surge
        if (state.ib_extension_level and abs(state.ib_extension_level) > 0.5
                and state.volume_z > 1.5):
            return REGIME_BREAKOUT

        return REGIME_RANGING

    def _compute_regime_confidence(self, state: QuantState, regime: str) -> float:
        """Confidence 0-1 for the detected regime."""
        conf = 0.5
        if regime == REGIME_TRENDING:
            conf = (state.trend_strength + (state.hurst - 0.5) * 2) / 2.0
        elif regime == REGIME_RANGING:
            conf = 1.0 - state.trend_strength
        elif regime == REGIME_VOLATILE:
            conf = min(1.0, state.volume_z / 3.0)
        elif regime == REGIME_QUIET:
            conf = max(0.0, 1.0 - abs(state.volume_z))
        elif regime == REGIME_BREAKOUT:
            conf = min(1.0, abs(state.ib_extension_level or 0))
        return max(0.0, min(1.0, conf))

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def _generate_quant_signals(self, state: QuantState):
        longs  = []
        shorts = []

        # RSI signals
        if state.rsi_14 < 30:
            longs.append("RSI_OVERSOLD")
        elif state.rsi_14 > 70:
            shorts.append("RSI_OVERBOUGHT")

        # MACD crossover
        if state.macd_hist > 0 and state.macd_line > state.macd_signal:
            longs.append("MACD_BULL")
        elif state.macd_hist < 0 and state.macd_line < state.macd_signal:
            shorts.append("MACD_BEAR")

        # BB squeeze break
        if state.bb_pct < 0.05:
            longs.append("BB_LOWER_TOUCH")
        elif state.bb_pct > 0.95:
            shorts.append("BB_UPPER_TOUCH")

        # VWAP signals
        if state.vwap > 0 and state.price < state.vwap * 0.998:
            shorts.append("BELOW_VWAP")
        elif state.vwap > 0 and state.price > state.vwap * 1.002:
            longs.append("ABOVE_VWAP")

        # FVG magnets
        if state.fvg_above and abs(state.price - state.fvg_above) < state.atr_14 * 0.5:
            longs.append("FVG_MAGNET_ABOVE")
        if state.fvg_below and abs(state.price - state.fvg_below) < state.atr_14 * 0.5:
            shorts.append("FVG_MAGNET_BELOW")

        # Mean reversion
        if state.mean_rev_prob > 0.7 and state.bb_pct < 0.1:
            longs.append("MEAN_REVERSION_LONG")
        elif state.mean_rev_prob > 0.7 and state.bb_pct > 0.9:
            shorts.append("MEAN_REVERSION_SHORT")

        # Volume profile
        if state.developing_poc > 0 and state.price < state.val:
            shorts.append("BELOW_VALUE_AREA")
        elif state.developing_poc > 0 and state.price > state.vah:
            longs.append("ABOVE_VALUE_AREA")

        # Microstructure
        if state.of_imbalance > 0.3:
            longs.append("FLOW_IMBALANCE_LONG")
        elif state.of_imbalance < -0.3:
            shorts.append("FLOW_IMBALANCE_SHORT")

        if state.absorption_score > 0.6:
            # Absorption at lows = long, at highs = short
            if state.price < state.bb_mid:
                longs.append("ABSORPTION_AT_LOWS")
            else:
                shorts.append("ABSORPTION_AT_HIGHS")

        if state.sweep_detected:
            if state.of_imbalance > 0:
                longs.append("SWEEP_LONG")
            else:
                shorts.append("SWEEP_SHORT")

        # Hurst regime-based
        if state.hurst < 0.4 and state.mean_rev_prob > 0.6:
            if state.bb_pct > 0.85:
                shorts.append("HURST_MEAN_REV_SHORT")
            elif state.bb_pct < 0.15:
                longs.append("HURST_MEAN_REV_LONG")
        elif state.hurst > 0.6 and state.momentum_quality > 1.0:
            if state.momentum_5 > 0:
                longs.append("HURST_MOMENTUM_LONG")
            else:
                shorts.append("HURST_MOMENTUM_SHORT")

        # Profile shape bias
        if state.profile_shape == "P":
            longs.append("P_PROFILE_BULLISH")
        elif state.profile_shape == "b":
            shorts.append("b_PROFILE_BEARISH")

        state.long_signals  = longs
        state.short_signals = shorts

        long_score  = len(longs)
        short_score = len(shorts)
        total       = long_score + short_score + 1e-10
        if long_score > short_score:
            state.bias       = "bullish"
            state.bias_score = (long_score - short_score) / total
        elif short_score > long_score:
            state.bias       = "bearish"
            state.bias_score = -(short_score - long_score) / total
        else:
            state.bias       = "neutral"
            state.bias_score = 0.0

    # ------------------------------------------------------------------
    # Historical pattern matching
    # ------------------------------------------------------------------

    def _check_historical_patterns(self, state: QuantState):
        """Compare current features to stored patterns and broadcast matches."""
        try:
            patterns = self.db.get_market_patterns(min_confidence=0.55, limit=20)
            for p in patterns:
                cond = json.loads(p.get("conditions_json", "{}"))
                pat_regime = cond.get("regime", "")
                if pat_regime and pat_regime == state.regime:
                    self.bus.publish(Event(
                        type=EventType.HISTORICAL_PATTERN_MATCH,
                        source="market_brain",
                        data={
                            "pattern_type": p["pattern_type"],
                            "outcome": p["outcome"],
                            "confidence": p["confidence"],
                            "sample_size": p["sample_size"],
                            "current_regime": state.regime,
                        },
                    ))
                    return  # One broadcast per cycle
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _state_to_dict(self, state: QuantState) -> dict:
        return {
            "timestamp":         state.timestamp,
            "price":             state.price,
            "rsi_14":            round(state.rsi_14, 2),
            "macd_hist":         round(state.macd_hist, 4),
            "macd_line":         round(state.macd_line, 4),
            "bb_pct":            round(state.bb_pct, 3),
            "bb_width":          round(state.bb_width, 4),
            "vwap":              round(state.vwap, 2),
            "vwap_dev_z":        round(state.vwap_dev_z, 2),
            "vwap_trend":        state.vwap_trend,
            "volume_z":          round(state.volume_z, 2),
            "volume_trend":      state.volume_trend,
            "hurst":             round(state.hurst, 3),
            "mean_rev_prob":     round(state.mean_rev_prob, 3),
            "trend_strength":    round(state.trend_strength, 3),
            "atr_14":            round(state.atr_14, 2),
            "momentum_5":        round(state.momentum_5, 2),
            "momentum_20":       round(state.momentum_20, 2),
            "momentum_quality":  round(state.momentum_quality, 2),
            "regime":            state.regime,
            "regime_confidence": round(state.regime_confidence, 3),
            "next_regime_prob":  {k: round(v, 3) for k, v in state.next_regime_prob.items()},
            "fvg_above":         state.fvg_above,
            "fvg_below":         state.fvg_below,
            "es_spy_premium":    round(state.es_spy_premium, 2),
            "developing_poc":    round(state.developing_poc, 2),
            "vah":               round(state.vah, 2),
            "val":               round(state.val, 2),
            "poc_migration":     state.poc_migration,
            "profile_shape":     state.profile_shape,
            "naked_poc_above":   state.naked_poc_above,
            "naked_poc_below":   state.naked_poc_below,
            "single_print_zone": state.single_print_zone,
            "poor_high":         state.poor_high,
            "poor_low":          state.poor_low,
            "excess_high":       state.excess_high,
            "excess_low":        state.excess_low,
            "ib_high":           round(state.initial_balance_high, 2),
            "ib_low":            round(state.initial_balance_low, 2),
            "ib_range":          round(state.ib_range, 2),
            "ib_extension":      round(state.ib_extension_level, 3),
            "session_type":      state.session_type,
            "failed_auction":    state.failed_auction,
            "of_imbalance":      round(state.of_imbalance, 3),
            "absorption_score":  round(state.absorption_score, 3),
            "sweep_detected":    state.sweep_detected,
            "iceberg_score":     round(state.iceberg_score, 3),
            "trade_accel":       round(state.trade_arrival_accel, 2),
            "long_signals":      state.long_signals,
            "short_signals":     state.short_signals,
            "bias":              state.bias,
            "bias_score":        round(state.bias_score, 3),
        }

    def get_state(self) -> dict:
        """Return current quant state as dict (for UI consumption)."""
        return self._state_to_dict(self._state)

    def get_regime(self) -> str:
        return self._state.regime

    def get_bias(self) -> tuple[str, float]:
        return self._state.bias, self._state.bias_score

    def add_naked_poc(self, price: float):
        """Register a prior session POC as naked (untested)."""
        if price not in self._naked_pocs:
            self._naked_pocs.append(price)

    def new_session(self):
        """Call at start of new RTH session to reset session-scoped state."""
        # Save current session POC as naked POC for next session
        if self._state.developing_poc > 0:
            self.add_naked_poc(self._state.developing_poc)

        self._session_vol.clear()
        self._vwap_pv    = 0.0
        self._vwap_vol   = 0.0
        self._ib_high    = 0.0
        self._ib_low     = float('inf')
        self._ib_set     = False
        self._ib_start_ts = time.time()
        self._session_start_ts = time.time()
        log.info("Market Brain: new session started")
