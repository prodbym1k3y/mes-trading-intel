"""Dark Pool Detection Agent — monitors SPY dark pool prints as proxy for MES institutional activity.

Sources: Finnhub dark pool endpoint, FINRA ADF/TRF data.
Detects block trades, builds price-level heatmaps, and feeds significant levels
into the confluence zone scorer for support/resistance identification.
"""
from __future__ import annotations

import logging
import time
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType

log = logging.getLogger(__name__)

# Block trade threshold — prints above this notional are flagged
BLOCK_THRESHOLD = 10_000_000  # $10M

# How many price levels to keep in the heatmap
MAX_HEATMAP_LEVELS = 200

# How quickly old prints decay in relevance (half-life in seconds)
RECENCY_HALF_LIFE = 3600  # 1 hour

# Minimum notional to even track a print
MIN_NOTIONAL = 100_000  # $100K

# FINRA venues
FINRA_VENUES = {"ADF", "TRF_CARTERET", "TRF_CHICAGO"}

# Round price to this tick size for level aggregation (SPY)
LEVEL_TICK_SIZE = 0.25


def _round_to_tick(price: float, tick: float = LEVEL_TICK_SIZE) -> float:
    """Round a price down to the nearest tick increment."""
    return round(round(price / tick) * tick, 2)


@dataclass
class DarkPoolPrint:
    """A single dark pool print."""
    timestamp: float
    symbol: str
    price: float
    size: int
    notional: float
    venue: str = "unknown"
    is_block: bool = False

    def __post_init__(self):
        self.is_block = self.notional >= BLOCK_THRESHOLD


@dataclass
class DarkPoolLevel:
    """Aggregated dark pool volume at a price level."""
    price: float
    total_volume: int = 0
    total_notional: float = 0.0
    print_count: int = 0
    block_count: int = 0
    last_print_time: float = 0.0
    # Track whether this level has held as S/R after being established
    sr_tests: int = 0
    sr_holds: int = 0

    @property
    def hold_rate(self) -> float:
        """How often this dark pool level holds as support/resistance."""
        if self.sr_tests == 0:
            return 0.0
        return self.sr_holds / self.sr_tests

    def add_print(self, dp: DarkPoolPrint):
        self.total_volume += dp.size
        self.total_notional += dp.notional
        self.print_count += 1
        if dp.is_block:
            self.block_count += 1
        self.last_print_time = max(self.last_print_time, dp.timestamp)


