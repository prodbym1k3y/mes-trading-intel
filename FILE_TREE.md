# MES Intel — Project File Tree
*Generated: 2026-04-02*

```
mes_intel/
├── __init__.py (2) — Package entry point, version string
├── __main__.py (3) — CLI entry point stub
├── main.py (366) — Entry point, initializes all agents and data feeds
├── config.py (288) — Configuration management for Rithmic, ATAS, Alpaca, and UI settings
├── database.py (1010) — SQLite schema for signals, trades, grades, patterns, regimes, and analytics
├── event_bus.py (188) — Lightweight pub/sub system for inter-agent communication
├── orderflow.py (374) — Volume profile, delta profile, value area, and footprint chart data structures
├── orderflow_advanced.py (1369) — Advanced order flow analysis with imbalance, DOM, exhaustion, absorption signals
│
├── agents/
│   ├── __init__.py (1) — Package entry point
│   ├── signal_engine.py (364) — Multi-strategy ensemble signal generation with weighted voting
│   ├── chart_monitor.py (259) — Monitors price action, order flow, and volume delta from Rithmic/ATAS
│   ├── market_brain.py (1123) — Quantitative market learning engine with 60+ institutional-grade features
│   ├── meta_learner.py (2060) — Learns from trades, teaches agents, self-improves with RL reward tracking
│   ├── news_scanner.py (1157) — News aggregation from Finnhub, Twitter/X, RSS feeds, economic calendar
│   ├── dark_pool.py (619) — Monitors SPY dark pool prints as proxy for institutional MES activity
│   ├── trade_journal.py (575) — Logs trades, quantitatively grades them, tracks P&L and mae/mfe
│   └── app_optimizer.py (504) — Monitors UI behavior and suggests app optimizations based on usage patterns
│
├── ai/
│   ├── __init__.py (1) — AI assistant package init
│   └── llm_assistant.py (525) — Claude API backend with tool calling (query_database, read_file, list_files, get_agent_status)
│
├── data/
│   ├── __init__.py (6) — Data feed layer docstring
│   ├── rithmic_feed.py (726) — Rithmic R|API connection for real-time MES tick data via AMP Futures
│   ├── atas_bridge.py (670) — ATAS CSV file monitoring and parsing for footprint/cluster data
│   ├── alpaca_feed.py (285) — Alpaca REST API feed for SPY/QQQ spot data and options data
│   ├── cross_asset_feed.py (736) — Cross-asset data aggregation (futures, forex, crypto, indices)
│   └── amp_sync.py (816) — AMP Futures account sync and order status monitoring
│
├── strategies/
│   ├── __init__.py (1) — Quantitative trading strategies package
│   ├── base.py (48) — Base Strategy class with score/confidence/direction interface
│   ├── momentum.py (170) — Kalman filter + ADX + rate of change momentum detection
│   ├── mean_reversion.py (101) — VWAP + Z-score mean reversion with overbought/oversold detection
│   ├── stat_arb.py (105) — Statistical arbitrage between ES and SPY with spread analysis
│   ├── order_flow.py (126) — Order flow imbalance, cumulative delta, absorption-based signals
│   ├── gex_model.py (141) — Gamma exposure model from options market positioning
│   ├── hmm_regime.py (200) — Hidden Markov Model regime detection (trending/range/reversal)
│   ├── microstructure.py (228) — Tick velocity, VPIN, market microstructure patterns
│   ├── tick_momentum.py (239) — Tick-by-tick momentum with large trade acceleration
│   ├── ml_scorer.py (254) — ML ensemble scoring using XGBoost and engineered features
│   ├── delta_divergence.py (274) — Price/delta divergence for momentum reversals
│   ├── liquidity_sweep.py (288) — Liquidity sweep and stop run detection
│   ├── options_gamma.py (294) — Options gamma and vega-weighted levels
│   ├── cross_asset.py (296) — Cross-asset correlation and relative value signals
│   ├── vwap_bands.py (324) — VWAP + bands with mean reversion and breakout signals
│   ├── iceberg_detection.py (375) — Iceberg order detection from footprint patterns
│   ├── market_internals.py (392) — Market breadth, put/call ratios, advance/decline lines
│   ├── auction_theory.py (504) — Market profile auction theory with initial balance, extensions, range days
│   ├── confluence.py (573) — Confluence zone detection from support/resistance levels
│   ├── quant_strategies.py (1157) — Collection of additional quant strategies (FVG, POI, volume analysis)
│   └── twap_deviation.py (176) — Time-weighted average price deviation detection
│
├── ml/
│   ├── __init__.py (15) — Lazy-loading module for ML components
│   ├── features.py (391) — Feature engineering for ~60 features (price, volatility, momentum, order flow)
│   ├── trainer.py (605) — XGBoost model training pipeline with cross-validation
│   └── validator.py (267) — Walk-forward validation and walk-forward backtesting
│
├── engines/
│   ├── __init__.py (1) — Engines package entry point
│   ├── big_trades.py (430) — Large trade detection, clustering, heatmap analysis
│   └── advanced_orderflow.py (688) — Advanced order flow signals (diagonal/stacked imbalance, footprint patterns)
│
└── ui/
    ├── __init__.py (1) — Desktop UI components package
    ├── app.py (1600) — Main desktop application window with phase 3 enhancements
    ├── theme.py (535) — 80s neon retro Tron/Blade Runner synthwave color theme
    ├── widgets.py (1215) — Custom widgets (footprint chart, volume profile, signal meter, scorecard)
    ├── ai_chat.py (636) — AI assistant chat UI with neon theme and quick-action buttons
    ├── analytics.py (1009) — Analytics dashboard (equity curve, drawdown, strategy performance, correlation)
    ├── animations.py (906) — CRT scanline effect, glow effects, and UI animations
    ├── charts_enhanced.py (437) — Neon line chart with glow effects, zoom, pan
    ├── indicators_enhanced.py (1112) — Overlaid technical indicators (RSI, MACD, Bollinger, VWAP, POC)
    ├── signals_enhanced.py (1321) — Signal visualization with confidence bars and strategy voter breakdown
    ├── journal_enhanced.py (2161) — Trade journal UI with grades, P&L, mae/mfe, emotion tracking
    ├── big_trades.py (635) — Big trades widget with volume profile heatmap and stats
    ├── big_trades_chart.py (660) — Big trades chart visualization with price levels
    ├── footprint_advanced.py (788) — Advanced footprint chart with stacked imbalance markers
    ├── footprint_atas.py (1911) — ATAS-style footprint (Sierra Chart layout, session profiles, delta divergence)
    ├── combined_footprint.py (589) — Multi-timeframe footprint aggregation
    ├── cross_asset_panel.py (654) — ES/SPY/QQQ correlation and relative strength display
    ├── reactive_fx.py (438) — Reactive effects (scanline, glow, transitions)
    ├── cyberpunk_fx.py (435) — Cyberpunk visual effects (neon glitch, chromatic aberration)
    ├── settings_panel.py (515) — Settings UI (Rithmic, ATAS, Alpaca, strategy weights, optimizer)
    ├── session_profiles.py (601) — Session profile selector (RTH/Overnight with stats by session)
    ├── easter_eggs.py (1839) — Easter egg system (Konami code, logo clicks, pixel art, games)
    ├── easter_eggs_v2.py (789) — Enhanced easter eggs (Matrix rain, snake game, visualizers)
    ├── vanity_sprites.py (336) — Vanity sprite definitions for easter eggs
    └── vanity/
        ├── __init__.py (1) — Vanity art package entry point
        └── pixel_art.py (487) — Pixel art definitions (pill, weed, money printer, chicken, rocket)
```

