"""
Database models for RIT Data Collection
Uses SQLite with SQLAlchemy for robust data storage
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import json

from config import DB_PATH


def get_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
    conn.execute("PRAGMA synchronous=NORMAL")  # Balance speed/safety
    return conn


def init_database():
    """Initialize all database tables."""
    conn = get_connection()
    cursor = conn.cursor()

    # Case information table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS case_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            case_name TEXT,
            period INTEGER,
            tick INTEGER,
            ticks_per_period INTEGER,
            total_periods INTEGER,
            status TEXT,
            is_enforce_trading_limits INTEGER,
            raw_json TEXT
        )
    """)

    # Trader information table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trader_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period INTEGER,
            tick INTEGER,
            trader_id TEXT,
            first_name TEXT,
            last_name TEXT,
            nlv REAL,
            raw_json TEXT
        )
    """)

    # Trading limits table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trading_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period INTEGER,
            tick INTEGER,
            limit_name TEXT,
            gross INTEGER,
            net INTEGER,
            gross_limit INTEGER,
            net_limit INTEGER,
            gross_fine REAL,
            net_fine REAL,
            raw_json TEXT
        )
    """)

    # Securities (prices, positions) table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS securities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period INTEGER,
            tick INTEGER,
            ticker TEXT NOT NULL,
            security_type TEXT,
            position INTEGER,
            vwap REAL,
            nlv REAL,
            last_price REAL,
            bid REAL,
            bid_size INTEGER,
            ask REAL,
            ask_size INTEGER,
            volume INTEGER,
            total_volume INTEGER,
            unrealized REAL,
            realized REAL,
            spread REAL,
            mid_price REAL,
            raw_json TEXT
        )
    """)

    # Order book snapshots table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_book (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period INTEGER,
            tick INTEGER,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            level INTEGER NOT NULL,
            price REAL,
            quantity INTEGER,
            quantity_filled INTEGER,
            order_id INTEGER,
            trader_id TEXT
        )
    """)

    # OHLC history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ohlc_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period INTEGER,
            ticker TEXT NOT NULL,
            history_tick INTEGER,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            close_price REAL
        )
    """)

    # Time and sales table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS time_and_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            tas_id INTEGER,
            period INTEGER,
            tick INTEGER,
            price REAL,
            quantity INTEGER
        )
    """)

    # Tender offers table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tenders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period INTEGER,
            tick INTEGER,
            tender_id INTEGER NOT NULL,
            ticker TEXT,
            caption TEXT,
            quantity INTEGER,
            action TEXT,
            is_fixed_bid INTEGER,
            price REAL,
            expires INTEGER,
            status TEXT,
            raw_json TEXT
        )
    """)

    # News table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            news_id INTEGER NOT NULL,
            period INTEGER,
            tick INTEGER,
            ticker TEXT,
            headline TEXT,
            body TEXT,
            raw_json TEXT,
            UNIQUE(news_id)
        )
    """)

    # Orders table (own orders)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            period INTEGER,
            tick INTEGER,
            order_id INTEGER NOT NULL,
            trader_id TEXT,
            ticker TEXT,
            order_type TEXT,
            quantity INTEGER,
            action TEXT,
            price REAL,
            quantity_filled INTEGER,
            vwap REAL,
            status TEXT,
            raw_json TEXT
        )
    """)

    # Connection status log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS connection_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT,
            success INTEGER
        )
    """)

    # System events log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            period INTEGER,
            tick INTEGER,
            message TEXT,
            data TEXT
        )
    """)

    # Create indexes for common queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_securities_ticker_tick ON securities(ticker, period, tick)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_book_ticker_tick ON order_book(ticker, period, tick)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_tender_id ON tenders(tender_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tas_ticker ON time_and_sales(ticker, period, tick)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ohlc_ticker ON ohlc_history(ticker, period)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_id ON news(news_id)")

    conn.commit()
    conn.close()
    print(f"Database initialized at: {DB_PATH}")


class DataStore:
    """Data storage class with methods for inserting various data types."""

    def __init__(self):
        init_database()

    def _get_timestamp(self) -> str:
        return datetime.now().isoformat()

    def save_case_info(self, data: Dict[str, Any], period: int = None, tick: int = None):
        """Save case information."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO case_info
            (timestamp, case_name, period, tick, ticks_per_period, total_periods,
             status, is_enforce_trading_limits, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self._get_timestamp(),
            data.get('name'),
            data.get('period', period),
            data.get('tick', tick),
            data.get('ticks_per_period'),
            data.get('total_periods'),
            data.get('status'),
            1 if data.get('is_enforce_trading_limits') else 0,
            json.dumps(data)
        ))
        conn.commit()
        conn.close()

    def save_trader_info(self, data: Dict[str, Any], period: int = None, tick: int = None):
        """Save trader information."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trader_info
            (timestamp, period, tick, trader_id, first_name, last_name, nlv, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self._get_timestamp(),
            period,
            tick,
            data.get('trader_id'),
            data.get('first_name'),
            data.get('last_name'),
            data.get('nlv'),
            json.dumps(data)
        ))
        conn.commit()
        conn.close()

    def save_trading_limits(self, limits: List[Dict[str, Any]], period: int = None, tick: int = None):
        """Save trading limits."""
        conn = get_connection()
        cursor = conn.cursor()
        for limit in limits:
            cursor.execute("""
                INSERT INTO trading_limits
                (timestamp, period, tick, limit_name, gross, net, gross_limit,
                 net_limit, gross_fine, net_fine, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self._get_timestamp(),
                period,
                tick,
                limit.get('name'),
                limit.get('gross'),
                limit.get('net'),
                limit.get('gross_limit'),
                limit.get('net_limit'),
                limit.get('gross_fine'),
                limit.get('net_fine'),
                json.dumps(limit)
            ))
        conn.commit()
        conn.close()

    def save_securities(self, securities: List[Dict[str, Any]], period: int = None, tick: int = None):
        """Save securities data."""
        conn = get_connection()
        cursor = conn.cursor()
        for sec in securities:
            bid = sec.get('bid', 0) or 0
            ask = sec.get('ask', 0) or 0
            spread = (ask - bid) if (bid > 0 and ask > 0) else None
            mid_price = (bid + ask) / 2 if (bid > 0 and ask > 0) else None

            cursor.execute("""
                INSERT INTO securities
                (timestamp, period, tick, ticker, security_type, position, vwap, nlv,
                 last_price, bid, bid_size, ask, ask_size, volume, total_volume,
                 unrealized, realized, spread, mid_price, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self._get_timestamp(),
                period,
                tick,
                sec.get('ticker'),
                sec.get('type'),
                sec.get('position'),
                sec.get('vwap'),
                sec.get('nlv'),
                sec.get('last'),
                bid,
                sec.get('bid_size'),
                ask,
                sec.get('ask_size'),
                sec.get('volume'),
                sec.get('total_volume'),
                sec.get('unrealized'),
                sec.get('realized'),
                spread,
                mid_price,
                json.dumps(sec)
            ))
        conn.commit()
        conn.close()

    def save_order_book(self, ticker: str, book: Dict[str, Any], period: int = None, tick: int = None):
        """Save order book snapshot."""
        conn = get_connection()
        cursor = conn.cursor()
        timestamp = self._get_timestamp()

        # Save bids
        for level, order in enumerate(book.get('bid', []) or book.get('bids', [])):
            cursor.execute("""
                INSERT INTO order_book
                (timestamp, period, tick, ticker, side, level, price, quantity,
                 quantity_filled, order_id, trader_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp, period, tick, ticker, 'BID', level,
                order.get('price'),
                order.get('quantity'),
                order.get('quantity_filled'),
                order.get('order_id'),
                order.get('trader_id')
            ))

        # Save asks
        for level, order in enumerate(book.get('ask', []) or book.get('asks', [])):
            cursor.execute("""
                INSERT INTO order_book
                (timestamp, period, tick, ticker, side, level, price, quantity,
                 quantity_filled, order_id, trader_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp, period, tick, ticker, 'ASK', level,
                order.get('price'),
                order.get('quantity'),
                order.get('quantity_filled'),
                order.get('order_id'),
                order.get('trader_id')
            ))

        conn.commit()
        conn.close()

    def save_ohlc_history(self, ticker: str, history: List[Dict[str, Any]], period: int = None):
        """Save OHLC price history."""
        conn = get_connection()
        cursor = conn.cursor()
        timestamp = self._get_timestamp()

        for bar in history:
            cursor.execute("""
                INSERT INTO ohlc_history
                (timestamp, period, ticker, history_tick, open_price, high_price,
                 low_price, close_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                period,
                ticker,
                bar.get('tick'),
                bar.get('open'),
                bar.get('high'),
                bar.get('low'),
                bar.get('close')
            ))

        conn.commit()
        conn.close()

    def save_time_and_sales(self, ticker: str, trades: List[Dict[str, Any]]):
        """Save time and sales data."""
        conn = get_connection()
        cursor = conn.cursor()
        timestamp = self._get_timestamp()

        for trade in trades:
            cursor.execute("""
                INSERT OR IGNORE INTO time_and_sales
                (timestamp, ticker, tas_id, period, tick, price, quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                ticker,
                trade.get('id'),
                trade.get('period'),
                trade.get('tick'),
                trade.get('price'),
                trade.get('quantity')
            ))

        conn.commit()
        conn.close()

    def save_tender(self, tender: Dict[str, Any], period: int = None, tick: int = None, status: str = 'ACTIVE'):
        """Save tender offer."""
        conn = get_connection()
        cursor = conn.cursor()

        # Extract ticker from caption if not directly available
        ticker = tender.get('ticker') or tender.get('caption', '').split()[0] if tender.get('caption') else None

        cursor.execute("""
            INSERT INTO tenders
            (timestamp, period, tick, tender_id, ticker, caption, quantity, action,
             is_fixed_bid, price, expires, status, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self._get_timestamp(),
            tender.get('period', period),
            tender.get('tick', tick),
            tender.get('tender_id'),
            ticker,
            tender.get('caption'),
            tender.get('quantity'),
            tender.get('action'),
            1 if tender.get('is_fixed_bid') else 0,
            tender.get('price'),
            tender.get('expires'),
            status,
            json.dumps(tender)
        ))

        conn.commit()
        conn.close()

    def save_news(self, news_items: List[Dict[str, Any]]):
        """Save news items."""
        conn = get_connection()
        cursor = conn.cursor()
        timestamp = self._get_timestamp()

        for news in news_items:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO news
                    (timestamp, news_id, period, tick, ticker, headline, body, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    news.get('news_id'),
                    news.get('period'),
                    news.get('tick'),
                    news.get('ticker'),
                    news.get('headline'),
                    news.get('body'),
                    json.dumps(news)
                ))
            except sqlite3.IntegrityError:
                pass  # Already exists

        conn.commit()
        conn.close()

    def save_orders(self, orders: List[Dict[str, Any]], period: int = None, tick: int = None):
        """Save orders."""
        conn = get_connection()
        cursor = conn.cursor()
        timestamp = self._get_timestamp()

        for order in orders:
            cursor.execute("""
                INSERT INTO orders
                (timestamp, period, tick, order_id, trader_id, ticker, order_type,
                 quantity, action, price, quantity_filled, vwap, status, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                order.get('period', period),
                order.get('tick', tick),
                order.get('order_id'),
                order.get('trader_id'),
                order.get('ticker'),
                order.get('type'),
                order.get('quantity'),
                order.get('action'),
                order.get('price'),
                order.get('quantity_filled'),
                order.get('vwap'),
                order.get('status'),
                json.dumps(order)
            ))

        conn.commit()
        conn.close()

    def log_connection_event(self, event_type: str, message: str, success: bool = True):
        """Log connection events."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO connection_log (timestamp, event_type, message, success)
            VALUES (?, ?, ?, ?)
        """, (self._get_timestamp(), event_type, message, 1 if success else 0))
        conn.commit()
        conn.close()

    def log_system_event(self, event_type: str, message: str, period: int = None,
                         tick: int = None, data: Any = None):
        """Log system events."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_events (timestamp, event_type, period, tick, message, data)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            self._get_timestamp(),
            event_type,
            period,
            tick,
            message,
            json.dumps(data) if data else None
        ))
        conn.commit()
        conn.close()

    def get_last_news_id(self) -> int:
        """Get the last saved news ID."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(news_id) FROM news")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result and result[0] else 0

    def get_last_tas_id(self, ticker: str) -> int:
        """Get the last saved time & sales ID for a ticker."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(tas_id) FROM time_and_sales WHERE ticker = ?", (ticker,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result and result[0] else 0


if __name__ == "__main__":
    # Test database initialization
    init_database()
    print("Database tables created successfully!")
