"""
ATAS Bridge — File-based integration with ATAS order flow platform.
Watches ATAS export directory for CSV cluster data exports.
Parses and republishes as MES Intel events.

ATAS and this system share the same Rithmic connection through AMP Futures.
ATAS handles charting and footprint computation, then exports data as CSV
files that this bridge monitors and ingests into the event bus.

Supported CSV formats (auto-detected from headers):
  - cluster_*.csv:   DateTime, Price, BidVol, AskVol, Delta, Volume, POC, VAH, VAL
  - footprint_*.csv: DateTime, Price, BidVol, AskVol, Delta, Volume (bar-level)
  - profile_*.csv:   Price, BidVol, AskVol, Volume [, Delta]

Usage:
    bridge = ATASBridge(config, event_bus)
    bridge.start()
    # parsed data flows through event bus automatically
    bridge.stop()
"""
from __future__ import annotations

import csv
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config import ATASConfig
from ..event_bus import EventBus, Event, EventType
from ..orderflow import VolumeProfile, PriceLevel

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ATASClusterData dataclass
# ---------------------------------------------------------------------------

@dataclass
class ATASClusterData:
    """One price-level row from an ATAS cluster/footprint CSV export."""
    timestamp: datetime
    price: float
    bid_volume: int
    ask_volume: int
    delta: int                  # ask_volume - bid_volume
    total_volume: int
    is_poc: bool = False        # Point of Control flag
    is_vah: bool = False        # Value Area High flag
    is_val: bool = False        # Value Area Low flag
    imbalance_ratio: float = 0.0  # >3.0 when one side dominates 3:1
    notes: str = ""             # any ATAS annotation text from the row


# ---------------------------------------------------------------------------
# Timestamp parsing helpers
# ---------------------------------------------------------------------------

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S.%f",
    "%Y.%m.%d %H:%M:%S",
    "%d.%m.%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
)


def _parse_datetime(s: str) -> datetime:
    """Parse an ATAS datetime string in any known format."""
    s = s.strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    log.warning("Could not parse ATAS datetime: %r — using now()", s)
    return datetime.now()


def _norm(h: str) -> str:
    """Normalize a CSV header: lowercase, strip, remove spaces and underscores."""
    return h.strip().lower().replace(" ", "").replace("_", "")


def _int_or(val: str, default: int = 0) -> int:
    try:
        return int(float(val.strip())) if val.strip() else default
    except (ValueError, AttributeError):
        return default


def _float_or(val: str, default: float = 0.0) -> float:
    try:
        return float(val.strip()) if val.strip() else default
    except (ValueError, AttributeError):
        return default


def _bool_cell(val: str) -> bool:
    """Interpret ATAS boolean cells: 'true', '1', 'yes', 'x', 'poc', etc."""
    return val.strip().lower() in ("1", "true", "yes", "x", "poc", "vah", "val")


# ---------------------------------------------------------------------------
# ATASExportParser
# ---------------------------------------------------------------------------

