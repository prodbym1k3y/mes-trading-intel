"""MES Trading Intelligence System — main entry point.

Initializes all agents, database, event bus, and launches the desktop app.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Setup logging before imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mes_intel")


def main():
    """Launch the MES Trading Intelligence System."""
    log.info("=" * 60)
    log.info("MES TRADING INTELLIGENCE SYSTEM v4.0  [Phase 4]")
    log.info("=" * 60)

    # Core infrastructure
    from .config import AppConfig
    from .database import Database
    from .event_bus import bus, EventType, Event

    config = AppConfig.load()
    config.save()  # persist defaults

    db = Database(config.db_path)
    log.info("Database initialized: %s", config.db_path)

    # Ensure ML model directory exists
    Path(config.ml.model_dir).mkdir(parents=True, exist_ok=True)

    # Initialize Phase 1 agents
    from .agents.signal_engine import SignalEngine
    from .agents.trade_journal import TradeJournal
    from .agents.chart_monitor import ChartMonitor
    from .agents.meta_learner import MetaLearner
    from .agents.news_scanner import NewsScanner

    signal_engine = SignalEngine(config, db, bus)
    trade_journal = TradeJournal(config, db, bus)
    chart_monitor = ChartMonitor(config, db, bus)
    meta_learner = MetaLearner(config, db, bus)
    news_scanner = NewsScanner(config, db, bus)

    log.info("Phase 1 agents initialized (5/5)")

    # Initialize Phase 2 agents
    dark_pool_agent = None
    try:
        from .agents.dark_pool import DarkPoolAgent
        dark_pool_agent = DarkPoolAgent(config, db, bus)
        log.info("Dark pool agent initialized")
    except ImportError:
        log.warning("Dark pool agent not available (module not found)")

    # Phase 2: Advanced Order Flow Engine
    adv_flow_engine = None
    try:
        from .orderflow_advanced import AdvancedOrderFlowEngine
        adv_flow_engine = AdvancedOrderFlowEngine(bus)
        log.info("Advanced Order Flow Engine initialized")
    except Exception as exc:
        log.warning("Advanced order flow engine failed: %s", exc)

    # Register Phase 2 strategies with signal engine
    try:
        from .strategies.twap_deviation import TWAPDeviationStrategy
        from .strategies.microstructure import MicrostructureStrategy
        from .strategies.tick_momentum import TickMomentumStrategy
        from .strategies.delta_divergence import DeltaDivergenceStrategy
        from .strategies.liquidity_sweep import LiquiditySweepStrategy
        from .strategies.orb import ORBStrategy
        from .strategies.vwap_bands import VWAPBandsStrategy
        from .strategies.market_internals import MarketInternalsStrategy
        from .strategies.auction_theory import AuctionTheoryStrategy
        from .strategies.iceberg_detection import IcebergDetectionStrategy
        from .strategies.confluence import ConfluenceZoneDetector

        p2_strategies = [
            TWAPDeviationStrategy(),
            MicrostructureStrategy(),
            TickMomentumStrategy(),
            DeltaDivergenceStrategy(),
            LiquiditySweepStrategy(),
            ORBStrategy(),
            VWAPBandsStrategy(),
            MarketInternalsStrategy(),
            AuctionTheoryStrategy(),
            IcebergDetectionStrategy(),
            ConfluenceZoneDetector(),
        ]
        if hasattr(signal_engine, 'strategies') and isinstance(signal_engine.strategies, dict):
            for strategy in p2_strategies:
                name = strategy.__class__.__name__
                signal_engine.strategies[name] = strategy
            log.info("Phase 2: %d additional strategies registered", len(p2_strategies))
        elif hasattr(signal_engine, 'register_strategies'):
            signal_engine.register_strategies(p2_strategies)
    except Exception as exc:
        log.warning("Phase 2 strategy registration failed: %s", exc)

    # Register Phase 3: cross-asset + options strategies
    try:
        from .strategies.cross_asset import CrossAssetStrategy
        from .strategies.options_gamma import OptionsGammaStrategy
        p3_strategies = [CrossAssetStrategy(), OptionsGammaStrategy()]
        for s in p3_strategies:
            signal_engine.strategies[s.name] = s
        log.info("Phase 3: cross-asset + options gamma strategies registered")
    except Exception as exc:
        log.warning("Phase 3 strategy registration failed: %s", exc)

    # Register Phase 5: advanced quant strategies
    try:
        from .strategies.volume_profile_advanced import VolumeProfileAdvancedStrategy
        from .strategies.delta_flow import DeltaFlowStrategy
        from .strategies.vpin import VPINStrategy
        from .strategies.options_flow import OptionsFlowStrategy
        from .strategies.kalman_fair_value import KalmanFairValueStrategy
        from .strategies.hurst_regime import HurstRegimeStrategy
        from .strategies.orderflow_imbalance import OrderFlowImbalanceStrategy

        p5_strategies = [
            VolumeProfileAdvancedStrategy(),
            DeltaFlowStrategy(),
            VPINStrategy(),
            OptionsFlowStrategy(),
            KalmanFairValueStrategy(),
            HurstRegimeStrategy(),
            OrderFlowImbalanceStrategy(),
        ]
        for s in p5_strategies:
            signal_engine.strategies[s.name] = s
        log.info("Phase 5: %d advanced quant strategies registered", len(p5_strategies))
    except Exception as exc:
        log.warning("Phase 5 quant strategy registration failed: %s", exc)

    # Register Phase 6: systematic quant models
    try:
        from .strategies.time_series_momentum import TimeSeriesMomentumStrategy
        from .strategies.volatility_targeting import VolatilityTargetingStrategy
        from .strategies.relative_value import RelativeValueStrategy
        from .strategies.macro_regime import MacroRegimeStrategy
        from .strategies.factor_correlation import FactorCorrelationStrategy

        p6_strategies = [
            TimeSeriesMomentumStrategy(),
            VolatilityTargetingStrategy(),
            RelativeValueStrategy(),
            MacroRegimeStrategy(),
            FactorCorrelationStrategy(),
        ]
        for s in p6_strategies:
            signal_engine.strategies[s.name] = s
        log.info("Phase 6: %d systematic quant models registered", len(p6_strategies))
    except Exception as exc:
        log.warning("Phase 6 quant model registration failed: %s", exc)

    # Initialize Phase 4 agents
    from .agents.market_brain import MarketBrain
    from .agents.app_optimizer import AppOptimizer

    market_brain   = MarketBrain(config, db, bus)
    app_optimizer  = AppOptimizer(config, db, bus)
    log.info("Phase 4: Market Brain + App Optimizer initialized")

    # Wire brain into signal engine for pattern-based boosting/suppression
    signal_engine._meta_learner = meta_learner

    agents = [signal_engine, trade_journal, chart_monitor, meta_learner,
              news_scanner, market_brain, app_optimizer]
    if dark_pool_agent is not None:
        agents.append(dark_pool_agent)

    log.info("All %d agents initialized", len(agents))

    # Initialize data feeds (Phase 2)
    rithmic_feed = None
    atas_bridge = None

    # Rithmic live feed
    if config.rithmic.user and config.rithmic.password:
        try:
            from .data.rithmic_feed import RithmicFeed
            rithmic_feed = RithmicFeed(config.rithmic, bus)  # pass RithmicConfig not AppConfig
            rithmic_feed.start()
            log.info("Rithmic feed started (user=%s, system=%s)",
                     config.rithmic.user, config.rithmic.system)
        except ImportError:
            log.warning("Rithmic feed module not available — falling back to simulated data")
        except Exception as exc:
            log.warning("Rithmic feed failed to start: %s — falling back to simulated data", exc)
    else:
        log.info("No Rithmic credentials — using simulated data feed")

    # ATAS CSV bridge
    if config.atas.csv_export_dir:
        try:
            from .data.atas_bridge import ATASBridge
            atas_bridge = ATASBridge(config, bus)
            atas_bridge.start_watching()
            log.info("ATAS bridge started (dir=%s)", config.atas.csv_export_dir)
        except ImportError:
            log.warning("ATAS bridge module not available")
        except Exception as exc:
            log.warning("ATAS bridge failed to start: %s", exc)

    # Phase 3: Alpaca real-time feed (for cross-asset live prices)
    alpaca_feed = None
    if config.alpaca.api_key and config.alpaca.api_secret and config.alpaca.enabled:
        try:
            from .data.alpaca_feed import AlpacaFeed

            def _on_alpaca_quote(asset_name: str, price: float, prev_close: float):
                pass  # prices injected into cross_asset_feed on each poll

            alpaca_feed = AlpacaFeed(
                api_key=config.alpaca.api_key,
                api_secret=config.alpaca.api_secret,
                on_quote=_on_alpaca_quote,
                feed=config.alpaca.feed,
            )
            alpaca_feed.start()
            log.info("Alpaca real-time feed started (SPY, QQQ, IWM, GLD, USO, HYG, TLT, VXX, UUP)")
        except Exception as exc:
            log.warning("Alpaca feed failed to start: %s", exc)
    else:
        log.info("No Alpaca credentials — cross-asset using yfinance 5m bars (~15min delayed)")

    # Phase 3: Cross-asset + options feed
    cross_asset_feed = None
    try:
        from .data.cross_asset_feed import CrossAssetFeed

        def _on_cross_asset(event_name: str, data: dict):
            from .event_bus import Event
            # Feed emits 'cross_asset_update' for full snapshot
            bus.publish(Event(type=EventType.CROSS_ASSET_UPDATE, data=data, source='cross_asset_feed'))
            # Also publish options sub-data if present
            if data.get('gex'):
                bus.publish(Event(type=EventType.OPTIONS_DATA_UPDATE, data=data.get('gex', {}), source='cross_asset_feed'))

        cross_asset_feed = CrossAssetFeed(callback=_on_cross_asset, alpaca_feed=alpaca_feed)
        cross_asset_feed.start()
        chart_monitor.set_cross_asset_feed(cross_asset_feed)
        src = "Alpaca live + yfinance" if alpaca_feed else "yfinance 5m"
        log.info("Cross-asset feed started [%s] (VIX, DXY, yields, gold, oil, NQ, BTC, GEX)", src)
    except Exception as exc:
        log.warning("Cross-asset feed failed to start: %s", exc)

    # Start news scanner streaming (Finnhub WebSocket + RSS feeds)
    try:
        news_scanner.start_streaming()
        log.info("News scanner streaming started")
    except Exception as exc:
        log.warning("News scanner streaming failed: %s", exc)

    # Start dark pool monitoring
    if dark_pool_agent is not None:
        try:
            dark_pool_agent.start_monitoring()
            log.info("Dark pool monitoring started")
        except Exception as exc:
            log.warning("Dark pool monitoring failed: %s", exc)

    # Initialize ML trainer (Phase 2)
    ml_trainer = None
    try:
        from .ml.trainer import MLTrainer
        ml_trainer = MLTrainer(config=config.ml, db=db, event_bus=bus)
        log.info("ML trainer initialized (model_dir=%s)", config.ml.model_dir)
    except ImportError:
        log.warning("ML trainer module not available")
    except Exception as exc:
        log.warning("ML trainer init failed: %s", exc)

    # Launch desktop app
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from .ui.app import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("MES Trading Intelligence")

    window = MainWindow(config, db, bus,
                        market_brain=market_brain,
                        app_optimizer=app_optimizer,
                        meta_learner=meta_learner)

    # Demo mode: simulate some data so the UI isn't empty on first launch
    _seed_demo_data(chart_monitor, signal_engine, news_scanner, db)

    # Periodic signal evaluation (every 5 seconds when data is flowing)
    def _evaluate_signals():
        market_data = chart_monitor.get_market_data()
        if market_data.get("prices"):
            signal_engine.evaluate(market_data)

    eval_timer = QTimer()
    eval_timer.timeout.connect(_evaluate_signals)
    eval_timer.start(5000)

    # Periodic ML model evaluation (every 5 minutes — check if retrain needed)
    def _check_ml_retrain():
        if ml_trainer is None or not config.ml.auto_retrain:
            return
        try:
            recent_runs = db.get_training_runs(limit=1)
            should_train = False
            reason = ""
            if recent_runs:
                latest_accuracy = recent_runs[0].get("accuracy", 0) or 0
                if latest_accuracy < config.ml.retrain_threshold:
                    should_train = True
                    reason = "accuracy_below_threshold"
                    log.info("ML accuracy %.3f below threshold %.3f — triggering retrain",
                             latest_accuracy, config.ml.retrain_threshold)
            else:
                should_train = True
                reason = "initial_training"
                log.info("No ML training runs found — triggering initial training")

            if should_train:
                bus.publish(Event(
                    type=EventType.ML_TRAINING_STARTED,
                    data={"reason": reason},
                    source="main",
                ))
                # Gather training data from DB
                trades = db.get_trades(limit=5000)
                market_history = []  # populated from chart_monitor snapshots
                trade_history = [
                    {"pnl": getattr(t, "pnl", 0) or 0,
                     "direction": getattr(t, "direction", ""),
                     "entry_price": getattr(t, "entry_price", 0),
                     "exit_price": getattr(t, "exit_price", 0)}
                    for t in (trades or [])
                ]
                report = ml_trainer.train(market_history, trade_history)
                if report:
                    bus.publish(Event(
                        type=EventType.ML_TRAINING_COMPLETE,
                        data={"accuracy": report.mean_accuracy if hasattr(report, 'mean_accuracy') else 0},
                        source="main",
                    ))
        except Exception:
            log.exception("ML retrain check failed")

    ml_timer = QTimer()
    ml_timer.timeout.connect(_check_ml_retrain)
    ml_timer.start(300_000)  # 5 minutes

    # Pre-market scan: economic calendar + catalysts
    def _premarket_scan():
        try:
            catalysts = news_scanner.scan_premarket_catalysts()
            for c in catalysts:
                if c.expected_impact >= 2:
                    bus.publish(Event(
                        type=EventType.NEWS_ALERT,
                        source="news_scanner",
                        data={
                            "headline": f"UPCOMING: {c.name} (impact={c.expected_impact}/3)",
                            "sentiment_score": 0.0,
                            "category": c.category,
                        },
                    ))
        except Exception:
            log.debug("Pre-market scan skipped")

    QTimer.singleShot(3000, _premarket_scan)  # 3s after launch

    # Periodic catalyst scan (every 30 min)
    catalyst_timer = QTimer()
    catalyst_timer.timeout.connect(_premarket_scan)
    catalyst_timer.start(1_800_000)

    # Wire advanced order flow alerts into signals feed
    def _on_orderflow_alert(event: Event):
        """Forward order flow alerts (institutional patterns, divergence) to news feed."""
        data = event.data
        alert_type = data.get("alert_type", "")
        if alert_type in ("INSTITUTIONAL", "BIG_TRADE"):
            pattern = data.get("pattern_type", "")
            side = data.get("side", "")
            conf = data.get("confidence", 0)
            size = data.get("estimated_size", 0)
            headline = f"ORDER FLOW: {pattern} {side} detected (conf={conf:.0%}, size={size})"
            bus.publish(Event(
                type=EventType.NEWS_ALERT,
                source="orderflow_advanced",
                data={"headline": headline, "sentiment_score": 0.0,
                      "category": "order_flow"},
            ))
        elif alert_type == "CD_DIVERGENCE":
            div_type = data.get("divergence_type", "")
            price = data.get("price_extreme", 0)
            conf = data.get("confidence", 0)
            headline = f"DELTA DIVERGENCE: {div_type} @ {price:.2f} (conf={conf:.0%})"
            bus.publish(Event(
                type=EventType.NEWS_ALERT,
                source="orderflow_advanced",
                data={"headline": headline, "sentiment_score": 0.0,
                      "category": "order_flow"},
            ))

    try:
        bus.subscribe(EventType.ORDER_FLOW_UPDATE, _on_orderflow_alert)
    except Exception:
        pass

    window.show()
    log.info("Desktop app launched — ready for trading")

    sys.exit(app.exec())


def _seed_demo_data(chart_monitor, signal_engine, news_scanner, db):
    """Seed some demo data so the UI has something to show on first launch."""
    import json
    import random
    import time
    from .orderflow import Tick

    log.info("Seeding demo data...")

    # Simulate a trading session with realistic MES price action
    base_price = 5573.25
    t = time.time() - 3600  # start 1 hour ago

    for i in range(500):
        # Random walk with slight upward drift
        change = random.gauss(0, 0.5)
        base_price += change
        base_price = round(base_price / 0.25) * 0.25  # snap to tick

        size = random.randint(1, 15)
        is_buy = random.random() > 0.48  # slight buy bias

        chart_monitor.process_tick(base_price, size, is_buy, t)
        t += random.uniform(0.5, 5.0)

    # Some demo news
    demo_news = [
        ("Fed officials signal potential rate hold at next meeting", "Finnhub"),
        ("US-China trade talks resume as tariff deadline approaches", "Reuters"),
        ("TRUMP: 'Markets will be very happy with what's coming'", "X/Twitter"),
        ("Q1 GDP revised up to 2.4%, beating expectations", "CNBC"),
        ("Oil drops 3% on unexpected inventory build", "Bloomberg"),
    ]
    for headline, source in demo_news:
        news_scanner.process_headline(headline, source)

    # Demo dark pool prints (Phase 2)
    dp_time = time.time() - 1800  # 30 minutes ago
    demo_dp_prints = [
        {"timestamp": dp_time, "symbol": "MES", "price": 5570.00, "size": 2500,
         "notional": 13_925_000.0, "venue": "DARK", "is_block": 1},
        {"timestamp": dp_time + 120, "symbol": "MES", "price": 5572.50, "size": 1800,
         "notional": 10_030_500.0, "venue": "DARK", "is_block": 1},
        {"timestamp": dp_time + 300, "symbol": "MES", "price": 5575.25, "size": 500,
         "notional": 2_787_625.0, "venue": "DARK", "is_block": 0},
    ]
    for dp in demo_dp_prints:
        try:
            db.insert_dark_pool_print(dp)
        except Exception:
            pass  # table might not exist on first run before schema migration

    # Demo confluence zones (Phase 2)
    demo_zones = [
        {"timestamp": time.time() - 600, "price": 5570.00,
         "triggers": json.dumps(["VAL", "dark_pool_cluster", "prior_day_low"]),
         "confluence_score": 0.85, "zone_type": "support", "status": "active"},
        {"timestamp": time.time() - 300, "price": 5580.50,
         "triggers": json.dumps(["VAH", "POC_yesterday", "round_number"]),
         "confluence_score": 0.78, "zone_type": "resistance", "status": "active"},
    ]
    for zone in demo_zones:
        try:
            db.insert_confluence_zone(zone)
        except Exception:
            pass

    # Demo trades so journal/analytics aren't empty
    demo_trades = [
        {"signal_id": None, "direction": "LONG", "quantity": 2,
         "entry_price": 5568.50, "exit_price": 5573.25, "pnl": 47.26,
         "entry_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 7200)),
         "exit_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 6600)),
         "stop_price": 5565.00, "target_price": 5575.00, "fees": 2.48,
         "r_multiple": 1.36, "hold_time_sec": 600, "source": "demo",
         "notes": "VWAP bounce + delta confirmation", "status": "closed"},
        {"signal_id": None, "direction": "SHORT", "quantity": 1,
         "entry_price": 5580.00, "exit_price": 5576.75, "pnl": 15.63,
         "entry_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 5400)),
         "exit_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 4800)),
         "stop_price": 5582.50, "target_price": 5574.00, "fees": 1.24,
         "r_multiple": 1.30, "hold_time_sec": 600, "source": "demo",
         "notes": "VAH rejection + negative delta divergence", "status": "closed"},
        {"signal_id": None, "direction": "LONG", "quantity": 1,
         "entry_price": 5575.00, "exit_price": 5572.50, "pnl": -13.74,
         "entry_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 3600)),
         "exit_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 3000)),
         "stop_price": 5572.50, "target_price": 5580.00, "fees": 1.24,
         "r_multiple": -1.00, "hold_time_sec": 600, "source": "demo",
         "notes": "Stopped out — fake breakout above IB high", "status": "closed"},
        {"signal_id": None, "direction": "LONG", "quantity": 2,
         "entry_price": 5571.25, "exit_price": 5577.50, "pnl": 60.52,
         "entry_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 2400)),
         "exit_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 1500)),
         "stop_price": 5568.00, "target_price": 5578.00, "fees": 2.48,
         "r_multiple": 1.92, "hold_time_sec": 900, "source": "demo",
         "notes": "Strong buy absorption at VAL + stacked imbalance", "status": "closed"},
        {"signal_id": None, "direction": "SHORT", "quantity": 1,
         "entry_price": 5577.75, "exit_price": 5574.00, "pnl": 17.51,
         "entry_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 900)),
         "exit_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 300)),
         "stop_price": 5580.00, "target_price": 5573.00, "fees": 1.24,
         "r_multiple": 1.67, "hold_time_sec": 600, "source": "demo",
         "notes": "Excess at session high + poor high structure", "status": "closed"},
    ]
    for trade in demo_trades:
        try:
            db.insert_trade(trade)
        except Exception:
            pass

    log.info("Demo data seeded: 500 ticks, %d news items, %d dark pool prints, %d confluence zones, %d trades",
             len(demo_news), len(demo_dp_prints), len(demo_zones), len(demo_trades))


if __name__ == "__main__":
    main()
