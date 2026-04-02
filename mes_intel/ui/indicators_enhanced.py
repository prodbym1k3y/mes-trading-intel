"""Enhanced indicator widgets for the MES Trading Intelligence UI.

Provides visual indicator components for each agent section:
  - SIGNALS:    RSI gauge, MACD histogram, Bollinger width, ATR, Stochastic, ADX
  - ORDER FLOW: Cumulative delta chart, Volume ROC, Bid/Ask ratio, DOM depth,
                Aggressive trade ratio
  - JOURNAL:    Win rate gauge, Profit factor, R:R, Sharpe, Max drawdown, Expectancy
  - META:       Team score trend, Strategy weight pie, Agent contribution bars,
                Learning rate graph, Prediction accuracy trend

Also includes TickingNumber (animated count-up/count-down on value change).
"""
from __future__ import annotations

import math
import random
import time
from collections import deque
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame,
    QSizePolicy, QGridLayout,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal as QtSignal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QLinearGradient, QConicalGradient, QRadialGradient,
)

from .theme import COLORS


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _col(hex_str: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_str)
    c.setAlphaF(alpha)
    return c


# ---------------------------------------------------------------------------
# Ticking number label — animates from old to new value
# ---------------------------------------------------------------------------

class TickingNumber(QLabel):
    """A QLabel whose numeric value animates smoothly when changed."""

    def __init__(self, fmt: str = "{:.2f}", suffix: str = "", parent=None):
        super().__init__("—", parent)
        self._fmt = fmt
        self._suffix = suffix
        self._current = 0.0
        self._target = 0.0
        self._animating = False

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_value(self, v: float):
        self._target = v
        if not self._animating:
            self._animating = True
            self._timer.start()

    def _tick(self):
        diff = self._target - self._current
        if abs(diff) < 0.005:
            self._current = self._target
            self._animating = False
            self._timer.stop()
        else:
            self._current += diff * 0.18
        self.setText(self._fmt.format(self._current) + self._suffix)


# ---------------------------------------------------------------------------
# Reusable mini line chart
# ---------------------------------------------------------------------------

