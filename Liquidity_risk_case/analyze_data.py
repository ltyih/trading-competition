"""
Data Analysis Utilities for RIT Collected Data

Provides functions to analyze and visualize the collected market data.
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from config import DB_PATH, DATA_DIR


def get_connection():
    """Get database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class DataAnalyzer:
    """Analysis utilities for collected RIT data."""

    def __init__(self):
        self.conn = get_connection()

    def close(self):
        """Close database connection."""
        self.conn.close()

    def get_securities_summary(self) -> pd.DataFrame:
        """Get summary statistics for all securities."""
        query = """
        SELECT
            ticker,
            COUNT(*) as data_points,
            MIN(timestamp) as first_seen,
            MAX(timestamp) as last_seen,
            AVG(last_price) as avg_price,
            MIN(last_price) as min_price,
            MAX(last_price) as max_price,
            AVG(spread) as avg_spread,
            AVG(volume) as avg_volume,
            SUM(volume) as total_volume
        FROM securities
        WHERE last_price > 0
        GROUP BY ticker
        ORDER BY ticker
        """
        return pd.read_sql_query(query, self.conn)

    def get_price_history(self, ticker: str, period: int = None) -> pd.DataFrame:
        """Get price history for a security."""
        query = """
        SELECT
            timestamp,
            period,
            tick,
            last_price,
            bid,
            ask,
            bid_size,
            ask_size,
            spread,
            mid_price,
            volume,
            position
        FROM securities
        WHERE ticker = ?
        """
        params = [ticker]

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY timestamp"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_order_book_snapshots(self, ticker: str, period: int = None,
                                  tick: int = None) -> pd.DataFrame:
        """Get order book snapshots."""
        query = """
        SELECT
            timestamp,
            period,
            tick,
            side,
            level,
            price,
            quantity
        FROM order_book
        WHERE ticker = ?
        """
        params = [ticker]

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        if tick is not None:
            query += " AND tick = ?"
            params.append(tick)

        query += " ORDER BY timestamp, level"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_all_tenders(self) -> pd.DataFrame:
        """Get all tender offers."""
        query = """
        SELECT
            timestamp,
            period,
            tick,
            tender_id,
            ticker,
            caption,
            quantity,
            action,
            price,
            expires,
            status
        FROM tenders
        ORDER BY timestamp
        """
        return pd.read_sql_query(query, self.conn)

    def get_tender_analysis(self) -> pd.DataFrame:
        """Analyze tender offers with market context."""
        # Get tenders
        tenders = self.get_all_tenders()

        if tenders.empty:
            return tenders

        # For each tender, get the market price at that time
        results = []
        for _, tender in tenders.iterrows():
            # Get securities data at the tender time
            query = """
            SELECT last_price, bid, ask, spread
            FROM securities
            WHERE ticker = ? AND period = ? AND tick <= ?
            ORDER BY tick DESC
            LIMIT 1
            """
            market = pd.read_sql_query(query, self.conn,
                params=[tender['ticker'], tender['period'], tender['tick']])

            row = tender.to_dict()
            if not market.empty:
                row['market_price'] = market.iloc[0]['last_price']
                row['market_bid'] = market.iloc[0]['bid']
                row['market_ask'] = market.iloc[0]['ask']
                row['market_spread'] = market.iloc[0]['spread']

                # Calculate potential profit
                if tender['action'] == 'BUY':
                    # We buy at tender price, sell at market bid
                    row['potential_profit_per_share'] = market.iloc[0]['bid'] - tender['price']
                else:
                    # We sell at tender price, buy at market ask
                    row['potential_profit_per_share'] = tender['price'] - market.iloc[0]['ask']

                row['total_potential_profit'] = row['potential_profit_per_share'] * tender['quantity']

            results.append(row)

        return pd.DataFrame(results)

    def get_news_summary(self) -> pd.DataFrame:
        """Get news summary."""
        query = """
        SELECT
            timestamp,
            news_id,
            period,
            tick,
            ticker,
            headline,
            body
        FROM news
        ORDER BY news_id
        """
        return pd.read_sql_query(query, self.conn)

    def get_trading_performance(self) -> pd.DataFrame:
        """Get trading performance over time."""
        query = """
        SELECT
            timestamp,
            period,
            tick,
            nlv
        FROM trader_info
        ORDER BY timestamp
        """
        return pd.read_sql_query(query, self.conn)

    def get_position_history(self, ticker: str = None) -> pd.DataFrame:
        """Get position history."""
        query = """
        SELECT
            timestamp,
            period,
            tick,
            ticker,
            position,
            unrealized,
            realized
        FROM securities
        WHERE position != 0
        """
        params = []

        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)

        query += " ORDER BY timestamp"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_time_and_sales(self, ticker: str, period: int = None) -> pd.DataFrame:
        """Get time and sales data."""
        query = """
        SELECT
            timestamp,
            ticker,
            period,
            tick,
            price,
            quantity
        FROM time_and_sales
        WHERE ticker = ?
        """
        params = [ticker]

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY tas_id"

        return pd.read_sql_query(query, self.conn, params=params)

    def calculate_vwap(self, ticker: str, period: int = None) -> float:
        """Calculate VWAP for a security."""
        tas = self.get_time_and_sales(ticker, period)

        if tas.empty:
            return 0.0

        total_value = (tas['price'] * tas['quantity']).sum()
        total_volume = tas['quantity'].sum()

        return total_value / total_volume if total_volume > 0 else 0.0

    def get_liquidity_analysis(self, ticker: str, period: int = None) -> Dict[str, Any]:
        """Analyze liquidity for a security."""
        # Get order book data
        books = self.get_order_book_snapshots(ticker, period)

        if books.empty:
            return {}

        # Calculate average depth
        bid_depth = books[books['side'] == 'BID'].groupby('timestamp')['quantity'].sum()
        ask_depth = books[books['side'] == 'ASK'].groupby('timestamp')['quantity'].sum()

        # Get spread data
        prices = self.get_price_history(ticker, period)

        return {
            'avg_bid_depth': bid_depth.mean() if not bid_depth.empty else 0,
            'avg_ask_depth': ask_depth.mean() if not ask_depth.empty else 0,
            'max_bid_depth': bid_depth.max() if not bid_depth.empty else 0,
            'max_ask_depth': ask_depth.max() if not ask_depth.empty else 0,
            'avg_spread': prices['spread'].mean() if not prices.empty else 0,
            'avg_spread_pct': (prices['spread'] / prices['mid_price'] * 100).mean()
                if not prices.empty and prices['mid_price'].any() else 0
        }

    def export_all_to_excel(self, output_path: str = None):
        """Export all data to an Excel file with multiple sheets."""
        if output_path is None:
            output_path = DATA_DIR / f"rit_data_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Securities summary
            self.get_securities_summary().to_excel(writer, sheet_name='Securities Summary', index=False)

            # Tenders
            self.get_all_tenders().to_excel(writer, sheet_name='Tenders', index=False)

            # Tender analysis
            tender_analysis = self.get_tender_analysis()
            if not tender_analysis.empty:
                tender_analysis.to_excel(writer, sheet_name='Tender Analysis', index=False)

            # News
            self.get_news_summary().to_excel(writer, sheet_name='News', index=False)

            # Trading performance
            self.get_trading_performance().to_excel(writer, sheet_name='Performance', index=False)

            # Position history
            self.get_position_history().to_excel(writer, sheet_name='Positions', index=False)

        print(f"Data exported to: {output_path}")
        return output_path

    def print_summary(self):
        """Print a summary of collected data."""
        print("\n" + "=" * 60)
        print("RIT DATA COLLECTION SUMMARY")
        print("=" * 60)

        # Count records
        tables = ['securities', 'order_book', 'tenders', 'news',
                  'time_and_sales', 'trader_info', 'case_info']

        for table in tables:
            cursor = self.conn.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"  {table}: {count:,} records")

        # Securities summary
        summary = self.get_securities_summary()
        if not summary.empty:
            print("\n" + "-" * 40)
            print("SECURITIES TRACKED:")
            print("-" * 40)
            for _, row in summary.iterrows():
                print(f"  {row['ticker']}: {row['data_points']:,} data points, "
                      f"avg price: ${row['avg_price']:.2f}")

        # Tender summary
        tenders = self.get_all_tenders()
        if not tenders.empty:
            print("\n" + "-" * 40)
            print(f"TENDERS: {len(tenders)} received")
            print("-" * 40)
            for _, t in tenders.iterrows():
                print(f"  #{t['tender_id']}: {t['action']} {t['quantity']:,} {t['ticker']} @ ${t['price']}")

        print("\n" + "=" * 60)


def main():
    """Run analysis and print summary."""
    analyzer = DataAnalyzer()

    try:
        analyzer.print_summary()

        # Export to Excel
        print("\nExporting data to Excel...")
        output_path = analyzer.export_all_to_excel()
        print(f"Export complete: {output_path}")

    finally:
        analyzer.close()


if __name__ == "__main__":
    main()
