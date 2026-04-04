"""ATAS-Style Footprint Chart — Sierra Chart / PAVolumeTrader layout.

Features:
  - Regular candlestick chart at normal zoom (DEF_ROW_H=6)
  - Footprint mode after ~2 scroll clicks (row_h >= 10)
  - Session-based volume/delta profiles (RTH Vol, RTH Delta, ON Vol, ON Delta, All)
  - Footprint data in candle wicks (dimmed) AND body
  - POC line full chart width (dashed amber)
  - Delta divergence markers (bearish ▼ red, bullish ▲ green)
  - Stacked imbalance highlighting (3+ consecutive same-direction levels)
  - RTH session background + open/close vertical lines (Phoenix UTC-7)
  - Smooth cursor-centered zoom
  - Custom timeframe text input + days-of-data input

Controls:
  - Scroll wheel        → vertical zoom centered on cursor
  - Ctrl + scroll wheel → horizontal zoom (candle width)
  - Shift + scroll      → time pan
  - Click + drag        → pan (time + price)
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QButtonGroup, QSizePolicy, QApplication,
    QLineEdit,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QEvent
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QWheelEvent,
)

# ──────────────────────────────────────────────────────────
#  Palette
# ──────────────────────────────────────────────────────────
BG           = QColor(0x0a, 0x0a, 0x0f)   # very dark blue-black
BG_PANEL     = QColor(0x07, 0x07, 0x0d)
CYAN         = QColor(0x00, 0xff, 0xff)
GREEN        = QColor(0x00, 0xff, 0x41)
AMBER        = QColor(0xff, 0xcc, 0x00)
ORANGE       = QColor(0xff, 0x88, 0x00)
RED          = QColor(0xff, 0x22, 0x44)
PURPLE       = QColor(0xcc, 0x66, 0xff)
GRID         = QColor(0x14, 0x14, 0x20)   # subtle 5% grid
GRID_TICK    = QColor(0x0e, 0x0e, 0x18)
TEXT_DIM     = QColor(0x55, 0x55, 0x77)
WHITE        = QColor(0xcc, 0xcc, 0xdd)

# Footprint cell backgrounds — slightly more subdued
ASK_WEAK   = QColor(0x00, 0x44, 0x88, 45)
ASK_MED    = QColor(0x00, 0x88, 0xbb, 80)
ASK_STRONG = QColor(0x00, 0xcc, 0xee, 115)
BID_WEAK   = QColor(0x99, 0x00, 0x2a, 45)
BID_MED    = QColor(0xbb, 0x00, 0x44, 80)
BID_STRONG = QColor(0xee, 0x00, 0x55, 115)
NEUTRAL_A  = QColor(0x0b, 0x0b, 0x16)
NEUTRAL_B  = QColor(0x09, 0x09, 0x12)

# Wick-area variants (dimmer)
ASK_WEAK_W   = QColor(0x00, 0x33, 0x66, 40)
ASK_MED_W    = QColor(0x00, 0x66, 0x99, 70)
ASK_STRONG_W = QColor(0x00, 0x99, 0xbb, 100)
BID_WEAK_W   = QColor(0x66, 0x00, 0x22, 40)
BID_MED_W    = QColor(0x99, 0x00, 0x33, 70)
BID_STRONG_W = QColor(0xbb, 0x00, 0x44, 100)
NEUTRAL_AW   = QColor(0x0e, 0x0e, 0x1a)
NEUTRAL_BW   = QColor(0x0c, 0x0c, 0x17)

BUY_STACK_GLOW  = QColor(0x00, 0xff, 0xff, 180)
SELL_STACK_GLOW = QColor(0xff, 0x00, 0xff, 180)
BUY_STACK_BG    = QColor(0x00, 0x44, 0x66, 55)
SELL_STACK_BG   = QColor(0x66, 0x00, 0x44, 55)

# Session tints — barely-visible (5-8% opacity)
RTH_BG  = QColor(0x00, 0x20, 0x00, 15)
OFF_BG  = QColor(0x00, 0x00, 0x20, 10)

TICK = 0.25

# Phoenix/Arizona = UTC-7 (no DST)
_PHX_OFFSET = -7
RTH_OPEN_H,  RTH_OPEN_M  = 6,  30
RTH_CLOSE_H, RTH_CLOSE_M = 14, 0


# ──────────────────────────────────────────────────────────
#  Timeframe parser
# ──────────────────────────────────────────────────────────

def parse_tf(s: str) -> int:
    """Parse '1m','5m','15m','1h','4h','1d','30s' → seconds. Returns 0 on error."""
    s = s.strip().lower()
    if not s:
        return 0
    units = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    for suffix, mult in units.items():
        if s.endswith(suffix):
            try:
                return int(float(s[:-1]) * mult)
            except ValueError:
                return 0
    try:
        return int(s) * 60  # bare number = minutes
    except ValueError:
        return 0


# ──────────────────────────────────────────────────────────
#  Data structures
# ──────────────────────────────────────────────────────────

@dataclass
class LevelData:
    price: float
    bid_vol: int = 0
    ask_vol: int = 0

    @property
    def total(self) -> int:
        return self.bid_vol + self.ask_vol

    @property
    def delta(self) -> int:
        return self.ask_vol - self.bid_vol

    @property
    def ask_ratio(self) -> float:
        return self.ask_vol / max(self.bid_vol, 1)

    @property
    def bid_ratio(self) -> float:
        return self.bid_vol / max(self.ask_vol, 1)


@dataclass
class CandleData:
    timestamp: float
    open:  float = 0.0
    high:  float = -math.inf
    low:   float =  math.inf
    close: float = 0.0
    levels: Dict[float, LevelData] = field(default_factory=dict)
    closed: bool = False

    @property
    def is_bull(self) -> bool:
        return self.close >= self.open

    @property
    def total_vol(self) -> int:
        return sum(lv.total for lv in self.levels.values())

    @property
    def delta(self) -> int:
        return sum(lv.delta for lv in self.levels.values())

    @property
    def poc_price(self) -> float:
        if not self.levels:
            return self.close
        return max(self.levels.values(), key=lambda lv: lv.total).price

    def add_tick(self, price: float, bid: int, ask: int):
        p = round(price * 4) / 4
        if p not in self.levels:
            self.levels[p] = LevelData(price=p)
        lv = self.levels[p]
        lv.bid_vol += bid
        lv.ask_vol += ask
        self.close = price
        if math.isinf(self.high):
            self.high = price
            self.low  = price
            self.open = price
        else:
            self.high = max(self.high, price)
            self.low  = min(self.low,  price)


# ──────────────────────────────────────────────────────────
#  Simulated MES data
# ──────────────────────────────────────────────────────────

_RAND = random.Random(42)


def _gen_candles(n_days: int = 5, tf_secs: int = 300) -> List[CandleData]:
    """Generate n_days worth of candles including RTH + overnight sessions."""
    # Total candles needed: n_days * (24*60*60 / tf_secs)
    n = max(10, int(n_days * 86400 / tf_secs))
    candles: List[CandleData] = []
    price = 5508.75
    now   = time.time()
    # Align base_ts to a clean boundary
    base_ts = now - n * tf_secs

    for i in range(n + 1):
        ts = base_ts + i * tf_secs
        candle = CandleData(timestamp=ts)
        candle.open = price

        # Slightly different volatility for RTH vs overnight
        is_r = _is_rth(ts)
        drift     = _RAND.gauss(0, 0.3 if is_r else 0.1)
        vol_range = _RAND.uniform(2.0, 6.0) if is_r else _RAND.uniform(0.5, 2.5)

        high  = price + vol_range * _RAND.uniform(0.3, 1.0)
        low   = price - vol_range * _RAND.uniform(0.3, 1.0)
        close = price + drift + _RAND.gauss(0, 1.0 if is_r else 0.3)
        close = max(low + TICK, min(high - TICK, close))

        candle.high  = round(high  * 4) / 4
        candle.low   = round(low   * 4) / 4
        candle.close = round(close * 4) / 4
        candle.open  = round(price * 4) / 4

        price_levels = []
        p = candle.low
        while p <= candle.high + 0.001:
            price_levels.append(round(p * 4) / 4)
            p += TICK

        if not price_levels:
            price_levels = [candle.low]

        poc_idx = _RAND.randint(len(price_levels) // 4, max(len(price_levels) // 4, 3 * len(price_levels) // 4))
        poc_idx = min(poc_idx, len(price_levels) - 1)
        for j, px in enumerate(price_levels):
            dist = abs(j - poc_idx)
            base_v = max(1, int(200 * math.exp(-0.12 * dist * dist) + _RAND.gauss(0, 10)))
            base_v = max(1, base_v)

            if px < (candle.open + candle.close) / 2:
                ask_frac = 0.53 + _RAND.gauss(0, 0.07)
            else:
                ask_frac = 0.47 + _RAND.gauss(0, 0.07)
            ask_frac = max(0.05, min(0.95, ask_frac))

            ask = max(0, int(base_v * ask_frac))
            bid = max(0, base_v - ask)

            if _RAND.random() < 0.07:
                if _RAND.random() < 0.5:
                    ask = int(ask * _RAND.uniform(4, 7))
                else:
                    bid = int(bid * _RAND.uniform(4, 7))

            candle.levels[px] = LevelData(price=px, bid_vol=bid, ask_vol=ask)

        candles.append(candle)
        if i < n:
            candle.closed = True
        price = candle.close

    return candles


def _add_live_tick(candle: CandleData, price: float):
    p = round(price * 4) / 4
    bid = _RAND.randint(1, 30)
    ask = _RAND.randint(1, 30)
    if _RAND.random() < 0.06:
        if _RAND.random() < 0.5:
            ask *= _RAND.randint(3, 6)
        else:
            bid *= _RAND.randint(3, 6)
    candle.add_tick(p, bid, ask)


def _cell_bg(lv: LevelData, alt: bool, wick: bool = False) -> QColor:
    """Return background color for a footprint cell. wick=True → dimmed variant."""
    if lv.total == 0:
        return (NEUTRAL_BW if alt else NEUTRAL_AW) if wick else (NEUTRAL_B if alt else NEUTRAL_A)
    ar = lv.ask_ratio
    if wick:
        if ar >= 4.0: return ASK_STRONG_W
        if ar >= 2.0: return ASK_MED_W
        if ar >= 1.3: return ASK_WEAK_W
        br = lv.bid_ratio
        if br >= 4.0: return BID_STRONG_W
        if br >= 2.0: return BID_MED_W
        if br >= 1.3: return BID_WEAK_W
        return NEUTRAL_BW if alt else NEUTRAL_AW
    else:
        if ar >= 4.0: return ASK_STRONG
        if ar >= 2.0: return ASK_MED
        if ar >= 1.3: return ASK_WEAK
        br = lv.bid_ratio
        if br >= 4.0: return BID_STRONG
        if br >= 2.0: return BID_MED
        if br >= 1.3: return BID_WEAK
        return NEUTRAL_B if alt else NEUTRAL_A


def _is_rth(ts: float) -> bool:
    dt_utc = datetime.utcfromtimestamp(ts)
    ph_h = (dt_utc.hour + _PHX_OFFSET) % 24
    minutes = ph_h * 60 + dt_utc.minute
    return (RTH_OPEN_H * 60 + RTH_OPEN_M) <= minutes < (RTH_CLOSE_H * 60 + RTH_CLOSE_M)


# ──────────────────────────────────────────────────────────
#  Main canvas
# ──────────────────────────────────────────────────────────

class ATASCanvas(QWidget):

    MIN_ROW_H    = 3
    MAX_ROW_H    = 80
    DEF_ROW_H    = 6

    MIN_CANDLE_W = 8
    MAX_CANDLE_W = 200
    DEF_CANDLE_W = 14

    FOOTPRINT_SHOW_THRESH = 14

    PRICE_PANEL_W    = 64
    DELTA_BAR_W      = 20
    CANDLE_GAP       = 2
    VOL_OVERLAY_FRAC = 0.22   # panel width fraction for profile overlay (narrower)

    STACK_ASK_RATIO  = 1.5
    STACK_BID_RATIO  = 1.5
    STACK_MIN_LEVELS = 3

    CLUSTER_H = 80   # pixels reserved at bottom for cluster heatmap bar

    # Profile view modes
    PROFILE_MODES = ['RTH VOL', 'RTH Δ', 'ON VOL', 'ON Δ', 'ALL']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(700, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.grabGesture(Qt.GestureType.PinchGesture)

        self._tf_secs: int  = 300      # default 5m
        self._tf_label: str = '5m'
        self._n_days: int   = 5
        self._candles: List[CandleData] = []
        self._current_price: float = 5510.0
        self._needs_autofit: bool = True
        self._auto_zoom:    bool = True   # continuously fit vertical to visible candles

        self._row_h:        float = self.DEF_ROW_H
        self._candle_w:     float = self.DEF_CANDLE_W
        self._price_offset: float = 0.0
        self._time_offset:  int   = 0

        self._mouse_x: int = 0
        self._mouse_y: int = 0
        self._hover_candle_idx: Optional[int] = None

        self._drag_start:  Optional[Tuple[float, float]] = None
        self._drag_price0: float = 0.0
        self._drag_time0:  int   = 0

        self._pinch_start_row_h:    float = self.DEF_ROW_H
        self._pinch_start_candle_w: float = self.DEF_CANDLE_W

        self.show_vol_profile:      bool = True
        self.show_delta_bars:       bool = True
        self.show_footprint:        bool = True
        self.show_ma:               bool = True
        self.ma_period:             int  = 20
        self.show_divergence:       bool = True
        self.show_stacked_imbalance: bool = True
        self.show_session_lines:    bool = True

        # Profile mode: index into PROFILE_MODES
        self._profile_mode: int = 4  # default ALL

        self._fonts_init()
        self._reload_data()

    # ── fonts ─────────────────────────────────────────────
    def _fonts_init(self):
        # Menlo → Monaco → Courier New fallback chain (clean monospace)
        mono = "Menlo"
        self._font_cell  = QFont(mono, 7)
        self._font_small = QFont(mono, 7)
        self._font_small.setBold(True)
        self._font_price = QFont(mono, 8)
        self._font_price.setBold(True)
        self._font_time  = QFont(mono, 7)
        self._fm_cell    = QFontMetrics(self._font_cell)

    # ── data ──────────────────────────────────────────────
    def _reload_data(self):
        self._candles = _gen_candles(self._n_days, self._tf_secs)
        if self._candles:
            self._current_price = self._candles[-1].close
        self._price_offset = 0.0
        self._time_offset = 0  # 0 = most recent candle at right edge
        self._needs_autofit = True
        self.update()

    def _auto_fit(self):
        """Fit price range and candle width to show data on first render."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0 or not self._candles:
            return
        # Aim to show ~60 candles; clamp to available data
        x_left, x_right = self._chart_x_range()
        chart_w = x_right - x_left
        if chart_w <= 0:
            return
        target_count = min(60, len(self._candles))
        self._candle_w = max(self.MIN_CANDLE_W,
                             min(self.MAX_CANDLE_W, chart_w / target_count))
        # Recalculate visible positions with new candle_w
        positions = self._candle_positions()
        if not positions:
            return
        visible = [self._candles[ci] for ci, _ in positions]
        lo = min(c.low  for c in visible)
        hi = max(c.high for c in visible)
        price_range = hi - lo
        if price_range < 1.0:
            price_range = 1.0
        # 10% padding top and bottom
        lo -= price_range * 0.10
        hi += price_range * 0.10
        price_range = hi - lo
        # Set row_h so all candle prices fit in the chart height (excluding cluster bar)
        n_ticks = price_range / TICK
        h_chart = h - self.CLUSTER_H
        if n_ticks > 0:
            self._row_h = max(self.MIN_ROW_H,
                              min(self.MAX_ROW_H, h_chart / n_ticks))
        # Center on the midpoint of visible candle prices
        mid = (lo + hi) / 2
        self._price_offset = (mid - self._current_price) / TICK

    def _fit_vertical_to_visible(self):
        """Adjust row_h and price_offset to fit visible candles with 10% padding."""
        positions = self._candle_positions()
        if not positions:
            return
        visible = [self._candles[ci] for ci, _ in positions]
        lo = min(c.low  for c in visible)
        hi = max(c.high for c in visible)
        price_range = hi - lo
        if price_range < 1.0:
            price_range = 1.0
        lo -= price_range * 0.10
        hi += price_range * 0.10
        price_range = hi - lo
        n_ticks = price_range / TICK
        h_chart = self.height() - self.CLUSTER_H
        if n_ticks > 0 and h_chart > 0:
            self._row_h = max(self.MIN_ROW_H, min(self.MAX_ROW_H, h_chart / n_ticks))
        mid = (lo + hi) / 2
        self._price_offset = (mid - self._current_price) / TICK

    def _fit_all(self):
        """Fit all candles into view."""
        if not self._candles:
            return
        x_left, x_right = self._chart_x_range()
        chart_w = x_right - x_left
        if chart_w <= 0:
            return
        self._time_offset = 0
        self._candle_w = max(self.MIN_CANDLE_W,
                             min(self.MAX_CANDLE_W, chart_w / len(self._candles)))
        self._fit_vertical_to_visible()
        self.update()

    def _go_to_latest(self):
        """Scroll to the most recent candle and fit vertical."""
        self._time_offset = 0
        self._fit_vertical_to_visible()
        self.update()

    def set_timeframe(self, tf_label: str, tf_secs: int, n_days: int):
        self._tf_label = tf_label
        self._tf_secs  = tf_secs
        self._n_days   = n_days
        _RAND.seed(int(time.time() / 5))
        self._reload_data()

    def tick_live(self):
        if not self._candles:
            return
        live = self._candles[-1]
        dp = _RAND.gauss(0, 0.5)
        self._current_price = round((self._current_price + dp) * 4) / 4
        self._current_price = max(5490.0, min(5530.0, self._current_price))
        _add_live_tick(live, self._current_price)
        live.close = self._current_price
        live.high  = max(live.high, self._current_price)
        live.low   = min(live.low,  self._current_price)
        self.update()

    # ── coordinate helpers ────────────────────────────────
    def _visible_price_range(self) -> Tuple[float, float]:
        h = self.height() - self.CLUSTER_H
        n_ticks  = h / self._row_h
        mid      = self._current_price + self._price_offset * TICK
        return mid - (n_ticks / 2) * TICK, mid + (n_ticks / 2) * TICK

    def _price_to_y(self, price: float) -> float:
        h = self.height() - self.CLUSTER_H
        pmin, pmax = self._visible_price_range()
        if pmax == pmin:
            return h / 2
        return h - (price - pmin) / (pmax - pmin) * h

    def _y_to_price(self, y: float) -> float:
        h = self.height() - self.CLUSTER_H
        pmin, pmax = self._visible_price_range()
        return pmin + (h - y) / h * (pmax - pmin)

    def _chart_x_range(self) -> Tuple[float, float]:
        return 0.0, float(self.width() - self.PRICE_PANEL_W)

    def _candle_positions(self) -> List[Tuple[int, float]]:
        x_left, x_right = self._chart_x_range()
        if x_right <= x_left:
            return []
        cw        = max(1.0, self._candle_w)
        n_visible = max(1, int((x_right - x_left) / cw))
        total     = len(self._candles)
        end_idx   = total - self._time_offset
        start_idx = max(0, end_idx - n_visible)
        end_idx   = min(total, end_idx)
        result = []
        for ci in range(start_idx, end_idx):
            slots_from_right = end_idx - 1 - ci
            x_center = x_right - slots_from_right * cw - cw / 2
            result.append((ci, x_center))
        return result

    # ── volume / session profiles ─────────────────────────
    def _build_volume_profile(self, session: str = 'all') -> Dict[float, Tuple[int, int]]:
        """Build vol+delta profile. session='rth','on','all'."""
        agg: Dict[float, List[int]] = {}
        for ci, _ in self._candle_positions():
            c = self._candles[ci]
            if session == 'rth' and not _is_rth(c.timestamp):
                continue
            if session == 'on' and _is_rth(c.timestamp):
                continue
            for px, lv in c.levels.items():
                if px not in agg:
                    agg[px] = [0, 0]
                agg[px][0] += lv.total
                agg[px][1] += lv.delta
        return {px: (v[0], v[1]) for px, v in agg.items()}

    def _get_active_profile(self) -> Tuple[Dict[float, Tuple[int, int]], str, str]:
        """Return (profile_dict, session_key, mode_label)."""
        mode = self.PROFILE_MODES[self._profile_mode]
        if mode == 'RTH VOL':
            return self._build_volume_profile('rth'), 'vol', 'RTH VOL'
        elif mode == 'RTH Δ':
            return self._build_volume_profile('rth'), 'delta', 'RTH DELTA'
        elif mode == 'ON VOL':
            return self._build_volume_profile('on'), 'vol', 'ON VOL'
        elif mode == 'ON Δ':
            return self._build_volume_profile('on'), 'delta', 'ON DELTA'
        else:
            return self._build_volume_profile('all'), 'both', 'ALL'

    def _compute_poc_va(self, profile: Dict[float, Tuple[int, int]]):
        if not profile:
            return self._current_price, self._current_price, self._current_price
        prices        = sorted(profile.keys())
        poc_price     = max(profile, key=lambda px: profile[px][0])
        total_vol     = sum(v for v, _ in profile.values())
        sorted_by_vol = sorted(prices, key=lambda px: profile[px][0], reverse=True)
        va_set: set   = set()
        acc = 0
        for px in sorted_by_vol:
            acc += profile[px][0]
            va_set.add(px)
            if acc >= total_vol * 0.40:
                break
        vah = max(va_set) if va_set else poc_price
        val = min(va_set) if va_set else poc_price
        return poc_price, vah, val

    def _compute_sma(self, period: int) -> List[Tuple[float, float]]:
        result = []
        for ci, x_center in self._candle_positions():
            if ci < period - 1:
                continue
            closes = [self._candles[j].close for j in range(ci - period + 1, ci + 1)]
            result.append((x_center, sum(closes) / period))
        return result

    # ── delta divergence ──────────────────────────────────
    def _detect_delta_divergences(self) -> List[Tuple[str, int, int]]:
        positions = self._candle_positions()
        if len(positions) < 6:
            return []

        indices = [ci for ci, _ in positions]
        n = len(indices)

        cum_delta: List[float] = []
        running = 0.0
        for ci in indices:
            running += self._candles[ci].delta
            cum_delta.append(running)

        pivot_highs = []
        for i in range(2, n - 2):
            ci = indices[i]
            h  = self._candles[ci].high
            if (h > self._candles[indices[i-1]].high and
                h > self._candles[indices[i-2]].high and
                h > self._candles[indices[i+1]].high and
                h > self._candles[indices[i+2]].high):
                pivot_highs.append((ci, h, cum_delta[i]))

        pivot_lows = []
        for i in range(2, n - 2):
            ci = indices[i]
            lo = self._candles[ci].low
            if (lo < self._candles[indices[i-1]].low and
                lo < self._candles[indices[i-2]].low and
                lo < self._candles[indices[i+1]].low and
                lo < self._candles[indices[i+2]].low):
                pivot_lows.append((ci, lo, cum_delta[i]))

        divs: List[Tuple[str, int, int]] = []

        for j in range(1, len(pivot_highs)):
            ci1, p1, d1 = pivot_highs[j-1]
            ci2, p2, d2 = pivot_highs[j]
            if p2 > p1 and d2 < d1:
                divs.append(('bearish', ci1, ci2))

        for j in range(1, len(pivot_lows)):
            ci1, p1, d1 = pivot_lows[j-1]
            ci2, p2, d2 = pivot_lows[j]
            if p2 < p1 and d2 > d1:
                divs.append(('bullish', ci1, ci2))

        return divs[-5:]

    # ── stacked imbalance ─────────────────────────────────
    def _find_stacked_imbalances(self, candle: CandleData) -> List[Tuple[str, float, float, int]]:
        if not candle.levels:
            return []
        sorted_prices = sorted(candle.levels.keys())
        stacks = []
        i = 0
        while i < len(sorted_prices):
            px = sorted_prices[i]
            lv = candle.levels[px]
            if lv.ask_ratio >= self.STACK_ASK_RATIO:
                direction = 'buy'
            elif lv.bid_ratio >= self.STACK_BID_RATIO:
                direction = 'sell'
            else:
                i += 1
                continue
            count = 1
            j = i + 1
            while j < len(sorted_prices):
                nlv = candle.levels.get(sorted_prices[j])
                if nlv is None:
                    break
                if direction == 'buy'  and nlv.ask_ratio >= self.STACK_ASK_RATIO:
                    count += 1; j += 1
                elif direction == 'sell' and nlv.bid_ratio >= self.STACK_BID_RATIO:
                    count += 1; j += 1
                else:
                    break
            run_end = sorted_prices[j - 1]
            if count >= self.STACK_MIN_LEVELS:
                stacks.append((direction, px, run_end, count))
            i = j
        return stacks

    # ── paintEvent ────────────────────────────────────────
    def paintEvent(self, _ev):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        # Auto-fit once we know the real widget dimensions
        if self._needs_autofit and self._candles:
            self._auto_fit()
            self._needs_autofit = False
        # Auto-zoom: continuously fit vertical scale to visible candles
        elif self._auto_zoom and self._candles:
            self._fit_vertical_to_visible()

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        p.fillRect(0, 0, w, h, BG)

        if not self._candles:
            p.setFont(self._font_price)
            p.setPen(QPen(TEXT_DIM))
            msg = "No data — waiting for feed…"
            fm = QFontMetrics(self._font_price)
            p.drawText((w - fm.horizontalAdvance(msg)) // 2, h // 2, msg)
            p.end()
            return

        h_chart = h - self.CLUSTER_H
        pmin, pmax = self._visible_price_range()
        x_left, x_right = self._chart_x_range()

        profile, prof_type, prof_label = self._get_active_profile()
        poc_price, vah, val = self._compute_poc_va(profile)

        if self.show_session_lines:
            self._draw_session_backgrounds(p, int(x_left), int(x_right), h_chart)

        self._draw_grid(p, pmin, pmax, int(x_left), int(x_right), h_chart)

        if self.show_session_lines:
            self._draw_session_lines(p, int(x_left), int(x_right), h_chart)

        self._draw_key_levels(p, pmin, pmax, int(x_left), int(x_right), h_chart, poc_price, vah, val)
        self._draw_candles(p, pmin, pmax, x_left, x_right - x_left, h_chart)

        if self.show_divergence:
            self._draw_delta_divergences(p, h_chart)

        if self.show_ma:
            self._draw_sma(p, h_chart)

        if self.show_vol_profile:
            overlay_w = int((x_right - x_left) * self.VOL_OVERLAY_FRAC)
            self._draw_volume_overlay(p, pmin, pmax, int(x_left), overlay_w, h_chart,
                                      profile, prof_type, prof_label, poc_price, vah, val)

        self._draw_price_scale(p, pmin, pmax, int(x_right), self.PRICE_PANEL_W, h_chart)

        sep_pen = QPen(QColor(0x22, 0x22, 0x33))
        sep_pen.setWidth(1)
        p.setPen(sep_pen)
        p.drawLine(int(x_right), 0, int(x_right), h)

        self._draw_current_price_line(p, int(x_left), int(x_right), h_chart)
        self._draw_mode_indicator(p, w, h_chart)
        self._draw_cluster_grid(p, int(x_left), int(x_right), h)
        self._draw_scrollbar(p, int(x_left), int(x_right), h_chart)
        self._draw_zoom_indicator(p, int(x_left), int(x_right))
        p.end()

    # ── draw: session backgrounds ─────────────────────────
    def _draw_session_backgrounds(self, p: QPainter, x0: int, x1: int, h: int):
        cw = self._candle_w
        half = cw / 2
        for ci, x_center in self._candle_positions():
            col = RTH_BG if _is_rth(self._candles[ci].timestamp) else OFF_BG
            p.fillRect(int(x_center - half), 0, max(1, int(cw)), h, col)

    # ── draw: session lines ───────────────────────────────
    def _draw_session_lines(self, p: QPainter, x0: int, x1: int, h: int):
        cw = self._candle_w
        for ci, x_center in self._candle_positions():
            ts  = self._candles[ci].timestamp
            dtu = datetime.utcfromtimestamp(ts)
            ph_h = (dtu.hour + _PHX_OFFSET) % 24
            ph_m = dtu.minute
            is_open  = (ph_h == RTH_OPEN_H  and ph_m == RTH_OPEN_M)
            is_close = (ph_h == RTH_CLOSE_H and ph_m == RTH_CLOSE_M)
            if not (is_open or is_close):
                continue
            # Thin vertical line + small label (tertiary importance)
            color = QColor(0x00, 0xcc, 0x66, 120) if is_open else QColor(0xcc, 0x44, 0x44, 120)
            pen   = QPen(color)
            pen.setWidth(1)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            x = int(x_center - cw / 2)
            p.drawLine(x, 0, x, h)
            p.setFont(self._font_small)
            label_col = QColor(color); label_col.setAlpha(140)
            p.setPen(QPen(label_col))
            p.drawText(x + 3, 11, "O" if is_open else "C")

    # ── draw: grid ────────────────────────────────────────
    def _draw_grid(self, p: QPainter, pmin: float, pmax: float,
                   x0: int, x1: int, h: int):
        lo = math.floor(pmin * 4) / 4
        hi = math.ceil(pmax  * 4) / 4
        px = lo
        whole_pen = QPen(GRID);      whole_pen.setWidth(1)
        tick_pen  = QPen(GRID_TICK); tick_pen.setWidth(1)
        while px <= hi + 0.001:
            y = int(self._price_to_y(px))
            p.setPen(whole_pen if round(px * 4) % 4 == 0 else tick_pen)
            p.drawLine(x0, y, x1, y)
            px = round((px + TICK) * 4) / 4

    # ── draw: key levels ─────────────────────────────────
    def _draw_key_levels(self, p: QPainter, pmin: float, pmax: float,
                         x0: int, x1: int, h: int,
                         poc_price: float, vah: float, val: float):
        rn_pen = QPen(QColor(0xff, 0x88, 0x00, 55))
        rn_pen.setStyle(Qt.PenStyle.DotLine)
        p.setPen(rn_pen)
        lo, hi = math.floor(pmin), math.ceil(pmax)
        px = lo
        while px <= hi:
            if px % 5 == 0 and pmin <= px <= pmax:
                p.drawLine(x0, int(self._price_to_y(px)), x1, int(self._price_to_y(px)))
            px += 1

        # Value area shading — very subtle (8-10% opacity)
        va_top    = self._price_to_y(max(vah, val))
        va_bottom = self._price_to_y(min(vah, val))
        va_top    = max(0, min(h, va_top))
        va_bottom = max(0, min(h, va_bottom))
        if va_bottom > va_top:
            p.fillRect(x0, int(va_top), x1 - x0, int(va_bottom - va_top),
                       QColor(0x00, 0xaa, 0xcc, 12))

        # VAH/VAL — thin 1px dotted, subtle color
        VAH_COL = QColor(0x00, 0xaa, 0xcc, 130)
        VAL_COL = QColor(0x00, 0xaa, 0xcc, 130)
        for price, col, label in [
            (vah, VAH_COL, "VAH"),
            (val, VAL_COL, "VAL"),
        ]:
            y_raw = self._price_to_y(price)
            y     = int(max(0, min(h - 1, y_raw)))
            pen   = QPen(col); pen.setWidth(1); pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.drawLine(x0, y, x1, y)
            p.setFont(self._font_small)
            fm = QFontMetrics(self._font_small)
            lw = fm.horizontalAdvance(label)
            lc = QColor(col); lc.setAlpha(160)
            p.setPen(QPen(lc))
            ly = max(fm.ascent() + 1, min(h - 3, y - 2))
            p.fillRect(x1 - lw - 6, ly - fm.ascent(), lw + 4, fm.height(),
                       QColor(0x00, 0x0a, 0x12, 160))
            p.drawText(x1 - lw - 4, ly, label)

        # POC — thin 1px dashed amber line
        y_poc = int(max(0, min(h - 1, self._price_to_y(poc_price))))
        pen   = QPen(QColor(0xff, 0xcc, 0x00, 180))
        pen.setWidth(1); pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(x0, y_poc, x1, y_poc)
        p.setFont(self._font_small)
        fm = QFontMetrics(self._font_small)
        lw = fm.horizontalAdvance("POC")
        p.fillRect(x1 - lw - 6, y_poc - fm.ascent(), lw + 4, fm.height(),
                   QColor(0x14, 0x0c, 0x00, 160))
        p.setPen(QPen(QColor(0xff, 0xcc, 0x00, 200)))
        p.drawText(x1 - lw - 4, y_poc - 2, "POC")

    # ── draw: candles ─────────────────────────────────────
    def _draw_candles(self, p: QPainter, pmin: float, pmax: float,
                      cx: float, cw_total: float, h: int):
        positions = self._candle_positions()
        if not positions:
            return

        footprint_mode = self._row_h >= self.FOOTPRINT_SHOW_THRESH and self.show_footprint
        delta_w   = self.DELTA_BAR_W if (footprint_mode and self.show_delta_bars) else 0
        fp_gap    = max(6, int(self._candle_w * 0.10)) if footprint_mode else self.CANDLE_GAP
        body_w    = max(8, self._candle_w - fp_gap - delta_w)
        half_body = body_w / 2

        lo = math.floor(pmin * 4) / 4
        hi = math.ceil(pmax  * 4) / 4

        for ci, x_center in positions:
            self._draw_one_candle(
                p, self._candles[ci], x_center, half_body,
                pmin, pmax, lo, hi, h, footprint_mode, delta_w
            )

        p.setFont(self._font_time)
        p.setPen(QPen(TEXT_DIM))
        step = max(1, len(positions) // 10)
        for ci, x_center in positions[::step]:
            dt    = datetime.fromtimestamp(self._candles[ci].timestamp)
            label = dt.strftime("%H:%M")
            fm    = QFontMetrics(self._font_time)
            p.drawText(int(x_center - fm.horizontalAdvance(label) / 2), h - 2, label)

    # ── draw: one candle ──────────────────────────────────
    def _draw_one_candle(self, p: QPainter, candle: CandleData,
                         x_center: float, half_body: float,
                         pmin: float, pmax: float, lo: float, hi: float, h: int,
                         footprint_mode: bool, delta_w: int):
        is_bull  = candle.is_bull
        body_col = GREEN if is_bull else RED
        body_x   = x_center - half_body
        body_w   = half_body * 2

        y_open   = self._price_to_y(candle.open)
        y_close  = self._price_to_y(candle.close)
        y_high   = self._price_to_y(candle.high)
        y_low    = self._price_to_y(candle.low)
        body_top    = min(y_open, y_close)
        body_bottom = max(y_open, y_close)
        body_h      = max(2, body_bottom - body_top)

        stacks = (self._find_stacked_imbalances(candle)
                  if (footprint_mode and self.show_stacked_imbalance) else [])

        stack_map: Dict[float, Tuple[str, int]] = {}
        for (direction, s_lo, s_hi, count) in stacks:
            px = s_lo
            while px <= s_hi + 0.001:
                stack_map[round(px * 4) / 4] = (direction, count)
                px = round((px + TICK) * 4) / 4

        if footprint_mode:
            tick_px = lo
            alt = False
            while tick_px <= hi + 0.001:
                y_ct = self._price_to_y(tick_px + TICK)
                y_cb = self._price_to_y(tick_px)

                # Determine if this row is in body, upper wick, or lower wick
                in_body  = (y_ct < body_bottom and y_cb > body_top)
                # upper wick: high above body top
                in_upper = (not in_body and y_cb >= y_high and y_ct < body_top)
                # lower wick: low below body bottom
                in_lower = (not in_body and y_ct <= y_low and y_cb > body_bottom)
                in_wick  = in_upper or in_lower

                if in_body or in_wick:
                    lv = candle.levels.get(tick_px)
                    in_stack = stack_map.get(round(tick_px * 4) / 4) if in_body else None

                    if in_stack:
                        bg = BUY_STACK_BG if in_stack[0] == 'buy' else SELL_STACK_BG
                    else:
                        bg = _cell_bg(lv, alt, wick=in_wick) if (lv and lv.total > 0) else (
                            (NEUTRAL_BW if alt else NEUTRAL_AW) if in_wick else
                            (NEUTRAL_B  if alt else NEUTRAL_A)
                        )

                    if in_body:
                        clip_top    = max(y_ct, body_top)
                        clip_bottom = min(y_cb, body_bottom)
                    elif in_upper:
                        clip_top    = max(y_ct, y_high)
                        clip_bottom = min(y_cb, body_top)
                    else:  # lower wick
                        clip_top    = max(y_ct, body_bottom)
                        clip_bottom = min(y_cb, y_low)

                    if clip_bottom > clip_top:
                        # Wick area: draw narrow band (half body width, centered)
                        if in_wick:
                            wick_bx = body_x + body_w * 0.25
                            wick_bw = body_w * 0.5
                            p.fillRect(int(wick_bx), int(clip_top),
                                       max(1, int(wick_bw)), int(clip_bottom - clip_top), bg)
                        else:
                            p.fillRect(int(body_x), int(clip_top),
                                       int(body_w), int(clip_bottom - clip_top), bg)
                            if in_stack:
                                stripe = BUY_STACK_GLOW if in_stack[0] == 'buy' else SELL_STACK_GLOW
                                p.fillRect(int(body_x), int(clip_top),
                                           3, int(clip_bottom - clip_top), stripe)
                alt = not alt
                tick_px = round((tick_px + TICK) * 4) / 4
        else:
            # Clean solid fills: dark green / dark red bodies
            fill = QColor(0x1a, 0x3a, 0x2a) if is_bull else QColor(0x3a, 0x1a, 0x1a)
            p.fillRect(int(body_x), int(body_top), int(body_w), int(body_h), fill)

        # Stack glow borders
        if footprint_mode and stacks:
            for (direction, s_lo, s_hi, count) in stacks:
                y_st = self._price_to_y(s_hi + TICK)
                y_sb = self._price_to_y(s_lo)
                ct   = max(y_st, body_top)
                cb   = min(y_sb, body_bottom)
                if cb > ct:
                    glow = BUY_STACK_GLOW if direction == 'buy' else SELL_STACK_GLOW
                    gpen = QPen(glow); gpen.setWidth(2)
                    p.setPen(gpen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(int(body_x) + 1, int(ct), int(body_w) - 2, int(cb - ct))
                    if (cb - ct) >= 10:
                        lbl = f"STK\u00d7{count}"
                        p.setFont(self._font_small)
                        gc = QColor(glow); gc.setAlpha(255)
                        p.setPen(QPen(gc))
                        fm  = QFontMetrics(self._font_small)
                        tw  = fm.horizontalAdvance(lbl)
                        tx  = int(body_x + body_w - tw - 2)
                        ty  = int((ct + cb) / 2 + fm.ascent() / 2 - 1)
                        if tx >= int(body_x):
                            p.drawText(tx, ty, lbl)

        # Body outline
        pen = QPen(body_col); pen.setWidth(1)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(int(body_x), int(body_top), int(body_w), int(body_h))

        # Wicks — always thin 1px, same color as border
        wick_w = 1
        wick = QPen(body_col); wick.setWidth(wick_w)
        p.setPen(wick)
        mid_x = int(x_center)
        if y_high < body_top:
            p.drawLine(mid_x, int(y_high), mid_x, int(body_top))
        if y_low > body_bottom:
            p.drawLine(mid_x, int(body_bottom), mid_x, int(y_low))

        if footprint_mode:
            # Scale font with row height (Menlo monospace) — minimum 8pt
            font_pt = 8 if self._row_h < 22 else (9 if self._row_h < 30 else 10)
            if font_pt != getattr(self, '_last_cell_font_pt', 0):
                self._font_cell = QFont("Menlo", font_pt)
                self._fm_cell   = QFontMetrics(self._font_cell)
                self._last_cell_font_pt = font_pt

            p.setFont(self._font_cell)
            fm = self._fm_cell
            min_cell_h = max(fm.height() + 2, 10)

            # Precompute per-candle metrics for smart filtering + highlighting
            max_row_vol = max((lv.total for lv in candle.levels.values()), default=1) or 1
            poc_lvl_price = candle.poc_price
            vol_threshold = max_row_vol * 0.05  # skip rows < 5% of max

            # Bid×ask text: bid left, ask right, color-coded by dominance
            tick_px = lo
            while tick_px <= hi + 0.001:
                y_rt = self._price_to_y(tick_px + TICK)
                y_rb = self._price_to_y(tick_px)

                in_body  = (y_rt < body_bottom and y_rb > body_top)
                in_upper = (not in_body and y_rb >= y_high and y_rt < body_top)
                in_lower = (not in_body and y_rt <= y_low  and y_rb > body_bottom)

                if in_body or in_upper or in_lower:
                    lv = candle.levels.get(tick_px)
                    # Skip zero-volume and very-low-volume rows
                    if lv and lv.total >= vol_threshold:
                        dim = in_upper or in_lower
                        if in_body:
                            ct = max(y_rt, body_top)
                            cb = min(y_rb, body_bottom)
                            # Subtle cyan highlight for highest-volume row
                            if abs(tick_px - poc_lvl_price) < 0.001:
                                p.fillRect(int(body_x), int(ct), int(body_w),
                                           max(1, int(cb - ct)), QColor(0x00, 0xcc, 0xff, 18))
                        elif in_upper:
                            ct = max(y_rt, y_high)
                            cb = min(y_rb, body_top)
                        else:
                            ct = max(y_rt, body_bottom)
                            cb = min(y_rb, y_low)

                        # Fully-opaque colors — bright and easy to read
                        # Wick rows slightly dimmed but still legible
                        is_poc = abs(tick_px - poc_lvl_price) < 0.001
                        if is_poc:
                            # POC row: bright green bid, bright cyan ask
                            bid_col = QColor(0x00, 0xff, 0x88)   # bright green
                            ask_col = QColor(0x00, 0xdd, 0xff)   # bright cyan
                        elif lv.ask_ratio >= 1.3:
                            # Ask-dominant: bright green ask, muted red bid
                            ask_col = QColor(0x00, 0xff, 0x88) if not dim else QColor(0x00, 0xcc, 0x66)
                            bid_col = QColor(0xaa, 0x44, 0x55) if not dim else QColor(0x77, 0x33, 0x44)
                        elif lv.bid_ratio >= 1.3:
                            # Bid-dominant: bright red bid, muted green ask
                            bid_col = QColor(0xff, 0x44, 0x66) if not dim else QColor(0xcc, 0x33, 0x55)
                            ask_col = QColor(0x44, 0xaa, 0x55) if not dim else QColor(0x33, 0x77, 0x44)
                        else:
                            # Balanced: light gray, slightly dimmer in wick
                            c = QColor(0xbb, 0xbb, 0xcc) if not dim else QColor(0x88, 0x88, 0x99)
                            bid_col = ask_col = c

                        if (cb - ct) >= min_cell_h:
                            ty       = int((ct + cb) / 2 + fm.ascent() / 2 - 1)
                            bid_txt  = str(lv.bid_vol)
                            ask_txt  = str(lv.ask_vol)
                            bx_left  = int(body_x) + 2
                            bx_right = int(body_x + body_w) - fm.horizontalAdvance(ask_txt) - 2
                            # Subtle dark backdrop for contrast against cell overlays
                            pad = 1
                            p.fillRect(bx_left - pad, int(ct) + pad,
                                       fm.horizontalAdvance(bid_txt) + pad * 2,
                                       int(cb - ct) - pad * 2,
                                       QColor(0x00, 0x00, 0x00, 60))
                            p.fillRect(bx_right - pad, int(ct) + pad,
                                       fm.horizontalAdvance(ask_txt) + pad * 2,
                                       int(cb - ct) - pad * 2,
                                       QColor(0x00, 0x00, 0x00, 60))
                            p.setPen(QPen(bid_col))
                            p.drawText(bx_left, ty, bid_txt)
                            p.setPen(QPen(ask_col))
                            p.drawText(bx_right, ty, ask_txt)
                tick_px = round((tick_px + TICK) * 4) / 4

            # Delta bars
            if self.show_delta_bars and delta_w > 0:
                dx0 = x_center + half_body + 2
                max_d = max((abs(lv.delta) for lv in candle.levels.values()), default=1)
                max_d = max(max_d, 1)
                tick_px = lo
                while tick_px <= hi + 0.001:
                    y_rt = self._price_to_y(tick_px + TICK)
                    y_rb = self._price_to_y(tick_px)
                    if y_rt < body_bottom and y_rb > body_top:
                        lv = candle.levels.get(tick_px)
                        if lv and lv.delta != 0:
                            ct = max(y_rt, body_top)
                            cb = min(y_rb, body_bottom)
                            bh = max(1, int(cb - ct) - 1)
                            bw = max(1, int(abs(lv.delta) / max_d * delta_w))
                            p.fillRect(int(dx0), int(ct), bw, bh,
                                       GREEN if lv.delta > 0 else RED)
                    tick_px = round((tick_px + TICK) * 4) / 4

    # ── draw: delta divergences ───────────────────────────
    def _draw_delta_divergences(self, p: QPainter, h: int):
        divs = self._detect_delta_divergences()
        if not divs:
            return

        pos_map = {ci: x for ci, x in self._candle_positions()}
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        for div_type, ci1, ci2 in divs:
            if ci1 not in pos_map or ci2 not in pos_map:
                continue
            x1, x2 = pos_map[ci1], pos_map[ci2]
            c1, c2  = self._candles[ci1], self._candles[ci2]

            if div_type == 'bearish':
                py1    = self._price_to_y(c1.high)
                py2    = self._price_to_y(c2.high)
                color  = QColor(0xff, 0x33, 0x55, 180)
                ay1    = py1 - 14
                ay2    = py2 - 14
                label  = "\u25bc div"
                above  = True
            else:
                py1    = self._price_to_y(c1.low)
                py2    = self._price_to_y(c2.low)
                color  = QColor(0x00, 0xdd, 0x66, 180)
                ay1    = py1 + 14
                ay2    = py2 + 14
                label  = "\u25b2 div"
                above  = False

            dot_pen = QPen(color); dot_pen.setWidth(1)
            dot_pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(dot_pen)
            p.drawLine(int(x1), int(ay1), int(x2), int(ay2))

            # Small arrow markers
            sz = 5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            for ax, price_y in [(x1, py1), (x2, py2)]:
                path = QPainterPath()
                if above:
                    path.moveTo(ax, price_y - 3)
                    path.lineTo(ax - sz / 2, price_y - 3 - sz)
                    path.lineTo(ax + sz / 2, price_y - 3 - sz)
                else:
                    path.moveTo(ax, price_y + 3)
                    path.lineTo(ax - sz / 2, price_y + 3 + sz)
                    path.lineTo(ax + sz / 2, price_y + 3 + sz)
                path.closeSubpath()
                p.fillPath(path, QBrush(color))

            # Small label — secondary brightness
            lbl_col = QColor(color); lbl_col.setAlpha(160)
            p.setFont(self._font_small)
            p.setPen(QPen(lbl_col))
            fm = QFontMetrics(self._font_small)
            lx = int(x2 - fm.horizontalAdvance(label) / 2)
            ly = int(py2 - 20) if above else int(py2 + 28)
            p.drawText(lx, ly, label)

    # ── draw: SMA ─────────────────────────────────────────
    def _draw_sma(self, p: QPainter, h: int):
        pts = self._compute_sma(self.ma_period)
        if len(pts) < 2:
            return
        pen = QPen(PURPLE); pen.setWidth(2)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(pen)
        prev = None
        for x_c, sma_val in pts:
            y = self._price_to_y(sma_val)
            if prev is not None:
                p.drawLine(int(prev[0]), int(prev[1]), int(x_c), int(y))
            prev = (x_c, y)

    # ── draw: volume/session overlay ─────────────────────
    def _draw_volume_overlay(self, p: QPainter, pmin: float, pmax: float,
                              x0: int, overlay_w: int, h: int,
                              profile: Dict[float, Tuple[int, int]],
                              prof_type: str, prof_label: str,
                              poc_price: float, vah: float, val: float):
        if not profile:
            return
        prices    = sorted(profile.keys())
        max_vol   = max((v for v, _ in profile.values()), default=1) or 1
        max_delta = max((abs(d) for _, d in profile.values()), default=1) or 1

        # VOL bars: 60% of panel, DELTA bars: 35% — both narrow/subtle
        vol_area   = int(overlay_w * 0.60)
        delta_area = int(overlay_w * 0.35)

        poc_idx = prices.index(poc_price) if poc_price in prices else len(prices) // 2
        sigma   = max(len(prices) * 0.15, 3)

        # Draw volume profile (20-25% opacity)
        if prof_type in ('vol', 'both'):
            for i, px in enumerate(prices):
                if px < pmin or px > pmax:
                    continue
                vol, _ = profile[px]
                gw      = math.exp(-0.5 * ((i - poc_idx) / sigma) ** 2)
                bar_w   = max(2, int(vol * (0.7 + 0.3 * gw) / max_vol * vol_area))
                y_top   = self._price_to_y(px + TICK)
                y_bot   = self._price_to_y(px)
                row_h   = max(1, int(y_bot - y_top) - 1)

                # Session-aware color — kept subtle
                if prof_label.startswith('ON'):
                    if abs(px - poc_price) < 0.001:
                        col = QColor(0xdd, 0x88, 0xff, 50)
                    elif val <= px <= vah:
                        col = QColor(0xaa, 0x66, 0xdd, 35)
                    else:
                        col = QColor(0x88, 0x44, 0xaa, 22)
                else:
                    if abs(px - poc_price) < 0.001:
                        col = QColor(0xff, 0xcc, 0x00, 50)
                    elif val <= px <= vah:
                        col = QColor(0x00, 0xbb, 0xaa, 35)
                    else:
                        col = QColor(0x88, 0xaa, 0xcc, 22)
                p.fillRect(x0 + 2, int(y_top), bar_w, row_h, col)

        # Draw delta profile (15-20% opacity, narrower)
        if prof_type in ('delta', 'both'):
            for px in prices:
                if px < pmin or px > pmax:
                    continue
                _, delta = profile[px]
                if delta == 0:
                    continue
                bar_w = max(1, int(abs(delta) / max_delta * delta_area))
                y_top = self._price_to_y(px + TICK)
                y_bot = self._price_to_y(px)
                row_h = max(1, int(y_bot - y_top) - 1)
                if prof_label.startswith('ON'):
                    col = QColor(0x33, 0xcc, 0x66, 50) if delta > 0 else QColor(0xcc, 0x33, 0x55, 50)
                else:
                    col = QColor(0x00, 0xcc, 0x44, 55) if delta > 0 else QColor(0xcc, 0x22, 0x33, 55)
                p.fillRect(x0 + 2, int(y_top), bar_w, row_h, col)

        # Volume labels — only shown when rows are tall enough, very dim
        if prof_type in ('vol', 'both'):
            for i, px in enumerate(prices):
                if px < pmin or px > pmax:
                    continue
                vol, _ = profile[px]
                gw      = math.exp(-0.5 * ((i - poc_idx) / sigma) ** 2)
                bar_w   = max(2, int(vol * (0.7 + 0.3 * gw) / max_vol * vol_area))
                y_top   = self._price_to_y(px + TICK)
                y_bot   = self._price_to_y(px)
                row_h   = max(1, int(y_bot - y_top) - 1)
                if row_h >= 11:
                    p.setFont(self._font_small)
                    p.setPen(QPen(QColor(0x33, 0x33, 0x55)))
                    p.drawText(x0 + bar_w + 4, int(y_top + row_h - 2), str(vol))

        # Profile labels at top — tertiary brightness
        p.setFont(self._font_small)
        fm = QFontMetrics(self._font_small)
        if prof_type in ('vol', 'both'):
            p.setPen(QPen(QColor(0x33, 0x77, 0xaa, 160)))
            p.drawText(x0 + 3, 22, "VOL")
        if prof_type in ('delta', 'both'):
            p.setPen(QPen(QColor(0x00, 0xaa, 0x55, 160)))
            lbl_x = x0 + 3 + (28 if prof_type == 'both' else 0)
            p.drawText(lbl_x, 22, "DELTA")

        # Session label
        lbl_col = QColor(0xaa, 0x66, 0xcc, 180) if prof_label.startswith('ON') else QColor(0x00, 0xcc, 0xcc, 180)
        p.setPen(QPen(lbl_col))
        p.drawText(x0 + 3, 12, prof_label)

    # ── draw: price scale ─────────────────────────────────
    def _draw_price_scale(self, p: QPainter, pmin: float, pmax: float,
                           x0: int, pw: int, h: int):
        p.fillRect(x0, 0, pw, h, BG_PANEL)
        lo = math.floor(pmin * 4) / 4
        hi = math.ceil(pmax  * 4) / 4
        px = lo
        while px <= hi + 0.001:
            if round(px * 4) % 4 == 0:
                y = int(self._price_to_y(px))
                p.setPen(QPen(TEXT_DIM))
                p.setFont(self._font_price)
                p.drawText(x0 + 4, y + 4, f"{px:.2f}")
                tick_pen = QPen(GRID); tick_pen.setWidth(1)
                p.setPen(tick_pen)
                p.drawLine(x0, y, x0 + 4, y)
            px = round((px + TICK) * 4) / 4
        y_cur = int(self._price_to_y(self._current_price))
        p.fillRect(x0, y_cur - 9, pw, 18, QColor(0x00, 0x33, 0x44))
        p.setPen(QPen(CYAN))
        p.setFont(self._font_price)
        p.drawText(x0 + 4, y_cur + 5, f"{self._current_price:.2f}")

    # ── draw: current price line ──────────────────────────
    def _draw_current_price_line(self, p: QPainter, x0: int, x1: int, h: int):
        y   = int(self._price_to_y(self._current_price))
        pen = QPen(QColor(0x00, 0xff, 0xff, 220))
        pen.setWidth(1); pen.setStyle(Qt.PenStyle.DotLine)
        p.setPen(pen)
        p.drawLine(x0, y, x1, y)
        lbl = f" {self._current_price:.2f} "
        p.setFont(self._font_price)
        fm  = QFontMetrics(self._font_price)
        bw  = fm.horizontalAdvance(lbl) + 4
        bh  = fm.height() + 2
        bx  = x1 - bw - 4
        by  = y - bh // 2
        p.fillRect(bx, by, bw, bh, QColor(0x00, 0x44, 0x55))
        p.setPen(QPen(CYAN)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bx, by, bw, bh)
        p.setPen(QPen(CYAN))
        p.drawText(bx + 2, by + fm.ascent() + 1, lbl.strip())

    # ── draw: mode indicator ──────────────────────────────
    def _draw_mode_indicator(self, p: QPainter, w: int, h: int):
        in_fp = self._row_h >= self.FOOTPRINT_SHOW_THRESH
        mode  = "\u25c8 FOOTPRINT" if in_fp else "\u25b6 CANDLESTICK"
        col   = CYAN if in_fp else QColor(0x44, 0x44, 0x55)
        p.setFont(self._font_small)
        p.setPen(QPen(col))
        fm = QFontMetrics(self._font_small)
        p.drawText(w - self.PRICE_PANEL_W - fm.horizontalAdvance(mode) - 8, 12, mode)

    # ── draw: cluster heatmap bar ─────────────────────────
    def _draw_cluster_grid(self, p: QPainter, x0: int, x1: int, h: int):
        """5-row cluster heatmap across the bottom CLUSTER_H pixels."""
        if not self._candles:
            return

        ROW_H    = 16
        N_ROWS   = 5
        grid_y   = h - self.CLUSTER_H
        label_w  = 44  # width of row-label column
        chart_w  = x1 - x0 - label_w

        # Dark background for cluster area
        p.fillRect(x0, grid_y, x1 - x0, self.CLUSTER_H, QColor(0x06, 0x06, 0x10))
        # Separator line between chart and cluster
        sep = QPen(QColor(0x22, 0x33, 0x44)); sep.setWidth(1)
        p.setPen(sep)
        p.drawLine(x0, grid_y, x1, grid_y)

        positions = self._candle_positions()
        if not positions:
            return

        # Gather per-candle metrics
        candle_data = []   # list of (ci, x_center, bid, ask, total, delta)
        cum_delta   = 0
        first_cum   = True
        for ci, x_center in positions:
            c = self._candles[ci]
            bid   = sum(lv.bid_vol for lv in c.levels.values())
            ask   = sum(lv.ask_vol for lv in c.levels.values())
            total = bid + ask
            delta = ask - bid
            if first_cum:
                cum_delta = delta
                first_cum = False
            else:
                cum_delta += delta
            candle_data.append((ci, x_center, bid, ask, total, delta, cum_delta))

        # Per-row max for heat scaling
        max_delta = max((abs(d[5]) for d in candle_data), default=1) or 1
        max_bid   = max((d[2] for d in candle_data), default=1) or 1
        max_ask   = max((d[3] for d in candle_data), default=1) or 1
        max_vol   = max((d[4] for d in candle_data), default=1) or 1
        max_cum   = max((abs(d[6]) for d in candle_data), default=1) or 1

        row_labels = ["Delta", "Bid", "Ask", "Vol", "Ses Δ"]
        label_col  = QColor(0x55, 0x66, 0x77)
        p.setFont(self._font_small)
        fm = QFontMetrics(self._font_small)

        cw = max(1.0, self._candle_w)

        for row_idx, label in enumerate(row_labels):
            ry = grid_y + row_idx * ROW_H

            # Row label
            p.setPen(QPen(label_col))
            p.drawText(x0 + 2, ry + ROW_H - 4, label)

            # Row separator
            p.setPen(QPen(QColor(0x11, 0x11, 0x22)))
            p.drawLine(x0, ry + ROW_H, x1, ry + ROW_H)

            for ci, x_center, bid, ask, total, delta, cum_d in candle_data:
                cx0  = int(x_center - cw / 2) + 1
                cx1  = int(x_center + cw / 2) - 1
                cell_w = max(1, cx1 - cx0)
                cell_h = ROW_H - 1

                if row_idx == 0:   # Delta
                    ratio  = abs(delta) / max_delta
                    alpha  = int(0.15 * 255 + ratio * 0.65 * 255)
                    color  = QColor(0x00, 0xff, 0x66, alpha) if delta >= 0 else QColor(0xff, 0x33, 0x55, alpha)
                    val    = delta
                elif row_idx == 1: # Bid
                    ratio  = bid / max_bid
                    alpha  = int(0.15 * 255 + ratio * 0.65 * 255)
                    color  = QColor(0xff, 0x44, 0x55, alpha)
                    val    = bid
                elif row_idx == 2: # Ask
                    ratio  = ask / max_ask
                    alpha  = int(0.15 * 255 + ratio * 0.65 * 255)
                    color  = QColor(0x00, 0xff, 0x88, alpha)
                    val    = ask
                elif row_idx == 3: # Vol
                    ratio  = total / max_vol
                    alpha  = int(0.15 * 255 + ratio * 0.65 * 255)
                    color  = QColor(0x00, 0xcc, 0xff, alpha)
                    val    = total
                else:              # Ses Δ
                    ratio  = abs(cum_d) / max_cum
                    alpha  = int(0.15 * 255 + ratio * 0.65 * 255)
                    color  = QColor(0x00, 0xff, 0x66, alpha) if cum_d >= 0 else QColor(0xff, 0x33, 0x55, alpha)
                    val    = cum_d

                p.fillRect(cx0, ry + 1, cell_w, cell_h, color)

                # Hover highlight
                if ci == self._hover_candle_idx:
                    border_pen = QPen(QColor(0xff, 0xff, 0xff, 80))
                    border_pen.setWidth(1)
                    p.setPen(border_pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(cx0, ry + 1, cell_w - 1, cell_h - 1)

                # Value text — only if cell is wide enough
                if cw >= 20:
                    txt = str(val) if abs(val) < 10000 else f"{val/1000:.1f}k"
                    tw  = fm.horizontalAdvance(txt)
                    if tw < cell_w - 2:
                        p.setPen(QPen(QColor(0xff, 0xff, 0xff, 200)))
                        tx = cx0 + (cell_w - tw) // 2
                        ty = ry + ROW_H - 4
                        p.drawText(tx, ty, txt)

    # ── draw: mini scrollbar ─────────────────────────────
    def _draw_scrollbar(self, p: QPainter, x0: int, x1: int, h: int):
        """Draw a thin scrollbar at bottom showing position within all data."""
        if not self._candles:
            return
        total   = len(self._candles)
        cw      = max(1.0, self._candle_w)
        chart_w = x1 - x0
        n_vis   = max(1, int(chart_w / cw))
        if n_vis >= total:
            return  # all candles visible — no scrollbar needed
        sb_h = 4
        sb_y = h - sb_h
        sb_w = chart_w - 4
        p.fillRect(x0 + 2, sb_y, sb_w, sb_h, QColor(0x18, 0x18, 0x28))
        end_idx   = total - self._time_offset
        start_idx = max(0, end_idx - n_vis)
        vis_x0 = int(x0 + 2 + (start_idx / total) * sb_w)
        vis_x1 = int(x0 + 2 + (end_idx   / total) * sb_w)
        vis_w  = max(6, vis_x1 - vis_x0)
        p.fillRect(vis_x0, sb_y, vis_w, sb_h, QColor(0x00, 0xcc, 0xff, 140))

    # ── draw: zoom level indicator ────────────────────────
    def _draw_zoom_indicator(self, p: QPainter, x0: int, x1: int):
        """Show current candle width and auto-zoom state in top corner."""
        az_txt = "AZ:ON" if self._auto_zoom else "AZ:off"
        lbl    = f"{az_txt}  W:{self._candle_w:.0f}px"
        p.setFont(self._font_small)
        col = QColor(0x00, 0xcc, 0x88, 180) if self._auto_zoom else QColor(0x33, 0x33, 0x55)
        p.setPen(QPen(col))
        fm = QFontMetrics(self._font_small)
        tw = fm.horizontalAdvance(lbl)
        p.drawText(x1 - tw - 6, 24, lbl)

    # ── mouse / zoom ──────────────────────────────────────
    def wheelEvent(self, ev: QWheelEvent):
        dy   = ev.angleDelta().y()
        dx   = ev.angleDelta().x()
        mods = ev.modifiers()
        my   = int(ev.position().y())

        if mods & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+scroll = horizontal zoom (candle width)
            raw_steps = dy / 120.0
            factor = 1.0 + raw_steps * 0.15
            factor = max(0.5, min(2.0, factor))
            self._candle_w = max(self.MIN_CANDLE_W,
                                 min(self.MAX_CANDLE_W, self._candle_w * factor))
            if self._auto_zoom:
                self._fit_vertical_to_visible()
            ev.accept(); self.update(); return

        if mods & Qt.KeyboardModifier.ShiftModifier:
            # Shift+scroll = manual vertical zoom (cursor-centered), disables auto-zoom
            self._auto_zoom = False
            raw_steps = dy / 120.0
            factor = 1.0 + raw_steps * 0.15
            factor = max(0.5, min(2.0, factor))
            h = self.height()
            price_cursor = self._y_to_price(my)
            new_row_h = max(self.MIN_ROW_H, min(self.MAX_ROW_H, self._row_h * factor))
            if abs(new_row_h - self._row_h) >= 0.001:
                new_n_ticks = h / new_row_h
                new_mid = price_cursor - new_n_ticks * TICK * (my - h / 2) / h
                self._price_offset = (new_mid - self._current_price) / TICK
                self._row_h = new_row_h
            ev.accept(); self.update(); return

        # Plain scroll (vertical or horizontal trackpad) = time pan
        raw = dx if abs(dx) > abs(dy) else -dy
        step = max(1, int(abs(raw) / 15))
        direction = 1 if raw > 0 else -1
        x_left, x_right = self._chart_x_range()
        max_off = max(0, len(self._candles) - max(1, int((x_right - x_left) / self._candle_w)))
        self._time_offset = max(0, min(max_off, self._time_offset + direction * step))
        if self._auto_zoom:
            self._fit_vertical_to_visible()
        ev.accept(); self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_start  = (ev.position().x(), ev.position().y())
            self._drag_price0 = self._price_offset
            self._drag_time0  = self._time_offset

    def mouseMoveEvent(self, ev):
        self._mouse_x = int(ev.position().x())
        self._mouse_y = int(ev.position().y())
        # Track which candle column the cursor is hovering over
        hover = None
        mx = self._mouse_x
        for ci, x_center in self._candle_positions():
            half = self._candle_w / 2
            if abs(mx - x_center) <= half:
                hover = ci
                break
        if hover != self._hover_candle_idx:
            self._hover_candle_idx = hover
            self.update()
        if self._drag_start is None:
            return
        sx, sy  = self._drag_start
        cx, cy  = ev.position().x(), ev.position().y()
        # When auto-zoom is on, only pan horizontally — vertical is auto-fitted
        if not self._auto_zoom:
            self._price_offset = self._drag_price0 + (cy - sy) / self._row_h
        x_left, x_right = self._chart_x_range()
        max_off = max(0, len(self._candles) - max(1, int((x_right - x_left) / self._candle_w)))
        self._time_offset = max(0, min(max_off, self._drag_time0 - int((cx - sx) / self._candle_w)))
        if self._auto_zoom:
            self._fit_vertical_to_visible()
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_start = None

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._fit_all()

    def keyPressEvent(self, ev):
        key  = ev.key()
        x_left, x_right = self._chart_x_range()
        max_off = max(0, len(self._candles) - max(1, int((x_right - x_left) / self._candle_w)))

        if key == Qt.Key.Key_Left:
            self._time_offset = min(max_off, self._time_offset + 3)
            if self._auto_zoom: self._fit_vertical_to_visible()
            self.update()
        elif key == Qt.Key.Key_Right:
            self._time_offset = max(0, self._time_offset - 3)
            if self._auto_zoom: self._fit_vertical_to_visible()
            self.update()
        elif key == Qt.Key.Key_Up:
            self._auto_zoom = False
            self._row_h = min(self.MAX_ROW_H, self._row_h * 1.2)
            self.update()
        elif key == Qt.Key.Key_Down:
            self._auto_zoom = False
            self._row_h = max(self.MIN_ROW_H, self._row_h / 1.2)
            self.update()
        elif key == Qt.Key.Key_F:
            self._fit_all()
        elif key == Qt.Key.Key_L:
            self._go_to_latest()
        else:
            super().keyPressEvent(ev)

    def event(self, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.Gesture:
            return self._handle_gesture(ev)
        return super().event(ev)

    def _handle_gesture(self, ev) -> bool:
        gesture = ev.gesture(Qt.GestureType.PinchGesture)
        if gesture is None:
            return False
        gs = gesture.state()
        from PySide6.QtCore import Qt as _Qt
        if gs == _Qt.GestureState.GestureStarted:
            self._pinch_start_row_h    = self._row_h
            self._pinch_start_candle_w = self._candle_w
        elif gs in (_Qt.GestureState.GestureUpdated, _Qt.GestureState.GestureFinished):
            scale = gesture.totalScaleFactor()
            if scale > 0:
                self._row_h    = max(self.MIN_ROW_H,    min(self.MAX_ROW_H,    self._pinch_start_row_h    * scale))
                self._candle_w = max(self.MIN_CANDLE_W, min(self.MAX_CANDLE_W, self._pinch_start_candle_w * scale))
            self.update()
        ev.accept()
        return True


# ──────────────────────────────────────────────────────────
#  Panel wrapper (toolbar + canvas)
# ──────────────────────────────────────────────────────────

_BTN_BASE = (
    "QPushButton{background:#111827;color:#6b7280;border:1px solid #374151;"
    "font:7pt 'Menlo';padding:1px 4px;}"
    "QPushButton:checked{background:#064e3b;color:#34d399;border:1px solid #34d399;}"
    "QPushButton:hover{color:#e5e7eb;border-color:#6b7280;}"
)
_BTN_SMALL = (
    "QPushButton{background:#111827;color:#9ca3af;border:1px solid #374151;"
    "font:8pt 'Menlo';padding:0px 5px;min-width:22px;}"
    "QPushButton:hover{color:#e5e7eb;border-color:#6b7280;}"
)
_BTN_PROFILE = (
    "QPushButton{background:#0a0a14;color:#446688;border:1px solid #222233;"
    "font:7pt 'Menlo';padding:1px 3px;}"
    "QPushButton:checked{background:#003344;color:#00ffff;border:1px solid #00ffff;}"
    "QPushButton:hover{color:#88bbcc;border-color:#446688;}"
)
_INPUT_STYLE = (
    "QLineEdit{background:#0a0a1a;color:#00ffcc;border:2px solid #00bbaa;"
    "font:8pt 'Menlo';padding:2px 4px;}"
    "QLineEdit:focus{border:2px solid #00ffcc;background:#0d1225;}"
)


def _make_toggle(label: str, checked: bool = True) -> QPushButton:
    btn = QPushButton(label)
    btn.setCheckable(True); btn.setChecked(checked)
    btn.setFixedHeight(20); btn.setStyleSheet(_BTN_BASE)
    return btn


def _make_tf_btn(label: str, active: bool = False) -> QPushButton:
    btn = QPushButton(label)
    btn.setCheckable(True); btn.setChecked(active)
    btn.setFixedSize(36, 20); btn.setStyleSheet(_BTN_BASE)
    return btn


def _make_small_btn(label: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setFixedSize(22, 20); btn.setStyleSheet(_BTN_SMALL)
    return btn


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setStyleSheet("color:#374151;")
    return f


class ATASFootprintPanel(QWidget):

    _QUICK_TF = [('1m', 60), ('5m', 300), ('15m', 900), ('1h', 3600)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("atasPanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Row 1: Title + Quick TF buttons + Custom TF input ─
        row1 = QHBoxLayout()
        row1.setContentsMargins(6, 3, 6, 2); row1.setSpacing(4)

        title = QLabel("FOOTPRINT")
        title.setStyleSheet("color:#00ffcc;font:bold 9pt 'Menlo';letter-spacing:2px;")
        row1.addWidget(title); row1.addWidget(_sep())

        # Quick TF buttons
        self._tf_group = QButtonGroup(self)
        self._tf_group.setExclusive(True)
        for tf_label, tf_secs in self._QUICK_TF:
            btn = _make_tf_btn(tf_label, active=(tf_label == '5m'))
            row1.addWidget(btn)
            self._tf_group.addButton(btn)
            btn.clicked.connect(lambda _, tl=tf_label, ts=tf_secs: self._on_quick_tf(tl, ts))

        row1.addWidget(_sep())

        # Custom TF input
        tf_lbl = QLabel("TF:")
        tf_lbl.setStyleSheet("color:#00ccaa;font:bold 8pt 'Menlo';")
        row1.addWidget(tf_lbl)
        self._tf_input = QLineEdit("5m")
        self._tf_input.setFixedSize(48, 22)
        self._tf_input.setStyleSheet(_INPUT_STYLE)
        self._tf_input.setToolTip("e.g. 2m, 10m, 4h, 1d — then click Apply")
        self._tf_input.returnPressed.connect(self._on_load)
        row1.addWidget(self._tf_input)

        days_lbl = QLabel("Days:")
        days_lbl.setStyleSheet("color:#00ccaa;font:bold 8pt 'Menlo';")
        row1.addWidget(days_lbl)
        self._days_input = QLineEdit("5")
        self._days_input.setFixedSize(36, 22)
        self._days_input.setStyleSheet(_INPUT_STYLE)
        self._days_input.setToolTip("Number of days of data to load")
        self._days_input.returnPressed.connect(self._on_load)
        row1.addWidget(self._days_input)

        load_btn = QPushButton("Apply")
        load_btn.setFixedSize(48, 22)
        load_btn.setStyleSheet(
            "QPushButton{background:#063a2e;color:#00ffcc;border:2px solid #00ffcc;"
            "font:bold 8pt 'Menlo';padding:0px 4px;}"
            "QPushButton:hover{background:#00ffcc;color:#000000;}"
            "QPushButton:pressed{background:#00cc99;color:#000000;}"
        )
        load_btn.clicked.connect(self._on_load)
        row1.addWidget(load_btn)

        row1.addWidget(_sep())

        # Status label
        self._status_lbl = QLabel("5m | 5 days | loading…")
        self._status_lbl.setStyleSheet("color:#446677;font:8pt 'Menlo';")
        row1.addWidget(self._status_lbl)

        row1.addStretch()

        # Navigation buttons
        _NAV_STYLE = (
            "QPushButton{background:#0d1f17;color:#00cc88;border:1px solid #00cc88;"
            "font:bold 8pt 'Menlo';padding:1px 6px;}"
            "QPushButton:hover{background:#00cc88;color:#000;}"
            "QPushButton:pressed{background:#00aa66;color:#000;}"
        )
        btn_fit = QPushButton("⌂ Fit")
        btn_fit.setFixedHeight(20); btn_fit.setStyleSheet(_NAV_STYLE)
        btn_fit.setToolTip("Fit all candles (F)")
        row1.addWidget(btn_fit)

        btn_latest = QPushButton("↦ Latest")
        btn_latest.setFixedHeight(20); btn_latest.setStyleSheet(_NAV_STYLE)
        btn_latest.setToolTip("Jump to most recent candle (L)")
        row1.addWidget(btn_latest)

        row1.addWidget(_sep())
        self._mode_lbl = QLabel("\u25b6 CANDLESTICK")
        self._mode_lbl.setStyleSheet("color:#444466;font:7pt 'Menlo';")
        row1.addWidget(self._mode_lbl)
        root.addLayout(row1)

        # Store for connections (set up after canvas is created)
        self._btn_fit    = btn_fit
        self._btn_latest = btn_latest

        # ── Row 2: Toggles + Profile selector + Zoom controls ─
        row2 = QHBoxLayout()
        row2.setContentsMargins(6, 0, 6, 3); row2.setSpacing(4)

        self._btn_vol   = _make_toggle("Vol Profile", True)
        self._btn_delta = _make_toggle("Delta Bars",  True)
        self._btn_fp    = _make_toggle("Footprint",   True)
        self._btn_ma    = _make_toggle("MA(20)",      True)
        self._btn_div   = _make_toggle("\u0394 Div",  True)
        self._btn_stk   = _make_toggle("Stk Imb",    True)
        self._btn_sess  = _make_toggle("Session",     True)

        for btn in [self._btn_vol, self._btn_delta, self._btn_fp, self._btn_ma,
                    self._btn_div, self._btn_stk, self._btn_sess]:
            row2.addWidget(btn)

        row2.addWidget(_sep())

        # Profile mode selector
        prof_lbl = QLabel("Profile:")
        prof_lbl.setStyleSheet("color:#6b7280;font:7pt 'Menlo';")
        row2.addWidget(prof_lbl)

        self._prof_group = QButtonGroup(self)
        self._prof_group.setExclusive(True)
        for idx, mode in enumerate(ATASCanvas.PROFILE_MODES):
            btn = QPushButton(mode)
            btn.setCheckable(True)
            btn.setChecked(idx == 4)  # ALL default
            btn.setFixedHeight(20)
            btn.setStyleSheet(_BTN_PROFILE)
            row2.addWidget(btn)
            self._prof_group.addButton(btn)
            btn.clicked.connect(lambda _, i=idx: self._on_profile_mode(i))

        row2.addWidget(_sep())

        # Auto-zoom toggle
        _AZ_ON  = ("QPushButton{background:#064e3b;color:#34d399;border:1px solid #34d399;"
                   "font:bold 7pt 'Menlo';padding:1px 4px;}"
                   "QPushButton:hover{background:#10b981;color:#000;}")
        _AZ_OFF = ("QPushButton{background:#111827;color:#6b7280;border:1px solid #374151;"
                   "font:bold 7pt 'Menlo';padding:1px 4px;}"
                   "QPushButton:hover{color:#e5e7eb;border-color:#6b7280;}")
        self._btn_autozoom = QPushButton("⊙ AutoZoom")
        self._btn_autozoom.setCheckable(True)
        self._btn_autozoom.setChecked(True)  # on by default
        self._btn_autozoom.setFixedHeight(20)
        self._btn_autozoom.setStyleSheet(_AZ_ON)
        self._btn_autozoom.setToolTip("Auto-fit vertical scale to visible candles (Shift+scroll to disable)")
        self._az_on_style  = _AZ_ON
        self._az_off_style = _AZ_OFF
        row2.addWidget(self._btn_autozoom)

        row2.addWidget(_sep())

        rh_lbl = QLabel("V-Zoom:"); rh_lbl.setStyleSheet("color:#6b7280;font:7pt 'Menlo';")
        row2.addWidget(rh_lbl)
        btn_rh_dn = _make_small_btn("\u2212"); btn_rh_up = _make_small_btn("+")
        row2.addWidget(btn_rh_dn); row2.addWidget(btn_rh_up)

        row2.addWidget(_sep())
        cw_lbl = QLabel("H-Zoom:"); cw_lbl.setStyleSheet("color:#6b7280;font:7pt 'Menlo';")
        row2.addWidget(cw_lbl)
        btn_cw_dn = _make_small_btn("\u2212"); btn_cw_up = _make_small_btn("+")
        row2.addWidget(btn_cw_dn); row2.addWidget(btn_cw_up)

        row2.addWidget(_sep())
        cs_lbl = QLabel("Theme:"); cs_lbl.setStyleSheet("color:#6b7280;font:7pt 'Menlo';")
        row2.addWidget(cs_lbl)
        self._btn_theme = QPushButton("Dark")
        self._btn_theme.setFixedHeight(20); self._btn_theme.setStyleSheet(_BTN_SMALL)
        row2.addWidget(self._btn_theme)
        row2.addStretch()

        hint = QLabel("scroll=time-pan  ctrl+scroll=h-zoom  shift+scroll=v-zoom  drag=pan  F=fit  L=latest  dbl-click=fit")
        hint.setStyleSheet("color:#1f2937;font:6pt 'Menlo';")
        row2.addWidget(hint)
        root.addLayout(row2)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#1f2937;"); line.setFixedHeight(1)
        root.addWidget(line)

        self._canvas = ATASCanvas(self)
        root.addWidget(self._canvas, 1)

        # Connections
        self._btn_vol.toggled.connect(lambda v: self._set(show_vol_profile=v))
        self._btn_delta.toggled.connect(lambda v: self._set(show_delta_bars=v))
        self._btn_fp.toggled.connect(lambda v: self._set(show_footprint=v))
        self._btn_ma.toggled.connect(lambda v: self._set(show_ma=v))
        self._btn_div.toggled.connect(lambda v: self._set(show_divergence=v))
        self._btn_stk.toggled.connect(lambda v: self._set(show_stacked_imbalance=v))
        self._btn_sess.toggled.connect(lambda v: self._set(show_session_lines=v))

        btn_rh_dn.clicked.connect(lambda: self._adj_row_h(0.80))
        btn_rh_up.clicked.connect(lambda: self._adj_row_h(1.25))
        btn_cw_dn.clicked.connect(lambda: self._adj_candle_w(0.80))
        btn_cw_up.clicked.connect(lambda: self._adj_candle_w(1.25))
        self._btn_theme.clicked.connect(self._on_theme)
        self._btn_fit.clicked.connect(lambda: self._canvas._fit_all())
        self._btn_latest.clicked.connect(lambda: self._canvas._go_to_latest())
        self._btn_autozoom.toggled.connect(self._on_autozoom)

        self._themes    = ["Dark", "Midnight", "Matrix"]
        self._theme_idx = 0

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        self._update_status()

    def _update_status(self):
        c = self._canvas
        n = len(c._candles)
        secs = c._tf_secs
        if secs < 60:
            tf_str = f"{secs}s"
        elif secs < 3600:
            tf_str = f"{secs//60}m"
        elif secs < 86400:
            tf_str = f"{secs//3600}h"
        else:
            tf_str = f"{secs//86400}d"
        self._status_lbl.setText(f"{tf_str} candles | {c._n_days} days | {n} bars")

    def _set(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self._canvas, k, v)
        self._canvas.update()

    def _on_quick_tf(self, tf_label: str, tf_secs: int):
        try:
            n_days = int(self._days_input.text().strip()) or 5
        except ValueError:
            n_days = 5
        n_days = max(1, min(90, n_days))
        self._tf_input.setText(tf_label)
        self._canvas.set_timeframe(tf_label, tf_secs, n_days)
        self._update_status()

    def _on_load(self):
        tf_str = self._tf_input.text().strip() or '5m'
        tf_secs = parse_tf(tf_str)
        if tf_secs <= 0:
            self._status_lbl.setText(f"bad TF: '{tf_str}' — use e.g. 5m, 1h")
            return
        try:
            n_days = int(self._days_input.text().strip()) or 5
        except ValueError:
            n_days = 5
        n_days = max(1, min(90, n_days))

        # Deselect quick-TF buttons since we're using custom
        for btn in self._tf_group.buttons():
            btn.setChecked(False)

        self._canvas.set_timeframe(tf_str, tf_secs, n_days)
        self._update_status()

    def _on_profile_mode(self, idx: int):
        self._canvas._profile_mode = idx
        self._canvas.update()

    def _on_tf(self, tf: str):
        self._canvas.set_timeframe(tf, parse_tf(tf), self._canvas._n_days)
        self._update_status()

    def _on_autozoom(self, enabled: bool):
        self._canvas._auto_zoom = enabled
        self._btn_autozoom.setStyleSheet(
            self._az_on_style if enabled else self._az_off_style
        )
        if enabled:
            self._canvas._fit_vertical_to_visible()
        self._canvas.update()

    def _adj_row_h(self, factor: float):
        c = self._canvas
        # Manual V-zoom turns off auto-zoom
        c._auto_zoom = False
        self._btn_autozoom.setChecked(False)
        c._row_h = max(c.MIN_ROW_H, min(c.MAX_ROW_H, c._row_h * factor))
        c.update()

    def _adj_candle_w(self, factor: float):
        c = self._canvas
        c._candle_w = max(c.MIN_CANDLE_W, min(c.MAX_CANDLE_W, c._candle_w * factor))
        c.update()

    def _on_theme(self):
        self._theme_idx = (self._theme_idx + 1) % len(self._themes)
        theme = self._themes[self._theme_idx]
        self._btn_theme.setText(theme)
        global BG, BG_PANEL, GREEN, RED
        if theme == "Midnight":
            BG = QColor(0x04, 0x04, 0x14); BG_PANEL = QColor(0x02, 0x02, 0x0e)
        elif theme == "Matrix":
            BG = QColor(0x00, 0x08, 0x00); BG_PANEL = QColor(0x00, 0x04, 0x00)
            GREEN = QColor(0x00, 0xff, 0x41); RED = QColor(0xff, 0x44, 0x00)
        else:
            BG = QColor(0x0a, 0x0a, 0x0a); BG_PANEL = QColor(0x08, 0x08, 0x10)
            GREEN = QColor(0x00, 0xff, 0x41); RED = QColor(0xff, 0x22, 0x44)
        self._canvas.update()

    def _on_tick(self):
        self._canvas.tick_live()
        rh    = self._canvas._row_h
        thresh = self._canvas.FOOTPRINT_SHOW_THRESH
        if rh >= thresh and self._canvas.show_footprint:
            self._mode_lbl.setText("\u25c8 FOOTPRINT")
            self._mode_lbl.setStyleSheet("color:#00ffcc;font:7pt 'Menlo';")
        else:
            self._mode_lbl.setText("\u25b6 CANDLESTICK")
            self._mode_lbl.setStyleSheet("color:#444466;font:7pt 'Menlo';")
        # Sync AutoZoom button if canvas state drifted (e.g. shift+scroll)
        az = self._canvas._auto_zoom
        if self._btn_autozoom.isChecked() != az:
            self._btn_autozoom.blockSignals(True)
            self._btn_autozoom.setChecked(az)
            self._btn_autozoom.setStyleSheet(
                self._az_on_style if az else self._az_off_style
            )
            self._btn_autozoom.blockSignals(False)

    def set_bars(self, bars) -> None:
        if not bars:
            return
        try:
            latest = bars[-1]
            if hasattr(latest, 'close'):
                self._canvas._current_price = float(latest.close)
            elif isinstance(latest, dict):
                self._canvas._current_price = float(latest.get('close', self._canvas._current_price))
        except Exception:
            pass
