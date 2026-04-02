"""Easter Egg System — complete implementation.

Triggers:
  Konami code          → rainbow neon mode + pixel art dance
  Click logo 7×        → Space Invaders mini-game
  Type "MOON"          → rocket shoots across screen
  Type "GUH"           → dramatic red flash
  Type "TENDIES"       → chicken tenders rain
  Type "420"           → weed leaf rain + rasta colors
  Type "PRINT"         → money printer BRRR animation
  Win 3+ trades        → UNSTOPPABLE banner + flames
  Lose 3+              → motivational quote
  P&L milestone        → level-up animation
  Idle 5 min           → Matrix code rain
  Shift+click time     → DJ mode visualizer
  Right-click news     → snake game
  Vanity animations    → pill/substance pixel art in corners
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
    QApplication, QDialog, QSizePolicy,
)
from PySide6.QtCore import (
    Qt, QTimer, QRect, QPoint, QSize, QRectF, QPointF,
    Signal, QObject,
)
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QKeyEvent,
    QLinearGradient, QRadialGradient, QPainterPath,
)

# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

KONAMI = [Qt.Key_Up, Qt.Key_Up, Qt.Key_Down, Qt.Key_Down,
          Qt.Key_Left, Qt.Key_Right, Qt.Key_Left, Qt.Key_Right,
          Qt.Key_B, Qt.Key_A]

MOTIVATIONAL = [
    "MARKET GIVES. MARKET TAKES. STAY FLAT.",
    "THE EDGE IS IN THE PROCESS, NOT THE P&L.",
    "ONE BAD TRADE DOESN'T DEFINE YOU.",
    "SIZE DOWN. BREATHE. RESET.",
    "EVERY GREAT TRADER HAS A STORY LIKE THIS.",
    "DISCIPLINE OVER INTUITION TODAY.",
    "THIS IS THE WAY.",
]

RAINBOW_COLORS = [
    QColor('#ff0000'), QColor('#ff7700'), QColor('#ffff00'),
    QColor('#00ff00'), QColor('#0077ff'), QColor('#8800ff'),
    QColor('#ff00ff'),
]


# ─────────────────────────────────────────────
#  Particle / Sprite base
# ─────────────────────────────────────────────

@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float         # 0→1 (1 = full life remaining)
    decay: float        # per-frame life reduction
    color: QColor
    size: float = 8.0
    rotation: float = 0.0
    spin: float = 0.0
    emoji: str = ''
    shape: str = 'circle'   # circle | rect | star | text


class ParticleSystem:
    """Manages a pool of particles."""

    def __init__(self):
        self._particles: List[Particle] = []

    def add(self, p: Particle):
        self._particles.append(p)

    def update(self):
        alive = []
        for p in self._particles:
            p.x += p.vx
            p.y += p.vy
            p.vy += 0.15  # gravity
            p.life -= p.decay
            p.rotation += p.spin
            if p.life > 0:
                alive.append(p)
        self._particles = alive

    def draw(self, painter: QPainter):
        for p in self._particles:
            alpha = int(p.life * 255)
            color = QColor(p.color)
            color.setAlpha(alpha)
            painter.save()
            painter.translate(p.x, p.y)
            painter.rotate(p.rotation)

            if p.shape == 'circle':
                painter.setBrush(QBrush(color))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(0, 0), p.size, p.size)
            elif p.shape == 'rect':
                painter.fillRect(QRectF(-p.size / 2, -p.size / 2, p.size, p.size), color)
            elif p.shape == 'star':
                path = QPainterPath()
                for i in range(5):
                    a = math.radians(i * 72 - 90)
                    r = p.size if i == 0 else p.size * 0.4
                    if i == 0:
                        path.moveTo(math.cos(a) * p.size, math.sin(a) * p.size)
                    else:
                        ai = math.radians((i - 0.5) * 72 - 90)
                        path.lineTo(math.cos(ai) * p.size * 0.4, math.sin(ai) * p.size * 0.4)
                        path.lineTo(math.cos(a) * p.size, math.sin(a) * p.size)
                path.closeSubpath()
                painter.fillPath(path, QBrush(color))
            elif p.shape == 'text' and p.emoji:
                painter.setPen(color)
                painter.setFont(QFont('Segoe UI Emoji', int(p.size)))
                painter.drawText(QPointF(-p.size / 2, p.size / 2), p.emoji)

            painter.restore()

    def clear(self):
        self._particles.clear()

    def __len__(self):
        return len(self._particles)


# ─────────────────────────────────────────────
#  Overlay Widget (transparent, full-window)
# ─────────────────────────────────────────────

class EasterEggOverlay(QWidget):
    """Transparent overlay drawn on top of the main window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(parent.size())

        self._particles = ParticleSystem()
        self._active_effects: Dict[str, float] = {}   # effect_name → expire_time
        self._matrix_chars: List[Dict] = []
        self._rainbow_phase = 0.0
        self._rocket_x = -100.0
        self._rocket_y = 0.0
        self._rocket_active = False
        self._brrr_phase = 0.0
        self._brrr_active = False
        self._flames: List[Dict] = []
        self._banner_text = ''
        self._banner_alpha = 0.0
        self._dj_mode = False
        self._dj_bars: List[float] = [0.0] * 32

        # ── New easter egg state ─────────────────
        self._slot_active = False
        self._slot_phase = 0.0          # 0→1 (spin), 1→2 (landing), 2→3 (hold/fade)
        self._slot_reels: List[str] = ['🎰', '🎰', '🎰']
        self._slot_spin_chars = ['🚀', '💎', '🌙', '🔥', '💰', '📈', '🎯', '⚡', '🏆', '💸']

        self._pump_active = False
        self._pump_y = 0.0

        self._dump_active = False
        self._dump_y = 0.0
        self._dump_exploded = False

        self._lambo_x = -200.0
        self._lambo_active = False

        self._chad_alpha = 0.0
        self._chad_active = False

        self._bruh_alpha = 0.0
        self._bruh_shake = 0

        self._stonks_alpha = 0.0
        self._stonks_active = False

        self._rip_y = -120.0
        self._rip_active = False
        self._rip_landed = False
        self._rip_f_alpha = 0.0
        self._rip_shake = 0

        self._plane_x = -60.0
        self._plane_y = 0.0
        self._plane_active = False

        # ── Random ambient effects ───────────────
        self._breath_phase = 0.0

        self._star_active = False
        self._star_x = 0.0
        self._star_y = 0.0
        self._star_vx = 0.0
        self._star_vy = 0.0
        self._star_life = 0.0

        self._glitch_active = False
        self._glitch_shift = 0
        self._glitch_life = 0.0

        self._signal_glow_alpha = 0.0
        self._signal_glow_text = ''

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30fps

    def _tick(self):
        now = time.time()
        # Expire effects
        expired = [k for k, v in self._active_effects.items() if now > v]
        for k in expired:
            del self._active_effects[k]

        self._particles.update()
        self._update_matrix()
        self._update_rocket()
        self._update_brrr()
        self._update_flames()
        self._update_banner()
        self._update_dj()

        # New effects
        self._update_slot()
        self._update_pump()
        self._update_dump()
        self._update_lambo()
        self._update_chad()
        self._update_bruh()
        self._update_stonks()
        self._update_rip()
        self._update_plane()
        self._update_breath()
        self._update_star()
        self._update_glitch()
        self._update_signal_glow()

        if self._anything_active():
            self.update()
        else:
            self.update()  # always update for idle detection

    def _anything_active(self) -> bool:
        return bool(
            self._active_effects or len(self._particles) > 0 or
            self._rocket_active or self._brrr_active or
            self._banner_alpha > 0 or self._dj_mode or
            self._slot_active or self._pump_active or self._dump_active or
            self._lambo_active or self._chad_active or self._bruh_alpha > 0 or
            self._stonks_active or self._rip_active or self._plane_active or
            self._star_active or self._glitch_active or self._signal_glow_alpha > 0
        )

    # ── Effect triggers ──────────────────────

    def trigger_rainbow(self):
        self._active_effects['rainbow'] = time.time() + 8.0

    def trigger_red_flash(self):
        self._active_effects['red_flash'] = time.time() + 1.5

    def trigger_tendies_rain(self):
        w, h = self.width(), self.height()
        for _ in range(40):
            self._particles.add(Particle(
                x=random.uniform(0, w), y=random.uniform(-100, -10),
                vx=random.uniform(-1, 1), vy=random.uniform(2, 5),
                life=1.0, decay=0.004, color=QColor('#ffcc44'),
                size=random.uniform(14, 22), shape='text', emoji='🍗',
            ))

    def trigger_weed_rain(self):
        w, _ = self.width(), self.height()
        self._active_effects['rasta'] = time.time() + 6.0
        for _ in range(35):
            self._particles.add(Particle(
                x=random.uniform(0, w), y=random.uniform(-120, -10),
                vx=random.uniform(-0.5, 0.5), vy=random.uniform(1.5, 3),
                life=1.0, decay=0.003, color=QColor('#00cc44'),
                size=random.uniform(14, 24), shape='text', emoji='🍃',
                spin=random.uniform(-3, 3),
            ))

    def trigger_rocket(self):
        self._rocket_x = -60.0
        self._rocket_y = self.height() * random.uniform(0.3, 0.7)
        self._rocket_active = True

    def trigger_brrr(self):
        self._brrr_active = True
        self._brrr_phase = 0.0
        self._active_effects['brrr'] = time.time() + 4.0
        w, h = self.width(), self.height()
        for _ in range(50):
            self._particles.add(Particle(
                x=random.uniform(0, w), y=random.uniform(0, h),
                vx=random.uniform(-3, 3), vy=random.uniform(-4, 0),
                life=1.0, decay=0.015, color=QColor('#00cc44'),
                size=random.uniform(12, 20), shape='text', emoji='💵',
            ))

    def trigger_unstoppable(self):
        self._banner_text = 'UNSTOPPABLE'
        self._banner_alpha = 1.0
        # Spawn flame particles
        w, h = self.width(), self.height()
        self._active_effects['flames'] = time.time() + 4.0
        for _ in range(60):
            self._flames.append({
                'x': random.uniform(0, w), 'y': float(h),
                'vx': random.uniform(-1, 1), 'vy': random.uniform(-3, -8),
                'life': 1.0, 'size': random.uniform(8, 20),
            })

    def trigger_motivation(self):
        self._banner_text = random.choice(MOTIVATIONAL)
        self._banner_alpha = 1.0

    def trigger_levelup(self, label: str = 'LEVEL UP!'):
        self._banner_text = label
        self._banner_alpha = 1.0
        w, h = self.width(), self.height()
        for _ in range(80):
            color = random.choice(RAINBOW_COLORS)
            self._particles.add(Particle(
                x=w / 2, y=h / 2,
                vx=random.uniform(-8, 8), vy=random.uniform(-8, 8),
                life=1.0, decay=0.012, color=color,
                size=random.uniform(4, 10), shape=random.choice(['circle', 'star']),
                spin=random.uniform(-5, 5),
            ))

    def trigger_matrix(self):
        self._active_effects['matrix'] = time.time() + 15.0
        self._init_matrix()

    def trigger_dj_mode(self):
        self._dj_mode = not self._dj_mode

    # ── New easter egg triggers ──────────────

    def trigger_yolo(self):
        """Slot machine — 3 spinning reels."""
        self._slot_active = True
        self._slot_phase = 0.0

    def trigger_pump(self):
        """Green arrow + trail shoots from bottom to top."""
        self._pump_active = True
        self._pump_y = float(self.height() + 60)
        self._active_effects['pump_text'] = time.time() + 2.5

    def trigger_dump(self):
        """Red arrow crashes from top to bottom + explosion."""
        self._dump_active = True
        self._dump_y = -60.0
        self._dump_exploded = False

    def trigger_lambo(self):
        """Pixel art Lambo drives across the bottom."""
        self._lambo_x = -200.0
        self._lambo_active = True

    def trigger_chad(self):
        """GigaChad appears briefly in center."""
        self._chad_alpha = 1.0
        self._chad_active = True

    def trigger_hodl(self):
        """Diamond hands rain from top."""
        w, _ = self.width(), self.height()
        for _ in range(35):
            self._particles.add(Particle(
                x=random.uniform(0, w), y=random.uniform(-100, -5),
                vx=random.uniform(-0.5, 0.5), vy=random.uniform(2.0, 4.5),
                life=1.0, decay=0.004, color=QColor('#00ccff'),
                size=random.uniform(16, 26), shape='text', emoji='💎',
                spin=random.uniform(-2, 2),
            ))
        # Sparkles
        for _ in range(20):
            self._particles.add(Particle(
                x=random.uniform(0, w), y=random.uniform(-60, 0),
                vx=random.uniform(-1, 1), vy=random.uniform(1, 3),
                life=1.0, decay=0.01, color=QColor('#ffffff'),
                size=random.uniform(2, 5), shape='star',
                spin=random.uniform(-5, 5),
            ))

    def trigger_bruh(self):
        """Screen shake + huge BRUH text."""
        self._bruh_alpha = 1.0
        self._bruh_shake = 12
        self._active_effects['bruh'] = time.time() + 2.0

    def trigger_stonks(self):
        """Stonks meme — stick figure + green diagonal arrow."""
        self._stonks_alpha = 1.0
        self._stonks_active = True
        self._active_effects['stonks'] = time.time() + 3.0

    def trigger_rip(self):
        """Tombstone drops from top, lands with shake, F appears."""
        self._rip_y = -120.0
        self._rip_active = True
        self._rip_landed = False
        self._rip_f_alpha = 0.0
        self._rip_shake = 0

    def trigger_sendit(self):
        """Paper airplane flies diagonally across screen."""
        self._plane_x = -60.0
        self._plane_y = float(self.height()) * random.uniform(0.2, 0.6)
        self._plane_active = True

    def trigger_shooting_star(self):
        """Bright comet streak diagonally."""
        w, h = self.width(), self.height()
        # Start from left or top edge
        if random.random() < 0.5:
            self._star_x = random.uniform(0, w * 0.5)
            self._star_y = random.uniform(0, h * 0.3)
        else:
            self._star_x = random.uniform(w * 0.1, w * 0.6)
            self._star_y = random.uniform(0, h * 0.2)
        speed = random.uniform(18, 28)
        angle = random.uniform(20, 50)
        self._star_vx = speed * math.cos(math.radians(angle))
        self._star_vy = speed * math.sin(math.radians(angle))
        self._star_life = 1.0
        self._star_active = True

    def trigger_price_milestone(self, x: int = 0, y: int = 0):
        """Golden sparkle burst at a position."""
        cx = x if x else self.width() // 2
        cy = y if y else self.height() // 2
        for _ in range(40):
            angle = random.uniform(0, math.pi * 2)
            speed = random.uniform(2, 9)
            self._particles.add(Particle(
                x=cx, y=cy,
                vx=math.cos(angle) * speed, vy=math.sin(angle) * speed - 2,
                life=1.0, decay=0.02,
                color=random.choice([QColor('#ffd700'), QColor('#ffaa00'), QColor('#ffffff'), QColor('#ffe44d')]),
                size=random.uniform(4, 10), shape=random.choice(['star', 'circle']),
                spin=random.uniform(-8, 8),
            ))

    def trigger_signal_fire(self, text: str = 'SIGNAL FIRE!'):
        """High-confidence signal — glowing text + particle burst."""
        self._signal_glow_text = text
        self._signal_glow_alpha = 1.0
        w, h = self.width(), self.height()
        colors = [QColor('#00ff88'), QColor('#00ffcc'), QColor('#ffff00'), QColor('#ffffff')]
        for _ in range(60):
            angle = random.uniform(0, math.pi * 2)
            speed = random.uniform(3, 10)
            self._particles.add(Particle(
                x=w // 2, y=h // 2,
                vx=math.cos(angle) * speed, vy=math.sin(angle) * speed - 3,
                life=1.0, decay=0.015,
                color=random.choice(colors),
                size=random.uniform(4, 12), shape=random.choice(['star', 'circle']),
                spin=random.uniform(-6, 6),
            ))

    def trigger_glitch_frame(self):
        """VHS tape hiccup — brief horizontal glitch."""
        self._glitch_active = True
        self._glitch_shift = random.randint(8, 25)
        self._glitch_life = 1.0
        self._active_effects['vhs_glitch'] = time.time() + 0.12

    # ── Internal updaters ────────────────────

    def _update_matrix(self):
        if 'matrix' not in self._active_effects:
            self._matrix_chars.clear()
            return
        if not self._matrix_chars:
            self._init_matrix()
        for col in self._matrix_chars:
            col['y'] += col['speed']
            if col['y'] > self.height() + 20:
                col['y'] = random.uniform(-200, 0)
            col['char'] = chr(random.randint(0x30A0, 0x30FF))  # Katakana

    def _init_matrix(self):
        self._matrix_chars = []
        for x in range(0, self.width(), 14):
            self._matrix_chars.append({
                'x': x, 'y': random.uniform(-self.height(), 0),
                'speed': random.uniform(3, 9),
                'char': chr(random.randint(0x30A0, 0x30FF)),
                'bright': random.random() > 0.85,
            })

    def _update_rocket(self):
        if not self._rocket_active:
            return
        self._rocket_x += 12
        self._rocket_y += random.uniform(-0.5, 0.5)
        # Trail particles
        self._particles.add(Particle(
            x=self._rocket_x - 20, y=self._rocket_y,
            vx=random.uniform(-2, -0.5), vy=random.uniform(-1, 1),
            life=1.0, decay=0.08,
            color=random.choice([QColor('#ff8800'), QColor('#ffff00'), QColor('#ffffff')]),
            size=random.uniform(2, 5), shape='circle',
        ))
        if self._rocket_x > self.width() + 80:
            self._rocket_active = False

    def _update_brrr(self):
        if 'brrr' not in self._active_effects:
            self._brrr_active = False
        self._brrr_phase += 0.08

    def _update_flames(self):
        if 'flames' not in self._active_effects:
            self._flames.clear()
            return
        alive = []
        for f in self._flames:
            f['y'] += f['vy']
            f['x'] += f['vx']
            f['life'] -= 0.015
            if f['life'] > 0:
                alive.append(f)
        self._flames = alive

    def _update_banner(self):
        if self._banner_alpha > 0:
            self._banner_alpha -= 0.005
            if self._banner_alpha < 0:
                self._banner_alpha = 0.0

    def _update_dj(self):
        if not self._dj_mode:
            return
        for i in range(len(self._dj_bars)):
            target = random.uniform(0.1, 1.0)
            self._dj_bars[i] += (target - self._dj_bars[i]) * 0.2

    def _update_slot(self):
        if not self._slot_active:
            return
        self._slot_phase += 0.025
        if self._slot_phase < 1.0:
            # spinning — randomize all reels
            self._slot_reels = [random.choice(self._slot_spin_chars) for _ in range(3)]
        elif self._slot_phase < 2.0:
            # landing sequence — lock reels one by one
            landed = int((self._slot_phase - 1.0) * 3)
            finals = ['🚀', '💎', '🌙']
            for i in range(landed):
                self._slot_reels[i] = finals[i]
            for i in range(landed, 3):
                self._slot_reels[i] = random.choice(self._slot_spin_chars)
        elif self._slot_phase < 3.0:
            self._slot_reels = ['🚀', '💎', '🌙']
        else:
            self._slot_active = False

    def _update_pump(self):
        if not self._pump_active:
            return
        self._pump_y -= 14
        # Trail
        self._particles.add(Particle(
            x=self.width() // 2 + random.randint(-8, 8),
            y=self._pump_y + 30,
            vx=random.uniform(-2, 2), vy=random.uniform(1, 3),
            life=1.0, decay=0.06,
            color=random.choice([QColor('#00ff44'), QColor('#00cc88'), QColor('#ffffff')]),
            size=random.uniform(4, 10), shape='circle',
        ))
        if self._pump_y < -80:
            self._pump_active = False
            del self._active_effects['pump_text']

    def _update_dump(self):
        if not self._dump_active:
            return
        if self._dump_exploded:
            return
        self._dump_y += 14
        # Trail
        self._particles.add(Particle(
            x=self.width() // 2 + random.randint(-8, 8),
            y=self._dump_y - 30,
            vx=random.uniform(-2, 2), vy=random.uniform(-3, -1),
            life=1.0, decay=0.06,
            color=random.choice([QColor('#ff2244'), QColor('#ff6600'), QColor('#ffff00')]),
            size=random.uniform(4, 10), shape='circle',
        ))
        if self._dump_y > self.height() - 80:
            self._dump_exploded = True
            self._active_effects['dump_text'] = time.time() + 2.5
            # Explosion
            cx, cy = self.width() // 2, self.height() - 80
            for _ in range(60):
                angle = random.uniform(0, math.pi * 2)
                speed = random.uniform(3, 12)
                self._particles.add(Particle(
                    x=cx, y=cy,
                    vx=math.cos(angle) * speed, vy=math.sin(angle) * speed - 4,
                    life=1.0, decay=0.025,
                    color=random.choice([QColor('#ff2244'), QColor('#ff6600'), QColor('#ffff00'), QColor('#ff4400')]),
                    size=random.uniform(5, 14), shape=random.choice(['circle', 'star']),
                    spin=random.uniform(-8, 8),
                ))
        if 'dump_text' not in self._active_effects and self._dump_exploded:
            self._dump_active = False

    def _update_lambo(self):
        if not self._lambo_active:
            return
        self._lambo_x += 8
        # Exhaust trail
        if int(self._lambo_x) % 3 == 0:
            self._particles.add(Particle(
                x=self._lambo_x - 10,
                y=self.height() - 55 + random.randint(-4, 4),
                vx=random.uniform(-3, -1), vy=random.uniform(-1, 1),
                life=1.0, decay=0.04,
                color=random.choice([QColor('#888888'), QColor('#aaaaaa'), QColor('#ffaa44')]),
                size=random.uniform(4, 9), shape='circle',
            ))
        if self._lambo_x > self.width() + 220:
            self._lambo_active = False

    def _update_chad(self):
        if not self._chad_active:
            return
        self._chad_alpha -= 0.008
        if self._chad_alpha <= 0:
            self._chad_alpha = 0.0
            self._chad_active = False

    def _update_bruh(self):
        if self._bruh_alpha <= 0:
            return
        self._bruh_alpha -= 0.012
        if self._bruh_shake > 0:
            self._bruh_shake -= 1
        if self._bruh_alpha < 0:
            self._bruh_alpha = 0.0

    def _update_stonks(self):
        if not self._stonks_active:
            return
        if 'stonks' not in self._active_effects:
            self._stonks_alpha -= 0.015
            if self._stonks_alpha <= 0:
                self._stonks_alpha = 0.0
                self._stonks_active = False
        else:
            if self._stonks_alpha < 1.0:
                self._stonks_alpha = min(1.0, self._stonks_alpha + 0.05)

    def _update_rip(self):
        if not self._rip_active:
            return
        if not self._rip_landed:
            self._rip_y += 16
            if self._rip_y >= self.height() - 140:
                self._rip_y = self.height() - 140
                self._rip_landed = True
                self._rip_shake = 10
                self._active_effects['rip_show'] = time.time() + 3.0
                # Dust particles
                cx = self.width() // 2
                cy = int(self._rip_y) + 100
                for _ in range(25):
                    self._particles.add(Particle(
                        x=cx + random.randint(-40, 40), y=cy,
                        vx=random.uniform(-3, 3), vy=random.uniform(-4, -1),
                        life=1.0, decay=0.04,
                        color=QColor('#888866'),
                        size=random.uniform(4, 8), shape='circle',
                    ))
        else:
            if self._rip_shake > 0:
                self._rip_shake -= 1
            self._rip_f_alpha = min(1.0, self._rip_f_alpha + 0.03)
            if 'rip_show' not in self._active_effects:
                self._rip_active = False

    def _update_plane(self):
        if not self._plane_active:
            return
        self._plane_x += 10
        self._plane_y -= 3
        # Trail
        self._particles.add(Particle(
            x=self._plane_x - 15 + random.randint(-4, 4),
            y=self._plane_y + 8 + random.randint(-4, 4),
            vx=random.uniform(-1.5, -0.5), vy=random.uniform(-0.5, 0.5),
            life=1.0, decay=0.05,
            color=random.choice([QColor('#ccddff'), QColor('#ffffff'), QColor('#aaccff')]),
            size=random.uniform(2, 5), shape='circle',
        ))
        if self._plane_x > self.width() + 80 or self._plane_y < -80:
            self._plane_active = False

    def _update_breath(self):
        self._breath_phase += 0.003

    def _update_star(self):
        if not self._star_active:
            return
        self._star_x += self._star_vx
        self._star_y += self._star_vy
        self._star_life -= 0.025
        # Trail particles
        if random.random() < 0.7:
            self._particles.add(Particle(
                x=self._star_x, y=self._star_y,
                vx=random.uniform(-1, 1), vy=random.uniform(-1, 1),
                life=self._star_life, decay=0.06,
                color=random.choice([QColor('#ffffff'), QColor('#aaffff'), QColor('#88ddff')]),
                size=random.uniform(2, 6), shape='circle',
            ))
        if self._star_life <= 0 or self._star_x > self.width() + 50 or self._star_y > self.height() + 50:
            self._star_active = False

    def _update_glitch(self):
        if not self._glitch_active:
            return
        self._glitch_life -= 0.15
        if self._glitch_life <= 0 and 'vhs_glitch' not in self._active_effects:
            self._glitch_active = False

    def _update_signal_glow(self):
        if self._signal_glow_alpha > 0:
            self._signal_glow_alpha -= 0.008
            if self._signal_glow_alpha < 0:
                self._signal_glow_alpha = 0.0

    # ── Paint ────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if 'rainbow' in self._active_effects:
            self._draw_rainbow(p, w, h)
        if 'red_flash' in self._active_effects:
            self._draw_red_flash(p, w, h)
        if 'rasta' in self._active_effects:
            self._draw_rasta_overlay(p, w, h)
        if 'matrix' in self._active_effects:
            self._draw_matrix(p, w, h)
        if self._brrr_active:
            self._draw_brrr(p, w, h)

        # Breathing background (very subtle, always on)
        self._draw_breath(p, w, h)

        self._draw_flames(p)
        self._particles.draw(p)

        if self._rocket_active:
            self._draw_rocket(p)
        if self._banner_alpha > 0:
            self._draw_banner(p, w, h)
        if self._dj_mode:
            self._draw_dj(p, w, h)

        # New effects
        if self._slot_active:
            self._draw_slot(p, w, h)
        if self._pump_active:
            self._draw_pump(p, w, h)
        if 'pump_text' in self._active_effects and not self._pump_active:
            self._draw_pump_text(p, w, h)
        if self._dump_active or 'dump_text' in self._active_effects:
            self._draw_dump(p, w, h)
        if self._lambo_active:
            self._draw_lambo(p, w, h)
        if self._chad_active:
            self._draw_chad(p, w, h)
        if self._bruh_alpha > 0:
            self._draw_bruh(p, w, h)
        if self._stonks_active:
            self._draw_stonks(p, w, h)
        if self._rip_active:
            self._draw_rip(p, w, h)
        if self._plane_active:
            self._draw_plane(p, w, h)
        if self._star_active:
            self._draw_star(p)
        if self._glitch_active:
            self._draw_glitch(p, w, h)
        if self._signal_glow_alpha > 0:
            self._draw_signal_glow(p, w, h)

        p.end()

    def _draw_rainbow(self, p: QPainter, w: int, h: int):
        self._rainbow_phase += 0.03
        for i, color in enumerate(RAINBOW_COLORS):
            phase = (self._rainbow_phase + i * 0.5) % 1.0
            alpha = int(abs(math.sin(phase * math.pi)) * 30)
            c = QColor(color)
            c.setAlpha(alpha)
            p.fillRect(0, i * h // 7, w, h // 7 + 1, c)

    def _draw_red_flash(self, p: QPainter, w: int, h: int):
        remain = self._active_effects.get('red_flash', 0) - time.time()
        alpha = int(min(remain / 1.5, 1.0) * 180)
        p.fillRect(0, 0, w, h, QColor(220, 0, 0, alpha))

    def _draw_rasta_overlay(self, p: QPainter, w: int, h: int):
        rasta = [QColor('#ff0000'), QColor('#ffcc00'), QColor('#00aa44')]
        for i, c in enumerate(rasta):
            c2 = QColor(c)
            c2.setAlpha(18)
            p.fillRect(0, i * h // 3, w, h // 3 + 1, c2)

    def _draw_matrix(self, p: QPainter, w: int, h: int):
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, 60))
        for col in self._matrix_chars:
            if col['bright']:
                p.setPen(QColor('#aaffaa'))
            else:
                p.setPen(QColor('#00aa33'))
            p.setFont(QFont('Courier New', 11))
            p.drawText(int(col['x']), int(col['y']), col['char'])

    def _draw_brrr(self, p: QPainter, w: int, h: int):
        p.setFont(QFont('Courier New', 32, QFont.Bold))
        shake = random.randint(-3, 3)
        p.setPen(QColor(0, 200, 80, 180))
        text = 'BRRRRR'
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        p.drawText((w - tw) // 2 + shake, h // 2 + shake, text)

    def _draw_rocket(self, p: QPainter):
        p.setFont(QFont('Segoe UI Emoji', 36))
        p.setPen(Qt.white)
        p.drawText(QPointF(self._rocket_x, self._rocket_y), '🚀')

    def _draw_flames(self, p: QPainter):
        for f in self._flames:
            alpha = int(f['life'] * 200)
            ratio = 1.0 - f['life']
            r = int(255)
            g = int(180 * f['life'])
            b = 0
            c = QColor(r, g, b, alpha)
            p.setBrush(QBrush(c))
            p.setPen(Qt.NoPen)
            s = f['size'] * f['life']
            p.drawEllipse(QPointF(f['x'], f['y']), s, s)

    def _draw_banner(self, p: QPainter, w: int, h: int):
        is_unstoppable = self._banner_text == 'UNSTOPPABLE'
        alpha = int(self._banner_alpha * 255)

        font_size = 48 if is_unstoppable else 20
        p.setFont(QFont('Courier New', font_size, QFont.Bold))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(self._banner_text)
        tx = (w - tw) // 2
        ty = h // 3

        # Glow
        glow = QColor('#00ffff' if not is_unstoppable else '#ff8800')
        glow.setAlpha(alpha // 3)
        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3), (0, -3)]:
            p.setPen(glow)
            p.drawText(tx + dx, ty + dy, self._banner_text)

        # Main text
        text_color = QColor('#ffffff')
        text_color.setAlpha(alpha)
        p.setPen(text_color)
        p.drawText(tx, ty, self._banner_text)

    def _draw_dj(self, p: QPainter, w: int, h: int):
        n = len(self._dj_bars)
        bar_w = w // n
        for i, val in enumerate(self._dj_bars):
            bar_h = int(val * h * 0.4)
            hue = (i / n * 360 + time.time() * 60) % 360
            color = QColor.fromHsv(int(hue), 255, 255, 180)
            x = i * bar_w
            y = h - 80 - bar_h
            p.fillRect(x + 2, y, bar_w - 4, bar_h, color)

    def _draw_breath(self, p: QPainter, w: int, h: int):
        # Very subtle breathing background pulse between #000000 and #0a0a1a
        pulse = (math.sin(self._breath_phase) + 1) / 2   # 0→1
        b_val = int(pulse * 10)   # 0→10 (very dark blue)
        if b_val > 0:
            c = QColor(0, 0, b_val, 18)
            p.fillRect(0, 0, w, h, c)

    def _draw_slot(self, p: QPainter, w: int, h: int):
        cx, cy = w // 2, h // 2
        reel_w, reel_h = 90, 90
        gap = 12
        total_w = 3 * reel_w + 2 * gap
        x0 = cx - total_w // 2

        # fade
        if self._slot_phase > 2.5:
            fade = max(0.0, 1.0 - (self._slot_phase - 2.5) * 2)
        else:
            fade = 1.0

        # Background panel
        bg = QColor(0, 0, 0, int(200 * fade))
        border = QColor(255, 200, 0, int(220 * fade))
        p.setBrush(QBrush(bg))
        p.setPen(QPen(border, 3))
        p.drawRoundedRect(x0 - 20, cy - reel_h - 40, total_w + 40, reel_h + 80, 14, 14)

        # Title
        p.setFont(QFont('Courier New', 14, QFont.Bold))
        title_c = QColor('#ffd700')
        title_c.setAlpha(int(255 * fade))
        p.setPen(title_c)
        fm = p.fontMetrics()
        title = '🎰 YOLO MACHINE 🎰'
        p.drawText(cx - fm.horizontalAdvance(title) // 2, cy - reel_h - 10, title)

        # Reels
        for i, emoji in enumerate(self._slot_reels):
            rx = x0 + i * (reel_w + gap)
            reel_bg = QColor(20, 20, 30, int(230 * fade))
            p.setBrush(QBrush(reel_bg))
            p.setPen(QPen(QColor(100, 80, 200, int(200 * fade)), 2))
            p.drawRoundedRect(rx, cy - reel_h // 2, reel_w, reel_h, 8, 8)
            p.setFont(QFont('Segoe UI Emoji', 36))
            p.setPen(Qt.white)
            p.drawText(QPointF(rx + 14, cy + 22), emoji)

    def _draw_pump(self, p: QPainter, w: int, h: int):
        if not self._pump_active:
            return
        cx = w // 2
        # Arrow body
        pen = QPen(QColor(0, 255, 80, 220), 6)
        p.setPen(pen)
        p.drawLine(cx, int(self._pump_y) + 60, cx, int(self._pump_y))
        # Arrow head
        path = QPainterPath()
        path.moveTo(cx, self._pump_y - 20)
        path.lineTo(cx - 20, self._pump_y + 20)
        path.lineTo(cx + 20, self._pump_y + 20)
        path.closeSubpath()
        p.setBrush(QBrush(QColor(0, 255, 80, 220)))
        p.setPen(Qt.NoPen)
        p.drawPath(path)

    def _draw_pump_text(self, p: QPainter, w: int, h: int):
        remain = self._active_effects.get('pump_text', 0) - time.time()
        if remain <= 0:
            return
        alpha = int(min(remain / 2.5, 1.0) * 255)
        p.setFont(QFont('Courier New', 40, QFont.Bold))
        fm = p.fontMetrics()
        text = 'PUMP IT'
        tx = (w - fm.horizontalAdvance(text)) // 2
        glow = QColor(0, 255, 80, alpha // 4)
        for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3)]:
            p.setPen(glow)
            p.drawText(tx + dx, h // 2 + dy, text)
        c = QColor(0, 255, 80, alpha)
        p.setPen(c)
        p.drawText(tx, h // 2, text)

    def _draw_dump(self, p: QPainter, w: int, h: int):
        cx = w // 2
        if not self._dump_exploded:
            # Arrow head
            path = QPainterPath()
            path.moveTo(cx, self._dump_y + 20)
            path.lineTo(cx - 20, self._dump_y - 20)
            path.lineTo(cx + 20, self._dump_y - 20)
            path.closeSubpath()
            p.setBrush(QBrush(QColor(255, 30, 60, 220)))
            p.setPen(Qt.NoPen)
            p.drawPath(path)
            # Arrow body
            pen = QPen(QColor(255, 30, 60, 220), 6)
            p.setPen(pen)
            p.drawLine(cx, int(self._dump_y) - 20, cx, int(self._dump_y) - 80)
        elif 'dump_text' in self._active_effects:
            remain = self._active_effects.get('dump_text', 0) - time.time()
            alpha = int(min(remain / 2.5, 1.0) * 255)
            p.setFont(QFont('Courier New', 32, QFont.Bold))
            fm = p.fontMetrics()
            text = 'DRILL TEAM 6'
            tx = (w - fm.horizontalAdvance(text)) // 2
            glow = QColor(255, 30, 60, alpha // 4)
            for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3)]:
                p.setPen(glow)
                p.drawText(tx + dx, h // 2 + dy, text)
            c = QColor(255, 60, 60, alpha)
            p.setPen(c)
            p.drawText(tx, h // 2, text)

    def _draw_lambo(self, p: QPainter, w: int, h: int):
        # Pixel art Lamborghini using rectangles
        lx = int(self._lambo_x)
        ly = h - 80
        alpha = 240
        # Body
        body = QColor(255, 165, 0, alpha)  # orange
        accent = QColor(255, 200, 50, alpha)
        dark = QColor(20, 20, 20, alpha)
        chrome = QColor(200, 200, 220, alpha)

        def rect(rx, ry, rw, rh, col):
            p.fillRect(lx + rx, ly + ry, rw, rh, col)

        # Main body
        rect(20, 20, 140, 30, body)
        # Low roof
        rect(50, 0, 80, 24, body)
        rect(52, 2, 76, 20, accent)
        # Front spoiler
        rect(155, 40, 20, 8, dark)
        # Rear
        rect(0, 30, 24, 20, body)
        rect(0, 38, 10, 8, dark)
        # Windows (dark)
        rect(55, 4, 30, 16, dark)
        rect(90, 4, 36, 16, dark)
        # Headlights
        rect(156, 24, 10, 10, QColor(255, 255, 100, alpha))
        rect(10, 24, 10, 10, QColor(220, 80, 80, alpha))
        # Side stripe
        rect(25, 28, 130, 4, accent)
        # Wheels
        for wx in [30, 140]:
            p.setBrush(QBrush(dark))
            p.setPen(QPen(chrome, 2))
            p.drawEllipse(lx + wx, ly + 44, 26, 26)
            p.setBrush(QBrush(chrome))
            p.setPen(Qt.NoPen)
            p.drawEllipse(lx + wx + 8, ly + 52, 10, 10)

    def _draw_chad(self, p: QPainter, w: int, h: int):
        alpha = int(self._chad_alpha * 255)
        cx, cy = w // 2, h // 2
        # GigaChad ASCII outline using drawn text
        lines = [
            "  ████████████  ",
            " ██  ██████  ██ ",
            "██ ▄█▀    ▀█▄ ██",
            "██ ▀▀ ▄▄▄ ▀▀ ██ ",
            " ██  █▄▄▄█  ██  ",
            "  ████▀▀▀████   ",
            "    ████████    ",
            "  ██ CHAD ██    ",
        ]
        p.setFont(QFont('Courier New', 13, QFont.Bold))
        fm = p.fontMetrics()
        lh = fm.height() + 2
        total_h = len(lines) * lh
        for i, line in enumerate(lines):
            tw = fm.horizontalAdvance(line)
            tx = cx - tw // 2
            ty = cy - total_h // 2 + i * lh
            glow = QColor(0, 255, 200, alpha // 5)
            for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                p.setPen(glow)
                p.drawText(tx + dx, ty + dy, line)
            c = QColor(0, 255, 200, alpha)
            p.setPen(c)
            p.drawText(tx, ty, line)

    def _draw_bruh(self, p: QPainter, w: int, h: int):
        alpha = int(self._bruh_alpha * 255)
        shake = random.randint(-self._bruh_shake, self._bruh_shake) if self._bruh_shake > 0 else 0
        p.setFont(QFont('Courier New', 72, QFont.Bold))
        fm = p.fontMetrics()
        text = 'BRUH'
        tx = (w - fm.horizontalAdvance(text)) // 2
        ty = h // 2
        # Glow layers
        for r in range(3, 0, -1):
            glow = QColor(255, 100, 0, alpha // (r * 3))
            for dx, dy in [(-r*3, 0), (r*3, 0), (0, -r*3), (0, r*3)]:
                p.setPen(glow)
                p.drawText(tx + dx + shake, ty + dy + shake, text)
        c = QColor(255, 180, 0, alpha)
        p.setPen(c)
        p.drawText(tx + shake, ty + shake, text)

    def _draw_stonks(self, p: QPainter, w: int, h: int):
        alpha = int(self._stonks_alpha * 220)
        cx, cy = w // 3, h * 2 // 3

        # Stick figure (stonks man)
        pen = QPen(QColor(0, 200, 80, alpha), 3)
        p.setPen(pen)
        # head
        p.drawEllipse(cx - 10, cy - 70, 20, 20)
        # body
        p.drawLine(cx, cy - 50, cx, cy - 10)
        # arms
        p.drawLine(cx - 20, cy - 35, cx + 20, cy - 35)
        # legs
        p.drawLine(cx, cy - 10, cx - 15, cy + 20)
        p.drawLine(cx, cy - 10, cx + 15, cy + 20)

        # Diagonal arrow going up-right
        ax0, ay0 = cx + 40, cy + 10
        ax1, ay1 = cx + 160, cy - 110
        arrow_pen = QPen(QColor(0, 255, 80, alpha), 5)
        p.setPen(arrow_pen)
        p.drawLine(ax0, ay0, ax1, ay1)
        # Arrowhead
        path = QPainterPath()
        path.moveTo(ax1, ay1)
        path.lineTo(ax1 - 20, ay1 + 5)
        path.lineTo(ax1 - 5, ay1 + 20)
        path.closeSubpath()
        p.setBrush(QBrush(QColor(0, 255, 80, alpha)))
        p.setPen(Qt.NoPen)
        p.drawPath(path)

        # "STONKS" text
        p.setFont(QFont('Courier New', 28, QFont.Bold))
        text = 'STONKS'
        fm = p.fontMetrics()
        tc = QColor(0, 255, 80, alpha)
        p.setPen(tc)
        p.drawText(cx - 10, cy - 120, text)

    def _draw_rip(self, p: QPainter, w: int, h: int):
        cx = w // 2
        ty = int(self._rip_y)
        shake = random.randint(-self._rip_shake, self._rip_shake) if self._rip_shake > 0 else 0
        sx = cx + shake

        # Tombstone
        stone = QColor(160, 160, 170, 230)
        dark_stone = QColor(100, 100, 110, 230)
        p.setBrush(QBrush(stone))
        p.setPen(QPen(dark_stone, 2))
        # Base
        p.drawRect(sx - 45, ty + 80, 90, 20)
        # Stone body
        p.drawRect(sx - 35, ty + 20, 70, 70)
        # Arched top
        p.drawChord(sx - 35, ty, 70, 44, 0, 180 * 16)

        # RIP text on stone
        p.setFont(QFont('Courier New', 14, QFont.Bold))
        p.setPen(dark_stone)
        p.drawText(sx - 16, ty + 52, 'R.I.P')
        p.setFont(QFont('Courier New', 8))
        p.drawText(sx - 22, ty + 68, 'YOUR GAINS')

        # "F" press F to pay respects
        if self._rip_f_alpha > 0:
            f_alpha = int(self._rip_f_alpha * 255)
            p.setFont(QFont('Courier New', 48, QFont.Bold))
            fm = p.fontMetrics()
            f_text = 'F'
            f_c = QColor(200, 180, 255, f_alpha)
            glow = QColor(150, 100, 255, f_alpha // 4)
            fx = (w - fm.horizontalAdvance(f_text)) // 2
            fy = ty - 30
            for dx, dy in [(-4, 0), (4, 0), (0, -4), (0, 4)]:
                p.setPen(glow)
                p.drawText(fx + dx, fy + dy, f_text)
            p.setPen(f_c)
            p.drawText(fx, fy, f_text)

    def _draw_plane(self, p: QPainter, w: int, h: int):
        px, py = self._plane_x, self._plane_y
        p.setFont(QFont('Segoe UI Emoji', 30))
        p.setPen(Qt.white)
        p.save()
        p.translate(px, py)
        p.rotate(-17)  # slight upward tilt
        p.drawText(QPointF(0, 0), '✈️')
        p.restore()

    def _draw_star(self, p: QPainter):
        if not self._star_active:
            return
        alpha = int(self._star_life * 255)
        # Bright head
        head = QColor(255, 255, 255, alpha)
        p.setBrush(QBrush(head))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(self._star_x, self._star_y), 5, 5)
        # Glow halo
        glow = QColor(180, 240, 255, alpha // 4)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(self._star_x, self._star_y), 12, 12)

    def _draw_glitch(self, p: QPainter, w: int, h: int):
        if not self._glitch_active:
            return
        # Grab 3 horizontal slices and shift them
        alpha = int(min(self._glitch_life, 1.0) * 80)
        shift = self._glitch_shift
        # Draw a few colored horizontal bands with offset
        colors = [
            QColor(255, 0, 0, alpha),
            QColor(0, 255, 200, alpha // 2),
            QColor(255, 255, 0, alpha // 3),
        ]
        for i, c in enumerate(colors):
            band_y = random.randint(0, h - 20)
            band_h = random.randint(3, 15)
            dx = shift * (1 if i % 2 == 0 else -1)
            p.fillRect(dx, band_y, w, band_h, c)
        # Scanline glitch
        for _ in range(random.randint(2, 5)):
            gy = random.randint(0, h)
            p.fillRect(0, gy, w, 1, QColor(255, 255, 255, random.randint(20, 60)))

    def _draw_signal_glow(self, p: QPainter, w: int, h: int):
        alpha = int(self._signal_glow_alpha * 255)
        if not self._signal_glow_text or alpha <= 0:
            return
        p.setFont(QFont('Courier New', 22, QFont.Bold))
        fm = p.fontMetrics()
        text = f'⚡ {self._signal_glow_text} ⚡'
        tx = (w - fm.horizontalAdvance(text)) // 2
        ty = h // 4
        glow_c = QColor(0, 255, 150, alpha // 3)
        for r in range(4, 0, -1):
            for dx, dy in [(-r*2, 0), (r*2, 0), (0, -r*2), (0, r*2)]:
                p.setPen(glow_c)
                p.drawText(tx + dx, ty + dy, text)
        p.setPen(QColor(200, 255, 220, alpha))
        p.drawText(tx, ty, text)


# ─────────────────────────────────────────────
#  Space Invaders Mini-Game
# ─────────────────────────────────────────────

class SpaceInvadersGame(QWidget):
    """Minimal Space Invaders popup game."""

    closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self.setFixedSize(400, 350)
        self.setStyleSheet('background: #000; border: 2px solid #00ff88;')
        self.setWindowTitle('SPACE INVADERS')

        self._player_x = 190.0
        self._bullets: List[Dict] = []
        self._invaders: List[Dict] = []
        self._score = 0
        self._alive = True
        self._keys: set = set()

        self._init_invaders()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

    def _init_invaders(self):
        self._invaders = []
        for row in range(4):
            for col in range(9):
                self._invaders.append({
                    'x': 30.0 + col * 38,
                    'y': 30.0 + row * 28,
                    'alive': True,
                    'dx': 1.0,
                    'emoji': ['👾', '👽', '🛸', '💀'][row],
                })
        self._inv_dir = 1.0
        self._inv_move_timer = 0

    def keyPressEvent(self, event: QKeyEvent):
        self._keys.add(event.key())
        if event.key() == Qt.Key_Space:
            self._fire()
        if event.key() == Qt.Key_Escape:
            self._timer.stop()
            self.closed.emit()
            self.close()

    def keyReleaseEvent(self, event: QKeyEvent):
        self._keys.discard(event.key())

    def _fire(self):
        if len(self._bullets) < 5:
            self._bullets.append({'x': self._player_x + 10, 'y': 290.0})

    def _tick(self):
        if Qt.Key_Left in self._keys:
            self._player_x = max(0, self._player_x - 6)
        if Qt.Key_Right in self._keys:
            self._player_x = min(375, self._player_x + 6)

        # Move bullets
        alive_bullets = []
        for b in self._bullets:
            b['y'] -= 12
            if b['y'] > 0:
                alive_bullets.append(b)
        self._bullets = alive_bullets

        # Collision
        for inv in self._invaders:
            if not inv['alive']:
                continue
            for b in list(self._bullets):
                if abs(b['x'] - inv['x']) < 18 and abs(b['y'] - inv['y']) < 18:
                    inv['alive'] = False
                    self._bullets.remove(b)
                    self._score += 10
                    break

        # Move invaders
        self._inv_move_timer += 1
        if self._inv_move_timer >= 8:
            self._inv_move_timer = 0
            alive = [inv for inv in self._invaders if inv['alive']]
            if not alive:
                self._init_invaders()
                return
            max_x = max(inv['x'] for inv in alive)
            min_x = min(inv['x'] for inv in alive)
            if max_x >= 375 or min_x <= 5:
                self._inv_dir *= -1
                for inv in alive:
                    inv['y'] += 10
            for inv in alive:
                inv['x'] += self._inv_dir * 4

        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor('#000000'))

        # Score
        p.setFont(QFont('Courier New', 10, QFont.Bold))
        p.setPen(QColor('#00ff88'))
        p.drawText(10, 18, f'SCORE: {self._score}')
        p.setPen(QColor('#ff4444'))
        p.drawText(w - 100, 18, 'ESC = EXIT')

        # Invaders
        p.setFont(QFont('Segoe UI Emoji', 16))
        for inv in self._invaders:
            if inv['alive']:
                p.drawText(QPointF(inv['x'], inv['y'] + 16), inv['emoji'])

        # Player
        p.setFont(QFont('Segoe UI Emoji', 18))
        p.drawText(QPointF(self._player_x, 315), '🚀')

        # Bullets
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor('#00ffff')))
        for b in self._bullets:
            p.drawRect(int(b['x']), int(b['y']), 3, 10)

        p.end()


# ─────────────────────────────────────────────
#  Snake Mini-Game
# ─────────────────────────────────────────────

CELL = 14

class SnakeGame(QWidget):
    """Snake game popup (right-click news ticker)."""

    closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        cols, rows = 25, 20
        self.setFixedSize(cols * CELL, rows * CELL + 20)
        self.setStyleSheet('background: #000; border: 2px solid #ff00ff;')
        self._cols = cols
        self._rows = rows
        self._reset()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(120)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

    def _reset(self):
        mid = (self._cols // 2, self._rows // 2)
        self._snake = [mid, (mid[0] - 1, mid[1]), (mid[0] - 2, mid[1])]
        self._dir = (1, 0)
        self._next_dir = (1, 0)
        self._food = self._random_food()
        self._score = 0
        self._dead = False

    def _random_food(self) -> Tuple[int, int]:
        while True:
            pos = (random.randint(0, self._cols - 1), random.randint(0, self._rows - 1))
            if pos not in self._snake:
                return pos

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        dx, dy = self._dir
        if key == Qt.Key_Left and dx == 0:
            self._next_dir = (-1, 0)
        elif key == Qt.Key_Right and dx == 0:
            self._next_dir = (1, 0)
        elif key == Qt.Key_Up and dy == 0:
            self._next_dir = (0, -1)
        elif key == Qt.Key_Down and dy == 0:
            self._next_dir = (0, 1)
        elif key == Qt.Key_Escape:
            self._timer.stop()
            self.closed.emit()
            self.close()
        elif key == Qt.Key_R and self._dead:
            self._reset()

    def _tick(self):
        if self._dead:
            return
        self._dir = self._next_dir
        head = (self._snake[0][0] + self._dir[0], self._snake[0][1] + self._dir[1])
        if (head[0] < 0 or head[0] >= self._cols or
                head[1] < 0 or head[1] >= self._rows or
                head in self._snake):
            self._dead = True
        else:
            self._snake.insert(0, head)
            if head == self._food:
                self._score += 10
                self._food = self._random_food()
            else:
                self._snake.pop()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor('#080808'))

        p.setFont(QFont('Courier New', 9))
        p.setPen(QColor('#ff00ff'))
        p.drawText(4, 14, f'SCORE: {self._score}   ESC=EXIT  R=RESTART')

        off_y = 20
        # Grid
        p.setPen(QPen(QColor('#111'), 1))
        for x in range(0, w, CELL):
            p.drawLine(x, off_y, x, h)
        for y in range(off_y, h, CELL):
            p.drawLine(0, y, w, y)

        # Food
        p.fillRect(
            self._food[0] * CELL + 2, off_y + self._food[1] * CELL + 2,
            CELL - 4, CELL - 4, QColor('#ff4444')
        )

        # Snake
        for i, (sx, sy) in enumerate(self._snake):
            color = QColor('#00ff88') if i == 0 else QColor('#00cc66')
            p.fillRect(sx * CELL + 1, off_y + sy * CELL + 1, CELL - 2, CELL - 2, color)

        if self._dead:
            p.setFont(QFont('Courier New', 16, QFont.Bold))
            p.setPen(QColor('#ff4444'))
            p.drawText(self.rect(), Qt.AlignCenter, f'DEAD\nSCORE: {self._score}\nR to restart')

        p.end()


# ─────────────────────────────────────────────
#  Vanity Pixel Art (pill/substance animations)
# ─────────────────────────────────────────────

class VanitySprite(QWidget):
    """Small corner animation for vanity pixel art."""

    SPRITES = {
        'blue_pill': {'emoji': '💊', 'color': '#0088ff', 'motion': 'float'},
        'smiley_pill': {'emoji': '😊', 'color': '#ffcc00', 'motion': 'bounce'},
        'sparkle': {'emoji': '✨', 'color': '#ffffff', 'motion': 'sparkle'},
        'weed_leaf': {'emoji': '🍃', 'color': '#00aa44', 'motion': 'sway'},
        'lean_cup': {'emoji': '🥤', 'color': '#aa44ff', 'motion': 'vapor'},
    }

    def __init__(self, sprite_name: str, corner: str = 'br', parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._sprite = self.SPRITES.get(sprite_name, self.SPRITES['blue_pill'])
        self._corner = corner
        self._phase = random.uniform(0, math.pi * 2)
        self.setFixedSize(50, 60)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self):
        self._phase += 0.08
        if self._phase > math.pi * 100:
            self._phase -= math.pi * 100
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        motion = self._sprite['motion']
        if motion == 'float':
            off_y = int(math.sin(self._phase) * 6)
        elif motion == 'bounce':
            off_y = int(abs(math.sin(self._phase)) * -12)
        elif motion == 'sway':
            off_y = 0
            p.translate(int(math.sin(self._phase) * 4), 0)
        elif motion == 'vapor':
            off_y = int(math.sin(self._phase) * 3)
        else:
            off_y = 0

        # Vapor wisps
        if motion == 'vapor':
            for i in range(3):
                alpha = int(abs(math.sin(self._phase + i)) * 60)
                c = QColor(self._sprite['color'])
                c.setAlpha(alpha)
                p.setPen(c)
                p.setFont(QFont('Courier New', 8))
                woff = int(math.sin(self._phase * 0.7 + i) * 5)
                p.drawText(18 + woff, 10 + off_y - i * 8, '~')

        # Sparkle wisps
        if motion == 'sparkle':
            for i in range(4):
                a = math.radians(self._phase * 30 + i * 90)
                sx = 25 + int(math.cos(a) * 12)
                sy = 30 + int(math.sin(a) * 12)
                c = QColor('#ffff88')
                c.setAlpha(int(abs(math.sin(self._phase + i)) * 180))
                p.setPen(c)
                p.drawText(sx - 4, sy + 5, '·')

        p.setFont(QFont('Segoe UI Emoji', 22))
        p.setPen(Qt.white)
        p.drawText(8, 40 + off_y, self._sprite['emoji'])
        p.end()


# ─────────────────────────────────────────────
#  Easter Egg Manager
# ─────────────────────────────────────────────

class EasterEggManager(QObject):
    """Attached to the main window; intercepts keys + events, fires effects."""

    def __init__(self, main_window: QWidget):
        super().__init__(main_window)
        self._win = main_window
        self._overlay = EasterEggOverlay(main_window)
        self._overlay.resize(main_window.size())
        self._overlay.show()

        self._konami_buf: List[int] = []
        self._type_buf = ''
        self._logo_clicks = 0
        self._last_logo_click = 0.0
        self._last_activity = time.time()
        self._idle_triggered = False

        self._win_streak = 0
        self._loss_streak = 0

        self._space_invaders: Optional[SpaceInvadersGame] = None
        self._snake: Optional[SnakeGame] = None

        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_timer.start(10_000)

        # ── Random auto-egg timer ──────────────────────────────────
        # Fires every 30–60s and triggers a random effect
        self._random_egg_timer = QTimer(self)
        self._random_egg_timer.setSingleShot(True)
        self._random_egg_timer.timeout.connect(self._fire_random_egg)
        self._random_egg_timer.start(random.randint(20_000, 45_000))

        # Shooting star timer (60–120s)
        self._star_timer = QTimer(self)
        self._star_timer.setSingleShot(True)
        self._star_timer.timeout.connect(self._auto_shooting_star)
        self._star_timer.start(random.randint(15_000, 40_000))

        # VHS glitch timer (3–5 min)
        self._glitch_timer = QTimer(self)
        self._glitch_timer.setSingleShot(True)
        self._glitch_timer.timeout.connect(self._auto_glitch)
        self._glitch_timer.start(random.randint(60_000, 120_000))

        # Last seen price for milestone detection
        self._last_milestone_price: Optional[float] = None

        # Install event filter on main window
        self._win.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.KeyPress:
            self._handle_key(event)
            self._last_activity = time.time()
            self._idle_triggered = False
        if event.type() == QEvent.MouseButtonPress:
            self._last_activity = time.time()
            self._idle_triggered = False
        if event.type() == QEvent.Resize:
            self._overlay.resize(self._win.size())
        return False

    def _handle_key(self, event: QKeyEvent):
        key = event.key()

        # Konami buffer
        self._konami_buf.append(key)
        if len(self._konami_buf) > len(KONAMI):
            self._konami_buf.pop(0)
        if self._konami_buf == list(KONAMI):
            self._overlay.trigger_rainbow()
            self._konami_buf.clear()
            return

        # Text buffer
        char = event.text().upper()
        if char.isalnum():
            self._type_buf = (self._type_buf + char)[-10:]
            self._check_keywords()

    def _check_keywords(self):
        buf = self._type_buf
        triggers = {
            'MOON':   self._overlay.trigger_rocket,
            'GUH':    self._overlay.trigger_red_flash,
            'TENDIES': self._overlay.trigger_tendies_rain,
            '420':    self._overlay.trigger_weed_rain,
            'PRINT':  self._overlay.trigger_brrr,
            'YOLO':   self._overlay.trigger_yolo,
            'PUMP':   self._overlay.trigger_pump,
            'DUMP':   self._overlay.trigger_dump,
            'LAMBO':  self._overlay.trigger_lambo,
            'CHAD':   self._overlay.trigger_chad,
            'HODL':   self._overlay.trigger_hodl,
            'BRUH':   self._overlay.trigger_bruh,
            'STONKS': self._overlay.trigger_stonks,
            'RIP':    self._overlay.trigger_rip,
            'SENDIT': self._overlay.trigger_sendit,
        }
        for word, fn in triggers.items():
            if buf.endswith(word):
                fn()
                self._type_buf = ''
                return

    def on_logo_click(self):
        now = time.time()
        if now - self._last_logo_click > 2.0:
            self._logo_clicks = 0
        self._last_logo_click = now
        self._logo_clicks += 1
        if self._logo_clicks >= 7:
            self._logo_clicks = 0
            self._launch_space_invaders()

    def on_news_right_click(self):
        self._launch_snake()

    def on_time_shift_click(self):
        self._overlay.trigger_dj_mode()

    def on_trade_result(self, is_win: bool, pnl: float = 0.0):
        if is_win:
            self._win_streak += 1
            self._loss_streak = 0
            if self._win_streak == 3:
                self._overlay.trigger_unstoppable()
            elif self._win_streak > 3 and self._win_streak % 3 == 0:
                self._overlay.trigger_levelup(f'{self._win_streak} WINS IN A ROW!')
        else:
            self._loss_streak += 1
            self._win_streak = 0
            if self._loss_streak == 3:
                self._overlay.trigger_motivation()

        milestones = [100, 500, 1000, 5000, 10000]
        for m in milestones:
            if abs(pnl) >= m and abs(pnl - m) < 50:
                self._overlay.trigger_levelup(f'${int(pnl):,} P&L!')
                break

    def on_signal(self, direction: str):
        """Called when a signal fires — optionally trigger a signal fire effect."""
        pass  # high-confidence check done in update_market_data via confidence field

    def on_high_confidence_signal(self, direction: str, confidence: float):
        """Called from app._on_signal when confidence > 0.8."""
        if confidence > 0.8:
            self._overlay.trigger_signal_fire(f'{direction} {confidence:.0%}')

    def update_market_data(self, price: float):
        """Called on every price update — detect round-number milestones."""
        if self._last_milestone_price is None:
            self._last_milestone_price = price
            return
        # Check if price crossed a round number (multiples of 25)
        step = 25.0
        old_bucket = int(self._last_milestone_price / step)
        new_bucket = int(price / step)
        if old_bucket != new_bucket:
            self._overlay.trigger_price_milestone()
        self._last_milestone_price = price

    def _fire_random_egg(self):
        """Trigger a random easter egg effect, then reschedule."""
        effects = [
            self._overlay.trigger_rocket,
            self._overlay.trigger_yolo,
            self._overlay.trigger_pump,
            self._overlay.trigger_dump,
            self._overlay.trigger_lambo,
            self._overlay.trigger_chad,
            self._overlay.trigger_hodl,
            self._overlay.trigger_bruh,
            self._overlay.trigger_stonks,
            self._overlay.trigger_rip,
            self._overlay.trigger_sendit,
            self._overlay.trigger_tendies_rain,
            self._overlay.trigger_weed_rain,
            self._overlay.trigger_brrr,
            self._overlay.trigger_levelup,
        ]
        random.choice(effects)()
        # Reschedule 30–60 seconds later
        self._random_egg_timer.start(random.randint(30_000, 60_000))

    def _auto_shooting_star(self):
        self._overlay.trigger_shooting_star()
        self._star_timer.start(random.randint(20_000, 60_000))

    def _auto_glitch(self):
        self._overlay.trigger_glitch_frame()
        self._glitch_timer.start(random.randint(120_000, 300_000))

    def _check_idle(self):
        elapsed = time.time() - self._last_activity
        if elapsed >= 300 and not self._idle_triggered:
            self._idle_triggered = True
            self._overlay.trigger_matrix()

    def _launch_space_invaders(self):
        if self._space_invaders and not self._space_invaders.isHidden():
            return
        self._space_invaders = SpaceInvadersGame()
        win_geo = self._win.geometry()
        self._space_invaders.move(
            win_geo.center().x() - 200,
            win_geo.center().y() - 175,
        )
        self._space_invaders.show()

    def _launch_snake(self):
        if self._snake and not self._snake.isHidden():
            return
        self._snake = SnakeGame()
        win_geo = self._win.geometry()
        self._snake.move(
            win_geo.center().x() - 175,
            win_geo.center().y() - 150,
        )
        self._snake.show()

    def toggle_vanity(self, name: str):
        """Spawn a vanity sprite in a corner."""
        corners = ['bl', 'br', 'tl', 'tr']
        sprite = VanitySprite(name, random.choice(corners), self._win)
        sprite.show()
        self._position_sprite(sprite)

    def _position_sprite(self, sprite: VanitySprite):
        w, h = self._win.width(), self._win.height()
        c = sprite._corner
        if c == 'br':
            sprite.move(w - 55, h - 65)
        elif c == 'bl':
            sprite.move(5, h - 65)
        elif c == 'tr':
            sprite.move(w - 55, 40)
        elif c == 'tl':
            sprite.move(5, 40)
