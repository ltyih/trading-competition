================================================================================
                    RIT DATA COLLECTOR - LIQUIDITY RISK CASE
                    RITC 2026 - Rotman International Trading Competition
================================================================================

OVERVIEW
--------
This is a 24/7 data collection system for the RIT Liquidity Risk Case. It
automatically captures all market data from the RIT API including:

- Prices and quotes for all securities
- Full order book depth
- Tender offers (private, competitive, winner-take-all)
- News announcements
- Time and sales (trade history)
- OHLC price history
- Trading positions and P&L
- Trading limits

All data is stored in a SQLite database for later analysis.

================================================================================
QUICK START
================================================================================

1. PREREQUISITES
   - Python 3.8+ installed
   - RIT Client application installed and running
   - Login credentials:
     * Server: flserver.rotman.utoronto.ca
     * Port: 16500
     * Username: kanish10
     * Password: Kanish@123

2. GET YOUR API KEY
   a. Open the RIT Client application
   b. Login with the credentials above
   c. Click on the "API" icon in the status bar at the bottom
   d. Copy the "API Key" value (looks like: ABC12345)

3. INSTALL DEPENDENCIES
   Open Command Prompt in this folder and run:

   pip install -r requirements.txt

4. START THE COLLECTOR
   Option A - Using batch file:
       Double-click "start_collector.bat"
       Enter your API key when prompted

   Option B - From command line:
       python main.py

5. VIEWING COLLECTED DATA
   Run the analysis script:
       python analyze_data.py

   This will:
   - Print a summary of all collected data
   - Export everything to an Excel file in the data/ folder

================================================================================
FILE STRUCTURE
================================================================================

Liquidity_risk_case/
├── config.py           # Configuration settings
├── models.py           # Database models and storage
├── rit_client.py       # RIT API client with auto-reconnect
├── data_collector.py   # Main data collection logic
├── main.py             # Entry point script
├── watchdog.py         # Process monitor for 24/7 operation
├── analyze_data.py     # Data analysis utilities
├── start_collector.bat # Windows batch file to start
├── requirements.txt    # Python dependencies
├── data/               # Data storage directory
│   ├── rit_data.db     # SQLite database (created automatically)
│   └── csv_exports/    # CSV exports (created automatically)
└── logs/               # Log files directory

================================================================================
24/7 OPERATION
================================================================================

For unattended 24/7 data collection:

1. Use the watchdog script:
       python watchdog.py --api-key YOUR_API_KEY

2. Create Windows startup scripts:
       python watchdog.py --create-startup

   This creates:
   - start_collector.bat (visible window)
   - start_collector_hidden.vbs (runs in background)

3. Add to Windows startup:
   a. Press Win+R
   b. Type: shell:startup
   c. Copy start_collector_hidden.vbs to that folder

AUTO-RECONNECTION
-----------------
The system automatically handles:
- API disconnections (will retry)
- RIT Client logouts (will wait and alert)
- Network interruptions (will reconnect)
- Process crashes (watchdog will restart)

================================================================================
DATA ANALYSIS
================================================================================

The SQLite database (data/rit_data.db) contains the following tables:

- case_info         : Case status, tick, period information
- trader_info       : Your NLV, trader ID
- trading_limits    : Gross/net limits and current usage
- securities        : Price, position, spread for each security
- order_book        : Full order book snapshots
- ohlc_history      : OHLC candlestick data
- time_and_sales    : All executed trades
- tenders           : All tender offers received
- news              : News announcements
- orders            : Your orders
- connection_log    : Connection events
- system_events     : System logs

To query the database:
    import sqlite3
    import pandas as pd

    conn = sqlite3.connect('data/rit_data.db')
    df = pd.read_sql_query("SELECT * FROM securities WHERE ticker='RITC'", conn)

================================================================================
TROUBLESHOOTING
================================================================================

1. "Connection refused" error
   - Make sure RIT Client is running
   - Check that you're logged in
   - Verify the API is enabled (API icon should be green)

2. "401 Unauthorized" error
   - API key may be incorrect or expired
   - Re-login to RIT Client and get a new API key

3. No data being collected
   - Check if the case is running (status should be ACTIVE)
   - Wait for the case to start if it shows tick = 0

4. Database locked errors
   - Only run one collector instance at a time
   - Close any Excel files that might be accessing the database

================================================================================
IMPORTANT NOTES FOR COMPETITION
================================================================================

1. TENDER EVALUATION
   The case PDF mentions three types of tenders:
   - Private Tenders: Fixed price, your decision to accept/decline
   - Competitive Auctions: Submit your price, beats reserve = filled
   - Winner-Take-All: Best bid/offer wins the entire tender

2. AVOID SPECULATION
   - Only trade to unwind positions from accepted tenders
   - Trading without an active tender = speculative = PENALTIES
   - Front-running (trading before accepting/declining) = PENALTIES

3. PENALTIES
   - $1/share for first 5,000 speculative shares
   - $2/share for additional speculative shares
   - $10/share for unclosed positions at end

4. LIMITS
   - Gross limit: 250,000 shares
   - Net limit: 150,000 shares
   - Max order size: 10,000 shares

================================================================================
CONTACT / SUPPORT
================================================================================

For issues with this collector, check the logs in the logs/ folder.

For RIT platform issues: https://rit.rotman.utoronto.ca

================================================================================
