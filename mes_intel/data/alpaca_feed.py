"""Alpaca Markets real-time websocket feed for US stocks and ETFs.

Provides true real-time (or near-real-time via IEX) quotes for the
cross-asset panel: SPY, QQQ, IWM, GLD, USO, HYG, TLT, VXX, etc.

Free account at alpaca.markets gives real-time IEX data at no cost.
No futures data — this supplements Rithmic (which handles MES/ES).

Usage:
    feed = AlpacaFeed(api_key, api_secret, symbols, on_quote)
    feed.start()
    ...
    feed.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# Alpaca WebSocket Data Stream v2
_WS_URL_IEX = "wss://stream.data.alpaca.markets/v2/iex"
_WS_URL_SIP = "wss://stream.data.alpaca.markets/v2/sip"  # paid

# Symbols to request (maps Alpaca symbol → our asset name)
DEFAULT_SYMBOLS: Dict[str, str] = {
    "SPY":  "SPY",
    "QQQ":  "NQ FUTS",    # Nasdaq proxy
    "IWM":  "RUSSELL",    # Russell 2000 proxy
    "GLD":  "GOLD",       # Gold ETF proxy
    "USO":  "OIL/WTI",    # WTI crude proxy
    "HYG":  "HY BONDS",
    "TLT":  "LT BONDS",
    "VXX":  "VIX",        # VIX short-term futures ETN proxy
    "UUP":  "DXY",        # Dollar index ETF proxy
}

# Crypto via Alpaca (separate stream)
CRYPTO_SYMBOLS: Dict[str, str] = {
    "BTC/USD": "BITCOIN",
}


class AlpacaFeed:
    """Real-time quote stream from Alpaca Markets.

    Connects via WebSocket, authenticates, and subscribes to trade/quote
    updates for the given symbols. Calls `on_quote(asset_name, price, change_pct)`
    on each update.

    Parameters
    ----------
    api_key : str
    api_secret : str
    on_quote : Callable[[str, float, float], None]
        Called with (asset_name, price, prev_close) on each update.
    feed : str
        'iex' (free, real-time) or 'sip' (paid, consolidated tape).
    symbols : dict, optional
        Override DEFAULT_SYMBOLS mapping.
    """

    RECONNECT_DELAY = 5.0
    MAX_RECONNECT_DELAY = 60.0
    PING_INTERVAL = 30.0

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        on_quote: Callable[[str, float, float], None],
        feed: str = "iex",
        symbols: Optional[Dict[str, str]] = None,
    ):
        self._key = api_key
        self._secret = api_secret
        self._on_quote = on_quote
        self._feed = feed
        self._symbols = symbols or DEFAULT_SYMBOLS
        self._url = _WS_URL_SIP if feed == "sip" else _WS_URL_IEX

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._reconnect_delay = self.RECONNECT_DELAY

        # Latest prices per symbol (for change_pct computation)
        self._prev_close: Dict[str, float] = {}
        self._last_price: Dict[str, float] = {}
        self._connected = False
        self._authenticated = False

        # Stats
        self.ticks_received = 0
        self.last_update: Dict[str, float] = {}  # symbol → timestamp

    def start(self):
        """Start the feed in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_main, name="alpaca-feed", daemon=True
        )
        self._thread.start()
        log.info("AlpacaFeed started (feed=%s, %d symbols)", self._feed, len(self._symbols))

    def stop(self):
        """Stop the feed."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        log.info("AlpacaFeed stopped")

    def is_live(self) -> bool:
        return self._connected and self._authenticated

    def get_status(self) -> dict:
        return {
            "connected": self._connected,
            "authenticated": self._authenticated,
            "feed": self._feed,
            "ticks": self.ticks_received,
            "symbols": list(self._symbols.keys()),
        }

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        while self._running:
            try:
                self._loop.run_until_complete(self._connect_and_stream())
            except Exception as exc:
                log.warning("AlpacaFeed stream error: %s", exc)
            if not self._running:
                break
            log.info("AlpacaFeed reconnecting in %.0fs...", self._reconnect_delay)
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 1.5, self.MAX_RECONNECT_DELAY
            )
        self._loop.close()

    async def _connect_and_stream(self):
        try:
            import websockets
        except ImportError:
            log.error("websockets package not installed — pip install websockets")
            self._running = False
            return

        self._connected = False
        self._authenticated = False

        try:
            async with websockets.connect(
                self._url,
                ping_interval=self.PING_INTERVAL,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                self._connected = True
                log.info("AlpacaFeed WebSocket connected: %s", self._url)

                # Step 1: receive welcome
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(msg)
                if isinstance(data, list) and data and data[0].get("T") != "success":
                    log.warning("AlpacaFeed unexpected welcome: %s", data)

                # Step 2: authenticate
                await ws.send(json.dumps({
                    "action": "auth",
                    "key": self._key,
                    "secret": self._secret,
                }))
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(msg)
                if isinstance(data, list):
                    for item in data:
                        if item.get("T") == "success" and item.get("msg") == "authenticated":
                            self._authenticated = True
                            self._reconnect_delay = self.RECONNECT_DELAY
                            log.info("AlpacaFeed authenticated")
                            break

                if not self._authenticated:
                    log.error("AlpacaFeed authentication failed: %s", data)
                    return

                # Step 3: subscribe to trades
                syms = list(self._symbols.keys())
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "trades": syms,
                    "quotes": syms,
                }))
                log.info("AlpacaFeed subscribed to %d symbols: %s", len(syms), syms)

                # Step 4: stream
                async for raw in ws:
                    if not self._running:
                        break
                    try:
                        self._handle_message(json.loads(raw))
                    except Exception as exc:
                        log.debug("AlpacaFeed message parse error: %s", exc)

        except Exception as exc:
            log.warning("AlpacaFeed connection error: %s", exc)
        finally:
            self._connected = False
            self._authenticated = False

    def _handle_message(self, messages):
        if not isinstance(messages, list):
            return
        for msg in messages:
            t = msg.get("T")
            sym = msg.get("S", "")

            if t == "t":
                # Trade message
                price = float(msg.get("p", 0))
                if price <= 0 or sym not in self._symbols:
                    continue

                asset_name = self._symbols[sym]
                prev = self._prev_close.get(sym, price)
                self._last_price[sym] = price
                self.last_update[sym] = time.time()
                self.ticks_received += 1

                try:
                    self._on_quote(asset_name, price, prev)
                except Exception as exc:
                    log.debug("AlpacaFeed on_quote error: %s", exc)

            elif t == "q":
                # Quote (bid/ask midpoint)
                bid = float(msg.get("bp", 0))
                ask = float(msg.get("ap", 0))
                if bid > 0 and ask > 0 and sym in self._symbols:
                    mid = (bid + ask) / 2
                    self._last_price[sym] = mid
                    self.last_update[sym] = time.time()

            elif t == "d":
                # Daily bar (contains prev close info)
                sym2 = msg.get("S", "")
                if sym2 in self._symbols:
                    prev_c = float(msg.get("c", 0))  # previous close
                    if prev_c > 0:
                        self._prev_close[sym2] = prev_c

    def inject_prev_closes(self, prev_closes: Dict[str, float]):
        """Seed previous close prices (e.g. from yfinance on startup)."""
        self._prev_close.update(prev_closes)

    def get_latest_prices(self) -> Dict[str, dict]:
        """Return latest prices for all tracked symbols."""
        out = {}
        now = time.time()
        for sym, asset_name in self._symbols.items():
            price = self._last_price.get(sym, 0.0)
            prev = self._prev_close.get(sym, price)
            age = int(now - self.last_update.get(sym, 0)) if sym in self.last_update else 9999
            if price > 0:
                chg = (price - prev) / prev * 100 if prev > 0 else 0.0
                out[asset_name] = {
                    "price": price,
                    "prev_close": prev,
                    "change_pct": round(chg, 3),
                    "age_sec": age,
                    "live": self._authenticated and age < 30,
                    "symbol": sym,
                }
        return out
