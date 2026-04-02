"""
Analytics Dashboard — Phase 2
Equity curve, drawdown, strategy performance, ML metrics, correlation heatmap
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy, QGridLayout, QScrollArea, QPushButton, QSplitter,
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

            if hasattr(self._db, 'get_strategy_scores'):
                scores = self._db.get_strategy_scores()
                self.strategy_perf.update_data(scores)

            if hasattr(self._db, 'get_model_performance'):
                try:
                    perf = self._db.get_model_performance("meta_learner")
                    self.ml_perf.update_data(perf)
                except TypeError:
                    pass  # method signature may vary
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
