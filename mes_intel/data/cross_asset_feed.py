"""Cross-asset data feed — yfinance background poller.

Fetches prices for correlated assets every 60s and SPY options chain every 5min.
Computes GEX (Gamma Exposure) using Black-Scholes formula.
Publishes updates via callback.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import warnings
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np

# Suppress yfinance / urllib3 noise before importing yfinance
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*")
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")
warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")
import urllib3
urllib3.disable_warnings()

import yfinance as yf
from scipy.stats import norm

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Asset universe
# ---------------------------------------------------------------------------

ASSETS = {
    '^VIX':     {'name': 'VIX',      'type': 'volatility', 'mes_correlation': -1.0},
    '^TNX':     {'name': '10Y YIELD','type': 'rates',       'mes_correlation': -0.7},
    'DX-Y.NYB': {'name': 'DXY',      'type': 'dollar',      'mes_correlation': -0.6},
    'GC=F':     {'name': 'GOLD',     'type': 'commodity',   'mes_correlation': -0.3},
    'CL=F':     {'name': 'OIL/WTI', 'type': 'commodity',   'mes_correlation':  0.4},
    'NQ=F':     {'name': 'NQ FUTS',  'type': 'equity',      'mes_correlation':  0.92},
    'RTY=F':    {'name': 'RUSSELL',  'type': 'equity',      'mes_correlation':  0.75},
    'HYG':      {'name': 'HY BONDS', 'type': 'credit',      'mes_correlation':  0.65},
    'TLT':      {'name': 'LT BONDS', 'type': 'rates',       'mes_correlation': -0.5},
    'BTC-USD':  {'name': 'BITCOIN',  'type': 'crypto',      'mes_correlation':  0.45},
}

# Risk-free rate for BS gamma
_RISK_FREE = 0.045

# ---------------------------------------------------------------------------
# Black-Scholes helpers
# ---------------------------------------------------------------------------

def _bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Return Black-Scholes gamma. Returns 0 on bad inputs."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
        return norm.pdf(d1) / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


# ---------------------------------------------------------------------------
# Asset signal computation
# ---------------------------------------------------------------------------

def _asset_signal(ticker: str, price: float, prev_close: float, change_pct: float,
                  vix_change_pct: float = 0.0) -> tuple[float, str]:
    """
    Compute per-asset signal in [-1, 1] and human label.
    Positive = bullish for MES, negative = bearish.
    """
    name_key = ASSETS[ticker]['name']

    if name_key == 'VIX':
        # Rising VIX = bearish, falling = bullish
        if change_pct > 10:
            return -1.0, 'strong_bearish'
        elif change_pct > 5:
            return -0.7, 'bearish'
        elif change_pct > 2:
            return -0.4, 'mild_bearish'
        elif change_pct < -5:
            return 0.7, 'bullish'
        elif change_pct < -2:
            return 0.4, 'mild_bullish'
        else:
            return 0.0, 'neutral'

    elif name_key == 'DXY':
        # Rising DXY = bearish for risk assets
        if change_pct > 1.0:
            return -0.8, 'bearish'
        elif change_pct > 0.5:
            return -0.4, 'mild_bearish'
        elif change_pct < -0.5:
            return 0.4, 'bullish'
        elif change_pct < -1.0:
            return 0.8, 'strong_bullish'
        else:
            return 0.0, 'neutral'

    elif name_key == '10Y YIELD':
        # Rising yield = bearish (bps approximation: ^TNX is yield × 10)
        # change_pct on a ~4% yield: 1% chg ≈ 4 bps
        bps_change = change_pct * prev_close * 10 if prev_close > 0 else 0
        if bps_change > 10:
            return -0.8, 'strong_bearish'
        elif bps_change > 5:
            return -0.4, 'mild_bearish'
        elif bps_change < -5:
            return 0.4, 'mild_bullish'
        else:
            return 0.0, 'neutral'

    elif name_key == 'GOLD':
        # Rising gold + rising VIX = risk-off = bearish
        if change_pct > 1.0 and vix_change_pct > 2:
            return -0.6, 'risk_off_bearish'
        elif change_pct > 1.5:
            return -0.3, 'mild_bearish'
        elif change_pct < -1.0:
            return 0.2, 'mild_bullish'
        else:
            return 0.0, 'neutral'

    elif name_key == 'OIL/WTI':
        # Mild positive correlation — big moves signal macro risk
        if change_pct > 2.0:
            return 0.3, 'mild_bullish'
        elif change_pct < -2.0:
            return -0.3, 'mild_bearish'
        else:
            return 0.0, 'neutral'

    elif name_key == 'NQ FUTS':
        # NQ outperforming = bullish; underperforming = divergence (bearish)
        # Signal is the direction itself since correlation is ~0.92
        if change_pct > 0.5:
            return 0.6, 'confirming_bullish'
        elif change_pct < -0.5:
            return -0.6, 'confirming_bearish'
        elif 0.1 < change_pct <= 0.5:
            return 0.3, 'mild_bullish'
        elif -0.5 <= change_pct < -0.1:
            return -0.3, 'mild_bearish'
        else:
            return 0.0, 'neutral'

    elif name_key == 'RUSSELL':
        # Risk-on breadth proxy
        if change_pct > 0.5:
            return 0.5, 'risk_on'
        elif change_pct < -0.5:
            return -0.5, 'risk_off'
        else:
            return 0.0, 'neutral'

    elif name_key == 'HY BONDS':
        # Falling HYG = credit stress = bearish
        if change_pct < -0.5:
            return -0.7, 'credit_stress'
        elif change_pct < -0.2:
            return -0.3, 'mild_stress'
        elif change_pct > 0.2:
            return 0.3, 'credit_positive'
        else:
            return 0.0, 'neutral'

    elif name_key == 'LT BONDS':
        # Flight to bonds = risk-off, rate-driven (negative corr to MES)
        if change_pct > 0.5:
            return -0.3, 'mild_bearish'  # bonds up = risk-off
        elif change_pct < -0.5:
            return 0.3, 'mild_bullish'
        else:
            return 0.0, 'neutral'

    elif name_key == 'BITCOIN':
        # Proxy for risk appetite
        if change_pct > 3.0:
            return 0.4, 'risk_appetite'
        elif change_pct > 1.0:
            return 0.2, 'mild_bullish'
        elif change_pct < -3.0:
            return -0.4, 'risk_aversion'
        elif change_pct < -1.0:
            return -0.2, 'mild_bearish'
        else:
            return 0.0, 'neutral'

    return 0.0, 'neutral'


def _signal_label_to_str(sig: float) -> str:
    if sig > 0.5:
        return 'bullish'
    elif sig > 0.15:
        return 'mild_bullish'
    elif sig < -0.5:
        return 'bearish'
    elif sig < -0.15:
        return 'mild_bearish'
    return 'neutral'


# ---------------------------------------------------------------------------
# GEX computation
# ---------------------------------------------------------------------------

def _compute_gex(spot: float, chains: list[dict]) -> dict:
    """
    Compute GEX from a list of option chain records.
    Each record: {'strike': float, 'oi': int, 'iv': float, 'T': float,
                  'opt_price': float, 'is_call': bool}
    Returns dict with net_gex, flip_price, call_wall, put_wall, max_pain, gex_profile, put_call_ratio.
    """
    if not chains or spot <= 0:
        return {}

    # Aggregate by strike
    strike_data: dict[float, dict] = {}
    total_call_oi = 0
    total_put_oi = 0

    for rec in chains:
        K = rec['strike']
        oi = rec['oi']
        iv = rec['iv']
        T = rec['T']
        is_call = rec['is_call']
        opt_price = rec.get('opt_price', 0.0)

        if oi <= 0 or iv <= 0 or T <= 0:
            continue

        gamma = _bs_gamma(spot, K, T, _RISK_FREE, iv)
        gex_value = gamma * oi * spot * 100  # raw GEX per contract notional

        if K not in strike_data:
            strike_data[K] = {
                'call_gex': 0.0, 'put_gex': 0.0,
                'call_oi': 0, 'put_oi': 0,
                'call_price_oi': 0.0, 'put_price_oi': 0.0,
            }

        sd = strike_data[K]
        if is_call:
            sd['call_gex'] += gex_value
            sd['call_oi'] += oi
            sd['call_price_oi'] += opt_price * oi
            total_call_oi += oi
        else:
            sd['put_gex'] += gex_value
            sd['put_oi'] += oi
            sd['put_price_oi'] += opt_price * oi
            total_put_oi += oi

    if not strike_data:
        return {}

    strikes = sorted(strike_data.keys())

    # GEX profile: call GEX positive, put GEX negative
    gex_profile: list[tuple[float, float]] = []
    net_gex = 0.0
    call_wall_strike = 0.0
    call_wall_gex = 0.0
    put_wall_strike = 0.0
    put_wall_gex = 0.0

    for K in strikes:
        sd = strike_data[K]
        net_at_strike = sd['call_gex'] - sd['put_gex']
        gex_profile.append((K, net_at_strike))
        net_gex += net_at_strike

        if sd['call_gex'] > call_wall_gex:
            call_wall_gex = sd['call_gex']
            call_wall_strike = K
        if sd['put_gex'] > put_wall_gex:
            put_wall_gex = sd['put_gex']
            put_wall_strike = K

    # Gamma flip: strike where cumulative GEX crosses zero
    flip_price: Optional[float] = None
    cumulative = 0.0
    prev_cumulative = 0.0
    for K, gex_val in gex_profile:
        prev_cumulative = cumulative
        cumulative += gex_val
        if prev_cumulative * cumulative < 0 and flip_price is None:
            # Linear interpolation of the zero crossing
            if abs(cumulative - prev_cumulative) > 1e-10:
                frac = abs(prev_cumulative) / abs(cumulative - prev_cumulative)
                flip_price = K * frac + (K - (K - strikes[max(0, strikes.index(K) - 1)])) * (1 - frac)
            else:
                flip_price = K

    if flip_price is None:
        # No sign change found; pick strike closest to zero cumulative
        cum = 0.0
        min_abs = float('inf')
        for K, gex_val in gex_profile:
            cum += gex_val
            if abs(cum) < min_abs:
                min_abs = abs(cum)
                flip_price = K

    # Max pain: strike minimizing total option holder value
    max_pain_strike = strikes[0]
    min_pain_value = float('inf')
    for K_test in strikes:
        total_pain = 0.0
        for K, sd in strike_data.items():
            # Call holders lose when K_test < K (OTM at expiry)
            call_intrinsic = max(0.0, K_test - K) * sd['call_oi']
            # Put holders lose when K_test > K
            put_intrinsic = max(0.0, K - K_test) * sd['put_oi']
            total_pain += call_intrinsic + put_intrinsic
        if total_pain < min_pain_value:
            min_pain_value = total_pain
            max_pain_strike = K_test

    put_call_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else 1.0

    return {
        'net_gex': net_gex,
        'flip_price': flip_price,
        'call_wall': call_wall_strike,
        'put_wall': put_wall_strike,
        'max_pain': max_pain_strike,
        'gex_profile': gex_profile,
        'put_call_ratio': round(put_call_ratio, 3),
    }


# ---------------------------------------------------------------------------
# VIX regime helpers
# ---------------------------------------------------------------------------

def _vix_regime(vix: float) -> tuple[str, str]:
    """Return (regime, color_hex)."""
    if vix < 15:
        return 'low', '#00ff88'
    elif vix < 20:
        return 'normal', '#00d4ff'
    elif vix < 25:
        return 'elevated', '#ff8c00'
    else:
        return 'fear', '#ff3344'


# ---------------------------------------------------------------------------
# Main CrossAssetFeed class
# ---------------------------------------------------------------------------

class CrossAssetFeed:
    """Background thread that polls cross-asset prices and SPY options GEX."""

    PRICE_INTERVAL = 30
    OPTIONS_INTERVAL = 300

    def __init__(self, callback: Callable[[str, dict], None], alpaca_feed=None, config=None):
        self._callback = callback
        self._alpaca = alpaca_feed  # optional AlpacaFeed for real-time prices
        self._config = config
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Cached state
        self._latest: dict = {
            'assets': {},
            'gex': {},
            'composite_signal': 0.0,
            'composite_direction': 'FLAT',
            'age_sec': 0,
        }
        self._last_price_fetch: float = 0.0
        self._last_options_fetch: float = 0.0
        self._last_update_time: float = 0.0

        # Keep prev closes for first-run continuity
        self._prev_closes: dict[str, float] = {}

    def set_alpaca_feed(self, alpaca_feed) -> None:
        """Attach an AlpacaFeed for real-time price updates (replaces yfinance)."""
        self._alpaca = alpaca_feed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the background polling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name='CrossAssetFeed')
        self._thread.start()
        log.info("CrossAssetFeed started")

    def stop(self):
        """Signal the background thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("CrossAssetFeed stopped")

    def get_latest(self) -> dict:
        """Return the most recent fetched data (thread-safe)."""
        with self._lock:
            data = dict(self._latest)
            if self._last_update_time > 0:
                data['age_sec'] = int(time.time() - self._last_update_time)
            return data

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self):
        """Main polling loop."""
        while not self._stop_event.is_set():
            now = time.time()
            price_interval, options_interval = self._current_intervals()
            do_prices = (now - self._last_price_fetch) >= price_interval
            do_options = (now - self._last_options_fetch) >= options_interval

            if do_prices:
                self._fetch_prices()
            if do_options:
                self._fetch_options()

            if do_prices or do_options:
                self._recompute_composite()
                self._emit()

            self._stop_event.wait(timeout=10)

    def _current_intervals(self) -> tuple[int, int]:
        if self._config is None:
            return self.PRICE_INTERVAL, self.OPTIONS_INTERVAL

        if self._is_rth_session():
            return (
                self._config.cross_asset.price_interval_sec,
                self._config.cross_asset.options_interval_sec,
            )

        return (
            self._config.cross_asset.off_hours_price_interval_sec,
            self._config.cross_asset.off_hours_options_interval_sec,
        )

    def _is_rth_session(self) -> bool:
        now = datetime.now(tz=ZoneInfo("America/Phoenix"))
        minutes = now.hour * 60 + now.minute
        return 390 <= minutes < 840

    # ------------------------------------------------------------------
    # Price fetching
    # ------------------------------------------------------------------

    def _fetch_prices(self):
        """Fetch intraday 5-min bars for real-time prices vs yesterday's close."""
        from datetime import date as _date
        tickers = list(ASSETS.keys())
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = yf.download(
                    tickers,
                    period='2d',
                    interval='5m',       # real-time intraday (~15min delay for free)
                    group_by='ticker',
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
        except Exception as exc:
            log.warning("Price download failed: %s", exc)
            self._last_price_fetch = time.time()
            return

        assets_out: dict[str, dict] = {}
        now = time.time()
        today = _date.today()

        for ticker, meta in ASSETS.items():
            name = meta['name']
            try:
                if len(tickers) == 1:
                    df = raw
                else:
                    df = raw[ticker] if ticker in raw.columns.get_level_values(0) else None

                if df is None or df.empty:
                    assets_out[name] = self._stale_asset(name)
                    continue

                closes = df['Close'].dropna()
                if closes.empty:
                    assets_out[name] = self._stale_asset(name)
                    continue

                # Split into today vs yesterday for accurate change_pct
                try:
                    today_closes = closes[closes.index.normalize().date == today] if hasattr(closes.index, 'normalize') else closes[closes.index.map(lambda x: x.date()) == today]
                    yest_closes = closes[closes.index.normalize().date != today] if hasattr(closes.index, 'normalize') else closes[closes.index.map(lambda x: x.date()) != today]
                except Exception:
                    today_closes = closes
                    yest_closes = closes[:-1]

                price = float(today_closes.iloc[-1]) if not today_closes.empty else float(closes.iloc[-1])
                prev_close = float(yest_closes.iloc[-1]) if not yest_closes.empty else self._prev_closes.get(ticker, price)
                if prev_close > 0:
                    self._prev_closes[ticker] = prev_close

                change_pct = ((price - prev_close) / prev_close * 100) if prev_close != 0 else 0.0

                # VIX change needed for gold signal — pass 0 here; cross-computed in composite
                sig_val, sig_label = _asset_signal(ticker, price, prev_close, change_pct)

                assets_out[name] = {
                    'price': round(price, 4),
                    'prev_close': round(prev_close, 4),
                    'change_pct': round(change_pct, 3),
                    'signal': _signal_label_to_str(sig_val),
                    'signal_value': round(sig_val, 3),
                    'signal_detail': sig_label,
                    'age_sec': 0,
                    'ticker': ticker,
                    'correlation': meta['mes_correlation'],
                    'asset_type': meta['type'],
                    '_fetched_at': now,
                }

            except Exception as exc:
                log.debug("Error processing %s: %s", ticker, exc)
                assets_out[name] = self._stale_asset(name)

        # Re-compute gold signal with actual VIX change
        vix_chg = assets_out.get('VIX', {}).get('change_pct', 0.0)
        if 'GOLD' in assets_out and 'GC=F' in ASSETS:
            gold = assets_out['GOLD']
            sig_val, sig_label = _asset_signal(
                'GC=F', gold['price'], gold['prev_close'], gold['change_pct'], vix_chg
            )
            gold['signal_value'] = round(sig_val, 3)
            gold['signal'] = _signal_label_to_str(sig_val)
            gold['signal_detail'] = sig_label

        # Overlay real-time Alpaca prices where available (supersedes yfinance)
        if self._alpaca is not None:
            try:
                live = self._alpaca.get_latest_prices()
                for asset_name, live_data in live.items():
                    if asset_name in assets_out and live_data.get('price', 0) > 0:
                        entry = assets_out[asset_name]
                        entry['price'] = live_data['price']
                        entry['prev_close'] = live_data.get('prev_close', entry['prev_close'])
                        entry['change_pct'] = live_data.get('change_pct', entry['change_pct'])
                        entry['age_sec'] = live_data.get('age_sec', 0)
                        entry['live'] = live_data.get('live', False)
                        entry['_fetched_at'] = time.time() - live_data.get('age_sec', 0)
                        # Recompute signal with fresh price
                        ticker = entry.get('ticker', '')
                        if ticker in ASSETS:
                            sig_val, sig_label = _asset_signal(
                                ticker, entry['price'], entry['prev_close'], entry['change_pct']
                            )
                            entry['signal_value'] = round(sig_val, 3)
                            entry['signal'] = _signal_label_to_str(sig_val)
                            entry['signal_detail'] = sig_label
            except Exception as exc:
                log.debug("Alpaca overlay error: %s", exc)

        with self._lock:
            self._latest['assets'] = assets_out
        self._last_price_fetch = time.time()
        live_count = sum(1 for a in assets_out.values() if a.get('live'))
        log.debug("Prices fetched: %d assets (%d live via Alpaca)", len(assets_out), live_count)

    def _stale_asset(self, name: str) -> dict:
        """Return the previously cached entry for an asset, with incremented age."""
        existing = self._latest.get('assets', {}).get(name, {})
        if existing:
            age = int(time.time() - existing.get('_fetched_at', time.time()))
            return dict(existing, age_sec=age)
        return {
            'price': 0.0, 'prev_close': 0.0, 'change_pct': 0.0,
            'signal': 'neutral', 'signal_value': 0.0, 'signal_detail': 'no_data',
            'age_sec': 9999, '_fetched_at': 0,
        }

    # ------------------------------------------------------------------
    # Options / GEX fetching
    # ------------------------------------------------------------------

    def _fetch_options(self):
        try:
            spy = yf.Ticker('SPY')
            # Get real-time spot from intraday history (more reliable than fast_info in yf 1.2)
            spot = 0.0
            try:
                h = spy.history(period='1d', interval='1m')
                if not h.empty:
                    spot = float(h['Close'].dropna().iloc[-1])
            except Exception:
                pass
            if spot <= 0:
                try:
                    info = spy.fast_info
                    spot = float(info.get('lastPrice') or info.get('regularMarketPrice') or 0)
                except Exception:
                    pass
            if spot <= 0:
                # Last resort: use cached NQ price / 10 ≈ SPY
                with self._lock:
                    nq = self._latest.get('assets', {}).get('NQ FUTS', {})
                spot = nq.get('price', 0) / 42.0 if nq else 0  # NQ/42 ≈ SPY roughly

            expirations = spy.options  # tuple of date strings
        except Exception as exc:
            log.warning("Failed to get SPY options dates: %s", exc)
            self._last_options_fetch = time.time()
            return

        chains: list[dict] = []
        today = time.time()
        max_expirations = 3

        for exp_str in (expirations or [])[:max_expirations]:
            try:
                chain = spy.option_chain(exp_str)
                # days to expiry
                exp_ts = time.mktime(time.strptime(exp_str, '%Y-%m-%d'))
                T = max((exp_ts - today) / 86400 / 365, 1 / 365)

                for is_call, df in ((True, chain.calls), (False, chain.puts)):
                    for _, row in df.iterrows():
                        oi = int(row.get('openInterest', 0) or 0)
                        if oi <= 0:
                            continue
                        iv = float(row.get('impliedVolatility', 0) or 0)
                        strike = float(row.get('strike', 0) or 0)
                        opt_price = float(row.get('lastPrice', 0) or 0)
                        if iv <= 0 or strike <= 0:
                            continue
                        chains.append({
                            'strike': strike,
                            'oi': oi,
                            'iv': iv,
                            'T': T,
                            'opt_price': opt_price,
                            'is_call': is_call,
                        })
            except Exception as exc:
                log.debug("Options chain fetch error for %s: %s", exp_str, exc)

        if not chains:
            self._last_options_fetch = time.time()
            return

        try:
            gex_data = _compute_gex(spot, chains)
        except Exception as exc:
            log.warning("GEX computation failed: %s", exc)
            self._last_options_fetch = time.time()
            return

        if not gex_data:
            self._last_options_fetch = time.time()
            return

        # Augment with derived fields
        net_gex = gex_data.get('net_gex', 0)
        pcr = gex_data.get('put_call_ratio', 1.0)

        # VIX for regime
        with self._lock:
            vix_price = self._latest.get('assets', {}).get('VIX', {}).get('price', 18.0)

        vix_regime, regime_color = _vix_regime(vix_price)

        if net_gex > 2_000_000_000:
            dealer_pos = 'long_gamma'
        elif net_gex < -1_000_000_000:
            dealer_pos = 'short_gamma'
        elif net_gex > 0:
            dealer_pos = 'long_gamma'
        else:
            dealer_pos = 'short_gamma'

        gex_data.update({
            'vix_regime': vix_regime,
            'regime_color': regime_color,
            'dealer_positioning': dealer_pos,
            '_fetched_at': time.time(),
        })

        with self._lock:
            self._latest['gex'] = gex_data
        self._last_options_fetch = time.time()
        log.debug("GEX fetched: net_gex=%.2e flip=%.2f", net_gex, gex_data.get('flip_price', 0))

    # ------------------------------------------------------------------
    # Composite signal
    # ------------------------------------------------------------------

    def _recompute_composite(self):
        """Weighted average of individual asset signals × correlation magnitude."""
        with self._lock:
            assets = dict(self._latest.get('assets', {}))

        if not assets:
            return

        # Correlation-weighted average
        weighted_sum = 0.0
        weight_total = 0.0

        for name, data in assets.items():
            sig = data.get('signal_value', 0.0)
            corr = data.get('correlation', 0.0)
            # Invert signal for negatively correlated assets
            # (signal_value is already from MES perspective, so just weight by |corr|)
            w = abs(corr)
            weighted_sum += sig * w
            weight_total += w

        composite = (weighted_sum / weight_total) if weight_total > 0 else 0.0
        composite = float(np.clip(composite, -1.0, 1.0))

        if composite > 0.15:
            direction = 'LONG'
        elif composite < -0.15:
            direction = 'SHORT'
        else:
            direction = 'FLAT'

        with self._lock:
            self._latest['composite_signal'] = round(composite, 4)
            self._latest['composite_direction'] = direction
            self._last_update_time = time.time()

    def _emit(self):
        """Call the user callback with the latest snapshot."""
        try:
            data = self.get_latest()
            self._callback('cross_asset_update', data)
        except Exception as exc:
            log.warning("Callback error: %s", exc)
