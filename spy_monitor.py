#!/usr/bin/env python3
"""
ES Futures Active Monitor — Real-Time Move Detection + Catalyst Attribution
============================================================================
Tracks E-mini S&P 500 futures (ES) as the primary instrument. Detects significant
moves across multiple timeframes, cross-references correlated assets (SPY, oil,
bonds, VIX, dollar, gold, sector ETFs) to fingerprint the catalyst, and pulls
breaking news to explain what's driving the move.

GEX levels sourced from Menthor Q daily report (gex_levels.json) with Alpaca
options chain fallback.

Fires macOS desktop alerts with catalyst attribution on big moves.

Usage:
  python spy_monitor.py                  # Default: poll every 5s, alert on 0.3%+ moves
  python spy_monitor.py -i 3             # Poll every 3 seconds
  python spy_monitor.py --threshold 0.5  # Only alert on 0.5%+ moves
  python spy_monitor.py --no-notify      # No desktop notifications
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
MENTHORQ_USER = os.getenv("MENTHORQ_USER", "")
MENTHORQ_PASS = os.getenv("MENTHORQ_PASS", "")
ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}

console = Console()

# Move detection thresholds (percentage)
MOVE_THRESHOLDS = {
    "1m":  0.15,   # 0.15% in 1 minute = unusual
    "5m":  0.30,   # 0.30% in 5 minutes = notable
    "15m": 0.50,   # 0.50% in 15 minutes = significant
    "30m": 0.70,   # 0.70% in 30 minutes = big
    "1h":  1.00,   # 1.00% in 1 hour = major
}

# Primary instrument: ES (E-mini S&P 500 Futures) via yfinance
PRIMARY_TICKER = "ES=F"
PRIMARY_NAME = "E-mini S&P"

# Path to Menthor Q GEX levels file (update daily from their free report)
GEX_LEVELS_FILE = Path(__file__).parent / "gex_levels.json"

# Correlated assets to check for catalyst fingerprinting
CORRELATED_ASSETS = {
    # SPY — cash S&P 500 ETF (via Alpaca for speed)
    "SPY":   {"name": "SPY",         "category": "INDEX",  "inverse": False},
    # Oil — inflation / energy sector driver
    "CL=F":  {"name": "Crude Oil",   "category": "OIL",    "inverse": False},
    # Bonds — rate expectations
    "^TNX":  {"name": "10Y Yield",   "category": "YIELDS", "inverse": True},
    "^TYX":  {"name": "30Y Yield",   "category": "YIELDS", "inverse": True},
    "TLT":   {"name": "20Y+ Bonds",  "category": "BONDS",  "inverse": False},
    # Volatility
    "^VIX":  {"name": "VIX",         "category": "VIX",    "inverse": True},
    # Dollar
    "UUP":   {"name": "US Dollar",   "category": "DOLLAR", "inverse": True},
    # Gold — safe haven
    "GLD":   {"name": "Gold",        "category": "GOLD",   "inverse": False},
    # Sector ETFs for rotation detection
    "XLE":   {"name": "Energy",      "category": "SECTOR", "inverse": False},
    "XLF":   {"name": "Financials",  "category": "SECTOR", "inverse": False},
    "XLK":   {"name": "Tech",        "category": "SECTOR", "inverse": False},
    "XLV":   {"name": "Healthcare",  "category": "SECTOR", "inverse": False},
    "XLI":   {"name": "Industrials", "category": "SECTOR", "inverse": False},
    "XLRE":  {"name": "Real Estate", "category": "SECTOR", "inverse": False},
    "XLU":   {"name": "Utilities",   "category": "SECTOR", "inverse": False},
    "XLP":   {"name": "Staples",     "category": "SECTOR", "inverse": False},
    "XLY":   {"name": "Discretion.", "category": "SECTOR", "inverse": False},
    "XLC":   {"name": "Comms",       "category": "SECTOR", "inverse": False},
    "XLB":   {"name": "Materials",   "category": "SECTOR", "inverse": False},
}

DEFAULT_POLL_INTERVAL = 5
DEFAULT_MOVE_THRESHOLD = 0.30  # minimum % move to trigger alert
NEWS_POLL_INTERVAL = 30  # seconds between news polls
GEX_REFRESH_INTERVAL = 300  # seconds between GEX recalculations (5 min)
ARTICLE_FETCH_TIMEOUT = 8

# ---------------------------------------------------------------------------
# Alpaca helpers
# ---------------------------------------------------------------------------

def alpaca_get(path: str, params: dict = None, retries: int = 5) -> dict:
    url = f"{ALPACA_DATA_URL}{path}"
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=ALPACA_HEADERS, params=params or {}, timeout=12)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.RequestException) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # 1, 2, 4, 8, ...
                continue
    raise last_err


def fetch_es_price() -> dict:
    """Get current ES futures price via yfinance."""
    t = yf.Ticker(PRIMARY_TICKER)
    info = t.fast_info
    price = float(info.get("lastPrice", 0) or info.get("last_price", 0))
    prev_close = float(info.get("previousClose", 0) or info.get("previous_close", price))
    open_price = float(info.get("open", price))

    # If fast_info fails, fall back to history
    if price <= 0:
        hist = t.history(period="2d", interval="1m")
        if hist is not None and len(hist) > 0:
            hist = hist.dropna(subset=["Close"])
            if len(hist) > 0:
                price = float(hist["Close"].iloc[-1])
                open_price = float(hist["Open"].iloc[0])
                # Get prev day close
                daily = t.history(period="5d", interval="1d").dropna(subset=["Close"])
                if daily is not None and len(daily) >= 2:
                    prev_close = float(daily["Close"].iloc[-2])

    if price <= 0:
        raise ValueError(f"Could not fetch {PRIMARY_TICKER} price")

    return {
        "price": price,
        "vwap": 0,  # yfinance doesn't provide VWAP for futures
        "open": open_price,
        "high": 0,
        "low": 0,
        "volume": 0,
        "prev_close": prev_close if prev_close > 0 else price,
        "timestamp": datetime.now(timezone.utc),
    }


def fetch_spy_snapshot() -> dict | None:
    """Get SPY price from Alpaca (used as correlated asset)."""
    try:
        snap = alpaca_get("/v2/stocks/SPY/snapshot")
        return {
            "price": snap["latestTrade"]["p"],
            "vwap": snap["dailyBar"]["vw"],
            "open": snap["dailyBar"]["o"],
            "high": snap["dailyBar"]["h"],
            "low": snap["dailyBar"]["l"],
            "volume": snap["dailyBar"]["v"],
            "prev_close": snap["prevDailyBar"]["c"],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GEX (Gamma Exposure) Engine
# ---------------------------------------------------------------------------

@dataclass
class GEXLevel:
    """A key gamma exposure level."""
    strike: float
    net_gex: float  # positive = call-dominated, negative = put-dominated
    call_gex: float
    put_gex: float
    label: str = ""  # e.g., "CALL WALL", "PUT WALL", "ZERO GAMMA"


@dataclass
class GEXProfile:
    """Complete gamma exposure profile for SPY."""
    levels: list[GEXLevel]
    zero_gamma: float  # strike where net GEX flips sign
    call_wall: float   # strike with highest call gamma (resistance)
    put_wall: float    # strike with highest put gamma (support)
    max_gamma: float   # strike with highest total gamma
    net_gex_total: float  # positive = dealers long gamma (suppress vol), negative = short (amplify vol)
    regime: str        # "POSITIVE" (mean-reverting) or "NEGATIVE" (trending/volatile)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key_levels(self) -> list[tuple[float, str]]:
        """Return key levels sorted by strike for display."""
        seen = set()
        levels = []
        for l in self.levels:
            if l.label and l.strike > 0 and l.strike not in seen:
                levels.append((l.strike, l.label))
                seen.add(l.strike)
        # Add computed levels if not already labeled
        if self.put_wall > 0 and self.put_wall not in seen:
            levels.append((self.put_wall, "PUT WALL (support)"))
            seen.add(self.put_wall)
        if self.zero_gamma > 0 and self.zero_gamma not in seen:
            levels.append((self.zero_gamma, "ZERO GAMMA (flip)"))
            seen.add(self.zero_gamma)
        if self.call_wall > 0 and self.call_wall not in seen:
            levels.append((self.call_wall, "CALL WALL (resist)"))
            seen.add(self.call_wall)
        if self.max_gamma > 0 and self.max_gamma not in seen:
            levels.append((self.max_gamma, "MAX GAMMA"))
        levels.sort(key=lambda x: x[0])
        return levels


class GEXCalculator:
    """GEX levels from Menthor Q daily report (gex_levels.json) with Alpaca fallback.

    Key concepts:
    - CALL WALL / Call Resistance: highest call gamma above price (resistance)
    - PUT WALL / Put Support: highest put gamma below price (support)
    - HVL: High Volatility Level — transition between positive/negative gamma regimes
    - ZERO GAMMA: Price where net GEX flips sign
    - Positive net GEX = dealers suppress vol (mean-revert)
    - Negative net GEX = dealers amplify vol (trending)
    """

    def __init__(self):
        self.profile: GEXProfile | None = None
        self.last_calc: float = 0
        self.lock = threading.Lock()
        self.menthorq_data: dict = {}
        self.menthorq_loaded: bool = False
        self.menthorq_session: requests.Session | None = None
        self.menthorq_last_fetch: float = 0

    def _menthorq_login(self) -> requests.Session | None:
        """Login to Menthor Q and return an authenticated session."""
        if not MENTHORQ_USER or not MENTHORQ_PASS:
            return None
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            # Get login page for cookies/nonce
            login_page = session.get("https://menthorq.com/wp-login.php", timeout=10)
            soup = BeautifulSoup(login_page.text, "html.parser")
            form_data = {
                "log": MENTHORQ_USER,
                "pwd": MENTHORQ_PASS,
                "wp-submit": "Log In",
                "redirect_to": "https://menthorq.com/",
                "testcookie": "1",
            }
            form = soup.find("form", {"id": "loginform"})
            if form:
                for inp in form.find_all("input", {"type": "hidden"}):
                    name = inp.get("name")
                    if name:
                        form_data[name] = inp.get("value", "")

            resp = session.post(
                "https://menthorq.com/wp-login.php",
                data=form_data,
                timeout=15,
                allow_redirects=True,
            )
            if "wp-login.php" not in resp.url or resp.status_code == 200:
                self.menthorq_session = session
                return session
        except Exception:
            pass
        return None

    def _get_menthorq_nonce(self, session: requests.Session) -> str:
        """Get the QDataParams AJAX security nonce from the dashboard page."""
        try:
            resp = session.get(
                "https://menthorq.com/account/?action=data&type=dashboard&commands=futures&tickers=futures",
                timeout=15,
            )
            # Extract nonce specifically from QDataParams JS object
            m = re.search(r'var QDataParams\s*=\s*\{[^}]*"nonce"\s*:\s*"([a-f0-9]+)"', resp.text)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    def _fetch_menthorq_api(self) -> dict:
        """Fetch GEX levels from Menthor Q AJAX API (key_levels command)."""
        session = self.menthorq_session or self._menthorq_login()
        if not session:
            return {}

        nonce = self._get_menthorq_nonce(session)
        if not nonce:
            return {}

        data = {}
        # Use most recent weekday (Menthor Q only has data for trading days)
        now = datetime.now()
        day = now
        while day.weekday() >= 5:  # Sat=5, Sun=6
            day -= timedelta(days=1)
        today = day.strftime("%Y-%m-%d")

        ajax_headers = {"X-Requested-With": "XMLHttpRequest"}

        try:
            # Fetch key_levels for ES1! (fall back to previous trading days)
            raw = {}
            fetch_day = day
            for _ in range(5):
                resp = session.post(
                    "https://menthorq.com/wp-admin/admin-ajax.php",
                    data={
                        "action": "get_command",
                        "security": nonce,
                        "command_slug": "key_levels",
                        "date": fetch_day.strftime("%Y-%m-%d"),
                        "is_intraday": "false",
                        "ticker": "es1!",
                    },
                    headers=ajax_headers,
                    timeout=15,
                )
                result = resp.json()
                if isinstance(result, dict) and result.get("success"):
                    raw = result.get("data", {}).get("resource", {}).get("data", {})
                    break
                fetch_day -= timedelta(days=1)
                while fetch_day.weekday() >= 5:
                    fetch_day -= timedelta(days=1)
                if raw:
                    # Map Menthor Q field names to our format
                    field_map = {
                        "Call Resistance": "call_resistance",
                        "Put Support": "put_support",
                        "HVL": "hvl",
                        "High Vol Level": "hvl",
                        "Call Resistance 0DTE": "0dte_call_resistance",
                        "Put Support 0DTE": "0dte_put_support",
                        "1D Max.": "expected_move_high",
                        "1D Min.": "expected_move_low",
                    }
                    for mq_key, our_key in field_map.items():
                        val = raw.get(mq_key)
                        if val is not None:
                            try:
                                data[our_key] = float(str(val).replace(",", "").replace("M", "e6").replace("B", "e9").rstrip("%"))
                            except (ValueError, TypeError):
                                pass

            # Also fetch net GEX data for regime info
            resp2 = session.post(
                "https://menthorq.com/wp-admin/admin-ajax.php",
                data={
                    "action": "get_command",
                    "security": nonce,
                    "command_slug": "netgex",
                    "date": fetch_day.strftime("%Y-%m-%d"),
                    "is_intraday": "false",
                    "ticker": "es1!",
                },
                headers=ajax_headers,
                timeout=15,
            )
            result2 = resp2.json()
            if result2.get("success"):
                raw2 = result2.get("data", {}).get("resource", {}).get("data", {})
                if raw2:
                    top_strikes = raw2.get("Top Net GEX Strikes", [])
                    if top_strikes:
                        data["gex_levels"] = [{"strike": s, "label": "GEX"} for s in top_strikes]

        except Exception:
            pass

        # Save to file for caching (only if we got the main levels)
        has_main = any(data.get(k, 0) for k in ("call_resistance", "put_support", "hvl"))
        if data and has_main:
            data["_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            data["_source"] = "menthorq_api"
            try:
                GEX_LEVELS_FILE.write_text(json.dumps(data, indent=4))
            except Exception:
                pass
            self.menthorq_data = data
            self.menthorq_loaded = True

        return data

    def load_menthorq(self) -> dict:
        """Load GEX levels from local JSON file. Cron + API keep the file fresh."""
        now = time.time()

        # Return cached data if fresh (re-read file every 5 min to pick up cron updates)
        if now - self.menthorq_last_fetch < 300 and self.menthorq_data:
            return self.menthorq_data

        # Always try local JSON file first (fast, reliable)
        try:
            if GEX_LEVELS_FILE.exists():
                data = json.loads(GEX_LEVELS_FILE.read_text())
                has_data = any(
                    data.get(k, 0) > 0
                    for k in ("call_resistance", "put_support", "hvl", "zero_gamma")
                )
                if has_data:
                    self.menthorq_data = data
                    self.menthorq_loaded = True
                    self.menthorq_last_fetch = now
                    return data
        except Exception:
            pass

        # If file is empty/missing, try API fetch (once per day)
        if MENTHORQ_USER and MENTHORQ_PASS and (now - self.menthorq_last_fetch > 86400 or not self.menthorq_data):
            data = self._fetch_menthorq_api()
            if data:
                self.menthorq_last_fetch = now
                return data

        return {}

    def build_from_menthorq(self, price: float) -> GEXProfile | None:
        """Build GEX profile from Menthor Q data."""
        d = self.menthorq_data
        if not d:
            return None

        call_wall = d.get("call_resistance", 0) or 0
        put_wall = d.get("put_support", 0) or 0
        hvl = d.get("hvl", 0) or 0
        zero_gamma = d.get("zero_gamma", 0) or hvl  # HVL ≈ zero gamma
        regime_str = d.get("regime", "")

        # Determine regime from HVL position
        if regime_str:
            regime = regime_str.upper()
        elif hvl > 0:
            regime = "POSITIVE" if price > hvl else "NEGATIVE"
        elif zero_gamma > 0:
            regime = "POSITIVE" if price > zero_gamma else "NEGATIVE"
        else:
            regime = "UNKNOWN"

        # Build levels list for display
        levels = []
        if put_wall > 0:
            levels.append(GEXLevel(strike=put_wall, net_gex=-1, call_gex=0, put_gex=-1, label="PUT WALL"))
        if zero_gamma > 0:
            levels.append(GEXLevel(strike=zero_gamma, net_gex=0, call_gex=0, put_gex=0, label="ZERO GAMMA"))
        if hvl > 0 and hvl != zero_gamma:
            levels.append(GEXLevel(strike=hvl, net_gex=0, call_gex=0, put_gex=0, label="HVL"))
        if call_wall > 0:
            levels.append(GEXLevel(strike=call_wall, net_gex=1, call_gex=1, put_gex=0, label="CALL WALL"))

        # Add 0DTE levels if available
        dte0_call = d.get("0dte_call_resistance", 0) or 0
        dte0_put = d.get("0dte_put_support", 0) or 0
        if dte0_call > 0 and dte0_call != call_wall:
            levels.append(GEXLevel(strike=dte0_call, net_gex=1, call_gex=1, put_gex=0, label="0DTE CALL"))
        if dte0_put > 0 and dte0_put != put_wall:
            levels.append(GEXLevel(strike=dte0_put, net_gex=-1, call_gex=0, put_gex=-1, label="0DTE PUT"))

        # Add extra GEX levels from the array
        for gl in d.get("gex_levels", []):
            if isinstance(gl, dict):
                s = gl.get("strike", 0)
                lbl = gl.get("label", "GEX")
                if s > 0:
                    levels.append(GEXLevel(strike=s, net_gex=0, call_gex=0, put_gex=0, label=lbl))
            elif isinstance(gl, (int, float)) and gl > 0:
                levels.append(GEXLevel(strike=gl, net_gex=0, call_gex=0, put_gex=0, label="GEX"))

        levels.sort(key=lambda l: l.strike)

        return GEXProfile(
            levels=levels,
            zero_gamma=zero_gamma,
            call_wall=call_wall,
            put_wall=put_wall,
            max_gamma=call_wall,  # Best approximation from Menthor Q data
            net_gex_total=1 if regime == "POSITIVE" else -1,
            regime=regime if regime in ("POSITIVE", "NEGATIVE") else "POSITIVE",
        )

    def calculate(self, price: float) -> GEXProfile | None:
        """Load GEX from Menthor Q file, fall back to Alpaca options chain."""
        now = time.time()
        if self.profile and (now - self.last_calc) < GEX_REFRESH_INTERVAL:
            with self.lock:
                return self.profile

        # Try Menthor Q file first (reload each time to pick up manual updates)
        mq_data = self.load_menthorq()
        if mq_data:
            profile = self.build_from_menthorq(price)
            if profile:
                with self.lock:
                    self.profile = profile
                    self.last_calc = now
                return profile

        # Fallback: calculate from Alpaca options chain
        try:
            today = datetime.now().date()
            # Capture 0-7 DTE (weekly gamma) + 7-45 DTE (monthly gamma)
            # Near-term expirations carry the most gamma impact
            exp_near = today.strftime("%Y-%m-%d")
            exp_far = (today + timedelta(days=45)).strftime("%Y-%m-%d")
            # Wide strike range: ±10% to catch all meaningful gamma
            strike_lo = round(price * 0.90)
            strike_hi = round(price * 1.10)

            # Fetch calls and puts from Alpaca (paginate fully)
            call_data = self._fetch_chain("SPY", "call", strike_lo, strike_hi, exp_near, exp_far)
            put_data = self._fetch_chain("SPY", "put", strike_lo, strike_hi, exp_near, exp_far)

            if not call_data and not put_data:
                return self.profile  # Keep old profile

            # Calculate per-strike GEX, aggregating across all expirations
            strike_gex: dict[float, dict] = {}

            for sym, snap in call_data.items():
                strike = self._parse_strike(sym)
                if strike is None:
                    continue
                greeks = snap.get("greeks") or {}
                gamma = greeks.get("gamma") or 0
                oi = snap.get("openInterest") or 0
                if gamma <= 0 or oi <= 0:
                    continue
                # GEX = Gamma × OI × 100 (multiplier) × Spot
                # Calls: dealers are long gamma (bought by customers, dealers delta-hedge)
                call_gex = gamma * oi * 100 * price
                if strike not in strike_gex:
                    strike_gex[strike] = {"call_gex": 0, "put_gex": 0, "call_oi": 0, "put_oi": 0}
                strike_gex[strike]["call_gex"] += call_gex
                strike_gex[strike]["call_oi"] += oi

            for sym, snap in put_data.items():
                strike = self._parse_strike(sym)
                if strike is None:
                    continue
                greeks = snap.get("greeks") or {}
                gamma = greeks.get("gamma") or 0
                oi = snap.get("openInterest") or 0
                if gamma <= 0 or oi <= 0:
                    continue
                # Puts: dealers are short gamma (negative contribution)
                put_gex = gamma * oi * 100 * price  # store as positive magnitude
                if strike not in strike_gex:
                    strike_gex[strike] = {"call_gex": 0, "put_gex": 0, "call_oi": 0, "put_oi": 0}
                strike_gex[strike]["put_gex"] += put_gex
                strike_gex[strike]["put_oi"] += oi

            if not strike_gex:
                return self.profile

            # Build levels with net GEX (call_gex positive, put_gex negative)
            levels = []
            for strike in sorted(strike_gex.keys()):
                g = strike_gex[strike]
                net = g["call_gex"] - g["put_gex"]  # calls add gamma, puts subtract
                levels.append(GEXLevel(
                    strike=strike, net_gex=net,
                    call_gex=g["call_gex"], put_gex=-g["put_gex"],  # put_gex stored as negative
                ))

            # CALL WALL: strike AT or ABOVE price with highest call gamma
            calls_above = [l for l in levels if l.strike >= price and l.call_gex > 0]
            call_wall = max(calls_above, key=lambda l: l.call_gex).strike if calls_above else 0
            # If nothing above, take the overall max call gamma
            if call_wall == 0:
                calls_any = [l for l in levels if l.call_gex > 0]
                call_wall = max(calls_any, key=lambda l: l.call_gex).strike if calls_any else 0

            # PUT WALL: strike AT or BELOW price with highest put gamma (most negative put_gex)
            puts_below = [l for l in levels if l.strike <= price and l.put_gex < 0]
            put_wall = min(puts_below, key=lambda l: l.put_gex).strike if puts_below else 0
            # If nothing below, take the overall max put gamma
            if put_wall == 0:
                puts_any = [l for l in levels if l.put_gex < 0]
                put_wall = min(puts_any, key=lambda l: l.put_gex).strike if puts_any else 0

            # MAX GAMMA: strike with highest absolute net GEX
            meaningful = [l for l in levels if abs(l.net_gex) > 0]
            max_gamma = max(meaningful, key=lambda l: abs(l.net_gex)).strike if meaningful else 0

            # ZERO GAMMA: interpolate where net GEX crosses zero
            # Search from below price upward for the most relevant crossing
            zero_gamma = 0
            crossings = []
            for i in range(len(levels) - 1):
                if levels[i].net_gex * levels[i + 1].net_gex < 0:
                    s1, g1 = levels[i].strike, levels[i].net_gex
                    s2, g2 = levels[i + 1].strike, levels[i + 1].net_gex
                    cross = s1 + (s2 - s1) * (-g1) / (g2 - g1)
                    crossings.append(cross)
            if crossings:
                # Pick the crossing closest to current price
                zero_gamma = min(crossings, key=lambda c: abs(c - price))

            # Total net GEX — determines regime
            net_total = sum(l.net_gex for l in levels)
            regime = "POSITIVE" if net_total > 0 else "NEGATIVE"

            # Label key levels
            for l in levels:
                if l.strike == call_wall:
                    l.label = "CALL WALL"
                elif l.strike == put_wall:
                    l.label = "PUT WALL"
                elif l.strike == max_gamma:
                    l.label = "MAX GAMMA"

            profile = GEXProfile(
                levels=levels,
                zero_gamma=round(zero_gamma, 1),
                call_wall=call_wall,
                put_wall=put_wall,
                max_gamma=max_gamma,
                net_gex_total=net_total,
                regime=regime,
            )

            with self.lock:
                self.profile = profile
                self.last_calc = now

            return profile

        except Exception:
            return self.profile  # Keep old profile on error

    def _fetch_chain(self, symbol: str, option_type: str, strike_lo: float,
                     strike_hi: float, exp_near: str, exp_far: str) -> dict:
        """Fetch options snapshots from Alpaca, paginating fully."""
        all_snapshots = {}
        page_token = None
        max_pages = 20  # Safety limit
        try:
            for _ in range(max_pages):
                params = {
                    "limit": 250,  # Max per page
                    "type": option_type,
                    "strike_price_gte": str(strike_lo),
                    "strike_price_lte": str(strike_hi),
                    "expiration_date_gte": exp_near,
                    "expiration_date_lte": exp_far,
                }
                if page_token:
                    params["page_token"] = page_token
                data = alpaca_get(f"/v1beta1/options/snapshots/{symbol}", params)
                all_snapshots.update(data.get("snapshots", {}))
                page_token = data.get("next_page_token")
                if not page_token:
                    break
        except Exception:
            pass
        return all_snapshots

    def _parse_strike(self, sym: str) -> float | None:
        """Parse strike from OCC symbol like SPY260410C00670000."""
        try:
            # Find C or P separator, strike is 8 digits after it
            match = re.search(r'[CP](\d{8})$', sym)
            if match:
                return int(match.group(1)) / 1000
            # Fallback: old method
            base = sym.replace("SPY", "")
            return int(base[7:]) / 1000
        except (ValueError, IndexError):
            return None

    def assess_move_risk(self, price: float) -> dict:
        """Assess whether a big move is likely based on GEX positioning."""
        with self.lock:
            profile = self.profile

        if not profile:
            return {"risk": "UNKNOWN", "details": "GEX data not loaded yet"}

        result = {
            "regime": profile.regime,
            "call_wall": profile.call_wall,
            "put_wall": profile.put_wall,
            "zero_gamma": profile.zero_gamma,
            "net_gex": profile.net_gex_total,
            "warnings": [],
        }

        # Distance to key levels
        if profile.call_wall > 0:
            dist_call_wall = (profile.call_wall - price) / price * 100
            result["dist_call_wall_pct"] = dist_call_wall
            if abs(dist_call_wall) < 0.3:
                result["warnings"].append(f"AT CALL WALL ${profile.call_wall:.0f} — strong resistance, likely rejection")
            elif dist_call_wall > 0 and dist_call_wall < 0.5:
                result["warnings"].append(f"Approaching call wall ${profile.call_wall:.0f} ({dist_call_wall:.1f}% away) — expect resistance")

        if profile.put_wall > 0:
            dist_put_wall = (price - profile.put_wall) / price * 100
            result["dist_put_wall_pct"] = dist_put_wall
            if abs(dist_put_wall) < 0.3:
                result["warnings"].append(f"AT PUT WALL ${profile.put_wall:.0f} — strong support, likely bounce")
            elif dist_put_wall > 0 and dist_put_wall < 0.5:
                result["warnings"].append(f"Approaching put wall ${profile.put_wall:.0f} ({dist_put_wall:.1f}% away) — expect support")

        if profile.zero_gamma > 0:
            dist_zero = (price - profile.zero_gamma) / price * 100
            result["dist_zero_gamma_pct"] = dist_zero
            if abs(dist_zero) < 0.2:
                result["warnings"].append(f"AT ZERO GAMMA ${profile.zero_gamma:.0f} — volatility regime flip zone!")

        # Regime-based warnings
        if profile.regime == "NEGATIVE":
            result["warnings"].append("NEGATIVE GAMMA — dealers amplifying moves, big swings likely")
            if price < profile.zero_gamma:
                result["warnings"].append("BELOW ZERO GAMMA — downside moves accelerate")
            result["risk"] = "HIGH"
        else:
            if not result["warnings"]:
                result["warnings"].append("Positive gamma — dealers suppressing volatility, mean-reversion likely")
            result["risk"] = "LOW"

        # If near any wall, override risk
        for w in result["warnings"]:
            if "AT CALL WALL" in w or "AT PUT WALL" in w or "AT ZERO GAMMA" in w:
                result["risk"] = "CRITICAL"
                break
            if "Approaching" in w:
                result["risk"] = "ELEVATED"

        return result


# ---------------------------------------------------------------------------
# Price History + Move Detection
# ---------------------------------------------------------------------------

@dataclass
class PricePoint:
    price: float
    timestamp: datetime
    volume: int = 0


@dataclass
class MoveEvent:
    """A detected significant price move."""
    timeframe: str
    pct_change: float
    start_price: float
    end_price: float
    start_time: datetime
    end_time: datetime
    direction: str = ""  # "UP" or "DOWN", set in __post_init__
    catalysts: list[str] = field(default_factory=list)
    correlated_moves: dict = field(default_factory=dict)
    news_matches: list = field(default_factory=list)
    fingerprint: str = ""

    def __post_init__(self):
        self.direction = "UP" if self.pct_change > 0 else "DOWN"
        ts = f"{self.timeframe}{self.start_time.isoformat()}{self.pct_change:.4f}"
        self.fingerprint = hashlib.md5(ts.encode()).hexdigest()[:10]


class PriceTracker:
    """Tracks SPY price history and detects significant moves."""

    def __init__(self, move_threshold_mult: float = 1.0):
        self.history: deque[PricePoint] = deque(maxlen=7200)  # ~10 hours at 5s
        self.move_threshold_mult = move_threshold_mult
        self.recent_moves: deque[MoveEvent] = deque(maxlen=50)
        self.alerted_fingerprints: set[str] = set()
        self.lock = threading.Lock()

    def add_price(self, price: float, volume: int = 0) -> list[MoveEvent]:
        """Add a price point and check for moves. Returns new move events."""
        now = datetime.now(timezone.utc)
        pp = PricePoint(price=price, timestamp=now, volume=volume)

        with self.lock:
            self.history.append(pp)

        if len(self.history) < 3:
            return []

        moves = []
        timeframe_seconds = {
            "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
        }

        for tf, seconds in timeframe_seconds.items():
            threshold = MOVE_THRESHOLDS[tf] * self.move_threshold_mult
            cutoff = now - timedelta(seconds=seconds)

            # Find the price at the start of this window
            start_pp = None
            with self.lock:
                for pp_hist in self.history:
                    if pp_hist.timestamp >= cutoff:
                        start_pp = pp_hist
                        break

            if start_pp is None:
                continue

            pct_change = (price - start_pp.price) / start_pp.price * 100

            if abs(pct_change) >= threshold:
                move = MoveEvent(
                    timeframe=tf,
                    pct_change=pct_change,
                    start_price=start_pp.price,
                    end_price=price,
                    start_time=start_pp.timestamp,
                    end_time=now,
                )
                # Don't re-alert on the same move
                if move.fingerprint not in self.alerted_fingerprints:
                    self.alerted_fingerprints.add(move.fingerprint)
                    moves.append(move)
                    with self.lock:
                        self.recent_moves.appendleft(move)

        # Expire old fingerprints (keep last hour)
        cutoff_time = now - timedelta(hours=1)
        self.alerted_fingerprints = {
            fp for fp in self.alerted_fingerprints
            if any(m.fingerprint == fp and m.end_time > cutoff_time
                   for m in self.recent_moves)
        }

        return moves

    def get_current_stats(self) -> dict:
        """Get rolling price change stats across timeframes."""
        if not self.history:
            return {}

        now = datetime.now(timezone.utc)
        current = self.history[-1].price
        stats = {"price": current}

        timeframe_seconds = {
            "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
        }

        for tf, seconds in timeframe_seconds.items():
            cutoff = now - timedelta(seconds=seconds)
            start_pp = None
            with self.lock:
                for pp in self.history:
                    if pp.timestamp >= cutoff:
                        start_pp = pp
                        break
            if start_pp:
                pct = (current - start_pp.price) / start_pp.price * 100
                stats[tf] = pct
            else:
                stats[tf] = 0.0

        return stats


# ---------------------------------------------------------------------------
# Cross-Asset Correlation Scanner
# ---------------------------------------------------------------------------

class AssetCorrelator:
    """Scans correlated assets to fingerprint what's driving SPY."""

    def __init__(self):
        self.cache: dict[str, dict] = {}
        self.last_fetch: float = 0
        self.fetch_interval = 15  # seconds between full scans
        self.lock = threading.Lock()

    def _fetch_ticker(self, ticker: str, info: dict) -> tuple[str, dict | None]:
        """Fetch a single ticker's data via yfinance. Returns (ticker, result_or_None)."""
        try:
            t = yf.Ticker(ticker)
            # Try 5d first, fall back to 1mo if needed
            for period in ("5d", "1mo"):
                try:
                    hist = t.history(period=period, interval="1d")
                    if hist is not None and len(hist) >= 1:
                        break
                except Exception:
                    hist = None
            if hist is None or len(hist) < 1:
                return ticker, None
            # Drop any rows with NaN close
            hist = hist.dropna(subset=["Close"])
            if len(hist) < 1:
                return ticker, None
            current = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current
            if np.isnan(current) or np.isnan(prev) or prev == 0:
                return ticker, None
            pct_change = (current - prev) / prev * 100
            if np.isnan(pct_change):
                return ticker, None
            return ticker, {
                **info,
                "price": current,
                "change_pct": pct_change,
                "prev": prev,
            }
        except Exception:
            return ticker, None

    def scan(self, force: bool = False) -> dict[str, dict]:
        """Fetch current prices and day changes for all correlated assets."""
        now = time.time()
        if not force and (now - self.last_fetch) < self.fetch_interval:
            with self.lock:
                return dict(self.cache)

        results = {}

        # Step 1: Fetch ALL ETFs via Alpaca first (fast + reliable)
        alpaca_tickers = {
            "QQQ": ("Nasdaq 100", "INDEX"),
            "IWM": ("Russell 2000", "INDEX"),
            "DIA": ("Dow 30", "INDEX"),
        }
        # Add all non-index, non-futures tickers from CORRELATED_ASSETS
        for ticker, info in CORRELATED_ASSETS.items():
            if not ticker.startswith("^") and "=" not in ticker:
                alpaca_tickers[ticker] = (info["name"], info["category"])

        def _fetch_alpaca(ticker, name, category):
            try:
                snap = alpaca_get(f"/v2/stocks/{ticker}/snapshot")
                price = snap["latestTrade"]["p"]
                prev = snap["prevDailyBar"]["c"]
                if price and prev and prev != 0:
                    pct = (price - prev) / prev * 100
                    info = CORRELATED_ASSETS.get(ticker, {"inverse": False})
                    return ticker, {
                        "name": name,
                        "category": category,
                        "price": price,
                        "change_pct": pct,
                        "prev": prev,
                        "inverse": info.get("inverse", False),
                    }
            except Exception:
                pass
            return ticker, None

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {
                pool.submit(_fetch_alpaca, t, n, c): t
                for t, (n, c) in alpaca_tickers.items()
            }
            for f in as_completed(futures, timeout=12):
                try:
                    ticker, result = f.result()
                    if result is not None:
                        results[ticker] = result
                except Exception:
                    pass

        # Step 2: yfinance only for indices/futures that Alpaca can't serve
        yf_tickers = {
            ticker: info for ticker, info in CORRELATED_ASSETS.items()
            if (ticker.startswith("^") or "=" in ticker) and ticker not in results
        }
        if yf_tickers:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {
                    pool.submit(self._fetch_ticker, ticker, info): ticker
                    for ticker, info in yf_tickers.items()
                }
                for f in as_completed(futures, timeout=15):
                    try:
                        ticker, result = f.result()
                        if result is not None:
                            results[ticker] = result
                    except Exception:
                        pass

        # Merge with previous cache — keep old values for tickers that failed this time
        with self.lock:
            for ticker, data in self.cache.items():
                if ticker not in results:
                    results[ticker] = data
            self.cache = results
            self.last_fetch = now

        return results

    def attribute_move(self, spy_change: float, timeframe: str = "session") -> list[str]:
        """Given a SPY move, identify the most likely drivers from correlated assets."""
        with self.lock:
            assets = dict(self.cache)

        if not assets:
            return []

        spy_dir = "up" if spy_change > 0 else "down"
        catalysts = []

        # Check oil — if oil moved big, it's often the catalyst
        for ticker in ["CL=F"]:
            if ticker in assets:
                oil = assets[ticker]
                oil_chg = oil["change_pct"]
                if abs(oil_chg) > 1.0:
                    if oil_chg > 1.5 and spy_change < 0:
                        catalysts.append(f"OIL SURGE ({oil_chg:+.1f}%) → inflation fears pressuring stocks")
                    elif oil_chg < -1.5 and spy_change > 0:
                        catalysts.append(f"OIL PULLBACK ({oil_chg:+.1f}%) → easing inflation pressure lifting stocks")
                    elif oil_chg > 1.0:
                        catalysts.append(f"Oil {oil_chg:+.1f}% — energy costs rising")
                    elif oil_chg < -1.0:
                        catalysts.append(f"Oil {oil_chg:+.1f}% — energy costs falling")

        # Check yields — if yields spike/drop, bonds are driving
        for ticker in ["^TNX"]:
            if ticker in assets:
                yld = assets[ticker]
                yld_chg = yld["change_pct"]
                if abs(yld_chg) > 1.0:
                    if yld_chg > 1.5 and spy_change < 0:
                        catalysts.append(f"YIELDS SURGING (10Y {yld_chg:+.1f}%) → higher rates pressuring equities")
                    elif yld_chg < -1.5 and spy_change > 0:
                        catalysts.append(f"YIELDS FALLING (10Y {yld_chg:+.1f}%) → rate relief lifting stocks")
                    else:
                        catalysts.append(f"10Y yield {yld_chg:+.1f}%")

        # Check VIX — fear gauge
        if "^VIX" in assets:
            vix = assets["^VIX"]
            vix_chg = vix["change_pct"]
            if abs(vix_chg) > 5:
                if vix_chg > 10:
                    catalysts.append(f"VIX SPIKE ({vix_chg:+.1f}%) → fear surging")
                elif vix_chg < -5:
                    catalysts.append(f"VIX CRUSH ({vix_chg:+.1f}%) → volatility easing")
                elif vix_chg > 5:
                    catalysts.append(f"VIX up {vix_chg:+.1f}%")
                elif vix_chg < -5:
                    catalysts.append(f"VIX down {vix_chg:+.1f}%")

        # Check dollar
        if "UUP" in assets:
            dxy = assets["UUP"]
            dxy_chg = dxy["change_pct"]
            if abs(dxy_chg) > 0.5:
                if dxy_chg > 0.5 and spy_change < 0:
                    catalysts.append(f"Dollar strengthening ({dxy_chg:+.1f}%) → headwind for multinationals")
                elif dxy_chg < -0.5 and spy_change > 0:
                    catalysts.append(f"Dollar weakening ({dxy_chg:+.1f}%) → tailwind for earnings")

        # Check gold — safe haven flows
        if "GLD" in assets:
            gold = assets["GLD"]
            gold_chg = gold["change_pct"]
            if abs(gold_chg) > 1.0:
                if gold_chg > 1.0 and spy_change < 0:
                    catalysts.append(f"Gold surging ({gold_chg:+.1f}%) → risk-off / safe haven flows")
                elif gold_chg < -1.0 and spy_change > 0:
                    catalysts.append(f"Gold falling ({gold_chg:+.1f}%) → risk-on rotation")

        # Sector analysis — find outliers
        sector_moves = {}
        for ticker, info in assets.items():
            if info.get("category") == "SECTOR":
                sector_moves[info["name"]] = info["change_pct"]

        if sector_moves:
            avg_sector = np.mean(list(sector_moves.values()))
            for name, chg in sorted(sector_moves.items(), key=lambda x: abs(x[1] - avg_sector), reverse=True):
                dev = chg - avg_sector
                if abs(dev) > 1.5:
                    direction = "leading" if dev > 0 else "lagging"
                    catalysts.append(f"{name} {direction} ({chg:+.1f}% vs avg {avg_sector:+.1f}%)")
                    break  # Just the biggest outlier

        # SPY vs ES divergence (ES is primary, SPY is correlated)
        if "SPY" in assets:
            spy = assets["SPY"]
            spy_chg = spy["change_pct"]
            if abs(spy_chg - spy_change) > 0.3:
                if spy_chg > spy_change + 0.3:
                    catalysts.append(f"SPY cash leading ({spy_chg:+.1f}% vs ES {spy_change:+.1f}%)")
                elif spy_chg < spy_change - 0.3:
                    catalysts.append(f"SPY cash lagging ({spy_chg:+.1f}% vs ES {spy_change:+.1f}%)")

        # Index divergence
        index_tickers = {"QQQ": "Nasdaq", "IWM": "Russell", "DIA": "Dow"}
        for ticker, name in index_tickers.items():
            if ticker in assets:
                idx = assets[ticker]
                idx_chg = idx["change_pct"]
                if abs(idx_chg - spy_change) > 0.5:
                    catalysts.append(f"{name} diverging ({idx_chg:+.1f}% vs SPY {spy_change:+.1f}%)")

        return catalysts