class ATASExportParser:
    """Parses ATAS CSV export files into structured Python objects.

    All parse methods are static and return lists or VolumeProfile instances.
    The parser is robust: it skips malformed rows, tolerates varying column
    order, and handles multiple timestamp formats.
    """

    def parse_cluster_csv(self, filepath: str) -> List[ATASClusterData]:
        """Parse a cluster/footprint CSV export into ATASClusterData rows.

        Expected columns: DateTime, Price, BidVol(ume), AskVol(ume),
        Delta, Volume, POC, VAH, VAL [, Notes]
        """
        rows: List[ATASClusterData] = []
        path = Path(filepath)
        if not path.exists():
            log.error("Cluster CSV not found: %s", filepath)
            return rows

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    return rows

                col = {_norm(h): h for h in reader.fieldnames}

                for raw in reader:
                    try:
                        dt_key = col.get("datetime") or col.get("time") or col.get("date")
                        if not dt_key:
                            continue
                        ts = _parse_datetime(raw[dt_key])
                        price = _float_or(raw.get(col.get("price", ""), ""))
                        bid_vol = _int_or(raw.get(col.get("bidvolume") or
                                                  col.get("bidvol", ""), ""))
                        ask_vol = _int_or(raw.get(col.get("askvolume") or
                                                  col.get("askvol", ""), ""))
                        delta = _int_or(raw.get(col.get("delta", ""), ""),
                                        ask_vol - bid_vol)
                        total = _int_or(raw.get(col.get("volume", ""), ""),
                                        bid_vol + ask_vol)

                        is_poc = _bool_cell(raw.get(col.get("poc", ""), ""))
                        is_vah = _bool_cell(raw.get(col.get("vah", ""), ""))
                        is_val = _bool_cell(raw.get(col.get("val", ""), ""))
                        notes = raw.get(col.get("notes") or
                                        col.get("annotations", ""), "").strip()

                        # Imbalance: flag when one side is >3× the other
                        imbalance = 0.0
                        if bid_vol > 0 and ask_vol > 0:
                            imbalance = max(ask_vol / bid_vol,
                                           bid_vol / ask_vol)
                        elif bid_vol == 0 and ask_vol > 0:
                            imbalance = float(ask_vol)
                        elif ask_vol == 0 and bid_vol > 0:
                            imbalance = float(bid_vol)

                        rows.append(ATASClusterData(
                            timestamp=ts,
                            price=price,
                            bid_volume=bid_vol,
                            ask_volume=ask_vol,
                            delta=delta,
                            total_volume=total,
                            is_poc=is_poc,
                            is_vah=is_vah,
                            is_val=is_val,
                            imbalance_ratio=round(imbalance, 2),
                            notes=notes,
                        ))
                    except Exception as exc:
                        log.debug("Skipping cluster row: %s — %s", raw, exc)
        except OSError as exc:
            log.error("Cannot read cluster CSV %s: %s", filepath, exc)

        return rows

    def parse_footprint_csv(self, filepath: str) -> List[dict]:
        """Parse a footprint CSV into a list of bar dicts.

        Each dict contains: start_time, open, high, low, close,
        volume, delta, levels (list of price-level dicts).
        """
        bars: dict = {}
        path = Path(filepath)
        if not path.exists():
            log.error("Footprint CSV not found: %s", filepath)
            return []

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    return []
                col = {_norm(h): h for h in reader.fieldnames}

                for raw in reader:
                    try:
                        dt_key = col.get("datetime") or col.get("time")
                        if not dt_key:
                            continue
                        ts = _parse_datetime(raw[dt_key])
                        bar_key = ts.strftime("%Y-%m-%d %H:%M")  # 1-min buckets

                        price = _float_or(raw.get(col.get("price", ""), ""))
                        bid_v = _int_or(raw.get(col.get("bidvolume") or
                                                col.get("bidvol", ""), ""))
                        ask_v = _int_or(raw.get(col.get("askvolume") or
                                                col.get("askvol", ""), ""))
                        total = _int_or(raw.get(col.get("volume", ""), ""),
                                        bid_v + ask_v)
                        delta = ask_v - bid_v

                        if bar_key not in bars:
                            bars[bar_key] = {
                                "start_time": ts,
                                "open": price, "high": price,
                                "low": price, "close": price,
                                "volume": 0, "delta": 0,
                                "levels": [],
                            }
                        b = bars[bar_key]
                        b["high"] = max(b["high"], price)
                        b["low"] = min(b["low"], price)
                        b["close"] = price
                        b["volume"] += total
                        b["delta"] += delta
                        b["levels"].append({
                            "price": price,
                            "bid_vol": bid_v,
                            "ask_vol": ask_v,
                            "delta": delta,
                        })
                    except Exception as exc:
                        log.debug("Skipping footprint row: %s — %s", raw, exc)
        except OSError as exc:
            log.error("Cannot read footprint CSV %s: %s", filepath, exc)

        return sorted(bars.values(), key=lambda b: b["start_time"])

    def parse_volume_profile_csv(self, filepath: str) -> VolumeProfile:
        """Parse a volume profile snapshot CSV into a VolumeProfile object.

        Expected columns: Price, BidVol(ume), AskVol(ume), Volume [, Delta]
        """
        profile = VolumeProfile()
        path = Path(filepath)
        if not path.exists():
            log.error("Volume profile CSV not found: %s", filepath)
            return profile

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    return profile
                col = {_norm(h): h for h in reader.fieldnames}

                for raw in reader:
                    try:
                        price = _float_or(raw.get(col.get("price", ""), ""))
                        if price == 0.0:
                            continue
                        bid_v = _int_or(raw.get(col.get("bidvolume") or
                                                col.get("bidvol", ""), ""))
                        ask_v = _int_or(raw.get(col.get("askvolume") or
                                                col.get("askvol", ""), ""))

                        price_key = profile._round_price(price)
                        if price_key not in profile.levels:
                            profile.levels[price_key] = PriceLevel(price=price_key)
                        lv = profile.levels[price_key]
                        lv.bid_volume += bid_v
                        lv.ask_volume += ask_v
                        profile.cumulative_delta += (ask_v - bid_v)
                    except Exception as exc:
                        log.debug("Skipping VP row: %s — %s", raw, exc)
        except OSError as exc:
            log.error("Cannot read VP CSV %s: %s", filepath, exc)

        return profile


