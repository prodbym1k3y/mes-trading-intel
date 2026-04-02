"""
Rithmic R|API Feed — MES Futures Real-Time Data
Connects through AMP Futures to Rithmic infrastructure.

Requires: rapi (Rithmic's Python API, obtained from AMP/Rithmic)
Falls back to simulated data if rapi not installed.

Connection lifecycle:
    DISCONNECTED -> CONNECTING -> AUTHENTICATED -> SUBSCRIBED

Usage:
    feed = ActiveFeed(config, event_bus)
    feed.start()
    feed.subscribe("MESH6", "CME")
    # ticks flow through event bus as TICK_RECEIVED / PRICE_UPDATE events
    feed.stop()
"""
from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple

from ..config import RithmicConfig
from ..event_bus import EventBus, Event, EventType
from ..orderflow import Tick

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CME contract helpers
# ---------------------------------------------------------------------------

_MES_MONTHS = {3: "H", 6: "M", 9: "U", 12: "Z"}


def detect_front_month() -> str:
    """Return the front-month MES contract symbol, e.g. 'MESH6'."""
    dt = datetime.now()
    yr = dt.year % 10
    for month in sorted(_MES_MONTHS):
        if dt.month < month or (dt.month == month and dt.day <= 14):
            return f"MES{_MES_MONTHS[month]}{yr}"
    return f"MES{_MES_MONTHS[3]}{(yr + 1) % 10}"


# ---------------------------------------------------------------------------
# Connection state
# ---------------------------------------------------------------------------

class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    AUTHENTICATED = auto()
    SUBSCRIBED = auto()
    ERROR = auto()


# ---------------------------------------------------------------------------
# RithmicCallbacks mixin — override in subclass to hook rapi events
# ---------------------------------------------------------------------------

class RithmicCallbacks:
    """Mixin providing callback stubs for rapi events.

    When using a real rapi client, subclass both RithmicCallbacks and the
    rapi session class, then override these methods to receive live data.
    """

    def on_tick(self, ticker: str, exchange: str, price: float,
                qty: int, aggressor: bool, timestamp: float) -> None:
        """Called on each trade tick. aggressor=True means buyer-initiated."""

    def on_dom_update(self, ticker: str,
                      bids: List[Tuple[float, int]],
                      asks: List[Tuple[float, int]]) -> None:
        """Called when the depth-of-market (DOM) changes."""

    def on_bar_update(self, ticker: str, open: float, high: float,
                      low: float, close: float, volume: int) -> None:
        """Called when a bar is completed or updated."""

    def on_connected(self) -> None:
        """Called when the TCP connection to Rithmic is established."""

    def on_disconnected(self, reason: str) -> None:
        """Called when the connection drops. reason is a human-readable string."""

    def on_login_response(self, result: bool, text: str) -> None:
        """Called with the authentication result. result=True means success."""


# ---------------------------------------------------------------------------
# RithmicFeed — real implementation (requires rapi package)
# ---------------------------------------------------------------------------

