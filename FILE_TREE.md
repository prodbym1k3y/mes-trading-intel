# MES Trading Intelligence — Project Structure
*Updated: 2026-04-04*

## 35 Strategies | 8 Agents | 8 Tabs | FinBERT Sentiment | RL Weight Learning

```
mes_intel/
├── main.py                       # Entry point, agent wiring, data feeds, timers
├── config.py                     # AppConfig (Rithmic, Alpaca, Finnhub, Claude, ATAS, ML)
├── database.py                   # SQLite (trades, signals, strategy_scores, agents, ML)
├── event_bus.py                  # Pub/sub (30+ event types)
├── orderflow.py                  # Tick, VolumeProfile, FootprintBar/Chart
├── orderflow_advanced.py         # BigTrades, Institutional, DOM, CumDelta, Spoofing
│
├── agents/                       # 8 AI agents
│   ├── signal_engine.py          # Ensemble: 35 strategies → weighted vote → signal
│   ├── chart_monitor.py          # market_data assembly (VWAP, delta, profiles)
│   ├── trade_journal.py          # AI grading, auto-import, regime tagging
│   ├── meta_learner.py           # RL weight learning, post-mortems, team meetings
│   ├── news_scanner.py           # Finnhub + RSS + FinBERT + economic calendar
│   ├── dark_pool.py              # Block trade detection, institutional S/R
│   ├── market_brain.py           # Hurst, FVG, auction theory, microstructure, Markov
│   └── app_optimizer.py          # Usage analytics, UI optimization
│
├── strategies/                   # 35 quant strategies (6 phases)
│   ├── base.py                   # Strategy ABC + StrategyResult
│   ├── [Phase 1]                 # mean_reversion, momentum, stat_arb, order_flow,
│   │                             #   gex_model, hmm_regime, ml_scorer
│   ├── [Phase 2]                 # twap, microstructure, tick_momentum, delta_divergence,
│   │                             #   liquidity_sweep, orb, vwap_bands, market_internals,
│   │                             #   auction_theory, iceberg, confluence
│   ├── [Phase 3]                 # cross_asset, options_gamma
│   ├── [Phase 5]                 # volume_profile_advanced, delta_flow, vpin,
│   │                             #   options_flow, kalman_fair_value, hurst_regime,
│   │                             #   orderflow_imbalance
│   └── [Phase 6]                 # ts_momentum, vol_targeting, relative_value,
│                                 #   macro_regime, factor_correlation
│
├── data/                         # Data feeds
│   ├── rithmic_feed.py           # Live Rithmic + SimulatedRithmicFeed
│   ├── cross_asset_feed.py       # VIX, DXY, yields, gold, oil, NQ, BTC, GEX
│   ├── atas_bridge.py            # ATAS CSV watcher
│   ├── alpaca_feed.py            # Real-time SPY/QQQ quotes
│   └── amp_sync.py               # AMP CSV import + Rithmic sync
│
├── ml/                           # Machine learning pipeline
│   ├── trainer.py                # MLTrainer (XGBoost + heuristic fallback)
│   ├── features.py               # 60-feature engineering
│   └── validator.py              # Walk-forward validation
│
├── ai/                           # LLM
│   └── llm_assistant.py          # Claude API + 8 tools
│
├── ui/                           # PySide6 (8 tabs)
│   ├── app.py                    # MainWindow
│   ├── signals_enhanced.py       # SIGNALS: confluence cards + strategy breakdown
│   ├── big_trades.py             # BIG TRADES: dot chart + heatmap
│   ├── journal_enhanced.py       # JOURNAL: AI grading + Exit@Market + AMP import
│   ├── meta_ai_enhanced.py       # META-AI: 8-agent dashboard + Team IQ
│   ├── analytics.py              # ANALYTICS: equity, Sharpe, VaR, heatmaps
│   ├── cross_asset_panel.py      # INTEL: cross-asset + GEX
│   ├── ai_chat.py                # AI ASSISTANT: Claude chat
│   ├── settings_panel.py         # SETTINGS: config + optimizer
│   ├── theme.py                  # Cyberpunk neon aesthetic
│   └── widgets.py                # Shared neon widgets
│
├── legacy/                       # Old SPY tools (archived)
└── trade_journal/                # Legacy Flask journal (archived)
```
