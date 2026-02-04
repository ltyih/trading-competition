"""
Data Analysis for RIT Liquidity Risk Case

Enhanced to work with session-based databases and show:
- Tick snapshots (price/volume per tick)
- Player limit orders (non-ANON)
- Multi-collector data merging

Usage:
    python analyze_data.py                    # Analyze most recent session
    python analyze_data.py path/to/session.db # Analyze specific session
    python analyze_data.py --list             # List all sessions
    python analyze_data.py --export           # Export to Excel
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

# Try to import pandas - required for this script
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("WARNING: pandas not installed. Install with: pip install pandas")

from config import DATA_DIR, SESSIONS_DIR


def get_latest_session_db() -> Optional[Path]:
    """Find the most recent session database."""
    session_files = list(SESSIONS_DIR.glob("*.db"))
    if not session_files:
        return None
    # Sort by modification time, get most recent
    return max(session_files, key=lambda p: p.stat().st_mtime)


def list_sessions():
    """List all available sessions."""
    print("\n" + "=" * 70)
    print("AVAILABLE SESSIONS")
    print("=" * 70)

    session_files = sorted(SESSIONS_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not session_files:
        print("No sessions found.")
        return

    for db_path in session_files:
        size_mb = db_path.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(db_path.stat().st_mtime)
        print(f"\n{db_path.name}")
        print(f"  Size: {size_mb:.2f} MB")
        print(f"  Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")

        # Quick stats
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM tick_snapshots")
            tick_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT ticker) FROM tick_snapshots")
            ticker_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM player_limit_orders")
            player_orders = cursor.fetchone()[0]

            print(f"  Tick snapshots: {tick_count:,}")
            print(f"  Tickers: {ticker_count}")
            print(f"  Player orders: {player_orders}")

            conn.close()
        except Exception as e:
            print(f"  (Could not read stats: {e})")


class SessionAnalyzer:
    """Analyzer for session-based RIT data."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))

    def close(self):
        self.conn.close()

    # =========================================================================
    # TICK SNAPSHOTS - Price and Volume per Tick
    # =========================================================================

    def get_tick_snapshots(self, ticker: str = None, limit: int = None) -> pd.DataFrame:
        """
        Get tick-level price and volume data.

        This is the key table for price/volume analysis.
        """
        query = """
        SELECT
            period, tick, ticker,
            last_price, bid, ask, bid_size, ask_size,
            spread, mid_price,
            volume, total_volume, tick_volume,
            vwap, collector_id, timestamp
        FROM tick_snapshots
        """

        if ticker:
            query += f" WHERE ticker = '{ticker}'"

        query += " ORDER BY period, tick, ticker"

        if limit:
            query += f" LIMIT {limit}"

        return pd.read_sql_query(query, self.conn)

    def get_price_per_tick(self, ticker: str) -> pd.DataFrame:
        """Get simple price series per tick for a ticker."""
        query = f"""
        SELECT
            tick,
            last_price as price,
            bid,
            ask,
            mid_price,
            spread,
            volume as tick_volume,
            total_volume
        FROM tick_snapshots
        WHERE ticker = '{ticker}'
        ORDER BY period, tick
        """
        return pd.read_sql_query(query, self.conn)

    def get_volume_per_tick(self, ticker: str = None) -> pd.DataFrame:
        """Get volume data per tick."""
        where = f"WHERE ticker = '{ticker}'" if ticker else ""
        query = f"""
        SELECT
            period, tick, ticker,
            volume as tick_volume,
            total_volume,
            bid_size,
            ask_size,
            (bid_size + ask_size) as total_depth
        FROM tick_snapshots
        {where}
        ORDER BY period, tick, ticker
        """
        return pd.read_sql_query(query, self.conn)

    # =========================================================================
    # PLAYER LIMIT ORDERS - Non-ANON Orders
    # =========================================================================

    def get_player_orders(self, ticker: str = None, active_only: bool = False) -> pd.DataFrame:
        """
        Get limit orders from identified players (not ANON).

        Args:
            ticker: Filter by ticker
            active_only: Only return currently active orders
        """
        conditions = []
        if ticker:
            conditions.append(f"ticker = '{ticker}'")
        if active_only:
            conditions.append("is_active = 1")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        query = f"""
        SELECT
            period, tick, ticker, side, price, quantity,
            quantity_filled, order_id, trader_id,
            first_seen_tick, last_seen_tick,
            (last_seen_tick - first_seen_tick) as ticks_active,
            is_active,
            collector_id, timestamp
        FROM player_limit_orders
        {where}
        ORDER BY first_seen_tick DESC, ticker
        """
        return pd.read_sql_query(query, self.conn)

    def get_player_order_summary(self) -> pd.DataFrame:
        """Get summary of player orders grouped by trader."""
        query = """
        SELECT
            trader_id,
            COUNT(*) as total_orders,
            SUM(CASE WHEN side = 'BID' THEN 1 ELSE 0 END) as bid_orders,
            SUM(CASE WHEN side = 'ASK' THEN 1 ELSE 0 END) as ask_orders,
            AVG(quantity) as avg_quantity,
            AVG(price) as avg_price,
            AVG(last_seen_tick - first_seen_tick) as avg_ticks_active,
            COUNT(DISTINCT ticker) as unique_tickers,
            GROUP_CONCAT(DISTINCT ticker) as tickers
        FROM player_limit_orders
        GROUP BY trader_id
        ORDER BY total_orders DESC
        """
        return pd.read_sql_query(query, self.conn)

    def get_order_book_non_anon(self, ticker: str, tick: int = None) -> pd.DataFrame:
        """Get order book entries from identified players only."""
        tick_filter = f"AND tick = {tick}" if tick else ""
        query = f"""
        SELECT
            period, tick, side, level, price, quantity,
            quantity_filled, order_id, trader_id
        FROM order_book
        WHERE ticker = '{ticker}' AND is_anon = 0 {tick_filter}
        ORDER BY tick, side, level
        """
        return pd.read_sql_query(query, self.conn)

    # =========================================================================
    # COLLECTOR INFO
    # =========================================================================

    def get_collectors(self) -> pd.DataFrame:
        """Get info about collectors who contributed data."""
        query = """
        SELECT
            collector_id, collector_name, hostname, username,
            start_time, end_time, polls_completed, records_saved, is_active
        FROM collector_instances
        ORDER BY start_time
        """
        return pd.read_sql_query(query, self.conn)

    # =========================================================================
    # CLASSIC DATA
    # =========================================================================

    def get_securities_summary(self) -> pd.DataFrame:
        """Get summary statistics for all securities."""
        query = """
        SELECT
            ticker,
            COUNT(*) as data_points,
            MIN(tick) as first_tick,
            MAX(tick) as last_tick,
            AVG(last_price) as avg_price,
            MIN(last_price) as min_price,
            MAX(last_price) as max_price,
            AVG(spread) as avg_spread,
            MAX(total_volume) as total_volume
        FROM tick_snapshots
        WHERE last_price > 0
        GROUP BY ticker
        ORDER BY ticker
        """
        return pd.read_sql_query(query, self.conn)

    def get_tenders(self) -> pd.DataFrame:
        """Get all tender offers."""
        query = """
        SELECT
            period, tick, tender_id, ticker, caption,
            quantity, action, price, expires, status,
            collector_id, timestamp
        FROM tenders
        ORDER BY tick
        """
        return pd.read_sql_query(query, self.conn)

    def get_news(self) -> pd.DataFrame:
        """Get all news items."""
        query = """
        SELECT
            news_id, period, tick, ticker, headline, body,
            collector_id, timestamp
        FROM news
        ORDER BY news_id
        """
        return pd.read_sql_query(query, self.conn)

    def get_trades(self, ticker: str = None) -> pd.DataFrame:
        """Get time and sales (executed trades)."""
        where = f"WHERE ticker = '{ticker}'" if ticker else ""
        query = f"""
        SELECT
            period, tick, ticker, tas_id, price, quantity,
            collector_id, timestamp
        FROM time_and_sales
        {where}
        ORDER BY period, tick, tas_id
        """
        return pd.read_sql_query(query, self.conn)

    # =========================================================================
    # EXPORT
    # =========================================================================

    def export_to_excel(self, output_path: str = None) -> Path:
        """Export all data to Excel with multiple sheets."""
        if output_path is None:
            output_path = DATA_DIR / f"analysis_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        output_path = Path(output_path)

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Tick snapshots (sample if too large)
            tick_data = self.get_tick_snapshots()
            if len(tick_data) > 50000:
                # Sample to avoid huge Excel files
                tick_data = tick_data.iloc[::10]  # Every 10th row
                print(f"  Tick snapshots sampled to {len(tick_data)} rows")
            tick_data.to_excel(writer, sheet_name='Tick Snapshots', index=False)

            # Player orders
            self.get_player_orders().to_excel(writer, sheet_name='Player Orders', index=False)

            # Player summary
            self.get_player_order_summary().to_excel(writer, sheet_name='Player Summary', index=False)

            # Securities summary
            self.get_securities_summary().to_excel(writer, sheet_name='Securities Summary', index=False)

            # Tenders
            self.get_tenders().to_excel(writer, sheet_name='Tenders', index=False)

            # News
            self.get_news().to_excel(writer, sheet_name='News', index=False)

            # Collectors
            self.get_collectors().to_excel(writer, sheet_name='Collectors', index=False)

        print(f"Exported to: {output_path}")
        return output_path

    def export_to_csv(self, output_dir: str = None) -> Path:
        """Export key data to CSV files."""
        if output_dir is None:
            output_dir = DATA_DIR / f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True)

        # Tick snapshots
        self.get_tick_snapshots().to_csv(output_dir / "tick_snapshots.csv", index=False)
        print(f"  Saved tick_snapshots.csv")

        # Player orders
        self.get_player_orders().to_csv(output_dir / "player_orders.csv", index=False)
        print(f"  Saved player_orders.csv")

        # Volume per tick
        self.get_volume_per_tick().to_csv(output_dir / "volume_per_tick.csv", index=False)
        print(f"  Saved volume_per_tick.csv")

        # Trades
        self.get_trades().to_csv(output_dir / "trades.csv", index=False)
        print(f"  Saved trades.csv")

        # Tenders
        self.get_tenders().to_csv(output_dir / "tenders.csv", index=False)
        print(f"  Saved tenders.csv")

        print(f"\nExported to: {output_dir}")
        return output_dir

    # =========================================================================
    # PRINT SUMMARY
    # =========================================================================

    def print_summary(self):
        """Print a comprehensive summary of the data."""
        print("\n" + "=" * 70)
        print(f"SESSION ANALYSIS: {self.db_path.name}")
        print("=" * 70)

        # Collector info
        collectors = self.get_collectors()
        if not collectors.empty:
            print("\nCOLLECTORS:")
            print("-" * 40)
            for _, c in collectors.iterrows():
                status = "ACTIVE" if c.get('is_active') else "stopped"
                print(f"  {c['collector_id']}")
                print(f"    Host: {c.get('hostname', 'N/A')}, User: {c.get('username', 'N/A')}")
                print(f"    Status: {status}, Polls: {c.get('polls_completed', 'N/A')}")

        # Tick snapshots stats
        tick_data = self.get_tick_snapshots()
        if not tick_data.empty:
            print("\nTICK SNAPSHOTS:")
            print("-" * 40)
            print(f"  Total records: {len(tick_data):,}")
            print(f"  Tickers: {tick_data['ticker'].unique().tolist()}")
            print(f"  Tick range: {tick_data['tick'].min()} - {tick_data['tick'].max()}")

        # Securities summary
        summary = self.get_securities_summary()
        if not summary.empty:
            print("\nPRICE/VOLUME SUMMARY BY TICKER:")
            print("-" * 40)
            for _, row in summary.iterrows():
                print(f"  {row['ticker']}:")
                print(f"    Price: ${row['avg_price']:.2f} (range: ${row['min_price']:.2f} - ${row['max_price']:.2f})")
                print(f"    Avg spread: ${row['avg_spread']:.4f}")
                print(f"    Total volume: {row['total_volume']:,.0f}")
                print(f"    Data points: {row['data_points']:,}")

        # Player orders
        player_orders = self.get_player_orders()
        if not player_orders.empty:
            print("\nPLAYER LIMIT ORDERS (Non-ANON):")
            print("-" * 40)
            print(f"  Total orders tracked: {len(player_orders)}")

            # Summary by trader
            player_summary = self.get_player_order_summary()
            if not player_summary.empty:
                print("\n  By Trader:")
                for _, p in player_summary.iterrows():
                    print(f"    {p['trader_id']}: {p['total_orders']} orders "
                          f"({p['bid_orders']} bids, {p['ask_orders']} asks), "
                          f"tickers: {p['tickers']}")

        # Tenders
        tenders = self.get_tenders()
        if not tenders.empty:
            print(f"\nTENDERS: {len(tenders)} received")
            print("-" * 40)
            for _, t in tenders.iterrows():
                price_str = f"${t['price']:.2f}" if pd.notna(t['price']) else "N/A"
                print(f"  #{t['tender_id']}: {t['action']} {t['quantity']:,} {t['ticker']} @ {price_str}")

        # News
        news = self.get_news()
        if not news.empty:
            print(f"\nNEWS: {len(news)} items")
            print("-" * 40)
            for _, n in news.head(5).iterrows():
                print(f"  [{n['tick']}] {n['headline']}")
            if len(news) > 5:
                print(f"  ... and {len(news) - 5} more")

        print("\n" + "=" * 70)

    def print_tick_data(self, ticker: str, last_n: int = 20):
        """Print recent tick data for a ticker."""
        df = self.get_price_per_tick(ticker)

        if df.empty:
            print(f"No tick data for {ticker}")
            return

        print(f"\nTICK DATA FOR {ticker} (last {last_n} ticks):")
        print("-" * 70)
        print(f"{'Tick':>6} {'Price':>10} {'Bid':>10} {'Ask':>10} {'Spread':>8} {'Volume':>10}")
        print("-" * 70)

        for _, row in df.tail(last_n).iterrows():
            print(f"{row['tick']:>6} "
                  f"${row['price']:>9.2f} "
                  f"${row['bid']:>9.2f} "
                  f"${row['ask']:>9.2f} "
                  f"${row['spread']:>7.4f} "
                  f"{row['tick_volume']:>10,.0f}")

    def print_player_orders(self, ticker: str = None, last_n: int = 20):
        """Print recent player limit orders."""
        df = self.get_player_orders(ticker=ticker)

        if df.empty:
            print("No player limit orders found")
            return

        title = f"PLAYER LIMIT ORDERS FOR {ticker}" if ticker else "ALL PLAYER LIMIT ORDERS"
        print(f"\n{title} (last {last_n}):")
        print("-" * 80)
        print(f"{'Tick':>6} {'Ticker':>6} {'Side':>4} {'Price':>10} {'Qty':>8} {'Trader':>15} {'Active':>6}")
        print("-" * 80)

        for _, row in df.head(last_n).iterrows():
            active = "YES" if row['is_active'] else "no"
            print(f"{row['tick']:>6} "
                  f"{row['ticker']:>6} "
                  f"{row['side']:>4} "
                  f"${row['price']:>9.2f} "
                  f"{row['quantity']:>8,} "
                  f"{row['trader_id']:>15} "
                  f"{active:>6}")


