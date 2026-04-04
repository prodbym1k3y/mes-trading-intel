"""LLM Assistant backend — Claude API with tool calling for MES Intel.

Tools available to the LLM:
  - query_database(sql)      : run a SELECT query against mes_intel.db
  - read_file(path)          : read any file under ~/trading/
  - list_files(directory)    : list files in a directory under ~/trading/
  - get_agent_status(name)   : pull agent knowledge / accuracy from DB
  - get_current_signals()    : latest N signals from DB
  - get_market_regime()      : most recent regime from DB
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TRADING_ROOT = Path.home() / "trading"
MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """\
You are the MES Intel AI Assistant — an expert trading intelligence advisor embedded in \
a professional MES (Micro E-mini S&P 500) futures trading platform.

## Platform Context
- The platform is MES Intel v4.0, a multi-agent ensemble trading system running on AMP/Rithmic/ATAS
- It runs 8 autonomous agents: SignalEngine, TradeJournal, ChartMonitor, MetaLearner, \
NewsScanner, DarkPoolAgent, MarketBrain, AppOptimizer
- 24 quantitative strategies vote on signals via ensemble with dynamic weighting
- Database: SQLite with 28 tables (signals, trades, agent_knowledge, learning_history, \
strategy_weights_history, market_patterns, market_regimes, usage_analytics, agent_accuracy, etc.)
- The trader uses high-confidence setups only (min 0.70 ensemble confidence, 3+ strategies agree)

## Key Tables
- signals: generated trade signals (direction, confidence, ensemble_score, regime)
- trades: executed trades (entry/exit price, pnl, mae, mfe, r_multiple, emotion, tags)
- trade_grades: AI-graded trade quality (setup_quality, entry_timing, exit_timing, overall_grade)
- agent_knowledge: agent-specific learned data (key/value by agent_name and knowledge_type)
- learning_history: lessons learned per agent (lesson_type, description, impact_score)
- strategy_weights_history: dynamic strategy weights (weight, cumulative_reward, win_count, loss_count)
- market_patterns: historical pattern memory (pattern_type, conditions_json, outcome, confidence)
- market_regimes: regime history (regime, volatility, trend_strength, features_json)
- agent_accuracy: per-agent signal accuracy by regime (win_count, loss_count, avg_confidence)
- usage_analytics: UI usage tracking (event_type, tab_name, feature_name)
- journal_trades: trade journal entries with full context

## Your Role
- Answer trading questions using real data from the database
- Analyze trade performance, win rates, patterns, and coaching insights
- Explain what the agents are doing and what they've learned
- Read and explain code files in the trading project
- Be concise, data-driven, and use a sharp professional tone
- Format numbers clearly: prices to 2 decimals, percentages to 1 decimal
- Always show what SQL you ran so the trader can learn the schema

## Constraints
- Database: SELECT queries only — never INSERT/UPDATE/DELETE
- Files: read-only access under ~/trading/ only
- Focus on MES futures trading context

