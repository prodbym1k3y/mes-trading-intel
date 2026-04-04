#!/usr/bin/env python3
"""
MES SIGNAL ENGINE (Rithmic + Alpaca + yfinance)
=================================================
Real-time MES futures analysis combining:
  1. Order Flow Analysis   - True tick-level cumulative delta, VWAP deviation,
                             OBV divergence, MFI, volume climax, buy/sell pressure
  2. Quantitative Analysis - RSI-9/3, Bollinger z-score, stochastic, momentum,
                             mean-reversion z-score, Hurst exponent
  3. Options Market Analysis - SPY put/call ratio, IV skew, VIX level & term structure

Data sources:
  - Rithmic (async-rithmic): Real-time MES tick stream + historical 5-min bars
  - Alpaca Markets: SPY options chains with greeks/IV
  - yfinance: VIX family (^VIX, ^VIX9D, ^VIX3M)

Each pillar scores -100 (extremely oversold) to +100 (extremely overbought).
"""

import argparse
import asyncio
import os
import subprocess
import sys
import time
import warnings
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from scipy import stats as sp_stats
from dotenv import load_dotenv
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text
from rich import box

warnings.filterwarnings("ignore")
console = Console()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Rithmic credentials
RITHMIC_USER = os.getenv("RITHMIC_USER")
RITHMIC_PASS = os.getenv("RITHMIC_PASSWORD")
RITHMIC_SYSTEM = os.getenv("RITHMIC_SYSTEM", "Rithmic Paper Trading")
RITHMIC_GATEWAY = os.getenv("RITHMIC_GATEWAY", "Chicago")

# Alpaca (for SPY options only)
ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}

# Rithmic gateway URLs (WebSocket)
RITHMIC_GATEWAYS = {
    "Chicago": "rituz00100.rithmic.com:443",
    "Seoul": "rituz00100.rithmic.com:443",
    "Test": "rituz00100.rithmic.com:443",
}

# MES contract details
MES_EXCHANGE = "CME"
MES_SYMBOL_ROOT = "MES"

WEIGHTS = {"order_flow": 0.35, "quantitative": 0.35, "options": 0.30}

EXTREME_OB = 60
STRONG_OB = 40
MILD_OB = 20
NEUTRAL_LOW = -20
STRONG_OS = -40
EXTREME_OS = -60

