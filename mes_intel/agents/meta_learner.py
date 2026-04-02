"""Meta-Learner Agent — Phase 2 Enhanced.

Learns from every trade, teaches all other agents, and self-improves.
Phase 2 adds:
  - Full personalized post-mortem with per-agent feedback narratives
  - RL reward tracking per agent with explicit reward signals
  - Self-accuracy tracking: monitors own recommendation accuracy
  - Adaptive learning rate with momentum
  - Team performance trending with momentum detection
  - Agent scorecard: which agent is the MVP vs the laggard
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType

log = logging.getLogger(__name__)

LEARNING_DIR = Path(__file__).parent.parent.parent / "var" / "mes_intel" / "learning"


@dataclass
class LearningRecord:
    """A record of what the meta-learner taught and whether it helped."""
    timestamp: float = 0.0
    trade_id: int = 0
    action: str = ""
    target_agent: str = ""
    pre_metric: float = 0.0
    post_metric: float = 0.0
    was_helpful: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "trade_id": self.trade_id,
            "action": self.action,
            "target_agent": self.target_agent,
            "pre_metric": self.pre_metric,
            "post_metric": self.post_metric,
            "was_helpful": self.was_helpful,
        }


@dataclass
class AgentRewardRecord:
    """RL reward signal for a single agent on a single trade."""
    agent: str
    trade_id: int
    reward: float          # -1.0 to +1.0
    rationale: str         # human-readable explanation
    confidence_was: float  # agent's confidence at signal time
    was_correct: bool
    timestamp: float = field(default_factory=time.time)


@dataclass
class StrategyTracker:
    """Tracks a strategy's performance over time for weight adjustment."""
    name: str = ""
    recent_scores: list[float] = field(default_factory=list)
    recent_correct: list[bool] = field(default_factory=list)
    recent_rewards: deque = field(default_factory=lambda: deque(maxlen=100))
    cumulative_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    regime_performance: dict[str, dict] = field(default_factory=lambda: defaultdict(
        lambda: {"wins": 0, "losses": 0, "pnl": 0.0}
    ))
    # RL state
    rl_value: float = 0.0          # estimated value function
    momentum: float = 0.0          # gradient momentum for weight update
    own_recommendation_hits: int = 0
    own_recommendation_total: int = 0
    # Knowledge tracking (Phase 3)
    knowledge_score: float = 1.0   # grows with each lesson learned
    lessons_learned: int = 0       # total lessons absorbed

    @property
    def accuracy(self) -> float:
        if not self.recent_correct:
            return 0.5
        return sum(self.recent_correct[-50:]) / len(self.recent_correct[-50:])

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.5

    @property
    def avg_reward(self) -> float:
        if not self.recent_rewards:
            return 0.0
        return float(np.mean(list(self.recent_rewards)))

    @property
    def own_accuracy(self) -> float:
        if self.own_recommendation_total == 0:
            return 0.5
        return self.own_recommendation_hits / self.own_recommendation_total


@dataclass
class PostMortemReport:
    """Detailed post-trade analysis report."""
    trade_id: int
    pnl: float
    outcome: str
    regime: str
    timestamp: float

    # Per-agent verdicts
    signal_engine_verdict: str = ""
    chart_monitor_verdict: str = ""
    news_scanner_verdict: str = ""
    dark_pool_verdict: str = ""

    # What went right / wrong
    what_worked: list[str] = field(default_factory=list)
    what_didnt_work: list[str] = field(default_factory=list)
    key_lesson: str = ""

    # Weight changes made
    weight_changes: dict[str, tuple[float, float]] = field(default_factory=dict)  # name → (before, after)

    # Grade-pnl correlation at time of trade
    grade_pnl_correlation: float = 0.0

    def to_narrative(self) -> str:
        """Generate a readable narrative for UI display."""
        lines = [
            f"═══ POST-MORTEM: Trade #{self.trade_id} ═══",
            f"Outcome: {self.outcome.upper()} | PnL: ${self.pnl:+.2f} | Regime: {self.regime}",
            "",
        ]
        if self.signal_engine_verdict:
            lines.append(f"Signal Engine: {self.signal_engine_verdict}")
        if self.chart_monitor_verdict:
            lines.append(f"Chart Monitor: {self.chart_monitor_verdict}")
        if self.news_scanner_verdict:
            lines.append(f"News Scanner: {self.news_scanner_verdict}")
        if self.dark_pool_verdict:
            lines.append(f"Dark Pool: {self.dark_pool_verdict}")
        if self.what_worked:
            lines.append("\n✓ What worked:")
            lines.extend(f"  • {w}" for w in self.what_worked)
        if self.what_didnt_work:
            lines.append("\n✗ What didn't:")
            lines.extend(f"  • {w}" for w in self.what_didnt_work)
        if self.key_lesson:
            lines.append(f"\n→ Key lesson: {self.key_lesson}")
        if self.weight_changes:
            lines.append("\nWeight adjustments:")
            for name, (before, after) in self.weight_changes.items():
                arrow = "↑" if after > before else "↓"
                lines.append(f"  {name}: {before:.3f} {arrow} {after:.3f}")
        return "\n".join(lines)


