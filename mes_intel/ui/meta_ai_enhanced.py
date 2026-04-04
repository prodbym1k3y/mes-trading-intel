"""Enhanced META-AI Dashboard — Phase 5.

Full intelligence dashboard for all 8 agents working together:
  - Agent status cards (name, status, confidence, accuracy, last lesson)
  - Team IQ score with sparkline trend
  - Agent accuracy leaderboard
  - Regime-aware performance breakdown
  - Recent team meeting summaries
  - Cross-agent agreement visualization
  - Learning history timeline
  - 80s cyberpunk neon aesthetic
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy, QGridLayout, QSplitter,
    QTextEdit, QTabWidget, QProgressBar,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QRect
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QLinearGradient, QPainterPath, QFontMetrics,
)

from .theme import COLORS

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = COLORS["bg_dark"]
BG_CARD   = COLORS["bg_card"]
BG_PANEL  = COLORS["bg_panel"]
CYAN      = COLORS["cyan"]
GREEN     = COLORS["green_bright"]
RED       = COLORS["red"]
AMBER     = COLORS["amber"]
MAGENTA   = COLORS["magenta"]
DIM       = COLORS["text_muted"]
WHITE     = COLORS["text_white"]
BORDER    = COLORS["border"]
CYAN_DIM  = COLORS["cyan_dim"]

AGENT_COLORS = {
    "SignalEngine":   "#00ffff",
    "ChartMonitor":   "#00ff88",
    "TradeJournal":   "#ff6600",
    "MetaLearner":    "#ff00ff",
    "NewsScanner":    "#ffff00",
    "DarkPool":       "#9933ff",
    "MarketBrain":    "#00ccff",
    "AppOptimizer":   "#ff99cc",
}

AGENT_ICONS = {
    "SignalEngine":   "◈",
    "ChartMonitor":   "▶",
    "TradeJournal":   "◆",
    "MetaLearner":    "▸",
    "NewsScanner":    "★",
    "DarkPool":       "⬡",
    "MarketBrain":    "◉",
    "AppOptimizer":   "⚙",
}

ALL_AGENTS = list(AGENT_COLORS.keys())


def _mono(size: int = 9, bold: bool = False) -> QFont:
    f = QFont("Courier New", size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    if bold:
        f.setBold(True)
    return f


def _qc(h: str) -> QColor:
    return QColor(h)


def _glow_pen(color: str, width: float = 1.5) -> QPen:
    p = QPen(_qc(color))
    p.setWidthF(width)
    return p


# ── Sparkline widget ───────────────────────────────────────────────────────────

class SparklineWidget(QWidget):
    """Tiny line chart for trend display."""

    def __init__(self, color: str = CYAN, height: int = 32, parent=None):
        super().__init__(parent)
        self._data: list[float] = []
        self._color = color
        self.setFixedHeight(height)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, data: list[float]):
        self._data = list(data)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(0, 0, w, h, _qc(BG_CARD))

        d = self._data
        if len(d) < 2:
            return

        mn, mx = min(d), max(d)
        span = max(mx - mn, 0.001)
        pad = 3

        def pt(i, v):
            x = pad + (i / (len(d) - 1)) * (w - 2 * pad)
            y = h - pad - ((v - mn) / span) * (h - 2 * pad)
            return QPointF(x, y)

        # Gradient fill
        path = QPainterPath()
        path.moveTo(QPointF(pad, h))
        for i, v in enumerate(d):
            path.lineTo(pt(i, v))
        path.lineTo(QPointF(w - pad, h))
        path.closeSubpath()

        grad = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        c = _qc(self._color)
        c.setAlpha(80)
        grad.setColorAt(0, c)
        c.setAlpha(0)
        grad.setColorAt(1, c)
        painter.fillPath(path, QBrush(grad))

        # Line
        for i in range(1, len(d)):
            pen = QPen(_qc(self._color), 1.5)
            painter.setPen(pen)
            painter.drawLine(pt(i - 1, d[i - 1]), pt(i, d[i]))

        # Glow line (semi-transparent wider)
        for i in range(1, len(d)):
            c2 = _qc(self._color)
            c2.setAlpha(40)
            pen2 = QPen(c2, 4)
            painter.setPen(pen2)
            painter.drawLine(pt(i - 1, d[i - 1]), pt(i, d[i]))

        painter.end()


# ── Accuracy ring widget ───────────────────────────────────────────────────────

class AccuracyRing(QWidget):
    """Circular arc showing accuracy %."""

    def __init__(self, size: int = 56, parent=None):
        super().__init__(parent)
        self._pct = 0.5
        self._color = GREEN
        self.setFixedSize(size, size)

    def set_value(self, pct: float, color: str = None):
        self._pct = max(0.0, min(1.0, pct))
        if color:
            self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin = 4
        rect = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)

        # Background arc
        painter.setPen(QPen(_qc(BORDER), 3))
        painter.drawArc(rect, 30 * 16, -300 * 16)

        # Value arc
        angle = int(-300 * self._pct)
        pen = QPen(_qc(self._color), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 30 * 16, angle * 16)

        # Glow arc
        c = _qc(self._color)
        c.setAlpha(30)
        pen2 = QPen(c, 7)
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen2)
        painter.drawArc(rect, 30 * 16, angle * 16)

        # Text
        painter.setPen(_qc(self._color))
        painter.setFont(_mono(8, bold=True))
        pct_str = f"{self._pct * 100:.0f}%"
        painter.drawText(rect.toRect(), Qt.AlignmentFlag.AlignCenter, pct_str)
        painter.end()


# ── Agent status card ──────────────────────────────────────────────────────────

class AgentCard(QFrame):
    """Single agent card — name, icon, status, accuracy ring, sparkline, last lesson."""

    def __init__(self, agent_name: str, parent=None):
        super().__init__(parent)
        self._agent = agent_name
        self._color = AGENT_COLORS.get(agent_name, CYAN)
        self._icon = AGENT_ICONS.get(agent_name, "◈")
        self._status = "IDLE"
        self._confidence = 0.5
        self._accuracy = 0.5
        self._lessons = 0
        self._last_lesson = ""

        self.setObjectName("panel")
        self.setMinimumWidth(200)
        self.setFixedHeight(120)
        self.setStyleSheet(
            f"QFrame#panel {{ background: {BG_CARD}; border: 1px solid {self._color}22; "
            f"border-left: 3px solid {self._color}; }}"
        )

        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Row 1: icon + name + status badge
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        icon_lbl = QLabel(self._icon)
        icon_lbl.setStyleSheet(f"color: {self._color}; font-size: 16px;")
        icon_lbl.setFixedWidth(20)
        row1.addWidget(icon_lbl)

        name_lbl = QLabel(self._agent.upper())
        name_lbl.setStyleSheet(
            f"color: {self._color}; font-family: 'Courier New'; font-size: 10px; "
            f"font-weight: bold; letter-spacing: 1px;"
        )
        row1.addWidget(name_lbl, 1)

        self._status_badge = QLabel("IDLE")
        self._status_badge.setStyleSheet(
            f"color: {DIM}; font-family: 'Courier New'; font-size: 8px; "
            f"border: 1px solid {DIM}44; padding: 1px 4px;"
        )
        row1.addWidget(self._status_badge)
        layout.addLayout(row1)

        # Row 2: accuracy ring + sparkline + confidence bar
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self._ring = AccuracyRing(size=50)
        self._ring.set_value(self._accuracy, self._color)
        row2.addWidget(self._ring)

        metrics_col = QVBoxLayout()
        metrics_col.setSpacing(2)

        conf_lbl = QLabel("CONFIDENCE")
        conf_lbl.setStyleSheet(f"color: {DIM}; font-size: 7px; font-family: 'Courier New';")
        metrics_col.addWidget(conf_lbl)

        self._conf_bar = QProgressBar()
        self._conf_bar.setRange(0, 100)
        self._conf_bar.setValue(50)
        self._conf_bar.setFixedHeight(8)
        self._conf_bar.setTextVisible(False)
        self._conf_bar.setStyleSheet(
            f"QProgressBar {{ background: {BG}; border: 1px solid {BORDER}; }} "
            f"QProgressBar::chunk {{ background: {self._color}; }}"
        )
        metrics_col.addWidget(self._conf_bar)

        self._sparkline = SparklineWidget(color=self._color, height=28)
        metrics_col.addWidget(self._sparkline)

        row2.addLayout(metrics_col, 1)
        layout.addLayout(row2)

        # Row 3: lessons + last lesson text
        row3 = QHBoxLayout()
        self._lessons_lbl = QLabel("0 LESSONS")
        self._lessons_lbl.setStyleSheet(
            f"color: {AMBER}; font-size: 8px; font-family: 'Courier New';"
        )
        row3.addWidget(self._lessons_lbl)
        row3.addStretch()
        self._last_lbl = QLabel("")
        self._last_lbl.setStyleSheet(
            f"color: {DIM}; font-size: 7px; font-family: 'Courier New';"
        )
        self._last_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        row3.addWidget(self._last_lbl)
        layout.addLayout(row3)

    def update_agent(self, data: dict):
        """Update card with new agent data dict."""
        status = data.get("status", "IDLE")
        confidence = data.get("confidence", 0.5)
        accuracy = data.get("accuracy", 0.5)
        lessons = data.get("lessons", 0)
        last_lesson = data.get("last_lesson", "")
        accuracy_history = data.get("accuracy_history", [accuracy])

        # Status badge color
        status_color = {
            "ACTIVE": GREEN, "LEARNING": AMBER,
            "ANALYZING": CYAN, "IDLE": DIM, "ERROR": RED,
        }.get(status.upper(), DIM)

        self._status_badge.setText(status.upper())
        self._status_badge.setStyleSheet(
            f"color: {status_color}; font-family: 'Courier New'; font-size: 8px; "
            f"border: 1px solid {status_color}66; padding: 1px 4px;"
        )

        self._ring.set_value(accuracy, self._color)
        self._conf_bar.setValue(int(confidence * 100))
        self._sparkline.set_data(accuracy_history)
        self._lessons_lbl.setText(f"{lessons} LESSONS")

        if last_lesson:
            # truncate
            txt = last_lesson[:40] + "…" if len(last_lesson) > 40 else last_lesson
            self._last_lbl.setText(txt)


# ── Team IQ display ────────────────────────────────────────────────────────────

class TeamIQWidget(QWidget):
    """Big Team IQ score + sparkline + trend."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._iq = 50.0
        self._history: list[float] = [50.0]
        self._trend = 0.0

        self.setFixedHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"background: {BG_CARD}; border: 1px solid {CYAN_DIM};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(20)

        # IQ number
        iq_col = QVBoxLayout()
        iq_col.setSpacing(2)

        iq_title = QLabel("TEAM IQ SCORE")
        iq_title.setStyleSheet(
            f"color: {DIM}; font-size: 8px; font-family: 'Courier New'; letter-spacing: 2px;"
        )
        iq_col.addWidget(iq_title)

        self._iq_label = QLabel("50.0")
        self._iq_label.setStyleSheet(
            f"color: {CYAN}; font-size: 36px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 3px;"
        )
        iq_col.addWidget(self._iq_label)

        layout.addLayout(iq_col)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {CYAN_DIM}; max-width: 1px;")
        layout.addWidget(sep)

        # Sparkline + trend
        spark_col = QVBoxLayout()
        spark_col.setSpacing(4)

        trend_row = QHBoxLayout()
        self._trend_lbl = QLabel("▲ +0.0")
        self._trend_lbl.setStyleSheet(
            f"color: {GREEN}; font-size: 10px; font-family: 'Courier New'; font-weight: bold;"
        )
        trend_row.addWidget(self._trend_lbl)
        trend_row.addStretch()
        self._trade_count_lbl = QLabel("0 TRADES")
        self._trade_count_lbl.setStyleSheet(
            f"color: {DIM}; font-size: 9px; font-family: 'Courier New';"
        )
        trend_row.addWidget(self._trade_count_lbl)
        spark_col.addLayout(trend_row)

        self._spark = SparklineWidget(color=CYAN, height=44)
        spark_col.addWidget(self._spark)

        layout.addLayout(spark_col, 1)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {CYAN_DIM}; max-width: 1px;")
        layout.addWidget(sep2)

        # Quick metrics
        metrics_col = QGridLayout()
        metrics_col.setSpacing(4)
        metrics_col.setContentsMargins(0, 0, 0, 0)

        self._metric_labels: dict[str, QLabel] = {}
        for i, (key, label) in enumerate([
            ("win_rate", "WIN RATE"),
            ("profit_factor", "P-FACTOR"),
            ("avg_reward", "AVG REWARD"),
            ("meetings", "MEETINGS"),
        ]):
            lbl_k = QLabel(label)
            lbl_k.setStyleSheet(
                f"color: {DIM}; font-size: 7px; font-family: 'Courier New';"
            )
            lbl_v = QLabel("--")
            lbl_v.setStyleSheet(
                f"color: {AMBER}; font-size: 11px; font-weight: bold; "
                f"font-family: 'Courier New';"
            )
            self._metric_labels[key] = lbl_v
            metrics_col.addWidget(lbl_k, i // 2, (i % 2) * 2)
            metrics_col.addWidget(lbl_v, i // 2, (i % 2) * 2 + 1)

        layout.addLayout(metrics_col)

    def update_data(self, iq: float, history: list[float],
                    trade_count: int = 0, metrics: dict = None):
        self._iq = iq
        self._history = history
        self._iq_label.setText(f"{iq:.1f}")

        # Color by score
        if iq >= 70:
            color = GREEN
        elif iq >= 50:
            color = AMBER
        else:
            color = RED
        self._iq_label.setStyleSheet(
            f"color: {color}; font-size: 36px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 3px;"
        )

        if len(history) >= 2:
            trend = history[-1] - history[-2]
            arrow = "▲" if trend >= 0 else "▼"
            tcol = GREEN if trend >= 0 else RED
            self._trend_lbl.setText(f"{arrow} {trend:+.1f}")
            self._trend_lbl.setStyleSheet(
                f"color: {tcol}; font-size: 10px; font-family: 'Courier New'; font-weight: bold;"
            )

        self._trade_count_lbl.setText(f"{trade_count} TRADES")
        self._spark.set_data(history)

        if metrics:
            for key, val in metrics.items():
                if key in self._metric_labels:
                    self._metric_labels[key].setText(str(val))


# ── Leaderboard widget ─────────────────────────────────────────────────────────

class LeaderboardWidget(QWidget):
    """Ranked list of agents by accuracy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self.setStyleSheet(f"background: {BG_PANEL};")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def update_data(self, rows: list[dict]):
        """rows: list of {name, accuracy, wins, losses, reward} sorted desc by accuracy."""
        self._rows = sorted(rows, key=lambda r: r.get("accuracy", 0), reverse=True)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _qc(BG_PANEL))

        if not self._rows:
            painter.setPen(_qc(DIM))
            painter.setFont(_mono(9))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                             "NO AGENT DATA")
            painter.end()
            return

        row_h = min(36, (h - 28) // max(len(self._rows), 1))
        header_y = 16

        # Header
        painter.setPen(_qc(DIM))
        painter.setFont(_mono(7))
        painter.drawText(4, header_y, "RANK  AGENT")
        painter.drawText(w - 160, header_y, "ACC    W    L  REWARD")

        y = header_y + 6
        # Separator
        painter.setPen(QPen(_qc(BORDER), 1))
        painter.drawLine(4, y, w - 4, y)
        y += 4

        medal = ["◉", "◈", "◆", "▸", "★", "⬡", "▶", "⚙"]

        for rank, row in enumerate(self._rows):
            name = row.get("name", "?")
            acc = row.get("accuracy", 0.5)
            wins = row.get("wins", 0)
            losses = row.get("losses", 0)
            reward = row.get("reward", 0.0)
            color = AGENT_COLORS.get(name, CYAN)

            # Row background
            if rank == 0:
                bg = _qc(color)
                bg.setAlpha(15)
                painter.fillRect(0, y, w, row_h, bg)

            # Medal + rank
            m = medal[rank] if rank < len(medal) else str(rank + 1)
            painter.setPen(_qc(color))
            painter.setFont(_mono(10, bold=(rank == 0)))
            painter.drawText(6, y + row_h - 10, f"{m} {rank + 1}")

            # Agent name
            painter.setPen(_qc(color if rank < 3 else WHITE))
            painter.setFont(_mono(9, bold=(rank == 0)))
            painter.drawText(42, y + row_h - 10, name.upper())

            # Accuracy bar
            bar_x = w - 250
            bar_w = 80
            bar_h = 6
            bar_y = y + (row_h - bar_h) // 2
            painter.setPen(QPen(_qc(BORDER), 1))
            painter.drawRect(bar_x, bar_y, bar_w, bar_h)
            fill_w = int(bar_w * acc)
            painter.fillRect(bar_x + 1, bar_y + 1, fill_w - 2, bar_h - 2,
                             _qc(color) if rank < 3 else _qc(DIM))

            # Stats text
            acc_str = f"{acc * 100:.0f}%"
            reward_col = GREEN if reward >= 0 else RED
            painter.setPen(_qc(WHITE))
            painter.setFont(_mono(8))
            painter.drawText(w - 160, y + row_h - 10,
                             f"{acc_str:>5}  {wins:>3}  {losses:>3}")
            painter.setPen(_qc(reward_col))
            painter.drawText(w - 55, y + row_h - 10, f"{reward:>+.2f}")

            y += row_h

        painter.end()


# ── Regime performance heatmap ─────────────────────────────────────────────────

class RegimeHeatmap(QWidget):
    """Grid showing which agents work best in which regimes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict[str, dict[str, float]] = {}
        self._agents: list[str] = ALL_AGENTS
        self._regimes: list[str] = ["trending_up", "trending_down", "ranging", "volatile", "unknown"]
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: {BG_PANEL};")

    def update_data(self, data: dict):
        """data: {agent_name: {regime: accuracy_float}}"""
        self._data = data
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _qc(BG_PANEL))

        if not self._data:
            painter.setPen(_qc(DIM))
            painter.setFont(_mono(9))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                             "NO REGIME DATA")
            painter.end()
            return

        agents = self._agents
        regimes = self._regimes
        n_a, n_r = len(agents), len(regimes)

        ml = 90   # left margin (agent names)
        mt = 24   # top margin (regime headers)
        mr = 8
        mb = 4

        cell_w = (w - ml - mr) / n_r
        cell_h = (h - mt - mb) / n_a

        # Regime headers
        painter.setPen(_qc(CYAN))
        painter.setFont(_mono(7))
        for ri, reg in enumerate(regimes):
            cx = ml + (ri + 0.5) * cell_w
            txt = reg.replace("_", " ").upper()
            fm = QFontMetrics(painter.font())
            tw = fm.horizontalAdvance(txt)
            painter.drawText(int(cx - tw / 2), mt - 4, txt)

        for ai, agent in enumerate(agents):
            color = AGENT_COLORS.get(agent, CYAN)
            cy = mt + ai * cell_h

            # Agent name
            painter.setPen(_qc(color))
            painter.setFont(_mono(8, bold=True))
            painter.drawText(2, int(cy + cell_h / 2 + 4), agent[:10])

            agent_data = self._data.get(agent, {})
            for ri, reg in enumerate(regimes):
                acc = agent_data.get(reg, 0.5)
                cx = ml + ri * cell_w

                # Cell color
                if acc > 0.65:
                    cell_col = _qc(GREEN)
                    cell_col.setAlpha(int((acc - 0.5) * 255 * 1.5))
                elif acc < 0.45:
                    cell_col = _qc(RED)
                    cell_col.setAlpha(int((0.5 - acc) * 255 * 1.5))
                else:
                    cell_col = _qc(AMBER)
                    cell_col.setAlpha(60)

                painter.fillRect(
                    int(cx + 1), int(cy + 1),
                    int(cell_w - 2), int(cell_h - 2),
                    cell_col
                )

                # Accuracy text
                painter.setPen(_qc(WHITE) if abs(acc - 0.5) > 0.1 else _qc(DIM))
                painter.setFont(_mono(7))
                txt = f"{acc * 100:.0f}%"
                fm = QFontMetrics(painter.font())
                tw = fm.horizontalAdvance(txt)
                painter.drawText(
                    int(cx + cell_w / 2 - tw / 2),
                    int(cy + cell_h / 2 + 4),
                    txt
                )

        painter.end()


