"""Database models for trade journal."""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class Trade(db.Model):
    __tablename__ = 'trades'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    symbol = db.Column(db.String(20), nullable=False)
    side = db.Column(db.String(4), nullable=False)  # BUY or SELL
    quantity = db.Column(db.Integer, nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    exit_price = db.Column(db.Float, nullable=True)
    entry_time = db.Column(db.DateTime, nullable=False)
    exit_time = db.Column(db.DateTime, nullable=True)
    pnl = db.Column(db.Float, nullable=True)
    fees = db.Column(db.Float, default=0.0)
    tick_value = db.Column(db.Float, default=12.50)
    notes = db.Column(db.Text, nullable=True)
    stop_loss = db.Column(db.Float, nullable=True)
    r_multiple = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(10), default='closed')
    source = db.Column(db.String(20), default='manual')  # manual, csv, rithmic
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat(),
            'symbol': self.symbol,
            'side': self.side,
            'quantity': self.quantity,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'entry_time': self.entry_time.isoformat() if self.entry_time else None,
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'pnl': self.pnl,
            'fees': self.fees,
            'tick_value': self.tick_value,
            'notes': self.notes,
            'stop_loss': self.stop_loss,
            'r_multiple': self.r_multiple,
            'status': self.status,
            'source': self.source,
        }


class JournalEntry(db.Model):
    __tablename__ = 'journal_entries'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    pre_market_plan = db.Column(db.Text, nullable=True)
    post_market_review = db.Column(db.Text, nullable=True)
    emotions = db.Column(db.String(200), nullable=True)
    rating = db.Column(db.Integer, nullable=True)  # 1-5 self-rating
    lessons = db.Column(db.Text, nullable=True)
    mistakes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat(),
            'pre_market_plan': self.pre_market_plan,
            'post_market_review': self.post_market_review,
            'emotions': self.emotions,
            'rating': self.rating,
            'lessons': self.lessons,
            'mistakes': self.mistakes,
        }


class DailyStats(db.Model):
    __tablename__ = 'daily_stats'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    total_trades = db.Column(db.Integer, default=0)
    winning_trades = db.Column(db.Integer, default=0)
    losing_trades = db.Column(db.Integer, default=0)
    gross_pnl = db.Column(db.Float, default=0.0)
    net_pnl = db.Column(db.Float, default=0.0)
    total_fees = db.Column(db.Float, default=0.0)
    largest_win = db.Column(db.Float, default=0.0)
    largest_loss = db.Column(db.Float, default=0.0)
    avg_win = db.Column(db.Float, default=0.0)
    avg_loss = db.Column(db.Float, default=0.0)
    win_rate = db.Column(db.Float, default=0.0)
    profit_factor = db.Column(db.Float, default=0.0)
    max_drawdown = db.Column(db.Float, default=0.0)
    avg_hold_time_seconds = db.Column(db.Float, default=0.0)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns
                if c.name not in ('id',)}
