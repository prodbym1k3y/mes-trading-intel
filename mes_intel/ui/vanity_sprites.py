"""Drug-themed vanity sprites — animated QPainter widgets floating over the app."""
from __future__ import annotations

import math
import random

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath,
    QFont, QPolygonF, QLinearGradient,
)
from PySide6.QtWidgets import QWidget


class _BaseSprite(QWidget):
    SIZE = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.SubWindow)
        self._t = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self):
        self._t += 1
        self.update()


class Perc30Sprite(_BaseSprite):
    """Actual Perc 30 — small round blue pill with M-stamp on one side, 30 on other.
    Bobbing up/down animation."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bob = math.sin(self._t * 0.15) * 3
        cx, cy = 17.0, 17.0 + bob

        # Pill body — round, light blue (M30 Mallinckrodt color)
        grad = QLinearGradient(cx - 10, cy - 10, cx + 10, cy + 10)
        grad.setColorAt(0.0, QColor(100, 160, 240, 230))
        grad.setColorAt(0.5, QColor(60, 110, 210, 230))
        grad.setColorAt(1.0, QColor(40, 80, 180, 230))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(130, 190, 255, 200), 1.2))
        r = 11
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Score line across middle
        p.setPen(QPen(QColor(180, 210, 255, 160), 0.8))
        p.drawLine(int(cx - r + 2), int(cy), int(cx + r - 2), int(cy))

        # "M" stamp on front face
        font = QFont("Courier New", 6, QFont.Bold)
        p.setFont(font)
        p.setPen(QColor(200, 225, 255, 200))
        p.drawText(QRectF(cx - 5, cy - r + 1, 10, 9), Qt.AlignCenter, "M")

        # "30" below score line
        font2 = QFont("Courier New", 5, QFont.Bold)
        p.setFont(font2)
        p.drawText(QRectF(cx - 6, cy + 1, 12, 8), Qt.AlignCenter, "30")

        # Highlight gleam
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(220, 235, 255, 70)))
        p.drawEllipse(QPointF(cx - 3, cy - 5), 4, 2.5)

        p.end()


class EcstasySprite(_BaseSprite):
    """Tesla ecstasy pill — round white/silver pill with Tesla 'T' logo stamped on it.
    Bouncing animation."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bounce = abs(math.sin(self._t * 0.18)) * 5
        cx, cy = 17.0, 17.0 - bounce

        # Pill body — white/silver like a Tesla pill
        grad = QLinearGradient(cx - 11, cy - 11, cx + 11, cy + 11)
        grad.setColorAt(0.0, QColor(240, 240, 255, 230))
        grad.setColorAt(0.5, QColor(200, 205, 220, 230))
        grad.setColorAt(1.0, QColor(160, 165, 185, 230))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.2))
        r = 11
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Tesla 'T' logo — the distinctive T with wide top bar
        p.setPen(QPen(QColor(200, 20, 20, 230), 1.5))  # Tesla red
        p.setBrush(Qt.NoBrush)
        # Top horizontal bar of T
        p.drawLine(int(cx - 5), int(cy - 4), int(cx + 5), int(cy - 4))
        # Vertical stem
        p.drawLine(int(cx), int(cy - 4), int(cx), int(cy + 4))
        # Small curves at top corners (Tesla T style)
        p.setPen(QPen(QColor(200, 20, 20, 200), 1.0))
        p.drawLine(int(cx - 5), int(cy - 4), int(cx - 4), int(cy - 6))
        p.drawLine(int(cx + 5), int(cy - 4), int(cx + 4), int(cy - 6))

        # Highlight
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 90)))
        p.drawEllipse(QPointF(cx - 3, cy - 5), 5, 3)

        p.end()


