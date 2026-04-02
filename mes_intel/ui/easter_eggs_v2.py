"""Easter Egg Volume 2 — new animations triggered by keywords, keypresses, and events.

Triggers:
  "MOON"    → rocket shoots across screen
  "GUH"     → red screen flash (loss meme)
  "TENDIES" → chicken tenders rain from top
  "420"     → weed leaf rain + Bob Marley color scheme (5s)
  "PRINT"   → money printer go BRRR
  P&L $100/$500/$1000 → level-up animation
  3 wins in a row → UNSTOPPABLE banner + flames
  3 losses in a row → motivational quote
  price +10pts/1min → HOLY SHIT toast + explosion particles
  idle 5min → matrix takeover (handled in main manager)
  DJ mode → Shift+click time display
  Alt+click chart → Comic Sans 3s joke
"""
from __future__ import annotations

import math
import random
import time
from typing import Optional

from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QLinearGradient,
    QRadialGradient, QPainterPath,
)

from .theme import COLORS


# ── helpers ───────────────────────────────────────────────────────────────────

def _overlay(parent: QWidget) -> tuple[int, int]:
    return parent.width(), parent.height()


# ── ROCKET (MOON) ─────────────────────────────────────────────────────────────

class RocketAnimation(QWidget):
    """Rocket + star trail shoots diagonally across the screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._x = 0.0
        self._y = 0.0
        self._trail: list[dict] = []
        self._active = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def launch(self):
        w, h = self.width(), self.height()
        self._x = float(random.randint(50, w - 50))
        self._y = float(h + 20)
        self._trail = []
        self._active = True
        self.show(); self.raise_()
        self._timer.start(30)

    def _step(self):
        if not self._active:
            return
        self._trail.append({"x": self._x, "y": self._y,
                            "alpha": 1.0, "r": random.randint(3, 8)})
        self._x += random.uniform(-1.5, 1.5)
        self._y -= 12

        for p in self._trail:
            p["alpha"] -= 0.04
        self._trail = [p for p in self._trail if p["alpha"] > 0]

        if self._y < -60:
            self._active = False
            self._timer.stop()
            self.hide()
        self.update()

    def paintEvent(self, event):
        if not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Trail
        for p in self._trail:
            c = QColor(255, int(200 * p["alpha"]), 0)
            c.setAlphaF(p["alpha"])
            painter.setBrush(QBrush(c)); painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(p["x"]) - p["r"] // 2,
                                int(p["y"]) - p["r"] // 2, p["r"], p["r"])

        # Rocket body (simple triangle)
        painter.setBrush(QBrush(QColor(200, 200, 255)))
        painter.setPen(QPen(QColor(COLORS["cyan"]), 1))
        rx, ry = int(self._x), int(self._y)
        path = QPainterPath()
        path.moveTo(rx, ry - 16)
        path.lineTo(rx - 8, ry + 8)
        path.lineTo(rx + 8, ry + 8)
        path.closeSubpath()
        painter.drawPath(path)

        # "TO THE MOON" text fades in
        painter.setFont(QFont("JetBrains Mono", 14, QFont.Weight.Bold))
        painter.setPen(QColor(COLORS["amber"]))
        painter.drawText(QRectF(rx - 100, ry - 40, 200, 24),
                         Qt.AlignmentFlag.AlignCenter, "🚀 TO THE MOON!")
        painter.end()


# ── GUH FLASH ─────────────────────────────────────────────────────────────────

class GuhFlash(QWidget):
    """Dramatic red screen flash — loss meme."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._alpha = 0.0
        self._phase = "in"  # "in" | "hold" | "out"
        self._hold_frames = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def trigger(self):
        self._alpha = 0.0
        self._phase = "in"
        self._hold_frames = 0
        self.show(); self.raise_()
        self._timer.start(30)

    def _step(self):
        if self._phase == "in":
            self._alpha = min(self._alpha + 0.12, 0.75)
            if self._alpha >= 0.75:
                self._phase = "hold"
        elif self._phase == "hold":
            self._hold_frames += 1
            if self._hold_frames > 8:
                self._phase = "out"
        elif self._phase == "out":
            self._alpha = max(self._alpha - 0.06, 0.0)
            if self._alpha <= 0:
                self._timer.stop()
                self.hide()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        c = QColor(200, 0, 0)
        c.setAlphaF(self._alpha)
        painter.fillRect(0, 0, self.width(), self.height(), c)

        if self._phase in ("hold", "out") and self._alpha > 0.2:
            painter.setFont(QFont("Impact", 72, QFont.Weight.Bold))
            c2 = QColor(255, 255, 255)
            c2.setAlphaF(self._alpha)
            painter.setPen(c2)
            painter.drawText(QRectF(0, 0, self.width(), self.height()),
                             Qt.AlignmentFlag.AlignCenter, "GUH")
        painter.end()


