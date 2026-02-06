"""
Volatility Case Data Analysis Tools

Provides analysis tools for:
- Options pricing and Greeks analysis
- Implied volatility surface
- Portfolio delta tracking
- Volatility announcement impact
- Price and volume analysis
"""
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from config import (
    SESSIONS_DIR, DATA_DIR, OPTIONS_METADATA, UNDERLYING_TICKER,
    STRIKE_PRICES, CALL_OPTIONS, PUT_OPTIONS, OPTIONS_MULTIPLIER
)
from session_manager import SessionManager


class VolatilityAnalyzer:
    """
    Analysis tools for Volatility Case data.
    """

    def __init__(self, db_path: str = None, session_id: str = None):
        """
        Initialize analyzer.

        Args:
            db_path: Direct path to database file
            session_id: Session ID to load from sessions index
        """
        if db_path:
            self.db_path = Path(db_path)
        elif session_id:
            manager = SessionManager()
            for s in manager.list_sessions():
                if s['session_id'] == session_id:
                    self.db_path = Path(s['db_path'])
                    break
            else:
                raise ValueError(f"Session {session_id} not found")
        else:
            # Use most recent session
            manager = SessionManager()
            sessions = manager.list_sessions()
            if not sessions:
                raise ValueError("No sessions found")
            self.db_path = Path(sessions[-1]['db_path'])

        self.conn = None

    def connect(self):
        """Connect to database."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        return self

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # =========================================================================
    # OPTIONS ANALYSIS
    # =========================================================================

    def get_options_data(self, ticker: str = None, period: int = None,
                         tick_range: tuple = None) -> pd.DataFrame:
        """
        Get options snapshot data.

        Args:
            ticker: Specific option ticker (e.g., 'RTM1C50')
            period: Period filter
            tick_range: Tuple of (start_tick, end_tick)
        """
        query = "SELECT * FROM options_snapshots WHERE 1=1"
        params = []

        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        if period is not None:
            query += " AND period = ?"
            params.append(period)
        if tick_range:
            query += " AND tick BETWEEN ? AND ?"
            params.extend(tick_range)

        query += " ORDER BY tick, ticker"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_options_chain(self, tick: int, period: int = None) -> pd.DataFrame:
        """
        Get full options chain at a specific tick.
        Returns calls and puts with all Greeks.
        """
        query = """
            SELECT * FROM options_snapshots
            WHERE tick = ?
        """
        params = [tick]

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY strike_price, option_type"

        df = pd.read_sql_query(query, self.conn, params=params)

        if df.empty:
            return df

        # Pivot to create chain format
        calls = df[df['option_type'] == 'CALL'].copy()
        puts = df[df['option_type'] == 'PUT'].copy()

        calls = calls.add_prefix('call_')
        puts = puts.add_prefix('put_')

        calls = calls.rename(columns={'call_strike_price': 'strike'})
        puts = puts.rename(columns={'put_strike_price': 'strike'})

        chain = pd.merge(calls, puts, on='strike', how='outer')
        return chain.sort_values('strike')

    def get_iv_surface(self, period: int = None) -> pd.DataFrame:
        """
        Get implied volatility surface data.
        Returns IV by strike and tick (time).
        """
        query = """
            SELECT tick, ticker, strike_price, option_type, implied_volatility, days_to_expiry
            FROM options_snapshots
            WHERE implied_volatility IS NOT NULL
        """
        params = []

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY tick, strike_price"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_greeks_time_series(self, ticker: str, period: int = None) -> pd.DataFrame:
        """
        Get Greeks time series for a specific option.
        """
        query = """
            SELECT tick, delta, gamma, theta, vega, rho, implied_volatility,
                   last_price, underlying_price, days_to_expiry
            FROM options_snapshots
            WHERE ticker = ?
        """
        params = [ticker]

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY tick"

        return pd.read_sql_query(query, self.conn, params=params)

    # =========================================================================
    # PORTFOLIO DELTA ANALYSIS
    # =========================================================================

    def get_portfolio_delta(self, period: int = None, tick_range: tuple = None) -> pd.DataFrame:
        """
        Get portfolio delta history.
        """
        query = "SELECT * FROM portfolio_delta WHERE 1=1"
        params = []

        if period is not None:
            query += " AND period = ?"
            params.append(period)
        if tick_range:
            query += " AND tick BETWEEN ? AND ?"
            params.extend(tick_range)

        query += " ORDER BY tick"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_delta_violations(self, period: int = None) -> pd.DataFrame:
        """
        Get ticks where portfolio delta exceeded limit.
        """
        query = """
            SELECT * FROM portfolio_delta
            WHERE is_over_limit = 1
        """
        params = []

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY tick"

        return pd.read_sql_query(query, self.conn, params=params)

    def calculate_total_penalty(self, period: int = None) -> float:
        """
        Calculate total penalty amount from delta violations.
        """
        query = """
            SELECT SUM(penalty_amount) as total_penalty
            FROM portfolio_delta
            WHERE is_over_limit = 1
        """
        params = []

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        result = pd.read_sql_query(query, self.conn, params=params)
        return result['total_penalty'].iloc[0] or 0.0

    # =========================================================================
    # VOLATILITY ANALYSIS
    # =========================================================================

    def get_volatility_announcements(self, period: int = None) -> pd.DataFrame:
        """
        Get all volatility announcements.
        """
        query = """
            SELECT * FROM volatility_announcements
            WHERE 1=1
        """
        params = []

        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY tick"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_volatility_state_at_tick(self, tick: int) -> Dict[str, Any]:
        """
        Get the volatility state at a specific tick.
        Returns the most recent announcements before that tick.
        """
        query = """
            SELECT * FROM volatility_announcements
            WHERE tick <= ?
            ORDER BY tick DESC
        """

        df = pd.read_sql_query(query, self.conn, params=[tick])

        state = {
            'current_volatility': None,
            'forecast_vol_low': None,
            'forecast_vol_high': None,
            'delta_limit': None,
            'penalty_rate': None
        }

        for _, row in df.iterrows():
            if state['current_volatility'] is None and row['current_volatility']:
                state['current_volatility'] = row['current_volatility']
            if state['forecast_vol_low'] is None and row['forecast_vol_low']:
                state['forecast_vol_low'] = row['forecast_vol_low']
                state['forecast_vol_high'] = row['forecast_vol_high']
            if state['delta_limit'] is None and row['delta_limit']:
                state['delta_limit'] = row['delta_limit']
            if state['penalty_rate'] is None and row['penalty_rate']:
                state['penalty_rate'] = row['penalty_rate']

            # Stop if we have all values
            if all(v is not None for v in state.values()):
                break

        return state

    # =========================================================================
    # UNDERLYING ANALYSIS
    # =========================================================================

    def get_underlying_data(self, period: int = None, tick_range: tuple = None) -> pd.DataFrame:
        """
        Get underlying ETF price history.
        """
        query = "SELECT * FROM underlying_snapshots WHERE 1=1"
        params = []

        if period is not None:
            query += " AND period = ?"
            params.append(period)
        if tick_range:
            query += " AND tick BETWEEN ? AND ?"
            params.extend(tick_range)

        query += " ORDER BY tick"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_underlying_volatility(self, period: int = None, window: int = 20) -> pd.DataFrame:
        """
        Calculate realized volatility of underlying.
        """
        df = self.get_underlying_data(period=period)

        if df.empty or len(df) < window:
            return pd.DataFrame()

        df['returns'] = df['last_price'].pct_change()
        df['realized_vol'] = df['returns'].rolling(window=window).std() * np.sqrt(240)  # Annualized

        return df[['tick', 'last_price', 'returns', 'realized_vol']].dropna()

    # =========================================================================
    # PRICE ANALYSIS
    # =========================================================================

    def get_tick_snapshots(self, ticker: str = None, period: int = None) -> pd.DataFrame:
        """
        Get general tick snapshots.
        """
        query = "SELECT * FROM tick_snapshots WHERE 1=1"
        params = []

        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        if period is not None:
            query += " AND period = ?"
            params.append(period)

        query += " ORDER BY tick, ticker"

        return pd.read_sql_query(query, self.conn, params=params)

    def get_price_pivot(self, period: int = None) -> pd.DataFrame:
        """
        Get price data pivoted by ticker (one column per security).
        """
        df = self.get_tick_snapshots(period=period)

        if df.empty:
            return df

        pivot = df.pivot_table(
            index='tick',
            columns='ticker',
            values='last_price',
            aggfunc='last'
        )
        return pivot

    # =========================================================================
    # NEWS ANALYSIS
    # =========================================================================

    def get_news(self, period: int = None, volatility_only: bool = False) -> pd.DataFrame:
        """
        Get news items.
        """
        query = "SELECT * FROM news WHERE 1=1"
        params = []

        if period is not None:
            query += " AND period = ?"
            params.append(period)
        if volatility_only:
            query += " AND is_volatility_announcement = 1"

        query += " ORDER BY tick"

        return pd.read_sql_query(query, self.conn, params=params)

    # =========================================================================
    # EXPORT FUNCTIONS
    # =========================================================================

    def export_to_excel(self, output_path: str = None, period: int = None):
        """
        Export analysis data to Excel with multiple sheets.
        """
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = DATA_DIR / f"volatility_analysis_{timestamp}.xlsx"

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Options data
            try:
                df = self.get_options_data(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='Options_Snapshots', index=False)
            except:
                pass

            # Underlying data
            try:
                df = self.get_underlying_data(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='Underlying', index=False)
            except:
                pass

            # Portfolio delta
            try:
                df = self.get_portfolio_delta(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='Portfolio_Delta', index=False)
            except:
                pass

            # Delta violations
            try:
                df = self.get_delta_violations(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='Delta_Violations', index=False)
            except:
                pass

            # Volatility announcements
            try:
                df = self.get_volatility_announcements(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='Vol_Announcements', index=False)
            except:
                pass

            # IV Surface
            try:
                df = self.get_iv_surface(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='IV_Surface', index=False)
            except:
                pass

            # News
            try:
                df = self.get_news(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='News', index=False)
            except:
                pass

            # Price pivot
            try:
                df = self.get_price_pivot(period=period)
                if not df.empty:
                    df.to_excel(writer, sheet_name='Price_Pivot')
            except:
                pass

        print(f"Exported analysis to: {output_path}")
        return output_path

    # =========================================================================
    # SUMMARY STATISTICS
    # =========================================================================

    def get_session_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics for the session.
        """
        summary = {}

        # Basic counts
        for table in ['securities', 'options_snapshots', 'underlying_snapshots',
                      'portfolio_delta', 'volatility_announcements', 'news',
                      'order_book', 'time_and_sales']:
            try:
                result = pd.read_sql_query(f"SELECT COUNT(*) as count FROM {table}", self.conn)
                summary[f'{table}_records'] = result['count'].iloc[0]
            except:
                summary[f'{table}_records'] = 0

        # Tick range
        try:
            result = pd.read_sql_query(
                "SELECT MIN(tick) as min_tick, MAX(tick) as max_tick FROM securities",
                self.conn
            )
            summary['min_tick'] = result['min_tick'].iloc[0]
            summary['max_tick'] = result['max_tick'].iloc[0]
        except:
            summary['min_tick'] = None
            summary['max_tick'] = None

        # Delta violations
        try:
            violations = self.get_delta_violations()
            summary['delta_violation_count'] = len(violations)
            summary['total_penalty'] = self.calculate_total_penalty()
        except:
            summary['delta_violation_count'] = 0
            summary['total_penalty'] = 0.0

        # Volatility announcements
        try:
            vol_df = self.get_volatility_announcements()
            summary['volatility_announcement_count'] = len(vol_df)
        except:
            summary['volatility_announcement_count'] = 0

        return summary