## Key File Highlights

**Core Infrastructure:**
- `main.py` — Entry point orchestrating all 8 agents and the event bus
- `config.py` — Centralized config for Rithmic, ATAS, Alpaca, and UI settings
- `database.py` — 28-table SQLite schema backing the entire system
- `event_bus.py` — Lightweight pub/sub for inter-agent events
- `orderflow.py` & `orderflow_advanced.py` — Order flow data structures and analysis

**8 Autonomous Agents:**
- `SignalEngine` (364 LOC) — Ensemble voting across 24 strategies
- `TradeJournal` (575 LOC) — Trade logging and quantitative grading
- `ChartMonitor` (259 LOC) — Price/volume monitoring from Rithmic/ATAS
- `MetaLearner` (2060 LOC) — RL-based learning and agent feedback
- `NewsScanner` (1157 LOC) — Multi-source news aggregation
- `DarkPoolAgent` (619 LOC) — Institutional activity detection
- `MarketBrain` (1123 LOC) — 60+ institutional quant features
- `AppOptimizer` (504 LOC) — UI optimization suggestions

**24 Quantitative Strategies:**
Top 10 by complexity: `quant_strategies` (1157) → `auction_theory` (504) → `confluence` (573) → `market_internals` (392) → `vwap_bands` (324) → `iceberg_detection` (375) → `cross_asset` (296) → `options_gamma` (294) → `liquidity_sweep` (288) → `delta_divergence` (274)

**Data Feeds:**
- Rithmic (726 LOC) — Direct AMP connection for ticks
- ATAS (670 LOC) — CSV-based footprint import
- Alpaca (285 LOC) — SPY/QQQ spot + options
- Cross-Asset (736 LOC) — Multi-asset aggregation

**ML Pipeline:**
- Features (391 LOC) — ~60 engineered features
- Trainer (605 LOC) — XGBoost with cross-validation
- Validator (267 LOC) — Walk-forward backtesting

**Desktop UI (Phase 3):**
- App (1600 LOC) — Main window + CRT effects
- Journal (2161 LOC) — Trade journal with AI grading
- Footprint ATAS (1911 LOC) — Sierra Chart-style footprint
- Signals (1321 LOC) — Strategy voter breakdown
- Easter Eggs (1839 + 789 LOC) — Konami code, games, pixel art

**AI Assistant:**
- LLM Assistant (525 LOC) — Claude API with 8 tools
- AI Chat (636 LOC) — Chat UI with quick-action buttons
