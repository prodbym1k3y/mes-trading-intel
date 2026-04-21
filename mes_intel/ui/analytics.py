"""
Analytics Dashboard — Phase 2
Equity curve, drawdown, strategy performance, ML metrics, correlation heatmap
"""
from __future__ import annotations

import math
import json
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy, QGridLayout, QScrollArea, QPushButton, QSplitter, QTextEdit,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal as QtSignal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QLinearGradient,
    QPolygonF, QPainterPath, QFontMetrics,
)

try:
    import pyqtgraph as pg
    pg.setConfigOptions(antialias=True, background="#0a0a0f", foreground="#00ff41")
    HAS_PG = True
except ImportError:
    HAS_PG = False

from .theme import COLORS
from ..event_bus import bus, EventType, Event

# ── Color shorthands ──────────────────────────────────────────────────────────
C        = COLORS
BG       = C["bg_dark"]
BG_PANEL = C["bg_panel"]
CYAN     = C["cyan"]
GREEN    = C["green_bright"]
RED      = C["red"]
AMBER    = C["amber"]
MAGENTA  = C["magenta"]
DIM      = C["text_muted"]
WHITE    = C["text_white"]
GRID     = C["grid"]
BORDER   = C["border"]

_STRATEGY_PALETTE = [
    "#00ffff", "#00ff88", "#ff3366", "#ffff00", "#9933ff",
    "#ff6600", "#00ccff", "#ff99cc", "#33ff99", "#cc99ff",
]


def _mono(size: int = 9, bold: bool = False) -> QFont:
    f = QFont("JetBrains Mono", size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    if bold:
        f.setBold(True)
    return f


def _scanlines(painter: QPainter, w: int, h: int):
    painter.save()
    painter.setOpacity(0.04)
    pen = QPen(QColor(0, 0, 0), 1)
    painter.setPen(pen)
    y = 0
    while y < h:
        painter.drawLine(0, y, w, y)
        y += 3
    painter.restore()


def _get_pnl(t) -> float:
    if hasattr(t, "pnl"):
        return t.pnl or 0.0
    if isinstance(t, dict):
        return t.get("pnl") or 0.0
    return 0.0


def _get_attr(t, attr: str, default=None):
    if hasattr(t, attr):
        return getattr(t, attr, default)
    if isinstance(t, dict):
        return t.get(attr, default)
    return default


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


# ─────────────────────────────────────────────────────────────────────────────
# EquityCurveWidget
# ─────────────────────────────────────────────────────────────────────────────

class EquityCurveWidget(QWidget):
    """Cumulative PnL with HWM, VWAP-equity, underwater fill, and drawdown subplot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cum_pnl: list[float] = []
        self._hwm: list[float] = []
        self._dd_pct: list[float] = []
        self._vwap_eq: list[float] = []
        self._ann_text = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        hdr = QLabel("EQUITY CURVE")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        self._eq_canvas = _PaintWidget(self._paint_equity, min_h=200)
        self._dd_canvas = _PaintWidget(self._paint_dd, min_h=70)
        root.addWidget(self._eq_canvas, 3)
        root.addWidget(self._dd_canvas, 1)

        self._ann_lbl = QLabel("")
        self._ann_lbl.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        self._ann_lbl.setWordWrap(True)
        root.addWidget(self._ann_lbl)

    def update_data(self, trades: list):
        cum, hwm = 0.0, 0.0
        cl, hl, dl = [], [], []
        for t in trades:
            pnl = _get_pnl(t)
            cum += pnl
            hwm = max(hwm, cum)
            dd = ((hwm - cum) / hwm * 100) if hwm > 0 else 0.0
            cl.append(cum); hl.append(hwm); dl.append(dd)

        self._cum_pnl = cl
        self._hwm = hl
        self._dd_pct = dl

        win = max(1, len(cl) // 5)
        vw = []
        for i in range(len(cl)):
            sl = cl[max(0, i - win + 1):i + 1]
            vw.append(sum(sl) / len(sl))
        self._vwap_eq = vw

        parts = []
        if dl:
            md = max(dl)
            idx = dl.index(md)
            parts.append(f"Max DD: {md:.1f}% @ trade #{idx + 1}")
        wins = [1 if _get_pnl(t) > 0 else 0 for t in trades]
        best, worst, cw2, cl2 = 0, 0, 0, 0
        for wf in wins:
            if wf:
                cw2 += 1; cl2 = 0
            else:
                cl2 += 1; cw2 = 0
            best = max(best, cw2); worst = max(worst, cl2)
        parts.append(f"Best streak: {best}W  Worst: {worst}L")
        self._ann_text = "  |  ".join(parts)
        self._ann_lbl.setText(self._ann_text)

        self._eq_canvas.update()
        self._dd_canvas.update()

    def _paint_equity(self, painter: QPainter, w: int, h: int):
        ML, MR, MT, MB = 58, 8, 6, 6
        cw, ch = w - ML - MR, h - MT - MB
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))

        vals = self._cum_pnl
        if not vals:
            return

        ymin = min(min(vals), 0); ymax = max(max(vals), 0.01)
        yspan = ymax - ymin or 1.0
        n = len(vals)

        def px(i, v):
            return (ML + (i / max(n - 1, 1)) * cw,
                    MT + ch - ((v - ymin) / yspan) * ch)

        zy = MT + ch - ((0 - ymin) / yspan) * ch

        # Grid
        painter.setPen(QPen(QColor(GRID), 1))
        for gi in range(5):
            gy = MT + (gi / 4) * ch
            painter.drawLine(ML, int(gy), ML + cw, int(gy))
        painter.setPen(QColor(DIM))
        painter.setFont(_mono(7))
        for gi in range(5):
            gv = ymax - (gi / 4) * yspan
            gy = MT + (gi / 4) * ch
            painter.drawText(2, int(gy) + 4, f"${gv:.0f}")

        # Zero line
        painter.setPen(QPen(QColor(DIM), 1, Qt.PenStyle.DashLine))
        painter.drawLine(ML, int(zy), ML + cw, int(zy))

        # Underwater fill (red)
        painter.setPen(Qt.PenStyle.NoPen)
        rf = QColor(RED); rf.setAlphaF(0.18)
        for i in range(n - 1):
            if vals[i] < 0 or vals[i + 1] < 0:
                x1, y1 = px(i, vals[i]); x2, y2 = px(i + 1, vals[i + 1])
                poly = QPolygonF([QPointF(x1, y1), QPointF(x2, y2),
                                  QPointF(x2, zy), QPointF(x1, zy)])
                painter.setBrush(QBrush(rf))
                painter.drawPolygon(poly)

        # Positive fill (green gradient)
        path = QPainterPath()
        path.moveTo(*px(0, vals[0]))
        for i in range(1, n):
            path.lineTo(*px(i, vals[i]))
        path.lineTo(*px(n - 1, 0)); path.lineTo(*px(0, 0)); path.closeSubpath()
        grad = QLinearGradient(0, MT, 0, MT + ch)
        grad.setColorAt(0, QColor(0, 255, 65, 45)); grad.setColorAt(1, QColor(0, 255, 65, 5))
        painter.setBrush(QBrush(grad))
        painter.setClipRect(QRectF(ML, MT, cw, zy - MT))
        painter.drawPath(path)
        painter.setClipping(False)

        # HWM line (green dashed)
        if self._hwm:
            painter.setPen(QPen(QColor(GREEN), 1, Qt.PenStyle.DashLine))
            for i in range(len(self._hwm) - 1):
                x1, y1 = px(i, self._hwm[i]); x2, y2 = px(i + 1, self._hwm[i + 1])
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # VWAP equity (yellow dashed)
        if self._vwap_eq:
            painter.setPen(QPen(QColor(AMBER), 1, Qt.PenStyle.DashLine))
            for i in range(len(self._vwap_eq) - 1):
                x1, y1 = px(i, self._vwap_eq[i]); x2, y2 = px(i + 1, self._vwap_eq[i + 1])
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Main equity line (cyan, glow)
        eq_path = QPainterPath()
        eq_path.moveTo(*px(0, vals[0]))
        for i in range(1, n):
            eq_path.lineTo(*px(i, vals[i]))
        painter.setPen(QPen(QColor(CYAN + "50"), 5)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(eq_path)
        painter.setPen(QPen(QColor(CYAN), 2))
        painter.drawPath(eq_path)

        # Max DD dot
        if self._dd_pct:
            md = max(self._dd_pct)
            idx = self._dd_pct.index(md)
            if idx < n:
                ax, ay = px(idx, vals[idx])
                painter.setBrush(QBrush(QColor(RED))); painter.setPen(QPen(QColor(RED), 1))
                painter.drawEllipse(int(ax) - 4, int(ay) - 4, 8, 8)

        # Legend
        painter.setFont(_mono(7))
        items = [("equity", CYAN), ("HWM", GREEN), ("VWAP", AMBER)]
        for ki, (lbl, col) in enumerate(items):
            lx = ML + 8 + ki * 64
            painter.setPen(QPen(QColor(col), 2))
            painter.drawLine(lx, MT + 8, lx + 16, MT + 8)
            painter.setPen(QColor(DIM))
            painter.drawText(lx + 18, MT + 12, lbl)

        _scanlines(painter, w, h)

    def _paint_dd(self, painter: QPainter, w: int, h: int):
        ML, MR, MT, MB = 58, 8, 4, 4
        cw, ch = w - ML - MR, h - MT - MB
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))

        vals = self._dd_pct
        if not vals:
            return

        ymax = max(max(vals), 0.1)
        n = len(vals)

        def px(i, v):
            return (ML + (i / max(n - 1, 1)) * cw,
                    MT + (v / ymax) * ch)

        # Fill path
        path = QPainterPath()
        path.moveTo(ML, MT)
        for i in range(n):
            path.lineTo(*px(i, vals[i]))
        path.lineTo(*px(n - 1, 0)); path.lineTo(ML, MT); path.closeSubpath()

        grad = QLinearGradient(0, MT, 0, MT + ch)
        grad.setColorAt(0, QColor(255, 23, 68, 80)); grad.setColorAt(1, QColor(255, 23, 68, 15))
        painter.setBrush(QBrush(grad)); painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        line = QPainterPath()
        line.moveTo(*px(0, vals[0]))
        for i in range(1, n):
            line.lineTo(*px(i, vals[i]))
        painter.setPen(QPen(QColor(RED), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)

        painter.setFont(_mono(7)); painter.setPen(QColor(DIM))
        painter.drawText(2, MT + 10, f"{ymax:.1f}%")
        painter.drawText(2, MT + ch, "0%")
        painter.drawText(ML + 4, h - 2, "DRAWDOWN %")
        _scanlines(painter, w, h)


# ─────────────────────────────────────────────────────────────────────────────
# DrawdownWidget
# ─────────────────────────────────────────────────────────────────────────────

class DrawdownWidget(QWidget):
    """Rolling drawdown chart with severity indicator and stats panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dd_pct: list[float] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        hdr = QLabel("DRAWDOWN MONITOR")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        self._canvas = _PaintWidget(self._paint, min_h=120)
        root.addWidget(self._canvas, 1)

        stats = QHBoxLayout()
        self._cur_lbl   = QLabel("Current: 0.0%")
        self._max_lbl   = QLabel("Max: 0.0%")
        self._avg_lbl   = QLabel("Avg: 0.0%")
        self._rec_lbl   = QLabel("Recovery: -")
        for lbl in [self._cur_lbl, self._max_lbl, self._avg_lbl, self._rec_lbl]:
            lbl.setStyleSheet(f"color: {WHITE}; font-size: 10px;")
            stats.addWidget(lbl)
        root.addLayout(stats)

    def update_data(self, trades: list):
        cum, hwm = 0.0, 0.0
        dds = []
        for t in trades:
            pnl = _get_pnl(t)
            cum += pnl; hwm = max(hwm, cum)
            dds.append(((hwm - cum) / hwm * 100) if hwm > 0 else 0.0)
        self._dd_pct = dds

        cur = dds[-1] if dds else 0.0
        max_dd = max(dds) if dds else 0.0
        avg_dd = sum(dds) / len(dds) if dds else 0.0
        rec = sum(1 for d in reversed(dds) for _ in [None] if d > 0)  # simplified
        rec = 0
        for d in reversed(dds):
            if d > 0: rec += 1
            else: break

        sev = GREEN if cur < 5 else (AMBER if cur < 10 else RED)
        self._cur_lbl.setText(f"Current: {cur:.1f}%")
        self._cur_lbl.setStyleSheet(f"color: {sev}; font-size: 10px; font-weight: bold;")
        self._max_lbl.setText(f"Max: {max_dd:.1f}%")
        self._avg_lbl.setText(f"Avg: {avg_dd:.1f}%")
        self._rec_lbl.setText(f"Recovery: {rec} bars")
        self._canvas.update()

    def _paint(self, painter: QPainter, w: int, h: int):
        ML, MR, MT, MB = 44, 8, 6, 6
        cw, ch = w - ML - MR, h - MT - MB
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))

        vals = self._dd_pct
        if not vals:
            return

        ymax = max(max(vals), 0.1)
        n = len(vals)

        def px(i, v):
            return (ML + (i / max(n - 1, 1)) * cw,
                    MT + (v / ymax) * ch)

        # Severity bands
        for threshold, col in [(5, GREEN), (10, AMBER), (ymax, RED)]:
            yb = MT + (min(threshold, ymax) / ymax) * ch
            bc = QColor(col); bc.setAlphaF(0.06)
            painter.fillRect(ML, MT, cw, int(yb - MT), QBrush(bc))

        path = QPainterPath()
        path.moveTo(ML, MT)
        for i in range(n):
            path.lineTo(*px(i, vals[i]))
        path.lineTo(*px(n - 1, 0)); path.lineTo(ML, MT); path.closeSubpath()

        grad = QLinearGradient(0, MT, 0, MT + ch)
        grad.setColorAt(0, QColor(255, 23, 68, 70)); grad.setColorAt(1, QColor(255, 23, 68, 10))
        painter.setBrush(QBrush(grad)); painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        line = QPainterPath()
        line.moveTo(*px(0, vals[0]))
        for i in range(1, n):
            line.lineTo(*px(i, vals[i]))
        painter.setPen(QPen(QColor(RED), 1)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)

        painter.setFont(_mono(7)); painter.setPen(QColor(DIM))
        painter.drawText(2, MT + 10, f"{ymax:.0f}%")
        painter.drawText(2, MT + ch, "0%")
        _scanlines(painter, w, h)


