"""Signal Engine Agent — generates trade signals using ensemble of quantitative strategies.

Runs all strategies, combines scores via weighted voting, and emits
high-confidence signals through the event bus. Designed for a user who
takes few trades — thresholds are set high.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType
from ..orderflow import VolumeProfile, FootprintChart
from ..strategies.base import Strategy, StrategyResult
from ..strategies.mean_reversion import MeanReversionStrategy
from ..strategies.momentum import MomentumStrategy
from ..strategies.stat_arb import StatArbStrategy
from ..strategies.order_flow import OrderFlowStrategy
from ..strategies.gex_model import GEXModelStrategy
from ..strategies.hmm_regime import HMMRegimeStrategy
from ..strategies.ml_scorer import MLScorer, engineer_features

log = logging.getLogger(__name__)


@dataclass
class Signal:
    """A generated trade signal."""
    id: Optional[int] = None
    timestamp: float = 0.0
    direction: str = "FLAT"
    confidence: float = 0.0
    ensemble_score: float = 0.0
    strategies_agree: int = 0
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    regime: str = "unknown"
    status: str = "active"
    strategy_results: list[StrategyResult] = field(default_factory=list)

    @property
    def risk_reward(self) -> Optional[float]:
        if self.entry_price and self.stop_price and self.target_price:
            risk = abs(self.entry_price - self.stop_price)
            reward = abs(self.target_price - self.entry_price)
            return reward / risk if risk > 0 else None
        return None

    def to_db_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "direction": self.direction,
            "confidence": self.confidence,
            "ensemble_score": self.ensemble_score,
            "strategies_agree": self.strategies_agree,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "regime": self.regime,
            "status": self.status,
            "strategy_scores": {
                sr.name: {"score": sr.score, "direction": sr.direction, "meta": sr.meta}
                for sr in self.strategy_results
            },
        }


class SignalEngine:
    """Ensemble signal engine that combines multiple quantitative strategies."""

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus
        self._last_signal_time: float = 0
        self._signal_count: int = 0

        # Initialize strategies
        self.strategies: dict[str, Strategy] = {
            "mean_reversion": MeanReversionStrategy(),
            "momentum": MomentumStrategy(),
            "stat_arb": StatArbStrategy(),
            "order_flow": OrderFlowStrategy(),
            "gex_model": GEXModelStrategy(),
            "hmm_regime": HMMRegimeStrategy(),
        }
        self.ml_scorer = MLScorer()

        # Subscribe to events
        self.bus.subscribe(EventType.PRICE_UPDATE, self._on_price_update)
        self.bus.subscribe(EventType.WEIGHT_ADJUSTMENT, self._on_weight_adjustment)
        self.bus.subscribe(EventType.LESSON_LEARNED, self._on_lesson_learned)
        self.bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.QUANT_SIGNAL, self._on_quant_signal)

        # Regime-aware threshold adjustments
        self._regime_threshold_delta: dict[str, float] = {
            "trending":  -0.05,   # easier in trending (momentum signals worth more)
            "ranging":   +0.05,   # harder in ranging (more noise)
            "volatile":  +0.10,   # much harder in volatile (high noise)
            "quiet":     +0.03,
            "breakout":  -0.03,
        }
        self._current_regime: str = "unknown"
        self._current_quant: dict = {}

        # Per-signal-type win rate tracking for auto-confidence adjustment
        self._signal_type_wins:  dict[str, int] = {}
        self._signal_type_total: dict[str, int] = {}

        log.info("Signal Engine initialized with %d strategies", len(self.strategies))

    def evaluate(self, market_data: dict) -> Optional[Signal]:
        """Run all strategies and generate a signal if conditions are met.

        Returns a Signal if ensemble confidence exceeds threshold, else None.
        """
        now = time.time()
        cfg = self.config.signals

        # Cooldown check
        if now - self._last_signal_time < cfg.signal_cooldown_sec:
            return None

        # Run each strategy
        results: list[StrategyResult] = []
        for name, strategy in self.strategies.items():
            try:
                result = strategy.evaluate(market_data)
                results.append(result)
                self.bus.publish(Event(
                    type=EventType.STRATEGY_SCORE,
                    source="signal_engine",
                    data={"strategy": name, "score": result.score,
                          "confidence": result.confidence, "direction": result.direction},
                ))
            except Exception:
                log.exception("Strategy %s failed", name)

        if not results:
            return None

        # Get regime from HMM
        regime = "unknown"
        for r in results:
            if r.name == "hmm_regime" and "regime" in r.meta:
                regime = r.meta["regime"]
                break

        # Run ML scorer with strategy results as features
        ml_data = dict(market_data)
        ml_data["strategy_results"] = [
            {"score": r.score, "direction": r.direction} for r in results
        ]
        ml_result = self.ml_scorer.evaluate(ml_data)
        results.append(ml_result)

        # Inject quant context into market_data for strategies that can use it
        if self._current_quant:
            market_data["quant"] = self._current_quant
            market_data["regime"] = self._current_regime

        # Ensemble scoring
        signal = self._ensemble_vote(results, regime, cfg)

        # Regime-aware confidence threshold
        regime_delta = self._regime_threshold_delta.get(self._current_regime, 0.0)
        effective_min_conf = max(0.1, cfg.min_confidence + regime_delta)

        if signal and signal.confidence >= effective_min_conf and signal.strategies_agree >= cfg.min_strategies_agree:
            signal.timestamp = now
            signal.regime = regime

            # Persist
            signal.id = self.db.insert_signal(signal.to_db_dict())
            self._last_signal_time = now
            self._signal_count += 1

            # Publish
            self.bus.publish(Event(
                type=EventType.SIGNAL_GENERATED,
                source="signal_engine",
                data={
                    "signal_id": signal.id,
                    "direction": signal.direction,
                    "confidence": signal.confidence,
                    "ensemble_score": signal.ensemble_score,
                    "entry": signal.entry_price,
                    "stop": signal.stop_price,
                    "target": signal.target_price,
                    "regime": regime,
                    "risk_reward": signal.risk_reward,
                },
            ))

            log.info(
                "SIGNAL: %s | conf=%.2f | score=%.3f | agree=%d | R:R=%.1f | regime=%s",
                signal.direction, signal.confidence, signal.ensemble_score,
                signal.strategies_agree,
                signal.risk_reward or 0,
                regime,
            )
            return signal

        # Publish ensemble update even if no signal
        self.bus.publish(Event(
            type=EventType.ENSEMBLE_UPDATE,
            source="signal_engine",
            data={
                "scores": {r.name: r.score for r in results},
                "confidences": {r.name: r.confidence for r in results},
                "directions": {r.name: r.direction for r in results},
                "regime": regime,
            },
        ))

        return None

    def _ensemble_vote(self, results: list[StrategyResult], regime: str,
                       cfg) -> Signal:
        """Weighted ensemble voting across strategy results."""
        weights = cfg.weights

        weighted_scores = []
        directions = {"LONG": 0.0, "SHORT": 0.0, "FLAT": 0.0}
        entries = []
        stops = []
        targets = []

        for r in results:
            w = weights.get(r.name, 1.0)
            weighted_scores.append(r.score * r.confidence * w)

            if r.direction != "FLAT":
                directions[r.direction] += w * r.confidence

            if r.entry_price:
                entries.append(r.entry_price)
            if r.stop_price:
                stops.append(r.stop_price)
            if r.target_price:
                targets.append(r.target_price)

        total_weight = sum(weights.get(r.name, 1.0) for r in results)
        ensemble_score = sum(weighted_scores) / max(total_weight, 1e-10)

        # Determine direction
        if directions["LONG"] > directions["SHORT"] and directions["LONG"] > directions["FLAT"]:
            direction = "LONG"
        elif directions["SHORT"] > directions["LONG"] and directions["SHORT"] > directions["FLAT"]:
            direction = "SHORT"
        else:
            direction = "FLAT"

        # Count agreeing strategies
        strategies_agree = sum(1 for r in results if r.direction == direction and r.is_actionable)

        # Confidence = weighted average of individual confidences × agreement ratio
        avg_confidence = sum(r.confidence * weights.get(r.name, 1.0) for r in results) / max(total_weight, 1e-10)
        agreement_ratio = strategies_agree / max(len(results), 1)
        confidence = avg_confidence * (0.5 + 0.5 * agreement_ratio)

        # Aggregate entry/stop/target (median of suggestions)
        entry = float(sorted(entries)[len(entries) // 2]) if entries else None
        stop = float(sorted(stops)[len(stops) // 2]) if stops else None
        target = float(sorted(targets)[len(targets) // 2]) if targets else None

        return Signal(
            direction=direction,
            confidence=confidence,
            ensemble_score=ensemble_score,
            strategies_agree=strategies_agree,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            strategy_results=results,
        )

    def _on_price_update(self, event: Event):
        """Handle incoming price updates — trigger re-evaluation."""
        # Market data is assembled by the chart monitor and passed here
        pass

    def _on_weight_adjustment(self, event: Event):
        """Handle weight adjustments from meta-learner."""
        new_weights = event.data.get("weights", {})
        for name, weight in new_weights.items():
            if name in self.config.signals.weights:
                self.config.signals.weights[name] = weight
        log.info("Strategy weights updated: %s", new_weights)

    def _on_lesson_learned(self, event: Event):
        """Receive a lesson from meta-learner and persist it."""
        data = event.data
        target = data.get("target_agent", "")
        if target not in ("signal_engine", "all"):
            return
        lesson_type = data.get("lesson_type", "")
        description = data.get("description", "")
        impact = data.get("impact_score", 0.0)
        try:
            self.db.upsert_agent_knowledge(
                agent_name="signal_engine",
                knowledge_type=f"lesson:{lesson_type}",
                key=f"ts_{int(event.timestamp)}",
                value={"description": description, "impact": impact},
                confidence=min(1.0, abs(impact)),
            )
        except Exception:
            pass

    def _on_trade_result(self, event: Event):
        """Learn from final trade outcome — adjust internal thresholds."""
        outcome = event.data.get("outcome", "")
        regime = event.data.get("regime", "")
        pnl = event.data.get("pnl", 0)
        if not outcome or not regime:
            return
        # Persist regime-outcome pattern for future signal filtering
        try:
            self.db.upsert_agent_knowledge(
                agent_name="signal_engine",
                knowledge_type="regime_outcome",
                key=f"{regime}_{outcome}",
                value={"regime": regime, "outcome": outcome, "pnl": pnl},
                confidence=min(1.0, abs(pnl) / 20.0),
            )
        except Exception:
            pass
        # Update per-signal-type win rate
        sig_types = event.data.get("signal_types", [])
        win = outcome == "win"
        for st in sig_types:
            self._signal_type_total[st] = self._signal_type_total.get(st, 0) + 1
            if win:
                self._signal_type_wins[st] = self._signal_type_wins.get(st, 0) + 1

    def _on_regime_change(self, event: Event):
        """Receive regime transition from Market Brain — adjust signal thresholds."""
        new_regime = event.data.get("to_regime", "unknown")
        if new_regime != self._current_regime:
            log.info("Signal Engine: regime → %s (was %s)", new_regime, self._current_regime)
            self._current_regime = new_regime

    def _on_quant_signal(self, event: Event):
        """Receive full quant state from Market Brain for strategy enrichment."""
        self._current_quant = event.data
        # Update regime from quant data if more recent
        regime = event.data.get("regime", "")
        if regime and regime != "unknown":
            self._current_regime = regime

    def get_recent_signals(self, limit: int = 20) -> list[dict]:
        return self.db.get_signals(limit=limit)

    @property
    def signal_count(self) -> int:
        return self._signal_count