## Risk/BYPASS Policy
- If you think the user is about to request something that could impact privacy, security, or workflow correctness, ask for explicit bypass confirmation.
- Use the token phrase: **BYPASS_CONFIRMATION_REQUIRED** and then explain what the action would do and why it needs approval.
- If the user sets bypass mode on, avoid executing potentially risky non-core actions and keep responses short and safe.
"""

# ── Tool definitions sent to Claude ──────────────────────────────────────────

TOOLS = [
    {
        "name": "query_database",
        "description": (
            "Execute a read-only SELECT query against the MES Intel SQLite database. "
            "Use this to answer questions about trades, signals, agent performance, "
            "market patterns, win rates, P&L, etc. ONLY SELECT statements are allowed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A valid SQLite SELECT statement. No INSERT/UPDATE/DELETE.",
                }
            },
            "required": ["sql"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file under ~/trading/. "
            "Useful for reading strategy code, config files, agent source, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to ~/trading/ (e.g. 'mes_intel/strategies/momentum.py')",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List files and directories under ~/trading/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path relative to ~/trading/ (e.g. 'mes_intel/strategies'). Use '.' for root.",
                }
            },
            "required": ["directory"],
        },
    },
    {
        "name": "get_agent_status",
        "description": (
            "Get the current status, knowledge, and accuracy stats for a specific agent. "
            "Agents: SignalEngine, TradeJournal, ChartMonitor, MetaLearner, NewsScanner, "
            "DarkPoolAgent, MarketBrain, AppOptimizer"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent (e.g. 'MetaLearner', 'MarketBrain', 'SignalEngine')",
                }
            },
            "required": ["agent_name"],
        },
    },
    {
        "name": "get_current_signals",
        "description": "Get the most recent trading signals generated by the ensemble.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent signals to return (default 10, max 50)",
                    "default": 10,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_market_regime",
        "description": "Get the current and recent market regime classifications from MarketBrain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent regime entries to return (default 5)",
                    "default": 5,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_strategy_brain_report",
        "description": (
            "Get the brain's intelligence report on all 35 strategies. Shows each strategy's "
            "current weight, accuracy, win rate, decay status (improving/stable/decaying), "
            "cumulative P&L, regime performance breakdown, and learning trajectory. "
            "Use this to answer questions like 'Which strategies are working best right now?' "
            "or 'What's decaying?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_pattern_insight",
        "description": (
            "Query the brain's pattern memory for the current regime, day, and time. "
            "Returns historical win rate, sample size, best-performing strategy combos, "
            "and whether the brain recommends boosting or suppressing signals right now. "
            "Use this to answer 'Should I trade right now?' or 'What works best on Fridays in trending?'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "regime": {
                    "type": "string",
                    "description": "Market regime to query (e.g. 'trending', 'ranging', 'volatile'). Leave empty for current.",
                    "default": "",
                }
            },
            "required": [],
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────────────────

class ToolExecutor:
    """Executes LLM tool calls safely."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def execute(self, tool_name: str, tool_input: dict) -> str:
        try:
            if tool_name == "query_database":
                return self._query_database(tool_input["sql"])
            elif tool_name == "read_file":
                return self._read_file(tool_input["path"])
            elif tool_name == "list_files":
                return self._list_files(tool_input["directory"])
            elif tool_name == "get_agent_status":
                return self._get_agent_status(tool_input["agent_name"])
            elif tool_name == "get_current_signals":
                limit = tool_input.get("limit", 10)
                return self._get_current_signals(min(int(limit), 50))
            elif tool_name == "get_market_regime":
                limit = tool_input.get("limit", 5)
                return self._get_market_regime(min(int(limit), 20))
            elif tool_name == "get_strategy_brain_report":
                return self._get_strategy_brain_report()
            elif tool_name == "get_pattern_insight":
                regime = tool_input.get("regime", "")
                return self._get_pattern_insight(regime)
            else:
                return f"ERROR: Unknown tool '{tool_name}'"
        except Exception as exc:
            log.warning("Tool %s error: %s", tool_name, exc)
            return f"ERROR executing {tool_name}: {exc}"

    def _query_database(self, sql: str) -> str:
        sql_stripped = sql.strip().upper()
        # Safety: only SELECT
        if not sql_stripped.startswith("SELECT") and not sql_stripped.startswith("WITH"):
            return "ERROR: Only SELECT queries are allowed. No INSERT/UPDATE/DELETE/DROP."
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql)
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return "Query returned 0 rows."
            # Format as text table
            cols = rows[0].keys()
            lines = ["  ".join(str(c).upper() for c in cols)]
            lines.append("─" * min(len(lines[0]), 120))
            for row in rows[:200]:
                lines.append("  ".join(str(row[c]) if row[c] is not None else "NULL" for c in cols))
            if len(rows) > 200:
                lines.append(f"... ({len(rows) - 200} more rows truncated)")
            return f"Returned {len(rows)} row(s):\n" + "\n".join(lines)
        except Exception as exc:
            return f"SQL ERROR: {exc}"

    def _read_file(self, rel_path: str) -> str:
        # Sanitize: must stay under ~/trading/
        target = (TRADING_ROOT / rel_path).resolve()
        if not str(target).startswith(str(TRADING_ROOT.resolve())):
            return "ERROR: Access denied — path must be under ~/trading/"
        if not target.exists():
            return f"ERROR: File not found: {target}"
        if target.is_dir():
            return f"ERROR: {rel_path} is a directory. Use list_files instead."
        try:
            content = target.read_text(errors="replace")
            lines = content.splitlines()
            if len(lines) > 500:
                content = "\n".join(lines[:500]) + f"\n... ({len(lines) - 500} more lines truncated)"
            return f"File: {rel_path}  ({len(lines)} lines)\n\n{content}"
        except Exception as exc:
            return f"ERROR reading file: {exc}"

    def _list_files(self, rel_dir: str) -> str:
        target = (TRADING_ROOT / rel_dir).resolve()
        if not str(target).startswith(str(TRADING_ROOT.resolve())):
            return "ERROR: Access denied — path must be under ~/trading/"
        if not target.exists():
            return f"ERROR: Directory not found: {target}"
        if not target.is_dir():
            return f"ERROR: {rel_dir} is a file, not a directory."
        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines = []
            for e in entries[:200]:
                if e.name.startswith("__pycache__"):
                    continue
                kind = "DIR " if e.is_dir() else "FILE"
                size = ""
                if e.is_file():
                    try:
                        lc = len(e.read_text(errors="replace").splitlines())
                        size = f"  ({lc} lines)"
                    except Exception:
                        size = f"  ({e.stat().st_size} bytes)"
                lines.append(f"  {kind}  {e.name}{size}")
            return f"Contents of {rel_dir}/  ({len(lines)} entries):\n" + "\n".join(lines)
        except Exception as exc:
            return f"ERROR listing directory: {exc}"

    def _get_agent_status(self, agent_name: str) -> str:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            results = []

            # Agent knowledge
            rows = conn.execute(
                "SELECT knowledge_type, key, value_json, confidence, updated_at "
                "FROM agent_knowledge WHERE agent_name = ? ORDER BY updated_at DESC LIMIT 20",
                (agent_name,),
            ).fetchall()
            if rows:
                results.append(f"=== {agent_name} KNOWLEDGE ({len(rows)} entries) ===")
                for r in rows:
                    val = r["value_json"]
                    if val and len(val) > 120:
                        val = val[:120] + "..."
                    results.append(
                        f"  [{r['knowledge_type']}] {r['key']}: {val}  (conf={r['confidence']:.2f})"
                    )
            else:
                results.append(f"=== {agent_name}: no knowledge entries found ===")

            # Agent accuracy
            acc_rows = conn.execute(
                "SELECT signal_type, regime, win_count, loss_count, avg_confidence "
                "FROM agent_accuracy WHERE agent_name = ? ORDER BY win_count + loss_count DESC LIMIT 10",
                (agent_name,),
            ).fetchall()
            if acc_rows:
                results.append(f"\n=== {agent_name} ACCURACY ===")
                for r in acc_rows:
                    total = (r["win_count"] or 0) + (r["loss_count"] or 0)
                    wr = (r["win_count"] / total * 100) if total > 0 else 0
                    results.append(
                        f"  {r['signal_type']} / {r['regime']}: "
                        f"{wr:.1f}% WR  ({r['win_count']}W/{r['loss_count']}L)  "
                        f"avg_conf={r['avg_confidence']:.2f}"
                    )

            # Recent learning
            learn_rows = conn.execute(
                "SELECT lesson_type, description, impact_score, timestamp "
                "FROM learning_history WHERE agent_name = ? ORDER BY timestamp DESC LIMIT 5",
                (agent_name,),
            ).fetchall()
            if learn_rows:
                results.append(f"\n=== {agent_name} RECENT LESSONS ===")
                for r in learn_rows:
                    results.append(
                        f"  [{r['lesson_type']}] {r['description'][:100]}  (impact={r['impact_score']:.2f})"
                    )

            conn.close()
            return "\n".join(results) if results else f"No data found for agent: {agent_name}"
        except Exception as exc:
            return f"ERROR getting agent status: {exc}"

    def _get_current_signals(self, limit: int = 10) -> str:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, datetime(timestamp, 'unixepoch') as time, direction, "
                "confidence, ensemble_score, strategies_agree, entry_price, "
                "stop_price, target_price, regime, status "
                "FROM signals ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            if not rows:
                return "No signals in database yet."
            lines = [f"=== LATEST {len(rows)} SIGNALS ==="]
            for r in rows:
                lines.append(
                    f"  [{r['time']}] {r['direction']:5s}  conf={r['confidence']:.2f}  "
                    f"ensemble={r['ensemble_score']:.2f}  agree={r['strategies_agree']}  "
                    f"entry={r['entry_price']}  regime={r['regime']}  [{r['status']}]"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"ERROR getting signals: {exc}"

    def _get_market_regime(self, limit: int = 5) -> str:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT datetime(timestamp, 'unixepoch') as time, regime, "
                "volatility, trend_strength "
                "FROM market_regimes ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            if not rows:
                # Fall back to regime_history table
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT datetime(timestamp, 'unixepoch') as time, regime, confidence "
                    "FROM regime_history ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                conn.close()
            if not rows:
                return "No regime data available yet."
            lines = ["=== MARKET REGIME HISTORY ==="]
            for r in rows:
                row_dict = dict(r)
                parts = [f"  [{row_dict.get('time', '?')}]", f"regime={row_dict.get('regime', '?')}"]
                if "volatility" in row_dict and row_dict["volatility"] is not None:
                    parts.append(f"vol={row_dict['volatility']:.3f}")
                if "trend_strength" in row_dict and row_dict["trend_strength"] is not None:
                    parts.append(f"trend={row_dict['trend_strength']:.3f}")
                if "confidence" in row_dict and row_dict["confidence"] is not None:
                    parts.append(f"conf={row_dict['confidence']:.2f}")
                lines.append("  ".join(parts))
            return "\n".join(lines)
        except Exception as exc:
            return f"ERROR getting regime: {exc}"

    def _get_strategy_brain_report(self) -> str:
        """Get the brain's full strategy intelligence report."""
        try:
            from ..agents.meta_learner import MetaLearner
            # Find meta_learner via event bus subscribers or direct access
            report = []
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            # Pull strategy tracker data from agent_knowledge
            rows = conn.execute(
                "SELECT agent_name, key, value_json FROM agent_knowledge "
                "WHERE knowledge_type = 'tracker' AND key = 'state' "
                "ORDER BY agent_name"
            ).fetchall()
            conn.close()

            if not rows:
                return "No strategy brain data yet. Trades are needed for the brain to learn."

            lines = ["=== STRATEGY BRAIN REPORT ===", ""]
            for r in rows:
                name = r["agent_name"].replace("strategy:", "")
                try:
                    v = json.loads(r["value_json"]) if isinstance(r["value_json"], str) else r["value_json"]
                except Exception:
                    v = {}
                acc = v.get("accuracy", 0)
                wr = v.get("win_rate", 0)
                weight = v.get("weight", 1.0)
                ks = v.get("knowledge_score", 1.0)
                lessons = v.get("lessons_learned", 0)

                status = "SHARP" if acc >= 0.6 else ("WEAK" if acc < 0.4 else "OK")
                lines.append(
                    f"  {name:30s} weight={weight:.2f}  acc={acc:.0%}  "
                    f"WR={wr:.0%}  lessons={lessons}  [{status}]"
                )

            # Pull weight history
            wh = conn if hasattr(conn, 'execute') else sqlite3.connect(self.db_path)
            try:
                conn2 = sqlite3.connect(self.db_path)
                conn2.row_factory = sqlite3.Row
                weight_rows = conn2.execute(
                    "SELECT strategy_name, weight, win_count, loss_count "
                    "FROM strategy_weights_history "
                    "ORDER BY timestamp DESC LIMIT 50"
                ).fetchall()
                conn2.close()
                if weight_rows:
                    lines.append("")
                    lines.append("=== RECENT WEIGHT CHANGES ===")
                    seen = set()
                    for wr in weight_rows:
                        name = wr["strategy_name"]
                        if name not in seen:
                            seen.add(name)
                            w = wr["weight"]
                            wins = wr["win_count"]
                            losses = wr["loss_count"]
                            lines.append(f"  {name:30s} w={w:.3f}  W={wins} L={losses}")
            except Exception:
                pass

            return "\n".join(lines)
        except Exception as exc:
            return f"ERROR getting brain report: {exc}"

    def _get_pattern_insight(self, regime: str = "") -> str:
        """Query the brain's pattern memory."""
        try:
            import time as _time
            from datetime import datetime

            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                "SELECT key, value_json, confidence FROM agent_knowledge "
                "WHERE agent_name = 'meta_learner' AND knowledge_type = 'trade_pattern' "
                "ORDER BY confidence DESC"
            ).fetchall()
            conn.close()

            if not rows:
                return "No pattern data yet. The brain needs trades to build pattern memory."

            now = datetime.now()
            current_key = f"{regime or 'unknown'}_{now.strftime('%A')}_{now.hour:02d}"

            lines = ["=== BRAIN PATTERN MEMORY ===", ""]
            lines.append(f"Current: {now.strftime('%A')} {now.hour:02d}:00 | Regime: {regime or 'unknown'}")
            lines.append(f"Looking for pattern: {current_key}")
            lines.append("")

            # Show current pattern first
            found_current = False
            for r in rows:
                try:
                    v = json.loads(r["value_json"]) if isinstance(r["value_json"], str) else r["value_json"]
                except Exception:
                    v = {}
                key = r["key"]
                wins = v.get("wins", 0)
                losses = v.get("losses", 0)
                total = wins + losses
                if total == 0:
                    continue

                wr = wins / total
                strats = v.get("strategies", {})
                top_strats = sorted(strats.items(), key=lambda x: x[1], reverse=True)[:3]
                strats_str = ", ".join(f"{s}({c})" for s, c in top_strats) if top_strats else "none"

                marker = " ◀ CURRENT" if key == current_key else ""
                if key == current_key:
                    found_current = True

                rec = "BOOST ↑" if wr >= 0.6 and total >= 10 else (
                    "SUPPRESS ↓" if wr <= 0.35 and total >= 10 else "NEUTRAL")
                lines.append(
                    f"  {key:35s} WR={wr:.0%} ({wins}W/{losses}L)  "
                    f"top=[{strats_str}]  → {rec}{marker}"
                )

            if not found_current:
                lines.append(f"\n  No pattern data for {current_key} yet.")

            return "\n".join(lines)
        except Exception as exc:
            return f"ERROR getting pattern insight: {exc}"


