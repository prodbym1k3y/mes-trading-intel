"""
AMP Futures trade data importer.

Supports:
1. AMP daily statement CSV export
2. Rithmic CSV export (via R|Trader)
3. NinjaTrader trade export
4. Generic CSV with configurable columns

AMP/Rithmic CSV typically has columns like:
  Account, Symbol, Side, Qty, Price, Time, Commission, etc.
"""
import csv
import io
from datetime import datetime, date


# Common futures contract multipliers / tick values
FUTURES_TICK_VALUES = {
    'ES':  12.50,   # E-mini S&P 500
    'MES': 1.25,    # Micro E-mini S&P 500
    'NQ':  5.00,    # E-mini Nasdaq 100
    'MNQ': 0.50,    # Micro E-mini Nasdaq
    'YM':  5.00,    # E-mini Dow
    'MYM': 0.50,    # Micro Dow
    'RTY': 5.00,    # E-mini Russell
    'M2K': 0.50,    # Micro Russell
    'CL':  10.00,   # Crude Oil
    'MCL': 1.00,    # Micro Crude Oil
    'GC':  10.00,   # Gold
    'MGC': 1.00,    # Micro Gold
    'SI':  25.00,   # Silver
    'ZB':  31.25,   # 30-Year Bond
    'ZN':  15.625,  # 10-Year Note
    'ZF':  7.8125,  # 5-Year Note
    '6E':  12.50,   # Euro FX
    '6J':  12.50,   # Japanese Yen
}

# Point values (dollars per full point move)
FUTURES_POINT_VALUES = {
    'ES':  50.00,
    'MES': 5.00,
    'NQ':  20.00,
    'MNQ': 2.00,
    'YM':  5.00,
    'MYM': 0.50,
    'RTY': 50.00,
    'M2K': 5.00,
    'CL':  1000.00,
    'MCL': 100.00,
    'GC':  100.00,
    'MGC': 10.00,
    'SI':  5000.00,
    'ZB':  1000.00,
    'ZN':  1000.00,
    'ZF':  1000.00,
    '6E':  125000.00,
    '6J':  12500000.00,
}


def normalize_symbol(raw_symbol):
    """Extract base symbol from futures contract string like 'ESH6' or 'ES 03-26'."""
    raw = raw_symbol.strip().upper()
    # Remove exchange prefix like "CME:" or "NYMEX:"
    if ':' in raw:
        raw = raw.split(':')[1]
    # Try matching known symbols from longest to shortest
    for sym in sorted(FUTURES_TICK_VALUES.keys(), key=len, reverse=True):
        if raw.startswith(sym):
            return sym
    # Fallback: take letters before first digit
    base = ''
    for ch in raw:
        if ch.isalpha():
            base += ch
        else:
            break
    return base if base else raw


def get_tick_value(symbol):
    base = normalize_symbol(symbol)
    return FUTURES_TICK_VALUES.get(base, 12.50)


def get_point_value(symbol):
    base = normalize_symbol(symbol)
    return FUTURES_POINT_VALUES.get(base, 50.0)


def calculate_pnl(side, entry_price, exit_price, quantity, symbol):
    """Calculate P&L for a futures trade."""
    point_value = get_point_value(symbol)
    if side.upper() == 'BUY':
        return (exit_price - entry_price) * quantity * point_value
    else:
        return (entry_price - exit_price) * quantity * point_value


def parse_amp_csv(file_content):
    """
    Parse AMP/Rithmic trade CSV export.
    Returns list of trade dicts ready for DB insertion.

    Supports:
    - AMP Futures portal CSV export
    - Rithmic R|Trader CSV export
    - Comma and semicolon delimiters
    - Various column naming conventions
    """
    # Strip BOM if present
    content = file_content.lstrip('\ufeff').strip()
    if not content:
        return []

    # Detect delimiter: semicolon vs comma
    first_line = content.split('\n')[0]
    delimiter = ';' if first_line.count(';') > first_line.count(',') else ','

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

    # Normalize column names (strip whitespace, lowercase) and all values
    trades_raw = []
    for row in reader:
        if row is None:
            continue
        normalized = {}
        for k, v in row.items():
            if k is None:
                continue
            normalized[k.strip().lower()] = v.strip() if v else ''
        # Skip completely empty rows
        if any(v for v in normalized.values()):
            trades_raw.append(normalized)

    if not trades_raw:
        return []

    # Detect format based on columns
    cols = set(trades_raw[0].keys())

    return _parse_generic(trades_raw, cols)


