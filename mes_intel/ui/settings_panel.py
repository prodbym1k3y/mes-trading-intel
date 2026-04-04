"""Settings panel — enter API credentials and connection config.

Saves to var/mes_intel/config.json. Changes take effect on restart.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QGroupBox, QFormLayout,
    QCheckBox, QComboBox, QTextEdit,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

log = logging.getLogger(__name__)

_BG       = "#050508"
_CYAN     = "#00d4ff"
_GREEN    = "#00ff88"
_AMBER    = "#ff8c00"
_RED      = "#ff3344"
_DIM      = "#444466"
_TEXT     = "#ccddff"
_MONO     = "Courier New, monospace"

_BASE_STYLE = f"""
QWidget {{ background: {_BG}; color: {_TEXT}; font-family: {_MONO}; font-size: 11px; }}
QLineEdit {{
    background: #0c0c18;
    color: {_GREEN};
    border: 1px solid {_DIM};
    padding: 4px 8px;
    font-family: {_MONO};
    font-size: 11px;
}}
QLineEdit:focus {{ border: 1px solid {_CYAN}; }}
QPushButton {{
    background: #0a0a18;
    color: {_CYAN};
    border: 1px solid {_DIM};
    padding: 6px 18px;
    font-family: {_MONO};
    font-size: 11px;
    letter-spacing: 1px;
}}
QPushButton:hover {{ background: #14142a; border-color: {_CYAN}; }}
QPushButton:pressed {{ background: #1a1a38; }}
QGroupBox {{
    color: {_CYAN};
    border: 1px solid {_DIM};
    border-radius: 4px;
    margin-top: 14px;
    font-family: {_MONO};
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
QLabel {{ background: transparent; }}
QCheckBox {{ color: {_TEXT}; background: transparent; }}
QComboBox {{
    background: #0c0c18; color: {_GREEN}; border: 1px solid {_DIM};
    padding: 3px 8px;
}}
"""


def _field(label: str, placeholder: str = "", password: bool = False,
           tooltip: str = "") -> tuple[QLabel, QLineEdit]:
    lbl = QLabel(label)
    lbl.setStyleSheet(f"color: {_DIM}; font-size: 10px; letter-spacing: 1px;")
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    if password:
        edit.setEchoMode(QLineEdit.Password)
    if tooltip:
        edit.setToolTip(tooltip)
    return lbl, edit


class SettingsPanel(QWidget):
    """Credentials and config panel. Emits saved() when settings are written."""

    saved = Signal()

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = config
        self.setStyleSheet(_BASE_STYLE)
        self._build_ui()
        if config:
            self._load_from_config(config)

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {_BG}; }}")

        inner = QWidget()
        inner.setStyleSheet(f"background: {_BG};")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────
        hdr = QLabel("◈ DATA SOURCES & CREDENTIALS")
        hdr.setStyleSheet(
            f"color: {_CYAN}; font-size: 15px; font-weight: bold; "
            f"letter-spacing: 4px; border-bottom: 1px solid {_DIM}; padding-bottom: 8px;"
        )
        layout.addWidget(hdr)

        note = QLabel(
            "Changes take effect on restart.  "
            "Credentials stored locally in var/mes_intel/config.json"
        )
        note.setStyleSheet(f"color: {_DIM}; font-size: 9px; letter-spacing: 1px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        # ── Rithmic ───────────────────────────────────────────────────
        rith = QGroupBox("RITHMIC  (MES/ES live ticks via AMP Futures)")
        rith_form = QFormLayout(rith)
        rith_form.setSpacing(8)

        _, self.rith_user = _field("Username", "your@email.com",
            tooltip="AMP Futures login email")
        _, self.rith_pass = _field("Password", "••••••••", password=True,
            tooltip="AMP Futures / Rithmic password")

        self.rith_system = QComboBox()
        self.rith_system.addItems([
            "Rithmic Paper Trading",
            "Rithmic 01",
            "Rithmic Test",
        ])
        self.rith_system.setStyleSheet(
            f"background: #0c0c18; color: {_GREEN}; border: 1px solid {_DIM}; padding: 3px 8px;"
        )

        _, self.rith_account = _field("Account ID", "optional",
            tooltip="Your AMP account ID (optional)")

        rith_form.addRow("Username:", self.rith_user)
        rith_form.addRow("Password:", self.rith_pass)
        rith_form.addRow("System:", self.rith_system)
        rith_form.addRow("Account ID:", self.rith_account)

        rith_note = QLabel(
            "ℹ  Requires rapi Python package from Rithmic/AMP.\n"
            "   Email support@ampfutures.com and request the 'Rithmic R|API Python package'.\n"
            "   Install with: pip install rapi-x.x.x.whl"
        )
        rith_note.setStyleSheet(f"color: {_AMBER}; font-size: 9px; padding: 4px 0;")
        rith_note.setWordWrap(True)
        rith_form.addRow(rith_note)
        layout.addWidget(rith)

        # ── Alpaca ────────────────────────────────────────────────────
        alp = QGroupBox("ALPACA MARKETS  (SPY/QQQ/GLD/TLT real-time quotes — FREE)")
        alp_form = QFormLayout(alp)
        alp_form.setSpacing(8)

        _, self.alp_key = _field("API Key", "PK...",
            tooltip="Alpaca API key (free at alpaca.markets)")
        _, self.alp_secret = _field("API Secret", "••••••••", password=True,
            tooltip="Alpaca API secret")

        self.alp_feed = QComboBox()
        self.alp_feed.addItems(["iex", "sip"])
        self.alp_feed.setStyleSheet(
            f"background: #0c0c18; color: {_GREEN}; border: 1px solid {_DIM}; padding: 3px 8px;"
        )
        self.alp_feed.setToolTip("iex = free real-time, sip = paid consolidated tape")

        self.alp_enabled = QCheckBox("Enable Alpaca feed")
        self.alp_enabled.setChecked(True)

        alp_form.addRow("API Key:", self.alp_key)
        alp_form.addRow("API Secret:", self.alp_secret)
        alp_form.addRow("Feed:", self.alp_feed)
        alp_form.addRow("", self.alp_enabled)

        alp_note = QLabel(
            "ℹ  Free account at alpaca.markets → Your Account → API Keys.\n"
            "   IEX feed: real-time US stocks/ETFs at no cost.\n"
            "   Replaces 15-min delayed yfinance data for SPY, QQQ, GLD, TLT, HYG, VXX..."
        )
        alp_note.setStyleSheet(f"color: {_GREEN}; font-size: 9px; padding: 4px 0;")
        alp_note.setWordWrap(True)
        alp_form.addRow(alp_note)
        layout.addWidget(alp)

        # ── Finnhub ───────────────────────────────────────────────────
        fh = QGroupBox("FINNHUB  (news, dark pool, sentiment)")
        fh_form = QFormLayout(fh)
        fh_form.setSpacing(8)

        _, self.fh_key = _field("API Key", "free at finnhub.io",
            tooltip="Finnhub API key — free tier covers news + dark pool")

        fh_form.addRow("API Key:", self.fh_key)

        fh_note = QLabel("ℹ  Free at finnhub.io — used for dark pool prints, news, insider activity.")
        fh_note.setStyleSheet(f"color: {_DIM}; font-size: 9px; padding: 4px 0;")
        fh_note.setWordWrap(True)
        fh_form.addRow(fh_note)
        layout.addWidget(fh)

        # ── ATAS ──────────────────────────────────────────────────────
        atas = QGroupBox("ATAS  (footprint + order flow CSV bridge)")
        atas_form = QFormLayout(atas)
        atas_form.setSpacing(8)

        _, self.atas_dir = _field(
            "CSV Export Dir",
            "/path/to/ATAS/export",
            tooltip="ATAS → Settings → Export → set export folder here",
        )
        atas_form.addRow("Export Dir:", self.atas_dir)

        atas_note = QLabel(
            "ℹ  In ATAS: Settings → Data Export → enable cluster CSV export.\n"
            "   Point this to that folder. Files are picked up automatically."
        )
        atas_note.setStyleSheet(f"color: {_DIM}; font-size: 9px; padding: 4px 0;")
        atas_note.setWordWrap(True)
        atas_form.addRow(atas_note)
        layout.addWidget(atas)

        # ── Anthropic AI Assistant ────────────────────────────────────
        ai = QGroupBox("ANTHROPIC  (AI ASSISTANT — Claude API)")
        ai_form = QFormLayout(ai)
        ai_form.setSpacing(8)

        _, self.anthropic_key = _field(
            "API Key", "sk-ant-...", password=True,
            tooltip="Anthropic API key — get one at console.anthropic.com"
        )

        ai_form.addRow("API Key:", self.anthropic_key)

        self.anthropic_bypass = QCheckBox("Enable bypass mode")
        self.anthropic_bypass.setToolTip(
            "When enabled, AI assistant will skip tool-based actions and require explicit bypass confirmation."
        )
        self.anthropic_bypass.setStyleSheet(f"color: {_DIM}; font-size: 10px;")
        ai_form.addRow(self.anthropic_bypass)

        ai_note = QLabel(
            "ℹ  Free API key at console.anthropic.com → API Keys.\n"
            "   Powers the AI ASSISTANT tab — ask questions about your trades,\n"
            "   strategies, signals, and agent performance in plain English."
        )
        ai_note.setStyleSheet(f"color: {_CYAN}; font-size: 9px; padding: 4px 0;")
        ai_note.setWordWrap(True)
        ai_form.addRow(ai_note)
        layout.addWidget(ai)

        # ── Save button ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("◈ SAVE  (restart to apply)")
        save_btn.setStyleSheet(
            f"background: #0a1a0a; color: {_GREEN}; border: 1px solid {_GREEN}; "
            f"padding: 8px 28px; font-size: 12px; letter-spacing: 2px;"
        )
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 10px; letter-spacing: 1px;")
        layout.addWidget(self._status_lbl)

        layout.addStretch()
        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _load_from_config(self, config):
        try:
            self.rith_user.setText(config.rithmic.user or "")
            self.rith_pass.setText(config.rithmic.password or "")
            idx = self.rith_system.findText(config.rithmic.system)
            if idx >= 0:
                self.rith_system.setCurrentIndex(idx)
            self.rith_account.setText(config.rithmic.account_id or "")

            self.alp_key.setText(config.alpaca.api_key or "")
            self.alp_secret.setText(config.alpaca.api_secret or "")
            idx2 = self.alp_feed.findText(config.alpaca.feed)
            if idx2 >= 0:
                self.alp_feed.setCurrentIndex(idx2)
            self.alp_enabled.setChecked(config.alpaca.enabled)

            self.fh_key.setText(config.news.finnhub_key or "")
            self.atas_dir.setText(config.atas.csv_export_dir or "")
            self.anthropic_key.setText(getattr(config, "anthropic_api_key", "") or "")
            self.anthropic_bypass.setChecked(getattr(config, "anthropic_bypass_mode", False))
        except Exception as exc:
            log.debug("Settings load error: %s", exc)

    def _save(self):
        config_path = Path(__file__).parent.parent.parent / "var" / "mes_intel" / "config.json"
        try:
            existing = {}
            if config_path.exists():
                existing = json.loads(config_path.read_text())

            # Patch only the fields we manage
            existing.setdefault("rithmic", {})
            existing["rithmic"]["user"] = self.rith_user.text().strip()
            existing["rithmic"]["password"] = self.rith_pass.text()
            existing["rithmic"]["system"] = self.rith_system.currentText()
            existing["rithmic"]["account_id"] = self.rith_account.text().strip()

            existing.setdefault("alpaca", {})
            existing["alpaca"]["api_key"] = self.alp_key.text().strip()
            existing["alpaca"]["api_secret"] = self.alp_secret.text()
            existing["alpaca"]["feed"] = self.alp_feed.currentText()
            existing["alpaca"]["enabled"] = self.alp_enabled.isChecked()

            existing.setdefault("news", {})
            existing["news"]["finnhub_key"] = self.fh_key.text().strip()

            existing.setdefault("dark_pool", {})
            existing["dark_pool"]["finnhub_key"] = self.fh_key.text().strip()

            existing.setdefault("atas", {})
            existing["atas"]["csv_export_dir"] = self.atas_dir.text().strip()

            existing["anthropic_api_key"] = self.anthropic_key.text().strip()
            existing["anthropic_bypass_mode"] = self.anthropic_bypass.isChecked()

            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(existing, indent=2))

            self._status_lbl.setText("✓ SAVED — restart app to apply changes")
            self._status_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 10px;")
            log.info("Settings saved to %s", config_path)
            self.saved.emit()

        except Exception as exc:
            self._status_lbl.setText(f"ERROR: {exc}")
            self._status_lbl.setStyleSheet(f"color: {_RED}; font-size: 10px;")
            log.exception("Settings save failed")


class AppOptimizerPanel(QWidget):
    """App Optimizer panel — shows usage analytics and pending suggestions."""

    def __init__(self, app_optimizer=None, bus=None, parent=None):
        super().__init__(parent)
        self._optimizer = app_optimizer
        self._bus = bus
        self.setStyleSheet(_BASE_STYLE)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header
        hdr = QLabel("◈ APP OPTIMIZER — USAGE ANALYTICS & AUTO-TUNING")
        hdr.setStyleSheet(f"color: {_CYAN}; font-size: 12px; font-weight: bold; letter-spacing: 2px;")
        layout.addWidget(hdr)

        # Suggestions group
        sug_box = QGroupBox("PENDING SUGGESTIONS")
        sug_layout = QVBoxLayout(sug_box)
        self._suggestions_text = QTextEdit()
        self._suggestions_text.setReadOnly(True)
        self._suggestions_text.setMinimumHeight(160)
        self._suggestions_text.setStyleSheet(
            f"background: #050508; color: {_AMBER}; border: 1px solid #222244; "
            f"font-family: 'Courier New'; font-size: 10px;"
        )
        sug_layout.addWidget(self._suggestions_text)

        btn_row = QHBoxLayout()
        self._approve_btn = QPushButton("✓ APPROVE ALL")
        self._approve_btn.setStyleSheet(
            f"QPushButton {{ background: #001a00; color: {_GREEN}; border: 1px solid {_GREEN}; "
            f"padding: 4px 12px; font-size: 10px; }} "
            f"QPushButton:hover {{ background: #003300; }}"
        )
        self._approve_btn.clicked.connect(self._approve_all)

        self._reject_btn = QPushButton("✗ REJECT ALL")
        self._reject_btn.setStyleSheet(
            f"QPushButton {{ background: #1a0000; color: {_RED}; border: 1px solid {_RED}; "
            f"padding: 4px 12px; font-size: 10px; }} "
            f"QPushButton:hover {{ background: #330000; }}"
        )
        self._reject_btn.clicked.connect(self._reject_all)

        self._refresh_btn = QPushButton("↻ REFRESH")
        self._refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self._approve_btn)
        btn_row.addWidget(self._reject_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._refresh_btn)
        sug_layout.addLayout(btn_row)
        layout.addWidget(sug_box)

        # Tab usage group
        tab_box = QGroupBox("TAB USAGE ANALYTICS")
        tab_layout = QVBoxLayout(tab_box)
        self._tab_stats_text = QTextEdit()
        self._tab_stats_text.setReadOnly(True)
        self._tab_stats_text.setMaximumHeight(140)
        self._tab_stats_text.setStyleSheet(
            f"background: #050508; color: {_TEXT}; border: 1px solid #222244; "
            f"font-family: 'Courier New'; font-size: 10px;"
        )
        tab_layout.addWidget(self._tab_stats_text)
        layout.addWidget(tab_box)

        # Signal engagement group
        sig_box = QGroupBox("SIGNAL ENGAGEMENT")
        sig_layout = QVBoxLayout(sig_box)
        self._sig_stats_text = QTextEdit()
        self._sig_stats_text.setReadOnly(True)
        self._sig_stats_text.setMaximumHeight(120)
        self._sig_stats_text.setStyleSheet(
            f"background: #050508; color: {_TEXT}; border: 1px solid #222244; "
            f"font-family: 'Courier New'; font-size: 10px;"
        )
        sig_layout.addWidget(self._sig_stats_text)
        layout.addWidget(sig_box)

        layout.addStretch()
        self.refresh()

    def refresh(self):
        """Reload data from optimizer."""
        if self._optimizer is None:
            self._suggestions_text.setPlainText("No optimizer connected.")
            return

        try:
            # Suggestions
            pending = self._optimizer.get_suggestions("pending")
            if pending:
                lines = []
                for s in pending:
                    conf_pct = int(s["confidence"] * 100)
                    lines.append(
                        f"[{s['category'].upper()}] {s['description']}\n"
                        f"  → {s['rationale']}\n"
                        f"  Confidence: {conf_pct}%  |  ID: {s['id']}\n"
                    )
                self._suggestions_text.setPlainText("\n".join(lines))
            else:
                self._suggestions_text.setPlainText(
                    "No pending suggestions yet.\n"
                    "Use the app for a while and the optimizer will start learning your patterns."
                )

            # Tab stats
            tab_stats = self._optimizer.get_tab_summary()
            if tab_stats:
                rows = [f"{'TAB':<22} {'VISITS':>6}  {'TOTAL MIN':>9}  {'AVG SEC':>8}"]
                rows.append("─" * 50)
                for s in tab_stats[:10]:
                    rows.append(
                        f"{s['tab']:<22} {s['visits']:>6}  {s['total_min']:>9.1f}  {s['avg_sec']:>8.0f}"
                    )
                self._tab_stats_text.setPlainText("\n".join(rows))
            else:
                self._tab_stats_text.setPlainText("No tab usage data yet.")

            # Signal engagement
            sig_data = self._optimizer.get_signal_engagement()
            if sig_data:
                rows = [f"{'SIGNAL TYPE':<28} {'SHOWN':>6}  {'ACTED':>6}  {'ACT%':>6}"]
                rows.append("─" * 52)
                for s in sig_data[:8]:
                    rows.append(
                        f"{s['type']:<28} {s['shown']:>6}  {s['acted']:>6}  {s['act_rate_pct']:>6.1f}%"
                    )
                self._sig_stats_text.setPlainText("\n".join(rows))
            else:
                self._sig_stats_text.setPlainText("No signal engagement data yet.")
        except Exception as exc:
            log.warning("Optimizer panel refresh failed: %s", exc)

    def _approve_all(self):
        if self._optimizer is None:
            return
        try:
            pending = self._optimizer.get_suggestions("pending")
            for s in pending:
                self._optimizer.approve_suggestion(s["id"])
            self.refresh()
        except Exception as exc:
            log.warning("Approve all failed: %s", exc)

    def _reject_all(self):
        if self._optimizer is None:
            return
        try:
            pending = self._optimizer.get_suggestions("pending")
            for s in pending:
                self._optimizer.reject_suggestion(s["id"])
            self.refresh()
        except Exception as exc:
            log.warning("Reject all failed: %s", exc)
