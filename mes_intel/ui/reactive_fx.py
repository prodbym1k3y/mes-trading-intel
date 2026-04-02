"""Reactive / digitized UI effects — LED ticker, delta bar, waveform, scanline border."""
from __future__ import annotations

import math
import random

from PySide6.QtWidgets import QWidget, QSizePolicy, QTabBar
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QRect, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QLinearGradient


class ScrollingTicker(QWidget):
    """LED-style neon green scrolling text on black background."""

    DEFAULT_TEXT = (
        "◈ MES FUTURES LIVE  ◈  ORDER FLOW ANALYSIS  ◈  SIGNAL ENGINE ARMED  "
        "◈  WATCH THE TAPE  ◈  DELTA DIVERGENCE DETECTED  ◈  STAY DISCIPLINED  "
        "◈  MES INTEL v3.0  ◈  HIGH CONFIDENCE ONLY  ◈  "
    )

    def __init__(self, parent=None, height: int = 18):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._text = self.DEFAULT_TEXT
        self._offset = 0
        self._font = QFont("Courier New", 9, QFont.Bold)
        self._text_width = 0
        self._speed = 2
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def set_text(self, text: str):
        self._text = text + "  ◈  "
        self._text_width = 0  # force recalc

    def append_message(self, msg: str):
        self._text = self._text.rstrip("  ◈  ") + "  ◈  " + msg + "  ◈  "
        self._text_width = 0

    def _tick(self):
        self._offset -= self._speed
        fm = QFontMetrics(self._font)
        if self._text_width == 0:
            self._text_width = fm.horizontalAdvance(self._text)
        if self._offset < -self._text_width:
            self._offset = self.width()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(0, 0, self.width(), self.height(), QColor(0, 0, 0, 255))

        # LED scanline effect
        for y in range(0, self.height(), 2):
            p.fillRect(0, y, self.width(), 1, QColor(0, 20, 0, 40))

        # Neon glow — draw text twice (glow + sharp)
        p.setFont(self._font)

        # Glow pass
        p.setPen(QColor(0, 255, 80, 60))
        p.drawText(self._offset - 1, self.height() - 4, self._text)
        p.drawText(self._offset + 1, self.height() - 4, self._text)

        # Sharp pass
        p.setPen(QColor(0, 255, 80, 230))
        p.drawText(self._offset, self.height() - 4, self._text)

        # Border
        p.setPen(QPen(QColor(0, 180, 60, 100), 1))
        p.drawLine(0, 0, self.width(), 0)
        p.end()