class MetaLearner:
    """Meta-learner agent — Phase 2 enhanced orchestrator.

    Core responsibilities:
    1. After each trade: full post-mortem, personalized feedback to each agent
    2. Strategy weight optimization via RL with momentum
    3. Self-evaluation: tracks own recommendation accuracy
    4. Regime-aware adaptation
    5. Agent scorecards: MVP and laggard detection
    """

    MIN_LR = 0.005
    MAX_LR = 0.10
    INITIAL_LR = 0.03
    LR_MOMENTUM = 0.9       # momentum coefficient for gradient updates

    MIN_WEIGHT = 0.1
    MAX_WEIGHT = 3.0

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus

        self.learning_rate = self.INITIAL_LR
        self.lr_gradient = 0.0   # momentum accumulator
        self.confidence_adjustment = 0.0

        # Strategy trackers
        self.trackers: dict[str, StrategyTracker] = {}
        for name in config.signals.weights:
            self.trackers[name] = StrategyTracker(name=name)

        # Teaching history
        self.teaching_log: list[LearningRecord] = []
        self.teaching_accuracy: float = 0.5
        self.team_performance_scores: list[float] = []
        self.team_score_momentum: float = 0.0

        # RL reward history per agent
        self.reward_history: dict[str, list[AgentRewardRecord]] = defaultdict(list)

        # Post-mortem history (last 50)
        self.post_mortems: deque[PostMortemReport] = deque(maxlen=50)

        # Self-accuracy tracking (meta-learner's own predictions)
        self.own_predictions: list[dict] = []   # {trade_id, predicted_outcome, actual}
        self.own_accuracy_history: deque[float] = deque(maxlen=50)

        self.meta_metrics = {
            "weight_adjustments": 0,
            "helpful_adjustments": 0,
            "harmful_adjustments": 0,
            "lr_adjustments": 0,
            "total_post_mortems": 0,
            "regime_transitions": 0,
            "rl_reward_signals": 0,
        }

        # Phase 3: Knowledge scores for non-strategy agents
        self.agent_knowledge_scores: dict[str, float] = {
            "signal_engine": 1.0,
            "chart_monitor": 1.0,
            "news_scanner": 1.0,
            "dark_pool": 1.0,
            "trade_journal": 1.0,
            "meta_learner": 1.0,
        }
        # Phase 3: Lessons learned per agent (persistent counter)
        self.agent_lessons_learned: dict[str, int] = {k: 0 for k in self.agent_knowledge_scores}
        # Phase 3: Agent combination performance tracking
        self.agent_combo_stats: dict[str, dict] = {}  # "a+b" → {wins, losses, pnl}
        # Phase 3: Recent agent-to-agent communication log (for UI)
        self.communication_log: deque[dict] = deque(maxlen=200)

        self._load_state()
        self._load_knowledge_from_db()

        self.bus.subscribe(EventType.TRADE_CLOSED, self._on_trade_closed, priority=10)
        self.bus.subscribe(EventType.TRADE_GRADED, self._on_trade_graded)
        self.bus.subscribe(EventType.REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.ENSEMBLE_UPDATE, self._on_ensemble_update)
        self.bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal_generated)
        self.bus.subscribe(EventType.DARK_POOL_ALERT, self._on_dark_pool_alert)
        self.bus.subscribe(EventType.NEWS_ALERT, self._on_news_event)
        self.bus.subscribe(EventType.BREAKING_NEWS, self._on_news_event)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_market_regime_change)
        self.bus.subscribe(EventType.QUANT_SIGNAL, self._on_quant_signal)

        # Phase 4: Bayesian agent weight priors and per-regime accuracy
        self.bayesian_weights: dict[str, float] = {
            name: config.signals.weights.get(name, 1.0)
            for name in config.signals.weights
        }
        # Track accuracy per agent per regime
        self.regime_agent_accuracy: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"wins": 0, "losses": 0})
        )
        self._current_regime: str = "unknown"
        self._current_quant: dict = {}

        # Team meeting counter (every 50 trades)
        self._trade_count_since_meeting: int = 0
        self.TEAM_MEETING_INTERVAL = 50

        log.info("Meta-Learner Phase 4 initialized (lr=%.4f, total_lessons=%d)",
                 self.learning_rate,
                 sum(self.agent_lessons_learned.values()))

    # ══════════════════════════════════════════════
    # CORE: Signal tracking (record before trade)
    # ══════════════════════════════════════════════

    def _on_signal_generated(self, event: Event):
        """Record the meta-learner's prediction at signal time."""
        signal_data = event.data
        # Record what we predicted this trade would do
        prediction = {
            "signal_id": signal_data.get("signal_id"),
            "predicted_direction": signal_data.get("direction", "FLAT"),
            "predicted_confidence": signal_data.get("confidence", 0.5),
            "timestamp": time.time(),
            "actual_outcome": None,  # filled in post-mortem
        }
        self.own_predictions.append(prediction)
        if len(self.own_predictions) > 200:
            self.own_predictions = self.own_predictions[-200:]

    # ══════════════════════════════════════════════
    # CORE: Full post-mortem after each trade
    # ══════════════════════════════════════════════

    def _on_trade_closed(self, event: Event):
        """Full post-mortem when a trade closes."""
        trade_id = event.data.get("trade_id")
        pnl = event.data.get("pnl", 0)
        r_multiple = event.data.get("r_multiple")

        self.meta_metrics["total_post_mortems"] += 1

        trades = self.db.get_trades(limit=20)
        trade = next((t for t in trades if t["id"] == trade_id), None)
        if not trade:
            return

        signal_id = trade.get("signal_id")
        regime = self._get_current_regime()
        outcome = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")

        log.info("Post-mortem: trade #%d | %s | PnL=$%.2f | R=%.2f | regime=%s",
                 trade_id, outcome, pnl, r_multiple or 0, regime)

        # Build detailed post-mortem report
        report = PostMortemReport(
            trade_id=trade_id,
            pnl=pnl,
            outcome=outcome,
            regime=regime,
            timestamp=time.time(),
        )

        # Collect pre-adjustment weights for report
        pre_weights = dict(self.config.signals.weights)

        # 1. Teach Signal Engine — RL weight adjustment + verdicts
        self._teach_signal_engine(report, signal_id, pnl, r_multiple, outcome, regime)

        # 2. Teach Trade Journal — grading calibration
        report.grade_pnl_correlation = self._teach_trade_journal(trade_id, pnl, outcome)

        # 3. Teach Chart Monitor — order flow pattern feedback
        chart_verdict = self._teach_chart_monitor(trade_id, pnl, outcome, regime)
        report.chart_monitor_verdict = chart_verdict

        # 4. Teach News Scanner — signal vs noise analysis
        news_verdict = self._teach_news_scanner(trade_id, pnl, outcome)
        report.news_scanner_verdict = news_verdict

        # 5. Teach Dark Pool agent — institutional flow feedback
        dp_verdict = self._teach_dark_pool(trade_id, pnl, outcome, regime)
        report.dark_pool_verdict = dp_verdict

        # 6. Teach self — meta-learning
        self._teach_self(trade_id, pnl, outcome)

        # Compute weight changes for report
        post_weights = dict(self.config.signals.weights)
        report.weight_changes = {
            k: (pre_weights[k], post_weights[k])
            for k in pre_weights
            if abs(pre_weights[k] - post_weights.get(k, pre_weights[k])) > 0.001
        }

        # Finalize narrative and synthesize lessons
        self._synthesize_lessons(report, pnl, outcome, regime)

        # Store post-mortem
        self.post_mortems.append(report)

        # Update own prediction record
        self._update_own_prediction(signal_id, outcome)

        # Update team performance score
        self._update_team_score()

        # Phase 3: Track agent combination performance
        self._track_agent_combo(signal_id, outcome, pnl, regime)

        # Phase 3: Update knowledge scores for all agents
        self._update_knowledge_scores_after_trade(outcome, pnl, report)

        # Phase 3: Broadcast TRADE_RESULT so every agent can learn
        self.bus.publish(Event(
            type=EventType.TRADE_RESULT,
            source="meta_learner",
            data={
                "trade_id": trade_id,
                "signal_id": signal_id,
                "pnl": pnl,
                "outcome": outcome,
                "regime": regime,
                "r_multiple": r_multiple,
                "weight_changes": report.weight_changes,
                "agent_verdicts": {
                    "signal_engine": report.signal_engine_verdict,
                    "chart_monitor": report.chart_monitor_verdict,
                    "news_scanner": report.news_scanner_verdict,
                    "dark_pool": report.dark_pool_verdict,
                },
                "knowledge_scores": dict(self.agent_knowledge_scores),
                "team_iq": self._compute_team_iq(),
            },
            priority=8,
        ))

        # Publish post-mortem for UI
        self.bus.publish(Event(
            type=EventType.PERFORMANCE_REPORT,
            source="meta_learner",
            data={
                "type": "post_mortem",
                "trade_id": trade_id,
                "narrative": report.to_narrative(),
                "outcome": outcome,
                "pnl": pnl,
                "weight_changes": report.weight_changes,
            },
            priority=6,
        ))

        # Phase 4: Update regime-aware agent accuracy
        self._update_regime_agent_accuracy(outcome, regime)

        # Phase 4: Team meeting every 50 trades
        self._check_team_meeting(trade_id, pnl, outcome)

        self._save_state()
        self._save_knowledge_to_db(trade_id=trade_id)

    def _synthesize_lessons(self, report: PostMortemReport, pnl: float,
                             outcome: str, regime: str):
        """Synthesize what worked / what didn't and a key lesson."""
        if outcome == "win":
            # What worked?
            best_strategies = [
                name for name, t in self.trackers.items()
                if t.accuracy > 0.55 and self.config.signals.weights.get(name, 1.0) > 1.0
            ]
            if best_strategies:
                report.what_worked.append(
                    f"High-weight strategies ({', '.join(best_strategies[:3])}) aligned correctly"
                )
            if regime in ("TRENDING_UP", "TRENDING_DOWN"):
                report.what_worked.append(f"Trend followed correctly in {regime} regime")
            if pnl > 20:
                report.what_worked.append("Held through full target — excellent exit discipline")
            report.key_lesson = (
                f"In {regime}: strategies {', '.join(best_strategies[:2]) if best_strategies else 'ensemble'} "
                f"are reliable. Weights boosted."
            )
        elif outcome == "loss":
            laggards = [
                name for name, t in self.trackers.items()
                if t.accuracy < 0.45 and self.config.signals.weights.get(name, 1.0) > 0.8
            ]
            if laggards:
                report.what_didnt_work.append(
                    f"Low-accuracy strategies ({', '.join(laggards[:3])}) had too much weight"
                )
            if regime == "RANGE" and abs(pnl) > 10:
                report.what_didnt_work.append("Momentum strategy in ranging market — regime mismatch")
            report.key_lesson = (
                f"Regime={regime}: reduce reliance on "
                f"{laggards[0] if laggards else 'overconfident signals'}. "
                "Check regime before next entry."
            )
        else:
            report.key_lesson = "Breakeven — timing issue likely. Review entry trigger sharpness."

    # ══════════════════════════════════════════════
    # TEACHING: Signal Engine (RL-enhanced)
    # ══════════════════════════════════════════════

    def _teach_signal_engine(self, report: PostMortemReport, signal_id: Optional[int],
                              pnl: float, r_multiple: Optional[float],
                              outcome: str, regime: str):
        """RL weight adjustment with momentum. Generates per-strategy reward signals."""
        if not signal_id:
            report.signal_engine_verdict = "No signal_id — cannot do per-strategy feedback."
            return

        signals = self.db.get_signals(limit=100)
        signal = next((s for s in signals if s["id"] == signal_id), None)
        if not signal:
            report.signal_engine_verdict = "Signal record not found in DB."
            return

        with self.db.conn() as c:
            strat_scores = c.execute(
                "SELECT strategy_name, score, direction FROM strategy_scores WHERE signal_id=?",
                (signal_id,)
            ).fetchall()

        signal_direction = signal["direction"]
        correct_strategies = []
        wrong_strategies = []

        for row in strat_scores:
            name = row["strategy_name"]
            score = row["score"]
            strat_direction = row["direction"]

            if name not in self.trackers:
                self.trackers[name] = StrategyTracker(name=name)

            tracker = self.trackers[name]

            # Was this strategy's direction correct?
            was_correct = (
                (outcome == "win" and strat_direction == signal_direction) or
                (outcome == "loss" and strat_direction != signal_direction)
            )

            tracker.recent_scores.append(score)
            tracker.recent_correct.append(was_correct)
            tracker.cumulative_pnl += pnl if strat_direction == signal_direction else -pnl

            if outcome == "win":
                tracker.win_count += 1
            elif outcome == "loss":
                tracker.loss_count += 1

            rp = tracker.regime_performance.setdefault(
                regime, {"wins": 0, "losses": 0, "pnl": 0.0}
            )
            if outcome == "win":
                rp["wins"] += 1
            elif outcome == "loss":
                rp["losses"] += 1
            rp["pnl"] += pnl if strat_direction == signal_direction else -pnl

            tracker.recent_scores = tracker.recent_scores[-200:]
            tracker.recent_correct = tracker.recent_correct[-200:]

            # RL reward signal
            if outcome == "win":
                reward = min(1.0, abs(pnl) / 20.0) * (1.0 if was_correct else -0.5)
            elif outcome == "loss":
                reward = -min(1.0, abs(pnl) / 20.0) * (1.0 if not was_correct else -0.5)
            else:
                reward = 0.0

            tracker.recent_rewards.append(reward)

            rl_rec = AgentRewardRecord(
                agent=name,
                trade_id=report.trade_id,
                reward=reward,
                rationale=(
                    f"{'Correct' if was_correct else 'Wrong'} direction in {regime}. "
                    f"Score was {score:+.2f}. PnL ${pnl:+.2f}."
                ),
                confidence_was=abs(score),
                was_correct=was_correct,
            )
            self.reward_history[name].append(rl_rec)
            if len(self.reward_history[name]) > 200:
                self.reward_history[name] = self.reward_history[name][-200:]

            self.meta_metrics["rl_reward_signals"] += 1

            if was_correct:
                correct_strategies.append(name)
            else:
                wrong_strategies.append(name)

        # Verdict narrative for signal engine
        if correct_strategies and not wrong_strategies:
            report.signal_engine_verdict = (
                f"All {len(correct_strategies)} strategies correct ✓. "
                f"Leaders: {', '.join(correct_strategies[:3])}"
            )
            # Cross-teach Chart Monitor: winning pattern to watch for
            self._publish_lesson_learned(
                lesson_type="winning_pattern",
                source_agent="signal_engine",
                target_agent="chart_monitor",
                description=(
                    f"All strategies aligned in {regime}: "
                    f"{', '.join(correct_strategies[:3])}. "
                    f"Watch for same setup → high-probability entry."
                ),
                impact_score=min(1.0, abs(pnl) / 20.0),
                trade_id=report.trade_id,
            )
        elif wrong_strategies and not correct_strategies:
            report.signal_engine_verdict = (
                f"All {len(wrong_strategies)} strategies wrong ✗. "
                f"Culprits: {', '.join(wrong_strategies[:3])}. Weights penalized."
            )
            self._publish_lesson_learned(
                lesson_type="false_signal_pattern",
                source_agent="signal_engine",
                target_agent="chart_monitor",
                description=(
                    f"Full strategy disagreement in {regime} — "
                    f"all wrong: {', '.join(wrong_strategies[:3])}. "
                    "Avoid similar confluence in this regime."
                ),
                impact_score=-min(1.0, abs(pnl) / 20.0),
                trade_id=report.trade_id,
            )
        else:
            report.signal_engine_verdict = (
                f"Split verdict — {len(correct_strategies)} correct, {len(wrong_strategies)} wrong. "
                f"Correct: {', '.join(correct_strategies[:2])}. "
                f"Wrong: {', '.join(wrong_strategies[:2])}."
            )

        self._adjust_weights_rl(outcome, regime)

    def _adjust_weights_rl(self, outcome: str, regime: str):
        """RL weight adjustment with momentum (Adam-lite)."""
        weights = self.config.signals.weights
        adjustments = {}

        for name, tracker in self.trackers.items():
            if name not in weights:
                continue

            current_weight = weights[name]
            accuracy = tracker.accuracy
            avg_reward = tracker.avg_reward

            # Gradient: move weight in direction of recent reward
            gradient = avg_reward * (accuracy - 0.5) * 2.0  # scale to reasonable range

            # Regime-aware gradient scaling
            rp = tracker.regime_performance.get(regime, {})
            regime_wr = rp.get("wins", 0) / max(rp.get("wins", 0) + rp.get("losses", 0), 1)
            if regime_wr > 0.6:
                gradient *= 1.3
            elif regime_wr < 0.4:
                gradient *= 0.7

            # Momentum update
            tracker.momentum = self.LR_MOMENTUM * tracker.momentum + (1 - self.LR_MOMENTUM) * gradient
            adj = self.learning_rate * tracker.momentum

            new_weight = float(np.clip(current_weight + adj, self.MIN_WEIGHT, self.MAX_WEIGHT))
            if abs(new_weight - current_weight) > 0.001:
                adjustments[name] = new_weight
                weights[name] = new_weight

                self._log_teaching(
                    trade_id=0,
                    action=f"RL weight {name}: {current_weight:.3f} → {new_weight:.3f} (reward={avg_reward:+.3f})",
                    target="signal_engine",
                    pre_metric=current_weight,
                )

        if adjustments:
            self.meta_metrics["weight_adjustments"] += 1
            self.bus.publish(Event(
                type=EventType.WEIGHT_ADJUSTMENT,
                source="meta_learner",
                data={"weights": adjustments, "reason": outcome, "regime": regime},
            ))
            log.info("RL weight adjustments: %s",
                     {k: f"{v:.3f}" for k, v in adjustments.items()})

    # ══════════════════════════════════════════════
    # TEACHING: Trade Journal
    # ══════════════════════════════════════════════

    def _teach_trade_journal(self, trade_id: int, pnl: float, outcome: str) -> float:
        """Analyze grade-PnL correlation. Returns correlation coefficient."""
        with self.db.conn() as c:
            rows = c.execute("""
                SELECT tg.overall_grade, t.pnl
                FROM trade_grades tg
                JOIN trades t ON t.id = tg.trade_id
                WHERE t.status = 'closed' AND t.pnl IS NOT NULL
                ORDER BY tg.created_at DESC LIMIT 30
            """).fetchall()

        if len(rows) < 5:
            return 0.0

        grades = [r["overall_grade"] for r in rows]
        pnls = [r["pnl"] for r in rows]

        correlation = 0.0
        if np.std(grades) > 0 and np.std(pnls) > 0:
            correlation = float(np.corrcoef(grades, pnls)[0, 1])

        if correlation < 0.1:
            self._log_teaching(
                trade_id=trade_id,
                action=f"grade recalibration needed (corr={correlation:.3f})",
                target="trade_journal",
                pre_metric=correlation,
            )
            log.info("TradeJournal: grade-PnL correlation=%.3f — recalibration suggested", correlation)
            # Publish feedback to UI
            self.bus.publish(Event(
                type=EventType.PERFORMANCE_REPORT,
                source="meta_learner",
                data={
                    "target": "trade_journal",
                    "message": f"Grading correlation={correlation:.2f}. "
                               "High-graded trades not reliably winning — review criteria.",
                    "severity": "warning" if correlation < 0 else "info",
                },
            ))

        return correlation

    # ══════════════════════════════════════════════
    # TEACHING: Chart Monitor
    # ══════════════════════════════════════════════

    def _teach_chart_monitor(self, trade_id: int, pnl: float,
                              outcome: str, regime: str) -> str:
        """Send order flow pattern feedback to chart monitor."""
        trades = self.db.get_trades(limit=10)
        trade = next((t for t in trades if t["id"] == trade_id), None)
        if not trade:
            return "Trade not found."

        # Analyze whether the trade entry had good order flow context
        entry_price = trade.get("entry_price", 0)
        exit_price = trade.get("exit_price", 0)

        verdict = ""
        feedback_msg = ""

        if outcome == "win":
            verdict = f"Order flow at entry supported direction. Price moved ${pnl:+.2f} as predicted."
            feedback_msg = "Reinforce: order flow patterns at entry were valid signals."
            lesson_desc = (
                f"Order flow confirmed direction in {regime} → +${pnl:.2f}. "
                f"Entry={entry_price}, Exit={exit_price}. "
                "Pattern: strong order flow alignment = high-confidence entry."
            )
            lesson_impact = min(1.0, abs(pnl) / 20.0)
        elif outcome == "loss":
            verdict = f"Order flow at entry was misleading or regime ({regime}) hostile to strategy."
            feedback_msg = f"In {regime} regime, be more selective about order flow triggers."
            lesson_desc = (
                f"Order flow signal misleading in {regime} → ${pnl:.2f}. "
                "Increase filter threshold for order flow in this regime."
            )
            lesson_impact = -min(1.0, abs(pnl) / 20.0)
        else:
            verdict = "Order flow context neutral — breakeven suggests timing slightly off."
            feedback_msg = "Tighten entry: wait for confirmation tick after order flow signal."
            lesson_desc = "Breakeven with neutral order flow — sharpen entry timing confirmation."
            lesson_impact = 0.0

        self._publish_lesson_learned(
            lesson_type="order_flow_feedback",
            source_agent="meta_learner",
            target_agent="chart_monitor",
            description=lesson_desc,
            impact_score=lesson_impact,
            trade_id=trade_id,
        )

        self.bus.publish(Event(
            type=EventType.PERFORMANCE_REPORT,
            source="meta_learner",
            data={
                "target": "chart_monitor",
                "trade_id": trade_id,
                "outcome": outcome,
                "pnl": pnl,
                "regime": regime,
                "feedback": feedback_msg,
                "entry_price": entry_price,
                "exit_price": exit_price,
            },
        ))
        return verdict

    # ══════════════════════════════════════════════
    # TEACHING: News Scanner
    # ══════════════════════════════════════════════

    def _teach_news_scanner(self, trade_id: int, pnl: float, outcome: str) -> str:
        """Analyze news impact around trade entry."""
        entry_ts = time.time() - 600  # look back 10 min
        with self.db.conn() as c:
            news = c.execute("""
                SELECT headline, category, sentiment_score, is_trump, market_impact
                FROM news_events
                WHERE timestamp BETWEEN ? AND ?
                ORDER BY timestamp DESC
            """, (entry_ts, time.time() + 60)).fetchall()

        verdict = ""
        if not news:
            verdict = "No news events near entry — purely technical trade."
            self._publish_lesson_learned(
                lesson_type="no_news_trade",
                source_agent="meta_learner",
                target_agent="news_scanner",
                description=f"Clean technical trade (no news) → {outcome}. PnL=${pnl:+.2f}.",
                impact_score=pnl / 20.0,
                trade_id=trade_id,
            )
        else:
            high_impact = [n for n in news if (n["market_impact"] or 0) >= 2]
            categories = list(set(n["category"] for n in news))
            verdict = (
                f"{len(news)} news items near entry ({', '.join(categories[:3])}). "
                f"{len(high_impact)} high-impact. Trade {outcome}."
            )

            # Teach all agents about news→price impact pattern
            lesson_desc = (
                f"{len(news)} news items ({', '.join(categories[:2])}) near entry → "
                f"{outcome} (PnL=${pnl:+.2f}). "
            )
            if high_impact:
                lesson_desc += (
                    f"{len(high_impact)} high-impact items. "
                    "Pattern: high-impact news during entry = heightened volatility risk."
                )

            self._publish_lesson_learned(
                lesson_type="news_price_impact",
                source_agent="news_scanner",
                target_agent="all",
                description=lesson_desc,
                impact_score=pnl / 20.0,
                trade_id=trade_id,
            )

            self.bus.publish(Event(
                type=EventType.PERFORMANCE_REPORT,
                source="meta_learner",
                data={
                    "target": "news_scanner",
                    "trade_id": trade_id,
                    "outcome": outcome,
                    "pnl": pnl,
                    "news_count": len(news),
                    "category": categories[0] if categories else "",
                    "feedback": f"{len(news)} news items near entry → {outcome}",
                },
            ))

        return verdict

    # ══════════════════════════════════════════════
    # TEACHING: Dark Pool
    # ══════════════════════════════════════════════

    def _teach_dark_pool(self, trade_id: int, pnl: float,
                          outcome: str, regime: str) -> str:
        """Analyze dark pool signal effectiveness for this trade."""
        try:
            with self.db.conn() as c:
                dp_prints = c.execute("""
                    SELECT price, notional_value, side
                    FROM dark_pool_prints
                    WHERE timestamp > ? AND notional_value >= 10000000
                    ORDER BY timestamp DESC LIMIT 5
                """, (time.time() - 900,)).fetchall()
        except Exception:
            return "Dark pool data unavailable."

        if not dp_prints:
            return "No dark pool prints detected near entry."

        sides = [p["side"] for p in dp_prints]
        dominant = max(set(sides), key=sides.count) if sides else "unknown"
        verdict = (
            f"{len(dp_prints)} block prints near entry (dominant: {dominant}). "
            f"Trade {outcome}. "
        )

        if outcome == "win":
            verdict += "Dark pool signal was directionally correct ✓"
            lesson_desc = (
                f"Dark pool: {len(dp_prints)} block prints (dominant={dominant}) → "
                f"{outcome} in {regime}. PnL=${pnl:+.2f}. "
                "Signal Engine: boost weight when dark pool aligns with signal direction."
            )
            lesson_impact = min(1.0, abs(pnl) / 20.0)
        else:
            verdict += "Dark pool signal was misleading or trade was counter to institutional flow ✗"
            lesson_desc = (
                f"Dark pool: {len(dp_prints)} block prints (dominant={dominant}) in {regime} "
                f"but trade lost ${pnl:.2f}. "
                "Institutional flow was not predictive here — reduce confidence when counter-trend."
            )
            lesson_impact = -min(1.0, abs(pnl) / 20.0)

        # Cross-teach: share institutional flow direction with Signal Engine
        self._publish_lesson_learned(
            lesson_type="institutional_flow_outcome",
            source_agent="dark_pool",
            target_agent="signal_engine",
            description=lesson_desc,
            impact_score=lesson_impact,
            trade_id=trade_id,
        )

        self.bus.publish(Event(
            type=EventType.PERFORMANCE_REPORT,
            source="meta_learner",
            data={
                "target": "dark_pool",
                "trade_id": trade_id,
                "outcome": outcome,
                "pnl": pnl,
                "dp_print_count": len(dp_prints),
                "dominant_side": dominant,
            },
        ))
        return verdict

    # ══════════════════════════════════════════════
    # SELF-TEACHING & OWN ACCURACY
    # ══════════════════════════════════════════════

    def _teach_self(self, trade_id: int, pnl: float, outcome: str):
        """Self-improvement: evaluate and adjust own parameters."""
        # 1. Evaluate recent teaching effectiveness
        recent_teachings = self.teaching_log[-50:]
        if len(recent_teachings) >= 10:
            helpful = sum(1 for t in recent_teachings if t.was_helpful is True)
            total = sum(1 for t in recent_teachings if t.was_helpful is not None)
            if total > 0:
                self.teaching_accuracy = helpful / total

                # Adaptive learning rate with momentum
                if self.teaching_accuracy > 0.6:
                    lr_gradient = 0.01   # accelerate
                elif self.teaching_accuracy < 0.4:
                    lr_gradient = -0.02  # decelerate
                else:
                    lr_gradient = 0.0

                # Momentum on LR adjustment
                self.lr_gradient = 0.9 * self.lr_gradient + 0.1 * lr_gradient
                self.learning_rate = float(np.clip(
                    self.learning_rate + self.lr_gradient * 0.001,
                    self.MIN_LR, self.MAX_LR,
                ))
                self.meta_metrics["lr_adjustments"] += 1

        # 2. Own accuracy: did meta-learner's weight adjustments help?
        self._score_past_teachings()

        # 3. Confidence threshold self-adjustment
        self._adjust_confidence_threshold()

        # 4. Log meta-evaluation
        own_acc = float(np.mean(list(self.own_accuracy_history))) if self.own_accuracy_history else 0.5
        self._log_teaching(
            trade_id=trade_id,
            action=(
                f"self-eval: teaching_acc={self.teaching_accuracy:.2f}, "
                f"lr={self.learning_rate:.4f}, own_acc={own_acc:.2f}"
            ),
            target="meta_learner",
            pre_metric=self.teaching_accuracy,
        )

        # Phase 3: Update meta-learner's own knowledge score
        delta = 0.05 if pnl > 0 else -0.02 if pnl < 0 else 0.01
        self._update_agent_knowledge_score("meta_learner", delta)

        # Phase 3: Broadcast AGENT_REPORT so UI can display status
        self.bus.publish(Event(
            type=EventType.AGENT_REPORT,
            source="meta_learner",
            data={
                "agent": "meta_learner",
                "teaching_accuracy": self.teaching_accuracy,
                "learning_rate": self.learning_rate,
                "own_accuracy": own_acc,
                "knowledge_score": self.agent_knowledge_scores.get("meta_learner", 1.0),
                "total_lessons": sum(self.agent_lessons_learned.values()),
                "team_iq": self._compute_team_iq(),
            },
        ))

        self._publish_lesson_learned(
            lesson_type="meta_self_eval",
            source_agent="meta_learner",
            target_agent="meta_learner",
            description=(
                f"Self-eval after trade #{trade_id}: "
                f"teaching_acc={self.teaching_accuracy:.2f}, own_acc={own_acc:.2f}, "
                f"lr={self.learning_rate:.4f}, outcome={outcome}"
            ),
            impact_score=delta,
            trade_id=trade_id,
        )

    def _update_own_prediction(self, signal_id: Optional[int], outcome: str):
        """Update meta-learner's own prediction accuracy."""
        if not signal_id:
            return
        for pred in reversed(self.own_predictions):
            if pred.get("signal_id") == signal_id and pred["actual_outcome"] is None:
                pred["actual_outcome"] = outcome
                was_correct = (
                    (pred["predicted_direction"] == "LONG" and outcome == "win") or
                    (pred["predicted_direction"] == "SHORT" and outcome == "win") or
                    (pred["predicted_direction"] == "FLAT" and outcome == "breakeven")
                )
                self.own_accuracy_history.append(1.0 if was_correct else 0.0)
                break

    def _adjust_confidence_threshold(self):
        """Self-adjust confidence threshold based on high-conf signal outcomes."""
        recent_signals = self.db.get_signals(limit=30)
        triggered = [s for s in recent_signals if s["status"] == "triggered"]
        if len(triggered) < 5:
            return

        high_conf = [s for s in triggered if (s.get("confidence") or 0) >= 0.7]
        if len(high_conf) < 3:
            return

        signal_outcomes = []
        for sig in high_conf:
            with self.db.conn() as c:
                trade = c.execute(
                    "SELECT pnl FROM trades WHERE signal_id=? AND status='closed'",
                    (sig["id"],)
                ).fetchone()
                if trade:
                    signal_outcomes.append(trade["pnl"])

        if not signal_outcomes:
            return

        high_conf_wr = sum(1 for p in signal_outcomes if p > 0) / len(signal_outcomes)
        if high_conf_wr < 0.5:
            self.confidence_adjustment = min(self.confidence_adjustment + 0.02, 0.2)
            log.info("Meta-learner: raising confidence threshold +0.02 (high-conf WR=%.0f%%)",
                     high_conf_wr * 100)
        elif high_conf_wr > 0.7 and self.confidence_adjustment > -0.1:
            self.confidence_adjustment = max(self.confidence_adjustment - 0.01, -0.1)

    def _score_past_teachings(self):
        """Retroactively score past weight adjustments."""
        if len(self.teaching_log) < 10:
            return

        weight_teachings = [
            t for t in self.teaching_log[-30:]
            if t.target_agent == "signal_engine" and "weight" in t.action and t.was_helpful is None
        ]

        recent_trades = self.db.get_trades(limit=20)
        recent_pnls = [t["pnl"] for t in recent_trades if t.get("pnl") is not None]
        if len(recent_pnls) < 5:
            return

        recent_wr = sum(1 for p in recent_pnls if p > 0) / len(recent_pnls)

        for teaching in weight_teachings:
            teaching.post_metric = recent_wr
            teaching.was_helpful = recent_wr > teaching.pre_metric

            if teaching.was_helpful:
                self.meta_metrics["helpful_adjustments"] += 1
            else:
                self.meta_metrics["harmful_adjustments"] += 1

    # ══════════════════════════════════════════════
    # TEAM PERFORMANCE
    # ══════════════════════════════════════════════

    def _update_team_score(self):
        """Calculate team performance score with momentum trend detection."""
        recent_trades = self.db.get_trades(limit=20)
        closed = [t for t in recent_trades
                  if t["status"] == "closed" and t.get("pnl") is not None]

        if len(closed) < 3:
            return

        pnls = [t["pnl"] for t in closed]
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        avg_pnl = float(np.mean(pnls))
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        pf = gross_profit / max(gross_loss, 1)

        # Own accuracy bonus
        own_acc = float(np.mean(list(self.own_accuracy_history))) if self.own_accuracy_history else 0.5

        # Team score components (0-100)
        score_wr = wr * 30                              # 0-30
        score_pf = min(pf, 3.0) * 10                  # 0-30
        score_pnl = float(np.clip(avg_pnl / 5.0, -10, 10))  # -10 to +10
        score_teaching = self.teaching_accuracy * 20   # 0-20
        score_own = own_acc * 10                       # 0-10

        team_score = score_wr + score_pf + score_pnl + score_teaching + score_own
        self.team_performance_scores.append(float(team_score))

        # Momentum-based trend
        scores = self.team_performance_scores
        if len(scores) >= 5:
            recent = scores[-5:]
            trend_slope = float(np.polyfit(range(len(recent)), recent, 1)[0])
            # Momentum smoothing
            self.team_score_momentum = 0.8 * self.team_score_momentum + 0.2 * trend_slope

            if self.team_score_momentum > 0.5:
                trend_str = "IMPROVING"
            elif self.team_score_momentum < -0.5:
                trend_str = "DECLINING"
            else:
                trend_str = "STABLE"

            # Agent scorecards
            mvp = max(self.trackers.items(), key=lambda x: x[1].accuracy, default=(None, None))
            laggard = min(self.trackers.items(), key=lambda x: x[1].accuracy, default=(None, None))

            team_iq = self._compute_team_iq()
            total_lessons = sum(self.agent_lessons_learned.values())

            self.bus.publish(Event(
                type=EventType.PERFORMANCE_REPORT,
                source="meta_learner",
                data={
                    "team_score": float(team_score),
                    "team_iq": team_iq,
                    "trend": trend_str,
                    "trend_momentum": float(self.team_score_momentum),
                    "teaching_accuracy": self.teaching_accuracy,
                    "learning_rate": self.learning_rate,
                    "own_accuracy": own_acc,
                    "meta_metrics": dict(self.meta_metrics),
                    "strategy_accuracies": {
                        name: tracker.accuracy for name, tracker in self.trackers.items()
                    },
                    "strategy_avg_rewards": {
                        name: tracker.avg_reward for name, tracker in self.trackers.items()
                    },
                    "agent_knowledge_scores": dict(self.agent_knowledge_scores),
                    "strategy_knowledge_scores": {
                        name: t.knowledge_score for name, t in self.trackers.items()
                    },
                    "total_lessons_learned": total_lessons,
                    "agent_lessons": dict(self.agent_lessons_learned),
                    "mvp_agent": mvp[0] if mvp[0] else "unknown",
                    "laggard_agent": laggard[0] if laggard[0] else "unknown",
                },
            ))

            log.info(
                "Team score: %.1f (%s, momentum=%.2f) | WR=%.0f%% PF=%.2f | "
                "teaching=%.0f%% | own_acc=%.0f%% | lr=%.4f | MVP=%s",
                team_score, trend_str, self.team_score_momentum,
                wr * 100, pf, self.teaching_accuracy * 100, own_acc * 100,
                self.learning_rate,
                mvp[0] if mvp[0] else "?",
            )

    def _get_current_regime(self) -> str:
        with self.db.conn() as c:
            row = c.execute(
                "SELECT regime FROM regime_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return row["regime"] if row else "unknown"

    # ══════════════════════════════════════════════
    # EVENT HANDLERS
    # ══════════════════════════════════════════════

    def _on_trade_graded(self, event: Event):
        pass  # Teaching happens in _on_trade_closed

    def _on_regime_change(self, event: Event):
        new_regime = event.data.get("regime", "unknown")
        self.meta_metrics["regime_transitions"] += 1

        adjustments = {}
        for name, tracker in self.trackers.items():
            rp = tracker.regime_performance.get(new_regime)
            if not rp:
                continue
            total = rp["wins"] + rp["losses"]
            if total < 3:
                continue
            regime_wr = rp["wins"] / total
            current = self.config.signals.weights.get(name, 1.0)

            if regime_wr > 0.6:
                new_w = float(min(current * 1.15, self.MAX_WEIGHT))
            elif regime_wr < 0.4:
                new_w = float(max(current * 0.85, self.MIN_WEIGHT))
            else:
                continue

            adjustments[name] = new_w
            self.config.signals.weights[name] = new_w

        if adjustments:
            self.bus.publish(Event(
                type=EventType.WEIGHT_ADJUSTMENT,
                source="meta_learner",
                data={"weights": adjustments, "reason": f"regime_change:{new_regime}"},
            ))
            log.info("Regime → %s | pre-adjusted weights: %s", new_regime, adjustments)

    def _on_ensemble_update(self, event: Event):
        pass

    def _on_market_regime_change(self, event: Event):
        """Handle Market Brain regime transition — update Bayesian weights."""
        new_regime = event.data.get("to_regime", "unknown")
        old_regime = self._current_regime
        self._current_regime = new_regime
        self.meta_metrics["regime_transitions"] += 1

        # Bayesian weight update: boost agents that performed well in this regime
        for agent_name, regime_data in self.regime_agent_accuracy.items():
            rdata = regime_data.get(new_regime, {})
            wins   = rdata.get("wins", 0)
            losses = rdata.get("losses", 0)
            total  = wins + losses
            if total < 3:
                continue
            regime_wr = wins / total
            current_w = self.bayesian_weights.get(agent_name, 1.0)
            if regime_wr > 0.6:
                self.bayesian_weights[agent_name] = min(current_w * 1.1, self.MAX_WEIGHT)
            elif regime_wr < 0.4:
                self.bayesian_weights[agent_name] = max(current_w * 0.9, self.MIN_WEIGHT)

        # Broadcast updated Bayesian weights
        if self.bayesian_weights:
            self.bus.publish(Event(
                type=EventType.WEIGHT_ADJUSTMENT,
                source="meta_learner",
                data={
                    "weights": dict(self.bayesian_weights),
                    "reason": f"bayesian_regime_change:{new_regime}",
                },
            ))

        log.info("Meta-Learner: Bayesian weights updated for regime %s → %s", old_regime, new_regime)

    def _on_quant_signal(self, event: Event):
        """Receive quant state from Market Brain."""
        self._current_quant = event.data
        regime = event.data.get("regime", "unknown")
        if regime and regime != "unknown":
            self._current_regime = regime

    def _check_team_meeting(self, trade_id: int, pnl: float, outcome: str):
        """Hold a team meeting every TEAM_MEETING_INTERVAL trades."""
        self._trade_count_since_meeting += 1
        if self._trade_count_since_meeting < self.TEAM_MEETING_INTERVAL:
            return

        self._trade_count_since_meeting = 0
        log.info("═══ TEAM MEETING: reviewing last %d trades ═══",
                 self.TEAM_MEETING_INTERVAL)

        # Analyze regime accuracy across all agents
        meeting_report = {
            "trigger": "50_trade_interval",
            "trade_id": trade_id,
            "regime_accuracy": {},
            "top_combos": [],
            "lessons": [],
            "team_iq": self.get_team_iq(),
        }

        # Per-regime accuracy summary
        for agent_name, regime_data in self.regime_agent_accuracy.items():
            for regime, rdata in regime_data.items():
                wins   = rdata.get("wins", 0)
                losses = rdata.get("losses", 0)
                total  = wins + losses
                if total >= 3:
                    wr = wins / total
                    key = f"{agent_name}:{regime}"
                    meeting_report["regime_accuracy"][key] = round(wr, 3)

        # Top agent combinations
        if self.agent_combo_stats:
            combos_sorted = sorted(
                self.agent_combo_stats.items(),
                key=lambda kv: kv[1].get("pnl", 0),
                reverse=True,
            )[:3]
            meeting_report["top_combos"] = [
                {"combo": k, "pnl": round(v.get("pnl", 0), 2),
                 "wins": v.get("wins", 0), "losses": v.get("losses", 0)}
                for k, v in combos_sorted
            ]

        # Generate lessons from poor regime performance
        for key, wr in meeting_report["regime_accuracy"].items():
            if wr < 0.40:
                agent, regime = key.split(":", 1)
                lesson = (f"{agent} underperforms in {regime} regime "
                          f"(win rate: {wr*100:.0f}%). "
                          f"Consider reducing its weight in {regime} conditions.")
                meeting_report["lessons"].append(lesson)
                # Broadcast lesson to all agents
                self.bus.publish(Event(
                    type=EventType.LESSON_LEARNED,
                    source="meta_learner",
                    data={
                        "target_agent": "all",
                        "lesson_type": "regime_performance",
                        "description": lesson,
                        "impact_score": 0.5,
                        "regime": regime,
                        "agent": agent,
                    },
                ))

        # Broadcast team meeting performance report
        self.bus.publish(Event(
            type=EventType.PERFORMANCE_REPORT,
            source="meta_learner",
            data={
                "report_type": "team_meeting",
                "meeting_report": meeting_report,
                "team_iq": meeting_report["team_iq"],
            },
        ))

        log.info("Team meeting complete. Team IQ: %.1f | Regime accuracy keys: %d",
                 meeting_report["team_iq"],
                 len(meeting_report["regime_accuracy"]))

    # ══════════════════════════════════════════════
    # LOGGING & PERSISTENCE
    # ══════════════════════════════════════════════

    def _log_teaching(self, trade_id: int, action: str, target: str, pre_metric: float = 0.0):
        record = LearningRecord(
            timestamp=time.time(),
            trade_id=trade_id,
            action=action,
            target_agent=target,
            pre_metric=pre_metric,
        )
        self.teaching_log.append(record)
        if len(self.teaching_log) > 500:
            self.teaching_log = self.teaching_log[-500:]

    def _save_state(self):
        LEARNING_DIR.mkdir(parents=True, exist_ok=True)

        state = {
            "learning_rate": self.learning_rate,
            "lr_gradient": self.lr_gradient,
            "confidence_adjustment": self.confidence_adjustment,
            "teaching_accuracy": self.teaching_accuracy,
            "team_performance_scores": self.team_performance_scores[-100:],
            "team_score_momentum": self.team_score_momentum,
            "meta_metrics": self.meta_metrics,
            "own_accuracy_history": list(self.own_accuracy_history),
            "trackers": {
                name: {
                    "name": t.name,
                    "recent_correct": t.recent_correct[-100:],
                    "cumulative_pnl": t.cumulative_pnl,
                    "win_count": t.win_count,
                    "loss_count": t.loss_count,
                    "momentum": t.momentum,
                    "rl_value": t.rl_value,
                    "regime_performance": dict(t.regime_performance),
                    "own_recommendation_hits": t.own_recommendation_hits,
                    "own_recommendation_total": t.own_recommendation_total,
                    "knowledge_score": t.knowledge_score,
                    "lessons_learned": t.lessons_learned,
                }
                for name, t in self.trackers.items()
            },
            "teaching_log": [t.to_dict() for t in self.teaching_log[-100:]],
            # Phase 3
            "agent_knowledge_scores": dict(self.agent_knowledge_scores),
            "agent_lessons_learned": dict(self.agent_lessons_learned),
            "agent_combo_stats": self.agent_combo_stats,
            "saved_at": time.time(),
        }

        state_file = LEARNING_DIR / "meta_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def _load_state(self):
        state_file = LEARNING_DIR / "meta_state.json"
        if not state_file.exists():
            return

        try:
            with open(state_file) as f:
                state = json.load(f)

            self.learning_rate = state.get("learning_rate", self.INITIAL_LR)
            self.lr_gradient = state.get("lr_gradient", 0.0)
            self.confidence_adjustment = state.get("confidence_adjustment", 0.0)
            self.teaching_accuracy = state.get("teaching_accuracy", 0.5)
            self.team_performance_scores = state.get("team_performance_scores", [])
            self.team_score_momentum = state.get("team_score_momentum", 0.0)
            self.meta_metrics = state.get("meta_metrics", self.meta_metrics)
            self.own_accuracy_history = deque(
                state.get("own_accuracy_history", []), maxlen=50
            )

            for name, tdata in state.get("trackers", {}).items():
                if name not in self.trackers:
                    self.trackers[name] = StrategyTracker(name=name)
                t = self.trackers[name]
                t.recent_correct = tdata.get("recent_correct", [])
                t.cumulative_pnl = tdata.get("cumulative_pnl", 0)
                t.win_count = tdata.get("win_count", 0)
                t.loss_count = tdata.get("loss_count", 0)
                t.momentum = tdata.get("momentum", 0.0)
                t.rl_value = tdata.get("rl_value", 0.0)
                t.own_recommendation_hits = tdata.get("own_recommendation_hits", 0)
                t.own_recommendation_total = tdata.get("own_recommendation_total", 0)
                t.knowledge_score = tdata.get("knowledge_score", 1.0)
                t.lessons_learned = tdata.get("lessons_learned", 0)
                for regime, data in tdata.get("regime_performance", {}).items():
                    t.regime_performance[regime] = data

            # Phase 3
            for k, v in state.get("agent_knowledge_scores", {}).items():
                self.agent_knowledge_scores[k] = v
            for k, v in state.get("agent_lessons_learned", {}).items():
                self.agent_lessons_learned[k] = v
            self.agent_combo_stats = state.get("agent_combo_stats", {})

            for tdata in state.get("teaching_log", []):
                self.teaching_log.append(LearningRecord(**{
                    k: tdata[k] for k in LearningRecord.__dataclass_fields__ if k in tdata
                }))

            log.info(
                "Meta-learner state loaded (lr=%.4f, teaching_acc=%.2f, %d teachings, "
                "own_acc=%.2f, momentum=%.2f)",
                self.learning_rate, self.teaching_accuracy, len(self.teaching_log),
                float(np.mean(list(self.own_accuracy_history))) if self.own_accuracy_history else 0.5,
                self.team_score_momentum,
            )
        except Exception:
            log.exception("Failed to load meta-learner state")

    def get_status(self) -> dict:
        own_acc = float(np.mean(list(self.own_accuracy_history))) if self.own_accuracy_history else 0.5
        mvp = max(self.trackers.items(), key=lambda x: x[1].accuracy, default=(None, None))
        laggard = min(self.trackers.items(), key=lambda x: x[1].accuracy, default=(None, None))
        total_lessons = sum(self.agent_lessons_learned.values())
        team_iq = self._compute_team_iq()

        # Recent lessons from DB for UI scroll feed
        recent_lessons = []
        try:
            recent_lessons = self.db.get_learning_history(limit=20)
        except Exception:
            pass

        # Agent communication log (most recent 20)
        comm_log = list(self.communication_log)[-20:]

        return {
            "learning_rate": self.learning_rate,
            "teaching_accuracy": self.teaching_accuracy,
            "confidence_adjustment": self.confidence_adjustment,
            "own_recommendation_accuracy": own_acc,
            "total_post_mortems": self.meta_metrics["total_post_mortems"],
            "weight_adjustments": self.meta_metrics["weight_adjustments"],
            "rl_reward_signals": self.meta_metrics["rl_reward_signals"],
            "helpful_pct": (
                self.meta_metrics["helpful_adjustments"] /
                max(self.meta_metrics["helpful_adjustments"] +
                    self.meta_metrics["harmful_adjustments"], 1)
            ),
            "team_score": self.team_performance_scores[-1] if self.team_performance_scores else 0,
            "team_score_trend": (
                "IMPROVING" if self.team_score_momentum > 0.5
                else "DECLINING" if self.team_score_momentum < -0.5
                else "STABLE"
            ),
            "team_iq": team_iq,
            "mvp_agent": mvp[0] if mvp[0] else "unknown",
            "laggard_agent": laggard[0] if laggard[0] else "unknown",
            "strategy_accuracies": {name: t.accuracy for name, t in self.trackers.items()},
            "strategy_avg_rewards": {name: t.avg_reward for name, t in self.trackers.items()},
            "strategy_weights": dict(self.config.signals.weights),
            "strategy_weights_history": self.team_performance_scores[-20:],
            # Phase 3: Knowledge & learning
            "agent_knowledge_scores": dict(self.agent_knowledge_scores),
            "strategy_knowledge_scores": {
                name: t.knowledge_score for name, t in self.trackers.items()
            },
            "total_lessons_learned": total_lessons,
            "agent_lessons": dict(self.agent_lessons_learned),
            "agent_combo_stats": dict(self.agent_combo_stats),
            "recent_lessons": recent_lessons,
            "communication_log": comm_log,
            "recent_post_mortem": (
                self.post_mortems[-1].to_narrative() if self.post_mortems else ""
            ),
        }

    def get_post_mortems(self, n: int = 10) -> list[PostMortemReport]:
        """Return last N post-mortem reports."""
        return list(self.post_mortems)[-n:]

    def get_agent_scorecard(self) -> dict:
        """Return ranked agent scorecard."""
        return {
            name: {
                "accuracy": t.accuracy,
                "win_rate": t.win_rate,
                "avg_reward": t.avg_reward,
                "cumulative_pnl": t.cumulative_pnl,
                "current_weight": self.config.signals.weights.get(name, 1.0),
                "momentum": t.momentum,
                "knowledge_score": t.knowledge_score,
                "lessons_learned": t.lessons_learned,
                "regime_performance": dict(t.regime_performance),
            }
            for name, t in sorted(
                self.trackers.items(),
                key=lambda x: x[1].accuracy,
                reverse=True,
            )
        }

    # ══════════════════════════════════════════════
    # PHASE 3: KNOWLEDGE SCORES & CROSS-TEACHING
    # ══════════════════════════════════════════════

    def _publish_lesson_learned(self, lesson_type: str, source_agent: str,
                                  target_agent: str, description: str,
                                  impact_score: float = 0.0,
                                  trade_id: Optional[int] = None):
        """Record a lesson, broadcast LESSON_LEARNED event, log to DB."""
        # Add to communication log (in-memory for UI)
        self.communication_log.append({
            "ts": time.time(),
            "from": source_agent,
            "to": target_agent,
            "type": lesson_type,
            "description": description[:200],
            "impact": round(impact_score, 3),
        })

        # Update lessons-learned counters
        for agent in [source_agent, target_agent]:
            if agent == "all":
                for k in self.agent_lessons_learned:
                    self.agent_lessons_learned[k] += 1
                break
            if agent in self.agent_lessons_learned:
                self.agent_lessons_learned[agent] += 1

        # Update strategy tracker lessons (if source or target maps to a strategy)
        for agent in [source_agent, target_agent]:
            if agent in self.trackers:
                self.trackers[agent].lessons_learned += 1

        # Persist to SQLite
        try:
            self.db.insert_learning_history(
                agent_name=source_agent,
                lesson_type=lesson_type,
                description=description,
                impact_score=impact_score,
                trade_id=trade_id,
            )
        except Exception:
            log.debug("Failed to insert learning_history", exc_info=True)

        # Broadcast event
        self.bus.publish(Event(
            type=EventType.LESSON_LEARNED,
            source="meta_learner",
            data={
                "lesson_type": lesson_type,
                "source_agent": source_agent,
                "target_agent": target_agent,
                "description": description,
                "impact_score": impact_score,
                "trade_id": trade_id,
                "timestamp": time.time(),
            },
        ))

        log.debug("Lesson: [%s→%s] %s (impact=%.2f)", source_agent, target_agent,
                  lesson_type, impact_score)

    def _update_agent_knowledge_score(self, agent_name: str, delta: float,
                                        reason: str = ""):
        """Update knowledge score for an agent (clamped to [0.5, 100.0])."""
        if agent_name in self.agent_knowledge_scores:
            old = self.agent_knowledge_scores[agent_name]
            new = float(np.clip(old + delta, 0.5, 100.0))
            self.agent_knowledge_scores[agent_name] = new
            log.debug("Knowledge score %s: %.2f → %.2f (%s)", agent_name, old, new, reason)

    def _update_knowledge_scores_after_trade(self, outcome: str, pnl: float,
                                               report: PostMortemReport):
        """Update all agent knowledge scores based on trade outcome."""
        magnitude = min(1.0, abs(pnl) / 20.0)
        if outcome == "win":
            # Winning agents grow faster
            self._update_agent_knowledge_score("signal_engine", 0.08 * magnitude, "trade_win")
            self._update_agent_knowledge_score("chart_monitor", 0.06 * magnitude, "trade_win")
            self._update_agent_knowledge_score("trade_journal", 0.05 * magnitude, "trade_win")
            if report.news_scanner_verdict and "No news" not in report.news_scanner_verdict:
                self._update_agent_knowledge_score("news_scanner", 0.04 * magnitude, "trade_win")
            if report.dark_pool_verdict and "No dark pool" not in report.dark_pool_verdict:
                if "correct" in report.dark_pool_verdict:
                    self._update_agent_knowledge_score("dark_pool", 0.07 * magnitude, "dp_correct")
                else:
                    self._update_agent_knowledge_score("dark_pool", -0.02 * magnitude, "dp_wrong")
        elif outcome == "loss":
            # Penalize less — still learning
            self._update_agent_knowledge_score("signal_engine", -0.03 * magnitude, "trade_loss")
            self._update_agent_knowledge_score("chart_monitor", -0.02 * magnitude, "trade_loss")
            if report.dark_pool_verdict and "misleading" in report.dark_pool_verdict:
                self._update_agent_knowledge_score("dark_pool", -0.03 * magnitude, "dp_misleading")
        else:  # breakeven — small positive for learning
            for a in ["signal_engine", "chart_monitor", "trade_journal"]:
                self._update_agent_knowledge_score(a, 0.01, "breakeven")

        # Strategy trackers: update knowledge scores based on accuracy delta
        for name, tracker in self.trackers.items():
            if tracker.accuracy > 0.55:
                tracker.knowledge_score = float(np.clip(
                    tracker.knowledge_score + 0.05 * tracker.accuracy, 0.5, 100.0
                ))
            elif tracker.accuracy < 0.45:
                tracker.knowledge_score = float(np.clip(
                    tracker.knowledge_score - 0.02, 0.5, 100.0
                ))

    def _track_agent_combo(self, signal_id: Optional[int], outcome: str,
                             pnl: float, regime: str):
        """Track which combinations of strategies win together."""
        if not signal_id:
            return
        try:
            with self.db.conn() as c:
                rows = c.execute(
                    "SELECT strategy_name, score FROM strategy_scores "
                    "WHERE signal_id=? AND ABS(score) > 0.3",
                    (signal_id,)
                ).fetchall()
        except Exception:
            return

        active = sorted([r["strategy_name"] for r in rows])
        if len(active) < 2:
            return

        # Track pairs
        from itertools import combinations
        for a, b in combinations(active, 2):
            key = f"{a}+{b}"
            if key not in self.agent_combo_stats:
                self.agent_combo_stats[key] = {"wins": 0, "losses": 0, "pnl": 0.0, "regime": {}}
            combo = self.agent_combo_stats[key]
            if outcome == "win":
                combo["wins"] += 1
            elif outcome == "loss":
                combo["losses"] += 1
            combo["pnl"] = round(combo["pnl"] + pnl, 2)
            combo["regime"][regime] = combo["regime"].get(regime, 0) + 1

        # Track the full combination
        if len(active) >= 3:
            key = "+".join(active[:4])  # cap at 4 to avoid combinatorial explosion
            if key not in self.agent_combo_stats:
                self.agent_combo_stats[key] = {"wins": 0, "losses": 0, "pnl": 0.0, "regime": {}}
            combo = self.agent_combo_stats[key]
            if outcome == "win":
                combo["wins"] += 1
            elif outcome == "loss":
                combo["losses"] += 1
            combo["pnl"] = round(combo["pnl"] + pnl, 2)

    def _update_regime_agent_accuracy(self, outcome: str, regime: str):
        """Update per-regime per-agent accuracy for Bayesian weighting."""
        for agent_name in self.agent_knowledge_scores:
            rdata = self.regime_agent_accuracy[agent_name][regime]
            if outcome == "win":
                rdata["wins"] = rdata.get("wins", 0) + 1
            elif outcome == "loss":
                rdata["losses"] = rdata.get("losses", 0) + 1
            # Persist to DB
            try:
                wins   = rdata.get("wins", 0)
                losses = rdata.get("losses", 0)
                total  = wins + losses
                avg_conf = self.agent_knowledge_scores.get(agent_name, 0.5)
                self.db.upsert_agent_accuracy(
                    agent_name=agent_name,
                    signal_type="all",
                    regime=regime,
                    win=(outcome == "win"),
                    avg_confidence=avg_conf,
                )
            except Exception:
                pass

    def _compute_team_iq(self) -> float:
        """Compute a composite 'Team IQ' score (0–200, grows over time).

        Components:
          - Base: average knowledge score across all agents
          - Bonus: win rate, profit factor, teaching accuracy, total lessons
          - Multiplier: faster-learning agents count more
        """
        if not self.agent_knowledge_scores:
            return 100.0

        # Weighted average of all knowledge scores
        all_scores = list(self.agent_knowledge_scores.values())
        all_scores += [t.knowledge_score for t in self.trackers.values()]
        avg_knowledge = float(np.mean(all_scores)) if all_scores else 1.0

        # Lesson bonus: +1 IQ per 10 lessons learned
        total_lessons = sum(self.agent_lessons_learned.values())
        lesson_bonus = total_lessons / 10.0

        # Performance bonus from recent trades
        perf_bonus = 0.0
        try:
            recent = self.db.get_trades(limit=20)
            closed = [t for t in recent if t.get("status") == "closed" and t.get("pnl") is not None]
            if len(closed) >= 3:
                pnls = [t["pnl"] for t in closed]
                wr = sum(1 for p in pnls if p > 0) / len(pnls)
                perf_bonus = wr * 20.0  # 0–20
        except Exception:
            pass

        team_iq = avg_knowledge * 50.0 + lesson_bonus + perf_bonus
        return round(float(np.clip(team_iq, 0, 999)), 1)

    def get_team_iq(self) -> float:
        """Public alias for _compute_team_iq."""
        return self._compute_team_iq()

    def _save_knowledge_to_db(self, trade_id: Optional[int] = None):
        """Persist current knowledge scores and strategy weights to SQLite."""
        try:
            # Save agent knowledge scores
            for agent, score in self.agent_knowledge_scores.items():
                self.db.upsert_agent_knowledge(
                    agent_name=agent,
                    knowledge_type="knowledge_score",
                    key="current",
                    value={"score": score, "lessons": self.agent_lessons_learned.get(agent, 0)},
                    confidence=score / 10.0 if score <= 10 else 1.0,
                )

            # Save strategy tracker knowledge
            for name, tracker in self.trackers.items():
                self.db.upsert_agent_knowledge(
                    agent_name=f"strategy:{name}",
                    knowledge_type="tracker",
                    key="state",
                    value={
                        "knowledge_score": tracker.knowledge_score,
                        "lessons_learned": tracker.lessons_learned,
                        "accuracy": tracker.accuracy,
                        "win_rate": tracker.win_rate,
                        "avg_reward": tracker.avg_reward,
                        "weight": self.config.signals.weights.get(name, 1.0),
                    },
                    confidence=tracker.accuracy,
                )
                # Log weight to history
                self.db.upsert_strategy_weight(
                    strategy_name=name,
                    weight=self.config.signals.weights.get(name, 1.0),
                    cumulative_reward=sum(tracker.recent_rewards),
                    win_count=tracker.win_count,
                    loss_count=tracker.loss_count,
                )

            # Save combo stats
            for combo_key, stats in self.agent_combo_stats.items():
                total = stats["wins"] + stats["losses"]
                if total >= 2:
                    self.db.upsert_agent_knowledge(
                        agent_name="meta_learner",
                        knowledge_type="agent_combo",
                        key=combo_key,
                        value=stats,
                        confidence=stats["wins"] / max(total, 1),
                    )

            # Save team IQ as performance metric
            self.db.insert_agent_performance(
                agent_name="meta_learner",
                metric_name="team_iq",
                value=self._compute_team_iq(),
            )

        except Exception:
            log.debug("Failed to save knowledge to DB", exc_info=True)

    def _load_knowledge_from_db(self):
        """Load persisted knowledge scores from SQLite on startup."""
        try:
            for agent in list(self.agent_knowledge_scores.keys()):
                records = self.db.get_agent_knowledge(agent, knowledge_type="knowledge_score")
                for r in records:
                    if r["key"] == "current":
                        v = r.get("value", {})
                        self.agent_knowledge_scores[agent] = v.get(
                            "score", self.agent_knowledge_scores[agent]
                        )
                        self.agent_lessons_learned[agent] = v.get(
                            "lessons", self.agent_lessons_learned.get(agent, 0)
                        )

            for name, tracker in self.trackers.items():
                records = self.db.get_agent_knowledge(
                    f"strategy:{name}", knowledge_type="tracker"
                )
                for r in records:
                    if r["key"] == "state":
                        v = r.get("value", {})
                        tracker.knowledge_score = v.get("knowledge_score", tracker.knowledge_score)
                        tracker.lessons_learned = v.get("lessons_learned", tracker.lessons_learned)

            # Load combo stats
            combos = self.db.get_agent_knowledge("meta_learner", knowledge_type="agent_combo")
            for r in combos:
                self.agent_combo_stats[r["key"]] = r.get("value", {})

            total = sum(self.agent_lessons_learned.values())
            log.info("Knowledge loaded from DB: %d agents, total_lessons=%d, team_iq=%.1f",
                     len(self.agent_knowledge_scores), total, self._compute_team_iq())

        except Exception:
            log.debug("Failed to load knowledge from DB", exc_info=True)

    # ══════════════════════════════════════════════
    # PHASE 3: CROSS-AGENT EVENT HANDLERS
    # ══════════════════════════════════════════════

    def _on_dark_pool_alert(self, event: Event):
        """When dark pool detects institutional flow, share direction with Signal Engine."""
        dp_data = event.data
        symbol = dp_data.get("symbol", "")
        notional = dp_data.get("notional", 0)
        is_block = dp_data.get("is_block", False)
        price = dp_data.get("price", 0)

        if not is_block or notional < 10_000_000:
            return  # Only care about block trades

        # Store this knowledge for Signal Engine to use
        try:
            self.db.upsert_agent_knowledge(
                agent_name="signal_engine",
                knowledge_type="dark_pool_level",
                key=f"block_{int(price * 4) / 4:.2f}",
                value={
                    "price": price,
                    "notional": notional,
                    "symbol": symbol,
                    "timestamp": event.timestamp,
                },
                confidence=min(1.0, notional / 100_000_000),
            )
        except Exception:
            pass

        self._publish_lesson_learned(
            lesson_type="dark_pool_institutional_level",
            source_agent="dark_pool",
            target_agent="signal_engine",
            description=(
                f"Block print: {symbol} @ ${price:.2f}, "
                f"${notional/1e6:.1f}M notional. "
                "Signal Engine: treat this price as institutional support/resistance."
            ),
            impact_score=0.3,
        )

    def _on_news_event(self, event: Event):
        """When a high-impact news event fires, teach all agents the expected price impact."""
        data = event.data
        headline = data.get("headline", "")
        impact = data.get("market_impact", 0) or 0
        sentiment = data.get("sentiment_score", 0) or 0
        category = data.get("category", "unknown")

        if impact < 2:
            return  # Only teach on high-impact news

        direction = "BULLISH" if sentiment > 0.2 else "BEARISH" if sentiment < -0.2 else "MIXED"
        lesson = (
            f"High-impact {category} news ({direction}): '{headline[:80]}'. "
            f"Impact={impact:.1f}. All agents: expect volatility spike."
        )

        self._publish_lesson_learned(
            lesson_type="high_impact_news",
            source_agent="news_scanner",
            target_agent="all",
            description=lesson,
            impact_score=impact * (1 if sentiment > 0 else -1),
        )

        # Tell Signal Engine to be cautious on new entries
        try:
            self.db.upsert_agent_knowledge(
                agent_name="signal_engine",
                knowledge_type="news_caution",
                key=f"cat_{category}",
                value={
                    "headline": headline[:120],
                    "impact": impact,
                    "direction": direction,
                    "timestamp": event.timestamp,
                },
                confidence=min(1.0, impact / 3.0),
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════
    # PHASE 3: WEEKLY INTELLIGENCE REPORT
    # ══════════════════════════════════════════════

    def generate_weekly_intelligence_report(self) -> str:
        """Generate a human-readable weekly report on what the team learned."""
        lines = [
            "╔══════════════════════════════════════════════╗",
            "║     WEEKLY INTELLIGENCE REPORT — MES INTEL  ║",
            "╚══════════════════════════════════════════════╝",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        # Team IQ
        team_iq = self._compute_team_iq()
        total_lessons = sum(self.agent_lessons_learned.values())
        lines += [
            f"TEAM IQ: {team_iq:.1f}  |  Total Lessons: {total_lessons}",
            f"Team Trend: {('IMPROVING' if self.team_score_momentum > 0.5 else 'DECLINING' if self.team_score_momentum < -0.5 else 'STABLE')}",
            "",
        ]

        # Agent knowledge scores
        lines.append("── AGENT KNOWLEDGE SCORES ──")
        for agent, score in sorted(self.agent_knowledge_scores.items(),
                                    key=lambda x: -x[1]):
            lessons = self.agent_lessons_learned.get(agent, 0)
            bar = "█" * int(score / 5) + "░" * max(0, 10 - int(score / 5))
            lines.append(f"  {agent:<18} {bar}  {score:.1f}  ({lessons} lessons)")
        lines.append("")

        # Strategy scorecards
        lines.append("── STRATEGY SCORECARDS ──")
        for name, t in sorted(self.trackers.items(),
                               key=lambda x: -x[1].knowledge_score):
            w = self.config.signals.weights.get(name, 1.0)
            lines.append(
                f"  {name:<22} acc={t.accuracy:.0%}  kr={t.knowledge_score:.1f}  "
                f"w={w:.2f}  {t.win_count}W/{t.loss_count}L"
            )
        lines.append("")

        # Top winning combos
        top_combos = sorted(
            [(k, v) for k, v in self.agent_combo_stats.items()
             if v.get("wins", 0) + v.get("losses", 0) >= 3],
            key=lambda x: x[1].get("wins", 0) / max(x[1].get("wins", 0) + x[1].get("losses", 0), 1),
            reverse=True,
        )[:5]
        if top_combos:
            lines.append("── TOP STRATEGY COMBOS ──")
            for combo, stats in top_combos:
                total = stats["wins"] + stats["losses"]
                wr = stats["wins"] / total if total > 0 else 0
                lines.append(
                    f"  {combo[:40]:<42} WR={wr:.0%}  PnL=${stats['pnl']:+.2f}"
                )
            lines.append("")

        # Recent lessons (last 10)
        try:
            lessons = self.db.get_learning_history(limit=10)
            if lessons:
                lines.append("── RECENT LESSONS ──")
                for lesson in lessons:
                    ts = time.strftime('%m/%d %H:%M', time.localtime(lesson["timestamp"]))
                    impact = lesson.get("impact_score", 0)
                    sign = "+" if impact >= 0 else ""
                    lines.append(
                        f"  [{ts}] [{lesson['agent_name']:<14}] "
                        f"({sign}{impact:.2f}) {lesson['description'][:80]}"
                    )
        except Exception:
            pass

        lines += ["", "═" * 50]
        report = "\n".join(lines)

        # Save report to file
        try:
            LEARNING_DIR.mkdir(parents=True, exist_ok=True)
            report_file = LEARNING_DIR / f"weekly_report_{time.strftime('%Y%m%d')}.txt"
            report_file.write_text(report)
        except Exception:
            pass

        return report

    # ══════════════════════════════════════════════
    # PUBLIC API: process_trade_result & _teach_agents
    # ══════════════════════════════════════════════

    def process_trade_result(self, trade_result: dict):
        """Public entry point to process a completed trade.

        Accepts a dict with at minimum: trade_id, pnl, outcome, regime, r_multiple.
        Fires the same internal post-mortem pipeline that TRADE_CLOSED triggers.
        """
        # Synthesize a fake event so we can re-use _on_trade_closed
        event = Event(
            type=EventType.TRADE_CLOSED,
            source="external",
            data={
                "trade_id": trade_result.get("trade_id"),
                "pnl": trade_result.get("pnl", 0),
                "r_multiple": trade_result.get("r_multiple"),
                "outcome": trade_result.get("outcome"),
                "regime": trade_result.get("regime", "unknown"),
            },
        )
        self._on_trade_closed(event)

    def _teach_agents(self, lesson_type: str, description: str,
                       impact_score: float = 0.0, trade_id: Optional[int] = None):
        """Broadcast a lesson to ALL agents simultaneously via LESSON_LEARNED event.

        This is a convenience wrapper around _publish_lesson_learned for cases
        where the lesson applies system-wide (e.g. regime change, macro event).
        """
        self._publish_lesson_learned(
            lesson_type=lesson_type,
            source_agent="meta_learner",
            target_agent="all",
            description=description,
            impact_score=impact_score,
            trade_id=trade_id,
        )
