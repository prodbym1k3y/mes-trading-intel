"""AMP Futures / Rithmic trade sync for MES Intel journal.

Two modes:
  Live (rapi installed):  Connect to Rithmic, pull fill history, auto-sync every N seconds.
  Offline (no rapi):      Parse AMP trade history CSV exports from the account portal.

Trade matching:
  Fills are matched into round-trip trades (entry + exit) using FIFO by default.
  Partial fills are averaged. Scaling in/out is supported.

After import: auto-grade each trade using the same rules as TradeJournal._grade_trade.

MES contract values:
  $5.00 per point  |  $1.25 per tick (0.25 points)
"""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

# Try importing rapi — same pattern as rithmic_feed.py
try:
    import rapi  # type: ignore[import]
    RITHMIC_AVAILABLE = True
except ImportError:
    RITHMIC_AVAILABLE = False

MES_POINT_VALUE = 5.0   # $ per point
MES_TICK_VALUE  = 1.25  # $ per tick
MES_FEE         = 0.62  # $ per contract per side (AMP typical)


# ─────────────────────────────────────────────────────────────────────────────
#  Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Fill:
    """A single fill / execution from Rithmic or CSV."""
    timestamp:  datetime
    symbol:     str
    side:       str          # 'BUY' or 'SELL'
    quantity:   int
    price:      float
    commission: float = 0.0
    order_id:   str   = ""
    order_type: str   = "LIMIT"