class MiniLineChart(QWidget):
    """Compact line chart with neon glow, for use inside indicator strips."""

    def __init__(self, color: str = COLORS["green_bright"],
                 max_points: int = 60, label: str = "", parent=None):
        super().__init__(parent)
        self._color = color
        self._label = label
        self._data: deque[float] = deque(maxlen=max_points)
        self._zero_line = False   # draw zero reference line
        self.setMinimumSize(80, 40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def add_point(self, v: float):
        self._data.append(v)
        self.update()

    def set_data(self, pts: list[float]):
        self._data.clear()
        self._data.extend(pts)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        pts = list(self._data)
        if len(pts) < 2:
            if self._label:
                painter.setPen(_col(COLORS["text_muted"]))
                painter.setFont(QFont("JetBrains Mono", 7))
                painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, self._label)
            painter.end()
            return

        mn = min(pts)
        mx = max(pts)
        span = (mx - mn) or 1.0

        pad = 4
        cw = w - pad * 2
        ch = h - pad * 2 - (12 if self._label else 0)

        def to_xy(i, v):
            px = pad + (i / (len(pts) - 1)) * cw
            py = pad + ch - ((v - mn) / span) * ch
            return QPointF(px, py)

        # Zero reference
        if self._zero_line and mn < 0 < mx:
            zero_y = pad + ch - ((0 - mn) / span) * ch
            painter.setPen(QPen(_col(COLORS["border"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(pad, int(zero_y), w - pad, int(zero_y))

        # Fill area under curve
        fill_color = _col(self._color, 0.12)
        path_pts = [to_xy(i, v) for i, v in enumerate(pts)]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(fill_color))
        bottom_y = pad + ch
        poly_pts = [QPointF(path_pts[0].x(), bottom_y)] + path_pts + [QPointF(path_pts[-1].x(), bottom_y)]
        from PySide6.QtGui import QPolygonF
        painter.drawPolygon(QPolygonF(poly_pts))

        # Glow line (blurred layer)
        glow_col = _col(self._color, 0.3)
        painter.setPen(QPen(glow_col, 3))
        for i in range(len(path_pts) - 1):
            painter.drawLine(path_pts[i], path_pts[i + 1])

        # Main line
        painter.setPen(QPen(_col(self._color), 1.5))
        for i in range(len(path_pts) - 1):
            painter.drawLine(path_pts[i], path_pts[i + 1])

        # Last value dot
        last_pt = path_pts[-1]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_col(self._color)))
        painter.drawEllipse(int(last_pt.x()) - 2, int(last_pt.y()) - 2, 4, 4)

        # Label
        if self._label:
            painter.setFont(QFont("JetBrains Mono", 7))
            painter.setPen(_col(COLORS["text_muted"]))
            painter.drawText(QRectF(0, h - 12, w, 12), Qt.AlignmentFlag.AlignCenter, self._label)

        painter.end()


# ---------------------------------------------------------------------------
# Arc gauge (RSI, Win Rate, etc.)
# ---------------------------------------------------------------------------

class ArcGauge(QWidget):
    """Semi-circular arc gauge, 0–100."""

    def __init__(self, label: str = "RSI",
                 zones=None,   # [(0,30,red),(30,70,amber),(70,100,green)]
                 parent=None):
        super().__init__(parent)
        self._label = label
        self._zones = zones or [
            (0, 30, COLORS["short_color"]),
            (30, 70, COLORS["amber"]),
            (70, 100, COLORS["long_color"]),
        ]
        self._value = 50.0
        self._display = 50.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setMinimumSize(90, 68)
        self.setMaximumSize(160, 110)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def set_value(self, v: float):
        self._value = max(0, min(100, v))
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        diff = self._value - self._display
        if abs(diff) < 0.15:
            self._display = self._value
            self._timer.stop()
        else:
            self._display += diff * 0.15
        self.update()

    def _value_color(self) -> str:
        v = self._display
        for lo, hi, color in self._zones:
            if lo <= v <= hi:
                return color
        return COLORS["text_muted"]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        r = min(w, h * 1.6) * 0.36
        cx = w // 2
        cy = int(h * 0.62)

        # Arc spans 210° (from 210° to 330°, sweeping −240°)
        START_ANGLE = 210   # degrees
        SWEEP = -240        # degrees (clockwise)

        # Background arc
        painter.setPen(QPen(_col(COLORS["border"], 0.6), 5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                        START_ANGLE * 16, SWEEP * 16)

        # Zone arcs
        for lo, hi, color in self._zones:
            a_start = START_ANGLE - lo * abs(SWEEP) / 100
            a_span = -(hi - lo) * abs(SWEEP) / 100
            zone_col = _col(color, 0.25)
            painter.setPen(QPen(zone_col, 5))
            painter.drawArc(int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                            int(a_start * 16), int(a_span * 16))

        # Value arc
        val_angle_start = START_ANGLE * 16
        val_angle_span = int(-self._display * abs(SWEEP) / 100 * 16)
        val_color = _col(self._value_color())
        painter.setPen(QPen(val_color, 5))
        painter.drawArc(int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                        val_angle_start, val_angle_span)

        # Needle
        angle_deg = START_ANGLE - self._display * abs(SWEEP) / 100
        angle_rad = math.radians(angle_deg)
        nx = cx + (r - 7) * math.cos(angle_rad)
        ny = cy - (r - 7) * math.sin(angle_rad)
        painter.setPen(QPen(val_color, 2))
        painter.drawLine(cx, cy, int(nx), int(ny))

        # Hub dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(val_color))
        painter.drawEllipse(cx - 3, cy - 3, 6, 6)

        # Value text
        font_big = QFont("JetBrains Mono", 13)
        font_big.setBold(True)
        painter.setFont(font_big)
        painter.setPen(val_color)
        painter.drawText(
            QRectF(cx - 30, cy - 18, 60, 18),
            Qt.AlignmentFlag.AlignCenter, f"{self._display:.0f}",
        )

        # Label
        font_sm = QFont("JetBrains Mono", 7)
        painter.setFont(font_sm)
        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 13, w, 13), Qt.AlignmentFlag.AlignCenter, self._label)

        painter.end()