# ---------------------------------------------------------------------------
# News Scanner (adapted from news_alert.py)
# ---------------------------------------------------------------------------

# Impact scoring rules (subset of the most important from news_alert.py)
NEWS_RULES: list[tuple[str, int, str]] = [
    # Fed
    (r'\bfomc\b.*(statement|decision|minutes|rate)', 5, 'FED'),
    (r'\bpowell\b.*(speak|press|conference|testimony|remark|signal|warn|said)', 5, 'FED'),
    (r'\bfed\b.*(rate|cut|hike|hold|pause|pivot|hawkish|dovish)', 5, 'FED'),
    (r'\brate (cut|hike|decision)\b', 5, 'FED'),
    # Inflation
    (r'\bcpi\b', 5, 'CPI'),
    (r'\bpce\b.*(data|index|reading|price|core|rose|fell)', 5, 'PCE'),
    (r'\bcore (cpi|pce|inflation)\b', 5, 'CPI'),
    (r'\binflation\b.*(hot|cool|surprise|higher|lower|rose|fell|data|report)', 4, 'CPI'),
    # Jobs
    (r'\b(non-?farm|payrolls?)\b', 5, 'NFP'),
    (r'\bjobless claims\b', 5, 'CLAIMS'),
    (r'\bunemployment rate\b', 5, 'JOBS'),
    # GDP
    (r'\bgdp\b.*(grew|shrank|contract|expand|revised|surprise|data|report)', 4, 'GDP'),
    (r'\brecession\b.*(official|confirm|enter|signal|warn|risk)', 4, 'GDP'),
    # Tariffs
    (r'\btariff\b.*(impose|announce|raise|increase|threat|retali|escalat|delay|pause|exempt)', 4, 'TARIFF'),
    (r'\btrade war\b.*(escalat|intensif|deal|agreement)', 4, 'TARIFF'),
    # Oil
    (r'\b(crude|oil|brent|wti)\b.*(surge|spike|crash|plunge|soar|jump|pull)', 4, 'OIL'),
    (r'\bopec\b.*(cut|boost|output|production|agree|surprise)', 4, 'OIL'),
    # Market action
    (r'\b(s&p 500|s&p500|spx|spy)\b.*(correction|bear|record|crash|rally|halt|selloff)', 4, 'SPY'),
    (r'\bfutures?\b.*(drop|crash|surge|rally|plunge|tumble|soar|limit|gap)', 4, 'FUTURES'),
    (r'\bvix\b.*(spike|surge|jump|above|hit|soar)', 4, 'VIX'),
    # Yields / bonds
    (r'\b(10[- ]?year|treasury) yield\b.*(surge|spike|plunge|jump|hit|record)', 4, 'YIELDS'),
    (r'\byield curve\b.*(invert|steepen|flatten)', 4, 'YIELDS'),
    # Crisis
    (r'\b(circuit breaker|market halt|trading halt|flash crash)\b', 5, 'CRISIS'),
    (r'\b(bank run|bank failure|systemic risk|contagion)\b', 5, 'CRISIS'),
    # Geopolitics
    (r'\b(military strike|air strike|missile)\b.*(iran|israel|china|taiwan|russia)', 4, 'GEO'),
    # Mega-cap earnings
    (r'\b(apple|aapl|microsoft|msft|nvidia|nvda|amazon|amzn|alphabet|google|goog|meta|tesla|tsla)\b.*(earn|revenue|beat|miss|guidance|results|profit|eps)', 5, 'MEGA_EARN'),
    # Retail / ISM
    (r'\b(retail sales|consumer spending)\b.*(fell|rose|drop|surge|miss|beat)', 4, 'RETAIL'),
    (r'\bism\b.*(manufacturing|services|contract|expand|pmi)', 4, 'ISM'),
    # Generic market / equities
    (r'\b(stock market|wall street)\b.*(crash|rout|plunge|selloff|rally|surge|rebound)', 3, 'MARKET'),
    (r'\b(dow|nasdaq|russell)\b.*(drop|crash|surge|rally|record)', 3, 'INDEX'),
]