def list_sessions():
    """List all available sessions."""
    manager = SessionManager()
    sessions = manager.list_sessions()

    if not sessions:
        print("No sessions found.")
        return

    print("\n" + "=" * 70)
    print("AVAILABLE VOLATILITY CASE SESSIONS")
    print("=" * 70)

    for s in sessions:
        print(f"\n{s['session_id']}")
        print(f"  Case: {s.get('case_name', 'N/A')}")
        print(f"  Period: {s.get('period', 'N/A')}")
        print(f"  Start: {s.get('start_time', 'N/A')}")
        print(f"  End: {s.get('end_time', 'In progress')}")
        print(f"  DB: {s.get('db_path', 'N/A')}")


def main():
    """Main function for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(description='Volatility Case Data Analysis')
    parser.add_argument('--list', action='store_true', help='List available sessions')
    parser.add_argument('--session', type=str, help='Session ID to analyze')
    parser.add_argument('--db', type=str, help='Direct database path')
    parser.add_argument('--export', action='store_true', help='Export to Excel')
    parser.add_argument('--summary', action='store_true', help='Print session summary')
    parser.add_argument('--period', type=int, help='Filter by period')

    args = parser.parse_args()

    if args.list:
        list_sessions()
        return

    # Initialize analyzer
    try:
        if args.db:
            analyzer = VolatilityAnalyzer(db_path=args.db)
        elif args.session:
            analyzer = VolatilityAnalyzer(session_id=args.session)
        else:
            analyzer = VolatilityAnalyzer()  # Most recent session
    except Exception as e:
        print(f"Error: {e}")
        return

    with analyzer:
        if args.summary:
            summary = analyzer.get_session_summary()
            print("\n" + "=" * 50)
            print("SESSION SUMMARY")
            print("=" * 50)
            for key, value in summary.items():
                print(f"  {key}: {value}")

        if args.export:
            analyzer.export_to_excel(period=args.period)


if __name__ == "__main__":
    main()