# ---------------------------------------------------------------------------
# MACD histogram widget
# ---------------------------------------------------------------------------

class MACDWidget(QWidget):
    """Mini MACD histogram with signal line crossover visualization."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hist: deque[float] = deque(maxlen=30)
        self._macd: deque[float] = deque(maxlen=30)
        self._signal: deque[float] = deque(maxlen=30)
        self.setMinimumSize(90, 55)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def update_data(self, hist: float, macd: float, signal: float):
        self._hist.append(hist)
        self._macd.append(macd)
        self._signal.append(signal)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        hist = list(self._hist)
        if not hist:
            painter.setPen(_col(COLORS["text_muted"]))
            painter.setFont(QFont("JetBrains Mono", 7))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "MACD")
            painter.end()
            return

        pad = 4
        ch = h - pad * 2 - 10
        cw = w - pad * 2
        n = len(hist)

        mn = min(hist)
        mx = max(hist)
        span = (mx - mn) or 1.0
        zero_y = pad + ch - ((0 - mn) / span) * ch

        # Zero line
        painter.setPen(QPen(_col(COLORS["border"]), 1))
        painter.drawLine(pad, int(zero_y), w - pad, int(zero_y))

        # Histogram bars
        bw = max(cw / n - 1, 1)
        for i, v in enumerate(hist):
            bx = pad + i * (cw / n)
            bar_y = pad + ch - ((v - mn) / span) * ch
            bar_h = abs(bar_y - zero_y)
            if v >= 0:
                col = _col(COLORS["delta_positive"], 0.75)
                painter.fillRect(int(bx), int(min(bar_y, zero_y)), int(bw), max(int(bar_h), 1), col)
            else:
                col = _col(COLORS["delta_negative"], 0.75)
                painter.fillRect(int(bx), int(min(bar_y, zero_y)), int(bw), max(int(bar_h), 1), col)

        # Signal line
        sig = list(self._signal)
        if len(sig) >= 2:
            macd_vals = list(self._macd)
            mn2 = min(min(sig), min(macd_vals))
            mx2 = max(max(sig), max(macd_vals))
            span2 = (mx2 - mn2) or 1.0

            def to_y(v):
                return pad + ch - ((v - mn2) / span2) * ch

            painter.setPen(QPen(_col(COLORS["amber"], 0.9), 1.5))
            for i in range(1, len(sig)):
                x1 = int(pad + (i - 1) * cw / n)
                x2 = int(pad + i * cw / n)
                painter.drawLine(x1, int(to_y(sig[i - 1])), x2, int(to_y(sig[i])))

        # Label
        painter.setFont(QFont("JetBrains Mono", 7))
        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 10, w, 10), Qt.AlignmentFlag.AlignCenter, "MACD")

        painter.end()


# ---------------------------------------------------------------------------
# Bid/Ask ratio bar
# ---------------------------------------------------------------------------

class BidAskRatioBar(QWidget):
    """Horizontal bar split into bid (red) and ask (green) proportions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bid_ratio = 0.5
        self.setMinimumSize(80, 28)
        self.setMaximumHeight(44)

    def set_ratio(self, bid_ratio: float):
        self._bid_ratio = max(0.0, min(1.0, bid_ratio))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        pad = 2
        bar_h = h - pad * 2 - 12
        bar_w = w - pad * 2

        ask_w = int(bar_w * (1 - self._bid_ratio))
        bid_w = bar_w - ask_w

        # Ask (green, left)
        painter.fillRect(pad, pad, ask_w, bar_h, _col(COLORS["long_color"], 0.75))
        # Bid (red, right)
        painter.fillRect(pad + ask_w, pad, bid_w, bar_h, _col(COLORS["short_color"], 0.65))

        # Border
        painter.setPen(QPen(_col(COLORS["border"]), 1))
        painter.drawRect(pad, pad, bar_w, bar_h)

        # Labels
        font = QFont("JetBrains Mono", 7)
        painter.setFont(font)
        painter.setPen(_col(COLORS["long_color"]))
        painter.drawText(pad + 2, pad + bar_h - 2, f"A {1-self._bid_ratio:.0%}")
        painter.setPen(_col(COLORS["short_color"]))
        painter.drawText(w - 40, pad + bar_h - 2, f"B {self._bid_ratio:.0%}")

        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 11, w, 11), Qt.AlignmentFlag.AlignCenter, "BID/ASK")

        painter.end()


