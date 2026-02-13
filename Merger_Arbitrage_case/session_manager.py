"""
Session Manager for Merger Arbitrage Case Data Collection.

Creates separate database files for each session (sub-heat).
Enhanced with deal spread tracking, news analysis, and probability history.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from config import (
    DATA_DIR, SESSIONS_DIR, SEPARATE_DB_PER_SESSION,
    COLLECTOR_ID, COLLECTOR_HOSTNAME, COLLECTOR_USERNAME, COLLECTOR_NAME,
    ANON_TRADER_IDS, SAVE_TICK_SNAPSHOTS, TRACK_PLAYER_ORDERS, DEALS
)


class Session:
    def __init__(self, session_id: str, db_path: Path):
        self.session_id = session_id
        self.db_path = db_path
        self.start_time = datetime.now()
        self.end_time = None
        self.case_name = None
        self.period = None
        self.securities = set()
        self.tick_count = 0
        self.collector_id = COLLECTOR_ID
        self.collector_name = COLLECTOR_NAME
        self.collector_hostname = COLLECTOR_HOSTNAME
        self.collector_username = COLLECTOR_USERNAME

    def to_dict(self) -> Dict[str, Any]:
        return {
            'session_id': self.session_id,
            'collector_id': self.collector_id,
            'db_path': str(self.db_path),
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'case_name': self.case_name,
            'period': self.period,
            'securities': list(self.securities),
            'tick_count': self.tick_count
        }


class SessionManager:
    def __init__(self):
        self.current_session: Optional[Session] = None
        self.sessions_index_path = SESSIONS_DIR / "sessions_index.json"
        self._load_sessions_index()

    def _load_sessions_index(self):
        if self.sessions_index_path.exists():
            with open(self.sessions_index_path, 'r') as f:
                self.sessions_index = json.load(f)
        else:
            self.sessions_index = {'sessions': []}

    def _save_sessions_index(self):
        with open(self.sessions_index_path, 'w') as f:
            json.dump(self.sessions_index, f, indent=2)

    def create_session(self, case_name: str = None, period: int = None) -> Session:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if case_name and period:
            session_id = f"{case_name}_period{period}_{timestamp}"
        elif case_name:
            session_id = f"{case_name}_{timestamp}"
        else:
            session_id = f"merger_arb_{timestamp}"

        session_id = "".join(c if c.isalnum() or c in '_-' else '_' for c in session_id)

        if SEPARATE_DB_PER_SESSION:
            db_path = SESSIONS_DIR / f"{session_id}.db"
        else:
            db_path = DATA_DIR / "merger_arb_data.db"

        self.current_session = Session(session_id, db_path)
        self.current_session.case_name = case_name
        self.current_session.period = period

        self._init_session_db(db_path)
        self._register_collector_instance(db_path)

        self.sessions_index['sessions'].append({
            'session_id': session_id,
            'collector_id': COLLECTOR_ID,
            'collector_name': COLLECTOR_NAME,
            'db_path': str(db_path),
            'start_time': self.current_session.start_time.isoformat(),
            'case_name': case_name,
            'period': period
        })
        self._save_sessions_index()

        print(f"[SESSION] Created: {session_id}")
        print(f"[SESSION] Database: {db_path}")
        return self.current_session

    def _register_collector_instance(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO collector_instances
            (collector_id, collector_name, hostname, username, start_time, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (COLLECTOR_ID, COLLECTOR_NAME, COLLECTOR_HOSTNAME,
              COLLECTOR_USERNAME, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def _init_session_db(self, db_path: Path):
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Collector instances
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS collector_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT NOT NULL,
                collector_name TEXT,
                hostname TEXT, username TEXT,
                start_time TEXT NOT NULL, end_time TEXT,
                polls_completed INTEGER DEFAULT 0,
                records_saved INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                UNIQUE(collector_id)
            )
        """)

        # Session info
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_info (
                id INTEGER PRIMARY KEY,
                session_id TEXT, collector_id TEXT,
                case_name TEXT, period INTEGER,
                start_time TEXT, end_time TEXT,
                total_ticks INTEGER, securities TEXT, notes TEXT
            )
        """)

        # Case info
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS case_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                case_name TEXT, period INTEGER, tick INTEGER,
                ticks_per_period INTEGER, total_periods INTEGER,
                status TEXT, is_enforce_trading_limits INTEGER,
                raw_json TEXT
            )
        """)

        # Trader info
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trader_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                trader_id TEXT, first_name TEXT, last_name TEXT,
                nlv REAL, raw_json TEXT
            )
        """)

        # Trading limits
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trading_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                limit_name TEXT,
                gross INTEGER, net INTEGER,
                gross_limit INTEGER, net_limit INTEGER,
                gross_fine REAL, net_fine REAL,
                raw_json TEXT
            )
        """)

        # Securities (all 10 tickers)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS securities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                ticker TEXT NOT NULL,
                security_type TEXT,
                position INTEGER, vwap REAL, nlv REAL,
                last_price REAL, bid REAL, bid_size INTEGER,
                ask REAL, ask_size INTEGER,
                volume INTEGER, total_volume INTEGER,
                unrealized REAL, realized REAL,
                spread REAL, mid_price REAL,
                raw_json TEXT
            )
        """)

        # Tick snapshots (one row per ticker per tick)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tick_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER NOT NULL, tick INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                last_price REAL, bid REAL, ask REAL,
                bid_size INTEGER, ask_size INTEGER,
                spread REAL, mid_price REAL,
                volume INTEGER, total_volume INTEGER,
                tick_volume INTEGER, vwap REAL,
                UNIQUE(collector_id, period, tick, ticker)
            )
        """)

        # =====================================================================
        # DEAL SPREADS TABLE - THE CORE OF MERGER ARB DATA
        # Tracks deal spread, implied probability, and deal value every tick
        # =====================================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deal_spreads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER NOT NULL, tick INTEGER NOT NULL,
                deal_id TEXT NOT NULL,
                target_ticker TEXT NOT NULL,
                acquirer_ticker TEXT NOT NULL,
                target_price REAL,
                acquirer_price REAL,
                deal_value REAL,
                standalone_value REAL,
                deal_spread REAL,
                deal_spread_pct REAL,
                implied_prob REAL,
                analyst_prob REAL,
                prob_diff REAL,
                structure TEXT,
                UNIQUE(collector_id, period, tick, deal_id)
            )
        """)

        # =====================================================================
        # NEWS TABLE - ENHANCED FOR MERGER ARB
        # Captures full news text plus parsed classification
        # =====================================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                news_id INTEGER NOT NULL,
                period INTEGER, tick INTEGER,
                ticker TEXT,
                headline TEXT,
                body TEXT,
                deal_id TEXT,
                news_category TEXT,
                news_direction TEXT,
                news_severity TEXT,
                estimated_delta_p REAL,
                raw_json TEXT,
                UNIQUE(news_id)
            )
        """)

        # =====================================================================
        # PROBABILITY HISTORY - Track deal probability over time
        # Updated after each news item
        # =====================================================================
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS probability_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                deal_id TEXT NOT NULL,
                prob_before REAL,
                prob_after REAL,
                delta_p REAL,
                trigger_type TEXT,
                trigger_news_id INTEGER,
                notes TEXT
            )
        """)

        # Order book
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_book (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL, level INTEGER NOT NULL,
                price REAL, quantity INTEGER,
                quantity_filled INTEGER,
                order_id INTEGER, trader_id TEXT,
                is_anon INTEGER DEFAULT 1
            )
        """)

        # Player limit orders
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_limit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL, quantity INTEGER,
                quantity_filled INTEGER,
                order_id INTEGER,
                trader_id TEXT NOT NULL,
                first_seen_tick INTEGER,
                last_seen_tick INTEGER,
                is_active INTEGER DEFAULT 1
            )
        """)

        # OHLC history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ohlc_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, ticker TEXT NOT NULL,
                history_tick INTEGER,
                open_price REAL, high_price REAL,
                low_price REAL, close_price REAL
            )
        """)

        # Time and sales
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS time_and_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                tas_id INTEGER,
                period INTEGER, tick INTEGER,
                price REAL, quantity INTEGER,
                UNIQUE(collector_id, ticker, tas_id)
            )
        """)

        # Tenders (MA case may have some)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                tender_id INTEGER NOT NULL,
                ticker TEXT, caption TEXT,
                quantity INTEGER, action TEXT,
                is_fixed_bid INTEGER, price REAL,
                expires INTEGER, tender_type TEXT,
                status TEXT, raw_json TEXT
            )
        """)

        # Own orders
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                order_id INTEGER NOT NULL,
                trader_id TEXT, ticker TEXT,
                order_type TEXT, quantity INTEGER,
                action TEXT, price REAL,
                quantity_filled INTEGER, vwap REAL,
                status TEXT, raw_json TEXT
            )
        """)

        # System events
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                period INTEGER, tick INTEGER,
                message TEXT, data TEXT
            )
        """)

        # Connection log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS connection_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector_id TEXT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT, success INTEGER, details TEXT
            )
        """)

        # =====================================================================
        # INDEXES
        # =====================================================================
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_securities_ticker_tick ON securities(ticker, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_securities_period_tick ON securities(period, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tick_snapshots_period_tick ON tick_snapshots(period, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tick_snapshots_ticker ON tick_snapshots(ticker, period, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_deal_spreads_deal ON deal_spreads(deal_id, period, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_deal_spreads_tick ON deal_spreads(period, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_deal ON news(deal_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_tick ON news(period, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_category ON news(news_category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prob_history_deal ON probability_history(deal_id, period, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_book_ticker_tick ON order_book(ticker, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_book_not_anon ON order_book(ticker, tick) WHERE is_anon = 0")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_orders_trader ON player_limit_orders(trader_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tas_ticker ON time_and_sales(ticker, tick)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenders_id ON tenders(tender_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ohlc_ticker ON ohlc_history(ticker, period)")

        conn.commit()
        conn.close()

    def end_session(self, polls_completed: int = 0, records_saved: int = 0):
        if self.current_session:
            self.current_session.end_time = datetime.now()
            conn = sqlite3.connect(str(self.current_session.db_path))
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO session_info
                (id, session_id, collector_id, case_name, period, start_time, end_time,
                 total_ticks, securities, notes)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.current_session.session_id, self.current_session.collector_id,
                self.current_session.case_name, self.current_session.period,
                self.current_session.start_time.isoformat(),
                self.current_session.end_time.isoformat(),
                self.current_session.tick_count,
                json.dumps(list(self.current_session.securities)), None
            ))
            cursor.execute("""
                UPDATE collector_instances
                SET end_time = ?, is_active = 0, polls_completed = ?, records_saved = ?
                WHERE collector_id = ?
            """, (self.current_session.end_time.isoformat(),
                  polls_completed, records_saved, self.current_session.collector_id))
            conn.commit()
            conn.close()

            for s in self.sessions_index['sessions']:
                if s['session_id'] == self.current_session.session_id:
                    s['end_time'] = self.current_session.end_time.isoformat()
                    break
            self._save_sessions_index()
            print(f"[SESSION] Ended: {self.current_session.session_id}")


class SessionDataStore:
    """
    Data store with both standalone and batched (_conn) write methods.
    Standalone methods open/commit/close their own connection.
    _conn methods use a provided connection for batched writes (single commit).
    """

    def __init__(self, session: Session):
        self.session = session
        self.db_path = session.db_path
        self.collector_id = session.collector_id
        self._last_volumes: Dict[str, int] = {}

    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ts(self) -> str:
        return datetime.now().isoformat()

    def flush(self):
        """No-op for now; all writes are immediate."""
        pass

    # =====================================================================
    # BATCHED _conn methods (used by optimized collector - no commit)
    # =====================================================================

    def _save_case_info_conn(self, conn, data: Dict[str, Any], period: int, tick: int):
        conn.execute("""
            INSERT INTO case_info
            (collector_id, timestamp, case_name, period, tick, ticks_per_period,
             total_periods, status, is_enforce_trading_limits, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (self.collector_id, self._ts(), data.get('name'),
              data.get('period', period), data.get('tick', tick),
              data.get('ticks_per_period'), data.get('total_periods'),
              data.get('status'), 1 if data.get('is_enforce_trading_limits') else 0,
              json.dumps(data)))
        if self.session:
            self.session.case_name = data.get('name')
            self.session.tick_count = max(self.session.tick_count, data.get('tick', 0))

    def _save_trader_info_conn(self, conn, data: Dict[str, Any], period: int, tick: int):
        conn.execute("""
            INSERT INTO trader_info
            (collector_id, timestamp, period, tick, trader_id,
             first_name, last_name, nlv, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (self.collector_id, self._ts(), period, tick,
              data.get('trader_id'), data.get('first_name'),
              data.get('last_name'), data.get('nlv'), json.dumps(data)))

    def _save_trading_limits_conn(self, conn, data: list, period: int, tick: int):
        ts = self._ts()
        for limit in data:
            conn.execute("""
                INSERT INTO trading_limits
                (collector_id, timestamp, period, tick, limit_name,
                 gross, net, gross_limit, net_limit, gross_fine, net_fine, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (self.collector_id, ts, period, tick,
                  limit.get('name'), limit.get('gross'), limit.get('net'),
                  limit.get('gross_limit'), limit.get('net_limit'),
                  limit.get('gross_fine'), limit.get('net_fine'),
                  json.dumps(limit)))

    def _save_securities_conn(self, conn, data: list, period: int, tick: int):
        ts = self._ts()
        for sec in data:
            bid = sec.get('bid')
            ask = sec.get('ask')
            spread = round(ask - bid, 4) if bid and ask else None
            mid = round((bid + ask) / 2, 4) if bid and ask else None

            conn.execute("""
                INSERT INTO securities
                (collector_id, timestamp, period, tick, ticker, security_type,
                 position, vwap, nlv, last_price, bid, bid_size, ask, ask_size,
                 volume, total_volume, unrealized, realized, spread, mid_price, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (self.collector_id, ts, period, tick,
                  sec.get('ticker'), sec.get('type'),
                  sec.get('position'), sec.get('vwap'), sec.get('nlv'),
                  sec.get('last'), sec.get('bid'), sec.get('bid_size'),
                  sec.get('ask'), sec.get('ask_size'),
                  sec.get('volume'), sec.get('total_volume'),
                  sec.get('unrealized'), sec.get('realized'),
                  spread, mid, json.dumps(sec)))

            if SAVE_TICK_SNAPSHOTS:
                ticker = sec.get('ticker')
                total_vol = sec.get('total_volume', 0) or 0
                last_total = self._last_volumes.get(ticker, total_vol)
                tick_vol = max(0, total_vol - last_total)
                self._last_volumes[ticker] = total_vol
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO tick_snapshots
                        (collector_id, timestamp, period, tick, ticker,
                         last_price, bid, ask, bid_size, ask_size,
                         spread, mid_price, volume, total_volume, tick_volume, vwap)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (self.collector_id, ts, period, tick, ticker,
                          sec.get('last'), bid, ask,
                          sec.get('bid_size'), sec.get('ask_size'),
                          spread, mid, sec.get('volume'), total_vol,
                          tick_vol, sec.get('vwap')))
                except:
                    pass

            if self.session:
                self.session.securities.add(sec.get('ticker'))

    def _save_deal_spread_conn(self, conn, deal_data: Dict[str, Any], period: int, tick: int):
        try:
            conn.execute("""
                INSERT OR REPLACE INTO deal_spreads
                (collector_id, timestamp, period, tick, deal_id,
                 target_ticker, acquirer_ticker,
                 target_price, acquirer_price,
                 deal_value, standalone_value,
                 deal_spread, deal_spread_pct,
                 implied_prob, analyst_prob, prob_diff, structure)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (self.collector_id, self._ts(), period, tick,
                  deal_data['deal_id'], deal_data['target_ticker'],
                  deal_data['acquirer_ticker'],
                  deal_data['target_price'], deal_data['acquirer_price'],
                  deal_data['deal_value'], deal_data['standalone_value'],
                  deal_data['deal_spread'], deal_data['deal_spread_pct'],
                  deal_data['implied_prob'], deal_data['analyst_prob'],
                  deal_data['prob_diff'], deal_data['structure']))
        except:
            pass

    def _save_order_book_conn(self, conn, ticker: str, data: Dict[str, Any],
                               period: int, tick: int):
        ts = self._ts()
        for side_name, side_key in [('BID', 'bids'), ('ASK', 'asks')]:
            entries = data.get(side_key, [])
            for level, entry in enumerate(entries):
                trader_id = entry.get('trader_id', '')
                is_anon = 1 if trader_id in ANON_TRADER_IDS else 0
                conn.execute("""
                    INSERT INTO order_book
                    (collector_id, timestamp, period, tick, ticker, side, level,
                     price, quantity, quantity_filled, order_id, trader_id, is_anon)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (self.collector_id, ts, period, tick, ticker, side_name, level,
                      entry.get('price'), entry.get('quantity'),
                      entry.get('quantity_filled'), entry.get('order_id'),
                      trader_id, is_anon))

                if TRACK_PLAYER_ORDERS and not is_anon and trader_id:
                    try:
                        conn.execute("""
                            INSERT INTO player_limit_orders
                            (collector_id, timestamp, period, tick, ticker, side,
                             price, quantity, quantity_filled, order_id, trader_id,
                             first_seen_tick, last_seen_tick, is_active)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                        """, (self.collector_id, ts, period, tick, ticker, side_name,
                              entry.get('price'), entry.get('quantity'),
                              entry.get('quantity_filled'), entry.get('order_id'),
                              trader_id, tick, tick))
                    except:
                        pass

    def _save_time_and_sales_conn(self, conn, ticker: str, data: list):
        ts = self._ts()
        for trade in data:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO time_and_sales
                    (collector_id, timestamp, ticker, tas_id, period, tick, price, quantity)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (self.collector_id, ts, ticker, trade.get('id'),
                      trade.get('period'), trade.get('tick'),
                      trade.get('price'), trade.get('quantity')))
            except:
                pass

    def _save_tender_conn(self, conn, tender: Dict[str, Any], period: int,
                           tick: int, status: str):
        conn.execute("""
            INSERT INTO tenders
            (collector_id, timestamp, period, tick, tender_id, ticker, caption,
             quantity, action, is_fixed_bid, price, expires, tender_type, status, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (self.collector_id, self._ts(), period, tick,
              tender.get('tender_id'), tender.get('ticker'), tender.get('caption'),
              tender.get('quantity'), tender.get('action'),
              1 if tender.get('is_fixed_bid') else 0,
              tender.get('price'), tender.get('expires'),
              tender.get('type'), status, json.dumps(tender)))

    def _save_orders_conn(self, conn, data: list, period: int, tick: int):
        ts = self._ts()
        for order in data:
            conn.execute("""
                INSERT INTO orders
                (collector_id, timestamp, period, tick, order_id, trader_id, ticker,
                 order_type, quantity, action, price, quantity_filled, vwap, status, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (self.collector_id, ts, period, tick,
                  order.get('order_id'), order.get('trader_id'),
                  order.get('ticker'), order.get('type'),
                  order.get('quantity'), order.get('action'),
                  order.get('price'), order.get('quantity_filled'),
                  order.get('vwap'), order.get('status'), json.dumps(order)))

    def _save_ohlc_history_conn(self, conn, ticker: str, data: list, period: int):
        ts = self._ts()
        for candle in data:
            try:
                conn.execute("""
                    INSERT INTO ohlc_history
                    (collector_id, timestamp, period, ticker, history_tick,
                     open_price, high_price, low_price, close_price)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (self.collector_id, ts, period, ticker,
                      candle.get('tick'), candle.get('open'),
                      candle.get('high'), candle.get('low'), candle.get('close')))
            except:
                pass

    # =====================================================================
    # STANDALONE methods (used for one-off writes like news, prob updates)
    # =====================================================================

    def save_news(self, data: list):
        conn = self._get_connection()
        for news in data:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO news
                    (collector_id, timestamp, news_id, period, tick,
                     ticker, headline, body, deal_id,
                     news_category, news_direction, news_severity,
                     estimated_delta_p, raw_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (self.collector_id, self._ts(),
                      news.get('news_id'), news.get('period'), news.get('tick'),
                      news.get('ticker'), news.get('headline'), news.get('body'),
                      news.get('_deal_id'), news.get('_category'),
                      news.get('_direction'), news.get('_severity'),
                      news.get('_delta_p'),
                      json.dumps(news)))
            except:
                pass
        conn.commit()
        conn.close()

    def save_probability_update(self, deal_id: str, period: int, tick: int,
                                prob_before: float, prob_after: float,
                                trigger_type: str, news_id: int = None,
                                notes: str = None):
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO probability_history
            (collector_id, timestamp, period, tick, deal_id,
             prob_before, prob_after, delta_p, trigger_type,
             trigger_news_id, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (self.collector_id, self._ts(), period, tick, deal_id,
              prob_before, prob_after, prob_after - prob_before,
              trigger_type, news_id, notes))
        conn.commit()
        conn.close()

    def log_system_event(self, event_type: str, message: str,
                         period: int = None, tick: int = None, data: Any = None):
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO system_events
            (collector_id, timestamp, event_type, period, tick, message, data)
            VALUES (?,?,?,?,?,?,?)
        """, (self.collector_id, self._ts(), event_type, period, tick,
              message, json.dumps(data) if data else None))
        conn.commit()
        conn.close()

    def log_connection_event(self, event_type: str, message: str, success: bool):
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO connection_log
            (collector_id, timestamp, event_type, message, success)
            VALUES (?,?,?,?,?)
        """, (self.collector_id, self._ts(), event_type, message, 1 if success else 0))
        conn.commit()
        conn.close()