class RithmicFeed(RithmicCallbacks):
    """Live Rithmic R|API feed connector for MES futures via AMP Futures.

    Wraps the rapi package (Rithmic's licensed Python SDK). Publishes
    TICK_RECEIVED and PRICE_UPDATE events to the EventBus on every trade tick.

    Parameters
    ----------
    config : RithmicConfig
        Rithmic credentials and server selection.
    event_bus : EventBus
        Bus to publish market data events onto.
    """

    HEARTBEAT_INTERVAL = 30.0
    RECONNECT_BASE = 2.0
    RECONNECT_MAX = 60.0
    RECONNECT_MULT = 2.0

    def __init__(self, config: RithmicConfig, event_bus: EventBus) -> None:
        self._config = config
        self._bus = event_bus
        self._state = ConnectionState.DISCONNECTED
        self._symbol: Optional[str] = None
        self._exchange: str = "CME"
        self._running = False
        self._reconnect_delay = self.RECONNECT_BASE
        self._ticks_received = 0
        self._start_time: Optional[float] = None
        self._last_tick_time: Optional[float] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._main_thread: Optional[threading.Thread] = None

        # rapi session object — set during connect()
        self._session = None

    # -- Properties -----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._state in (ConnectionState.AUTHENTICATED,
                               ConnectionState.SUBSCRIBED)

    # -- Public API -----------------------------------------------------------

    def connect(self) -> bool:
        """Authenticate with the Rithmic server. Returns True on success."""
        if self._state not in (ConnectionState.DISCONNECTED,
                               ConnectionState.ERROR):
            log.warning("connect() called in state %s", self._state.name)
            return self.is_connected

        self._state = ConnectionState.CONNECTING
        rc = self._config
        log.info("Connecting to Rithmic: system=%s user=%s url=%s:%s",
                 rc.system_name, rc.username, rc.host, rc.port)

        try:
            import rapi  # type: ignore[import]
            # Build rapi session — rapi.REngine is the main entry point
            engine = rapi.REngine(
                appName=rc.app_name,
                appVersion=rc.app_version,
            )
            self._session = engine.createSession()
            self._session.setCallbacks(self)

            result = self._session.login(
                server=rc.host,
                port=rc.port,
                user=rc.username,
                password=rc.password,
                systemName=rc.system_name,
                interface=rc.interface,
            )
        except Exception:
            log.exception("Failed to connect to Rithmic")
            self._state = ConnectionState.ERROR
            self._bus.publish(Event(
                type=EventType.ERROR,
                source="rithmic_feed",
                data={"error": "connect() exception"},
            ))
            return False

        if result:
            self._state = ConnectionState.AUTHENTICATED
            self._reconnect_delay = self.RECONNECT_BASE
            self._start_heartbeat()
            log.info("Rithmic authentication successful")
            self._bus.publish(Event(
                type=EventType.RITHMIC_CONNECTED,
                source="rithmic_feed",
                data={"system": rc.system_name, "host": rc.host},
            ))
            return True
        else:
            self._state = ConnectionState.ERROR
            log.error("Rithmic authentication failed")
            return False

    def disconnect(self) -> None:
        """Gracefully disconnect from Rithmic."""
        log.info("Disconnecting from Rithmic (state=%s)", self._state.name)
        self._running = False
        try:
            if self._session is not None:
                self._session.logout()
                self._session = None
        except Exception:
            log.exception("Error during Rithmic disconnect")

        self._state = ConnectionState.DISCONNECTED
        self._heartbeat_thread = None
        self._bus.publish(Event(
            type=EventType.RITHMIC_DISCONNECTED,
            source="rithmic_feed",
            data={"symbol": self._symbol, "ticks": self._ticks_received},
        ))

    def subscribe(self, symbol: str = "MESH5", exchange: str = "CME") -> bool:
        """Subscribe to MES market data. symbol defaults to front-month MESH5."""
        if self._state != ConnectionState.AUTHENTICATED:
            log.error("Cannot subscribe — not authenticated (state=%s)",
                      self._state.name)
            return False

        if symbol is None:
            symbol = detect_front_month()

        self._symbol = symbol
        self._exchange = exchange

        try:
            self._session.subscribeMarketData(exchange, symbol)
            self._state = ConnectionState.SUBSCRIBED
            log.info("Subscribed to %s:%s", exchange, symbol)
            return True
        except Exception:
            log.exception("Failed to subscribe to %s:%s", exchange, symbol)
            return False

    def start(self) -> None:
        """Start the feed in a background thread (connect + subscribe)."""
        self._running = True
        self._start_time = time.time()

        def _run():
            ok = self.connect()
            if ok:
                self.subscribe(self._symbol or detect_front_month(),
                               self._exchange)
                # rapi dispatches callbacks on its own threads; we just block
                while self._running:
                    time.sleep(1.0)

        self._main_thread = threading.Thread(
            target=_run, name="rithmic-feed", daemon=True
        )
        self._main_thread.start()
        log.info("RithmicFeed started")

    def stop(self) -> None:
        """Stop the feed and disconnect."""
        self._running = False
        self.disconnect()
        if self._main_thread:
            self._main_thread.join(timeout=5.0)
            self._main_thread = None
        log.info("RithmicFeed stopped")

    # -- rapi Callback overrides ----------------------------------------------

    def on_tick(self, ticker: str, exchange: str, price: float,
                qty: int, aggressor: bool, timestamp: float) -> None:
        tick = Tick(
            timestamp=timestamp,
            price=price,
            size=qty,
            aggressor="ASK" if aggressor else "BID",
        )
        self._process_tick(price, qty, aggressor, timestamp)

    def on_connected(self) -> None:
        log.info("[rapi] TCP connection established")

    def on_disconnected(self, reason: str) -> None:
        log.warning("[rapi] Disconnected: %s", reason)
        self._state = ConnectionState.DISCONNECTED
        self._bus.publish(Event(
            type=EventType.RITHMIC_DISCONNECTED,
            source="rithmic_feed",
            data={"reason": reason},
        ))
        if self._running:
            self._schedule_reconnect()

    def on_login_response(self, result: bool, text: str) -> None:
        log.info("[rapi] Login response: result=%s text=%s", result, text)

    def on_dom_update(self, ticker: str,
                      bids: List[Tuple[float, int]],
                      asks: List[Tuple[float, int]]) -> None:
        self._bus.publish(Event(
            type=EventType.DOM_UPDATE,
            source="rithmic_feed",
            data={"ticker": ticker, "bids": bids, "asks": asks},
        ))

    def on_bar_update(self, ticker: str, open: float, high: float,
                      low: float, close: float, volume: int) -> None:
        self._bus.publish(Event(
            type=EventType.FOOTPRINT_UPDATE,
            source="rithmic_feed",
            data={"ticker": ticker, "ohlc": [open, high, low, close],
                  "volume": volume},
        ))

    # -- Internal processing --------------------------------------------------

    def _process_tick(self, price: float, size: int,
                      aggressor: bool, timestamp: float) -> None:
        """Create a Tick object and publish TICK_RECEIVED + PRICE_UPDATE."""
        tick = Tick(
            timestamp=timestamp,
            price=price,
            size=size,
            aggressor="ASK" if aggressor else "BID",
        )
        self._ticks_received += 1
        self._last_tick_time = timestamp

        self._bus.publish(Event(
            type=EventType.PRICE_UPDATE,  # TICK_RECEIVED maps to PRICE_UPDATE
            source="rithmic_feed",
            data={
                "tick": tick,
                "symbol": self._symbol,
                "price": price,
                "size": size,
                "aggressor": tick.aggressor,
                "tick_count": self._ticks_received,
            },
        ))
        self._publish_price_update(tick)

    def _publish_price_update(self, tick: Tick) -> None:
        """Build and publish a market_data dict as a PRICE_UPDATE event."""
        market_data = {
            "symbol": self._symbol,
            "exchange": self._exchange,
            "price": tick.price,
            "size": tick.size,
            "aggressor": tick.aggressor,
            "timestamp": tick.timestamp,
            "tick_count": self._ticks_received,
            "is_buy": tick.is_buy,
            "is_sell": tick.is_sell,
        }
        self._bus.publish(Event(
            type=EventType.PRICE_UPDATE,
            source="rithmic_feed",
            data=market_data,
        ))

    # -- Heartbeat ------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread is not None:
            return

        def _loop():
            while self._running and self.is_connected:
                time.sleep(self.HEARTBEAT_INTERVAL)
                try:
                    if self._session:
                        self._session.heartbeat()
                except Exception:
                    log.warning("Heartbeat failed")

        self._heartbeat_thread = threading.Thread(
            target=_loop, name="rithmic-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    # -- Reconnection ---------------------------------------------------------

    def _schedule_reconnect(self) -> None:
        delay = self._reconnect_delay
        self._reconnect_delay = min(
            self._reconnect_delay * self.RECONNECT_MULT, self.RECONNECT_MAX
        )
        log.info("Reconnecting in %.1fs", delay)

        def _attempt():
            time.sleep(delay)
            if not self._running:
                return
            ok = self.connect()
            if ok and self._symbol:
                self.subscribe(self._symbol, self._exchange)

        threading.Thread(target=_attempt, name="rithmic-reconnect",
                         daemon=True).start()

    # -- Stats ----------------------------------------------------------------

    def get_stats(self) -> Dict:
        """Return feed diagnostics: ticks_received, uptime, last_tick_time."""
        uptime = (time.time() - self._start_time) if self._start_time else 0.0
        return {
            "state": self._state.name,
            "symbol": self._symbol,
            "ticks_received": self._ticks_received,
            "uptime": uptime,
            "last_tick_time": self._last_tick_time,
            "is_simulated": False,
        }

    def __repr__(self) -> str:
        return (f"RithmicFeed(state={self._state.name}, "
                f"symbol={self._symbol}, ticks={self._ticks_received})")


# ---------------------------------------------------------------------------
# SimulatedRithmicFeed — fallback when rapi is unavailable
# ---------------------------------------------------------------------------

class SimulatedRithmicFeed(RithmicCallbacks):
    """Generates realistic MES tick data using Brownian motion with mean reversion.

    Produces the same TICK_RECEIVED / PRICE_UPDATE events as RithmicFeed.
    Uses a background thread for continuous tick generation.

    Simulated features:
      - Gaussian price steps snapped to 0.25 tick grid
      - Mean reversion toward a drifting base price
      - Realistic volume distribution (small trades dominant, occasional large)
      - Burst mode: short periods of rapid high-volume ticks (algo patterns)
      - Simulated 5-level bid/ask DOM published as DOM_UPDATE events
      - Occasional large block trades

    Parameters
    ----------
    config : RithmicConfig
        Used for symbol and connection metadata (no real connection made).
    event_bus : EventBus
        Bus to publish events onto.
    start_price : float
        Starting MES price for the simulation.
    tick_size : float
        MES minimum tick (0.25 points).
    tick_interval_ms : int
        Average milliseconds between ticks.
    """

    _TICK_SIZE = 0.25
    _SPREAD_TICKS = 1          # 1-tick bid/ask spread
    _DOM_LEVELS = 5
    _BURST_PROB = 0.02          # probability of entering burst mode each tick
    _BURST_DURATION = (10, 40)  # burst lasts 10-40 ticks
    _LARGE_TRADE_PROB = 0.005   # probability of a block trade each tick
    _LARGE_TRADE_SIZE = (20, 100)

    def __init__(self, config: RithmicConfig, event_bus: EventBus,
                 start_price: float = 5000.0,
                 tick_size: float = 0.25,
                 tick_interval_ms: int = 100) -> None:
        self._config = config
        self._bus = event_bus
        self._base_price = start_price
        self._price = start_price
        self._tick_size = tick_size
        self._tick_interval_ms = tick_interval_ms
        self._state = ConnectionState.DISCONNECTED
        self._symbol: str = "MESH5"
        self._exchange: str = "CME"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ticks_received = 0
        self._start_time: Optional[float] = None
        self._last_tick_time: Optional[float] = None

        # DOM state — 5 levels each side
        self._bids: List[Tuple[float, int]] = []
        self._asks: List[Tuple[float, int]] = []
        self._rebuild_dom()

    # -- Properties -----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._state in (ConnectionState.AUTHENTICATED,
                               ConnectionState.SUBSCRIBED)

    # -- Public API -----------------------------------------------------------

    def connect(self) -> bool:
        """Simulate authentication (always succeeds)."""
        # Support both RithmicConfig variants: spec fields (username/system_name)
        # and the deployed config fields (user/system).
        user = getattr(self._config, "username",
                       getattr(self._config, "user", "sim_user")) or "sim_user"
        system = getattr(self._config, "system_name",
                         getattr(self._config, "system", "Simulated"))
        log.info("[SIM] Simulated Rithmic login as %s (system=%s)", user, system)
        self._state = ConnectionState.AUTHENTICATED
        self._bus.publish(Event(
            type=EventType.RITHMIC_CONNECTED,
            source="rithmic_feed_sim",
            data={"system": system, "simulated": True},
        ))
        return True

    def disconnect(self) -> None:
        """Stop the simulation."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._state = ConnectionState.DISCONNECTED
        self._bus.publish(Event(
            type=EventType.RITHMIC_DISCONNECTED,
            source="rithmic_feed_sim",
            data={"symbol": self._symbol, "ticks": self._ticks_received},
        ))
        log.info("[SIM] Disconnected")

    def subscribe(self, symbol: str = "MESH5", exchange: str = "CME") -> bool:
        """Start generating ticks for the given symbol."""
        if self._state != ConnectionState.AUTHENTICATED:
            log.error("[SIM] Not authenticated")
            return False
        self._symbol = symbol
        self._exchange = exchange
        self._state = ConnectionState.SUBSCRIBED
        log.info("[SIM] Subscribed to %s:%s", exchange, symbol)
        return True

    def start(self) -> None:
        """Start the feed: connect, subscribe, and launch the tick generator."""
        self._running = True
        self._start_time = time.time()
        self.connect()
        self.subscribe(detect_front_month())

        self._thread = threading.Thread(
            target=self._generate_ticks,
            name="sim-rithmic-ticks",
            daemon=True,
        )
        self._thread.start()
        log.info("[SIM] SimulatedRithmicFeed started (interval=%dms)",
                 self._tick_interval_ms)

    def stop(self) -> None:
        """Stop tick generation and disconnect."""
        self.disconnect()
        log.info("[SIM] SimulatedRithmicFeed stopped (%d ticks generated)",
                 self._ticks_received)

    # -- Tick generation ------------------------------------------------------

    def _generate_ticks(self) -> None:
        """Background thread: continuous Brownian-motion tick stream."""
        interval = self._tick_interval_ms / 1000.0
        burst_remaining = 0
        drift_price = self._base_price   # slow drift target

        while self._running:
            try:
                # Slow drift in base price (trend)
                drift_price += random.gauss(0, 0.05)
                drift_price = max(drift_price, 4000.0)

                # Mean reversion toward drift target
                mean_rev = (drift_price - self._price) * 0.003
                sigma = 0.30 if burst_remaining == 0 else 0.60
                step = random.gauss(mean_rev, sigma)

                # Snap to tick grid
                raw = self._price + step
                self._price = round(round(raw / self._tick_size) * self._tick_size, 2)
                self._price = max(self._price, 1000.0)

                # Burst mode: enter or continue
                if burst_remaining == 0 and random.random() < self._BURST_PROB:
                    burst_remaining = random.randint(*self._BURST_DURATION)
                if burst_remaining > 0:
                    burst_remaining -= 1
                    interval = 0.020   # 20 ms during burst
                else:
                    interval = self._tick_interval_ms / 1000.0

                # Large block trade
                if random.random() < self._LARGE_TRADE_PROB:
                    size = random.randint(*self._LARGE_TRADE_SIZE)
                else:
                    size = random.choices(
                        [1, 2, 3, 5, 10],
                        weights=[50, 25, 12, 8, 5]
                    )[0]

                aggressor = random.random() > 0.48  # slight buy bias

                self._process_tick(self._price, size, aggressor, time.time())

                # Occasionally refresh DOM
                if self._ticks_received % 10 == 0:
                    self._rebuild_dom()
                    self._publish_dom()

                time.sleep(interval)

            except Exception:
                log.exception("[SIM] Error generating tick")
                time.sleep(0.5)

    def _rebuild_dom(self) -> None:
        """Rebuild a simulated 5-level DOM around current price."""
        p = self._price
        ts = self._tick_size
        self._bids = [
            (round(p - (i + 1) * ts, 2),
             random.randint(5, 80))
            for i in range(self._DOM_LEVELS)
        ]
        self._asks = [
            (round(p + i * ts, 2),
             random.randint(5, 80))
            for i in range(self._DOM_LEVELS)
        ]

    def _publish_dom(self) -> None:
        self._bus.publish(Event(
            type=EventType.DOM_UPDATE,
            source="rithmic_feed_sim",
            data={
                "ticker": self._symbol,
                "bids": list(self._bids),
                "asks": list(self._asks),
            },
        ))

    # -- Internal processing (mirrors RithmicFeed) ----------------------------

    def _process_tick(self, price: float, size: int,
                      aggressor: bool, timestamp: float) -> None:
        tick = Tick(
            timestamp=timestamp,
            price=price,
            size=size,
            aggressor="ASK" if aggressor else "BID",
        )
        self._ticks_received += 1
        self._last_tick_time = timestamp

        self._bus.publish(Event(
            type=EventType.PRICE_UPDATE,
            source="rithmic_feed_sim",
            data={
                "tick": tick,
                "symbol": self._symbol,
                "price": price,
                "size": size,
                "aggressor": tick.aggressor,
                "tick_count": self._ticks_received,
            },
        ))
        self._publish_price_update(tick)

    def _publish_price_update(self, tick: Tick) -> None:
        market_data = {
            "symbol": self._symbol,
            "exchange": self._exchange,
            "price": tick.price,
            "size": tick.size,
            "aggressor": tick.aggressor,
            "timestamp": tick.timestamp,
            "tick_count": self._ticks_received,
            "is_buy": tick.is_buy,
            "is_sell": tick.is_sell,
            "simulated": True,
            "dom_bids": self._bids,
            "dom_asks": self._asks,
        }
        self._bus.publish(Event(
            type=EventType.PRICE_UPDATE,
            source="rithmic_feed_sim",
            data=market_data,
        ))

    # -- Stats ----------------------------------------------------------------

    def get_stats(self) -> Dict:
        uptime = (time.time() - self._start_time) if self._start_time else 0.0
        return {
            "state": self._state.name,
            "symbol": self._symbol,
            "ticks_received": self._ticks_received,
            "uptime": uptime,
            "last_tick_time": self._last_tick_time,
            "is_simulated": True,
            "current_price": self._price,
        }

    def __repr__(self) -> str:
        return (f"SimulatedRithmicFeed(state={self._state.name}, "
                f"symbol={self._symbol}, ticks={self._ticks_received}, "
                f"price={self._price})")


# ---------------------------------------------------------------------------
# Module-level rapi detection and ActiveFeed export
# ---------------------------------------------------------------------------

try:
    import rapi  # type: ignore[import]  # Rithmic licensed SDK
    ActiveFeed = RithmicFeed
    RITHMIC_AVAILABLE = True
    log.info("rapi package found — using live RithmicFeed")
except ImportError:
    ActiveFeed = SimulatedRithmicFeed  # type: ignore[misc]
    RITHMIC_AVAILABLE = False
    log.info("rapi not installed — using SimulatedRithmicFeed")
