"""SQLite database schema for MES Trading Intelligence System.

Tables:
  - signals: generated trade signals with ensemble scores
  - trades: executed trades with entry/exit/PnL
  - trade_grades: quantitative grading of each trade
  - strategy_scores: per-strategy scores for each signal
  - model_performance: ML model accuracy tracking over time
  - regime_history: HMM regime classifications
  - news_events: market-moving news with sentiment scores
  - order_flow_snapshots: periodic order flow state captures
  - daily_stats: aggregated daily trading statistics
  - dark_pool_prints: dark pool / off-exchange significant prints
  - confluence_zones: detected confluence zones with triggers
  - ml_training_runs: ML model training run metrics
  - news_impact_history: historical news category impact analysis
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


def init_db(db_path: str) -> str:
    """Initialize the database schema. Returns the db_path."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    return db_path


def _migrate(conn):
    """Add new columns to existing tables without breaking old data."""
    new_cols = [
        ("trades", "emotion",           "TEXT"),
        ("trades", "tags",              "TEXT"),
        ("trades", "ai_grade",          "TEXT"),
        ("trades", "ai_analysis_json",  "TEXT"),
        ("trades", "screenshot_path",   "TEXT"),
        ("trades", "mae",               "REAL"),
        ("trades", "mfe",               "REAL"),
        ("trades", "market_regime",     "TEXT"),
        ("trades", "time_of_day",       "TEXT"),
        ("trades", "day_of_week",       "TEXT"),
        ("trades", "quant_context_json","TEXT"),
    ]
    existing_trades = {
        row[1]
        for row in conn.execute("PRAGMA table_info(trades)").fetchall()
    }
    for table, col, dtype in new_cols:
        if col not in existing_trades:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass

    autonomy_cols = [
        ("context_key", "TEXT DEFAULT 'global'"),
        ("validation_status", "TEXT DEFAULT 'pending'"),
        ("validation_checked_at", "REAL"),
        ("validation_notes", "TEXT DEFAULT ''"),
        ("reverted", "INTEGER DEFAULT 0"),
        ("reverted_at", "REAL"),
        ("revert_notes", "TEXT DEFAULT ''"),
    ]
    existing_autonomy = {
        row[1]
        for row in conn.execute("PRAGMA table_info(autonomous_changes)").fetchall()
    }
    for col, dtype in autonomy_cols:
        if col not in existing_autonomy:
            try:
                conn.execute(f"ALTER TABLE autonomous_changes ADD COLUMN {col} {dtype}")
            except sqlite3.OperationalError:
                pass

    # Ensure learning tables exist (idempotent)
    conn.executescript(LEARNING_SCHEMA)
    # Ensure Phase 4 tables exist (idempotent)
    conn.executescript(PHASE4_SCHEMA)
    # Ensure AI chat history table exists (idempotent)
    conn.executescript(AI_CHAT_SCHEMA)
    # Ensure autonomous optimization tables exist (idempotent)
    conn.executescript(AUTONOMY_SCHEMA)


