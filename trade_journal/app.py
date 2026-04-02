#!/usr/bin/env python3
"""Trade Journal - Comprehensive futures trade journaling application."""
import os
import json
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify
from models import db, Trade, JournalEntry, DailyStats
from amp_import import parse_amp_csv, parse_manual_trade, calculate_pnl, get_point_value, normalize_symbol
import rithmic_connector

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///trade_journal.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db.init_app(app)

# Store rithmic creds in memory (loaded from .env or settings)
_rithmic_creds = {}
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), 'settings.json')


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    return {}


def save_settings(data):
    with open(SETTINGS_PATH, 'w') as f:
        json.dump(data, f, indent=2)


with app.app_context():
    db.create_all()

    # Migrate: add new columns if missing
    import sqlalchemy
    inspector = sqlalchemy.inspect(db.engine)
    trade_cols = [c['name'] for c in inspector.get_columns('trades')]
    with db.engine.connect() as conn:
        if 'stop_loss' not in trade_cols:
            conn.execute(sqlalchemy.text('ALTER TABLE trades ADD COLUMN stop_loss FLOAT'))
        if 'r_multiple' not in trade_cols:
            conn.execute(sqlalchemy.text('ALTER TABLE trades ADD COLUMN r_multiple FLOAT'))
        if 'source' not in trade_cols:
            conn.execute(sqlalchemy.text("ALTER TABLE trades ADD COLUMN source VARCHAR(20) DEFAULT 'manual'"))
        conn.commit()


def calc_r_multiple(side, entry_price, exit_price, stop_loss):
    """Calculate R-multiple: reward / risk."""
    if not stop_loss or not exit_price:
        return None
    if side == 'BUY':
        risk = abs(entry_price - stop_loss)
        reward = exit_price - entry_price
    else:
        risk = abs(stop_loss - entry_price)
        reward = entry_price - exit_price
    if risk == 0:
        return None
    return round(reward / risk, 2)


def recalculate_daily_stats(target_date):
    """Recalculate stats for a given date based on trades."""
    trades = Trade.query.filter_by(date=target_date, status='closed').all()

    stats = DailyStats.query.filter_by(date=target_date).first()
    if not stats:
        stats = DailyStats(date=target_date)
        db.session.add(stats)

    if not trades:
        db.session.delete(stats)
        db.session.commit()
        return

    winners = [t for t in trades if t.pnl and t.pnl > 0]
    losers = [t for t in trades if t.pnl and t.pnl < 0]

    total_wins = sum(t.pnl for t in winners)
    total_losses = abs(sum(t.pnl for t in losers))

    stats.total_trades = len(trades)
    stats.winning_trades = len(winners)
    stats.losing_trades = len(losers)
    stats.gross_pnl = sum(t.pnl for t in trades if t.pnl)
    stats.total_fees = sum(t.fees for t in trades if t.fees)
    stats.net_pnl = stats.gross_pnl - stats.total_fees
    stats.largest_win = max((t.pnl for t in winners), default=0)
    stats.largest_loss = min((t.pnl for t in losers), default=0)
    stats.avg_win = total_wins / len(winners) if winners else 0
    stats.avg_loss = -total_losses / len(losers) if losers else 0
    stats.win_rate = len(winners) / len(trades) * 100 if trades else 0
    stats.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf') if total_wins > 0 else 0

    hold_times = []
    for t in trades:
        if t.entry_time and t.exit_time:
            hold_times.append((t.exit_time - t.entry_time).total_seconds())
    stats.avg_hold_time_seconds = sum(hold_times) / len(hold_times) if hold_times else 0

    sorted_trades = sorted(trades, key=lambda t: t.entry_time or datetime.min)
    running = 0
    peak = 0
    max_dd = 0
    for t in sorted_trades:
        running += (t.pnl or 0)
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    stats.max_drawdown = max_dd

    db.session.commit()


# ─── Routes ──────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    return render_template('index.html')