class DeltaBar(QWidget):
    """Horizontal bar shifting red(left)/green(right) based on cumulative delta."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._delta = 0
        self._display_delta = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def set_delta(self, value: int):
        self._delta = max(-1000, min(1000, value))

    def _tick(self):
        # Smooth interpolation
        self._display_delta += (self._delta - self._display_delta) * 0.12
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor(10, 10, 20, 255))

        # Normalized -1..1
        norm = max(-1.0, min(1.0, self._display_delta / 1000.0))
        center = w // 2

        if norm > 0:
            bar_w = int(norm * (w // 2))
            grad = QLinearGradient(center, 0, center + bar_w, 0)
            grad.setColorAt(0, QColor(0, 180, 80, 160))
            grad.setColorAt(1, QColor(0, 255, 100, 220))
            p.fillRect(center, 1, bar_w, h - 2, grad)
        elif norm < 0:
            bar_w = int(-norm * (w // 2))
            grad = QLinearGradient(center, 0, center - bar_w, 0)
            grad.setColorAt(0, QColor(200, 50, 50, 160))
            grad.setColorAt(1, QColor(255, 60, 60, 220))
            p.fillRect(center - bar_w, 1, bar_w, h - 2, grad)

        # Center tick
        p.setPen(QPen(QColor(100, 100, 150, 200), 1))
        p.drawLine(center, 0, center, h)

        p.end()


class WaveformBars(QWidget):
    """10 vertical bars bouncing like a music visualizer, driven by trade velocity."""

    NUM_BARS = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 28)
        self._heights = [0.1] * self.NUM_BARS
        self._targets = [0.1] * self.NUM_BARS
        self._velocity = 0.0
        self._t = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_velocity(self, trades_per_second: float):
        self._velocity = min(1.0, trades_per_second / 10.0)

    def _tick(self):
        self._t += 1
        base = self._velocity
        for i in range(self.NUM_BARS):
            wave = math.sin(self._t * 0.15 + i * 0.7) * base
            noise = random.uniform(-0.05, 0.05) * base
            self._targets[i] = max(0.05, min(1.0, base * 0.5 + abs(wave) + noise))
            self._heights[i] += (self._targets[i] - self._heights[i]) * 0.25
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 0))

        bar_w = w / self.NUM_BARS
        for i, height in enumerate(self._heights):
            bh = int(height * (h - 4))
            x = int(i * bar_w) + 1
            y = h - bh - 2

            hue = int(120 - height * 120)  # green -> yellow -> red
            color = QColor.fromHsv(hue, 220, 200, 200)
            p.fillRect(x, y, int(bar_w) - 1, bh, color)

        p.end()


class ScanlineBorder(QWidget):
    """A bright pixel dot traveling around the border of the widget (Tron light trail)."""

    TRAIL_LEN = 20
    SPEED = 4

    def __init__(self, target: QWidget, parent=None):
        super().__init__(parent or target.parent())
        self._target = target
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._pos = 0  # perimeter position in pixels
        self._perimeter = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(20)
        self._sync_geometry()

    def _sync_geometry(self):
        if self._target:
            self.setGeometry(self._target.geometry())
            w, h = self.width(), self.height()
            self._perimeter = max(1, 2 * (w + h))

    def _tick(self):
        self._sync_geometry()
        self._pos = (self._pos + self.SPEED) % max(1, self._perimeter)
        self.update()

    def _pos_to_point(self, pos: int) -> QPointF:
        w, h = self.width(), self.height()
        if w == 0 or h == 0:
            return QPointF(0, 0)
        pos = pos % (2 * (w + h))
        if pos < w:
            return QPointF(pos, 0)
        pos -= w
        if pos < h:
            return QPointF(w - 1, pos)
        pos -= h
        if pos < w:
            return QPointF(w - 1 - pos, h - 1)
        pos -= w
        return QPointF(0, h - 1 - pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        perim = max(1, self._perimeter)

        for i in range(self.TRAIL_LEN):
            trail_pos = (self._pos - i * 2) % perim
            pt = self._pos_to_point(trail_pos)
            alpha = int(220 * (1 - i / self.TRAIL_LEN))
            size = max(0.5, 3 - i * 0.12)
            color = QColor(0, 220, 255, alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(pt, size, size)

        p.end()


class InfluxIndicator(QWidget):
    """Flashing badge that fires on delta surge or volume spike."""

    WINDOW = 30   # seconds of rolling history
    THRESH = 2.0  # multiplier over rolling avg to trigger

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(140, 28)
        self._state = 'idle'      # 'idle' | 'delta_surge_up' | 'delta_surge_dn' | 'vol_spike'
        self._flash_t = 0
        self._alpha = 0.0
        self._delta_history: list[tuple[float, float]] = []
        self._vol_history: list[tuple[float, float]] = []
        self._font = QFont("Courier New", 8, QFont.Bold)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def push_delta(self, delta_rate: float, positive: bool):
        """Call with abs delta rate and direction. Checks against rolling avg."""
        import time as _time
        now = _time.time()
        self._delta_history.append((now, abs(delta_rate)))
        cutoff = now - self.WINDOW
        self._delta_history = [(t, v) for t, v in self._delta_history if t > cutoff]
        if len(self._delta_history) > 2:
            avg = sum(v for _, v in self._delta_history[:-1]) / max(1, len(self._delta_history) - 1)
            if avg > 0 and abs(delta_rate) > avg * self.THRESH:
                self._trigger('delta_surge_up' if positive else 'delta_surge_dn')

    def push_volume(self, vol_rate: float):
        """Call with volume rate. Checks for spike regardless of direction."""
        import time as _time
        now = _time.time()
        self._vol_history.append((now, vol_rate))
        cutoff = now - self.WINDOW
        self._vol_history = [(t, v) for t, v in self._vol_history if t > cutoff]
        if len(self._vol_history) > 2:
            avg = sum(v for _, v in self._vol_history[:-1]) / max(1, len(self._vol_history) - 1)
            if avg > 0 and vol_rate > avg * self.THRESH and self._state == 'idle':
                self._trigger('vol_spike')

    def _trigger(self, state: str):
        self._state = state
        self._flash_t = 60  # 3 seconds at 50ms
        self._alpha = 1.0

    def _tick(self):
        if self._flash_t > 0:
            self._flash_t -= 1
            self._alpha = max(0.3, abs(math.sin(self._flash_t * 0.25)))
        else:
            self._state = 'idle'
            self._alpha = 0.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if self._state == 'idle':
            p.fillRect(0, 0, w, h, QColor(10, 10, 20, 180))
            p.setPen(QColor(60, 60, 80, 150))
            p.setFont(self._font)
            p.drawText(0, 0, w, h, Qt.AlignCenter, "INFLUX ──")
            p.end()
            return

        alpha_i = int(self._alpha * 255)
        if self._state == 'delta_surge_up':
            color = QColor(0, 255, 65, alpha_i)
            text = "DELTA SURGE ▲"
        elif self._state == 'delta_surge_dn':
            color = QColor(255, 0, 68, alpha_i)
            text = "DELTA SURGE ▼"
        else:
            color = QColor(255, 200, 0, alpha_i)
            text = "VOLUME SPIKE"

        bg = QColor(color.red(), color.green(), color.blue(), int(self._alpha * 80))
        p.fillRect(0, 0, w, h, bg)
        p.setPen(QPen(color, 1.5))
        p.drawRect(1, 1, w - 2, h - 2)
        p.setPen(color)
        p.setFont(self._font)
        p.drawText(0, 0, w, h, Qt.AlignCenter, text)
        p.end()


class NeonTabBar(QTabBar):
    """Custom tab bar — neon glow text, active tab pulses, Tron-style underline.

    Drop-in replacement: tabs.setTabBar(NeonTabBar())
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._font = QFont("Courier New", 9, QFont.Bold)
        self._font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)   # ~17fps — light on CPU
        self.setFont(self._font)

    def _tick(self):
        self._phase = (self._phase + 0.07) % (2 * math.pi)
        self.update()

    def tabSizeHint(self, index: int) -> QSize:
        sz = super().tabSizeHint(index)
        return QSize(max(sz.width(), 90), max(sz.height(), 34))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pulse = 0.55 + 0.35 * math.sin(self._phase)

        for i in range(self.count()):
            rect = self.tabRect(i)
            is_active = (i == self.currentIndex())

            if is_active:
                # Subtle neon tinted background
                bg_alpha = int(pulse * 20)
                p.fillRect(rect, QColor(0, 255, 255, bg_alpha))

                # Bottom glow bar — gradient peaks at center
                bar = QRect(rect.left(), rect.bottom() - 2, rect.width(), 3)
                grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
                grad.setColorAt(0.0, QColor(0, 255, 255, 0))
                grad.setColorAt(0.5, QColor(0, 255, 255, int(pulse * 255)))
                grad.setColorAt(1.0, QColor(0, 255, 255, 0))
                p.fillRect(bar, QBrush(grad))

                # Top accent sliver
                p.fillRect(QRect(rect.left(), rect.top(), rect.width(), 1),
                           QColor(0, 255, 255, int(pulse * 60)))
            else:
                p.fillRect(rect, QColor(5, 5, 8, 255))
                # Faint dim underline
                p.setPen(QPen(QColor(0, 80, 80, 55), 1))
                p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

            # Right separator
            p.setPen(QPen(QColor(0, 80, 80, 70), 1))
            p.drawLine(rect.right(), rect.top() + 5, rect.right(), rect.bottom() - 5)

            # Text rendering
            p.setFont(self._font)
            text = self.tabText(i)
            if is_active:
                # Glow passes (offset draws)
                glow_alpha = int(pulse * 65)
                p.setPen(QColor(0, 255, 255, glow_alpha))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    shifted = QRect(rect.left() + dx, rect.top() + dy,
                                    rect.width(), rect.height())
                    p.drawText(shifted, Qt.AlignCenter, text)
                # Sharp text on top
                p.setPen(QColor(0, 255, 255, 230))
                p.drawText(rect, Qt.AlignCenter, text)
            else:
                p.setPen(QColor(34, 68, 68, 180))
                p.drawText(rect, Qt.AlignCenter, text)

        p.end()


class BreathingBackground(QWidget):
    """Very subtle pulsing overlay — the window breathes between near-black shades.

    Parent it to the main window; it fills the full area with an alpha ~0-10
    cyan tint pulse so the background feels alive without obscuring content.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)   # ~12fps — very slow breathe

    def _tick(self):
        # ~0.015 rad/tick × 12 fps ≈ 0.18 rad/s → ~35s per full cycle
        self._phase = (self._phase + 0.015) % (2 * math.pi)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        alpha = int(pulse * 9)          # 0-9 alpha: barely perceptible
        p.fillRect(0, 0, self.width(), self.height(), QColor(0, 20, 40, alpha))
        p.end()
