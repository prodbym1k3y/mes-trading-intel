"""Enhanced Tradezella-style AI Trade Journal — MES Intel.

Layout:
  LEFT   — Scrollable trade list with search/filter/sort
  RIGHT  — Tabs: [LOG TRADE] [DETAIL] [DASHBOARD]

Features:
  TradeEntryForm    — full form with emotion, tags, notes, screenshot
  TradeListView     — clickable rows, color-coded P&L, filter/search
  TradeDetailPanel  — detail view with synthetic mini chart + AI analysis
  JournalDashboard  — equity curve, heatmaps, stats, AI insights
  EnhancedJournalTab — main container (replaces old journal tab)
"""
from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSplitter, QTextEdit, QLineEdit,
    QComboBox, QCheckBox, QGridLayout, QTabWidget, QFileDialog,
    QSizePolicy, QSpacerItem,
)
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QLinearGradient,
    QPainterPath, QPixmap, QCursor,
)

try:
    from ..data.amp_sync import (
        import_from_csv, rithmic_sync, AutoSyncManager, RITHMIC_AVAILABLE,
    )
    _AMP_SYNC_AVAILABLE = True
except Exception:
    _AMP_SYNC_AVAILABLE = False
    RITHMIC_AVAILABLE = False

from .theme import COLORS
from .charts_enhanced import NeonLineChart

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

SETUP_TYPES = [
    "Absorption Play", "Breakout", "Mean Reversion", "News Play",
    "Fade", "Opening Range", "VWAP Bounce", "Delta Divergence",
    "Level 2 Stack", "Imbalance Fill", "Custom",
]

EMOTIONS = ["Confident", "Calm", "Nervous", "FOMO", "Revenge", "Excited", "Bored", "Uncertain"]

GRADE_COLORS = {
    "A+": COLORS["cyan"],
    "A":  COLORS["green_bright"],
    "B":  COLORS["green_mid"],
    "C":  COLORS["amber"],
    "D":  COLORS["orange"],
    "F":  COLORS["pink"],
    "?":  COLORS["text_muted"],
}

MONO = "'Courier New', monospace"

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _label(text: str, size: int = 10, color: str = COLORS["text_muted"],
           bold: bool = False, spacing: int = 2) -> QLabel:
    lbl = QLabel(text)
    weight = "bold" if bold else "normal"
    lbl.setStyleSheet(
        f"font-family: {MONO}; font-size: {size}px; color: {color}; "
        f"font-weight: {weight}; letter-spacing: {spacing}px; background: transparent;"
    )
    return lbl


def _input(placeholder: str = "", width: int | None = None) -> QLineEdit:
    w = QLineEdit()
    w.setPlaceholderText(placeholder)
    if width:
        w.setFixedWidth(width)
    w.setStyleSheet(
        f"background: {COLORS['bg_input']}; color: {COLORS['cyan']}; "
        f"border: 1px solid {COLORS['cyan_dim']}; padding: 4px 8px; "
        f"font-family: {MONO}; font-size: 12px; "
        f"selection-background-color: {COLORS['cyan_dim']};"
    )
    return w


def _combo(items: list[str]) -> QComboBox:
    w = QComboBox()
    w.addItems(items)
    w.setStyleSheet(
        f"background: {COLORS['bg_input']}; color: {COLORS['cyan']}; "
        f"border: 1px solid {COLORS['cyan_dim']}; padding: 3px 6px; "
        f"font-family: {MONO}; font-size: 11px;"
    )
    return w