# ── Main assistant class ──────────────────────────────────────────────────────

class LLMAssistant:
    """Manages conversation with Claude, tool calls, and chat history persistence."""

    def __init__(self, db_path: str, api_key: str = "", bypass_mode: bool = False):
        self.db_path = db_path
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._executor = ToolExecutor(db_path)
        self._client = None
        self._messages: list[dict] = []  # conversation history
        self._bypass_mode = bool(bypass_mode)

    def set_bypass_mode(self, enabled: bool):
        self._bypass_mode = bool(enabled)

    def get_bypass_mode(self) -> bool:
        return self._bypass_mode

    def _build_system_prompt(self) -> str:
        if self._bypass_mode:
            return SYSTEM_PROMPT + "\n[System status: BYPASS MODE ENABLED - avoid non-essential actions, do not execute tool calls without explicit user permission.]"
        return SYSTEM_PROMPT + "\n[System status: BYPASS MODE DISABLED - normal operation.]"

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                key = self._api_key or os.environ.get("ANTHROPIC_API_KEY", "")
                if not key:
                    raise ValueError(
                        "ANTHROPIC_API_KEY not set. Add it in Settings → AI ASSISTANT tab."
                    )
                self._client = anthropic.Anthropic(api_key=key)
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
        return self._client

    def set_api_key(self, key: str):
        self._api_key = key
        self._client = None  # force re-init with new key

    def reset_conversation(self):
        self._messages = []

    def chat(
        self,
        user_message: str,
        on_tool_call: "callable | None" = None,
    ) -> tuple[str, list[dict], int]:
        """Send a message and get a response.

        Returns:
            (response_text, tool_calls_log, tokens_used)
        """
        client = self._get_client()
        self._messages.append({"role": "user", "content": user_message})

        tool_calls_log: list[dict] = []
        total_tokens = 0

        # Agentic loop — may iterate multiple times if tool use is needed
        while True:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=self._build_system_prompt(),
                tools=TOOLS,
                messages=self._messages,
            )
            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            if response.stop_reason == "tool_use":
                # Process all tool calls in this response
                assistant_content = response.content
                self._messages.append({"role": "assistant", "content": assistant_content})

                if self._bypass_mode:
                    notice = (
                        "BYPASS MODE ENABLED: tool calls have been skipped to preserve privacy "
                        "and workflow safety. Disable bypass mode to execute tools."
                    )
                    self._messages.append({"role": "assistant", "content": notice})
                    return notice, tool_calls_log, total_tokens

                tool_results = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        if on_tool_call:
                            on_tool_call(tool_name, tool_input)
                        result = self._executor.execute(tool_name, tool_input)
                        tool_calls_log.append({
                            "tool": tool_name,
                            "input": tool_input,
                            "result_preview": result[:300],
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                self._messages.append({"role": "user", "content": tool_results})

            else:
                # Final text response
                text_parts = []
                for block in response.content:
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
                final_text = "\n".join(text_parts)
                self._messages.append({"role": "assistant", "content": final_text})
                return final_text, tool_calls_log, total_tokens

    def save_to_db(
        self,
        role: str,
        content: str,
        tool_calls_json: str = "",
        tokens_used: int = 0,
    ):
        """Persist a chat message to chat_history table."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO chat_history (timestamp, role, content, tool_calls_json, tokens_used) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), role, content, tool_calls_json, tokens_used),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            log.debug("chat_history insert failed: %s", exc)

    def load_history(self, limit: int = 100) -> list[dict]:
        """Load recent chat history from DB."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT timestamp, role, content, tool_calls_json, tokens_used "
                "FROM chat_history ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in reversed(rows)]
        except Exception as exc:
            log.debug("chat_history load failed: %s", exc)
            return []
