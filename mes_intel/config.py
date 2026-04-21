"""Configuration management for MES Trading Intelligence System."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


CONFIG_DIR = Path(__file__).parent.parent / "var" / "mes_intel"
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_PATH = CONFIG_DIR / "mes_intel.db"


@dataclass
class RithmicConfig:
    user: str = ""
    password: str = ""
    system: str = "Rithmic Paper Trading"
    gateway: str = "CHICAGO"
    account_id: str = ""
    app_name: str = "MES_Intel"
    app_version: str = "3.0"
    interface: str = "TcpIp"

    # Server URLs (host:port)
    SERVERS: dict = field(default_factory=lambda: {
        "Rithmic Test":          "rituz00100.rithmic.com:443",
        "Rithmic Paper Trading": "ritpa11120.11.rithmic.com:443",
        "Rithmic 01":            "ritpz01000.01.rithmic.com:443",
    })

    @property
    def url(self) -> str:
        return self.SERVERS.get(self.system, self.SERVERS["Rithmic Paper Trading"])

    @property
    def host(self) -> str:
        return self.url.split(":")[0]

    @property
    def port(self) -> int:
        parts = self.url.split(":")
        return int(parts[1]) if len(parts) > 1 else 443

    # Aliases used by RithmicFeed (rapi expects these names)
    @property
    def username(self) -> str:
        return self.user

    @property
    def system_name(self) -> str:
        return self.system


@dataclass
class AlpacaConfig:
    """Alpaca Markets — free real-time US stock/ETF data.
    Get a free paper account at alpaca.markets → API Keys section."""
    api_key: str = ""
    api_secret: str = ""
    # IEX feed is free real-time; SIP feed requires paid subscription
    feed: str = "iex"
    enabled: bool = True


@dataclass
class ATASConfig:
    csv_export_dir: str = ""          # ATAS cluster data CSV export path
    alert_file: str = ""              # ATAS alert bridge file path
    poll_interval_ms: int = 500


@dataclass
class SignalConfig:
    # Ensemble voting
    min_strategies_agree: int = 3     # minimum strategies that must agree
    min_confidence: float = 0.70      # minimum ensemble confidence to signal
    signal_cooldown_sec: int = 120    # seconds between signals
    eval_interval_ms: int = 15000     # timer interval for strategy evaluation
    skip_duplicate_snapshots: bool = True

    # Strategy weights (auto-adjusted by meta-learner)
    weights: dict = field(default_factory=lambda: {
        # Phase 1 — core
        "mean_reversion": 1.0,
        "momentum": 1.0,
        "stat_arb": 0.8,
        "order_flow": 1.2,
        "gex_model": 1.0,
        "hmm_regime": 0.8,
        "ml_scorer": 1.5,
        # Phase 2 — quant
        "TWAPDeviationStrategy": 0.8,
        "MicrostructureStrategy": 1.0,
        "TickMomentumStrategy": 0.9,
        "DeltaDivergenceStrategy": 1.1,
        "LiquiditySweepStrategy": 1.0,
        "ORBStrategy": 1.0,
        "VWAPBandsStrategy": 1.0,
        "MarketInternalsStrategy": 0.8,
        "AuctionTheoryStrategy": 1.1,
        "IcebergDetectionStrategy": 0.9,
        "ConfluenceZoneDetector": 1.2,
        # Phase 3 — cross-asset + options
        "cross_asset": 1.0,
        "options_gamma": 1.0,
        # Phase 5 — advanced quant
        "volume_profile_advanced": 1.1,
        "delta_flow": 1.2,
        "vpin": 1.0,
        "options_flow": 1.0,
        "kalman_fair_value": 1.0,
        "hurst_regime": 0.9,
        "orderflow_imbalance": 1.2,
        # Phase 6 — systematic models
        "ts_momentum": 1.0,
        "vol_targeting": 0.9,
        "relative_value": 0.8,
        "macro_regime": 0.9,
        "factor_correlation": 0.8,
    })

    # Risk
    max_position_size: int = 2        # max MES contracts
    tick_value: float = 1.25          # MES tick value ($1.25 per tick)
    point_value: float = 5.0          # MES point value ($5 per point)


@dataclass
class NewsConfig:
    finnhub_key: str = ""
    alpha_vantage_key: str = ""
    twitter_bearer: str = ""
    poll_interval_sec: int = 30
    trump_alert_priority: bool = True


@dataclass
class DarkPoolConfig:
    finnhub_key: str = ""
    poll_interval_sec: int = 60
    alert_threshold_notional: float = 10_000_000
    enabled: bool = True


@dataclass
class CrossAssetConfig:
    price_interval_sec: int = 30
    options_interval_sec: int = 300
    off_hours_price_interval_sec: int = 300
    off_hours_options_interval_sec: int = 900


@dataclass
class AutonomyConfig:
    enabled: bool = True
    paper_mode_only: bool = True
    check_interval_sec: int = 3600
    min_closed_trades: int = 12
    min_context_closed_trades: int = 6
    validation_min_closed_trades: int = 8
    min_confidence_floor: float = 0.65
    min_confidence_ceiling: float = 0.85
    confidence_step_up: float = 0.02
    confidence_step_down: float = 0.01
    eval_interval_step_ms: int = 5000
    min_eval_interval_ms: int = 5000
    max_eval_interval_ms: int = 60000
    # min_strategies_agree tuning
    min_strategies_agree_floor: int = 2
    min_strategies_agree_ceiling: int = 6
    # signal_cooldown_sec tuning
    cooldown_floor_sec: int = 60
    cooldown_ceiling_sec: int = 600
    cooldown_step_sec: int = 30
    # gradual policy decay
    policy_fade_start_fraction: float = 0.7  # fade starts at this fraction of decay window
    rollback_win_rate_drop: float = 0.08
    rollback_avg_pnl_drop: float = 25.0
    regime_confidence_bounds: dict = field(default_factory=lambda: {
        "trending": [0.60, 0.82],
        "ranging": [0.68, 0.88],
        "volatile": [0.72, 0.90],
        "quiet": [0.64, 0.84],
        "breakout": [0.62, 0.86],
        "unknown": [0.65, 0.85],
    })
    regime_eval_interval_bounds_ms: dict = field(default_factory=lambda: {
        "trending": [5000, 30000],
        "ranging": [10000, 45000],
        "volatile": [12000, 60000],
        "quiet": [8000, 45000],
        "breakout": [5000, 30000],
        "unknown": [5000, 60000],
    })
    weekly_report_weekday: int = 6
    policy_decay_days: int = 7  # days of inactivity before a context policy is discarded


@dataclass
class MLConfig:
    model_dir: str = str(CONFIG_DIR / "models")
    retrain_threshold: float = 0.55
    walk_forward_splits: int = 5
    auto_retrain: bool = True


@dataclass
class AmpSyncConfig:
    """AMP Futures / Rithmic trade sync settings."""
    # Rithmic credentials (mirror of RithmicConfig for convenience — set once)
    amp_username: str = ""
    amp_password: str = ""
    rithmic_server: str = "Rithmic Paper Trading"

    # Auto-sync
    auto_sync_enabled: bool = True
    auto_sync_interval: int = 30          # seconds between live syncs

    # CSV import
    csv_import_path: str = str(Path.home() / "Downloads")  # default browse location

    # Matching
    match_method: str = "FIFO"            # 'FIFO' or 'LIFO'


@dataclass
class UIConfig:
    vanity_enabled: bool = False
    sound_enabled: bool = True
    sound_volume: float = 0.7
    layout_file: str = str(CONFIG_DIR / "layout.json")
    particle_effects: bool = False
    animations_enabled: bool = False


@dataclass
class AppConfig:
    rithmic: RithmicConfig = field(default_factory=RithmicConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    atas: ATASConfig = field(default_factory=ATASConfig)
    signals: SignalConfig = field(default_factory=SignalConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    dark_pool: DarkPoolConfig = field(default_factory=DarkPoolConfig)
    cross_asset: CrossAssetConfig = field(default_factory=CrossAssetConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    ui_config: UIConfig = field(default_factory=UIConfig)
    amp_sync: AmpSyncConfig = field(default_factory=AmpSyncConfig)

    # AI Assistant
    anthropic_api_key: str = ""
    anthropic_bypass_mode: bool = False

    # UI
    theme: str = "retro"
    window_width: int = 1600
    window_height: int = 1000

    # Database
    db_path: str = str(DB_PATH)

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()

        # Load from config file
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            cfg = cls._from_dict(data)

        # Override Rithmic creds from .env or existing settings.json
        cfg._load_env_overrides()
        return cfg

    @classmethod
    def _from_dict(cls, data: dict) -> "AppConfig":
        cfg = cls()
        if "rithmic" in data:
            for k, v in data["rithmic"].items():
                if hasattr(cfg.rithmic, k):
                    setattr(cfg.rithmic, k, v)
        if "atas" in data:
            for k, v in data["atas"].items():
                if hasattr(cfg.atas, k):
                    setattr(cfg.atas, k, v)
        if "signals" in data:
            for k, v in data["signals"].items():
                if hasattr(cfg.signals, k):
                    setattr(cfg.signals, k, v)
        if "news" in data:
            for k, v in data["news"].items():
                if hasattr(cfg.news, k):
                    setattr(cfg.news, k, v)
        if "dark_pool" in data:
            for k, v in data["dark_pool"].items():
                if hasattr(cfg.dark_pool, k):
                    setattr(cfg.dark_pool, k, v)
        if "cross_asset" in data:
            for k, v in data["cross_asset"].items():
                if hasattr(cfg.cross_asset, k):
                    setattr(cfg.cross_asset, k, v)
        if "ml" in data:
            for k, v in data["ml"].items():
                if hasattr(cfg.ml, k):
                    setattr(cfg.ml, k, v)
        if "autonomy" in data:
            for k, v in data["autonomy"].items():
                if hasattr(cfg.autonomy, k):
                    setattr(cfg.autonomy, k, v)
        if "ui_config" in data:
            for k, v in data["ui_config"].items():
                if hasattr(cfg.ui_config, k):
                    setattr(cfg.ui_config, k, v)
        if "amp_sync" in data:
            for k, v in data["amp_sync"].items():
                if hasattr(cfg.amp_sync, k):
                    setattr(cfg.amp_sync, k, v)
        for k in ("theme", "window_width", "window_height", "db_path", "anthropic_api_key", "anthropic_bypass_mode"):
            if k in data:
                setattr(cfg, k, data[k])
        return cfg

    def _load_env_overrides(self):
        """Pull credentials from .env and existing settings.json."""
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

        # Rithmic from env
        if not self.rithmic.user:
            self.rithmic.user = os.environ.get("RITHMIC_USER", "")
        if not self.rithmic.password:
            self.rithmic.password = os.environ.get("RITHMIC_PASSWORD", "")

        # Existing trade_journal settings.json
        tj_settings = Path(__file__).parent.parent / "trade_journal" / "settings.json"
        if tj_settings.exists():
            with open(tj_settings, encoding="utf-8") as f:
                tj = json.load(f)
            if not self.rithmic.user and tj.get("rithmic_user"):
                self.rithmic.user = tj["rithmic_user"]
            if not self.rithmic.password and tj.get("rithmic_password"):
                self.rithmic.password = tj["rithmic_password"]
            if tj.get("rithmic_system"):
                self.rithmic.system = tj["rithmic_system"]
            if tj.get("rithmic_gateway"):
                self.rithmic.gateway = tj["rithmic_gateway"]
            if tj.get("rithmic_account_id"):
                self.rithmic.account_id = tj["rithmic_account_id"]

        # AMP Sync creds from env (fallback to Rithmic creds if not set separately)
        if not self.amp_sync.amp_username:
            self.amp_sync.amp_username = os.environ.get(
                "AMP_USERNAME", self.rithmic.user
            )
        if not self.amp_sync.amp_password:
            self.amp_sync.amp_password = os.environ.get(
                "AMP_PASSWORD", self.rithmic.password
            )

        # News keys from env
        self.news.twitter_bearer = os.environ.get("TWITTER_BEARER_TOKEN", self.news.twitter_bearer)

        # Dark pool key from env (fall back to news finnhub key)
        if not self.dark_pool.finnhub_key:
            self.dark_pool.finnhub_key = os.environ.get(
                "FINNHUB_KEY", self.news.finnhub_key
            )

        # Anthropic API key from env
        if not self.anthropic_api_key:
            self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
