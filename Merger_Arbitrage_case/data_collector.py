"""
Main Data Collector for RIT Merger Arbitrage Case - OPTIMIZED.

Uses threading for parallel API calls and batched DB writes to achieve
sub-500ms cycle times. Prioritizes news collection (the primary signal).
"""
import logging
import time
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, Set, List, Tuple

from config import (
    POLL_INTERVAL_SEC, BOOK_DEPTH_LIMIT, NEWS_LIMIT, TAS_LIMIT,
    HEALTH_CHECK_INTERVAL_SEC, KNOWN_SECURITIES, DEALS,
    COLLECTOR_ID, COLLECTOR_HOSTNAME, COLLECTOR_USERNAME, COLLECTOR_NAME,
    SAVE_TICK_SNAPSHOTS, TRACK_PLAYER_ORDERS, TRACK_DEAL_SPREADS,
    SEPARATE_DB_PER_SESSION, EXPORT_CSV, CSV_EXPORT_INTERVAL_SEC, DATA_DIR
)
from session_manager import SessionManager, SessionDataStore
from rit_client import *REMOVED*lient, *REMOVED*lientManager
from deal_analyzer import DealAnalyzer

logger = logging.getLogger(__name__)

# Number of threads for parallel API calls
API_THREAD_POOL_SIZE = 6


