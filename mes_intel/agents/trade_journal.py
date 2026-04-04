"""Trade Journal Agent — logs trades, grades them quantitatively, tracks P&L.

Provides quantitative grading on every trade using:
  - Setup quality (was there a valid signal?)
  - Entry timing (how close to optimal entry?)
  - Exit timing (captured how much of the move?)
  - Risk management (honored stop? proper sizing?)
  - Plan adherence (followed the signal?)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

import numpy as np

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType

log = logging.getLogger(__name__)


def _tod_label(hour: int, minute: int) -> str:
    """Return a human-readable time-of-day label."""
    t = hour * 60 + minute
    if t < 9 * 60 + 30:
        return "pre_market"
    elif t < 10 * 60 + 30:
        return "opening_hour"
    elif t < 12 * 60:
        return "mid_morning"
    elif t < 14 * 60:
        return "midday"
    elif t < 15 * 60 + 30:
        return "power_hour"
    elif t < 16 * 60:
        return "closing_30"
    else:
        return "after_hours"


@dataclass
class TradeRecord:
    """A complete trade record with entry, exit, and grading."""
    id: Optional[int] = None
    signal_id: Optional[int] = None
    entry_time: str = ""
    exit_time: Optional[str] = None
    direction: str = "LONG"
    quantity: int = 1
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    fees: float = 0.0
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    r_multiple: Optional[float] = None
    hold_time_sec: Optional[float] = None
    source: str = "manual"
    notes: str = ""
    status: str = "open"

    def to_db_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "direction": self.direction,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "fees": self.fees,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "r_multiple": self.r_multiple,
            "hold_time_sec": self.hold_time_sec,
            "source": self.source,
            "notes": self.notes,
            "status": self.status,
        }


@dataclass
class TradeGrade:
    """Quantitative grade for a trade."""
    trade_id: int = 0
    setup_quality: float = 0.0     # 0-10: was there a valid setup/signal?
    entry_timing: float = 0.0     # 0-10: how close to optimal entry?
    exit_timing: float = 0.0      # 0-10: how much of move captured?
    risk_management: float = 0.0  # 0-10: honored stops, proper sizing?
    plan_adherence: float = 0.0   # 0-10: followed the signal plan?
    overall_grade: float = 0.0    # 0-10: weighted average
    edge_ratio: float = 0.0      # avg_win / avg_loss (running)
    notes: str = ""

    @property
    def letter_grade(self) -> str:
        if self.overall_grade >= 9:
            return "A+"
        elif self.overall_grade >= 8:
            return "A"
        elif self.overall_grade >= 7:
            return "B"
        elif self.overall_grade >= 6:
            return "C"
        elif self.overall_grade >= 5:
            return "D"
        return "F"

    def to_db_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "setup_quality": self.setup_quality,
            "entry_timing": self.entry_timing,
            "exit_timing": self.exit_timing,
            "risk_management": self.risk_management,
            "plan_adherence": self.plan_adherence,
            "overall_grade": self.overall_grade,
            "edge_ratio": self.edge_ratio,
            "notes": self.notes,
        }


class TradeJournal:
    """Trade journal agent — tracks, grades, and analyzes trades."""

    MES_POINT_VALUE = 5.0    # $5 per point for MES
    MES_TICK_VALUE = 1.25    # $1.25 per tick (0.25 points)
    DEFAULT_FEE = 0.62       # typical MES round-turn fee at AMP

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus
        self._open_trades: dict[int, TradeRecord] = {}

        # Subscribe to events
        self.bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal)
        self.bus.subscribe(EventType.LESSON_LEARNED, self._on_lesson_learned)
        self.bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.QUANT_SIGNAL, self._on_quant_signal)

        # Regime tagging
        self._current_regime: str = "unknown"
        self._current_quant: dict = {}

        # Coaching accumulators — regime performance stats
        self._regime_stats: dict[str, dict] = {}  # regime → {wins, losses, pnl}

        log.info("Trade Journal initialized")

    def open_trade(self, trade: TradeRecord) -> int:
        """Log a new trade entry."""
        if not trade.entry_time:
            trade.entry_time = datetime.now().isoformat()
        if trade.fees == 0:
            trade.fees = self.DEFAULT_FEE * trade.quantity

        # Auto-tag with regime and time context
        from datetime import datetime as _dt
        now = _dt.now()
        _regime   = self._current_regime
        _time_lbl = _tod_label(now.hour, now.minute)
        _dow      = now.strftime("%A")
        _quant    = self._current_quant

        # Attempt to persist regime-tagged columns
        db_dict = trade.to_db_dict()
        db_dict["market_regime"]     = _regime
        db_dict["time_of_day"]       = _time_lbl
        db_dict["day_of_week"]       = _dow
        import json as _json
        db_dict["quant_context_json"] = _json.dumps({
            "rsi": _quant.get("rsi_14", 0),
            "vwap_dev_z": _quant.get("vwap_dev_z", 0),
            "hurst": _quant.get("hurst", 0.5),
            "volume_z": _quant.get("volume_z", 0),
            "bias": _quant.get("bias", "neutral"),
            "profile_shape": _quant.get("profile_shape", "D"),
        })
        try:
            trade.id = self.db.insert_trade(db_dict)
        except Exception:
            trade.id = self.db.insert_trade(trade.to_db_dict())
        self._open_trades[trade.id] = trade

        self.bus.publish(Event(
            type=EventType.TRADE_OPENED,
            source="trade_journal",
            data={
                "trade_id": trade.id,
                "direction": trade.direction,
                "entry_price": trade.entry_price,
                "quantity": trade.quantity,
                "stop": trade.stop_price,
                "target": trade.target_price,
            },
        ))

        log.info("Trade opened: #%d %s %d @ %.2f", trade.id, trade.direction,
                 trade.quantity, trade.entry_price)
        return trade.id

    def close_trade(self, trade_id: int, exit_price: float,
                    exit_time: Optional[str] = None, notes: str = "") -> TradeGrade:
        """Close a trade and grade it."""
        trade = self._open_trades.pop(trade_id, None)

        if trade is None:
            # Load from DB
            trades = self.db.get_trades(limit=1)
            for t in trades:
                if t["id"] == trade_id:
                    trade = TradeRecord(**{k: t[k] for k in TradeRecord.__dataclass_fields__ if k in t})
                    break

        if trade is None:
            raise ValueError(f"Trade #{trade_id} not found")

        trade.exit_price = exit_price
        trade.exit_time = exit_time or datetime.now().isoformat()
        trade.status = "closed"

        # Calculate PnL
        if trade.direction == "LONG":
            points = exit_price - trade.entry_price
        else:
            points = trade.entry_price - exit_price

        trade.pnl = points * self.MES_POINT_VALUE * trade.quantity - trade.fees

        # R-multiple
        if trade.stop_price:
            risk_points = abs(trade.entry_price - trade.stop_price)
            if risk_points > 0:
                trade.r_multiple = points / risk_points

        # Hold time
        try:
            entry_dt = datetime.fromisoformat(trade.entry_time)
            exit_dt = datetime.fromisoformat(trade.exit_time)
            trade.hold_time_sec = (exit_dt - entry_dt).total_seconds()
        except (ValueError, TypeError):
            trade.hold_time_sec = None

        # Update DB
        self.db.update_trade(trade_id, {
            "exit_price": trade.exit_price,
            "exit_time": trade.exit_time,
            "pnl": trade.pnl,
            "r_multiple": trade.r_multiple,
            "hold_time_sec": trade.hold_time_sec,
            "status": "closed",
            "notes": notes,
        })

        # Grade the trade
        grade = self._grade_trade(trade)

        self.bus.publish(Event(
            type=EventType.TRADE_CLOSED,
            source="trade_journal",
            data={
                "trade_id": trade_id,
                "pnl": trade.pnl,
                "r_multiple": trade.r_multiple,
                "grade": grade.letter_grade,
                "overall_score": grade.overall_grade,
            },
        ))

        # Update daily stats
        self._update_daily_stats()

        log.info("Trade closed: #%d PnL=$%.2f R=%.1f Grade=%s",
                 trade_id, trade.pnl, trade.r_multiple or 0, grade.letter_grade)

        return grade

    def _grade_trade(self, trade: TradeRecord) -> TradeGrade:
        """Quantitatively grade a closed trade."""
        grade = TradeGrade(trade_id=trade.id)

        # 1. Setup Quality — was there a signal backing this trade?
        if trade.signal_id:
            signals = self.db.get_signals(limit=100, status=None)
            signal = next((s for s in signals if s["id"] == trade.signal_id), None)
            if signal:
                grade.setup_quality = min(signal["confidence"] * 10, 10.0)
            else:
                grade.setup_quality = 5.0
        else:
            grade.setup_quality = 3.0  # no signal = lower quality setup

        # 2. Entry Timing — R-multiple tells us about entry quality
        if trade.r_multiple is not None:
            if trade.r_multiple >= 3.0:
                grade.entry_timing = 9.0
            elif trade.r_multiple >= 2.0:
                grade.entry_timing = 8.0
            elif trade.r_multiple >= 1.0:
                grade.entry_timing = 7.0
            elif trade.r_multiple >= 0:
                grade.entry_timing = 5.0 + trade.r_multiple * 2
            else:
                grade.entry_timing = max(3.0 + trade.r_multiple, 0)
        else:
            grade.entry_timing = 5.0

        # 3. Exit Timing — how much of potential move was captured
        if trade.target_price and trade.entry_price and trade.exit_price:
            potential_move = abs(trade.target_price - trade.entry_price)
            if trade.direction == "LONG":
                actual_move = trade.exit_price - trade.entry_price
            else:
                actual_move = trade.entry_price - trade.exit_price

            if potential_move > 0:
                capture_ratio = actual_move / potential_move
                grade.exit_timing = min(max(capture_ratio * 10, 0), 10.0)
            else:
                grade.exit_timing = 5.0
        else:
            grade.exit_timing = 5.0 if (trade.pnl or 0) > 0 else 3.0

        # 4. Risk Management
        if trade.stop_price:
            # Did they honor the stop?
            if trade.direction == "LONG":
                breached_stop = trade.exit_price and trade.exit_price < trade.stop_price
            else:
                breached_stop = trade.exit_price and trade.exit_price > trade.stop_price

            if breached_stop:
                grade.risk_management = 2.0  # held through stop
            elif trade.pnl and trade.pnl < 0:
                # Lost money but respected stop
                grade.risk_management = 7.0
            else:
                grade.risk_management = 9.0
        else:
            grade.risk_management = 4.0  # no stop = poor risk management

        # 5. Plan Adherence
        if trade.signal_id:
            grade.plan_adherence = 8.0  # traded with a signal
        else:
            grade.plan_adherence = 4.0  # no signal = off-plan

        # Overall (weighted)
        grade.overall_grade = (
            grade.setup_quality * 0.25 +
            grade.entry_timing * 0.20 +
            grade.exit_timing * 0.20 +
            grade.risk_management * 0.20 +
            grade.plan_adherence * 0.15
        )

        # Edge ratio (running average)
        recent_trades = self.db.get_trades(limit=50)
        wins = [t["pnl"] for t in recent_trades if t.get("pnl") and t["pnl"] > 0]
        losses = [abs(t["pnl"]) for t in recent_trades if t.get("pnl") and t["pnl"] < 0]
        if wins and losses:
            grade.edge_ratio = np.mean(wins) / np.mean(losses)

        grade.notes = f"PnL=${trade.pnl:.2f}" if trade.pnl else ""

        # Save grade
        self.db.insert_grade(grade.to_db_dict())

        self.bus.publish(Event(
            type=EventType.TRADE_GRADED,
            source="trade_journal",
            data={
                "trade_id": trade.id,
                "grade": grade.letter_grade,
                "scores": {
                    "setup": grade.setup_quality,
                    "entry": grade.entry_timing,
                    "exit": grade.exit_timing,
                    "risk": grade.risk_management,
                    "plan": grade.plan_adherence,
                },
                "overall": grade.overall_grade,
                "edge_ratio": grade.edge_ratio,
            },
        ))

        return grade

    def _update_daily_stats(self):
        """Recalculate today's stats."""
        today = date.today().isoformat()
        trades = self.db.get_trades(limit=500, start_date=today)
        closed = [t for t in trades if t["status"] == "closed"]

        if not closed:
            return

        pnls = [t["pnl"] for t in closed if t["pnl"] is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        fees = sum(t.get("fees", 0) for t in closed)

        gross_pnl = sum(pnls)

        # Sharpe (annualized from daily)
        if len(pnls) > 1:
            sharpe = np.mean(pnls) / max(np.std(pnls), 1e-10) * np.sqrt(252)
        else:
            sharpe = 0

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0

        # Profit factor
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0
        pf = total_wins / max(total_losses, 1e-10)

        # Avg R
        r_multiples = [t["r_multiple"] for t in closed if t.get("r_multiple") is not None]
        avg_r = float(np.mean(r_multiples)) if r_multiples else 0

        stats = {
            "date": today,
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "gross_pnl": gross_pnl,
            "net_pnl": gross_pnl - fees,
            "fees": fees,
            "largest_win": max(wins) if wins else 0,
            "largest_loss": min(losses) if losses else 0,
            "avg_win": float(np.mean(wins)) if wins else 0,
            "avg_loss": float(np.mean(losses)) if losses else 0,
            "win_rate": len(wins) / max(len(closed), 1),
            "profit_factor": pf,
            "max_drawdown": max_dd,
            "avg_r_multiple": avg_r,
            "sharpe": float(sharpe),
        }

        self.db.upsert_daily_stats(stats)

        self.bus.publish(Event(
            type=EventType.DAILY_STATS_UPDATE,
            source="trade_journal",
            data=stats,
        ))

    def _on_signal(self, event: Event):
        """Auto-create draft trade entry from high-confidence signals."""
        d = event.data
        direction = d.get("direction", "FLAT")
        confidence = d.get("confidence", 0)
        entry_price = d.get("entry") or d.get("entry_price")
        signal_id = d.get("signal_id")

        if direction == "FLAT" or not entry_price:
            return

        # Only auto-create for signals above min_confidence
        if confidence < 0.5:
            return

        try:
            trade = TradeRecord(
                signal_id=signal_id,
                direction=direction,
                entry_price=float(entry_price),
                stop_price=d.get("stop"),
                target_price=d.get("target"),
                quantity=1,
                source="auto_signal",
                status="open",
                notes=f"Auto from signal #{signal_id} "
                      f"(conf={confidence:.0%}, regime={d.get('regime', '?')})",
            )
            trade_id = self.open_trade(trade)
            log.info(
                "Auto-created trade #%d from signal: %s @ %.2f",
                trade_id, direction, entry_price,
            )
        except Exception:
            log.debug("Signal received (no auto-trade): %s", d)

    def _on_lesson_learned(self, event: Event):
        """Receive a lesson from meta-learner and persist it."""
        data = event.data
        target = data.get("target_agent", "")
        if target not in ("trade_journal", "all"):
            return
        lesson_type = data.get("lesson_type", "")
        description = data.get("description", "")
        impact = data.get("impact_score", 0.0)
        try:
            self.db.upsert_agent_knowledge(
                agent_name="trade_journal",
                knowledge_type=f"lesson:{lesson_type}",
                key=f"ts_{int(event.timestamp)}",
                value={"description": description, "impact": impact},
                confidence=min(1.0, abs(impact)),
            )
        except Exception:
            pass

    def _on_trade_result(self, event: Event):
        """Learn from final trade outcome — persist grading calibration data."""
        outcome = event.data.get("outcome", "")
        pnl     = event.data.get("pnl", 0)
        regime  = event.data.get("regime", "")
        if not outcome:
            return

        # Update regime stats for coaching
        if regime:
            rs = self._regime_stats.setdefault(regime, {"wins": 0, "losses": 0, "pnl": 0.0})
            if outcome == "win":
                rs["wins"] += 1
            elif outcome == "loss":
                rs["losses"] += 1
            rs["pnl"] = round(rs["pnl"] + pnl, 2)

        try:
            self.db.upsert_agent_knowledge(
                agent_name="trade_journal",
                knowledge_type="grade_calibration",
                key=f"{regime}_{outcome}",
                value={"outcome": outcome, "pnl": pnl, "regime": regime},
                confidence=min(1.0, abs(pnl) / 20.0),
            )
        except Exception:
            pass

    def _on_regime_change(self, event: Event):
        self._current_regime = event.data.get("to_regime", "unknown")

    def _on_quant_signal(self, event: Event):
        self._current_quant = event.data
        regime = event.data.get("regime", "unknown")
        if regime and regime != "unknown":
            self._current_regime = regime

    def get_regime_coaching(self) -> list[dict]:
        """Return coaching insights based on regime-tagged trade performance."""
        coaching = []
        for regime, stats in self._regime_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < 3:
                continue
            wr = stats["wins"] / total
            msg = ""
            if wr >= 0.65:
                msg = (f"Strong performance in {regime} conditions "
                       f"({wr*100:.0f}% win rate, {total} trades). "
                       f"P&L: ${stats['pnl']:+.2f}. Keep trading this regime.")
            elif wr <= 0.40:
                msg = (f"Struggling in {regime} conditions "
                       f"({wr*100:.0f}% win rate, {total} trades). "
                       f"P&L: ${stats['pnl']:+.2f}. Consider sitting out or reducing size.")
            if msg:
                coaching.append({
                    "regime": regime,
                    "win_rate": round(wr, 3),
                    "total_trades": total,
                    "pnl": stats["pnl"],
                    "coaching": msg,
                })
        return sorted(coaching, key=lambda x: x["win_rate"])

    def get_performance_summary(self, days: int = 30) -> dict:
        """Get performance summary for the last N days."""
        trades = self.db.get_trades(limit=500)
        if not trades:
            return {"message": "No trades recorded"}

        closed = [t for t in trades if t["status"] == "closed" and t["pnl"] is not None]
        if not closed:
            return {"message": "No closed trades"}

        pnls = [t["pnl"] for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return {
            "total_trades": len(closed),
            "win_rate": len(wins) / len(closed) * 100,
            "total_pnl": sum(pnls),
            "avg_pnl": float(np.mean(pnls)),
            "profit_factor": sum(wins) / max(abs(sum(losses)), 1e-10) if losses else float("inf"),
            "largest_win": max(wins) if wins else 0,
            "largest_loss": min(losses) if losses else 0,
            "avg_win": float(np.mean(wins)) if wins else 0,
            "avg_loss": float(np.mean(losses)) if losses else 0,
            "sharpe": float(np.mean(pnls) / max(np.std(pnls), 1e-10) * np.sqrt(252)),
            "avg_r": float(np.mean([t["r_multiple"] for t in closed if t.get("r_multiple")])) if any(t.get("r_multiple") for t in closed) else 0,
        }
