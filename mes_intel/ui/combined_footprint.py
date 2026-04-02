"""Combined Footprint + Volume Profile + Delta Profile — Unified View.

Three synchronized panels sharing the same price axis:
  Left (25%):   Volume profile histogram — horizontal bid/ask bars, POC/VAH/VAL markers
  Center (50%): ATAS-style BID × ASK footprint grid per time bar
  Right (25%):  Delta profile — net delta horizontal bars

Animated breathing glow borders, cyberpunk neon aesthetic.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSplitter, QPushButton, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QRect
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath,
)

from .theme import COLORS
from ..orderflow import VolumeProfile, FootprintBar, FootprintChart, PriceLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _neon_pen(color: str, width: float = 1.5, alpha: float = 1.0) -> QPen:
    c = QColor(color)
    c.setAlphaF(alpha)
    return QPen(c, width)


def _glow_color(color: str, alpha: float = 0.25) -> QColor:
    c = QColor(color)
    c.setAlphaF(alpha)
    return c


# ---------------------------------------------------------------------------
# Main combined widget
# ---------------------------------------------------------------------------

class CombinedFootprintWidget(QWidget):
    """Three-panel unified footprint canvas.

    Shares a single Y-axis (price) across all three panels so every
    row is perfectly aligned.
    """

    TICK = 0.25          # MES tick size
    VISIBLE_BARS = 8     # how many time bars to display

    def __init__(self, parent=None):
        super().__init__(parent)
        self._volume_profile: Optional[VolumeProfile] = None
        self._footprint_bars: list[FootprintBar] = []

        # animation state
        self._glow_phase = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick_animation)
        self._anim_timer.start(40)       # 25 fps

        # volatility (0-1): drives border glow intensity
        self._volatility = 0.0

        self.setMinimumSize(700, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: {COLORS['bg_panel']};")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_profile(self, profile: VolumeProfile):
        self._volume_profile = profile
        self.update()

    def set_bars(self, bars: list[FootprintBar]):
        self._footprint_bars = bars
        self.update()

    def set_volatility(self, v: float):
        self._volatility = max(0.0, min(1.0, v))

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def _tick_animation(self):
        self._glow_phase = (self._glow_phase + 0.04) % (2 * math.pi)
        self.update()

    def _glow_alpha(self) -> float:
        base = 0.3 + 0.15 * self._volatility
        return base + (0.15 + 0.1 * self._volatility) * math.sin(self._glow_phase)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # Solid background
        painter.fillRect(0, 0, w, h, QColor(COLORS["bg_panel"]))

        # Subtle animated gradient overlay (cyberpunk atmosphere)
        grad = QLinearGradient(0, 0, w, h)
        shift = 0.3 + 0.1 * math.sin(self._glow_phase * 0.7)
        grad.setColorAt(0, _glow_color(COLORS["cyan"], 0.015))
        grad.setColorAt(shift, _glow_color(COLORS["bg_panel"], 0.0))
        grad.setColorAt(1, _glow_color(COLORS["magenta"], 0.01))
        painter.fillRect(0, 0, w, h, QBrush(grad))

        # Compute price range from all data
        prices: set[float] = set()
        if self._volume_profile and self._volume_profile.levels:
            prices.update(self._volume_profile.levels.keys())
        for bar in self._footprint_bars[-self.VISIBLE_BARS:]:
            prices.update(bar.profile.levels.keys())

        if not prices:
            self._draw_empty(painter, w, h)
            painter.end()
            return

        min_price = min(prices)
        max_price = max(prices)
        price_range = max_price - min_price or 1.0

        margin_top = 28
        margin_bottom = 22
        chart_h = h - margin_top - margin_bottom

        def price_to_y(p: float) -> float:
            return margin_top + chart_h - ((p - min_price) / price_range) * chart_h

        tick = self.TICK
        num_levels = max(int(price_range / tick) + 1, 1)
        row_h = max(chart_h / num_levels, 7.0)

        # Panel widths
        vol_w = int(w * 0.23)
        fp_w = int(w * 0.52)
        delta_x = vol_w + fp_w
        delta_w = w - delta_x

        # Draw panels
        self._draw_volume_profile(painter, 0, vol_w, margin_top, chart_h,
                                  min_price, max_price, price_to_y, row_h)

        self._draw_footprint(painter, vol_w, fp_w, margin_top, chart_h,
                             min_price, max_price, price_to_y, row_h)

        self._draw_delta_profile(painter, delta_x, delta_w, margin_top, chart_h,
                                 min_price, max_price, price_to_y, row_h)

        # Panel separator lines with neon glow
        glow_a = self._glow_alpha()
        sep_col = QColor(COLORS["cyan"])
        sep_col.setAlphaF(glow_a * 0.7)
        sep_pen = QPen(sep_col, 1)
        painter.setPen(sep_pen)
        painter.drawLine(vol_w, 0, vol_w, h)
        painter.drawLine(delta_x, 0, delta_x, h)

        # Animated neon outer border
        border_col = QColor(COLORS["green_bright"])
        border_col.setAlphaF(glow_a * (0.4 + 0.3 * self._volatility))
        painter.setPen(QPen(border_col, 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Panel headers
        self._draw_headers(painter, vol_w, fp_w, delta_x, delta_w, margin_top)

        # Bottom info bar
        self._draw_info_bar(painter, 0, w, h - margin_bottom, margin_bottom)

        painter.end()

    def _draw_empty(self, painter: QPainter, w: int, h: int):
        painter.setPen(QColor(COLORS["text_muted"]))
        font = QFont("JetBrains Mono", 11)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
            "FOOTPRINT COMMAND CENTER\n\nAwaiting market data stream...",
        )

    def _draw_headers(self, painter: QPainter, vol_w, fp_w, delta_x, delta_w, margin_top):
        font = QFont("JetBrains Mono", 8)
        font.setBold(True)
        painter.setFont(font)

        headers = [
            (vol_w // 2,       "VOLUME PROFILE",    COLORS["cyan"]),
            (vol_w + fp_w // 2, "BID × ASK FOOTPRINT", COLORS["green_bright"]),
            (delta_x + delta_w // 2, "DELTA PROFILE", COLORS["amber"]),
        ]
        for cx, text, color in headers:
            painter.setPen(QColor(color))
            painter.drawText(
                QRectF(cx - 140, 2, 280, margin_top - 4),
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            # Neon underline
            underline_col = QColor(color)
            underline_col.setAlphaF(0.5)
            painter.setPen(QPen(underline_col, 1))
            text_w = min(len(text) * 7, 200)
            painter.drawLine(cx - text_w // 2, margin_top - 2,
                             cx + text_w // 2, margin_top - 2)

    def _draw_info_bar(self, painter: QPainter, x, w, y, h):
        painter.fillRect(x, y, w, h,
                         QColor(COLORS["bg_card"]))
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawLine(x, y, x + w, y)

        font = QFont("JetBrains Mono", 8)
        painter.setFont(font)

        parts = []
        if self._volume_profile:
            cd = self._volume_profile.cumulative_delta
            vol = self._volume_profile.total_volume
            poc = self._volume_profile.poc
            val, vah = self._volume_profile.value_area()
            color = COLORS["delta_positive"] if cd >= 0 else COLORS["delta_negative"]
            parts.append((f" Cumul Δ: {cd:+,}", color))
            parts.append((f"  Vol: {vol:,}", COLORS["text_secondary"]))
            if poc:
                parts.append((f"  POC: {poc:.2f}", COLORS["cyan"]))
            if vah and val:
                parts.append((f"  VAH: {vah:.2f}  VAL: {val:.2f}", COLORS["amber"]))

        bx = x + 4
        for text, color in parts:
            painter.setPen(QColor(color))
            painter.drawText(bx, y + h - 4, text)
            bx += len(text) * 6.5

    # ------------------------------------------------------------------
    # Volume Profile Panel (left)
    # ------------------------------------------------------------------

    def _draw_volume_profile(self, painter, x, w, margin_top, chart_h,
                              min_price, max_price, price_to_y, row_h):
        if not self._volume_profile or not self._volume_profile.levels:
            painter.setPen(QColor(COLORS["text_muted"]))
            font = QFont("JetBrains Mono", 8)
            painter.setFont(font)
            painter.drawText(
                QRectF(x, margin_top, w, chart_h),
                Qt.AlignmentFlag.AlignCenter, "no data",
            )
            return

        profile = self._volume_profile
        levels = profile.sorted_levels()
        max_vol = max((lv.total_volume for lv in levels), default=1) or 1
        poc = profile.poc
        val, vah = profile.value_area()

        label_w = 44
        bar_area = w - label_w - 6
        font_tiny = QFont("JetBrains Mono", 6)
        painter.setFont(font_tiny)

        for lv in levels:
            y = price_to_y(lv.price)
            bar_y = int(y - row_h / 2 + 1)
            bar_h = max(int(row_h - 2), 1)

            # Value area background
            in_va = val and vah and val <= lv.price <= vah
            if in_va:
                va_col = QColor(COLORS["green_bright"])
                va_col.setAlphaF(0.04)
                painter.fillRect(x, bar_y - 1, w, bar_h + 2, va_col)

            # Volume bars (ask=green, bid=red side by side)
            total_w = (lv.total_volume / max_vol) * bar_area
            if lv.total_volume > 0:
                ask_w = int(total_w * (lv.ask_volume / lv.total_volume))
                bid_w = int(total_w * (lv.bid_volume / lv.total_volume))
            else:
                ask_w = bid_w = 0

            bx = x + label_w + 2

            # Ask (green)
            if ask_w > 0:
                col = QColor(COLORS["delta_positive"])
                col.setAlphaF(0.7)
                painter.fillRect(bx, bar_y, ask_w, bar_h, col)

            # Bid (red)
            if bid_w > 0:
                col = QColor(COLORS["delta_negative"])
                col.setAlphaF(0.6)
                painter.fillRect(bx + ask_w, bar_y, bid_w, bar_h, col)

            # POC glow ring
            if poc and abs(lv.price - poc) < 0.001:
                glow = self._glow_alpha()
                poc_col = QColor(COLORS["cyan"])
                poc_col.setAlphaF(glow)
                painter.setPen(QPen(poc_col, 1))
                painter.drawRect(int(bx) - 1, bar_y - 1,
                                 int(total_w) + 2, bar_h + 2)
                # POC label
                painter.setPen(QColor(COLORS["cyan"]))
                painter.drawText(int(bx + total_w + 3), int(y + 3), "P")

            # Price label
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(
                QRectF(x + 1, bar_y, label_w - 3, bar_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{lv.price:.2f}",
            )

        # VAH / VAL dashed lines
        font_marker = QFont("JetBrains Mono", 7)
        painter.setFont(font_marker)
        if vah:
            vy = price_to_y(vah)
            painter.setPen(QPen(QColor(COLORS["amber"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x + label_w, int(vy), x + w, int(vy))
            painter.setPen(QColor(COLORS["amber"]))
            painter.drawText(x + label_w + 2, int(vy - 1), "VAH")
        if val:
            vy = price_to_y(val)
            painter.setPen(QPen(QColor(COLORS["amber"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x + label_w, int(vy), x + w, int(vy))
            painter.setPen(QColor(COLORS["amber"]))
            painter.drawText(x + label_w + 2, int(vy + 9), "VAL")

    # ------------------------------------------------------------------
    # Footprint Grid Panel (center)
    # ------------------------------------------------------------------

    def _draw_footprint(self, painter, x, w, margin_top, chart_h,
                        min_price, max_price, price_to_y, row_h):
        bars = self._footprint_bars[-self.VISIBLE_BARS:]

        if not bars:
            painter.setPen(QColor(COLORS["text_muted"]))
            font = QFont("JetBrains Mono", 9)
            painter.setFont(font)
            painter.drawText(
                QRectF(x, margin_top, w, chart_h),
                Qt.AlignmentFlag.AlignCenter, "awaiting\nfootprint data",
            )
            return

        n = len(bars)
        bar_w = w / n

        max_lv_vol = max(
            (lv.total_volume for bar in bars for lv in bar.profile.levels.values()),
            default=1,
        ) or 1

        font_cell = QFont("JetBrains Mono", 6)
        painter.setFont(font_cell)

        # Grid lines between bars
        painter.setPen(QPen(QColor(COLORS["grid"]), 1))
        for i in range(1, n):
            bx = int(x + i * bar_w)
            painter.drawLine(bx, margin_top, bx, margin_top + chart_h)

        for i, bar in enumerate(bars):
            bx = x + i * bar_w

            # OHLC body background (very subtle)
            if bar.open is not None and bar.close is not None:
                o_y = price_to_y(bar.open)
                c_y = price_to_y(bar.close)
                body_col = QColor(
                    COLORS["delta_positive"] if bar.is_bullish else COLORS["delta_negative"]
                )
                body_col.setAlphaF(0.05)
                painter.fillRect(
                    int(bx), int(min(o_y, c_y)),
                    int(bar_w - 1), max(int(abs(c_y - o_y)), 1),
                    body_col,
                )

            for lv in bar.profile.levels.values():
                y = price_to_y(lv.price)
                cell_y = int(y - row_h / 2 + 1)
                cell_h = max(int(row_h - 2), 5)

                # Cell background intensity (volume-weighted)
                intensity = min(lv.total_volume / max_lv_vol, 1.0)
                if lv.delta > 0:
                    bg = QColor(COLORS["delta_positive"])
                elif lv.delta < 0:
                    bg = QColor(COLORS["delta_negative"])
                else:
                    bg = QColor(COLORS["delta_neutral"])

                bg.setAlphaF(0.08 + 0.45 * intensity)
                painter.fillRect(int(bx) + 1, cell_y, int(bar_w) - 2, cell_h, bg)

                # Horizontal cell separator
                painter.setPen(QPen(QColor(COLORS["grid"]), 1))
                painter.drawLine(int(bx), cell_y, int(bx + bar_w), cell_y)

                # Bid × Ask text (if cell tall enough)
                if bar_w >= 48 and cell_h >= 7:
                    half = bar_w / 2
                    mid_x = bx + half

                    # Bid (left side, red)
                    bid_col = QColor(COLORS["short_color"])
                    painter.setPen(bid_col)
                    painter.drawText(
                        QRectF(bx + 1, cell_y, half - 5, cell_h),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        str(lv.bid_volume),
                    )

                    # Separator ×
                    painter.setPen(QColor(COLORS["text_muted"]))
                    painter.drawText(
                        QRectF(mid_x - 4, cell_y, 8, cell_h),
                        Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                        "×",
                    )

                    # Ask (right side, green)
                    ask_col = QColor(COLORS["long_color"])
                    painter.setPen(ask_col)
                    painter.drawText(
                        QRectF(mid_x + 5, cell_y, half - 6, cell_h),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        str(lv.ask_volume),
                    )

            # Bar delta total at bottom
            delta_col = QColor(
                COLORS["delta_positive"] if bar.delta >= 0 else COLORS["delta_negative"]
            )
            font_delta = QFont("JetBrains Mono", 7)
            font_delta.setBold(True)
            painter.setFont(font_delta)
            painter.setPen(delta_col)
            painter.drawText(
                QRectF(bx, margin_top + chart_h + 2, bar_w, 18),
                Qt.AlignmentFlag.AlignCenter, f"{bar.delta:+d}",
            )
            painter.setFont(font_cell)

    # ------------------------------------------------------------------
    # Delta Profile Panel (right)
    # ------------------------------------------------------------------

    def _draw_delta_profile(self, painter, x, w, margin_top, chart_h,
                             min_price, max_price, price_to_y, row_h):
        if not self._volume_profile or not self._volume_profile.levels:
            return

        levels = self._volume_profile.sorted_levels()
        if not levels:
            return

        max_abs_delta = max((abs(lv.delta) for lv in levels), default=1) or 1
        half_w = (w - 8) // 2
        center_x = x + w // 2

        font_tiny = QFont("JetBrains Mono", 6)
        painter.setFont(font_tiny)

        # Center axis
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawLine(center_x, margin_top, center_x, margin_top + chart_h)

        for lv in levels:
            y = price_to_y(lv.price)
            bar_y = int(y - row_h / 2 + 1)
            bar_h = max(int(row_h - 2), 1)

            frac = abs(lv.delta) / max_abs_delta
            bw = int(half_w * frac)

            if lv.delta > 0:
                col = QColor(COLORS["delta_positive"])
                col.setAlphaF(0.7)
                painter.fillRect(center_x, bar_y, bw, bar_h, col)
                # Glow tip
                tip_col = QColor(COLORS["delta_positive"])
                tip_col.setAlphaF(0.35 * self._glow_alpha())
                painter.fillRect(center_x + bw - 2, bar_y, 2, bar_h, tip_col)
            elif lv.delta < 0:
                col = QColor(COLORS["delta_negative"])
                col.setAlphaF(0.7)
                painter.fillRect(int(center_x - bw), bar_y, bw, bar_h, col)
                tip_col = QColor(COLORS["delta_negative"])
                tip_col.setAlphaF(0.35 * self._glow_alpha())
                painter.fillRect(int(center_x - bw), bar_y, 2, bar_h, tip_col)

            # Value text
            if bar_h >= 7:
                painter.setPen(QColor(COLORS["text_white"]))
                painter.drawText(
                    QRectF(x, bar_y, w - 2, bar_h),
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                    f"{lv.delta:+d}",
                )


# ---------------------------------------------------------------------------
# Container widget that wraps combined footprint with a header and controls
# ---------------------------------------------------------------------------

class FootprintCommandCenter(QFrame):
    """Full footprint command center: combined chart + session info row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Header row
        header = QHBoxLayout()
        title = QLabel("FOOTPRINT COMMAND CENTER")
        title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {COLORS['green_bright']}; "
            f"letter-spacing: 3px; padding: 2px 4px;"
        )
        header.addWidget(title)
        header.addStretch()

        self._vol_label = QLabel("Vol: —")
        self._vol_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        header.addWidget(self._vol_label)

        self._poc_label = QLabel("POC: —")
        self._poc_label.setStyleSheet(f"color: {COLORS['cyan']}; font-size: 11px;")
        header.addWidget(self._poc_label)

        self._delta_label = QLabel("Δ: —")
        self._delta_label.setStyleSheet(f"color: {COLORS['amber']}; font-size: 11px; padding-right: 4px;")
        header.addWidget(self._delta_label)

        layout.addLayout(header)

        # The chart
        self.chart = CombinedFootprintWidget()
        layout.addWidget(self.chart, 1)

    def set_profile(self, profile: VolumeProfile):
        self.chart.set_profile(profile)
        # Update header labels
        if profile:
            poc = profile.poc
            val, vah = profile.value_area()
            cd = profile.cumulative_delta
            vol = profile.total_volume
            self._vol_label.setText(f"Vol: {vol:,}")
            self._poc_label.setText(f"POC: {poc:.2f}" if poc else "POC: —")
            color = COLORS["delta_positive"] if cd >= 0 else COLORS["delta_negative"]
            self._delta_label.setText(f"Δ: {cd:+,}")
            self._delta_label.setStyleSheet(f"color: {color}; font-size: 11px; padding-right: 4px;")

    def set_bars(self, bars: list[FootprintBar]):
        self.chart.set_bars(bars)

    def set_volatility(self, v: float):
        self.chart.set_volatility(v)