def _parse_generic(rows, cols):
    """Generic parser that tries to match common column names."""
    # Column name mappings (possible names -> our name)
    symbol_cols = ['symbol', 'instrument', 'contract', 'ticker', 'product', 'sym', 'security']
    side_cols = ['side', 'action', 'buy/sell', 'b/s', 'type', 'order side', 'order action',
                 'buysell', 'buy_sell', 'direction', 'transaction type']
    qty_cols = ['qty', 'quantity', 'filled qty', 'size', 'contracts', 'lots',
                'filled quantity', 'fill qty', 'volume', 'amount']
    price_cols = ['price', 'fill price', 'avg price', 'execution price', 'avg fill price',
                  'fillprice', 'fill_price', 'avgprice', 'avg_price', 'exec price', 'trade price']
    time_cols = ['time', 'datetime', 'fill time', 'execution time', 'timestamp', 'date/time',
                 'fill datetime', 'exec time', 'trade time', 'filldatetime']
    date_cols = ['date', 'trade date', 'tradedate', 'trade_date', 'fill date', 'exec date']
    fee_cols = ['commission', 'fees', 'comm', 'fee', 'commissions', 'total commission',
                'total fees', 'trading fees', 'brokerage']

    def find_col(candidates):
        for c in candidates:
            if c in cols:
                return c
        return None

    symbol_col = find_col(symbol_cols)
    side_col = find_col(side_cols)
    qty_col = find_col(qty_cols)
    price_col = find_col(price_cols)
    time_col = find_col(time_cols)
    date_col = find_col(date_cols)
    fee_col = find_col(fee_cols)

    # Group executions into trades (pair entries with exits)
    executions = []
    for row in rows:
        try:
            symbol = row.get(symbol_col, 'ES') if symbol_col else 'ES'
            side_raw = row.get(side_col, '').upper() if side_col else ''

            if not side_raw:
                continue

            # Normalize side
            if side_raw in ('BUY', 'B', 'LONG', 'BOT'):
                side = 'BUY'
            elif side_raw in ('SELL', 'S', 'SHORT', 'SLD'):
                side = 'SELL'
            else:
                continue

            qty = int(float(row.get(qty_col, 1))) if qty_col else 1
            price = float(row.get(price_col, 0)) if price_col else 0

            # Parse timestamp
            dt = None
            if time_col and row.get(time_col):
                for fmt in ['%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y %I:%M:%S %p',
                           '%Y-%m-%dT%H:%M:%S', '%m/%d/%y %H:%M:%S', '%H:%M:%S']:
                    try:
                        dt = datetime.strptime(row[time_col], fmt)
                        break
                    except ValueError:
                        continue

            if dt is None and date_col and row.get(date_col):
                for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y']:
                    try:
                        dt = datetime.strptime(row[date_col], fmt)
                        break
                    except ValueError:
                        continue

            if dt is None:
                dt = datetime.now()

            fee = float(row.get(fee_col, 0)) if fee_col else 0

            executions.append({
                'symbol': symbol,
                'side': side,
                'quantity': qty,
                'price': price,
                'datetime': dt,
                'fee': abs(fee),
            })
        except (ValueError, TypeError):
            continue

    # Pair executions into round-trip trades
    return pair_executions(executions)


def pair_executions(executions):
    """Pair buy/sell executions into round-trip trades."""
    if not executions:
        return []

    executions.sort(key=lambda x: x['datetime'])
    trades = []
    position = {}  # symbol -> list of open entries

    for ex in executions:
        sym = ex['symbol']
        if sym not in position:
            position[sym] = []

        open_entries = position[sym]

        # Check if this closes an existing position
        if open_entries and open_entries[0]['side'] != ex['side']:
            # This is an exit
            entry = open_entries.pop(0)
            qty = min(entry['quantity'], ex['quantity'])

            pnl = calculate_pnl(entry['side'], entry['price'], ex['price'], qty, sym)

            trades.append({
                'date': entry['datetime'].date(),
                'symbol': sym,
                'side': entry['side'],
                'quantity': qty,
                'entry_price': entry['price'],
                'exit_price': ex['price'],
                'entry_time': entry['datetime'],
                'exit_time': ex['datetime'],
                'pnl': pnl,
                'fees': entry['fee'] + ex['fee'],
                'tick_value': get_tick_value(sym),
                'status': 'closed',
            })

            # Handle partial fills
            remaining = ex['quantity'] - qty
            if remaining > 0:
                position[sym].append({**ex, 'quantity': remaining})
            remaining_entry = entry['quantity'] - qty
            if remaining_entry > 0:
                position[sym].insert(0, {**entry, 'quantity': remaining_entry})
        else:
            # This is a new entry
            open_entries.append(ex)

    return trades


def parse_manual_trade(data):
    """Parse a manually entered trade from form data."""
    symbol = data.get('symbol', 'ES').upper()
    side = data.get('side', 'BUY').upper()
    quantity = int(data.get('quantity', 1))
    entry_price = float(data.get('entry_price'))
    exit_price = float(data.get('exit_price')) if data.get('exit_price') else None

    entry_time = datetime.fromisoformat(data['entry_time']) if data.get('entry_time') else datetime.now()
    exit_time = datetime.fromisoformat(data['exit_time']) if data.get('exit_time') else None

    fees = float(data.get('fees', 0))

    pnl = None
    status = 'open'
    if exit_price is not None:
        pnl = calculate_pnl(side, entry_price, exit_price, quantity, symbol)
        status = 'closed'

    return {
        'date': entry_time.date(),
        'symbol': symbol,
        'side': side,
        'quantity': quantity,
        'entry_price': entry_price,
        'exit_price': exit_price,
        'entry_time': entry_time,
        'exit_time': exit_time,
        'pnl': pnl,
        'fees': fees,
        'tick_value': get_tick_value(symbol),
        'notes': data.get('notes', ''),
        'status': status,
    }