# ---------------------------------------------------------------------------
# Simple stat display card
# ---------------------------------------------------------------------------

class StatCard(QFrame):
    """Single stat: label + big value + optional sub-label."""

    def __init__(self, label: str, fmt: str = "{:.2f}", color: str = COLORS["green_bright"],
                 parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._fmt = fmt
        self._color = color
        self._value = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(0)

        self._label_w = QLabel(label)
        self._label_w.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 9px;")
        self._label_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label_w)

        self._value_w = TickingNumber(fmt=fmt)
        self._value_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value_w.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {color}; padding: 2px;"
        )
        layout.addWidget(self._value_w)

        self.setMaximumWidth(110)
        self.setMinimumWidth(70)

    def set_value(self, v: float, color: Optional[str] = None):
        self._value = v
        self._value_w.set_value(v)
        if color:
            self._value_w.setStyleSheet(
                f"font-size: 16px; font-weight: bold; color: {color}; padding: 2px;"
            )


# ---------------------------------------------------------------------------
# Strategy weight pie chart
# ---------------------------------------------------------------------------

class StrategyWeightPie(QWidget):
    """Pie chart showing relative strategy weights."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._weights: dict[str, float] = {}
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: (setattr(self, '_phase', (self._phase + 0.03) % (2 * math.pi)),
                                             self.update()))
        self._timer.start(50)
        self.setMinimumSize(120, 120)

    def set_weights(self, weights: dict[str, float]):
        self._weights = weights
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        if not self._weights:
            painter.setPen(_col(COLORS["text_muted"]))
            painter.setFont(QFont("JetBrains Mono", 8))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "WEIGHTS\nno data")
            painter.end()
            return

        total = sum(self._weights.values()) or 1.0
        r = min(w, h) * 0.38
        cx, cy = w // 2, int(h * 0.48)

        SLICE_COLORS = [
            COLORS["green_bright"], COLORS["cyan"], COLORS["amber"],
            COLORS["magenta"], COLORS["blue"], COLORS["long_color"],
            COLORS["short_color"], "#ff6b6b", "#ffd93d",
        ]

        start_angle = 90  # 12 o'clock
        for i, (name, weight) in enumerate(sorted(self._weights.items(),
                                                   key=lambda x: -x[1])):
            frac = weight / total
            sweep = frac * 360
            col = QColor(SLICE_COLORS[i % len(SLICE_COLORS)])
            col.setAlphaF(0.75)
            painter.setPen(QPen(_col(COLORS["bg_panel"]), 1))
            painter.setBrush(QBrush(col))
            painter.drawPie(int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                            int(start_angle * 16), int(-sweep * 16))
            start_angle -= sweep

        # Inner circle (donut hole)
        inner_r = r * 0.42
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_col(COLORS["bg_card"])))
        painter.drawEllipse(int(cx - inner_r), int(cy - inner_r),
                            int(inner_r * 2), int(inner_r * 2))

        # Center text
        painter.setFont(QFont("JetBrains Mono", 8))
        painter.setPen(_col(COLORS["green_bright"]))
        painter.drawText(QRectF(cx - 30, cy - 8, 60, 16), Qt.AlignmentFlag.AlignCenter,
                         f"{len(self._weights)}\nSTRATS")

        # Title
        painter.setFont(QFont("JetBrains Mono", 7))
        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 12, w, 12), Qt.AlignmentFlag.AlignCenter, "STRATEGY WEIGHTS")

        painter.end()


# ---------------------------------------------------------------------------
# Agent contribution bars
# ---------------------------------------------------------------------------

class AgentContributionBars(QWidget):
    """Horizontal bars showing each agent's contribution/score."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict[str, float] = {}
        self.setMinimumSize(150, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, data: dict[str, float]):
        self._data = data
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        if not self._data:
            painter.setPen(_col(COLORS["text_muted"]))
            painter.setFont(QFont("JetBrains Mono", 8))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "no data")
            painter.end()
            return

        items = sorted(self._data.items(), key=lambda x: -abs(x[1]))[:8]
        n = len(items)
        row_h = (h - 14) / max(n, 1)
        max_abs = max(abs(v) for _, v in items) or 1.0

        label_w = 80
        bar_area = w - label_w - 6

        font = QFont("JetBrains Mono", 7)
        painter.setFont(font)

        for i, (name, val) in enumerate(items):
            y = int(i * row_h)
            bh = max(int(row_h - 2), 1)

            painter.setPen(_col(COLORS["text_muted"]))
            painter.drawText(
                QRectF(2, y, label_w - 4, bh),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                name[:12],
            )

            bw = int(abs(val) / max_abs * bar_area)
            col = _col(COLORS["long_color"] if val >= 0 else COLORS["short_color"], 0.75)
            painter.fillRect(label_w + 2, y + 1, bw, bh - 2, col)

            painter.setPen(_col(COLORS["text_white"], 0.8))
            painter.drawText(
                QRectF(label_w + bw + 4, y, 40, bh),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{val:+.2f}",
            )

        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 12, w, 12), Qt.AlignmentFlag.AlignCenter, "AGENT CONTRIBUTION")

        painter.end()


