"""Retro animations, easter eggs, and fun effects for the MES Intel UI.

Features:
  - Matrix rain (real market data falling)
  - Konami code handler → rainbow neon mode
  - Winning streak celebrations (pixel explosions, confetti)
  - Retro pixel characters walking across screen
  - Big win animation
  - Rage mode (screen shake + motivational message)
  - Neon glow pulse for signals
  - Idle animations (matrix takeover after 5min)
  - Hidden mini-game (Space Invaders — 7 logo clicks)
  - Keywords: MOON, GUH, TENDIES, 420, PRINT
  - P&L milestones: level-up animation
  - Streaks: UNSTOPPABLE banner / motivational quote
  - Price +10pts/1min: HOLY SHIT toast
  - DJ mode: Shift+click time
"""
from __future__ import annotations

import math
import random
import time
from typing import Optional

from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QPushButton
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QPoint, QRect,
    QEasingCurve, QSequentialAnimationGroup, QParallelAnimationGroup,
    Property, Signal as QtSignal, QSize,
)
from PySide6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QLinearGradient,
    QKeyEvent, QPainterPath,
)

from .theme import COLORS
from .easter_eggs_v2 import (
    RocketAnimation, GuhFlash, TendiesRain, WeedRain,
    MoneyPrinterAnimation, LevelUpAnimation, UnstoppableBanner,
    HolyShitToast, DJModeVisualizer, KeywordDetector,
)


# ============================================================
# MATRIX RAIN — falling market data numbers
# ============================================================

