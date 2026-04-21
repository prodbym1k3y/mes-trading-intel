"""Autonomous optimization loop for bounded self-learning in paper mode.

This module tunes only non-critical runtime parameters and persists every
decision for auditability and rollback.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import AppConfig
from .database import Database
from .event_bus import EventBus, Event, EventType

log = logging.getLogger(__name__)

PHOENIX_TZ = ZoneInfo("America/Phoenix")


@dataclass
class OptimizationSummary:
    status: str
    reason: str
    metrics: dict[str, Any]
    changes: list[dict[str, Any]]


class AutonomousOptimizer:
    """Bounded self-optimization service for paper-mode learning.

    The optimizer adjusts only non-critical parameters and records every change.
    """

    AGENT_NAME = "AutonomousOptimizer"

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus
        # Snapshot startup defaults used as the "baseline" for gradual policy fading.
        self._initial_params: dict[str, Any] = {
            "min_confidence": config.signals.min_confidence,
            "eval_interval_ms": config.signals.eval_interval_ms,
            "min_strategies_agree": config.signals.min_strategies_agree,
            "signal_cooldown_sec": config.signals.signal_cooldown_sec,
        }

    def maybe_run_daily_optimization(self) -> OptimizationSummary:
        if not self.config.autonomy.enabled:
            return OptimizationSummary("skipped", "autonomy_disabled", {}, [])
        if self.config.autonomy.paper_mode_only and not self._is_paper_mode():
            return OptimizationSummary("skipped", "paper_mode_required", {}, [])
        if self.db.get_pending_autonomous_changes():
            return OptimizationSummary("skipped", "pending_validation", {}, [])

        # Apply policy decay: remove stale context policies before optimization.
        decay_summary = self._decay_stale_policies()
        if decay_summary.get("removed", 0) > 0:
            self.db.insert_learning_history(
                self.AGENT_NAME,
                "policy_decay",
                f"Decayed {decay_summary['removed']} stale context policies",
                impact_score=0.0,
            )

        now = self._now_phoenix()
        day_key = now.date().isoformat()
        last_run = self._read_state("last_daily_run")
        if last_run == day_key:
            return OptimizationSummary("skipped", "already_ran_today", {}, [])

        trades = [
            trade for trade in self.db.get_trades(limit=200)
            if trade.get("status") == "closed"
        ]
        if len(trades) < self.config.autonomy.min_closed_trades:
            metrics = self._trade_metrics(trades)
            self._write_state("last_daily_run", day_key)
            self.db.insert_learning_history(
                self.AGENT_NAME,
                "optimization_skipped",
                (
                    f"Skipped optimization due to insufficient closed trades: "
                    f"{len(trades)} < {self.config.autonomy.min_closed_trades}"
                ),
                impact_score=0.0,
            )
            return OptimizationSummary("skipped", "insufficient_closed_trades", metrics, [])

        metrics = self._trade_metrics(trades)
        focus_context, focus_metrics = self._select_focus_context(metrics)
        changes = self._propose_changes(focus_metrics, focus_context)

        applied_changes: list[dict[str, Any]] = []
        if changes:
            for change in changes:
                self._apply_change(change, metrics, focus_context)
                applied_changes.append(change)
            self.config.save()
            self.db.insert_learning_history(
                self.AGENT_NAME,
                "daily_optimization",
                (
                    f"Applied {len(applied_changes)} bounded parameter changes "
                    f"(context={focus_context})"
                ),
                impact_score=focus_metrics.get("win_rate", 0.0),
            )
            self.bus.publish(Event(
                type=EventType.AUTONOMY_POLICY_CHANGED,
                data={
                    "action": "applied",
                    "context": focus_context,
                    "change_count": len(applied_changes),
                    "changes": [{"target": c["target"], "new_value": c["new_value"]} for c in applied_changes],
                    "metrics": {"win_rate": focus_metrics.get("win_rate"), "avg_pnl": focus_metrics.get("avg_pnl")},
                },
                source=self.AGENT_NAME,
            ))
        else:
            self.db.insert_learning_history(
                self.AGENT_NAME,
                "daily_optimization",
                f"No parameter changes were necessary (context={focus_context})",
                impact_score=focus_metrics.get("win_rate", 0.0),
            )

        self.db.upsert_agent_knowledge(
            self.AGENT_NAME,
            "optimizer_state",
            "latest_metrics",
            metrics,
            confidence=0.8,
        )
        self.db.upsert_agent_knowledge(
            self.AGENT_NAME,
            "optimizer_state",
            "latest_focus_context",
            {"value": focus_context, "metrics": focus_metrics},
            confidence=0.8,
        )
        self._write_state("last_daily_run", day_key)
        return OptimizationSummary("completed", "ok", metrics, applied_changes)

    def maybe_apply_runtime_context_policy(self) -> dict[str, Any]:
        """Apply context-specific tuning policy for the current market context.

        This activates per-context parameter bands learned over time.
        """
        if not self.config.autonomy.enabled:
            return {"status": "skipped", "reason": "autonomy_disabled"}

        context_key = self._runtime_context_key()
        policy_map = self._get_context_policy_map()
        if not policy_map:
            policy_map = {
                context_key: {
                    "min_confidence": self.config.signals.min_confidence,
                    "eval_interval_ms": self.config.signals.eval_interval_ms,
                    "min_strategies_agree": self.config.signals.min_strategies_agree,
                    "signal_cooldown_sec": self.config.signals.signal_cooldown_sec,
                    "updated_at": time.time(),
                    "last_active_timestamp": time.time(),
                }
            }
            self._set_context_policy_map(policy_map)

        policy = policy_map.get(context_key)
        if not policy:
            return {"status": "skipped", "reason": "no_policy_for_context", "context": context_key}

        # Update last active timestamp to track when this context was last used.
        policy["last_active_timestamp"] = time.time()

        changed = False
        target_conf = float(policy.get("min_confidence", self.config.signals.min_confidence))
        target_interval = int(policy.get("eval_interval_ms", self.config.signals.eval_interval_ms))
        target_agree = int(policy.get("min_strategies_agree", self.config.signals.min_strategies_agree))
        target_cooldown = int(policy.get("signal_cooldown_sec", self.config.signals.signal_cooldown_sec))
        target_conf, target_interval = self._apply_regime_guardrails(
            context_key,
            target_conf,
            target_interval,
        )
        target_agree = max(
            self.config.autonomy.min_strategies_agree_floor,
            min(self.config.autonomy.min_strategies_agree_ceiling, target_agree),
        )
        target_cooldown = max(
            self.config.autonomy.cooldown_floor_sec,
            min(self.config.autonomy.cooldown_ceiling_sec, target_cooldown),
        )

        if (
            abs(target_conf - float(policy.get("min_confidence", target_conf))) > 1e-9
            or target_interval != int(policy.get("eval_interval_ms", target_interval))
            or target_agree != int(policy.get("min_strategies_agree", target_agree))
            or target_cooldown != int(policy.get("signal_cooldown_sec", target_cooldown))
        ):
            policy["min_confidence"] = target_conf
            policy["eval_interval_ms"] = target_interval
            policy["min_strategies_agree"] = target_agree
            policy["signal_cooldown_sec"] = target_cooldown
            policy["updated_at"] = time.time()
            policy_map[context_key] = policy
            self._set_context_policy_map(policy_map)

        if abs(self.config.signals.min_confidence - target_conf) > 1e-9:
            self.config.signals.min_confidence = target_conf
            changed = True
        if self.config.signals.eval_interval_ms != target_interval:
            self.config.signals.eval_interval_ms = target_interval
            changed = True
        if self.config.signals.min_strategies_agree != target_agree:
            self.config.signals.min_strategies_agree = target_agree
            changed = True
        if self.config.signals.signal_cooldown_sec != target_cooldown:
            self.config.signals.signal_cooldown_sec = target_cooldown
            changed = True

        if changed:
            self.config.save()

        self.db.upsert_agent_knowledge(
            self.AGENT_NAME,
            "optimizer_state",
            "active_runtime_context",
            {
                "context": context_key,
                "policy": policy,
                "applied": changed,
                "timestamp": time.time(),
            },
            confidence=0.9,
        )

        return {
            "status": "completed",
            "context": context_key,
            "applied": changed,
            "policy": policy,
        }

    def maybe_validate_recent_changes(self) -> dict[str, Any]:
        if not self.config.autonomy.enabled:
            return {"status": "skipped", "reason": "autonomy_disabled"}

        pending = self.db.get_pending_autonomous_changes()
        if not pending:
            return {"status": "skipped", "reason": "no_pending_changes"}

        results: list[dict[str, Any]] = []
        for change in pending:
            result = self._validate_change(change)
            results.append(result)

        rolled_back = sum(1 for result in results if result.get("status") == "rolled_back")
        validated = sum(1 for result in results if result.get("status") == "validated")
        return {
            "status": "completed",
            "checked": len(results),
            "validated": validated,
            "rolled_back": rolled_back,
            "results": results,
        }

    def maybe_run_weekly_drift_report(self) -> dict[str, Any]:
        if not self.config.autonomy.enabled:
            return {"status": "skipped", "reason": "autonomy_disabled"}

        now = self._now_phoenix()
        if now.weekday() != self.config.autonomy.weekly_report_weekday:
            return {"status": "skipped", "reason": "not_report_day"}

        week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        if self._read_state("last_weekly_report") == week_key:
            return {"status": "skipped", "reason": "already_ran_this_week"}

        closed_trades = [
            trade for trade in self.db.get_trades(limit=500)
            if trade.get("status") == "closed"
        ]
        metrics = self._trade_metrics(closed_trades)
        recent_changes = self.db.get_autonomous_changes(limit=20)
        pending_changes = self.db.get_pending_autonomous_changes()
        report = {
            "timestamp": time.time(),
            "report_type": "weekly_drift",
            "period_key": week_key,
            "summary_json": json.dumps({
                "metrics": metrics,
                "recent_change_count": len(recent_changes),
                "pending_change_count": len(pending_changes),
                "recent_changes": recent_changes[:10],
            }),
        }
        self.db.insert_autonomy_report(report)
        self._write_state("last_weekly_report", week_key)
        self.db.insert_learning_history(
            self.AGENT_NAME,
            "weekly_drift_report",
            f"Generated weekly drift report for {week_key}",
            impact_score=metrics.get("win_rate", 0.0),
        )
        return {"status": "completed", "period_key": week_key, "metrics": metrics}

    def _propose_changes(self, metrics: dict[str, Any], context_key: str) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        win_rate = metrics.get("win_rate", 0.0)
        pnl = metrics.get("net_pnl", 0.0)
        current_conf, current_interval = self._context_params(context_key)
        autonomy = self.config.autonomy
        policy_map = self._get_context_policy_map()
        policy = policy_map.get(context_key, {})
        current_agree = int(policy.get(
            "min_strategies_agree", self.config.signals.min_strategies_agree
        ))

        current_cooldown = int(policy.get(
            "signal_cooldown_sec", self.config.signals.signal_cooldown_sec
        ))

        if win_rate < 0.45 or pnl < 0:
            new_conf = min(autonomy.min_confidence_ceiling, current_conf + autonomy.confidence_step_up)
            new_interval = min(autonomy.max_eval_interval_ms, current_interval + autonomy.eval_interval_step_ms)
            new_conf, new_interval = self._apply_regime_guardrails(
                context_key,
                new_conf,
                new_interval,
            )
            if new_conf != current_conf:
                changes.append(self._change_dict(
                    "signal_threshold",
                    "signals.min_confidence",
                    current_conf,
                    new_conf,
                    "Recent closed-trade performance degraded; tighten signal threshold.",
                ))
            if new_interval != current_interval:
                changes.append(self._change_dict(
                    "polling",
                    "signals.eval_interval_ms",
                    current_interval,
                    new_interval,
                    "Recent closed-trade performance degraded; slow evaluation cadence to reduce noise and CPU.",
                ))
            # Require more strategies to agree when performance is poor.
            new_agree = min(autonomy.min_strategies_agree_ceiling, current_agree + 1)
            if new_agree != current_agree:
                changes.append(self._change_dict(
                    "signal_threshold",
                    "signals.min_strategies_agree",
                    current_agree,
                    new_agree,
                    "Recent closed-trade performance degraded; require more strategy agreement.",
                ))
            # Increase cooldown when performance is poor — space out signals more.
            new_cooldown = min(autonomy.cooldown_ceiling_sec, current_cooldown + autonomy.cooldown_step_sec)
            if new_cooldown != current_cooldown:
                changes.append(self._change_dict(
                    "signal_threshold",
                    "signals.signal_cooldown_sec",
                    current_cooldown,
                    new_cooldown,
                    "Recent closed-trade performance degraded; increase signal cooldown to filter noise.",
                ))
        elif win_rate > 0.58 and pnl > 0:
            new_conf = max(autonomy.min_confidence_floor, current_conf - autonomy.confidence_step_down)
            new_conf, _ = self._apply_regime_guardrails(
                context_key,
                new_conf,
                current_interval,
            )
            if new_conf != current_conf:
                changes.append(self._change_dict(
                    "signal_threshold",
                    "signals.min_confidence",
                    current_conf,
                    new_conf,
                    "Recent closed-trade performance improved; cautiously loosen threshold.",
                ))
            # Loosen agree threshold when things are going well.
            new_agree = max(autonomy.min_strategies_agree_floor, current_agree - 1)
            if new_agree != current_agree:
                changes.append(self._change_dict(
                    "signal_threshold",
                    "signals.min_strategies_agree",
                    current_agree,
                    new_agree,
                    "Recent closed-trade performance improved; loosen strategy agreement requirement.",
                ))
            # Decrease cooldown when performance is good — allow more opportunities.
            new_cooldown = max(autonomy.cooldown_floor_sec, current_cooldown - autonomy.cooldown_step_sec)
            if new_cooldown != current_cooldown:
                changes.append(self._change_dict(
                    "signal_threshold",
                    "signals.signal_cooldown_sec",
                    current_cooldown,
                    new_cooldown,
                    "Recent closed-trade performance improved; decrease signal cooldown to capture more opportunities.",
                ))

        return changes

    def _apply_change(self, change: dict[str, Any], metrics: dict[str, Any],
                      context_key: str) -> None:
        target = change["target"]
        policy_map = self._get_context_policy_map()
        now_ts = time.time()
        context_policy = policy_map.get(
            context_key,
            {
                "min_confidence": self.config.signals.min_confidence,
                "eval_interval_ms": self.config.signals.eval_interval_ms,
                "min_strategies_agree": self.config.signals.min_strategies_agree,
                "updated_at": now_ts,
                "last_active_timestamp": now_ts,
            },
        )

        if target == "signals.min_confidence":
            context_policy["min_confidence"] = float(change["new_value"])
        elif target == "signals.eval_interval_ms":
            context_policy["eval_interval_ms"] = int(change["new_value"])
        elif target == "signals.min_strategies_agree":
            context_policy["min_strategies_agree"] = max(
                self.config.autonomy.min_strategies_agree_floor,
                min(self.config.autonomy.min_strategies_agree_ceiling, int(change["new_value"])),
            )
        elif target == "signals.signal_cooldown_sec":
            context_policy["signal_cooldown_sec"] = max(
                self.config.autonomy.cooldown_floor_sec,
                min(self.config.autonomy.cooldown_ceiling_sec, int(change["new_value"])),
            )

        guarded_conf, guarded_interval = self._apply_regime_guardrails(
            context_key,
            float(context_policy.get("min_confidence", self.config.signals.min_confidence)),
            int(context_policy.get("eval_interval_ms", self.config.signals.eval_interval_ms)),
        )
        context_policy["min_confidence"] = guarded_conf
        context_policy["eval_interval_ms"] = guarded_interval

        context_policy["updated_at"] = now_ts
        context_policy["last_active_timestamp"] = now_ts
        policy_map[context_key] = context_policy
        self._set_context_policy_map(policy_map)

        # Apply immediately if we are currently in this context.
        if self._runtime_context_key() == context_key:
            self.config.signals.min_confidence = float(context_policy["min_confidence"])
            self.config.signals.eval_interval_ms = int(context_policy["eval_interval_ms"])
            if "min_strategies_agree" in context_policy:
                self.config.signals.min_strategies_agree = int(context_policy["min_strategies_agree"])
            if "signal_cooldown_sec" in context_policy:
                self.config.signals.signal_cooldown_sec = int(context_policy["signal_cooldown_sec"])

        change_record = {
            "timestamp": time.time(),
            "change_type": change["change_type"],
            "target": target,
            "old_value": str(change["old_value"]),
            "new_value": str(change["new_value"]),
            "rationale": change["rationale"],
            "metric_snapshot_json": json.dumps(metrics),
            "applied": 1,
            "rollout_mode": "paper" if self._is_paper_mode() else "live",
            "context_key": context_key,
            "validation_status": "pending",
            "validation_checked_at": None,
            "validation_notes": "Awaiting shadow validation window.",
            "reverted": 0,
            "reverted_at": None,
            "revert_notes": "",
        }
        self.db.insert_autonomous_change(change_record)

    def _validate_change(self, change: dict[str, Any]) -> dict[str, Any]:
        context_key = change.get("context_key", "global")
        recent_trades = self.db.get_closed_trades_since(
            change["timestamp"],
            limit=200,
        )
        contextual_trades = [
            trade for trade in recent_trades
            if self._trade_context_key(trade) == context_key
        ]
        scoped_trades = contextual_trades if context_key != "global" else recent_trades

        # Fall back to global validation if contextual sample is too small.
        if context_key != "global" and len(scoped_trades) < self.config.autonomy.validation_min_closed_trades:
            if len(recent_trades) >= self.config.autonomy.validation_min_closed_trades:
                scoped_trades = recent_trades

        trade_count = len(scoped_trades)
        if trade_count < self.config.autonomy.validation_min_closed_trades:
            notes = (
                "Monitoring: "
                f"{trade_count}/{self.config.autonomy.validation_min_closed_trades} "
                "closed trades observed since change."
            )
            self.db.update_autonomous_change(
                change["id"],
                {
                    "validation_status": "monitoring",
                    "validation_checked_at": time.time(),
                    "validation_notes": notes,
                },
            )
            return {"status": "monitoring", "change_id": change["id"], "notes": notes}

        baseline = self._safe_load_metrics(change.get("metric_snapshot_json", "{}"))
        post_metrics = self._trade_metrics(scoped_trades)
        should_rollback, reason = self._should_rollback(baseline, post_metrics)
        if should_rollback:
            self._rollback_change(change, reason, post_metrics)
            self.bus.publish(Event(
                type=EventType.AUTONOMY_POLICY_CHANGED,
                data={
                    "action": "rolled_back",
                    "change_id": change["id"],
                    "target": change.get("target"),
                    "reason": reason,
                    "post_metrics": {"win_rate": post_metrics.get("win_rate"), "avg_pnl": post_metrics.get("avg_pnl")},
                },
                source=self.AGENT_NAME,
            ))
            return {"status": "rolled_back", "change_id": change["id"], "reason": reason}

        notes = (
            f"Validated after {trade_count} closed trades. "
            f"Post-change win_rate={post_metrics['win_rate']:.2f}, avg_pnl={post_metrics['avg_pnl']:.2f}."
        )
        self.db.update_autonomous_change(
            change["id"],
            {
                "validation_status": "validated",
                "validation_checked_at": time.time(),
                "validation_notes": notes,
            },
        )
        self.db.insert_learning_history(
            self.AGENT_NAME,
            "shadow_validation",
            f"Validated autonomous change #{change['id']} for {change['target']}",
            impact_score=post_metrics.get("win_rate", 0.0),
        )
        return {"status": "validated", "change_id": change["id"], "notes": notes}

    def _should_rollback(self, baseline: dict[str, Any], post_metrics: dict[str, Any]) -> tuple[bool, str]:
        baseline_win_rate = float(baseline.get("win_rate", 0.0) or 0.0)
        baseline_avg_pnl = float(baseline.get("avg_pnl", 0.0) or 0.0)
        post_win_rate = float(post_metrics.get("win_rate", 0.0) or 0.0)
        post_avg_pnl = float(post_metrics.get("avg_pnl", 0.0) or 0.0)

        win_rate_drop = baseline_win_rate - post_win_rate
        avg_pnl_drop = baseline_avg_pnl - post_avg_pnl

        if win_rate_drop >= self.config.autonomy.rollback_win_rate_drop:
            return True, (
                "Rollback triggered: win rate fell from "
                f"{baseline_win_rate:.2f} to {post_win_rate:.2f}."
            )
        if avg_pnl_drop >= self.config.autonomy.rollback_avg_pnl_drop:
            return True, (
                "Rollback triggered: average PnL fell from "
                f"{baseline_avg_pnl:.2f} to {post_avg_pnl:.2f}."
            )
        return False, ""

    def _rollback_change(self, change: dict[str, Any], reason: str,
                         post_metrics: dict[str, Any]) -> None:
        target = change["target"]
        old_value = change["old_value"]
        if target == "signals.min_confidence":
            self.config.signals.min_confidence = float(old_value)
        elif target == "signals.eval_interval_ms":
            self.config.signals.eval_interval_ms = int(float(old_value))
        elif target == "signals.min_strategies_agree":
            self.config.signals.min_strategies_agree = int(float(old_value))
        elif target == "signals.signal_cooldown_sec":
            self.config.signals.signal_cooldown_sec = int(float(old_value))
        self.config.save()

        self.db.update_autonomous_change(
            change["id"],
            {
                "validation_status": "rolled_back",
                "validation_checked_at": time.time(),
                "validation_notes": reason,
                "reverted": 1,
                "reverted_at": time.time(),
                "revert_notes": json.dumps(post_metrics),
            },
        )
        self.db.insert_learning_history(
            self.AGENT_NAME,
            "autonomy_rollback",
            f"Rolled back autonomous change #{change['id']} for {target}: {reason}",
            impact_score=post_metrics.get("win_rate", 0.0),
        )

    def _safe_load_metrics(self, payload: str) -> dict[str, Any]:
        try:
            return json.loads(payload) if payload else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _trade_metrics(self, trades: list[dict[str, Any]]) -> dict[str, Any]:
        closed_count = len(trades)
        wins = sum(1 for trade in trades if (trade.get("pnl") or 0) > 0)
        losses = sum(1 for trade in trades if (trade.get("pnl") or 0) < 0)
        net_pnl = sum((trade.get("pnl") or 0.0) for trade in trades)
        avg_pnl = net_pnl / closed_count if closed_count else 0.0
        context_metrics = self._context_metrics(trades)
        return {
            "closed_trades": closed_count,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / closed_count) if closed_count else 0.0,
            "net_pnl": round(net_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "current_min_confidence": self.config.signals.min_confidence,
            "current_eval_interval_ms": self.config.signals.eval_interval_ms,
            "context_metrics": context_metrics,
        }

    def _context_metrics(self, trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for trade in trades:
            key = self._trade_context_key(trade)
            buckets.setdefault(key, []).append(trade)

        summary: dict[str, dict[str, Any]] = {}
        for key, grouped in buckets.items():
            count = len(grouped)
            if count == 0:
                continue
            pnls = [(trade.get("pnl") or 0.0) for trade in grouped]
            wins = sum(1 for pnl in pnls if pnl > 0)
            summary[key] = {
                "closed_trades": count,
                "win_rate": wins / count,
                "avg_pnl": round(sum(pnls) / count, 2),
                "net_pnl": round(sum(pnls), 2),
            }
        return summary

    def _context_params(self, context_key: str) -> tuple[float, int]:
        policy = self._get_context_policy_map().get(context_key)
        if not policy:
            return self.config.signals.min_confidence, self.config.signals.eval_interval_ms
        conf, interval = (
            float(policy.get("min_confidence", self.config.signals.min_confidence)),
            int(policy.get("eval_interval_ms", self.config.signals.eval_interval_ms)),
        )
        return self._apply_regime_guardrails(context_key, conf, interval)

    def _runtime_context_key(self) -> str:
        regime = "unknown"
        try:
            current_regime = self.db.get_current_regime()
            if current_regime:
                regime = str(current_regime.get("regime") or "unknown").strip().lower() or "unknown"
        except (TypeError, ValueError, AttributeError):
            regime = "unknown"

        now = self._now_phoenix()
        tod = self._time_bucket_from_minutes(now.hour * 60 + now.minute)
        return f"{regime}|{tod}"

    def _apply_regime_guardrails(self, context_key: str, confidence: float,
                                 eval_interval_ms: int) -> tuple[float, int]:
        regime, _ = self._split_context_key(context_key)

        conf_bounds = self.config.autonomy.regime_confidence_bounds.get(
            regime,
            self.config.autonomy.regime_confidence_bounds.get("unknown", [0.65, 0.85]),
        )
        interval_bounds = self.config.autonomy.regime_eval_interval_bounds_ms.get(
            regime,
            self.config.autonomy.regime_eval_interval_bounds_ms.get("unknown", [5000, 60000]),
        )

        try:
            conf_min = float(conf_bounds[0])
            conf_max = float(conf_bounds[1])
        except (TypeError, ValueError, IndexError):
            conf_min, conf_max = 0.65, 0.85

        try:
            int_min = int(interval_bounds[0])
            int_max = int(interval_bounds[1])
        except (TypeError, ValueError, IndexError):
            int_min, int_max = 5000, 60000

        guarded_conf = min(max(confidence, conf_min), conf_max)
        guarded_interval = min(max(eval_interval_ms, int_min), int_max)
        return guarded_conf, guarded_interval

    def _split_context_key(self, context_key: str) -> tuple[str, str]:
        if "|" not in context_key:
            return "unknown", "unknown"
        regime, tod = context_key.split("|", 1)
        return regime.strip().lower() or "unknown", tod.strip().lower() or "unknown"

    def _trade_context_key(self, trade: dict[str, Any]) -> str:
        regime = str(trade.get("market_regime") or "unknown").strip().lower()
        if not regime:
            regime = "unknown"

        tod = str(trade.get("time_of_day") or "").strip().lower()
        if not tod:
            tod = self._time_bucket_from_entry(trade.get("entry_time"))

        return f"{regime}|{tod}"

    def _time_bucket_from_entry(self, entry_time: Any) -> str:
        if not entry_time:
            return "unknown"
        try:
            dt = datetime.fromisoformat(str(entry_time))
            return self._time_bucket_from_minutes(dt.hour * 60 + dt.minute)
        except ValueError:
            return "unknown"

    def _time_bucket_from_minutes(self, total: int) -> str:
        if total < 9 * 60 + 30:
            return "pre_market"
        if total < 10 * 60 + 30:
            return "opening_hour"
        if total < 12 * 60:
            return "mid_morning"
        if total < 14 * 60:
            return "midday"
        if total < 15 * 60 + 30:
            return "power_hour"
        if total < 16 * 60:
            return "closing_30"
        return "after_hours"

    def _select_focus_context(self, metrics: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        context_metrics = metrics.get("context_metrics", {})
        min_samples = self.config.autonomy.min_context_closed_trades
        eligible = [
            (key, data)
            for key, data in context_metrics.items()
            if data.get("closed_trades", 0) >= min_samples
        ]
        if not eligible:
            return "global", metrics

        # Use the context with the largest sample size; if tied, pick lower avg pnl first.
        eligible.sort(
            key=lambda item: (
                item[1].get("closed_trades", 0),
                -item[1].get("avg_pnl", 0.0),
            ),
            reverse=True,
        )
        context_key, context_data = eligible[0]
        focused = {
            **metrics,
            "closed_trades": context_data.get("closed_trades", 0),
            "win_rate": context_data.get("win_rate", metrics.get("win_rate", 0.0)),
            "avg_pnl": context_data.get("avg_pnl", metrics.get("avg_pnl", 0.0)),
            "net_pnl": context_data.get("net_pnl", metrics.get("net_pnl", 0.0)),
            "focus_context": context_key,
        }
        return context_key, focused

    def _get_context_policy_map(self) -> dict[str, dict[str, Any]]:
        rows = self.db.get_agent_knowledge(self.AGENT_NAME, "optimizer_state")
        for row in rows:
            if row.get("key") == "context_policy_map":
                value = row.get("value", {})
                if isinstance(value, dict):
                    return value
        return {}

    def _set_context_policy_map(self, policy_map: dict[str, dict[str, Any]]) -> None:
        self.db.upsert_agent_knowledge(
            self.AGENT_NAME,
            "optimizer_state",
            "context_policy_map",
            policy_map,
            confidence=0.85,
        )

    def _change_dict(self, change_type: str, target: str, old_value: Any,
                     new_value: Any, rationale: str) -> dict[str, Any]:
        return {
            "change_type": change_type,
            "target": target,
            "old_value": old_value,
            "new_value": new_value,
            "rationale": rationale,
        }

    def _read_state(self, key: str) -> str:
        rows = self.db.get_agent_knowledge(self.AGENT_NAME, "optimizer_state")
        for row in rows:
            if row.get("key") == key:
                value = row.get("value", {})
                if isinstance(value, dict):
                    return str(value.get("value", ""))
        return ""

    def _write_state(self, key: str, value: str) -> None:
        self.db.upsert_agent_knowledge(
            self.AGENT_NAME,
            "optimizer_state",
            key,
            {"value": value},
            confidence=1.0,
        )

    def _is_paper_mode(self) -> bool:
        system_name = (self.config.rithmic.system or "").lower()
        return (not self.config.rithmic.user) or ("paper" in system_name) or ("test" in system_name)

    def _decay_stale_policies(self) -> dict[str, Any]:
        """Gradually fade context policies toward startup baseline, then remove.

        In the final ``policy_fade_start_fraction`` of the decay window, each tunable
        value is linearly interpolated back toward the startup-default value captured
        at __init__ time. Once the full decay period elapses the policy is removed.
        """
        policy_map = self._get_context_policy_map()
        if not policy_map:
            return {"removed": 0, "contexts": []}

        now_ts = time.time()
        autonomy = self.config.autonomy
        decay_seconds = autonomy.policy_decay_days * 86400
        fade_start = autonomy.policy_fade_start_fraction  # e.g. 0.7
        removed_contexts: list[str] = []
        faded_contexts: list[str] = []

        for context_key in list(policy_map.keys()):
            policy = policy_map[context_key]
            last_active = float(policy.get("last_active_timestamp", 0.0))
            if last_active == 0.0:
                last_active = float(policy.get("updated_at", now_ts))

            age_seconds = now_ts - last_active
            age_ratio = age_seconds / decay_seconds

            if age_ratio >= 1.0:
                # Full expiry — remove.
                removed_contexts.append(context_key)
                del policy_map[context_key]
                log.info(
                    "Decayed stale policy for context=%s (inactive for %.1f days)",
                    context_key, age_seconds / 86400,
                )
            elif age_ratio >= fade_start:
                # Gradual fade: blend values toward startup baseline.
                fade = (age_ratio - fade_start) / (1.0 - fade_start)  # 0.0 → 1.0
                for param, baseline_val in self._initial_params.items():
                    if param not in policy:
                        continue
                    learned = policy[param]
                    if isinstance(baseline_val, float):
                        policy[param] = round(learned + (baseline_val - learned) * fade, 4)
                    else:
                        policy[param] = int(round(learned + (baseline_val - learned) * fade))
                policy["updated_at"] = now_ts
                policy_map[context_key] = policy
                faded_contexts.append(context_key)
                log.debug(
                    "Fading policy for context=%s (age_ratio=%.2f, fade=%.2f)",
                    context_key, age_ratio, fade,
                )

        if removed_contexts or faded_contexts:
            self._set_context_policy_map(policy_map)

        if removed_contexts:
            self.bus.publish(Event(
                type=EventType.AUTONOMY_POLICY_CHANGED,
                data={
                    "action": "decayed",
                    "removed_count": len(removed_contexts),
                    "contexts": removed_contexts,
                },
                source=self.AGENT_NAME,
            ))

        return {"removed": len(removed_contexts), "contexts": removed_contexts, "faded": len(faded_contexts)}

    def _last_trade_in_context(self, context_key: str) -> float:
        """Get the timestamp of the most recent trade in a given context.

        Returns the timestamp (seconds since epoch) of the last trade that matches
        the context_key (regime|time_of_day), or 0.0 if no trades found.
        """
        trades = [trade for trade in self.db.get_trades(limit=500) if trade.get("status") == "closed"]
        matching = [
            float(trade.get("closed_at", 0.0) or 0.0)
            for trade in trades
            if self._trade_context_key(trade) == context_key
        ]
        return max(matching) if matching else 0.0

    def _now_phoenix(self) -> datetime:
        return datetime.now(tz=PHOENIX_TZ)
