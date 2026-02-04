"""
Session Manager for RIT Data Collection

Organizes data collection by sessions (each case run/sub-heat).
Creates separate database files for each session for easier analysis.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import shutil

from config import DATA_DIR, SESSIONS_DIR, SEPARATE_DB_PER_SESSION


class Session:
    """Represents a data collection session."""

    def __init__(self, session_id: str, db_path: Path):
        self.session_id = session_id
        self.db_path = db_path
        self.start_time = datetime.now()
        self.end_time = None
        self.case_name = None
        self.period = None
        self.securities = set()
        self.tender_count = 0
        self.tick_count = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'session_id': self.session_id,
            'db_path': str(self.db_path),
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'case_name': self.case_name,
            'period': self.period,
            'securities': list(self.securities),
            'tender_count': self.tender_count,
            'tick_count': self.tick_count
        }


class SessionManager:
    """
    Manages data collection sessions.

    Each session corresponds to a case run (or sub-heat).
    Data is stored in separate database files for easy analysis.
    """

    def __init__(self):
        self.current_session: Optional[Session] = None
        self.sessions_index_path = SESSIONS_DIR / "sessions_index.json"
        self._load_sessions_index()

    def _load_sessions_index(self):
        """Load the sessions index file."""
        if self.sessions_index_path.exists():
            with open(self.sessions_index_path, 'r') as f:
                self.sessions_index = json.load(f)
        else:
            self.sessions_index = {'sessions': []}

    def _save_sessions_index(self):
        """Save the sessions index file."""
        with open(self.sessions_index_path, 'w') as f:
            json.dump(self.sessions_index, f, indent=2)

    def create_session(self, case_name: str = None, period: int = None) -> Session:
        """
        Create a new data collection session.

        Args:
            case_name: Name of the case being run
            period: Period/sub-heat number

        Returns:
            New Session object
        """
        # Generate session ID based on timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if case_name and period:
            session_id = f"{case_name}_period{period}_{timestamp}"
        elif case_name:
            session_id = f"{case_name}_{timestamp}"
        else:
            session_id = f"session_{timestamp}"

        # Clean session ID (remove invalid characters)
        session_id = "".join(c if c.isalnum() or c in '_-' else '_' for c in session_id)

        # Create database path
        if SEPARATE_DB_PER_SESSION:
            db_path = SESSIONS_DIR / f"{session_id}.db"
        else:
            db_path = DATA_DIR / "rit_data.db"

        # Create session
        self.current_session = Session(session_id, db_path)
        self.current_session.case_name = case_name
        self.current_session.period = period

        # Initialize database
        self._init_session_db(db_path)

        # Add to index
        self.sessions_index['sessions'].append({
            'session_id': session_id,
            'db_path': str(db_path),
            'start_time': self.current_session.start_time.isoformat(),
            'case_name': case_name,
            'period': period
        })
        self._save_sessions_index()

        print(f"Created new session: {session_id}")
        print(f"Database: {db_path}")

        return self.current_session

    def _init_session_db(self, db_path: Path):
        """Initialize the session database with all tables."""
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Session metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_info (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                case_name TEXT,
                period INTEGER,
                start_time TEXT,
                end_time TEXT,
                total_ticks INTEGER,
                total_tenders INTEGER,
                securities TEXT,
                notes TEXT
            )
        """)

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
                quantity INTEGER,
                UNIQUE(ticker, tas_id)
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
                tender_type TEXT,
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

        # Connection log table for tracking connection events
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS connection_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT,
                success INTEGER,
                details TEXT
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_securities_ticker_tick ON securities(ticker, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_book_ticker_tick ON order_book(ticker, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_tender_id ON tenders(tender_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tas_ticker ON time_and_sales(ticker, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ohlc_ticker ON ohlc_history(ticker, period)")

        conn.commit()
        conn.close()

    def end_session(self):
        """End the current session and save metadata."""
        if self.current_session:
            self.current_session.end_time = datetime.now()

            # Update session info in database
            conn = sqlite3.connect(str(self.current_session.db_path))
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO session_info
                (id, session_id, case_name, period, start_time, end_time,
                 total_ticks, total_tenders, securities, notes)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.current_session.session_id,
                self.current_session.case_name,
                self.current_session.period,
                self.current_session.start_time.isoformat(),
                self.current_session.end_time.isoformat(),
                self.current_session.tick_count,
                self.current_session.tender_count,
                json.dumps(list(self.current_session.securities)),
                None
            ))

            conn.commit()
            conn.close()

            # Update index
            for s in self.sessions_index['sessions']:
                if s['session_id'] == self.current_session.session_id:
                    s['end_time'] = self.current_session.end_time.isoformat()
                    s['total_ticks'] = self.current_session.tick_count
                    s['total_tenders'] = self.current_session.tender_count
                    break
            self._save_sessions_index()

            print(f"Session ended: {self.current_session.session_id}")
            print(f"  Ticks: {self.current_session.tick_count}")
            print(f"  Tenders: {self.current_session.tender_count}")
            print(f"  Securities: {self.current_session.securities}")

    def get_session_db_path(self) -> Optional[Path]:
        """Get the current session's database path."""
        if self.current_session:
            return self.current_session.db_path
        return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all recorded sessions."""
        return self.sessions_index.get('sessions', [])

    def get_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get summary of a specific session."""
        for s in self.sessions_index['sessions']:
            if s['session_id'] == session_id:
                # Load additional info from database
                db_path = Path(s['db_path'])
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    cursor = conn.cursor()

                    # Get counts
                    cursor.execute("SELECT COUNT(*) FROM securities")
                    s['securities_records'] = cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM tenders")
                    s['tender_records'] = cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM order_book")
                    s['orderbook_records'] = cursor.fetchone()[0]

                    cursor.execute("SELECT DISTINCT ticker FROM securities")
                    s['tickers'] = [row[0] for row in cursor.fetchall()]

                    conn.close()

                return s
        return None


class SessionDataStore:
    """
    Data store that works with session-based storage.
    Wraps the database operations for a specific session.
    """

    def __init__(self, session: Session):
        self.session = session
        self.db_path = session.db_path

    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_timestamp(self) -> str:
        return datetime.now().isoformat()

    def save_case_info(self, data: Dict[str, Any], period: int = None, tick: int = None):
        conn = self._get_connection()
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

        # Update session
        if self.session:
            self.session.case_name = data.get('name')
            self.session.tick_count = max(self.session.tick_count, data.get('tick', 0))

    def save_trader_info(self, data: Dict[str, Any], period: int = None, tick: int = None):
        conn = self._get_connection()
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
        conn = self._get_connection()
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
        conn = self._get_connection()
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

            # Track securities in session
            if self.session and sec.get('ticker'):
                self.session.securities.add(sec.get('ticker'))

        conn.commit()
        conn.close()

    def save_order_book(self, ticker: str, book: Dict[str, Any], period: int = None, tick: int = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        timestamp = self._get_timestamp()

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
        conn = self._get_connection()
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
        conn = self._get_connection()
        cursor = conn.cursor()
        timestamp = self._get_timestamp()

        for trade in trades:
            try:
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
            except:
                pass

        conn.commit()
        conn.close()

    def save_tender(self, tender: Dict[str, Any], period: int = None, tick: int = None, status: str = 'ACTIVE'):
        conn = self._get_connection()
        cursor = conn.cursor()

        ticker = tender.get('ticker') or (tender.get('caption', '').split()[0] if tender.get('caption') else None)

        cursor.execute("""
            INSERT INTO tenders
            (timestamp, period, tick, tender_id, ticker, caption, quantity, action,
             is_fixed_bid, price, expires, tender_type, status, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            tender.get('type'),  # private, competitive, winner-take-all
            status,
            json.dumps(tender)
        ))

        conn.commit()
        conn.close()

        # Update session
        if self.session:
            self.session.tender_count += 1

    def save_news(self, news_items: List[Dict[str, Any]]):
        conn = self._get_connection()
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
            except:
                pass

        conn.commit()
        conn.close()

    def save_orders(self, orders: List[Dict[str, Any]], period: int = None, tick: int = None):
        conn = self._get_connection()
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

    def log_system_event(self, event_type: str, message: str, period: int = None,
                         tick: int = None, data: Any = None):
        conn = self._get_connection()
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

    def log_connection_event(self, event_type: str, message: str, success: bool, details: Any = None):
        """Log a connection event (health check, reconnect, etc.)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO connection_log (timestamp, event_type, message, success, details)
            VALUES (?, ?, ?, ?, ?)
        """, (
            self._get_timestamp(),
            event_type,
            message,
            1 if success else 0,
            json.dumps(details) if details else None
        ))
        conn.commit()
        conn.close()

    def get_last_news_id(self) -> int:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(news_id) FROM news")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result and result[0] else 0

    def get_last_tas_id(self, ticker: str) -> int:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(tas_id) FROM time_and_sales WHERE ticker = ?", (ticker,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result and result[0] else 0


def list_all_sessions():
    """Print all recorded sessions."""
    manager = SessionManager()
    sessions = manager.list_sessions()

    if not sessions:
        print("No sessions recorded yet.")
        return

    print("\n" + "=" * 70)
    print("RECORDED SESSIONS")
    print("=" * 70)

    for s in sessions:
        print(f"\nSession: {s['session_id']}")
        print(f"  Case: {s.get('case_name', 'N/A')}")
        print(f"  Period: {s.get('period', 'N/A')}")
        print(f"  Start: {s.get('start_time', 'N/A')}")
        print(f"  End: {s.get('end_time', 'In progress')}")
        print(f"  DB: {s.get('db_path', 'N/A')}")

        # Get more details
        details = manager.get_session_summary(s['session_id'])
        if details:
            print(f"  Records: {details.get('securities_records', 0):,} securities, "
                  f"{details.get('tender_records', 0)} tenders, "
                  f"{details.get('orderbook_records', 0):,} order book entries")
            print(f"  Tickers: {details.get('tickers', [])}")


if __name__ == "__main__":
    list_all_sessions()