@app.route('/api/dashboard')
def api_dashboard():
    """Get dashboard data with optional timeframe filter."""
    timeframe = request.args.get('timeframe', 'all')  # today, week, month, quarter, year, all

    query = DailyStats.query
    now = date.today()

    if timeframe == 'today':
        query = query.filter(DailyStats.date == now)
    elif timeframe == 'week':
        start = now - timedelta(days=now.weekday())
        query = query.filter(DailyStats.date >= start)
    elif timeframe == 'month':
        start = now.replace(day=1)
        query = query.filter(DailyStats.date >= start)
    elif timeframe == 'quarter':
        q_month = ((now.month - 1) // 3) * 3 + 1
        start = now.replace(month=q_month, day=1)
        query = query.filter(DailyStats.date >= start)
    elif timeframe == 'year':
        start = now.replace(month=1, day=1)
        query = query.filter(DailyStats.date >= start)
    elif timeframe == '7d':
        query = query.filter(DailyStats.date >= now - timedelta(days=7))
    elif timeframe == '30d':
        query = query.filter(DailyStats.date >= now - timedelta(days=30))
    elif timeframe == '90d':
        query = query.filter(DailyStats.date >= now - timedelta(days=90))

    days = query.order_by(DailyStats.date).all()

    cumulative = 0
    daily_data = []
    streak = 0
    best_day = None
    worst_day = None

    for d in days:
        cumulative += d.net_pnl
        entry = d.to_dict()
        entry['date'] = d.date.isoformat()
        entry['cumulative_pnl'] = cumulative
        daily_data.append(entry)

        if best_day is None or d.net_pnl > best_day['net_pnl']:
            best_day = entry
        if worst_day is None or d.net_pnl < worst_day['net_pnl']:
            worst_day = entry

    # Current streak (always from most recent day regardless of filter)
    recent = DailyStats.query.order_by(DailyStats.date.desc()).all()
    if recent:
        direction = 'win' if recent[0].net_pnl >= 0 else 'loss'
        for d in recent:
            if (direction == 'win' and d.net_pnl >= 0) or (direction == 'loss' and d.net_pnl < 0):
                streak += 1
            else:
                break
        if direction == 'loss':
            streak = -streak

    # Avg R-multiple for the filtered period
    date_filter = [d.date.isoformat() for d in days]
    r_trades = Trade.query.filter(
        Trade.r_multiple.isnot(None),
        Trade.date.in_([d.date for d in days]) if days else True
    ).all() if days else []
    avg_r = sum(t.r_multiple for t in r_trades) / len(r_trades) if r_trades else 0

    total_stats = {
        'total_days': len(days),
        'total_pnl': cumulative,
        'avg_daily_pnl': cumulative / len(days) if days else 0,
        'winning_days': sum(1 for d in days if d.net_pnl > 0),
        'losing_days': sum(1 for d in days if d.net_pnl < 0),
        'best_day': best_day,
        'worst_day': worst_day,
        'current_streak': streak,
        'avg_r_multiple': round(avg_r, 2),
        'total_trades': sum(d.total_trades for d in days),
    }

    return jsonify({'daily': daily_data, 'summary': total_stats})


@app.route('/api/day/<date_str>')
def api_day(date_str):
    target = date.fromisoformat(date_str)
    trades = Trade.query.filter_by(date=target).order_by(Trade.entry_time).all()
    journal = JournalEntry.query.filter_by(date=target).first()
    stats = DailyStats.query.filter_by(date=target).first()

    # Compute avg R for the day
    r_trades = [t for t in trades if t.r_multiple is not None]
    avg_r = sum(t.r_multiple for t in r_trades) / len(r_trades) if r_trades else None

    stats_dict = stats.to_dict() if stats else None
    if stats_dict:
        stats_dict['avg_r_multiple'] = avg_r

    return jsonify({
        'date': date_str,
        'trades': [t.to_dict() for t in trades],
        'journal': journal.to_dict() if journal else None,
        'stats': stats_dict,
    })


@app.route('/api/chart-data/<date_str>')
def api_chart_data(date_str):
    target = date.fromisoformat(date_str)
    trades = Trade.query.filter_by(date=target).order_by(Trade.entry_time).all()
    interval = request.args.get('interval', '5m')  # 1m, 5m, 15m, 30m, 1h

    if not trades:
        return jsonify({'candles': [], 'markers': []})

    symbol = trades[0].symbol if trades else 'ES'
    base_symbol = normalize_symbol(symbol)

    candles = []
    try:
        import yfinance as yf
        yf_map = {
            'ES': 'ES=F', 'MES': 'ES=F', 'NQ': 'NQ=F', 'MNQ': 'NQ=F',
            'YM': 'YM=F', 'MYM': 'YM=F', 'RTY': 'RTY=F', 'M2K': 'RTY=F',
            'CL': 'CL=F', 'MCL': 'CL=F', 'GC': 'GC=F', 'MGC': 'GC=F',
        }
        ticker = yf_map.get(base_symbol, f'{base_symbol}=F')
        start = target
        end = target + timedelta(days=1)
        data = yf.download(ticker, start=start, end=end, interval=interval, progress=False)

        if data is not None and len(data) > 0:
            for idx, row in data.iterrows():
                candles.append({
                    'time': int(idx.timestamp()),
                    'open': round(float(row.iloc[0]), 2),
                    'high': round(float(row.iloc[1]), 2),
                    'low': round(float(row.iloc[2]), 2),
                    'close': round(float(row.iloc[3]), 2),
                })
    except Exception as e:
        print(f"Chart data fetch error: {e}")

    markers = []
    for t in trades:
        if t.entry_time:
            markers.append({
                'time': int(t.entry_time.timestamp()),
                'position': 'belowBar' if t.side == 'BUY' else 'aboveBar',
                'color': '#00ffaa' if t.side == 'BUY' else '#ff2a6d',
                'shape': 'arrowUp' if t.side == 'BUY' else 'arrowDown',
                'text': f'{t.side} {t.quantity}@{t.entry_price}',
                'price': t.entry_price,
                'type': 'entry',
                'trade_id': t.id,
            })
        if t.exit_time and t.exit_price:
            r_text = f' ({t.r_multiple:+.1f}R)' if t.r_multiple else ''
            markers.append({
                'time': int(t.exit_time.timestamp()),
                'position': 'aboveBar' if t.side == 'BUY' else 'belowBar',
                'color': '#bf5af2',
                'shape': 'circle',
                'text': f'EXIT {t.quantity}@{t.exit_price} ({"+" if t.pnl and t.pnl >= 0 else ""}{t.pnl:.0f}){r_text}',
                'price': t.exit_price,
                'type': 'exit',
                'trade_id': t.id,
            })
        # Add stop loss line if set
        if t.stop_loss:
            markers.append({
                'time': int(t.entry_time.timestamp()) if t.entry_time else 0,
                'price': t.stop_loss,
                'type': 'stop_loss',
                'trade_id': t.id,
                'color': '#ff9f0a',
            })

    markers.sort(key=lambda m: m.get('time', 0))

    return jsonify({'candles': candles, 'markers': markers, 'symbol': base_symbol})


@app.route('/api/trades', methods=['POST'])
def add_trade():
    data = request.json
    trade_data = parse_manual_trade(data)

    # Handle stop loss and R-multiple
    stop_loss = float(data['stop_loss']) if data.get('stop_loss') else None
    trade_data['stop_loss'] = stop_loss
    if stop_loss and trade_data.get('exit_price'):
        trade_data['r_multiple'] = calc_r_multiple(
            trade_data['side'], trade_data['entry_price'],
            trade_data['exit_price'], stop_loss
        )

    trade = Trade(**trade_data)
    db.session.add(trade)
    db.session.commit()
    recalculate_daily_stats(trade.date)
    return jsonify(trade.to_dict()), 201


@app.route('/api/trades/<int:trade_id>', methods=['PUT'])
def update_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    data = request.json
    for key in ['symbol', 'side', 'quantity', 'entry_price', 'exit_price', 'notes', 'fees', 'stop_loss']:
        if key in data:
            val = data[key]
            if key in ('entry_price', 'exit_price', 'fees', 'stop_loss') and val is not None and val != '':
                val = float(val)
            elif key in ('quantity',) and val is not None:
                val = int(val)
            setattr(trade, key, val if val != '' else None)
    if 'entry_time' in data:
        trade.entry_time = datetime.fromisoformat(data['entry_time'])
    if 'exit_time' in data:
        trade.exit_time = datetime.fromisoformat(data['exit_time'])
    if trade.exit_price:
        trade.pnl = calculate_pnl(trade.side, trade.entry_price, trade.exit_price, trade.quantity, trade.symbol)
        trade.status = 'closed'
    if trade.stop_loss and trade.exit_price:
        trade.r_multiple = calc_r_multiple(trade.side, trade.entry_price, trade.exit_price, trade.stop_loss)
    db.session.commit()
    recalculate_daily_stats(trade.date)
    return jsonify(trade.to_dict())


@app.route('/api/trades/<int:trade_id>/notes', methods=['PUT'])
def update_trade_notes(trade_id):
    """Quick endpoint to update just a trade's notes."""
    trade = Trade.query.get_or_404(trade_id)
    data = request.json
    trade.notes = data.get('notes', '')
    db.session.commit()
    return jsonify({'ok': True, 'notes': trade.notes})


@app.route('/api/trades/<int:trade_id>', methods=['DELETE'])
def delete_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    trade_date = trade.date
    db.session.delete(trade)
    db.session.commit()
    recalculate_daily_stats(trade_date)
    return jsonify({'ok': True})


@app.route('/api/import', methods=['POST'])
def import_trades():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not file.filename.lower().endswith('.csv'):
        return jsonify({'error': 'Only CSV files are supported. Please upload a .csv file.'}), 400

    try:
        content = file.read().decode('utf-8')
    except UnicodeDecodeError:
        try:
            file.seek(0)
            content = file.read().decode('latin-1')
        except Exception:
            return jsonify({'error': 'Could not read file. Ensure it is a valid CSV with UTF-8 or Latin-1 encoding.'}), 400

    if not content.strip():
        return jsonify({'error': 'File is empty.'}), 400

    try:
        trades_data = parse_amp_csv(content)
    except Exception as e:
        return jsonify({'error': f'CSV parsing error: {str(e)}. Check that your file is a valid trade export.'}), 400

    if not trades_data:
        return jsonify({'error': 'No trades found in file. Make sure your CSV has columns for Symbol, Side, and Price. '
                        'Export from AMP: Reports → Trade History → Export CSV.'}), 400

    preview = request.args.get('preview', '').lower() in ('true', '1', 'yes')

    if preview:
        # Return parsed trades without saving
        preview_trades = []
        for td in trades_data:
            preview_trades.append({
                'date': td['date'].isoformat() if td.get('date') else '',
                'symbol': td.get('symbol', ''),
                'side': td.get('side', ''),
                'quantity': td.get('quantity', 1),
                'entry_price': td.get('entry_price', 0),
                'exit_price': td.get('exit_price'),
                'pnl': round(td.get('pnl', 0), 2) if td.get('pnl') is not None else None,
                'fees': round(td.get('fees', 0), 2),
                'entry_time': td['entry_time'].isoformat() if td.get('entry_time') else '',
                'exit_time': td['exit_time'].isoformat() if td.get('exit_time') else '',
                'status': td.get('status', 'open'),
            })
        return jsonify({'preview': True, 'trades': preview_trades, 'count': len(preview_trades)})

    imported = 0
    dates_affected = set()
    for td in trades_data:
        td['source'] = 'csv'
        trade = Trade(**td)
        db.session.add(trade)
        dates_affected.add(td['date'])
        imported += 1
    db.session.commit()
    for d in dates_affected:
        recalculate_daily_stats(d)
    return jsonify({'imported': imported, 'dates': [d.isoformat() for d in sorted(dates_affected)]})


@app.route('/api/journal', methods=['POST'])
def save_journal():
    data = request.json
    target = date.fromisoformat(data['date'])
    entry = JournalEntry.query.filter_by(date=target).first()
    if not entry:
        entry = JournalEntry(date=target)
        db.session.add(entry)
    for field in ['pre_market_plan', 'post_market_review', 'emotions', 'rating', 'lessons', 'mistakes']:
        if field in data:
            setattr(entry, field, data[field])
    db.session.commit()
    return jsonify(entry.to_dict())


@app.route('/api/calendar')
def api_calendar():
    stats = DailyStats.query.order_by(DailyStats.date).all()
    return jsonify([{
        'date': s.date.isoformat(),
        'net_pnl': s.net_pnl,
        'total_trades': s.total_trades,
        'win_rate': s.win_rate,
    } for s in stats])


@app.route('/api/trading-days')
def api_trading_days():
    dates = db.session.query(Trade.date).distinct().order_by(Trade.date.desc()).all()
    return jsonify([d[0].isoformat() for d in dates])


# ─── Rithmic / AMP Connection ───────────────────────────────────────────

@app.route('/api/rithmic/status')
def rithmic_status():
    settings = load_settings()
    status = rithmic_connector.get_status()
    status['configured'] = bool(settings.get('rithmic_user'))
    return jsonify(status)


@app.route('/api/rithmic/settings', methods=['GET'])
def get_rithmic_settings():
    settings = load_settings()
    return jsonify({
        'user': settings.get('rithmic_user', ''),
        'system': settings.get('rithmic_system', 'Rithmic Paper Trading'),
        'gateway': settings.get('rithmic_gateway', 'CHICAGO'),
        'account_id': settings.get('rithmic_account_id', ''),
        'configured': bool(settings.get('rithmic_user')),
    })


@app.route('/api/rithmic/settings', methods=['POST'])
def save_rithmic_settings():
    data = request.json
    settings = load_settings()
    settings['rithmic_user'] = data.get('user', '')
    settings['rithmic_password'] = data.get('password', '')
    settings['rithmic_system'] = data.get('system', 'Rithmic Paper Trading')
    settings['rithmic_gateway'] = data.get('gateway', 'CHICAGO')
    settings['rithmic_account_id'] = data.get('account_id', '')
    save_settings(settings)
    return jsonify({'ok': True})


@app.route('/api/rithmic/test', methods=['POST'])
def test_rithmic():
    """Test connection — accepts creds directly from the form so you don't need to save first."""
    data = request.json or {}
    settings = load_settings()

    # Use form values if provided, fall back to saved settings
    creds = {
        'user': data.get('user') or settings.get('rithmic_user', ''),
        'password': data.get('password') or settings.get('rithmic_password', ''),
        'system': data.get('system') or settings.get('rithmic_system', 'Rithmic Paper Trading'),
        'gateway': data.get('gateway') or settings.get('rithmic_gateway', 'CHICAGO'),
    }

    if not creds['user'] or not creds['password']:
        return jsonify({'ok': False, 'error': 'Username and password are required'}), 400

    result = rithmic_connector.test_connection(creds)
    return jsonify(result)


@app.route('/api/rithmic/sync', methods=['POST'])
def sync_rithmic():
    """Sync trades from Rithmic for a given date. Accepts creds from form directly."""
    data = request.json or {}
    target_date_str = data.get('date', date.today().isoformat())
    target = date.fromisoformat(target_date_str)

    settings = load_settings()

    creds = {
        'user': data.get('user') or settings.get('rithmic_user', ''),
        'password': data.get('password') or settings.get('rithmic_password', ''),
        'system': data.get('system') or settings.get('rithmic_system', 'Rithmic Paper Trading'),
        'gateway': data.get('gateway') or settings.get('rithmic_gateway', 'CHICAGO'),
        'account_id': data.get('account_id') or settings.get('rithmic_account_id', ''),
    }

    if not creds['user'] or not creds['password']:
        return jsonify({'error': 'Username and password are required'}), 400

    try:
        trades_data = rithmic_connector.fetch_fills_sync(creds, target)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if not trades_data:
        return jsonify({'imported': 0, 'message': 'No fills found for this date'})

    imported = 0
    for td in trades_data:
        trade = Trade(**td)
        db.session.add(trade)
        imported += 1
    db.session.commit()
    recalculate_daily_stats(target)

    return jsonify({'imported': imported, 'date': target.isoformat()})


# ─── AI Trade Analysis ──────────────────────────────────────────────────

@app.route('/api/ai/analyze-day', methods=['POST'])
def ai_analyze_day():
    """AI analysis of a trading day — learns your patterns over time."""
    data = request.json
    target = date.fromisoformat(data['date'])

    trades = Trade.query.filter_by(date=target, status='closed').order_by(Trade.entry_time).all()
    stats = DailyStats.query.filter_by(date=target).first()
    journal = JournalEntry.query.filter_by(date=target).first()

    if not trades:
        return jsonify({'analysis': 'No closed trades to analyze for this day.'})

    # Build trading history context (last 30 days for pattern recognition)
    history_stats = DailyStats.query.filter(
        DailyStats.date < target,
        DailyStats.date >= target - timedelta(days=30)
    ).order_by(DailyStats.date).all()

    history_trades = Trade.query.filter(
        Trade.date < target,
        Trade.date >= target - timedelta(days=30),
        Trade.status == 'closed'
    ).order_by(Trade.entry_time).all()

    # Load stored AI insights for learning
    settings = load_settings()
    past_insights = settings.get('ai_insights', [])

    # Build the prompt
    trades_detail = []
    for t in trades:
        hold = ''
        if t.entry_time and t.exit_time:
            secs = (t.exit_time - t.entry_time).total_seconds()
            hold = f'{int(secs//60)}m {int(secs%60)}s'
        trades_detail.append(
            f"  #{t.id}: {t.side} {t.quantity}x {t.symbol} @ {t.entry_price} -> {t.exit_price} "
            f"| P&L: ${t.pnl:.2f} | Hold: {hold} | R: {t.r_multiple or 'N/A'} "
            f"| Stop: {t.stop_loss or 'none'} | Notes: {t.notes or 'none'}"
        )

    history_summary = []
    for h in history_stats[-10:]:
        history_summary.append(f"  {h.date}: {h.total_trades} trades, ${h.net_pnl:.2f}, WR: {h.win_rate:.0f}%")

    # Recurring patterns from past trades
    all_r_multiples = [t.r_multiple for t in history_trades if t.r_multiple]
    avg_hold_times = []
    win_times = []
    loss_times = []
    for t in history_trades:
        if t.entry_time and t.exit_time:
            secs = (t.exit_time - t.entry_time).total_seconds()
            avg_hold_times.append(secs)
            if t.pnl and t.pnl > 0:
                win_times.append(secs)
            elif t.pnl and t.pnl < 0:
                loss_times.append(secs)

    pattern_context = ""
    if avg_hold_times:
        avg_hold = sum(avg_hold_times) / len(avg_hold_times)
        pattern_context += f"30-day avg hold time: {avg_hold/60:.1f} minutes\n"
    if win_times and loss_times:
        pattern_context += f"Avg winning trade hold: {sum(win_times)/len(win_times)/60:.1f}m, Avg losing trade hold: {sum(loss_times)/len(loss_times)/60:.1f}m\n"
    if all_r_multiples:
        pattern_context += f"30-day avg R-multiple: {sum(all_r_multiples)/len(all_r_multiples):.2f}R\n"

    # Past insights for continuity
    insights_context = ""
    if past_insights:
        recent_insights = past_insights[-5:]
        insights_context = "Previous AI observations about this trader:\n" + "\n".join(f"  - {i}" for i in recent_insights)

    prompt = f"""You are an elite futures trading coach analyzing a trader's session. Be specific, direct, and actionable.
Your job: identify what went right, what went wrong, specific patterns you notice, and concrete improvements.

TODAY'S SESSION ({target.isoformat()}):
{chr(10).join(trades_detail)}

Day Stats: {stats.total_trades} trades, Net P&L: ${stats.net_pnl:.2f}, Win Rate: {stats.win_rate:.0f}%, Profit Factor: {stats.profit_factor:.2f}, Max DD: ${stats.max_drawdown:.2f}

{f"Journal notes: Pre-market: {journal.pre_market_plan or 'none'} | Post-market: {journal.post_market_review or 'none'} | Emotions: {journal.emotions or 'none'} | Mistakes: {journal.mistakes or 'none'}" if journal else "No journal entry for this day."}

RECENT HISTORY (last 10 sessions):
{chr(10).join(history_summary) if history_summary else "No prior history available."}

PATTERN DATA:
{pattern_context or "Not enough data yet."}

{insights_context}

Provide your analysis in this exact format:
**SESSION GRADE:** [A+ to F]

**WHAT YOU DID WELL:**
- (specific observations)

**MISTAKES & AREAS TO IMPROVE:**
- (specific observations with actionable fixes)

**PATTERNS I NOTICE:**
- (recurring behaviors, timing patterns, hold time issues, risk management)

**KEY INSIGHT:**
(One sentence — the most important thing to internalize from today)

Be brutally honest but constructive. Reference specific trades by number. If you see the same mistakes repeating from history, call it out firmly."""

    # Try Claude API first via Anthropic SDK, then OpenAI, then return prompt-based analysis
    analysis = None

    # Try Anthropic (Claude)
    api_key = settings.get('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY')
    if api_key and not analysis:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            analysis = response.content[0].text
        except Exception as e:
            print(f"Anthropic API error: {e}")

    # Try OpenAI
    if not analysis:
        openai_key = settings.get('openai_api_key') or os.environ.get('OPENAI_API_KEY')
        if openai_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=openai_key)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "system", "content": "You are an expert futures trading coach."}, {"role": "user", "content": prompt}],
                    max_tokens=1500,
                )
                analysis = response.choices[0].message.content
            except Exception as e:
                print(f"OpenAI API error: {e}")

    if not analysis:
        # Fallback: rule-based analysis
        analysis = generate_rule_based_analysis(trades, stats, journal, history_stats)

    # Store key insight for learning continuity
    if analysis and 'KEY INSIGHT' in analysis:
        insight_line = analysis.split('KEY INSIGHT')[1].strip().split('\n')[0].strip(': *')
        if insight_line:
            past_insights.append(f"[{target.isoformat()}] {insight_line[:200]}")
            # Keep last 50 insights
            settings['ai_insights'] = past_insights[-50:]
            save_settings(settings)

    return jsonify({'analysis': analysis})


