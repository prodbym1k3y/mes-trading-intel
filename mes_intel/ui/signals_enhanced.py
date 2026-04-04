"""Enhanced Signals Panel — Multi-Agent Confluence Signal Feed.

Real-time scrolling feed of confluence signals compiled from all 5 agents:
  ◈ Signal Engine   ▶ Chart Monitor   ◆ Dark Pool   ★ News Scanner   ▸ Meta-Learner

Each signal card shows:
  • Which agents agree / disagree (icons + direction + confidence)
  • 5-bar confluence meter
  • Historical win-rate estimate + R:R estimate
  • Color-coded confidence (green ≥75% / amber ≥50% / red <50%)
  • ⚡FIRE badge when 4+ agents align (pulsing amber border)
  • Expandable per-agent reasoning + order-flow context + cross-asset context

Right panel:
  • Deep order-flow compilation (delta divergence, imbalances, POC, shape, cum-delta)
  • Cross-asset correlation catalysts (VIX, DXY, 10Y, NQ, Gold, Oil, BTC)
  • Live catalyst log (news + options + cross-asset momentum shifts)
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient,
    QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QSplitter,
    QVBoxLayout, QWidget,
)

from .theme import COLORS
from ..event_bus import Event, EventBus, EventType

# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------
AGENT_DEFS = [
    {"key": "signal_engine", "icon": "◈", "label": "SIG",  "src": "signal_engine"},
    {"key": "chart_monitor",  "icon": "▶", "label": "CHT",  "src": "chart_monitor"},
    {"key": "dark_pool",      "icon": "◆", "label": "DRK",  "src": "dark_pool"},
    {"key": "news_scanner",   "icon": "★", "label": "NWS",  "src": "news_scanner"},
    {"key": "meta_learner",   "icon": "▸", "label": "META", "src": "meta_learner"},
]

# Colour shortcuts
_BG   = COLORS["bg_dark"]
_BGP  = COLORS["bg_panel"]
_BGC  = COLORS["bg_card"]
_CYAN = COLORS["cyan"]
_GRN  = COLORS["green_bright"]
_RED  = COLORS["pink"]
_AMB  = COLORS["amber"]
_MAG  = COLORS["magenta"]
_DIM  = COLORS["text_muted"]
_WHT  = COLORS["text_white"]
_ORG  = COLORS["orange"]
_MONO = "Courier New"

_MAX_CARDS = 40          # max signal cards kept in feed
_MIN_CARD_GAP = 45       # seconds minimum between confluence signals (same direction)


def _qc(h: str) -> QColor:
    return QColor(h)


def _dir_color(d: str) -> str:
    return _GRN if d == "LONG" else _RED if d == "SHORT" else _AMB


def _dir_arrow(d: str) -> str:
    return "▲" if d == "LONG" else "▼" if d == "SHORT" else "◈"


def _conf_color(c: float) -> str:
    return _GRN if c >= 0.75 else _AMB if c >= 0.50 else _RED


def _star_bar(filled: int, total: int = 5) -> str:
    return "★" * filled + "☆" * (total - filled)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AgentVote:
    agent: str
    direction: str = "NEUTRAL"   # LONG / SHORT / FLAT / NEUTRAL
    confidence: float = 0.0
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
    active: bool = True


_SIG_ID = 0


def _next_id() -> int:
    global _SIG_ID
    _SIG_ID += 1
    return _SIG_ID


@dataclass
class ConfluenceSignal:
    sig_id: int
    timestamp: float
    direction: str
    votes: List[AgentVote]
    confluence: float        # 0..1  fraction of active agents agreeing
    agents_agree: int
    confidence: float        # weighted avg
    is_fire: bool            # True when ≥4 agents agree
    win_rate: float          # estimated win-rate
    risk_reward: float       # R:R estimate
    entry_price: Optional[float]
    catalyst: str
    reasoning: str
    order_flow: Dict[str, str]
    cross_asset: Dict[str, str]
    regime: str = "unknown"
    strategy_breakdown: List[Dict] = field(default_factory=list)


# ===========================================================================
# Sparkline widget
# ===========================================================================
class _Sparkline(QWidget):
    """Tiny inline line chart (80 × 24 px)."""

    def __init__(self, maxlen: int = 40, color: str = _CYAN, parent=None):
        super().__init__(parent)
        self._data: deque[float] = deque(maxlen=maxlen)
        self._color = color
        self.setFixedSize(80, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def push(self, v: float):
        self._data.append(v)
        self.update()

    def set_data(self, values: list[float]):
        self._data.clear()
        for v in values:
            self._data.append(v)
        self.update()

    def paintEvent(self, _):
        data = list(self._data)
        if len(data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mn, mx = min(data), max(data)
        rng = (mx - mn) or 1.0
        w, h = float(self.width()), float(self.height())
        path = QPainterPath()
        for i, v in enumerate(data):
            x = i / (len(data) - 1) * w
            y = h - (v - mn) / rng * h * 0.85 - h * 0.075
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(_qc(self._color), 1.4))
        p.drawPath(path)
        # fill
        fill = QPainterPath(path)
        fill.lineTo(w, h)
        fill.lineTo(0, h)
        fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c1 = _qc(self._color); c1.setAlpha(45)
        c2 = _qc(self._color); c2.setAlpha(0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.fillPath(fill, QBrush(grad))


# ===========================================================================
# Confluence meter (5 segments)
# ===========================================================================
class _ConfluenceMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filled = 0
        self.setFixedSize(100, 12)

    def set_score(self, agents_agree: int):
        self._filled = max(0, min(5, agents_agree))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n, gap = 5, 3
        seg_w = (self.width() - gap * (n - 1)) / n
        for i in range(n):
            x = i * (seg_w + gap)
            r = QRectF(x, 0, seg_w, self.height())
            if i < self._filled:
                if self._filled >= 4:
                    c = _qc(_AMB)
                elif self._filled >= 3:
                    c = _qc(_CYAN)
                else:
                    c = _qc(_GRN)
                c.setAlpha(210)
            else:
                c = _qc(_DIM)
                c.setAlpha(70)
            p.fillRect(r, c)


# ===========================================================================
# Agent badge (icon + direction pip)
# ===========================================================================
class _AgentBadge(QLabel):
    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._label = label
        self._dir = "NEUTRAL"
        self._conf = 0.0
        self._refresh()

    def set_vote(self, direction: str, confidence: float, reason: str = ""):
        self._dir = direction
        self._conf = confidence
        self._reason = reason
        self._refresh()
        self.setToolTip(f"{self._label}: {direction} ({confidence:.0%})\n{reason[:120]}")

    def _refresh(self):
        d = self._dir
        if d == "LONG":
            bg, fg, sym = COLORS["green_dim"], _GRN, "▲"
        elif d == "SHORT":
            bg, fg, sym = COLORS["pink_dim"], _RED, "▼"
        elif d == "NEUTRAL":
            bg, fg, sym = _BGC, _DIM, "?"
        else:
            bg, fg, sym = COLORS["amber_dim"], _AMB, "◈"
        self.setText(f"{self._icon}{sym}")
        self.setFixedSize(26, 18)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; "
            f"font-size:9px; font-weight:bold; font-family:'{_MONO}'; "
            f"border:1px solid {fg}40; border-radius:2px; padding:1px;"
        )


# ===========================================================================
# Signal card
# ===========================================================================
class SignalCard(QFrame):
    """Full-featured confluence signal card."""

    def __init__(self, sig: ConfluenceSignal, price_hist: list[float], parent=None):
        super().__init__(parent)
        self._sig = sig
        self._expanded = False
        self._pulse = False
        self.setObjectName("SigCard")
        self._build(price_hist)
        self._style(False)
        if sig.is_fire:
            self._pt = QTimer(self)
            self._pt.timeout.connect(self._on_pulse)
            self._pt.start(420)
        else:
            self._pt = None

    # ── build ────────────────────────────────────────────────────────────────
    def _build(self, price_hist: list[float]):
        sig = self._sig
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(3)

        # Row 1 — timestamp | direction | FIRE | sparkline | expand
        r1 = QHBoxLayout(); r1.setSpacing(6)

        ts_lbl = QLabel(time.strftime("%H:%M:%S", time.localtime(sig.timestamp)))
        ts_lbl.setStyleSheet(f"color:{_DIM};font-size:9px;font-family:'{_MONO}';")
        r1.addWidget(ts_lbl)

        dc = _dir_color(sig.direction)
        dir_lbl = QLabel(f"{_dir_arrow(sig.direction)} {sig.direction}")
        dir_lbl.setStyleSheet(
            f"color:{dc};font-size:13px;font-weight:bold;"
            f"font-family:'{_MONO}';letter-spacing:2px;"
        )
        r1.addWidget(dir_lbl)

        if sig.is_fire:
            self._fire_lbl = QLabel("⚡FIRE")
            self._fire_lbl.setStyleSheet(self._fire_style(bright=True))
            r1.addWidget(self._fire_lbl)
        else:
            self._fire_lbl = None

        r1.addStretch()

        # sparkline
        spark_color = dc
        self._spark = _Sparkline(color=spark_color)
        self._spark.set_data(price_hist[-40:])
        r1.addWidget(self._spark)

        # expand btn
        self._ebtn = QPushButton("▼")
        self._ebtn.setFixedSize(18, 18)
        self._ebtn.setStyleSheet(
            f"background:transparent;color:{_DIM};border:none;"
            f"font-family:'{_MONO}';font-size:10px;"
        )
        self._ebtn.clicked.connect(self._toggle_expand)
        r1.addWidget(self._ebtn)
        root.addLayout(r1)

        # Row 2 — agent badges + confluence meter
        r2 = QHBoxLayout(); r2.setSpacing(4)
        votes_by = {v.agent: v for v in sig.votes}
        self._badges: dict[str, _AgentBadge] = {}
        for a in AGENT_DEFS:
            b = _AgentBadge(a["icon"], a["label"])
            v = votes_by.get(a["key"])
            if v:
                b.set_vote(v.direction, v.confidence, v.reason)
            r2.addWidget(b)
            self._badges[a["key"]] = b
        r2.addSpacing(10)
        self._meter = _ConfluenceMeter()
        self._meter.set_score(sig.agents_agree)
        r2.addWidget(self._meter)
        # star bar label
        stars = _star_bar(sig.agents_agree)
        star_lbl = QLabel(stars)
        star_c = _AMB if sig.agents_agree >= 4 else _CYAN if sig.agents_agree >= 3 else _DIM
        star_lbl.setStyleSheet(f"color:{star_c};font-size:10px;font-family:'{_MONO}';")
        r2.addWidget(star_lbl)
        r2.addStretch()
        root.addLayout(r2)

        # Row 3 — stats strip
        r3 = QHBoxLayout(); r3.setSpacing(14)
        for lbl_text, lbl_color in [
            (f"CONF {sig.confidence:.0%}", _conf_color(sig.confidence)),
            (f"R:R {sig.risk_reward:.1f}", _CYAN),
            (f"WIN {sig.win_rate:.0%}",
             _GRN if sig.win_rate >= 0.60 else _AMB if sig.win_rate >= 0.50 else _RED),
            (f"[{sig.regime.upper()}]", _ORG),
        ]:
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(
                f"color:{lbl_color};font-size:10px;font-weight:bold;"
                f"font-family:'{_MONO}';"
            )
            r3.addWidget(lbl)
        if sig.entry_price:
            ep = QLabel(f"@ {sig.entry_price:.2f}")
            ep.setStyleSheet(f"color:{_WHT};font-size:10px;font-family:'{_MONO}';")
            r3.addWidget(ep)
        r3.addStretch()
        root.addLayout(r3)

        # Row 4 — catalyst summary
        if sig.catalyst:
            cat = QLabel(f"◈ {sig.catalyst[:120]}")
            cat.setStyleSheet(
                f"color:{_DIM};font-size:9px;font-family:'{_MONO}';"
                f"font-style:italic;"
            )
            cat.setWordWrap(True)
            root.addWidget(cat)

        # Expandable detail
        self._det = QFrame()
        self._det.setVisible(False)
        dl = QVBoxLayout(self._det)
        dl.setContentsMargins(2, 4, 2, 2)
        dl.setSpacing(2)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_DIM}40;background:{_DIM}40;max-height:1px;")
        dl.addWidget(sep)

        # per-agent lines
        for a in AGENT_DEFS:
            v = votes_by.get(a["key"])
            if v and v.reason:
                ll = QLabel(f"{a['icon']} {a['label']}: {v.reason[:160]}")
                ll.setStyleSheet(
                    f"color:{_dir_color(v.direction)};font-size:9px;"
                    f"font-family:'{_MONO}';"
                )
                ll.setWordWrap(True)
                dl.addWidget(ll)

        # strategy breakdown (from ensemble)
        if sig.strategy_breakdown:
            shdr = QLabel("── STRATEGY BREAKDOWN ──")
            shdr.setStyleSheet(
                f"color:{_DIM};font-size:8px;letter-spacing:2px;"
                f"font-family:'{_MONO}';margin-top:4px;"
            )
            dl.addWidget(shdr)
            # Sort by absolute score, show top contributors
            sorted_strats = sorted(
                sig.strategy_breakdown,
                key=lambda s: abs(s.get("score", 0)),
                reverse=True,
            )
            for s in sorted_strats[:12]:
                sc = s.get("score", 0)
                conf = s.get("confidence", 0)
                sdir = s.get("direction", "FLAT")
                name = s.get("name", "?")
                sc_color = _GRN if sc > 0.1 else _RED if sc < -0.1 else _DIM
                line = f"  {name}: {sc:+.2f} ({conf:.0%}) {sdir}"
                # Append key reasoning if available
                notes = s.get("notes", [])
                if isinstance(notes, list) and notes:
                    line += f" — {notes[0][:60]}"
                elif isinstance(notes, str) and notes:
                    line += f" — {notes[:60]}"
                sl = QLabel(line)
                sl.setStyleSheet(
                    f"color:{sc_color};font-size:9px;"
                    f"font-family:'{_MONO}';"
                )
                sl.setWordWrap(True)
                dl.addWidget(sl)

        # order flow breakdown
        of = sig.order_flow
        if any(of.values()):
            hdr = QLabel("── ORDER FLOW ──")
            hdr.setStyleSheet(
                f"color:{_DIM};font-size:8px;letter-spacing:2px;"
                f"font-family:'{_MONO}';margin-top:4px;"
            )
            dl.addWidget(hdr)
            for k, v in of.items():
                if v:
                    li = QLabel(f"  {k}: {v}")
                    li.setStyleSheet(f"color:{_CYAN};font-size:9px;font-family:'{_MONO}';")
                    dl.addWidget(li)

        # cross-asset breakdown
        ca = sig.cross_asset
        if any(ca.values()):
            hdr2 = QLabel("── CROSS ASSET ──")
            hdr2.setStyleSheet(
                f"color:{_DIM};font-size:8px;letter-spacing:2px;"
                f"font-family:'{_MONO}';margin-top:4px;"
            )
            dl.addWidget(hdr2)
            for k, v in ca.items():
                if v:
                    li = QLabel(f"  {k}: {v}")
                    li.setStyleSheet(f"color:{_MAG};font-size:9px;font-family:'{_MONO}';")
                    dl.addWidget(li)

        root.addWidget(self._det)

    # ── interactions ─────────────────────────────────────────────────────────
    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._det.setVisible(self._expanded)
        self._ebtn.setText("▲" if self._expanded else "▼")

    def _on_pulse(self):
        self._pulse = not self._pulse
        self._style(self._pulse)
        if self._fire_lbl:
            self._fire_lbl.setStyleSheet(self._fire_style(self._pulse))

    def _style(self, bright: bool):
        dc = _dir_color(self._sig.direction)
        if self._sig.is_fire and bright:
            border, bg_stop = _AMB, f"{_AMB}18"
        elif self._sig.confidence >= 0.75:
            border, bg_stop = dc, f"{dc}10"
        else:
            border = f"{dc}80"
            bg_stop = _BGC
        self.setStyleSheet(
            f"QFrame#SigCard{{"
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {dc}1a,stop:0.06 {_BGC},stop:1 {_BGC});"
            f"border:1px solid {border}40;"
            f"border-left:3px solid {border};"
            f"border-radius:2px;margin-bottom:2px;}}"
        )

    @staticmethod
    def _fire_style(bright: bool) -> str:
        c = _AMB if bright else COLORS["orange_dim"]
        return (
            f"color:{c};font-size:10px;font-weight:bold;"
            f"font-family:'{_MONO}';letter-spacing:1px;"
            f"background:{COLORS['amber_dim']};border:1px solid {c};"
            f"padding:1px 4px;border-radius:2px;"
        )

    def add_price_point(self, p: float):
        self._spark.push(p)


# ===========================================================================
# Agent status bar
# ===========================================================================
class AgentStatusBar(QFrame):
    """Top strip showing each agent's last signal direction + heartbeat."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"background:{_BGC};border-bottom:1px solid {COLORS['cyan_dim']};"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(6)

        title = QLabel("AGENT STATUS:")
        title.setStyleSheet(
            f"color:{_DIM};font-size:9px;font-family:'{_MONO}';"
            f"letter-spacing:2px;"
        )
        lay.addWidget(title)
        lay.addSpacing(4)

        self._badges: dict[str, _AgentBadge] = {}
        for a in AGENT_DEFS:
            b = _AgentBadge(a["icon"], a["label"])
            # label next to badge
            lbl = QLabel(a["label"])
            lbl.setStyleSheet(
                f"color:{_DIM};font-size:8px;font-family:'{_MONO}';"
            )
            lay.addWidget(b)
            lay.addWidget(lbl)
            lay.addSpacing(4)
            self._badges[a["key"]] = b

        lay.addStretch()

        self._confluence_lbl = QLabel("CONFLUENCE: --")
        self._confluence_lbl.setStyleSheet(
            f"color:{_CYAN};font-size:9px;font-weight:bold;"
            f"font-family:'{_MONO}';letter-spacing:2px;"
        )
        lay.addWidget(self._confluence_lbl)

    def update_vote(self, agent_key: str, direction: str, confidence: float, reason: str = ""):
        if agent_key in self._badges:
            self._badges[agent_key].set_vote(direction, confidence, reason)

    def update_confluence(self, agents_agree: int, direction: str):
        dc = _dir_color(direction)
        label = "⚡FIRE" if agents_agree >= 4 else f"{agents_agree}/5"
        self._confluence_lbl.setText(f"CONFLUENCE: {label}")
        self._confluence_lbl.setStyleSheet(
            f"color:{dc};font-size:9px;font-weight:bold;"
            f"font-family:'{_MONO}';letter-spacing:2px;"
        )


