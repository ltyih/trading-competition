"""
Main Data Collector for RIT Liquidity Risk Case

Continuously collects all available data from the RIT API and stores it
in a SQLite database for later analysis.

Now supports session-based storage - each case run gets its own database file.
"""
import logging
import time
import signal
import sys
from datetime import datetime
from typing import Dict, Any, Set, Optional
from pathlib import Path

from config import (
    POLL_INTERVAL_SEC, BOOK_DEPTH_LIMIT, NEWS_LIMIT,
    HEALTH_CHECK_INTERVAL_SEC, KNOWN_SECURITIES,
    DATA_DIR, CSV_EXPORT_INTERVAL_SEC, EXPORT_CSV,
    SEPARATE_DB_PER_SESSION,
    COLLECTOR_ID, COLLECTOR_HOSTNAME, COLLECTOR_USERNAME, COLLECTOR_NAME,
    SAVE_TICK_SNAPSHOTS, TRACK_PLAYER_ORDERS
)
from session_manager import SessionManager, SessionDataStore
from rit_client import *REMOVED*lient, *REMOVED*lientManager

logger = logging.getLogger(__name__)


class DataCollector:
    """
    Main data collector that continuously polls the RIT API
    and stores all data in the database.

    Now supports session-based storage - each case run gets its own database.
    """

    def __init__(self, api_key: str = None):
        self.client = *REMOVED*lient(api_key)
        self.rit_manager = *REMOVED*lientManager(api_key)

        # Session management
        self.session_manager = SessionManager()
        self.data_store = None  # Will be set when session starts
        self.current_session = None

        self.running = False
        self.current_tick = 0
        self.current_period = 0
        self.last_period = 0  # Track period changes for new sessions
        self.current_status = "STOPPED"
        self.case_name = None

        # Track what securities are active in current case
        self.active_securities: Set[str] = set()

        # Track last known IDs for incremental fetches
        self.last_news_id = 0
        self.last_tas_ids: Dict[str, int] = {}

        # Track seen tenders to avoid duplicates
        self.seen_tender_ids: Set[int] = set()

        # Last OHLC fetch tick per security
        self.last_ohlc_tick: Dict[str, int] = {}

        # Statistics
        self.stats = {
            'polls': 0,
            'securities_saved': 0,
            'order_books_saved': 0,
            'tick_snapshots_saved': 0,
            'player_orders_tracked': 0,
            'tenders_saved': 0,
            'news_saved': 0,
            'tas_saved': 0,
            'errors': 0,
            'start_time': None,
            'collector_id': COLLECTOR_ID
        }

        # CSV export tracking
        self.last_csv_export = datetime.now()

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info("Shutdown signal received, stopping collector...")
        self.running = False

    def _start_new_session(self, case_name: str = None, period: int = None):
        """Start a new data collection session."""
        # End previous session if exists
        if self.current_session:
            self.session_manager.end_session()

        # Create new session
        self.current_session = self.session_manager.create_session(case_name, period)
        self.data_store = SessionDataStore(self.current_session)

        # Reset tracking for new session
        self.last_news_id = 0
        self.last_tas_ids = {}
        self.seen_tender_ids = set()

        logger.info(f"Started new session: {self.current_session.session_id}")

    def start(self):
        """Start the data collection loop."""
        self.running = True
        self.stats['start_time'] = datetime.now()
        self.setup_signal_handlers()

        logger.info("=" * 60)
        logger.info("RIT Data Collector Starting (Enhanced Edition)")
        logger.info("=" * 60)
        logger.info(f"Collector ID: {COLLECTOR_ID}")
        logger.info(f"Collector Name: {COLLECTOR_NAME or 'Auto-generated'}")
        logger.info(f"Hostname: {COLLECTOR_HOSTNAME}")
        logger.info(f"Username: {COLLECTOR_USERNAME}")
        logger.info("=" * 60)
        logger.info(f"Poll interval: {POLL_INTERVAL_SEC}s")
        logger.info(f"Book depth: {BOOK_DEPTH_LIMIT}")
        logger.info(f"Session storage: {SEPARATE_DB_PER_SESSION}")
        logger.info(f"Tick snapshots: {SAVE_TICK_SNAPSHOTS}")
        logger.info(f"Player order tracking: {TRACK_PLAYER_ORDERS}")
        logger.info("=" * 60)

        # Wait for initial connection
        logger.info("Waiting for RIT Client connection...")

        while self.running and not self.client.health_check():
            logger.info("Waiting for RIT Client to be ready...")
            time.sleep(5)

        if not self.running:
            return

        logger.info("Connected! Starting data collection...")

        # Main collection loop
        last_health_check = time.time()

        # Track consecutive connection failures for backoff
        connection_failures = 0

        while self.running:
            try:
                # Periodic health check
                if time.time() - last_health_check > HEALTH_CHECK_INTERVAL_SEC:
                    if not self.client.health_check():
                        connection_failures += 1
                        logger.warning(f"Health check failed (attempt {connection_failures}), attempting reconnect...")
                        if self.data_store:
                            self.data_store.log_connection_event("HEALTH_CHECK_FAILED",
                                f"Health check failed (attempt {connection_failures})", False)

                        # Exponential backoff for connection failures (max 2 minutes)
                        backoff_time = min(5 * (2 ** min(connection_failures - 1, 5)), 120)
                        logger.info(f"Waiting {backoff_time}s before retry...")
                        time.sleep(backoff_time)
                        continue
                    else:
                        # Reset failure counter on successful health check
                        if connection_failures > 0:
                            logger.info(f"Connection restored after {connection_failures} failures")
                            if self.data_store:
                                self.data_store.log_connection_event("CONNECTION_RESTORED",
                                    f"Restored after {connection_failures} failures", True)
                        connection_failures = 0
                    last_health_check = time.time()

                # Collect all data
                self._collect_cycle()

                # Export to CSV periodically
                if EXPORT_CSV:
                    if (datetime.now() - self.last_csv_export).total_seconds() > CSV_EXPORT_INTERVAL_SEC:
                        self._export_to_csv()
                        self.last_csv_export = datetime.now()

                # Sleep before next cycle
                time.sleep(POLL_INTERVAL_SEC)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                self.running = False

            except Exception as e:
                self.stats['errors'] += 1
                logger.error(f"Error in collection cycle: {e}")
                if self.data_store:
                    self.data_store.log_system_event("ERROR", str(e),
                        self.current_period, self.current_tick)
                # Brief pause on error before continuing
                time.sleep(2)

        # Cleanup
        self._shutdown()

    def _collect_cycle(self):
        """Perform one complete data collection cycle."""
        self.stats['polls'] += 1

        # 1. Get case info first (updates tick/period, may start new session)
        self._collect_case_info()

        # Skip if case is not active or no data store yet
        if self.current_status not in ('ACTIVE', 'RUNNING'):
            if self.stats['polls'] % 10 == 0:  # Log periodically
                logger.info(f"Case status: {self.current_status}, waiting...")
            return

        if not self.data_store:
            logger.debug("No data store available yet, waiting for session to start...")
            return

        # 2. Get trader info
        self._collect_trader_info()

        # 3. Get trading limits
        self._collect_limits()

        # 4. Get securities data
        self._collect_securities()

        # 5. Get order books for active securities
        self._collect_order_books()

        # 6. Get tenders
        self._collect_tenders()

        # 7. Get news
        self._collect_news()

        # 8. Get time & sales for active securities
        self._collect_time_and_sales()

        # 9. Get OHLC history (less frequently)
        if self.stats['polls'] % 10 == 0:  # Every 10 polls
            self._collect_ohlc_history()

        # 10. Get orders
        self._collect_orders()

        # Log progress periodically
        if self.stats['polls'] % 20 == 0:
            self._log_stats()

    def _collect_case_info(self):
        """Collect and save case information."""
        success, data = self.client.get_case()

        if success:
            new_tick = data.get('tick', 0)
            new_period = data.get('period', 0)
            new_status = data.get('status', 'UNKNOWN')
            new_case_name = data.get('name', 'Unknown')

            # Check if we need to start a new session
            # New session when: first run, period changes, or case restarts (tick goes back to 0)
            should_start_new_session = (
                self.data_store is None or
                new_period != self.last_period or
                (self.current_tick > 0 and new_tick == 0) or
                self.case_name != new_case_name
            )

            if should_start_new_session and new_status in ('ACTIVE', 'RUNNING'):
                logger.info(f"Detected new session: Case={new_case_name}, Period={new_period}")
                self._start_new_session(new_case_name, new_period)

            self.current_tick = new_tick
            self.current_period = new_period
            self.last_period = new_period
            self.current_status = new_status
            self.case_name = new_case_name

            # Save case info if we have a data store
            if self.data_store:
                self.data_store.save_case_info(data, self.current_period, self.current_tick)

    def _collect_trader_info(self):
        """Collect and save trader information."""
        success, data = self.client.get_trader()

        if success:
            self.data_store.save_trader_info(data, self.current_period, self.current_tick)

    def _collect_limits(self):
        """Collect and save trading limits."""
        success, data = self.client.get_limits()

        if success and data:
            self.data_store.save_trading_limits(data, self.current_period, self.current_tick)

    def _collect_securities(self):
        """Collect and save securities data."""
        success, data = self.client.get_securities()

        if success and data:
            # Update active securities set
            self.active_securities = {sec.get('ticker') for sec in data if sec.get('is_tradeable')}

            self.data_store.save_securities(data, self.current_period, self.current_tick)
            self.stats['securities_saved'] += len(data)

    def _collect_order_books(self):
        """Collect and save order books for all active securities."""
        for ticker in self.active_securities:
            success, data = self.client.get_order_book(ticker, BOOK_DEPTH_LIMIT)

            if success and data:
                self.data_store.save_order_book(ticker, data,
                    self.current_period, self.current_tick)
                self.stats['order_books_saved'] += 1

    def _collect_tenders(self):
        """Collect and save tender offers."""
        success, data = self.client.get_tenders()

        if success and data:
            for tender in data:
                tender_id = tender.get('tender_id')

                # Save if we haven't seen this tender before
                if tender_id not in self.seen_tender_ids:
                    self.data_store.save_tender(tender,
                        self.current_period, self.current_tick, 'ACTIVE')
                    self.seen_tender_ids.add(tender_id)
                    self.stats['tenders_saved'] += 1

                    # Log tender for immediate visibility
                    logger.info(f"NEW TENDER: ID={tender_id}, "
                               f"Ticker={tender.get('caption', tender.get('ticker'))}, "
                               f"Qty={tender.get('quantity')}, "
                               f"Action={tender.get('action')}, "
                               f"Price={tender.get('price')}, "
                               f"Expires={tender.get('expires')}")

    def _collect_news(self):
        """Collect and save news items."""
        success, data = self.client.get_news(since=self.last_news_id, limit=NEWS_LIMIT)

        if success and data:
            self.data_store.save_news(data)

            # Update last news ID
            if data:
                max_id = max(item.get('news_id', 0) for item in data)
                if max_id > self.last_news_id:
                    self.last_news_id = max_id
                    self.stats['news_saved'] += len(data)

                    # Log new news
                    for news in data:
                        logger.info(f"NEWS: {news.get('headline')}")

    def _collect_time_and_sales(self):
        """Collect time & sales data for active securities."""
        for ticker in self.active_securities:
            last_id = self.last_tas_ids.get(ticker, 0)

            success, data = self.client.get_time_and_sales(ticker, after=last_id)

            if success and data:
                self.data_store.save_time_and_sales(ticker, data)

                # Update last ID
                if data:
                    max_id = max(trade.get('id', 0) for trade in data)
                    if max_id > last_id:
                        self.last_tas_ids[ticker] = max_id
                        self.stats['tas_saved'] += len(data)

    def _collect_ohlc_history(self):
        """Collect OHLC price history."""
        for ticker in self.active_securities:
            # Only fetch new data (last 10 ticks)
            success, data = self.client.get_securities_history(
                ticker, period=self.current_period, limit=10)

            if success and data:
                self.data_store.save_ohlc_history(ticker, data, self.current_period)

    def _collect_orders(self):
        """Collect order information."""
        # Get open orders
        success, data = self.client.get_orders(status='OPEN')
        if success and data:
            self.data_store.save_orders(data, self.current_period, self.current_tick)

        # Get recent transacted orders
        success, data = self.client.get_orders(status='TRANSACTED')
        if success and data:
            self.data_store.save_orders(data, self.current_period, self.current_tick)

    def _log_stats(self):
        """Log collection statistics."""
        runtime = datetime.now() - self.stats['start_time'] if self.stats['start_time'] else None
        runtime_str = str(runtime).split('.')[0] if runtime else "N/A"

        logger.info(f"Stats - Tick: {self.current_tick}, Period: {self.current_period}, "
                   f"Status: {self.current_status}, Runtime: {runtime_str}")
        logger.info(f"  Polls: {self.stats['polls']}, Securities: {self.stats['securities_saved']}, "
                   f"Books: {self.stats['order_books_saved']}, Tenders: {self.stats['tenders_saved']}, "
                   f"News: {self.stats['news_saved']}, Errors: {self.stats['errors']}")

    def _export_to_csv(self):
        """Export recent data to CSV files for easy analysis."""
        try:
            from models import get_connection
            import pandas as pd

            conn = get_connection()
            export_dir = DATA_DIR / "csv_exports"
            export_dir.mkdir(exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Export securities data (last 1000 rows)
            df = pd.read_sql_query(
                "SELECT * FROM securities ORDER BY id DESC LIMIT 1000", conn)
            if not df.empty:
                df.to_csv(export_dir / f"securities_{timestamp}.csv", index=False)

            # Export tenders
            df = pd.read_sql_query("SELECT * FROM tenders ORDER BY id DESC LIMIT 500", conn)
            if not df.empty:
                df.to_csv(export_dir / f"tenders_{timestamp}.csv", index=False)

            # Export news
            df = pd.read_sql_query("SELECT * FROM news ORDER BY id DESC LIMIT 200", conn)
            if not df.empty:
                df.to_csv(export_dir / f"news_{timestamp}.csv", index=False)

            conn.close()
            logger.info(f"Exported data to CSV at {export_dir}")

        except Exception as e:
            logger.error(f"CSV export failed: {e}")

    def _shutdown(self):
        """Perform cleanup on shutdown."""
        logger.info("Shutting down data collector...")
        logger.info(f"Collector ID: {COLLECTOR_ID}")

        if self.data_store:
            self.data_store.log_system_event("SHUTDOWN", "Collector stopped",
                self.current_period, self.current_tick, self.stats)

        # Calculate total records saved
        total_records = (
            self.stats['securities_saved'] +
            self.stats['order_books_saved'] +
            self.stats['tenders_saved'] +
            self.stats['news_saved'] +
            self.stats['tas_saved']
        )

        # End current session with stats
        if self.current_session:
            self.session_manager.end_session(
                polls_completed=self.stats['polls'],
                records_saved=total_records
            )

        # Final CSV export
        if EXPORT_CSV:
            self._export_to_csv()

        self._log_stats()
        logger.info("Data collector stopped.")


class TenderMonitor:
    """
    Specialized monitor for tender offers.

    Provides real-time alerts and analysis of incoming tenders.
    """

    def __init__(self, client: *REMOVED*lient, data_store: SessionDataStore):
        self.client = client
        self.data_store = data_store
        self.active_tenders: Dict[int, Dict[str, Any]] = {}

    def check_tenders(self, current_tick: int) -> list:
        """
        Check for new tenders and return alerts.

        Returns list of new/updated tender alerts.
        """
        alerts = []

        success, tenders = self.client.get_tenders()
        if not success:
            return alerts

        current_ids = set()

        for tender in tenders:
            tender_id = tender.get('tender_id')
            current_ids.add(tender_id)

            if tender_id not in self.active_tenders:
                # New tender
                self.active_tenders[tender_id] = tender
                alerts.append({
                    'type': 'NEW_TENDER',
                    'tender': tender,
                    'message': self._format_tender_alert(tender)
                })

            # Check for expiring tenders
            expires = tender.get('expires', 0)
            if expires > 0 and expires - current_tick <= 5:
                alerts.append({
                    'type': 'EXPIRING_TENDER',
                    'tender': tender,
                    'message': f"Tender {tender_id} expiring in {expires - current_tick} ticks!"
                })

        # Check for expired/removed tenders
        expired = set(self.active_tenders.keys()) - current_ids
        for tender_id in expired:
            alerts.append({
                'type': 'TENDER_EXPIRED',
                'tender': self.active_tenders[tender_id],
                'message': f"Tender {tender_id} has expired/been handled"
            })
            del self.active_tenders[tender_id]

        return alerts

    def _format_tender_alert(self, tender: Dict[str, Any]) -> str:
        """Format a tender for display."""
        return (f"TENDER #{tender.get('tender_id')}: "
                f"{tender.get('action')} {tender.get('quantity'):,} "
                f"{tender.get('caption', tender.get('ticker', 'UNKNOWN'))} "
                f"@ ${tender.get('price', 'N/A')} "
                f"(expires: tick {tender.get('expires')})")


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('data_collector.log')
        ]
    )

    # Note: You need to provide your API key
    # Get it from the RIT Client by clicking on the API icon
    API_KEY = None  # Set your API key here

    collector = DataCollector(api_key=API_KEY)
    collector.start()