# ─────────────────────────────────────────────────────────────────────────────
# StrategyPerformanceWidget
# ─────────────────────────────────────────────────────────────────────────────

class StrategyPerformanceWidget(QWidget):
    """Win-rate bar chart + sortable strategy table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scores: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        hdr = QLabel("STRATEGY PERFORMANCE")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        self._bar_canvas = _PaintWidget(self._paint_bars, min_h=90, fixed_h=90)
        root.addWidget(self._bar_canvas)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Strategy", "Signals", "Win Rate", "Avg PnL", "Prof Factor", "Weight"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setMinimumHeight(140)
        root.addWidget(self._table, 1)

    def update_data(self, strategy_scores: dict):
        self._scores = strategy_scores
        self._bar_canvas.update()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(strategy_scores))
        for row, (name, data) in enumerate(strategy_scores.items()):
            wr = data.get("win_rate", 0)
            avg_pnl = data.get("avg_pnl", 0)
            pf = data.get("profit_factor", 0)
            sig = data.get("signals", 0)
            wt = data.get("weight", 0)

            row_col = QColor(GREEN if avg_pnl > 0 else (RED if avg_pnl < 0 else AMBER))
            row_col.setAlphaF(0.08)

            for col, (text, fg) in enumerate([
                (name, WHITE),
                (str(sig), DIM),
                (f"{wr:.0%}", GREEN if wr >= 0.5 else RED),
                (f"${avg_pnl:.2f}", GREEN if avg_pnl >= 0 else RED),
                (f"{pf:.2f}", DIM),
                (f"{wt:.3f}", CYAN),
            ]):
                item = QTableWidgetItem(text)
                item.setBackground(QBrush(row_col))
                item.setForeground(QColor(fg))
                self._table.setItem(row, col, item)
        self._table.setSortingEnabled(True)

    def _paint_bars(self, painter: QPainter, w: int, h: int):
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))
        if not self._scores:
            return

        names = list(self._scores.keys())
        n = len(names)
        pad = 3
        bar_w = max(1, (w - pad) // max(n, 1) - pad)
        label_h = 13
        chart_h = h - label_h - 6

        painter.setFont(_mono(7))

        for i, name in enumerate(names):
            wr = self._scores[name].get("win_rate", 0)
            col = GREEN if wr >= 0.5 else RED
            bx = pad + i * (bar_w + pad)
            bar_h = int(wr * (chart_h - 4))

            # Background
            bg = QColor(col); bg.setAlphaF(0.2)
            painter.fillRect(bx, 4, bar_w, chart_h - 4, bg)
            # Fill
            painter.fillRect(bx, 4 + (chart_h - 4 - bar_h), bar_w, bar_h, QColor(col))

            # 50% marker
            y50 = 4 + int((chart_h - 4) * 0.5)
            painter.setPen(QPen(QColor(AMBER), 1, Qt.PenStyle.DashLine))
            painter.drawLine(bx, y50, bx + bar_w, y50)

            painter.setPen(QColor(DIM))
            painter.drawText(bx, h - 2, name[:6])

        _scanlines(painter, w, h)


# ─────────────────────────────────────────────────────────────────────────────
# WeightEvolutionWidget
# ─────────────────────────────────────────────────────────────────────────────

class WeightEvolutionWidget(QWidget):
    """Line chart: each strategy's weight over last 50 events."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: dict[str, list[float]] = defaultdict(list)
        self._max_events = 50

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        hdr = QLabel("WEIGHT EVOLUTION  (meta-learner)")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        self._canvas = _PaintWidget(self._paint, min_h=150)
        root.addWidget(self._canvas, 1)

    def add_weight_event(self, weights: dict[str, float]):
        for name, w in weights.items():
            self._history[name].append(w)
            if len(self._history[name]) > self._max_events:
                self._history[name].pop(0)
        self._canvas.update()

    def _paint(self, painter: QPainter, w: int, h: int):
        ML, MR, MT, MB = 44, 110, 8, 8
        cw, ch = w - ML - MR, h - MT - MB
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))

        if not self._history:
            return

        max_len = max(len(v) for v in self._history.values())
        if max_len < 2:
            return

        painter.setPen(QPen(QColor(GRID), 1))
        for gi in range(5):
            gy = MT + (gi / 4) * ch
            painter.drawLine(ML, int(gy), ML + cw, int(gy))

        strategies = list(self._history.keys())
        for si, name in enumerate(strategies):
            vals = self._history[name]
            col = _STRATEGY_PALETTE[si % len(_STRATEGY_PALETTE)]
            pen = QPen(QColor(col), 2)
            painter.setPen(pen)
            nv = len(vals)
            for i in range(nv - 1):
                x1 = ML + (i / max(max_len - 1, 1)) * cw
                x2 = ML + ((i + 1) / max(max_len - 1, 1)) * cw
                y1 = MT + ch - vals[i] * ch
                y2 = MT + ch - vals[i + 1] * ch
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

            # Legend
            lx = ML + cw + 6
            ly = MT + si * 14
            painter.fillRect(lx, ly + 3, 10, 2, QColor(col))
            painter.setFont(_mono(7))
            painter.setPen(QColor(col))
            painter.drawText(lx + 12, ly + 11, name[:12])

        painter.setFont(_mono(7)); painter.setPen(QColor(DIM))
        painter.drawText(2, MT + 8, "1.0")
        painter.drawText(2, MT + ch // 2, "0.5")
        painter.drawText(2, MT + ch, "0.0")
        _scanlines(painter, w, h)


# ─────────────────────────────────────────────────────────────────────────────
# CorrelationHeatmapWidget
# ─────────────────────────────────────────────────────────────────────────────

class CorrelationHeatmapWidget(QWidget):
    """QPainter heatmap of strategy return correlations. Hover shows tooltip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._matrix: list[list[float]] = []
        self._labels: list[str] = []
        self._cell_rects: list[tuple] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        hdr = QLabel("STRATEGY CORRELATION HEATMAP")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        self._canvas = _PaintWidget(self._paint, min_h=200)
        self._canvas.setMouseTracking(True)
        self._canvas.mouseMoveEvent = self._on_mouse_move
        root.addWidget(self._canvas, 1)

        legend = QLabel("■ GREEN = positive  ■ RED = negative  ■ DIM = zero")
        legend.setStyleSheet(f"color: {DIM}; font-size: 8px;")
        root.addWidget(legend)

    def update_data(self, trades: list):
        by_strat: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            sn = _get_attr(t, "strategy_name", "unknown")
            by_strat[sn].append(_get_pnl(t))

        labels = sorted(by_strat.keys())
        n = len(labels)
        if n < 2:
            self._matrix = [[1.0]]
            self._labels = labels
            self._canvas.update()
            return

        max_len = max(len(v) for v in by_strat.values())
        series = [by_strat[lb] + [0.0] * (max_len - len(by_strat[lb])) for lb in labels]

        def pearson(a: list, b: list) -> float:
            n2 = len(a)
            if n2 < 2:
                return 0.0
            ma, mb = sum(a) / n2, sum(b) / n2
            num = sum((a[i] - ma) * (b[i] - mb) for i in range(n2))
            da = math.sqrt(sum((x - ma) ** 2 for x in a))
            db = math.sqrt(sum((x - mb) ** 2 for x in b))
            return num / (da * db) if da * db > 0 else 0.0

        self._matrix = [[pearson(series[r], series[c]) for c in range(n)] for r in range(n)]
        self._labels = labels
        self._canvas.update()

    def _paint(self, painter: QPainter, w: int, h: int):
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))
        n = len(self._labels)
        if not n:
            return

        label_w = 62
        cell = min((w - label_w) // max(n, 1), (h - label_w) // max(n, 1), 56)
        ox, oy = label_w, label_w
        self._cell_rects = []
        painter.setFont(_mono(7))

        for i, lb in enumerate(self._labels):
            painter.setPen(QColor(DIM))
            painter.drawText(ox + i * cell + 2, oy - 4, lb[:7])
            painter.save()
            painter.translate(ox - 4, oy + (i + 1) * cell)
            painter.rotate(-90)
            painter.drawText(0, 0, lb[:7])
            painter.restore()

        for r in range(n):
            for c in range(n):
                val = self._matrix[r][c]
                cx = ox + c * cell; cy = oy + r * cell
                if val > 0:
                    col = QColor(GREEN); col.setAlphaF(0.12 + 0.78 * val)
                elif val < 0:
                    col = QColor(RED); col.setAlphaF(0.12 + 0.78 * abs(val))
                else:
                    col = QColor(WHITE); col.setAlphaF(0.08)

                painter.fillRect(cx, cy, cell - 1, cell - 1, col)
                painter.setPen(QColor(GRID))
                painter.drawRect(cx, cy, cell - 1, cell - 1)
                painter.setPen(QColor(WHITE))
                painter.drawText(QRectF(cx, cy, cell - 1, cell - 1),
                                 Qt.AlignmentFlag.AlignCenter, f"{val:.2f}")
                self._cell_rects.append((cx, cy, cell - 1, cell - 1, val))

        _scanlines(painter, w, h)

    def _on_mouse_move(self, event):
        from PySide6.QtWidgets import QToolTip
        pos = event.position()
        for cx, cy, cw, ch, val in self._cell_rects:
            if cx <= pos.x() <= cx + cw and cy <= pos.y() <= cy + ch:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  f"Correlation: {val:.4f}", self._canvas)
                return
        QToolTip.hideText()


# ─────────────────────────────────────────────────────────────────────────────
# MLPerformanceWidget
# ─────────────────────────────────────────────────────────────────────────────

class MLPerformanceWidget(QWidget):
    """Model accuracy chart (walk-forward folds) + feature importance bars."""

    RETRAIN_THRESHOLD = 0.55

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model_perf: list[dict] = []
        self._fi: dict[str, float] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        hdr = QLabel("ML MODEL PERFORMANCE")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        meta = QHBoxLayout()
        self._retrain_lbl = QLabel("Last retrain: Never")
        self._next_lbl    = QLabel("Next: on degradation")
        self._deg_lbl     = QLabel("Status: OK")
        for lbl in [self._retrain_lbl, self._next_lbl, self._deg_lbl]:
            lbl.setStyleSheet(f"color: {DIM}; font-size: 9px;")
            meta.addWidget(lbl)
        root.addLayout(meta)

        acc_hdr = QLabel("ACCURACY  — — —  dashed=train  solid=val  red=threshold")
        acc_hdr.setStyleSheet(f"color: {DIM}; font-size: 8px;")
        root.addWidget(acc_hdr)

        self._acc_canvas = _PaintWidget(self._paint_acc, min_h=120)
        root.addWidget(self._acc_canvas, 1)

        fi_hdr = QLabel("FEATURE IMPORTANCE  (top 10)")
        fi_hdr.setStyleSheet(f"color: {DIM}; font-size: 8px;")
        root.addWidget(fi_hdr)

        self._fi_canvas = _PaintWidget(self._paint_fi, min_h=110)
        root.addWidget(self._fi_canvas, 1)

    def update_data(self, model_performance: list):
        self._model_perf = model_performance
        if model_performance:
            last = model_performance[-1]
            ts = last.get("timestamp", "")
            self._retrain_lbl.setText(f"Last retrain: {str(ts)[:19]}")
            self._fi = last.get("feature_importance", {})
            val = last.get("val_accuracy", 1.0)
            if val < self.RETRAIN_THRESHOLD:
                self._deg_lbl.setText("Status: DEGRADED — retrain needed")
                self._deg_lbl.setStyleSheet(f"color: {RED}; font-size: 9px; font-weight: bold;")
            else:
                self._deg_lbl.setText("Status: OK")
                self._deg_lbl.setStyleSheet(f"color: {GREEN}; font-size: 9px;")
        self._acc_canvas.update()
        self._fi_canvas.update()

    def _paint_acc(self, painter: QPainter, w: int, h: int):
        ML, MR, MT, MB = 44, 8, 6, 6
        cw, ch = w - ML - MR, h - MT - MB
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))

        if not self._model_perf or len(self._model_perf) < 2:
            return

        train_acc = [p.get("train_accuracy", 0) for p in self._model_perf]
        val_acc   = [p.get("val_accuracy",   0) for p in self._model_perf]
        n = len(train_acc)

        ymin, ymax = 0.4, 1.0; yspan = ymax - ymin

        def px(i, v):
            return (ML + (i / max(n - 1, 1)) * cw,
                    MT + ch - ((v - ymin) / yspan) * ch)

        painter.setPen(QPen(QColor(GRID), 1))
        for gi in range(4):
            gy = MT + (gi / 3) * ch
            painter.drawLine(ML, int(gy), ML + cw, int(gy))

        # Threshold
        ry = MT + ch - ((self.RETRAIN_THRESHOLD - ymin) / yspan) * ch
        painter.setPen(QPen(QColor(RED), 1, Qt.PenStyle.DashLine))
        painter.drawLine(ML, int(ry), ML + cw, int(ry))
        painter.setFont(_mono(7)); painter.setPen(QColor(RED))
        painter.drawText(ML + 4, int(ry) - 2, f"retrain < {self.RETRAIN_THRESHOLD:.0%}")

        # Train (dashed cyan)
        painter.setPen(QPen(QColor(CYAN), 1, Qt.PenStyle.DashLine))
        for i in range(n - 1):
            x1, y1 = px(i, train_acc[i]); x2, y2 = px(i + 1, train_acc[i + 1])
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Val (solid green)
        painter.setPen(QPen(QColor(GREEN), 2))
        for i in range(n - 1):
            x1, y1 = px(i, val_acc[i]); x2, y2 = px(i + 1, val_acc[i + 1])
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        painter.setFont(_mono(7)); painter.setPen(QColor(DIM))
        for gi in range(4):
            v = ymin + (gi / 3) * yspan
            gy = MT + ch - (gi / 3) * ch
            painter.drawText(2, int(gy) + 4, f"{v:.0%}")
        _scanlines(painter, w, h)

    def _paint_fi(self, painter: QPainter, w: int, h: int):
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))
        if not self._fi:
            return

        top10 = sorted(self._fi.items(), key=lambda x: -x[1])[:10]
        n = len(top10)
        row_h = max(8, (h - 8) // max(n, 1))
        max_val = top10[0][1] if top10 else 1.0
        label_w = 116

        painter.setFont(_mono(8))
        for i, (feat, val) in enumerate(top10):
            y = 4 + i * row_h
            bar_w = int((val / max(max_val, 0.001)) * (w - label_w - 8))
            col = _STRATEGY_PALETTE[i % len(_STRATEGY_PALETTE)]
            painter.setPen(QColor(DIM))
            painter.drawText(2, y + row_h - 2, feat[:14])
            painter.fillRect(label_w, y + 1, bar_w, row_h - 2, QColor(col))
            painter.setPen(QColor(WHITE))
            painter.drawText(label_w + bar_w + 4, y + row_h - 2, f"{val:.3f}")
        _scanlines(painter, w, h)


# ─────────────────────────────────────────────────────────────────────────────
# _PaintWidget — internal helper
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# TradeMetricsWidget — key quant metrics panel
# ─────────────────────────────────────────────────────────────────────────────

class TradeMetricsWidget(QWidget):
    """Key trading metrics: Sharpe, Sortino, profit factor, VaR, ES, win rate."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._metrics: dict[str, str] = {}
        self._rolling_7: dict[str, str] = {}
        self._rolling_30: dict[str, str] = {}
        self._rolling_90: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        hdr = QLabel("KEY PERFORMANCE METRICS")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        # Top grid: main metrics
        self._grid = QGridLayout()
        self._grid.setSpacing(6)
        self._metric_vals: dict[str, QLabel] = {}

        METRIC_DEFS = [
            ("sharpe",         "SHARPE RATIO",    CYAN),
            ("sortino",        "SORTINO RATIO",   GREEN),
            ("profit_factor",  "PROFIT FACTOR",   AMBER),
            ("win_rate",       "WIN RATE",         GREEN),
            ("avg_win",        "AVG WIN ($)",     GREEN),
            ("avg_loss",       "AVG LOSS ($)",    RED),
            ("max_dd",         "MAX DRAWDOWN",    RED),
            ("max_dd_pct",     "MAX DD %",        RED),
            ("var_95",         "VaR 95% ($)",     MAGENTA),
            ("cvar_95",        "CVaR/ES 95% ($)", MAGENTA),
            ("trade_count",    "TOTAL TRADES",    DIM),
            ("expectancy",     "EXPECTANCY ($)",  AMBER),
        ]

        for i, (key, label, color) in enumerate(METRIC_DEFS):
            frame = QFrame()
            frame.setStyleSheet(
                f"background: {BG_PANEL}; border: 1px solid {BORDER}; "
                f"border-left: 3px solid {color};"
            )
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(8, 4, 8, 4)
            fl.setSpacing(1)

            lbl_k = QLabel(label)
            lbl_k.setStyleSheet(f"color: {DIM}; font-size: 8px; font-family: 'Courier New';")
            fl.addWidget(lbl_k)

            lbl_v = QLabel("--")
            lbl_v.setStyleSheet(
                f"color: {color}; font-size: 18px; font-weight: bold; "
                f"font-family: 'Courier New';"
            )
            fl.addWidget(lbl_v)
            self._metric_vals[key] = lbl_v

            self._grid.addWidget(frame, i // 4, i % 4)

        root.addLayout(self._grid)

        # Rolling metrics section
        roll_hdr = QLabel("ROLLING PERFORMANCE")
        roll_hdr.setStyleSheet(f"color: {AMBER}; font-size: 10px; font-weight: bold;")
        root.addWidget(roll_hdr)

        roll_grid = QGridLayout()
        roll_grid.setSpacing(4)

        periods = [("7D", self._rolling_7), ("30D", self._rolling_30), ("90D", self._rolling_90)]
        self._roll_labels: dict[str, dict[str, QLabel]] = {}

        for col, (period, _) in enumerate(periods):
            col_lbl = QLabel(f"  {period}")
            col_lbl.setStyleSheet(f"color: {CYAN}; font-size: 10px; font-weight: bold; font-family: 'Courier New';")
            roll_grid.addWidget(col_lbl, 0, col + 1)
            self._roll_labels[period] = {}

        roll_metrics = [
            ("pnl",    "P&L"),
            ("wr",     "WIN RATE"),
            ("trades", "TRADES"),
            ("pf",     "P-FACTOR"),
        ]

        for row, (key, label) in enumerate(roll_metrics):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {DIM}; font-size: 8px; font-family: 'Courier New';")
            roll_grid.addWidget(lbl, row + 1, 0)

            for col, (period, _) in enumerate(periods):
                v = QLabel("--")
                v.setStyleSheet(f"color: {WHITE}; font-size: 9px; font-family: 'Courier New'; font-weight: bold;")
                roll_grid.addWidget(v, row + 1, col + 1)
                self._roll_labels[period][key] = v

        root.addLayout(roll_grid)
        root.addStretch()

    def update_trades(self, trades: list):
        if not trades:
            return

        pnls = [_get_pnl(t) for t in trades]
        if not pnls:
            return

        import statistics

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        n = len(pnls)
        n_win = len(wins)
        n_loss = len(losses)

        win_rate = n_win / n if n > 0 else 0
        avg_win  = sum(wins) / n_win if wins else 0
        avg_loss = sum(losses) / n_loss if losses else 0
        gross_profit = sum(wins)
        gross_loss   = abs(sum(losses))
        profit_factor = gross_profit / max(gross_loss, 0.01)
        expectancy = sum(pnls) / n if n else 0
        total_pnl = sum(pnls)

        # Drawdown
        cum, hwm, max_dd, max_dd_pct = 0.0, 0.0, 0.0, 0.0
        for p in pnls:
            cum += p
            hwm = max(hwm, cum)
            dd = hwm - cum
            max_dd = max(max_dd, dd)
            if hwm > 0:
                max_dd_pct = max(max_dd_pct, dd / hwm * 100)

        # Sharpe / Sortino — use empyrical if available, else manual calc
        try:
            import empyrical
            import numpy as _np
            returns = _np.array(pnls) / max(abs(sum(pnls)), 1.0)
            sharpe = float(empyrical.sharpe_ratio(returns, period='daily'))
            sortino = float(empyrical.sortino_ratio(returns, period='daily'))
            if _np.isnan(sharpe):
                sharpe = 0.0
            if _np.isnan(sortino):
                sortino = 0.0
        except Exception:
            if len(pnls) >= 3:
                mean = statistics.mean(pnls)
                stdev = statistics.stdev(pnls) if len(pnls) > 1 else 1.0
                downside_returns = [p for p in pnls if p < 0]
                downside_std = statistics.stdev(downside_returns) if len(downside_returns) > 1 else stdev
                ann_factor = math.sqrt(252)
                sharpe  = (mean / max(stdev, 0.001)) * ann_factor
                sortino = (mean / max(downside_std, 0.001)) * ann_factor
            else:
                sharpe = sortino = 0.0

        # VaR / CVaR (historical, 95%)
        sorted_pnls = sorted(pnls)
        var_idx = max(0, int(len(sorted_pnls) * 0.05) - 1)
        var_95 = sorted_pnls[var_idx] if sorted_pnls else 0.0
        cvar_95 = sum(sorted_pnls[:var_idx + 1]) / (var_idx + 1) if var_idx >= 0 else var_95

        def _fmt(v, fmt=".2f", prefix=""):
            return f"{prefix}{v:{fmt}}"

        wr_color = GREEN if win_rate >= 0.5 else RED
        pf_color = GREEN if profit_factor >= 1.0 else RED
        sh_color = GREEN if sharpe >= 0.5 else (RED if sharpe < 0 else AMBER)

        self._metric_vals["sharpe"].setText(f"{sharpe:.2f}")
        self._metric_vals["sharpe"].setStyleSheet(
            f"color: {sh_color}; font-size: 18px; font-weight: bold; font-family: 'Courier New';"
        )
        self._metric_vals["sortino"].setText(f"{sortino:.2f}")
        self._metric_vals["profit_factor"].setText(f"{profit_factor:.2f}")
        self._metric_vals["profit_factor"].setStyleSheet(
            f"color: {pf_color}; font-size: 18px; font-weight: bold; font-family: 'Courier New';"
        )
        self._metric_vals["win_rate"].setText(f"{win_rate * 100:.1f}%")
        self._metric_vals["win_rate"].setStyleSheet(
            f"color: {wr_color}; font-size: 18px; font-weight: bold; font-family: 'Courier New';"
        )
        self._metric_vals["avg_win"].setText(f"${avg_win:.2f}")
        self._metric_vals["avg_loss"].setText(f"${avg_loss:.2f}")
        self._metric_vals["max_dd"].setText(f"${max_dd:.2f}")
        self._metric_vals["max_dd_pct"].setText(f"{max_dd_pct:.1f}%")
        self._metric_vals["var_95"].setText(f"${var_95:.2f}")
        self._metric_vals["cvar_95"].setText(f"${cvar_95:.2f}")
        self._metric_vals["trade_count"].setText(str(n))
        self._metric_vals["expectancy"].setText(f"${expectancy:.2f}")
        exp_col = GREEN if expectancy > 0 else RED
        self._metric_vals["expectancy"].setStyleSheet(
            f"color: {exp_col}; font-size: 18px; font-weight: bold; font-family: 'Courier New';"
        )

        # Rolling periods
        from datetime import datetime, timedelta
        now = datetime.utcnow()

        for days, period in [(7, "7D"), (30, "30D"), (90, "90D")]:
            cutoff = now - timedelta(days=days)
            period_trades = []
            for t in trades:
                ts_str = _get_attr(t, "entry_time") or _get_attr(t, "timestamp") or ""
                try:
                    ts = datetime.fromisoformat(str(ts_str)[:19])
                    if ts >= cutoff:
                        period_trades.append(t)
                except Exception:
                    pass

            if period_trades:
                ppnls = [_get_pnl(t) for t in period_trades]
                p_wins = [p for p in ppnls if p > 0]
                p_losses = [p for p in ppnls if p < 0]
                p_wr = len(p_wins) / len(ppnls) if ppnls else 0
                p_pf = sum(p_wins) / max(abs(sum(p_losses)), 0.01) if p_losses else (9.9 if p_wins else 0)

                wr_c = GREEN if p_wr >= 0.5 else RED
                pnl_c = GREEN if sum(ppnls) >= 0 else RED

                self._roll_labels[period]["pnl"].setText(f"${sum(ppnls):.0f}")
                self._roll_labels[period]["pnl"].setStyleSheet(
                    f"color: {pnl_c}; font-size: 9px; font-family: 'Courier New'; font-weight: bold;"
                )
                self._roll_labels[period]["wr"].setText(f"{p_wr * 100:.0f}%")
                self._roll_labels[period]["wr"].setStyleSheet(
                    f"color: {wr_c}; font-size: 9px; font-family: 'Courier New'; font-weight: bold;"
                )
                self._roll_labels[period]["trades"].setText(str(len(ppnls)))
                self._roll_labels[period]["pf"].setText(f"{min(p_pf, 9.9):.1f}")
            else:
                for k in ("pnl", "wr", "trades", "pf"):
                    self._roll_labels[period][k].setText("--")


# ─────────────────────────────────────────────────────────────────────────────
# WinRateBreakdownWidget
# ─────────────────────────────────────────────────────────────────────────────

class WinRateBreakdownWidget(QWidget):
    """Win rate breakdown by time of day, day of week, regime, and setup tags."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._by_hour: dict[int, dict] = {}
        self._by_dow: dict[str, dict] = {}
        self._by_regime: dict[str, dict] = {}
        self._by_tag: dict[str, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        hdr = QLabel("WIN RATE BREAKDOWN")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: hour + DOW
        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.setContentsMargins(0, 0, 4, 0)
        left_l.setSpacing(4)

        hour_hdr = QLabel("BY TIME OF DAY (RTH)")
        hour_hdr.setStyleSheet(f"color: {AMBER}; font-size: 9px; font-weight: bold;")
        left_l.addWidget(hour_hdr)
        self._hour_canvas = _PaintWidget(self._paint_hour, min_h=120)
        left_l.addWidget(self._hour_canvas, 1)

        dow_hdr = QLabel("BY DAY OF WEEK")
        dow_hdr.setStyleSheet(f"color: {AMBER}; font-size: 9px; font-weight: bold;")
        left_l.addWidget(dow_hdr)
        self._dow_canvas = _PaintWidget(self._paint_dow, min_h=80)
        left_l.addWidget(self._dow_canvas, 1)

        splitter.addWidget(left_w)

        # Right: regime + tags
        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.setContentsMargins(4, 0, 0, 0)
        right_l.setSpacing(4)

        regime_hdr = QLabel("BY MARKET REGIME")
        regime_hdr.setStyleSheet(f"color: {MAGENTA}; font-size: 9px; font-weight: bold;")
        right_l.addWidget(regime_hdr)
        self._regime_canvas = _PaintWidget(self._paint_regime, min_h=100)
        right_l.addWidget(self._regime_canvas, 1)

        tag_hdr = QLabel("BY SETUP TYPE / TAGS")
        tag_hdr.setStyleSheet(f"color: {MAGENTA}; font-size: 9px; font-weight: bold;")
        right_l.addWidget(tag_hdr)
        self._tag_canvas = _PaintWidget(self._paint_tags, min_h=100)
        right_l.addWidget(self._tag_canvas, 1)

        splitter.addWidget(right_w)
        splitter.setSizes([500, 500])
        root.addWidget(splitter, 1)

    def update_trades(self, trades: list):
        by_hour: dict[int, dict] = defaultdict(lambda: {"wins": 0, "total": 0})
        by_dow:  dict[str, dict] = defaultdict(lambda: {"wins": 0, "total": 0})
        by_regime: dict[str, dict] = defaultdict(lambda: {"wins": 0, "total": 0})
        by_tag:  dict[str, dict] = defaultdict(lambda: {"wins": 0, "total": 0})

        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for t in trades:
            pnl = _get_pnl(t)
            is_win = pnl > 0

            ts_str = _get_attr(t, "entry_time") or _get_attr(t, "timestamp") or ""
            try:
                ts = datetime.fromisoformat(str(ts_str)[:19])
                # Phoenix time = UTC-7
                phx_hour = (ts.hour - 7) % 24
                by_hour[phx_hour]["total"] += 1
                if is_win:
                    by_hour[phx_hour]["wins"] += 1

                dow = dow_names[ts.weekday()]
                by_dow[dow]["total"] += 1
                if is_win:
                    by_dow[dow]["wins"] += 1
            except Exception:
                pass

            regime = _get_attr(t, "regime") or "unknown"
            by_regime[regime]["total"] += 1
            if is_win:
                by_regime[regime]["wins"] += 1

            tags_str = _get_attr(t, "tags") or ""
            for tag in str(tags_str).split(","):
                tag = tag.strip()
                if tag:
                    by_tag[tag]["total"] += 1
                    if is_win:
                        by_tag[tag]["wins"] += 1

        self._by_hour = dict(by_hour)
        self._by_dow = dict(by_dow)
        self._by_regime = dict(by_regime)
        self._by_tag = dict(by_tag)

        for canvas in [self._hour_canvas, self._dow_canvas,
                       self._regime_canvas, self._tag_canvas]:
            canvas.update()

    def _paint_bars(self, painter: QPainter, w: int, h: int,
                    data: dict, key_fn=None):
        """Generic horizontal bar painter for win rate data."""
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))
        if not data:
            painter.setPen(QColor(DIM))
            painter.setFont(_mono(8))
            painter.drawText(4, h // 2, "No data")
            return

        items = sorted(data.items(), key=lambda x: x[0])
        n = len(items)
        bar_h = max(8, (h - 16) // n - 2)
        ML = 52
        MR = 48

        painter.setFont(_mono(7))
        for i, (key, stats) in enumerate(items):
            total = stats.get("total", 0)
            wins = stats.get("wins", 0)
            wr = wins / total if total > 0 else 0

            y = 8 + i * (bar_h + 2)

            # Label
            label = key_fn(key) if key_fn else str(key)
            painter.setPen(QColor(DIM))
            painter.drawText(2, y + bar_h - 2, label[:7])

            # Background bar
            painter.setPen(QColor(BORDER))
            painter.drawRect(ML, y, w - ML - MR, bar_h)

            # Win rate fill
            bar_w = int((w - ML - MR) * wr)
            col = GREEN if wr >= 0.6 else (AMBER if wr >= 0.45 else RED)
            painter.fillRect(ML + 1, y + 1, max(0, bar_w - 2), bar_h - 2, QColor(col))

            # Stats text
            painter.setPen(QColor(WHITE))
            painter.drawText(w - MR + 4, y + bar_h - 2,
                             f"{wr * 100:.0f}% ({total})")

        # 50% line
        x50 = ML + int((w - ML - MR) * 0.5)
        painter.setPen(QPen(QColor(DIM), 1, Qt.PenStyle.DashLine))
        painter.drawLine(x50, 4, x50, h - 4)

    def _paint_hour(self, painter: QPainter, w: int, h: int):
        self._paint_bars(painter, w, h, self._by_hour,
                         key_fn=lambda k: f"{k:02d}:00")

    def _paint_dow(self, painter: QPainter, w: int, h: int):
        dow_order = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4}
        sorted_dow = {k: v for k, v in
                      sorted(self._by_dow.items(),
                             key=lambda x: dow_order.get(x[0], 9))}
        self._paint_bars(painter, w, h, sorted_dow)

    def _paint_regime(self, painter: QPainter, w: int, h: int):
        self._paint_bars(painter, w, h, self._by_regime)

    def _paint_tags(self, painter: QPainter, w: int, h: int):
        top_tags = dict(sorted(self._by_tag.items(),
                                key=lambda x: x[1].get("total", 0),
                                reverse=True)[:12])
        self._paint_bars(painter, w, h, top_tags)


# ─────────────────────────────────────────────────────────────────────────────
# PnLHistogramWidget
# ─────────────────────────────────────────────────────────────────────────────

class PnLHistogramWidget(QWidget):
    """Distribution histogram of P&L per trade."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pnls: list[float] = []
        self._bins: list[tuple[float, float, int]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        hdr = QLabel("P&L DISTRIBUTION")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        self._canvas = _PaintWidget(self._paint, min_h=200)
        root.addWidget(self._canvas, 1)

    def update_trades(self, trades: list):
        self._pnls = [_get_pnl(t) for t in trades if _get_pnl(t) != 0]
        if self._pnls:
            self._compute_bins()
        self._canvas.update()

    def _compute_bins(self):
        n_bins = 20
        mn, mx = min(self._pnls), max(self._pnls)
        if mn == mx:
            self._bins = [(mn, mx, len(self._pnls))]
            return
        step = (mx - mn) / n_bins
        bins = []
        for i in range(n_bins):
            lo = mn + i * step
            hi = lo + step
            count = sum(1 for p in self._pnls if lo <= p < hi)
            bins.append((lo, hi, count))
        # last bin inclusive
        if bins:
            lo, hi, _ = bins[-1]
            count = sum(1 for p in self._pnls if lo <= p <= hi)
            bins[-1] = (lo, hi, count)
        self._bins = bins

    def _paint(self, painter: QPainter, w: int, h: int):
        ML, MR, MT, MB = 50, 10, 10, 28
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))

        if not self._bins:
            painter.setPen(QColor(DIM))
            painter.setFont(_mono(9))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                             "NO TRADE DATA")
            return

        cw, ch = w - ML - MR, h - MT - MB
        max_count = max(b[2] for b in self._bins) or 1
        n_bins = len(self._bins)
        bin_w = cw / n_bins

        zero_bin_x = None

        for i, (lo, hi, count) in enumerate(self._bins):
            bx = ML + i * bin_w
            bh = int((count / max_count) * ch)
            by = MT + ch - bh

            # Color by positive/negative
            col = GREEN if lo >= 0 else RED
            mid = (lo + hi) / 2
            if abs(mid) < abs(self._bins[1][1] - self._bins[0][1]) * 0.5:
                col = AMBER

            c = QColor(col)
            c.setAlpha(180)
            painter.fillRect(int(bx + 1), by, max(1, int(bin_w - 2)), bh, c)

            # Border
            painter.setPen(QPen(QColor(col), 1))
            painter.drawRect(int(bx), by, max(1, int(bin_w - 1)), bh)

            # Zero line reference
            if lo <= 0 <= hi:
                zero_bin_x = int(bx + bin_w * (-lo / max(hi - lo, 0.001)))

        # Zero line
        if zero_bin_x:
            painter.setPen(QPen(QColor(WHITE), 2, Qt.PenStyle.DashLine))
            painter.drawLine(zero_bin_x, MT, zero_bin_x, MT + ch)
            painter.setFont(_mono(7))
            painter.setPen(QColor(DIM))
            painter.drawText(zero_bin_x + 3, MT + 12, "$0")

        # X-axis labels
        painter.setFont(_mono(7))
        painter.setPen(QColor(DIM))
        for i in range(0, n_bins + 1, max(1, n_bins // 5)):
            if i < len(self._bins):
                lo, _, _ = self._bins[i]
                x = int(ML + i * bin_w)
                painter.drawText(x - 12, h - 4, f"${lo:.0f}")

        # Y-axis
        for yi in range(3):
            gy = MT + (yi / 2) * ch
            painter.setPen(QPen(QColor(GRID), 1))
            painter.drawLine(ML, int(gy), ML + cw, int(gy))
            count_at = int(max_count * (1 - yi / 2))
            painter.setPen(QColor(DIM))
            painter.drawText(2, int(gy) + 4, str(count_at))

        _scanlines(painter, w, h)


# ─────────────────────────────────────────────────────────────────────────────
# PnLHeatmapWidget — P&L by hour and day
# ─────────────────────────────────────────────────────────────────────────────

class PnLHeatmapWidget(QWidget):
    """Heatmap of average P&L by hour (columns) and day of week (rows)."""

    _DOW  = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    _HOURS = list(range(6, 21))  # 6am–8pm Phoenix (covers RTH + pre/post)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid: dict[tuple[int, int], dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        hdr = QLabel("P&L HEATMAP  (by hour × day of week, Phoenix time)")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        self._canvas = _PaintWidget(self._paint, min_h=180)
        root.addWidget(self._canvas, 1)

        legend = QLabel("Green = profitable  |  Red = losing  |  Brighter = more significant  |  # = trade count")
        legend.setStyleSheet(f"color: {DIM}; font-size: 8px;")
        root.addWidget(legend)

    def update_trades(self, trades: list):
        grid: dict[tuple[int, int], dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0})

        for t in trades:
            pnl = _get_pnl(t)
            ts_str = _get_attr(t, "entry_time") or _get_attr(t, "timestamp") or ""
            try:
                ts = datetime.fromisoformat(str(ts_str)[:19])
                phx_hour = (ts.hour - 7) % 24
                dow = ts.weekday()  # 0=Mon
                if dow < 5:
                    grid[(dow, phx_hour)]["pnl"] += pnl
                    grid[(dow, phx_hour)]["count"] += 1
            except Exception:
                pass

        self._grid = dict(grid)
        self._canvas.update()

    def _paint(self, painter: QPainter, w: int, h: int):
        painter.fillRect(0, 0, w, h, QColor(BG_PANEL))
        ML = 36  # day labels
        MT = 22  # hour labels
        MR = 4
        MB = 4

        n_hours = len(self._HOURS)
        n_days = len(self._DOW)
        cell_w = (w - ML - MR) / n_hours
        cell_h = (h - MT - MB) / n_days

        # Hour headers
        painter.setPen(QColor(DIM))
        painter.setFont(_mono(7))
        for hi, hour in enumerate(self._HOURS):
            cx = ML + (hi + 0.5) * cell_w
            label = f"{hour:02d}"
            fm = QFontMetrics(painter.font())
            tw = fm.horizontalAdvance(label)
            painter.drawText(int(cx - tw / 2), MT - 4, label)

        # Day labels + cells
        all_pnls = [v["pnl"] for v in self._grid.values() if v["count"] > 0]
        max_abs = max((abs(p) for p in all_pnls), default=1.0)

        for di, dow_name in enumerate(self._DOW):
            cy = MT + di * cell_h

            # Day label
            painter.setPen(QColor(AMBER))
            painter.setFont(_mono(8, bold=True))
            painter.drawText(2, int(cy + cell_h / 2 + 4), dow_name)

            for hi, hour in enumerate(self._HOURS):
                cx = ML + hi * cell_w
                key = (di, hour)
                stats = self._grid.get(key)

                if stats and stats["count"] > 0:
                    pnl = stats["pnl"]
                    count = stats["count"]
                    intensity = min(abs(pnl) / max(max_abs, 0.01), 1.0)

                    if pnl > 0:
                        c = QColor(GREEN)
                    else:
                        c = QColor(RED)
                    c.setAlpha(int(40 + intensity * 180))
                    painter.fillRect(int(cx), int(cy), int(cell_w - 1), int(cell_h - 1), c)

                    # Count text
                    painter.setPen(QColor(WHITE) if intensity > 0.4 else QColor(DIM))
                    painter.setFont(_mono(6))
                    painter.drawText(int(cx + 2), int(cy + cell_h - 4), str(count))
                else:
                    # Empty cell
                    c = QColor(BORDER)
                    c.setAlpha(40)
                    painter.fillRect(int(cx), int(cy), int(cell_w - 1), int(cell_h - 1), c)

        # Grid lines
        painter.setPen(QPen(QColor(BORDER), 1))
        for hi in range(n_hours + 1):
            x = int(ML + hi * cell_w)
            painter.drawLine(x, MT, x, MT + int(n_days * cell_h))
        for di in range(n_days + 1):
            y = int(MT + di * cell_h)
            painter.drawLine(ML, y, ML + int(n_hours * cell_w), y)

        _scanlines(painter, w, h)


class _PaintWidget(QWidget):
    """Generic widget that delegates paintEvent to a callable."""

    def __init__(self, paint_fn, min_h: int = 100, fixed_h: Optional[int] = None, parent=None):
        super().__init__(parent)
        self._paint_fn = paint_fn
        self.setMinimumHeight(min_h)
        if fixed_h:
            self.setFixedHeight(fixed_h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_fn(painter, self.width(), self.height())
        painter.end()


class AutonomyInsightsWidget(QWidget):
    """Autonomy dashboard for optimizer activity, validation, and reports."""

    def __init__(self, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        hdr = QLabel("AUTONOMY INSIGHTS")
        hdr.setStyleSheet(f"color: {CYAN}; font-size: 11px; font-weight: bold;")
        root.addWidget(hdr)

        summary_row = QHBoxLayout()
        self._summary_lbl = QLabel("Total: 0  Pending: 0  Validated: 0  RolledBack: 0")
        self._summary_lbl.setStyleSheet(f"color: {WHITE}; font-size: 10px;")
        summary_row.addWidget(self._summary_lbl)
        summary_row.addStretch()
        root.addLayout(summary_row)

        self._active_lbl = QLabel("Active Context: -")
        self._active_lbl.setStyleSheet(f"color: {GREEN}; font-size: 10px;")
        root.addWidget(self._active_lbl)

        self._policy_lbl = QLabel("Runtime Policy: -")
        self._policy_lbl.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        root.addWidget(self._policy_lbl)

        self._changes = QTableWidget()
        self._changes.setColumnCount(8)
        self._changes.setHorizontalHeaderLabels([
            "Time", "Context", "Target", "Old", "New", "Validation", "Reverted", "Rationale",
        ])
        self._changes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._changes.verticalHeader().setVisible(False)
        self._changes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._changes.setAlternatingRowColors(True)
        self._changes.setMinimumHeight(180)
        root.addWidget(self._changes, 2)

        rep_hdr = QLabel("RECENT AUTONOMY REPORTS")
        rep_hdr.setStyleSheet(f"color: {MAGENTA}; font-size: 10px; font-weight: bold;")
        root.addWidget(rep_hdr)

        self._reports = QTableWidget()
        self._reports.setColumnCount(4)
        self._reports.setHorizontalHeaderLabels(["Time", "Type", "Period", "Summary"])
        self._reports.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._reports.verticalHeader().setVisible(False)
        self._reports.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._reports.setMinimumHeight(120)
        root.addWidget(self._reports, 1)

        self._notes = QTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setMinimumHeight(70)
        self._notes.setStyleSheet(
            f"background: {BG_PANEL}; color: {DIM}; border: 1px solid {BORDER};"
        )
        root.addWidget(self._notes)

    def update_data(self, db) -> None:
        if db is None:
            return
        changes = db.get_autonomous_changes(limit=40)
        pending = db.get_pending_autonomous_changes()
        reports = db.get_autonomy_reports(limit=10)
        state_rows = db.get_agent_knowledge("AutonomousOptimizer", "optimizer_state")

        active_state = {}
        policy_map = {}
        for row in state_rows:
            key = row.get("key")
            value = row.get("value", {})
            if key == "active_runtime_context" and isinstance(value, dict):
                active_state = value
            elif key == "context_policy_map" and isinstance(value, dict):
                policy_map = value

        validated = sum(1 for c in changes if c.get("validation_status") == "validated")
        rolled_back = sum(1 for c in changes if int(c.get("reverted") or 0) == 1)
        self._summary_lbl.setText(
            f"Total: {len(changes)}  Pending: {len(pending)}  "
            f"Validated: {validated}  RolledBack: {rolled_back}"
        )

        context = active_state.get("context", "-")
        policy = active_state.get("policy", {}) if isinstance(active_state.get("policy", {}), dict) else {}
        p_conf = policy.get("min_confidence")
        p_eval = policy.get("eval_interval_ms")
        p_agree = policy.get("min_strategies_agree")
        p_cool = policy.get("signal_cooldown_sec")
        p_conf_txt = f"{float(p_conf):.3f}" if p_conf is not None else "-"
        p_eval_txt = str(int(p_eval)) if p_eval is not None else "-"
        p_agree_txt = str(int(p_agree)) if p_agree is not None else "-"
        p_cool_txt = str(int(p_cool)) if p_cool is not None else "-"
        self._active_lbl.setText(f"Active Context: {context}")
        self._policy_lbl.setText(
            f"Runtime Policy: min_confidence={p_conf_txt}  "
            f"eval_interval_ms={p_eval_txt}  min_strategies_agree={p_agree_txt}  "
            f"cooldown_sec={p_cool_txt}"
        )

        self._changes.setRowCount(len(changes))
        for r, change in enumerate(changes):
            row_vals = [
                _fmt_ts(change.get("timestamp", 0.0)),
                str(change.get("context_key") or "global"),
                str(change.get("target") or "-"),
                str(change.get("old_value") or "-"),
                str(change.get("new_value") or "-"),
                str(change.get("validation_status") or "pending"),
                "YES" if int(change.get("reverted") or 0) == 1 else "NO",
                str(change.get("rationale") or "-")[:120],
            ]
            for c, val in enumerate(row_vals):
                item = QTableWidgetItem(val)
                if c == 5:
                    status = val.lower()
                    if status == "validated":
                        item.setForeground(QColor(GREEN))
                    elif status == "monitoring":
                        item.setForeground(QColor(AMBER))
                    elif status == "pending":
                        item.setForeground(QColor(CYAN))
                    else:
                        item.setForeground(QColor(RED))
                self._changes.setItem(r, c, item)

        self._reports.setRowCount(len(reports))
        for r, report in enumerate(reports):
            summary = ""
            raw = str(report.get("summary_json") or "")
            if raw:
                try:
                    parsed = json.loads(raw)
                    summary = (
                        f"changes={parsed.get('recent_change_count', 0)} "
                        f"pending={parsed.get('pending_change_count', 0)}"
                    )
                except json.JSONDecodeError:
                    summary = raw[:140]

            for c, val in enumerate([
                _fmt_ts(report.get("timestamp", 0.0)),
                str(report.get("report_type") or "-"),
                str(report.get("period_key") or "-"),
                summary or "-",
            ]):
                self._reports.setItem(r, c, QTableWidgetItem(val))

        note_lines = [
            f"Context policies tracked: {len(policy_map)}",
            f"Last refresh: {_fmt_ts(time.time())}",
        ]
        if context and context != "-":
            note_lines.append(f"Current context key: {context}")
        self._notes.setPlainText("\n".join(note_lines))


# ─────────────────────────────────────────────────────────────────────────────
# AnalyticsDashboard — main widget
# ─────────────────────────────────────────────────────────────────────────────

class AnalyticsDashboard(QWidget):
    """Tabbed analytics dashboard. Auto-refreshes on TRADE_CLOSED events."""

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # Header bar
        hdr = QHBoxLayout()
        title_lbl = QLabel("MES INTEL  |  ANALYTICS DASHBOARD")
        title_lbl.setStyleSheet(f"color: {GREEN}; font-size: 13px; font-weight: bold;")
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        self._refresh_btn = QPushButton("REFRESH")
        self._refresh_btn.setFixedWidth(90)
        self._refresh_btn.clicked.connect(self.refresh_all)
        self._export_btn = QPushButton("EXPORT")
        self._export_btn.setFixedWidth(80)
        self._export_btn.clicked.connect(self._on_export)
        hdr.addWidget(self._refresh_btn)
        hdr.addWidget(self._export_btn)
        root.addLayout(hdr)

        # Tabs
        self._tabs = QTabWidget()
        root.addWidget(self._tabs, 1)

        # EQUITY tab
        eq_w = QWidget()
        eq_l = QVBoxLayout(eq_w)
        eq_l.setContentsMargins(4, 4, 4, 4)
        self.equity_curve = EquityCurveWidget()
        self.drawdown     = DrawdownWidget()
        eq_split = QSplitter(Qt.Orientation.Vertical)
        eq_split.addWidget(self.equity_curve)
        eq_split.addWidget(self.drawdown)
        eq_split.setSizes([300, 160])
        eq_l.addWidget(eq_split)
        self._tabs.addTab(eq_w, "EQUITY")

        # STRATEGIES tab
        st_w = QWidget()
        st_l = QVBoxLayout(st_w)
        st_l.setContentsMargins(4, 4, 4, 4)
        self.strategy_perf = StrategyPerformanceWidget()
        self.weight_evo    = WeightEvolutionWidget()
        st_split = QSplitter(Qt.Orientation.Vertical)
        st_split.addWidget(self.strategy_perf)
        st_split.addWidget(self.weight_evo)
        st_split.setSizes([280, 160])
        st_l.addWidget(st_split)
        self._tabs.addTab(st_w, "STRATEGIES")

        # ML tab (scrollable)
        ml_scroll = QScrollArea()
        ml_scroll.setWidgetResizable(True)
        self.ml_perf = MLPerformanceWidget()
        ml_scroll.setWidget(self.ml_perf)
        self._tabs.addTab(ml_scroll, "ML")

        # CORRELATIONS tab (scrollable)
        corr_scroll = QScrollArea()
        corr_scroll.setWidgetResizable(True)
        self.correlation = CorrelationHeatmapWidget()
        corr_scroll.setWidget(self.correlation)
        self._tabs.addTab(corr_scroll, "CORRELATIONS")

        # METRICS tab — Sharpe, Sortino, VaR, rolling, profit factor
        metrics_scroll = QScrollArea()
        metrics_scroll.setWidgetResizable(True)
        self.trade_metrics = TradeMetricsWidget()
        metrics_scroll.setWidget(self.trade_metrics)
        self._tabs.addTab(metrics_scroll, "METRICS")

        # BREAKDOWN tab — win rate by hour/DOW/regime/tag
        breakdown_w = QWidget()
        breakdown_l = QVBoxLayout(breakdown_w)
        breakdown_l.setContentsMargins(4, 4, 4, 4)
        self.win_rate_breakdown = WinRateBreakdownWidget()
        breakdown_l.addWidget(self.win_rate_breakdown)
        self._tabs.addTab(breakdown_w, "BREAKDOWN")

        # HISTOGRAM tab — P&L distribution
        hist_w = QWidget()
        hist_l = QVBoxLayout(hist_w)
        hist_l.setContentsMargins(4, 4, 4, 4)
        self.pnl_histogram = PnLHistogramWidget()
        hist_l.addWidget(self.pnl_histogram)
        self._tabs.addTab(hist_w, "HISTOGRAM")

        # HEATMAP tab — P&L by hour × day
        heatmap_w = QWidget()
        heatmap_l = QVBoxLayout(heatmap_w)
        heatmap_l.setContentsMargins(4, 4, 4, 4)
        self.pnl_heatmap = PnLHeatmapWidget()
        heatmap_l.addWidget(self.pnl_heatmap)
        self._tabs.addTab(heatmap_w, "HEATMAP")

        # AUTONOMY tab — policy state, recent changes, weekly reports
        autonomy_w = QWidget()
        autonomy_l = QVBoxLayout(autonomy_w)
        autonomy_l.setContentsMargins(4, 4, 4, 4)
        self.autonomy_insights = AutonomyInsightsWidget()
        autonomy_l.addWidget(self.autonomy_insights)
        self._tabs.addTab(autonomy_w, "AUTONOMY")

        # Event bus subscriptions
        bus.subscribe(EventType.TRADE_CLOSED,       self._on_trade_closed)
        bus.subscribe(EventType.WEIGHT_ADJUSTMENT,  self._on_weight_adj)
        bus.subscribe(EventType.ML_TRAINING_COMPLETE, self._on_ml_done)

        # 60-second auto-refresh
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_all)
        self._timer.start(60_000)

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_trade_closed(self, event: Event):
        self.refresh_all()

    def _on_weight_adj(self, event: Event):
        weights = event.data.get("weights", {})
        if weights:
            self.weight_evo.add_weight_event(weights)

    def _on_ml_done(self, event: Event):
        if self._db:
            try:
                self.ml_perf.update_data(self._db.get_model_performance())
            except Exception:
                pass

    def _on_export(self):
        report = self.export_report()
        print(f"[AnalyticsDashboard] export: {report}")

    # ── Public API ───────────────────────────────────────────────────────────

    def refresh_all(self):
        """Load fresh data from db and update all sub-widgets."""
        if not self._db:
            return
        try:
            trades = self._db.get_trades(limit=10000)
            self.equity_curve.update_data(trades)
            self.drawdown.update_data(trades)
            self.correlation.update_data(trades)
            # New analytics tabs
            self.trade_metrics.update_trades(trades)
            self.win_rate_breakdown.update_trades(trades)
            self.pnl_histogram.update_trades(trades)
            self.pnl_heatmap.update_trades(trades)

            if hasattr(self._db, 'get_strategy_scores'):
                scores = self._db.get_strategy_scores()
                self.strategy_perf.update_data(scores)

            if hasattr(self._db, 'get_model_performance'):
                try:
                    perf = self._db.get_model_performance("meta_learner")
                    self.ml_perf.update_data(perf)
                except TypeError:
                    pass  # method signature may vary

            if hasattr(self, "autonomy_insights"):
                self.autonomy_insights.update_data(self._db)
        except Exception as exc:
            print(f"[AnalyticsDashboard] refresh_all error: {exc}")

    def export_report(self) -> dict:
        """Generate a summary dict for external reporting."""
        eq = self.equity_curve
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "equity": {
                "cum_pnl":       eq._cum_pnl[-1] if eq._cum_pnl else 0.0,
                "max_dd_pct":    max(eq._dd_pct) if eq._dd_pct else 0.0,
                "trade_count":   len(eq._cum_pnl),
            },
            "strategies": {
                name: {
                    "win_rate": d.get("win_rate", 0),
                    "avg_pnl":  d.get("avg_pnl", 0),
                    "weight":   d.get("weight", 0),
                }
                for name, d in self.strategy_perf._scores.items()
            },
            "ml_degraded": self.ml_perf._deg_lbl.text().startswith("Status: DEGRADED"),
        }
