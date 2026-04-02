"""Chart Monitor Agent — monitors price action, order flow, volume delta.

Bridges Rithmic data feed and ATAS CSV exports into the order flow
data structures. Publishes volume profile, footprint, and price
updates to the event bus.
"""
from __future__ import annotations

import csv
import logging
import time
from pathlib import Path
from typing import Optional

from ..config import AppConfig
from ..database import Database
from ..event_bus import EventBus, Event, EventType
from ..orderflow import VolumeProfile, FootprintChart, Tick

log = logging.getLogger(__name__)


class ChartMonitor:
    """Chart monitor agent — maintains live order flow state."""

    def __init__(self, config: AppConfig, db: Database, bus: EventBus):
        self.config = config
        self.db = db
        self.bus = bus

        self.session_profile = VolumeProfile(tick_size=0.25)
        self.footprint = FootprintChart(bar_duration_sec=60.0, tick_size=0.25)

        self._last_price: Optional[float] = None
        self._session_open: Optional[float] = None
        self._tick_count = 0

        # Historical pattern matching from Market Brain
        self._current_regime: str = "unknown"
        self._quant_state: dict = {}
        self._historical_matches: list[dict] = []
        self._abnormal_session: bool = False

        self.bus.subscribe(EventType.LESSON_LEARNED, self._on_lesson_learned)
        self.bus.subscribe(EventType.TRADE_RESULT, self._on_trade_result)
        self.bus.subscribe(EventType.MARKET_REGIME_CHANGE, self._on_regime_change)
        self.bus.subscribe(EventType.QUANT_SIGNAL, self._on_quant_signal)
        self.bus.subscribe(EventType.HISTORICAL_PATTERN_MATCH, self._on_pattern_match)

        log.info("Chart Monitor initialized")

    def process_tick(self, price: float, size: int, is_buy: bool, timestamp: Optional[float] = None):
        """Process a single trade tick from Rithmic."""
        ts = timestamp or time.time()
        tick = Tick(timestamp=ts, price=price, size=size,
                    aggressor="ASK" if is_buy else "BID")

        self.session_profile.add_tick(tick)
        self.footprint.add_tick(tick)
        self._tick_count += 1

        if self._session_open is None:
            self._session_open = price

        self._last_price = price

        # Publish price update every tick
        self.bus.publish(Event(
            type=EventType.PRICE_UPDATE,
            source="chart_monitor",
            data={
                "price": price,
                "change": price - self._session_open if self._session_open else 0,
                "change_pct": ((price / self._session_open) - 1) * 100 if self._session_open else 0,
                "size": size,
                "is_buy": is_buy,
            },
        ))

        # Publish volume profile periodically (every 100 ticks)
        if self._tick_count % 100 == 0:
            poc = getattr(self.session_profile, 'poc', None) or 0.0
            vah = getattr(self.session_profile, 'vah', None) or 0.0
            val = getattr(self.session_profile, 'val', None) or 0.0
            self.bus.publish(Event(
                type=EventType.VOLUME_PROFILE_UPDATE,
                source="chart_monitor",
                data={
                    "profile": self.session_profile,
                    "poc_price": poc,
                    "vah_price": vah,
                    "val_price": val,
                },
            ))

        # Publish footprint when a bar completes
        current_bar = self.footprint.current_bar
        if current_bar and current_bar.is_complete:
            self.bus.publish(Event(
                type=EventType.FOOTPRINT_UPDATE,
                source="chart_monitor",
                data={"bars": self.footprint.recent_bars(30)},
            ))

    def load_atas_csv(self, csv_path: str):
        """Import order flow cluster data from ATAS CSV export."""
        path = Path(csv_path)
        if not path.exists():
            log.warning("ATAS CSV not found: %s", csv_path)
            return 0

        count = 0
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    price = float(row.get("Price", 0))
                    bid_vol = int(row.get("BidVolume", 0))
                    ask_vol = int(row.get("AskVolume", 0))

                    if bid_vol > 0:
                        self.session_profile.add_trade(price, bid_vol, is_buy=False)
                    if ask_vol > 0:
                        self.session_profile.add_trade(price, ask_vol, is_buy=True)
                    count += 1
                except (ValueError, KeyError):
                    continue

        log.info("Loaded %d levels from ATAS CSV", count)
        return count

    def set_cross_asset_feed(self, feed) -> None:
        """Attach a CrossAssetFeed so strategies get cross-asset + options data."""
        self._cross_asset_feed = feed

    def get_market_data(self) -> dict:
        """Assemble current market data dict for strategy evaluation."""
        profile = self.session_profile
        bars = self.footprint.recent_bars(100)

        prices = [b.close for b in bars if b.close is not None]
        highs = [b.high for b in bars if b.high is not None]
        lows = [b.low for b in bars if b.low is not None]
        volumes = [b.volume for b in bars]

        data = {
            "prices": prices,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
            "volume_profile": profile,
            "footprint_bars": bars,
            "poc": profile.poc,
            "val": profile.val,
            "vah": profile.vah,
            "cumulative_delta": profile.cumulative_delta,
            "recent_deltas": [b.delta for b in bars[-20:]],
            "vwap": profile.poc,  # approximation
        }

        # Attach cross-asset + options data if feed is available
        feed = getattr(self, '_cross_asset_feed', None)
        if feed is not None:
            try:
                ca = feed.get_latest()
                data['cross_asset'] = ca
                data['options_data'] = ca.get('gex', {})
            except Exception:
                pass

        # Attach Market Brain quant state
        if self._quant_state:
            data['quant'] = self._quant_state
            data['regime'] = self._current_regime

        # Flag abnormal sessions for downstream strategies
        data['abnormal_session'] = self._abnormal_session
        data['historical_matches'] = self._historical_matches[-5:]

        return data

    def _on_lesson_learned(self, event: Event):
        """Receive cross-agent lessons about patterns to watch for."""
        data = event.data
        target = data.get("target_agent", "")
        if target not in ("chart_monitor", "all"):
            return
        lesson_type = data.get("lesson_type", "")
        description = data.get("description", "")
        impact = data.get("impact_score", 0.0)
        try:
            self.db.upsert_agent_knowledge(
                agent_name="chart_monitor",
                knowledge_type=f"lesson:{lesson_type}",
                key=f"ts_{int(event.timestamp)}",
                value={"description": description, "impact": impact},
                confidence=min(1.0, abs(impact)),
            )
        except Exception:
            pass

    def _on_trade_result(self, event: Event):
        """Learn from trade outcomes — record order flow context patterns."""
        outcome = event.data.get("outcome", "")
        pnl = event.data.get("pnl", 0)
        regime = event.data.get("regime", "")
        if not outcome:
            return
        try:
            self.db.upsert_agent_knowledge(
                agent_name="chart_monitor",
                knowledge_type="trade_outcome",
                key=f"{regime}_{outcome}_{int(time.time())}",
                value={
                    "outcome": outcome,
                    "pnl": pnl,
                    "regime": regime,
                    "price": self._last_price,
                    "cum_delta": self.session_profile.cumulative_delta,
                },
                confidence=min(1.0, abs(pnl) / 20.0),
            )
        except Exception:
            pass

    def _on_regime_change(self, event: Event):
        self._current_regime = event.data.get("to_regime", "unknown")

    def _on_quant_signal(self, event: Event):
        """Store latest quant state; detect abnormal session behavior."""
        self._quant_state = event.data
        regime = event.data.get("regime", "unknown")
        if regime:
            self._current_regime = regime
        # Abnormal if volume z-score > 2.5 or sweep detected
        vol_z   = event.data.get("volume_z", 0.0)
        sweep   = event.data.get("sweep_detected", False)
        self._abnormal_session = vol_z > 2.5 or sweep

    def _on_pattern_match(self, event: Event):
        """Store historical pattern matches for signal context."""
        match = {
            "pattern_type": event.data.get("pattern_type", ""),
            "outcome":       event.data.get("outcome", ""),
            "confidence":    event.data.get("confidence", 0.0),
            "timestamp":     event.timestamp,
        }
        self._historical_matches.append(match)
        if len(self._historical_matches) > 20:
            self._historical_matches = self._historical_matches[-20:]

    def reset_session(self):
        """Reset for new session."""
        self.session_profile.reset()
        self.footprint = FootprintChart(bar_duration_sec=60.0, tick_size=0.25)
        self._session_open = None
        self._tick_count = 0
        self._historical_matches.clear()
        log.info("Session reset")
