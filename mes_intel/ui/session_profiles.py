"""Session Profile Manager — RTH and Overnight profiles with volume + delta.

RTH:       6:30 AM – 2:00 PM  Phoenix (America/Phoenix, no DST)
Overnight: 3:00 PM – 6:29:59 AM Phoenix (next day)

Each session: Volume Profile histogram + Delta Profile, POC/VAH/VAL markers,
prior session reference lines, session stats panel.
RTH = cyan/green palette; Overnight = purple/magenta palette.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QButtonGroup, QSizePolicy, QSplitter,
)
from PySide6.QtCore import Qt, QTimer, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QLinearGradient,
    QPainterPath,
)

# ─────────────────────────────────────────────
#  Phoenix timezone helper (UTC-7, no DST)
# ─────────────────────────────────────────────

PHOENIX_TZ = timezone(timedelta(hours=-7))


def phoenix_now() -> datetime:
    return datetime.now(tz=PHOENIX_TZ)


def phoenix_time(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=PHOENIX_TZ)


def session_for(dt: datetime) -> str:
    """Return 'RTH' or 'overnight' for a Phoenix datetime."""
    h, m = dt.hour, dt.minute
    minutes = h * 60 + m
    rth_start = 6 * 60 + 30    # 6:30 AM
    rth_end = 14 * 60           # 2:00 PM
    if rth_start <= minutes < rth_end:
        return 'RTH'
    return 'overnight'


def current_session() -> str:
    return session_for(phoenix_now())


# ─────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────

TICK = 0.25  # MES tick size


def round_tick(price: float) -> float:
    return round(price / TICK) * TICK


@dataclass
class ProfileLevel:
    price: float
    volume: int = 0
    buy_vol: int = 0
    sell_vol: int = 0

    @property
    def delta(self) -> int:
        return self.buy_vol - self.sell_vol


@dataclass
class SessionProfile:
    session_type: str   # 'RTH' | 'overnight'
    date_label: str
    levels: Dict[float, ProfileLevel] = field(default_factory=dict)
    open_price: float = 0.0
    high_price: float = -math.inf
    low_price: float = math.inf
    close_price: float = 0.0
    total_volume: int = 0
    total_delta: int = 0
    start_ts: float = 0.0
    end_ts: float = 0.0
    active: bool = True

    # Value area (70% of volume)
    vah: float = 0.0
    val: float = 0.0
    poc: float = 0.0

    def add_trade(self, price: float, vol: int, side: str):
        tick = round_tick(price)
        if tick not in self.levels:
            self.levels[tick] = ProfileLevel(price=tick)
        lv = self.levels[tick]
        lv.volume += vol
        if side == 'buy':
            lv.buy_vol += vol
        else:
            lv.sell_vol += vol
        self.total_volume += vol
        self.total_delta += vol if side == 'buy' else -vol
        if price > self.high_price:
            self.high_price = price
        if price < self.low_price:
            self.low_price = price
        self.close_price = price
        if self.open_price == 0.0:
            self.open_price = price
        self._recalc_value_area()

    def _recalc_value_area(self):
        if not self.levels:
            return
        prices = sorted(self.levels.keys())
        vols = [self.levels[p].volume for p in prices]
        total = sum(vols) or 1

        # POC = max volume level
        poc_idx = vols.index(max(vols))
        self.poc = prices[poc_idx]

        # Value area: expand from POC until 40%
        target = total * 0.40
        lo_idx = hi_idx = poc_idx
        included = vols[poc_idx]

        while included < target:
            can_up = hi_idx < len(prices) - 1
            can_dn = lo_idx > 0
            if not can_up and not can_dn:
                break
            add_up = vols[hi_idx + 1] if can_up else -1
            add_dn = vols[lo_idx - 1] if can_dn else -1
            if add_up >= add_dn and can_up:
                hi_idx += 1
                included += vols[hi_idx]
            elif can_dn:
                lo_idx -= 1
                included += vols[lo_idx]
            else:
                hi_idx += 1
                included += vols[hi_idx]

        self.vah = prices[hi_idx]
        self.val = prices[lo_idx]

    def range_size(self) -> float:
        if self.high_price == -math.inf or self.low_price == math.inf:
            return 0.0
        return self.high_price - self.low_price

    def elapsed_minutes(self) -> float:
        if self.start_ts == 0:
            return 0.0
        end = self.end_ts if not self.active else time.time()
        return (end - self.start_ts) / 60.0


# ─────────────────────────────────────────────
#  Session Manager
# ─────────────────────────────────────────────

class SessionManager:
    """Tracks current and prior sessions, auto-detects RTH vs overnight."""

    MAX_HISTORY = 10

    def __init__(self):
        self._sessions: List[SessionProfile] = []
        self._current: Optional[SessionProfile] = None
        self._last_session_type: Optional[str] = None

    def process_trade(self, timestamp: float, price: float, vol: int, side: str):
        dt = phoenix_time(timestamp)
        stype = session_for(dt)
        date_label = dt.strftime('%m/%d %H:%M')

        if self._current is None or stype != self._last_session_type:
            self._rotate(stype, date_label, timestamp)

        self._current.add_trade(price, vol, side)

    def _rotate(self, stype: str, date_label: str, ts: float):
        if self._current is not None:
            self._current.active = False
            self._current.end_ts = ts

        profile = SessionProfile(
            session_type=stype,
            date_label=date_label,
            start_ts=ts,
        )
        self._sessions.append(profile)
        if len(self._sessions) > self.MAX_HISTORY:
            self._sessions.pop(0)
        self._current = profile
        self._last_session_type = stype

    @property
    def current(self) -> Optional[SessionProfile]:
        return self._current

    @property
    def prior(self) -> Optional[SessionProfile]:
        completed = [s for s in self._sessions if not s.active]
        return completed[-1] if completed else None

    def history(self, n: int = 5) -> List[SessionProfile]:
        return self._sessions[-n:]


# ─────────────────────────────────────────────
#  Volume + Delta Profile Painter
# ─────────────────────────────────────────────

RTH_COLORS = {
    'vol_fill': QColor('#00ff8866'),
    'vol_border': QColor('#00ff88'),
    'delta_pos': QColor('#00ffcc88'),
    'delta_neg': QColor('#ff445588'),
    'poc': QColor('#ffff00'),
    'vah': QColor('#00ffcc'),
    'val': QColor('#00ffcc'),
    'text': QColor('#aaffdd'),
    'bg': QColor('#080f10'),
    'header': QColor('#00ffff'),
    'ref_line': QColor('#00ff8840'),
}

NIGHT_COLORS = {
    'vol_fill': QColor('#cc00ff66'),
    'vol_border': QColor('#cc44ff'),
    'delta_pos': QColor('#ff88ff88'),
    'delta_neg': QColor('#ff224488'),
    'poc': QColor('#ffaa00'),
    'vah': QColor('#ff88ff'),
    'val': QColor('#ff88ff'),
    'text': QColor('#ddaaff'),
    'bg': QColor('#0c080f'),
    'header': QColor('#ff88ff'),
    'ref_line': QColor('#cc44ff40'),
}

MARGIN_L = 6
MARGIN_R = 60   # space for price labels
MARGIN_T = 30
MARGIN_B = 10


class ProfilePainter:
    """Stateless painter: draws volume + delta profile for a SessionProfile."""

    def __init__(self, profile: SessionProfile, palette: Dict):
        self.profile = profile
        self.pal = palette

    def draw(self, painter: QPainter, rect: QRectF, mode: str = 'side_by_side',
             prior: Optional[SessionProfile] = None):
        """mode: 'side_by_side' | 'overlay'"""
        p = self.profile
        if not p.levels:
            return

        prices = sorted(p.levels.keys())
        n = len(prices)
        if n == 0:
            return

        h = rect.height() - MARGIN_T - MARGIN_B
        w = rect.width() - MARGIN_L - MARGIN_R
        row_h = max(2.0, h / n)

        price_min = prices[0]
        price_max = prices[-1]
        price_range = price_max - price_min or 1.0

        max_vol = max((lv.volume for lv in p.levels.values()), default=1)
        max_delta = max((abs(lv.delta) for lv in p.levels.values()), default=1)

        # Draw prior session reference lines
        if prior and prior.levels:
            for ref_price, label in [(prior.poc, 'pPOC'), (prior.vah, 'pVAH'), (prior.val, 'pVAL')]:
                if price_min <= ref_price <= price_max:
                    y = self._price_y(ref_price, price_min, price_range, rect, row_h)
                    pen = QPen(self.pal['ref_line'], 1, Qt.DashLine)
                    painter.setPen(pen)
                    painter.drawLine(
                        int(rect.x() + MARGIN_L), int(y),
                        int(rect.x() + rect.width() - MARGIN_R), int(y)
                    )
                    painter.setFont(QFont('Courier New', 7))
                    painter.setPen(self.pal['ref_line'])
                    painter.drawText(int(rect.x() + rect.width() - MARGIN_R + 2), int(y) + 4, label)

        if mode == 'side_by_side':
            vol_w = w * 0.5
            delta_x_start = rect.x() + MARGIN_L + vol_w + 4
            delta_w = w * 0.5 - 4
        else:
            vol_w = w
            delta_x_start = rect.x() + MARGIN_L
            delta_w = w

        # Draw bars
        for price in prices:
            lv = p.levels[price]
            y = self._price_y(price, price_min, price_range, rect, row_h)
            bar_h = max(1.0, row_h - 1)

            # Volume bar
            vol_bar_w = (lv.volume / max_vol) * vol_w
            x0 = rect.x() + MARGIN_L

            if mode == 'overlay':
                vol_color = QColor(self.pal['vol_fill'])
                vol_color.setAlpha(80)
            else:
                vol_color = self.pal['vol_fill']

            painter.fillRect(int(x0), int(y), int(vol_bar_w), int(bar_h), vol_color)

            # Delta bar
            d = lv.delta
            delta_bar_w = abs(d) / max_delta * delta_w
            d_color = self.pal['delta_pos'] if d >= 0 else self.pal['delta_neg']
            if mode == 'side_by_side':
                painter.fillRect(int(delta_x_start), int(y), int(delta_bar_w), int(bar_h), d_color)
            else:
                # Center-out overlay
                mid_x = delta_x_start + delta_w / 2
                if d >= 0:
                    painter.fillRect(int(mid_x), int(y), int(delta_bar_w / 2), int(bar_h), d_color)
                else:
                    painter.fillRect(int(mid_x - delta_bar_w / 2), int(y), int(delta_bar_w / 2), int(bar_h), d_color)

        # POC line
        if p.poc > 0:
            y = self._price_y(p.poc, price_min, price_range, rect, row_h)
            painter.setPen(QPen(self.pal['poc'], 2))
            painter.drawLine(
                int(rect.x() + MARGIN_L), int(y),
                int(rect.x() + rect.width() - MARGIN_R), int(y)
            )
            painter.setFont(QFont('Courier New', 7, QFont.Bold))
            painter.setPen(self.pal['poc'])
            painter.drawText(int(rect.x() + rect.width() - MARGIN_R + 2), int(y) + 4, f'POC {p.poc:.2f}')

        # VAH/VAL lines
        for ref_p, label in [(p.vah, 'VAH'), (p.val, 'VAL')]:
            if ref_p > 0 and price_min <= ref_p <= price_max:
                y = self._price_y(ref_p, price_min, price_range, rect, row_h)
                painter.setPen(QPen(self.pal['vah'], 1, Qt.DashLine))
                painter.drawLine(
                    int(rect.x() + MARGIN_L), int(y),
                    int(rect.x() + rect.width() - MARGIN_R), int(y)
                )
                painter.setFont(QFont('Courier New', 7))
                painter.setPen(self.pal['vah'])
                painter.drawText(int(rect.x() + rect.width() - MARGIN_R + 2), int(y) + 4, f'{label} {ref_p:.2f}')

        # Price axis
        painter.setFont(QFont('Courier New', 7))
        painter.setPen(self.pal['text'])
        step = max(1, n // 8)
        for i in range(0, n, step):
            price = prices[i]
            y = self._price_y(price, price_min, price_range, rect, row_h)
            painter.drawText(
                int(rect.x() + rect.width() - MARGIN_R + 2), int(y) + 4,
                f'{price:.2f}'
            )

    def _price_y(self, price: float, price_min: float, price_range: float,
                 rect: QRectF, row_h: float) -> float:
        ratio = (price - price_min) / price_range
        return rect.y() + MARGIN_T + (1 - ratio) * (rect.height() - MARGIN_T - MARGIN_B)


# ─────────────────────────────────────────────
#  Session Profiles Widget
# ─────────────────────────────────────────────

class SessionProfilesWidget(QWidget):
    """Dual-panel widget: RTH and Overnight session profiles."""

    def __init__(self, session_mgr: SessionManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._mgr = session_mgr
        self._mode = 'side_by_side'

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        title = QLabel('SESSION PROFILES')
        title.setStyleSheet('font-family: Courier New; font-size: 10px; color: #00ffff; padding: 2px 8px;')
        toolbar.addWidget(title)
        toolbar.addStretch()

        self._active_lbl = QLabel('─')
        self._active_lbl.setStyleSheet('font-family: Courier New; font-size: 9px; color: #aaffcc; padding: 0 8px;')
        toolbar.addWidget(self._active_lbl)

        btn_group = QButtonGroup(self)
        for label, mode in [('SIDE', 'side_by_side'), ('OVERLAY', 'overlay')]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedSize(60, 20)
            btn.setStyleSheet(
                'QPushButton { font-family: Courier New; font-size: 8px; '
                'background: #0d1117; color: #44ffaa; border: 1px solid #00ff8840; } '
                'QPushButton:checked { background: #00ff88; color: #000; }'
            )
            btn.clicked.connect(lambda checked, m=mode: self._set_mode(m))
            btn_group.addButton(btn)
            toolbar.addWidget(btn)
            if mode == 'side_by_side':
                btn.setChecked(True)

        layout.addLayout(toolbar)

        # Canvas
        self._canvas = ProfileCanvas(self._mgr)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._canvas)

        # Stats row
        self._stats_rth = SessionStatsPanel('RTH', RTH_COLORS)
        self._stats_night = SessionStatsPanel('OVERNIGHT', NIGHT_COLORS)
        stats_row = QHBoxLayout()
        stats_row.addWidget(self._stats_rth)
        stats_row.addWidget(self._stats_night)
        layout.addLayout(stats_row)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(1000)

    def _set_mode(self, mode: str):
        self._mode = mode
        self._canvas.set_mode(mode)

    def _refresh(self):
        sess = current_session()
        elapsed_str = ''
        if self._mgr.current:
            mins = self._mgr.current.elapsed_minutes()
            elapsed_str = f'{int(mins)}m elapsed'
        self._active_lbl.setText(f'{sess}  {elapsed_str}')

        # Update stats panels
        rth = self._find_last(self._mgr.history(), 'RTH')
        night = self._find_last(self._mgr.history(), 'overnight')
        if rth:
            self._stats_rth.update(rth)
        if night:
            self._stats_night.update(night)

        self._canvas.update()

    def _find_last(self, sessions: List[SessionProfile], stype: str) -> Optional[SessionProfile]:
        for s in reversed(sessions):
            if s.session_type == stype:
                return s
        return None

    def add_trade(self, timestamp: float, price: float, vol: int, side: str):
        self._mgr.process_trade(timestamp, price, vol, side)

    def inject_demo(self):
        import random
        base = 5250.0
        now = time.time()
        # Simulate RTH session (current)
        for i in range(500):
            ts = now - (500 - i) * 30
            price = base + random.gauss(0, 8)
            vol = random.randint(1, 50)
            side = random.choice(['buy', 'sell'])
            self._mgr.process_trade(ts, price, vol, side)


# ─────────────────────────────────────────────
#  Profile Canvas (QPainter-based)
# ─────────────────────────────────────────────

class ProfileCanvas(QWidget):
    """Renders up to 2 session profiles side by side."""

    def __init__(self, mgr: SessionManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._mgr = mgr
        self._mode = 'side_by_side'
        self.setMinimumSize(300, 200)

    def set_mode(self, mode: str):
        self._mode = mode
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor('#080810'))

        sessions = self._mgr.history(6)
        if not sessions:
            p.setPen(QColor('#333'))
            p.setFont(QFont('Courier New', 10))
            p.drawText(self.rect(), Qt.AlignCenter, 'No session data')
            p.end()
            return

        # Split canvas: left = RTH, right = overnight
        rth_list = [s for s in sessions if s.session_type == 'RTH']
        night_list = [s for s in sessions if s.session_type == 'overnight']

        rth_rect = QRectF(0, 0, w / 2 - 1, h)
        night_rect = QRectF(w / 2 + 1, 0, w / 2 - 1, h)

        # Section headers
        p.setFont(QFont('Courier New', 8, QFont.Bold))
        if rth_list:
            p.setPen(RTH_COLORS['header'])
            p.drawText(int(rth_rect.x()), 12, 'RTH')
            painter = ProfilePainter(rth_list[-1], RTH_COLORS)
            prior = rth_list[-2] if len(rth_list) >= 2 else None
            painter.draw(p, rth_rect, self._mode, prior=prior)

        # Divider
        p.setPen(QPen(QColor('#1a1a3a'), 1))
        p.drawLine(int(w / 2), 0, int(w / 2), h)

        if night_list:
            p.setPen(NIGHT_COLORS['header'])
            p.drawText(int(night_rect.x() + 4), 12, 'OVERNIGHT')
            painter2 = ProfilePainter(night_list[-1], NIGHT_COLORS)
            prior2 = night_list[-2] if len(night_list) >= 2 else None
            painter2.draw(p, night_rect, self._mode, prior=prior2)

        p.end()


# ─────────────────────────────────────────────
#  Session Stats Panel
# ─────────────────────────────────────────────

class SessionStatsPanel(QFrame):
    """Compact stats: volume, delta, range, time in session."""

    def __init__(self, label: str, palette: Dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._palette = palette
        color = palette['header'].name()
        self.setStyleSheet(f'QFrame {{ background: #0a0a12; border: 1px solid {color}40; }}')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(1)

        title = QLabel(label)
        title.setStyleSheet(f'font-family: Courier New; font-size: 9px; font-weight: bold; color: {color};')
        layout.addWidget(title)

        self._vol_lbl = self._lbl('VOL: —')
        self._delta_lbl = self._lbl('DELTA: —')
        self._range_lbl = self._lbl('RANGE: —')
        self._time_lbl = self._lbl('TIME: —')
        self._poc_lbl = self._lbl('POC: —')
        for lbl in [self._vol_lbl, self._delta_lbl, self._range_lbl, self._time_lbl, self._poc_lbl]:
            layout.addWidget(lbl)

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet('font-family: Courier New; font-size: 8px; color: #aaccaa;')
        return lbl

    def update(self, s: SessionProfile):
        self._vol_lbl.setText(f'VOL: {s.total_volume:,}')
        sign = '+' if s.total_delta >= 0 else ''
        delta_color = '#00ff88' if s.total_delta >= 0 else '#ff4466'
        self._delta_lbl.setText(f'DELTA: {sign}{s.total_delta:,}')
        self._delta_lbl.setStyleSheet(f'font-family: Courier New; font-size: 8px; color: {delta_color};')
        self._range_lbl.setText(f'RANGE: {s.range_size():.2f}')
        mins = s.elapsed_minutes()
        self._time_lbl.setText(f'TIME: {int(mins)}m')
        self._poc_lbl.setText(f'POC: {s.poc:.2f}' if s.poc > 0 else 'POC: —')
