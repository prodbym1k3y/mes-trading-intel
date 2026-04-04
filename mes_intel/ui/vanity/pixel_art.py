"""Animated pixel art vanity elements for the MES Intel UI.

All elements are small (30-40px max), 8-bit pixel art style,
drawn with QPainter. Each has show/hide/dance methods.
Managed by VanityManager which handles placement, visibility,
and Konami code activation.
"""
from __future__ import annotations
import math
import random
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QBrush
from ..theme import COLORS


# ---------------------------------------------------------------------------
# Base class for vanity elements
# ---------------------------------------------------------------------------

class _VanityElement(QWidget):
    """Base class for all pixel art vanity widgets.

    Provides animation timer, show/hide, and dance mode.
    Subclasses implement _draw_pixels(painter, w, h, frame).
    """

    def __init__(self, parent: QWidget | None = None, size: tuple[int, int] = (30, 30)):
        super().__init__(parent)
        self.setFixedSize(*size)
        self.hide()

        self._frame = 0
        self._dancing = False
        self._dance_phase = random.uniform(0, 2 * math.pi)
        self._base_x = 0
        self._base_y = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(200)  # ~5 fps — decorative only

    def _tick(self):
        self._frame += 1
        if self._dancing:
            # Sine wave bounce
            dx = int(6 * math.sin(self._frame * 0.15 + self._dance_phase))
            dy = int(4 * math.sin(self._frame * 0.2 + self._dance_phase + 1.0))
            self.move(self._base_x + dx, self._base_y + dy)
        self.update()

    def show_at(self, x: int, y: int):
        self._base_x = x
        self._base_y = y
        self.move(x, y)
        self.show()
        self.raise_()

    def dance(self, on: bool = True):
        self._dancing = on
        if not on:
            self.move(self._base_x, self._base_y)

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()

        if self._dancing:
            # Rotation in dance mode
            painter.translate(w / 2, h / 2)
            angle = 10 * math.sin(self._frame * 0.12 + self._dance_phase)
            painter.rotate(angle)
            painter.translate(-w / 2, -h / 2)

        self._draw_pixels(painter, w, h, self._frame)
        painter.end()

    def _draw_pixels(self, painter: QPainter, w: int, h: int, frame: int):
        """Override in subclasses to draw the pixel art."""
        pass

    @staticmethod
    def _px(painter: QPainter, x: int, y: int, color: QColor, scale: int = 2):
        """Draw a single scaled pixel."""
        painter.fillRect(x * scale, y * scale, scale, scale, color)


# ---------------------------------------------------------------------------
# 1. Blue Pill
# ---------------------------------------------------------------------------