class CocaineSprite(_BaseSprite):
    """Clearly visible cocaine — credit card, chopped white lines, rolled bill."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rng = random.Random(self._t // 5)

        # Dark surface (mirror/card)
        p.fillRect(2, 16, 30, 14, QColor(20, 20, 30, 200))
        p.setPen(QPen(QColor(80, 80, 100, 150), 0.5))
        p.drawRect(2, 16, 29, 13)

        # Two white lines (rails)
        line1_w = 20 + rng.randint(-2, 2)
        line2_w = 18 + rng.randint(-2, 2)

        p.setPen(Qt.NoPen)
        # Line 1 — chunky white line
        p.setBrush(QBrush(QColor(255, 255, 255, 240)))
        p.drawRoundedRect(QRectF(4, 19, line1_w, 3), 1, 1)

        # Line 2 — slightly thinner
        p.setBrush(QBrush(QColor(240, 240, 255, 220)))
        p.drawRoundedRect(QRectF(5, 24, line2_w, 2.5), 1, 1)

        # Powder scatter dots around lines
        for _ in range(8):
            sx = rng.uniform(3, 31)
            sy = rng.uniform(17, 29)
            a = rng.randint(80, 200)
            sz = rng.uniform(0.5, 1.5)
            p.setBrush(QBrush(QColor(255, 255, 255, a)))
            p.drawEllipse(QPointF(sx, sy), sz, sz)

        # Rolled bill (top, diagonal)
        p.setPen(QPen(QColor(100, 160, 80, 200), 1.5))
        p.setBrush(QBrush(QColor(80, 140, 60, 150)))
        # Rolled up tube shape
        p.drawRoundedRect(QRectF(22, 5, 7, 14), 3, 3)

        # Shimmer on line 1
        shimmer_x = 4 + (math.sin(self._t * 0.3) * 0.5 + 0.5) * 18
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 200)))
        p.drawEllipse(QPointF(shimmer_x, 20.5), 1.5, 1.5)

        p.end()


class WeedLeafSprite(_BaseSprite):
    """Green 7-point cannabis leaf swaying, smoke wisps rising."""

    def _petal(self, cx: float, cy: float, angle_deg: float,
                length: float, width_half: float) -> QPainterPath:
        rad = math.radians(angle_deg)
        perp = rad + math.pi / 2
        tip_x = cx + math.sin(rad) * length
        tip_y = cy - math.cos(rad) * length
        left_x = cx + math.sin(perp) * width_half
        left_y = cy - math.cos(perp) * width_half
        right_x = cx - math.sin(perp) * width_half
        right_y = cy + math.cos(perp) * width_half
        path = QPainterPath()
        path.moveTo(cx, cy)
        path.quadTo(left_x + math.sin(rad) * length * 0.6,
                    left_y - math.cos(rad) * length * 0.6,
                    tip_x, tip_y)
        path.quadTo(right_x + math.sin(rad) * length * 0.6,
                    right_y - math.cos(rad) * length * 0.6,
                    cx, cy)
        return path

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        sway = math.sin(self._t * 0.08) * 6
        cx, cy = 17.0, 22.0

        # Smoke wisps
        for i in range(4):
            phase = (self._t + i * 7) % 28
            smoke_y = cy - 12 - phase * 0.45
            alpha = max(0, int(160 - phase * 6))
            wx = cx + math.sin(self._t * 0.04 + i * 1.1) * 2.5
            if alpha > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(150, 150, 150, alpha)))
                p.drawEllipse(QPointF(wx, smoke_y), 1.5 + i * 0.3, 1.5 + i * 0.3)

        # Stem
        p.setPen(QPen(QColor(30, 110, 30, 200), 1.5))
        p.drawLine(int(cx), int(cy), int(cx), int(cy + 6))

        # 7 petals (1 center top + 2 pairs each side)
        green = QColor(25, 155, 45, 210)
        edge = QColor(15, 100, 25, 180)
        p.setBrush(QBrush(green))
        p.setPen(QPen(edge, 0.5))

        base_angles = [sway, sway - 35, sway + 35, sway - 65, sway + 65, sway - 90, sway + 90]
        lengths =     [12,   10,        10,        8,         8,         6,         6]
        widths =      [2.5,  2.2,       2.2,       2.0,       2.0,       1.8,       1.8]
        for angle, length, width in zip(base_angles, lengths, widths):
            path = self._petal(cx, cy, angle, length, width)
            p.drawPath(path)

        p.end()


class LeanSprite(_BaseSprite):
    """White double cup (styrofoam) with purple lean and purple vapor."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Outer cup (bigger, behind) — white styrofoam
        outer_cup = QPolygonF([
            QPointF(6, 8), QPointF(26, 8),
            QPointF(24, 28), QPointF(8, 28),
        ])
        p.setBrush(QBrush(QColor(240, 240, 245, 220)))
        p.setPen(QPen(QColor(180, 180, 190, 200), 1))
        p.drawPolygon(outer_cup)

        # Inner cup (front) — slightly offset and smaller, also white
        inner_cup = QPolygonF([
            QPointF(8, 8), QPointF(24, 8),
            QPointF(22, 27), QPointF(10, 27),
        ])
        p.setBrush(QBrush(QColor(255, 255, 255, 240)))
        p.setPen(QPen(QColor(200, 200, 210, 200), 0.8))
        p.drawPolygon(inner_cup)

        # Purple lean liquid inside (fill from bottom)
        lean_top = 20
        lean_poly = QPolygonF([
            QPointF(10.5, lean_top), QPointF(21.5, lean_top),
            QPointF(22, 27), QPointF(10, 27),
        ])
        p.setBrush(QBrush(QColor(110, 30, 180, 200)))
        p.setPen(Qt.NoPen)
        p.drawPolygon(lean_poly)

        # Liquid surface shimmer
        p.setPen(QPen(QColor(180, 100, 255, 120), 0.8))
        p.drawLine(int(10.5), lean_top, int(21.5), lean_top)

        # White lid on top
        p.setBrush(QBrush(QColor(245, 245, 250, 230)))
        p.setPen(QPen(QColor(180, 180, 190, 200), 1))
        p.drawRoundedRect(QRectF(7, 6, 18, 3), 1, 1)

        # Straw — red/white striped look
        straw_sway = math.sin(self._t * 0.07) * 1.5
        sx = 18 + straw_sway
        p.setPen(QPen(QColor(220, 60, 60, 200), 1.5))
        p.drawLine(int(sx), 0, int(sx - 1), 7)

        # Vapor wisps (purple) rising
        for i in range(3):
            phase = (self._t + i * 9) % 22
            vx = 16 + math.sin(self._t * 0.05 + i * 1.3) * 3
            vy = 5 - phase * 0.4
            valpha = max(0, int(140 - phase * 7))
            if valpha > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor(140, 60, 220, valpha)))
                r = 1.2 + i * 0.4
                p.drawEllipse(QPointF(vx, vy), r, r)

        # Cup ridge lines (styrofoam texture)
        p.setPen(QPen(QColor(200, 200, 210, 80), 0.5))
        for ridge_y in [12, 15, 18]:
            ridge_left = 8 + (ridge_y - 8) * 0.2
            ridge_right = 24 - (ridge_y - 8) * 0.2
            p.drawLine(int(ridge_left), ridge_y, int(ridge_right), ridge_y)

        p.end()


def create_vanity_sprites(parent_window) -> list:
    """Create all 5 sprites and position them at window corners + center-bottom."""
    sprites = [
        Perc30Sprite(parent_window),
        EcstasySprite(parent_window),
        CocaineSprite(parent_window),
        WeedLeafSprite(parent_window),
        LeanSprite(parent_window),
    ]

    def _reposition():
        w = parent_window.width()
        h = parent_window.height()
        sz = _BaseSprite.SIZE
        positions = [
            (2, 2),                        # top-left
            (w - sz - 2, 2),               # top-right
            (2, h - sz - 2),               # bottom-left
            (w - sz - 2, h - sz - 2),      # bottom-right
            (w // 2 - sz // 2, h - sz - 2),  # center-bottom
        ]
        for sprite, (x, y) in zip(sprites, positions):
            sprite.move(x, y)
            sprite.raise_()
            sprite.show()

    _reposition()

    orig_resize = getattr(parent_window, 'resizeEvent', None)

    def _on_resize(event):
        if orig_resize:
            orig_resize(event)
        _reposition()

    parent_window.resizeEvent = _on_resize

    return sprites