# ---------------------------------------------------------------------------
# DOM depth chart
# ---------------------------------------------------------------------------

class DOMDepthWidget(QWidget):
    """Order book depth chart — bid (green) and ask (red) volume by price."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bids: list[tuple[float, float]] = []   # (price, vol)
        self._asks: list[tuple[float, float]] = []
        self.setMinimumSize(100, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_dom(self, bids: list[tuple[float, float]],
                asks: list[tuple[float, float]]):
        self._bids = sorted(bids, reverse=True)[:10]   # top 10 levels
        self._asks = sorted(asks)[:10]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        all_vols = [v for _, v in self._bids + self._asks]
        if not all_vols:
            painter.setPen(_col(COLORS["text_muted"]))
            painter.setFont(QFont("JetBrains Mono", 7))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "DOM")
            painter.end()
            return

        max_vol = max(all_vols) or 1
        n_rows = 10
        row_h = (h - 14) / (n_rows * 2)
        cx = w // 2

        font = QFont("JetBrains Mono", 6)
        painter.setFont(font)

        # Draw asks (above center, red)
        for i, (price, vol) in enumerate(self._asks[:n_rows]):
            y = int((n_rows - 1 - i) * row_h)
            bw = int((vol / max_vol) * cx * 0.85)
            col = _col(COLORS["short_color"], 0.6)
            painter.fillRect(cx, y, bw, max(int(row_h - 1), 1), col)
            painter.setPen(_col(COLORS["text_muted"]))
            painter.drawText(2, y + int(row_h) - 1, f"{price:.2f}")

        # Draw bids (below center, green)
        for i, (price, vol) in enumerate(self._bids[:n_rows]):
            y = int(n_rows * row_h + i * row_h)
            bw = int((vol / max_vol) * cx * 0.85)
            col = _col(COLORS["long_color"], 0.6)
            painter.fillRect(cx, y, bw, max(int(row_h - 1), 1), col)
            painter.setPen(_col(COLORS["text_muted"]))
            painter.drawText(2, y + int(row_h) - 1, f"{price:.2f}")

        # Center line
        cy = int(n_rows * row_h)
        painter.setPen(QPen(_col(COLORS["cyan"], 0.6), 1))
        painter.drawLine(0, cy, w, cy)

        # Label
        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 12, w, 12), Qt.AlignmentFlag.AlignCenter, "DOM DEPTH")

        painter.end()


# ---------------------------------------------------------------------------
# Win rate gauge (0–100%)
# ---------------------------------------------------------------------------

class WinRateGauge(ArcGauge):
    """Win rate arc gauge, 0–100%."""

    def __init__(self, parent=None):
        super().__init__(
            label="WIN RATE",
            zones=[
                (0, 40, COLORS["short_color"]),
                (40, 55, COLORS["amber"]),
                (55, 100, COLORS["long_color"]),
            ],
            parent=parent,
        )

    def set_win_rate(self, pct: float):
        self.set_value(pct * 100 if pct <= 1.0 else pct)


# ---------------------------------------------------------------------------
# ADX trend strength meter
# ---------------------------------------------------------------------------

class ADXMeter(QWidget):
    """ADX strength bar: 0–100, color bands for weak/moderate/strong trend."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._display = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setMinimumSize(80, 32)
        self.setMaximumHeight(48)

    def set_value(self, v: float):
        self._value = max(0, min(100, v))
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        diff = self._value - self._display
        if abs(diff) < 0.1:
            self._display = self._value
            self._timer.stop()
        else:
            self._display += diff * 0.15
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        pad = 3
        bar_h = h - pad * 2 - 11
        bar_w = w - pad * 2
        fill_w = int(bar_w * self._display / 100)

        # Background
        painter.fillRect(pad, pad, bar_w, bar_h, _col(COLORS["bg_dark"]))

        # Color bands
        bands = [
            (0, 25, COLORS["text_muted"]),
            (25, 50, COLORS["amber"]),
            (50, 75, COLORS["cyan"]),
            (75, 100, COLORS["magenta"]),
        ]
        for lo, hi, color in bands:
            bx = pad + int(lo / 100 * bar_w)
            bw = int((hi - lo) / 100 * bar_w)
            col = _col(color, 0.15)
            painter.fillRect(bx, pad, bw, bar_h, col)

        # Fill to value
        if fill_w > 0:
            v = self._display
            if v < 25:
                col = _col(COLORS["text_muted"], 0.6)
            elif v < 50:
                col = _col(COLORS["amber"], 0.75)
            elif v < 75:
                col = _col(COLORS["cyan"], 0.8)
            else:
                col = _col(COLORS["magenta"], 0.85)
            painter.fillRect(pad, pad, fill_w, bar_h, col)

        # Border
        painter.setPen(QPen(_col(COLORS["border"]), 1))
        painter.drawRect(pad, pad, bar_w, bar_h)

        # Labels
        painter.setFont(QFont("JetBrains Mono", 7))
        painter.setPen(_col(COLORS["text_white"]))
        painter.drawText(pad + 2, pad + bar_h - 2, f"{self._display:.0f}")
        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 11, w, 11), Qt.AlignmentFlag.AlignCenter, "ADX")

        painter.end()


