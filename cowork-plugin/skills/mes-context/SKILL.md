---
name: mes-context
description: Loads MES Trading Intelligence System context — instrument specs, session times, agent architecture, database schema. Use when any other MES skill needs ground-truth facts about the trader's setup, the SQLite brain DB structure, or the 8-agent system. Auto-load this skill when user mentions MES, futures session times, the brain DB, agent accuracy, or order flow tooling specifics.
---

# MES Trading Intel — Ground Truth Context

This is reference knowledge for every other skill in the `mes-trading-intel` plugin. Load this first when answering anything about the trader's system, instrument, schedule, or the brain database.

## Trader profile

- Trades **MES (Micro E-mini S&P 500)** futures via **AMP Futures** (broker), **Rithmic R|API** (data + execution feed), **ATAS** (charting).
- Operates **own retail account + multiple prop firm accounts** in parallel — prop accounts have separate rule sets (drawdown, profit target, daily loss, scaling).
- Methodology: **quantitative + order flow + options-derived dealer flow**. Specifically uses volume profile (with **40% Value Area**, non-standard), delta footprints, cumulative delta, and GEX (gamma exposure) levels for dealer positioning.
- High-confidence setups only. Prefers fewer, better signals over churn.

## Instrument specs (MES)

| Spec               | Value                            |
|--------------------|----------------------------------|
| Tick size          | 0.25 points                      |
| Tick value         | $1.25 (NOT $5 — $5 is per point) |
| Point value        | $5.00                            |
| Contract           | 1/10 the size of full ES         |
| Trading hours (RTH)| 6:30 AM – 2:00 PM Phoenix (UTC-7)|
| Trading hours (ETH)| 3:00 PM – 6:29 AM Phoenix        |
| Settlement         | 1:00 PM Phoenix                  |

> **Timezone is America/Phoenix (UTC-7, no DST).** Always render times in Phoenix unless explicitly asked otherwise. Convert any UTC timestamps from the DB to Phoenix before showing them.

## Value Area convention

**40% Value Area is intentional and non-standard.** The typical 70% VA captures most volume — the trader uses 40% to surface a much tighter "consensus zone" for high-conviction setups. Do not "correct" this to 70%.

## The 8-agent system

The MES app (`/Users/m1k3y/trading/`, entry `python3 -m mes_intel`) runs 8 agents that share an event bus and a SQLite brain:

| Agent          | Role                                                                                |
|----------------|-------------------------------------------------------------------------------------|
| SignalEngine   | 35 quantitative strategies, ensemble scoring, gates by `min_confidence`             |
| ChartMonitor   | Price action + pattern detection                                                    |
| TradeJournal   | AI-graded logging with pattern matching against historical trades                   |
| MetaLearner    | Bayesian weight updates for strategies, team meetings every 50 trades               |
| NewsScanner    | News sentiment + catalyst detection (premarket sweep)                               |
| DarkPoolAgent  | Large institutional print detection                                                 |
| MarketBrain    | Regime (trending/ranging/volatile/quiet/breakout), Hurst exponent, FVGs, auction theory, Markov regime transitions |
| AppOptimizer   | Learns user UI behavior, suggests workflow optimizations                            |

`mac-experimental` branch on GitHub also contains an `AutonomousOptimizer` that tunes signal thresholds per regime — not on `main` yet, may or may not be running locally.

## The shared Brain — three MCPs

This plugin wires three MCP servers, all rooted in the iCloud-synced Brain folder shared between Mac and PC:

| MCP            | Backed by                        | Use for                                                       |
|----------------|----------------------------------|---------------------------------------------------------------|
| `mes-brain`    | SQLite at `mes-state/mes_intel.db` | All structured queries: trades, regimes, agents, dark pool, learning history |
| `brain-files`  | Filesystem rooted at `Brain/`    | Read/write Obsidian vault notes, Claude memory, configs, models |
| `mes-repo`     | Filesystem rooted at the trading repo | Read code, configs, strategy implementations, agent source — when discussing system behavior |

Env vars required (each set per machine — see plugin README): `MES_BRAIN_DB_PATH`, `BRAIN_ROOT`, `MES_REPO_ROOT`.

### Brain folder layout

```
Brain/
├── mes-state/        ← config.json, mes_intel.db, models/, RUNNING.lock
├── obsidian/
│   └── Obsidian Vault/   ← trading notes (Trading/, Ideas/, etc.)
└── claude-memory/    ← MEMORY.md + per-topic memory files
```

### Brain DB — `mes_intel.db`

Connect via `mes-brain`.

### Key tables

| Table                       | Holds                                                                       |
|-----------------------------|-----------------------------------------------------------------------------|
| `journal_trades`            | Every logged trade — entry/exit, size, P&L, AI grade, regime at entry       |
| `agent_knowledge`           | What each agent has learned (key/value per agent)                           |
| `learning_history`          | Time-series of lessons learned (event-sourced)                              |
| `strategy_weights_history`  | Meta-learner's strategy weight changes over time                            |
| `market_patterns`           | Library of recognized chart/orderflow patterns with outcome stats           |
| `market_regimes`            | Regime transitions logged by MarketBrain                                    |
| `agent_accuracy`            | Per-agent prediction accuracy over rolling windows                          |
| `dark_pool_prints`          | Large institutional trades detected                                         |
| `confluence_zones`          | Multi-factor high-conviction price levels                                   |
| `chat_history`              | In-app AI assistant conversations                                           |
| `usage_analytics`           | UI usage events (powers AppOptimizer)                                       |

### Querying conventions

- Always **introspect the schema first** with `PRAGMA table_info(<table>)` before assuming column names — schema evolves between branches.
- Times in the DB are **UTC ISO strings** unless a column name says otherwise. Convert to Phoenix for display.
- `journal_trades` has account-tagging columns (look for `account_id` or `account_name`) so prop firms can be separated from the personal account.

## Critical files in the repo

| Path                                  | Purpose                                                            |
|---------------------------------------|--------------------------------------------------------------------|
| `mes_intel/main.py`                   | Entry point, lockfile guard for shared state                       |
| `mes_intel/config.py`                 | All tunable thresholds (`AppConfig` dataclass)                     |
| `mes_intel/agents/`                   | The 8 agent modules                                                |
| `mes_intel/strategies/`               | 35 strategy implementations, all inherit `base.Strategy`           |
| `mes_intel/database.py`               | SQLite wrapper + schema                                            |
| `var/mes_intel/`                      | Symlink → `Brain/mes-state/` (config.json, mes_intel.db, models/)  |
| `BOOTSTRAP_WINDOWS.md`                | PC-side setup walkthrough                                          |

## Common pitfalls

- The state path `var/mes_intel/` is a **symlink to iCloud Brain**. Never assume it's a local-only directory.
- `RUNNING.lock` in the state dir guards against both machines running the app at once. Don't delete it without confirming the other machine is actually stopped.
- The venv lives at the project root (`bin/`, `lib/`, `include/`, `pyvenv.cfg`) — those aren't project code.
- macOS uses `python3`, not `python`.
