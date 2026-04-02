"""Advanced Footprint Chart widget for Phase 2.

Enhanced footprint with multiple modes, auction detection, absorption,
imbalance markers, POC migration, cluster analysis, and floating window support.
"""
from __future__ import annotations
import math
from typing import Optional
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu, QDialog, QSizePolicy, QToolButton)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal as QtSignal
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QLinearGradient, QWheelEvent, QMouseEvent, QCursor
from .theme import COLORS
from ..orderflow import VolumeProfile, FootprintBar, FootprintChart, PriceLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _c(key: str) -> QColor:
    return QColor(COLORS[key])


def _mono(size: int = 8, bold: bool = False) -> QFont:
    f = QFont("JetBrains Mono", size)
    if bold:
        f.setBold(True)
    return f


# ---------------------------------------------------------------------------
# Imbalance Indicator (small dot strip)
# ---------------------------------------------------------------------------

class ImbalanceIndicator(QWidget):
    """Vertical strip of colored dots showing stacked imbalances at each price level.

    Placed outside the footprint bars to flag 3+ consecutive levels
    where ask/bid ratio > 3:1 (buying imbalance) or bid/ask > 3:1 (selling).
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._buy_levels: list[float] = []   # prices with buy imbalance
        self._sell_levels: list[float] = []  # prices with sell imbalance
        self._price_range: tuple[float, float] = (0.0, 1.0)
        self.setFixedWidth(16)

    def set_imbalances(self, buy_levels: list[float], sell_levels: list[float],
                       price_lo: float, price_hi: float):
        self._buy_levels = list(buy_levels)
        self._sell_levels = list(sell_levels)
        self._price_range = (price_lo, price_hi)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _c("bg_panel"))

        lo, hi = self._price_range
        rng = hi - lo
        if rng <= 0:
            painter.end()
            return

        dot_r = 3

        def py(price: float) -> float:
            return 4 + (h - 8) - ((price - lo) / rng) * (h - 8)

        # Buy imbalance dots (cyan)
        painter.setPen(Qt.PenStyle.NoPen)
        for p in self._buy_levels:
            y = py(p)
            painter.setBrush(_c("cyan"))
            painter.drawEllipse(QPointF(w * 0.35, y), dot_r, dot_r)
            # Glow
            glow = QColor(0, 229, 255, 50)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(w * 0.35, y), dot_r + 2, dot_r + 2)

        # Sell imbalance dots (magenta)
        for p in self._sell_levels:
            y = py(p)
            painter.setBrush(_c("magenta"))
            painter.drawEllipse(QPointF(w * 0.65, y), dot_r, dot_r)
            glow = QColor(224, 64, 251, 50)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(w * 0.65, y), dot_r + 2, dot_r + 2)

        painter.end()


# ---------------------------------------------------------------------------
# Advanced Footprint Widget
# ---------------------------------------------------------------------------

class AdvancedFootprintWidget(QWidget):
    """Enhanced footprint chart with multiple modes and order flow analysis.

    Modes:
        BID_ASK  - Show bid x ask volume at each cell
        DELTA    - Show delta (ask - bid) per cell
        VOLUME   - Show total volume per cell

    Detections:
        - Unfinished auctions (no opposing volume at high/low)
        - Exhaustion (large volume, no follow-through)
        - Absorption (large volume absorbed at a level)
        - Initiative vs responsive activity
        - POC migration tracking
        - Single prints (volume on only one side)
        - Excess (aggressive activity at extremes)
        - Stacked imbalance (3+ consecutive ratios > 3:1)
        - Diagonal imbalance (ask@N vs bid@N+1)
        - Cluster analysis auto-signals
    """

    MODE_BID_ASK = "BID_ASK"
    MODE_DELTA = "DELTA"
    MODE_VOLUME = "VOLUME"

    # Signals
    mode_changed = QtSignal(str)
    bar_clicked = QtSignal(int)  # bar index

    # Imbalance threshold
    IMBALANCE_RATIO = 3.0
    STACKED_MIN = 3

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._bars: list[FootprintBar] = []
        self._mode = self.MODE_BID_ASK
        self._visible_bars = 20
        self._scroll_offset = 0      # pan offset (in bars)
        self._zoom_level = 1.0
        self._drag_start: QPointF | None = None
        self._drag_offset_start = 0
        self._hover_bar: int = -1
        self._hover_price: float = 0.0
        self._show_signals = True

        self.setMinimumSize(500, 350)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Imbalance indicator (outside the chart)
        self._imbalance_ind = ImbalanceIndicator(self)
        self._imbalance_ind.move(0, 0)

    # -- public API --

    def set_bars(self, bars: list[FootprintBar]):
        self._bars = bars
        self._recompute_signals()
        self.update()

    def set_mode(self, mode: str):
        if mode in (self.MODE_BID_ASK, self.MODE_DELTA, self.MODE_VOLUME):
            self._mode = mode
            self.mode_changed.emit(mode)
            self.update()

    @property
    def mode(self) -> str:
        return self._mode

    # -- signal detection --

    def _recompute_signals(self):
        """Run all detections on current bars."""
        self._unfinished: dict[int, str] = {}      # bar_idx -> "high"/"low"
        self._exhaustion: dict[int, list[float]] = {}   # bar_idx -> [prices]
        self._absorption: dict[int, list[float]] = {}
        self._single_prints: dict[int, list[float]] = {}
        self._poc_arrows: list[tuple[float, float]] = []  # (prev_poc, cur_poc) pairs
        self._stacked_buy: dict[int, list[float]] = {}
        self._stacked_sell: dict[int, list[float]] = {}
        self._diagonal_imb: dict[int, list[float]] = {}
        self._excess: dict[int, str] = {}

        for bi, bar in enumerate(self._bars):
            levels = bar.profile.sorted_levels()
            if not levels:
                continue

            # --- Unfinished auction ---
            top = levels[-1]
            bot = levels[0]
            if top.ask_volume > 0 and top.bid_volume == 0:
                self._unfinished[bi] = "high"
            if bot.bid_volume > 0 and bot.ask_volume == 0:
                self._unfinished[bi] = "low"

            # --- Single prints ---
            singles = []
            for lv in levels:
                if (lv.bid_volume > 0 and lv.ask_volume == 0) or \
                   (lv.ask_volume > 0 and lv.bid_volume == 0):
                    singles.append(lv.price)
            if singles:
                self._single_prints[bi] = singles

            # --- Exhaustion: large volume at extreme, no follow-through ---
            if len(levels) >= 3:
                avg_vol = sum(lv.total_volume for lv in levels) / len(levels)
                # Top exhaustion
                if top.total_volume > avg_vol * 2.5 and bi + 1 < len(self._bars):
                    next_levels = self._bars[bi + 1].profile.sorted_levels()
                    if next_levels and next_levels[-1].price <= top.price:
                        self._exhaustion.setdefault(bi, []).append(top.price)
                # Bottom exhaustion
                if bot.total_volume > avg_vol * 2.5 and bi + 1 < len(self._bars):
                    next_levels = self._bars[bi + 1].profile.sorted_levels()
                    if next_levels and next_levels[0].price >= bot.price:
                        self._exhaustion.setdefault(bi, []).append(bot.price)

            # --- Absorption ---
            if len(levels) >= 2:
                avg_vol = sum(lv.total_volume for lv in levels) / len(levels)
                for lv in levels:
                    if lv.total_volume > avg_vol * 3 and abs(lv.delta_pct) < 0.2:
                        self._absorption.setdefault(bi, []).append(lv.price)

            # --- Excess at extremes ---
            if len(levels) >= 3:
                if top.total_volume > 0 and abs(top.delta_pct) > 0.8:
                    self._excess[bi] = "high"
                if bot.total_volume > 0 and abs(bot.delta_pct) > 0.8:
                    self._excess[bi] = "low"

            # --- Stacked imbalances ---
            buy_stack = []
            sell_stack = []
            for lv in levels:
                if lv.bid_volume > 0 and lv.ask_volume / max(lv.bid_volume, 1) >= self.IMBALANCE_RATIO:
                    buy_stack.append(lv.price)
                else:
                    if len(buy_stack) >= self.STACKED_MIN:
                        self._stacked_buy.setdefault(bi, []).extend(buy_stack)
                    buy_stack = []

                if lv.ask_volume > 0 and lv.bid_volume / max(lv.ask_volume, 1) >= self.IMBALANCE_RATIO:
                    sell_stack.append(lv.price)
                else:
                    if len(sell_stack) >= self.STACKED_MIN:
                        self._stacked_sell.setdefault(bi, []).extend(sell_stack)
                    sell_stack = []

            if len(buy_stack) >= self.STACKED_MIN:
                self._stacked_buy.setdefault(bi, []).extend(buy_stack)
            if len(sell_stack) >= self.STACKED_MIN:
                self._stacked_sell.setdefault(bi, []).extend(sell_stack)

            # --- Diagonal imbalance: ask@N vs bid@N+1 ---
            diag = []
            for li in range(len(levels) - 1):
                ask_n = levels[li].ask_volume
                bid_n1 = levels[li + 1].bid_volume
                if bid_n1 > 0 and ask_n / max(bid_n1, 1) >= self.IMBALANCE_RATIO:
                    diag.append(levels[li].price)
                if ask_n > 0 and bid_n1 / max(ask_n, 1) >= self.IMBALANCE_RATIO:
                    diag.append(levels[li + 1].price)
            if diag:
                self._diagonal_imb[bi] = diag

        # --- POC migration ---
        self._poc_arrows = []
        for bi in range(1, len(self._bars)):
            prev_poc = self._bars[bi - 1].profile.poc
            cur_poc = self._bars[bi].profile.poc
            if prev_poc is not None and cur_poc is not None and prev_poc != cur_poc:
                self._poc_arrows.append((prev_poc, cur_poc))
            else:
                self._poc_arrows.append((0, 0))

    # -- coordinate helpers --

    def _visible_range(self) -> tuple[int, int]:
        n = len(self._bars)
        vis = max(2, int(self._visible_bars / self._zoom_level))
        end = max(vis, n - self._scroll_offset)
        start = max(0, end - vis)
        return start, end

    def _chart_rect(self) -> QRectF:
        ml, mr, mt, mb = 60, 20, 10, 24
        return QRectF(ml, mt, self.width() - ml - mr, self.height() - mt - mb)

    def _price_range(self, bars: list[FootprintBar]) -> tuple[float, float]:
        all_prices: list[float] = []
        for bar in bars:
            for lv in bar.profile.levels.values():
                all_prices.append(lv.price)
        if not all_prices:
            return 0.0, 1.0
        return min(all_prices), max(all_prices)

    # -- painting --

    def paintEvent(self, event):
        if not self._bars:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _c("bg_panel"))

        start, end = self._visible_range()
        bars = self._bars[start:end]
        if not bars:
            painter.end()
            return

        rect = self._chart_rect()
        ml = int(rect.left())
        mt = int(rect.top())
        cw = rect.width()
        ch = rect.height()
        mb = 24

        lo, hi = self._price_range(bars)
        price_rng = hi - lo
        if price_rng == 0:
            price_rng = 1.0

        n = len(bars)
        bar_w = cw / max(n, 1)
        tick = 0.25
        cell_h = max((ch / (price_rng / tick)), 6)

        # Max volume for colour intensity
        max_vol = 1
        for bar in bars:
            for lv in bar.profile.levels.values():
                max_vol = max(max_vol, lv.total_volume)

        font_tiny = _mono(6)
        font_small = _mono(7)

        # --- Price axis ---
        painter.setFont(font_small)
        painter.setPen(_c("text_muted"))
        price = lo
        while price <= hi:
            y = mt + ch - ((price - lo) / price_rng) * ch
            painter.drawText(QRectF(0, y - 6, ml - 4, 12),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{price:.2f}")
            painter.setPen(QPen(_c("grid"), 1))
            painter.drawLine(ml, int(y), int(ml + cw), int(y))
            painter.setPen(_c("text_muted"))
            price += tick * 4

        # --- Footprint bars ---
        painter.setFont(font_tiny)
        for bi_local, bar in enumerate(bars):
            bi_global = start + bi_local
            bx = ml + bi_local * bar_w

            # OHLC body tint
            if bar.open is not None and bar.close is not None:
                o_y = mt + ch - ((bar.open - lo) / price_rng) * ch
                c_y = mt + ch - ((bar.close - lo) / price_rng) * ch
                body_color = QColor(COLORS["delta_positive"] + "20") if bar.is_bullish else QColor(COLORS["delta_negative"] + "20")
                painter.fillRect(int(bx + 1), int(min(o_y, c_y)),
                                 int(bar_w - 2), max(int(abs(c_y - o_y)), 1), body_color)

            # --- Each price level ---
            for lv in bar.profile.levels.values():
                py = mt + ch - ((lv.price - lo) / price_rng) * ch
                ch_cell = max(cell_h - 1, 4)

                intensity = min(lv.total_volume / max(max_vol * 0.3, 1), 1.0)

                # Cell background
                if lv.delta > 0:
                    bg = QColor(COLORS["delta_positive"])
                    bg.setAlphaF(0.15 + 0.45 * intensity)
                elif lv.delta < 0:
                    bg = QColor(COLORS["delta_negative"])
                    bg.setAlphaF(0.15 + 0.45 * intensity)
                else:
                    bg = QColor(COLORS["delta_neutral"])
                    bg.setAlphaF(0.1)

                painter.fillRect(int(bx + 1), int(py - ch_cell / 2),
                                 int(bar_w - 2), int(ch_cell), bg)

                # Cell text
                if bar_w > 28 and ch_cell > 7:
                    painter.setPen(_c("text_white"))
                    if self._mode == self.MODE_BID_ASK:
                        txt = f"{lv.bid_volume}x{lv.ask_volume}"
                    elif self._mode == self.MODE_DELTA:
                        txt = f"{lv.delta:+d}"
                    else:
                        txt = str(lv.total_volume)
                    painter.drawText(
                        QRectF(bx + 1, py - ch_cell / 2, bar_w - 2, ch_cell),
                        Qt.AlignmentFlag.AlignCenter, txt)

                # --- Signal markers on cells ---
                if self._show_signals:
                    # Absorption: yellow dot
                    if bi_global in self._absorption and lv.price in self._absorption[bi_global]:
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(QColor(COLORS["amber"]))
                        painter.drawEllipse(QPointF(bx + bar_w - 5, py), 2, 2)
                        painter.setBrush(Qt.BrushStyle.NoBrush)

                    # Single print: small dash
                    if bi_global in self._single_prints and lv.price in self._single_prints[bi_global]:
                        painter.setPen(QPen(_c("magenta"), 1))
                        painter.drawLine(int(bx + 2), int(py), int(bx + 6), int(py))

                    # Diagonal imbalance: small triangle
                    if bi_global in self._diagonal_imb and lv.price in self._diagonal_imb[bi_global]:
                        painter.setPen(QPen(_c("blue"), 1))
                        painter.drawLine(int(bx + bar_w - 8), int(py - 2),
                                         int(bx + bar_w - 4), int(py + 2))

            # --- Bar-level signals ---
            if self._show_signals:
                # Unfinished auction marker
                if bi_global in self._unfinished:
                    side = self._unfinished[bi_global]
                    if side == "high":
                        yy = mt + 2
                    else:
                        yy = mt + ch - 10
                    painter.setPen(_c("cyan"))
                    painter.setFont(_mono(6, bold=True))
                    painter.drawText(int(bx + 2), int(yy + 8), "UF")

                # Exhaustion marker
                if bi_global in self._exhaustion:
                    for ep in self._exhaustion[bi_global]:
                        ey = mt + ch - ((ep - lo) / price_rng) * ch
                        painter.setPen(QPen(_c("amber"), 2))
                        painter.drawLine(int(bx + bar_w * 0.2), int(ey),
                                         int(bx + bar_w * 0.8), int(ey))

                # Excess marker
                if bi_global in self._excess:
                    painter.setPen(_c("red"))
                    painter.setFont(_mono(5, bold=True))
                    if self._excess[bi_global] == "high":
                        painter.drawText(int(bx + 2), int(mt + 16), "EX")
                    else:
                        painter.drawText(int(bx + 2), int(mt + ch - 4), "EX")

            # --- POC marker ---
            poc = bar.profile.poc
            if poc is not None:
                poc_y = mt + ch - ((poc - lo) / price_rng) * ch
                painter.setPen(QPen(_c("cyan"), 2))
                painter.drawLine(int(bx), int(poc_y), int(bx + bar_w), int(poc_y))

            # --- POC migration arrow ---
            if bi_local > 0 and bi_local - 1 < len(self._poc_arrows):
                arr = self._poc_arrows[start + bi_local - 1] if (start + bi_local - 1) < len(self._poc_arrows) else (0, 0)
                prev_poc, cur_poc = arr
                if prev_poc != 0 and cur_poc != 0 and prev_poc != cur_poc:
                    py1 = mt + ch - ((prev_poc - lo) / price_rng) * ch
                    py2 = mt + ch - ((cur_poc - lo) / price_rng) * ch
                    painter.setPen(QPen(_c("cyan"), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(int(bx - bar_w * 0.2), int(py1),
                                     int(bx + bar_w * 0.2), int(py2))
                    # Arrowhead
                    direction = -1 if py2 < py1 else 1
                    painter.drawLine(int(bx + bar_w * 0.2), int(py2),
                                     int(bx + bar_w * 0.1), int(py2 + 4 * direction))
                    painter.drawLine(int(bx + bar_w * 0.2), int(py2),
                                     int(bx + bar_w * 0.3), int(py2 + 4 * direction))

            # --- Stacked imbalance dots (outside bar) ---
            if bi_global in self._stacked_buy:
                for sp in self._stacked_buy[bi_global]:
                    sy = mt + ch - ((sp - lo) / price_rng) * ch
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(_c("cyan"))
                    painter.drawEllipse(QPointF(bx - 3, sy), 2, 2)
                    painter.setBrush(Qt.BrushStyle.NoBrush)

            if bi_global in self._stacked_sell:
                for sp in self._stacked_sell[bi_global]:
                    sy = mt + ch - ((sp - lo) / price_rng) * ch
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(_c("magenta"))
                    painter.drawEllipse(QPointF(bx + bar_w + 3, sy), 2, 2)
                    painter.setBrush(Qt.BrushStyle.NoBrush)

            # --- Delta total at bottom ---
            painter.setPen(_c("delta_positive") if bar.delta >= 0 else _c("delta_negative"))
            painter.setFont(font_small)
            painter.drawText(
                QRectF(bx, mt + ch + 2, bar_w, mb - 4),
                Qt.AlignmentFlag.AlignCenter, f"{bar.delta:+d}")

        # --- Cluster analysis: highlight high-activity regions ---
        self._draw_cluster_highlights(painter, bars, start, ml, mt, cw, ch, lo, price_rng, bar_w, max_vol)

        # --- Hover tooltip ---
        if self._hover_bar >= 0:
            self._draw_tooltip(painter, bars, start, ml, mt, cw, ch, lo, price_rng, bar_w)

        # Mode label
        painter.setPen(_c("cyan"))
        painter.setFont(_mono(8, bold=True))
        painter.drawText(QRectF(ml, 0, cw, 14), Qt.AlignmentFlag.AlignCenter,
                         f"FOOTPRINT [{self._mode.replace('_', ' ')}]")

        # Scanlines
        painter.setOpacity(0.04)
        pen = QPen(QColor(0, 0, 0), 1)
        painter.setPen(pen)
        y = 0
        while y < h:
            painter.drawLine(0, y, w, y)
            y += 3
        painter.setOpacity(1.0)
        painter.end()

        # Update imbalance indicator
        self._update_imbalance_indicator(bars, start, lo, hi)

    def _draw_cluster_highlights(self, painter: QPainter, bars: list[FootprintBar],
                                  start: int, ml: int, mt: int, cw: float, ch: float,
                                  lo: float, rng: float, bar_w: float, max_vol: int):
        """Draw semi-transparent rectangles over high-activity clusters."""
        threshold = max_vol * 0.7
        for bi_local, bar in enumerate(bars):
            bx = ml + bi_local * bar_w
            for lv in bar.profile.levels.values():
                if lv.total_volume >= threshold:
                    py = mt + ch - ((lv.price - lo) / rng) * ch
                    c = QColor(COLORS["green_glow"])
                    c.setAlphaF(0.15)
                    painter.fillRect(int(bx), int(py - 6), int(bar_w), 12, c)

    def _draw_tooltip(self, painter: QPainter, bars: list[FootprintBar],
                       start: int, ml: int, mt: int, cw: float, ch: float,
                       lo: float, rng: float, bar_w: float):
        """Draw hover tooltip showing bid/ask/delta/total at hovered cell."""
        bi_local = self._hover_bar - start
        if bi_local < 0 or bi_local >= len(bars):
            return
        bar = bars[bi_local]
        # Find closest price level
        best: PriceLevel | None = None
        best_dist = float("inf")
        for lv in bar.profile.levels.values():
            d = abs(lv.price - self._hover_price)
            if d < best_dist:
                best_dist = d
                best = lv
        if best is None:
            return

        tip_w, tip_h = 140, 60
        # Position near cursor
        bx = ml + bi_local * bar_w + bar_w + 4
        py = mt + ch - ((best.price - lo) / rng) * ch - tip_h / 2
        # Clamp
        bx = min(bx, self.width() - tip_w - 4)
        py = max(mt, min(py, mt + ch - tip_h))

        painter.fillRect(int(bx), int(py), tip_w, tip_h, _c("bg_card"))
        painter.setPen(QPen(_c("green_dim"), 1))
        painter.drawRect(int(bx), int(py), tip_w, tip_h)

        painter.setPen(_c("text_white"))
        painter.setFont(_mono(7))
        lines = [
            f"Price: {best.price:.2f}",
            f"Bid: {best.bid_volume}  Ask: {best.ask_volume}",
            f"Delta: {best.delta:+d}  Vol: {best.total_volume}",
        ]
        for i, line in enumerate(lines):
            painter.drawText(int(bx + 6), int(py + 14 + i * 16), line)

    def _update_imbalance_indicator(self, bars: list[FootprintBar], start: int,
                                     lo: float, hi: float):
        """Feed data to the ImbalanceIndicator widget."""
        buy_prices: list[float] = []
        sell_prices: list[float] = []
        for bi_local in range(len(bars)):
            bi_global = start + bi_local
            if bi_global in self._stacked_buy:
                buy_prices.extend(self._stacked_buy[bi_global])
            if bi_global in self._stacked_sell:
                sell_prices.extend(self._stacked_sell[bi_global])
        rect = self._chart_rect()
        self._imbalance_ind.setFixedHeight(int(rect.height()))
        self._imbalance_ind.move(int(rect.right()) + 2, int(rect.top()))
        self._imbalance_ind.set_imbalances(buy_prices, sell_prices, lo, hi)

    # -- interaction --

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom
            factor = 1.15 if delta > 0 else 0.87
            self._zoom_level = max(0.3, min(5.0, self._zoom_level * factor))
        else:
            # Scroll
            step = 1 if delta < 0 else -1
            max_offset = max(0, len(self._bars) - int(self._visible_bars / self._zoom_level))
            self._scroll_offset = max(0, min(max_offset, self._scroll_offset + step))
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position()
            self._drag_offset_start = self._scroll_offset
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        rect = self._chart_rect()

        if self._drag_start is not None:
            dx = pos.x() - self._drag_start.x()
            bar_w = rect.width() / max(int(self._visible_bars / self._zoom_level), 1)
            bar_shift = int(-dx / max(bar_w, 1))
            max_offset = max(0, len(self._bars) - int(self._visible_bars / self._zoom_level))
            self._scroll_offset = max(0, min(max_offset, self._drag_offset_start + bar_shift))
            self.update()
        else:
            # Hover detection
            if rect.contains(pos):
                start, end = self._visible_range()
                bars = self._bars[start:end]
                n = len(bars)
                bar_w = rect.width() / max(n, 1)
                bi_local = int((pos.x() - rect.left()) / bar_w)
                if 0 <= bi_local < n:
                    self._hover_bar = start + bi_local
                    lo, hi = self._price_range(bars)
                    rng = hi - lo if hi != lo else 1.0
                    self._hover_price = hi - ((pos.y() - rect.top()) / rect.height()) * rng
                else:
                    self._hover_bar = -1
            else:
                self._hover_bar = -1
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_start is not None:
                dx = abs(event.position().x() - self._drag_start.x())
                if dx < 5 and self._hover_bar >= 0:
                    self.bar_clicked.emit(self._hover_bar)
            self._drag_start = None

    def leaveEvent(self, event):
        self._hover_bar = -1
        self.update()

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {COLORS['bg_card']}; color: {COLORS['text_primary']}; "
            f"border: 1px solid {COLORS['border']}; }}"
            f"QMenu::item:selected {{ background: {COLORS['bg_hover']}; }}"
        )

        act_ba = menu.addAction("Bid x Ask Mode")
        act_delta = menu.addAction("Delta Mode")
        act_vol = menu.addAction("Volume Mode")
        menu.addSeparator()
        act_signals = menu.addAction("Toggle Signals")
        act_signals.setCheckable(True)
        act_signals.setChecked(self._show_signals)
        menu.addSeparator()
        act_float = menu.addAction("Pop Out (Floating)")

        action = menu.exec(self.mapToGlobal(pos))
        if action == act_ba:
            self.set_mode(self.MODE_BID_ASK)
        elif action == act_delta:
            self.set_mode(self.MODE_DELTA)
        elif action == act_vol:
            self.set_mode(self.MODE_VOLUME)
        elif action == act_signals:
            self._show_signals = not self._show_signals
            self.update()
        elif action == act_float:
            self._pop_out_floating()

    def _pop_out_floating(self):
        """Create a floating window copy of this footprint chart."""
        dlg = FloatingFootprintWindow(self._bars, self._mode, parent=None)
        dlg.show()


# ---------------------------------------------------------------------------
# Floating Footprint Window
# ---------------------------------------------------------------------------

class FloatingFootprintWindow(QDialog):
    """Pop-out floating window wrapper for the advanced footprint chart.

    Draggable, resizable, with optional stay-on-top toggle.
    """

    def __init__(self, bars: list[FootprintBar] | None = None,
                 mode: str = AdvancedFootprintWidget.MODE_BID_ASK,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("FOOTPRINT CHART")
        self.setMinimumSize(600, 400)
        self.resize(800, 500)
        self.setStyleSheet(f"background: {COLORS['bg_dark']}; color: {COLORS['text_primary']};")

        self._stay_on_top = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # Toolbar
        toolbar = QHBoxLayout()
        title = QLabel("FOOTPRINT")
        title.setStyleSheet(f"color: {COLORS['cyan']}; font-size: 12px; font-weight: bold;")
        toolbar.addWidget(title)
        toolbar.addStretch()

        btn_pin = QToolButton()
        btn_pin.setText("PIN")
        btn_pin.setCheckable(True)
        btn_pin.setStyleSheet(
            f"color: {COLORS['text_muted']}; border: 1px solid {COLORS['border']}; padding: 2px 6px;")
        btn_pin.toggled.connect(self._toggle_on_top)
        toolbar.addWidget(btn_pin)

        btn_ba = QToolButton()
        btn_ba.setText("B/A")
        btn_ba.setStyleSheet(
            f"color: {COLORS['green_bright']}; border: 1px solid {COLORS['border']}; padding: 2px 6px;")
        btn_ba.clicked.connect(lambda: self._chart.set_mode(AdvancedFootprintWidget.MODE_BID_ASK))
        toolbar.addWidget(btn_ba)

        btn_d = QToolButton()
        btn_d.setText("DLT")
        btn_d.setStyleSheet(
            f"color: {COLORS['cyan']}; border: 1px solid {COLORS['border']}; padding: 2px 6px;")
        btn_d.clicked.connect(lambda: self._chart.set_mode(AdvancedFootprintWidget.MODE_DELTA))
        toolbar.addWidget(btn_d)

        btn_v = QToolButton()
        btn_v.setText("VOL")
        btn_v.setStyleSheet(
            f"color: {COLORS['amber']}; border: 1px solid {COLORS['border']}; padding: 2px 6px;")
        btn_v.clicked.connect(lambda: self._chart.set_mode(AdvancedFootprintWidget.MODE_VOLUME))
        toolbar.addWidget(btn_v)

        layout.addLayout(toolbar)

        # Chart
        self._chart = AdvancedFootprintWidget()
        self._chart.set_mode(mode)
        if bars:
            self._chart.set_bars(bars)
        layout.addWidget(self._chart)

    @property
    def chart(self) -> AdvancedFootprintWidget:
        return self._chart

    def _toggle_on_top(self, checked: bool):
        self._stay_on_top = checked
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()  # re-show after flag change

    def set_bars(self, bars: list[FootprintBar]):
        self._chart.set_bars(bars)
