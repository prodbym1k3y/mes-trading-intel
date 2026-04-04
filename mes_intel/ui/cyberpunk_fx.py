"""Cyberpunk visual effects for MES Intel — Phase 4.

Provides:
  - MatrixGridBackground: animated matrix/grid behind panels
  - GlitchOverlay: brief glitch distortion on tab transitions
  - ParticleBurst: signal-fire particle explosion
  - HolographicShimmer: shimmering gradient on key numbers
  - NeonBorderAnimation: breathing glow borders tied to volatility
  - CRTIntensifier: enhanced CRT scanlines + vignette
  - NumberTicker: holographic animating number display
"""
from __future__ import annotations

import math
import random
import time
from typing import Optional

from PySide6.QtWidgets import QWidget, QLabel, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QPropertyAnimation, QRect
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QLinearGradient, QRadialGradient, QConicalGradient,
    QPainterPath,
)

from .theme import COLORS


# Katakana + ASCII for matrix effect
_MATRIX_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*"
    "ｦｧｨｩｪｫｬｭｮｯｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ"
    "1234567890.+-×÷<>{}[]|\\/"
)


# ---------------------------------------------------------------------------
# Matrix grid background overlay
# ---------------------------------------------------------------------------

class MatrixGridBackground(QWidget):
    """Animated matrix-style falling characters behind panels.

    Renders at very low opacity so it doesn't obscure content.
    Opacity increases with volatility (set via set_volatility()).
    """

    def __init__(self, parent=None, char_size: int = 11):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._char_h = char_size
        self._char_w = int(char_size * 0.65)
        self._columns: list[dict] = []
        self._base_opacity = 0.045
        self._volatility = 0.0
        self._grid_phase = 0.0
        self._active = True

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(250)   # ~4 fps — background decoration

    def set_volatility(self, v: float):
        self._volatility = max(0.0, min(1.0, v))

    def set_active(self, active: bool):
        self._active = active
        if active:
            self._timer.start(250)
        else:
            self._timer.stop()
            self.update()

    def _step(self):
        if not self._active:
            return

        w, h = self.width(), self.height()
        if w < 20:
            return

        max_cols = max(w // self._char_w, 1)
        spawn_p = 0.08 + 0.12 * self._volatility

        if random.random() < spawn_p and len(self._columns) < max_cols // 2:
            x = random.randint(0, w - self._char_w)
            self._columns.append({
                "x": x,
                "y": random.uniform(-h * 0.3, 0),
                "speed": random.uniform(1.5, 5.0 + self._volatility * 4),
                "length": random.randint(6, 20),
                "chars": [random.choice(_MATRIX_CHARS) for _ in range(25)],
                "mutate_timer": 0,
                "opacity": random.uniform(0.03, 0.10 + self._volatility * 0.08),
            })

        alive = []
        for col in self._columns:
            col["y"] += col["speed"]
            col["mutate_timer"] += 1
            if col["mutate_timer"] >= 4:
                col["mutate_timer"] = 0
                idx = random.randint(0, len(col["chars"]) - 1)
                col["chars"][idx] = random.choice(_MATRIX_CHARS)
            tail_y = col["y"] + col["length"] * self._char_h
            if tail_y < h + self._char_h * 5:
                alive.append(col)

        self._columns = alive
        self._grid_phase = (self._grid_phase + 0.02) % (2 * math.pi)
        self.update()

    def paintEvent(self, event):
        if not self._active or not self._columns:
            return

        painter = QPainter(self)
        font = QFont("JetBrains Mono", self._char_h - 3)
        painter.setFont(font)

        h = self.height()
        op_scale = 1.0 + 3.0 * self._volatility

        for col in self._columns:
            base_op = col["opacity"] * op_scale
            for i in range(col["length"]):
                cy = int(col["y"] - i * self._char_h)
                if cy < -self._char_h or cy > h:
                    continue
                fade = 1.0 - (i / col["length"])
                # Leading char is bright white/green
                if i == 0:
                    color = QColor(200, 255, 210, int(min(base_op * 3, 0.9) * 255))
                else:
                    alpha = int(min(base_op * fade, 1.0) * 255)
                    color = QColor(0, 200, 60, alpha)
                painter.setPen(color)
                char_idx = i % len(col["chars"])
                painter.drawText(int(col["x"]), cy, col["chars"][char_idx])

        painter.end()


# ---------------------------------------------------------------------------
# Glitch overlay — brief glitch on tab transition
# ---------------------------------------------------------------------------

class GlitchOverlay(QWidget):
    """Brief horizontal line glitch effect, triggered on demand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._slices: list[dict] = []
        self._active = False
        self._frame = 0
        self._duration = 8   # frames

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._step)

    def trigger(self):
        """Trigger a glitch flash."""
        self._active = True
        self._frame = 0
        h = self.height()
        w = self.width()

        n_slices = random.randint(4, 12)
        self._slices = []
        for _ in range(n_slices):
            y = random.randint(0, max(h - 20, 1))
            sh = random.randint(2, 18)
            offset = random.randint(-30, 30)
            color_idx = random.choice([
                COLORS["cyan"], COLORS["magenta"], COLORS["green_bright"],
                COLORS["amber"],
            ])
            self._slices.append({
                "y": y, "h": sh, "offset": offset, "color": color_idx,
                "alpha": random.uniform(0.2, 0.55),
            })

        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()

    def _step(self):
        self._frame += 1
        if self._frame >= self._duration:
            self._active = False
            self._timer.stop()
            self.hide()
            return
        # Shuffle slice offsets
        for s in self._slices:
            if random.random() < 0.3:
                s["offset"] = random.randint(-25, 25)
        self.update()

    def paintEvent(self, event):
        if not self._active or not self._slices:
            return

        painter = QPainter(self)
        w = self.width()
        fade = 1.0 - self._frame / self._duration

        for s in self._slices:
            col = QColor(s["color"])
            col.setAlphaF(s["alpha"] * fade)

            # Horizontal offset slice
            painter.fillRect(
                s["offset"], s["y"],
                w, s["h"],
                col,
            )

            # Scanline flicker
            if random.random() < 0.4:
                scan_col = QColor(255, 255, 255)
                scan_col.setAlphaF(0.04 * fade)
                painter.fillRect(0, s["y"], w, 1, scan_col)

        painter.end()


# ---------------------------------------------------------------------------
# Particle burst — fires on new signal
# ---------------------------------------------------------------------------

class ParticleBurst(QWidget):
    """Exploding particle effect, centered on widget or custom point."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._particles: list[dict] = []
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)

    def fire(self, cx: int = 0, cy: int = 0,
             color: str = COLORS["green_bright"], count: int = 60):
        if not cx:
            cx = self.width() // 2
        if not cy:
            cy = self.height() // 2

        colors = [color, COLORS["cyan"], COLORS["amber"], "#ffffff"]
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(2, 12)
            self._particles.append({
                "x": float(cx), "y": float(cy),
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "life": random.uniform(0.6, 1.0),
                "decay": random.uniform(0.018, 0.035),
                "size": random.uniform(1.5, 4.5),
                "color": random.choice(colors),
                "gravity": random.uniform(0.08, 0.18),
            })

        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()

    def _step(self):
        alive = []
        for p in self._particles:
            p["x"] += p["vx"]
            p["vy"] += p["gravity"]
            p["y"] += p["vy"]
            p["vx"] *= 0.97
            p["life"] -= p["decay"]
            if p["life"] > 0:
                alive.append(p)

        self._particles = alive
        if not alive:
            self._timer.stop()
            self.hide()
        self.update()

    def paintEvent(self, event):
        if not self._particles:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        for p in self._particles:
            col = QColor(p["color"])
            col.setAlphaF(max(p["life"], 0))
            painter.setBrush(QBrush(col))
            s = p["size"]
            painter.drawEllipse(QRectF(p["x"] - s / 2, p["y"] - s / 2, s, s))

        painter.end()