# ===========================================================================
# Order flow analysis panel (right side, top)
# ===========================================================================
class OrderFlowPanel(QFrame):
    """Compiled deep order-flow metrics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setMinimumHeight(200)
        self._build()
        # rolling buffers
        self._price_buf: deque[float] = deque(maxlen=60)
        self._delta_buf: deque[float] = deque(maxlen=60)
        self._session_delta: float = 0.0
        self._last_price: float = 0.0
        self._poc: Optional[float] = None
        self._vah: Optional[float] = None
        self._val: Optional[float] = None
        self._abs_count: int = 0

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        hdr = QLabel("▶ ORDER FLOW ANALYSIS")
        hdr.setStyleSheet(
            f"color:{_CYAN};font-size:10px;font-weight:bold;"
            f"font-family:'{_MONO}';letter-spacing:3px;"
        )
        lay.addWidget(hdr)

        # sparkline for cum delta
        spark_row = QHBoxLayout()
        spark_row.setSpacing(6)
        spark_lbl = QLabel("CUM Δ")
        spark_lbl.setStyleSheet(f"color:{_DIM};font-size:8px;font-family:'{_MONO}';")
        spark_row.addWidget(spark_lbl)
        self._delta_spark = _Sparkline(maxlen=60, color=_CYAN)
        spark_row.addWidget(self._delta_spark)
        spark_row.addStretch()
        lay.addLayout(spark_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_DIM}40;background:{_DIM}40;max-height:1px;")
        lay.addWidget(sep)

        self._rows: dict[str, QLabel] = {}
        metrics = [
            ("DELTA_DIV",  "Delta Divergence",   "—"),
            ("IMBALANCE",  "Stacked Imbalance",  "—"),
            ("ABSORPTION", "Absorption",         "—"),
            ("POC_POS",    "POC Position",       "—"),
            ("VOL_SHAPE",  "Volume Shape",       "—"),
            ("CUM_DELTA",  "Cum Delta Trend",    "—"),
            ("DARK_POOL",  "Dark Pool Cluster",  "—"),
        ]
        for key, title, init in metrics:
            row = QHBoxLayout(); row.setSpacing(4)
            title_lbl = QLabel(f"{title}:")
            title_lbl.setFixedWidth(130)
            title_lbl.setStyleSheet(
                f"color:{_DIM};font-size:9px;font-family:'{_MONO}';"
            )
            val_lbl = QLabel(init)
            val_lbl.setStyleSheet(
                f"color:{_WHT};font-size:9px;font-weight:bold;font-family:'{_MONO}';"
            )
            row.addWidget(title_lbl)
            row.addWidget(val_lbl)
            row.addStretch()
            lay.addLayout(row)
            self._rows[key] = val_lbl

        lay.addStretch()

    def _set(self, key: str, text: str, color: str = _WHT):
        lbl = self._rows.get(key)
        if lbl:
            lbl.setText(text)
            lbl.setStyleSheet(
                f"color:{color};font-size:9px;font-weight:bold;font-family:'{_MONO}';"
            )

    def on_price(self, price: float, is_buy: bool, size: int):
        self._last_price = price
        self._price_buf.append(price)
        delta_tick = size if is_buy else -size
        self._session_delta += delta_tick
        self._delta_buf.append(self._session_delta)
        self._delta_spark.push(self._session_delta)
        self._recompute()

    def on_volume_profile(self, profile):
        try:
            self._poc = profile.poc
            val, vah = profile.value_area()
            self._val, self._vah = val, vah
            self._recompute_profile()
        except Exception:
            pass

    def on_big_trade(self, price: float, size: int, direction: str):
        self._abs_count += 1
        self._set("ABSORPTION",
                  f"DETECTED ×{self._abs_count} [{direction} {size}@{price:.2f}]",
                  _AMB)

    def on_dark_pool(self, price: float, notional: float):
        notional_m = notional / 1_000_000
        color = _MAG if notional_m >= 50 else _CYAN
        self._set("DARK_POOL", f"${notional_m:.1f}M @ {price:.2f}", color)

    def _recompute(self):
        prices = list(self._price_buf)
        deltas = list(self._delta_buf)
        if len(prices) >= 6 and len(deltas) >= 6:
            price_trend = prices[-1] - prices[-6]
            delta_trend = deltas[-1] - deltas[-6]
            if price_trend > 0 and delta_trend < 0:
                self._set("DELTA_DIV", "BEARISH DIV ▼ (↑px ↓Δ)", _RED)
            elif price_trend < 0 and delta_trend > 0:
                self._set("DELTA_DIV", "BULLISH DIV ▲ (↓px ↑Δ)", _GRN)
            elif price_trend > 0 and delta_trend > 0:
                self._set("DELTA_DIV", "ALIGNED BULL ▲", _GRN)
            elif price_trend < 0 and delta_trend < 0:
                self._set("DELTA_DIV", "ALIGNED BEAR ▼", _RED)
            else:
                self._set("DELTA_DIV", "NEUTRAL ◈", _AMB)

        cd = self._session_delta
        if abs(cd) > 500:
            trend = "STRONG BULL ▲" if cd > 0 else "STRONG BEAR ▼"
            c = _GRN if cd > 0 else _RED
        elif abs(cd) > 100:
            trend = "MILD BULL" if cd > 0 else "MILD BEAR"
            c = _GRN if cd > 0 else _RED
        else:
            trend, c = "NEUTRAL ◈", _AMB
        self._set("CUM_DELTA", f"{cd:+,.0f}  {trend}", c)

    def _recompute_profile(self):
        if self._poc and self._last_price:
            diff = self._last_price - self._poc
            if abs(diff) < 0.5:
                self._set("POC_POS", f"AT POC {self._poc:.2f} ◈", _AMB)
            elif diff > 0:
                self._set("POC_POS", f"ABOVE POC {self._poc:.2f} ▲", _GRN)
            else:
                self._set("POC_POS", f"BELOW POC {self._poc:.2f} ▼", _RED)

        # Crude volume shape detection from VAH/VAL/POC
        if self._poc and self._vah and self._val:
            total = self._vah - self._val
            if total > 0:
                upper = self._vah - self._poc
                lower = self._poc - self._val
                ratio = upper / total
                if ratio < 0.35:
                    shape, c = "b-SHAPE (bearish)", _RED
                elif ratio > 0.65:
                    shape, c = "P-SHAPE (bullish)", _GRN
                else:
                    shape, c = "D-SHAPE (balanced)", _AMB
                self._set("VOL_SHAPE", shape, c)

    def get_summary(self) -> dict[str, str]:
        """Return current state as a dict for embedding in signal cards."""
        out = {}
        for k, lbl in self._rows.items():
            txt = lbl.text()
            if txt and txt != "—":
                out[k] = txt
        return out


# ===========================================================================
# Cross-asset catalyst panel (right side, bottom)
# ===========================================================================

_ASSET_ROWS = [
    ("VIX",  "VIX",        "Volatility — rising = bearish ES"),
    ("DXY",  "DXY",        "Dollar — strength bearish for risk"),
    ("10Y",  "10Y YIELD",  "Rising yields → bearish equities"),
    ("NQ",   "NQ/ES DIVG", "NQ leading or lagging ES"),
    ("GOLD", "GOLD",       "Risk-off flows"),
    ("OIL",  "OIL",        "Macro risk sentiment"),
    ("BTC",  "BITCOIN",    "Risk-on proxy"),
]


class CrossAssetPanel(QFrame):
    """Live cross-asset correlation signals + catalyst log."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._build()
        self._last_data: dict = {}

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        hdr = QLabel("⬡ CROSS-ASSET CATALYSTS")
        hdr.setStyleSheet(
            f"color:{_MAG};font-size:10px;font-weight:bold;"
            f"font-family:'{_MONO}';letter-spacing:3px;"
        )
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_DIM}40;background:{_DIM}40;max-height:1px;")
        lay.addWidget(sep)

        self._asset_rows: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
        for key, name, tooltip in _ASSET_ROWS:
            row = QHBoxLayout(); row.setSpacing(4)
            name_lbl = QLabel(f"{name}:")
            name_lbl.setFixedWidth(80)
            name_lbl.setStyleSheet(f"color:{_DIM};font-size:8px;font-family:'{_MONO}';")
            name_lbl.setToolTip(tooltip)
            val_lbl = QLabel("--")
            val_lbl.setFixedWidth(70)
            val_lbl.setStyleSheet(f"color:{_WHT};font-size:9px;font-family:'{_MONO}';")
            sig_lbl = QLabel("—")
            sig_lbl.setStyleSheet(f"color:{_DIM};font-size:9px;font-family:'{_MONO}';")
            row.addWidget(name_lbl)
            row.addWidget(val_lbl)
            row.addWidget(sig_lbl)
            row.addStretch()
            lay.addLayout(row)
            self._asset_rows[key] = (name_lbl, val_lbl, sig_lbl)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color:{_DIM}40;background:{_DIM}40;max-height:1px;")
        lay.addWidget(sep2)

        # Catalyst log (last 5 events)
        cat_hdr = QLabel("CATALYST LOG:")
        cat_hdr.setStyleSheet(
            f"color:{_DIM};font-size:8px;letter-spacing:2px;font-family:'{_MONO}';"
        )
        lay.addWidget(cat_hdr)
        self._cat_lbls: list[QLabel] = []
        for _ in range(5):
            cl = QLabel("")
            cl.setStyleSheet(f"color:{_DIM};font-size:8px;font-family:'{_MONO}';")
            cl.setWordWrap(True)
            lay.addWidget(cl)
            self._cat_lbls.append(cl)
        self._cat_log: deque[str] = deque(maxlen=5)

        lay.addStretch()

    def _set_asset(self, key: str, value: str, signal: str, color: str):
        row = self._asset_rows.get(key)
        if row:
            _, val_lbl, sig_lbl = row
            val_lbl.setText(value)
            val_lbl.setStyleSheet(f"color:{color};font-size:9px;font-family:'{_MONO}';")
            sig_lbl.setText(signal)
            sig_lbl.setStyleSheet(f"color:{color};font-size:9px;font-family:'{_MONO}';")

    def update_data(self, data: dict):
        self._last_data = data
        assets = data.get("assets", {})

        def _parse_asset(key: str, asset_data: dict, invert: bool = False):
            if not asset_data:
                return
            price = asset_data.get("price", 0.0)
            change = asset_data.get("change_pct", asset_data.get("change", 0.0))
            signal_raw = asset_data.get("signal", asset_data.get("impact", "neutral"))
            # Map signal to color
            sig_str = str(signal_raw).lower()
            if any(x in sig_str for x in ("bull", "long", "positive", "risk_on", "rising" if invert else "")):
                color = _GRN
            elif any(x in sig_str for x in ("bear", "short", "negative", "risk_off")):
                color = _RED
            else:
                color = _AMB
            arrow = "▲" if change > 0 else "▼" if change < 0 else "◈"
            val_str = f"{price:.2f}" if price > 1 else f"{price:.4f}"
            sig_display = f"{arrow} {change:+.2f}%  {sig_str[:20].upper()}"
            self._set_asset(key, val_str, sig_display, color)

        for key in ("VIX", "DXY", "10Y", "GOLD", "OIL", "BTC"):
            _parse_asset(key, assets.get(key, assets.get(key.lower(), {})))

        # NQ/ES divergence — check composite
        nq_data = assets.get("NQ", assets.get("nq", {}))
        composite = data.get("composite_signal", data.get("direction", ""))
        if nq_data:
            nq_ch = nq_data.get("change_pct", 0)
            es_ch = data.get("es_change_pct", 0)
            divg = nq_ch - es_ch
            if abs(divg) > 0.2:
                color = _GRN if divg > 0 else _RED
                self._set_asset("NQ", f"NQ{divg:+.2f}%", f"DIVG ({'NQ LEADING' if divg > 0 else 'NQ LAGGING'})", color)
            else:
                self._set_asset("NQ", "IN SYNC", "◈ ALIGNED", _AMB)

    def add_catalyst(self, text: str):
        ts = time.strftime("%H:%M")
        self._cat_log.appendleft(f"[{ts}] {text}")
        for i, lbl in enumerate(self._cat_lbls):
            if i < len(self._cat_log):
                lbl.setText(list(self._cat_log)[i])
                lbl.setStyleSheet(f"color:{_CYAN if i == 0 else _DIM};font-size:8px;font-family:'{_MONO}';")
            else:
                lbl.setText("")

    def get_summary(self) -> dict[str, str]:
        out = {}
        for key, (_, val_lbl, sig_lbl) in self._asset_rows.items():
            v = val_lbl.text()
            s = sig_lbl.text()
            if v and v != "--":
                out[key] = f"{v} {s}"
        return out