# ---------------------------------------------------------------------------
# Stochastic oscillator
# ---------------------------------------------------------------------------

class StochasticOscillator(QWidget):
    """%K and %D lines on a 0–100 scale with overbought/oversold zones."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._k: deque[float] = deque(maxlen=30)
        self._d: deque[float] = deque(maxlen=30)
        self.setMinimumSize(80, 55)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def update_data(self, k: float, d: float):
        self._k.append(k)
        self._d.append(d)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _col(COLORS["bg_card"]))

        pad = 3
        ch = h - pad * 2 - 10

        def to_y(v):
            return int(pad + ch - (v / 100) * ch)

        # OB/OS zones
        ob_y = to_y(80)
        os_y = to_y(20)
        painter.fillRect(pad, pad, w - pad * 2, ob_y - pad, _col(COLORS["short_color"], 0.06))
        painter.fillRect(pad, os_y, w - pad * 2, ch - (os_y - pad), _col(COLORS["long_color"], 0.06))

        # Zone lines
        painter.setPen(QPen(_col(COLORS["short_color"], 0.3), 1, Qt.PenStyle.DashLine))
        painter.drawLine(pad, ob_y, w - pad, ob_y)
        painter.setPen(QPen(_col(COLORS["long_color"], 0.3), 1, Qt.PenStyle.DashLine))
        painter.drawLine(pad, os_y, w - pad, os_y)

        k_pts = list(self._k)
        d_pts = list(self._d)
        n = max(len(k_pts), 1)

        def to_x(i):
            return int(pad + i * (w - pad * 2) / max(n - 1, 1))

        # %D line (amber, dashed)
        if len(d_pts) >= 2:
            painter.setPen(QPen(_col(COLORS["amber"], 0.8), 1, Qt.PenStyle.DashLine))
            for i in range(1, len(d_pts)):
                painter.drawLine(to_x(i - 1), to_y(d_pts[i - 1]),
                                 to_x(i), to_y(d_pts[i]))

        # %K line (cyan, solid)
        if len(k_pts) >= 2:
            painter.setPen(QPen(_col(COLORS["cyan"], 0.9), 1.5))
            for i in range(1, len(k_pts)):
                painter.drawLine(to_x(i - 1), to_y(k_pts[i - 1]),
                                 to_x(i), to_y(k_pts[i]))

        # Label
        painter.setFont(QFont("JetBrains Mono", 7))
        painter.setPen(_col(COLORS["text_muted"]))
        painter.drawText(QRectF(0, h - 10, w, 10), Qt.AlignmentFlag.AlignCenter, "STOCH")

        painter.end()


# ---------------------------------------------------------------------------
# Indicator strip containers
# ---------------------------------------------------------------------------

class SignalsIndicatorStrip(QFrame):
    """Horizontal row of indicators for the SIGNALS tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setMaximumHeight(120)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # RSI gauge
        self.rsi = ArcGauge("RSI")
        layout.addWidget(self.rsi)

        # MACD
        self.macd = MACDWidget()
        layout.addWidget(self.macd, 1)

        # Stochastic
        self.stoch = StochasticOscillator()
        layout.addWidget(self.stoch, 1)

        # ADX
        self.adx = ADXMeter()
        layout.addWidget(self.adx, 1)

        # BB Width
        bb_frame = QFrame()
        bb_lay = QVBoxLayout(bb_frame)
        bb_lay.setContentsMargins(2, 2, 2, 2)
        bb_lay.setSpacing(1)
        bb_lbl = QLabel("BB WIDTH")
        bb_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8px;")
        bb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bb_width = TickingNumber(fmt="{:.2f}", suffix="")
        self.bb_width.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {COLORS['magenta']};"
        )
        self.bb_width.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bb_lay.addWidget(bb_lbl)
        bb_lay.addWidget(self.bb_width)
        layout.addWidget(bb_frame)

        # ATR
        atr_frame = QFrame()
        atr_lay = QVBoxLayout(atr_frame)
        atr_lay.setContentsMargins(2, 2, 2, 2)
        atr_lay.setSpacing(1)
        atr_lbl = QLabel("ATR")
        atr_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8px;")
        atr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.atr = TickingNumber(fmt="{:.2f}", suffix="")
        self.atr.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {COLORS['cyan']};"
        )
        self.atr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        atr_lay.addWidget(atr_lbl)
        atr_lay.addWidget(self.atr)
        layout.addWidget(atr_frame)


