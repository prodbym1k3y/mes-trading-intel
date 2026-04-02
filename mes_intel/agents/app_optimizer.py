"""App Optimizer Agent — monitors user behavior and optimizes the app over time.

Tracks:
  - Which tabs the user visits and how long they spend on each
  - Which signals the user acts on vs ignores
  - Which features get clicked vs scrolled past
  - Which signal types correlate with the user taking trades
  - Weight suggestions based on revealed preferences

After enough data, publishes OPTIMIZATION_SUGGESTION events and surfaces
recommendations in the Settings > Optimizer panel.

Users can approve or reject changes. Approved changes are persisted to config.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType

log = logging.getLogger(__name__)

# Minimum interaction events before generating suggestions
MIN_EVENTS_FOR_SUGGESTION = 30

# How often (seconds) to evaluate and potentially publish suggestions
EVAL_INTERVAL_SEC = 300.0  # 5 minutes


@dataclass
class TabStats:
    name: str
    visit_count: int = 0
    total_seconds: float = 0.0
    last_visited: float = 0.0

    @property
    def avg_dwell(self) -> float:
        return self.total_seconds / self.visit_count if self.visit_count else 0.0


@dataclass
class SignalInteractionStats:
    signal_type: str
    shown_count: int = 0
    acted_on: int = 0    # user opened trade journal or clicked signal
    ignored: int = 0

    @property
    def act_rate(self) -> float:
        total = self.acted_on + self.ignored
        return self.acted_on / total if total > 0 else 0.0


@dataclass
class OptimizationSuggestion:
    id: str
    category: str          # "weight_adjustment", "ui_layout", "feature_removal"
    description: str
    rationale: str
    proposed_change: dict  # machine-readable change spec
    confidence: float
    status: str = "pending"  # "pending", "approved", "rejected"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "rationale": self.rationale,
            "proposed_change": self.proposed_change,
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
        }


class AppOptimizer:
    """Monitors usage and generates optimization recommendations."""

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus

        # In-memory usage tracking
        self._tab_stats: dict[str, TabStats] = {}
        self._current_tab: Optional[str] = None
        self._tab_entered_ts: float = 0.0

        # Signal interaction tracking
        self._signal_stats: dict[str, SignalInteractionStats] = defaultdict(
            lambda: SignalInteractionStats(signal_type="unknown")
        )
        self._recent_signals: deque[dict] = deque(maxlen=200)

        # Feature click tracking
        self._feature_clicks: dict[str, int] = defaultdict(int)
        self._feature_impressions: dict[str, int] = defaultdict(int)

        # Pending suggestions
        self._suggestions: list[OptimizationSuggestion] = []
        self._suggestion_counter: int = 0

        # Regime-signal correlations
        self._regime_trades: dict[str, dict] = defaultdict(
            lambda: {"acted": 0, "ignored": 0}
        )

        # Timing
        self._last_eval_ts: float = 0.0
        self._total_events: int = 0

        # Subscribe
        self.bus.subscribe(EventType.UI_USAGE_EVENT, self._on_usage_event)
        self.bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal_generated)
        self.bus.subscribe(EventType.TRADE_OPENED, self._on_trade_opened)
        self.bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_regime_change)

        # Load historical usage from DB
        self._load_historical_usage()

        log.info("App Optimizer initialized")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_usage_event(self, event: Event):
        data = event.data
        evt_type     = data.get("event_type", "")
        tab_name     = data.get("tab_name", "")
        feature_name = data.get("feature_name", "")
        duration     = data.get("duration_seconds", 0.0)
        metadata     = data.get("metadata", {})

        self._total_events += 1

        # Persist to DB
        try:
            self.db.insert_usage_event(
                event_type=evt_type,
                tab_name=tab_name,
                feature_name=feature_name,
                duration_seconds=duration,
                metadata_json=json.dumps(metadata),
            )
        except Exception:
            pass

        if evt_type == "tab_enter":
            self._current_tab = tab_name
            self._tab_entered_ts = time.time()
            if tab_name not in self._tab_stats:
                self._tab_stats[tab_name] = TabStats(name=tab_name)
            self._tab_stats[tab_name].visit_count += 1
            self._tab_stats[tab_name].last_visited = time.time()

        elif evt_type == "tab_exit":
            if self._current_tab and self._tab_entered_ts:
                dwell = time.time() - self._tab_entered_ts
                if self._current_tab in self._tab_stats:
                    self._tab_stats[self._current_tab].total_seconds += dwell
            self._current_tab = None

        elif evt_type == "feature_click":
            if feature_name:
                self._feature_clicks[feature_name] += 1

        elif evt_type == "feature_impression":
            if feature_name:
                self._feature_impressions[feature_name] += 1

        elif evt_type == "signal_acted":
            sig_id = metadata.get("signal_id", "")
            sig_type = metadata.get("signal_type", "unknown")
            stats = self._signal_stats[sig_type]
            stats.signal_type = sig_type
            stats.acted_on += 1

        elif evt_type == "signal_dismissed":
            sig_type = metadata.get("signal_type", "unknown")
            stats = self._signal_stats[sig_type]
            stats.signal_type = sig_type
            stats.ignored += 1

        # Throttled evaluation
        now = time.time()
        if (now - self._last_eval_ts > EVAL_INTERVAL_SEC
                and self._total_events >= MIN_EVENTS_FOR_SUGGESTION):
            self._evaluate_and_suggest()
            self._last_eval_ts = now

    def _on_signal_generated(self, event: Event):
        data = event.data
        self._recent_signals.append({
            "signal_id": data.get("signal_id"),
            "direction": data.get("direction"),
            "confidence": data.get("confidence"),
            "regime": data.get("regime"),
            "timestamp": event.timestamp,
            "acted_on": False,
        })
        sig_type = f"{data.get('direction', 'FLAT')}_{data.get('regime', 'unknown')}"
        self._signal_stats[sig_type].shown_count += 1

    def _on_trade_opened(self, event: Event):
        """When user opens a trade, mark recent signals as acted on."""
        now = time.time()
        # Mark any signal generated in last 60 seconds as acted on
        for sig in self._recent_signals:
            if not sig["acted_on"] and (now - sig["timestamp"]) < 60.0:
                sig["acted_on"] = True
                sig_type = f"{sig['direction']}_{sig.get('regime', 'unknown')}"
                self._signal_stats[sig_type].acted_on += 1
                regime = sig.get("regime", "unknown")
                self._regime_trades[regime]["acted"] += 1
                break

    def _on_trade_result(self, event: Event):
        data = event.data
        regime = data.get("regime", "unknown")
        pnl    = data.get("pnl", 0.0)
        # Track regime-level P&L to weight suggestions
        if "pnl_total" not in self._regime_trades[regime]:
            self._regime_trades[regime]["pnl_total"] = 0.0
        self._regime_trades[regime]["pnl_total"] = (
            self._regime_trades[regime].get("pnl_total", 0.0) + pnl
        )

    def _on_regime_change(self, event: Event):
        regime = event.data.get("to_regime", "")
        if regime:
            self._regime_trades[regime].setdefault("acted", 0)

    # ------------------------------------------------------------------
    # Suggestion engine
    # ------------------------------------------------------------------

    def _evaluate_and_suggest(self):
        """Generate optimization suggestions from accumulated usage data."""
        suggestions = []

        # 1. Unused tabs: visited < 5% of sessions
        total_visits = sum(s.visit_count for s in self._tab_stats.values())
        if total_visits > 10:
            for name, stats in self._tab_stats.items():
                visit_rate = stats.visit_count / total_visits
                if visit_rate < 0.02 and stats.visit_count > 0:
                    suggestions.append(OptimizationSuggestion(
                        id=self._next_id(),
                        category="ui_layout",
                        description=f"Hide or demote '{name}' tab",
                        rationale=(
                            f"You've visited '{name}' only {stats.visit_count} times "
                            f"({visit_rate*100:.1f}% of tab switches). "
                            f"Consider hiding it to reduce clutter."
                        ),
                        proposed_change={"action": "hide_tab", "tab": name},
                        confidence=min(0.9, (1 - visit_rate) * 0.9),
                    ))

        # 2. Most-used tab: suggest making it the default
        if self._tab_stats:
            top_tab = max(self._tab_stats.values(), key=lambda s: s.total_seconds)
            if top_tab.total_seconds > 600:  # at least 10 minutes
                suggestions.append(OptimizationSuggestion(
                    id=self._next_id(),
                    category="ui_layout",
                    description=f"Make '{top_tab.name}' the default tab",
                    rationale=(
                        f"You spend {top_tab.total_seconds/60:.0f} min avg in '{top_tab.name}'. "
                        f"Opening to this tab saves time."
                    ),
                    proposed_change={"action": "set_default_tab", "tab": top_tab.name},
                    confidence=0.7,
                ))

        # 3. Signal type weight adjustments based on act rate
        for sig_type, stats in self._signal_stats.items():
            if stats.shown_count < 5:
                continue
            if stats.act_rate > 0.5:
                suggestions.append(OptimizationSuggestion(
                    id=self._next_id(),
                    category="weight_adjustment",
                    description=f"Increase weight for '{sig_type}' signals",
                    rationale=(
                        f"You act on {sig_type} signals {stats.act_rate*100:.0f}% of the time "
                        f"({stats.acted_on}/{stats.acted_on+stats.ignored}). High engagement."
                    ),
                    proposed_change={
                        "action": "adjust_signal_weight",
                        "signal_type": sig_type,
                        "direction": "increase",
                        "delta": 0.1,
                    },
                    confidence=min(0.85, stats.act_rate),
                ))
            elif stats.act_rate < 0.1 and stats.shown_count > 10:
                suggestions.append(OptimizationSuggestion(
                    id=self._next_id(),
                    category="weight_adjustment",
                    description=f"Lower threshold/weight for '{sig_type}' signals",
                    rationale=(
                        f"You ignore {sig_type} signals {(1-stats.act_rate)*100:.0f}% of the time. "
                        f"Raising the bar may reduce noise."
                    ),
                    proposed_change={
                        "action": "adjust_signal_weight",
                        "signal_type": sig_type,
                        "direction": "decrease",
                        "delta": 0.1,
                    },
                    confidence=min(0.8, 1 - stats.act_rate),
                ))

        # 4. Regime-specific coaching
        for regime, data in self._regime_trades.items():
            pnl = data.get("pnl_total", 0.0)
            acted = data.get("acted", 0)
            if acted >= 3 and pnl < -10:
                suggestions.append(OptimizationSuggestion(
                    id=self._next_id(),
                    category="weight_adjustment",
                    description=f"Reduce signal frequency in '{regime}' regime",
                    rationale=(
                        f"Your {acted} trades in '{regime}' regime lost "
                        f"${abs(pnl):.2f} total. Consider being more selective."
                    ),
                    proposed_change={
                        "action": "regime_confidence_boost",
                        "regime": regime,
                        "min_confidence_delta": +0.05,
                    },
                    confidence=0.65,
                ))

        # Remove duplicates (same category + description already suggested)
        existing_descs = {s.description for s in self._suggestions}
        new_suggestions = [s for s in suggestions if s.description not in existing_descs]

        if new_suggestions:
            self._suggestions.extend(new_suggestions)
            for s in new_suggestions:
                log.info("Optimizer suggestion: [%s] %s", s.category, s.description)
                self.bus.publish(Event(
                    type=EventType.OPTIMIZATION_SUGGESTION,
                    source="app_optimizer",
                    data=s.to_dict(),
                ))

    # ------------------------------------------------------------------
    # Public API (for UI)
    # ------------------------------------------------------------------

    def get_suggestions(self, status: str = "pending") -> list[dict]:
        return [s.to_dict() for s in self._suggestions if s.status == status]

    def get_all_suggestions(self) -> list[dict]:
        return [s.to_dict() for s in self._suggestions]

    def approve_suggestion(self, suggestion_id: str) -> Optional[dict]:
        """Approve a suggestion and apply the change."""
        for s in self._suggestions:
            if s.id == suggestion_id and s.status == "pending":
                s.status = "approved"
                self._apply_change(s.proposed_change)
                log.info("Optimizer: approved suggestion '%s'", s.description)
                return s.to_dict()
        return None

    def reject_suggestion(self, suggestion_id: str) -> Optional[dict]:
        for s in self._suggestions:
            if s.id == suggestion_id and s.status == "pending":
                s.status = "rejected"
                log.info("Optimizer: rejected suggestion '%s'", s.description)
                return s.to_dict()
        return None

    def get_tab_summary(self) -> list[dict]:
        """Return tab usage stats sorted by total dwell time."""
        return sorted(
            [
                {
                    "tab": s.name,
                    "visits": s.visit_count,
                    "total_min": round(s.total_seconds / 60, 1),
                    "avg_sec": round(s.avg_dwell, 0),
                }
                for s in self._tab_stats.values()
            ],
            key=lambda x: x["total_min"],
            reverse=True,
        )

    def get_signal_engagement(self) -> list[dict]:
        """Return signal engagement rates."""
        return sorted(
            [
                {
                    "type": s.signal_type,
                    "shown": s.shown_count,
                    "acted": s.acted_on,
                    "ignored": s.ignored,
                    "act_rate_pct": round(s.act_rate * 100, 1),
                }
                for s in self._signal_stats.values()
                if s.shown_count > 0
            ],
            key=lambda x: x["act_rate_pct"],
            reverse=True,
        )

    def record_tab_change(self, from_tab: str, to_tab: str):
        """Called by the UI when the user switches tabs."""
        now = time.time()
        if from_tab:
            dwell = now - self._tab_entered_ts if self._tab_entered_ts else 0
            self.bus.publish(Event(
                type=EventType.UI_USAGE_EVENT,
                source="app_optimizer",
                data={
                    "event_type": "tab_exit",
                    "tab_name": from_tab,
                    "duration_seconds": dwell,
                    "metadata": {},
                },
            ))
        self._tab_entered_ts = now
        self.bus.publish(Event(
            type=EventType.UI_USAGE_EVENT,
            source="app_optimizer",
            data={
                "event_type": "tab_enter",
                "tab_name": to_tab,
                "duration_seconds": 0.0,
                "metadata": {},
            },
        ))

    def record_feature_click(self, feature_name: str, metadata: Optional[dict] = None):
        """Called by the UI when any interactive feature is clicked."""
        self.bus.publish(Event(
            type=EventType.UI_USAGE_EVENT,
            source="app_optimizer",
            data={
                "event_type": "feature_click",
                "tab_name": self._current_tab or "",
                "feature_name": feature_name,
                "duration_seconds": 0.0,
                "metadata": metadata or {},
            },
        ))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_change(self, change: dict):
        """Apply an approved change to the config."""
        action = change.get("action", "")
        try:
            if action == "adjust_signal_weight":
                sig_type = change.get("signal_type", "")
                direction = change.get("direction", "increase")
                delta = change.get("delta", 0.1)
                weights = self.config.signals.weights
                if sig_type in weights:
                    if direction == "increase":
                        weights[sig_type] = min(2.0, weights[sig_type] + delta)
                    else:
                        weights[sig_type] = max(0.1, weights[sig_type] - delta)
                    self.config.save()
                    log.info("Weight %s for %s → %.2f", direction, sig_type, weights[sig_type])
        except Exception as exc:
            log.warning("Could not apply optimizer change: %s", exc)

    def _next_id(self) -> str:
        self._suggestion_counter += 1
        return f"opt_{int(time.time())}_{self._suggestion_counter}"

    def _load_historical_usage(self):
        """Bootstrap in-memory stats from DB on startup."""
        try:
            summary = self.db.get_tab_time_summary()
            for row in summary:
                name = row["tab_name"]
                self._tab_stats[name] = TabStats(
                    name=name,
                    visit_count=row["visit_count"],
                    total_seconds=row["total_seconds"],
                )
        except Exception:
            pass