class MatrixRain(QWidget):
    """Matrix-style falling numbers showing real market data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._columns: list[dict] = []
        self._market_numbers: list[str] = ["5", "5", "7", "3", ".", "2", "5", "0", "0"]
        self._active = True
        self._opacity_level: float = 0.07   # default: very subtle

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(80)

    def set_market_data(self, numbers: list[str]):
        """Update the pool of numbers to display."""
        self._market_numbers = numbers

    def set_active(self, active: bool):
        self._active = active

    def setOpacity_level(self, level: float):
        """Set the opacity multiplier for the rain (0.05 = subtle, 0.35 = takeover)."""
        self._opacity_level = max(0.01, min(1.0, level))

    def _step(self):
        if not self._active:
            return

        w, h = self.width(), self.height()
        if w < 10:
            return

        # Add new columns randomly
        if random.random() < 0.15 and len(self._columns) < w // 15:
            self._columns.append({
                "x": random.randint(0, w),
                "y": random.randint(-50, 0),
                "speed": random.uniform(2, 6),
                "chars": [random.choice(self._market_numbers) for _ in range(random.randint(5, 15))],
                "opacity": random.uniform(0.05, 0.15),
            })

        # Update positions
        for col in self._columns:
            col["y"] += col["speed"]

        # Remove off-screen columns
        self._columns = [c for c in self._columns if c["y"] - len(c["chars"]) * 14 < h]

        self.update()

    def paintEvent(self, event):
        if not self._columns:
            return

        painter = QPainter(self)
        font = QFont("JetBrains Mono", 10)
        painter.setFont(font)

        for col in self._columns:
            for i, char in enumerate(col["chars"]):
                y = col["y"] - i * 14
                if 0 <= y < self.height():
                    opacity = col["opacity"] * (1.0 - i / len(col["chars"])) * (self._opacity_level / 0.07)
                    if i == 0:
                        painter.setPen(QColor(0, 255, 65, int(min(opacity * 3, 1.0) * 255)))
                    else:
                        painter.setPen(QColor(0, 200, 50, int(min(opacity, 1.0) * 255)))
                    painter.drawText(col["x"], int(y), char)

        painter.end()


# ============================================================
# PIXEL CHARACTER — walks across the bottom of the screen
# ============================================================

class PixelCharacter(QWidget):
    """A retro pixel art character that walks across the screen."""

    # Simple pixel art frames (8x8 grid encoded as lists of row strings)
    CHARACTERS = {
        "trader": [
            # Frame 1 - walking
            [
                "  ██  ",
                " ████ ",
                "  ██  ",
                " ████ ",
                "██  ██",
                "  ██  ",
                " █  █ ",
                "██  ██",
            ],
            # Frame 2 - walking
            [
                "  ██  ",
                " ████ ",
                "  ██  ",
                " ████ ",
                "██  ██",
                "  ██  ",
                "██  █ ",
                "█   ██",
            ],
        ],
        "bull": [
            [
                "█    █",
                "██  ██",
                " ████ ",
                "██████",
                "██████",
                " ████ ",
                " █  █ ",
                "██  ██",
            ],
            [
                "█    █",
                "██  ██",
                " ████ ",
                "██████",
                "██████",
                " ████ ",
                "██  █ ",
                "█   ██",
            ],
        ],
        "bear": [
            [
                " █  █ ",
                "██████",
                "█ ██ █",
                "██████",
                " ████ ",
                " ████ ",
                " █  █ ",
                "██  ██",
            ],
            [
                " █  █ ",
                "██████",
                "█ ██ █",
                "██████",
                " ████ ",
                " ████ ",
                "██  █ ",
                "█   ██",
            ],
        ],
    }

    def __init__(self, parent=None, char_type: str = "trader"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._char_type = char_type
        self._frames = self.CHARACTERS.get(char_type, self.CHARACTERS["trader"])
        self._frame_idx = 0
        self._x = -50
        self._speed = random.uniform(0.5, 2.0)
        self._color = QColor(random.choice([
            COLORS["green_bright"], COLORS["cyan"], COLORS["amber"], COLORS["magenta"],
        ]))
        self._active = False

        self.setFixedSize(60, 60)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def start_walk(self):
        self._active = True
        self._x = -50
        self._frame_idx = 0
        self._timer.start(150)
        self.show()

    def _step(self):
        self._x += self._speed
        self._frame_idx = (self._frame_idx + 1) % len(self._frames)

        parent = self.parent()
        if parent:
            self.move(int(self._x), parent.height() - 65)
            if self._x > parent.width() + 50:
                self._active = False
                self._timer.stop()
                self.hide()

        self.update()

    def paintEvent(self, event):
        if not self._active:
            return

        painter = QPainter(self)
        frame = self._frames[self._frame_idx]
        pixel_size = 5

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._color))

        for row_i, row in enumerate(frame):
            for col_i, char in enumerate(row):
                if char == "█":
                    painter.drawRect(col_i * pixel_size, row_i * pixel_size,
                                     pixel_size - 1, pixel_size - 1)

        painter.end()


# ============================================================
# CONFETTI / CELEBRATION
# ============================================================

class ConfettiWidget(QWidget):
    """Celebration confetti / pixel explosion for wins."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._particles: list[dict] = []
        self._active = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def celebrate(self, intensity: int = 50):
        """Launch celebration effect."""
        self._active = True
        w, h = self.width(), self.height()

        colors = [
            COLORS["green_bright"], COLORS["cyan"], COLORS["amber"],
            COLORS["magenta"], COLORS["blue"], "#ff6b6b", "#ffd93d",
        ]

        self._particles = []
        for _ in range(intensity):
            self._particles.append({
                "x": w / 2 + random.uniform(-100, 100),
                "y": h / 2,
                "vx": random.uniform(-8, 8),
                "vy": random.uniform(-12, -2),
                "size": random.randint(3, 8),
                "color": random.choice(colors),
                "life": random.uniform(0.5, 1.0),
                "gravity": 0.2,
            })

        self.show()
        self.raise_()
        self._timer.start(33)

    def _step(self):
        alive = []
        for p in self._particles:
            p["x"] += p["vx"]
            p["vy"] += p["gravity"]
            p["y"] += p["vy"]
            p["life"] -= 0.02
            if p["life"] > 0 and p["y"] < self.height() + 20:
                alive.append(p)

        self._particles = alive
        if not alive:
            self._active = False
            self._timer.stop()
            self.hide()

        self.update()

    def paintEvent(self, event):
        if not self._particles:
            return

        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)

        for p in self._particles:
            color = QColor(p["color"])
            color.setAlphaF(max(p["life"], 0))
            painter.setBrush(QBrush(color))
            painter.drawRect(int(p["x"]), int(p["y"]), p["size"], p["size"])

        painter.end()


# ============================================================
# SIGNAL PULSE — glowing animation for trade signals
# ============================================================