@contextmanager
def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class Database:
    """Database access layer."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        init_db(db_path)

    @contextmanager
    def conn(self):
        with _connect(self.db_path) as c:
            yield c

    # --- Signals ---

    def insert_signal(self, signal: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO signals (timestamp, direction, confidence, ensemble_score,
                    strategies_agree, entry_price, stop_price, target_price, regime, status)
                VALUES (:timestamp, :direction, :confidence, :ensemble_score,
                    :strategies_agree, :entry_price, :stop_price, :target_price, :regime, :status)
            """, signal)
            signal_id = cur.lastrowid
            # Insert per-strategy scores
            for strat_name, score_data in signal.get("strategy_scores", {}).items():
                c.execute("""
                    INSERT INTO strategy_scores (signal_id, strategy_name, score, direction, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (signal_id, strat_name, score_data["score"],
                      score_data.get("direction", ""), str(score_data.get("meta", ""))))
            return signal_id

    def get_signals(self, limit: int = 50, status: Optional[str] = None) -> list[dict]:
        with self.conn() as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM signals WHERE status=? ORDER BY timestamp DESC LIMIT ?",
                    (status, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def update_signal_status(self, signal_id: int, status: str):
        with self.conn() as c:
            c.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))

    def get_strategy_scores(self, limit: int = 500) -> dict:
        """Get per-strategy performance aggregated from strategy_scores + trades.

        Returns dict of {strategy_name: {wins, losses, win_rate, avg_score, total}}.
        """
        with self.conn() as c:
            rows = c.execute("""
                SELECT ss.strategy_name, ss.score, ss.direction,
                       t.pnl, t.status AS trade_status
                FROM strategy_scores ss
                JOIN signals s ON s.id = ss.signal_id
                LEFT JOIN trades t ON t.signal_id = s.id
                WHERE t.status = 'closed'
                ORDER BY s.timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()

        scores: dict = {}
        for row in rows:
            name = row["strategy_name"]
            if name not in scores:
                scores[name] = {
                    "wins": 0, "losses": 0, "total": 0,
                    "sum_score": 0.0, "sum_pnl": 0.0,
                }
            entry = scores[name]
            entry["total"] += 1
            entry["sum_score"] += row["score"] or 0
            pnl = row["pnl"] or 0
            entry["sum_pnl"] += pnl
            if pnl > 0:
                entry["wins"] += 1
            elif pnl < 0:
                entry["losses"] += 1

        # Compute derived metrics
        for name, d in scores.items():
            total = d["total"]
            d["win_rate"] = d["wins"] / total if total > 0 else 0
            d["avg_score"] = d["sum_score"] / total if total > 0 else 0
            d["avg_pnl"] = d["sum_pnl"] / total if total > 0 else 0

        return scores

    # --- Trades ---

    def insert_trade(self, trade: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO trades (signal_id, entry_time, exit_time, direction, quantity,
                    entry_price, exit_price, pnl, fees, stop_price, target_price,
                    r_multiple, hold_time_sec, source, notes, status)
                VALUES (:signal_id, :entry_time, :exit_time, :direction, :quantity,
                    :entry_price, :exit_price, :pnl, :fees, :stop_price, :target_price,
                    :r_multiple, :hold_time_sec, :source, :notes, :status)
            """, trade)
            return cur.lastrowid

    def insert_trade_enhanced(self, trade: dict) -> int:
        """Insert a trade with all extended journal fields (emotion, tags, etc.)."""
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO trades (signal_id, entry_time, exit_time, direction, quantity,
                    entry_price, exit_price, pnl, fees, stop_price, target_price,
                    r_multiple, hold_time_sec, source, notes, status,
                    emotion, tags, ai_grade, ai_analysis_json, screenshot_path, mae, mfe)
                VALUES (:signal_id, :entry_time, :exit_time, :direction, :quantity,
                    :entry_price, :exit_price, :pnl, :fees, :stop_price, :target_price,
                    :r_multiple, :hold_time_sec, :source, :notes, :status,
                    :emotion, :tags, :ai_grade, :ai_analysis_json, :screenshot_path, :mae, :mfe)
            """, trade)
            return cur.lastrowid

    def get_trades(self, limit: int = 100, start_date: Optional[str] = None) -> list[dict]:
        with self.conn() as c:
            if start_date:
                rows = c.execute(
                    "SELECT * FROM trades WHERE entry_time >= ? ORDER BY entry_time DESC LIMIT ?",
                    (start_date, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def update_trade(self, trade_id: int, updates: dict):
        with self.conn() as c:
            sets = ", ".join(f"{k}=?" for k in updates)
            c.execute(f"UPDATE trades SET {sets} WHERE id=?", (*updates.values(), trade_id))

    # --- Trade Grades ---

    def get_trade_grades(self, trade_id: int) -> list[dict]:
        """Get all grades for a specific trade."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM trade_grades WHERE trade_id=? ORDER BY created_at ASC",
                (trade_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def insert_grade(self, grade: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO trade_grades (trade_id, setup_quality, entry_timing, exit_timing,
                    risk_management, plan_adherence, overall_grade, edge_ratio, notes)
                VALUES (:trade_id, :setup_quality, :entry_timing, :exit_timing,
                    :risk_management, :plan_adherence, :overall_grade, :edge_ratio, :notes)
            """, grade)
            return cur.lastrowid

    # --- Model Performance ---

    def log_model_performance(self, record: dict):
        with self.conn() as c:
            c.execute("""
                INSERT INTO model_performance (timestamp, strategy_name, accuracy, precision_score,
                    recall, f1, sharpe, win_rate, profit_factor, sample_size, notes)
                VALUES (:timestamp, :strategy_name, :accuracy, :precision_score,
                    :recall, :f1, :sharpe, :win_rate, :profit_factor, :sample_size, :notes)
            """, record)

    def get_model_performance(self, strategy: str, limit: int = 30) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM model_performance WHERE strategy_name=? ORDER BY timestamp DESC LIMIT ?",
                (strategy, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Regime ---

    def log_regime(self, regime: str, confidence: float, features: str = ""):
        with self.conn() as c:
            c.execute("""
                INSERT INTO regime_history (timestamp, regime, confidence, features)
                VALUES (?, ?, ?, ?)
            """, (time.time(), regime, confidence, features))

    # --- News ---

    def insert_news(self, news: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO news_events (timestamp, headline, source, sentiment_score,
                    market_impact, category, url, is_trump)
                VALUES (:timestamp, :headline, :source, :sentiment_score,
                    :market_impact, :category, :url, :is_trump)
            """, news)
            return cur.lastrowid

    # --- Daily Stats ---

    def upsert_daily_stats(self, stats: dict):
        with self.conn() as c:
            c.execute("""
                INSERT INTO daily_stats (date, total_trades, wins, losses, gross_pnl, net_pnl,
                    fees, largest_win, largest_loss, avg_win, avg_loss, win_rate,
                    profit_factor, max_drawdown, avg_r_multiple, sharpe)
                VALUES (:date, :total_trades, :wins, :losses, :gross_pnl, :net_pnl,
                    :fees, :largest_win, :largest_loss, :avg_win, :avg_loss, :win_rate,
                    :profit_factor, :max_drawdown, :avg_r_multiple, :sharpe)
                ON CONFLICT(date) DO UPDATE SET
                    total_trades=excluded.total_trades, wins=excluded.wins,
                    losses=excluded.losses, gross_pnl=excluded.gross_pnl,
                    net_pnl=excluded.net_pnl, fees=excluded.fees,
                    largest_win=excluded.largest_win, largest_loss=excluded.largest_loss,
                    avg_win=excluded.avg_win, avg_loss=excluded.avg_loss,
                    win_rate=excluded.win_rate, profit_factor=excluded.profit_factor,
                    max_drawdown=excluded.max_drawdown, avg_r_multiple=excluded.avg_r_multiple,
                    sharpe=excluded.sharpe
            """, stats)

    # --- Order Flow ---

    def insert_orderflow_snapshot(self, snapshot: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO order_flow_snapshots (timestamp, price, bid_volume, ask_volume,
                    delta, cumulative_delta, poc_price, vah_price, val_price, data_json)
                VALUES (:timestamp, :price, :bid_volume, :ask_volume,
                    :delta, :cumulative_delta, :poc_price, :vah_price, :val_price, :data_json)
            """, snapshot)
            return cur.lastrowid

    # --- Dark Pool Prints (Phase 2) ---

    def insert_dark_pool_print(self, print_data: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO dark_pool_prints (timestamp, symbol, price, size, notional,
                    venue, is_block, created_at)
                VALUES (:timestamp, :symbol, :price, :size, :notional,
                    :venue, :is_block, CURRENT_TIMESTAMP)
            """, print_data)
            return cur.lastrowid

    def get_dark_pool_prints(self, limit: int = 100, min_notional: float = 0) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM dark_pool_prints WHERE notional >= ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (min_notional, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Confluence Zones (Phase 2) ---

    def insert_confluence_zone(self, zone: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO confluence_zones (timestamp, price, triggers, confluence_score,
                    zone_type, status, created_at)
                VALUES (:timestamp, :price, :triggers, :confluence_score,
                    :zone_type, :status, CURRENT_TIMESTAMP)
            """, zone)
            return cur.lastrowid

    # --- ML Training Runs (Phase 2) ---

    def insert_training_run(self, run: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO ml_training_runs (timestamp, model_name, accuracy, precision_score,
                    recall, f1, sharpe, win_rate, profit_factor, features_used,
                    hyperparams, notes, created_at)
                VALUES (:timestamp, :model_name, :accuracy, :precision_score,
                    :recall, :f1, :sharpe, :win_rate, :profit_factor, :features_used,
                    :hyperparams, :notes, CURRENT_TIMESTAMP)
            """, run)
            return cur.lastrowid

    def get_training_runs(self, model_name: Optional[str] = None, limit: int = 30) -> list[dict]:
        with self.conn() as c:
            if model_name:
                rows = c.execute(
                    "SELECT * FROM ml_training_runs WHERE model_name=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (model_name, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM ml_training_runs ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # --- Learning System ---

    def upsert_agent_knowledge(self, agent_name: str, knowledge_type: str,
                                key: str, value: dict, confidence: float = 1.0):
        """Upsert a knowledge record for an agent."""
        import json as _json
        with self.conn() as c:
            c.execute("""
                INSERT INTO agent_knowledge (agent_name, knowledge_type, key, value_json, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_name, knowledge_type, key) DO UPDATE SET
                    value_json=excluded.value_json,
                    confidence=excluded.confidence,
                    updated_at=excluded.updated_at
            """, (agent_name, knowledge_type, key, _json.dumps(value), confidence, time.time()))

    def get_agent_knowledge(self, agent_name: str,
                             knowledge_type: Optional[str] = None) -> list[dict]:
        import json as _json
        with self.conn() as c:
            if knowledge_type:
                rows = c.execute(
                    "SELECT * FROM agent_knowledge WHERE agent_name=? AND knowledge_type=? "
                    "ORDER BY updated_at DESC",
                    (agent_name, knowledge_type)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM agent_knowledge WHERE agent_name=? ORDER BY updated_at DESC",
                    (agent_name,)
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["value"] = _json.loads(d["value_json"])
                except _json.JSONDecodeError:
                    d["value"] = {}
                result.append(d)
            return result

    def insert_learning_history(self, agent_name: str, lesson_type: str,
                                  description: str, impact_score: float = 0.0,
                                  trade_id: Optional[int] = None) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO learning_history (timestamp, agent_name, lesson_type, description,
                    impact_score, trade_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (time.time(), agent_name, lesson_type, description, impact_score, trade_id))
            return cur.lastrowid

    def get_learning_history(self, agent_name: Optional[str] = None,
                              limit: int = 100) -> list[dict]:
        with self.conn() as c:
            if agent_name:
                rows = c.execute(
                    "SELECT * FROM learning_history WHERE agent_name=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (agent_name, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM learning_history ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def count_lessons_learned(self, agent_name: Optional[str] = None) -> int:
        with self.conn() as c:
            if agent_name:
                row = c.execute(
                    "SELECT COUNT(*) FROM learning_history WHERE agent_name=?", (agent_name,)
                ).fetchone()
            else:
                row = c.execute("SELECT COUNT(*) FROM learning_history").fetchone()
            return row[0] if row else 0

    def upsert_strategy_weight(self, strategy_name: str, weight: float,
                                cumulative_reward: float, win_count: int, loss_count: int):
        with self.conn() as c:
            c.execute("""
                INSERT INTO strategy_weights_history
                    (strategy_name, weight, cumulative_reward, win_count, loss_count, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (strategy_name, weight, cumulative_reward, win_count, loss_count, time.time()))

    def get_strategy_weights_history(self, strategy_name: str, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM strategy_weights_history WHERE strategy_name=? "
                "ORDER BY last_updated DESC LIMIT ?",
                (strategy_name, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def insert_agent_performance(self, agent_name: str, metric_name: str,
                                   value: float, period: str = "session"):
        with self.conn() as c:
            c.execute("""
                INSERT INTO agent_performance (agent_name, metric_name, value, period, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (agent_name, metric_name, value, period, time.time()))

    def get_agent_performance(self, agent_name: str,
                               metric_name: Optional[str] = None,
                               limit: int = 30) -> list[dict]:
        with self.conn() as c:
            if metric_name:
                rows = c.execute(
                    "SELECT * FROM agent_performance WHERE agent_name=? AND metric_name=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (agent_name, metric_name, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM agent_performance WHERE agent_name=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (agent_name, limit)
                ).fetchall()
            return [dict(r) for r in rows]

    # --- News Impact History (Phase 2) ---

    def insert_news_impact(self, impact: dict) -> int:
        with self.conn() as c:
            cur = c.execute("""
                INSERT INTO news_impact_history (headline_pattern, category, avg_price_impact,
                    avg_duration_sec, sample_count, last_updated, created_at)
                VALUES (:headline_pattern, :category, :avg_price_impact,
                    :avg_duration_sec, :sample_count, :last_updated, CURRENT_TIMESTAMP)
            """, impact)
            return cur.lastrowid

    def get_news_impacts(self, category: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            if category:
                rows = c.execute(
                    "SELECT * FROM news_impact_history WHERE category=? "
                    "ORDER BY last_updated DESC LIMIT ?",
                    (category, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM news_impact_history ORDER BY last_updated DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # --- Market Patterns (Phase 4) ---

    def upsert_market_pattern(self, pattern_type: str, conditions_json: str,
                               outcome: str, confidence: float, sample_size: int):
        import json as _json
        with self.conn() as c:
            c.execute("""
                INSERT INTO market_patterns (pattern_type, conditions_json, outcome,
                    confidence, sample_size, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pattern_type, outcome) DO UPDATE SET
                    confidence=excluded.confidence,
                    sample_size=excluded.sample_size,
                    last_seen=excluded.last_seen
            """, (pattern_type, conditions_json, outcome, confidence, sample_size, time.time()))

    def get_market_patterns(self, pattern_type: Optional[str] = None,
                             min_confidence: float = 0.0, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            if pattern_type:
                rows = c.execute(
                    "SELECT * FROM market_patterns WHERE pattern_type=? AND confidence>=? "
                    "ORDER BY confidence DESC LIMIT ?",
                    (pattern_type, min_confidence, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM market_patterns WHERE confidence>=? "
                    "ORDER BY confidence DESC LIMIT ?",
                    (min_confidence, limit)
                ).fetchall()
            return [dict(r) for r in rows]

    # --- Market Regimes (Phase 4) ---

    def insert_market_regime(self, regime: str, volatility: float,
                              trend_strength: float, features_json: str):
        with self.conn() as c:
            c.execute("""
                INSERT INTO market_regimes (timestamp, regime, volatility,
                    trend_strength, features_json)
                VALUES (?, ?, ?, ?, ?)
            """, (time.time(), regime, volatility, trend_strength, features_json))

    def get_market_regimes(self, limit: int = 100) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM market_regimes ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_current_regime(self) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM market_regimes ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    # --- Usage Analytics (Phase 4) ---

    def insert_usage_event(self, event_type: str, tab_name: str = "",
                            feature_name: str = "", duration_seconds: float = 0.0,
                            metadata_json: str = "{}"):
        with self.conn() as c:
            c.execute("""
                INSERT INTO usage_analytics (timestamp, event_type, tab_name,
                    feature_name, duration_seconds, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (time.time(), event_type, tab_name, feature_name,
                  duration_seconds, metadata_json))

    def get_usage_analytics(self, event_type: Optional[str] = None,
                             tab_name: Optional[str] = None,
                             limit: int = 500) -> list[dict]:
        with self.conn() as c:
            if event_type and tab_name:
                rows = c.execute(
                    "SELECT * FROM usage_analytics WHERE event_type=? AND tab_name=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (event_type, tab_name, limit)
                ).fetchall()
            elif event_type:
                rows = c.execute(
                    "SELECT * FROM usage_analytics WHERE event_type=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (event_type, limit)
                ).fetchall()
            elif tab_name:
                rows = c.execute(
                    "SELECT * FROM usage_analytics WHERE tab_name=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (tab_name, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM usage_analytics ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_tab_time_summary(self) -> list[dict]:
        """Aggregate total time spent per tab."""
        with self.conn() as c:
            rows = c.execute("""
                SELECT tab_name,
                       COUNT(*) as visit_count,
                       SUM(duration_seconds) as total_seconds,
                       AVG(duration_seconds) as avg_seconds
                FROM usage_analytics
                WHERE event_type='tab_view' AND tab_name != ''
                GROUP BY tab_name
                ORDER BY total_seconds DESC
            """).fetchall()
            return [dict(r) for r in rows]

    # --- Agent Accuracy (Phase 4) ---

    def upsert_agent_accuracy(self, agent_name: str, signal_type: str,
                               regime: str, win: bool, avg_confidence: float):
        with self.conn() as c:
            # Try update first
            c.execute("""
                INSERT INTO agent_accuracy (agent_name, signal_type, regime,
                    win_count, loss_count, avg_confidence, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_name, signal_type, regime) DO UPDATE SET
                    win_count = win_count + ?,
                    loss_count = loss_count + ?,
                    avg_confidence = (avg_confidence + ?) / 2.0,
                    last_updated = excluded.last_updated
            """, (
                agent_name, signal_type, regime,
                1 if win else 0, 0 if win else 1, avg_confidence, time.time(),
                1 if win else 0, 0 if win else 1, avg_confidence,
            ))

    def get_agent_accuracy(self, agent_name: Optional[str] = None,
                            regime: Optional[str] = None) -> list[dict]:
        with self.conn() as c:
            if agent_name and regime:
                rows = c.execute(
                    "SELECT * FROM agent_accuracy WHERE agent_name=? AND regime=? "
                    "ORDER BY last_updated DESC",
                    (agent_name, regime)
                ).fetchall()
            elif agent_name:
                rows = c.execute(
                    "SELECT * FROM agent_accuracy WHERE agent_name=? "
                    "ORDER BY last_updated DESC",
                    (agent_name,)
                ).fetchall()
            elif regime:
                rows = c.execute(
                    "SELECT * FROM agent_accuracy WHERE regime=? "
                    "ORDER BY last_updated DESC",
                    (regime,)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM agent_accuracy ORDER BY last_updated DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    # --- Autonomous Optimization (Phase 1) ---

    def insert_autonomous_change(self, change: dict) -> int:
        change = {
            "context_key": "global",
            **change,
        }
        with self.conn() as c:
            cur = c.execute(
                """
                INSERT INTO autonomous_changes (
                    timestamp, change_type, target, old_value, new_value,
                    rationale, metric_snapshot_json, applied, rollout_mode,
                    context_key
                )
                VALUES (
                    :timestamp, :change_type, :target, :old_value, :new_value,
                    :rationale, :metric_snapshot_json, :applied, :rollout_mode,
                    :context_key
                )
                """,
                change,
            )
            return cur.lastrowid

    def get_autonomous_changes(self, limit: int = 100) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM autonomous_changes ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_pending_autonomous_changes(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM autonomous_changes "
                "WHERE applied=1 AND COALESCE(reverted, 0)=0 "
                "AND COALESCE(validation_status, 'pending') IN ('pending', 'monitoring') "
                "ORDER BY timestamp ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def update_autonomous_change(self, change_id: int, updates: dict) -> None:
        with self.conn() as c:
            sets = ", ".join(f"{key}=?" for key in updates)
            c.execute(
                f"UPDATE autonomous_changes SET {sets} WHERE id=?",
                (*updates.values(), change_id),
            )

    def get_closed_trades_since(self, timestamp: float, limit: int = 200) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM trades WHERE status='closed' "
                "AND datetime(created_at) >= datetime(?, 'unixepoch') "
                "ORDER BY created_at DESC LIMIT ?",
                (timestamp, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def insert_autonomy_report(self, report: dict) -> int:
        with self.conn() as c:
            cur = c.execute(
                """
                INSERT INTO autonomy_reports (
                    timestamp, report_type, period_key, summary_json
                )
                VALUES (:timestamp, :report_type, :period_key, :summary_json)
                """,
                report,
            )
            return cur.lastrowid

    def get_autonomy_reports(self, report_type: Optional[str] = None,
                              limit: int = 30) -> list[dict]:
        with self.conn() as c:
            if report_type:
                rows = c.execute(
                    "SELECT * FROM autonomy_reports WHERE report_type=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (report_type, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM autonomy_reports ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG', 'SHORT', 'FLAT')),
    confidence REAL NOT NULL,
    ensemble_score REAL NOT NULL,
    strategies_agree INTEGER NOT NULL,
    entry_price REAL,
    stop_price REAL,
    target_price REAL,
    regime TEXT DEFAULT 'unknown',
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'triggered', 'expired', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    strategy_name TEXT NOT NULL,
    score REAL NOT NULL,
    direction TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    direction TEXT NOT NULL CHECK(direction IN ('LONG', 'SHORT')),
    quantity INTEGER NOT NULL DEFAULT 1,
    entry_price REAL NOT NULL,
    exit_price REAL,
    pnl REAL,
    fees REAL DEFAULT 0,
    stop_price REAL,
    target_price REAL,
    r_multiple REAL,
    hold_time_sec REAL,
    source TEXT DEFAULT 'manual',
    notes TEXT,
    status TEXT DEFAULT 'open' CHECK(status IN ('open', 'closed', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trade_grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    setup_quality REAL,
    entry_timing REAL,
    exit_timing REAL,
    risk_management REAL,
    plan_adherence REAL,
    overall_grade REAL,
    edge_ratio REAL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    strategy_name TEXT NOT NULL,
    accuracy REAL,
    precision_score REAL,
    recall REAL,
    f1 REAL,
    sharpe REAL,
    win_rate REAL,
    profit_factor REAL,
    sample_size INTEGER,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS regime_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    regime TEXT NOT NULL,
    confidence REAL,
    features TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS news_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    headline TEXT NOT NULL,
    source TEXT,
    sentiment_score REAL,
    market_impact REAL,
    category TEXT,
    url TEXT,
    is_trump INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_flow_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    price REAL,
    bid_volume INTEGER,
    ask_volume INTEGER,
    delta INTEGER,
    cumulative_delta INTEGER,
    poc_price REAL,
    vah_price REAL,
    val_price REAL,
    data_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    total_trades INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    gross_pnl REAL DEFAULT 0,
    net_pnl REAL DEFAULT 0,
    fees REAL DEFAULT 0,
    largest_win REAL DEFAULT 0,
    largest_loss REAL DEFAULT 0,
    avg_win REAL DEFAULT 0,
    avg_loss REAL DEFAULT 0,
    win_rate REAL DEFAULT 0,
    profit_factor REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    avg_r_multiple REAL DEFAULT 0,
    sharpe REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dark_pool_prints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    size INTEGER NOT NULL,
    notional REAL NOT NULL,
    venue TEXT,
    is_block INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS confluence_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    price REAL NOT NULL,
    triggers TEXT,
    confluence_score REAL,
    zone_type TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ml_training_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    model_name TEXT NOT NULL,
    accuracy REAL,
    precision_score REAL,
    recall REAL,
    f1 REAL,
    sharpe REAL,
    win_rate REAL,
    profit_factor REAL,
    features_used TEXT,
    hyperparams TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS news_impact_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headline_pattern TEXT NOT NULL,
    category TEXT,
    avg_price_impact REAL,
    avg_duration_sec REAL,
    sample_count INTEGER DEFAULT 0,
    last_updated REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_trades_entry ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_strategy_scores_signal ON strategy_scores(signal_id);
CREATE INDEX IF NOT EXISTS idx_model_perf_strategy ON model_performance(strategy_name);
CREATE INDEX IF NOT EXISTS idx_regime_timestamp ON regime_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_news_timestamp ON news_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_orderflow_timestamp ON order_flow_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_dark_pool_timestamp ON dark_pool_prints(timestamp);
CREATE INDEX IF NOT EXISTS idx_dark_pool_notional ON dark_pool_prints(notional);
CREATE INDEX IF NOT EXISTS idx_dark_pool_symbol ON dark_pool_prints(symbol);
CREATE INDEX IF NOT EXISTS idx_confluence_timestamp ON confluence_zones(timestamp);
CREATE INDEX IF NOT EXISTS idx_confluence_status ON confluence_zones(status);
CREATE INDEX IF NOT EXISTS idx_ml_runs_model ON ml_training_runs(model_name);
CREATE INDEX IF NOT EXISTS idx_ml_runs_timestamp ON ml_training_runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_news_impact_category ON news_impact_history(category);

CREATE TABLE IF NOT EXISTS journal_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    entry_price REAL NOT NULL,
    exit_price REAL,
    size INTEGER NOT NULL DEFAULT 1,
    stop_loss REAL,
    take_profit REAL,
    pnl REAL,
    rr_ratio REAL,
    duration_seconds REAL,
    mae REAL,
    mfe REAL,
    notes TEXT DEFAULT '',
    emotion TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    ai_grade TEXT DEFAULT '',
    ai_analysis_json TEXT DEFAULT '{}',
    screenshot_path TEXT DEFAULT '',
    confluence_data_json TEXT DEFAULT '{}',
    signal_confidence REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal_trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_journal_grade ON journal_trades(ai_grade);
"""

LEARNING_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    knowledge_type TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL DEFAULT 1.0,
    updated_at REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_name, knowledge_type, key)
);

CREATE TABLE IF NOT EXISTS learning_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    agent_name TEXT NOT NULL,
    lesson_type TEXT NOT NULL,
    description TEXT NOT NULL,
    impact_score REAL DEFAULT 0.0,
    trade_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_weights_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    weight REAL NOT NULL,
    cumulative_reward REAL DEFAULT 0.0,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    last_updated REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    period TEXT DEFAULT 'session',
    timestamp REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_knowledge_agent ON agent_knowledge(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_knowledge_type ON agent_knowledge(knowledge_type);
CREATE INDEX IF NOT EXISTS idx_learning_history_ts ON learning_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_learning_history_agent ON learning_history(agent_name);
CREATE INDEX IF NOT EXISTS idx_strategy_weights_name ON strategy_weights_history(strategy_name);
CREATE INDEX IF NOT EXISTS idx_agent_perf_name ON agent_performance(agent_name);
"""

PHASE4_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,
    conditions_json TEXT NOT NULL DEFAULT '{}',
    outcome TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    sample_size INTEGER NOT NULL DEFAULT 1,
    last_seen REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pattern_type, outcome)
);

CREATE TABLE IF NOT EXISTS market_regimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    regime TEXT NOT NULL,
    volatility REAL NOT NULL DEFAULT 0.0,
    trend_strength REAL NOT NULL DEFAULT 0.0,
    features_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    tab_name TEXT NOT NULL DEFAULT '',
    feature_name TEXT NOT NULL DEFAULT '',
    duration_seconds REAL NOT NULL DEFAULT 0.0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_accuracy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    regime TEXT NOT NULL DEFAULT 'all',
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0,
    avg_confidence REAL NOT NULL DEFAULT 0.5,
    last_updated REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_name, signal_type, regime)
);

CREATE INDEX IF NOT EXISTS idx_market_patterns_type ON market_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_market_patterns_confidence ON market_patterns(confidence);
CREATE INDEX IF NOT EXISTS idx_market_regimes_ts ON market_regimes(timestamp);
CREATE INDEX IF NOT EXISTS idx_market_regimes_regime ON market_regimes(regime);
CREATE INDEX IF NOT EXISTS idx_usage_analytics_ts ON usage_analytics(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_analytics_tab ON usage_analytics(tab_name);
CREATE INDEX IF NOT EXISTS idx_agent_accuracy_agent ON agent_accuracy(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_accuracy_regime ON agent_accuracy(regime);
"""

AI_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    tool_calls_json TEXT NOT NULL DEFAULT '',
    tokens_used INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_history_ts ON chat_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_chat_history_role ON chat_history(role);
"""

AUTONOMY_SCHEMA = """
CREATE TABLE IF NOT EXISTS autonomous_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    change_type TEXT NOT NULL,
    target TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    metric_snapshot_json TEXT NOT NULL DEFAULT '{}',
    applied INTEGER NOT NULL DEFAULT 0,
    rollout_mode TEXT NOT NULL DEFAULT 'paper',
    context_key TEXT NOT NULL DEFAULT 'global',
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_checked_at REAL,
    validation_notes TEXT NOT NULL DEFAULT '',
    reverted INTEGER NOT NULL DEFAULT 0,
    reverted_at REAL,
    revert_notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS autonomy_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    report_type TEXT NOT NULL,
    period_key TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_autonomous_changes_ts ON autonomous_changes(timestamp);
CREATE INDEX IF NOT EXISTS idx_autonomous_changes_target ON autonomous_changes(target);
CREATE INDEX IF NOT EXISTS idx_autonomy_reports_type ON autonomy_reports(report_type);
CREATE INDEX IF NOT EXISTS idx_autonomy_reports_period ON autonomy_reports(period_key);
"""

