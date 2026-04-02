"""Cross-Asset Intelligence Dashboard Panel.

Retro Bloomberg/Tron terminal aesthetic.
Shows: per-asset grid, GEX profile chart, gamma levels, composite signal.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QGridLayout, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush,
    QLinearGradient, QFontMetrics, QPainterPath,
)

# ---------------------------------------------------------------------------
# Color constants — retro terminal palette
# ---------------------------------------------------------------------------
_BG        = '#050508'
_BG_PANEL  = '#080810'
_BG_CARD   = '#0a0a14'
_CYAN      = '#00d4ff'
_GREEN     = '#00ff88'
_RED       = '#ff3344'
_AMBER     = '#ff8c00'
_DIM_TEXT  = '#666688'
_WHITE     = '#ccdddd'
_MAGENTA   = '#ff00ff'
_BORDER    = '#003344'

_MONO_FONT = 'Courier New'


def _qc(hex_str: str) -> QColor:
    return QColor(hex_str)


def _asset_color(signal: str) -> str:
    """Return hex color based on signal string."""
    if signal in ('bullish', 'strong_bullish', 'mild_bullish', 'risk_on',
                  'credit_positive', 'risk_appetite', 'confirming_bullish'):
        return _GREEN
    elif signal in ('bearish', 'strong_bearish', 'mild_bearish', 'risk_off',
                    'credit_stress', 'risk_aversion', 'confirming_bearish',
                    'risk_off_bearish'):
        return _RED
    return _DIM_TEXT


# ---------------------------------------------------------------------------
# GEX Profile Chart widget
# ---------------------------------------------------------------------------

class GEXProfileChart(QWidget):
    """Custom paint widget — draws the Gamma Exposure bar chart."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._gex_profile: list[tuple[float, float]] = []
        self._flip_price: Optional[float] = None
        self._call_wall: Optional[float] = None
        self._put_wall: Optional[float] = None
        self._current_price: Optional[float] = None
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background-color: {_BG_CARD}; border: 1px solid {_BORDER};")

    def update_data(self, gex: dict, spy_price: Optional[float] = None):
        self._gex_profile = gex.get('gex_profile', [])
        self._flip_price = gex.get('flip_price')
        self._call_wall = gex.get('call_wall')
        self._put_wall = gex.get('put_wall')
        self._current_price = spy_price
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(0, 0, w, h, _qc(_BG_CARD))

        # Title
        title_font = QFont(_MONO_FONT, 9, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(_qc(_CYAN))
        painter.drawText(6, 14, "GAMMA EXPOSURE PROFILE")

        chart_top = 22
        chart_bottom = h - 20
        chart_left = 8
        chart_right = w - 8
        chart_h = chart_bottom - chart_top
        chart_w = chart_right - chart_left

        if not self._gex_profile:
            painter.setPen(_qc(_DIM_TEXT))
            painter.drawText(chart_left, chart_top + chart_h // 2, "AWAITING GEX DATA...")
            painter.end()
            return

        strikes = [p[0] for p in self._gex_profile]
        values  = [p[1] for p in self._gex_profile]

        min_strike = min(strikes)
        max_strike = max(strikes)
        strike_range = max_strike - min_strike if max_strike != min_strike else 1.0

        max_abs_gex = max(abs(v) for v in values) if values else 1.0
        if max_abs_gex == 0:
            max_abs_gex = 1.0

        zero_y = chart_top + chart_h // 2  # midline = zero GEX

        # Draw grid lines
        grid_pen = QPen(_qc(_BORDER))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        painter.drawLine(chart_left, zero_y, chart_right, zero_y)

        # Draw GEX bars
        bar_width = max(2, chart_w // max(len(strikes), 1) - 1)

        for strike, val in self._gex_profile:
            x = chart_left + int((strike - min_strike) / strike_range * chart_w)
            bar_h = int(abs(val) / max_abs_gex * (chart_h // 2 - 4))

            if val >= 0:
                bar_color = QColor(_GREEN)
                bar_color.setAlpha(180)
                rect_y = zero_y - bar_h
                rect_h = bar_h
            else:
                bar_color = QColor(_RED)
                bar_color.setAlpha(180)
                rect_y = zero_y
                rect_h = bar_h

            painter.fillRect(x - bar_width // 2, rect_y, bar_width, rect_h, bar_color)

        # Vertical reference lines
        def _strike_to_x(k: float) -> int:
            return chart_left + int((k - min_strike) / strike_range * chart_w)

        line_items = []
        if self._flip_price is not None:
            line_items.append((_strike_to_x(self._flip_price), _qc('#ffffff'), Qt.PenStyle.DashLine, 'FLIP'))
        if self._call_wall is not None:
            line_items.append((_strike_to_x(self._call_wall), _qc(_GREEN), Qt.PenStyle.SolidLine, 'CALL'))
        if self._put_wall is not None:
            line_items.append((_strike_to_x(self._put_wall), _qc(_RED), Qt.PenStyle.SolidLine, 'PUT'))
        if self._current_price is not None:
            line_items.append((_strike_to_x(self._current_price), _qc(_CYAN), Qt.PenStyle.DotLine, 'NOW'))

        lbl_font = QFont(_MONO_FONT, 7)
        painter.setFont(lbl_font)

        for lx, color, style, label in line_items:
            if chart_left <= lx <= chart_right:
                pen = QPen(color, 1, style)
                painter.setPen(pen)
                painter.drawLine(lx, chart_top, lx, chart_bottom)

                # Label at top
                color.setAlpha(200)
                painter.setPen(color)
                lbl_x = max(chart_left, min(lx - 10, chart_right - 24))
                painter.drawText(lbl_x, chart_top + 9, label)

        # X-axis strike labels (every N strikes)
        label_step = max(1, len(strikes) // 6)
        lbl_font2 = QFont(_MONO_FONT, 7)
        painter.setFont(lbl_font2)
        painter.setPen(_qc(_DIM_TEXT))
        for i, strike in enumerate(strikes):
            if i % label_step == 0:
                lx = _strike_to_x(strike)
                painter.drawText(lx - 12, chart_bottom + 14, f"{strike:.0f}")

        painter.end()


# ---------------------------------------------------------------------------
# Asset row widget
# ---------------------------------------------------------------------------

class AssetRow(QWidget):
    """Single-line asset display: symbol | price | chg% | signal bar | status."""

    BAR_MAX_W = 60
    BAR_H = 6

    def __init__(self, symbol: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.symbol = symbol
        self._price = 0.0
        self._change_pct = 0.0
        self._signal_val = 0.0
        self._signal_str = 'neutral'
        self._color = _DIM_TEXT
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def update_asset(self, data: dict):
        self._price = data.get('price', 0.0)
        self._change_pct = data.get('change_pct', 0.0)
        self._signal_val = data.get('signal_value', 0.0)
        self._signal_str = data.get('signal', 'neutral')
        self._color = _asset_color(self._signal_str)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Background — slight highlight on hover (static for now)
        painter.fillRect(0, 0, w, h, _qc(_BG_PANEL))

        mono = QFont(_MONO_FONT, 9)
        painter.setFont(mono)
        fm = QFontMetrics(mono)

        color = _qc(self._color)
        dim   = _qc(_DIM_TEXT)
        cyan  = _qc(_CYAN)

        y_text = h // 2 + fm.ascent() // 2 - 1

        # Symbol (fixed 10 chars)
        sym_str = f"{self.symbol:<10}"
        painter.setPen(cyan)
        painter.drawText(4, y_text, sym_str)

        # Price
        price_str = f"{self._price:>10.2f}"
        painter.setPen(color)
        painter.drawText(85, y_text, price_str)

        # Change %
        chg_str = f"{self._change_pct:>+7.2f}%"
        chg_color = _qc(_GREEN) if self._change_pct >= 0 else _qc(_RED)
        painter.setPen(chg_color)
        painter.drawText(170, y_text, chg_str)

        # Signal bar
        bar_x = 250
        bar_y = h // 2 - self.BAR_H // 2
        bar_bg_rect = (bar_x, bar_y, self.BAR_MAX_W, self.BAR_H)
        painter.fillRect(*bar_bg_rect, _qc(_BORDER))

        fill_val = abs(self._signal_val)
        fill_w = int(fill_val * self.BAR_MAX_W)
        if fill_w > 0:
            bar_color = QColor(self._color)
            bar_color.setAlpha(200)
            painter.fillRect(bar_x, bar_y, fill_w, self.BAR_H, bar_color)

        # Signal label
        sig_label = self._signal_str.replace('_', ' ').upper()[:14]
        painter.setPen(color)
        painter.setFont(QFont(_MONO_FONT, 8))
        painter.drawText(bar_x + self.BAR_MAX_W + 6, y_text, sig_label)

        # Separator line
        painter.setPen(QPen(_qc(_BORDER), 1))
        painter.drawLine(0, h - 1, w, h - 1)

        painter.end()


# ---------------------------------------------------------------------------
# Gamma Levels Text Panel
# ---------------------------------------------------------------------------

class GammaLevelsPanel(QWidget):
    """Monospace text grid showing GEX key levels."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._lines: list[tuple[str, str, str]] = []  # (label, value, note)
        self._gex: dict = {}
        self._spy_price: Optional[float] = None
        self.setMinimumHeight(130)
        self.setStyleSheet(f"background-color: {_BG_CARD}; border: 1px solid {_BORDER};")

    def update_data(self, gex: dict, spy_price: Optional[float] = None):
        self._gex = gex
        self._spy_price = spy_price
        self.update()

    def _build_lines(self) -> list[tuple[str, str, str, str]]:
        """Returns list of (label, value, bar_color, note) tuples."""
        g = self._gex
        spy = self._spy_price
        lines = []

        flip = g.get('flip_price')
        if flip is not None:
            if spy is not None:
                pos = 'ABOVE (long gamma)' if spy > flip else 'BELOW (short gamma)'
                col = _GREEN if spy > flip else _RED
            else:
                pos, col = '', _DIM_TEXT
            lines.append(('GAMMA FLIP:', f"{flip:>8.2f}", col, pos))

        call_wall = g.get('call_wall')
        if call_wall is not None:
            dist = f"+{(call_wall - spy):.1f}pts" if spy else ''
            lines.append(('CALL WALL: ', f"{call_wall:>8.2f}", _GREEN, dist))

        put_wall = g.get('put_wall')
        if put_wall is not None:
            dist = f"-{(spy - put_wall):.1f}pts" if spy else ''
            lines.append(('PUT WALL:  ', f"{put_wall:>8.2f}", _RED, dist))

        max_pain = g.get('max_pain')
        if max_pain is not None:
            lines.append(('MAX PAIN:  ', f"{max_pain:>8.2f}", _AMBER, ''))

        net_gex = g.get('net_gex')
        if net_gex is not None:
            if abs(net_gex) >= 1e9:
                gex_str = f"{'+'if net_gex>=0 else ''}{net_gex/1e9:>6.2f}B"
            elif abs(net_gex) >= 1e6:
                gex_str = f"{'+'if net_gex>=0 else ''}{net_gex/1e6:>6.0f}M"
            else:
                gex_str = f"{net_gex:>+8.0f}"
            dealer = g.get('dealer_positioning', '')
            regime_note = 'PINNING' if net_gex > 2e9 else ('AMPLIFYING' if net_gex < -1e9 else '')
            col = _GREEN if net_gex > 0 else _RED
            lines.append(('NET GEX:   ', gex_str, col, f"{dealer.upper().replace('_',' ')} {regime_note}"))

        pcr = g.get('put_call_ratio')
        if pcr is not None:
            if pcr < 0.7:
                pcr_note, pcr_col = 'COMPLACENT', _RED
            elif pcr > 1.3:
                pcr_note, pcr_col = 'FEARFUL', _GREEN
            else:
                pcr_note, pcr_col = 'NEUTRAL', _DIM_TEXT
            lines.append(('PUT/CALL:  ', f"{pcr:>8.2f}", pcr_col, pcr_note))

        vix_regime = g.get('vix_regime', '')
        regime_color = g.get('regime_color', _DIM_TEXT)
        if vix_regime:
            lines.append(('VIX REGIME:', f"{vix_regime.upper():>8}", regime_color, ''))

        return lines

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, _qc(_BG_CARD))

        if not self._gex:
            painter.setPen(_qc(_DIM_TEXT))
            font = QFont(_MONO_FONT, 9)
            painter.setFont(font)
            painter.drawText(8, 20, "GEX DATA PENDING...")
            painter.end()
            return

        lines = self._build_lines()
        font = QFont(_MONO_FONT, 9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        line_h = fm.height() + 4

        y = fm.ascent() + 6
        for label, value, color, note in lines:
            # Label (dim)
            painter.setPen(_qc(_DIM_TEXT))
            painter.drawText(6, y, label)

            # Value (colored)
            painter.setPen(_qc(color))
            painter.drawText(6 + fm.horizontalAdvance(label) + 4, y, value)

            # Mini block indicator
            bar_x = 6 + fm.horizontalAdvance(label) + fm.horizontalAdvance(value) + 16
            painter.fillRect(bar_x, y - fm.ascent() + 2, 18, fm.ascent() - 2, _qc(color))

            # Note
            if note:
                painter.setPen(_qc(color))
                painter.setFont(QFont(_MONO_FONT, 8))
                painter.drawText(bar_x + 24, y, note)
                painter.setFont(font)

            y += line_h
            if y > h - 4:
                break

        painter.end()


# ---------------------------------------------------------------------------
# Composite Signal Display
# ---------------------------------------------------------------------------

class CompositeSignalDisplay(QWidget):
    """Large centered composite direction display with score bar."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._direction = 'FLAT'
        self._score = 0.0
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def update_signal(self, direction: str, score: float):
        self._direction = direction
        self._score = score
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, _qc(_BG))

        # Color by direction
        if self._direction == 'LONG':
            color_hex = _GREEN
            label = '\u25c8 CROSS-ASSET: BULLISH \u25c8'
        elif self._direction == 'SHORT':
            color_hex = _RED
            label = '\u25c8 CROSS-ASSET: BEARISH \u25c8'
        else:
            color_hex = _DIM_TEXT
            label = '\u25c8 CROSS-ASSET: NEUTRAL \u25c8'

        # Border
        border_pen = QPen(_qc(color_hex), 1)
        painter.setPen(border_pen)
        painter.drawRect(1, 1, w - 2, h - 2)

        # Main label
        big_font = QFont(_MONO_FONT, 13, QFont.Weight.Bold)
        painter.setFont(big_font)
        painter.setPen(_qc(color_hex))
        fm = QFontMetrics(big_font)
        text_w = fm.horizontalAdvance(label)
        text_x = (w - text_w) // 2
        painter.drawText(text_x, 22, label)

        # Score bar (centered, below text)
        bar_total_w = min(300, w - 40)
        bar_h = 6
        bar_x = (w - bar_total_w) // 2
        bar_y = 33

        # Background
        painter.fillRect(bar_x, bar_y, bar_total_w, bar_h, _qc(_BORDER))

        # Center divider
        mid_x = bar_x + bar_total_w // 2
        painter.setPen(QPen(_qc(_DIM_TEXT), 1))
        painter.drawLine(mid_x, bar_y, mid_x, bar_y + bar_h)

        # Fill — from center outward
        fill_w = int(abs(self._score) * (bar_total_w // 2))
        bar_color = QColor(color_hex)
        bar_color.setAlpha(220)
        if self._score >= 0:
            painter.fillRect(mid_x, bar_y, fill_w, bar_h, bar_color)
        else:
            painter.fillRect(mid_x - fill_w, bar_y, fill_w, bar_h, bar_color)

        # Score numeric
        score_font = QFont(_MONO_FONT, 8)
        painter.setFont(score_font)
        painter.setPen(_qc(_DIM_TEXT))
        score_label = f"score: {self._score:+.3f}"
        sfm = QFontMetrics(score_font)
        painter.drawText(
            (w - sfm.horizontalAdvance(score_label)) // 2,
            bar_y + bar_h + 10,
            score_label,
        )

        painter.end()


# ---------------------------------------------------------------------------
# Main CrossAssetPanel
# ---------------------------------------------------------------------------

class CrossAssetPanel(QWidget):
    """Cross-asset intelligence dashboard — retro terminal aesthetic."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._asset_rows: dict[str, AssetRow] = {}
        self._current_data: dict = {}
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {_BG};
                color: {_CYAN};
                font-family: '{_MONO_FONT}', monospace;
                font-size: 11px;
            }}
            QFrame#section {{
                background-color: {_BG_PANEL};
                border: 1px solid {_BORDER};
            }}
            QLabel#header {{
                color: {_MAGENTA};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 2px 6px;
                border-bottom: 1px solid {_BORDER};
                background: {_BG_CARD};
            }}
        """)

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(8)

        # ── Left column ──
        left_col = QVBoxLayout()
        left_col.setSpacing(6)

        # Composite signal display (top of left column)
        self.composite_display = CompositeSignalDisplay()
        left_col.addWidget(self.composite_display)

        # Asset grid header
        asset_header = QLabel("CROSS-ASSET CORRELATION GRID")
        asset_header.setObjectName("header")
        left_col.addWidget(asset_header)

        # Asset grid container (scrollable)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {_BG_PANEL}; }}")

        asset_container = QWidget()
        asset_container.setStyleSheet(f"background: {_BG_PANEL};")
        self._asset_layout = QVBoxLayout(asset_container)
        self._asset_layout.setContentsMargins(0, 0, 0, 0)
        self._asset_layout.setSpacing(0)

        # Create rows for each asset in a fixed display order
        _DISPLAY_ORDER = [
            'VIX', '10Y YIELD', 'DXY', 'NQ FUTS', 'RUSSELL',
            'HY BONDS', 'LT BONDS', 'GOLD', 'OIL/WTI', 'BITCOIN',
        ]
        for name in _DISPLAY_ORDER:
            row = AssetRow(name)
            self._asset_rows[name] = row
            self._asset_layout.addWidget(row)

        self._asset_layout.addStretch(1)
        scroll.setWidget(asset_container)
        left_col.addWidget(scroll, stretch=1)

        # ── Right column ──
        right_col = QVBoxLayout()
        right_col.setSpacing(6)

        # GEX chart header
        gex_chart_header = QLabel("GAMMA EXPOSURE PROFILE")
        gex_chart_header.setObjectName("header")
        right_col.addWidget(gex_chart_header)

        # GEX profile chart
        self.gex_chart = GEXProfileChart()
        right_col.addWidget(self.gex_chart, stretch=2)

        # Gamma levels header
        levels_header = QLabel("KEY GAMMA LEVELS")
        levels_header.setObjectName("header")
        right_col.addWidget(levels_header)

        # Gamma levels panel
        self.gamma_levels = GammaLevelsPanel()
        right_col.addWidget(self.gamma_levels, stretch=1)

        # Assemble root layout
        left_widget = QWidget()
        left_widget.setLayout(left_col)
        left_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        right_widget = QWidget()
        right_widget.setLayout(right_col)
        right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root_layout.addWidget(left_widget, stretch=5)
        root_layout.addWidget(right_widget, stretch=5)

    # ------------------------------------------------------------------
    # Public update method
    # ------------------------------------------------------------------

    def update_data(self, cross_asset_data: dict):
        """Called whenever the CrossAssetFeed emits new data."""
        if not cross_asset_data:
            return

        self._current_data = cross_asset_data
        assets = cross_asset_data.get('assets', {})
        gex = cross_asset_data.get('gex', {})
        direction = cross_asset_data.get('composite_direction', 'FLAT')
        score = cross_asset_data.get('composite_signal', 0.0)

        # Update composite display
        self.composite_display.update_signal(direction, score)

        # Update asset rows
        for name, row in self._asset_rows.items():
            asset_data = assets.get(name)
            if asset_data:
                row.update_asset(asset_data)

        # Derive SPY price from NQ if available
        spy_price: Optional[float] = None
        nq_data = assets.get('NQ FUTS', {})
        if nq_data and nq_data.get('price', 0) > 0:
            # SPY ≈ NQ / ~20 (rough proxy)
            pass  # We'll use flip_price directly (already in SPY terms)
        flip_price = gex.get('flip_price')
        if flip_price and flip_price > 0:
            spy_price = flip_price  # treat as reference price area

        # Update GEX chart & levels
        self.gex_chart.update_data(gex, spy_price=None)  # chart draws current price line via flip
        self.gamma_levels.update_data(gex, spy_price=spy_price)