_COMPILED_NEWS = [(re.compile(p, re.IGNORECASE), w, c) for p, w, c in NEWS_RULES]

NOISE_PATTERNS = re.compile(
    r'\b(movie|film|tv show|album|song|concert|recipe|celebrity|grammy|oscar|'
    r'nfl|nba|mlb|nhl|super bowl|olympic|wedding|divorce|baby|pet|horoscope|'
    r'crypto|bitcoin|ethereum|solana|meme coin|nft)\b', re.IGNORECASE
)

RSS_FEEDS = [
    ("CNBC Top",            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC Economy",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("MarketWatch Top",     "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("MarketWatch Markets", "https://feeds.marketwatch.com/marketwatch/marketpulse"),
    ("Yahoo Finance",       "https://finance.yahoo.com/news/rssindex"),
    ("Investing.com",       "https://www.investing.com/rss/news.rss"),
    ("Google SPY/Futures",  "https://news.google.com/rss/search?q=%22SPY%22+OR+%22S%26P+500+futures%22+OR+%22stock+futures%22+OR+%22FOMC%22&hl=en-US&gl=US&ceid=US:en"),
    ("Google Macro",        "https://news.google.com/rss/search?q=%22CPI+report%22+OR+%22jobs+report%22+OR+%22nonfarm+payrolls%22+OR+%22Powell%22+OR+%22rate+decision%22&when=1d&hl=en-US&gl=US&ceid=US:en"),
    ("ZeroHedge",           "https://feeds.feedburner.com/zerohedge/feed"),
    ("Fed Releases",        "https://www.federalreserve.gov/feeds/press_all.xml"),
]


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published: datetime
    score: int = 0
    categories: list[str] = field(default_factory=list)
    fingerprint: str = ""
    description: str = ""
    details: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.fingerprint:
            clean = re.sub(r'\s*[-–—|]\s*[\w\s.]+$', '', self.title)
            norm = re.sub(r'\s+', ' ', clean.lower().strip())
            self.fingerprint = hashlib.md5(norm.encode()).hexdigest()[:12]


# Article detail extraction — scoring patterns
_DETAIL_RULES: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r'\b\d+\.?\d*\s*(%|percent|basis points?|bps)\b', re.I), 3.0, 'DATA'),
    (re.compile(r'\$[\d,.]+\s*(billion|trillion|million|B|T|M)\b', re.I), 3.0, 'DATA'),
    (re.compile(r'\b(beat|miss|exceeded|fell short|above|below|versus|vs\.?|estimate|consensus|expected)\b', re.I), 3.0, 'SURPRISE'),
    (re.compile(r'\b(surprise|unexpected|shock|hotter.than|cooler.than)\b', re.I), 3.5, 'SURPRISE'),
    (re.compile(r'\b(first time since|highest since|lowest since|record high|record low)\b', re.I), 3.0, 'SURPRISE'),
    (re.compile(r'\b(guidance|outlook|forecast|dot plot|rate path|forward)\b', re.I), 2.5, 'OUTLOOK'),
    (re.compile(r'\bfutures?\b.*(rose|fell|drop|jump|surge|rally|tumble|plunge)', re.I), 3.5, 'REACTION'),
    (re.compile(r'\b(spy|spx|s&p)\b.*(rose|fell|drop|jump|surge|up|down)', re.I), 3.5, 'REACTION'),
    (re.compile(r'\b(yield|10[- ]?year|treasury)\b.*(rose|fell|drop|jump|surge|spike)', re.I), 2.5, 'REACTION'),
    (re.compile(r'\bvix\b.*(rose|fell|spike|surge|jump)', re.I), 2.5, 'REACTION'),
    (re.compile(r'\b(crude|oil|brent|wti)\b.*(surge|spike|crash|plunge|pull|soar|jump)', re.I), 2.5, 'OIL'),
    (re.compile(r'\btariff\b.*(impose|raise|increase|new|sweep|reciprocal|retali|escalat|delay|pause)', re.I), 2.5, 'TARIFF'),
    (re.compile(r'"[^"]{25,200}"', re.I), 2.0, 'QUOTE'),
    (re.compile(r'\b(powell|yellen|waller|bostic|kashkari|williams|goolsbee)\b', re.I), 2.0, 'FED'),
]