class SignalPulse(QWidget):
    """Glowing pulse ring that expands when a signal fires."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._rings: list[dict] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def pulse(self, color: str = COLORS["green_bright"], x: int = 0, y: int = 0):
        """Fire a pulse ring."""
        for i in range(3):
            self._rings.append({
                "x": x or self.width() // 2,
                "y": y or self.height() // 2,
                "radius": 10 + i * 15,
                "max_radius": 120 + i * 30,
                "color": color,
                "alpha": 1.0,
            })

        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start(33)

    def _step(self):
        alive = []
        for ring in self._rings:
            ring["radius"] += 3
            ring["alpha"] -= 0.03
            if ring["alpha"] > 0 and ring["radius"] < ring["max_radius"]:
                alive.append(ring)

        self._rings = alive
        if not alive:
            self._timer.stop()
            self.hide()

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for ring in self._rings:
            color = QColor(ring["color"])
            color.setAlphaF(max(ring["alpha"], 0))
            pen = QPen(color, 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r = int(ring["radius"])
            painter.drawEllipse(ring["x"] - r, ring["y"] - r, r * 2, r * 2)

        painter.end()


# ============================================================
# RAGE MODE — screen shake + motivational message
# ============================================================

class RageMode:
    """Screen shake and motivational message on losses."""

    MESSAGES = [
        "THE MARKET GIVETH, THE MARKET TAKETH AWAY",
        "EVERY LOSS IS A LESSON IN DISGUISE",
        "DISCIPLINE > EMOTION — YOU GOT THIS",
        "ONE TRADE DOESN'T DEFINE YOU",
        "STICK TO THE SYSTEM, TRUST THE PROCESS",
        "LOSSES ARE THE COST OF DOING BUSINESS",
        "THE BEST TRADERS LOSE SMALL",
        "REVENGE TRADING IS THE REAL ENEMY",
        "BREATHE. RESET. NEXT TRADE.",
        "THIS IS WHY WE USE STOPS",
    ]

    def __init__(self, window):
        self._window = window
        self._original_pos = None
        self._shake_count = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._shake_step)

    def trigger(self, pnl: float):
        """Trigger rage mode for a loss."""
        self._original_pos = self._window.pos()
        self._shake_count = 0
        self._timer.start(30)

        # Show motivational message
        msg = random.choice(self.MESSAGES)
        self._show_message(msg)

    def _shake_step(self):
        if self._shake_count >= 20:
            self._timer.stop()
            if self._original_pos:
                self._window.move(self._original_pos)
            return

        offset_x = random.randint(-4, 4)
        offset_y = random.randint(-3, 3)
        if self._original_pos:
            self._window.move(
                self._original_pos.x() + offset_x,
                self._original_pos.y() + offset_y,
            )
        self._shake_count += 1

    def _show_message(self, msg: str):
        """Show a floating motivational message."""
        label = QLabel(msg, self._window)
        label.setStyleSheet(
            f"background: {COLORS['bg_card']}; color: {COLORS['amber']}; "
            f"font-size: 14px; font-weight: bold; padding: 12px 24px; "
            f"border: 2px solid {COLORS['amber']}; border-radius: 6px;"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.adjustSize()

        # Center it
        x = (self._window.width() - label.width()) // 2
        y = self._window.height() // 3
        label.move(x, y)
        label.show()
        label.raise_()

        # Auto-hide after 3 seconds
        QTimer.singleShot(3000, label.deleteLater)


# ============================================================
# KONAMI CODE HANDLER
# ============================================================

class KonamiCodeHandler:
    """Detects the Konami code: ↑↑↓↓←→←→BA"""

    SEQUENCE = [
        Qt.Key.Key_Up, Qt.Key.Key_Up,
        Qt.Key.Key_Down, Qt.Key.Key_Down,
        Qt.Key.Key_Left, Qt.Key.Key_Right,
        Qt.Key.Key_Left, Qt.Key.Key_Right,
        Qt.Key.Key_B, Qt.Key.Key_A,
    ]

    activated = False

    def __init__(self):
        self._buffer: list[int] = []

    def key_pressed(self, key: int) -> bool:
        """Returns True if Konami code was just completed."""
        self._buffer.append(key)
        if len(self._buffer) > len(self.SEQUENCE):
            self._buffer = self._buffer[-len(self.SEQUENCE):]

        if self._buffer == self.SEQUENCE:
            self.activated = not self.activated
            self._buffer.clear()
            return True
        return False


# ============================================================
# RAINBOW MODE — activated by Konami code
# ============================================================

class RainbowMode(QWidget):
    """Rainbow border glow that cycles colors."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._hue = 0
        self._active = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    def toggle(self):
        self._active = not self._active
        if self._active:
            self._timer.start(33)
            self.show()
            self.raise_()
        else:
            self._timer.stop()
            self.hide()

    def _step(self):
        self._hue = (self._hue + 3) % 360
        self.update()

    def paintEvent(self, event):
        if not self._active:
            return

        painter = QPainter(self)
        w, h = self.width(), self.height()

        for i in range(3):
            color = QColor.fromHsv((self._hue + i * 40) % 360, 255, 255, 60 - i * 15)
            pen = QPen(color, 3 - i)
            painter.setPen(pen)
            painter.drawRect(i * 2, i * 2, w - i * 4 - 1, h - i * 4 - 1)

        painter.end()