@dataclass
class RoundTrip:
    """A matched entry + exit pair, ready to insert into the trades table."""
    symbol:          str
    direction:       str    # 'LONG' or 'SHORT'
    entry_time:      str
    exit_time:       str
    entry_price:     float  # average entry (handles partial fills)
    exit_price:      float  # average exit
    quantity:        int
    pnl:             float
    fees:            float
    r_multiple:      Optional[float] = None
    hold_time_sec:   Optional[float] = None
    source:          str = "amp_import"
    notes:           str = ""

    def to_trade_dict(self) -> dict:
        return {
            "signal_id":      None,
            "entry_time":     self.entry_time,
            "exit_time":      self.exit_time,
            "direction":      self.direction,
            "quantity":       self.quantity,
            "entry_price":    self.entry_price,
            "exit_price":     self.exit_price,
            "pnl":            self.pnl,
            "fees":           self.fees,
            "stop_price":     None,
            "target_price":   None,
            "r_multiple":     self.r_multiple,
            "hold_time_sec":  self.hold_time_sec,
            "source":         self.source,
            "notes":          self.notes,
            "status":         "closed",
            "emotion":        "",
            "tags":           "amp_import",
            "ai_grade":       None,
            "ai_analysis_json": None,
            "screenshot_path": "",
            "mae":            None,
            "mfe":            None,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  CSV parsing — handles common AMP / Rithmic export formats
# ─────────────────────────────────────────────────────────────────────────────

# AMP standard CSV headers (case-insensitive matching)
_FIELD_MAP = {
    # timestamp fields
    "date":        "date",
    "time":        "time",
    "datetime":    "datetime",
    "timestamp":   "datetime",
    "fill time":   "datetime",
    "filled at":   "datetime",
    # symbol
    "symbol":      "symbol",
    "contract":    "symbol",
    "instrument":  "symbol",
    # side
    "side":        "side",
    "action":      "side",
    "buy/sell":    "side",
    "b/s":         "side",
    # quantity
    "qty":         "quantity",
    "quantity":    "quantity",
    "filled":      "quantity",
    "filled qty":  "quantity",
    # price
    "price":       "price",
    "fill price":  "price",
    "avg price":   "price",
    "executed at": "price",
    # commission
    "commission":  "commission",
    "comm":        "commission",
    "fees":        "commission",
    # order id
    "order id":    "order_id",
    "orderid":     "order_id",
    "ref":         "order_id",
    # order type
    "type":        "order_type",
    "order type":  "order_type",
}


def _normalise_headers(raw_headers: list[str]) -> dict[str, str]:
    """Map raw CSV headers to canonical field names."""
    result: dict[str, str] = {}
    for h in raw_headers:
        key = h.strip().lower()
        if key in _FIELD_MAP:
            result[h] = _FIELD_MAP[key]
    return result


def _parse_side(raw: str) -> str:
    """Normalise side value to 'BUY' or 'SELL'."""
    v = raw.strip().upper()
    if v in ("B", "BUY", "BOT", "BOUGHT"):
        return "BUY"
    if v in ("S", "SELL", "SLD", "SOLD"):
        return "SELL"
    return v


def _parse_dt(date_str: str, time_str: str = "") -> datetime:
    """Parse various date/time formats from AMP/Rithmic CSVs."""
    combined = f"{date_str} {time_str}".strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {combined!r}")


def parse_amp_csv(content: str) -> list[Fill]:
    """Parse AMP trade history CSV content into Fill objects.

    Handles several common export formats from AMP Futures' account portal
    and Rithmic's own CSV reports. Returns an empty list if the format
    cannot be recognised.
    """
    reader = csv.DictReader(io.StringIO(content.strip()))
    if not reader.fieldnames:
        log.warning("amp_sync: CSV has no headers")
        return []

    hmap = _normalise_headers(list(reader.fieldnames))
    if not hmap:
        log.warning("amp_sync: no recognised headers in CSV")
        return []

    def _get(row: dict, canonical: str, default: str = "") -> str:
        for raw, can in hmap.items():
            if can == canonical and raw in row:
                return row[raw].strip()
        return default

    fills: list[Fill] = []
    for row in reader:
        try:
            # Timestamp
            dt_raw  = _get(row, "datetime") or _get(row, "date")
            tm_raw  = _get(row, "time")
            if not dt_raw:
                continue
            dt = _parse_dt(dt_raw, tm_raw)

            # Symbol
            symbol = _get(row, "symbol", "MES").upper()
            if not symbol:
                symbol = "MES"

            # Side
            side_raw = _get(row, "side")
            if not side_raw:
                continue
            side = _parse_side(side_raw)
            if side not in ("BUY", "SELL"):
                log.debug("amp_sync: unknown side %r — skipping", side_raw)
                continue

            # Quantity
            qty_raw = _get(row, "quantity", "1")
            qty = max(1, int(float(qty_raw)))

            # Price
            price_raw = _get(row, "price")
            if not price_raw:
                continue
            price = float(price_raw)

            # Commission (optional)
            comm_raw = _get(row, "commission", "0")
            try:
                comm = float(comm_raw)
            except ValueError:
                comm = 0.0

            fills.append(Fill(
                timestamp=dt,
                symbol=symbol,
                side=side,
                quantity=qty,
                price=price,
                commission=comm,
                order_id=_get(row, "order_id"),
                order_type=_get(row, "order_type", "LIMIT"),
            ))
        except (ValueError, KeyError) as exc:
            log.debug("amp_sync: skipping row %r — %s", row, exc)

    log.info("amp_sync: parsed %d fills from CSV", len(fills))
    return fills


# ─────────────────────────────────────────────────────────────────────────────
#  Trade matching — FIFO / LIFO
# ─────────────────────────────────────────────────────────────────────────────

def match_fills(fills: list[Fill], method: str = "FIFO") -> list[RoundTrip]:
    """Match fills into round-trip trades.

    Supports:
      - FIFO: first-in, first-out (default)
      - LIFO: last-in, first-out
      - Partial fills (multiple fills averaged)
      - Scaling in/out (track open position qty separately)

    Returns a list of completed RoundTrip trades.
    """
    # Group by symbol
    by_symbol: dict[str, list[Fill]] = {}
    for f in sorted(fills, key=lambda x: x.timestamp):
        by_symbol.setdefault(f.symbol, []).append(f)

    trips: list[RoundTrip] = []

    for symbol, sym_fills in by_symbol.items():
        # Open position tracking: deque of (price, qty, timestamp, commission)
        open_longs:  deque = deque()   # BUY fills waiting to be matched
        open_shorts: deque = deque()   # SELL fills waiting to be matched

        for fill in sym_fills:
            if fill.side == "BUY":
                _process_fill(fill, open_longs, open_shorts, "SHORT",
                              trips, symbol, method)
            else:  # SELL
                _process_fill(fill, open_shorts, open_longs, "LONG",
                              trips, symbol, method)

    return trips


def _process_fill(
    fill: Fill,
    same_side_queue: deque,
    opposite_queue: deque,
    direction_when_closing: str,
    trips: list[RoundTrip],
    symbol: str,
    method: str,
) -> None:
    """Attempt to close open positions from opposite_queue, remainder goes to same_side_queue."""
    remaining_qty = fill.quantity

    while remaining_qty > 0 and opposite_queue:
        if method == "LIFO":
            open_entry = opposite_queue[-1]
        else:
            open_entry = opposite_queue[0]

        matched_qty = min(remaining_qty, open_entry["qty"])

        # Build round trip
        if direction_when_closing == "LONG":
            # We had open longs, now selling → closing long
            entry_price = open_entry["avg_price"]
            exit_price  = fill.price
            entry_time  = open_entry["first_time"]
        else:
            # We had open shorts, now buying → closing short
            entry_price = open_entry["avg_price"]
            exit_price  = fill.price
            entry_time  = open_entry["first_time"]

        exit_time = fill.timestamp

        # PnL
        if direction_when_closing == "LONG":
            points = exit_price - entry_price
        else:
            points = entry_price - exit_price

        fees = MES_FEE * matched_qty * 2  # round-turn (entry + exit)
        pnl  = points * MES_POINT_VALUE * matched_qty - fees

        hold_sec = (exit_time - entry_time).total_seconds()

        trips.append(RoundTrip(
            symbol=symbol,
            direction=direction_when_closing,
            entry_time=entry_time.isoformat(),
            exit_time=exit_time.isoformat(),
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=matched_qty,
            pnl=pnl,
            fees=fees,
            hold_time_sec=hold_sec,
        ))

        # Reduce queued entry
        remaining_qty -= matched_qty
        open_entry["qty"] -= matched_qty
        if open_entry["qty"] <= 0:
            if method == "LIFO":
                opposite_queue.pop()
            else:
                opposite_queue.popleft()

    # Any leftover qty is a new open position
    if remaining_qty > 0:
        same_side_queue.append({
            "avg_price":  fill.price,
            "qty":        remaining_qty,
            "first_time": fill.timestamp,
            "commission": fill.commission,
        })


def _merge_fills_into_entry(
    queue: deque,
    new_fill: Fill,
    method: str,
) -> None:
    """Add a fill to the queue, merging with existing entries at the same price level (scaling in)."""
    # Check if there's already an entry at this price (scale-in scenario)
    for entry in queue:
        if abs(entry["avg_price"] - new_fill.price) < 0.001:
            total_qty = entry["qty"] + new_fill.quantity
            entry["avg_price"] = (
                (entry["avg_price"] * entry["qty"] + new_fill.price * new_fill.quantity)
                / total_qty
            )
            entry["qty"] = total_qty
            return
    queue.append({
        "avg_price":  new_fill.price,
        "qty":        new_fill.quantity,
        "first_time": new_fill.timestamp,
        "commission": new_fill.commission,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Auto-grader for imported trades
# ─────────────────────────────────────────────────────────────────────────────

def auto_grade(trip: RoundTrip) -> dict:
    """Compute A-F grade for an imported trade without a signal.

    Returns a grade dict compatible with db.insert_grade() and
    updates trip.notes with a brief AI-style analysis.
    """
    pnl = trip.pnl

    # Entry timing: proxy via hold time vs P&L
    if pnl > 50:
        entry_timing = 9.0
    elif pnl > 20:
        entry_timing = 7.5
    elif pnl > 0:
        entry_timing = 6.0
    elif pnl > -10:
        entry_timing = 4.5
    else:
        entry_timing = max(2.0, 5.0 + pnl / 20.0)

    # Exit timing: did we capture a meaningful move?
    points = abs(trip.exit_price - trip.entry_price)
    if points >= 8:
        exit_timing = 9.0
    elif points >= 4:
        exit_timing = 7.5
    elif points >= 2:
        exit_timing = 6.0
    else:
        exit_timing = 4.0

    # Risk management: rough proxy
    risk_mgmt = 7.0 if pnl > 0 else (4.0 if pnl > -50 else 2.0)

    # No signal → setup quality / plan adherence lower
    setup_quality  = 5.0
    plan_adherence = 4.0

    overall = (
        setup_quality  * 0.25 +
        entry_timing   * 0.20 +
        exit_timing    * 0.20 +
        risk_mgmt      * 0.20 +
        plan_adherence * 0.15
    )

    # Letter grade
    if overall >= 9:
        letter = "A+"
    elif overall >= 8:
        letter = "A"
    elif overall >= 7:
        letter = "B"
    elif overall >= 6:
        letter = "C"
    elif overall >= 5:
        letter = "D"
    else:
        letter = "F"

    # AI-style analysis text
    if pnl > 50:
        well = "Strong execution — captured a large move with good size."
        improve = "Look for opportunities to trail the stop to protect gains."
    elif pnl > 0:
        well = "Profitable trade. Direction was correct."
        improve = "Aim to hold winning trades longer to maximise captured move."
    elif pnl > -25:
        well = "Small loss — acceptable if stop was honoured."
        improve = "Review entry timing; was there a higher-probability setup available?"
    else:
        well = "Trade ended with a significant loss."
        improve = "Review whether stop was respected and position size was appropriate."

    trip.notes = (
        f"[AMP Import] {letter} ({overall:.1f}/10) | "
        f"Well: {well} | Improve: {improve}"
    )

    return {
        "setup_quality":  setup_quality,
        "entry_timing":   entry_timing,
        "exit_timing":    exit_timing,
        "risk_management": risk_mgmt,
        "plan_adherence": plan_adherence,
        "overall_grade":  overall,
        "edge_ratio":     0.0,
        "notes":          letter,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Database insertion
# ─────────────────────────────────────────────────────────────────────────────

def store_trades(trips: list[RoundTrip], db, skip_duplicates: bool = True) -> int:
    """Insert matched round-trips into the trades table.

    Grades each trade and inserts a trade_grades record.
    Returns the number of new trades inserted.
    """
    if not trips:
        return 0

    inserted = 0

    # Load existing entry times to detect duplicates
    if skip_duplicates:
        try:
            existing = db.get_trades(limit=2000)
            existing_keys = {
                (t["entry_time"], t.get("direction"), t.get("entry_price"))
                for t in existing
            }
        except Exception:
            existing_keys = set()
    else:
        existing_keys = set()

    for trip in trips:
        key = (trip.entry_time, trip.direction, trip.entry_price)
        if skip_duplicates and key in existing_keys:
            log.debug("amp_sync: skipping duplicate trade @ %s %s %.2f",
                      trip.entry_time, trip.direction, trip.entry_price)
            continue

        grade_dict = auto_grade(trip)
        trade_dict = trip.to_trade_dict()
        trade_dict["ai_grade"] = grade_dict["notes"]  # letter grade stored here

        try:
            trade_id = db.insert_trade_enhanced(trade_dict)
        except AttributeError:
            # Fallback to basic insert
            basic_keys = [
                "signal_id", "entry_time", "exit_time", "direction", "quantity",
                "entry_price", "exit_price", "pnl", "fees", "stop_price",
                "target_price", "r_multiple", "hold_time_sec", "source", "notes", "status",
            ]
            trade_id = db.insert_trade({k: trade_dict[k] for k in basic_keys})

        grade_dict["trade_id"] = trade_id
        try:
            db.insert_grade(grade_dict)
        except Exception as exc:
            log.debug("amp_sync: could not insert grade for trade %d — %s", trade_id, exc)

        existing_keys.add(key)
        inserted += 1
        log.info("amp_sync: imported trade #%d  %s %s %.2f→%.2f  P&L=$%.2f",
                 trade_id, trip.direction, trip.symbol,
                 trip.entry_price, trip.exit_price, trip.pnl)

    log.info("amp_sync: inserted %d new trades", inserted)
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
#  CSV import entry-point
# ─────────────────────────────────────────────────────────────────────────────

def import_from_csv(
    file_path: str,
    db,
    match_method: str = "FIFO",
    on_progress: Optional[Callable[[str], None]] = None,
) -> int:
    """Parse an AMP/Rithmic CSV export and insert trades into the DB.

    Args:
        file_path:    Path to the CSV file.
        db:           Database instance.
        match_method: 'FIFO' or 'LIFO'.
        on_progress:  Optional callback receiving status strings.

    Returns:
        Number of trades inserted.
    """
    def _emit(msg: str):
        log.info("amp_sync: %s", msg)
        if on_progress:
            on_progress(msg)

    _emit(f"Reading {file_path} …")
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        _emit(f"ERROR: cannot read file — {exc}")
        return 0

    _emit("Parsing fills …")
    fills = parse_amp_csv(content)
    if not fills:
        _emit("No fills found — check CSV format")
        return 0
    _emit(f"Found {len(fills)} fills, matching round-trips ({match_method}) …")

    trips = match_fills(fills, method=match_method)
    _emit(f"Matched {len(trips)} round-trip trades")

    if not trips:
        _emit("No round-trip trades to import")
        return 0

    _emit("Grading and inserting …")
    inserted = store_trades(trips, db)
    _emit(f"Done — imported {inserted} new trades")
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
#  Rithmic live sync (requires rapi)
# ─────────────────────────────────────────────────────────────────────────────

class RithmicOrderHistoryFetcher:
    """Pulls trade history from Rithmic R|API when rapi is installed.

    Connects using the existing RithmicConfig, requests order history for
    the configured account, and hands fills to match_fills() + store_trades().

    NOTE: rapi order-history callbacks vary slightly by broker configuration.
    This implementation follows AMP Futures' rapi setup guide.
    """

    def __init__(self, config, db, on_progress=None):
        self._config  = config
        self._db      = db
        self._on_progress = on_progress
        self._fills: list[Fill] = []
        self._done  = threading.Event()
        self._session = None

    def _emit(self, msg: str):
        log.info("amp_sync(rapi): %s", msg)
        if self._on_progress:
            self._on_progress(msg)

    def fetch(self, days_back: int = 30) -> int:
        """Fetch and import the last N days of trades. Returns trades inserted."""
        if not RITHMIC_AVAILABLE:
            self._emit("rapi not installed — use CSV import instead")
            return 0

        rc = self._config.rithmic
        self._emit(f"Connecting to {rc.system} as {rc.user} …")

        try:
            engine = rapi.REngine(appName=rc.app_name, appVersion=rc.app_version)
            session = engine.createSession()
            self._session = session

            # Register callbacks
            session.setCallbacks(self)

            ok = session.login(
                server=rc.host,
                port=rc.port,
                user=rc.username,
                password=rc.password,
                systemName=rc.system_name,
                interface=rc.interface,
            )

            if not ok:
                self._emit("Login failed — check credentials")
                return 0

            self._emit("Authenticated. Requesting order history …")

            start_ts = int((time.time() - days_back * 86400) * 1000)
            end_ts   = int(time.time() * 1000)

            # Request fill/order history — rapi method names may vary by version
            session.requestOrderHistory(
                account=rc.account_id,
                startTime=start_ts,
                endTime=end_ts,
            )

            # Wait up to 30s for the callback to fire
            self._done.wait(timeout=30.0)

        except Exception as exc:
            self._emit(f"ERROR: {exc}")
            log.exception("amp_sync: rapi fetch error")
            return 0
        finally:
            try:
                if self._session:
                    self._session.logout()
            except Exception:
                pass

        self._emit(f"Received {len(self._fills)} fills, matching trades …")
        trips = match_fills(self._fills)
        inserted = store_trades(trips, self._db)
        self._emit(f"Imported {inserted} new trades from Rithmic")
        return inserted

    # ── rapi callbacks (called on rapi's thread) ──────────────────────────────

    def on_order_history_received(self, orders: list) -> None:
        """Called by rapi when order history arrives."""
        for order in orders:
            try:
                # Attribute names follow rapi's order object spec (AMP flavour)
                side = "BUY" if getattr(order, "side", "").upper() in ("B", "BUY", "BOT") else "SELL"
                self._fills.append(Fill(
                    timestamp=datetime.fromtimestamp(
                        getattr(order, "fillTimestamp", time.time()) / 1000.0
                    ),
                    symbol=getattr(order, "symbol", "MES"),
                    side=side,
                    quantity=int(getattr(order, "filledQuantity", 1)),
                    price=float(getattr(order, "avgFillPrice", 0.0)),
                    commission=float(getattr(order, "commission", MES_FEE)),
                    order_id=str(getattr(order, "orderId", "")),
                    order_type=str(getattr(order, "orderType", "LIMIT")),
                ))
            except Exception as exc:
                log.debug("amp_sync: skipping order — %s", exc)
        self._done.set()

    def on_fill_received(self, fill_obj) -> None:
        """Alternative callback name used by some rapi versions."""
        try:
            side_raw = getattr(fill_obj, "side", getattr(fill_obj, "buySell", ""))
            side = "BUY" if side_raw.upper() in ("B", "BUY", "BOT") else "SELL"
            self._fills.append(Fill(
                timestamp=datetime.fromtimestamp(
                    getattr(fill_obj, "timestamp", time.time())
                ),
                symbol=getattr(fill_obj, "symbol", "MES"),
                side=side,
                quantity=int(getattr(fill_obj, "qty", 1)),
                price=float(getattr(fill_obj, "price", 0.0)),
                commission=float(getattr(fill_obj, "commission", MES_FEE)),
                order_id=str(getattr(fill_obj, "orderId", "")),
            ))
        except Exception as exc:
            log.debug("amp_sync: fill_received error — %s", exc)


def rithmic_sync(config, db, days_back: int = 30, on_progress=None) -> int:
    """Convenience wrapper: fetch Rithmic history and import."""
    fetcher = RithmicOrderHistoryFetcher(config, db, on_progress=on_progress)
    return fetcher.fetch(days_back=days_back)


# ─────────────────────────────────────────────────────────────────────────────
#  Auto-sync manager
# ─────────────────────────────────────────────────────────────────────────────

class AutoSyncManager:
    """Runs rithmic_sync() on a fixed interval in a background thread."""

    def __init__(self, config, db, interval_sec: int = 30, on_progress=None):
        self._config       = config
        self._db           = db
        self._interval     = interval_sec
        self._on_progress  = on_progress
        self._running      = False
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        if self._running:
            return
        if not RITHMIC_AVAILABLE:
            log.warning("amp_sync: AutoSync requires rapi — not available")
            if self._on_progress:
                self._on_progress("Auto-Sync requires Rithmic R|API (rapi not installed)")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="amp-autosync", daemon=True
        )
        self._thread.start()
        log.info("amp_sync: AutoSyncManager started (interval=%ds)", self._interval)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        log.info("amp_sync: AutoSyncManager stopped")

    def _loop(self):
        while self._running:
            try:
                rithmic_sync(self._config, self._db, days_back=1,
                             on_progress=self._on_progress)
            except Exception:
                log.exception("amp_sync: auto-sync error")
            # Sleep in small chunks so stop() is responsive
            for _ in range(self._interval * 10):
                if not self._running:
                    break
                time.sleep(0.1)