# ── TENDIES RAIN ──────────────────────────────────────────────────────────────

class TendiesRain(QWidget):
    """Chicken tenders (🍗) rain from the top."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._tenders: list[dict] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def rain(self, duration_ms: int = 3000):
        w = self.width()
        self._tenders = []
        for _ in range(25):
            self._tenders.append({
                "x": float(random.randint(20, max(w - 20, 21))),
                "y": float(random.randint(-200, 0)),
                "vy": random.uniform(3, 9),
                "rot": random.uniform(0, 360),
                "rot_speed": random.uniform(-5, 5),
                "size": random.randint(18, 36),
                "emoji": random.choice(["🍗", "🍗", "💰", "💵", "🍗"]),
            })
        self.show(); self.raise_()
        self._timer.start(33)
        QTimer.singleShot(duration_ms, self._stop)

    def _stop(self):
        self._timer.stop()
        self.hide()

    def _step(self):
        h = self.height()
        for t in self._tenders:
            t["y"] += t["vy"]
            t["rot"] += t["rot_speed"]
            if t["y"] > h + 40:
                t["y"] = float(random.randint(-80, -20))
                t["x"] = float(random.randint(20, max(self.width() - 20, 21)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for t in self._tenders:
            painter.save()
            painter.translate(t["x"], t["y"])
            painter.rotate(t["rot"])
            painter.setFont(QFont("Arial", int(t["size"])))
            painter.drawText(QRectF(-t["size"], -t["size"], t["size"] * 2, t["size"] * 2),
                             Qt.AlignmentFlag.AlignCenter, t["emoji"])
            painter.restore()
        painter.end()


# ── WEED RAIN (420) ───────────────────────────────────────────────────────────

class WeedRain(QWidget):
    """🌿 rains from top with Bob Marley color scheme for 5 seconds."""

    MARLEY_COLORS = ["#009900", "#FFCC00", "#CC0000", "#009900", "#FFCC00"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._leaves: list[dict] = []
        self._hue_offset = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def rain(self, duration_ms: int = 5000):
        w = self.width()
        self._leaves = []
        for _ in range(30):
            self._leaves.append({
                "x": float(random.randint(0, max(w, 1))),
                "y": float(random.randint(-300, 0)),
                "vy": random.uniform(2, 6),
                "sway": random.uniform(-0.8, 0.8),
                "sway_speed": random.uniform(0.03, 0.08),
                "sway_phase": random.uniform(0, math.pi * 2),
                "t": 0.0,
                "size": random.randint(16, 28),
                "color_idx": random.randint(0, len(self.MARLEY_COLORS) - 1),
            })
        self.show(); self.raise_()
        self._timer.start(33)
        QTimer.singleShot(duration_ms, self._stop)

    def _stop(self):
        self._timer.stop()
        self.hide()

    def _step(self):
        self._hue_offset = (self._hue_offset + 2) % 360
        h = self.height()
        for leaf in self._leaves:
            leaf["t"] += 0.05
            leaf["y"] += leaf["vy"]
            leaf["x"] += leaf["sway"] * math.sin(leaf["sway_phase"] + leaf["t"])
            if leaf["y"] > h + 40:
                leaf["y"] = float(random.randint(-60, -10))
                leaf["x"] = float(random.randint(0, max(self.width(), 1)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        for leaf in self._leaves:
            painter.save()
            painter.translate(leaf["x"], leaf["y"])
            painter.setFont(QFont("Arial", int(leaf["size"])))
            painter.drawText(QRectF(-leaf["size"], -leaf["size"],
                                    leaf["size"] * 2, leaf["size"] * 2),
                             Qt.AlignmentFlag.AlignCenter, "🌿")
            painter.restore()
        painter.end()


# ── MONEY PRINTER (PRINT) ─────────────────────────────────────────────────────

class MoneyPrinterAnimation(QWidget):
    """Money printer go BRRR — dollars fly out of a printer graphic."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._bills: list[dict] = []
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def print_money(self, duration_ms: int = 4000):
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2 + 30
        self._bills = []
        for _ in range(35):
            angle = random.uniform(-math.pi, 0)  # upward arc
            speed = random.uniform(4, 12)
            self._bills.append({
                "x": float(cx + random.randint(-20, 20)),
                "y": float(cy),
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "gravity": 0.3,
                "rot": random.uniform(0, 360),
                "rot_v": random.uniform(-8, 8),
                "alpha": 1.0,
                "size": random.randint(24, 40),
                "emoji": random.choice(["💵", "💰", "💸", "💵", "💵"]),
            })
        self._frame = 0
        self.show(); self.raise_()
        self._timer.start(33)
        QTimer.singleShot(duration_ms, self._stop)

    def _stop(self):
        self._timer.stop()
        self.hide()

    def _step(self):
        self._frame += 1
        for b in self._bills:
            b["x"] += b["vx"]
            b["vy"] += b["gravity"]
            b["y"] += b["vy"]
            b["rot"] += b["rot_v"]
            if self._frame > 40:
                b["alpha"] -= 0.015
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2 + 30

        # Printer graphic
        painter.setFont(QFont("Arial", 36))
        painter.drawText(QRectF(cx - 30, cy - 10, 60, 50),
                         Qt.AlignmentFlag.AlignCenter, "🖨️")

        # BRRR text
        if self._frame < 60:
            alpha = min(1.0, self._frame / 15)
            c = QColor(0, 255, 100)
            c.setAlphaF(alpha)
            painter.setPen(c)
            painter.setFont(QFont("Impact", 28, QFont.Weight.Bold))
            painter.drawText(QRectF(cx - 100, cy - 60, 200, 40),
                             Qt.AlignmentFlag.AlignCenter, "MONEY PRINTER")
            painter.setFont(QFont("Impact", 40, QFont.Weight.Bold))
            c2 = QColor(COLORS["green_bright"])
            c2.setAlphaF(alpha)
            painter.setPen(c2)
            painter.drawText(QRectF(cx - 120, cy - 105, 240, 50),
                             Qt.AlignmentFlag.AlignCenter, "GO BRRR")

        # Bills
        for b in self._bills:
            if b["alpha"] <= 0:
                continue
            painter.save()
            painter.translate(b["x"], b["y"])
            painter.rotate(b["rot"])
            painter.setOpacity(max(b["alpha"], 0))
            painter.setFont(QFont("Arial", b["size"]))
            painter.drawText(QRectF(-b["size"], -b["size"],
                                    b["size"] * 2, b["size"] * 2),
                             Qt.AlignmentFlag.AlignCenter, b["emoji"])
            painter.restore()
        painter.end()


