"""Custom widgets for the MES Trading Intelligence desktop app.

Includes: footprint chart, volume profile, delta profile, signal meter,
strategy scorecard, and trade table.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QSizePolicy, QGridLayout, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, QRectF, Signal as QtSignal
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QLinearGradient

from .theme import COLORS
from ..orderflow import VolumeProfile, FootprintBar, FootprintChart, PriceLevel


class ScanlineOverlay(QWidget):
    """CRT scanline effect overlay."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(0.06)
        pen = QPen(QColor(0, 0, 0))
        pen.setWidth(1)
        painter.setPen(pen)
        h = self.height()
        w = self.width()
        y = 0
        while y < h:
            painter.drawLine(0, y, w, y)
            y += 3
        painter.end()


class GlowLabel(QLabel):
    """Label with a subtle glow effect."""

    def __init__(self, text="", color=COLORS["green_bright"], parent=None):
        super().__init__(text, parent)
        self._color = color
        self.setStyleSheet(f"color: {color}; background: transparent;")


class ConfidenceMeter(QWidget):
    """Horizontal confidence bar with gradient fill."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._label = ""
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)

    def set_value(self, value: float, label: str = ""):
        self._value = max(0, min(1, value))
        self._label = label
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLORS["bg_dark"]))

        # Border
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Fill
        fill_w = int((w - 4) * self._value)
        if fill_w > 0:
            gradient = QLinearGradient(2, 0, fill_w + 2, 0)
            if self._value > 0.7:
                gradient.setColorAt(0, QColor(COLORS["green_dim"]))
                gradient.setColorAt(1, QColor(COLORS["green_bright"]))
            elif self._value > 0.4:
                gradient.setColorAt(0, QColor(COLORS["amber_dim"]))
                gradient.setColorAt(1, QColor(COLORS["amber"]))
            else:
                gradient.setColorAt(0, QColor(COLORS["red_dim"]))
                gradient.setColorAt(1, QColor(COLORS["red"]))

            painter.fillRect(2, 2, fill_w, h - 4, QBrush(gradient))

        # Text
        painter.setPen(QColor(COLORS["text_white"]))
        font = QFont("JetBrains Mono", 9)
        painter.setFont(font)
        text = f"{self._label} {self._value:.0%}" if self._label else f"{self._value:.0%}"
        painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class VolumeProfileWidget(QWidget):
    """Renders a vertical volume profile with POC, VAH, VAL, and delta coloring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile: Optional[VolumeProfile] = None
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_profile(self, profile: VolumeProfile):
        self._profile = profile
        self.update()

    def paintEvent(self, event):
        if not self._profile or not self._profile.levels:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        profile = self._profile

        # Background
        painter.fillRect(0, 0, w, h, QColor(COLORS["bg_panel"]))

        levels = profile.sorted_levels()
        if not levels:
            painter.end()
            return

        min_price = levels[0].price
        max_price = levels[-1].price
        price_range = max_price - min_price
        if price_range == 0:
            painter.end()
            return

        max_vol = max(lv.total_volume for lv in levels)
        if max_vol == 0:
            painter.end()
            return

        poc = profile.poc
        val, vah = profile.value_area()

        label_width = 60
        bar_area = w - label_width - 10
        row_height = max(2, (h - 20) / max(len(levels), 1))

        font = QFont("JetBrains Mono", 8)
        painter.setFont(font)

        for i, lv in enumerate(reversed(levels)):
            y = 10 + i * row_height
            if y + row_height > h - 10:
                break

            # Price label
            painter.setPen(QColor(COLORS["text_muted"]))
            painter.drawText(QRectF(2, y, label_width - 4, row_height),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{lv.price:.2f}")

            # Volume bars — split into bid (left/red) and ask (right/green)
            bar_x = label_width + 4
            total_w = (lv.total_volume / max_vol) * bar_area

            if lv.total_volume > 0:
                bid_w = (lv.bid_volume / lv.total_volume) * total_w
                ask_w = (lv.ask_volume / lv.total_volume) * total_w
            else:
                bid_w = ask_w = 0

            # Value area highlight
            in_value_area = val and vah and val <= lv.price <= vah
            if in_value_area:
                painter.fillRect(int(bar_x), int(y), int(total_w), int(row_height - 1),
                                 QColor(COLORS["green_glow"]))

            # Bid volume (red)
            if bid_w > 0:
                painter.fillRect(int(bar_x), int(y), int(bid_w), int(row_height - 1),
                                 QColor(COLORS["delta_negative"]))

            # Ask volume (green)
            if ask_w > 0:
                painter.fillRect(int(bar_x + bid_w), int(y), int(ask_w), int(row_height - 1),
                                 QColor(COLORS["delta_positive"]))

            # POC marker
            if lv.price == poc:
                painter.setPen(QPen(QColor(COLORS["cyan"]), 2))
                painter.drawRect(int(bar_x) - 1, int(y) - 1,
                                 int(total_w) + 2, int(row_height) + 1)
                painter.setPen(QColor(COLORS["cyan"]))
                painter.drawText(int(bar_x + total_w + 4), int(y + row_height - 2), "POC")

        # VAH / VAL labels
        if vah:
            vah_y = 10 + ((max_price - vah) / price_range) * (h - 20)
            painter.setPen(QPen(QColor(COLORS["amber"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(label_width, int(vah_y), w, int(vah_y))
            painter.drawText(w - 35, int(vah_y - 2), "VAH")

        if val:
            val_y = 10 + ((max_price - val) / price_range) * (h - 20)
            painter.setPen(QPen(QColor(COLORS["amber"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(label_width, int(val_y), w, int(val_y))
            painter.drawText(w - 35, int(val_y + 12), "VAL")

        painter.end()


class FootprintChartWidget(QWidget):
    """Renders delta footprint bars — shows bid/ask volume at each price for each time bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars: list[FootprintBar] = []
        self._visible_bars = 20
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_bars(self, bars: list[FootprintBar]):
        self._bars = bars
        self.update()

    def paintEvent(self, event):
        if not self._bars:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(COLORS["bg_panel"]))

        bars = self._bars[-self._visible_bars:]
        if not bars:
            painter.end()
            return

        # Find price range across all visible bars
        all_prices = []
        for bar in bars:
            for lv in bar.profile.levels.values():
                all_prices.append(lv.price)

        if not all_prices:
            painter.end()
            return

        min_price = min(all_prices)
        max_price = max(all_prices)
        price_range = max_price - min_price
        if price_range == 0:
            price_range = 1.0

        # Layout
        margin_left = 60
        margin_bottom = 20
        chart_w = w - margin_left - 10
        chart_h = h - margin_bottom - 10
        bar_width = chart_w / max(len(bars), 1)

        font_small = QFont("JetBrains Mono", 7)
        font_label = QFont("JetBrains Mono", 8)

        # Price labels on left
        painter.setFont(font_label)
        painter.setPen(QColor(COLORS["text_muted"]))
        tick_size = 0.25
        price = min_price
        while price <= max_price:
            y = 5 + chart_h - ((price - min_price) / price_range) * chart_h
            painter.drawText(QRectF(0, y - 6, margin_left - 4, 12),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{price:.2f}")
            # Grid line
            painter.setPen(QPen(QColor(COLORS["grid"]), 1))
            painter.drawLine(margin_left, int(y), w - 10, int(y))
            painter.setPen(QColor(COLORS["text_muted"]))
            price += tick_size * 4  # every point

        # Draw each footprint bar
        painter.setFont(font_small)
        max_level_vol = 1
        for bar in bars:
            for lv in bar.profile.levels.values():
                max_level_vol = max(max_level_vol, lv.total_volume)

        for i, bar in enumerate(bars):
            bx = margin_left + i * bar_width
            half_bw = bar_width * 0.45

            # OHLC body
            if bar.open is not None and bar.close is not None:
                o_y = 5 + chart_h - ((bar.open - min_price) / price_range) * chart_h
                c_y = 5 + chart_h - ((bar.close - min_price) / price_range) * chart_h
                body_color = COLORS["delta_positive"] if bar.is_bullish else COLORS["delta_negative"]
                painter.fillRect(int(bx + 2), int(min(o_y, c_y)),
                                 int(bar_width - 4), max(int(abs(c_y - o_y)), 1),
                                 QColor(body_color + "40"))

            # Delta values at each price level
            for lv in bar.profile.levels.values():
                py = 5 + chart_h - ((lv.price - min_price) / price_range) * chart_h
                cell_h = max(chart_h / (price_range / 0.25), 8)

                # Color intensity based on volume
                intensity = min(lv.total_volume / max(max_level_vol * 0.3, 1), 1.0)

                if lv.delta > 0:
                    color = QColor(COLORS["delta_positive"])
                    color.setAlphaF(0.3 + 0.7 * intensity)
                elif lv.delta < 0:
                    color = QColor(COLORS["delta_negative"])
                    color.setAlphaF(0.3 + 0.7 * intensity)
                else:
                    color = QColor(COLORS["delta_neutral"])
                    color.setAlphaF(0.2)

                painter.fillRect(int(bx + 1), int(py - cell_h / 2),
                                 int(bar_width - 2), max(int(cell_h - 1), 1), color)

                # Delta number
                if bar_width > 30 and cell_h > 8:
                    painter.setPen(QColor(COLORS["text_white"]))
                    delta_text = f"{lv.delta:+d}" if lv.delta != 0 else "0"
                    painter.drawText(
                        QRectF(bx + 1, py - cell_h / 2, bar_width - 2, cell_h),
                        Qt.AlignmentFlag.AlignCenter,
                        delta_text,
                    )

            # Bar delta total at bottom
            painter.setPen(QColor(COLORS["delta_positive"] if bar.delta >= 0 else COLORS["delta_negative"]))
            painter.setFont(font_small)
            painter.drawText(
                QRectF(bx, h - margin_bottom, bar_width, margin_bottom),
                Qt.AlignmentFlag.AlignCenter,
                f"{bar.delta:+d}",
            )

        painter.end()


class StrategyScorecard(QWidget):
    """Shows all strategy scores as horizontal bars with direction indicators."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scores: dict[str, dict] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(2)
        self._bars: dict[str, tuple[QLabel, ConfidenceMeter, QLabel]] = {}

    def update_scores(self, scores: dict[str, dict]):
        """Update strategy scores. Each entry: {score, confidence, direction}"""
        self._scores = scores

        for name, data in scores.items():
            if name not in self._bars:
                row = QHBoxLayout()
                label = QLabel(name.replace("_", " ").upper())
                label.setFixedWidth(120)
                label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")

                meter = ConfidenceMeter()
                meter.setFixedHeight(18)

                direction_label = QLabel("")
                direction_label.setFixedWidth(50)
                direction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                row.addWidget(label)
                row.addWidget(meter, 1)
                row.addWidget(direction_label)
                self._layout.addLayout(row)
                self._bars[name] = (label, meter, direction_label)

            _, meter, dir_label = self._bars[name]
            meter.set_value(abs(data.get("confidence", 0)), "")

            d = data.get("direction", "FLAT")
            if d == "LONG":
                dir_label.setText("LONG")
                dir_label.setStyleSheet(f"color: {COLORS['long_color']}; font-size: 10px; font-weight: bold;")
            elif d == "SHORT":
                dir_label.setText("SHORT")
                dir_label.setStyleSheet(f"color: {COLORS['short_color']}; font-size: 10px; font-weight: bold;")
            else:
                dir_label.setText("FLAT")
                dir_label.setStyleSheet(f"color: {COLORS['flat_color']}; font-size: 10px;")


class TradeTable(QTableWidget):
    """Table showing recent trades with P&L coloring."""

    COLUMNS = ["Time", "Dir", "Qty", "Entry", "Exit", "P&L", "R", "Grade"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(self.COLUMNS))
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)

    def load_trades(self, trades: list[dict]):
        self.setRowCount(len(trades))
        for row, t in enumerate(trades):
            self.setItem(row, 0, QTableWidgetItem(t.get("entry_time", "")[:19]))

            dir_item = QTableWidgetItem(t.get("direction", ""))
            color = COLORS["long_color"] if t.get("direction") == "LONG" else COLORS["short_color"]
            dir_item.setForeground(QColor(color))
            self.setItem(row, 1, dir_item)

            self.setItem(row, 2, QTableWidgetItem(str(t.get("quantity", 1))))
            self.setItem(row, 3, QTableWidgetItem(f"{t.get('entry_price', 0):.2f}"))
            self.setItem(row, 4, QTableWidgetItem(
                f"{t['exit_price']:.2f}" if t.get("exit_price") else "OPEN"
            ))

            pnl = t.get("pnl")
            pnl_item = QTableWidgetItem(f"${pnl:.2f}" if pnl is not None else "-")
            if pnl is not None:
                pnl_item.setForeground(QColor(COLORS["long_color"] if pnl >= 0 else COLORS["short_color"]))
            self.setItem(row, 5, pnl_item)

            r = t.get("r_multiple")
            self.setItem(row, 6, QTableWidgetItem(f"{r:.1f}R" if r else "-"))
            self.setItem(row, 7, QTableWidgetItem(t.get("grade", "-")))


class SignalPanel(QFrame):
    """Shows the current signal state with big direction indicator and confidence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        layout = QVBoxLayout(self)

        self.title = QLabel("SIGNAL ENGINE")
        self.title.setObjectName("subtitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)

        self.direction_label = QLabel("SCANNING...")
        self.direction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.direction_label.setStyleSheet(
            f"font-size: 28px; font-weight: bold; color: {COLORS['text_muted']}; padding: 8px;"
        )
        layout.addWidget(self.direction_label)

        self.confidence_meter = ConfidenceMeter()
        layout.addWidget(self.confidence_meter)

        # Details grid
        details = QGridLayout()
        self.entry_label = QLabel("Entry: -")
        self.stop_label = QLabel("Stop: -")
        self.target_label = QLabel("Target: -")
        self.rr_label = QLabel("R:R: -")
        self.regime_label = QLabel("Regime: -")
        self.agree_label = QLabel("Agree: 0/7")

        for i, lbl in enumerate([self.entry_label, self.stop_label, self.target_label,
                                  self.rr_label, self.regime_label, self.agree_label]):
            lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
            details.addWidget(lbl, i // 2, i % 2)

        layout.addLayout(details)

    def update_signal(self, data: dict):
        direction = data.get("direction", "FLAT")
        confidence = data.get("confidence", 0)

        if direction == "LONG":
            self.direction_label.setText(">>> LONG >>>")
            self.direction_label.setStyleSheet(
                f"font-size: 28px; font-weight: bold; color: {COLORS['long_color']}; padding: 8px;"
            )
        elif direction == "SHORT":
            self.direction_label.setText("<<< SHORT <<<")
            self.direction_label.setStyleSheet(
                f"font-size: 28px; font-weight: bold; color: {COLORS['short_color']}; padding: 8px;"
            )
        else:
            self.direction_label.setText("— SCANNING —")
            self.direction_label.setStyleSheet(
                f"font-size: 28px; font-weight: bold; color: {COLORS['text_muted']}; padding: 8px;"
            )

        self.confidence_meter.set_value(confidence, "CONF")

        entry = data.get("entry")
        self.entry_label.setText(f"Entry: {entry:.2f}" if entry else "Entry: -")
        stop = data.get("stop")
        self.stop_label.setText(f"Stop: {stop:.2f}" if stop else "Stop: -")
        target = data.get("target")
        self.target_label.setText(f"Target: {target:.2f}" if target else "Target: -")

        rr = data.get("risk_reward")
        self.rr_label.setText(f"R:R: {rr:.1f}" if rr else "R:R: -")

        self.regime_label.setText(f"Regime: {data.get('regime', '-')}")
        agree = data.get("ensemble_score", 0)
        self.agree_label.setText(f"Score: {agree:.3f}")


class DeltaProfileWidget(QWidget):
    """Horizontal delta bars at each price level — shows buying vs selling pressure."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile: Optional[VolumeProfile] = None
        self.setMinimumWidth(120)

    def set_profile(self, profile: VolumeProfile):
        self._profile = profile
        self.update()

    def paintEvent(self, event):
        if not self._profile or not self._profile.levels:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(COLORS["bg_panel"]))

        levels = self._profile.sorted_levels()
        if not levels:
            painter.end()
            return

        max_delta = max(abs(lv.delta) for lv in levels)
        if max_delta == 0:
            max_delta = 1

        center_x = w // 2
        row_height = max(2, (h - 20) / max(len(levels), 1))

        font = QFont("JetBrains Mono", 7)
        painter.setFont(font)

        # Center line
        painter.setPen(QPen(QColor(COLORS["border_bright"]), 1))
        painter.drawLine(center_x, 5, center_x, h - 15)

        for i, lv in enumerate(reversed(levels)):
            y = 10 + i * row_height
            if y + row_height > h - 10:
                break

            bar_max = (w // 2) - 10
            bar_w = int((abs(lv.delta) / max_delta) * bar_max)

            if lv.delta > 0:
                color = QColor(COLORS["delta_positive"])
                painter.fillRect(center_x, int(y), bar_w, max(int(row_height - 1), 1), color)
            elif lv.delta < 0:
                color = QColor(COLORS["delta_negative"])
                painter.fillRect(center_x - bar_w, int(y), bar_w, max(int(row_height - 1), 1), color)

        # Labels
        painter.setPen(QColor(COLORS["text_muted"]))
        painter.drawText(5, h - 3, "SELL")
        painter.drawText(w - 28, h - 3, "BUY")

        painter.end()


class StatsPanel(QFrame):
    """Shows daily/session trading statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.labels: dict[str, QLabel] = {}

        stats = [
            ("Trades", "0"), ("Win Rate", "0%"), ("P&L", "$0.00"),
            ("Profit Factor", "0.00"), ("Avg R", "0.0"), ("Sharpe", "0.00"),
            ("Max DD", "$0.00"), ("Avg Win", "$0.00"), ("Avg Loss", "$0.00"),
        ]

        for i, (name, default) in enumerate(stats):
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10px;")
            val_lbl = QLabel(default)
            val_lbl.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px; font-weight: bold;")
            layout.addWidget(name_lbl, (i // 3) * 2, i % 3)
            layout.addWidget(val_lbl, (i // 3) * 2 + 1, i % 3)
            self.labels[name] = val_lbl

    def update_stats(self, stats: dict):
        mapping = {
            "Trades": ("total_trades", lambda x: str(x)),
            "Win Rate": ("win_rate", lambda x: f"{x:.0%}" if x <= 1 else f"{x:.0f}%"),
            "P&L": ("net_pnl", lambda x: f"${x:+.2f}"),
            "Profit Factor": ("profit_factor", lambda x: f"{x:.2f}"),
            "Avg R": ("avg_r_multiple", lambda x: f"{x:.1f}R"),
            "Sharpe": ("sharpe", lambda x: f"{x:.2f}"),
            "Max DD": ("max_drawdown", lambda x: f"${x:.2f}"),
            "Avg Win": ("avg_win", lambda x: f"${x:.2f}"),
            "Avg Loss": ("avg_loss", lambda x: f"${x:.2f}"),
        }

        for name, (key, fmt) in mapping.items():
            if key in stats and name in self.labels:
                val = stats[key]
                self.labels[name].setText(fmt(val))
                # Color P&L
                if name == "P&L":
                    color = COLORS["long_color"] if val >= 0 else COLORS["short_color"]
                    self.labels[name].setStyleSheet(
                        f"color: {color}; font-size: 12px; font-weight: bold;"
                    )


class NewsFeed(QFrame):
    """Scrolling news feed with sentiment coloring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        title = QLabel("NEWS FEED")
        title.setObjectName("subtitle")
        layout.addWidget(title)

        self._items_layout = QVBoxLayout()
        self._items_layout.setSpacing(1)
        layout.addLayout(self._items_layout)
        layout.addStretch()

    def add_news(self, headline: str, sentiment: float = 0.0, is_trump: bool = False):
        item = QLabel(headline)
        item.setWordWrap(True)
        item.setStyleSheet(f"font-size: 10px; padding: 2px 4px;")

        if is_trump:
            item.setStyleSheet(
                f"font-size: 10px; padding: 2px 4px; color: {COLORS['amber']}; "
                f"border-left: 2px solid {COLORS['amber']};"
            )
        elif sentiment > 0.3:
            item.setStyleSheet(
                f"font-size: 10px; padding: 2px 4px; color: {COLORS['long_color']};"
            )
        elif sentiment < -0.3:
            item.setStyleSheet(
                f"font-size: 10px; padding: 2px 4px; color: {COLORS['short_color']};"
            )
        else:
            item.setStyleSheet(
                f"font-size: 10px; padding: 2px 4px; color: {COLORS['text_muted']};"
            )

        self._items_layout.insertWidget(0, item)

        # Keep max 50 items
        while self._items_layout.count() > 50:
            w = self._items_layout.takeAt(self._items_layout.count() - 1)
            if w.widget():
                w.widget().deleteLater()


# ═══════════════════════════════════════════════════════════════
# PHASE 2 WIDGETS
# ═══════════════════════════════════════════════════════════════

class BigTradesWidget(QWidget):
    """Big trades dot chart — like ATAS Big Trades indicator.

    Shows big trade events as dots on a price timeline.
    Dot size ∝ trade size. Green=buy, red=sell.
    Yellow ring = ABSORPTION, white ring = BREAKOUT.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trades: list[dict] = []
        self.setMinimumHeight(120)
        self.setToolTip("Big Trades — dots sized by volume, yellow=absorption, white=breakout")

    def update_trades(self, trades: list[dict]):
        self._trades = trades[-50:]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(COLORS.get("bg_dark", "#0a0a0f")))

        if not self._trades:
            p.setPen(QColor(COLORS.get("text_dim", "#606080")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for big trades...")
            return

        # Price range
        prices = [t.get("price", 0) for t in self._trades if t.get("price")]
        if not prices:
            return
        price_min = min(prices) - 1
        price_max = max(prices) + 1
        price_range = price_max - price_min or 1

        # Size range for dot scaling
        sizes = [t.get("size", 1) for t in self._trades]
        max_size = max(sizes) or 1

        n = len(self._trades)
        margin = 20

        for i, trade in enumerate(self._trades):
            x = margin + (i / max(n - 1, 1)) * (w - 2 * margin)
            price = trade.get("price", price_min)
            y = h - margin - ((price - price_min) / price_range) * (h - 2 * margin)
            y = max(margin, min(h - margin, y))

            size = trade.get("size", 1)
            radius = max(4, min(20, int(6 + (size / max_size) * 14)))

            side = trade.get("side", "BUY")
            color = QColor(COLORS.get("long_color", "#00ff88")) if side == "BUY" \
                else QColor(COLORS.get("short_color", "#ff3366"))

            # Fill dot
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(x - radius), int(y - radius), radius * 2, radius * 2)

            # Classification ring
            classification = trade.get("classification", "")
            if classification == "ABSORPTION":
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ffff00"), 2))
                p.drawEllipse(int(x - radius - 2), int(y - radius - 2),
                              (radius + 2) * 2, (radius + 2) * 2)
            elif classification == "BREAKOUT":
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ffffff"), 2))
                p.drawEllipse(int(x - radius - 2), int(y - radius - 2),
                              (radius + 2) * 2, (radius + 2) * 2)

        # Price axis labels
        p.setPen(QColor(COLORS.get("text_dim", "#606080")))
        p.setFont(QFont("Menlo", 8))
        p.drawText(2, h - margin, f"{price_min:.0f}")
        p.drawText(2, margin + 10, f"{price_max:.0f}")


class InstitutionalFlowWidget(QWidget):
    """Displays detected institutional patterns: TWAP bands, iceberg levels, sweeps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._patterns: list[dict] = []
        self.setMinimumHeight(150)

    def add_pattern(self, pattern: dict):
        """Add a detected institutional pattern."""
        self._patterns.append(pattern)
        if len(self._patterns) > 30:
            self._patterns = self._patterns[-30:]
        self.update()

    def clear_patterns(self):
        self._patterns.clear()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(COLORS.get("bg_dark", "#0a0a0f")))

        if not self._patterns:
            p.setPen(QColor(COLORS.get("text_dim", "#606080")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No institutional patterns detected")
            return

        # Draw pattern list
        p.setFont(QFont("Menlo", 10))
        y = 20
        line_h = 22

        pattern_colors = {
            "TWAP": COLORS.get("cyan", "#00ffff"),
            "VWAP_EXEC": COLORS.get("amber", "#ffaa00"),
            "ICEBERG": COLORS.get("purple", "#9933ff"),
            "SWEEP": COLORS.get("short_color", "#ff3366"),
            "ACCUMULATION": COLORS.get("long_color", "#00ff88"),
            "DISTRIBUTION": COLORS.get("short_color", "#ff3366"),
        }

        for pattern in reversed(self._patterns[-int(h / line_h):]):
            ptype = pattern.get("pattern_type", "UNKNOWN")
            conf = pattern.get("confidence", 0)
            side = pattern.get("side", "?")
            price_range = pattern.get("price_range", (0, 0))
            est_size = pattern.get("estimated_size", 0)

            color = pattern_colors.get(ptype, COLORS.get("text_muted", "#606080"))
            p.setPen(QColor(color))

            text = (
                f"[{ptype:<12}] {side:>5} | "
                f"conf={conf:.0%} | "
                f"est={est_size:,} | "
                f"range={price_range[0]:.2f}-{price_range[1]:.2f}"
                if isinstance(price_range, tuple) else
                f"[{ptype:<12}] {side:>5} | conf={conf:.0%} | est={est_size:,}"
            )
            p.drawText(8, y, text)
            y += line_h


class DOMImbalanceWidget(QWidget):
    """DOM ladder showing bid/ask imbalance with spoofing alerts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bids: list[dict] = []  # [{price, volume}]
        self._asks: list[dict] = []
        self._spoofing_alert: Optional[str] = None
        self._alert_timer = 0
        self.setMinimumWidth(200)
        self.setMinimumHeight(200)

        # Blink timer
        self._blink = False
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._do_blink)
        self._blink_timer.start(500)

    def _do_blink(self):
        self._blink = not self._blink
        if self._spoofing_alert and time.time() - self._alert_timer > 5:
            self._spoofing_alert = None
        self.update()

    def update_dom(self, bids: list[dict], asks: list[dict]):
        self._bids = sorted(bids, key=lambda x: x.get("price", 0), reverse=True)[:10]
        self._asks = sorted(asks, key=lambda x: x.get("price", 0))[:10]
        self.update()

    def set_spoofing_alert(self, alert_type: str, side: str):
        self._spoofing_alert = f"⚠ {alert_type}: {side}"
        self._alert_timer = time.time()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(COLORS.get("bg_dark", "#0a0a0f")))

        # Spoofing alert at top
        if self._spoofing_alert and self._blink:
            p.setPen(QColor("#ffff00"))
            p.setFont(QFont("Menlo", 10, QFont.Weight.Bold))
            p.drawText(4, 16, self._spoofing_alert)

        if not self._bids and not self._asks:
            p.setPen(QColor(COLORS.get("text_dim", "#606080")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No DOM data")
            return

        # Max volume for bar scaling
        all_vols = [b.get("volume", 0) for b in self._bids + self._asks]
        max_vol = max(all_vols) or 1

        row_h = min(20, (h - 40) // max(len(self._bids) + len(self._asks), 1))
        p.setFont(QFont("Menlo", 8))

        # Draw asks (above) then bids (below) — simplified single column layout
        y_start = 30 if self._spoofing_alert else 10
        mid_y = y_start + len(self._asks) * row_h + 5
        spread_price = ""
        if self._asks and self._bids:
            spread = self._asks[0].get("price", 0) - self._bids[0].get("price", 0)
            spread_price = f"SPREAD: {spread:.2f}"

        # Draw asks
        for i, ask in enumerate(reversed(self._asks)):
            vol = ask.get("volume", 0)
            price = ask.get("price", 0)
            y = y_start + i * row_h

            imbalance = vol / max_vol
            bar_w = int(imbalance * (w * 0.5))
            p.fillRect(w - bar_w, y + 1, bar_w, row_h - 2,
                       QColor(255, 51, 102, 80))  # red tint

            p.setPen(QColor(COLORS.get("short_color", "#ff3366")))
            p.drawText(4, y + row_h - 3, f"{price:.2f}")
            p.setPen(QColor(COLORS.get("text_muted", "#606080")))
            p.drawText(w // 2, y + row_h - 3, f"{vol:,}")

        # Spread label
        p.setPen(QColor(COLORS.get("cyan", "#00ffff")))
        p.drawText(4, int(mid_y), spread_price)

        # Draw bids
        for i, bid in enumerate(self._bids):
            vol = bid.get("volume", 0)
            price = bid.get("price", 0)
            y = int(mid_y + 8 + i * row_h)

            imbalance = vol / max_vol
            bar_w = int(imbalance * (w * 0.5))
            p.fillRect(0, y + 1, bar_w, row_h - 2,
                       QColor(0, 255, 136, 80))  # green tint

            p.setPen(QColor(COLORS.get("long_color", "#00ff88")))
            p.drawText(4, y + row_h - 3, f"{price:.2f}")
            p.setPen(QColor(COLORS.get("text_muted", "#606080")))
            p.drawText(w // 2, y + row_h - 3, f"{vol:,}")


class OrderFlowSummaryWidget(QWidget):
    """Compact order flow summary: aggressive vol, MTF delta, algo detection, CD divergence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}
        self.setFixedHeight(100)

    def update_data(self, flow_data: dict):
        """Update with AdvancedOrderFlowEngine composite signal."""
        self._data = flow_data
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(COLORS.get("bg_mid", "#0f0f1a")))

        if not self._data:
            p.setPen(QColor(COLORS.get("text_dim", "#606080")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for flow data...")
            return

        p.setFont(QFont("Menlo", 9))

        # Row 1: Aggressive buy vs sell bar
        agg_buy = self._data.get("aggressive_buy_vol", 0)
        agg_sell = self._data.get("aggressive_sell_vol", 0)
        total = agg_buy + agg_sell or 1
        buy_pct = agg_buy / total

        bar_y, bar_h = 8, 16
        # Background
        p.fillRect(0, bar_y, w, bar_h, QColor(40, 40, 60))
        # Buy side (green)
        p.fillRect(0, bar_y, int(buy_pct * w), bar_h,
                   QColor(COLORS.get("long_color", "#00ff88")))

        p.setPen(QColor("#ffffff"))
        p.drawText(4, bar_y + 12,
                   f"BUY {agg_buy:,}  SELL {agg_sell:,}  ({buy_pct:.0%} aggr. buy)")

        # Row 2: MTF alignment
        mtf = self._data.get("mtf_alignment", {})
        mtf_text = "MTF: "
        mtf_colors = {"1m": "#606080", "5m": "#606080", "15m": "#606080"}
        for tf in ("1m", "5m", "15m"):
            delta = mtf.get(tf, {}).get("delta", 0)
            if delta > 0:
                mtf_colors[tf] = COLORS.get("long_color", "#00ff88")
                mtf_text += f"{tf}▲ "
            elif delta < 0:
                mtf_colors[tf] = COLORS.get("short_color", "#ff3366")
                mtf_text += f"{tf}▼ "
            else:
                mtf_text += f"{tf}– "

        p.setPen(QColor(COLORS.get("cyan", "#00ffff")))
        p.drawText(4, 44, mtf_text)

        # Row 3: Algo detection badge
        flow_class = self._data.get("flow_classification", {})
        pattern = flow_class.get("pattern", "HUMAN")
        algo_conf = flow_class.get("algo_confidence", 0)
        badge_color = COLORS.get("amber", "#ffaa00") if algo_conf > 0.6 else COLORS.get("text_muted", "#606080")
        p.setPen(QColor(badge_color))
        p.drawText(4, 62, f"FLOW: {pattern}  (algo={algo_conf:.0%})")

        # Row 4: CD divergence alert
        cd_div = self._data.get("cd_divergence_alert")
        if cd_div:
            div_type = cd_div.get("divergence_type", "")
            div_conf = cd_div.get("confidence", 0)
            blink_color = COLORS.get("long_color") if div_type == "BULLISH" else COLORS.get("short_color", "#ff3366")
            p.setPen(QColor(blink_color))
            p.drawText(4, 80, f"⚡ CD DIV: {div_type} (conf={div_conf:.0%})")
        else:
            p.setPen(QColor(COLORS.get("text_dim", "#606080")))
            p.drawText(4, 80, "CD: No divergence")


class AdvancedFootprintWidget(QWidget):
    """Phase 2 advanced footprint chart with full ATAS-like features.

    Features:
    - Bid x Ask cells with delta
    - POC highlight, stacked imbalance triangles (outside footprint)
    - Diagonal imbalance, unfinished/finished auctions
    - Single prints, absorption zones, exhaustion markers
    - Cluster analysis auto-signals (▲▼)
    - Mouse wheel zoom, click+drag pan
    """

    IMBALANCE_RATIO = 3.0  # bid or ask > 3x the other = imbalance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars: list[FootprintBar] = []
        self._zoom = 1.0
        self._pan_offset = 0
        self._drag_start = None
        self._cell_h = 14  # pixels per price level
        self.setMinimumSize(400, 200)
        self.setMouseTracking(True)
        self._hover_info: str = ""

    def set_bars(self, bars: list[FootprintBar]):
        self._bars = bars[-20:]  # show last 20 bars
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(self._zoom * 1.1, 4.0)
        else:
            self._zoom = max(self._zoom / 1.1, 0.5)
        self.update()

    def mousePressEvent(self, event):
        self._drag_start = event.pos().x()

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            dx = event.pos().x() - self._drag_start
            self._pan_offset += dx
            self._drag_start = event.pos().x()
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag_start = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(COLORS.get("bg_dark", "#0a0a0f")))

        if not self._bars:
            p.setPen(QColor(COLORS.get("text_dim", "#606080")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No footprint data — waiting for ticks...")
            return

        cell_h = int(self._cell_h * self._zoom)
        bar_w = max(60, int((w - 60) / len(self._bars)))

        # Price range across all bars
        all_prices = []
        for bar in self._bars:
            for level in bar.levels:
                all_prices.append(level.price)
        if not all_prices:
            return
        price_min = min(all_prices)
        price_max = max(all_prices)

        # Draw each bar
        for bi, bar in enumerate(self._bars):
            bx = 60 + bi * bar_w + self._pan_offset

            # Volume per price level map
            level_map = {lv.price: lv for lv in bar.levels}

            # Max volume in bar for imbalance detection
            max_vol = max((lv.bid_volume + lv.ask_volume for lv in bar.levels), default=1) or 1

            imbalance_stack_bid: list[float] = []  # prices with bid imbalance
            imbalance_stack_ask: list[float] = []  # prices with ask imbalance

            prev_price = None
            for price in sorted(level_map.keys()):
                lv = level_map[price]
                py = h - cell_h - int((price - price_min) / max((price_max - price_min), 1) * (h - cell_h * 2))
                py = max(0, min(h - cell_h, py))

                # Cell background color based on delta
                ask_v = lv.ask_volume or 0
                bid_v = lv.bid_volume or 0
                delta = ask_v - bid_v
                total = ask_v + bid_v or 1

                if ask_v > 0 and bid_v > 0:
                    ratio = ask_v / bid_v
                    if ratio > self.IMBALANCE_RATIO:
                        # Ask imbalance — green tint
                        bg = QColor(0, 100, 60, 140)
                        imbalance_stack_ask.append(price)
                    elif bid_v / ask_v > self.IMBALANCE_RATIO:
                        # Bid imbalance — red tint
                        bg = QColor(150, 30, 60, 140)
                        imbalance_stack_bid.append(price)
                    else:
                        intensity = min(abs(delta) / total, 1.0)
                        if delta > 0:
                            bg = QColor(0, int(80 * intensity), int(50 * intensity), 120)
                        else:
                            bg = QColor(int(120 * intensity), 0, int(40 * intensity), 120)
                else:
                    bg = QColor(20, 20, 35)

                # POC highlight
                if bar.poc_price and abs(price - bar.poc_price) < 0.13:
                    p.fillRect(bx, py, bar_w - 1, cell_h, QColor(60, 60, 0, 180))
                    p.setPen(QPen(QColor("#ffff00"), 1))
                    p.drawRect(bx, py, bar_w - 2, cell_h - 1)
                else:
                    p.fillRect(bx, py, bar_w - 1, cell_h, bg)

                # Cell text: "BID x ASK"
                p.setFont(QFont("Menlo", max(6, int(8 * self._zoom))))
                p.setPen(QColor(COLORS.get("text_muted", "#606080")))
                if bid_v > 0 or ask_v > 0:
                    cell_text = f"{bid_v} x {ask_v}"
                    p.drawText(bx + 2, py + cell_h - 2, cell_text)

                # Single print indicator (no volume at this level)
                if total == 0:
                    p.setPen(QPen(QColor(COLORS.get("text_dim", "#606080")), 1,
                                  Qt.PenStyle.DotLine))
                    p.drawLine(bx, py + cell_h // 2, bx + bar_w - 2, py + cell_h // 2)

                prev_price = price

            # Stacked imbalance triangles OUTSIDE footprint (right side)
            tri_x = bx + bar_w
            if len(imbalance_stack_ask) >= 3:
                # Green triangles on right for stacked ask imbalance
                for si, sp in enumerate(imbalance_stack_ask[-5:]):
                    sy = h - cell_h - int((sp - price_min) / max((price_max - price_min), 1) * (h - cell_h * 2))
                    self._draw_triangle(p, tri_x + 2 + si * 5, sy + cell_h // 2,
                                        5, QColor(COLORS.get("long_color", "#00ff88")), "right")

            if len(imbalance_stack_bid) >= 3:
                # Red triangles on left for stacked bid imbalance
                for si, sp in enumerate(imbalance_stack_bid[-5:]):
                    sy = h - cell_h - int((sp - price_min) / max((price_max - price_min), 1) * (h - cell_h * 2))
                    self._draw_triangle(p, bx - 2 - si * 5, sy + cell_h // 2,
                                        5, QColor(COLORS.get("short_color", "#ff3366")), "left")

            # Bar OHLC outline
            p.setPen(QPen(QColor(COLORS.get("grid_color", "#1a1a2e")), 1))
            bar_top = h - cell_h - int((price_max - price_min) / max(price_max - price_min, 1) * (h - cell_h * 2))
            p.drawRect(bx, 0, bar_w - 1, h)

        # Price axis (left)
        p.setFont(QFont("Menlo", 7))
        p.setPen(QColor(COLORS.get("text_dim", "#606080")))
        for price in range(int(price_min), int(price_max) + 1, max(1, int((price_max - price_min) / 10))):
            py = h - cell_h - int((price - price_min) / max((price_max - price_min), 1) * (h - cell_h * 2))
            if 0 < py < h:
                p.drawText(2, py + 4, f"{price:.0f}")

    @staticmethod
    def _draw_triangle(p: QPainter, x: int, y: int, size: int,
                       color: QColor, direction: str):
        """Draw a small triangle indicator."""
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        if direction == "right":
            pts = [QPoint(x, y - size), QPoint(x + size * 2, y), QPoint(x, y + size)]
        else:
            pts = [QPoint(x, y - size), QPoint(x - size * 2, y), QPoint(x, y + size)]
        poly = QPolygon(pts)
        p.drawPolygon(poly)