_JUNK_SENTENCE = re.compile(
    r'\b(click here|subscribe|sign up|read more|newsletter|cookie|privacy policy|'
    r'download the app|share this|tweet this|facebook|trending now)\b', re.IGNORECASE
)

TAG_COLORS = {
    'DATA': 'bright_cyan', 'SURPRISE': 'bright_green', 'OUTLOOK': 'bright_magenta',
    'REACTION': 'bright_yellow', 'OIL': 'dark_orange', 'TARIFF': 'bright_red',
    'QUOTE': 'white', 'FED': 'bright_blue',
}


def extract_article_details(html: str, title: str, max_lines: int = 4) -> list[str]:
    """Extract key market-moving details from article HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'aside', 'header',
                              'form', 'iframe', 'noscript', 'svg', 'button']):
        tag.decompose()

    article = (
        soup.find('article') or
        soup.find('div', class_=re.compile(r'article|story|content|post-body|entry-content', re.I)) or
        soup.body
    )
    if not article:
        return []

    paragraphs = article.find_all('p')
    full_text = ' '.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 25)
    if len(full_text) < 100:
        return []

    full_text = re.sub(r'(?<=[A-Z])\.(?=[A-Z])', '_DOT_', full_text)
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    sentences = [s.replace('_DOT_', '.').strip() for s in sentences]
    sentences = [s for s in sentences if 35 < len(s) < 400]

    if not sentences:
        return []

    title_words = set(re.findall(r'\b\w{4,}\b', title.lower()))
    scored = []

    for sent in sentences:
        if _JUNK_SENTENCE.search(sent):
            continue
        total = 0.0
        best_tag = ''
        best_score = 0.0
        for pattern, weight, tag in _DETAIL_RULES:
            if pattern.search(sent):
                total += weight
                if weight > best_score:
                    best_score = weight
                    best_tag = tag
        sent_words = set(re.findall(r'\b\w{4,}\b', sent.lower()))
        overlap = len(title_words & sent_words)
        if overlap >= 2:
            total += 0.3 * overlap
        if overlap >= len(title_words) * 0.8 and len(title_words) > 3:
            total -= 3.0
        if total >= 2.0:
            scored.append((total, best_tag, sent))

    scored.sort(key=lambda x: -x[0])

    details = []
    seen_content = []
    for score, tag, sent in scored:
        if len(details) >= max_lines:
            break
        sent_words = set(re.findall(r'\b\w{4,}\b', sent.lower()))
        is_dup = any(len(sent_words & prev) > len(sent_words) * 0.6 for prev in seen_content)
        if is_dup:
            continue
        seen_content.append(sent_words)
        if len(sent) > 200:
            sent = sent[:197] + "..."
        details.append(f"[{tag or 'DATA'}] {sent}")

    return details


class NewsScanner:
    """Scans RSS feeds for SPY-moving news."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) SPY-Monitor/1.0"
        })
        self.seen: dict[str, float] = {}
        self.items: list[NewsItem] = []
        self.lock = threading.Lock()
        self.last_poll: float = 0
        self.detail_cache: dict[str, list[str]] = {}
        self.detail_pending: set[str] = set()

    def score_headline(self, text: str) -> tuple[int, list[str]]:
        if NOISE_PATTERNS.search(text):
            return 0, []
        max_score = 0
        cats = []
        for pattern, weight, category in _COMPILED_NEWS:
            if pattern.search(text):
                if weight > max_score:
                    max_score = weight
                cats.append(category)
        return max_score, list(dict.fromkeys(cats))

    def parse_feed(self, name: str, url: str) -> list[NewsItem]:
        items = []
        try:
            resp = self.session.get(url, timeout=8)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('.//item')
            if not entries:
                entries = root.findall('.//atom:entry', ns)

            for entry in entries:
                title_el = entry.find('title')
                if title_el is None:
                    title_el = entry.find('atom:title', ns)
                if title_el is None or not title_el.text:
                    continue
                title = title_el.text.strip()

                link_el = entry.find('link')
                if link_el is None:
                    link_el = entry.find('atom:link', ns)
                link = ""
                if link_el is not None:
                    link = link_el.text or link_el.get('href', '') or ""

                pub_el = entry.find('pubDate')
                if pub_el is None:
                    pub_el = entry.find('atom:published', ns)
                if pub_el is None:
                    pub_el = entry.find('atom:updated', ns)
                pub_dt = datetime.now(timezone.utc)
                if pub_el is not None and pub_el.text:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub_el.text)
                    except Exception:
                        pass

                desc_el = entry.find('description')
                if desc_el is None:
                    desc_el = entry.find('atom:summary', ns)
                desc_text = ""
                combined = title
                if desc_el is not None and desc_el.text:
                    desc_text = BeautifulSoup(desc_el.text, 'html.parser').get_text()
                    combined = f"{title} {desc_text[:500]}"

                score, cats = self.score_headline(combined)
                if score >= 2:
                    items.append(NewsItem(
                        title=title, source=name, url=link.strip(),
                        published=pub_dt, score=score, categories=cats,
                        description=desc_text[:500],
                    ))
        except Exception:
            pass
        return items

    def poll(self) -> list[NewsItem]:
        """Poll all feeds, return new high-impact items."""
        results: list[list[NewsItem]] = [[] for _ in RSS_FEEDS]
        threads = []

        def fetch(idx, name, url):
            results[idx] = self.parse_feed(name, url)

        for i, (name, url) in enumerate(RSS_FEEDS):
            t = threading.Thread(target=fetch, args=(i, name, url), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=10)

        now = time.time()
        # Expire old seen items
        expired = [k for k, v in self.seen.items() if now - v > 3600 * 6]
        for k in expired:
            del self.seen[k]

        new_items = []
        for batch in results:
            for item in batch:
                if item.fingerprint not in self.seen and item.score >= 2:
                    self.seen[item.fingerprint] = now
                    new_items.append(item)

        # Dedup by title similarity
        deduped = []
        for item in sorted(new_items, key=lambda x: (-x.score, -x.published.timestamp())):
            title_words = set(re.findall(r'\b\w{4,}\b', item.title.lower()))
            is_dup = False
            for existing in deduped:
                existing_words = set(re.findall(r'\b\w{4,}\b', existing.title.lower()))
                overlap = len(title_words & existing_words)
                min_len = min(len(title_words), len(existing_words))
                if min_len > 0 and overlap / min_len > 0.6:
                    is_dup = True
                    break
            if not is_dup:
                deduped.append(item)

        # Background fetch article details for high-impact items
        to_fetch = [i for i in deduped if i.score >= 3 and i.url
                    and 'news.google.com' not in i.url
                    and i.fingerprint not in self.detail_cache
                    and i.fingerprint not in self.detail_pending]
        if to_fetch:
            t = threading.Thread(target=self._fetch_details_batch, args=(to_fetch,), daemon=True)
            t.start()

        with self.lock:
            combined = deduped + [i for i in self.items if i.fingerprint not in {n.fingerprint for n in deduped}]
            combined.sort(key=lambda x: (-x.score, -x.published.timestamp()))
            # Dedup combined
            final = []
            for item in combined:
                title_words = set(re.findall(r'\b\w{4,}\b', item.title.lower()))
                is_dup = False
                for existing in final:
                    existing_words = set(re.findall(r'\b\w{4,}\b', existing.title.lower()))
                    overlap = len(title_words & existing_words)
                    min_len = min(len(title_words), len(existing_words))
                    if min_len > 0 and overlap / min_len > 0.6:
                        is_dup = True
                        break
                if not is_dup:
                    final.append(item)
            self.items = final[:30]

        self.last_poll = now
        return deduped

    def _fetch_details_batch(self, items: list[NewsItem]):
        for item in items[:10]:
            self.detail_pending.add(item.fingerprint)

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self._fetch_one, item): item for item in items[:10]}
            for f in as_completed(futures, timeout=ARTICLE_FETCH_TIMEOUT + 5):
                try:
                    f.result()
                except Exception:
                    pass

    def _fetch_one(self, item: NewsItem):
        try:
            if 'news.google.com' in (item.url or ''):
                return
            resp = self.session.get(item.url, timeout=ARTICLE_FETCH_TIMEOUT, allow_redirects=True)
            if resp.status_code == 200:
                details = extract_article_details(resp.text, item.title)
                if details:
                    self.detail_cache[item.fingerprint] = details
                    self.detail_pending.discard(item.fingerprint)
                    return
            # Fallback to description
            if item.description and len(item.description) > 50:
                self.detail_cache[item.fingerprint] = [f"[DATA] {item.description[:200]}"]
        except Exception:
            pass
        finally:
            self.detail_pending.discard(item.fingerprint)

    def match_news_to_move(self, move: MoveEvent) -> list[NewsItem]:
        """Find news items that likely explain a move."""
        window_start = move.start_time - timedelta(minutes=15)
        window_end = move.end_time + timedelta(minutes=5)

        matches = []
        with self.lock:
            for item in self.items:
                pub = item.published if item.published.tzinfo else item.published.replace(tzinfo=timezone.utc)
                if window_start <= pub <= window_end and item.score >= 3:
                    matches.append(item)

        # Also match by category correlation
        move_dir = "DOWN" if move.pct_change < 0 else "UP"
        cat_matches = []
        with self.lock:
            for item in self.items:
                if item.score >= 4:
                    # High-impact items from last hour are always relevant
                    pub = item.published if item.published.tzinfo else item.published.replace(tzinfo=timezone.utc)
                    age_mins = (datetime.now(timezone.utc) - pub).total_seconds() / 60
                    if age_mins <= 60:
                        cat_matches.append(item)

        # Combine and dedup
        all_matches = matches + [i for i in cat_matches if i.fingerprint not in {m.fingerprint for m in matches}]
        all_matches.sort(key=lambda x: -x.score)
        return all_matches[:5]