class OrderFlowIndicatorStrip(QFrame):
    """Indicator row for the FOOTPRINT / ORDER FLOW tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setMaximumHeight(100)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Cumulative delta chart
        self.cum_delta = MiniLineChart(COLORS["cyan"], label="CUM Δ")
        self.cum_delta.setMinimumWidth(100)
        self.cum_delta._zero_line = True
        layout.addWidget(self.cum_delta, 2)

        # Volume ROC
        vol_frame = QFrame()
        vol_lay = QVBoxLayout(vol_frame)
        vol_lay.setContentsMargins(2, 2, 2, 2)
        vol_lay.setSpacing(1)
        vol_lbl = QLabel("VOL ROC")
        vol_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8px;")
        vol_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.vol_roc = TickingNumber(fmt="{:+.1f}", suffix="%")
        self.vol_roc.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {COLORS['amber']};"
        )
        self.vol_roc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vol_lay.addWidget(vol_lbl)
        vol_lay.addWidget(self.vol_roc)
        layout.addWidget(vol_frame)

        # Bid/ask ratio
        self.ba_ratio = BidAskRatioBar()
        layout.addWidget(self.ba_ratio, 1)

        # DOM depth
        self.dom = DOMDepthWidget()
        layout.addWidget(self.dom, 2)

        # Aggressive trade ratio
        agg_frame = QFrame()
        agg_lay = QVBoxLayout(agg_frame)
        agg_lay.setContentsMargins(2, 2, 2, 2)
        agg_lay.setSpacing(1)
        agg_lbl = QLabel("AGG RATIO")
        agg_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8px;")
        agg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.agg_ratio = TickingNumber(fmt="{:.1%}")
        self.agg_ratio.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {COLORS['magenta']};"
        )
        self.agg_ratio.setAlignment(Qt.AlignmentFlag.AlignCenter)
        agg_lay.addWidget(agg_lbl)
        agg_lay.addWidget(self.agg_ratio)
        layout.addWidget(agg_frame)


class JournalIndicatorStrip(QFrame):
    """Stats row for the JOURNAL tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setMaximumHeight(110)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Win rate gauge
        self.win_rate = WinRateGauge()
        layout.addWidget(self.win_rate)

        # Profit factor
        self.profit_factor = StatCard("PROFIT FACTOR", fmt="{:.2f}",
                                      color=COLORS["green_bright"])
        layout.addWidget(self.profit_factor)

        # Average R:R
        self.avg_rr = StatCard("AVG R:R", fmt="{:.1f}R", color=COLORS["cyan"])
        layout.addWidget(self.avg_rr)

        # Sharpe
        self.sharpe = StatCard("SHARPE", fmt="{:.2f}", color=COLORS["amber"])
        layout.addWidget(self.sharpe)

        # Max drawdown
        self.max_dd = StatCard("MAX DD", fmt="${:.0f}", color=COLORS["short_color"])
        layout.addWidget(self.max_dd)

        # Expectancy
        self.expectancy = StatCard("EXPECTANCY", fmt="${:.2f}", color=COLORS["magenta"])
        layout.addWidget(self.expectancy)

        layout.addStretch()

    def update_stats(self, stats: dict):
        """Update from a stats dict (keys: win_rate, profit_factor, etc.)."""
        wr = stats.get("win_rate", 0)
        self.win_rate.set_win_rate(wr)

        pf = stats.get("profit_factor", 0)
        color = COLORS["long_color"] if pf >= 1.5 else (COLORS["amber"] if pf >= 1.0 else COLORS["short_color"])
        self.profit_factor.set_value(pf, color)

        self.avg_rr.set_value(stats.get("avg_rr", 0))
        self.sharpe.set_value(stats.get("sharpe", 0))
        self.max_dd.set_value(abs(stats.get("max_drawdown", 0)))
        self.expectancy.set_value(stats.get("expectancy", 0))