# ---------------------------------------------------------------------------
# CRT intensifier
# ---------------------------------------------------------------------------

class CRTIntensifier(QWidget):
    """Enhanced CRT effect: denser scanlines + corner vignette + noise."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._noise_frame = 0
        self._noise_cells: list[tuple[int, int, int]] = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)   # 2 fps noise flicker

    def _tick(self):
        self._noise_frame += 1
        # Regenerate noise positions
        w, h = self.width(), self.height()
        if w > 0 and h > 0:
            self._noise_cells = [
                (random.randint(0, w), random.randint(0, h), random.randint(1, 3))
                for _ in range(random.randint(0, 12))
            ]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()

        # Scanlines (every 3px)
        painter.setOpacity(0.07)
        pen = QPen(QColor(0, 0, 0))
        pen.setWidth(1)
        painter.setPen(pen)
        y = 0
        while y < h:
            painter.drawLine(0, y, w, y)
            y += 3
        painter.setOpacity(1.0)

        # Vignette corners
        vign = QRadialGradient(w / 2, h / 2, max(w, h) * 0.7)
        vign.setColorAt(0, QColor(0, 0, 0, 0))
        vign.setColorAt(1, QColor(0, 0, 0, 55))
        painter.fillRect(0, 0, w, h, QBrush(vign))

        # Random noise pixels
        painter.setOpacity(0.06)
        for nx, ny, ns in self._noise_cells:
            color = QColor(random.randint(0, 255), random.randint(0, 255),
                           random.randint(0, 255))
            painter.fillRect(nx, ny, ns, ns, color)
        painter.setOpacity(1.0)

        painter.end()


# ---------------------------------------------------------------------------
# Neon underline that animates on hover (for tab labels)
# ---------------------------------------------------------------------------

class AnimatedTabBar(QWidget):
    """Draws an animated neon underline below the active tab.

    This is purely decorative — position it under the actual QTabBar.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedHeight(3)
        self._phase = 0.0
        self._active_x = 0
        self._active_w = 100
        self._color = COLORS["green_bright"]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(120)

    def set_active_tab(self, x: int, w: int):
        self._active_x = x
        self._active_w = w
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.1) % (2 * math.pi)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()

        glow = 0.4 + 0.3 * math.sin(self._phase)

        # Full faint line
        faint = QColor(COLORS["border"])
        faint.setAlphaF(0.3)
        painter.fillRect(0, 1, w, 1, faint)

        # Active tab glow segment
        if self._active_w > 0:
            grad = QLinearGradient(self._active_x, 0,
                                   self._active_x + self._active_w, 0)
            bright = QColor(self._color)
            bright.setAlphaF(glow)
            dim = QColor(self._color)
            dim.setAlphaF(glow * 0.2)
            grad.setColorAt(0, dim)
            grad.setColorAt(0.5, bright)
            grad.setColorAt(1, dim)
            painter.fillRect(self._active_x, 0, self._active_w, 3, QBrush(grad))

        painter.end()
