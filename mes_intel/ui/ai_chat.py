"""AI Assistant Chat UI — neon cyberpunk chat interface with Claude.

Features:
  - Full chat interface with scrolling message history
  - LLM responses in neon cyan, user messages in white
  - "Thinking..." animation while waiting
  - SQL/code blocks with dark syntax highlighting
  - Quick-action buttons: Win Rate, Current Regime, Best Setups, Agent Status, Recent Signals
  - Chat history persisted to SQLite (chat_history table)
  - Tool call display (shows SQL queries run)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QScrollArea, QTextEdit, QSizePolicy,
    QApplication,
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QObject
from PySide6.QtGui import QFont, QTextCursor, QColor

log = logging.getLogger(__name__)

# ── Colors ────────────────────────────────────────────────────────────────────
_BG        = "#050508"
_BG2       = "#080810"
_BG3       = "#0a0a18"
_CYAN      = "#00d4ff"
_CYAN_DIM  = "#0066aa"
_MAGENTA   = "#ff00ff"
_GREEN     = "#00ff88"
_AMBER     = "#ff8c00"
_RED       = "#ff3344"
_DIM       = "#333355"
_TEXT      = "#ccddff"
_WHITE     = "#e8f0ff"
_MONO      = "Courier New, monospace"

_STYLE = f"""
QWidget {{
    background: {_BG};
    color: {_TEXT};
    font-family: {_MONO};
    font-size: 11px;
}}
QScrollArea {{
    border: none;
    background: {_BG};
}}
QScrollBar:vertical {{
    background: {_BG2};
    width: 8px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {_CYAN_DIM};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QLineEdit {{
    background: {_BG3};
    color: {_WHITE};
    border: 1px solid {_DIM};
    border-radius: 3px;
    padding: 8px 12px;
    font-family: {_MONO};
    font-size: 12px;
}}
QLineEdit:focus {{
    border: 1px solid {_CYAN};
}}
QPushButton {{
    background: {_BG3};
    color: {_CYAN};
    border: 1px solid {_DIM};
    padding: 6px 14px;
    font-family: {_MONO};
    font-size: 10px;
    letter-spacing: 1px;
}}
QPushButton:hover {{
    background: #12122a;
    border-color: {_CYAN};
}}
QPushButton:pressed {{
    background: #1a1a38;
}}
QLabel {{ background: transparent; }}
"""

# ── Worker thread for LLM calls ───────────────────────────────────────────────

class _LLMWorker(QObject):
    """Runs LLM.chat() in a background thread and emits signals."""
    tool_called   = Signal(str, str)   # tool_name, input_preview
    response_ready = Signal(str, str, int)  # text, tool_calls_json, tokens
    error_occurred = Signal(str)

    def __init__(self, assistant, message: str):
        super().__init__()
        self._assistant = assistant
        self._message = message

    def run(self):
        try:
            def on_tool(name, inp):
                preview = json.dumps(inp)[:80]
                self.tool_called.emit(name, preview)

            text, tool_calls, tokens = self._assistant.chat(
                self._message, on_tool_call=on_tool
            )
            tc_json = json.dumps(tool_calls) if tool_calls else ""
            self.response_ready.emit(text, tc_json, tokens)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


# ── Individual message bubble ─────────────────────────────────────────────────

class _MessageBubble(QFrame):
    """A single chat message — user or assistant."""

    def __init__(self, role: str, content: str, tool_calls_json: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        is_user = role == "user"
        is_thinking = role == "thinking"
        is_tool = role == "tool"

        if is_user:
            header_color = _WHITE
            header_text = "▸ YOU"
            bg_color = "#0c0c20"
            border_color = _DIM
            content_color = _WHITE
        elif is_thinking:
            header_color = _AMBER
            header_text = "⟳ THINKING..."
            bg_color = "#0a0a14"
            border_color = _AMBER
            content_color = _AMBER
        elif is_tool:
            header_color = _MAGENTA
            header_text = "⚙ TOOL CALL"
            bg_color = "#0a0814"
            border_color = "#551155"
            content_color = _MAGENTA
        else:
            header_color = _CYAN
            header_text = "◈ MES INTEL AI"
            bg_color = "#060614"
            border_color = _CYAN_DIM
            content_color = _TEXT

        self.setStyleSheet(
            f"QFrame {{ background: {bg_color}; border: 1px solid {border_color}; "
            f"border-radius: 4px; margin: 2px 4px; }}"
        )

        # Header row
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)
        hdr_lbl = QLabel(header_text)
        hdr_lbl.setStyleSheet(
            f"color: {header_color}; font-size: 9px; font-weight: bold; "
            f"letter-spacing: 2px; background: transparent;"
        )
        hdr_row.addWidget(hdr_lbl)
        hdr_row.addStretch()
        ts_lbl = QLabel(datetime.now().strftime("%H:%M:%S"))
        ts_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 9px; background: transparent;"
        )
        hdr_row.addWidget(ts_lbl)
        layout.addLayout(hdr_row)

        # Content — use QTextEdit for selectable, formatted text
        if not is_thinking:
            txt = QTextEdit()
            txt.setReadOnly(True)
            txt.setFrameShape(QFrame.NoFrame)
            txt.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            txt.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            txt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            txt.setStyleSheet(
                f"QTextEdit {{ background: transparent; color: {content_color}; "
                f"border: none; font-family: {_MONO}; font-size: 11px; padding: 0; }}"
            )
            txt.setHtml(self._render_content(content, content_color))
            # Auto-resize height
            doc = txt.document()
            doc.setTextWidth(txt.viewport().width() or 800)
            h = int(doc.size().height()) + 12
            txt.setFixedHeight(max(h, 24))
            layout.addWidget(txt)
            self._txt = txt
        else:
            self._thinking_lbl = QLabel(content)
            self._thinking_lbl.setStyleSheet(
                f"color: {content_color}; font-size: 11px; background: transparent;"
            )
            layout.addWidget(self._thinking_lbl)
            self._dot_count = 0
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._animate)
            self._timer.start(400)

        # Tool calls display
        if tool_calls_json:
            try:
                calls = json.loads(tool_calls_json)
                for call in calls:
                    tc_lbl = QLabel(
                        f"  ⚡ {call.get('tool','?')}({json.dumps(call.get('input',''))[:60]})"
                    )
                    tc_lbl.setStyleSheet(
                        f"color: {_MAGENTA}; font-size: 9px; "
                        f"letter-spacing: 1px; background: transparent;"
                    )
                    layout.addWidget(tc_lbl)
            except Exception:
                pass

    def _animate(self):
        self._dot_count = (self._dot_count + 1) % 4
        self._thinking_lbl.setText("⟳ THINKING" + "." * self._dot_count)

    def stop_thinking(self):
        try:
            self._timer.stop()
        except Exception:
            pass

    def _render_content(self, text: str, default_color: str) -> str:
        """Convert plain text to HTML, with code block highlighting."""
        import html as _html
        parts = text.split("```")
        result = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # Code block
                lines = part.split("\n", 1)
                lang = lines[0].strip() if lines else ""
                code = lines[1] if len(lines) > 1 else part
                code_escaped = _html.escape(code)
                result.append(
                    f'<pre style="background:#0a0a1e; color:{_GREEN}; '
                    f'border:1px solid #1a1a3a; padding:8px; '
                    f'font-family:Courier New,monospace; font-size:10px; '
                    f'white-space:pre-wrap; margin:4px 0;">'
                    f'<span style="color:{_MAGENTA}; font-size:9px;">{_html.escape(lang)}</span>'
                    f'{"<br>" if lang else ""}{code_escaped}</pre>'
                )
            else:
                # Regular text — convert newlines and inline formatting
                escaped = _html.escape(part)
                # Bold **text**
                import re
                escaped = re.sub(
                    r'\*\*(.*?)\*\*',
                    rf'<b style="color:{_CYAN};">\1</b>',
                    escaped
                )
                # Inline `code`
                escaped = re.sub(
                    r'`([^`]+)`',
                    rf'<code style="background:#0a0a1e;color:{_GREEN};padding:1px 4px;">\1</code>',
                    escaped
                )
                escaped = escaped.replace("\n", "<br>")
                result.append(
                    f'<span style="color:{default_color};">{escaped}</span>'
                )
        return "".join(result)


# ── Main chat panel ───────────────────────────────────────────────────────────

QUICK_ACTIONS = [
    ("Win Rate",       "What is my overall win rate and P&L breakdown? Show this week vs all-time."),
    ("Regime",         "What is the current market regime? Show the last 5 regime changes with details."),
    ("Best Setups",    "What are my best performing setups and strategies? Which have the highest win rate?"),
    ("Agent Status",   "Give me a status report on all 8 agents — accuracy, recent lessons, and team IQ from the MetaLearner."),
    ("Signals",        "Show me the 10 most recent signals with confidence scores and outcomes."),
    ("Coaching",       "Analyze my last 10 trades and give me specific coaching feedback on what I should improve."),
    ("Strategy IQ",    "Which strategies are currently weighted highest by the MetaLearner? Show strategy weights and recent performance."),
]


class AIChatPanel(QWidget):
    """Full AI assistant chat panel."""

    def __init__(self, db_path: str, config=None, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._config = config
        self._assistant = None
        self._thinking_bubble = None
        self._worker_thread = None
        self._busy = False

        self.setStyleSheet(_STYLE)
        self._build_ui()
        self._init_assistant()
        self._load_history()

    def _init_assistant(self):
        from ..ai.llm_assistant import LLMAssistant
        api_key = ""
        if self._config is not None:
            try:
                api_key = getattr(self._config, "anthropic_api_key", "") or ""
            except Exception:
                pass
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        bypass_mode = False
        if self._config is not None:
            bypass_mode = getattr(self._config, "anthropic_bypass_mode", False)
        self._assistant = LLMAssistant(db_path=self._db_path, api_key=api_key, bypass_mode=bypass_mode)

    def refresh_api_key(self, key: str):
        """Called when user saves a new API key in settings."""
        if self._assistant:
            self._assistant.set_api_key(key)
            if self._config is not None:
                self._assistant.set_bypass_mode(getattr(self._config, "anthropic_bypass_mode", False))

    def set_bypass_mode(self, enabled: bool):
        """Enable or disable LLM assistant bypass mode from settings."""
        if self._assistant:
            self._assistant.set_bypass_mode(enabled)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setFixedHeight(48)
        hdr.setStyleSheet(
            f"QFrame {{ background: {_BG2}; border-bottom: 1px solid {_CYAN_DIM}; }}"
        )
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(16, 0, 16, 0)
        hdr_layout.setSpacing(12)

        icon_lbl = QLabel("◈◈")
        icon_lbl.setStyleSheet(
            f"color: {_CYAN}; font-size: 18px; font-weight: bold; "
            f"letter-spacing: 4px; background: transparent;"
        )
        title_lbl = QLabel("MES INTEL  AI  ASSISTANT")
        title_lbl.setStyleSheet(
            f"color: {_CYAN}; font-size: 14px; font-weight: bold; "
            f"letter-spacing: 4px; background: transparent;"
        )
        model_lbl = QLabel("claude-sonnet-4")
        model_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 9px; letter-spacing: 2px; background: transparent;"
        )
        clear_btn = QPushButton("◻ CLEAR")
        clear_btn.setFixedWidth(80)
        clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_DIM}; border: 1px solid {_DIM}; "
            f"padding: 3px 8px; font-size: 9px; letter-spacing: 1px; }} "
            f"QPushButton:hover {{ color: {_RED}; border-color: {_RED}; }}"
        )
        clear_btn.clicked.connect(self._clear_chat)

        hdr_layout.addWidget(icon_lbl)
        hdr_layout.addWidget(title_lbl)
        hdr_layout.addWidget(model_lbl)
        hdr_layout.addStretch()
        hdr_layout.addWidget(clear_btn)
        outer.addWidget(hdr)

        # ── Quick action buttons ──────────────────────────────────────
        qa_frame = QFrame()
        qa_frame.setFixedHeight(36)
        qa_frame.setStyleSheet(
            f"QFrame {{ background: {_BG2}; border-bottom: 1px solid {_DIM}; }}"
        )
        qa_layout = QHBoxLayout(qa_frame)
        qa_layout.setContentsMargins(8, 4, 8, 4)
        qa_layout.setSpacing(6)

        qa_lbl = QLabel("QUICK:")
        qa_lbl.setStyleSheet(f"color: {_DIM}; font-size: 9px; letter-spacing: 1px;")
        qa_layout.addWidget(qa_lbl)

        for label, prompt in QUICK_ACTIONS:
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                f"QPushButton {{ background: #0a0a1a; color: {_CYAN_DIM}; "
                f"border: 1px solid #1a1a3a; padding: 2px 10px; "
                f"font-size: 9px; letter-spacing: 1px; }} "
                f"QPushButton:hover {{ background: #10103a; color: {_CYAN}; "
                f"border-color: {_CYAN}; }}"
            )
            btn.clicked.connect(lambda checked=False, p=prompt: self._send_message(p))
            qa_layout.addWidget(btn)
        qa_layout.addStretch()
        outer.addWidget(qa_frame)

        # ── Chat scroll area ──────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)

        self._chat_container = QWidget()
        self._chat_container.setStyleSheet(f"background: {_BG};")
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(8, 8, 8, 8)
        self._chat_layout.setSpacing(6)
        self._chat_layout.addStretch()

        self._scroll.setWidget(self._chat_container)
        outer.addWidget(self._scroll, 1)

        # ── Status bar (shows tool calls in progress) ─────────────────
        self._status_frame = QFrame()
        self._status_frame.setFixedHeight(20)
        self._status_frame.setStyleSheet(
            f"QFrame {{ background: {_BG2}; border-top: 1px solid {_DIM}; border-bottom: 1px solid {_DIM}; }}"
        )
        sl = QHBoxLayout(self._status_frame)
        sl.setContentsMargins(12, 0, 12, 0)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {_MAGENTA}; font-size: 9px; letter-spacing: 1px; background: transparent;"
        )
        sl.addWidget(self._status_lbl)
        sl.addStretch()
        self._token_lbl = QLabel("")
        self._token_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 9px; background: transparent;"
        )
        sl.addWidget(self._token_lbl)
        outer.addWidget(self._status_frame)

        # ── Input row ─────────────────────────────────────────────────
        input_frame = QFrame()
        input_frame.setFixedHeight(54)
        input_frame.setStyleSheet(
            f"QFrame {{ background: {_BG2}; border-top: 1px solid {_CYAN_DIM}; }}"
        )
        in_layout = QHBoxLayout(input_frame)
        in_layout.setContentsMargins(12, 8, 12, 8)
        in_layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText(
            "Ask anything... e.g. 'What's my win rate this week?' or 'Show me the momentum strategy code'"
        )
        self._input.returnPressed.connect(self._on_enter)

        self._send_btn = QPushButton("▶ SEND")
        self._send_btn.setFixedWidth(90)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: #0a1a0a; color: {_GREEN}; "
            f"border: 1px solid {_GREEN}; padding: 6px 12px; "
            f"font-size: 11px; letter-spacing: 2px; font-weight: bold; }} "
            f"QPushButton:hover {{ background: #143014; }} "
            f"QPushButton:disabled {{ color: {_DIM}; border-color: {_DIM}; background: {_BG3}; }}"
        )
        self._send_btn.clicked.connect(self._on_enter)

        in_layout.addWidget(self._input)
        in_layout.addWidget(self._send_btn)
        outer.addWidget(input_frame)

    def _load_history(self):
        """Load persisted chat history from DB."""
        if self._assistant is None:
            return
        try:
            history = self._assistant.load_history(limit=50)
            for entry in history:
                role = entry.get("role", "assistant")
                content = entry.get("content", "")
                tc = entry.get("tool_calls_json", "")
                if role in ("user", "assistant") and content:
                    self._add_bubble(role, content, tc)
        except Exception as exc:
            log.debug("History load error: %s", exc)

    def _add_bubble(self, role: str, content: str, tool_calls_json: str = ""):
        """Insert a message bubble into the chat."""
        bubble = _MessageBubble(role, content, tool_calls_json, self._chat_container)
        # Insert before the trailing stretch
        count = self._chat_layout.count()
        self._chat_layout.insertWidget(count - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self):
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _on_enter(self):
        text = self._input.text().strip()
        if text:
            self._input.clear()
            self._send_message(text)

    def _send_message(self, text: str):
        if self._busy:
            return
        if not self._assistant:
            self._add_bubble("assistant", "ERROR: Assistant not initialized.")
            return

        # Check API key
        api_key = ""
        if self._config is not None:
            try:
                api_key = getattr(self._config, "anthropic_api_key", "") or ""
            except Exception:
                pass
        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            self._add_bubble(
                "assistant",
                "**No API key configured.**\n\n"
                "To use the AI Assistant:\n"
                "1. Get a free API key at **console.anthropic.com**\n"
                "2. Go to `Settings → ⚙ CONFIG`\n"
                "3. Paste your key in the **ANTHROPIC API KEY** field\n"
                "4. Click SAVE\n\n"
                "Or set `ANTHROPIC_API_KEY` in your environment."
            )
            return

        self._assistant.set_api_key(api_key)

        # Show user bubble
        self._add_bubble("user", text)
        self._assistant.save_to_db("user", text)

        # Show thinking bubble
        self._thinking_bubble = _MessageBubble("thinking", "⟳ THINKING", parent=self._chat_container)
        count = self._chat_layout.count()
        self._chat_layout.insertWidget(count - 1, self._thinking_bubble)
        self._scroll_to_bottom()

        # Disable input
        self._busy = True
        self._send_btn.setEnabled(False)
        self._input.setEnabled(False)
        self._status_lbl.setText("◈ Contacting Claude API...")

        # Spawn worker thread
        self._worker_thread = QThread()
        self._worker = _LLMWorker(self._assistant, text)
        self._worker.moveToThread(self._worker_thread)

        self._worker.tool_called.connect(self._on_tool_called)
        self._worker.response_ready.connect(self._on_response_ready)
        self._worker.error_occurred.connect(self._on_error)

        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.start()

    def _on_tool_called(self, tool_name: str, input_preview: str):
        self._status_lbl.setText(f"⚡ {tool_name}({input_preview[:60]})")

    def _on_response_ready(self, text: str, tool_calls_json: str, tokens: int):
        self._cleanup_worker()
        if self._thinking_bubble:
            self._thinking_bubble.stop_thinking()
            idx = self._chat_layout.indexOf(self._thinking_bubble)
            if idx >= 0:
                self._chat_layout.takeAt(idx)
                self._thinking_bubble.deleteLater()
            self._thinking_bubble = None

        self._add_bubble("assistant", text, tool_calls_json)
        if self._assistant:
            self._assistant.save_to_db("assistant", text, tool_calls_json, tokens)

        self._status_lbl.setText("")
        self._token_lbl.setText(f"tokens: {tokens:,}")
        self._busy = False
        self._send_btn.setEnabled(True)
        self._input.setEnabled(True)
        self._input.setFocus()

    def _on_error(self, error: str):
        self._cleanup_worker()
        if self._thinking_bubble:
            self._thinking_bubble.stop_thinking()
            idx = self._chat_layout.indexOf(self._thinking_bubble)
            if idx >= 0:
                self._chat_layout.takeAt(idx)
                self._thinking_bubble.deleteLater()
            self._thinking_bubble = None

        msg = f"**ERROR:** {error}"
        if "api_key" in error.lower() or "ANTHROPIC_API_KEY" in error:
            msg += "\n\nGo to **Settings → ⚙ CONFIG** and enter your Anthropic API key."
        elif "anthropic" in error.lower() and "install" in error.lower():
            msg += "\n\nRun: `pip install anthropic`"
        self._add_bubble("assistant", msg)
        self._status_lbl.setText(f"ERROR: {error[:80]}")
        self._busy = False
        self._send_btn.setEnabled(True)
        self._input.setEnabled(True)

    def _cleanup_worker(self):
        try:
            if self._worker_thread and self._worker_thread.isRunning():
                self._worker_thread.quit()
                self._worker_thread.wait(2000)
        except Exception:
            pass
        self._worker_thread = None
        self._worker = None

    def _clear_chat(self):
        """Remove all message bubbles from UI (does not erase DB history)."""
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        if self._assistant:
            self._assistant.reset_conversation()
        self._token_lbl.setText("")
        self._status_lbl.setText("")
