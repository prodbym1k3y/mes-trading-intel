# MES Futures Trading Intelligence System

## Overview
PySide6 desktop app for MES (Micro E-mini S&P 500) futures trading intelligence. 8 AI agents that learn from each other, the market, and the user's trades. 80s cyberpunk neon aesthetic.

## Architecture
- **Framework**: PySide6/Qt for desktop GUI with QPainter custom rendering
- **Database**: SQLite (`mes_intel.db`) for trades, signals, agent knowledge, learning history
- **Data Feed**: Rithmic R|API via AMP Futures (`SimulatedRithmicFeed` for dev)
- **Cross-Asset**: yfinance for VIX, 10Y, DXY, NQ, gold, oil, BTC (~15min delayed)
- **ML**: scikit-learn, XGBoost for ensemble scoring
- **LLM**: Anthropic Claude API for built-in AI assistant

## Entry Points
- **Primary**: `python3 -m mes_intel` (uses `mes_intel/__main__.py` → `main.py`)
- **Dev launch**: `./run.sh` (kills existing instance, then launches)
- **Legacy SPY tools**: `./spy_signal.py`, `./spy_monitor.py`, `./news_alert.py`
- **Trade journal (Flask)**: `trade_journal/launch.sh`

## Project Structure
```
mes_intel/
├── main.py            # Entry point: initializes all agents + launches Qt app
├── config.py          # AppConfig dataclass (loads from var/mes_intel/config.json)
├── database.py        # SQLite wrapper
├── event_bus.py       # Pub/sub event system
├── orderflow.py       # Tick, VolumeProfile, FootprintBar, FootprintChart
├── orderflow_advanced.py
├── agents/            # 8 AI agents
├── data/              # Data feeds (Rithmic, ATAS, Alpaca, cross-asset)
├── engines/           # Advanced order flow + big trades engines
├── ml/                # scikit-learn/XGBoost trainer, features, validator
├── strategies/        # 35 quantitative trading strategies
└── ui/                # PySide6 tabs, widgets, theme, animations
```

## Agents (`mes_intel/agents/`)
1. **SignalEngine** (`signal_engine.py`) — 20+ quantitative strategies, ensemble scoring
2. **ChartMonitor** (`chart_monitor.py`) — price action and pattern detection
3. **TradeJournal** (`trade_journal.py`) — AI-graded trade logging with pattern matching
4. **MetaLearner** (`meta_learner.py`) — orchestrates all agents, Bayesian weight updates, team meetings every 50 trades
5. **NewsScanner** (`news_scanner.py`) — news sentiment and catalyst detection
6. **DarkPoolAgent** (`dark_pool.py`) — large institutional trade detection
7. **MarketBrain** (`market_brain.py`) — regime detection, Hurst exponent, fair value gaps, volume profile, auction theory, microstructure, Markov chain transitions
8. **AppOptimizer** (`app_optimizer.py`) — learns user behavior, suggests UI optimizations

## UI Tabs (`mes_intel/ui/`)
`SIGNALS | BIG TRADES | JOURNAL | META-AI | ANALYTICS | INTEL | AI ASSISTANT | SETTINGS`

Key files:
- `app.py` — `MainWindow`: top-level Qt window, tab setup, agent wiring
- `theme.py` — `COLORS`, `STYLESHEET`: cyberpunk neon palette
- `widgets.py` — Shared neon widgets (`NeonLineChart`, `NeonButton`, etc.)
- `signals_enhanced.py`, `journal_enhanced.py`, etc. — individual tabs

## Strategies (`mes_intel/strategies/`)
35 strategies all inherit from `base.Strategy` and return `StrategyResult`. Registered on `SignalEngine.strategies` dict at startup in `main.py`.
- Phase 5 quant: volume_profile_advanced, delta_flow, vpin, options_flow, kalman_fair_value, hurst_regime, orderflow_imbalance
- Phase 6 systematic: ts_momentum, vol_targeting, relative_value, macro_regime, factor_correlation

## Key Constants
- MES tick: 0.25 points = $1.25 (not $5 — $5 is per full point)
- Value Area: **40%** (non-standard — user preference, not the typical 70%)
- Timezone: **America/Phoenix** (UTC-7, no DST)
- RTH session: 6:30 AM – 2:00 PM Phoenix
- Overnight: 3:00 PM – 6:29 AM Phoenix

## Database Schema (`mes_intel/database.py`)
Key tables: `journal_trades`, `agent_knowledge`, `learning_history`, `strategy_weights_history`, `market_patterns`, `market_regimes`, `usage_analytics`, `agent_accuracy`, `chat_history`, `dark_pool_prints`, `confluence_zones`

## Event Bus (`mes_intel/event_bus.py`)
Singleton `bus` instance. Key event types: `TRADE_RESULT`, `LESSON_LEARNED`, `MARKET_REGIME_CHANGE`, `QUANT_SIGNAL`, `UI_USAGE_EVENT`, `OPTIMIZATION_SUGGESTION`, `CROSS_ASSET_UPDATE`, `OPTIONS_DATA_UPDATE`, `ML_TRAINING_STARTED`, `ML_TRAINING_COMPLETE`

## Data Feeds (`mes_intel/data/`)
- `rithmic_feed.py` — Live Rithmic R|API (falls back to simulated if no credentials)
- `atas_bridge.py` — Watches ATAS CSV export directory
- `alpaca_feed.py` — Real-time quotes (SPY, QQQ, IWM, GLD, USO, HYG, TLT, VXX, UUP)
- `cross_asset_feed.py` — Aggregates VIX, DXY, yields, gold, oil, NQ, BTC, GEX
- `amp_sync.py` — AMP Futures account sync

## Common Pitfalls
- Use `python3` not `python` on this Mac
- `QFont.SpacingType.AbsoluteSpacing` — NOT `QFont.LetterSpacingType`
- `NeonLineChart` does NOT accept a `y_label` parameter
- PySide6 app cannot run headless — requires a GUI session
- `rapi` not installed = expected; `SimulatedRithmicFeed` is the dev fallback
- XGBoost on macOS needs `brew install libomp` for full performance
- The venv lives at the **project root** (`bin/`, `lib/`, `include/`, `var/` are venv dirs — not project code)
- Config and DB live in `var/mes_intel/` (not the `mes_intel/` package dir)

## User Preferences
- 80s cyberpunk neon aesthetic: dark backgrounds, neon green/cyan/magenta accents
- Practical features over flashy ones
- Trades MES futures via AMP Futures with ATAS charting software
- Phoenix, AZ timezone (America/Phoenix, UTC-7, no DST)
- High-confidence signals only — prefers fewer, better signals
- Quantitative, data-driven analysis

## Environment
- Python 3.14 (venv at project root)
- macOS (Darwin 25.3.0)
- Activate venv: `source bin/activate` from project root
- DB path: `var/mes_intel/mes_intel.db`
- Config path: `var/mes_intel/config.json`