NOTIFY_THRESHOLD = 20
DEFAULT_MONITOR_INTERVAL = 5  # seconds
DEFAULT_NOTIFY_INTERVAL = 900  # seconds (15 min)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp(val: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def send_notification(title: str, message: str, sound: str = "default") -> None:
    script = (
        f'display notification "{message}" '
        f'with title "{title}" '
        f'sound name "{sound}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception:
        pass


def notify_signal(score: float, price: float, label: str,
                   of_score: float = 0, qa_score: float = 0, op_score: float = 0,
                   mes_price: Optional[float] = None) -> None:
    abs_score = abs(score)
    if abs_score >= 60:
        sound, intensity = "Sosumi", "EXTREME"
    elif abs_score >= 40:
        sound, intensity = "Glass", "STRONG"
    elif abs_score >= 20:
        sound, intensity = "Pop", "MILD"
    else:
        sound, intensity = "default", "NEUTRAL"

    direction = "OVERBOUGHT" if score > 0 else "OVERSOLD" if score < 0 else "NEUTRAL"
    title = f"MES {intensity} {direction}  [{score:+.1f}]"
    price_str = f"MES ${mes_price:.2f}" if mes_price else f"SPY ${price:.2f}"
    body = (
        f"{price_str}  |  Composite: {score:+.1f}/100\n"
        f"Flow:{of_score:+.0f}  Quant:{qa_score:+.0f}  Opts:{op_score:+.0f}"
    )
    send_notification(title, body, sound)


def zscore_to_score(z: float, cap: float = 3.0) -> float:
    return smooth_score(z, scale=cap)


def smooth_score(raw: float, scale: float = 50.0) -> float:
    """Map a raw value through a tanh curve so scores approach +/-100 smoothly.
    scale controls how quickly it saturates: higher = harder to reach extremes.
    raw=scale -> ~76, raw=2*scale -> ~96, raw=0.5*scale -> ~46."""
    return float(np.tanh(raw / scale) * 100)


def pct_rank(series: pd.Series, current: float) -> float:
    return float((series < current).sum()) / len(series) * 100


def hurst_exponent(ts: pd.Series, max_lag: int = 40) -> float:
    ts = ts.dropna().values
    if len(ts) < max_lag * 2:
        return 0.5
    lags = range(2, max_lag)
    tau = []
    for lag in lags:
        chunks = [ts[i : i + lag] for i in range(0, len(ts) - lag, lag)]
        if len(chunks) < 2:
            continue
        rs_values = []
        for chunk in chunks:
            if len(chunk) < 2:
                continue
            mean_c = np.mean(chunk)
            deviate = np.cumsum(chunk - mean_c)
            r = np.max(deviate) - np.min(deviate)
            s = np.std(chunk, ddof=1)
            if s > 0:
                rs_values.append(r / s)
        if rs_values:
            tau.append((lag, np.mean(rs_values)))
    if len(tau) < 4:
        return 0.5
    log_lags = np.log([t[0] for t in tau])
    log_rs = np.log([t[1] for t in tau])
    slope, _, _, _, _ = sp_stats.linregress(log_lags, log_rs)
    return float(slope)


def signal_label(score: float) -> tuple[str, str]:
    if score >= EXTREME_OB:
        return "EXTREMELY OVERBOUGHT", "bold red"
    elif score >= STRONG_OB:
        return "STRONGLY OVERBOUGHT", "red"
    elif score >= MILD_OB:
        return "MILDLY OVERBOUGHT", "yellow"
    elif score > NEUTRAL_LOW:
        return "NEUTRAL", "white"
    elif score > STRONG_OS:
        return "MILDLY OVERSOLD", "cyan"
    elif score > EXTREME_OS:
        return "STRONGLY OVERSOLD", "green"
    else:
        return "EXTREMELY OVERSOLD", "bold green"


# ---------------------------------------------------------------------------
# Rithmic Tick Accumulator
# ---------------------------------------------------------------------------

class TickAccumulator:
    """Accumulates real-time MES ticks into order flow metrics.

    Tracks:
    - Real cumulative delta (buy vs sell volume classified by trade @ bid/ask)
    - Tick-level buy/sell pressure
    - Volume profile
    - Real-time VWAP
    - Best bid/ask spread
    """

    def __init__(self, max_ticks: int = 50000):
        self.ticks = deque(maxlen=max_ticks)
        self.last_price: Optional[float] = None
        self.last_size: int = 0
        self.bid: Optional[float] = None
        self.ask: Optional[float] = None
        self.bid_size: int = 0
        self.ask_size: int = 0
        self.session_start: Optional[datetime] = None

        # Running accumulators (reset each session)
        self.cum_delta: float = 0.0
        self.total_buy_vol: int = 0
        self.total_sell_vol: int = 0
        self.total_volume: int = 0
        self.cum_tp_vol: float = 0.0  # for VWAP: sum(price * volume)
        self.cum_vol: int = 0         # for VWAP: sum(volume)

        # Recent window for short-term analysis
        self.recent_deltas = deque(maxlen=3000)  # ~5 minutes of ticks
        self.recent_trades = deque(maxlen=6000)   # ~10 minutes

        # Per-bar aggregation (5-min bars built from ticks)
        self._bar_start: Optional[datetime] = None
        self._bar_open: float = 0
        self._bar_high: float = 0
        self._bar_low: float = 999999
        self._bar_close: float = 0
        self._bar_volume: int = 0
        self._bar_buy_vol: int = 0
        self._bar_sell_vol: int = 0
        self.live_bars: list = []  # completed 5-min bars from tick data
        self._lock = asyncio.Lock()

    def reset_session(self):
        self.cum_delta = 0.0
        self.total_buy_vol = 0
        self.total_sell_vol = 0
        self.total_volume = 0
        self.cum_tp_vol = 0.0
        self.cum_vol = 0
        self.recent_deltas.clear()
        self.recent_trades.clear()
        self.live_bars.clear()
        self._bar_start = None
        self.session_start = datetime.now(timezone.utc)

    async def on_tick(self, tick: dict):
        """Process a real-time tick from Rithmic."""
        async with self._lock:
            now = datetime.now(timezone.utc)

            # BBO update
            if tick.get("bid") is not None:
                self.bid = tick["bid"]
            if tick.get("ask") is not None:
                self.ask = tick["ask"]
            if tick.get("bid_size") is not None:
                self.bid_size = tick["bid_size"]
            if tick.get("ask_size") is not None:
                self.ask_size = tick["ask_size"]

            # Last trade update
            price = tick.get("last_trade_price")
            size = tick.get("last_trade_size", 0)
            if price is None or price <= 0:
                return

            self.last_price = price
            self.last_size = size

            # Classify as buy or sell based on trade vs bid/ask
            if self.bid is not None and self.ask is not None:
                mid = (self.bid + self.ask) / 2
                if price >= self.ask:
                    delta = size  # bought at ask = buyer aggressor
                elif price <= self.bid:
                    delta = -size  # sold at bid = seller aggressor
                elif price > mid:
                    delta = size * 0.5  # lean buy
                elif price < mid:
                    delta = -size * 0.5  # lean sell
                else:
                    delta = 0  # at mid, neutral
            else:
                delta = 0

            # Accumulate
            self.cum_delta += delta
            self.total_volume += size
            if delta > 0:
                self.total_buy_vol += size
            elif delta < 0:
                self.total_sell_vol += size

            # VWAP accumulation
            self.cum_tp_vol += price * size
            self.cum_vol += size

            # Store for recent analysis
            self.recent_deltas.append((now, delta, size))
            self.recent_trades.append((now, price, size, delta))
            self.ticks.append((now, price, size, delta))

            # 5-min bar aggregation
            bar_minute = now.replace(second=0, microsecond=0)
            bar_slot = bar_minute.replace(minute=(bar_minute.minute // 5) * 5)

            if self._bar_start is None or bar_slot != self._bar_start:
                # Complete previous bar
                if self._bar_start is not None and self._bar_volume > 0:
                    self.live_bars.append({
                        "Timestamp": self._bar_start,
                        "Open": self._bar_open,
                        "High": self._bar_high,
                        "Low": self._bar_low,
                        "Close": self._bar_close,
                        "Volume": self._bar_volume,
                        "BuyVol": self._bar_buy_vol,
                        "SellVol": self._bar_sell_vol,
                        "Delta": self._bar_buy_vol - self._bar_sell_vol,
                    })
                # Start new bar
                self._bar_start = bar_slot
                self._bar_open = price
                self._bar_high = price
                self._bar_low = price
                self._bar_close = price
                self._bar_volume = size
                self._bar_buy_vol = size if delta > 0 else 0
                self._bar_sell_vol = size if delta < 0 else 0
            else:
                self._bar_high = max(self._bar_high, price)
                self._bar_low = min(self._bar_low, price)
                self._bar_close = price
                self._bar_volume += size
                if delta > 0:
                    self._bar_buy_vol += size
                elif delta < 0:
                    self._bar_sell_vol += size

    def get_vwap(self) -> float:
        """Session VWAP from accumulated tick data."""
        if self.cum_vol > 0:
            return self.cum_tp_vol / self.cum_vol
        return np.nan

    def get_recent_delta(self, seconds: int = 300) -> float:
        """Cumulative delta over recent N seconds."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        total = 0.0
        for ts, delta, _ in self.recent_deltas:
            if ts >= cutoff:
                total += delta
        return total

    def get_recent_pressure(self, seconds: int = 300) -> float:
        """Buy/sell pressure ratio over recent N seconds. Returns -1 to +1."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        buy = 0
        sell = 0
        for ts, _, size, delta in self.recent_trades:
            if ts >= cutoff:
                if delta > 0:
                    buy += size
                elif delta < 0:
                    sell += size
        total = buy + sell
        if total == 0:
            return 0.0
        return (buy - sell) / total

    def get_recent_volume(self, seconds: int = 300) -> int:
        """Total volume over recent N seconds."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        total = 0
        for ts, _, size, _ in self.recent_trades:
            if ts >= cutoff:
                total += size
        return total

    def get_spread(self) -> float:
        """Current bid-ask spread in points."""
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return np.nan

    def get_current_bar_snapshot(self) -> Optional[dict]:
        """Get the in-progress bar (not yet completed)."""
        if self._bar_start is None or self._bar_volume == 0:
            return None
        return {
            "Timestamp": self._bar_start,
            "Open": self._bar_open,
            "High": self._bar_high,
            "Low": self._bar_low,
            "Close": self._bar_close,
            "Volume": self._bar_volume,
            "BuyVol": self._bar_buy_vol,
            "SellVol": self._bar_sell_vol,
            "Delta": self._bar_buy_vol - self._bar_sell_vol,
        }


# ---------------------------------------------------------------------------
# Rithmic Connection Manager
# ---------------------------------------------------------------------------

class RithmicFeed:
    """Manages Rithmic connection, subscriptions, and data retrieval."""

    def __init__(self):
        self.client = None
        self.connected = False
        self.mes_symbol: Optional[str] = None
        self.accumulator = TickAccumulator()
        self.hist_bars_5m: Optional[pd.DataFrame] = None
        self.hist_bars_daily: Optional[pd.DataFrame] = None
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        """Connect to Rithmic and subscribe to MES data."""
        from async_rithmic import RithmicClient, ReconnectionSettings

        async with self._connect_lock:
            if self.connected:
                return

            gateway_url = RITHMIC_GATEWAYS.get(RITHMIC_GATEWAY, RITHMIC_GATEWAYS["Chicago"])

            self.client = RithmicClient(
                user=RITHMIC_USER,
                password=RITHMIC_PASS,
                system_name=RITHMIC_SYSTEM,
                app_name="MESSignalEngine",
                app_version="2.0",
                url=gateway_url,
                reconnection_settings=ReconnectionSettings(
                    max_retries=None,
                    backoff_type="linear",
                    interval=10,
                    max_delay=60,
                ),
            )

            # Connect to ticker + history plants only (we don't need order/pnl)
            from async_rithmic.enums import SysInfraType
            await self.client.connect(
                plants=[SysInfraType.TICKER_PLANT, SysInfraType.HISTORY_PLANT]
            )

            # Get front-month MES contract
            self.mes_symbol = await self.client.get_front_month_contract(
                MES_SYMBOL_ROOT, MES_EXCHANGE
            )
            if not self.mes_symbol:
                raise RuntimeError("Could not resolve front-month MES contract")

            # Attach tick handler
            self.client.on_tick += self.accumulator.on_tick

            # Subscribe to last trade + BBO
            from async_rithmic.enums import DataType
            await self.client.subscribe_to_market_data(
                self.mes_symbol, MES_EXCHANGE, DataType.LAST_TRADE
            )
            await self.client.subscribe_to_market_data(
                self.mes_symbol, MES_EXCHANGE, DataType.BBO
            )

            self.accumulator.reset_session()
            self.connected = True

    async def disconnect(self):
        if self.client and self.connected:
            try:
                await self.client.disconnect(timeout=5.0)
            except Exception:
                pass
            self.connected = False

    async def fetch_historical_bars(self, bar_minutes: int = 5, days_back: int = 3) -> pd.DataFrame:
        """Fetch historical bars from Rithmic history plant."""
        if not self.connected or not self.mes_symbol:
            return pd.DataFrame()

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days_back)

        try:
            bars = await self.client.get_historical_time_bars(
                symbol=self.mes_symbol,
                exchange=MES_EXCHANGE,
                start_time=start,
                end_time=now,
                bar_type=2,  # MINUTE_BAR
                bar_type_periods=bar_minutes,
                wait=True,
            )

            if not bars:
                return pd.DataFrame()

            rows = []
            for b in bars:
                rows.append({
                    "Timestamp": b.get("bar_end_datetime", b.get("datetime", now)),
                    "Open": b.get("open", 0),
                    "High": b.get("high", 0),
                    "Low": b.get("low", 0),
                    "Close": b.get("close", 0),
                    "Volume": b.get("volume", 0),
                })

            df = pd.DataFrame(rows)
            if not df.empty:
                df["Timestamp"] = pd.to_datetime(df["Timestamp"], utc=True)
                df = df.set_index("Timestamp").sort_index()
                # Remove zero-volume bars
                df = df[df["Volume"] > 0]
            return df
        except Exception as e:
            console.print(f"[dim red]  Rithmic history error: {e}[/]")
            return pd.DataFrame()

    async def fetch_daily_bars(self, days_back: int = 365) -> pd.DataFrame:
        """Fetch daily bars from Rithmic for regime detection."""
        if not self.connected or not self.mes_symbol:
            return pd.DataFrame()

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days_back)

        try:
            bars = await self.client.get_historical_time_bars(
                symbol=self.mes_symbol,
                exchange=MES_EXCHANGE,
                start_time=start,
                end_time=now,
                bar_type=3,  # DAILY_BAR
                bar_type_periods=1,
                wait=True,
            )

            if not bars:
                return pd.DataFrame()

            rows = []
            for b in bars:
                rows.append({
                    "Timestamp": b.get("bar_end_datetime", b.get("datetime", now)),
                    "Open": b.get("open", 0),
                    "High": b.get("high", 0),
                    "Low": b.get("low", 0),
                    "Close": b.get("close", 0),
                    "Volume": b.get("volume", 0),
                })

            df = pd.DataFrame(rows)
            if not df.empty:
                df["Timestamp"] = pd.to_datetime(df["Timestamp"], utc=True)
                df = df.set_index("Timestamp").sort_index()
                df = df[df["Volume"] > 0]
            return df
        except Exception:
            return pd.DataFrame()

    def get_combined_5m_bars(self) -> pd.DataFrame:
        """Combine Rithmic historical 5m bars with live tick-built bars."""
        parts = []

        # Historical bars
        if self.hist_bars_5m is not None and not self.hist_bars_5m.empty:
            parts.append(self.hist_bars_5m)

        # Live bars from tick accumulation
        if self.accumulator.live_bars:
            live_df = pd.DataFrame(self.accumulator.live_bars)
            live_df["Timestamp"] = pd.to_datetime(live_df["Timestamp"], utc=True)
            live_df = live_df.set_index("Timestamp").sort_index()
            parts.append(live_df)

        if not parts:
            return pd.DataFrame()

        combined = pd.concat(parts)
        combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()
        return combined


# ---------------------------------------------------------------------------
# Alpaca Data Fetching (SPY options only)
# ---------------------------------------------------------------------------

def alpaca_get(path: str, params: dict = None, base: str = None, retries: int = 3) -> dict:
    url = f"{base or ALPACA_DATA_URL}{path}"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=ALPACA_HEADERS, params=params or {}, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def fetch_spy_snapshot() -> dict:
    return alpaca_get("/v2/stocks/SPY/snapshot")


def fetch_options_chain(symbol: str, option_type: str, strike_gte: float,
                        strike_lte: float, exp_gte: str, exp_lte: str,
                        limit: int = 50) -> dict:
    all_snapshots = {}
    page_token = None
    while True:
        params = {
            "limit": limit,
            "type": option_type,
            "strike_price_gte": str(strike_gte),
            "strike_price_lte": str(strike_lte),
            "expiration_date_gte": exp_gte,
            "expiration_date_lte": exp_lte,
        }
        if page_token:
            params["page_token"] = page_token
        data = alpaca_get(f"/v1beta1/options/snapshots/{symbol}", params)
        all_snapshots.update(data.get("snapshots", {}))
        page_token = data.get("next_page_token")
        if not page_token:
            break
    return all_snapshots


def fetch_vix_data() -> dict:
    vix = yf.Ticker("^VIX")
    vix9d = yf.Ticker("^VIX9D")
    vix3m = yf.Ticker("^VIX3M")
    return {
        "vix_hist": vix.history(period="6mo", interval="1d"),
        "vix9d_hist": vix9d.history(period="3mo", interval="1d"),
        "vix3m_hist": vix3m.history(period="3mo", interval="1d"),
    }


def fetch_alpaca_bars(symbol: str = "SPY", timeframe: str = "5Min",
                      days_back: int = 3) -> pd.DataFrame:
    """Fetch historical bars from Alpaca as fallback when Rithmic has no data."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    all_bars = []
    page_token = None
    try:
        while True:
            params = {
                "timeframe": timeframe,
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 10000,
                "adjustment": "raw",
                "feed": "iex",
            }
            if page_token:
                params["page_token"] = page_token
            data = alpaca_get(f"/v2/stocks/{symbol}/bars", params)
            bars = data.get("bars", [])
            all_bars.extend(bars)
            page_token = data.get("next_page_token")
            if not page_token:
                break
        if not all_bars:
            return pd.DataFrame()
        rows = []
        for b in all_bars:
            rows.append({
                "Timestamp": b["t"],
                "Open": b["o"],
                "High": b["h"],
                "Low": b["l"],
                "Close": b["c"],
                "Volume": b["v"],
                "VWAP": b.get("vw", 0),
            })
        df = pd.DataFrame(rows)
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], utc=True)
        df = df.set_index("Timestamp").sort_index()
        return df[df["Volume"] > 0]
    except Exception:
        return pd.DataFrame()


def fetch_alpaca_daily_bars(symbol: str = "SPY", days_back: int = 400) -> pd.DataFrame:
    """Fetch daily bars from Alpaca."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    all_bars = []
    page_token = None
    try:
        while True:
            params = {
                "timeframe": "1Day",
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "limit": 10000,
                "adjustment": "raw",
                "feed": "iex",
            }
            if page_token:
                params["page_token"] = page_token
            data = alpaca_get(f"/v2/stocks/{symbol}/bars", params)
            all_bars.extend(data.get("bars", []))
            page_token = data.get("next_page_token")
            if not page_token:
                break
        if not all_bars:
            return pd.DataFrame()
        rows = []
        for b in all_bars:
            rows.append({
                "Timestamp": b["t"],
                "Open": b["o"],
                "High": b["h"],
                "Low": b["l"],
                "Close": b["c"],
                "Volume": b["v"],
            })
        df = pd.DataFrame(rows)
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], utc=True)
        df = df.set_index("Timestamp").sort_index()
        return df[df["Volume"] > 0]
    except Exception:
        return pd.DataFrame()


def compute_vwap_from_bars(df_intra: pd.DataFrame) -> float:
    if df_intra.empty or "Volume" not in df_intra.columns:
        return np.nan
    if "VWAP" in df_intra.columns:
        last_vwap = df_intra["VWAP"].iloc[-1]
        if not np.isnan(last_vwap) and last_vwap > 0:
            return float(last_vwap)
    typical = (df_intra["High"] + df_intra["Low"] + df_intra["Close"]) / 3
    cum_vol = df_intra["Volume"].cumsum()
    cum_tp_vol = (typical * df_intra["Volume"]).cumsum()
    vwap_series = cum_tp_vol / cum_vol.replace(0, np.nan)
    return float(vwap_series.iloc[-1]) if len(vwap_series) > 0 else np.nan


# ---------------------------------------------------------------------------
# Unified Data Fetch
# ---------------------------------------------------------------------------

def fetch_options_and_vix(spy_price: float, cached_options: dict = None,
                          cached_vix: dict = None) -> dict:
    """Fetch SPY options from Alpaca and VIX from yfinance."""
    result = {}

    # Options
    if cached_options:
        result.update(cached_options)
    else:
        today_d = datetime.now().date()
        exp_near = (today_d + timedelta(days=1)).strftime("%Y-%m-%d")
        exp_monthly_start = (today_d + timedelta(days=25)).strftime("%Y-%m-%d")
        exp_monthly_end = (today_d + timedelta(days=45)).strftime("%Y-%m-%d")
        strike_lo = round(spy_price * 0.93)
        strike_hi = round(spy_price * 1.07)

        calls = fetch_options_chain("SPY", "call", strike_lo, strike_hi,
                                    exp_monthly_start, exp_monthly_end)
        puts = fetch_options_chain("SPY", "put", strike_lo, strike_hi,
                                   exp_monthly_start, exp_monthly_end)
        exp_near_end = (today_d + timedelta(days=7)).strftime("%Y-%m-%d")
        near_calls = fetch_options_chain("SPY", "call", strike_lo, strike_hi,
                                         exp_near, exp_near_end, limit=50)
        near_puts = fetch_options_chain("SPY", "put", strike_lo, strike_hi,
                                        exp_near, exp_near_end, limit=50)
        result["calls"] = calls
        result["puts"] = puts
        result["near_calls"] = near_calls
        result["near_puts"] = near_puts

    # VIX
    if cached_vix:
        result.update(cached_vix)
    else:
        vix_data = fetch_vix_data()
        result["vix_hist"] = vix_data["vix_hist"]
        result["vix9d_hist"] = vix_data["vix9d_hist"]
        result["vix3m_hist"] = vix_data["vix3m_hist"]

    return result


def build_data_dict(rithmic_feed: 'RithmicFeed', spy_price: float,
                    opts_vix: dict) -> dict:
    """Build the unified data dictionary from Rithmic + Alpaca + yfinance."""
    acc = rithmic_feed.accumulator
    mes_price = acc.last_price
    df_5m = rithmic_feed.get_combined_5m_bars()
    df_daily = rithmic_feed.hist_bars_daily if rithmic_feed.hist_bars_daily is not None else pd.DataFrame()

    # Use tick-level VWAP if available, else compute from bars
    tick_vwap = acc.get_vwap()
    if np.isnan(tick_vwap) and not df_5m.empty:
        # Compute VWAP from today's bars only
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if hasattr(df_5m.index, 'strftime'):
            today_bars = df_5m[df_5m.index.strftime("%Y-%m-%d") == today_str]
        else:
            today_bars = df_5m
        tick_vwap = compute_vwap_from_bars(today_bars if not today_bars.empty else df_5m)

    # Real tick-level order flow data
    tick_data = {
        "cum_delta": acc.cum_delta,
        "total_buy_vol": acc.total_buy_vol,
        "total_sell_vol": acc.total_sell_vol,
        "total_volume": acc.total_volume,
        "recent_delta_5m": acc.get_recent_delta(300),
        "recent_delta_30m": acc.get_recent_delta(1800),
        "recent_delta_2h": acc.get_recent_delta(7200),
        "recent_pressure_5m": acc.get_recent_pressure(300),
        "recent_pressure_1h": acc.get_recent_pressure(3600),
        "recent_vol_5m": acc.get_recent_volume(300),
        "recent_vol_30m": acc.get_recent_volume(1800),
        "spread": acc.get_spread(),
        "bid": acc.bid,
        "ask": acc.ask,
        "bid_size": acc.bid_size,
        "ask_size": acc.ask_size,
    }

    # SPY previous close for daily change calc
    spy_snapshot = fetch_spy_snapshot()
    spy_daily_bar = spy_snapshot.get("dailyBar", {})
    spy_prev_bar = spy_snapshot.get("prevDailyBar", {})

    # Use MES price if available, else SPY*10 estimate
    price = mes_price if mes_price else (spy_price * 10 if spy_price else 0)
    if not mes_price and spy_price:
        mes_price = spy_price * 10  # synthetic MES price for display

    # Volume info
    vol_today = acc.total_volume
    if vol_today == 0 and not df_5m.empty:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if hasattr(df_5m.index, 'strftime'):
            today_bars = df_5m[df_5m.index.strftime("%Y-%m-%d") == today_str]
        else:
            today_bars = df_5m
        vol_today = int(today_bars["Volume"].sum()) if not today_bars.empty else 0

    # Buy pressure from tick data (superior to bar-based estimate)
    buy_pressure = acc.get_recent_pressure(3600)  # 1-hour pressure

    return {
        "price": price,
        "mes_price": mes_price,
        "spy_price": spy_price,
        "spy_snapshot": spy_snapshot,
        "daily_vwap": tick_vwap,
        "daily_info": {
            "daily_close": float(spy_daily_bar.get("c", spy_price)),
            "daily_open": float(spy_daily_bar.get("o", spy_price)),
            "daily_high": float(spy_daily_bar.get("h", spy_price)),
            "daily_low": float(spy_daily_bar.get("l", spy_price)),
            "daily_volume": int(spy_daily_bar.get("v", 0)),
            "prev_close": float(spy_prev_bar.get("c", spy_price)),
            "prev_volume": int(spy_prev_bar.get("v", 0)),
        },
        "vol_today": vol_today,
        "hist_daily": df_daily,
        "hist_5m": df_5m,
        "tick_data": tick_data,
        "buy_pressure": buy_pressure,
        **opts_vix,
    }


# ---------------------------------------------------------------------------
# 1. ORDER FLOW ANALYSIS (Rithmic tick-level)
# ---------------------------------------------------------------------------

def analyze_order_flow(data: dict) -> dict:
    """Order flow analysis using real Rithmic tick data + 5-min bars."""
    results = {}
    df_intra = data["hist_5m"]
    df_daily = data["hist_daily"]
    price = data["price"]
    daily_vwap = data["daily_vwap"]
    tick = data.get("tick_data", {})

    # --- VWAP deviation (tick-level VWAP from Rithmic) ---
    if not np.isnan(daily_vwap) and daily_vwap > 0 and price > 0:
        vwap_dev_pct = (price - daily_vwap) / daily_vwap * 100
    else:
        vwap_dev_pct = 0.0
    # MES VWAP dev: similar to SPY but in futures ticks
    # scale=0.8 -> 0.4% dev = +46, 0.8% = +76, 1.2% = +89
    vwap_score = smooth_score(vwap_dev_pct, scale=0.8)
    results["vwap_price"] = daily_vwap
    results["vwap_dev_pct"] = vwap_dev_pct
    results["vwap_score"] = vwap_score

    # --- Real Cumulative Delta from tick data (last 2.5 hours) ---
    recent_delta = tick.get("recent_delta_2h", 0)
    total_vol = tick.get("total_volume", 0)

    if total_vol > 100:
        # Normalize delta by total volume to get a percentage
        cd_norm = (recent_delta / max(total_vol, 1)) * 100

        # Also compute from bars if we have enough history for percentile context
        if not df_intra.empty and len(df_intra) >= 30 and "Delta" in df_intra.columns:
            # Use bar-level delta for percentile ranking
            all_cd30 = df_intra["Delta"].rolling(30).sum().dropna()
            all_avg = df_intra["Volume"].rolling(30).mean()
            all_cd_norm = (all_cd30 / (all_avg * 30).replace(0, np.nan) * 100).dropna()
            if len(all_cd_norm) > 5:
                cd_pctile = pct_rank(all_cd_norm, cd_norm)
                cd_score = smooth_score(cd_pctile - 50, scale=50)
            else:
                cd_score = smooth_score(cd_norm, scale=50)
        else:
            # Pure tick-based scoring
            cd_score = smooth_score(cd_norm, scale=50)
        results["cum_delta"] = cd_norm
    elif not df_intra.empty and len(df_intra) >= 30:
        # Fallback: bar-based cumulative delta proxy
        recent = df_intra.iloc[-30:]
        rng = recent["High"] - recent["Low"]
        rng = rng.replace(0, np.nan)
        close_pos = (recent["Close"] - recent["Low"]) / rng
        delta_proxy = (2 * close_pos - 1) * recent["Volume"]
        cum_delta = delta_proxy.sum()
        avg_bar_vol = recent["Volume"].mean()
        cd_norm = cum_delta / (avg_bar_vol * 30) * 100 if avg_bar_vol > 0 else 0
        cd_score = smooth_score(cd_norm, scale=50)
        results["cum_delta"] = cd_norm
    else:
        cd_score = 0
        results["cum_delta"] = 0
    results["cum_delta_score"] = cd_score

    # --- OBV Divergence (from 5-min bars, last 2 hours) ---
    lookback = min(24, len(df_intra) - 1) if not df_intra.empty else 0
    if lookback >= 12:
        recent = df_intra.iloc[-lookback:]
        obv = (np.sign(recent["Close"].diff()) * recent["Volume"]).fillna(0).cumsum()
        if len(obv) >= 6:
            avg_vol = recent["Volume"].mean()
            obv_slope = sp_stats.linregress(range(len(obv)), obv.values)[0]
            obv_s_norm = obv_slope / avg_vol if avg_vol > 0 else 0

            if len(df_intra) >= 60:
                all_obv = (np.sign(df_intra["Close"].diff()) * df_intra["Volume"]).fillna(0).cumsum()
                all_slopes = []
                step = max(1, lookback // 4)
                for i in range(lookback, len(df_intra), step):
                    seg = all_obv.iloc[i-lookback:i]
                    s = sp_stats.linregress(range(len(seg)), seg.values)[0]
                    a = df_intra["Volume"].iloc[i-lookback:i].mean()
                    all_slopes.append(s / a if a > 0 else 0)
                if all_slopes:
                    obv_pctile = pct_rank(pd.Series(all_slopes), obv_s_norm)
                    obv_score = smooth_score(obv_pctile - 50, scale=50)
                else:
                    obv_score = 0
            else:
                obv_score = smooth_score(obv_s_norm * 200, scale=50)
            results["obv_divergence"] = obv_s_norm
        else:
            obv_score = 0
            results["obv_divergence"] = 0
    else:
        obv_score = 0
        results["obv_divergence"] = 0
    results["obv_score"] = obv_score

    # --- MFI (14 bars of 5-min = ~70 minutes) ---
    if not df_intra.empty and len(df_intra) >= 20:
        period = 14
        typical = (df_intra["High"] + df_intra["Low"] + df_intra["Close"]) / 3
        raw_mf = typical * df_intra["Volume"]
        pos_mf = raw_mf.where(typical.diff() > 0, 0).rolling(period).sum()
        neg_mf = raw_mf.where(typical.diff() <= 0, 0).rolling(period).sum()
        mfi = 100 - (100 / (1 + pos_mf / neg_mf.replace(0, np.nan)))
        mfi_val = mfi.iloc[-1]
        mfi_score = smooth_score(mfi_val - 50, scale=40)
        results["mfi"] = mfi_val
    else:
        mfi_score = 0
        results["mfi"] = 50
    results["mfi_score"] = mfi_score

    # --- Relative Volume ---
    vol_today = data["vol_today"]
    if not df_daily.empty and len(df_daily) >= 21:
        vol_20_avg = df_daily["Volume"].iloc[-21:-1].mean()
        rvol = vol_today / vol_20_avg if vol_20_avg > 0 else 1.0
    else:
        rvol = 1.0
    results["rvol"] = rvol

    # --- Volume Surge (tick-level: 5-min vs 30-min average) ---
    recent_vol_5m = tick.get("recent_vol_5m", 0)
    recent_vol_30m = tick.get("recent_vol_30m", 0)
    if recent_vol_30m > 100:
        avg_5m_from_30m = recent_vol_30m / 6  # normalize to per-5-min
        if avg_5m_from_30m > 0:
            vol_z = (recent_vol_5m - avg_5m_from_30m) / avg_5m_from_30m
        else:
            vol_z = 0

        # Direction check from recent price movement
        recent_pressure = tick.get("recent_pressure_5m", 0)
        if vol_z > 0.8 and recent_pressure > 0.1:
            climax_score = smooth_score(vol_z * 40, scale=60)
        elif vol_z > 0.8 and recent_pressure < -0.1:
            climax_score = smooth_score(-vol_z * 40, scale=60)
        else:
            climax_score = 0
        results["vol_z"] = vol_z
    elif not df_intra.empty and len(df_intra) >= 36:
        # Fallback: bar-based
        recent_vol = df_intra["Volume"].iloc[-6:].mean()
        prior_vol = df_intra["Volume"].iloc[-36:-6].mean()
        vol_z = (recent_vol - prior_vol) / prior_vol if prior_vol > 0 else 0
        recent_chg = (price - df_intra["Close"].iloc[-7]) / df_intra["Close"].iloc[-7] if len(df_intra) >= 7 else 0
        if vol_z > 0.8 and recent_chg > 0.0015:
            climax_score = smooth_score(vol_z * 40, scale=60)
        elif vol_z > 0.8 and recent_chg < -0.0015:
            climax_score = smooth_score(-vol_z * 40, scale=60)
        else:
            climax_score = 0
        results["vol_z"] = vol_z
    else:
        climax_score = 0
        results["vol_z"] = 0
    results["climax_score"] = climax_score

    # --- Real Tick Buy/Sell Pressure (from Rithmic) ---
    buy_pressure = data.get("buy_pressure", 0.0)
    pressure_score = smooth_score(buy_pressure * 100, scale=55)
    results["buy_pressure"] = buy_pressure
    results["pressure_score"] = pressure_score

    results["trades_today"] = vol_today

    # --- Bid/Ask imbalance from L1 (Rithmic) ---
    bid_size = tick.get("bid_size", 0)
    ask_size = tick.get("ask_size", 0)
    if bid_size + ask_size > 0:
        imbalance = (bid_size - ask_size) / (bid_size + ask_size)
    else:
        imbalance = 0
    results["book_imbalance"] = imbalance

    # --- Composite (intraday-weighted) ---
    composite = (
        vwap_score * 0.25      # VWAP deviation
        + cd_score * 0.20      # Real cumulative delta
        + obv_score * 0.10     # OBV divergence
        + mfi_score * 0.20     # Money flow index
        + climax_score * 0.10  # Volume surge/climax
        + pressure_score * 0.15 # Buy/sell pressure
    )
    results["composite"] = clamp(composite)
    return results


# ---------------------------------------------------------------------------
# 2. QUANTITATIVE ANALYSIS
# ---------------------------------------------------------------------------

def analyze_quantitative(data: dict) -> dict:
    """Intraday quantitative analysis using 5-min bars."""
    results = {}
    df_intra = data["hist_5m"]
    df_daily = data["hist_daily"]
    price = data["price"]
    daily_vwap = data["daily_vwap"]

    if df_intra.empty or len(df_intra) < 10:
        # Not enough data — return neutral
        results.update({
            "rsi_9": 50, "rsi_score": 0, "rsi_3": 50, "rsi3_score": 0,
            "bb_zscore": 0, "bb_score": 0, "bb_upper": price, "bb_lower": price,
            "stoch_k": 50, "stoch_d": 50, "stoch_score": 0,
            "mom_1h": 0, "mom_3h": 0, "mom_score": 0,
            "mr_zscore": 0, "mr_score": 0, "hurst": 0.5, "regime": "N/A",
            "adx": 0, "sma50": 0, "sma200": 0, "composite": 0,
        })
        return results

    intra_close = df_intra["Close"]

    # --- RSI-9 (5-min bars) ---
    delta_i = intra_close.diff()
    gain9 = delta_i.where(delta_i > 0, 0).ewm(span=9, adjust=False).mean()
    loss9 = (-delta_i.where(delta_i < 0, 0)).ewm(span=9, adjust=False).mean()
    rs9 = gain9 / loss9.replace(0, np.nan)
    rsi9 = 100 - (100 / (1 + rs9))
    rsi9_val = rsi9.iloc[-1]
    rsi9_score = smooth_score(rsi9_val - 50, scale=40)
    results["rsi_9"] = rsi9_val
    results["rsi_score"] = rsi9_score

    # --- RSI-3 (ultra-fast) ---
    gain3 = delta_i.where(delta_i > 0, 0).ewm(span=3, adjust=False).mean()
    loss3 = (-delta_i.where(delta_i < 0, 0)).ewm(span=3, adjust=False).mean()
    rs3 = gain3 / loss3.replace(0, np.nan)
    rsi3 = 100 - (100 / (1 + rs3))
    rsi3_val = rsi3.iloc[-1]
    rsi3_score = smooth_score(rsi3_val - 50, scale=55)
    results["rsi_3"] = rsi3_val
    results["rsi3_score"] = rsi3_score

    # --- Bollinger Band z-score (20-period on 5-min) ---
    sma20i = intra_close.rolling(20).mean()
    std20i = intra_close.rolling(20).std()
    if len(sma20i.dropna()) > 0 and std20i.iloc[-1] > 0:
        bb_z = (price - sma20i.iloc[-1]) / std20i.iloc[-1]
        bb_upper = sma20i.iloc[-1] + 2 * std20i.iloc[-1]
        bb_lower = sma20i.iloc[-1] - 2 * std20i.iloc[-1]
    else:
        bb_z = 0
        bb_upper = bb_lower = price
    bb_score = zscore_to_score(bb_z, cap=3.0)
    results["bb_zscore"] = bb_z
    results["bb_score"] = bb_score
    results["bb_upper"] = bb_upper
    results["bb_lower"] = bb_lower

    # --- Stochastic (14-period on 5-min) ---
    if len(df_intra) >= 20:
        low14i = df_intra["Low"].rolling(14).min()
        high14i = df_intra["High"].rolling(14).max()
        stoch_k = ((intra_close - low14i) / (high14i - low14i)) * 100
        stoch_d = stoch_k.rolling(3).mean()
        results["stoch_k"] = stoch_k.iloc[-1]
        results["stoch_d"] = stoch_d.iloc[-1]
        stoch_score = smooth_score(stoch_k.iloc[-1] - 50, scale=45)
    else:
        results["stoch_k"] = 50
        results["stoch_d"] = 50
        stoch_score = 0
    results["stoch_score"] = stoch_score

    # --- Momentum (1h = 12 bars, 3h = 36 bars) ---
    if len(intra_close) >= 37:
        mom_1h = (price / intra_close.iloc[-13] - 1) * 100
        mom_3h = (price / intra_close.iloc[-37] - 1) * 100
        all_mom_1h = intra_close.pct_change(12).dropna() * 100
        mom_pctile = pct_rank(all_mom_1h, mom_1h) if len(all_mom_1h) > 0 else 50
        mom_score = smooth_score(mom_pctile - 50, scale=50)
        results["mom_1h"] = mom_1h
        results["mom_3h"] = mom_3h
    elif len(intra_close) >= 13:
        mom_1h = (price / intra_close.iloc[-13] - 1) * 100
        mom_score = smooth_score(mom_1h / 0.3 * 50, scale=50)
        results["mom_1h"] = mom_1h
        results["mom_3h"] = 0
    else:
        mom_score = 0
        results["mom_1h"] = 0
        results["mom_3h"] = 0
    results["mom_score"] = mom_score

    # --- Mean-reversion vs VWAP ---
    if not np.isnan(daily_vwap) and daily_vwap > 0:
        vwap_dev = (price - daily_vwap) / daily_vwap * 100
        if len(df_intra) >= 20:
            all_devs = ((intra_close - daily_vwap) / daily_vwap * 100)
            dev_std = all_devs.std()
            mr_z = vwap_dev / dev_std if dev_std > 0 else 0
        else:
            mr_z = vwap_dev / 0.3
        mr_score = zscore_to_score(mr_z, cap=3.0)
    else:
        mr_z = 0
        mr_score = 0
    results["mr_zscore"] = mr_z
    results["mr_score"] = mr_score

    # --- Daily context: Hurst + ADX ---
    if not df_daily.empty and len(df_daily) >= 50:
        daily_close = df_daily["Close"]
        log_returns = np.log(daily_close / daily_close.shift(1)).dropna()
        h = hurst_exponent(log_returns)
        results["hurst"] = h
        results["regime"] = "Mean-Reverting" if h < 0.45 else ("Trending" if h > 0.55 else "Random Walk")

        high = df_daily["High"]
        low = df_daily["Low"]
        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        plus_dm[plus_dm < minus_dm] = 0
        minus_dm[minus_dm < plus_dm] = 0
        tr = pd.concat([high - low, (high - daily_close.shift(1)).abs(),
                        (low - daily_close.shift(1)).abs()], axis=1).max(axis=1)
        atr14 = tr.ewm(span=14, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr14)
        minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr14)
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        adx = dx.ewm(span=14, adjust=False).mean()
        results["adx"] = adx.iloc[-1]

        sma50 = daily_close.rolling(50).mean()
        sma200 = daily_close.rolling(200).mean() if len(daily_close) >= 200 else daily_close.rolling(len(daily_close)).mean()
        results["sma50"] = sma50.iloc[-1]
        results["sma200"] = sma200.iloc[-1]
    else:
        results["hurst"] = 0.5
        results["regime"] = "N/A"
        results["adx"] = 0
        results["sma50"] = 0
        results["sma200"] = 0

    # --- Composite ---
    composite = (
        rsi9_score * 0.20
        + rsi3_score * 0.10
        + bb_score * 0.20
        + stoch_score * 0.15
        + mom_score * 0.15
        + mr_score * 0.20
    )
    results["composite"] = clamp(composite)
    return results


# ---------------------------------------------------------------------------
# 3. OPTIONS MARKET ANALYSIS
# ---------------------------------------------------------------------------

def parse_option_symbol(sym: str) -> dict:
    base = sym.replace("SPY", "")
    expiry = base[:6]
    opt_type = base[6]
    strike = int(base[7:]) / 1000
    return {"expiry": expiry, "type": opt_type, "strike": strike}


def analyze_options(data: dict) -> dict:
    results = {}
    spy_price = data["spy_price"]
    price = spy_price

    calls = data.get("calls", {})
    puts = data.get("puts", {})
    near_calls = data.get("near_calls", {})
    near_puts = data.get("near_puts", {})
    vix_hist = data.get("vix_hist", pd.DataFrame())

    if vix_hist.empty:
        results.update({
            "vix": 0, "vix_pctile": 50, "vix_1d_chg": 0, "vix_5d_chg": 0,
            "vix_score": 0, "term_structure": "N/A", "term_spread_pct": 0,
            "term_score": 0, "pc_vol_ratio": 1.0, "pc_oi_ratio": 1.0,
            "pc_score": 0, "iv_skew_pct": None, "skew_score": 0,
            "divergence_score": 0, "composite": 0,
        })
        return results

    # --- VIX: percentile + absolute + rate of change ---
    vix_current = vix_hist["Close"].iloc[-1]
    vix_pctile = pct_rank(vix_hist["Close"], vix_current)

    pctile_score = smooth_score(50 - vix_pctile, scale=55)

    if vix_current <= 13:
        abs_score = +50
    elif vix_current <= 16:
        abs_score = +25
    elif vix_current <= 20:
        abs_score = 0
    elif vix_current <= 25:
        abs_score = -25
    elif vix_current <= 30:
        abs_score = -50
    elif vix_current <= 40:
        abs_score = -75
    else:
        abs_score = -90

    if len(vix_hist) >= 6:
        vix_1d_chg = (vix_current / vix_hist["Close"].iloc[-2] - 1) * 100
        vix_5d_chg = (vix_current / vix_hist["Close"].iloc[-6] - 1) * 100
        roc_score = smooth_score(-vix_5d_chg, scale=30)
    else:
        roc_score = 0
        vix_1d_chg = 0
        vix_5d_chg = 0

    vix_score = pctile_score * 0.40 + abs_score * 0.35 + roc_score * 0.25
    results["vix"] = vix_current
    results["vix_pctile"] = vix_pctile
    results["vix_1d_chg"] = vix_1d_chg
    results["vix_5d_chg"] = vix_5d_chg
    results["vix_score"] = clamp(vix_score)

    # --- VIX term structure ---
    vix9d_hist = data.get("vix9d_hist", pd.DataFrame())
    vix3m_hist = data.get("vix3m_hist", pd.DataFrame())
    if len(vix9d_hist) > 0 and len(vix3m_hist) > 0:
        vix9d_val = vix9d_hist["Close"].iloc[-1]
        vix3m_val = vix3m_hist["Close"].iloc[-1]
        term_spread = (vix9d_val - vix3m_val) / vix3m_val * 100
        term_score = smooth_score(-term_spread, scale=30)
        results["vix9d"] = vix9d_val
        results["vix3m"] = vix3m_val
        results["term_spread_pct"] = term_spread
        results["term_structure"] = "Backwardation (FEAR)" if term_spread > 0 else "Contango (complacent)"
    else:
        term_score = 0
        results["term_structure"] = "N/A"
        results["term_spread_pct"] = 0
    results["term_score"] = term_score

    # --- Put/Call ratio ---
    call_vol = sum(s.get("dailyBar", {}).get("v", 0) for s in near_calls.values())
    put_vol = sum(s.get("dailyBar", {}).get("v", 0) for s in near_puts.values())
    pc_vol = put_vol / call_vol if call_vol > 0 else 1.0

    call_oi_total = sum(s.get("dailyBar", {}).get("v", 0) for s in calls.values())
    put_oi_total = sum(s.get("dailyBar", {}).get("v", 0) for s in puts.values())
    pc_oi = put_oi_total / call_oi_total if call_oi_total > 0 else 1.0

    avg_pc = pc_vol * 0.8 + pc_oi * 0.2
    pc_score = smooth_score((0.95 - avg_pc) * 100, scale=60)
    results["pc_vol_ratio"] = pc_vol
    results["pc_oi_ratio"] = pc_oi
    results["pc_score"] = pc_score

    # --- IV Skew ---
    otm_put_ivs = []
    otm_call_ivs = []
    for sym, snap in puts.items():
        info = parse_option_symbol(sym)
        iv = snap.get("impliedVolatility")
        if iv and iv > 0 and price * 0.95 <= info["strike"] <= price * 0.98:
            otm_put_ivs.append(iv)
    for sym, snap in calls.items():
        info = parse_option_symbol(sym)
        iv = snap.get("impliedVolatility")
        if iv and iv > 0 and price * 1.02 <= info["strike"] <= price * 1.05:
            otm_call_ivs.append(iv)

    if otm_put_ivs and otm_call_ivs:
        put_iv_avg = np.mean(otm_put_ivs)
        call_iv_avg = np.mean(otm_call_ivs)
        iv_skew = put_iv_avg - call_iv_avg
        skew_pct = iv_skew / call_iv_avg * 100 if call_iv_avg > 0 else 0
        skew_score = smooth_score(35 - skew_pct, scale=80)
        results["otm_put_iv"] = put_iv_avg
        results["otm_call_iv"] = call_iv_avg
        results["iv_skew_pct"] = skew_pct
    else:
        skew_score = 0
        results["iv_skew_pct"] = None
    results["skew_score"] = skew_score

    # --- ATM IV vs realized vol ---
    atm_ivs = []
    for sym, snap in calls.items():
        info = parse_option_symbol(sym)
        iv = snap.get("impliedVolatility")
        if iv and price * 0.99 <= info["strike"] <= price * 1.01:
            atm_ivs.append(iv)
    df_daily = data.get("hist_daily", pd.DataFrame())
    if atm_ivs:
        atm_iv = np.mean(atm_ivs)
        realized_vol = df_daily["Close"].pct_change().iloc[-20:].std() * np.sqrt(252) if not df_daily.empty and len(df_daily) >= 20 else 0
        results["atm_iv"] = atm_iv
        results["realized_vol_20d"] = realized_vol
        results["iv_rv_spread"] = atm_iv - realized_vol
    else:
        results["atm_iv"] = None

    # --- Net delta exposure ---
    total_call_delta = sum(
        snap.get("greeks", {}).get("delta", 0) * snap.get("dailyBar", {}).get("v", 0)
        for snap in calls.values()
    )
    total_put_delta = sum(
        snap.get("greeks", {}).get("delta", 0) * snap.get("dailyBar", {}).get("v", 0)
        for snap in puts.values()
    )
    net_delta = total_call_delta + total_put_delta
    results["net_delta"] = net_delta

    # --- VIX/SPY divergence ---
    div_score = 0
    if len(vix_hist) >= 20 and not df_daily.empty and len(df_daily) >= 21:
        spy_chg_20 = (price / df_daily["Close"].iloc[-21] - 1) * 100
        vix_chg_20 = (vix_hist["Close"].iloc[-1] / vix_hist["Close"].iloc[-21] - 1) * 100
        if spy_chg_20 > 0 and vix_chg_20 > 0:
            div_score = clamp(min(spy_chg_20, vix_chg_20) * 10)
        elif spy_chg_20 < 0 and vix_chg_20 < 0:
            div_score = clamp(max(spy_chg_20, vix_chg_20) * 10)
        results["spy_20d_chg"] = spy_chg_20
        results["vix_20d_chg"] = vix_chg_20
    results["divergence_score"] = div_score

    # --- Composite ---
    composite = (
        vix_score * 0.30
        + term_score * 0.20
        + div_score * 0.10
        + pc_score * 0.25
        + skew_score * 0.15
    )
    results["composite"] = clamp(composite)
    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def fmt(val, decimals=2, pct=False):
    if val is None:
        return "N/A"
    if isinstance(val, float) and np.isnan(val):
        return "N/A"
    s = f"{val:.{decimals}f}"
    if pct:
        s += "%"
    return s


def score_bar(score: float, width: int = 30) -> Text:
    normalized = (score + 100) / 200
    filled = int(normalized * width)
    bar = Text()
    bar.append("OS ", style="green")
    for i in range(width):
        if i < filled:
            if i < width * 0.3:
                bar.append("|", style="green")
            elif i < width * 0.7:
                bar.append("|", style="yellow")
            else:
                bar.append("|", style="red")
        else:
            bar.append(".", style="dim")
    bar.append(" OB", style="red")
    bar.append(f"  [{score:+.1f}]", style="bold")
    return bar


def build_dashboard(data: dict, of: dict, qa: dict, op: dict,
                     status_line: str = "") -> tuple[Group, float, str]:
    """Build a compact dashboard that fits in a single terminal screen."""
    mes_price = data.get("mes_price")
    spy_price = data.get("spy_price", 0)
    daily_info = data["daily_info"]
    prev_close = daily_info["prev_close"]

    # Use MES price for display if available
    display_price = mes_price if mes_price else spy_price
    daily_chg = (spy_price / prev_close - 1) * 100 if prev_close and not np.isnan(prev_close) else 0.0

    composite = clamp(
        of["composite"] * WEIGHTS["order_flow"]
        + qa["composite"] * WEIGHTS["quantitative"]
        + op["composite"] * WEIGHTS["options"]
    )
    label, color = signal_label(composite)

    # --- Single unified table ---
    t = Table(box=box.SIMPLE_HEAVY, padding=(0, 1), expand=True, show_header=True)
    t.add_column("Indicator", style="bold", ratio=3)
    t.add_column("Value", justify="right", ratio=2)
    t.add_column("Score", justify="right", ratio=1)
    t.add_column("Sig", ratio=1)

    def add_row(name, val, sc):
        if sc is not None:
            lbl, clr = signal_label(sc)
            t.add_row(name, val, f"{sc:+.1f}", f"[{clr}]{lbl.split()[0]}[/]")
        else:
            t.add_row(name, val, "[dim]--[/]", "")

    # Order Flow section
    _, of_c = signal_label(of["composite"])
    t.add_row(f"[bold magenta]ORDER FLOW[/] [dim](Rithmic ticks)[/]", "", f"[bold]{of['composite']:+.1f}[/]",
              f"[{of_c}]{signal_label(of['composite'])[0].split()[0]}[/]")
    add_row("  VWAP Dev", fmt(of["vwap_dev_pct"], pct=True), of["vwap_score"])
    add_row("  Cum Delta (real)", fmt(of["cum_delta"]), of["cum_delta_score"])
    add_row("  OBV Diverg 2h", fmt(of["obv_divergence"], decimals=4), of["obv_score"])
    add_row("  MFI 70min", fmt(of["mfi"]), of["mfi_score"])
    add_row("  Vol Surge", f"{fmt(of['vol_z'], pct=True)}", of["climax_score"])
    add_row("  Buy Pressure", f"{of['buy_pressure']:+.2f}", of["pressure_score"])

    # Quantitative section
    _, qa_c = signal_label(qa["composite"])
    t.add_row(f"[bold blue]QUANTITATIVE[/] [dim](intraday)[/]", "", f"[bold]{qa['composite']:+.1f}[/]",
              f"[{qa_c}]{signal_label(qa['composite'])[0].split()[0]}[/]")
    add_row("  RSI(9) / RSI(3)", f"{fmt(qa['rsi_9'])} / {fmt(qa['rsi_3'])}", qa["rsi_score"])
    add_row("  BB Z (5m)", fmt(qa["bb_zscore"]), qa["bb_score"])
    add_row("  Stoch %K/%D (5m)", f"{fmt(qa['stoch_k'])}/{fmt(qa['stoch_d'])}", qa["stoch_score"])
    mom_1h = qa.get("mom_1h", 0)
    mom_3h = qa.get("mom_3h", 0)
    add_row("  Mom 1h/3h", f"{fmt(mom_1h, pct=True)}/{fmt(mom_3h, pct=True)}", qa["mom_score"])
    add_row("  VWAP Mean-Rev", fmt(qa["mr_zscore"]), qa["mr_score"])

    # Options section
    _, op_c = signal_label(op["composite"])
    t.add_row(f"[bold yellow]OPTIONS[/]", "", f"[bold]{op['composite']:+.1f}[/]",
              f"[{op_c}]{signal_label(op['composite'])[0].split()[0]}[/]")
    add_row("  VIX", f"{fmt(op['vix'])} (P{op['vix_pctile']:.0f})", op["vix_score"])
    add_row("  Term Structure", op.get("term_structure", "N/A"), op["term_score"])
    add_row("  P/C Ratio", fmt(op["pc_vol_ratio"]), op["pc_score"])
    if op.get("iv_skew_pct") is not None:
        add_row("  IV Skew", fmt(op["iv_skew_pct"], pct=True), op["skew_score"])
    add_row("  VIX/SPY Diverg", "", op["divergence_score"])

    # --- Interpretation ---
    if composite >= EXTREME_OB:
        interp = "[bold red]EXTREME OVERBOUGHT - High prob mean-reversion[/]"
    elif composite >= STRONG_OB:
        interp = "[red]STRONGLY OVERBOUGHT - Watch for exhaustion[/]"
    elif composite >= MILD_OB:
        interp = "[yellow]MILDLY OVERBOUGHT - Getting extended[/]"
    elif composite > NEUTRAL_LOW:
        interp = "[white]NEUTRAL - No strong signal[/]"
    elif composite > STRONG_OS:
        interp = "[cyan]MILDLY OVERSOLD - Early opportunity[/]"
    elif composite > EXTREME_OS:
        interp = "[green]STRONGLY OVERSOLD - Good mean-reversion setup[/]"
    else:
        interp = "[bold green]EXTREME OVERSOLD - Capitulation zone[/]"

    # Conviction
    signs = [np.sign(of["composite"]), np.sign(qa["composite"]), np.sign(op["composite"])]
    if all(s == signs[0] for s in signs) and signs[0] != 0:
        conviction = "[bold] | ALL PILLARS AGREE - HIGH CONVICTION[/]"
    else:
        conviction = ""

    # Regime info
    regime_info = f"  [dim]Hurst={qa['hurst']:.2f}({qa['regime'][:3]}) ADX={qa['adx']:.0f} RVOL={of['rvol']:.1f}x[/]"

    # Tick data info
    tick = data.get("tick_data", {})
    spread = tick.get("spread", np.nan)
    bid = tick.get("bid")
    ask = tick.get("ask")

    # --- Header ---
    header_parts = []
    if mes_price is not None:
        header_parts.append(("MES ", "bold white"))
        header_parts.append((f"${mes_price:.2f} ", "bold cyan"))
        if bid and ask:
            header_parts.append((f"({bid:.2f}×{ask:.2f}) ", "dim"))
    header_parts.append(("SPY ", "bold white"))
    header_parts.append((f"${spy_price:.2f} ", "bold cyan"))
    header_parts.append((f"({daily_chg:+.2f}%)", "green" if daily_chg >= 0 else "red"))
    header_parts.append(("  |  ", "dim"))
    header_parts.append((f"SIGNAL [{composite:+.1f}]", f"bold {color}"))

    header = Text.assemble(*header_parts)

    # Reference line
    ref_parts = []
    vwap_val = data.get("daily_vwap", np.nan)
    if not np.isnan(vwap_val):
        ref_parts.append((f"VWAP:${vwap_val:.2f}", "dim"))
        ref_parts.append(("  ", ""))
    if not np.isnan(spread):
        ref_parts.append((f"Spread:{spread:.2f}", "dim"))
        ref_parts.append(("  ", ""))
    vol = data.get("vol_today", 0)
    ref_parts.append((f"Vol:{vol:,}", "dim"))
    total_delta = tick.get("cum_delta", 0) if tick else 0
    ref_parts.append(("  ", ""))
    ref_parts.append((f"Δ:{total_delta:+,.0f}", "green" if total_delta >= 0 else "red"))

    ref_line = Text.assemble(*ref_parts) if ref_parts else None

    bar = score_bar(composite)
    status = Text(status_line, style="dim") if status_line else Text("")
    interp_line = Text.from_markup(f" {interp}{conviction}")
    regime_line = Text.from_markup(regime_info)

    parts = [header]
    if ref_line:
        parts.append(ref_line)
    parts.extend([bar, t, interp_line, regime_line, status])
    dashboard = Group(*parts)
    return dashboard, composite, label


# ---------------------------------------------------------------------------
# Async Monitor Loop
# ---------------------------------------------------------------------------

async def async_monitor(interval: int, notify_threshold: float,
                        notify_interval: int = DEFAULT_NOTIFY_INTERVAL) -> None:
    """Main async monitor loop with Rithmic streaming."""
    global NOTIFY_THRESHOLD
    NOTIFY_THRESHOLD = notify_threshold

    rithmic_feed = RithmicFeed()

    # --- Connect to Rithmic ---
    console.print("[bold cyan]MES SIGNAL ENGINE[/]  [dim](Rithmic + Alpaca + yfinance)[/]")
    console.print("[dim]Connecting to Rithmic...[/]")

    try:
        await rithmic_feed.connect()
        console.print(f"[bold green]✓ Connected to Rithmic[/]  MES contract: [cyan]{rithmic_feed.mes_symbol}[/]")
    except Exception as e:
        console.print(f"[bold red]✗ Rithmic connection failed: {e}[/]")
        console.print("[yellow]Falling back to Alpaca-only mode...[/]")
        # Continue without Rithmic — will use bar-based fallbacks
        rithmic_feed.connected = False

    # --- Fetch initial historical data ---
    console.print("[dim]Fetching historical bars...[/]")
    if rithmic_feed.connected:
        try:
            rithmic_feed.hist_bars_5m = await rithmic_feed.fetch_historical_bars(5, 3)
            console.print(f"[dim]  Got {len(rithmic_feed.hist_bars_5m)} 5-min bars from Rithmic[/]")
        except Exception as e:
            console.print(f"[dim red]  5m bars error: {e}[/]")
            rithmic_feed.hist_bars_5m = pd.DataFrame()

        try:
            rithmic_feed.hist_bars_daily = await rithmic_feed.fetch_daily_bars(365)
            console.print(f"[dim]  Got {len(rithmic_feed.hist_bars_daily)} daily bars from Rithmic[/]")
        except Exception as e:
            console.print(f"[dim red]  Daily bars error: {e}[/]")
            rithmic_feed.hist_bars_daily = pd.DataFrame()

    # Fallback to Alpaca SPY bars if Rithmic has no data
    if rithmic_feed.hist_bars_5m is None or rithmic_feed.hist_bars_5m.empty:
        console.print("[dim]  Fetching SPY 5-min bars from Alpaca (fallback)...[/]")
        rithmic_feed.hist_bars_5m = fetch_alpaca_bars("SPY", "5Min", 3)
        if not rithmic_feed.hist_bars_5m.empty:
            # Scale SPY prices to MES range (MES ≈ SPY × 10)
            for col in ["Open", "High", "Low", "Close", "VWAP"]:
                if col in rithmic_feed.hist_bars_5m.columns:
                    rithmic_feed.hist_bars_5m[col] = rithmic_feed.hist_bars_5m[col] * 10
            console.print(f"[dim]  Got {len(rithmic_feed.hist_bars_5m)} 5-min bars from Alpaca[/]")

    if rithmic_feed.hist_bars_daily is None or rithmic_feed.hist_bars_daily.empty:
        console.print("[dim]  Fetching SPY daily bars from Alpaca (fallback)...[/]")
        rithmic_feed.hist_bars_daily = fetch_alpaca_daily_bars("SPY", 400)
        if not rithmic_feed.hist_bars_daily.empty:
            for col in ["Open", "High", "Low", "Close"]:
                if col in rithmic_feed.hist_bars_daily.columns:
                    rithmic_feed.hist_bars_daily[col] = rithmic_feed.hist_bars_daily[col] * 10
            console.print(f"[dim]  Got {len(rithmic_feed.hist_bars_daily)} daily bars from Alpaca[/]")

    # --- Get initial SPY price for options ---
    console.print("[dim]Fetching SPY snapshot for options...[/]")
    try:
        spy_snap = fetch_spy_snapshot()
        spy_price = spy_snap["latestTrade"]["p"]
    except Exception:
        spy_price = 560.0  # reasonable fallback

    # --- Initial options + VIX fetch ---
    console.print("[dim]Fetching options chain + VIX...[/]")
    try:
        opts_vix = fetch_options_and_vix(spy_price)
    except Exception as e:
        console.print(f"[dim red]  Options/VIX error: {e}[/]")
        opts_vix = {}

    console.print("[bold green]✓ Ready. Starting monitor...[/]")
    await asyncio.sleep(2)

    # --- Enter alt-screen ---
    sys.stdout.write("\033[?1049h")
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    last_label = None
    last_notify_time = 0.0
    scan_count = 0
    last_options_fetch = 0.0
    last_vix_fetch = 0.0
    last_hist_refresh = time.time()
    cached_options = opts_vix if "calls" in opts_vix else None
    cached_vix = opts_vix if "vix_hist" in opts_vix else None
    OPTIONS_VIX_REFRESH = 60
    HIST_REFRESH = 300  # refresh historical bars every 5 min

    try:
        while True:
            try:
                t0 = time.time()
                now = time.time()
                should_notify = (now - last_notify_time) >= notify_interval

                # Refresh options/VIX cache
                if (now - last_options_fetch) >= OPTIONS_VIX_REFRESH:
                    cached_options = None
                    cached_vix = None

                # Refresh historical bars periodically
                if (now - last_hist_refresh) >= HIST_REFRESH:
                    try:
                        if rithmic_feed.connected:
                            new_bars = await rithmic_feed.fetch_historical_bars(5, 3)
                            if not new_bars.empty:
                                rithmic_feed.hist_bars_5m = new_bars
                        if rithmic_feed.hist_bars_5m is None or rithmic_feed.hist_bars_5m.empty:
                            new_bars = fetch_alpaca_bars("SPY", "5Min", 3)
                            if not new_bars.empty:
                                for col in ["Open", "High", "Low", "Close", "VWAP"]:
                                    if col in new_bars.columns:
                                        new_bars[col] = new_bars[col] * 10
                                rithmic_feed.hist_bars_5m = new_bars
                        last_hist_refresh = now
                    except Exception:
                        pass

                # Fetch SPY snapshot (for options context + price)
                try:
                    spy_snap = fetch_spy_snapshot()
                    spy_price = spy_snap["latestTrade"]["p"]
                except Exception:
                    pass  # use last known spy_price

                # Fetch options + VIX as needed
                try:
                    fresh_opts_vix = fetch_options_and_vix(
                        spy_price,
                        cached_options=cached_options,
                        cached_vix=cached_vix,
                    )
                except Exception:
                    fresh_opts_vix = opts_vix  # use last known

                if cached_options is None:
                    cached_options = {k: fresh_opts_vix[k] for k in
                                      ["calls", "puts", "near_calls", "near_puts"]
                                      if k in fresh_opts_vix}
                    last_options_fetch = now
                if cached_vix is None:
                    cached_vix = {k: fresh_opts_vix[k] for k in
                                  ["vix_hist", "vix9d_hist", "vix3m_hist"]
                                  if k in fresh_opts_vix}
                    last_vix_fetch = now

                opts_vix = fresh_opts_vix

                # Build unified data dict
                data = build_data_dict(rithmic_feed, spy_price, opts_vix)

                # Run analysis
                of_results = analyze_order_flow(data)
                qa_results = analyze_quantitative(data)
                op_results = analyze_options(data)

                elapsed = time.time() - t0
                next_notify_in = max(0, notify_interval - (now - last_notify_time))
                next_opts_in = max(0, OPTIONS_VIX_REFRESH - (now - last_options_fetch))
                scan_count += 1

                tick_count = len(rithmic_feed.accumulator.ticks)
                tick_status = f"Ticks:{tick_count:,}" if rithmic_feed.connected else "Rithmic:OFF"

                status = (
                    f"Scan #{scan_count} ({elapsed:.1f}s)  |  {tick_status}  |  "
                    f"Opts:{int(next_opts_in)}s  |  "
                    f"Notify:{int(next_notify_in)}s  |  Ctrl+C to stop"
                )

                dashboard, composite, label = build_dashboard(
                    data, of_results, qa_results, op_results, status_line=status
                )

                # Render
                sys.stdout.write("\033[H\033[J")
                sys.stdout.flush()
                console.print(dashboard)

                # Notifications
                of_s = of_results["composite"]
                qa_s = qa_results["composite"]
                op_s = op_results["composite"]
                mes_p = data.get("mes_price")

                if should_notify and abs(composite) >= NOTIFY_THRESHOLD:
                    notify_signal(composite, spy_price, label, of_s, qa_s, op_s,
                                  mes_price=mes_p)
                    last_notify_time = time.time()

                if last_label is not None and label != last_label:
                    notify_signal(composite, spy_price, label, of_s, qa_s, op_s,
                                  mes_price=mes_p)
                    last_notify_time = time.time()
                last_label = label

                wait = max(0.5, interval - (time.time() - t0))
                await asyncio.sleep(wait)

            except KeyboardInterrupt:
                break
            except Exception as e:
                sys.stdout.write("\033[H\033[J")
                sys.stdout.flush()
                console.print(f"[bold red]Error: {e}. Retrying in {interval}s...[/]")
                await asyncio.sleep(interval)
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.write("\033[?1049l")
        sys.stdout.flush()
        console.print("[dim]Disconnecting from Rithmic...[/]")
        await rithmic_feed.disconnect()
        console.print("[bold]Monitor stopped.[/]")


async def async_once(notify: bool = True) -> float:
    """Run a single scan using Rithmic + Alpaca + yfinance."""
    rithmic_feed = RithmicFeed()

    console.print("[bold cyan]MES SIGNAL ENGINE[/]  [dim](Rithmic + Alpaca + yfinance)[/]")
    console.print("[dim]Connecting to Rithmic...[/]")

    try:
        await rithmic_feed.connect()
        console.print(f"[bold green]✓ Connected[/]  MES: [cyan]{rithmic_feed.mes_symbol}[/]")
    except Exception as e:
        console.print(f"[yellow]Rithmic unavailable ({e}), using bar fallbacks[/]")

    # Fetch historical data
    if rithmic_feed.connected:
        rithmic_feed.hist_bars_5m = await rithmic_feed.fetch_historical_bars(5, 3)
        rithmic_feed.hist_bars_daily = await rithmic_feed.fetch_daily_bars(365)
        # Wait briefly for some ticks to accumulate
        console.print("[dim]Collecting ticks for 10 seconds...[/]")
        await asyncio.sleep(10)

    # Alpaca fallback for bars
    if rithmic_feed.hist_bars_5m is None or rithmic_feed.hist_bars_5m.empty:
        rithmic_feed.hist_bars_5m = fetch_alpaca_bars("SPY", "5Min", 3)
        if not rithmic_feed.hist_bars_5m.empty:
            for col in ["Open", "High", "Low", "Close", "VWAP"]:
                if col in rithmic_feed.hist_bars_5m.columns:
                    rithmic_feed.hist_bars_5m[col] = rithmic_feed.hist_bars_5m[col] * 10
    if rithmic_feed.hist_bars_daily is None or rithmic_feed.hist_bars_daily.empty:
        rithmic_feed.hist_bars_daily = fetch_alpaca_daily_bars("SPY", 400)
        if not rithmic_feed.hist_bars_daily.empty:
            for col in ["Open", "High", "Low", "Close"]:
                if col in rithmic_feed.hist_bars_daily.columns:
                    rithmic_feed.hist_bars_daily[col] = rithmic_feed.hist_bars_daily[col] * 10

    # SPY + options + VIX
    spy_snap = fetch_spy_snapshot()
    spy_price = spy_snap["latestTrade"]["p"]
    opts_vix = fetch_options_and_vix(spy_price)

    data = build_data_dict(rithmic_feed, spy_price, opts_vix)
    of_results = analyze_order_flow(data)
    qa_results = analyze_quantitative(data)
    op_results = analyze_options(data)
    dashboard, composite, label = build_dashboard(data, of_results, qa_results, op_results)
    console.print(dashboard)

    if notify and abs(composite) >= NOTIFY_THRESHOLD:
        mes_p = data.get("mes_price")
        notify_signal(composite, spy_price, label,
                      of_results["composite"], qa_results["composite"], op_results["composite"],
                      mes_price=mes_p)

    await rithmic_feed.disconnect()
    return composite


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MES Signal Engine (Rithmic + Alpaca + yfinance)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python spy_signal.py                    # Monitor 5s cycles, notify every 15m\n"
            "  python spy_signal.py -n 300             # Notify every 5 min\n"
            "  python spy_signal.py -i 3 -n 600        # Scan 3s, notify every 10m\n"
            "  python spy_signal.py -t 40              # Only notify on strong+ signals\n"
            "  python spy_signal.py --once              # Single scan, then exit\n"
        ),
    )
    parser.add_argument("--once", action="store_true", help="Run a single scan then exit")
    parser.add_argument("--interval", "-i", type=int, default=DEFAULT_MONITOR_INTERVAL,
                        help=f"Seconds between scans (default: {DEFAULT_MONITOR_INTERVAL})")
    parser.add_argument("--notify-interval", "-n", type=int, default=DEFAULT_NOTIFY_INTERVAL,
                        help=f"Seconds between notifications (default: {DEFAULT_NOTIFY_INTERVAL})")
    parser.add_argument("--threshold", "-t", type=float, default=NOTIFY_THRESHOLD,
                        help=f"Min |score| to trigger notification (default: {NOTIFY_THRESHOLD})")
    parser.add_argument("--no-notify", action="store_true", help="Disable desktop notifications")
    args = parser.parse_args()

    if not RITHMIC_USER or not RITHMIC_PASS:
        console.print("[bold red]Set RITHMIC_USER and RITHMIC_PASSWORD in .env[/]")
        sys.exit(1)
    if not ALPACA_KEY or not ALPACA_SECRET:
        console.print("[bold red]Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env[/]")
        sys.exit(1)

    if args.once:
        asyncio.run(async_once(notify=not args.no_notify))
    else:
        asyncio.run(async_monitor(args.interval, args.threshold, args.notify_interval))


if __name__ == "__main__":
    main()