class BluePill(_VanityElement):
    """Blue pill (perc 30). 8x12 pixel capsule with glow pulse."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, size=(20, 30))

    def _draw_pixels(self, painter: QPainter, w: int, h: int, frame: int):
        s = 2  # pixel scale
        blue_main = QColor(30, 100, 220)
        blue_dark = QColor(20, 60, 160)
        blue_light = QColor(80, 150, 255)
        white = QColor(230, 230, 240)
        white_dim = QColor(200, 200, 210)

        # Glow pulse
        pulse = 0.6 + 0.4 * math.sin(frame * 0.08)
        glow = QColor(30, 100, 255, int(40 * pulse))
        painter.fillRect(0, 0, w, h, glow)

        # Capsule shape (8 wide, 12 tall at scale 2 = 16x24 -> fits in 20x30)
        # Top rounded part (blue)
        shape_blue = [
            (2, 0), (3, 0), (4, 0), (5, 0),
            (1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1),
            (1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2),
            (1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (6, 3),
            (1, 4), (2, 4), (3, 4), (4, 4), (5, 4), (6, 4),
        ]
        for px, py in shape_blue:
            c = blue_light if px <= 3 else blue_main
            self._px(painter, px + 1, py + 1, c, s)

        # Highlight
        self._px(painter, 3, 1, blue_light, s)
        self._px(painter, 4, 1, blue_light, s)

        # White band (middle)
        for px in range(1, 7):
            self._px(painter, px + 1, 6, white, s)
            self._px(painter, px + 1, 7, white_dim, s)

        # Bottom part (blue darker)
        shape_bot = [
            (1, 8), (2, 8), (3, 8), (4, 8), (5, 8), (6, 8),
            (1, 9), (2, 9), (3, 9), (4, 9), (5, 9), (6, 9),
            (1, 10), (2, 10), (3, 10), (4, 10), (5, 10), (6, 10),
            (2, 11), (3, 11), (4, 11), (5, 11),
        ]
        for px, py in shape_bot:
            self._px(painter, px + 1, py + 1, blue_dark, s)


# ---------------------------------------------------------------------------
# 2. Ecstasy Pill
# ---------------------------------------------------------------------------

class EcstasyPill(_VanityElement):
    """Circular pill with smiley face and rainbow color cycling. 10x10 pixels."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, size=(24, 24))

    def _draw_pixels(self, painter: QPainter, w: int, h: int, frame: int):
        s = 2
        # Rainbow cycle
        hue = (frame * 3) % 360
        base = QColor.fromHsv(hue, 200, 220)
        dark = QColor.fromHsv(hue, 220, 160)

        # Circular pill body
        circle = [
            (3, 0), (4, 0), (5, 0), (6, 0),
            (2, 1), (3, 1), (4, 1), (5, 1), (6, 1), (7, 1),
            (1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2), (7, 2), (8, 2),
            (1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (6, 3), (7, 3), (8, 3),
            (1, 4), (2, 4), (3, 4), (4, 4), (5, 4), (6, 4), (7, 4), (8, 4),
            (1, 5), (2, 5), (3, 5), (4, 5), (5, 5), (6, 5), (7, 5), (8, 5),
            (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6), (7, 6), (8, 6),
            (1, 7), (2, 7), (3, 7), (4, 7), (5, 7), (6, 7), (7, 7), (8, 7),
            (2, 8), (3, 8), (4, 8), (5, 8), (6, 8), (7, 8),
            (3, 9), (4, 9), (5, 9), (6, 9),
        ]
        for px, py in circle:
            self._px(painter, px + 1, py + 1, base, s)

        # Smiley face (dark pixels)
        eyes = [(4, 3), (6, 3)]
        for px, py in eyes:
            self._px(painter, px + 1, py + 1, dark, s)

        # Mouth
        mouth = [(3, 6), (4, 7), (5, 7), (6, 6)]
        for px, py in mouth:
            self._px(painter, px + 1, py + 1, dark, s)


# ---------------------------------------------------------------------------
# 3. Cocaine Line
# ---------------------------------------------------------------------------

class CocaineLine(_VanityElement):
    """White line with sparkle particles above. 20x4 pixels."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, size=(40, 16))
        self._sparkles: list[tuple[int, int, int]] = []  # x, y, life

    def _draw_pixels(self, painter: QPainter, w: int, h: int, frame: int):
        s = 2
        white = QColor(240, 240, 250)
        off_white = QColor(220, 220, 230)

        # Main line
        for px in range(2, 18):
            jitter = random.randint(0, 1) if frame % 8 == 0 else 0
            self._px(painter, px, 5 + jitter, white, s)
            if random.random() < 0.3:
                self._px(painter, px, 6, off_white, s)

        # Razor blade (small rectangle at end)
        blade = QColor(180, 180, 190)
        for py in range(4, 7):
            self._px(painter, 18, py, blade, s)
            self._px(painter, 19, py, QColor(160, 160, 170), s)

        # Sparkles
        if frame % 4 == 0 and random.random() < 0.6:
            sx = random.randint(3, 17)
            sy = random.randint(1, 3)
            self._sparkles.append((sx, sy, 8))

        new_sparkles = []
        for sx, sy, life in self._sparkles:
            if life > 0:
                alpha = int(255 * (life / 8))
                sparkle_c = QColor(255, 255, 255, alpha)
                self._px(painter, sx, sy, sparkle_c, s)
                new_sparkles.append((sx, sy - (1 if frame % 3 == 0 else 0), life - 1))
        self._sparkles = new_sparkles


# ---------------------------------------------------------------------------
# 4. Weed Leaf
# ---------------------------------------------------------------------------

class WeedLeaf(_VanityElement):
    """5-pointed cannabis leaf with smoke wisps rising."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, size=(30, 36))
        self._smoke: list[tuple[float, float, int]] = []

    def _draw_pixels(self, painter: QPainter, w: int, h: int, frame: int):
        s = 2
        green = QColor(30, 180, 40)
        dark_green = QColor(15, 120, 25)
        stem = QColor(80, 140, 50)

        # Simplified 5-pointed leaf shape
        # Center blade
        blade_c = [(6, 2), (7, 2), (6, 3), (7, 3), (6, 4), (7, 4),
                    (6, 5), (7, 5), (6, 6), (7, 6), (6, 7), (7, 7),
                    (6, 8), (7, 8)]
        for px, py in blade_c:
            self._px(painter, px, py + 2, green, s)

        # Left blades
        lb = [(4, 5), (3, 4), (2, 3), (5, 6), (4, 7), (3, 8),
              (5, 5), (4, 6)]
        for px, py in lb:
            self._px(painter, px, py + 2, green, s)

        # Right blades
        rb = [(9, 5), (10, 4), (11, 3), (8, 6), (9, 7), (10, 8),
              (8, 5), (9, 6)]
        for px, py in rb:
            self._px(painter, px, py + 2, green, s)

        # Veins (darker)
        veins = [(6, 4), (7, 4), (6, 6), (7, 6), (5, 5), (8, 5)]
        for px, py in veins:
            self._px(painter, px, py + 2, dark_green, s)

        # Stem
        for py in range(10, 14):
            self._px(painter, 6, py + 2, stem, s)
            self._px(painter, 7, py + 2, stem, s)

        # Smoke wisps
        if frame % 6 == 0 and random.random() < 0.4:
            sx = 6.5 + random.uniform(-1, 1)
            self._smoke.append((sx, 2.0, 20))

        new_smoke = []
        for sx, sy, life in self._smoke:
            if life > 0 and sy > -3:
                alpha = int(120 * (life / 20))
                gray = QColor(180, 180, 180, alpha)
                drift = math.sin(frame * 0.1 + sx) * 0.3
                self._px(painter, int(sx + drift), int(sy), gray, s)
                new_smoke.append((sx + drift * 0.1, sy - 0.15, life - 1))
        self._smoke = new_smoke


# ---------------------------------------------------------------------------
# 5. Double Cup Lean
# ---------------------------------------------------------------------------

class DoubleCupLean(_VanityElement):
    """Two stacked styrofoam cups with purple vapor rising."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, size=(28, 38))
        self._vapor: list[tuple[float, float, int]] = []

    def _draw_pixels(self, painter: QPainter, w: int, h: int, frame: int):
        s = 2
        cup_white = QColor(240, 235, 230)
        cup_shadow = QColor(210, 205, 200)
        purple_fill = QColor(140, 50, 180)
        purple_dark = QColor(100, 30, 140)
        lid = QColor(220, 215, 210)

        # Top cup
        # Lid
        for px in range(3, 11):
            self._px(painter, px, 4, lid, s)
        # Body (tapered)
        for py in range(5, 12):
            indent = max(0, (11 - py) // 3)
            for px in range(3 + indent, 11 - indent):
                c = cup_white if px < 7 else cup_shadow
                self._px(painter, px, py, c, s)
        # Purple fill visible at top
        for px in range(4, 10):
            self._px(painter, px, 5, purple_fill, s)
            self._px(painter, px, 6, purple_dark, s)

        # Bottom cup (slightly offset)
        for px in range(4, 12):
            self._px(painter, px, 11, lid, s)
        for py in range(12, 18):
            indent = max(0, (17 - py) // 3)
            for px in range(4 + indent, 12 - indent):
                c = cup_white if px < 8 else cup_shadow
                self._px(painter, px, py, c, s)

        # Straw
        straw_c = QColor(180, 180, 190)
        for py in range(1, 8):
            self._px(painter, 9, py, straw_c, s)

        # Purple vapor
        if frame % 5 == 0 and random.random() < 0.5:
            vx = 7.0 + random.uniform(-1.5, 1.5)
            self._vapor.append((vx, 3.0, 16))

        new_vapor = []
        for vx, vy, life in self._vapor:
            if life > 0 and vy > -2:
                alpha = int(100 * (life / 16))
                purple_v = QColor(160, 60, 220, alpha)
                drift = math.sin(frame * 0.08 + vx) * 0.4
                self._px(painter, int(vx + drift), int(vy), purple_v, s)
                new_vapor.append((vx + drift * 0.08, vy - 0.12, life - 1))
        self._vapor = new_vapor


# ---------------------------------------------------------------------------
# 6. Vanity Manager
# ---------------------------------------------------------------------------

class VanityManager:
    """Manages all vanity pixel art elements.

    - Toggleable visibility (default: hidden)
    - Places elements in random non-critical areas (corners, margins)
    - Konami code activates "dance mode"
    - Each element has show/hide/dance methods

    Usage:
        manager = VanityManager(main_window)
        manager.set_visible(True)
        # Konami code sequence listening is handled internally.
    """

    KONAMI = [
        Qt.Key.Key_Up, Qt.Key.Key_Up,
        Qt.Key.Key_Down, Qt.Key.Key_Down,
        Qt.Key.Key_Left, Qt.Key.Key_Right,
        Qt.Key.Key_Left, Qt.Key.Key_Right,
        Qt.Key.Key_B, Qt.Key.Key_A,
    ]

    def __init__(self, parent_widget: QWidget):
        self._parent = parent_widget
        self._visible = False
        self._dance_mode = False
        self._konami_progress = 0

        # Create all elements
        self._elements: list[_VanityElement] = [
            BluePill(parent_widget),
            EcstasyPill(parent_widget),
            CocaineLine(parent_widget),
            WeedLeaf(parent_widget),
            DoubleCupLean(parent_widget),
        ]

        # All start hidden
        for el in self._elements:
            el.hide()

    @property
    def elements(self) -> list[_VanityElement]:
        return list(self._elements)

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def dance_active(self) -> bool:
        return self._dance_mode

    def set_visible(self, show: bool):
        """Toggle visibility of all vanity elements."""
        self._visible = show
        if show:
            self._place_elements()
            for el in self._elements:
                el.show()
                el.raise_()
        else:
            for el in self._elements:
                el.hide()
            self._dance_mode = False
            for el in self._elements:
                el.dance(False)

    def toggle(self):
        self.set_visible(not self._visible)

    def _place_elements(self):
        """Place elements in random corners/margins of the parent widget."""
        pw = self._parent.width()
        ph = self._parent.height()
        margin = 10

        # Define candidate positions (corners and edges)
        positions = [
            (margin, margin),                            # top-left
            (pw - 50, margin),                           # top-right
            (margin, ph - 50),                           # bottom-left
            (pw - 50, ph - 50),                          # bottom-right
            (pw // 2 - 15, ph - 45),                     # bottom-center
            (pw - 45, ph // 2 - 15),                     # right-center
        ]
        random.shuffle(positions)

        for i, el in enumerate(self._elements):
            if i < len(positions):
                x, y = positions[i]
            else:
                x = random.randint(margin, max(margin + 1, pw - 50))
                y = random.randint(margin, max(margin + 1, ph - 50))
            el.show_at(x, y)

    def handle_key(self, key: int) -> bool:
        """Feed a key press to check for Konami code.

        Returns True if Konami code was just completed (dance mode toggled).
        Call this from the parent widget's keyPressEvent.
        """
        if key == self.KONAMI[self._konami_progress]:
            self._konami_progress += 1
            if self._konami_progress >= len(self.KONAMI):
                self._konami_progress = 0
                self._toggle_dance()
                return True
        else:
            self._konami_progress = 0
        return False

    def _toggle_dance(self):
        """Toggle dance mode on all elements."""
        self._dance_mode = not self._dance_mode
        if not self._visible:
            self.set_visible(True)
        for el in self._elements:
            el.dance(self._dance_mode)

    def reposition(self):
        """Call on parent resize to reposition elements."""
        if self._visible:
            self._place_elements()