# ---------------------------------------------------------------------------
# ATASFileWatcher — directory watcher with watchdog or polling fallback
# ---------------------------------------------------------------------------

try:
    from watchdog.observers import Observer           # type: ignore[import]
    from watchdog.events import FileSystemEventHandler  # type: ignore[import]
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
    log.info("watchdog not installed — ATASFileWatcher will use polling")


class ATASFileWatcher:
    """Watches an ATAS CSV export directory for new or modified files.

    Uses the watchdog library when available; falls back to polling at
    config.watch_interval seconds otherwise.

    File type is inferred from the filename pattern:
      cluster_*.csv    → parse_cluster_csv
      footprint_*.csv  → parse_footprint_csv
      profile_*.csv    → parse_volume_profile_csv

    Deduplication: files are tracked by MD5 hash to avoid reprocessing
    identical content (e.g. if a file is touched but unchanged).
    """

    def __init__(self, config: ATASConfig, event_bus: EventBus) -> None:
        self._config = config
        self._bus = event_bus
        self._parser = ATASExportParser()
        self._running = False
        self._processed_hashes: Dict[str, str] = {}   # filepath -> md5
        self._observer = None
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_mtimes: Dict[str, float] = {}

    # -- File type detection --------------------------------------------------

    def _file_type(self, name: str) -> Optional[str]:
        n = name.lower()
        if n.startswith("cluster_") and n.endswith(".csv"):
            return "cluster"
        if n.startswith("footprint_") and n.endswith(".csv"):
            return "footprint"
        if n.startswith("profile_") and n.endswith(".csv"):
            return "profile"
        return None

    # -- Deduplication --------------------------------------------------------

    def _file_hash(self, path: Path) -> str:
        try:
            return hashlib.md5(path.read_bytes()).hexdigest()
        except OSError:
            return ""

    def _already_processed(self, path: Path) -> bool:
        h = self._file_hash(path)
        if not h:
            return True
        key = str(path)
        if self._processed_hashes.get(key) == h:
            return True
        self._processed_hashes[key] = h
        return False

    # -- Dispatch -------------------------------------------------------------

    def _dispatch(self, path: Path) -> None:
        if self._already_processed(path):
            return
        ftype = self._file_type(path.name)
        if ftype is None:
            return

        log.info("ATAS file detected: %s (%s)", path.name, ftype)

        if ftype == "cluster":
            rows = self._parser.parse_cluster_csv(str(path))
            self._bus.publish(Event(
                type=EventType.ATAS_DATA_LOADED,
                source="atas_file_watcher",
                data={"type": "cluster", "file": path.name, "rows": len(rows),
                      "data": rows},
            ))

        elif ftype == "footprint":
            bars = self._parser.parse_footprint_csv(str(path))
            for bar in bars:
                self._bus.publish(Event(
                    type=EventType.FOOTPRINT_UPDATE,
                    source="atas_file_watcher",
                    data=bar,
                ))
            self._bus.publish(Event(
                type=EventType.ATAS_DATA_LOADED,
                source="atas_file_watcher",
                data={"type": "footprint", "file": path.name, "bars": len(bars)},
            ))

        elif ftype == "profile":
            profile = self._parser.parse_volume_profile_csv(str(path))
            self._bus.publish(Event(
                type=EventType.VOLUME_PROFILE_UPDATE,
                source="atas_file_watcher",
                data={
                    "type": "profile",
                    "file": path.name,
                    "profile": profile,
                    "poc": profile.poc,
                    "val": profile.val,
                    "vah": profile.vah,
                    "total_volume": profile.total_volume,
                },
            ))

    # -- Watchdog integration -------------------------------------------------

    def _build_watchdog_handler(self):
        parent = self

        class _Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    parent._dispatch(Path(event.src_path))

            def on_modified(self, event):
                if not event.is_directory:
                    parent._dispatch(Path(event.src_path))

        return _Handler()

    # -- Polling fallback -----------------------------------------------------

    def _poll_loop(self) -> None:
        export_dir = Path(self._config.csv_export_dir)
        interval = self._config.poll_interval_ms / 1000.0

        while self._running:
            try:
                if export_dir.is_dir():
                    for p in export_dir.iterdir():
                        if not p.is_file():
                            continue
                        mtime = p.stat().st_mtime
                        prev = self._poll_mtimes.get(str(p))
                        if prev is None or mtime > prev:
                            self._poll_mtimes[str(p)] = mtime
                            if prev is not None:  # skip initial scan
                                self._dispatch(p)
            except Exception:
                log.exception("Poll loop error")
            time.sleep(interval)

    # -- start / stop ---------------------------------------------------------

    def start(self) -> None:
        export_dir = Path(self._config.csv_export_dir)
        if not export_dir.is_dir():
            log.warning("ATAS export dir does not exist: %s", export_dir)
        self._running = True

        if _WATCHDOG_AVAILABLE:
            self._observer = Observer()
            self._observer.schedule(
                self._build_watchdog_handler(),
                str(export_dir),
                recursive=False,
            )
            self._observer.start()
            log.info("ATASFileWatcher started (watchdog) on %s", export_dir)
        else:
            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                name="atas-poll-watcher",
                daemon=True,
            )
            self._poll_thread.start()
            log.info("ATASFileWatcher started (polling, interval=%.1fs) on %s",
                     self._config.poll_interval_ms / 1000.0, export_dir)

    def stop(self) -> None:
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=3.0)
            self._observer = None
        if self._poll_thread:
            self._poll_thread.join(timeout=3.0)
            self._poll_thread = None
        log.info("ATASFileWatcher stopped")