class DataCollector:
    """
    Optimized data collector for the Merger Arbitrage case.
    Uses ThreadPoolExecutor for parallel API calls and batched DB writes.
    """

    def __init__(self, api_key: str = None):
        self.client = *REMOVED*lient(api_key)
        self.rit_manager = *REMOVED*lientManager(api_key)

        # Session management
        self.session_manager = SessionManager()
        self.data_store: SessionDataStore = None
        self.current_session = None

        # Deal analyzer
        self.deal_analyzer = DealAnalyzer()

        # Thread pool for parallel API calls
        self.executor = ThreadPoolExecutor(max_workers=API_THREAD_POOL_SIZE)

        self.running = False
        self.current_tick = 0
        self.current_period = 0
        self.last_period = 0
        self.current_status = "STOPPED"
        self.case_name = None

        # Active securities from API
        self.active_securities: Set[str] = set()

        # Latest prices for deal spread calculation
        self.latest_prices: Dict[str, float] = {}

        # Incremental fetch tracking
        self.last_news_id = 0
        self.last_tas_ids: Dict[str, int] = {}
        self.seen_tender_ids: Set[int] = set()

        # Track last tick we saved to avoid duplicate writes on same tick
        self.last_saved_tick = -1

        # Statistics
        self.stats = {
            'polls': 0,
            'ticks_captured': 0,
            'securities_saved': 0,
            'order_books_saved': 0,
            'deal_spreads_saved': 0,
            'tenders_saved': 0,
            'news_saved': 0,
            'news_classified': 0,
            'tas_saved': 0,
            'prob_updates': 0,
            'errors': 0,
            'avg_cycle_ms': 0,
            'start_time': None,
            'collector_id': COLLECTOR_ID,
        }
        self._cycle_times: List[float] = []

    def setup_signal_handlers(self):
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received, stopping collector...")
        self.running = False

    def _start_new_session(self, case_name: str = None, period: int = None):
        if self.current_session:
            self._flush_and_end_session()

        # Reset deal analyzer for new session/heat
        self.deal_analyzer = DealAnalyzer()

        self.current_session = self.session_manager.create_session(case_name, period)
        self.data_store = SessionDataStore(self.current_session)

        # Reset tracking
        self.last_news_id = 0
        self.last_tas_ids = {}
        self.seen_tender_ids = set()
        self.latest_prices = {}
        self.last_saved_tick = -1

        logger.info(f"Started new session: {self.current_session.session_id}")

    def _flush_and_end_session(self):
        """Flush pending DB writes and end session."""
        if self.data_store:
            self.data_store.flush()
        total_records = sum(self.stats[k] for k in [
            'securities_saved', 'order_books_saved', 'deal_spreads_saved',
            'tenders_saved', 'news_saved', 'tas_saved'])
        self.session_manager.end_session(
            polls_completed=self.stats['polls'],
            records_saved=total_records)

    def start(self):
        """Start the data collection loop."""
        self.running = True
        self.stats['start_time'] = datetime.now()
        self.setup_signal_handlers()

        logger.info("=" * 60)
        logger.info("MERGER ARBITRAGE Data Collector Starting (OPTIMIZED)")
        logger.info("=" * 60)
        logger.info(f"Collector ID: {COLLECTOR_ID}")
        logger.info(f"Poll interval: {POLL_INTERVAL_SEC}s | Threads: {API_THREAD_POOL_SIZE}")
        logger.info(f"Deals monitored: {len(DEALS)}")
        for did, d in DEALS.items():
            logger.info(f"  {did}: {d['target']}/{d['acquirer']} ({d['structure']}) "
                         f"p0={d['initial_prob']:.0%}")
        logger.info("=" * 60)

        # Wait for connection
        logger.info("Waiting for RIT Client connection...")
        while self.running and not self.client.health_check():
            logger.info("Waiting for RIT Client to be ready...")
            time.sleep(5)

        if not self.running:
            return

        logger.info("Connected! Starting data collection...")

        last_health_check = time.time()
        connection_failures = 0

        while self.running:
            try:
                # Periodic health check
                if time.time() - last_health_check > HEALTH_CHECK_INTERVAL_SEC:
                    if not self.client.health_check():
                        connection_failures += 1
                        logger.warning(f"Health check failed (attempt {connection_failures})")
                        if self.data_store:
                            self.data_store.log_connection_event(
                                "HEALTH_CHECK_FAILED",
                                f"Attempt {connection_failures}", False)
                        backoff_time = min(5 * (2 ** min(connection_failures - 1, 5)), 120)
                        time.sleep(backoff_time)
                        continue
                    else:
                        if connection_failures > 0:
                            logger.info(f"Connection restored after {connection_failures} failures")
                            if self.data_store:
                                self.data_store.log_connection_event(
                                    "CONNECTION_RESTORED",
                                    f"After {connection_failures} failures", True)
                        connection_failures = 0
                    last_health_check = time.time()

                # Timed collection cycle
                cycle_start = time.perf_counter()
                self._collect_cycle()
                cycle_ms = (time.perf_counter() - cycle_start) * 1000

                self._cycle_times.append(cycle_ms)
                if len(self._cycle_times) > 100:
                    self._cycle_times = self._cycle_times[-100:]

                # Adaptive sleep: sleep only the remaining time to hit target interval
                elapsed = time.perf_counter() - cycle_start
                sleep_time = max(0.01, POLL_INTERVAL_SEC - elapsed)
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                self.running = False

            except Exception as e:
                self.stats['errors'] += 1
                logger.error(f"Error in collection cycle: {e}", exc_info=True)
                if self.data_store:
                    self.data_store.log_system_event("ERROR", str(e),
                        self.current_period, self.current_tick)
                time.sleep(1)

        self._shutdown()

    def _collect_cycle(self):
        """Perform one optimized data collection cycle."""
        self.stats['polls'] += 1

        # ===== PHASE 1: Case info (must be first - determines session) =====
        success, case_data = self.client.get_case()
        if not success:
            return

        new_tick = case_data.get('tick', 0)
        new_period = case_data.get('period', 0)
        new_status = case_data.get('status', 'UNKNOWN')
        new_case_name = case_data.get('name', 'Unknown')

        should_start_new = (
            self.data_store is None or
            new_period != self.last_period or
            (self.current_tick > 0 and new_tick == 0) or
            self.case_name != new_case_name
        )

        if should_start_new and new_status in ('ACTIVE', 'RUNNING'):
            logger.info(f"New session: Case={new_case_name}, Period={new_period}")
            self._start_new_session(new_case_name, new_period)

        self.current_tick = new_tick
        self.current_period = new_period
        self.last_period = new_period
        self.current_status = new_status
        self.case_name = new_case_name

        if new_status not in ('ACTIVE', 'RUNNING'):
            if self.stats['polls'] % 10 == 0:
                logger.info(f"Case status: {new_status}, waiting...")
            return

        if not self.data_store:
            return

        is_new_tick = (new_tick != self.last_saved_tick)
        if is_new_tick:
            self.stats['ticks_captured'] += 1

        # ===== PHASE 2: Parallel fast calls (news + securities + tenders + orders) =====
        # These are the most critical and fastest calls - do them in parallel
        futures = {}
        futures['news'] = self.executor.submit(
            self.client.get_news, since=self.last_news_id, limit=NEWS_LIMIT)
        futures['securities'] = self.executor.submit(self.client.get_securities)
        futures['tenders'] = self.executor.submit(self.client.get_tenders)

        # Trader + limits only every 5 ticks (they rarely change)
        fetch_trader = is_new_tick and (new_tick % 5 == 0)
        if fetch_trader:
            futures['trader'] = self.executor.submit(self.client.get_trader)
            futures['limits'] = self.executor.submit(self.client.get_limits)

        # Orders every 3 ticks
        fetch_orders = is_new_tick and (new_tick % 3 == 0)
        if fetch_orders:
            futures['orders_open'] = self.executor.submit(
                self.client.get_orders, status='OPEN')
            futures['orders_transacted'] = self.executor.submit(
                self.client.get_orders, status='TRANSACTED')

        # Wait for critical results
        news_result = futures['news'].result()
        securities_result = futures['securities'].result()
        tenders_result = futures['tenders'].result()

        # Process securities first (need prices for deal spreads)
        sec_success, sec_data = securities_result
        if sec_success and sec_data:
            self.active_securities = {s.get('ticker') for s in sec_data if s.get('is_tradeable')}
            for sec in sec_data:
                ticker = sec.get('ticker')
                bid = sec.get('bid')
                ask = sec.get('ask')
                if bid and ask:
                    self.latest_prices[ticker] = round((bid + ask) / 2, 4)
                elif sec.get('last'):
                    self.latest_prices[ticker] = sec.get('last')

        # ===== PHASE 3: Parallel order books + TAS (the slow part) =====
        # Only fetch on new ticks to avoid redundant calls
        book_futures = {}
        tas_futures = {}
        if is_new_tick and self.active_securities:
            for ticker in self.active_securities:
                book_futures[ticker] = self.executor.submit(
                    self.client.get_order_book, ticker, BOOK_DEPTH_LIMIT)
                tas_futures[ticker] = self.executor.submit(
                    self.client.get_time_and_sales, ticker,
                    after=self.last_tas_ids.get(ticker, 0))

        # ===== PHASE 4: Process news IMMEDIATELY (highest priority) =====
        news_success, news_data = news_result
        if news_success and news_data:
            self._process_news(news_data)

        # ===== PHASE 5: Batch write everything to DB =====
        # Use a single connection for all writes this cycle
        if self.data_store:
            conn = self.data_store._get_connection()
            try:
                # Case info
                self.data_store._save_case_info_conn(conn, case_data,
                    self.current_period, self.current_tick)

                # Securities
                if sec_success and sec_data:
                    self.data_store._save_securities_conn(conn, sec_data,
                        self.current_period, self.current_tick)
                    self.stats['securities_saved'] += len(sec_data)

                # Trader + limits
                if fetch_trader:
                    trader_success, trader_data = futures['trader'].result()
                    if trader_success:
                        self.data_store._save_trader_info_conn(conn, trader_data,
                            self.current_period, self.current_tick)
                    limits_success, limits_data = futures['limits'].result()
                    if limits_success and limits_data:
                        self.data_store._save_trading_limits_conn(conn, limits_data,
                            self.current_period, self.current_tick)

                # Order books (wait for parallel results)
                for ticker, fut in book_futures.items():
                    book_success, book_data = fut.result()
                    if book_success and book_data:
                        self.data_store._save_order_book_conn(conn, ticker, book_data,
                            self.current_period, self.current_tick)
                        self.stats['order_books_saved'] += 1

                # Time & sales
                for ticker, fut in tas_futures.items():
                    tas_success, tas_data = fut.result()
                    if tas_success and tas_data:
                        self.data_store._save_time_and_sales_conn(conn, ticker, tas_data)
                        max_id = max(t.get('id', 0) for t in tas_data)
                        if max_id > self.last_tas_ids.get(ticker, 0):
                            self.last_tas_ids[ticker] = max_id
                            self.stats['tas_saved'] += len(tas_data)

                # Tenders
                tender_success, tender_data = tenders_result
                if tender_success and tender_data:
                    for tender in tender_data:
                        tid = tender.get('tender_id')
                        if tid not in self.seen_tender_ids:
                            self.data_store._save_tender_conn(conn, tender,
                                self.current_period, self.current_tick, 'ACTIVE')
                            self.seen_tender_ids.add(tid)
                            self.stats['tenders_saved'] += 1
                            logger.info(f"TENDER: ID={tid} {tender.get('ticker')} "
                                         f"Qty={tender.get('quantity')} "
                                         f"Price={tender.get('price')}")

                # Orders
                if fetch_orders:
                    for key in ('orders_open', 'orders_transacted'):
                        if key in futures:
                            o_success, o_data = futures[key].result()
                            if o_success and o_data:
                                self.data_store._save_orders_conn(conn, o_data,
                                    self.current_period, self.current_tick)

                # Deal spreads (pure computation, no API call)
                if TRACK_DEAL_SPREADS and is_new_tick:
                    for deal_id, deal in DEALS.items():
                        tp = self.latest_prices.get(deal['target'])
                        ap = self.latest_prices.get(deal['acquirer'])
                        if tp and ap:
                            spread_data = self.deal_analyzer.compute_deal_spread(
                                deal_id, tp, ap)
                            self.data_store._save_deal_spread_conn(conn, spread_data,
                                self.current_period, self.current_tick)
                            self.stats['deal_spreads_saved'] += 1

                # OHLC every 10 ticks
                if is_new_tick and new_tick % 10 == 0:
                    for ticker in self.active_securities:
                        ohlc_success, ohlc_data = self.client.get_securities_history(
                            ticker, period=self.current_period, limit=10)
                        if ohlc_success and ohlc_data:
                            self.data_store._save_ohlc_history_conn(
                                conn, ticker, ohlc_data, self.current_period)

                conn.commit()
            except Exception as e:
                logger.error(f"DB batch write error: {e}")
                self.stats['errors'] += 1
            finally:
                conn.close()

        self.last_saved_tick = new_tick

        # Log progress
        if self.stats['polls'] % 30 == 0:
            self._log_stats()

    def _process_news(self, data: list):
        """Classify news and update probabilities. Writes via batched connection."""
        new_items = [n for n in data if n.get('news_id', 0) > self.last_news_id]
        if not new_items:
            return

        for news in new_items:
            headline = news.get('headline', '')
            body = news.get('body', '')

            classification = self.deal_analyzer.classify_news(headline, body)

            news['_deal_id'] = classification['deal_id']
            news['_category'] = classification['category']
            news['_direction'] = classification['direction']
            news['_severity'] = classification['severity']
            news['_delta_p'] = classification['delta_p']
            self.stats['news_classified'] += 1

            # Apply probability impact
            if classification['deal_id'] and classification['delta_p'] != 0:
                prob_before, prob_after = self.deal_analyzer.apply_news_impact(
                    classification['deal_id'], classification['delta_p'],
                    news.get('news_id'))

                self.data_store.save_probability_update(
                    classification['deal_id'],
                    self.current_period, self.current_tick,
                    prob_before, prob_after,
                    trigger_type=f"NEWS_{classification['category']}",
                    news_id=news.get('news_id'),
                    notes=f"{classification['direction']}/{classification['severity']}: {headline[:100]}"
                )
                self.stats['prob_updates'] += 1

            deal_tag = f"[{classification['deal_id']}]" if classification['deal_id'] else "[???]"
            d = classification['direction'][0].upper()
            s = classification['severity'][0].upper()
            dp = classification['delta_p']
            logger.info(f"NEWS {deal_tag} {classification['category']} "
                         f"{d}/{s} dp={dp:+.3f}: {headline}")

        self.data_store.save_news(new_items)

        max_id = max(n.get('news_id', 0) for n in new_items)
        if max_id > self.last_news_id:
            self.last_news_id = max_id
            self.stats['news_saved'] += len(new_items)

    def _log_stats(self):
        runtime = datetime.now() - self.stats['start_time'] if self.stats['start_time'] else None
        runtime_str = str(runtime).split('.')[0] if runtime else "N/A"

        avg_ms = sum(self._cycle_times[-50:]) / max(1, len(self._cycle_times[-50:]))
        self.stats['avg_cycle_ms'] = round(avg_ms, 1)

        logger.info(f"[Tick {self.current_tick} P{self.current_period}] "
                     f"Runtime={runtime_str} AvgCycle={avg_ms:.0f}ms "
                     f"Ticks={self.stats['ticks_captured']}")
        logger.info(f"  Polls={self.stats['polls']} Books={self.stats['order_books_saved']} "
                     f"Spreads={self.stats['deal_spreads_saved']} "
                     f"News={self.stats['news_saved']}/{self.stats['news_classified']} "
                     f"Prob={self.stats['prob_updates']} Err={self.stats['errors']}")

        for deal_id, deal in DEALS.items():
            prob = self.deal_analyzer.analyst_probs.get(deal_id, deal['initial_prob'])
            tp = self.latest_prices.get(deal['target'], 0)
            ap = self.latest_prices.get(deal['acquirer'], 0)
            if tp and ap:
                dv = self.deal_analyzer.compute_deal_value(deal_id, ap)
                spread = dv - tp
                logger.info(f"  {deal_id} {deal['target']}/{deal['acquirer']}: "
                             f"T=${tp:.2f} A=${ap:.2f} DV=${dv:.2f} "
                             f"Spr=${spread:.2f} P={prob:.1%}")

    def _shutdown(self):
        logger.info("Shutting down data collector...")
        self.executor.shutdown(wait=False)

        if self.data_store:
            self.data_store.log_system_event("SHUTDOWN", "Collector stopped",
                self.current_period, self.current_tick, self.stats)
            self.data_store.flush()

        if self.current_session:
            self._flush_and_end_session()

        self._log_stats()
        logger.info("Data collector stopped.")