# ---------------------------------------------------------------------------
# Desktop Notification
# ---------------------------------------------------------------------------

def send_notification(title: str, message: str, sound: str = "Glass"):
    try:
        t = title.replace('"', '\\"')
        m = message.replace('"', '\\"')
        subprocess.Popen(
            ["osascript", "-e", f'display notification "{m}" with title "{t}" sound name "{sound}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Catalyst Attribution Engine
# ---------------------------------------------------------------------------

def build_catalyst_summary(move: MoveEvent, correlated: list[str], news: list[NewsItem],
                           news_scanner: NewsScanner) -> str:
    """Build a human-readable catalyst explanation for a move."""
    parts = []

    # Primary catalyst from correlated assets
    if correlated:
        parts.append(correlated[0])

    # Add news headline if available
    if news:
        best = news[0]
        cats = ",".join(best.categories[:2])
        parts.append(f"[{cats}] {best.title}")
        # Add details if available
        details = news_scanner.detail_cache.get(best.fingerprint, [])
        for d in details[:2]:
            parts.append(f"  {d}")

    # Secondary correlations
    for c in correlated[1:3]:
        parts.append(c)

    if not parts:
        parts.append("No clear catalyst identified — monitoring...")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Dashboard Renderer
# ---------------------------------------------------------------------------

def format_age(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    try:
        pub = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        mins = int((now - pub).total_seconds() / 60)
        if mins < 1:
            return "now"
        elif mins < 60:
            return f"{mins}m"
        elif mins < 1440:
            return f"{mins // 60}h{mins % 60}m"
        else:
            return f"{mins // 1440}d"
    except Exception:
        return "?"


def render_dashboard(tracker: PriceTracker, correlator: AssetCorrelator,
                     news_scanner: NewsScanner, spy_data: dict,
                     gex_calc: GEXCalculator = None, gex_risk: dict = None,
                     status_msg: str = "") -> Group:
    """Render the full monitoring dashboard."""
    import shutil
    term_h = shutil.get_terminal_size((120, 30)).lines
    now_local = datetime.now().strftime('%H:%M:%S')
    price = spy_data.get("price", 0)
    prev_close = spy_data.get("prev_close", price)
    daily_chg = (price - prev_close) / prev_close * 100 if prev_close else 0

    # ── Header ──
    chg_color = "green" if daily_chg >= 0 else "red"
    vwap = spy_data.get("vwap", 0)
    vol = spy_data.get("volume", 0)
    header_parts = [
        ("ES FUTURES MONITOR", "bold white"),
        ("  ", "dim"),
        (f"{price:.2f}", "bold cyan"),
        (f"  {daily_chg:+.2f}%", f"bold {chg_color}"),
    ]
    if vwap and vwap > 0:
        header_parts.append((f"  VWAP:{vwap:.2f}", "dim"))
    if vol and vol > 0:
        header_parts.append((f"  Vol:{vol:,.0f}", "dim"))
    header_parts.append((f"  {now_local}", "dim"))
    header = Text.assemble(*header_parts)

    # ── Rolling Price Changes ──
    stats = tracker.get_current_stats()
    tf_table = Table(box=box.SIMPLE, padding=(0, 1), expand=True, show_header=True)
    tf_table.add_column("1min", justify="center", width=10)
    tf_table.add_column("5min", justify="center", width=10)
    tf_table.add_column("15min", justify="center", width=10)
    tf_table.add_column("30min", justify="center", width=10)
    tf_table.add_column("1hr", justify="center", width=10)
    tf_table.add_column("Day", justify="center", width=10)

    values = []
    for tf in ["1m", "5m", "15m", "30m", "1h"]:
        v = stats.get(tf, 0)
        threshold = MOVE_THRESHOLDS.get(tf, 0.5)
        if abs(v) >= threshold:
            color = "bold red" if v < 0 else "bold green"
            marker = " !"
        elif abs(v) >= threshold * 0.7:
            color = "red" if v < 0 else "green"
            marker = ""
        else:
            color = "dim"
            marker = ""
        values.append(f"[{color}]{v:+.2f}%{marker}[/]")
    values.append(f"[bold {'green' if daily_chg >= 0 else 'red'}]{daily_chg:+.2f}%[/]")
    tf_table.add_row(*values)

    # ── Correlated Assets ──
    assets = correlator.cache
    asset_table = Table(box=box.SIMPLE, padding=(0, 1), expand=True, show_header=True,
                        title="[bold]CROSS-ASSET[/]", title_style="dim")
    asset_table.add_column("Asset", width=14)
    asset_table.add_column("Price", justify="right", width=10)
    asset_table.add_column("Chg%", justify="right", width=8)
    asset_table.add_column("Signal", width=30)

    def _valid_asset(a):
        """Check if asset data is valid (no NaN/None/inf)."""
        try:
            p = a.get("price")
            c = a.get("change_pct")
            if p is None or c is None:
                return False
            return np.isfinite(float(p)) and np.isfinite(float(c)) and float(p) > 0
        except (TypeError, ValueError):
            return False

    # Show the most important assets first (SPY is now a correlated asset, ES is primary)
    if term_h < 40:
        priority_order = ["SPY", "CL=F", "^TNX", "^VIX", "UUP", "GLD"]
        max_sectors = 1
    else:
        priority_order = ["SPY", "CL=F", "^TNX", "^VIX", "UUP", "GLD", "QQQ", "IWM", "TLT"]
        max_sectors = 3
    shown = set()
    for ticker in priority_order:
        if ticker in assets and _valid_asset(assets[ticker]):
            a = assets[ticker]
            chg = a["change_pct"]
            color = "green" if chg >= 0 else "red"
            signal = ""
            if abs(chg) > 2:
                signal = f"[bold {'red' if chg < 0 else 'green'}]BIG MOVE[/]"
            elif abs(chg) > 1:
                signal = f"[{'red' if chg < 0 else 'green'}]Notable[/]"
            asset_table.add_row(
                f"[bold]{a['name']}[/]",
                f"${a['price']:.2f}" if a['price'] > 10 else f"{a['price']:.3f}",
                f"[{color}]{chg:+.2f}%[/]",
                signal,
            )
            shown.add(ticker)

    # Show sector outliers
    sector_moves = []
    for ticker, a in assets.items():
        if a.get("category") == "SECTOR" and ticker not in shown and _valid_asset(a):
            sector_moves.append((ticker, a))
    sector_moves.sort(key=lambda x: abs(x[1]["change_pct"]), reverse=True)
    for ticker, a in sector_moves[:max_sectors]:
        chg = a["change_pct"]
        color = "green" if chg >= 0 else "red"
        asset_table.add_row(
            f"[dim]{a['name']}[/]",
            f"${a['price']:.2f}",
            f"[{color}]{chg:+.2f}%[/]",
            "",
        )

    # ── Recent Moves with Catalysts ──
    move_rows = []
    with tracker.lock:
        recent = list(tracker.recent_moves)[:10]
    if recent:
        move_table = Table(box=box.HEAVY_EDGE, padding=(0, 1), expand=True, show_header=True,
                           title="[bold red]MOVE ALERTS[/]", border_style="red")
        move_table.add_column("Time", width=8)
        move_table.add_column("Window", width=6)
        move_table.add_column("Move", width=10, justify="right")
        move_table.add_column("Catalyst / Attribution", ratio=4)

        for move in recent:
            age = format_age(move.end_time)
            color = "red" if move.direction == "DOWN" else "green"
            catalyst_text = "\n".join(move.catalysts[:3]) if move.catalysts else "[dim]Analyzing...[/]"
            # Escape rich markup in catalyst text
            catalyst_text = catalyst_text.replace("[DATA]", "\\[DATA]").replace("[SURPRISE]", "\\[SURPRISE]")
            catalyst_text = catalyst_text.replace("[OUTLOOK]", "\\[OUTLOOK]").replace("[REACTION]", "\\[REACTION]")
            catalyst_text = catalyst_text.replace("[OIL]", "\\[OIL]").replace("[TARIFF]", "\\[TARIFF]")
            catalyst_text = catalyst_text.replace("[QUOTE]", "\\[QUOTE]").replace("[FED]", "\\[FED]")
            move_table.add_row(
                f"[dim]{age}[/]",
                move.timeframe,
                f"[bold {color}]{move.pct_change:+.2f}%[/]",
                catalyst_text,
            )
        move_rows.append(move_table)

    # ── News Feed ──
    with news_scanner.lock:
        max_news = 2 if term_h < 40 else 5
        news_items = list(news_scanner.items)[:max_news]
    if news_items:
        news_table = Table(box=box.SIMPLE, padding=(0, 1), expand=True, show_header=True,
                           title="[bold yellow]BREAKING NEWS[/]", title_style="yellow")
        news_table.add_column("Impact", width=8, justify="center")
        news_table.add_column("Tag", width=10)
        news_table.add_column("Headline & Details", ratio=4)
        news_table.add_column("Age", width=6, justify="right")

        score_styles = {5: "bold red", 4: "bold yellow", 3: "yellow", 2: "dim"}
        score_labels = {5: "CRIT", 4: "HIGH", 3: "MED", 2: "LOW"}
        for item in news_items:
            color = score_styles.get(item.score, "dim")
            label = score_labels.get(item.score, "?")
            cats = ",".join(item.categories[:2])
            age = format_age(item.published)

            # Build headline + details
            parts = [f"[{color}]{item.title}[/]"]
            details = news_scanner.detail_cache.get(item.fingerprint, [])
            for d in details[:1]:
                tag_match = re.match(r'\[([A-Z/ ]+)\]\s*(.*)', d)
                if tag_match:
                    tag = tag_match.group(1)
                    text = tag_match.group(2).replace("[", "\\[")
                    tc = TAG_COLORS.get(tag, 'dim')
                    parts.append(f"  [{tc}]▸ {tag}:[/] [dim]{text}[/]")
                else:
                    parts.append(f"  [dim]▸ {d}[/]")

            news_table.add_row(
                f"[{color}]{label}[/]",
                f"[{color}]{cats}[/]",
                "\n".join(parts),
                f"[dim]{age}[/]",
            )
    else:
        news_table = Text.from_markup("[dim]Scanning for news...[/]")

    # ── GEX Levels & Move Risk ──
    gex_components = []
    if gex_calc and gex_calc.profile:
        profile = gex_calc.profile
        mq_src = gex_calc.menthorq_data.get("_source", "")
        if gex_calc.menthorq_loaded and mq_src == "menthorq_scrape":
            gex_source = "Menthor Q (live)"
        elif gex_calc.menthorq_loaded:
            gex_source = "Menthor Q (file)"
        else:
            gex_source = "Alpaca Chain"
        gex_title = f"[bold magenta]GAMMA EXPOSURE (GEX)[/] [dim]src: {gex_source}[/]"

        # ── Key Levels Summary Table ──
        mq = gex_calc.menthorq_data or {}
        lvl_table = Table(box=box.SIMPLE_HEAVY, padding=(0, 2), expand=True,
                          title=gex_title, title_style="magenta")
        lvl_table.add_column("Level", width=24)
        lvl_table.add_column("Strike", justify="right", width=10)
        lvl_table.add_column("Distance", justify="right", width=10)
        lvl_table.add_column("Signal", ratio=3)

        # Define all levels to display from Menthor Q data
        gex_rows = []
        call_wall = mq.get("call_resistance", 0) or 0
        put_wall = mq.get("put_support", 0) or 0
        hvl = mq.get("hvl", 0) or 0
        zero_gamma = mq.get("zero_gamma", 0) or 0
        dte0_call = mq.get("0dte_call_resistance", 0) or 0
        dte0_put = mq.get("0dte_put_support", 0) or 0
        em_lo = mq.get("expected_move_low", 0) or 0
        em_hi = mq.get("expected_move_high", 0) or 0

        if call_wall > 0:
            dist = (call_wall - price) / price * 100
            if abs(dist) < 0.3:
                sig = "[bold red]AT RESISTANCE — likely rejection[/]"
            elif dist > 0:
                sig = "[red]Resistance above[/]"
            else:
                sig = "[bold red]BROKEN — momentum up[/]"
            gex_rows.append(("Call Wall", call_wall, dist, sig, "red"))

        if dte0_call > 0:
            dist = (dte0_call - price) / price * 100
            sig = "[bright_magenta]Intraday resistance[/]" if dist > 0 else "[bold bright_magenta]BROKEN[/]"
            gex_rows.append(("0DTE Call Resist", dte0_call, dist, sig, "bright_magenta"))

        if hvl > 0:
            dist = (hvl - price) / price * 100
            if abs(dist) < 0.2:
                sig = "[bold yellow]AT VOLATILITY FLIP ZONE![/]"
            elif price > hvl:
                sig = "[yellow]Above = positive gamma (calm)[/]"
            else:
                sig = "[yellow]Below = negative gamma (volatile)[/]"
            gex_rows.append(("HVL", hvl, dist, sig, "yellow"))

        if zero_gamma > 0 and zero_gamma != hvl:
            dist = (zero_gamma - price) / price * 100
            if abs(dist) < 0.2:
                sig = "[bold yellow]VOLATILITY FLIP ZONE![/]"
            elif price > zero_gamma:
                sig = "[yellow]Above = positive gamma (calm)[/]"
            else:
                sig = "[yellow]Below = negative gamma (volatile)[/]"
            gex_rows.append(("Zero Gamma", zero_gamma, dist, sig, "yellow"))

        if dte0_put > 0:
            dist = (dte0_put - price) / price * 100
            sig = "[bright_magenta]Intraday support[/]" if dist < 0 else "[bold bright_magenta]BROKEN[/]"
            gex_rows.append(("0DTE Put Support", dte0_put, dist, sig, "bright_magenta"))

        if put_wall > 0:
            dist = (put_wall - price) / price * 100
            if abs(dist) < 0.3:
                sig = "[bold green]AT SUPPORT — likely bounce[/]"
            elif dist < 0:
                sig = "[green]Support below[/]"
            else:
                sig = "[bold green]BROKEN — momentum down[/]"
            gex_rows.append(("Put Wall", put_wall, dist, sig, "green"))

        # Sort by strike descending (resistance on top, support on bottom)
        gex_rows.sort(key=lambda r: r[1], reverse=True)

        for label, strike, dist, sig, color in gex_rows:
            lvl_table.add_row(
                f"[{color}]{label}[/]",
                f"[{color}]${strike:.0f}[/]",
                f"[{color}]{dist:+.2f}%[/]",
                sig,
            )

        # Expected move range
        if em_lo > 0 and em_hi > 0:
            in_range = em_lo <= price <= em_hi
            rc = "green" if in_range else "bold red"
            lvl_table.add_row(
                f"[{rc}]Expected Move[/]",
                f"[{rc}]${em_lo:.0f}-${em_hi:.0f}[/]",
                "",
                f"[{rc}]{'INSIDE range' if in_range else 'OUTSIDE — big move!'}[/]",
            )

        # Regime
        if profile.regime == "NEGATIVE":
            regime_text = "[bold red]NEG GAMMA[/] — dealers amplify moves, BIG SWINGS"
        else:
            regime_text = "[green]POS GAMMA[/] — dealers suppress vol, mean-revert"
        lvl_table.add_row("[bold]Regime[/]", "", "", regime_text)

        gex_components.append(lvl_table)

    # ── Move Risk Assessment ──
    if gex_risk and gex_risk.get("warnings"):
        risk_level = gex_risk.get("risk", "UNKNOWN")
        risk_colors = {"CRITICAL": "bold red", "HIGH": "red", "ELEVATED": "yellow", "LOW": "green", "UNKNOWN": "dim"}
        risk_color = risk_colors.get(risk_level, "dim")

        warnings = gex_risk["warnings"]
        risk_parts = [f"[{risk_color}]MOVE RISK: {risk_level}[/]"]
        for w in warnings[:3]:
            risk_parts.append(f"  [dim]▸[/] {w}")
        risk_text = Text.from_markup("\n".join(risk_parts))
        gex_components.append(risk_text)

    # ── Status line ──
    status = Text(status_msg, style="dim") if status_msg else Text("")

    # ── Assemble ──
    components = [header, tf_table, asset_table]
    components.extend(gex_components)
    components.append(news_table)
    components.extend(move_rows)
    components.append(status)

    return Group(*components)


# ---------------------------------------------------------------------------
# Main Monitor Loop
# ---------------------------------------------------------------------------

def run_monitor(poll_interval: int, move_threshold: float, no_notify: bool):
    if not ALPACA_KEY or not ALPACA_SECRET:
        console.print("[bold red]Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env[/]")
        sys.exit(1)

    tracker = PriceTracker(move_threshold_mult=move_threshold / DEFAULT_MOVE_THRESHOLD)
    correlator = AssetCorrelator()
    news_scanner = NewsScanner()
    gex_calc = GEXCalculator()

    # Check for Menthor Q GEX source
    if MENTHORQ_USER and MENTHORQ_PASS:
        gex_source = "Menthor Q (auto-scrape)"
    elif GEX_LEVELS_FILE.exists():
        gex_source = "Menthor Q (gex_levels.json manual)"
    else:
        gex_source = "Alpaca options chain (fallback)"

    console.print(Panel(
        f"[bold white]ES Futures Monitor — Move Detection + Catalyst + GEX[/]\n\n"
        f"[dim]Primary: {PRIMARY_NAME} ({PRIMARY_TICKER}) via yfinance\n"
        f"Price polling: every {poll_interval}s\n"
        f"Move threshold: {move_threshold:.2f}% (scaled per timeframe)\n"
        f"Cross-asset scan: {len(CORRELATED_ASSETS)} assets (SPY via Alpaca)\n"
        f"GEX levels: {gex_source}\n"
        f"News sources: {len(RSS_FEEDS)} RSS feeds\n"
        f"Desktop alerts: {'OFF' if no_notify else 'ON'}[/]\n\n"
        "[dim]Press Ctrl+C to exit[/]",
        border_style="cyan",
        padding=(1, 2),
    ))

    # Initial data fetch — ES futures
    console.print(f"[dim]Fetching {PRIMARY_TICKER}...[/]")
    try:
        spy_data = fetch_es_price()
        console.print(f"[bold cyan]{PRIMARY_NAME} {spy_data['price']:.2f}[/]")
    except Exception as e:
        console.print(f"[bold red]Failed to fetch {PRIMARY_TICKER}: {e}[/]")
        sys.exit(1)

    # Initial asset scan (background)
    console.print("[dim]Scanning correlated assets (SPY + cross-asset)...[/]")
    correlator.scan(force=True)
    console.print(f"[dim]  {len(correlator.cache)} assets loaded[/]")

    # Initial news scan
    console.print("[dim]Scanning news feeds...[/]")
    new_news = news_scanner.poll()
    console.print(f"[dim]  {len(new_news)} news items found[/]")

    # Initial GEX calculation
    console.print(f"[dim]Loading GEX levels ({gex_source})...[/]")
    gex_profile = gex_calc.calculate(spy_data["price"])
    if gex_profile:
        src = "Menthor Q" if gex_calc.menthorq_loaded else "Alpaca"
        console.print(f"[dim]  GEX ({src}): {gex_profile.regime} gamma | Call Wall: ${gex_profile.call_wall:.0f} | Put Wall: ${gex_profile.put_wall:.0f} | Zero: ${gex_profile.zero_gamma:.0f}[/]")
    else:
        console.print("[dim]  GEX: no data — update gex_levels.json with Menthor Q levels[/]")

    time.sleep(2)

    scan_count = 0
    last_news_poll = time.time()
    last_asset_scan = time.time()
    last_gex_calc = time.time()

    consecutive_errors = 0

    try:
        with Live(Text("Starting...", style="dim"), console=console, refresh_per_second=0.5, screen=True) as live:
            while True:
                try:
                    t0 = time.time()
                    scan_count += 1

                    # Fetch SPY price — use last known data on transient failure
                    try:
                        spy_data = fetch_es_price()
                        consecutive_errors = 0
                    except Exception as e:
                        consecutive_errors += 1
                        if consecutive_errors <= 5:
                            # Use stale data, just skip this tick
                            wait = max(0.5, poll_interval)
                            time.sleep(wait)
                            continue
                        elif consecutive_errors <= 15:
                            # Show warning but keep trying
                            live.update(Panel(
                                f"[yellow]Connection issue ({consecutive_errors}x): {e}[/]\n"
                                f"[dim]Retrying... last known SPY ${spy_data.get('price', 0):.2f}[/]",
                                border_style="yellow",
                            ))
                            time.sleep(poll_interval)
                            continue
                        else:
                            raise

                    price = spy_data["price"]
                    prev_close = spy_data["prev_close"]
                    daily_chg = (price - prev_close) / prev_close * 100

                    # Detect moves
                    try:
                        new_moves = tracker.add_price(price, spy_data["volume"])
                    except Exception:
                        new_moves = []

                    # Background: refresh correlated assets every 15s
                    if time.time() - last_asset_scan >= 15:
                        threading.Thread(
                            target=correlator.scan, kwargs={"force": True}, daemon=True
                        ).start()
                        last_asset_scan = time.time()

                    # Background: poll news every 30s
                    if time.time() - last_news_poll >= NEWS_POLL_INTERVAL:
                        def _poll_news():
                            new_items = news_scanner.poll()
                            if not no_notify:
                                for item in new_items:
                                    if item.score >= 4:
                                        cats = ",".join(item.categories[:2])
                                        send_notification(
                                            f"SPY News: {cats}",
                                            item.title,
                                            "Submarine" if item.score >= 5 else "Glass",
                                        )
                        threading.Thread(target=_poll_news, daemon=True).start()
                        last_news_poll = time.time()

                    # Background: refresh GEX every 5 minutes
                    if time.time() - last_gex_calc >= GEX_REFRESH_INTERVAL:
                        threading.Thread(
                            target=gex_calc.calculate, args=(price,), daemon=True
                        ).start()
                        last_gex_calc = time.time()

                    # Assess move risk from GEX
                    gex_risk = gex_calc.assess_move_risk(price)

                    # GEX-based notifications
                    if not no_notify and gex_risk.get("risk") in ("CRITICAL", "HIGH"):
                        gex_warnings = gex_risk.get("warnings", [])
                        if gex_warnings and new_moves:
                            send_notification(
                                f"ES GEX: {gex_risk['risk']}",
                                gex_warnings[0][:150],
                                "Sosumi" if gex_risk["risk"] == "CRITICAL" else "Glass",
                            )

                    # Process new moves — attribute catalysts (now includes GEX context)
                    for move in new_moves:
                        # Get correlated asset attribution
                        correlated = correlator.attribute_move(daily_chg)
                        move.correlated_moves = correlator.cache

                        # Match news
                        matched_news = news_scanner.match_news_to_move(move)
                        move.news_matches = matched_news

                        # Build catalyst summary — include GEX context
                        summary = build_catalyst_summary(move, correlated, matched_news, news_scanner)
                        catalyst_lines = summary.split("\n")

                        # Add GEX context if no other catalyst found
                        if gex_risk and gex_risk.get("warnings"):
                            gex_context = gex_risk["warnings"][0]
                            if len(catalyst_lines) <= 1 and catalyst_lines[0].startswith("No clear"):
                                catalyst_lines = [gex_context] + gex_risk["warnings"][1:2]
                            else:
                                catalyst_lines.append(f"GEX: {gex_context}")

                        move.catalysts = catalyst_lines

                        # Desktop notification
                        if not no_notify:
                            direction = "RALLY" if move.direction == "UP" else "SELLOFF"
                            sound = "Sosumi" if abs(move.pct_change) > 0.5 else "Glass"
                            catalyst_short = move.catalysts[0] if move.catalysts else "Unknown"
                            # Strip rich markup for notification
                            catalyst_clean = re.sub(r'\[.*?\]', '', catalyst_short)
                            send_notification(
                                f"ES {direction} {move.pct_change:+.2f}% ({move.timeframe})",
                                catalyst_clean[:150],
                                sound,
                            )

                    # Build status
                    elapsed = time.time() - t0
                    gex_status = ""
                    if gex_calc.profile:
                        gex_status = f"GEX: {gex_calc.profile.regime} | "
                    status = (
                        f"Scan #{scan_count} ({elapsed:.1f}s) | "
                        f"Next: {poll_interval}s | "
                        f"{gex_status}"
                        f"Moves: {len(tracker.recent_moves)} | "
                        f"News: {len(news_scanner.items)} | "
                        f"Assets: {len(correlator.cache)} | "
                        f"Ctrl+C to stop"
                    )

                    # Render
                    dashboard = render_dashboard(
                        tracker, correlator, news_scanner, spy_data,
                        gex_calc=gex_calc, gex_risk=gex_risk, status_msg=status,
                    )
                    live.update(Panel(dashboard, border_style="cyan", padding=(0, 1)))

                    wait = max(0.5, poll_interval - (time.time() - t0))
                    time.sleep(wait)

                except KeyboardInterrupt:
                    break
                except Exception as e:
                    import traceback
                    err_detail = traceback.format_exc().split('\n')[-3:]
                    live.update(Panel(
                        f"[bold red]Error: {e}[/]\n"
                        f"[dim]{'  '.join(err_detail)}[/]\n"
                        f"[dim]Retrying in {poll_interval}s...[/]",
                        border_style="red",
                    ))
                    time.sleep(poll_interval)

    except KeyboardInterrupt:
        pass

    console.print("\n[bold]Monitor stopped.[/]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SPY Active Monitor — Move Detection + Catalyst Attribution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python spy_monitor.py                  # Default settings\n"
            "  python spy_monitor.py -i 3             # Poll every 3 seconds\n"
            "  python spy_monitor.py --threshold 0.5  # Alert on 0.5%+ moves only\n"
            "  python spy_monitor.py --no-notify       # No desktop notifications\n"
        ),
    )
    parser.add_argument("--interval", "-i", type=int, default=DEFAULT_POLL_INTERVAL,
                        help=f"Seconds between price polls (default: {DEFAULT_POLL_INTERVAL})")
    parser.add_argument("--threshold", "-t", type=float, default=DEFAULT_MOVE_THRESHOLD,
                        help=f"Min %% move to trigger alert (default: {DEFAULT_MOVE_THRESHOLD})")
    parser.add_argument("--no-notify", action="store_true", help="Disable desktop notifications")
    args = parser.parse_args()

    run_monitor(args.interval, args.threshold, args.no_notify)


if __name__ == "__main__":
    main()