@app.route('/api/ai/settings', methods=['GET'])
def get_ai_settings():
    settings = load_settings()
    return jsonify({
        'anthropic_configured': bool(settings.get('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY')),
        'openai_configured': bool(settings.get('openai_api_key') or os.environ.get('OPENAI_API_KEY')),
    })


@app.route('/api/ai/settings', methods=['POST'])
def save_ai_settings():
    data = request.json
    settings = load_settings()
    if 'anthropic_api_key' in data:
        settings['anthropic_api_key'] = data['anthropic_api_key']
    if 'openai_api_key' in data:
        settings['openai_api_key'] = data['openai_api_key']
    save_settings(settings)
    return jsonify({'ok': True})


def generate_rule_based_analysis(trades, stats, journal, history):
    """Fallback analysis when no AI API key is configured."""
    lines = []
    grade = 'C'
    if stats.win_rate >= 70 and stats.net_pnl > 0:
        grade = 'A'
    elif stats.win_rate >= 55 and stats.net_pnl > 0:
        grade = 'B'
    elif stats.net_pnl > 0:
        grade = 'B-'
    elif stats.win_rate >= 40:
        grade = 'C'
    else:
        grade = 'D'

    lines.append(f"**SESSION GRADE:** {grade}")
    lines.append("")
    lines.append("**WHAT YOU DID WELL:**")

    winners = [t for t in trades if t.pnl and t.pnl > 0]
    losers = [t for t in trades if t.pnl and t.pnl < 0]

    if winners:
        best = max(winners, key=lambda t: t.pnl)
        lines.append(f"- Best trade: #{best.id} for +${best.pnl:.2f}")
    if stats.win_rate > 50:
        lines.append(f"- Win rate above 50% ({stats.win_rate:.0f}%)")
    if stats.profit_factor > 1.5:
        lines.append(f"- Strong profit factor of {stats.profit_factor:.2f}")

    lines.append("")
    lines.append("**MISTAKES & AREAS TO IMPROVE:**")

    if losers:
        worst = min(losers, key=lambda t: t.pnl)
        lines.append(f"- Largest loss: #{worst.id} for ${worst.pnl:.2f} — review this setup")
        if worst.exit_time and worst.entry_time:
            hold = (worst.exit_time - worst.entry_time).total_seconds()
            if hold < 60:
                lines.append("- Very short hold time on losing trade — possible panic exit")
    if stats.max_drawdown > abs(stats.net_pnl) * 2:
        lines.append(f"- Max drawdown (${stats.max_drawdown:.2f}) was large relative to net P&L")

    no_stops = [t for t in trades if not t.stop_loss]
    if no_stops:
        lines.append(f"- {len(no_stops)} trades without a stop loss defined — always set your risk")

    lines.append("")
    lines.append("**PATTERNS I NOTICE:**")
    lines.append(f"- {len(trades)} trades taken today")

    if history:
        avg_trades = sum(h.total_trades for h in history) / len(history)
        if len(trades) > avg_trades * 1.5:
            lines.append(f"- Overtrading: {len(trades)} trades vs your {avg_trades:.0f} avg")

    lines.append("")
    lines.append("**KEY INSIGHT:**")
    if stats.net_pnl > 0:
        lines.append("Profitable day — focus on repeating what worked and cutting trades that didn't fit your plan.")
    else:
        lines.append("Red day — review each loser and ask: did I follow my rules, or did emotion drive the decision?")

    lines.append("")
    lines.append("*Configure an API key (Anthropic or OpenAI) in Settings for detailed AI analysis with pattern recognition.*")

    return "\n".join(lines)


if __name__ == '__main__':
    app.run(debug=True, port=5050)
