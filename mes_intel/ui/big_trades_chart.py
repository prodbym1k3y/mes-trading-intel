"""Big Trades Indicator — detection, dot chart, heatmap, stats panel.

Priority 1 feature: detect trades > 3x rolling average, visualize as colored
dots on a price chart, show time×price heatmap, absorption/breakout alerts.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QPushButton, QButtonGroup, QAbstractButton,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal as QtSignal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QLinearGradient,
    QRadialGradient, QPainterPath, QFontMetrics,
)

from .theme import COLORS


# ── helpers ───────────────────────────────────────────────────────────────────

def _mono(sz: int = 9, bold: bool = False) -> QFont:
    f = QFont("JetBrains Mono", sz)
    f.setStyleHint(QFont.StyleHint.Monospace)
    if bold:
        f.setBold(True)
    return f


BIG_TRADE_THRESHOLD = 3.0   # multiplier over rolling avg
ROLLING_WINDOW      = 100   # ticks for rolling average
HEATMAP_TIME_BINS   = 60    # columns in heatmap (each = ~1 min)
HEATMAP_PRICE_BINS  = 40    # rows in heatmap


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class BigTrade:
    timestamp: float
    price: float
    size: int
    is_buy: bool
    multiplier: float       # how many times larger than rolling avg
    signal: str = ""        # "ABSORPTION", "BREAKOUT", or ""

    @property
    def color(self) -> str:
        return COLORS["cyan"] if self.is_buy else COLORS["magenta"]

    @property
    def dot_radius(self) -> int:
        """Dot radius scales with multiplier, capped at 24px."""
        return max(6, min(24, int(4 + self.multiplier * 2)))


class BigTradeDetector:
    """Detects trades that are significantly larger than the rolling average.

    Holds a rolling window of trade sizes and emits BigTrade objects when
    a new trade exceeds threshold × rolling_avg.
    """

    def __init__(self,
                 threshold: float = BIG_TRADE_THRESHOLD,
                 window: int = ROLLING_WINDOW):
        self._threshold  = threshold
        self._window     = deque(maxlen=window)
        self._recent_big: deque[BigTrade] = deque(maxlen=500)

        # For absorption/breakout detection
        self._price_history: deque[float] = deque(maxlen=30)   # last 30 big-trade prices
        self._cluster_counter: dict[float, list[BigTrade]] = {}

    def add_trade(self, price: float, size: int, is_buy: bool,
                  timestamp: float | None = None) -> Optional[BigTrade]:
        """Process a trade tick; return BigTrade if it qualifies, else None."""
        ts = timestamp or time.time()
        self._window.append(size)

        if len(self._window) < 20:
            return None

        avg = sum(self._window) / len(self._window)
        if avg == 0:
            return None

        multiplier = size / avg
        if multiplier < self._threshold:
            return None

        signal = self._classify(price, size, is_buy, ts)
        bt = BigTrade(ts, price, size, is_buy, multiplier, signal)
        self._recent_big.append(bt)
        self._price_history.append(price)

        # Update cluster map
        rounded = round(price * 4) / 4  # 0.25 tick
        if rounded not in self._cluster_counter:
            self._cluster_counter[rounded] = []
        self._cluster_counter[rounded].append(bt)

        return bt

    def _classify(self, price: float, size: int,
                  is_buy: bool, ts: float) -> str:
        """Absorption: big sell hits but price hasn't moved down.
        Breakout: burst of same-side big trades sweeping through."""
        if len(self._price_history) < 5:
            return ""

        recent_prices = list(self._price_history)[-5:]
        recent_buys = [bt for bt in list(self._recent_big)[-10:]
                       if time.time() - bt.timestamp < 30]

        if not is_buy:
            # Big sell at/near price: if price hasn't dropped, it's absorption
            price_drop = recent_prices[0] - price if recent_prices else 0
            if price_drop < 0.5:  # price held within 2 ticks
                return "ABSORPTION"
        else:
            # Big buy: if 3+ big buys in last 30s, it's a breakout
            buy_burst = [bt for bt in recent_buys if bt.is_buy]
            if len(buy_burst) >= 3:
                return "BREAKOUT"
        return ""

    @property
    def recent_trades(self) -> list[BigTrade]:
        return list(self._recent_big)

    def cluster_at(self, price: float) -> list[BigTrade]:
        rounded = round(price * 4) / 4
        return self._cluster_counter.get(rounded, [])

    def session_stats(self) -> dict:
        trades = list(self._recent_big)
        if not trades:
            return {"big_buys": 0, "big_sells": 0, "delta": 0,
                    "participation_rate": 0.0, "absorptions": 0, "breakouts": 0}
        big_buys  = sum(bt.size for bt in trades if bt.is_buy)
        big_sells = sum(bt.size for bt in trades if not bt.is_buy)
        total_vol = sum(self._window)
        total_big = sum(bt.size for bt in trades)
        return {
            "big_buys":  big_buys,
            "big_sells": big_sells,
            "delta":     big_buys - big_sells,
            "participation_rate": (total_big / total_vol) if total_vol > 0 else 0.0,
            "absorptions": sum(1 for bt in trades if bt.signal == "ABSORPTION"),
            "breakouts":   sum(1 for bt in trades if bt.signal == "BREAKOUT"),
        }


# ── dot chart ─────────────────────────────────────────────────────────────────

class BigTradeDotChart(QWidget):
    """Price ladder with colored dots showing big trade activity.

    Green/cyan dots = big buys, Red/magenta dots = big sells.
    Dot size ∝ multiplier. Cluster = brighter/larger marker.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trades: list[BigTrade] = []
        self._current_price: float = 0.0
        self._zoom: float = 1.0
        self._pan_offset: float = 0.0          # in price units
        self._drag_start: Optional[QPointF] = None
        self._drag_price_start: float = 0.0
        self._hover_trade: Optional[BigTrade] = None
        self._hover_pos: Optional[QPointF] = None

        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_trades(self, trades: list[BigTrade]):
        self._trades = trades[-200:]  # keep last 200
        self.update()

    def set_price(self, price: float):
        self._current_price = price
        self.update()

    # ── mouse ──────────────────────────────────────────────────────────────────

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self._zoom = max(0.5, min(8.0, self._zoom * (1.1 if delta > 0 else 0.9)))
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position()
            self._drag_price_start = self._pan_offset

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            dy = event.position().y() - self._drag_start.y()
            _, _, _, chart_h = self._chart_rect()
            if chart_h > 0:
                price_range = self._price_range()
                self._pan_offset = self._drag_price_start + (dy / chart_h) * price_range
        # hover detection
        self._hover_pos = event.position()
        self._hover_trade = self._find_hover(event.position())
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_start = None

    # ── helpers ────────────────────────────────────────────────────────────────

    def _chart_rect(self):
        ML, MR, MT, MB = 65, 8, 8, 30
        return ML, MT, self.width() - ML - MR, self.height() - MT - MB

    def _price_range(self) -> float:
        base = 20.0  # default ±10 pts visible
        return base / self._zoom

    def _price_to_y(self, price: float, mid: float,
                    span: float, chart_y: int, chart_h: int) -> float:
        return chart_y + chart_h * (1.0 - (price - (mid - span / 2)) / span)

    def _find_hover(self, pos: QPointF) -> Optional[BigTrade]:
        ML, MT, cw, ch = self._chart_rect()
        if not self._trades:
            return None
        mid = (self._current_price or 0) + self._pan_offset
        span = self._price_range()
        for bt in reversed(self._trades):
            y = self._price_to_y(bt.price, mid, span, MT, ch)
            x = ML + cw * ((bt.timestamp - (self._trades[0].timestamp or bt.timestamp)) /
                           max((self._trades[-1].timestamp - self._trades[0].timestamp), 1))
            r = bt.dot_radius + 4
            if abs(pos.x() - x) < r and abs(pos.y() - y) < r:
                return bt
        return None

    # ── paint ──────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ML, MT, cw, ch = self._chart_rect()

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLORS["bg_panel"]))

        if not self._trades and not self._current_price:
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.setFont(_mono(10))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                             "Waiting for big trades...")
            painter.end()
            return

        mid   = (self._current_price or 0) + self._pan_offset
        span  = self._price_range()
        p_min = mid - span / 2
        p_max = mid + span / 2

        # Grid + price axis
        painter.setFont(_mono(7))
        tick_spacing = 1.0  # every 1 pt
        p = math.floor(p_min)
        while p <= p_max:
            y = self._price_to_y(p, mid, span, MT, ch)
            if MT <= y <= MT + ch:
                is_round = (p % 5 == 0)
                pen_col = COLORS["border"] if is_round else COLORS["grid"]
                painter.setPen(QPen(QColor(pen_col), 1))
                painter.drawLine(ML, int(y), ML + cw, int(y))
                painter.setPen(QColor(COLORS["text_muted"]))
                painter.drawText(QRectF(2, y - 6, ML - 4, 12),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 f"{p:.2f}")
            p += tick_spacing

        # Time range for x-axis
        if self._trades:
            t_min = self._trades[0].timestamp
            t_max = self._trades[-1].timestamp
            t_span = max(t_max - t_min, 1.0)
        else:
            t_min = t_max = t_span = 0.0

        # Draw dots for each big trade
        for bt in self._trades:
            if not (p_min <= bt.price <= p_max):
                continue

            x = ML + (((bt.timestamp - t_min) / t_span) * cw if t_span else cw / 2)
            y = self._price_to_y(bt.price, mid, span, MT, ch)
            r = bt.dot_radius

            # Cluster boost: more trades at this level → brighter
            cluster = len([b for b in self._trades
                           if abs(b.price - bt.price) < 0.5 and b.is_buy == bt.is_buy])
            alpha = min(0.95, 0.5 + cluster * 0.07)

            base_col = QColor(COLORS["cyan"] if bt.is_buy else COLORS["magenta"])

            # Glow (larger, semi-transparent)
            glow_r = r + 6
            grad = QRadialGradient(x, y, glow_r)
            glow_c = QColor(base_col)
            glow_c.setAlphaF(alpha * 0.35)
            grad.setColorAt(0, glow_c)
            grad.setColorAt(1, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawEllipse(QPointF(x, y), glow_r, glow_r)

            # Main dot
            dot_c = QColor(base_col)
            dot_c.setAlphaF(alpha)
            painter.setBrush(QBrush(dot_c))
            painter.setPen(QPen(QColor(COLORS["text_white"]), 1))
            painter.drawEllipse(QPointF(x, y), r, r)

            # Contract count label (if large enough)
            if r >= 10:
                painter.setFont(_mono(7, bold=True))
                painter.setPen(QColor(COLORS["text_white"]))
                painter.drawText(QRectF(x - r, y - r, r * 2, r * 2),
                                 Qt.AlignmentFlag.AlignCenter, str(bt.size))

            # Signal badge
            if bt.signal:
                badge_col = COLORS["amber"] if bt.signal == "ABSORPTION" else COLORS["green_bright"]
                painter.setPen(QColor(badge_col))
                painter.setFont(_mono(6))
                painter.drawText(int(x + r + 2), int(y - 4), bt.signal[:3])

        # Current price line
        if self._current_price and p_min <= self._current_price <= p_max:
            cy = self._price_to_y(self._current_price, mid, span, MT, ch)
            painter.setPen(QPen(QColor(COLORS["amber"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(ML, int(cy), ML + cw, int(cy))
            painter.setFont(_mono(8, bold=True))
            painter.setPen(QColor(COLORS["amber"]))
            painter.drawText(QRectF(ML + cw - 60, cy - 8, 58, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{self._current_price:.2f}")

        # Hover tooltip
        if self._hover_trade and self._hover_pos:
            bt = self._hover_trade
            tx, ty = self._hover_pos.x(), self._hover_pos.y()
            tip = (f"{bt.size} lots @ {bt.price:.2f}\n"
                   f"{'BUY' if bt.is_buy else 'SELL'}  {bt.multiplier:.1f}x avg\n"
                   + (bt.signal if bt.signal else ""))
            lines = tip.split("\n")
            fm = QFontMetrics(_mono(8))
            tip_w = max(fm.horizontalAdvance(l) for l in lines) + 12
            tip_h = fm.height() * len(lines) + 8
            tx2 = min(tx + 12, w - tip_w - 4)
            ty2 = max(ty - tip_h - 4, 4)
            painter.fillRect(int(tx2), int(ty2), tip_w, tip_h, QColor(COLORS["bg_dark"] + "ee"))
            painter.setPen(QPen(QColor(COLORS["border"]), 1))
            painter.drawRect(int(tx2), int(ty2), tip_w, tip_h)
            painter.setFont(_mono(8))
            for i, line in enumerate(lines):
                col = COLORS["cyan"] if bt.is_buy else COLORS["magenta"]
                painter.setPen(QColor(col))
                painter.drawText(int(tx2 + 6), int(ty2 + fm.height() * (i + 1)), line)

        # Clip indicator
        painter.setPen(QColor(COLORS["border"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(ML, MT, cw, ch)

        painter.end()


# ── heatmap ───────────────────────────────────────────────────────────────────

class BigTradeHeatmap(QWidget):
    """Time × Price heatmap showing where big order activity concentrates.

    X axis = time (HEATMAP_TIME_BINS buckets), Y axis = price, color = volume.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trades: list[BigTrade] = []
        self._session_start: float = time.time()
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._hover: Optional[QPointF] = None

    def set_trades(self, trades: list[BigTrade], session_start: float | None = None):
        self._trades = trades
        if session_start:
            self._session_start = session_start
        self.update()

    def mouseMoveEvent(self, event):
        self._hover = event.position()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ML, MT, MB = 55, 5, 20

        painter.fillRect(0, 0, w, h, QColor(COLORS["bg_panel"]))

        if not self._trades:
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.setFont(_mono(9))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                             "No big trade data")
            painter.end()
            return

        cw = w - ML - 8
        ch = h - MT - MB

        T_BINS = HEATMAP_TIME_BINS
        P_BINS = HEATMAP_PRICE_BINS

        prices = [bt.price for bt in self._trades]
        p_min, p_max = min(prices) - 0.5, max(prices) + 0.5
        p_span = max(p_max - p_min, 1.0)

        now = time.time()
        session_start = min(bt.timestamp for bt in self._trades)
        t_span = max(now - session_start, 60.0)

        # Build grid
        grid_buy  = [[0] * T_BINS for _ in range(P_BINS)]
        grid_sell = [[0] * T_BINS for _ in range(P_BINS)]

        for bt in self._trades:
            ti = min(int((bt.timestamp - session_start) / t_span * T_BINS), T_BINS - 1)
            pi = min(int((bt.price - p_min) / p_span * P_BINS), P_BINS - 1)
            pi = P_BINS - 1 - pi   # flip so high price = top
            if bt.is_buy:
                grid_buy[pi][ti] += bt.size
            else:
                grid_sell[pi][ti] += bt.size

        max_val = max(
            max(max(row) for row in grid_buy),
            max(max(row) for row in grid_sell),
            1,
        )

        cell_w = cw / T_BINS
        cell_h = ch / P_BINS

        painter.setPen(Qt.PenStyle.NoPen)
        for pi in range(P_BINS):
            for ti in range(T_BINS):
                bv = grid_buy[pi][ti]
                sv = grid_sell[pi][ti]
                total = bv + sv
                if total == 0:
                    continue
                intensity = total / max_val
                cx = ML + ti * cell_w
                cy = MT + pi * cell_h
                if bv >= sv:
                    col = QColor(0, int(180 * intensity + 40), int(220 * intensity + 20),
                                 int(200 * intensity + 30))
                else:
                    col = QColor(int(220 * intensity + 20), 0, int(160 * intensity + 30),
                                 int(200 * intensity + 30))
                painter.fillRect(int(cx), int(cy), max(int(cell_w) - 1, 1),
                                 max(int(cell_h) - 1, 1), col)

        # Price axis
        painter.setFont(_mono(7))
        for pi in range(0, P_BINS, P_BINS // 8):
            py = MT + pi * cell_h
            price = p_max - (pi / P_BINS) * p_span
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(QRectF(2, py - 5, ML - 4, 10),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{price:.1f}")
            painter.setPen(QPen(QColor(COLORS["grid"]), 1))
            painter.drawLine(ML, int(py), ML + cw, int(py))

        # Time axis
        for ti in range(0, T_BINS, max(T_BINS // 8, 1)):
            tx = ML + ti * cell_w
            t_val = session_start + (ti / T_BINS) * t_span
            import datetime
            dt = datetime.datetime.fromtimestamp(t_val)
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(int(tx - 10), h - 4, dt.strftime("%H:%M"))
            painter.setPen(QPen(QColor(COLORS["grid"]), 1))
            painter.drawLine(int(tx), MT, int(tx), MT + ch)

        # Border
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(ML, MT, cw, ch)

        painter.end()


# ── stats panel ───────────────────────────────────────────────────────────────

class BigTradeStatsWidget(QWidget):
    """Compact stats bar: big buys, big sells, delta, participation %, alerts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self._stats: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        title = QLabel("BIG TRADE STATS")
        title.setStyleSheet(f"color: {COLORS['cyan']}; font-size: 10px; font-weight: bold;")
        layout.addWidget(title)

        row1 = QHBoxLayout()
        row2 = QHBoxLayout()

        self._buy_lbl  = self._make_lbl("BIG BUYS: --", COLORS["cyan"])
        self._sell_lbl = self._make_lbl("BIG SELLS: --", COLORS["magenta"])
        self._delta_lbl = self._make_lbl("BIG Δ: --", COLORS["text_white"])
        self._part_lbl  = self._make_lbl("PART: --%", COLORS["amber"])
        self._abs_lbl   = self._make_lbl("ABS: --", COLORS["green_bright"])
        self._brk_lbl   = self._make_lbl("BRKT: --", COLORS["red"])

        for w in [self._buy_lbl, self._sell_lbl, self._delta_lbl]:
            row1.addWidget(w)
        for w in [self._delta_lbl, self._part_lbl, self._abs_lbl, self._brk_lbl]:
            row2.addWidget(w)

        layout.addLayout(row1)
        layout.addLayout(row2)

    @staticmethod
    def _make_lbl(text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-family: 'JetBrains Mono';")
        return lbl

    def update_stats(self, stats: dict):
        self._stats = stats
        bb = stats.get("big_buys",  0)
        bs = stats.get("big_sells", 0)
        bd = stats.get("delta",     0)
        pr = stats.get("participation_rate", 0.0)
        ab = stats.get("absorptions", 0)
        bk = stats.get("breakouts",   0)

        sign = "+" if bd >= 0 else ""
        col  = COLORS["cyan"] if bd >= 0 else COLORS["magenta"]

        self._buy_lbl.setText(f"BIG BUYS: {bb:,}")
        self._sell_lbl.setText(f"BIG SELLS: {bs:,}")
        self._delta_lbl.setText(f"BIG Δ: {sign}{bd:,}")
        self._delta_lbl.setStyleSheet(f"color: {col}; font-size: 10px; font-family: 'JetBrains Mono';")
        self._part_lbl.setText(f"INST: {pr:.1%}")
        self._abs_lbl.setText(f"ABS: {ab}")
        self._brk_lbl.setText(f"BRKT: {bk}")


# ── combined panel ────────────────────────────────────────────────────────────

class BigTradePanel(QWidget):
    """Full big-trade analysis panel: stats + dot chart + heatmap.

    Use set_trades() to push new data.  Use set_price() to update current price.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.detector = BigTradeDetector()
        self._session_start = time.time()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # Stats
        self.stats_widget = BigTradeStatsWidget()
        root.addWidget(self.stats_widget)

        # View toggle
        btn_row = QHBoxLayout()
        self._dot_btn  = QPushButton("DOT CHART")
        self._heat_btn = QPushButton("HEATMAP")
        for btn in [self._dot_btn, self._heat_btn]:
            btn.setCheckable(True)
            btn.setFixedHeight(20)
            btn.setStyleSheet(
                f"QPushButton{{color:{COLORS['text_muted']};background:{COLORS['bg_panel']};"
                f"border:1px solid {COLORS['border']};font-size:9px;padding:0 6px;}}"
                f"QPushButton:checked{{color:{COLORS['cyan']};border-color:{COLORS['cyan']};}}"
            )
            btn_row.addWidget(btn)
        self._dot_btn.setChecked(True)
        self._dot_btn.clicked.connect(lambda: self._switch_view("dot"))
        self._heat_btn.clicked.connect(lambda: self._switch_view("heat"))
        root.addLayout(btn_row)

        # Charts (stacked — only one visible at a time)
        self.dot_chart  = BigTradeDotChart()
        self.heatmap    = BigTradeHeatmap()
        root.addWidget(self.dot_chart,  1)
        root.addWidget(self.heatmap,    1)
        self.heatmap.setVisible(False)

    def _switch_view(self, view: str):
        self._dot_btn.setChecked(view == "dot")
        self._heat_btn.setChecked(view == "heat")
        self.dot_chart.setVisible(view == "dot")
        self.heatmap.setVisible(view == "heat")

    def add_trade(self, price: float, size: int, is_buy: bool,
                  timestamp: float | None = None) -> Optional[BigTrade]:
        """Process one tick; return BigTrade if it was big enough."""
        bt = self.detector.add_trade(price, size, is_buy, timestamp)
        if bt is not None:
            self._refresh()
        return bt

    def set_trades(self, trades: list[BigTrade]):
        """Bulk-load pre-detected BigTrade objects."""
        self.dot_chart.set_trades(trades)
        self.heatmap.set_trades(trades, self._session_start)
        self.stats_widget.update_stats(self.detector.session_stats())

    def set_price(self, price: float):
        self.dot_chart.set_price(price)

    def _refresh(self):
        trades = self.detector.recent_trades
        self.dot_chart.set_trades(trades)
        self.heatmap.set_trades(trades, self._session_start)
        self.stats_widget.update_stats(self.detector.session_stats())

    def reset_session(self):
        """Call at session start to reset counters."""
        self.detector = BigTradeDetector()
        self._session_start = time.time()
        self._refresh()