def main():
    """Main entry point."""
    if not PANDAS_AVAILABLE:
        print("ERROR: pandas is required. Install with: pip install pandas")
        sys.exit(1)

    # Parse arguments
    args = sys.argv[1:]

    if "--list" in args or "-l" in args:
        list_sessions()
        return

    # Find database to analyze
    if args and not args[0].startswith("-"):
        db_path = Path(args[0])
        if not db_path.exists():
            print(f"ERROR: Database not found: {db_path}")
            sys.exit(1)
    else:
        db_path = get_latest_session_db()
        if not db_path:
            print("ERROR: No session databases found.")
            print(f"Looking in: {SESSIONS_DIR}")
            sys.exit(1)
        print(f"Using most recent session: {db_path.name}")

    # Create analyzer
    analyzer = SessionAnalyzer(db_path)

    try:
        # Print summary
        analyzer.print_summary()

        # Print tick data for each ticker
        summary = analyzer.get_securities_summary()
        for ticker in summary['ticker'].unique():
            analyzer.print_tick_data(ticker, last_n=10)

        # Print player orders
        analyzer.print_player_orders(last_n=15)

        # Export if requested
        if "--export" in args or "-e" in args:
            print("\nExporting to Excel...")
            analyzer.export_to_excel()

        if "--csv" in args:
            print("\nExporting to CSV...")
            analyzer.export_to_csv()

    finally:
        analyzer.close()


if __name__ == "__main__":
    main()
