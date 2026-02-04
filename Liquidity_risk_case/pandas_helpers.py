"""
Pandas Helper Functions for RIT Data Analysis

Provides easy-to-use functions for loading and analyzing collected data.
Designed to work with multi-collector scenarios where different team members
may have collected data simultaneously.

Usage:
    from pandas_helpers import RITDataLoader

    # Load data from a session database
    loader = RITDataLoader("path/to/session.db")

    # Get tick snapshots (one row per ticker per tick)
    df = loader.get_tick_snapshots()

    # Get player limit orders (non-ANON)
    df = loader.get_player_orders()

    # Merge data from multiple collectors
    merged = RITDataLoader.merge_sessions(["session1.db", "session2.db"])
"""
import sqlite3
import pandas as pd
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from datetime import datetime


class RITDataLoader:
    """
    Helper class for loading RIT data into pandas DataFrames.

    Designed for efficient data analysis with support for:
    - Multi-collector data merging
    - Tick-level price/volume analysis
    - Player limit order tracking
    - Session comparison
    """

    def __init__(self, db_path: Union[str, Path]):
        """
        Initialize with path to a session database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        return conn

    # =========================================================================
    # COLLECTOR INFORMATION
    # =========================================================================

    def get_collectors(self) -> pd.DataFrame:
        """
        Get information about all collectors that contributed to this database.

        Returns:
            DataFrame with collector_id, hostname, username, start_time, etc.
        """
        query = "SELECT * FROM collector_instances ORDER BY start_time"
        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn)

    def get_collector_stats(self) -> pd.DataFrame:
        """
        Get statistics grouped by collector.

        Returns:
            DataFrame with record counts per collector
        """
        query = """
        SELECT
            ci.collector_id,
            ci.collector_name,
            ci.hostname,
            ci.username,
            ci.start_time,
            ci.end_time,
            ci.polls_completed,
            ci.records_saved,
            COUNT(DISTINCT ts.tick) as unique_ticks,
            COUNT(DISTINCT ts.ticker) as unique_tickers
        FROM collector_instances ci
        LEFT JOIN tick_snapshots ts ON ci.collector_id = ts.collector_id
        GROUP BY ci.collector_id
        ORDER BY ci.start_time
        """
        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn)

    # =========================================================================
    # TICK SNAPSHOTS - Optimized for per-tick analysis
    # =========================================================================

    def get_tick_snapshots(self, ticker: str = None,
                           collector_id: str = None,
                           period: int = None,
                           start_tick: int = None,
                           end_tick: int = None) -> pd.DataFrame:
        """
        Get tick-level price/volume snapshots.

        This is the most efficient way to get price data for analysis.
        One row per ticker per tick.

        Args:
            ticker: Filter by ticker (optional)
            collector_id: Filter by collector (optional)
            period: Filter by period (optional)
            start_tick: Start tick for range filter (optional)
            end_tick: End tick for range filter (optional)

        Returns:
            DataFrame with columns: period, tick, ticker, last_price, bid, ask,
                                   bid_size, ask_size, spread, mid_price, volume,
                                   total_volume, tick_volume, vwap
        """
        conditions = []
        params = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)
        if collector_id:
            conditions.append("collector_id = ?")
            params.append(collector_id)
        if period is not None:
            conditions.append("period = ?")
            params.append(period)
        if start_tick is not None:
            conditions.append("tick >= ?")
            params.append(start_tick)
        if end_tick is not None:
            conditions.append("tick <= ?")
            params.append(end_tick)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
        SELECT
            collector_id, period, tick, ticker,
            last_price, bid, ask, bid_size, ask_size,
            spread, mid_price, volume, total_volume, tick_volume, vwap,
            timestamp
        FROM tick_snapshots
        WHERE {where_clause}
        ORDER BY period, tick, ticker
        """

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        # Convert timestamp to datetime
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        return df

    def get_price_series(self, ticker: str, price_col: str = 'mid_price',
                         collector_id: str = None) -> pd.Series:
        """
        Get a price time series for a specific ticker.

        Args:
            ticker: The ticker symbol
            price_col: Which price to use ('mid_price', 'last_price', 'bid', 'ask')
            collector_id: Filter by collector (optional)

        Returns:
            Series indexed by tick with the requested price
        """
        df = self.get_tick_snapshots(ticker=ticker, collector_id=collector_id)
        return df.set_index('tick')[price_col]

    # =========================================================================
    # PLAYER LIMIT ORDERS - Non-ANON orders from other traders
    # =========================================================================

    def get_player_orders(self, ticker: str = None,
                          trader_id: str = None,
                          active_only: bool = False,
                          collector_id: str = None) -> pd.DataFrame:
        """
        Get player limit orders (non-ANON orders from identified traders).

        Args:
            ticker: Filter by ticker (optional)
            trader_id: Filter by trader ID (optional)
            active_only: Only return currently active orders
            collector_id: Filter by collector (optional)

        Returns:
            DataFrame with player limit order details
        """
        conditions = []
        params = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)
        if trader_id:
            conditions.append("trader_id = ?")
            params.append(trader_id)
        if active_only:
            conditions.append("is_active = 1")
        if collector_id:
            conditions.append("collector_id = ?")
            params.append(collector_id)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
        SELECT
            collector_id, period, tick, ticker, side, price, quantity,
            quantity_filled, order_id, trader_id,
            first_seen_tick, last_seen_tick, is_active,
            (last_seen_tick - first_seen_tick) as ticks_active,
            timestamp
        FROM player_limit_orders
        WHERE {where_clause}
        ORDER BY first_seen_tick, ticker, side
        """

        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)

        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        return df

    def get_player_order_summary(self) -> pd.DataFrame:
        """
        Get summary statistics of player orders grouped by trader.

        Returns:
            DataFrame with trader_id, order counts, avg size, etc.
        """
        query = """
        SELECT
            trader_id,
            COUNT(*) as total_orders,
            COUNT(CASE WHEN side = 'BID' THEN 1 END) as bid_orders,
            COUNT(CASE WHEN side = 'ASK' THEN 1 END) as ask_orders,
            AVG(quantity) as avg_quantity,
            AVG(price) as avg_price,
            AVG(last_seen_tick - first_seen_tick) as avg_ticks_active,
            COUNT(DISTINCT ticker) as unique_tickers
        FROM player_limit_orders
        GROUP BY trader_id
        ORDER BY total_orders DESC
        """

        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn)

    # =========================================================================
    # ORDER BOOK DATA
    # =========================================================================

    def get_order_book_snapshots(self, ticker: str,
                                  tick: int = None,
                                  side: str = None,
                                  exclude_anon: bool = False) -> pd.DataFrame:
        """
        Get order book snapshots.

        Args:
            ticker: The ticker symbol
            tick: Specific tick (optional, gets all if not specified)
            side: 'BID' or 'ASK' (optional)
            exclude_anon: Exclude anonymous/market maker orders

        Returns:
            DataFrame with order book data
        """
        conditions = ["ticker = ?"]
        params = [ticker]

        if tick is not None:
            conditions.append("tick = ?")
            params.append(tick)
        if side:
            conditions.append("side = ?")
            params.append(side.upper())
        if exclude_anon:
            conditions.append("is_anon = 0")

        where_clause = " AND ".join(conditions)

        query = f"""
        SELECT
            collector_id, period, tick, ticker, side, level,
            price, quantity, quantity_filled, order_id, trader_id, is_anon,
            timestamp
        FROM order_book
        WHERE {where_clause}
        ORDER BY tick, side, level
        """

        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # =========================================================================
    # TIME AND SALES (TRADE HISTORY)
    # =========================================================================

    def get_trades(self, ticker: str = None,
                   period: int = None,
                   start_tick: int = None,
                   end_tick: int = None) -> pd.DataFrame:
        """
        Get trade history (time and sales data).

        Args:
            ticker: Filter by ticker (optional)
            period: Filter by period (optional)
            start_tick: Start tick for range filter (optional)
            end_tick: End tick for range filter (optional)

        Returns:
            DataFrame with trade details
        """
        conditions = []
        params = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)
        if period is not None:
            conditions.append("period = ?")
            params.append(period)
        if start_tick is not None:
            conditions.append("tick >= ?")
            params.append(start_tick)
        if end_tick is not None:
            conditions.append("tick <= ?")
            params.append(end_tick)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
        SELECT
            collector_id, ticker, tas_id, period, tick, price, quantity,
            timestamp
        FROM time_and_sales
        WHERE {where_clause}
        ORDER BY period, tick, tas_id
        """

        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_vwap_by_tick(self, ticker: str) -> pd.DataFrame:
        """
        Calculate VWAP by tick for a ticker.

        Args:
            ticker: The ticker symbol

        Returns:
            DataFrame with tick, vwap, total_volume, trade_count
        """
        query = """
        SELECT
            tick,
            SUM(price * quantity) / SUM(quantity) as vwap,
            SUM(quantity) as total_volume,
            COUNT(*) as trade_count
        FROM time_and_sales
        WHERE ticker = ?
        GROUP BY tick
        ORDER BY tick
        """

        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=[ticker])

    # =========================================================================
    # SECURITIES DATA
    # =========================================================================

    def get_securities(self, ticker: str = None,
                       collector_id: str = None) -> pd.DataFrame:
        """
        Get full securities data (includes position, unrealized P&L, etc.)

        Args:
            ticker: Filter by ticker (optional)
            collector_id: Filter by collector (optional)

        Returns:
            DataFrame with all securities data
        """
        conditions = []
        params = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)
        if collector_id:
            conditions.append("collector_id = ?")
            params.append(collector_id)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
        SELECT * FROM securities
        WHERE {where_clause}
        ORDER BY period, tick, ticker
        """

        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # =========================================================================
    # TENDERS
    # =========================================================================

    def get_tenders(self, ticker: str = None) -> pd.DataFrame:
        """
        Get tender offers.

        Args:
            ticker: Filter by ticker (optional)

        Returns:
            DataFrame with tender details
        """
        conditions = []
        params = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
        SELECT * FROM tenders
        WHERE {where_clause}
        ORDER BY period, tick
        """

        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # =========================================================================
    # NEWS
    # =========================================================================

    def get_news(self, ticker: str = None) -> pd.DataFrame:
        """
        Get news items.

        Args:
            ticker: Filter by ticker (optional)

        Returns:
            DataFrame with news items
        """
        conditions = []
        params = []

        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
        SELECT * FROM news
        WHERE {where_clause}
        ORDER BY period, tick
        """

        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # =========================================================================
    # MULTI-SESSION MERGING
    # =========================================================================

    @staticmethod
    def merge_sessions(db_paths: List[Union[str, Path]],
                       dedupe_by: str = 'tick') -> Dict[str, pd.DataFrame]:
        """
        Merge data from multiple session databases.

        Useful when multiple team members collected data simultaneously.
        The collector_id column allows you to identify the source of each record.

        Args:
            db_paths: List of paths to session databases
            dedupe_by: How to handle duplicates ('tick', 'first', 'all')
                      - 'tick': Keep one record per tick per ticker (first seen)
                      - 'first': Keep only first collector's data
                      - 'all': Keep all records (may have duplicates)

        Returns:
            Dictionary of DataFrames: {
                'tick_snapshots': merged tick snapshots,
                'player_orders': merged player orders,
                'trades': merged time and sales,
                'tenders': merged tenders,
                'collectors': info about all collectors
            }
        """
        all_snapshots = []
        all_player_orders = []
        all_trades = []
        all_tenders = []
        all_collectors = []

        for db_path in db_paths:
            loader = RITDataLoader(db_path)

            # Get data from each database
            try:
                all_snapshots.append(loader.get_tick_snapshots())
            except:
                pass

            try:
                all_player_orders.append(loader.get_player_orders())
            except:
                pass

            try:
                all_trades.append(loader.get_trades())
            except:
                pass

            try:
                all_tenders.append(loader.get_tenders())
            except:
                pass

            try:
                collectors = loader.get_collectors()
                collectors['source_db'] = str(db_path)
                all_collectors.append(collectors)
            except:
                pass

        result = {}

        # Merge tick snapshots
        if all_snapshots:
            df = pd.concat(all_snapshots, ignore_index=True)
            if dedupe_by == 'tick':
                df = df.drop_duplicates(subset=['period', 'tick', 'ticker'], keep='first')
            elif dedupe_by == 'first':
                df = df.sort_values('timestamp').drop_duplicates(
                    subset=['period', 'tick', 'ticker'], keep='first')
            result['tick_snapshots'] = df.sort_values(['period', 'tick', 'ticker'])

        # Merge player orders
        if all_player_orders:
            df = pd.concat(all_player_orders, ignore_index=True)
            df = df.drop_duplicates(subset=['order_id', 'collector_id'], keep='first')
            result['player_orders'] = df.sort_values(['first_seen_tick', 'ticker'])

        # Merge trades (dedupe by tas_id)
        if all_trades:
            df = pd.concat(all_trades, ignore_index=True)
            df = df.drop_duplicates(subset=['ticker', 'tas_id'], keep='first')
            result['trades'] = df.sort_values(['period', 'tick', 'tas_id'])

        # Merge tenders (dedupe by tender_id)
        if all_tenders:
            df = pd.concat(all_tenders, ignore_index=True)
            df = df.drop_duplicates(subset=['tender_id'], keep='first')
            result['tenders'] = df.sort_values(['period', 'tick'])

        # Collector info
        if all_collectors:
            result['collectors'] = pd.concat(all_collectors, ignore_index=True)

        return result

    # =========================================================================
    # ANALYSIS HELPERS
    # =========================================================================

    def get_spread_analysis(self, ticker: str) -> pd.DataFrame:
        """
        Get spread analysis for a ticker.

        Returns:
            DataFrame with spread statistics by tick
        """
        df = self.get_tick_snapshots(ticker=ticker)

        if df.empty:
            return pd.DataFrame()

        return pd.DataFrame({
            'tick': df['tick'],
            'spread': df['spread'],
            'spread_bps': (df['spread'] / df['mid_price'] * 10000),
            'bid_size': df['bid_size'],
            'ask_size': df['ask_size'],
            'imbalance': (df['bid_size'] - df['ask_size']) / (df['bid_size'] + df['ask_size']),
        })

    def get_volatility(self, ticker: str, window: int = 20) -> pd.Series:
        """
        Calculate rolling volatility for a ticker.

        Args:
            ticker: The ticker symbol
            window: Rolling window size in ticks

        Returns:
            Series with rolling volatility
        """
        prices = self.get_price_series(ticker, 'mid_price')
        returns = prices.pct_change()
        return returns.rolling(window=window).std()


def quick_load(db_path: Union[str, Path]) -> RITDataLoader:
    """
    Quick helper to create a data loader.

    Usage:
        loader = quick_load("session.db")
        df = loader.get_tick_snapshots()
    """
    return RITDataLoader(db_path)


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pandas_helpers.py <path_to_db>")
        print("\nExample:")
        print("  python pandas_helpers.py data/sessions/session_20260203.db")
        sys.exit(1)

    db_path = sys.argv[1]
    loader = RITDataLoader(db_path)

    print(f"\n{'='*60}")
    print(f"RIT Data Analysis - {db_path}")
    print(f"{'='*60}\n")

    # Show collector info
    print("COLLECTORS:")
    print(loader.get_collectors().to_string())
    print()

    # Show tick snapshot sample
    print("\nTICK SNAPSHOTS (first 10):")
    df = loader.get_tick_snapshots()
    print(f"Total records: {len(df)}")
    if not df.empty:
        print(df.head(10).to_string())
    print()

    # Show player orders
    print("\nPLAYER LIMIT ORDERS:")
    df = loader.get_player_orders()
    print(f"Total player orders: {len(df)}")
    if not df.empty:
        print("\nBy trader:")
        print(loader.get_player_order_summary().to_string())
