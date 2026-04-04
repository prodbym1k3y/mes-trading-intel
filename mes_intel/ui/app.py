"""Main desktop application window.

Retro-animated terminal aesthetic with live signal dashboard,
dark pool, analytics, and vanity art.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QSplitter, QLabel, QStatusBar, QFrame,
    QApplication, QPushButton, QTextEdit, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeyEvent

from .theme import STYLESHEET, COLORS
from .widgets import (
    ScanlineOverlay, SignalPanel, StrategyScorecard,
    VolumeProfileWidget, DeltaProfileWidget,
    TradeTable, StatsPanel, NewsFeed, ConfidenceMeter,
    # Phase 2 widgets
    BigTradesWidget, InstitutionalFlowWidget,
    DOMImbalanceWidget, OrderFlowSummaryWidget,
)
from .analytics import AnalyticsDashboard
from .vanity.pixel_art import VanityManager
from .session_profiles import current_session

# Phase 3 — new modules
from .big_trades import (
    BigTradesWidget as BigTradesIndicatorWidget,
    BigTradesHeatmap, BigTradesStatsPanel,
    BigTradesEngine,
)
from .easter_eggs import EasterEggManager as EggManager
from .cross_asset_panel import CrossAssetPanel
from .settings_panel import SettingsPanel, AppOptimizerPanel

# Phase 5 — reactive effects + vanity sprites
from .reactive_fx import (
    ScrollingTicker, DeltaBar, WaveformBars, InfluxIndicator,
    NeonTabBar, BreathingBackground, ScanlineBorder,
)
from .vanity_sprites import create_vanity_sprites

# Phase 4 — cyberpunk enhancements
from .indicators_enhanced import (
    SignalsIndicatorStrip,
    JournalIndicatorStrip, MetaIndicatorStrip,
)
from .cyberpunk_fx import (
    MatrixGridBackground, GlitchOverlay, ParticleBurst, CRTIntensifier,
    AnimatedTabBar,
)
from .journal_enhanced import EnhancedJournalTab
from .signals_enhanced import EnhancedSignalsPanel
from .ai_chat import AIChatPanel
from .meta_ai_enhanced import MetaAIDashboard
from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType
from ..orderflow import VolumeProfile

log = logging.getLogger(__name__)


def _play_alert(sound_name: str = "Glass") -> None:
    """Play a system alert sound cross-platform. Fails silently."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["afplay", f"/System/Library/Sounds/{sound_name}.aiff"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, config: AppConfig, db: Database, bus: EventBus,
                 market_brain=None, app_optimizer=None, meta_learner=None):
        super().__init__()
        self.config = config
        self.db = db
        self.bus = bus

        # Phase 4 agents (optional — UI gracefully handles None)
        self._market_brain  = market_brain
        self._app_optimizer = app_optimizer
        self._meta_learner  = meta_learner

        self.setWindowTitle("▸▸ MES TRADING INTELLIGENCE // v4.0 NEON ◈◈")
        self.resize(config.window_width, config.window_height)
        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(1200, 700)

        # Phase 3: standalone big trades engine (shared across tabs)
        self._big_trades_engine = BigTradesEngine()

        # State tracking for enhanced status bar
        self._signal_count = 0
        self._last_pnl = 0.0
        self._last_price_time = 0.0
        self._active_agents = 7  # +2 for Phase 4

        # Tab change tracking for App Optimizer
        self._current_tab_name: str = ""

        self._build_ui()
        self._subscribe_events()
        self._start_timers()

        # Phase 4: cyberpunk overlays (stacked above content, below scanline)
        self._matrix_bg = MatrixGridBackground(self)
        self._matrix_bg.resize(self.size())
        self._matrix_bg.show()

        self._glitch = GlitchOverlay(self)
        self._glitch.resize(self.size())
        self._glitch.hide()

        self._particles = ParticleBurst(self)
        self._particles.resize(self.size())
        self._particles.hide()

        self._crt = CRTIntensifier(self)
        self._crt.resize(self.size())
        self._crt.show()

        # Breathing background — slow cyan-tinted pulse overlaid on window
        self._breathing = BreathingBackground(self)
        self._breathing.resize(self.size())
        self._breathing.show()

        # Tron-style light-trail borders on main structural panels
        try:
            self._tron_tabs = ScanlineBorder(self.tabs)
            self._tron_tabs.show()
            self._tron_tabs.raise_()
        except Exception:
            pass
        try:
            self._tron_header = ScanlineBorder(self._header_frame)
            self._tron_header.show()
            self._tron_header.raise_()
        except Exception:
            pass

        # Animated neon underline below the active tab
        try:
            self._animated_tab_underline = AnimatedTabBar(self.tabs)
            self._animated_tab_underline.setGeometry(
                0, self.tabs.tabBar().height() - 3,
                self.tabs.width(), 3,
            )
            self._animated_tab_underline.show()
            self._animated_tab_underline.raise_()
        except Exception:
            pass

        # CRT scanline overlay (always on top)
        self._scanline = ScanlineOverlay(self)
        self._scanline.raise_()

        # Easter eggs — use new full system
        self._egg_mgr = EggManager(self)
        # Keep legacy alias for old event handler calls
        self._eggs = self._egg_mgr

        # Vanity pixel art elements
        self._vanity = VanityManager(self)
        if config.ui_config.vanity_enabled:
            self._vanity.toggle()

        # Drug-themed vanity sprites (always visible, float on top)
        self._vanity_sprites = create_vanity_sprites(self)

        # Wire news feed right-click → snake game
        try:
            self.news_feed.setContextMenuPolicy(Qt.CustomContextMenu)
            self.news_feed.customContextMenuRequested.connect(
                lambda pos: self._egg_mgr.on_news_right_click()
            )
        except Exception:
            pass

        # Phase 2: track recent big trades for BigTradesWidget
        self._recent_big_trades: list[dict] = []
        self._recent_institutional: list[dict] = []

        # Wire meta_learner into the META-AI dashboard
        if self._meta_learner is not None:
            try:
                self.meta_ai_dashboard.set_meta_learner(self._meta_learner)
            except Exception:
                pass

        log.info("Main window Phase 2 initialized")

    def _build_ui(self):
        """Construct the full UI layout."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Top LED ticker strip — very top of window, always visible
        self._top_ticker = ScrollingTicker(height=16)
        main_layout.addWidget(self._top_ticker)

        # Header
        self._header_frame = self._build_header()
        main_layout.addWidget(self._header_frame)

        # Tab widget for main views — NeonTabBar for glow/pulse effect
        self.tabs = QTabWidget()
        self.tabs.setTabBar(NeonTabBar())
        main_layout.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_dashboard_tab(), "◈ SIGNALS")
        self.tabs.addTab(self._build_big_trades_tab(), "★ BIG TRADES")
        self.tabs.addTab(self._build_journal_tab(), "◆ JOURNAL")
        self.tabs.addTab(self._build_meta_tab(), "▸ META-AI")
        self.tabs.addTab(self._build_analytics_tab(), "◆ ANALYTICS")
        self.tabs.addTab(self._build_cross_asset_tab(), "⬡ INTEL")
        self.tabs.addTab(self._build_ai_assistant_tab(), "◈ AI ASSISTANT")
        self.tabs.addTab(self._build_settings_tab(), "⚙ SETTINGS")

        # Glitch on tab change
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setStyleSheet(
            f"background: {COLORS['bg_dark']}; "
            f"color: {COLORS['cyan']}; "
            f"border-top: 1px solid {COLORS['cyan_dim']}; "
            f"font-family: 'Courier New', monospace; font-size: 10px;"
        )
        self._ticker = ScrollingTicker()
        self._status_label = QLabel("◈ INITIALIZING SYSTEMS...")
        self._status_label.setStyleSheet(f"color: {COLORS['cyan']}; letter-spacing: 1px;")
        self._status_label.hide()  # ticker replaces visible label
        self._clock_label = QLabel("")
        self._clock_label.setStyleSheet(
            f"color: {COLORS['magenta']}; font-weight: bold; font-size: 12px; "
            f"letter-spacing: 2px; padding: 0 8px; border-left: 1px solid {COLORS['cyan_dim']};"
        )
        self._clock_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clock_label.setToolTip("Shift+click = DJ Mode")
        self._clock_label.mousePressEvent = self._on_clock_click
        # Session label in status bar
        self._session_label = QLabel("SESSION: --")
        self._session_label.setStyleSheet(
            f"color: {COLORS['green_bright']}; font-size: 10px; "
            f"padding: 0 8px; border-left: 1px solid {COLORS['cyan_dim']};"
        )
        self.status_bar.addWidget(self._ticker, 1)
        self.status_bar.addWidget(self._status_label)
        self.status_bar.addPermanentWidget(self._session_label)

        # Neon status indicators — Phoenix time, agents, signals, P&L, connection
        _sb_base = (
            f"font-size: 10px; font-family: 'Courier New', monospace; "
            f"padding: 0 6px; border-left: 1px solid {COLORS['cyan_dim']};"
        )
        self._phx_time_label = QLabel("PHX --:--:--")
        self._phx_time_label.setStyleSheet(f"color: {COLORS['cyan']}; " + _sb_base)

        self._agents_label = QLabel("AGENTS: --")
        self._agents_label.setStyleSheet(f"color: {COLORS['magenta']}; " + _sb_base)

        self._signals_count_label = QLabel("SIGS: 0")
        self._signals_count_label.setStyleSheet(f"color: {COLORS['green_bright']}; " + _sb_base)

        self._pnl_status_label = QLabel("P&L: --")
        self._pnl_status_label.setStyleSheet(f"color: {COLORS['text_muted']}; " + _sb_base)

        self._cum_pnl_label = QLabel("Σ $0.00")
        self._cum_pnl_label.setStyleSheet(f"color: {COLORS['text_muted']}; " + _sb_base)
        self._cum_pnl_label.setToolTip("Cumulative session P&L")

        self._conn_label = QLabel("● OFFLINE")
        self._conn_label.setStyleSheet(
            f"color: {COLORS['pink']}; font-weight: bold; " + _sb_base
        )

        self.status_bar.addPermanentWidget(self._phx_time_label)
        self.status_bar.addPermanentWidget(self._agents_label)
        self.status_bar.addPermanentWidget(self._signals_count_label)
        self.status_bar.addPermanentWidget(self._pnl_status_label)
        self.status_bar.addPermanentWidget(self._cum_pnl_label)
        self.status_bar.addPermanentWidget(self._conn_label)
        self.status_bar.addPermanentWidget(self._clock_label)

    def _build_header(self) -> QFrame:
        """Top bar — neon command header with live price display."""
        frame = QFrame()
        frame.setObjectName("panel")
        frame.setFixedHeight(64)
        frame.setStyleSheet(
            f"QFrame#panel {{ "
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"  stop:0 #050508, stop:0.4 #080810, stop:1 #050508); "
            f"border: 0px; border-bottom: 2px solid {COLORS['cyan_dim']}; }}"
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(16)

        # ── Logo / title ──────────────────────────────────────────
        title_col = QVBoxLayout()
        title_col.setSpacing(0)

        title = QLabel("▸▸ MES INTEL")
        title.setStyleSheet(
            f"font-size: 22px; font-weight: bold; color: {COLORS['cyan']}; "
            f"letter-spacing: 6px; font-family: 'Courier New', monospace; "
            f"background: transparent;"
        )
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.mousePressEvent = lambda e: self._eggs.on_logo_click()
        title_col.addWidget(title)

        subtitle = QLabel("TRADING INTELLIGENCE SYSTEM // v4.0")
        subtitle.setStyleSheet(
            f"font-size: 9px; color: {COLORS['magenta']}; letter-spacing: 3px; "
            f"font-family: 'Courier New', monospace; background: transparent;"
        )
        title_col.addWidget(subtitle)
        layout.addLayout(title_col)

        # ── Vertical separator ────────────────────────────────────
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet(f"color: {COLORS['cyan_dim']}; background: {COLORS['cyan_dim']}; max-width: 1px;")
        layout.addWidget(sep1)

        # ── Live price (big) ──────────────────────────────────────
        price_col = QVBoxLayout()
        price_col.setSpacing(1)

        price_lbl = QLabel("PRICE")
        price_lbl.setStyleSheet(
            f"font-size: 8px; color: {COLORS['text_muted']}; letter-spacing: 3px; "
            f"font-family: 'Courier New', monospace; background: transparent;"
        )
        price_col.addWidget(price_lbl)

        self.price_label = QLabel("MES: -.--")
        self.price_label.setStyleSheet(
            f"font-size: 28px; font-weight: bold; color: {COLORS['cyan']}; "
            f"letter-spacing: 2px; font-family: 'Courier New', monospace; "
            f"background: transparent;"
        )
        price_col.addWidget(self.price_label)
        layout.addLayout(price_col)

        # ── Waveform bars (trade velocity visualizer) ─────────────
        self._waveform = WaveformBars()
        layout.addWidget(self._waveform)

        # ── Change ────────────────────────────────────────────────
        self.change_label = QLabel("+0.00  (0.00%)")
        self.change_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {COLORS['text_muted']}; "
            f"font-family: 'Courier New', monospace; background: transparent;"
        )
        layout.addWidget(self.change_label)

        layout.addStretch()

        # ── Delta ─────────────────────────────────────────────────
        delta_col = QVBoxLayout()
        delta_col.setSpacing(1)
        d_lbl = QLabel("SESSION DELTA")
        d_lbl.setStyleSheet(
            f"font-size: 8px; color: {COLORS['text_muted']}; letter-spacing: 2px; "
            f"font-family: 'Courier New', monospace; background: transparent;"
        )
        delta_col.addWidget(d_lbl)
        self.session_delta_label = QLabel("0")
        self.session_delta_label.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {COLORS['green_bright']}; "
            f"font-family: 'Courier New', monospace; background: transparent;"
        )
        delta_col.addWidget(self.session_delta_label)

        # Delta bar under session delta label
        self._delta_bar = DeltaBar()
        delta_col.addWidget(self._delta_bar)

        layout.addLayout(delta_col)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {COLORS['cyan_dim']}; background: {COLORS['cyan_dim']}; max-width: 1px;")
        layout.addWidget(sep2)

        # ── Regime badge ──────────────────────────────────────────
        self.regime_badge = QLabel("REGIME: --")
        self.regime_badge.setStyleSheet(
            f"font-size: 11px; font-weight: bold; color: {COLORS['orange']}; "
            f"border: 1px solid {COLORS['orange_dim']}; "
            f"padding: 4px 12px; letter-spacing: 2px; "
            f"font-family: 'Courier New', monospace; background: transparent;"
        )
        layout.addWidget(self.regime_badge)

        # ── Influx indicator ──────────────────────────────────────
        self._influx = InfluxIndicator()
        layout.addWidget(self._influx)

        return frame

    @staticmethod
    def _make_tab_header(icon: str, title: str, subtitle: str = "") -> QFrame:
        """Neon section header bar for each tab."""
        frame = QFrame()
        frame.setFixedHeight(28)
        frame.setStyleSheet(
            f"background: {COLORS['bg_card']}; "
            f"border-bottom: 1px solid {COLORS['cyan_dim']}; "
            f"border-top: 0px; border-left: 0px; border-right: 0px;"
        )
        row = QHBoxLayout(frame)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(8)
        lbl = QLabel(f"{icon} {title}")
        lbl.setStyleSheet(
            f"color: {COLORS['cyan']}; font-size: 11px; font-weight: bold; "
            f"letter-spacing: 4px; font-family: 'Courier New', monospace; "
            f"background: transparent;"
        )
        row.addWidget(lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet(
                f"color: {COLORS['text_muted']}; font-size: 9px; "
                f"letter-spacing: 2px; font-family: 'Courier New', monospace; "
                f"background: transparent;"
            )
            row.addWidget(sub)
        row.addStretch()
        # right-side decoration
        deco = QLabel("━━━━━━━━━━━━━━━━━━━━━━━━━━")
        deco.setStyleSheet(
            f"color: {COLORS['cyan_dim']}; font-size: 10px; background: transparent;"
        )
        row.addWidget(deco)
        return frame

    def _build_dashboard_tab(self) -> QWidget:
        """Enhanced Signal Dashboard — multi-agent confluence signal feed."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(4, 0, 4, 4)
        outer.setSpacing(2)

        outer.addWidget(self._make_tab_header(
            "◈", "SIGNAL DASHBOARD", "MULTI-AGENT CONFLUENCE INTELLIGENCE"
        ))

        # Enhanced signals panel (new main content)
        self.enhanced_signals = EnhancedSignalsPanel(bus=self.bus)
        outer.addWidget(self.enhanced_signals, 1)

        # Phase 4: indicator strip at bottom of SIGNALS tab
        self.signals_indicators = SignalsIndicatorStrip()
        outer.addWidget(self.signals_indicators)

        # ── Hidden compat widgets (referenced by existing event handlers) ──
        _hidden = QWidget()
        _hidden.setVisible(False)
        _hl = QVBoxLayout(_hidden)
        self.signal_panel = SignalPanel()
        _hl.addWidget(self.signal_panel)
        self.scorecard = StrategyScorecard()
        _hl.addWidget(self.scorecard)
        self.mini_volume_profile = VolumeProfileWidget()
        _hl.addWidget(self.mini_volume_profile)
        self.mini_delta_profile = DeltaProfileWidget()
        _hl.addWidget(self.mini_delta_profile)
        self.news_feed = NewsFeed()
        _hl.addWidget(self.news_feed)
        self.stats_panel = StatsPanel()
        _hl.addWidget(self.stats_panel)
        outer.addWidget(_hidden)

        return tab

    def _build_big_trades_tab(self) -> QWidget:
        """Phase 3: Full big trades indicator — dot chart, heatmap, stats."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._make_tab_header("★", "BIG TRADES RADAR", "INSTITUTIONAL FLOW DETECTION"))

        # Top row: dot chart (left) + stats (right)
        top_row = QSplitter(Qt.Orientation.Horizontal)

        # Dot chart panel
        dot_frame = QFrame()
        dot_frame.setObjectName("panel")
        dot_layout = QVBoxLayout(dot_frame)
        dot_title = QLabel("★ BIG TRADES — PRICE × TIME")
        dot_title.setObjectName("subtitle")
        dot_layout.addWidget(dot_title)
        self.big_trades_indicator = BigTradesIndicatorWidget()
        self.big_trades_indicator.engine = self._big_trades_engine
        dot_layout.addWidget(self.big_trades_indicator, 1)
        top_row.addWidget(dot_frame)

        # Stats panel
        self.big_trades_stats = BigTradesStatsPanel(self._big_trades_engine)
        self.big_trades_stats.setFixedWidth(220)
        top_row.addWidget(self.big_trades_stats)
        top_row.setSizes([700, 220])
        layout.addWidget(top_row, 3)

        # Bottom: heatmap
        hm_frame = QFrame()
        hm_frame.setObjectName("panel")
        hm_layout = QVBoxLayout(hm_frame)
        hm_title = QLabel("★ BIG TRADES HEATMAP — TIME × PRICE")
        hm_title.setObjectName("subtitle")
        hm_layout.addWidget(hm_title)
        self.big_trades_heatmap = BigTradesHeatmap(self._big_trades_engine)
        hm_layout.addWidget(self.big_trades_heatmap, 1)
        layout.addWidget(hm_frame, 2)

        return tab

    def _build_journal_tab(self) -> QWidget:
        """Trade journal tab — full Tradezella-style AI journal (Phase 5)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._make_tab_header("◆", "TRADE JOURNAL", "AI-POWERED ANALYSIS"))

        # Stats strip at top
        self.journal_indicators = JournalIndicatorStrip()
        layout.addWidget(self.journal_indicators)

        # Enhanced Tradezella-style journal
        self.enhanced_journal = EnhancedJournalTab(db=self.db, bus=self.bus, config=self.config)
        layout.addWidget(self.enhanced_journal, 1)

        # Legacy placeholder — some event handlers reference trade_table
        from .widgets import TradeTable
        self.trade_table = TradeTable()
        self.trade_table.hide()

        return tab

    def _build_meta_tab(self) -> QWidget:
        """Meta-AI intelligence dashboard — Phase 5 enhanced."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._make_tab_header(
            "▸", "META-AI ENGINE",
            "8 AGENTS · TEAM IQ · REGIME MATRIX · LEARNING TIMELINE"
        ))

        # Phase 4: visual indicator strip still at top
        self.meta_indicators = MetaIndicatorStrip()
        layout.addWidget(self.meta_indicators)

        # Phase 5: full enhanced dashboard
        self.meta_ai_dashboard = MetaAIDashboard(db=self.db)
        layout.addWidget(self.meta_ai_dashboard, 1)

        # Keep legacy aliases so old event handlers still work
        self.post_mortem_display = QTextEdit()
        self.post_mortem_display.hide()
        self.rl_scorecard_display = QTextEdit()
        self.rl_scorecard_display.hide()

        return tab

    def _build_analytics_tab(self) -> QWidget:
        """Analytics tab — equity curve, strategy performance, ML learning, correlations."""
        self.analytics_panel = AnalyticsDashboard(db=self.db)
        # Load initial data from database
        self._refresh_analytics()
        return self.analytics_panel

    def _build_ai_assistant_tab(self) -> QWidget:
        """AI Assistant chat interface powered by Claude API."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._ai_chat = AIChatPanel(
            db_path=self.db.db_path,
            config=self.config,
            parent=tab,
        )
        # Wire settings save → refresh API key in chat panel
        layout.addWidget(self._ai_chat)
        return tab

    def _build_settings_tab(self) -> QWidget:
        from PySide6.QtWidgets import QTabWidget
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)

        inner_tabs = QTabWidget()
        inner_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #222244; } "
            "QTabBar::tab { background: #0a0a18; color: #445566; "
            "  padding: 4px 12px; font-family: 'Courier New'; font-size: 10px; "
            "  letter-spacing: 1px; } "
            "QTabBar::tab:selected { color: #00d4ff; border-bottom: 2px solid #00d4ff; } "
        )

        self.settings_panel = SettingsPanel(config=self.config)
        self.settings_panel.saved.connect(self._on_settings_saved)
        inner_tabs.addTab(self.settings_panel, "⚙ CONFIG")

        # App Optimizer panel
        self._optimizer_panel = AppOptimizerPanel(
            app_optimizer=self._app_optimizer, bus=self.bus
        )
        inner_tabs.addTab(self._optimizer_panel, "◈ OPTIMIZER")

        layout.addWidget(inner_tabs)
        return tab

    def _build_cross_asset_tab(self) -> QWidget:
        """Phase 3: Cross-asset intelligence — real-time correlated assets + GEX."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._make_tab_header(
            "⬡", "CROSS-ASSET INTEL",
            "VIX · DXY · YIELDS · GOLD · OIL · NQ · GAMMA EXPOSURE"
        ))
        self.cross_asset_panel = CrossAssetPanel()
        layout.addWidget(self.cross_asset_panel, 1)
        return tab

    def _subscribe_events(self):
        """Subscribe to event bus for live updates."""
        self.bus.subscribe(EventType.SIGNAL_GENERATED, self._on_signal)
        self.bus.subscribe(EventType.ENSEMBLE_UPDATE, self._on_ensemble_update)
        self.bus.subscribe(EventType.TRADE_CLOSED, self._on_trade_closed)
        self.bus.subscribe(EventType.DAILY_STATS_UPDATE, self._on_stats_update)
        self.bus.subscribe(EventType.NEWS_ALERT, self._on_news)
        self.bus.subscribe(EventType.TRUMP_ALERT, self._on_news)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.OPTIMIZATION_SUGGESTION, self._on_optimization_suggestion)
        self.bus.subscribe(EventType.REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.VOLUME_PROFILE_UPDATE, self._on_volume_profile)
        self.bus.subscribe(EventType.PRICE_UPDATE, self._on_price)
        # Phase 2
        self.bus.subscribe(EventType.DARK_POOL_ALERT, self._on_dark_pool)
        self.bus.subscribe(EventType.BREAKING_NEWS, self._on_breaking_news)
        self.bus.subscribe(EventType.BIG_TRADE_ALERT, self._on_big_trade)
        self.bus.subscribe(EventType.CONFLUENCE_ALERT, self._on_confluence)
        self.bus.subscribe(EventType.VANITY_TOGGLE, self._on_vanity_toggle)
        self.bus.subscribe(EventType.PERFORMANCE_REPORT, self._on_performance_report)
        # Phase 3: cross-asset + options
        try:
            self.bus.subscribe(EventType.CROSS_ASSET_UPDATE, self._on_cross_asset_update)
            self.bus.subscribe(EventType.OPTIONS_DATA_UPDATE, self._on_cross_asset_update)
        except Exception:
            pass

    def _start_timers(self):
        """Start UI refresh timers."""
        # Clock
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)

        # Trade table refresh
        self._table_timer = QTimer(self)
        self._table_timer.timeout.connect(self._refresh_trades)
        self._table_timer.start(5000)

        # Analytics refresh (every 30 seconds)
        self._analytics_timer = QTimer(self)
        self._analytics_timer.timeout.connect(self._refresh_analytics)
        self._analytics_timer.start(30000)

        self._update_clock()
        self._status_label.setText("◈ SYSTEMS ONLINE ▸ AWAITING MARKET DATA...")
        self._ticker.set_text("◈ MES INTEL v4.0 ONLINE  ◈  ORDER FLOW ARMED  ◈  SIGNAL ENGINE READY  ◈  HIGH CONFIDENCE ONLY  ◈  AWAITING MARKET DATA  ◈  ")

    def _update_clock(self):
        now = datetime.now()
        self._clock_label.setText(now.strftime("%H:%M:%S"))

        # Phoenix / Arizona time — MST (UTC-7), no DST ever
        try:
            phx = datetime.utcnow() - timedelta(hours=7)
            self._phx_time_label.setText(f"PHX {phx.strftime('%H:%M:%S')}")
        except Exception:
            pass

        # Connection status — LIVE if price arrived in last 10s
        try:
            _sb_base = (
                f"font-size: 10px; font-family: 'Courier New', monospace; "
                f"padding: 0 6px; border-left: 1px solid {COLORS['cyan_dim']};"
            )
            if time.time() - self._last_price_time < 10:
                self._conn_label.setText("● LIVE")
                self._conn_label.setStyleSheet(
                    f"color: {COLORS['green_bright']}; font-weight: bold; " + _sb_base
                )
            elif self._last_price_time > 0:
                self._conn_label.setText("● OFFLINE")
                self._conn_label.setStyleSheet(
                    f"color: {COLORS['pink']}; font-weight: bold; " + _sb_base
                )
        except Exception:
            pass

        # Update session label in status bar
        try:
            sess = current_session()
            self._session_label.setText(f"SESSION: {sess}")
        except Exception:
            pass

    def _refresh_trades(self):
        trades = self.db.get_trades(limit=50)
        try:
            self.trade_table.load_trades(trades)
        except AttributeError:
            pass
        try:
            self.journal_layout.trade_list.refresh()
        except AttributeError:
            pass

    # --- Event handlers ---

    def _flash_signal_panel(self, color: str):
        """Briefly flash the signal panel border — visual alarm on new signal."""
        try:
            orig = self.signal_panel.styleSheet()
            self.signal_panel.setStyleSheet(
                f"border: 2px solid {color}; "
                f"background: rgba(0, 255, 65, 0.03);"
            )
            QTimer.singleShot(380, lambda: self.signal_panel.setStyleSheet(orig))
        except Exception:
            pass

    def _on_signal(self, event: Event):
        # Signal count + ticker
        self._signal_count += 1
        try:
            self._signals_count_label.setText(f"SIGS: {self._signal_count}")
        except Exception:
            pass

        self.signal_panel.update_signal(event.data)
        direction = event.data.get("direction", "FLAT")
        self._status_label.setText(
            f"◈ SIGNAL: {direction} @ "
            f"{event.data.get('entry', 0):.2f} | "
            f"CONF={event.data.get('confidence', 0):.0%}"
        )
        self._eggs.on_signal(direction)
        conf = event.data.get('confidence', 0)
        try:
            if conf > 0.8:
                self._eggs.on_high_confidence_signal(direction, conf)
        except Exception:
            pass

        # Phase 4: particle burst on signal
        signal_color = (COLORS["long_color"] if direction == "LONG"
                        else COLORS["short_color"] if direction == "SHORT"
                        else COLORS["amber"])
        try:
            cx = self.width() // 2
            cy = self.height() // 3
            self._particles.fire(cx, cy, signal_color, count=80)
        except Exception:
            pass

        # Signal flash: border glow on signal panel + top ticker update
        self._flash_signal_panel(signal_color)
        try:
            self._top_ticker.append_message(
                f"SIGNAL {direction} @ {event.data.get('entry', 0):.2f}  "
                f"CONF={conf:.0%}"
            )
        except Exception:
            pass

        # Sound alert on high-confidence signals
        if conf > 0.75 and self.config.ui_config.sound_enabled:
            _play_alert("Glass")

    def _on_ensemble_update(self, event: Event):
        scores = {}
        for name in event.data.get("scores", {}):
            scores[name] = {
                "score": event.data["scores"][name],
                "confidence": event.data.get("confidences", {}).get(name, 0),
                "direction": event.data.get("directions", {}).get(name, "FLAT"),
            }
        self.scorecard.update_scores(scores)
        # Update live agent count
        try:
            n = len(event.data.get("scores", {}))
            if n:
                self._active_agents = n
                self._agents_label.setText(f"AGENTS: {n}")
        except Exception:
            pass

        regime = event.data.get("regime", "unknown")
        self.regime_badge.setText(f"REGIME: {regime.upper()}")

        # Phase 4: feed signals indicator strip with strategy-derived values
        try:
            raw_scores = event.data.get("scores", {})
            # Use momentum strategy score as RSI proxy (normalized 0-100)
            mom = raw_scores.get("momentum", 0)
            rsi_proxy = 50 + mom * 45  # -1..+1 → 5..95
            self.signals_indicators.rsi.set_value(rsi_proxy)

            # mean_reversion score → ADX proxy
            mr = abs(raw_scores.get("mean_reversion", 0))
            self.signals_indicators.adx.set_value(mr * 90)

            # ensemble confidence → BB width proxy
            conf = event.data.get("confidence", 0.5)
            self.signals_indicators.bb_width.set_value(conf * 5)

            # order_flow score → stochastic
            of = raw_scores.get("order_flow", 0)
            k = 50 + of * 45
            d = k * 0.9 + 5
            self.signals_indicators.stoch.update_data(k, d)

            # ATR proxy from volatility (will be overwritten when real data arrives)
            self.signals_indicators.atr.set_value(event.data.get("atr", 2.5))

            # MACD from momentum + mean_reversion
            hist_val = mom * 0.5 - mr * 0.3
            self.signals_indicators.macd.update_data(hist_val, mom * 0.4, mr * 0.35)
        except Exception:
            pass

    def _on_trade_closed(self, event: Event):
        self._refresh_trades()
        pnl = event.data.get("pnl", 0)
        grade = event.data.get("grade", "-")
        self._status_label.setText(
            f"Trade closed: ${pnl:+.2f} | Grade: {grade}"
        )
        # Update P&L status bar label
        try:
            self._last_pnl = pnl
            _sb_base = (
                f"font-size: 10px; font-family: 'Courier New', monospace; "
                f"padding: 0 6px; border-left: 1px solid {COLORS['cyan_dim']};"
            )
            pnl_color = COLORS['green_bright'] if pnl >= 0 else COLORS['pink']
            self._pnl_status_label.setText(f"P&L: ${pnl:+.2f}")
            self._pnl_status_label.setStyleSheet(f"color: {pnl_color}; " + _sb_base)

            # Cumulative P&L
            self._cumulative_pnl = getattr(self, '_cumulative_pnl', 0.0) + pnl
            cum_color = COLORS['green_bright'] if self._cumulative_pnl >= 0 else COLORS['pink']
            self._cum_pnl_label.setText(f"Σ ${self._cumulative_pnl:+.2f}")
            self._cum_pnl_label.setStyleSheet(f"color: {cum_color}; " + _sb_base)
        except Exception:
            pass
        try:
            self._eggs.on_trade_closed(pnl)
        except AttributeError:
            pass
        # Phase 3 easter egg system
        try:
            is_win = pnl > 0
            self._egg_mgr.on_trade_result(is_win=is_win, pnl=pnl)
        except Exception:
            pass
        # Refresh charts after a trade closes
        QTimer.singleShot(500, self._refresh_analytics)

    def _on_stats_update(self, event: Event):
        self.stats_panel.update_stats(event.data)
        # Phase 4: feed journal indicator strip
        try:
            self.journal_indicators.update_stats(event.data)
        except Exception:
            pass

    def _on_news(self, event: Event):
        self.news_feed.add_news(
            event.data.get("headline", ""),
            event.data.get("sentiment_score", 0),
            event.data.get("is_trump", False),
        )

    def _on_regime_change(self, event: Event):
        regime = event.data.get("regime", "unknown")
        self.regime_badge.setText(f"REGIME: {regime.upper()}")

    def _on_volume_profile(self, event: Event):
        profile = event.data.get("profile")

        if isinstance(profile, VolumeProfile):
            self.mini_volume_profile.set_profile(profile)
            self.mini_delta_profile.set_profile(profile)

            self.session_delta_label.setText(f"Delta: {profile.cumulative_delta:+,}")
            try:
                self._delta_bar.set_delta(int(profile.cumulative_delta))
            except Exception:
                pass
            try:
                delta_rate = abs(profile.cumulative_delta)
                vol_rate = float(profile.total_volume)
                self._influx.push_delta(delta_rate, profile.cumulative_delta >= 0)
                self._influx.push_volume(vol_rate)
            except Exception:
                pass

    def _on_price(self, event: Event):
        self._last_price_time = time.time()  # heartbeat for connection status
        price = event.data.get("price")
        change = event.data.get("change", 0)
        change_pct = event.data.get("change_pct", 0)
        size   = event.data.get("size", 0)
        is_buy = event.data.get("is_buy", True)

        if price:
            self._last_price = float(price)
            self.price_label.setText(f"MES: {price:.2f}")
            sign = "+" if change >= 0 else ""
            self.change_label.setText(f"{sign}{change:.2f} ({sign}{change_pct:.2f}%)")
            color = COLORS["long_color"] if change >= 0 else COLORS["short_color"]
            self.change_label.setStyleSheet(f"font-size: 13px; color: {color};")

            # Feed live price to journal form's Exit @ Market button
            try:
                self.enhanced_journal._entry_form.set_live_price(float(price))
            except Exception:
                pass

            # Phase 4: volatility proxy drives matrix + border glow
            try:
                vol = min(abs(change_pct) / 0.5, 1.0)  # 0.5% move = full glow
                self._matrix_bg.set_volatility(vol)
            except Exception:
                pass

            try:
                self._eggs.update_market_data(price)
            except AttributeError:
                pass

            if size > 0:
                side = 'buy' if is_buy else 'sell'
                now = time.time()

                # Feed Phase 3 big trades engine + indicator widgets
                try:
                    trade = self._big_trades_engine.process_trade(now, price, size, side)
                    if trade.is_big:
                        self.big_trades_indicator.add_trade(now, price, size, side)
                except Exception:
                    pass

    # --- Phase 2 event handlers ---

    def _on_dark_pool(self, event: Event):
        notional = event.data.get("notional", 0)
        price = event.data.get("price", 0)
        self.news_feed.add_news(
            f"DARK POOL: ${notional/1e6:.1f}M @ {price:.2f}",
            sentiment=0.0, is_trump=False,
        )
        self._status_label.setText(
            f"DARK POOL ALERT: ${notional/1e6:.1f}M @ {price:.2f}"
        )

    def _on_breaking_news(self, event: Event):
        headline = event.data.get("headline", "")
        self.news_feed.add_news(headline, event.data.get("sentiment_score", 0),
                                event.data.get("is_trump", False))
        # Flash the status bar + push to ticker
        self._ticker.append_message(f"◈ BREAKING: {headline[:100]}")
        self._status_label.setText(f"BREAKING: {headline[:80]}")
        self._status_label.setStyleSheet(
            f"color: {event.data.get('flash_color', COLORS['red'])}; font-weight: bold;"
        )
        QTimer.singleShot(3000, lambda: self._status_label.setStyleSheet(
            f"color: {COLORS['text_muted']};"
        ))

    def _on_big_trade(self, event: Event):
        size = event.data.get("size", 0)
        price = event.data.get("price", 0)
        trade_type = event.data.get("type", "")
        self._status_label.setText(
            f"BIG TRADE: {size} lots @ {price:.2f} ({trade_type})"
        )
        if self.config.ui_config.sound_enabled:
            _play_alert("Submarine")
        # Update big trades widget
        self._recent_big_trades.append(event.data)
        if len(self._recent_big_trades) > 50:
            self._recent_big_trades = self._recent_big_trades[-50:]
        try:
            self.big_trades_widget.update_trades(self._recent_big_trades)
        except Exception:
            pass

    def _on_confluence(self, event: Event):
        price = event.data.get("price", 0)
        score = event.data.get("score", 0)
        zone_type = event.data.get("zone_type", "")
        self._status_label.setText(
            f"CONFLUENCE: {zone_type.upper()} @ {price:.2f} (score={score:.0%})"
        )

    def _on_vanity_toggle(self, event: Event):
        self._vanity.toggle()

    def _on_performance_report(self, event: Event):
        """Display meta-learner post-mortems and RL scorecards."""
        report_type = event.data.get("type", "")

        if report_type == "post_mortem":
            narrative = event.data.get("narrative", "")
            if narrative:
                try:
                    current = self.post_mortem_display.toPlainText()
                    sep = "\n" + "═" * 60 + "\n"
                    self.post_mortem_display.setPlainText(narrative + sep + current)
                    # Scroll to top to show latest
                    cursor = self.post_mortem_display.textCursor()
                    cursor.movePosition(cursor.MoveOperation.Start)
                    self.post_mortem_display.setTextCursor(cursor)
                except Exception:
                    pass
                # Also push to enhanced META-AI dashboard
                try:
                    self.meta_ai_dashboard.add_post_mortem(narrative)
                except Exception:
                    pass

        elif "strategy_accuracies" in event.data:
            # Team performance report — update RL scorecard
            accs = event.data.get("strategy_accuracies", {})
            rewards = event.data.get("strategy_avg_rewards", {})
            weights_data = event.data.get("strategy_weights", {})
            team_score = event.data.get("team_score", 0)
            trend = event.data.get("trend", "?")
            mvp = event.data.get("mvp_agent", "?")
            laggard = event.data.get("laggard_agent", "?")

            lines = [
                f"TEAM SCORE: {team_score:.1f} [{trend}]",
                f"MVP: {mvp.upper()} | LAGGARD: {laggard.upper()}",
                "",
                f"{'STRATEGY':<20} {'ACC':>6} {'REWARD':>8} {'WEIGHT':>8}",
                "-" * 46,
            ]
            for name in sorted(accs, key=lambda k: accs.get(k, 0), reverse=True):
                acc = accs.get(name, 0)
                rwd = rewards.get(name, 0)
                wgt = weights_data.get(name, 1.0)
                lines.append(f"{name:<20} {acc:>5.1%} {rwd:>+8.3f} {wgt:>8.3f}")

            try:
                self.rl_scorecard_display.setPlainText("\n".join(lines))
            except Exception:
                pass

            # Phase 4: feed meta indicator strip
            try:
                self.meta_indicators.team_score_trend.add_point(float(team_score))
                self.meta_indicators.weight_pie.set_weights(weights_data)
                agent_scores = {k: float(v) for k, v in accs.items()}
                self.meta_indicators.agent_bars.set_data(agent_scores)
                avg_acc = sum(accs.values()) / max(len(accs), 1)
                self.meta_indicators.accuracy_trend.add_point(avg_acc * 100)
            except Exception:
                pass

    def _on_cross_asset_update(self, event: Event):
        """Forward cross-asset / options data to the panel."""
        try:
            self.cross_asset_panel.update_data(event.data)
        except Exception:
            pass

    def _refresh_analytics(self):
        """Load analytics data from database and refresh analytics dashboard."""
        try:
            self.analytics_panel.refresh_all()
        except Exception:
            pass

    def _on_clock_click(self, event):
        """Shift+click clock → DJ mode toggle (easter_eggs.py)."""
        from PySide6.QtCore import Qt as _Qt
        if event.modifiers() & _Qt.KeyboardModifier.ShiftModifier:
            try:
                self._egg_mgr.on_time_shift_click()
            except Exception:
                pass

    def keyPressEvent(self, event: QKeyEvent):
        """Handle key presses for Konami code, vanity dance, and other easter eggs."""
        # Vanity Konami code check
        try:
            if self._vanity.handle_key(event.key()):
                return
        except Exception:
            pass
        try:
            if self._eggs._handle_key(event):
                return
        except Exception:
            pass
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        sz = self.size()
        self._scanline.resize(sz)
        self._scanline.raise_()
        try:
            self._matrix_bg.resize(sz)
            self._glitch.resize(sz)
            self._particles.resize(sz)
            self._crt.resize(sz)
            self._breathing.resize(sz)
        except Exception:
            pass
        try:
            self._eggs.resize_overlays(sz)
        except Exception:
            pass
        self._vanity.reposition()
        # Tron borders track their targets via their own timer — no manual resize needed
        # Raise them above newly-resized siblings
        try:
            self._tron_tabs.raise_()
            self._tron_header.raise_()
        except Exception:
            pass

    def _on_tab_changed(self, index: int):
        """Glitch flash on tab transition + App Optimizer tracking."""
        try:
            self._glitch.trigger()
        except Exception:
            pass
        # Track tab change for App Optimizer
        try:
            new_tab = self.tabs.tabText(index)
            if self._app_optimizer is not None:
                self._app_optimizer.record_tab_change(
                    from_tab=self._current_tab_name,
                    to_tab=new_tab,
                )
            self._current_tab_name = new_tab
        except Exception:
            pass


    def _on_regime_change(self, event: Event):
        """Flash status bar with new regime on Market Brain regime transitions."""
        try:
            regime = event.data.get("to_regime", "")
            conf   = event.data.get("confidence", 0.0)
            if regime:
                # Update status bar label if it exists
                try:
                    for child in self.status_bar.findChildren(QLabel):
                        if hasattr(child, '_is_regime_label'):
                            child.setText(f"REGIME: {regime.upper()}")
                except Exception:
                    pass
        except Exception:
            pass

    def _on_optimization_suggestion(self, event: Event):
        """Flash/notify when App Optimizer has a new suggestion."""
        try:
            desc = event.data.get("description", "")
            if desc and hasattr(self, '_optimizer_panel'):
                self._optimizer_panel.refresh()
        except Exception:
            pass

    def _on_settings_saved(self):
        """Propagate API key updates to the AI chat panel after settings save."""
        try:
            key = getattr(self.config, "anthropic_api_key", "") or ""
            # Re-read from config file since SettingsPanel writes directly to disk
            from ..config import AppConfig as _Cfg
            fresh = _Cfg.load()
            new_key = getattr(fresh, "anthropic_api_key", "") or key
            if hasattr(self, "_ai_chat"):
                self._ai_chat.refresh_api_key(new_key)
                bypass_enabled = getattr(fresh, "anthropic_bypass_mode", False)
                self._ai_chat.set_bypass_mode(bypass_enabled)
        except Exception:
            pass


if __name__ == "__main__":
    from mes_intel.main import main
    main()