class DarkPoolAgent:
    """Dark pool detection agent — monitors institutional prints and builds S/R heatmaps."""

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus

        # Price level heatmap: rounded_price -> DarkPoolLevel
        self.levels: dict[float, DarkPoolLevel] = {}

        # Recent prints buffer (for display / analysis)
        self.recent_prints: list[DarkPoolPrint] = []
        self._max_recent = 500

        # Current SPY price (updated via PRICE_UPDATE events)
        self.current_price: float = 0.0

        # Historical S/R tracking: levels that were tested
        self.sr_history: list[dict] = []

        # Monitoring state
        self._running = False
        self._poll_interval = getattr(config.news, "poll_interval_sec", 30)

        # Subscribe to price updates so we can track S/R tests
        self.bus.subscribe(EventType.PRICE_UPDATE, self._on_price_update)
        self.bus.subscribe(EventType.LESSON_LEARNED, self._on_lesson_learned)
        self.bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.QUANT_SIGNAL, self._on_quant_signal)

        # Regime context for divergence detection
        self._current_regime: str = "unknown"
        self._expected_volume_z: float = 0.0    # from quant state
        self._volume_z: float = 0.0

        log.info("Dark Pool Agent initialized")

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    def process_print(self, dp: DarkPoolPrint):
        """Process a single dark pool print — aggregate into levels, check for alerts."""
        if dp.notional < MIN_NOTIONAL:
            return

        # Aggregate into level
        level_price = _round_to_tick(dp.price)
        if level_price not in self.levels:
            self.levels[level_price] = DarkPoolLevel(price=level_price)
        self.levels[level_price].add_print(dp)

        # Buffer recent prints
        self.recent_prints.append(dp)
        if len(self.recent_prints) > self._max_recent:
            self.recent_prints = self.recent_prints[-self._max_recent:]

        # Alert on block trades
        if dp.is_block:
            relative = self._relative_position(dp.price)
            # Detect regime divergence: heavy dark pool flow contra to market regime
            regime_divergence = False
            if self._current_regime == "trending" and self._volume_z < 0:
                regime_divergence = True   # dark pool buying/selling in quiet trending phase
            elif self._current_regime == "ranging" and abs(self._volume_z) > 2.0:
                regime_divergence = True   # unusual dark pool in a ranging market
            log.info(
                "DARK POOL BLOCK: %s %d shares @ %.2f ($%.1fM) via %s [%s current price]%s",
                dp.symbol, dp.size, dp.price, dp.notional / 1_000_000,
                dp.venue, relative,
                " [REGIME DIVERGENCE]" if regime_divergence else "",
            )
            self.bus.publish(Event(
                type=EventType.DARK_POOL_ALERT,
                source="dark_pool",
                data={
                    "symbol": dp.symbol,
                    "price": dp.price,
                    "size": dp.size,
                    "notional": dp.notional,
                    "venue": dp.venue,
                    "is_block": True,
                    "relative_position": relative,
                    "level_total_notional": self.levels[level_price].total_notional,
                    "level_print_count": self.levels[level_price].print_count,
                    "regime": self._current_regime,
                    "regime_divergence": regime_divergence,
                    "volume_z": self._volume_z,
                },
                priority=7,
            ))

        # Prune stale levels if we have too many
        self._prune_levels()

    def get_heatmap_data(self, num_levels: int = 50) -> list[tuple[float, int, float]]:
        """Return heatmap data for chart overlay.

        Returns list of (price, volume, recency_weight) sorted by volume descending.
        recency_weight is 0.0-1.0 where 1.0 = very recent.
        """
        now = time.time()
        scored: list[tuple[float, int, float]] = []

        for level in self.levels.values():
            if level.total_volume == 0:
                continue

            # Exponential decay based on time since last print
            age = now - level.last_print_time
            recency = 2.0 ** (-age / RECENCY_HALF_LIFE)
            recency = max(0.0, min(1.0, recency))

            scored.append((level.price, level.total_volume, recency))

        # Sort by weighted volume (volume * recency) descending
        scored.sort(key=lambda x: x[1] * x[2], reverse=True)
        return scored[:num_levels]

    def get_nearby_levels(self, price: Optional[float] = None,
                          range_points: float = 5.0) -> list[DarkPoolLevel]:
        """Get dark pool levels near a given price, sorted by notional."""
        ref = price or self.current_price
        if ref <= 0:
            return []

        nearby = [
            lvl for lvl in self.levels.values()
            if abs(lvl.price - ref) <= range_points and lvl.total_notional > 0
        ]
        nearby.sort(key=lambda x: x.total_notional, reverse=True)
        return nearby

    def get_support_resistance(self, price: Optional[float] = None,
                               range_points: float = 10.0,
                               min_notional: float = 5_000_000) -> dict:
        """Identify dark-pool-based support and resistance levels.

        Returns dict with 'support' (levels below price) and 'resistance' (levels above).
        """
        ref = price or self.current_price
        if ref <= 0:
            return {"support": [], "resistance": []}

        support = []
        resistance = []

        for lvl in self.levels.values():
            if lvl.total_notional < min_notional:
                continue
            if abs(lvl.price - ref) > range_points:
                continue

            entry = {
                "price": lvl.price,
                "notional": lvl.total_notional,
                "print_count": lvl.print_count,
                "block_count": lvl.block_count,
                "hold_rate": lvl.hold_rate,
                "sr_tests": lvl.sr_tests,
            }

            if lvl.price < ref:
                support.append(entry)
            elif lvl.price > ref:
                resistance.append(entry)

        support.sort(key=lambda x: x["price"], reverse=True)  # nearest first
        resistance.sort(key=lambda x: x["price"])  # nearest first

        return {"support": support, "resistance": resistance}

    # ------------------------------------------------------------------
    # Monitoring / polling
    # ------------------------------------------------------------------

    def start_monitoring(self):
        """Start polling for dark pool prints. Uses Finnhub if key available,
        otherwise falls back to simulated data."""
        self._running = True
        self.bus.publish(Event(
            type=EventType.AGENT_STARTED,
            source="dark_pool",
            data={"agent": "dark_pool"},
        ))

        finnhub_key = self.config.news.finnhub_key

        if finnhub_key:
            log.info("Dark Pool Agent: starting Finnhub dark pool monitoring")
            self._poll_finnhub(finnhub_key)
        else:
            log.info("Dark Pool Agent: no Finnhub key, using simulated data")
            self._run_simulated()

    def stop_monitoring(self):
        """Stop the monitoring loop."""
        self._running = False
        self.bus.publish(Event(
            type=EventType.AGENT_STOPPED,
            source="dark_pool",
            data={"agent": "dark_pool"},
        ))
        log.info("Dark Pool Agent stopped")

    def _poll_finnhub(self, api_key: str):
        """Poll Finnhub dark pool endpoint for SPY prints."""
        import urllib.request
        import urllib.error

        base_url = "https://finnhub.io/api/v1/stock/dark-pool"

        while self._running:
            try:
                url = f"{base_url}?symbol=SPY&token={api_key}"
                req = urllib.request.Request(url, headers={"User-Agent": "mes-intel/2.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())

                if isinstance(data, list):
                    for item in data:
                        dp = self._parse_finnhub_print(item)
                        if dp:
                            self.process_print(dp)
                elif isinstance(data, dict) and "data" in data:
                    for item in data["data"]:
                        dp = self._parse_finnhub_print(item)
                        if dp:
                            self.process_print(dp)

            except urllib.error.HTTPError as e:
                log.warning("Finnhub dark pool HTTP error: %s", e)
            except urllib.error.URLError as e:
                log.warning("Finnhub dark pool connection error: %s", e)
            except Exception:
                log.exception("Dark pool polling error")

            time.sleep(self._poll_interval)

    def _parse_finnhub_print(self, item: dict) -> Optional[DarkPoolPrint]:
        """Parse a Finnhub dark pool response item into a DarkPoolPrint."""
        try:
            price = float(item.get("price", 0))
            size = int(item.get("volume", item.get("size", 0)))
            if price <= 0 or size <= 0:
                return None

            notional = price * size
            venue = item.get("venue", item.get("exchange", "unknown"))

            # Parse timestamp — Finnhub may use epoch or ISO format
            ts_raw = item.get("timestamp", item.get("t", 0))
            if isinstance(ts_raw, str):
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = time.time()
            else:
                ts = float(ts_raw) if ts_raw else time.time()

            return DarkPoolPrint(
                timestamp=ts,
                symbol=item.get("symbol", "SPY"),
                price=price,
                size=size,
                notional=notional,
                venue=venue,
            )
        except (ValueError, TypeError, KeyError) as e:
            log.debug("Failed to parse dark pool print: %s — %s", item, e)
            return None

    @staticmethod
    def parse_finra_adf_trf(raw_line: str) -> Optional[DarkPoolPrint]:
        """Parse a FINRA ADF/TRF data line into a DarkPoolPrint.

        Expected CSV format: timestamp,symbol,price,size,venue
        """
        try:
            parts = raw_line.strip().split(",")
            if len(parts) < 4:
                return None

            ts = float(parts[0])
            symbol = parts[1].strip().upper()
            price = float(parts[2])
            size = int(parts[3])
            venue = parts[4].strip() if len(parts) > 4 else "FINRA"

            if price <= 0 or size <= 0:
                return None

            return DarkPoolPrint(
                timestamp=ts,
                symbol=symbol,
                price=price,
                size=size,
                notional=price * size,
                venue=venue,
            )
        except (ValueError, IndexError):
            return None

    def _run_simulated(self):
        """Generate simulated dark pool prints for testing / demo mode."""
        import random

        log.info("Dark Pool Agent: running simulated dark pool data")

        # Simulate around a base price
        base_price = 540.0  # approximate SPY price
        venues = ["ADF", "TRF_CARTERET", "TRF_CHICAGO", "BATS_DARK", "IEX_DARK"]

        while self._running:
            try:
                # Generate 1-5 prints per cycle
                num_prints = random.randint(1, 5)
                for _ in range(num_prints):
                    # Price clustered around base with occasional outliers
                    offset = random.gauss(0, 1.5)
                    price = round(base_price + offset, 2)

                    # Size distribution: mostly small, occasionally massive
                    r = random.random()
                    if r < 0.01:
                        # Block trade — rare
                        size = random.randint(50_000, 500_000)
                    elif r < 0.1:
                        # Large print
                        size = random.randint(10_000, 50_000)
                    else:
                        # Normal dark pool print
                        size = random.randint(500, 10_000)

                    dp = DarkPoolPrint(
                        timestamp=time.time(),
                        symbol="SPY",
                        price=price,
                        size=size,
                        notional=price * size,
                        venue=random.choice(venues),
                    )
                    self.process_print(dp)

                # Slowly drift the base price
                base_price += random.gauss(0, 0.05)

            except Exception:
                log.exception("Simulated dark pool error")

            time.sleep(self._poll_interval)

    # ------------------------------------------------------------------
    # S/R tracking
    # ------------------------------------------------------------------

    def _on_lesson_learned(self, event: Event):
        """Receive cross-agent lessons — store knowledge about which DP levels predicted correctly."""
        data = event.data
        target = data.get("target_agent", "")
        if target not in ("dark_pool", "all"):
            return
        lesson_type = data.get("lesson_type", "")
        description = data.get("description", "")
        impact = data.get("impact_score", 0.0)
        try:
            self.db.upsert_agent_knowledge(
                agent_name="dark_pool",
                knowledge_type=f"lesson:{lesson_type}",
                key=f"ts_{int(event.timestamp)}",
                value={"description": description, "impact": impact},
                confidence=min(1.0, abs(impact)),
            )
        except Exception:
            pass

    def _on_trade_result(self, event: Event):
        """Learn from trade outcome — record which DP levels were predictive."""
        outcome = event.data.get("outcome", "")
        pnl = event.data.get("pnl", 0)
        regime = event.data.get("regime", "")
        if not outcome:
            return
        # Persist current significant levels with outcome context
        try:
            top_levels = sorted(
                self.levels.values(),
                key=lambda l: l.total_notional,
                reverse=True,
            )[:3]
            for lvl in top_levels:
                self.db.upsert_agent_knowledge(
                    agent_name="dark_pool",
                    knowledge_type="level_outcome",
                    key=f"{lvl.price:.2f}_{outcome}",
                    value={
                        "price": lvl.price,
                        "notional": lvl.total_notional,
                        "sr_tests": lvl.sr_tests,
                        "sr_holds": lvl.sr_holds,
                        "outcome": outcome,
                        "pnl": pnl,
                        "regime": regime,
                    },
                    confidence=min(1.0, abs(pnl) / 20.0),
                )
        except Exception:
            pass

    def _on_regime_change(self, event: Event):
        """Track current market regime for divergence detection."""
        self._current_regime = event.data.get("to_regime", "unknown")

    def _on_quant_signal(self, event: Event):
        """Use Market Brain volume analysis to contextualize prints."""
        self._volume_z = event.data.get("volume_z", 0.0)
        regime = event.data.get("regime", "unknown")
        if regime and regime != "unknown":
            self._current_regime = regime

    def _on_price_update(self, event: Event):
        """Track current price and check if dark pool levels are being tested."""
        price = event.data.get("price", 0.0)
        if price <= 0:
            return

        prev_price = self.current_price
        self.current_price = price

        if prev_price <= 0:
            return

        # Check if price crossed any significant dark pool level
        self._check_sr_tests(prev_price, price)

    def _check_sr_tests(self, prev: float, curr: float):
        """Check if price movement tested any dark pool levels."""
        low = min(prev, curr)
        high = max(prev, curr)

        for level in self.levels.values():
            if level.total_notional < 1_000_000:
                continue

            # Level is "tested" if price crossed through it
            if low <= level.price <= high:
                level.sr_tests += 1

                # Level "held" if price reversed (simplified: next tick went
                # back across). We approximate by checking if the close is on
                # the same side as the open.
                if prev < level.price and curr < level.price + 0.5:
                    # Approached from below, didn't break through convincingly
                    level.sr_holds += 1
                elif prev > level.price and curr > level.price - 0.5:
                    # Approached from above, held as support
                    level.sr_holds += 1

                self.sr_history.append({
                    "timestamp": time.time(),
                    "level_price": level.price,
                    "test_from": prev,
                    "test_to": curr,
                    "held": level.sr_holds == level.sr_tests,
                    "level_notional": level.total_notional,
                })

        # Cap history
        if len(self.sr_history) > 1000:
            self.sr_history = self.sr_history[-1000:]

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def _relative_position(self, price: float) -> str:
        """Describe a price relative to current price."""
        if self.current_price <= 0:
            return "unknown"
        diff = price - self.current_price
        if abs(diff) < 0.10:
            return "at"
        direction = "above" if diff > 0 else "below"
        return f"${abs(diff):.2f} {direction}"

    def _prune_levels(self):
        """Remove stale levels if we have too many."""
        if len(self.levels) <= MAX_HEATMAP_LEVELS:
            return

        now = time.time()
        # Score each level: low volume + old = prunable
        scored = []
        for price, lvl in self.levels.items():
            age = now - lvl.last_print_time
            score = lvl.total_notional * (2.0 ** (-age / RECENCY_HALF_LIFE))
            scored.append((price, score))

        scored.sort(key=lambda x: x[1])

        # Remove the weakest levels
        remove_count = len(self.levels) - MAX_HEATMAP_LEVELS
        for price, _ in scored[:remove_count]:
            del self.levels[price]

    def get_stats(self) -> dict:
        """Return summary statistics for display."""
        now = time.time()
        recent_blocks = [
            p for p in self.recent_prints
            if p.is_block and (now - p.timestamp) < 3600
        ]
        total_notional = sum(lvl.total_notional for lvl in self.levels.values())

        # Find the heaviest level
        heaviest = max(self.levels.values(), key=lambda x: x.total_notional) \
            if self.levels else None

        return {
            "tracked_levels": len(self.levels),
            "total_prints": sum(lvl.print_count for lvl in self.levels.values()),
            "total_notional": total_notional,
            "blocks_last_hour": len(recent_blocks),
            "heaviest_level": heaviest.price if heaviest else None,
            "heaviest_notional": heaviest.total_notional if heaviest else 0,
            "sr_tests_tracked": len(self.sr_history),
        }
