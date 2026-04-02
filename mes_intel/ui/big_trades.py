"""Big Trades Indicator — ATAS-style institutional order flow detection.

Detects and visualizes trades significantly larger than rolling average,
classifies absorption/breakout patterns, renders heatmap and stats panel.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QRadialGradient,
    QLinearGradient, QPainterPath,
)


# ─────────────────────────────────────────────
#  Data structures
# ─────────────────────────────────────────────

@dataclass
class Trade:
    timestamp: float
    price: float
    size: int
    side: str  # 'buy' | 'sell'
    is_big: bool = False
    absorption: Optional[str] = None   # 'bullish' | 'bearish' | None
    breakout: bool = False


@dataclass
class PriceLevel:
    price: float
    big_buy_vol: int = 0
    big_sell_vol: int = 0
    trades: List[Trade] = field(default_factory=list)


# ─────────────────────────────────────────────
#  Big Trades Engine
# ─────────────────────────────────────────────

class BigTradesEngine:
    """Detects trades > 3x rolling average size, classifies buy/sell."""

    MULTIPLIER = 3.0
    WINDOW = 200          # rolling window for average
    ABSORPTION_WINDOW = 5.0   # seconds to detect absorption

    def __init__(self):
        self._recent_sizes: deque = deque(maxlen=self.WINDOW)
        self._trades: deque = deque(maxlen=5000)
        self._price_levels: Dict[float, PriceLevel] = {}
        self._session_big_buys = 0
        self._session_big_sells = 0
        self._session_buy_vol = 0
        self._session_sell_vol = 0
        self._total_vol = 0

    def _tick_price(self, price: float) -> float:
        """Round to nearest 0.25 tick."""
        return round(price * 4) / 4

    def _rolling_avg(self) -> float:
        if not self._recent_sizes:
            return 1.0
        return sum(self._recent_sizes) / len(self._recent_sizes)

    def process_trade(self, timestamp: float, price: float, size: int, side: str) -> Trade:
        self._recent_sizes.append(size)
        avg = self._rolling_avg()
        is_big = size >= avg * self.MULTIPLIER

        trade = Trade(timestamp=timestamp, price=price, size=size, side=side, is_big=is_big)

        tick = self._tick_price(price)
        if tick not in self._price_levels:
            self._price_levels[tick] = PriceLevel(price=tick)
        lvl = self._price_levels[tick]

        if side == 'buy':
            self._session_buy_vol += size
            if is_big:
                lvl.big_buy_vol += size
                self._session_big_buys += size
        else:
            self._session_sell_vol += size
            if is_big:
                lvl.big_sell_vol += size
                self._session_big_sells += size

        self._total_vol += size

        if is_big:
            lvl.trades.append(trade)
            trade.absorption = self._check_absorption(lvl, side)
            trade.breakout = self._check_breakout(lvl, side)

        self._trades.append(trade)
        return trade

    def _check_absorption(self, lvl: PriceLevel, side: str) -> Optional[str]:
        """Big sell hits but price holds → bullish absorption. Big buy but price flat → bearish."""
        recent = [t for t in lvl.trades if time.time() - t.timestamp < self.ABSORPTION_WINDOW]
        if len(recent) < 3:
            return None
        big_sells = sum(t.size for t in recent if t.side == 'sell' and t.is_big)
        big_buys = sum(t.size for t in recent if t.side == 'buy' and t.is_big)
        if side == 'sell' and big_sells > big_buys * 2:
            return 'bullish'  # sellers hitting but can't push down
        if side == 'buy' and big_buys > big_sells * 2:
            return 'bearish'  # buyers hitting but can't push up
        return None

    def _check_breakout(self, lvl: PriceLevel, side: str) -> bool:
        """Burst of big trades sweeping through a level."""
        recent = [t for t in lvl.trades if time.time() - t.timestamp < 2.0 and t.is_big]
        return len(recent) >= 3

    def get_big_trades(self, count: int = 100) -> List[Trade]:
        return [t for t in reversed(self._trades) if t.is_big][:count]

    def institutional_participation_rate(self) -> float:
        if self._total_vol == 0:
            return 0.0
        big_total = self._session_big_buys + self._session_big_sells
        return big_total / self._total_vol * 100.0

    def big_trade_delta(self) -> int:
        return self._session_big_buys - self._session_big_sells

    @property
    def price_levels(self) -> Dict[float, PriceLevel]:
        return self._price_levels


# ─────────────────────────────────────────────
#  Absorption Detector
# ─────────────────────────────────────────────

class AbsorptionDetector:
    """Standalone detector: big sells hitting but price holds = bullish."""

    def __init__(self, price_tolerance: float = 0.5):
        self.price_tolerance = price_tolerance
        self._events: List[Dict] = []

    def analyze(self, engine: BigTradesEngine, current_price: float) -> List[Dict]:
        events = []
        for tick, lvl in engine.price_levels.items():
            if abs(tick - current_price) > self.price_tolerance * 4:
                continue
            ratio = lvl.big_sell_vol / max(lvl.big_buy_vol, 1)
            if ratio > 2.0 and lvl.big_sell_vol > 50:
                events.append({
                    'type': 'bullish_absorption',
                    'price': tick,
                    'big_sell': lvl.big_sell_vol,
                    'big_buy': lvl.big_buy_vol,
                    'strength': min(ratio / 5.0, 1.0),
                })
            ratio = lvl.big_buy_vol / max(lvl.big_sell_vol, 1)
            if ratio > 2.0 and lvl.big_buy_vol > 50:
                events.append({
                    'type': 'bearish_absorption',
                    'price': tick,
                    'big_buy': lvl.big_buy_vol,
                    'big_sell': lvl.big_sell_vol,
                    'strength': min(ratio / 5.0, 1.0),
                })
        self._events = events
        return events

    @property
    def latest_events(self) -> List[Dict]:
        return self._events


# ─────────────────────────────────────────────
#  Breakout Detector
# ─────────────────────────────────────────────

class BreakoutDetector:
    """Burst of big trades sweeping through a level."""

    BURST_WINDOW = 3.0  # seconds
    MIN_TRADES = 3

    def __init__(self):
        self._alerts: List[Dict] = []

    def analyze(self, engine: BigTradesEngine) -> List[Dict]:
        alerts = []
        now = time.time()
        for tick, lvl in engine.price_levels.items():
            recent_big = [t for t in lvl.trades if now - t.timestamp < self.BURST_WINDOW and t.is_big]
            if len(recent_big) >= self.MIN_TRADES:
                buy_vol = sum(t.size for t in recent_big if t.side == 'buy')
                sell_vol = sum(t.size for t in recent_big if t.side == 'sell')
                direction = 'up' if buy_vol > sell_vol else 'down'
                alerts.append({
                    'price': tick,
                    'direction': direction,
                    'trade_count': len(recent_big),
                    'total_vol': buy_vol + sell_vol,
                })
        self._alerts = alerts
        return alerts


# ─────────────────────────────────────────────
#  Big Trades Widget (dot chart)
# ─────────────────────────────────────────────

class BigTradesWidget(QWidget):
    """Renders colored dots at price levels. Cyan=buy, magenta=sell. Size ∝ trade size."""

    trade_selected = Signal(object)  # Trade

    DOT_MIN = 6
    DOT_MAX = 40

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine = BigTradesEngine()
        self.absorption = AbsorptionDetector()
        self.breakout = BreakoutDetector()
        self._trades: List[Trade] = []
        self._price_range: Tuple[float, float] = (0.0, 0.0)
        self._time_range: Tuple[float, float] = (0.0, 0.0)
        self._max_size = 1
        self._hovered: Optional[Trade] = None
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.update)
        self._refresh_timer.start(500)

    def add_trade(self, timestamp: float, price: float, size: int, side: str):
        trade = self.engine.process_trade(timestamp, price, size, side)
        if trade.is_big:
            self._trades.append(trade)
            if len(self._trades) > 500:
                self._trades.pop(0)
            self._max_size = max(self._max_size, size)
            self._update_ranges()

    def _update_ranges(self):
        if not self._trades:
            return
        prices = [t.price for t in self._trades]
        times = [t.timestamp for t in self._trades]
        pad = (max(prices) - min(prices)) * 0.1 or 1.0
        self._price_range = (min(prices) - pad, max(prices) + pad)
        self._time_range = (min(times), max(times))

    def _map_x(self, ts: float, w: int) -> float:
        t0, t1 = self._time_range
        if t1 == t0:
            return w / 2
        return 40 + (ts - t0) / (t1 - t0) * (w - 60)

    def _map_y(self, price: float, h: int) -> float:
        p0, p1 = self._price_range
        if p1 == p0:
            return h / 2
        return 20 + (1 - (price - p0) / (p1 - p0)) * (h - 40)

    def _dot_radius(self, size: int) -> float:
        ratio = math.sqrt(size / self._max_size)
        return self.DOT_MIN + ratio * (self.DOT_MAX - self.DOT_MIN)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(0, 0, w, h, QColor('#0a0a12'))

        if not self._trades:
            p.setPen(QColor('#333'))
            p.setFont(QFont('Courier New', 10))
            p.drawText(self.rect(), Qt.AlignCenter, 'Waiting for big trades...')
            p.end()
            return

        self._draw_grid(p, w, h)
        self._draw_price_axis(p, h)
        self._draw_absorption_zones(p, w, h)
        self._draw_dots(p, w, h)
        self._draw_hover_tooltip(p, w, h)
        p.end()

    def _draw_grid(self, p: QPainter, w: int, h: int):
        pen = QPen(QColor('#1a1a2e'), 1, Qt.DotLine)
        p.setPen(pen)
        for i in range(5):
            y = 20 + i * (h - 40) / 4
            p.drawLine(40, int(y), w - 20, int(y))
        for i in range(8):
            x = 40 + i * (w - 60) / 7
            p.drawLine(int(x), 10, int(x), h - 20)

    def _draw_price_axis(self, p: QPainter, h: int):
        p.setFont(QFont('Courier New', 7))
        p0, p1 = self._price_range
        for i in range(5):
            ratio = i / 4
            price = p1 - ratio * (p1 - p0)
            y = int(20 + ratio * (h - 40))
            p.setPen(QColor('#44ffaa'))
            p.drawText(2, y + 4, 36, 14, Qt.AlignRight, f'{price:.2f}')
            p.setPen(QPen(QColor('#1e3a2a'), 1, Qt.DotLine))
            p.drawLine(40, y, self.width() - 20, y)

    def _draw_absorption_zones(self, p: QPainter, w: int, h: int):
        events = self.absorption.analyze(self.engine, (self._price_range[0] + self._price_range[1]) / 2)
        for ev in events:
            y = int(self._map_y(ev['price'], h))
            alpha = int(ev['strength'] * 60)
            if ev['type'] == 'bullish_absorption':
                color = QColor(0, 255, 120, alpha)
            else:
                color = QColor(255, 60, 100, alpha)
            p.fillRect(40, y - 4, w - 60, 8, color)

    def _draw_dots(self, p: QPainter, w: int, h: int):
        for trade in self._trades:
            x = self._map_x(trade.timestamp, w)
            y = self._map_y(trade.price, h)
            r = self._dot_radius(trade.size)

            if trade.side == 'buy':
                core = QColor('#00ffff')
                glow_color = QColor(0, 255, 255, 80)
            else:
                core = QColor('#ff00ff')
                glow_color = QColor(255, 0, 255, 80)

            # Glow halo
            grad = QRadialGradient(x, y, r * 2)
            grad.setColorAt(0, glow_color)
            grad.setColorAt(1, Qt.transparent)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(x, y), r * 2, r * 2)

            # Absorption marker ring
            if trade.absorption == 'bullish':
                p.setPen(QPen(QColor('#00ff88'), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(x, y), r + 4, r + 4)
            elif trade.absorption == 'bearish':
                p.setPen(QPen(QColor('#ff4444'), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(x, y), r + 4, r + 4)

            # Breakout star burst
            if trade.breakout:
                p.setPen(QPen(QColor('#ffff00'), 1))
                for angle in range(0, 360, 45):
                    rad = math.radians(angle)
                    x2 = x + math.cos(rad) * (r + 8)
                    y2 = y + math.sin(rad) * (r + 8)
                    p.drawLine(QPointF(x, y), QPointF(x2, y2))

            # Core dot
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(core))
            p.drawEllipse(QPointF(x, y), r, r)

            # Contract count label
            p.setPen(QColor('#ffffff'))
            p.setFont(QFont('Courier New', 7, QFont.Bold))
            p.drawText(int(x - r), int(y - r), int(r * 2), int(r * 2),
                       Qt.AlignCenter, str(trade.size))

    def _draw_hover_tooltip(self, p: QPainter, w: int, h: int):
        if self._hovered is None:
            return
        t = self._hovered
        lines = [
            f'Price: {t.price:.2f}',
            f'Size: {t.size} contracts',
            f'Side: {t.side.upper()}',
        ]
        if t.absorption:
            lines.append(f'Absorption: {t.absorption.upper()}')
        if t.breakout:
            lines.append('BREAKOUT!')
        bx, by = 60, 30
        bw, bh = 160, len(lines) * 16 + 12
        p.fillRect(bx, by, bw, bh, QColor(10, 10, 20, 220))
        p.setPen(QColor('#00ffaa'))
        p.drawRect(bx, by, bw, bh)
        p.setFont(QFont('Courier New', 8))
        for i, line in enumerate(lines):
            p.drawText(bx + 6, by + 10 + i * 16, line)

    def mouseMoveEvent(self, event):
        w, h = self.width(), self.height()
        mx, my = event.x(), event.y()
        closest = None
        min_dist = 30.0
        for t in self._trades:
            x = self._map_x(t.timestamp, w)
            y = self._map_y(t.price, h)
            d = math.hypot(mx - x, my - y)
            if d < min_dist:
                min_dist = d
                closest = t
        self._hovered = closest
        self.update()

    def inject_demo_trades(self):
        """Inject random big trades for demo/testing."""
        import random
        now = time.time()
        base_price = 5250.0
        for i in range(80):
            ts = now - (80 - i) * 12
            price = base_price + random.uniform(-15, 15)
            size = random.randint(5, 200)
            side = random.choice(['buy', 'sell'])
            self.add_trade(ts, price, size, side)


# ─────────────────────────────────────────────
#  Big Trades Heatmap
# ─────────────────────────────────────────────

class BigTradesHeatmap(QWidget):
    """Time × price grid; color intensity = big order volume concentration."""

    PRICE_BUCKETS = 40
    TIME_BUCKETS = 60

    def __init__(self, engine: BigTradesEngine, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine = engine
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._grid_buy: List[List[int]] = []
        self._grid_sell: List[List[int]] = []
        self._price_min = 0.0
        self._price_max = 0.0
        self._time_min = 0.0
        self._time_max = 0.0

        self._refresh = QTimer(self)
        self._refresh.timeout.connect(self._rebuild_grid)
        self._refresh.start(1000)

    def _rebuild_grid(self):
        trades = self.engine.get_big_trades(2000)
        if not trades:
            return
        prices = [t.price for t in trades]
        times = [t.timestamp for t in trades]
        self._price_min = min(prices)
        self._price_max = max(prices)
        self._time_min = min(times)
        self._time_max = max(times)

        P, T = self.PRICE_BUCKETS, self.TIME_BUCKETS
        self._grid_buy = [[0] * T for _ in range(P)]
        self._grid_sell = [[0] * T for _ in range(P)]

        p_range = self._price_max - self._price_min or 1.0
        t_range = self._time_max - self._time_min or 1.0

        for t in trades:
            pi = min(int((t.price - self._price_min) / p_range * P), P - 1)
            ti = min(int((t.timestamp - self._time_min) / t_range * T), T - 1)
            if t.side == 'buy':
                self._grid_buy[pi][ti] += t.size
            else:
                self._grid_sell[pi][ti] += t.size
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor('#080810'))

        if not self._grid_buy:
            p.setPen(QColor('#333'))
            p.setFont(QFont('Courier New', 10))
            p.drawText(self.rect(), Qt.AlignCenter, 'No data')
            p.end()
            return

        P, T = self.PRICE_BUCKETS, self.TIME_BUCKETS
        margin_l, margin_b = 50, 25
        cw = (w - margin_l - 10) / T
        ch = (h - margin_b - 10) / P

        all_vals = [
            self._grid_buy[pi][ti] + self._grid_sell[pi][ti]
            for pi in range(P) for ti in range(T)
        ]
        max_val = max(all_vals) or 1

        for pi in range(P):
            for ti in range(T):
                buy_v = self._grid_buy[pi][ti]
                sell_v = self._grid_sell[pi][ti]
                total = buy_v + sell_v
                if total == 0:
                    continue
                intensity = math.sqrt(total / max_val)
                buy_ratio = buy_v / total
                r = int(255 * (1 - buy_ratio) * intensity)
                g = int(intensity * 40)
                b = int(255 * buy_ratio * intensity)
                color = QColor(r, g, b, 200)
                x = margin_l + ti * cw
                y = h - margin_b - (pi + 1) * ch
                p.fillRect(int(x), int(y), max(1, int(cw) - 1), max(1, int(ch) - 1), color)

        # Axes labels
        p.setPen(QColor('#44ffaa'))
        p.setFont(QFont('Courier New', 7))
        p_range = self._price_max - self._price_min or 1.0
        for i in range(5):
            ratio = i / 4
            price = self._price_min + ratio * p_range
            y = int(h - margin_b - ratio * (h - margin_b - 10))
            p.drawText(2, y - 6, 46, 14, Qt.AlignRight, f'{price:.1f}')

        p.drawText(margin_l, h - margin_b + 4, 'OLDER')
        p.drawText(w - 60, h - margin_b + 4, 'NOW')
        p.end()


# ─────────────────────────────────────────────
#  Stats Panel
# ─────────────────────────────────────────────

STAT_STYLE = """
QLabel {
    font-family: 'Courier New';
    font-size: 10px;
    color: #aaffcc;
    padding: 2px 6px;
}
"""

class BigTradesStatsPanel(QFrame):
    """Session big buy/sell counts, delta, institutional participation."""

    def __init__(self, engine: BigTradesEngine, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.engine = engine
        self.setStyleSheet('QFrame { background: #0d1117; border: 1px solid #00ff88; }')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        title = QLabel('BIG TRADES — SESSION STATS')
        title.setStyleSheet('font-family: Courier New; font-size: 10px; font-weight: bold; color: #00ffff; padding: 2px 6px;')
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet('color: #00ff8840;')
        layout.addWidget(sep)

        self._lbl_big_buys = self._make_label('BIG BUY VOL:  0')
        self._lbl_big_sells = self._make_label('BIG SELL VOL: 0')
        self._lbl_delta = self._make_label('BIG DELTA:    0')
        self._lbl_ipr = self._make_label('INST. PART:   0.0%')
        self._lbl_count = self._make_label('BIG TRADE #:  0')

        for lbl in [self._lbl_big_buys, self._lbl_big_sells, self._lbl_delta,
                    self._lbl_ipr, self._lbl_count]:
            layout.addWidget(lbl)

        self._delta_bar = QLabel()
        self._delta_bar.setFixedHeight(8)
        self._delta_bar.setStyleSheet('background: #1a1a2e; border: 1px solid #333;')
        layout.addWidget(self._delta_bar)

        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(500)

    def _make_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(STAT_STYLE)
        return lbl

    def _refresh(self):
        e = self.engine
        big_buy = e._session_big_buys
        big_sell = e._session_big_sells
        delta = e.big_trade_delta()
        ipr = e.institutional_participation_rate()
        count = len([t for t in e._trades if t.is_big])

        self._lbl_big_buys.setText(f'BIG BUY VOL:  {big_buy:,}')
        self._lbl_big_sells.setText(f'BIG SELL VOL: {big_sell:,}')

        delta_color = '#00ff88' if delta >= 0 else '#ff4466'
        self._lbl_delta.setText(f'BIG DELTA:    {delta:+,}')
        self._lbl_delta.setStyleSheet(f'font-family: Courier New; font-size: 10px; color: {delta_color}; padding: 2px 6px;')

        self._lbl_ipr.setText(f'INST. PART:   {ipr:.1f}%')
        self._lbl_count.setText(f'BIG TRADE #:  {count}')

        # Delta bar
        total = big_buy + big_sell or 1
        pct = (big_buy / total) * 100
        bar_w = self._delta_bar.width()
        fill = int(pct / 100 * bar_w)
        self._delta_bar.setStyleSheet(
            f'background: qlineargradient(x1:0,y1:0,x2:1,y2:0,'
            f'stop:{pct/100:.3f} #00ff88, stop:{pct/100:.3f} #ff4466);'
            f'border: 1px solid #333; border-radius: 2px;'
        )