class MetaIndicatorStrip(QFrame):
    """Visual charts row for the META tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setMaximumHeight(160)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Team score trend line
        trend_frame = QFrame()
        trend_frame.setObjectName("card")
        trend_lay = QVBoxLayout(trend_frame)
        trend_lay.setContentsMargins(2, 2, 2, 2)
        lbl = QLabel("TEAM SCORE TREND")
        lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        trend_lay.addWidget(lbl)
        self.team_score_trend = MiniLineChart(COLORS["green_bright"], label="")
        trend_lay.addWidget(self.team_score_trend, 1)
        layout.addWidget(trend_frame, 2)

        # Strategy weight pie
        self.weight_pie = StrategyWeightPie()
        layout.addWidget(self.weight_pie, 2)

        # Prediction accuracy trend
        acc_frame = QFrame()
        acc_frame.setObjectName("card")
        acc_lay = QVBoxLayout(acc_frame)
        acc_lay.setContentsMargins(2, 2, 2, 2)
        lbl2 = QLabel("PREDICTION ACCURACY")
        lbl2.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 8px;")
        lbl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        acc_lay.addWidget(lbl2)
        self.accuracy_trend = MiniLineChart(COLORS["cyan"], label="")
        acc_lay.addWidget(self.accuracy_trend, 1)
        layout.addWidget(acc_frame, 2)

        # Agent contribution bars
        self.agent_bars = AgentContributionBars()
        layout.addWidget(self.agent_bars, 3)
