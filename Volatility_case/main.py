#!/usr/bin/env python3
"""
RIT Data Collector - Volatility Case Entry Point

24/7 data collection system for the RIT Volatility Trading Case.
Captures all market data including:
- Options prices and Greeks (delta, gamma, theta, vega)
- Implied volatility
- Portfolio delta tracking
- Volatility announcements from news
- Order books, time & sales, positions

Usage:
    python main.py [--api-key YOUR_API_KEY]

The API key can be found in the RIT Client by clicking on the API icon.
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import LOG_FILE, LOG_LEVEL, LOGS_DIR, API_KEY, UNDERLYING_TICKER, CALL_OPTIONS, PUT_OPTIONS
from data_collector import DataCollector


def setup_logging():
    """Configure logging for the application."""
    LOGS_DIR.mkdir(exist_ok=True)

    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )

    # File handler (rotates daily)
    log_file = LOGS_DIR / f"collector_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Suppress noisy third-party library loggers (urllib3, requests, etc.)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('chardet').setLevel(logging.WARNING)

    return logging.getLogger(__name__)


def print_banner():
    """Print startup banner."""
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║  ██╗   ██╗ ██████╗ ██╗      █████╗ ████████╗██╗██╗     ██╗████████╗██╗   ██╗    ║
║  ██║   ██║██╔═══██╗██║     ██╔══██╗╚══██╔══╝██║██║     ██║╚══██╔══╝╚██╗ ██╔╝    ║
║  ██║   ██║██║   ██║██║     ███████║   ██║   ██║██║     ██║   ██║    ╚████╔╝     ║
║  ╚██╗ ██╔╝██║   ██║██║     ██╔══██║   ██║   ██║██║     ██║   ██║     ╚██╔╝      ║
║   ╚████╔╝ ╚██████╔╝███████╗██║  ██║   ██║   ██║███████╗██║   ██║      ██║       ║
║    ╚═══╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝╚══════╝╚═╝   ╚═╝      ╚═╝       ║
║                                                                  ║
║              COLLECTOR - Volatility Trading Case                 ║
║                                                                  ║
║     24/7 Options Data Collection System                          ║
║     RITC 2026 - Rotman International Trading Competition         ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
    """
    print(banner)

    print("\n" + "=" * 60)
    print("SECURITIES TRACKED")
    print("=" * 60)
    print(f"  Underlying ETF: {UNDERLYING_TICKER}")
    print(f"  Call Options ({len(CALL_OPTIONS)}): {', '.join(CALL_OPTIONS)}")
    print(f"  Put Options ({len(PUT_OPTIONS)}): {', '.join(PUT_OPTIONS)}")
    print("=" * 60)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='RIT Data Collector for Volatility Trading Case'
    )
    parser.add_argument(
        '--api-key', '-k',
        type=str,
        default=None,
        help='API key from RIT Client (find it by clicking API icon in RIT)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Setup logging
    logger = setup_logging()

    # Print banner
    print_banner()

    # Get API key
    api_key = args.api_key or API_KEY

    if not api_key:
        print("\n" + "=" * 60)
        print("IMPORTANT: API Key Required")
        print("=" * 60)
        print("""
To get your API key:
1. Open the RIT Client application
2. Login with your credentials:
   - Server: flserver.rotman.utoronto.ca
   - Port: 16500
3. Click on the 'API' icon in the status bar
4. Copy the API Key value
5. Run this script again with: python main.py --api-key YOUR_KEY

Or set it in config.py as API_KEY = "YOUR_KEY"
        """)
        print("=" * 60)

        api_key = input("\nEnter API key (or press Enter to wait for connection): ").strip()

        if not api_key:
            print("\nNo API key provided. Will attempt to connect without it...")

    # Start collector
    logger.info("Starting Volatility Case data collector...")
    logger.info(f"API Key: {'*' * (len(api_key) - 4) + api_key[-4:] if api_key and len(api_key) > 4 else 'Not provided'}")

    # Run with auto-restart on crash
    restart_count = 0
    base_restart_delay = 10

    while True:
        try:
            collector = DataCollector(api_key=api_key)
            collector.start()

            logger.info("Collector stopped gracefully")
            break

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt - shutting down")
            break

        except Exception as e:
            restart_count += 1
            restart_delay = min(base_restart_delay * (2 ** min(restart_count - 1, 6)), 600)

            logger.error(f"Collector crashed: {e}")
            logger.error(f"Restart attempt #{restart_count} - will retry in {restart_delay}s")

            try:
                import traceback
                error_log = LOGS_DIR / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                with open(error_log, 'w') as f:
                    f.write(f"Crash at {datetime.now().isoformat()}\n")
                    f.write(f"Restart count: {restart_count}\n")
                    f.write(f"Error: {e}\n\n")
                    traceback.print_exc(file=f)
                logger.info(f"Crash details saved to {error_log}")
            except:
                pass

            logger.info(f"Restarting in {restart_delay} seconds... (Ctrl+C to stop)")
            time.sleep(restart_delay)

    logger.info("Volatility Case data collector finished.")


if __name__ == "__main__":
    main()
