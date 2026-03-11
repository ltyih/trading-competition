#!/usr/bin/env python3
"""
RIT Merger Arbitrage Case - Data Collector Entry Point

24/7 data collection system for the *REMOVED* 2026 Merger Arbitrage Case.
Captures all market data including prices, order books, news, deal spreads,
implied probabilities, and more.

Usage:
    python main.py [--api-key YOUR_API_KEY]
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import LOG_FILE, LOG_LEVEL, LOGS_DIR, API_KEY, DEALS
from data_collector import DataCollector


def setup_logging():
    LOGS_DIR.mkdir(exist_ok=True)

    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )

    log_file = LOGS_DIR / f"merger_arb_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return logging.getLogger(__name__)


def print_banner():
    print("=" * 60)
    print("  MERGER ARBITRAGE Data Collector - *REMOVED* 2026")
    print("  5 Deals | 10 Securities | News-Driven Probabilities")
    print("  24/7 Market Data Collection System")
    print("=" * 60)
    print("Deals monitored:")
    for did, d in DEALS.items():
        print(f"  {did}: {d['target']}/{d['acquirer']} - {d['name']} "
              f"({d['structure']}) p0={d['initial_prob']:.0%}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='RIT Merger Arbitrage Case Data Collector'
    )
    parser.add_argument('--api-key', '-k', type=str, default=None,
                        help='API key from RIT Client')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()
    logger = setup_logging()

    print_banner()

    api_key = args.api_key or API_KEY

    if not api_key:
        print("\n" + "=" * 60)
        print("IMPORTANT: API Key Required")
        print("=" * 60)
        print("""
To get your API key:
1. Open the RIT Client application
2. Login with your credentials
3. Click on the 'API' icon in the status bar
4. Copy the API Key value
5. Run: python main.py --api-key YOUR_KEY
   Or set it in config.py as API_KEY = "YOUR_KEY"
        """)
        api_key = input("\nEnter API key (or press Enter to wait): ").strip()
        if not api_key:
            print("No API key provided. Will attempt to connect without it...")

    logger.info("Starting Merger Arbitrage Data Collector...")
    logger.info(f"API Key: {'*' * (len(api_key) - 4) + api_key[-4:] if api_key and len(api_key) > 4 else 'Not provided'}")

    # Run with auto-restart for 24/7 operation
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
            logger.error(f"Restart #{restart_count} in {restart_delay}s")

            try:
                import traceback
                error_log = LOGS_DIR / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                with open(error_log, 'w') as f:
                    f.write(f"Crash at {datetime.now().isoformat()}\n")
                    f.write(f"Restart count: {restart_count}\n")
                    f.write(f"Error: {e}\n\n")
                    traceback.print_exc(file=f)
            except:
                pass

            logger.info(f"Restarting in {restart_delay}s... (Ctrl+C to stop)")
            time.sleep(restart_delay)

    logger.info("Merger Arbitrage data collector finished.")


if __name__ == "__main__":
    main()