# ---------------------------------------------------------------------------
# ATASBridge — main integration class
# ---------------------------------------------------------------------------

class ATASBridge:
    """Composes ATASFileWatcher + ATASExportParser for complete ATAS integration.

    Provides:
      - File watching (new cluster/footprint/profile CSVs → event bus)
      - Manual CSV loading
      - Latest VolumeProfile cache
      - Cluster data history query
      - Rithmic shared_config for ATAS configuration consistency

    Parameters
    ----------
    config : ATASConfig
        ATAS export directory, watch interval, and symbol list.
    event_bus : EventBus
        Bus to publish parsed data events onto.
    """

    def __init__(self, config: ATASConfig, event_bus: EventBus) -> None:
        self._config = config
        self._bus = event_bus
        self._parser = ATASExportParser()
        self._watcher = ATASFileWatcher(config, event_bus)
        self._running = False

        self._latest_profile: Optional[VolumeProfile] = None
        self._cluster_history: List[ATASClusterData] = []
        self._max_cluster_history = 5000

        # Subscribe to our own events to cache data locally
        event_bus.subscribe(EventType.VOLUME_PROFILE_UPDATE,
                            self._on_profile_update)
        event_bus.subscribe(EventType.ATAS_DATA_LOADED,
                            self._on_cluster_loaded)

    # -- Cache handlers -------------------------------------------------------

    def _on_profile_update(self, event: Event) -> None:
        profile = event.data.get("profile")
        if isinstance(profile, VolumeProfile):
            self._latest_profile = profile

    def _on_cluster_loaded(self, event: Event) -> None:
        if event.data.get("type") == "cluster":
            rows = event.data.get("data", [])
            if isinstance(rows, list):
                self._cluster_history.extend(rows)
                # Keep only the most recent entries
                if len(self._cluster_history) > self._max_cluster_history:
                    self._cluster_history = \
                        self._cluster_history[-self._max_cluster_history:]

    # -- Public API -----------------------------------------------------------

    @property
    def shared_config(self) -> Dict:
        """Rithmic login info mirrored to ATAS configuration.

        ATAS uses the same AMP/Rithmic account. This dict provides the
        relevant fields so the UI settings panel can display them.
        """
        return {
            "symbols": ["MES"],
            "export_dir": self._config.csv_export_dir,
            "watch_interval_sec": self._config.poll_interval_ms / 1000.0,
            "platform": "ATAS",
            "data_source": "Rithmic (shared via AMP Futures)",
        }

    def get_latest_profile(self) -> Optional[VolumeProfile]:
        """Return the most recently parsed VolumeProfile, or None."""
        return self._latest_profile

    def get_cluster_data(self, since: datetime) -> List[ATASClusterData]:
        """Return cluster data rows timestamped after `since`."""
        return [r for r in self._cluster_history if r.timestamp >= since]

    def load_csv_manual(self, filepath: str) -> dict:
        """Manually load and parse any ATAS CSV, publishing events.

        Returns a summary dict with format, row/bar counts, and key levels.
        """
        path = Path(filepath)
        if not path.exists():
            return {"error": f"File not found: {filepath}"}

        ftype = self._watcher._file_type(path.name)
        if ftype is None:
            # Try to infer from content
            ftype = self._infer_type_from_headers(path)

        if ftype == "cluster":
            rows = self._parser.parse_cluster_csv(filepath)
            self._cluster_history.extend(rows)
            return {"format": "cluster", "rows": len(rows)}

        elif ftype == "footprint":
            bars = self._parser.parse_footprint_csv(filepath)
            return {"format": "footprint", "bars": len(bars)}

        elif ftype == "profile":
            profile = self._parser.parse_volume_profile_csv(filepath)
            self._latest_profile = profile
            self._bus.publish(Event(
                type=EventType.VOLUME_PROFILE_UPDATE,
                source="atas_bridge",
                data={"profile": profile, "poc": profile.poc,
                      "val": profile.val, "vah": profile.vah,
                      "total_volume": profile.total_volume},
            ))
            return {"format": "profile", "poc": profile.poc,
                    "val": profile.val, "vah": profile.vah,
                    "levels": len(profile.levels)}

        return {"error": "Unknown file type", "file": filepath}

    def _infer_type_from_headers(self, path: Path) -> Optional[str]:
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                headers = next(csv.reader(f), None) or []
            norm = {_norm(h) for h in headers}
            if {"datetime", "bidvolume", "askvol"} & norm:
                return "cluster"
            if {"price", "bidvol", "askvol", "volume"} <= norm:
                return "profile"
        except OSError:
            pass
        return None

    def start(self) -> None:
        """Start the ATAS file watcher."""
        self._running = True
        self._watcher.start()
        self._bus.publish(Event(
            type=EventType.AGENT_STARTED,
            source="atas_bridge",
            data={"export_dir": self._config.csv_export_dir,
                  "symbols": ["MES"],
                  "watchdog": _WATCHDOG_AVAILABLE},
        ))
        log.info("ATASBridge started (dir=%s, watchdog=%s)",
                 self._config.csv_export_dir, _WATCHDOG_AVAILABLE)

    def stop(self) -> None:
        """Stop the ATAS file watcher."""
        self._running = False
        self._watcher.stop()
        self._bus.publish(Event(
            type=EventType.AGENT_STOPPED,
            source="atas_bridge",
            data={"cluster_rows_cached": len(self._cluster_history)},
        ))
        log.info("ATASBridge stopped (%d cluster rows cached)",
                 len(self._cluster_history))

    def status(self) -> dict:
        """Return bridge status for UI diagnostics."""
        return {
            "running": self._running,
            "export_dir": self._config.csv_export_dir,
            "symbols": ["MES"],
            "watchdog_available": _WATCHDOG_AVAILABLE,
            "latest_profile_poc": self._latest_profile.poc
                if self._latest_profile else None,
            "cluster_rows_cached": len(self._cluster_history),
        }

    def __repr__(self) -> str:
        return (f"ATASBridge(dir={self._config.csv_export_dir!r}, "
                f"cached_rows={len(self._cluster_history)})")