# ── LEVEL UP ──────────────────────────────────────────────────────────────────

class LevelUpAnimation(QWidget):
    """Retro-style LEVEL UP animation when P&L crosses a milestone."""

    MILESTONES = [100, 500, 1000, 2500, 5000]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._milestone = 0
        self._alpha = 0.0
        self._scale = 0.3
        self._phase = "in"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._tracked_pnl = 0.0
        self._last_milestone_idx = -1

    def check_milestone(self, cumulative_pnl: float):
        """Call with running total P&L — triggers if a new milestone is crossed."""
        for i, m in enumerate(self.MILESTONES):
            if cumulative_pnl >= m and i > self._last_milestone_idx:
                self._last_milestone_idx = i
                self._trigger(m)
                break

    def _trigger(self, milestone: int):
        self._milestone = milestone
        self._alpha = 0.0
        self._scale = 0.3
        self._phase = "in"
        self.show(); self.raise_()
        self._timer.start(33)
        QTimer.singleShot(3500, self._begin_fade)

    def _begin_fade(self):
        self._phase = "out"

    def _step(self):
        if self._phase == "in":
            self._alpha = min(self._alpha + 0.08, 1.0)
            self._scale = min(self._scale + 0.06, 1.0)
        elif self._phase == "out":
            self._alpha = max(self._alpha - 0.05, 0.0)
            if self._alpha <= 0:
                self._timer.stop()
                self.hide()
        self.update()

    def paintEvent(self, event):
        if self._alpha <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        painter.save()
        painter.translate(cx, cy)
        painter.scale(self._scale, self._scale)

        # Glow circle
        grad = QRadialGradient(0, 0, 200)
        c1 = QColor(COLORS["green_bright"])
        c1.setAlphaF(self._alpha * 0.3)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(-200, -200, 400, 400)

        # "LEVEL UP!" text
        c2 = QColor(COLORS["amber"])
        c2.setAlphaF(self._alpha)
        painter.setPen(c2)
        painter.setFont(QFont("Impact", 52, QFont.Weight.Bold))
        painter.drawText(QRectF(-220, -70, 440, 70), Qt.AlignmentFlag.AlignCenter, "LEVEL UP!")

        # Milestone text
        c3 = QColor(COLORS["cyan"])
        c3.setAlphaF(self._alpha)
        painter.setPen(c3)
        painter.setFont(QFont("JetBrains Mono", 28, QFont.Weight.Bold))
        painter.drawText(QRectF(-220, 10, 440, 50), Qt.AlignmentFlag.AlignCenter,
                         f"${self._milestone:,} MILESTONE")

        painter.restore()
        painter.end()