# ── Cross-agent agreement chart ────────────────────────────────────────────────

class AgentAgreementWidget(QWidget):
    """Shows how often agent pairs agree/disagree."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._agreement: dict[str, float] = {}
        self._latest: dict[str, str] = {}  # agent → current signal direction
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: {BG_PANEL};")

    def update_data(self, agreement: dict, latest: dict):
        self._agreement = agreement
        self._latest = latest
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _qc(BG_PANEL))

        agents_with_signal = [a for a in ALL_AGENTS if a in self._latest]
        if not agents_with_signal:
            painter.setPen(_qc(DIM))
            painter.setFont(_mono(9))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                             "NO SIGNAL DATA")
            painter.end()
            return

        # Count directions
        direction_counts: dict[str, int] = defaultdict(int)
        for d in self._latest.values():
            direction_counts[d.upper()] += 1

        total = max(sum(direction_counts.values()), 1)
        n = len(agents_with_signal)
        bull = direction_counts.get("LONG", 0)
        bear = direction_counts.get("SHORT", 0)
        flat = direction_counts.get("FLAT", total - bull - bear)

        # Agreement bar at top
        bar_w = w - 20
        bar_h = 16
        bar_x = 10
        bar_y = 10

        painter.setPen(QPen(_qc(BORDER), 1))
        painter.drawRect(bar_x, bar_y, bar_w, bar_h)

        if total > 0:
            bull_w = int(bar_w * bull / total)
            bear_w = int(bar_w * bear / total)
            flat_w = bar_w - bull_w - bear_w

            painter.fillRect(bar_x, bar_y, bull_w, bar_h, _qc(GREEN))
            painter.fillRect(bar_x + bull_w, bar_y, bear_w, bar_h, _qc(RED))
            painter.fillRect(bar_x + bull_w + bear_w, bar_y, flat_w, bar_h, _qc(AMBER))

        painter.setPen(_qc(WHITE))
        painter.setFont(_mono(7))
        painter.drawText(bar_x + 4, bar_y + 11, f"LONG {bull}")
        painter.drawText(bar_x + int(bar_w * 0.4), bar_y + 11, f"SHORT {bear}")
        painter.drawText(bar_x + int(bar_w * 0.78), bar_y + 11, f"FLAT {flat}")

        # Per-agent direction dots
        dot_y = bar_y + bar_h + 16
        dot_size = min(32, (w - 20) // max(n, 1) - 4)

        for i, agent in enumerate(agents_with_signal):
            direction = self._latest.get(agent, "FLAT").upper()
            dot_x = 10 + i * (dot_size + 4)
            color = AGENT_COLORS.get(agent, CYAN)

            dot_col = {"LONG": GREEN, "SHORT": RED, "FLAT": AMBER}.get(direction, DIM)
            c = _qc(dot_col)
            c.setAlpha(180)
            painter.setBrush(QBrush(c))
            painter.setPen(QPen(_qc(dot_col), 1))
            painter.drawEllipse(dot_x, dot_y, dot_size, dot_size)

            # Agent icon
            painter.setPen(_qc(color))
            painter.setFont(_mono(9, bold=True))
            icon = AGENT_ICONS.get(agent, "?")
            fm = QFontMetrics(painter.font())
            tw = fm.horizontalAdvance(icon)
            painter.drawText(dot_x + dot_size // 2 - tw // 2,
                             dot_y + dot_size // 2 + 4, icon)

            # Direction text
            painter.setPen(_qc(dot_col))
            painter.setFont(_mono(6))
            painter.drawText(dot_x, dot_y + dot_size + 10,
                             direction[:4])

        painter.end()


# ── Learning timeline ──────────────────────────────────────────────────────────

class LearningTimeline(QWidget):
    """Scrollable timeline of learning events."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: list[dict] = []
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def update_events(self, events: list[dict]):
        """events: [{ts, agent, action, was_helpful, pre, post}] newest first."""
        self._events = events
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _qc(BG_PANEL))

        if not self._events:
            painter.setPen(_qc(DIM))
            painter.setFont(_mono(9))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                             "NO LEARNING EVENTS YET")
            painter.end()
            return

        row_h = 22
        visible = min(len(self._events), (h - 4) // row_h)

        # Vertical timeline line
        line_x = 30
        painter.setPen(QPen(_qc(CYAN_DIM), 1))
        painter.drawLine(line_x, 4, line_x, visible * row_h)

        for i, ev in enumerate(self._events[:visible]):
            y = 4 + i * row_h
            agent = ev.get("agent", "?")
            action = ev.get("action", "")
            helpful = ev.get("was_helpful")
            ts = ev.get("ts", 0)
            pre = ev.get("pre", 0.0)
            post = ev.get("post", 0.0)

            # Dot on timeline
            dot_col = GREEN if helpful is True else (RED if helpful is False else AMBER)
            dot_c = _qc(dot_col)
            painter.setBrush(QBrush(dot_c))
            painter.setPen(QPen(dot_c, 1))
            painter.drawEllipse(line_x - 4, y + 7, 8, 8)

            # Timestamp
            if ts:
                dt = datetime.fromtimestamp(ts)
                ts_str = dt.strftime("%H:%M")
            else:
                ts_str = "--:--"

            color = AGENT_COLORS.get(agent, CYAN)
            painter.setPen(_qc(DIM))
            painter.setFont(_mono(7))
            painter.drawText(line_x + 12, y + 15, ts_str)

            painter.setPen(_qc(color))
            painter.setFont(_mono(8, bold=True))
            painter.drawText(line_x + 44, y + 15, agent[:8].upper())

            painter.setPen(_qc(WHITE))
            painter.setFont(_mono(8))
            painter.drawText(line_x + 115, y + 15, action[:40])

            # Delta
            if pre != post:
                delta = post - pre
                d_col = GREEN if delta > 0 else RED
                painter.setPen(_qc(d_col))
                painter.drawText(w - 70, y + 15, f"{delta:+.3f}")

        painter.end()


# ── Team meeting summary ───────────────────────────────────────────────────────

class TeamMeetingPanel(QWidget):
    """Shows last N team meeting summaries."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._meetings: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._display = QTextEdit()
        self._display.setReadOnly(True)
        self._display.setStyleSheet(
            f"background: {BG}; color: {GREEN}; "
            f"font-family: 'Courier New', monospace; font-size: 10px; "
            f"border: none; selection-background-color: {CYAN_DIM};"
        )
        layout.addWidget(self._display)

    def update_meetings(self, meetings: list[dict]):
        self._meetings = meetings
        lines = []
        for m in reversed(meetings):
            ts = m.get("ts", 0)
            if ts:
                dt = datetime.fromtimestamp(ts)
                ts_str = dt.strftime("%Y-%m-%d %H:%M")
            else:
                ts_str = "UNKNOWN TIME"

            lines.append(f"══ TEAM MEETING @ {ts_str} ══")
            lines.append(m.get("summary", "No summary available."))
            lines.append(f"Trades since last meeting: {m.get('trades', '?')}")
            adjustments = m.get("adjustments", {})
            if adjustments:
                lines.append("Weight adjustments:")
                for name, (old, new) in adjustments.items():
                    arrow = "↑" if new > old else "↓"
                    lines.append(f"  {name}: {old:.3f} {arrow} {new:.3f}")
            lines.append("")

        if not lines:
            lines = ["No team meetings recorded yet.",
                     "Meetings occur every 50 trades."]

        self._display.setPlainText("\n".join(lines))

    def add_post_mortem(self, narrative: str):
        """Append a post-mortem narrative to the display."""
        current = self._display.toPlainText()
        self._display.setPlainText(narrative + "\n\n" + current)


# ── Main META-AI Dashboard ─────────────────────────────────────────────────────

class MetaAIDashboard(QWidget):
    """Full META-AI intelligence dashboard.

    Attach a MetaLearner instance with set_meta_learner() and it will
    auto-update every 5 seconds. Can also be updated manually via refresh().
    """

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._meta_learner = None

        self._build()
        self._start_timer()

    def set_meta_learner(self, meta_learner):
        self._meta_learner = meta_learner
        self.refresh()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        # ── Team IQ bar (always visible at top) ──────────────────────────────
        self.team_iq = TeamIQWidget()
        root.addWidget(self.team_iq)

        # ── Main splitter: left = agent cards + leaderboard, right = tabs ────
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.setStyleSheet("QSplitter::handle { background: #112233; width: 3px; }")

        # Left panel ─────────────────────────────────────────────────────────
        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.setContentsMargins(0, 0, 4, 0)
        left_l.setSpacing(6)

        # Agent cards grid
        cards_frame = QFrame()
        cards_frame.setObjectName("panel")
        cards_l = QVBoxLayout(cards_frame)
        cards_l.setContentsMargins(4, 4, 4, 4)
        cards_l.setSpacing(2)

        cards_hdr = QLabel("▸ AGENT STATUS")
        cards_hdr.setStyleSheet(
            f"color: {CYAN}; font-size: 10px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 2px;"
        )
        cards_l.addWidget(cards_hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"background: {BG}; border: none;")

        cards_inner = QWidget()
        cards_inner.setStyleSheet(f"background: {BG};")
        self._cards_grid = QGridLayout(cards_inner)
        self._cards_grid.setContentsMargins(0, 0, 0, 0)
        self._cards_grid.setSpacing(4)

        self._agent_cards: dict[str, AgentCard] = {}
        for i, agent in enumerate(ALL_AGENTS):
            card = AgentCard(agent)
            self._agent_cards[agent] = card
            self._cards_grid.addWidget(card, i // 2, i % 2)

        scroll.setWidget(cards_inner)
        cards_l.addWidget(scroll, 1)
        left_l.addWidget(cards_frame, 3)

        # Leaderboard
        lb_frame = QFrame()
        lb_frame.setObjectName("panel")
        lb_l = QVBoxLayout(lb_frame)
        lb_l.setContentsMargins(4, 4, 4, 4)
        lb_l.setSpacing(2)

        lb_hdr = QLabel("▸ ACCURACY LEADERBOARD")
        lb_hdr.setStyleSheet(
            f"color: {MAGENTA}; font-size: 10px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 2px;"
        )
        lb_l.addWidget(lb_hdr)
        self.leaderboard = LeaderboardWidget()
        lb_l.addWidget(self.leaderboard, 1)
        left_l.addWidget(lb_frame, 2)

        main_split.addWidget(left_w)

        # Right panel ─────────────────────────────────────────────────────────
        right_tabs = QTabWidget()
        right_tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {BORDER}; background: {BG}; }} "
            f"QTabBar::tab {{ background: {BG_CARD}; color: {DIM}; "
            f"  padding: 4px 10px; font-family: 'Courier New'; font-size: 9px; "
            f"  letter-spacing: 1px; border: 1px solid {BORDER}; }} "
            f"QTabBar::tab:selected {{ color: {CYAN}; border-bottom: 2px solid {CYAN}; }} "
        )

        # Tab: Regime Heatmap
        regime_w = QWidget()
        regime_l = QVBoxLayout(regime_w)
        regime_l.setContentsMargins(4, 4, 4, 4)
        regime_l.setSpacing(4)
        regime_hdr = QLabel("▸ REGIME PERFORMANCE MATRIX")
        regime_hdr.setStyleSheet(
            f"color: {AMBER}; font-size: 10px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 1px;"
        )
        regime_l.addWidget(regime_hdr)
        regime_l.addWidget(QLabel(
            "Green = strong | Yellow = neutral | Red = weak",
        ))
        self.regime_heatmap = RegimeHeatmap()
        regime_l.addWidget(self.regime_heatmap, 1)
        right_tabs.addTab(regime_w, "REGIMES")

        # Tab: Cross-Agent Agreement
        agree_w = QWidget()
        agree_l = QVBoxLayout(agree_w)
        agree_l.setContentsMargins(4, 4, 4, 4)
        agree_l.setSpacing(4)
        agree_hdr = QLabel("▸ CROSS-AGENT AGREEMENT")
        agree_hdr.setStyleSheet(
            f"color: {GREEN}; font-size: 10px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 1px;"
        )
        agree_l.addWidget(agree_hdr)
        self.agreement_widget = AgentAgreementWidget()
        agree_l.addWidget(self.agreement_widget, 1)
        right_tabs.addTab(agree_w, "AGREEMENT")

        # Tab: Learning Timeline
        timeline_w = QWidget()
        timeline_l = QVBoxLayout(timeline_w)
        timeline_l.setContentsMargins(4, 4, 4, 4)
        timeline_l.setSpacing(4)
        timeline_hdr = QLabel("▸ LEARNING HISTORY TIMELINE")
        timeline_hdr.setStyleSheet(
            f"color: {CYAN}; font-size: 10px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 1px;"
        )
        timeline_l.addWidget(timeline_hdr)
        self.timeline = LearningTimeline()
        timeline_l.addWidget(self.timeline, 1)
        right_tabs.addTab(timeline_w, "LEARNING")

        # Tab: Team Meetings / Post-Mortems
        pm_w = QWidget()
        pm_l = QVBoxLayout(pm_w)
        pm_l.setContentsMargins(4, 4, 4, 4)
        pm_l.setSpacing(4)
        pm_hdr = QLabel("▸ TEAM MEETINGS & POST-MORTEMS")
        pm_hdr.setStyleSheet(
            f"color: {MAGENTA}; font-size: 10px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 1px;"
        )
        pm_l.addWidget(pm_hdr)
        self.team_meeting_panel = TeamMeetingPanel()
        pm_l.addWidget(self.team_meeting_panel, 1)
        right_tabs.addTab(pm_w, "MEETINGS")

        # Tab: Brain — strategy weights, patterns, decay
        brain_w = QWidget()
        brain_l = QVBoxLayout(brain_w)
        brain_l.setContentsMargins(4, 4, 4, 4)
        brain_l.setSpacing(4)
        brain_hdr = QLabel("▸ STRATEGY BRAIN — LEARNING STATE")
        brain_hdr.setStyleSheet(
            f"color: {CYAN}; font-size: 10px; font-weight: bold; "
            f"font-family: 'Courier New'; letter-spacing: 1px;"
        )
        brain_l.addWidget(brain_hdr)

        # Pattern insight label
        self._pattern_label = QLabel("Pattern: loading...")
        self._pattern_label.setWordWrap(True)
        self._pattern_label.setStyleSheet(
            f"color: {AMBER}; font-size: 10px; font-family: 'Courier New'; "
            f"padding: 4px; background: {BG_CARD}; border: 1px solid {BORDER};"
        )
        brain_l.addWidget(self._pattern_label)

        # Strategy brain table (scroll area)
        brain_scroll = QScrollArea()
        brain_scroll.setWidgetResizable(True)
        brain_scroll.setStyleSheet(f"background: {BG}; border: none;")
        self._brain_inner = QWidget()
        self._brain_inner.setStyleSheet(f"background: {BG};")
        self._brain_layout = QVBoxLayout(self._brain_inner)
        self._brain_layout.setContentsMargins(2, 2, 2, 2)
        self._brain_layout.setSpacing(2)
        brain_scroll.setWidget(self._brain_inner)
        brain_l.addWidget(brain_scroll, 1)

        right_tabs.addTab(brain_w, "BRAIN")

        main_split.addWidget(right_tabs)
        main_split.setSizes([520, 560])
        root.addWidget(main_split, 1)

    def _start_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(5000)

    def refresh(self):
        """Pull latest data from meta_learner and db, update all widgets."""
        try:
            self._refresh_from_meta_learner()
        except Exception as exc:
            pass

        try:
            self._refresh_from_db()
        except Exception as exc:
            pass

    def _refresh_from_meta_learner(self):
        ml = self._meta_learner
        if ml is None:
            return

        # ── Team IQ ───────────────────────────────────────────────────────────
        perf_scores = list(ml.team_performance_scores) if hasattr(ml, "team_performance_scores") else []
        iq = (sum(perf_scores[-20:]) / len(perf_scores[-20:]) * 100) if perf_scores else 50.0
        iq_history = [s * 100 for s in perf_scores[-50:]] if perf_scores else [50.0]

        trade_count = ml.meta_metrics.get("total_post_mortems", 0)

        # Compute win rate + profit factor from trackers
        total_wins = sum(t.win_count for t in ml.trackers.values())
        total_losses = sum(t.loss_count for t in ml.trackers.values())
        total = total_wins + total_losses
        win_rate_str = f"{total_wins / total * 100:.0f}%" if total else "--"

        all_rewards = []
        for agent_rewards in ml.reward_history.values():
            all_rewards.extend(r.reward for r in agent_rewards[-20:])
        avg_reward = sum(all_rewards) / len(all_rewards) if all_rewards else 0.0

        meetings = ml._trade_count_since_meeting if hasattr(ml, "_trade_count_since_meeting") else 0

        self.team_iq.update_data(
            iq=iq,
            history=iq_history,
            trade_count=trade_count,
            metrics={
                "win_rate": win_rate_str,
                "profit_factor": f"{min(total_wins / max(total_losses, 1), 9.9):.1f}",
                "avg_reward": f"{avg_reward:+.3f}",
                "meetings": str(ml.meta_metrics.get("total_post_mortems", 0) // 50),
            }
        )

        # ── Agent cards ────────────────────────────────────────────────────────
        leaderboard_rows = []

        for agent in ALL_AGENTS:
            # Try to find relevant tracker
            tracker = None
            agent_lower = agent.lower().replace(" ", "_")
            for k, v in ml.trackers.items():
                if k.lower() == agent_lower or agent_lower in k.lower():
                    tracker = v
                    break

            # Aggregate all trackers if it's MetaLearner itself
            if agent == "MetaLearner":
                acc = float(ml.teaching_accuracy) if hasattr(ml, "teaching_accuracy") else 0.5
                wins = sum(t.win_count for t in ml.trackers.values())
                losses = sum(t.loss_count for t in ml.trackers.values())
                lessons = sum(ml.agent_lessons_learned.values()) if hasattr(ml, "agent_lessons_learned") else 0
                reward_vals = [r.reward for rl in ml.reward_history.values() for r in rl[-10:]]
                avg_r = sum(reward_vals) / len(reward_vals) if reward_vals else 0.0
                acc_history = list(ml.own_accuracy_history)[-20:] if hasattr(ml, "own_accuracy_history") else [acc]
            elif tracker:
                acc = tracker.accuracy
                wins = tracker.win_count
                losses = tracker.loss_count
                lessons = tracker.lessons_learned
                reward_vals = [r for r in list(tracker.recent_rewards)[-20:]]
                avg_r = sum(reward_vals) / len(reward_vals) if reward_vals else 0.0
                # Build accuracy history from recent_correct
                rc = tracker.recent_correct[-50:]
                if len(rc) >= 10:
                    acc_history = [
                        sum(rc[max(0, i - 5):i + 1]) / min(i + 1, 5)
                        for i in range(0, len(rc), 5)
                    ][-10:]
                else:
                    acc_history = [tracker.accuracy] * min(10, max(1, len(rc)))
            else:
                # Non-strategy agent — use agent_knowledge_scores
                k_name = agent_lower
                acc = ml.agent_knowledge_scores.get(k_name, 0.5) * 0.5 + 0.5
                lessons = ml.agent_lessons_learned.get(k_name, 0)
                wins = losses = 0
                avg_r = 0.0
                acc_history = [acc]

            # Status
            if acc > 0.65:
                status = "ACTIVE"
            elif acc > 0.5:
                status = "LEARNING"
            elif lessons > 0:
                status = "ANALYZING"
            else:
                status = "IDLE"

            # Last communication log entry for this agent
            last_lesson = ""
            if hasattr(ml, "communication_log"):
                for msg in reversed(list(ml.communication_log)):
                    if msg.get("from") == agent or msg.get("to") == agent:
                        last_lesson = msg.get("message", "")[:60]
                        break

            card_data = {
                "status": status,
                "confidence": acc,
                "accuracy": acc,
                "lessons": lessons,
                "last_lesson": last_lesson,
                "accuracy_history": acc_history,
            }
            if agent in self._agent_cards:
                self._agent_cards[agent].update_agent(card_data)

            leaderboard_rows.append({
                "name": agent,
                "accuracy": acc,
                "wins": wins,
                "losses": losses,
                "reward": avg_r,
            })

        self.leaderboard.update_data(leaderboard_rows)

        # ── Regime heatmap ─────────────────────────────────────────────────────
        if hasattr(ml, "regime_agent_accuracy"):
            regime_data: dict[str, dict[str, float]] = {}
            for agent in ALL_AGENTS:
                agent_lower = agent.lower().replace(" ", "_")
                regime_data[agent] = {}
                for regime, stats in ml.regime_agent_accuracy.items():
                    agent_stats = stats.get(agent_lower, {})
                    wins_ = agent_stats.get("wins", 0)
                    losses_ = agent_stats.get("losses", 0)
                    t = wins_ + losses_
                    regime_data[agent][regime] = wins_ / t if t > 0 else 0.5
            self.regime_heatmap.update_data(regime_data)

        # ── Cross-agent agreement ──────────────────────────────────────────────
        latest_signals: dict[str, str] = {}
        if hasattr(ml, "_current_quant") and ml._current_quant:
            q = ml._current_quant
            direction = q.get("direction", "FLAT")
            for agent in ALL_AGENTS:
                latest_signals[agent] = direction

        self.agreement_widget.update_data({}, latest_signals)

        # ── Learning timeline ──────────────────────────────────────────────────
        events = []
        for record in reversed(ml.teaching_log[-50:]):
            events.append({
                "ts": record.timestamp,
                "agent": record.target_agent,
                "action": record.action,
                "was_helpful": record.was_helpful,
                "pre": record.pre_metric,
                "post": record.post_metric,
            })
        self.timeline.update_events(events)

        # ── Team meetings ──────────────────────────────────────────────────────
        meetings_data = []
        for pm in reversed(list(ml.post_mortems)):
            meetings_data.append({
                "ts": pm.timestamp,
                "summary": pm.to_narrative(),
                "trades": pm.trade_id,
                "adjustments": pm.weight_changes,
            })
        self.team_meeting_panel.update_meetings(meetings_data[:10])

        # ── Brain report ───────────────────────────────────────────────────────
        try:
            if hasattr(ml, 'get_strategy_brain_report'):
                brain_report = ml.get_strategy_brain_report()
                self._update_brain_panel(brain_report)

            if hasattr(ml, 'get_pattern_insight'):
                regime = getattr(ml, '_current_regime', 'unknown')
                insight = ml.get_pattern_insight(regime)
                wr = insight.get("win_rate", 0.5)
                n = insight.get("sample_size", 0)
                rec = insight.get("recommendation", "NEUTRAL")
                best = ", ".join(insight.get("best_strategies", [])[:3]) or "—"
                rec_color = GREEN if rec == "BOOST" else (RED if rec == "SUPPRESS" else AMBER)
                self._pattern_label.setText(
                    f"Pattern [{insight.get('pattern_key', '?')}]:  WR={wr:.0%} ({n} trades)  "
                    f"Best: {best}  → {rec}"
                )
                self._pattern_label.setStyleSheet(
                    f"color: {rec_color}; font-size: 10px; font-family: 'Courier New'; "
                    f"padding: 4px; background: {BG_CARD}; border: 1px solid {BORDER};"
                )
        except Exception:
            pass

    def _update_brain_panel(self, report: list[dict]):
        """Rebuild the brain strategy cards from the report."""
        # Clear old
        while self._brain_layout.count():
            item = self._brain_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not report:
            lbl = QLabel("No brain data yet — trades are needed for learning.")
            lbl.setStyleSheet(f"color: {DIM}; font-size: 10px; font-family: 'Courier New';")
            self._brain_layout.addWidget(lbl)
            return

        for entry in report[:20]:
            name = entry.get("name", "?")
            weight = entry.get("weight", 1.0)
            acc = entry.get("accuracy", 0)
            wr = entry.get("win_rate", 0)
            trend = entry.get("trend", "stable")
            pnl = entry.get("cumulative_pnl", 0)
            total = entry.get("total_trades", 0)

            trend_icon = "↑" if trend == "improving" else ("↓" if trend == "decaying" else "→")
            trend_color = GREEN if trend == "improving" else (RED if trend == "decaying" else AMBER)
            acc_color = GREEN if acc >= 0.6 else (RED if acc < 0.4 else AMBER)
            pnl_color = GREEN if pnl > 0 else (RED if pnl < 0 else DIM)

            row = QLabel(
                f"  {name:28s}  w={weight:.2f}  acc={acc:.0%}  WR={wr:.0%}  "
                f"P&L=${pnl:+.0f}  [{total}t]  {trend_icon} {trend}"
            )
            row.setStyleSheet(
                f"color: {acc_color}; font-size: 9px; font-family: 'Courier New'; "
                f"padding: 1px 4px; background: {BG_CARD}; "
                f"border-left: 2px solid {trend_color};"
            )
            self._brain_layout.addWidget(row)

        self._brain_layout.addStretch()

    def _refresh_from_db(self):
        """Supplement with data from database if no meta_learner."""
        if self._db is None:
            return

        # Agent accuracy from DB
        try:
            accuracy_rows = self._db.get_agent_accuracy()
            if accuracy_rows and self._meta_learner is None:
                # Aggregate by agent
                agent_acc: dict[str, list] = defaultdict(list)
                for row in accuracy_rows:
                    name = row.get("agent_name", "")
                    acc = row.get("accuracy", 0.5)
                    agent_acc[name].append(acc)

                lb_rows = []
                for agent in ALL_AGENTS:
                    accs = agent_acc.get(agent, [0.5])
                    avg_acc = sum(accs) / len(accs)
                    lb_rows.append({
                        "name": agent, "accuracy": avg_acc,
                        "wins": 0, "losses": 0, "reward": 0.0,
                    })
                    if agent in self._agent_cards:
                        self._agent_cards[agent].update_agent({
                            "status": "ACTIVE" if avg_acc > 0.6 else "LEARNING",
                            "confidence": avg_acc,
                            "accuracy": avg_acc,
                            "lessons": len(accs),
                            "accuracy_history": accs[-10:],
                        })

                self.leaderboard.update_data(lb_rows)
        except Exception:
            pass

        # Learning history from DB
        try:
            history = self._db.get_learning_history(limit=50)
            events = []
            for row in history:
                events.append({
                    "ts": row.get("timestamp", 0),
                    "agent": row.get("agent_name", "?"),
                    "action": row.get("lesson_type", ""),
                    "was_helpful": None,
                    "pre": 0.0,
                    "post": row.get("improvement", 0.0),
                })
            if events and not (self._meta_learner and self._meta_learner.teaching_log):
                self.timeline.update_events(events)
        except Exception:
            pass

    def add_post_mortem(self, narrative: str):
        """Called externally to push a new post-mortem to the display."""
        self.team_meeting_panel.add_post_mortem(narrative)