# ============================================================
# MINI SPACE INVADERS — hidden game
# ============================================================

class SpaceInvaders(QWidget):
    """Tiny Space Invaders mini-game, hidden easter egg."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(300, 200)
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setStyleSheet(f"background: {COLORS['bg_dark']}; border: 2px solid {COLORS['green_bright']};")

        self._player_x = 150
        self._bullets: list[dict] = []
        self._invaders: list[dict] = []
        self._score = 0
        self._active = False

        # Create invaders
        for row in range(3):
            for col in range(6):
                self._invaders.append({
                    "x": 30 + col * 40,
                    "y": 20 + row * 25,
                    "alive": True,
                })

        self._invader_dir = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._game_step)

    def start_game(self):
        self._active = True
        self._score = 0
        for inv in self._invaders:
            inv["alive"] = True
        self.show()
        self.setFocus()
        self._timer.start(50)

    def keyPressEvent(self, event: QKeyEvent):
        if not self._active:
            return

        if event.key() == Qt.Key.Key_Left:
            self._player_x = max(10, self._player_x - 8)
        elif event.key() == Qt.Key.Key_Right:
            self._player_x = min(290, self._player_x + 8)
        elif event.key() == Qt.Key.Key_Space:
            self._bullets.append({"x": self._player_x, "y": 170})
        elif event.key() == Qt.Key.Key_Escape:
            self._active = False
            self._timer.stop()
            self.hide()

    def _game_step(self):
        # Move bullets
        for b in self._bullets:
            b["y"] -= 5
        self._bullets = [b for b in self._bullets if b["y"] > 0]

        # Move invaders
        move_down = False
        for inv in self._invaders:
            if inv["alive"]:
                inv["x"] += self._invader_dir * 2
                if inv["x"] > 280 or inv["x"] < 20:
                    move_down = True

        if move_down:
            self._invader_dir *= -1
            for inv in self._invaders:
                inv["y"] += 5

        # Collision detection
        for b in self._bullets:
            for inv in self._invaders:
                if inv["alive"] and abs(b["x"] - inv["x"]) < 12 and abs(b["y"] - inv["y"]) < 10:
                    inv["alive"] = False
                    self._score += 10
                    b["y"] = -10  # remove bullet

        # Check win
        if not any(inv["alive"] for inv in self._invaders):
            self._active = False
            self._timer.stop()

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(0, 0, 300, 200, QColor(COLORS["bg_dark"]))

        # Player
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(COLORS["green_bright"])))
        painter.drawRect(self._player_x - 8, 175, 16, 8)
        painter.drawRect(self._player_x - 2, 170, 4, 5)

        # Bullets
        painter.setBrush(QBrush(QColor(COLORS["cyan"])))
        for b in self._bullets:
            painter.drawRect(int(b["x"]) - 1, int(b["y"]), 2, 6)

        # Invaders
        for inv in self._invaders:
            if inv["alive"]:
                painter.setBrush(QBrush(QColor(COLORS["red"])))
                painter.drawRect(int(inv["x"]) - 8, int(inv["y"]) - 6, 16, 12)

        # Score
        painter.setPen(QColor(COLORS["amber"]))
        font = QFont("JetBrains Mono", 9)
        painter.setFont(font)
        painter.drawText(10, 195, f"SCORE: {self._score}")

        if not self._active and self._score > 0:
            painter.setPen(QColor(COLORS["green_bright"]))
            font.setPointSize(14)
            painter.setFont(font)
            painter.drawText(80, 100, "YOU WIN!")

        painter.end()


# ============================================================
# EASTER EGG MANAGER — coordinates all fun stuff
# ============================================================

class EasterEggManager:
    """Manages all easter eggs and animations — v2 extended."""

    BIG_WIN_THRESHOLD = 50.0
    STREAK_THRESHOLD  = 3
    PRICE_SPIKE_PTS   = 10.0    # pts/min to trigger HOLY SHIT
    IDLE_TIMEOUT      = 300.0   # seconds before idle matrix takeover

    MOTIVATIONAL_QUOTES = [
        "LOSING IS PART OF THE GAME — WHAT MATTERS IS STAYING IN IT",
        "THE MARKET WILL BE THERE TOMORROW. YOUR CAPITAL MUST BE TOO.",
        "THREE LOSSES DOESN'T MAKE YOU A LOSER. QUITTING DOES.",
        "REVIEW THE TRADE, NOT YOUR WORTH.",
        "LIVE TO TRADE ANOTHER DAY — THAT'S THE REAL WIN.",
        "DISCIPLINE > EMOTION. ALWAYS.",
        "YOUR EDGE IS STILL YOUR EDGE. TRUST THE PROCESS.",
        "EVERY PRO HAS A LOSING STREAK. YOU GOT THIS.",
    ]

    def __init__(self, window):
        self._window = window
        self._konami = KonamiCodeHandler()
        self._keyword = KeywordDetector()
        self._win_streak  = 0
        self._loss_streak = 0
        self._logo_clicks = 0
        self._last_interaction = time.time()
        self._cumulative_pnl   = 0.0

        # Price spike detection
        self._price_1min_ago   = 0.0
        self._last_price_time  = time.time()
        self._current_price    = 0.0

        # ── Overlay widgets ────────────────────────────────────────────────
        self.matrix_rain    = MatrixRain(window)
        self.confetti       = ConfettiWidget(window)
        self.signal_pulse   = SignalPulse(window)
        self.rainbow        = RainbowMode(window)
        self.rage           = RageMode(window)
        self.space_invaders = SpaceInvaders()

        # v2 overlays
        self.rocket      = RocketAnimation(window)
        self.guh_flash   = GuhFlash(window)
        self.tendies     = TendiesRain(window)
        self.weed_rain   = WeedRain(window)
        self.money_print = MoneyPrinterAnimation(window)
        self.level_up    = LevelUpAnimation(window)
        self.unstoppable = UnstoppableBanner(window)
        self.holy_shit   = HolyShitToast(window)
        self.dj_mode     = DJModeVisualizer(window)
        self._dj_active  = False

        # Pixel characters
        self._characters: list[PixelCharacter] = []

        # Wire keyword callbacks
        self._keyword.on("MOON",    self.rocket.launch)
        self._keyword.on("GUH",     self.guh_flash.trigger)
        self._keyword.on("TENDIES", lambda: self.tendies.rain(3500))
        self._keyword.on("420",     lambda: self.weed_rain.rain(5000))
        self._keyword.on("PRINT",   self.money_print.print_money)

        # Idle timer (60s check)
        self._idle_timer = QTimer()
        self._idle_timer.timeout.connect(self._on_idle)
        self._idle_timer.start(60000)

        # Price spike check (every 60s)
        self._spike_timer = QTimer()
        self._spike_timer.timeout.connect(self._check_price_spike)
        self._spike_timer.start(60000)

        # Periodic character walk
        self._walk_timer = QTimer()
        self._walk_timer.timeout.connect(self._maybe_walk_character)
        self._walk_timer.start(120000)

        # Matrix idle takeover: full opacity kicks in after idle detected
        self._matrix_takeover = False

    def resize_overlays(self, size):
        """Resize all overlay widgets to match window."""
        for widget in (self.matrix_rain, self.confetti, self.signal_pulse,
                       self.rainbow, self.rocket, self.guh_flash, self.tendies,
                       self.weed_rain, self.money_print, self.level_up,
                       self.unstoppable, self.holy_shit, self.dj_mode):
            widget.resize(size)

    def handle_key(self, key: int) -> bool:
        """Handle key press for Konami code + keyword detection."""
        self._last_interaction = time.time()

        # Wake from matrix takeover on any key
        if self._matrix_takeover:
            self._matrix_takeover = False
            self.matrix_rain.setOpacity_level(0.07)
            return False

        # Konami code
        if self._konami.key_pressed(key):
            self.rainbow.toggle()
            if self._konami.activated:
                self.confetti.celebrate(120)
                self.unstoppable.show_banner(99)  # special konami mode
            return True

        # Printable character → feed to keyword detector
        if 0x20 <= key <= 0x7E:
            self._keyword.key_char(chr(key))

        return False

    def on_logo_click(self):
        self._logo_clicks += 1
        if self._logo_clicks >= 7:
            self.space_invaders.start_game()
            self._logo_clicks = 0
        elif self._logo_clicks == 3:
            self._spawn_character()

    def on_trade_closed(self, pnl: float):
        """React to trade outcomes with appropriate animations."""
        self._cumulative_pnl += pnl
        self.level_up.check_milestone(self._cumulative_pnl)

        if pnl > 0:
            self._win_streak  += 1
            self._loss_streak  = 0

            if pnl >= self.BIG_WIN_THRESHOLD:
                self.confetti.celebrate(150)
                self.signal_pulse.pulse(COLORS["green_bright"],
                                        self._window.width() // 2,
                                        self._window.height() // 2)

            if self._win_streak >= self.STREAK_THRESHOLD:
                self.unstoppable.show_banner(self._win_streak)
                self.confetti.celebrate(80)
                self._spawn_character("bull")

        else:
            self._loss_streak += 1
            self._win_streak   = 0

            if pnl < -self.BIG_WIN_THRESHOLD:
                self.rage.trigger(pnl)
                self._spawn_character("bear")

            if self._loss_streak >= self.STREAK_THRESHOLD:
                self._show_motivational()

    def on_signal(self, direction: str):
        if direction == "LONG":
            self.signal_pulse.pulse(COLORS["long_color"])
        elif direction == "SHORT":
            self.signal_pulse.pulse(COLORS["short_color"])

    def on_dj_click(self):
        """Toggle DJ mode (triggered by Shift+click on time display)."""
        self._dj_active = not self._dj_active
        self.dj_mode.toggle()

    def _check_price_spike(self):
        """Check if price moved 10+ pts in last 1 min."""
        if self._current_price and self._price_1min_ago:
            diff = self._current_price - self._price_1min_ago
            if abs(diff) >= self.PRICE_SPIKE_PTS:
                direction = "up" if diff > 0 else "down"
                self.holy_shit.trigger(diff, direction)
        self._price_1min_ago = self._current_price

    def _on_idle(self):
        idle_time = time.time() - self._last_interaction
        if idle_time > self.IDLE_TIMEOUT and not self._matrix_takeover:
            self._matrix_takeover = True
            self.matrix_rain.set_active(True)
            self.matrix_rain.setOpacity_level(0.35)   # brighter during takeover
            self.matrix_rain.show()
            self.matrix_rain.raise_()
        elif idle_time < 60 and self._matrix_takeover:
            self._matrix_takeover = False
            self.matrix_rain.setOpacity_level(0.07)
        # Occasional character walk when idle
        if idle_time > 180 and random.random() < 0.4:
            self._spawn_character()

    def _maybe_walk_character(self):
        if random.random() < 0.2:
            self._spawn_character()

    def _spawn_character(self, char_type: str = ""):
        if not char_type:
            char_type = random.choice(["trader", "bull", "bear"])
        char = PixelCharacter(self._window, char_type)
        char.start_walk()
        self._characters.append(char)
        self._characters = [c for c in self._characters if c._active]

    def _show_motivational(self):
        quote = random.choice(self.MOTIVATIONAL_QUOTES)
        self.rage._show_message(quote)

    def update_market_data(self, price: float):
        self._current_price = price
        digits = list(f"{price:.2f}")
        self.matrix_rain.set_market_data(digits)
        # Feed volatility proxy to DJ mode
        if self._dj_active and price and self._price_1min_ago:
            vol = min(abs(price - self._price_1min_ago) / 5.0, 1.0)
            self.dj_mode.set_volatility(vol)