# ── UNSTOPPABLE BANNER ────────────────────────────────────────────────────────

class UnstoppableBanner(QWidget):
    """UNSTOPPABLE banner with flame effects for 3+ win streak."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._alpha = 0.0
        self._frame = 0
        self._flames: list[dict] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def show_banner(self, streak: int):
        self._alpha = 0.0
        self._frame = 0
        self._streak = streak
        self._flames = []
        self.show(); self.raise_()
        self._timer.start(33)
        QTimer.singleShot(4000, self._fade_out)

    def _fade_out(self):
        pass  # let _step handle it

    def _step(self):
        self._frame += 1
        # Fade in
        if self._frame < 20:
            self._alpha = self._frame / 20.0
        # Fade out after hold
        elif self._frame > 100:
            self._alpha = max(0, self._alpha - 0.04)
            if self._alpha <= 0:
                self._timer.stop()
                self.hide()
                return

        # Generate flames
        w = self.width()
        if self._frame < 100 and random.random() < 0.6:
            for _ in range(3):
                self._flames.append({
                    "x": float(random.randint(0, w)),
                    "y": float(self.height() // 2 + 40),
                    "vx": random.uniform(-1.5, 1.5),
                    "vy": random.uniform(-5, -2),
                    "life": 1.0,
                    "size": random.randint(8, 20),
                    "hue": random.randint(0, 40),
                })

        for f in self._flames:
            f["x"] += f["vx"]
            f["y"] += f["vy"]
            f["life"] -= 0.04
        self._flames = [f for f in self._flames if f["life"] > 0]
        self.update()

    def paintEvent(self, event):
        if self._alpha <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cy = h // 2

        # Flames
        for f in self._flames:
            hue = int(f["hue"] + (1.0 - f["life"]) * 20)
            c = QColor.fromHsv(hue, 255, 255)
            c.setAlphaF(f["life"] * self._alpha)
            painter.setBrush(QBrush(c))
            painter.setPen(Qt.PenStyle.NoPen)
            s = int(f["size"] * f["life"])
            painter.drawEllipse(int(f["x"]) - s // 2, int(f["y"]) - s // 2, s, s)

        # Banner background
        bg = QColor(0, 0, 0)
        bg.setAlphaF(self._alpha * 0.6)
        painter.fillRect(0, cy - 55, w, 110, bg)

        # Glow border
        pen_c = QColor(COLORS["amber"])
        pen_c.setAlphaF(self._alpha)
        painter.setPen(QPen(pen_c, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(4, cy - 53, w - 8, 106)

        # Text
        c_main = QColor(COLORS["amber"])
        c_main.setAlphaF(self._alpha)
        painter.setPen(c_main)
        painter.setFont(QFont("Impact", 48, QFont.Weight.Bold))
        painter.drawText(QRectF(0, cy - 52, w, 60), Qt.AlignmentFlag.AlignCenter,
                         "UNSTOPPABLE")

        c_sub = QColor(COLORS["green_bright"])
        c_sub.setAlphaF(self._alpha)
        painter.setPen(c_sub)
        painter.setFont(QFont("JetBrains Mono", 18, QFont.Weight.Bold))
        painter.drawText(QRectF(0, cy + 14, w, 36), Qt.AlignmentFlag.AlignCenter,
                         f"{self._streak} WINS IN A ROW")
        painter.end()


# ── HOLY SHIT TOAST ───────────────────────────────────────────────────────────

class HolyShitToast(QWidget):
    """Toast notification + explosion particles when price moves 10+ pts/1min."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._alpha = 0.0
        self._particles: list[dict] = []
        self._move = ""
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def trigger(self, points: float, direction: str):
        self._move = f"{'↑' if direction == 'up' else '↓'} {abs(points):.1f} pts in 1min!"
        self._alpha = 0.0
        self._frame = 0
        # Explosion particles from center
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        self._particles = []
        for _ in range(60):
            angle = random.uniform(0, math.pi * 2)
            speed = random.uniform(3, 15)
            self._particles.append({
                "x": float(cx), "y": float(cy),
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "life": 1.0,
                "size": random.randint(3, 9),
                "color": random.choice([
                    COLORS["amber"], COLORS["red"], COLORS["cyan"],
                    COLORS["green_bright"], "#ff6600",
                ]),
            })
        self.show(); self.raise_()
        self._timer.start(33)
        QTimer.singleShot(3500, self._fade)

    def _fade(self):
        # Signal start of fade
        if self._frame < 999:
            self._frame = 999

    def _step(self):
        self._frame += 1
        if self._frame < 999:
            self._alpha = min(self._alpha + 0.1, 1.0)
        else:
            self._alpha = max(self._alpha - 0.07, 0.0)
            if self._alpha <= 0:
                self._timer.stop()
                self.hide()
                return

        for p in self._particles:
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            p["vy"] += 0.3
            p["life"] -= 0.025
        self._particles = [p for p in self._particles if p["life"] > 0 and p["y"] < self.height() + 40]
        self.update()

    def paintEvent(self, event):
        if self._alpha <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Particles
        for p in self._particles:
            c = QColor(p["color"])
            c.setAlphaF(p["life"] * self._alpha)
            painter.setBrush(QBrush(c)); painter.setPen(Qt.PenStyle.NoPen)
            s = int(p["size"] * p["life"])
            if s > 0:
                painter.drawRect(int(p["x"]), int(p["y"]), s, s)

        # Toast box (top center)
        toast_w, toast_h = 380, 90
        tx = (w - toast_w) // 2
        ty = 60
        bg = QColor(40, 0, 0)
        bg.setAlphaF(self._alpha * 0.92)
        painter.fillRect(tx, ty, toast_w, toast_h, bg)
        bc = QColor(COLORS["red"])
        bc.setAlphaF(self._alpha)
        painter.setPen(QPen(bc, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(tx, ty, toast_w, toast_h)

        c1 = QColor(COLORS["red"])
        c1.setAlphaF(self._alpha)
        painter.setPen(c1)
        painter.setFont(QFont("Impact", 28, QFont.Weight.Bold))
        painter.drawText(QRectF(tx, ty + 6, toast_w, 42), Qt.AlignmentFlag.AlignCenter,
                         "⚡ HOLY SHIT ⚡")

        c2 = QColor(COLORS["amber"])
        c2.setAlphaF(self._alpha)
        painter.setPen(c2)
        painter.setFont(QFont("JetBrains Mono", 13, QFont.Weight.Bold))
        painter.drawText(QRectF(tx, ty + 50, toast_w, 32), Qt.AlignmentFlag.AlignCenter,
                         self._move)
        painter.end()


# ── DJ MODE visualizer ─────────────────────────────────────────────────────────

class DJModeVisualizer(QWidget):
    """Audio visualizer bars reacting to market volatility."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._bars: list[float] = [0.0] * 32
        self._target: list[float] = [0.0] * 32
        self._volatility = 0.1
        self._active = False
        self._hue = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def toggle(self):
        self._active = not self._active
        if self._active:
            self._timer.start(50)
            self.show(); self.raise_()
        else:
            self._timer.stop()
            self.hide()

    def set_volatility(self, vol: float):
        self._volatility = max(0.01, min(1.0, vol))

    def _step(self):
        self._hue = (self._hue + 3) % 360
        # Random bar heights modulated by volatility
        for i in range(len(self._bars)):
            self._target[i] = random.random() * self._volatility
            self._bars[i] += (self._target[i] - self._bars[i]) * 0.4
        self.update()

    def paintEvent(self, event):
        if not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        n = len(self._bars)
        bar_w = w / n
        max_h = h * 0.4

        for i, val in enumerate(self._bars):
            bh = max(4, int(val * max_h))
            bx = int(i * bar_w)
            by = h - 5 - bh
            hue = (self._hue + i * (360 // n)) % 360
            c = QColor.fromHsv(hue, 200, 255, 160)
            painter.fillRect(bx + 1, by, max(int(bar_w) - 2, 1), bh, c)

        painter.end()


# ── Keyword detector ──────────────────────────────────────────────────────────

class KeywordDetector:
    """Detects typed keywords anywhere in the app for easter egg triggers.

    Call key_char(char) for each printable key press.
    Register callbacks via on(keyword, callback).
    """

    KEYWORDS = ["MOON", "GUH", "TENDIES", "420", "PRINT"]

    def __init__(self):
        self._buffer = ""
        self._callbacks: dict[str, list] = {kw: [] for kw in self.KEYWORDS}
        self._max_len = max(len(kw) for kw in self.KEYWORDS) + 1

    def on(self, keyword: str, callback):
        if keyword in self._callbacks:
            self._callbacks[keyword].append(callback)

    def key_char(self, char: str):
        """Feed a single printable character."""
        self._buffer += char.upper()
        if len(self._buffer) > self._max_len:
            self._buffer = self._buffer[-self._max_len:]

        for kw in self.KEYWORDS:
            if self._buffer.endswith(kw):
                for cb in self._callbacks[kw]:
                    cb()
                self._buffer = ""
                return
