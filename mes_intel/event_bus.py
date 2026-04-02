"""Event bus for inter-agent communication.

Lightweight pub/sub system. Agents publish events, other agents subscribe to
event types they care about. All communication is local and synchronous by
default, with optional async dispatch.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional
from queue import Queue

log = logging.getLogger(__name__)


class EventType(Enum):
    # Signal Engine
    SIGNAL_GENERATED = auto()
    SIGNAL_EXPIRED = auto()
    STRATEGY_SCORE = auto()
    ENSEMBLE_UPDATE = auto()

    # Chart Monitor
    PRICE_UPDATE = auto()
    VOLUME_DELTA = auto()
    ORDER_FLOW_UPDATE = auto()
    FOOTPRINT_UPDATE = auto()
    VOLUME_PROFILE_UPDATE = auto()

    # Trade Journal
    TRADE_OPENED = auto()
    TRADE_CLOSED = auto()
    TRADE_GRADED = auto()
    DAILY_STATS_UPDATE = auto()

    # Meta-Learner
    WEIGHT_ADJUSTMENT = auto()
    MODEL_RETRAINED = auto()
    REGIME_CHANGE = auto()
    PERFORMANCE_REPORT = auto()

    # News Scanner
    NEWS_ALERT = auto()
    SENTIMENT_UPDATE = auto()
    TRUMP_ALERT = auto()

    # System
    AGENT_STARTED = auto()
    AGENT_STOPPED = auto()
    ERROR = auto()
    HEARTBEAT = auto()

    # --- Phase 2 ---

    # Dark Pool Agent
    DARK_POOL_ALERT = auto()        # dark pool significant print
    BIG_TRADE_ALERT = auto()        # big trade detected

    # Confluence Engine
    CONFLUENCE_ALERT = auto()       # confluence zone reached

    # News (enhanced)
    BREAKING_NEWS = auto()          # high-impact breaking news with flash

    # UI / Audio
    AUDIO_ALERT = auto()            # sound effect trigger
    LAYOUT_CHANGED = auto()         # UI layout saved/loaded
    VANITY_TOGGLE = auto()          # toggle vanity elements

    # Data Feeds
    RITHMIC_CONNECTED = auto()      # Rithmic feed connected
    RITHMIC_DISCONNECTED = auto()   # Rithmic feed disconnected
    ATAS_DATA_LOADED = auto()       # ATAS CSV data imported
    DOM_UPDATE = auto()             # Level 2 / DOM data update

    # ML Pipeline
    ML_TRAINING_STARTED = auto()    # ML training began
    ML_TRAINING_COMPLETE = auto()   # ML training finished

    # Institutional Flow
    INSTITUTIONAL_FLOW = auto()     # institutional flow detected

    # Cross-Asset Intelligence (Phase 3)
    CROSS_ASSET_UPDATE = auto()     # cross-asset prices + composite signal updated
    OPTIONS_DATA_UPDATE = auto()    # GEX levels + options chain freshly computed

    # Learning System (Phase 3)
    LESSON_LEARNED = auto()         # any agent learned something new
    PATTERN_DISCOVERED = auto()     # new recurring pattern identified
    AGENT_REPORT = auto()           # periodic status broadcast from each agent
    TRADE_RESULT = auto()           # full trade context + outcome sent to all agents after close

    # Market Brain (Phase 4)
    MARKET_REGIME_CHANGE = auto()   # regime transition detected (trending/ranging/volatile/quiet)
    QUANT_SIGNAL = auto()           # quantitative signal from Market Brain (RSI, MACD, BB, etc.)
    HISTORICAL_PATTERN_MATCH = auto()  # current price action matches a historical analog

    # App Optimizer (Phase 4)
    UI_USAGE_EVENT = auto()         # user interaction tracked (tab viewed, feature clicked, etc.)
    OPTIMIZATION_SUGGESTION = auto()  # optimizer recommends a UI or weight change


@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)
    source: str = ""
    timestamp: float = field(default_factory=time.time)
    priority: int = 0  # higher = more urgent

    def __repr__(self):
        return f"Event({self.type.name}, src={self.source}, t={self.timestamp:.1f})"


# Type alias for handlers
EventHandler = Callable[[Event], None]
AsyncEventHandler = Callable[[Event], Any]


class EventBus:
    """Central event bus for agent communication.

    Supports both sync and async handlers. Events are dispatched immediately
    to all subscribers. High-priority events (priority > 5) are dispatched
    first.
    """

    def __init__(self, max_history: int = 1000):
        self._handlers: dict[EventType, list[tuple[int, EventHandler | AsyncEventHandler]]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history = max_history
        self._event_queue: Queue = Queue()
        self._running = False

    def subscribe(self, event_type: EventType, handler: EventHandler | AsyncEventHandler, priority: int = 0):
        """Subscribe a handler to an event type. Higher priority handlers run first."""
        self._handlers[event_type].append((priority, handler))
        self._handlers[event_type].sort(key=lambda x: -x[0])
        log.debug("Subscribed %s to %s (priority=%d)", handler.__name__, event_type.name, priority)

    def unsubscribe(self, event_type: EventType, handler: EventHandler | AsyncEventHandler):
        """Remove a handler from an event type."""
        self._handlers[event_type] = [
            (p, h) for p, h in self._handlers[event_type] if h is not handler
        ]

    def publish(self, event: Event):
        """Publish an event to all subscribers synchronously."""
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers = self._handlers.get(event.type, [])
        for _priority, handler in handlers:
            try:
                result = handler(event)
                # If handler is async, schedule it
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        asyncio.run(result)
            except Exception:
                log.exception("Handler %s failed for %s", handler.__name__, event.type.name)

    def publish_async(self, event: Event):
        """Queue an event for async dispatch."""
        self._event_queue.put(event)

    def get_history(self, event_type: Optional[EventType] = None, limit: int = 50) -> list[Event]:
        """Get recent event history, optionally filtered by type."""
        events = self._history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]

    def clear_history(self):
        self._history.clear()


# Singleton instance
bus = EventBus()
