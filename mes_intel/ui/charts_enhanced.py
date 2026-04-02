"""Enhanced chart rendering — NeonLineChart with glow, zoom/pan, animated tickers."""
from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

from PySide6.QtWidgets import QWidget, QSizePolicy, QToolTip
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal, QPoint
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QLinearGradient,
    QRadialGradient, QPainterPath, QWheelEvent, QMouseEvent,
)


# ─────────────────────────────────────────────
#  Animated number ticker
# ─────────────────────────────────────────────

class AnimatedValue:
    """Smoothly animates a numeric value toward a target."""

    def __init__(self, initial: float = 0.0, speed: float = 0.15):
        self._current = initial
        self._target = initial
        self._speed = speed

    def set_target(self, value: float):
        self._target = value

    def tick(self) -> float:
        diff = self._target - self._current
        self._current += diff * self._speed
        return self._current

    @property
    def value(self) -> float:
        return self._current

    @property
    def target(self) -> float:
        return self._target


# ─────────────────────────────────────────────
#  NeonLineChart
# ─────────────────────────────────────────────

class NeonLineChart(QWidget):
    """Smooth neon polyline with glow + gradient fill. Zoom/pan. Hover tooltips."""

    hovered_value = Signal(float, float)  # x_data, y_data

    MARGIN_L = 62
    MARGIN_R = 16
    MARGIN_T = 28
    MARGIN_B = 36

    def __init__(
        self,
        title: str = '',
        line_color: str = '#00ffcc',
        fill_top_color: str = '#00ffcc44',
        fill_bot_color: str = '#00ffcc00',
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._title = title
        self._line_color = QColor(line_color)
        self._fill_top = QColor(fill_top_color)
        self._fill_bot = QColor(fill_bot_color)
        self._data: List[Tuple[float, float]] = []   # (x, y) pairs
        self._series: List[Tuple[str, List[Tuple[float, float]], str]] = []  # (name, data, color_hex)

        # Zoom/pan state
        self._zoom = 1.0
        self._pan_offset = 0.0   # fraction of data range
        self._dragging = False
        self._drag_start_x = 0
        self._drag_pan_start = 0.0

        # Hover
        self._mouse_x: Optional[int] = None
        self._hover_anim = AnimatedValue()

        # Animated values display
        self._anim_values: List[AnimatedValue] = []
        self._anim_labels: List[str] = []

        self.setMinimumSize(300, 140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.start(33)

    def _tick_anim(self):
        for av in self._anim_values:
            av.tick()
        self._hover_anim.tick()
        self.update()

    def set_data(self, data: List[Tuple[float, float]]):
        """Primary series. data = list of (x, y)."""
        self._data = data
        self._pan_offset = 0.0
        self.update()

    def add_series(self, name: str, data: List[Tuple[float, float]], color: str = '#ff8800'):
        """Additional overlaid series."""
        self._series.append((name, data, color))
        self.update()

    def clear_series(self):
        self._series.clear()
        self.update()

    def add_animated_value(self, label: str, initial: float = 0.0) -> int:
        """Add a ticking animated number. Returns its index."""
        self._anim_values.append(AnimatedValue(initial))
        self._anim_labels.append(label)
        return len(self._anim_values) - 1

    def set_animated_value(self, idx: int, value: float):
        if 0 <= idx < len(self._anim_values):
            self._anim_values[idx].set_target(value)

    def _visible_range(self) -> Tuple[float, float]:
        """Return (x_min, x_max) of the visible window."""
        if len(self._data) < 2:
            return (0.0, 1.0)
        xs = [d[0] for d in self._data]
        full_range = xs[-1] - xs[0]
        visible = full_range / self._zoom
        start = xs[0] + self._pan_offset * full_range
        return (start, start + visible)

    def _map_x(self, x: float, x_min: float, x_max: float, w: int) -> float:
        if x_max == x_min:
            return self.MARGIN_L
        plot_w = w - self.MARGIN_L - self.MARGIN_R
        return self.MARGIN_L + (x - x_min) / (x_max - x_min) * plot_w

    def _map_y(self, y: float, y_min: float, y_max: float, h: int) -> float:
        if y_max == y_min:
            return h / 2
        plot_h = h - self.MARGIN_T - self.MARGIN_B
        return self.MARGIN_T + (1 - (y - y_min) / (y_max - y_min)) * plot_h

    def _visible_data(self) -> List[Tuple[float, float]]:
        if not self._data:
            return []
        x_min, x_max = self._visible_range()
        return [(x, y) for x, y in self._data if x_min <= x <= x_max]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor('#08080f'))

        visible = self._visible_data()
        if len(visible) < 2:
            self._draw_empty(p, w, h)
            p.end()
            return

        ys = [y for _, y in visible]
        y_min, y_max = min(ys), max(ys)
        y_pad = (y_max - y_min) * 0.08 or 1.0
        y_min -= y_pad
        y_max += y_pad

        xs = [x for x, _ in visible]
        x_min, x_max = xs[0], xs[-1]

        self._draw_grid(p, w, h, x_min, x_max, y_min, y_max)
        self._draw_axes(p, w, h, x_min, x_max, y_min, y_max)

        # Extra series
        for name, series_data, color_hex in self._series:
            series_visible = [(x, y) for x, y in series_data if x_min <= x <= x_max]
            if len(series_visible) >= 2:
                self._draw_line(p, series_visible, x_min, x_max, y_min, y_max, w, h,
                                QColor(color_hex), glow=False, fill=False)

        # Primary series with glow + fill
        self._draw_gradient_fill(p, visible, x_min, x_max, y_min, y_max, w, h)
        self._draw_line(p, visible, x_min, x_max, y_min, y_max, w, h,
                        self._line_color, glow=True, fill=False)

        self._draw_hover(p, w, h, visible, x_min, x_max, y_min, y_max)
        self._draw_title(p, w)
        self._draw_animated_values(p, w, h)
        p.end()

    def _draw_empty(self, p: QPainter, w: int, h: int):
        p.setFont(QFont('Courier New', 10))
        p.setPen(QColor('#333344'))
        p.drawText(self.rect(), Qt.AlignCenter, f'{self._title}\n(no data)')

    def _build_path(self, data: List[Tuple[float, float]],
                    x_min: float, x_max: float, y_min: float, y_max: float,
                    w: int, h: int) -> QPainterPath:
        path = QPainterPath()
        for i, (x, y) in enumerate(data):
            px = self._map_x(x, x_min, x_max, w)
            py = self._map_y(y, y_min, y_max, h)
            if i == 0:
                path.moveTo(px, py)
            else:
                # Smooth via cubic bezier
                prev_x, prev_y = data[i - 1]
                ppx = self._map_x(prev_x, x_min, x_max, w)
                ppy = self._map_y(prev_y, y_min, y_max, h)
                cp_x = (ppx + px) / 2
                path.cubicTo(cp_x, ppy, cp_x, py, px, py)
        return path

    def _draw_line(self, p: QPainter, data: List[Tuple[float, float]],
                   x_min: float, x_max: float, y_min: float, y_max: float,
                   w: int, h: int, color: QColor, glow: bool, fill: bool):
        path = self._build_path(data, x_min, x_max, y_min, y_max, w, h)

        if glow:
            # Thick blurred glow layer
            glow_color = QColor(color)
            glow_color.setAlpha(60)
            pen = QPen(glow_color, 8)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

            # Medium glow
            glow_color.setAlpha(100)
            pen.setColor(glow_color)
            pen.setWidth(4)
            p.setPen(pen)
            p.drawPath(path)

        # Crisp bright line
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _draw_gradient_fill(self, p: QPainter, data: List[Tuple[float, float]],
                             x_min: float, x_max: float, y_min: float, y_max: float,
                             w: int, h: int):
        path = self._build_path(data, x_min, x_max, y_min, y_max, w, h)

        # Close to bottom
        last_px = self._map_x(data[-1][0], x_min, x_max, w)
        first_px = self._map_x(data[0][0], x_min, x_max, w)
        bottom = h - self.MARGIN_B
        path.lineTo(last_px, bottom)
        path.lineTo(first_px, bottom)
        path.closeSubpath()

        grad = QLinearGradient(0, self.MARGIN_T, 0, bottom)
        grad.setColorAt(0, self._fill_top)
        grad.setColorAt(1, self._fill_bot)
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawPath(path)

    def _draw_grid(self, p: QPainter, w: int, h: int,
                   x_min: float, x_max: float, y_min: float, y_max: float):
        pen = QPen(QColor('#141428'), 1, Qt.DotLine)
        p.setPen(pen)
        for i in range(6):
            y_frac = i / 5
            y_val = y_min + y_frac * (y_max - y_min)
            py = self._map_y(y_val, y_min, y_max, h)
            p.drawLine(self.MARGIN_L, int(py), w - self.MARGIN_R, int(py))
        for i in range(7):
            x_frac = i / 6
            x_val = x_min + x_frac * (x_max - x_min)
            px = self._map_x(x_val, x_min, x_max, w)
            p.drawLine(int(px), self.MARGIN_T, int(px), h - self.MARGIN_B)

    def _draw_axes(self, p: QPainter, w: int, h: int,
                   x_min: float, x_max: float, y_min: float, y_max: float):
        p.setFont(QFont('Courier New', 7))
        # Y axis labels
        for i in range(6):
            frac = i / 5
            val = y_min + frac * (y_max - y_min)
            py = int(self._map_y(val, y_min, y_max, h))
            p.setPen(QColor('#44aa88'))
            p.drawText(2, py - 6, self.MARGIN_L - 4, 14, Qt.AlignRight, f'{val:,.1f}')

        # X axis ticks
        p.setPen(QColor('#335555'))
        for i in range(7):
            frac = i / 6
            val = x_min + frac * (x_max - x_min)
            px = int(self._map_x(val, x_min, x_max, w))
            py = h - self.MARGIN_B + 2
            label = time.strftime('%H:%M', time.localtime(val)) if val > 1e9 else f'{val:.0f}'
            p.drawText(px - 20, py, 40, 14, Qt.AlignCenter, label)

        # Axis border
        p.setPen(QPen(QColor('#1a2a2a'), 1))
        p.drawLine(self.MARGIN_L, self.MARGIN_T, self.MARGIN_L, h - self.MARGIN_B)
        p.drawLine(self.MARGIN_L, h - self.MARGIN_B, w - self.MARGIN_R, h - self.MARGIN_B)

    def _draw_hover(self, p: QPainter, w: int, h: int,
                    data: List[Tuple[float, float]],
                    x_min: float, x_max: float, y_min: float, y_max: float):
        if self._mouse_x is None or not data:
            return
        mx = self._mouse_x

        # Find nearest point
        best = min(data, key=lambda d: abs(self._map_x(d[0], x_min, x_max, w) - mx))
        bx = self._map_x(best[0], x_min, x_max, w)
        by = self._map_y(best[1], y_min, y_max, h)

        # Vertical crosshair
        p.setPen(QPen(QColor('#00ffcc44'), 1, Qt.DashLine))
        p.drawLine(int(bx), self.MARGIN_T, int(bx), h - self.MARGIN_B)
        p.drawLine(self.MARGIN_L, int(by), w - self.MARGIN_R, int(by))

        # Dot
        dot_color = QColor('#00ffcc')
        p.setBrush(QBrush(dot_color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(bx, by), 4, 4)

        # Tooltip box
        val_str = f'{best[1]:,.2f}'
        time_str = time.strftime('%H:%M:%S', time.localtime(best[0])) if best[0] > 1e9 else f'{best[0]:.1f}'
        lines = [val_str, time_str]
        box_w, box_h = 90, 32
        bx2 = int(bx) + 8 if bx + box_w + 8 < w else int(bx) - box_w - 8
        by2 = int(by) - 20
        p.fillRect(bx2, by2, box_w, box_h, QColor(8, 8, 20, 220))
        p.setPen(QColor('#00ffcc'))
        p.drawRect(bx2, by2, box_w, box_h)
        p.setFont(QFont('Courier New', 8))
        for i, line in enumerate(lines):
            p.drawText(bx2 + 4, by2 + 10 + i * 14, line)

    def _draw_title(self, p: QPainter, w: int):
        if not self._title:
            return
        p.setFont(QFont('Courier New', 9, QFont.Bold))
        p.setPen(QColor('#00ffcc88'))
        p.drawText(self.MARGIN_L + 4, self.MARGIN_T - 6, self._title)

    def _draw_animated_values(self, p: QPainter, w: int, h: int):
        if not self._anim_values:
            return
        p.setFont(QFont('Courier New', 9, QFont.Bold))
        x = w - self.MARGIN_R - 80
        y = self.MARGIN_T + 4
        for i, (av, label) in enumerate(zip(self._anim_values, self._anim_labels)):
            val = av.value
            delta = val - av.target
            color = QColor('#00ff88') if val >= 0 else QColor('#ff4444')
            p.setPen(color)
            p.drawText(x, y + i * 16, f'{label}: {val:+,.1f}')

    # ── Zoom / Pan ───────────────────────────

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 0.87
        self._zoom = max(1.0, min(self._zoom * factor, 50.0))
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start_x = event.x()
            self._drag_pan_start = self._pan_offset

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent):
        self._mouse_x = event.x()
        if self._dragging and self._data:
            dx = event.x() - self._drag_start_x
            w = self.width() - self.MARGIN_L - self.MARGIN_R
            xs = [d[0] for d in self._data]
            full_range = xs[-1] - xs[0] or 1.0
            visible_frac = 1.0 / self._zoom
            pan_delta = -dx / w * visible_frac
            new_pan = self._drag_pan_start + pan_delta
            max_pan = 1.0 - visible_frac
            self._pan_offset = max(0.0, min(new_pan, max_pan))
        self.update()

    def leaveEvent(self, event):
        self._mouse_x = None
        self.update()


# ─────────────────────────────────────────────
#  Convenience factory functions
# ─────────────────────────────────────────────

def make_equity_chart(parent: Optional[QWidget] = None) -> NeonLineChart:
    return NeonLineChart(
        title='EQUITY CURVE',
        line_color='#00ffcc',
        fill_top_color='#00ffcc33',
        fill_bot_color='#00ffcc00',
        parent=parent,
    )


def make_drawdown_chart(parent: Optional[QWidget] = None) -> NeonLineChart:
    chart = NeonLineChart(
        title='DRAWDOWN',
        line_color='#ff4466',
        fill_top_color='#ff446633',
        fill_bot_color='#ff446600',
        parent=parent,
    )
    return chart


def make_delta_chart(parent: Optional[QWidget] = None) -> NeonLineChart:
    return NeonLineChart(
        title='CUMULATIVE DELTA',
        line_color='#ffaa00',
        fill_top_color='#ffaa0033',
        fill_bot_color='#ffaa0000',
        parent=parent,
    )