# ===========================================================================
# Main EnhancedSignalsPanel
# ===========================================================================

class EnhancedSignalsPanel(QWidget):
    """
    Full-screen enhanced SIGNALS tab.
    Left 60%: scrolling confluence signal feed (newest on top).
    Right 40%: order flow analysis (top) + cross-asset catalysts (bottom).
    """

    def __init__(self, bus: EventBus, parent=None):
        super().__init__(parent)
        self.bus = bus

        # Agent vote state
        self._votes: dict[str, AgentVote] = {a["key"]: AgentVote(agent=a["key"]) for a in AGENT_DEFS}
        self._regime = "unknown"
        self._entry_price: Optional[float] = None
        self._price_history: deque[float] = deque(maxlen=120)
        self._signal_cards: list[SignalCard] = []

        # Throttle: last time we emitted a confluence signal per direction
        self._last_emit: dict[str, float] = {"LONG": 0, "SHORT": 0, "FLAT": 0}
        self._pending_signal_id = 0

        self._build_ui()
        self._subscribe_events()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # Agent status bar (top strip)
        self._status_bar = AgentStatusBar()
        root.addWidget(self._status_bar)

        # Main split: feed (left) | analysis panels (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: Signal Feed ──────────────────────────────────────────────
        feed_container = QWidget()
        feed_lay = QVBoxLayout(feed_container)
        feed_lay.setContentsMargins(0, 0, 0, 0)
        feed_lay.setSpacing(2)

        feed_hdr = QFrame()
        feed_hdr.setFixedHeight(22)
        feed_hdr.setStyleSheet(
            f"background:{_BGC};border-bottom:1px solid {COLORS['cyan_dim']};"
        )
        feed_hdr_lay = QHBoxLayout(feed_hdr)
        feed_hdr_lay.setContentsMargins(8, 2, 8, 2)
        feed_hdr_lay.setSpacing(8)
        feed_title = QLabel("◈ CONFLUENCE SIGNAL FEED  //  NEWEST FIRST")
        feed_title.setStyleSheet(
            f"color:{_CYAN};font-size:9px;font-weight:bold;"
            f"font-family:'{_MONO}';letter-spacing:3px;"
        )
        feed_hdr_lay.addWidget(feed_title)
        feed_hdr_lay.addStretch()
        self._count_lbl = QLabel("0 signals")
        self._count_lbl.setStyleSheet(f"color:{_DIM};font-size:9px;font-family:'{_MONO}';")
        feed_hdr_lay.addWidget(self._count_lbl)
        feed_lay.addWidget(feed_hdr)

        # Live strategy scores strip
        self._live_scores_lbl = QLabel("Ensemble: waiting for data...")
        self._live_scores_lbl.setStyleSheet(
            f"color:{_DIM};font-size:8px;font-family:'{_MONO}';"
            f"padding:2px 8px;background:{COLORS.get('bg_panel', '#0a0a12')};"
            f"border-bottom:1px solid {COLORS.get('cyan_dim', '#113')};"
        )
        self._live_scores_lbl.setWordWrap(True)
        self._live_scores_lbl.setFixedHeight(28)
        feed_lay.addWidget(self._live_scores_lbl)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{_BG};border:none;}}"
            f"QScrollBar:vertical{{background:{_BGC};width:6px;border:none;}}"
            f"QScrollBar::handle:vertical{{background:{COLORS['cyan_dim']};border-radius:3px;}}"
        )
        self._feed_widget = QWidget()
        self._feed_widget.setStyleSheet(f"background:{_BG};")
        self._feed_layout = QVBoxLayout(self._feed_widget)
        self._feed_layout.setContentsMargins(4, 4, 4, 4)
        self._feed_layout.setSpacing(3)
        self._feed_layout.addStretch()   # stretch at bottom pushes cards up

        scroll.setWidget(self._feed_widget)
        feed_lay.addWidget(scroll, 1)
        self._scroll = scroll

        # Empty state label
        self._empty_lbl = QLabel(
            "◈ AWAITING CONFLUENCE...\n\n"
            "Signals appear when 2+ agents align.\n"
            "⚡FIRE badge = 4+ agents agree.\n\n"
            "Monitoring:\n"
            "  ◈ Signal Engine  ▶ Chart Monitor\n"
            "  ◆ Dark Pool      ★ News Scanner\n"
            "  ▸ Meta-Learner"
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"color:{_DIM};font-size:11px;font-family:'{_MONO}';"
            f"letter-spacing:1px;line-height:1.6;"
        )
        feed_lay.addWidget(self._empty_lbl)

        splitter.addWidget(feed_container)

        # ── Right: analysis panels ─────────────────────────────────────────
        right_split = QSplitter(Qt.Orientation.Vertical)

        self._of_panel = OrderFlowPanel()
        right_split.addWidget(self._of_panel)

        self._ca_panel = CrossAssetPanel()
        right_split.addWidget(self._ca_panel)

        right_split.setSizes([280, 260])
        splitter.addWidget(right_split)

        splitter.setSizes([620, 380])
        root.addWidget(splitter, 1)

    # ── Event subscriptions ──────────────────────────────────────────────────
    def _subscribe_events(self):
        subs = [
            (EventType.SIGNAL_GENERATED,    self._on_signal_generated),
            (EventType.ENSEMBLE_UPDATE,      self._on_ensemble_update),
            (EventType.DARK_POOL_ALERT,      self._on_dark_pool),
            (EventType.BIG_TRADE_ALERT,      self._on_big_trade),
            (EventType.NEWS_ALERT,           self._on_news),
            (EventType.TRUMP_ALERT,          self._on_news),
            (EventType.BREAKING_NEWS,        self._on_breaking_news),
            (EventType.REGIME_CHANGE,        self._on_regime_change),
            (EventType.VOLUME_PROFILE_UPDATE,self._on_volume_profile),
            (EventType.PRICE_UPDATE,         self._on_price),
            (EventType.CONFLUENCE_ALERT,     self._on_confluence),
            (EventType.PERFORMANCE_REPORT,   self._on_performance_report),
        ]
        for et, handler in subs:
            try:
                self.bus.subscribe(et, handler)
            except Exception:
                pass
        # Cross-asset — may not exist in older builds
        for et in (EventType.CROSS_ASSET_UPDATE, EventType.OPTIONS_DATA_UPDATE):
            try:
                self.bus.subscribe(et, self._on_cross_asset)
            except Exception:
                pass

    # ── Individual event handlers ────────────────────────────────────────────
    def _on_signal_generated(self, event: Event):
        d = event.data
        direction = d.get("direction", "FLAT")
        conf = float(d.get("confidence", 0.0))
        entry = d.get("entry_price") or d.get("entry")
        regime = d.get("regime", self._regime)
        self._regime = regime
        if entry:
            self._entry_price = float(entry)

        # Build reason from strategy breakdown
        breakdown = d.get("strategy_breakdown", [])
        self._latest_breakdown = breakdown
        if breakdown:
            agreeing = [s["name"] for s in breakdown
                        if s.get("direction") == direction
                        and abs(s.get("score", 0)) > 0.1]
            top = sorted(
                [s for s in breakdown if abs(s.get("score", 0)) > 0.1],
                key=lambda s: abs(s["score"]),
                reverse=True,
            )[:5]
            parts = [f"{s['name']}={s['score']:+.2f}" for s in top]
            n_agree = d.get("strategies_agree", len(agreeing))
            reason = (f"{n_agree} strategies agree | "
                      f"{', '.join(parts)}")
        else:
            scores = d.get("strategy_scores", {})
            agreeing = [k for k, v in scores.items()
                        if v.get("direction") == direction]
            reason = f"ensemble={conf:.0%} agree=[{', '.join(agreeing[:3])}]"

        self._votes["signal_engine"] = AgentVote(
            agent="signal_engine",
            direction=direction,
            confidence=conf,
            reason=reason,
        )
        self._status_bar.update_vote(
            "signal_engine", direction, conf, reason
        )
        self._check_confluence(force=True)

    def _on_ensemble_update(self, event: Event):
        d = event.data
        scores = d.get("scores", {})
        regime = d.get("regime", self._regime)
        self._regime = regime
        if not scores:
            return

        # Update live strategy scores strip (top 8 by absolute score)
        try:
            sorted_scores = sorted(scores.items(), key=lambda x: abs(x[1]), reverse=True)
            top = sorted_scores[:8]
            parts = []
            for name, sc in top:
                color_marker = "+" if sc > 0.1 else ("-" if sc < -0.1 else "~")
                parts.append(f"{name}:{sc:+.2f}")
            regime_str = regime.upper() if regime else "?"
            self._live_scores_lbl.setText(
                f"[{regime_str}] " + "  ".join(parts)
            )
            # Color based on overall bias
            net = sum(sc for _, sc in top)
            color = _GRN if net > 0.5 else (_RED if net < -0.5 else _DIM)
            self._live_scores_lbl.setStyleSheet(
                f"color:{color};font-size:8px;font-family:'{_MONO}';"
                f"padding:2px 8px;background:{COLORS.get('bg_panel', '#0a0a12')};"
                f"border-bottom:1px solid {COLORS.get('cyan_dim', '#113')};"
            )
        except Exception:
            pass

        # Derive chart_monitor direction from order_flow strategy score
        of_score = scores.get("order_flow", 0)
        mom_score = scores.get("momentum", 0)
        combined = (of_score + mom_score) / 2
        direction = "LONG" if combined > 0.15 else "SHORT" if combined < -0.15 else "FLAT"
        conf = abs(combined)
        reason = f"of={of_score:.2f} mom={mom_score:.2f} regime={regime}"
        self._votes["chart_monitor"] = AgentVote(
            agent="chart_monitor",
            direction=direction,
            confidence=min(conf, 1.0),
            reason=reason,
        )
        self._status_bar.update_vote("chart_monitor", direction, conf, reason)
        self._check_confluence()

    def _on_dark_pool(self, event: Event):
        d = event.data
        notional = float(d.get("notional", 0))
        price = float(d.get("price", 0))
        # Dark pool near current price is a bullish absorption signal
        current = self._entry_price or (list(self._price_history)[-1] if self._price_history else 0)
        if current and abs(price - current) < 2.0:
            direction = "LONG"  # absorption = institutional support
            reason = f"${notional/1e6:.1f}M block @ {price:.2f} NEAR PRICE — absorption"
        else:
            direction = "NEUTRAL"
            reason = f"${notional/1e6:.1f}M block @ {price:.2f}"
        conf = min(notional / 50_000_000, 1.0)  # 50M = max confidence
        self._votes["dark_pool"] = AgentVote(
            agent="dark_pool", direction=direction, confidence=conf, reason=reason,
        )
        self._status_bar.update_vote("dark_pool", direction, conf, reason)
        self._of_panel.on_dark_pool(price, notional)
        self._ca_panel.add_catalyst(f"DARK POOL ${notional/1e6:.1f}M @ {price:.2f}")
        self._check_confluence()

    def _on_big_trade(self, event: Event):
        d = event.data
        price = float(d.get("price", 0))
        size = int(d.get("size", 0))
        trade_type = str(d.get("type", ""))
        direction = "LONG" if "buy" in trade_type.lower() else "SHORT" if "sell" in trade_type.lower() else "NEUTRAL"
        self._of_panel.on_big_trade(price, size, direction)

    def _on_news(self, event: Event):
        d = event.data
        sentiment = float(d.get("sentiment_score", 0))
        headline = str(d.get("headline", ""))
        direction = "LONG" if sentiment > 0.2 else "SHORT" if sentiment < -0.2 else "NEUTRAL"
        conf = min(abs(sentiment) * 2, 1.0)
        reason = f"sentiment={sentiment:+.2f}  {headline[:80]}"
        self._votes["news_scanner"] = AgentVote(
            agent="news_scanner", direction=direction, confidence=conf, reason=reason,
        )
        self._status_bar.update_vote("news_scanner", direction, conf, reason)
        if abs(sentiment) > 0.3:
            self._ca_panel.add_catalyst(f"NEWS {direction}: {headline[:60]}")
        self._check_confluence()

    def _on_breaking_news(self, event: Event):
        d = event.data
        sentiment = float(d.get("sentiment_score", 0))
        headline = str(d.get("headline", ""))
        direction = "LONG" if sentiment > 0.1 else "SHORT" if sentiment < -0.1 else "NEUTRAL"
        conf = min(abs(sentiment) * 2.5, 1.0)
        reason = f"BREAKING sentiment={sentiment:+.2f}  {headline[:80]}"
        self._votes["news_scanner"] = AgentVote(
            agent="news_scanner", direction=direction, confidence=conf, reason=reason,
        )
        self._status_bar.update_vote("news_scanner", direction, conf, reason)
        self._ca_panel.add_catalyst(f"⚡BREAKING: {headline[:60]}")
        self._check_confluence(force=True)

    def _on_regime_change(self, event: Event):
        self._regime = event.data.get("regime", self._regime)

    def _on_volume_profile(self, event: Event):
        profile = event.data.get("profile")
        if profile:
            self._of_panel.on_volume_profile(profile)

    def _on_price(self, event: Event):
        d = event.data
        price = d.get("price")
        if price:
            self._price_history.append(float(price))
            # Feed latest price into existing cards' sparklines
            for card in self._signal_cards[-3:]:
                try:
                    card.add_price_point(float(price))
                except Exception:
                    pass
        is_buy = bool(d.get("is_buy", True))
        size = int(d.get("size", 0))
        if size > 0 and price:
            self._of_panel.on_price(float(price), is_buy, size)

    def _on_confluence(self, event: Event):
        d = event.data
        price = float(d.get("price", 0))
        score = float(d.get("score", 0))
        zone_type = str(d.get("zone_type", ""))
        if score > 0.5:
            self._ca_panel.add_catalyst(f"CONFLUENCE {zone_type.upper()} @ {price:.2f} ({score:.0%})")

    def _on_performance_report(self, event: Event):
        d = event.data
        accs = d.get("strategy_accuracies", {})
        trend = d.get("trend", "")
        team_score = float(d.get("team_score", 0))
        if not accs:
            return
        avg_acc = sum(accs.values()) / len(accs) if accs else 0.5
        direction = "LONG" if team_score > 60 else "SHORT" if team_score < 40 else "NEUTRAL"
        reason = f"team_score={team_score:.0f} avg_acc={avg_acc:.0%} trend={trend}"
        conf = min(abs(team_score - 50) / 50, 1.0)
        self._votes["meta_learner"] = AgentVote(
            agent="meta_learner", direction=direction, confidence=conf, reason=reason,
        )
        self._status_bar.update_vote("meta_learner", direction, conf, reason)
        self._check_confluence()

    def _on_cross_asset(self, event: Event):
        d = event.data
        self._ca_panel.update_data(d)
        # Derive cross-asset vote from composite signal
        composite = str(d.get("composite_signal", d.get("direction", "neutral"))).lower()
        if "bull" in composite or "long" in composite or "risk_on" in composite:
            direction = "LONG"
            conf = 0.65
        elif "bear" in composite or "short" in composite or "risk_off" in composite:
            direction = "SHORT"
            conf = 0.65
        else:
            direction = "NEUTRAL"
            conf = 0.3
        reason = f"composite={composite}"
        # We map cross-asset to chart_monitor as secondary vote (doesn't override primary)
        # Only update if chart monitor hasn't voted recently
        cm_vote = self._votes.get("chart_monitor")
        age = time.time() - (cm_vote.timestamp if cm_vote else 0)
        if age > 300:  # 5 min stale
            self._votes["chart_monitor"] = AgentVote(
                agent="chart_monitor", direction=direction, confidence=conf, reason=reason,
            )
            self._status_bar.update_vote("chart_monitor", direction, conf, reason)
            self._check_confluence()

    # ── Confluence compilation ────────────────────────────────────────────────
    def _check_confluence(self, force: bool = False):
        """Recompute agent agreement and emit a signal card if threshold met."""
        now = time.time()
        votes = list(self._votes.values())

        # Only count votes less than 10 minutes old
        active_votes = [v for v in votes if v.direction != "NEUTRAL"
                        and (now - v.timestamp) < 600]
        if not active_votes:
            return

        # Count directions
        dir_counts: dict[str, list[AgentVote]] = {"LONG": [], "SHORT": [], "FLAT": []}
        for v in active_votes:
            if v.direction in dir_counts:
                dir_counts[v.direction].append(v)

        best_dir = max(dir_counts, key=lambda d: len(dir_counts[d]))
        best_votes = dir_counts[best_dir]
        agents_agree = len(best_votes)
        total_active = len(active_votes)

        confluence = agents_agree / 5  # always out of 5 total agents
        is_fire = agents_agree >= 4

        # Threshold: emit signal if ≥2 agents agree
        if agents_agree < 2 and not force:
            return

        # Throttle: don't emit same direction within MIN_CARD_GAP seconds (unless fire)
        last_t = self._last_emit.get(best_dir, 0)
        gap = _MIN_CARD_GAP if not is_fire else 20
        if (now - last_t) < gap and not force:
            return

        self._last_emit[best_dir] = now

        # Update status bar
        self._status_bar.update_confluence(agents_agree, best_dir)

        # Weighted average confidence
        if best_votes:
            conf = sum(v.confidence for v in best_votes) / len(best_votes)
        else:
            conf = 0.3

        # Estimate win rate from confluence (heuristic)
        win_rate = 0.45 + confluence * 0.30  # 45% at 0 confluence → 75% at full

        # Estimate R:R
        risk_reward = 1.0 + confluence * 1.5  # 1.0 min → 2.5 at full

        # Order flow and cross-asset context
        of_context = self._of_panel.get_summary()
        ca_context = self._ca_panel.get_summary()

        # Build catalyst string from all reasons
        catalysts = []
        for v in best_votes:
            if v.reason:
                catalysts.append(v.reason[:60])
        catalyst_str = " | ".join(catalysts[:2])

        reasoning = "\n".join(
            f"{AGENT_DEFS[i]['icon']} {AGENT_DEFS[i]['label']}: "
            f"{self._votes[a['key']].direction} ({self._votes[a['key']].confidence:.0%}) — "
            f"{self._votes[a['key']].reason[:80]}"
            for i, a in enumerate(AGENT_DEFS)
            if self._votes[a["key"]].direction != "NEUTRAL"
        )

        sig = ConfluenceSignal(
            sig_id=_next_id(),
            timestamp=now,
            direction=best_dir,
            votes=list(self._votes.values()),
            confluence=confluence,
            agents_agree=agents_agree,
            confidence=conf,
            is_fire=is_fire,
            win_rate=win_rate,
            risk_reward=risk_reward,
            entry_price=self._entry_price,
            catalyst=catalyst_str,
            reasoning=reasoning,
            order_flow=of_context,
            cross_asset=ca_context,
            regime=self._regime,
            strategy_breakdown=getattr(self, '_latest_breakdown', []),
        )

        self._add_signal_card(sig)

    def _add_signal_card(self, sig: ConfluenceSignal):
        """Insert new card at top of feed."""
        card = SignalCard(sig, list(self._price_history))

        # Insert before the stretch (last item)
        insert_idx = self._feed_layout.count() - 1
        if insert_idx < 0:
            insert_idx = 0
        self._feed_layout.insertWidget(insert_idx, card)
        self._signal_cards.append(card)

        # Hide empty state label
        self._empty_lbl.setVisible(False)

        # Trim old cards
        while len(self._signal_cards) > _MAX_CARDS:
            old_card = self._signal_cards.pop(0)
            self._feed_layout.removeWidget(old_card)
            old_card.deleteLater()

        # Update count
        self._count_lbl.setText(f"{len(self._signal_cards)} signals")

        # Scroll to top to show newest card
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(0))
