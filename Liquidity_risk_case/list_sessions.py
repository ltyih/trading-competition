#!/usr/bin/env python3
"""
List all recorded data collection sessions.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from session_manager import list_all_sessions, SessionManager
import pandas as pd
import sqlite3


def analyze_session(session_id: str):
    """Analyze a specific session in detail."""
    manager = SessionManager()
    session = manager.get_session_summary(session_id)

    if not session:
        print(f"Session '{session_id}' not found.")
        return

    db_path = session.get('db_path')
    if not db_path or not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    print(f"\n{'='*60}")
    print(f"SESSION ANALYSIS: {session_id}")
    print(f"{'='*60}")

    conn = sqlite3.connect(db_path)

    # Securities summary
    print("\n--- SECURITIES ---")
    df = pd.read_sql_query("""
        SELECT
            ticker,
            COUNT(*) as records,
            MIN(tick) as first_tick,
            MAX(tick) as last_tick,
            AVG(last_price) as avg_price,
            MIN(last_price) as min_price,
            MAX(last_price) as max_price,
            AVG(spread) as avg_spread
        FROM securities
        WHERE last_price > 0
        GROUP BY ticker
    """, conn)
    if not df.empty:
        print(df.to_string(index=False))
    else:
        print("No securities data")

    # Tenders summary
    print("\n--- TENDERS ---")
    df = pd.read_sql_query("""
        SELECT
            tender_id,
            ticker,
            action,
            quantity,
            price,
            expires as expires_tick,
            status
        FROM tenders
        ORDER BY tender_id
    """, conn)
    if not df.empty:
        print(df.to_string(index=False))
    else:
        print("No tenders")

    # P&L over time
    print("\n--- P&L PROGRESSION ---")
    df = pd.read_sql_query("""
        SELECT tick, nlv
        FROM trader_info
        ORDER BY tick
    """, conn)
    if not df.empty and len(df) > 1:
        first_nlv = df.iloc[0]['nlv']
        last_nlv = df.iloc[-1]['nlv']
        print(f"Starting NLV: ${first_nlv:,.2f}")
        print(f"Ending NLV: ${last_nlv:,.2f}")
        print(f"Profit/Loss: ${last_nlv - first_nlv:,.2f}")

    conn.close()

    # Export to Excel
    export_path = Path(db_path).parent / f"{session_id}_analysis.xlsx"
    export_session_to_excel(db_path, export_path)
    print(f"\nExported to: {export_path}")


def export_session_to_excel(db_path: str, output_path: str):
    """Export a session's data to Excel."""
    conn = sqlite3.connect(db_path)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # Securities
        df = pd.read_sql_query("SELECT * FROM securities ORDER BY tick", conn)
        df.to_excel(writer, sheet_name='Securities', index=False)

        # Order Book (sample - last snapshot per ticker)
        df = pd.read_sql_query("""
            SELECT * FROM order_book
            WHERE (ticker, tick) IN (
                SELECT ticker, MAX(tick) FROM order_book GROUP BY ticker
            )
            ORDER BY ticker, side, level
        """, conn)
        df.to_excel(writer, sheet_name='Order Book (Last)', index=False)

        # Tenders
        df = pd.read_sql_query("SELECT * FROM tenders ORDER BY tender_id", conn)
        df.to_excel(writer, sheet_name='Tenders', index=False)

        # News
        df = pd.read_sql_query("SELECT * FROM news ORDER BY news_id", conn)
        df.to_excel(writer, sheet_name='News', index=False)

        # Time & Sales
        df = pd.read_sql_query("SELECT * FROM time_and_sales ORDER BY tas_id LIMIT 10000", conn)
        df.to_excel(writer, sheet_name='Time & Sales', index=False)

        # Trader P&L
        df = pd.read_sql_query("SELECT * FROM trader_info ORDER BY tick", conn)
        df.to_excel(writer, sheet_name='Trader PnL', index=False)

    conn.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description='List and analyze RIT data sessions')
    parser.add_argument('--analyze', '-a', type=str, help='Analyze a specific session ID')
    parser.add_argument('--export', '-e', type=str, help='Export session to Excel')

    args = parser.parse_args()

    if args.analyze:
        analyze_session(args.analyze)
    elif args.export:
        manager = SessionManager()
        session = manager.get_session_summary(args.export)
        if session and session.get('db_path'):
            output = Path(session['db_path']).parent / f"{args.export}_export.xlsx"
            export_session_to_excel(session['db_path'], output)
            print(f"Exported to: {output}")
        else:
            print(f"Session not found: {args.export}")
    else:
        list_all_sessions()


if __name__ == "__main__":
    main()