def _btn(text: str, color: str = COLORS["cyan"], bg: str = "transparent",
         size: int = 11) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton {{ background: {bg}; color: {color}; "
        f"border: 1px solid {color}; padding: 5px 12px; "
        f"font-family: {MONO}; font-size: {size}px; letter-spacing: 1px; "
        f"font-weight: bold; }}"
        f"QPushButton:hover {{ background: {color}20; }}"
        f"QPushButton:pressed {{ background: {color}40; }}"
    )
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    return btn


def _panel(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    frame.setStyleSheet(
        f"QFrame#panel {{ background: {COLORS['bg_panel']}; "
        f"border: 1px solid {COLORS['cyan_dim']}; }}"
    )
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(8, 6, 8, 6)
    lay.setSpacing(4)
    if title:
        lbl = _label(title, size=9, color=COLORS["cyan_mid"], bold=True, spacing=3)
        lay.addWidget(lbl)
    return frame, lay


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(
        f"color: {COLORS['cyan_dim']}; background: {COLORS['cyan_dim']}; max-height: 1px;"
    )
    return f


def _letter_grade(score: float) -> str:
    if score >= 9:
        return "A+"
    elif score >= 8:
        return "A"
    elif score >= 7:
        return "B"
    elif score >= 6:
        return "C"
    elif score >= 5:
        return "D"
    return "F"


def _pnl_color(pnl: float | None) -> str:
    if pnl is None:
        return COLORS["text_muted"]
    if pnl > 0:
        return COLORS["green_bright"]
    if pnl < 0:
        return COLORS["pink"]
    return COLORS["amber"]


# ─────────────────────────────────────────────────────────────────────────────
#  PriceActionMiniChart
# ─────────────────────────────────────────────────────────────────────────────

class PriceActionMiniChart(QWidget):
    """Synthetic mini chart showing price action around a trade."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trade: dict | None = None
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"background: {COLORS['bg_dark']};")

    def load_trade(self, trade: dict):
        self._trade = trade
        self.update()

    def _generate_path(self, entry: float, exit_p: float, n: int = 60) -> list[float]:
        prices = [entry]
        volatility = abs(exit_p - entry) * 0.12 + 0.25
        drift = (exit_p - entry) / (n * 0.8)
        for i in range(1, n):
            progress = i / n
            bias = drift + (exit_p - prices[-1]) * 0.08 * progress
            noise = random.gauss(0, volatility)
            prices.append(prices[-1] + bias + noise)
        prices[-1] = exit_p
        return prices

    def paintEvent(self, event):
        if not self._trade:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        ml, mr, mt, mb = 52, 8, 10, 24
        draw_w = w - ml - mr
        draw_h = h - mt - mb

        t = self._trade
        entry = t.get("entry_price", 5000.0)
        exit_p = t.get("exit_price") or entry
        stop = t.get("stop_price")
        target = t.get("target_price")
        direction = t.get("direction", "LONG")

        prices = self._generate_path(entry, exit_p)
        all_prices = list(prices)
        if stop:
            all_prices.append(stop)
        if target:
            all_prices.append(target)

        mn, mx = min(all_prices), max(all_prices)
        rng = mx - mn or 1.0
        padding = rng * 0.12
        mn -= padding
        mx += padding
        rng = mx - mn

        def px(i: int) -> float:
            return ml + i * draw_w / (len(prices) - 1)

        def py(price: float) -> float:
            return mt + draw_h * (1 - (price - mn) / rng)

        # Grid
        p.setPen(QPen(QColor(COLORS["grid"]), 1, Qt.PenStyle.DotLine))
        for row in range(5):
            y = mt + row * draw_h / 4
            p.drawLine(int(ml), int(y), int(w - mr), int(y))

        # Stop line
        if stop:
            p.setPen(QPen(QColor(COLORS["pink"]), 1, Qt.PenStyle.DashLine))
            sy = int(py(stop))
            p.drawLine(ml, sy, w - mr, sy)
            p.setFont(QFont("Courier New", 7))
            p.setPen(QColor(COLORS["pink"]))
            p.drawText(2, sy + 4, f"SL {stop:.2f}")

        # Target line
        if target:
            p.setPen(QPen(QColor(COLORS["green_bright"]), 1, Qt.PenStyle.DashLine))
            ty = int(py(target))
            p.drawLine(ml, ty, w - mr, ty)
            p.setFont(QFont("Courier New", 7))
            p.setPen(QColor(COLORS["green_bright"]))
            p.drawText(2, ty + 4, f"TP {target:.2f}")

        # Price path
        path = QPainterPath()
        path.moveTo(px(0), py(prices[0]))
        for i in range(1, len(prices)):
            path.lineTo(px(i), py(prices[i]))

        # Fill gradient
        fill_path = QPainterPath(path)
        fill_path.lineTo(px(len(prices) - 1), h - mb)
        fill_path.lineTo(px(0), h - mb)
        fill_path.closeSubpath()
        win = (t.get("pnl") or 0) > 0
        fill_color = QColor(COLORS["green_bright"] if win else COLORS["pink"])
        fill_color.setAlphaF(0.07)
        p.fillPath(fill_path, QBrush(fill_color))

        # Glow pass
        glow = QColor(COLORS["cyan"])
        glow.setAlphaF(0.15)
        p.setPen(QPen(glow, 5))
        p.drawPath(path)
        # Main line
        p.setPen(QPen(QColor(COLORS["cyan"]), 2))
        p.drawPath(path)

        # Entry marker
        ex = int(px(0))
        ey = int(py(entry))
        entry_color = QColor(COLORS["green_bright"])
        p.setBrush(QBrush(entry_color))
        p.setPen(QPen(entry_color, 1))
        arrow = QPainterPath()
        if direction == "LONG":
            arrow.moveTo(ex, ey - 10); arrow.lineTo(ex - 6, ey); arrow.lineTo(ex + 6, ey)
        else:
            arrow.moveTo(ex, ey + 10); arrow.lineTo(ex - 6, ey); arrow.lineTo(ex + 6, ey)
        arrow.closeSubpath()
        p.fillPath(arrow, QBrush(entry_color))

        # Exit marker
        exit_x = int(px(len(prices) - 1))
        exit_y = int(py(exit_p))
        exit_color = QColor(COLORS["pink"])
        p.setBrush(QBrush(exit_color))
        p.setPen(QPen(exit_color, 1))
        arrow2 = QPainterPath()
        if direction == "LONG":
            arrow2.moveTo(exit_x, exit_y + 10); arrow2.lineTo(exit_x - 6, exit_y); arrow2.lineTo(exit_x + 6, exit_y)
        else:
            arrow2.moveTo(exit_x, exit_y - 10); arrow2.lineTo(exit_x - 6, exit_y); arrow2.lineTo(exit_x + 6, exit_y)
        arrow2.closeSubpath()
        p.fillPath(arrow2, QBrush(exit_color))

        # Y axis labels
        p.setFont(QFont("Courier New", 7))
        for row in range(5):
            price_val = mn + rng * (1 - row / 4)
            y = mt + draw_h * row / 4
            p.setPen(QColor(COLORS["text_muted"]))
            p.drawText(2, int(y) + 4, f"{price_val:.1f}")

        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  AI Analysis Engine
# ─────────────────────────────────────────────────────────────────────────────

class AIAnalysisEngine:
    """Rule-based post-trade AI analysis. Generates Tradezella-style feedback."""

    def analyze(self, trade: dict, grade: dict | None = None,
                similar_trades: list[dict] | None = None) -> dict:
        well: list[str] = []
        improve: list[str] = []

        pnl = trade.get("pnl") or 0
        r = trade.get("r_multiple")
        entry = trade.get("entry_price", 0)
        exit_p = trade.get("exit_price") or entry
        stop = trade.get("stop_price")
        target = trade.get("target_price")
        direction = trade.get("direction", "LONG")
        emotion = trade.get("emotion", "") or ""
        hold_sec = trade.get("hold_time_sec")
        signal_id = trade.get("signal_id")
        tags = trade.get("tags", "") or ""

        # ── What you did well ──────────────────────────────────
        if signal_id:
            well.append("Traded with a confirmed signal — plan adherence is the foundation of edge.")
        if stop:
            well.append("You had a defined stop-loss — your risk was quantified before entry.")
        if target:
            well.append("You set a profit target, enabling systematic exit planning.")
        if r is not None and r >= 2.0:
            well.append(f"Excellent R-multiple of {r:.1f}R — you let the winner run.")
        elif r is not None and r >= 1.0:
            well.append(f"Positive R-multiple ({r:.1f}R) — trade was profitable relative to risk.")
        if pnl > 0 and stop and entry:
            risk = abs(entry - stop) * 5.0
            well.append(f"Captured ${pnl:.0f} while risking ~${risk:.0f} — positive expectancy.")
        if emotion in ("Confident", "Calm"):
            well.append(f"Emotional state '{emotion}' — disciplined, process-driven mindset.")
        if hold_sec and 120 <= hold_sec <= 1800:
            well.append(f"Hold time {hold_sec/60:.0f}min — appropriate for intraday MES scalping.")
        if not well:
            well.append("Trade was logged — journaling every trade is a professional habit.")

        # ── What to improve ────────────────────────────────────
        if not signal_id:
            improve.append("No signal ID linked — was this backed by confluence? Track signal context to measure edge.")
        if not stop:
            improve.append("No stop-loss recorded. Trading without a defined stop destroys expectancy over time.")
        if emotion in ("FOMO", "Revenge"):
            improve.append(f"Emotional state: '{emotion}' — highest-risk emotional states. Review entry criteria objectively.")
        if r is not None and r < 0:
            improve.append(f"Negative R ({r:.1f}R). Review whether entry had sufficient confluence.")
        elif r is not None and 0 <= r < 0.8:
            improve.append(f"Low R ({r:.1f}R). Improve entry or exit timing — review mini chart.")
        if stop and entry and exit_p:
            if direction == "LONG" and exit_p < stop:
                improve.append("Exit was BELOW stop-loss — stop was breached without being honored. Non-negotiable rule.")
            elif direction == "SHORT" and exit_p > stop:
                improve.append("Exit was ABOVE stop-loss — stop was breached.")
        if hold_sec and hold_sec < 30:
            improve.append("Trade closed under 30s — premature? Quick exits often leave money on the table.")
        if hold_sec and hold_sec > 7200:
            improve.append("Hold time > 2hrs. MES intraday setups typically resolve faster. Did the thesis change?")
        if target and entry and exit_p:
            potential = abs(target - entry)
            actual = abs(exit_p - entry)
            if potential > 0 and actual / potential < 0.4:
                improve.append(f"Only {actual/potential*100:.0f}% of target captured. Hold longer or use scale-out exit.")
        if not improve:
            improve.append("Execution was solid. Replicate this process consistently.")

        # ── Optimal entry/exit ────────────────────────────────
        optimal_entry = self._suggest_entry(trade)
        optimal_exit = self._suggest_exit(trade)

        # ── Pattern matching ──────────────────────────────────
        pattern_msg = ""
        if similar_trades and len(similar_trades) >= 3:
            wins = [t for t in similar_trades if (t.get("pnl") or 0) > 0]
            wr = len(wins) / len(similar_trades) * 100
            ids = [str(t.get("id", "?")) for t in similar_trades[:5]]
            pattern_msg = (
                f"Pattern '{tags}' matches trades #{', #'.join(ids)} — "
                f"your win rate on this setup: {wr:.0f}% ({len(wins)}/{len(similar_trades)})"
            )

        # ── Grade ─────────────────────────────────────────────
        if grade:
            score = grade.get("overall_grade", 5.0)
        else:
            score = self._quick_score(trade)
        letter = _letter_grade(score)

        return {
            "well": well,
            "improve": improve,
            "optimal_entry": optimal_entry,
            "optimal_exit": optimal_exit,
            "grade": letter,
            "grade_score": score,
            "grade_breakdown": self._grade_breakdown(trade, grade),
            "pattern_msg": pattern_msg,
        }

    def _suggest_entry(self, trade: dict) -> str:
        entry = trade.get("entry_price", 0)
        stop = trade.get("stop_price")
        direction = trade.get("direction", "LONG")
        if not entry:
            return "—"
        if stop:
            risk = abs(entry - stop)
            if risk > 1.0:
                better = entry + (0.5 if direction == "LONG" else -0.5)
                return (
                    f"{better:.2f} — Waiting for a 2-tick pullback would improve R:R "
                    f"from {risk:.1f}pts to {risk*0.8:.1f}pts risk."
                )
        return f"{entry:.2f} — Entry appeared reasonable given the setup conditions."

    def _suggest_exit(self, trade: dict) -> str:
        exit_p = trade.get("exit_price")
        target = trade.get("target_price")
        entry = trade.get("entry_price", 0)
        direction = trade.get("direction", "LONG")
        pnl = trade.get("pnl") or 0
        if not exit_p:
            return "—"
        if target:
            if direction == "LONG" and exit_p < target:
                missed = (target - exit_p) * 5
                return (f"{target:.2f} — Holding to target adds ~${missed:.0f}. "
                        f"Use limit orders at target level.")
            elif direction == "SHORT" and exit_p > target:
                missed = (exit_p - target) * 5
                return f"{target:.2f} — Holding to target adds ~${missed:.0f}."
        if pnl > 0:
            return f"{exit_p:.2f} — Profitable exit. Trail stop on future winners to capture more."
        return f"{exit_p:.2f} — Stop hit. Honor stops without hesitation — it's your job."

    def _quick_score(self, trade: dict) -> float:
        score = 5.0
        r = trade.get("r_multiple")
        if trade.get("signal_id"):
            score += 1.5
        if trade.get("stop_price"):
            score += 1.0
        if r:
            score += 1.5 if r >= 2 else (0.8 if r >= 1 else -1.0)
        emo = trade.get("emotion", "")
        if emo in ("Confident", "Calm"):
            score += 0.5
        elif emo in ("FOMO", "Revenge"):
            score -= 1.5
        return max(0.0, min(10.0, score))

    def _grade_breakdown(self, trade: dict, grade: dict | None) -> dict:
        if grade:
            return {
                "Setup":    grade.get("setup_quality", 5.0),
                "Entry":    grade.get("entry_timing", 5.0),
                "Exit":     grade.get("exit_timing", 5.0),
                "Risk Mgmt": grade.get("risk_management", 5.0),
                "Plan":     grade.get("plan_adherence", 5.0),
            }
        r = trade.get("r_multiple") or 0
        return {
            "Setup":    8.0 if trade.get("signal_id") else 4.0,
            "Entry":    min(10, max(0, 5 + r * 2)),
            "Exit":     6.0 if (trade.get("pnl") or 0) > 0 else 4.0,
            "Risk Mgmt": 8.0 if trade.get("stop_price") else 3.0,
            "Plan":     8.0 if trade.get("signal_id") else 4.0,
        }


_ai_engine = AIAnalysisEngine()


# ─────────────────────────────────────────────────────────────────────────────
#  TradeEntryForm
# ─────────────────────────────────────────────────────────────────────────────

class TradeEntryForm(QWidget):
    """Full trade entry form with emotion selector, tags, notes, screenshot."""

    trade_saved = Signal(dict)

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._screenshot_path: str | None = None
        self._edit_id: int | None = None
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # Mode toggle
        mode_row = QHBoxLayout()
        self._mode_full = _btn("◆ FULL ENTRY", COLORS["cyan"])
        self._mode_quick = _btn("▶ QUICK ADD", COLORS["magenta"])
        self._mode_full.setCheckable(True)
        self._mode_quick.setCheckable(True)
        self._mode_full.setChecked(True)
        self._mode_full.clicked.connect(lambda: self._set_mode(False))
        self._mode_quick.clicked.connect(lambda: self._set_mode(True))
        mode_row.addWidget(self._mode_full)
        mode_row.addWidget(self._mode_quick)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        lay.addWidget(_sep())

        # Core fields grid
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)

        def add_field(row, col, label_text, widget):
            grid.addWidget(_label(label_text, 8, COLORS["text_muted"]), row * 2, col)
            grid.addWidget(widget, row * 2 + 1, col)

        self._dir_combo = _combo(["LONG", "SHORT"])
        self._size = _input("1")
        self._size.setText("1")
        self._datetime_inp = _input(datetime.now().strftime("%Y-%m-%d %H:%M"))
        self._datetime_inp.setText(datetime.now().strftime("%Y-%m-%d %H:%M"))
        self._entry = _input("5000.00")
        self._exit = _input("5010.00")
        self._stop = _input("4995.00")
        self._target = _input("5020.00")

        add_field(0, 0, "DIRECTION", self._dir_combo)
        add_field(0, 1, "SIZE (CONTRACTS)", self._size)
        add_field(0, 2, "DATE / TIME", self._datetime_inp)
        add_field(1, 0, "ENTRY PRICE", self._entry)
        add_field(1, 1, "EXIT PRICE", self._exit)

        # Auto-calc display
        self._pnl_display = _label("P&L: —", 13, COLORS["text_muted"], bold=True)
        grid.addWidget(_label("P&L (auto)", 8, COLORS["text_muted"]), 2, 2)
        grid.addWidget(self._pnl_display, 3, 2)

        add_field(2, 0, "STOP LOSS", self._stop)
        add_field(2, 1, "TAKE PROFIT", self._target)

        self._rr_display = _label("R:R — : —", 12, COLORS["text_muted"], bold=True)
        grid.addWidget(_label("R:R RATIO (auto)", 8, COLORS["text_muted"]), 4, 2)
        grid.addWidget(self._rr_display, 5, 2)

        lay.addLayout(grid)

        # ── Full mode extras ──────────────────────────────────────
        self._full_widget = QWidget()
        fl = QVBoxLayout(self._full_widget)
        fl.setContentsMargins(0, 4, 0, 0)
        fl.setSpacing(6)

        fl.addWidget(_sep())

        # Setup type
        fl.addWidget(_label("SETUP TYPE / TAGS", 8, COLORS["text_muted"]))
        self._setup_combo = _combo(SETUP_TYPES)
        fl.addWidget(self._setup_combo)

        # Emotion
        fl.addWidget(_label("EMOTIONAL STATE", 8, COLORS["text_muted"]))
        emo_row = QHBoxLayout()
        emo_row.setSpacing(4)
        self._emotion_btns: dict[str, QPushButton] = {}
        for emo in EMOTIONS:
            color = COLORS["orange"] if emo in ("FOMO", "Revenge") else COLORS["cyan_mid"]
            b = _btn(emo, color, size=9)
            b.setCheckable(True)
            b.clicked.connect(lambda checked, e=emo: self._select_emotion(e))
            emo_row.addWidget(b)
            self._emotion_btns[emo] = b
        emo_row.addStretch()
        fl.addLayout(emo_row)

        # Notes
        fl.addWidget(_label("TRADE NOTES / SETUP DESCRIPTION", 8, COLORS["text_muted"]))
        self._notes = QTextEdit()
        self._notes.setPlaceholderText(
            "Describe your setup: What was the confluence? What triggered entry?\n"
            "How did price action look at entry? What was your thesis?"
        )
        self._notes.setFixedHeight(90)
        self._notes.setStyleSheet(
            f"background: {COLORS['bg_input']}; color: {COLORS['text_white']}; "
            f"border: 1px solid {COLORS['cyan_dim']}; padding: 6px; "
            f"font-family: {MONO}; font-size: 11px;"
        )
        fl.addWidget(self._notes)

        # Screenshot
        scr_row = QHBoxLayout()
        self._scr_btn = _btn("📷 ATTACH SCREENSHOT", COLORS["magenta"])
        self._scr_btn.clicked.connect(self._attach_screenshot)
        self._scr_label = _label("No screenshot attached", 9, COLORS["text_muted"])
        scr_row.addWidget(self._scr_btn)
        scr_row.addWidget(self._scr_label)
        scr_row.addStretch()
        fl.addLayout(scr_row)

        lay.addWidget(self._full_widget)

        lay.addWidget(_sep())

        # Save buttons
        btn_row = QHBoxLayout()
        self._save_btn = _btn("◆ SAVE TRADE", COLORS["green_bright"], size=12)
        self._save_btn.clicked.connect(self._save_trade)
        self._clear_btn = _btn("✕ CLEAR", COLORS["text_muted"])
        self._clear_btn.clicked.connect(self._clear_form)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        lay.addStretch()

        # Wire auto-calc
        for w in (self._entry, self._exit, self._size, self._stop, self._target):
            w.textChanged.connect(self._calc_pnl)
        self._dir_combo.currentTextChanged.connect(self._calc_pnl)

    def _set_mode(self, quick: bool):
        self._full_widget.setVisible(not quick)
        self._mode_full.setChecked(not quick)
        self._mode_quick.setChecked(quick)

    def _select_emotion(self, emo: str):
        for e, b in self._emotion_btns.items():
            b.setChecked(e == emo)

    def _attach_screenshot(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Chart Screenshot", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if path:
            self._screenshot_path = path
            self._scr_label.setText(os.path.basename(path))
            self._scr_label.setStyleSheet(
                f"font-family: {MONO}; font-size: 9px; "
                f"color: {COLORS['green_bright']}; background: transparent;"
            )

    def _calc_pnl(self):
        try:
            entry = float(self._entry.text() or 0)
            exit_p = float(self._exit.text() or 0)
            size = max(1, int(self._size.text() or 1))
            direction = self._dir_combo.currentText()
            stop_v = float(self._stop.text() or 0)
            target_v = float(self._target.text() or 0)

            points = (exit_p - entry) if direction == "LONG" else (entry - exit_p)
            pnl = points * 5.0 * size - 0.62 * size
            color = _pnl_color(pnl)
            sign = "+" if pnl > 0 else ""
            self._pnl_display.setText(f"P&L: {sign}${pnl:.2f}")
            self._pnl_display.setStyleSheet(
                f"font-family: {MONO}; font-size: 13px; font-weight: bold; "
                f"color: {color}; background: transparent;"
            )

            if stop_v and entry and target_v:
                risk = abs(entry - stop_v)
                reward = abs(target_v - entry)
                if risk > 0:
                    rr = reward / risk
                    rr_color = (COLORS["green_bright"] if rr >= 2 else
                                COLORS["amber"] if rr >= 1 else COLORS["pink"])
                    self._rr_display.setText(f"R:R 1 : {rr:.1f}")
                    self._rr_display.setStyleSheet(
                        f"font-family: {MONO}; font-size: 12px; font-weight: bold; "
                        f"color: {rr_color}; background: transparent;"
                    )
        except (ValueError, ZeroDivisionError):
            pass

    def _get_selected_emotion(self) -> str:
        for emo, btn in self._emotion_btns.items():
            if btn.isChecked():
                return emo
        return ""

    def _save_trade(self):
        try:
            entry = float(self._entry.text())
        except ValueError:
            return

        exit_v = float(self._exit.text()) if self._exit.text().strip() else None
        try:
            size = max(1, int(self._size.text() or 1))
        except ValueError:
            size = 1

        direction = "LONG" if self._dir_combo.currentText() == "LONG" else "SHORT"
        dt_str = self._datetime_inp.text() or datetime.now().strftime("%Y-%m-%d %H:%M")
        stop_v = float(self._stop.text()) if self._stop.text().strip() else None
        target_v = float(self._target.text()) if self._target.text().strip() else None

        pnl = r_multiple = None
        if exit_v:
            points = (exit_v - entry) if direction == "LONG" else (entry - exit_v)
            pnl = points * 5.0 * size - 0.62 * size
            if stop_v:
                risk = abs(entry - stop_v)
                if risk > 0:
                    r_multiple = points / risk

        emotion = self._get_selected_emotion()
        full_mode = self._full_widget.isVisible()
        setup_type = self._setup_combo.currentText() if full_mode else ""
        notes_text = self._notes.toPlainText() if full_mode else ""

        trade_dict = {
            "signal_id": None,
            "entry_time": dt_str,
            "exit_time": dt_str if exit_v else None,
            "direction": direction,
            "quantity": size,
            "entry_price": entry,
            "exit_price": exit_v,
            "pnl": pnl,
            "fees": 0.62 * size,
            "stop_price": stop_v,
            "target_price": target_v,
            "r_multiple": r_multiple,
            "hold_time_sec": None,
            "source": "manual",
            "notes": notes_text,
            "status": "closed" if exit_v else "open",
            "emotion": emotion,
            "tags": setup_type,
            "screenshot_path": self._screenshot_path,
            "ai_grade": None,
            "ai_analysis_json": None,
            "mae": None,
            "mfe": None,
        }

        if self._db:
            try:
                if self._edit_id:
                    self._db.update_trade(self._edit_id, {
                        k: v for k, v in trade_dict.items()
                        if k not in ("signal_id",)
                    })
                    trade_dict["id"] = self._edit_id
                else:
                    # Try enhanced insert first
                    try:
                        trade_id = self._db.insert_trade_enhanced(trade_dict)
                    except AttributeError:
                        # Fallback: basic insert + update extended fields
                        basic = {k: trade_dict[k] for k in [
                            "signal_id", "entry_time", "exit_time", "direction",
                            "quantity", "entry_price", "exit_price", "pnl", "fees",
                            "stop_price", "target_price", "r_multiple",
                            "hold_time_sec", "source", "notes", "status",
                        ]}
                        trade_id = self._db.insert_trade(basic)
                        try:
                            self._db.update_trade(trade_id, {
                                "emotion": emotion, "tags": setup_type,
                            })
                        except Exception:
                            pass
                    trade_dict["id"] = trade_id
            except Exception:
                pass

        self.trade_saved.emit(trade_dict)
        self._clear_form()

    def _clear_form(self):
        self._entry.clear()
        self._exit.clear()
        self._stop.clear()
        self._target.clear()
        self._size.setText("1")
        self._datetime_inp.setText(datetime.now().strftime("%Y-%m-%d %H:%M"))
        if hasattr(self, "_notes"):
            self._notes.clear()
        self._screenshot_path = None
        if hasattr(self, "_scr_label"):
            self._scr_label.setText("No screenshot attached")
        self._edit_id = None
        for b in self._emotion_btns.values():
            b.setChecked(False)
        self._pnl_display.setText("P&L: —")
        self._rr_display.setText("R:R — : —")

    def load_for_edit(self, trade: dict):
        self._edit_id = trade.get("id")
        self._entry.setText(str(trade.get("entry_price", "")))
        self._exit.setText(str(trade.get("exit_price") or ""))
        self._size.setText(str(trade.get("quantity", 1)))
        self._dir_combo.setCurrentText(trade.get("direction", "LONG"))
        self._stop.setText(str(trade.get("stop_price") or ""))
        self._target.setText(str(trade.get("target_price") or ""))
        self._datetime_inp.setText((trade.get("entry_time") or "")[:16])
        if hasattr(self, "_notes"):
            self._notes.setPlainText(trade.get("notes", "") or "")
        emo = trade.get("emotion", "")
        if emo in self._emotion_btns:
            self._emotion_btns[emo].setChecked(True)
        tags = trade.get("tags", "") or ""
        if tags in SETUP_TYPES and hasattr(self, "_setup_combo"):
            self._setup_combo.setCurrentText(tags)
        self._screenshot_path = trade.get("screenshot_path")
        if self._screenshot_path and hasattr(self, "_scr_label"):
            self._scr_label.setText(os.path.basename(self._screenshot_path))


# ─────────────────────────────────────────────────────────────────────────────
#  TradeRowWidget
# ─────────────────────────────────────────────────────────────────────────────

class TradeRowWidget(QFrame):
    """Single clickable trade row in the list."""

    clicked = Signal(dict)

    def __init__(self, trade: dict, parent=None):
        super().__init__(parent)
        self._trade = trade
        self._build()
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setObjectName("tradeRow")

    def _build(self):
        t = self._trade
        pnl = t.get("pnl")
        direction = t.get("direction", "LONG")
        entry = t.get("entry_price", 0)
        exit_p = t.get("exit_price") or 0
        grade_score = t.get("_grade_score")
        grade_letter = (_letter_grade(grade_score) if grade_score is not None
                        else t.get("ai_grade", "?"))
        tags = t.get("tags", "") or ""
        emotion = t.get("emotion", "") or ""
        entry_time = (t.get("entry_time") or "")[:16]

        if pnl is None or t.get("status") == "open":
            bg, border = COLORS["bg_panel"], COLORS["amber"]
        elif pnl > 0:
            bg, border = f"{COLORS['green_dim']}22", COLORS["green_bright"]
        elif pnl < 0:
            bg, border = f"{COLORS['red_dim']}22", COLORS["pink"]
        else:
            bg, border = f"{COLORS['amber_dim']}22", COLORS["amber"]

        self.setStyleSheet(
            f"QFrame#tradeRow {{ background: {bg}; border-left: 3px solid {border}; "
            f"border-bottom: 1px solid {COLORS['border']}; }}"
            f"QFrame#tradeRow:hover {{ background: {COLORS['bg_hover']}; }}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        lay.addWidget(_label(entry_time, 9, COLORS["text_muted"]))

        dir_sym = "▲" if direction == "LONG" else "▼"
        dir_color = COLORS["green_bright"] if direction == "LONG" else COLORS["pink"]
        dir_lbl = _label(f"{dir_sym} {direction}", 10, dir_color, bold=True)
        dir_lbl.setFixedWidth(60)
        lay.addWidget(dir_lbl)

        arrow = f"{entry:.2f}→{exit_p:.2f}" if exit_p else f"{entry:.2f}→OPEN"
        price_lbl = _label(arrow, 9, COLORS["cyan_mid"])
        price_lbl.setFixedWidth(105)
        lay.addWidget(price_lbl)

        if pnl is not None:
            sign = "+" if pnl > 0 else ""
            pnl_lbl = _label(f"{sign}${pnl:.2f}", 11, _pnl_color(pnl), bold=True)
        else:
            pnl_lbl = _label("OPEN", 11, COLORS["amber"], bold=True)
        pnl_lbl.setFixedWidth(72)
        lay.addWidget(pnl_lbl)

        if tags:
            lay.addWidget(_label(f"[{tags[:14]}]", 8, COLORS["magenta_mid"]))
        if emotion:
            emo_c = COLORS["orange"] if emotion in ("FOMO", "Revenge") else COLORS["cyan_dim"]
            lay.addWidget(_label(emotion, 8, emo_c))

        lay.addStretch()

        g_color = GRADE_COLORS.get(grade_letter, COLORS["text_muted"])
        grade_lbl = QLabel(f" {grade_letter} ")
        grade_lbl.setStyleSheet(
            f"font-family: {MONO}; font-size: 11px; font-weight: bold; "
            f"color: {g_color}; background: {g_color}22; "
            f"border: 1px solid {g_color}; padding: 0px 4px;"
        )
        lay.addWidget(grade_lbl)

    def mousePressEvent(self, event):
        self.clicked.emit(self._trade)
        super().mousePressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  TradeListView
# ─────────────────────────────────────────────────────────────────────────────

class TradeListView(QWidget):
    """Scrollable trade list with search, filter, sort."""

    trade_selected = Signal(dict)

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._all_trades: list[dict] = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # Search
        self._search = _input("🔍 search notes, tags, direction...")
        self._search.textChanged.connect(self._apply_filters)
        lay.addWidget(self._search)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        filter_row.addWidget(_label("FILTER:", 8, COLORS["text_muted"]))
        self._filter_result = _combo(["All", "Wins", "Losses", "Open"])
        self._filter_result.currentTextChanged.connect(self._apply_filters)
        filter_row.addWidget(self._filter_result)
        self._filter_grade = _combo(["All Grades", "A+", "A", "B", "C", "D", "F"])
        self._filter_grade.currentTextChanged.connect(self._apply_filters)
        filter_row.addWidget(self._filter_grade)
        self._filter_dir = _combo(["Both", "LONG", "SHORT"])
        self._filter_dir.currentTextChanged.connect(self._apply_filters)
        filter_row.addWidget(self._filter_dir)
        filter_row.addStretch()
        self._sort_combo = _combo(["Newest", "Best P&L", "Worst P&L", "Grade"])
        self._sort_combo.currentTextChanged.connect(self._apply_filters)
        filter_row.addWidget(self._sort_combo)
        lay.addLayout(filter_row)

        # Summary
        self._summary_lbl = _label("", 9, COLORS["cyan_mid"])
        lay.addWidget(self._summary_lbl)
        lay.addWidget(_sep())

        # Scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {COLORS['bg_dark']}; }}"
            f"QScrollBar:vertical {{ background: {COLORS['bg_dark']}; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: {COLORS['cyan_dim']}; }}"
        )
        self._list_widget = QWidget()
        self._list_lay = QVBoxLayout(self._list_widget)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)
        self._list_lay.addStretch()
        scroll.setWidget(self._list_widget)
        lay.addWidget(scroll, 1)

    def load_trades(self, trades: list[dict], grades: dict[int, dict] | None = None):
        self._all_trades = []
        for t in trades:
            t2 = dict(t)
            if grades and t2.get("id") in grades:
                g = grades[t2["id"]]
                t2["_grade_score"] = g.get("overall_grade")
                t2["ai_grade"] = _letter_grade(g.get("overall_grade", 0))
            self._all_trades.append(t2)
        self._apply_filters()

    def _apply_filters(self):
        query = self._search.text().lower()
        rf = self._filter_result.currentText()
        gf = self._filter_grade.currentText()
        df = self._filter_dir.currentText()
        sf = self._sort_combo.currentText()

        filtered = []
        for t in self._all_trades:
            pnl = t.get("pnl")
            tags = (t.get("tags") or "").lower()
            notes = (t.get("notes") or "").lower()
            direction = t.get("direction", "")

            if query and query not in tags and query not in notes and query not in direction.lower():
                continue
            if rf == "Wins" and (pnl is None or pnl <= 0):
                continue
            if rf == "Losses" and (pnl is None or pnl >= 0):
                continue
            if rf == "Open" and t.get("status") != "open":
                continue
            grade = t.get("ai_grade", "?")
            if gf != "All Grades" and grade != gf:
                continue
            if df != "Both" and direction != df:
                continue
            filtered.append(t)

        # Sort
        if sf == "Newest":
            filtered.sort(key=lambda t: t.get("entry_time", ""), reverse=True)
        elif sf == "Best P&L":
            filtered.sort(key=lambda t: t.get("pnl") or 0, reverse=True)
        elif sf == "Worst P&L":
            filtered.sort(key=lambda t: t.get("pnl") or 0)
        elif sf == "Grade":
            order = {"A+": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5, "?": 6}
            filtered.sort(key=lambda t: order.get(t.get("ai_grade", "?"), 6))

        self._render(filtered)

        wins = [t for t in filtered if (t.get("pnl") or 0) > 0]
        total_pnl = sum(t.get("pnl") or 0 for t in filtered)
        wr = len(wins) / len(filtered) * 100 if filtered else 0
        sign = "+" if total_pnl >= 0 else ""
        self._summary_lbl.setText(
            f"{len(filtered)} trades | {wr:.0f}% WR | {sign}${total_pnl:.2f}"
        )

    def _render(self, trades: list[dict]):
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not trades:
            e = _label("No trades yet — use ◆ LOG TRADE to record your first.", 10,
                       COLORS["text_muted"])
            e.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_lay.insertWidget(0, e)
            return

        for trade in trades:
            row = TradeRowWidget(trade)
            row.clicked.connect(self.trade_selected.emit)
            self._list_lay.insertWidget(self._list_lay.count() - 1, row)

    def add_trade(self, trade: dict):
        self._all_trades.insert(0, trade)
        self._apply_filters()


# ─────────────────────────────────────────────────────────────────────────────
#  GradeBar
# ─────────────────────────────────────────────────────────────────────────────

class GradeBar(QWidget):
    """Horizontal 0-10 score bar."""

    def __init__(self, label: str, value: float, parent=None):
        super().__init__(parent)
        self._label = label
        self._value = max(0.0, min(10.0, value))
        self.setFixedHeight(20)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.setFont(QFont("Courier New", 8))
        p.setPen(QColor(COLORS["text_muted"]))
        p.drawText(0, h - 4, self._label)

        bx, bw, bh = 90, w - 90 - 42, 8
        by = (h - bh) // 2

        p.fillRect(bx, by, bw, bh, QColor(COLORS["bg_input"]))

        fill = int(bw * self._value / 10)
        fc = (QColor(COLORS["green_bright"]) if self._value >= 7
              else QColor(COLORS["amber"]) if self._value >= 5
              else QColor(COLORS["pink"]))
        fc.setAlphaF(0.8)
        p.fillRect(bx, by, fill, bh, fc)

        p.setPen(QColor(COLORS["text_white"]))
        p.drawText(w - 40, h - 4, f"{self._value:.1f}/10")
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  TradeDetailPanel
# ─────────────────────────────────────────────────────────────────────────────

class TradeDetailPanel(QWidget):
    """Full detail view for a selected trade with AI analysis."""

    edit_requested = Signal(dict)

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._trade: dict | None = None
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)

        self._empty_lbl = _label(
            "← Select a trade from the list to view AI analysis and details",
            11, COLORS["text_muted"]
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._empty_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {COLORS['bg_dark']}; }}"
            f"QScrollBar:vertical {{ background: {COLORS['bg_dark']}; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: {COLORS['cyan_dim']}; }}"
        )
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(4, 4, 4, 4)
        self._content_lay.setSpacing(8)
        self._scroll.setWidget(self._content)
        lay.addWidget(self._scroll, 1)
        self._scroll.hide()

    def load_trade(self, trade: dict):
        self._trade = trade
        self._empty_lbl.hide()
        self._scroll.show()
        self._rebuild_content()

    def _rebuild_content(self):
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        t = self._trade
        pnl = t.get("pnl")
        direction = t.get("direction", "LONG")
        entry = t.get("entry_price", 0)
        exit_p = t.get("exit_price")
        stop = t.get("stop_price")
        target = t.get("target_price")
        r = t.get("r_multiple")
        hold_sec = t.get("hold_time_sec")
        emotion = t.get("emotion", "") or ""
        tags = t.get("tags", "") or ""
        notes = t.get("notes", "") or ""

        # Header
        hdr_frame, hdr_lay = _panel()
        hdr_row = QHBoxLayout()
        dir_sym = "▲" if direction == "LONG" else "▼"
        dir_color = COLORS["green_bright"] if direction == "LONG" else COLORS["pink"]
        hdr_row.addWidget(_label(f"{dir_sym} {direction}", 16, dir_color, bold=True))
        hdr_row.addWidget(_label(f"#{t.get('id', '?')}", 11, COLORS["text_muted"]))
        hdr_row.addWidget(_label((t.get("entry_time") or "")[:16], 11, COLORS["cyan_mid"]))
        hdr_row.addStretch()
        if pnl is not None:
            sign = "+" if pnl > 0 else ""
            hdr_row.addWidget(_label(f"{sign}${pnl:.2f}", 18, _pnl_color(pnl), bold=True))
        edit_btn = _btn("✎ EDIT", COLORS["amber"])
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(t))
        hdr_row.addWidget(edit_btn)
        hdr_lay.addLayout(hdr_row)
        self._content_lay.addWidget(hdr_frame)

        # Stats
        stats_frame, stats_lay = _panel("◈ TRADE STATS")
        sg = QGridLayout()
        sg.setHorizontalSpacing(16)
        sg.setVerticalSpacing(3)

        rr_str = "—"
        if stop and target and entry and abs(entry - stop) > 0:
            rr_str = f"1 : {abs(target - entry) / abs(entry - stop):.1f}"

        cells = [
            ("ENTRY",      f"{entry:.2f}",                   COLORS["cyan"]),
            ("EXIT",       f"{exit_p:.2f}" if exit_p else "OPEN", COLORS["cyan"]),
            ("SIZE",       f"{t.get('quantity', 1)} contracts", COLORS["text_white"]),
            ("STOP",       f"{stop:.2f}" if stop else "—",   COLORS["pink"]),
            ("TARGET",     f"{target:.2f}" if target else "—", COLORS["green_bright"]),
            ("R:R",        rr_str,                            COLORS["amber"]),
            ("R-MULTIPLE", f"{r:.2f}R" if r is not None else "—",
             COLORS["green_bright"] if (r or 0) > 0 else COLORS["pink"]),
            ("HOLD TIME",  f"{hold_sec/60:.0f}m" if hold_sec else "—", COLORS["text_white"]),
            ("FEES",       f"${t.get('fees', 0):.2f}",       COLORS["text_muted"]),
            ("STATUS",     (t.get("status") or "").upper(),  COLORS["amber"]),
        ]
        for i, (lbl, val, col) in enumerate(cells):
            row, colnum = divmod(i, 5)
            sg.addWidget(_label(lbl, 8, COLORS["text_muted"]), row * 2, colnum)
            sg.addWidget(_label(val, 12, col, bold=True), row * 2 + 1, colnum)

        stats_lay.addLayout(sg)
        self._content_lay.addWidget(stats_frame)

        # Mini chart
        if exit_p:
            chart_frame, chart_lay = _panel("◈ PRICE ACTION (SYNTHETIC SIMULATION)")
            mini = PriceActionMiniChart()
            mini.load_trade(t)
            chart_lay.addWidget(mini)
            chart_lay.addWidget(_label(
                "* Synthetic path for visual context. Use ATAS for actual replay.",
                8, COLORS["text_muted"]
            ))
            self._content_lay.addWidget(chart_frame)

        # AI Analysis
        ai_frame, ai_lay = _panel("▸▸ AI POST-TRADE ANALYSIS")
        ai_lay.setSpacing(8)

        # Fetch grade
        grade_dict = None
        if self._db:
            try:
                gs = self._db.get_trade_grades(t.get("id"))
                if gs:
                    grade_dict = gs[-1]
            except Exception:
                pass

        # Similar trades
        similar: list[dict] = []
        if self._db and tags:
            try:
                all_t = self._db.get_trades(limit=200)
                for ot in all_t:
                    if ot.get("id") == t.get("id"):
                        continue
                    if ot.get("direction") == t.get("direction") and ot.get("tags") == tags:
                        similar.append(ot)
                similar = similar[:10]
            except Exception:
                pass

        analysis = _ai_engine.analyze(t, grade_dict, similar or None)

        # Grade badge + breakdown
        grade = analysis["grade"]
        g_color = GRADE_COLORS.get(grade, COLORS["text_muted"])
        grade_row = QHBoxLayout()

        grade_badge = QLabel(grade)
        grade_badge.setStyleSheet(
            f"font-family: {MONO}; font-size: 40px; font-weight: bold; color: {g_color}; "
            f"background: {g_color}11; border: 2px solid {g_color}; "
            f"padding: 6px 20px; min-width: 70px;"
        )
        grade_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grade_row.addWidget(grade_badge)

        bars_col = QVBoxLayout()
        bars_col.setSpacing(2)
        for lbl, val in analysis.get("grade_breakdown", {}).items():
            bars_col.addWidget(GradeBar(lbl, val))
        grade_row.addLayout(bars_col, 1)
        ai_lay.addLayout(grade_row)

        ai_lay.addWidget(_sep())

        # What you did well
        ai_lay.addWidget(_label("✓ WHAT YOU DID WELL", 9, COLORS["green_bright"], bold=True))
        for pt in analysis["well"]:
            lbl = _label(f"  • {pt}", 10, COLORS["text_white"])
            lbl.setWordWrap(True)
            ai_lay.addWidget(lbl)

        ai_lay.addWidget(_sep())

        # What to improve
        ai_lay.addWidget(_label("⚠ WHAT TO IMPROVE", 9, COLORS["orange"], bold=True))
        for pt in analysis["improve"]:
            lbl = _label(f"  • {pt}", 10, COLORS["text_white"])
            lbl.setWordWrap(True)
            ai_lay.addWidget(lbl)

        ai_lay.addWidget(_sep())

        # Optimal entry/exit
        ai_lay.addWidget(_label("◈ OPTIMAL ENTRY", 9, COLORS["cyan"], bold=True))
        oe = _label(f"  {analysis['optimal_entry']}", 10, COLORS["text_white"])
        oe.setWordWrap(True)
        ai_lay.addWidget(oe)

        ai_lay.addWidget(_label("◈ OPTIMAL EXIT", 9, COLORS["cyan"], bold=True))
        ox = _label(f"  {analysis['optimal_exit']}", 10, COLORS["text_white"])
        ox.setWordWrap(True)
        ai_lay.addWidget(ox)

        # Pattern match
        if analysis.get("pattern_msg"):
            ai_lay.addWidget(_sep())
            ai_lay.addWidget(_label("◈ PATTERN RECOGNITION", 9, COLORS["magenta"], bold=True))
            pm = _label(f"  {analysis['pattern_msg']}", 10, COLORS["text_white"])
            pm.setWordWrap(True)
            ai_lay.addWidget(pm)

        self._content_lay.addWidget(ai_frame)

        # Notes/context
        if notes or tags or emotion:
            nf, nl = _panel("◆ NOTES & CONTEXT")
            if emotion:
                ec = COLORS["orange"] if emotion in ("FOMO", "Revenge") else COLORS["cyan_mid"]
                nl.addWidget(_label(f"Emotion: {emotion}", 10, ec, bold=True))
            if tags:
                nl.addWidget(_label(f"Setup: {tags}", 10, COLORS["magenta"]))
            if notes:
                nl.addWidget(_label("Notes:", 9, COLORS["text_muted"]))
                n_lbl = _label(notes, 10, COLORS["text_white"])
                n_lbl.setWordWrap(True)
                nl.addWidget(n_lbl)
            self._content_lay.addWidget(nf)

        # Screenshot
        scr = t.get("screenshot_path")
        if scr and os.path.exists(scr):
            sf2, sl2 = _panel("◈ CHART SCREENSHOT")
            pix = QPixmap(scr)
            if not pix.isNull():
                pix = pix.scaledToWidth(400, Qt.TransformationMode.SmoothTransformation)
                img_lbl = QLabel()
                img_lbl.setPixmap(pix)
                sl2.addWidget(img_lbl)
            self._content_lay.addWidget(sf2)

        self._content_lay.addStretch()


# ─────────────────────────────────────────────────────────────────────────────
#  HeatmapWidget
# ─────────────────────────────────────────────────────────────────────────────

class HeatmapWidget(QWidget):
    """Horizontal heatmap bar — P&L intensity per bucket."""

    def __init__(self, labels: list[str], values: list[float],
                 title: str = "", parent=None):
        super().__init__(parent)
        self._labels = labels
        self._values = values
        self._title = title
        self.setMinimumHeight(72)
        self.setStyleSheet(f"background: {COLORS['bg_dark']};")

    def update_data(self, labels: list[str], values: list[float]):
        self._labels = labels
        self._values = values
        self.update()

    def paintEvent(self, event):
        if not self._labels or not self._values:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._labels)
        pad = 4
        title_h = 14 if self._title else 0
        label_h = 18
        cell_w = max(28, (w - pad * 2) // max(n, 1))
        cell_h = h - pad * 2 - title_h - label_h

        if self._title:
            p.setFont(QFont("Courier New", 8))
            p.setPen(QColor(COLORS["text_muted"]))
            p.drawText(pad, pad + 10, self._title)

        max_v = max((abs(v) for v in self._values), default=1) or 1

        for i, (label, val) in enumerate(zip(self._labels, self._values)):
            x = pad + i * cell_w
            y = pad + title_h

            if val > 0:
                c = QColor(COLORS["green_bright"])
                c.setAlphaF(0.15 + min(val / max_v, 1.0) * 0.65)
            elif val < 0:
                c = QColor(COLORS["pink"])
                c.setAlphaF(0.15 + min(abs(val) / max_v, 1.0) * 0.65)
            else:
                c = QColor(COLORS["bg_input"])

            p.fillRect(int(x + 1), int(y), cell_w - 2, int(cell_h), QBrush(c))

            p.setFont(QFont("Courier New", 7))
            p.setPen(QColor(COLORS["text_white"]))
            vs = f"+${val:.0f}" if val >= 0 else f"${val:.0f}"
            p.drawText(int(x + 2), int(y + cell_h * 0.65), vs)

            p.setPen(QColor(COLORS["text_muted"]))
            p.drawText(int(x + 2), int(y + cell_h + label_h - 2), label[:5])

        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  CalendarWidget
# ─────────────────────────────────────────────────────────────────────────────

class CalendarWidget(QWidget):
    """Monthly P&L calendar — color-coded daily cells."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict[str, float] = {}
        self._month = datetime.now()
        self.setMinimumHeight(165)
        self.setStyleSheet(f"background: {COLORS['bg_dark']};")

    def update_data(self, daily_pnl: dict[str, float]):
        self._data = daily_pnl
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        days = ["M", "T", "W", "T", "F", "S", "S"]
        cell_w = w // 7
        hdr_h = 16

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        for i, d in enumerate(days):
            p.setPen(QColor(COLORS["text_muted"]))
            p.drawText(i * cell_w + cell_w // 2 - 4, hdr_h - 2, d)

        first = self._month.replace(day=1)
        start_dow = first.weekday()
        days_in_month = 28
        for d in range(28, 32):
            try:
                first.replace(day=d)
                days_in_month = d
            except ValueError:
                break

        cell_h = (h - hdr_h) // 6
        day_num = 1
        for week in range(6):
            for dow in range(7):
                if week * 7 + dow < start_dow or day_num > days_in_month:
                    continue
                date_str = f"{self._month.year}-{self._month.month:02d}-{day_num:02d}"
                pnl = self._data.get(date_str)
                x, y = dow * cell_w + 1, hdr_h + week * cell_h + 1

                if pnl is not None:
                    c = QColor(COLORS["green_bright"] if pnl > 0 else COLORS["pink"])
                    c.setAlphaF(0.25 + min(abs(pnl) / 500, 0.5))
                else:
                    c = QColor(COLORS["bg_panel"])
                p.fillRect(int(x), int(y), cell_w - 2, cell_h - 2, QBrush(c))

                p.setFont(QFont("Courier New", 8))
                p.setPen(QColor(COLORS["text_muted"]))
                p.drawText(int(x + 2), int(y + 11), str(day_num))

                if pnl is not None:
                    p.setFont(QFont("Courier New", 7))
                    p.setPen(QColor(COLORS["green_bright"] if pnl > 0 else COLORS["pink"]))
                    s = f"+{pnl:.0f}" if pnl > 0 else f"{pnl:.0f}"
                    p.drawText(int(x + 2), int(y + cell_h - 3), s)

                day_num += 1
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  StatBox
# ─────────────────────────────────────────────────────────────────────────────

class StatBox(QFrame):
    """Neon glowing KPI box."""

    def __init__(self, label: str, value: str = "—",
                 color: str = COLORS["cyan"], parent=None):
        super().__init__(parent)
        self.setObjectName("statBox")
        self._color = color
        self.setStyleSheet(
            f"QFrame#statBox {{ background: {COLORS['bg_panel']}; "
            f"border: 1px solid {color}44; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(2)
        self._lbl = _label(label, 8, COLORS["text_muted"], spacing=1)
        self._val = _label(value, 16, color, bold=True, spacing=0)
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)

    def set_value(self, value: str, color: str | None = None):
        self._val.setText(value)
        c = color or self._color
        self._val.setStyleSheet(
            f"font-family: {MONO}; font-size: 16px; font-weight: bold; "
            f"color: {c}; letter-spacing: 0px; background: transparent;"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  JournalDashboard
# ─────────────────────────────────────────────────────────────────────────────

class JournalDashboard(QWidget):
    """Full performance analytics dashboard."""

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)

        # KPI row 1
        kpi1 = QHBoxLayout()
        kpi1.setSpacing(4)
        self._sb_wr     = StatBox("WIN RATE", "—%",   COLORS["green_bright"])
        self._sb_pf     = StatBox("PROFIT FACTOR", "—", COLORS["cyan"])
        self._sb_pnl    = StatBox("TOTAL P&L", "$—",  COLORS["cyan"])
        self._sb_trades = StatBox("TOTAL TRADES", "—", COLORS["text_white"])
        self._sb_sharpe = StatBox("SHARPE", "—",      COLORS["amber"])
        self._sb_maxdd  = StatBox("MAX DRAWDOWN", "$—", COLORS["orange"])
        for sb in [self._sb_wr, self._sb_pf, self._sb_pnl,
                   self._sb_trades, self._sb_sharpe, self._sb_maxdd]:
            kpi1.addWidget(sb, 1)
        lay.addLayout(kpi1)

        # KPI row 2
        kpi2 = QHBoxLayout()
        kpi2.setSpacing(4)
        self._sb_avg_w   = StatBox("AVG WIN", "$—",    COLORS["green_bright"])
        self._sb_avg_l   = StatBox("AVG LOSS", "$—",   COLORS["pink"])
        self._sb_best    = StatBox("BEST TRADE", "$—", COLORS["green_bright"])
        self._sb_worst   = StatBox("WORST TRADE", "$—", COLORS["pink"])
        self._sb_wstreak = StatBox("WIN STREAK", "—",  COLORS["green_bright"])
        self._sb_lstreak = StatBox("LOSS STREAK", "—", COLORS["pink"])
        self._sb_exp     = StatBox("EXPECTANCY", "$—", COLORS["amber"])
        for sb in [self._sb_avg_w, self._sb_avg_l, self._sb_best, self._sb_worst,
                   self._sb_wstreak, self._sb_lstreak, self._sb_exp]:
            kpi2.addWidget(sb, 1)
        lay.addLayout(kpi2)

        lay.addWidget(_sep())

        # Charts row
        charts_split = QSplitter(Qt.Orientation.Horizontal)
        eq_frame, eq_lay = _panel("◈ EQUITY CURVE")
        self._equity_chart = NeonLineChart(
            title="", line_color=COLORS["cyan"],
        )
        self._equity_chart.setMinimumHeight(155)
        eq_lay.addWidget(self._equity_chart, 1)
        charts_split.addWidget(eq_frame)

        cal_frame, cal_lay = _panel("◆ MONTHLY P&L CALENDAR")
        self._calendar = CalendarWidget()
        cal_lay.addWidget(self._calendar, 1)
        charts_split.addWidget(cal_frame)
        charts_split.setSizes([620, 320])
        lay.addWidget(charts_split)

        # Heatmaps row
        heat_row = QHBoxLayout()
        dow_frame, dow_lay = _panel("◈ P&L BY DAY OF WEEK")
        self._dow_heat = HeatmapWidget(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                                       [0] * 7)
        dow_lay.addWidget(self._dow_heat)
        heat_row.addWidget(dow_frame, 1)

        tod_frame, tod_lay = _panel("◈ P&L BY TIME OF DAY")
        self._tod_heat = HeatmapWidget([f"{h:02d}h" for h in range(9, 17)], [0] * 8)
        tod_lay.addWidget(self._tod_heat)
        heat_row.addWidget(tod_frame, 2)
        lay.addLayout(heat_row)

        # Breakdown + emotion
        bd_row = QHBoxLayout()
        sf, sl = _panel("◈ P&L BY SETUP TYPE")
        self._setup_txt = QTextEdit()
        self._setup_txt.setReadOnly(True)
        self._setup_txt.setFixedHeight(95)
        self._setup_txt.setStyleSheet(
            f"background: {COLORS['bg_input']}; color: {COLORS['text_white']}; "
            f"border: none; font-family: {MONO}; font-size: 10px;"
        )
        sl.addWidget(self._setup_txt)
        bd_row.addWidget(sf, 1)

        ef, el = _panel("◈ P&L BY EMOTIONAL STATE")
        self._emo_txt = QTextEdit()
        self._emo_txt.setReadOnly(True)
        self._emo_txt.setFixedHeight(95)
        self._emo_txt.setStyleSheet(
            f"background: {COLORS['bg_input']}; color: {COLORS['text_white']}; "
            f"border: none; font-family: {MONO}; font-size: 10px;"
        )
        el.addWidget(self._emo_txt)
        bd_row.addWidget(ef, 1)
        lay.addLayout(bd_row)

        # AI Insights
        ai_f, ai_l = _panel("▸▸ META-AI INSIGHTS")
        self._insights_txt = QTextEdit()
        self._insights_txt.setReadOnly(True)
        self._insights_txt.setFixedHeight(88)
        self._insights_txt.setStyleSheet(
            f"background: {COLORS['bg_input']}; color: {COLORS['cyan']}; "
            f"border: none; font-family: {MONO}; font-size: 10px;"
        )
        self._insights_txt.setPlainText("◈ Log more trades to unlock AI insights...")
        ai_l.addWidget(self._insights_txt)
        lay.addWidget(ai_f)

    def refresh(self, trades: list[dict] | None = None):
        if trades is None and self._db:
            try:
                trades = self._db.get_trades(limit=500)
            except Exception:
                trades = []
        if not trades:
            return

        closed = [t for t in trades if t.get("status") == "closed"
                  and t.get("pnl") is not None]
        if not closed:
            return

        pnls = [t["pnl"] for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        n = len(pnls)
        total = sum(pnls)
        wr = len(wins) / n * 100 if n else 0
        avg_w = sum(wins) / len(wins) if wins else 0
        avg_l = sum(losses) / len(losses) if losses else 0
        pf = (sum(wins) / abs(sum(losses))) if losses and wins else float("inf")

        mean = total / n if n else 0
        std = (sum((p - mean) ** 2 for p in pnls) / n) ** 0.5 if n > 1 else 0
        sharpe = mean / max(std, 1e-9) * (252 ** 0.5) if std > 0 else 0

        # Max drawdown
        running, peak, max_dd = 0.0, 0.0, 0.0
        for t in sorted(closed, key=lambda x: x.get("entry_time", "")):
            running += t["pnl"]
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd

        # Streaks
        win_s = loss_s = cur_w = cur_l = 0
        for p in pnls:
            if p > 0:
                cur_w += 1; cur_l = 0
            else:
                cur_l += 1; cur_w = 0
            win_s = max(win_s, cur_w)
            loss_s = max(loss_s, cur_l)

        exp = wr / 100 * avg_w + (1 - wr / 100) * avg_l

        # Update KPIs
        self._sb_wr.set_value(f"{wr:.1f}%",
                              COLORS["green_bright"] if wr > 50 else COLORS["pink"])
        self._sb_pf.set_value(f"{pf:.2f}" if pf < 100 else "∞",
                              COLORS["green_bright"] if pf > 1.5 else COLORS["amber"])
        sign = "+" if total >= 0 else ""
        self._sb_pnl.set_value(f"{sign}${total:.2f}", _pnl_color(total))
        self._sb_trades.set_value(str(n))
        self._sb_sharpe.set_value(f"{sharpe:.2f}",
                                  COLORS["green_bright"] if sharpe > 1 else COLORS["amber"])
        self._sb_maxdd.set_value(f"-${max_dd:.2f}", COLORS["orange"])
        self._sb_avg_w.set_value(f"+${avg_w:.2f}", COLORS["green_bright"])
        self._sb_avg_l.set_value(f"${avg_l:.2f}", COLORS["pink"])
        self._sb_best.set_value(f"+${max(pnls):.2f}", COLORS["green_bright"])
        self._sb_worst.set_value(f"${min(pnls):.2f}", COLORS["pink"])
        self._sb_wstreak.set_value(str(win_s), COLORS["green_bright"])
        self._sb_lstreak.set_value(str(loss_s), COLORS["pink"])
        s2 = "+" if exp >= 0 else ""
        self._sb_exp.set_value(f"{s2}${exp:.2f}", _pnl_color(exp))

        # Equity curve
        sorted_t = sorted(closed, key=lambda x: x.get("entry_time", ""))
        cumul, running2 = [], 0.0
        for t in sorted_t:
            running2 += t["pnl"]
            cumul.append(running2)
        if cumul:
            self._equity_chart.set_data(list(range(len(cumul))), cumul)

        # Day of week heatmap
        dow_pnl = {d: 0.0 for d in range(7)}
        for t in closed:
            try:
                dow = datetime.fromisoformat(t["entry_time"]).weekday()
                dow_pnl[dow] += t["pnl"]
            except Exception:
                pass
        self._dow_heat.update_data(
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            [dow_pnl[d] for d in range(7)]
        )

        # Time of day heatmap
        tod_pnl: dict[int, float] = {}
        for t in closed:
            try:
                hr = datetime.fromisoformat(t["entry_time"]).hour
                tod_pnl[hr] = tod_pnl.get(hr, 0) + t["pnl"]
            except Exception:
                pass
        hours = list(range(9, 17))
        self._tod_heat.update_data(
            [f"{h:02d}h" for h in hours],
            [tod_pnl.get(h, 0) for h in hours]
        )

        # Calendar
        daily: dict[str, float] = {}
        for t in closed:
            day = (t.get("entry_time") or "")[:10]
            if day:
                daily[day] = daily.get(day, 0) + t["pnl"]
        self._calendar.update_data(daily)

        # Setup breakdown
        setup_pnl: dict[str, list[float]] = {}
        for t in closed:
            tag = (t.get("tags") or "Unknown")[:20]
            setup_pnl.setdefault(tag, []).append(t["pnl"])
        lines = []
        for tag, ps in sorted(setup_pnl.items(), key=lambda x: -sum(x[1])):
            tot = sum(ps)
            wr2 = len([p for p in ps if p > 0]) / len(ps) * 100
            s3 = "+" if tot >= 0 else ""
            lines.append(f"{tag:<22} {s3}${tot:>7.2f}  WR:{wr2:>4.0f}%  ({len(ps)})")
        self._setup_txt.setPlainText("\n".join(lines) or "No setup tags yet.")

        # Emotion breakdown
        emo_pnl: dict[str, list[float]] = {}
        for t in closed:
            emo = (t.get("emotion") or "Unknown")
            emo_pnl.setdefault(emo, []).append(t["pnl"])
        eml = []
        for emo, ps in sorted(emo_pnl.items(), key=lambda x: -sum(x[1])):
            tot = sum(ps)
            wr3 = len([p for p in ps if p > 0]) / len(ps) * 100
            s4 = "+" if tot >= 0 else ""
            eml.append(f"{emo:<14} {s4}${tot:>7.2f}  WR:{wr3:>4.0f}%  ({len(ps)})")
        self._emo_txt.setPlainText("\n".join(eml) or "No emotion data yet.")

        # AI Insights
        self._insights_txt.setPlainText(
            self._gen_insights(closed, wr, avg_w, avg_l, pf, setup_pnl, emo_pnl)
        )

    def _gen_insights(self, closed, wr, avg_w, avg_l, pf,
                      setup_pnl, emo_pnl) -> str:
        i = []
        if wr > 65:
            i.append(f"◈ WIN RATE {wr:.1f}% — Excellent selectivity. Your setups have real edge.")
        elif wr > 50:
            i.append(f"◈ WIN RATE {wr:.1f}% — Above breakeven. Focus on growing avg winner size.")
        else:
            i.append(f"◈ WIN RATE {wr:.1f}% — Below 50%. Tighten entry criteria. Quality > quantity.")

        if avg_l and avg_w / abs(avg_l) >= 1.5:
            i.append(f"◈ WIN/LOSS RATIO {avg_w/abs(avg_l):.2f}x — Outstanding. Let winners run.")
        elif avg_l:
            i.append(f"◈ WIN/LOSS RATIO {avg_w/abs(avg_l):.2f}x — Improve by holding winners longer.")

        if setup_pnl:
            best = max(setup_pnl.items(), key=lambda x: sum(x[1]))
            i.append(f"◈ BEST SETUP: '{best[0]}' — +${sum(best[1]):.0f} total. Stack this edge.")

        for bad in ("FOMO", "Revenge"):
            if bad in emo_pnl and sum(emo_pnl[bad]) < 0:
                i.append(f"⚠ {bad} costs you ${abs(sum(emo_pnl[bad])):.0f} — eliminate these entries.")

        if pf > 2:
            i.append(f"◈ PROFIT FACTOR {pf:.2f} — Elite edge. Protect it by staying selective.")
        elif pf < 1:
            i.append(f"⚠ PROFIT FACTOR {pf:.2f} — No statistical edge yet. Review criteria.")

        return "\n".join(i) if i else "Log more trades for pattern analysis."


# ─────────────────────────────────────────────────────────────────────────────
#  EnhancedJournalTab
# ─────────────────────────────────────────────────────────────────────────────

class EnhancedJournalTab(QWidget):
    """Main Tradezella-style journal tab — LEFT list / RIGHT tabs.

    Toolbar (above trade list):
      [Import Trades]  [Sync AMP]  [Auto-Sync ●]   <status label>
    """

    def __init__(self, db=None, bus=None, config=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._bus = bus
        self._config = config
        self._auto_sync_manager: Optional[AutoSyncManager] = None
        self._build()
        self._load_trades()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._load_trades)
        self._timer.start(30_000)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {COLORS['cyan_dim']}; }}"
        )

        # ── LEFT: Trade list + sync toolbar ──────────────────────
        left_frame, left_lay = _panel()

        # Title row
        title_row = QHBoxLayout()
        title_row.addWidget(_label("◆ TRADE LOG", 10, COLORS["cyan"], bold=True, spacing=3))
        title_row.addStretch()
        refresh_btn = _btn("↺", COLORS["cyan_mid"])
        refresh_btn.setFixedWidth(32)
        refresh_btn.clicked.connect(self._load_trades)
        title_row.addWidget(refresh_btn)
        left_lay.addLayout(title_row)

        # ── Sync toolbar ─────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.setContentsMargins(0, 4, 0, 4)

        # Import Trades button (CSV)
        self._import_btn = _btn("⬆ IMPORT CSV", COLORS["amber"], size=10)
        self._import_btn.setFixedHeight(26)
        self._import_btn.setToolTip("Import trade history from AMP CSV export")
        self._import_btn.clicked.connect(self._on_import_csv)
        toolbar.addWidget(self._import_btn)

        # Sync AMP button (live Rithmic)
        if RITHMIC_AVAILABLE:
            self._sync_btn = _btn("⟳ SYNC AMP", COLORS["cyan"], size=10)
            self._sync_btn.setToolTip("Pull live trade history from Rithmic/AMP account")
        else:
            self._sync_btn = _btn("⟳ SYNC AMP", COLORS["text_muted"], size=10)
            self._sync_btn.setToolTip("Requires Rithmic R|API (rapi package not installed)")
            self._sync_btn.setEnabled(False)
        self._sync_btn.setFixedHeight(26)
        self._sync_btn.clicked.connect(self._on_sync_amp)
        toolbar.addWidget(self._sync_btn)

        # Auto-Sync toggle
        self._autosync_btn = _btn("◉ AUTO", COLORS["text_muted"], size=10)
        self._autosync_btn.setFixedHeight(26)
        self._autosync_btn.setCheckable(True)
        self._autosync_btn.setChecked(False)
        if not RITHMIC_AVAILABLE:
            self._autosync_btn.setEnabled(False)
            self._autosync_btn.setToolTip("Requires Rithmic R|API")
        else:
            self._autosync_btn.setToolTip("Auto-sync every 30s from Rithmic")
        self._autosync_btn.toggled.connect(self._on_autosync_toggled)
        toolbar.addWidget(self._autosync_btn)

        toolbar.addStretch()

        # Progress / status label
        self._sync_status = _label("", 9, COLORS["text_muted"])
        self._sync_status.setWordWrap(False)
        toolbar.addWidget(self._sync_status)

        left_lay.addLayout(toolbar)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {COLORS['cyan_dim']}; background: {COLORS['cyan_dim']};")
        sep.setFixedHeight(1)
        left_lay.addWidget(sep)

        self._list_view = TradeListView(db=self._db)
        self._list_view.trade_selected.connect(self._on_trade_selected)
        left_lay.addWidget(self._list_view, 1)
        splitter.addWidget(left_frame)

        # ── RIGHT: Tabs ──────────────────────────────────────────
        right_frame = QFrame()
        right_frame.setStyleSheet(f"background: {COLORS['bg_dark']};")
        right_lay = QVBoxLayout(right_frame)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        self._right_tabs = QTabWidget()
        self._right_tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: none; background: {COLORS['bg_dark']}; }}"
            f"QTabBar::tab {{ background: {COLORS['bg_panel']}; color: {COLORS['text_muted']}; "
            f"  padding: 6px 18px; border: 1px solid {COLORS['cyan_dim']}; "
            f"  font-family: {MONO}; font-size: 10px; letter-spacing: 1px; }}"
            f"QTabBar::tab:selected {{ background: {COLORS['bg_hover']}; "
            f"  color: {COLORS['cyan']}; border-bottom: 2px solid {COLORS['cyan']}; }}"
        )

        # Tab 0: Log Trade
        self._entry_form = TradeEntryForm(db=self._db)
        self._entry_form.trade_saved.connect(self._on_trade_saved)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {COLORS['bg_dark']}; }}"
        )
        form_scroll.setWidget(self._entry_form)
        self._right_tabs.addTab(form_scroll, "◆ LOG TRADE")

        # Tab 1: Detail
        self._detail_panel = TradeDetailPanel(db=self._db)
        self._detail_panel.edit_requested.connect(self._on_edit_trade)
        self._right_tabs.addTab(self._detail_panel, "◈ DETAIL + AI")

        # Tab 2: Dashboard
        self._dashboard = JournalDashboard(db=self._db)
        dash_scroll = QScrollArea()
        dash_scroll.setWidgetResizable(True)
        dash_scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {COLORS['bg_dark']}; }}"
            f"QScrollBar:vertical {{ background: {COLORS['bg_dark']}; width: 8px; }}"
            f"QScrollBar::handle:vertical {{ background: {COLORS['cyan_dim']}; }}"
        )
        dash_scroll.setWidget(self._dashboard)
        self._right_tabs.addTab(dash_scroll, "◈ DASHBOARD")

        right_lay.addWidget(self._right_tabs, 1)
        splitter.addWidget(right_frame)
        splitter.setSizes([380, 820])
        lay.addWidget(splitter, 1)

    # ── Sync toolbar handlers ─────────────────────────────────────────────────

    def _set_status(self, msg: str, color: str = COLORS["text_muted"]):
        """Update sync status label (thread-safe via QTimer.singleShot)."""
        def _update():
            self._sync_status.setText(msg)
            self._sync_status.setStyleSheet(
                f"font-family: {MONO}; font-size: 9px; "
                f"color: {color}; background: transparent;"
            )
        # Must update UI on main thread
        QTimer.singleShot(0, _update)

    def _on_import_csv(self):
        """Open file dialog and import CSV in background thread."""
        if not _AMP_SYNC_AVAILABLE:
            self._set_status("amp_sync module not available", COLORS["pink"])
            return

        default_dir = ""
        if self._config:
            default_dir = self._config.amp_sync.csv_import_path

        path, _ = QFileDialog.getOpenFileName(
            self, "Import AMP Trade History", default_dir,
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return

        self._import_btn.setEnabled(False)
        self._set_status("Importing …", COLORS["amber"])

        def _run():
            try:
                count = import_from_csv(
                    path, self._db,
                    match_method=(
                        self._config.amp_sync.match_method
                        if self._config else "FIFO"
                    ),
                    on_progress=lambda msg: self._set_status(msg, COLORS["amber"]),
                )
                if count:
                    self._set_status(f"✓ Imported {count} trades", COLORS["green_bright"])
                    QTimer.singleShot(0, self._load_trades)
                else:
                    self._set_status("No new trades found", COLORS["text_muted"])
            except Exception as exc:
                self._set_status(f"Error: {exc}", COLORS["pink"])
            finally:
                QTimer.singleShot(0, lambda: self._import_btn.setEnabled(True))

        threading.Thread(target=_run, name="amp-csv-import", daemon=True).start()

    def _on_sync_amp(self):
        """Pull live fills from Rithmic in a background thread."""
        if not _AMP_SYNC_AVAILABLE or not RITHMIC_AVAILABLE:
            return

        self._sync_btn.setEnabled(False)
        self._set_status("Connecting to Rithmic …", COLORS["cyan"])

        def _run():
            try:
                count = rithmic_sync(
                    self._config, self._db,
                    days_back=30,
                    on_progress=lambda msg: self._set_status(msg, COLORS["cyan"]),
                )
                if count:
                    self._set_status(f"✓ Synced {count} trades", COLORS["green_bright"])
                    QTimer.singleShot(0, self._load_trades)
                else:
                    self._set_status("Up to date", COLORS["text_muted"])
            except Exception as exc:
                self._set_status(f"Sync error: {exc}", COLORS["pink"])
            finally:
                QTimer.singleShot(0, lambda: self._sync_btn.setEnabled(True))

        threading.Thread(target=_run, name="amp-rithmic-sync", daemon=True).start()

    def _on_autosync_toggled(self, checked: bool):
        """Start or stop the AutoSyncManager."""
        if not _AMP_SYNC_AVAILABLE:
            return

        if checked:
            interval = (
                self._config.amp_sync.auto_sync_interval
                if self._config else 30
            )
            self._auto_sync_manager = AutoSyncManager(
                config=self._config,
                db=self._db,
                interval_sec=interval,
                on_progress=lambda msg: self._set_status(msg, COLORS["cyan"]),
            )
            self._auto_sync_manager.start()
            self._autosync_btn.setStyleSheet(
                self._autosync_btn.styleSheet().replace(
                    COLORS["text_muted"], COLORS["green_bright"]
                )
            )
            self._autosync_btn.setText("◉ AUTO ON")
            self._set_status(f"Auto-sync every {interval}s", COLORS["green_bright"])
        else:
            if self._auto_sync_manager:
                self._auto_sync_manager.stop()
                self._auto_sync_manager = None
            self._autosync_btn.setText("◉ AUTO")
            self._set_status("Auto-sync off", COLORS["text_muted"])

    # ── Data ─────────────────────────────────────────────────────────────────

    def _load_trades(self):
        if not self._db:
            return
        try:
            trades = self._db.get_trades(limit=500)
            grades: dict[int, dict] = {}
            for t in trades:
                tid = t.get("id")
                if tid:
                    try:
                        gs = self._db.get_trade_grades(tid)
                        if gs:
                            grades[tid] = gs[-1]
                    except Exception:
                        pass
            self._list_view.load_trades(trades, grades)
            self._dashboard.refresh(trades)
        except Exception:
            pass

    def _on_trade_selected(self, trade: dict):
        self._detail_panel.load_trade(trade)
        self._right_tabs.setCurrentIndex(1)

    def _on_trade_saved(self, trade: dict):
        self._list_view.add_trade(trade)
        self._detail_panel.load_trade(trade)
        self._right_tabs.setCurrentIndex(1)
        self._load_trades()

    def _on_edit_trade(self, trade: dict):
        self._entry_form.load_for_edit(trade)
        self._right_tabs.setCurrentIndex(0)

    def refresh_from_event(self, data: dict | None = None):
        self._load_trades()
