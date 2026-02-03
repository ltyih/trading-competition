#!/usr/bin/env python3
"""
RIT Data Collector - Main Entry Point

24/7 data collection system for the RIT Liquidity Risk Case.
Captures all market data including prices, order books, tenders, news, etc.

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

from config import LOG_FILE, LOG_LEVEL, LOGS_DIR, API_KEY
from data_collector import DataCollector
from models import init_database

# Setup logging
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

    return logging.getLogger(__name__)


def print_banner():
    """Print startup banner."""
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║     ██████╗ ██╗████████╗    ██████╗  █████╗ ████████╗ █████╗     ║
║     ██╔══██╗██║╚══██╔══╝    ██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗    ║
║     ██████╔╝██║   ██║       ██║  ██║███████║   ██║   ███████║    ║
║     ██╔══██╗██║   ██║       ██║  ██║██╔══██║   ██║   ██╔══██║    ║
║     ██║  ██║██║   ██║       ██████╔╝██║  ██║   ██║   ██║  ██║    ║
║     ╚═╝  ╚═╝╚═╝   ╚═╝       ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝    ║
║                                                                  ║
║              COLLECTOR - Liquidity Risk Case                     ║
║                                                                  ║
║     24/7 Market Data Collection System                           ║
║     RITC 2026 - Rotman International Trading Competition         ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
    """
    print(banner)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='RIT Data Collector for Liquidity Risk Case'
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
   - Username: kanish10
   - Password: Kanish@123
3. Click on the 'API' icon in the status bar
4. Copy the API Key value
5. Run this script again with: python main.py --api-key YOUR_KEY

Or set it in config.py as API_KEY = "YOUR_KEY"
        """)
        print("=" * 60)

        # Prompt for API key
        api_key = input("\nEnter API key (or press Enter to wait for connection): ").strip()

        if not api_key:
            print("\nNo API key provided. Will attempt to connect without it...")
            print("(This may fail if the RIT Client requires authentication)")

    # Initialize database
    logger.info("Initializing database...")
    init_database()

    # Start collector
    logger.info("Starting data collector...")
    logger.info(f"API Key: {'*' * (len(api_key) - 4) + api_key[-4:] if api_key and len(api_key) > 4 else 'Not provided'}")

    # Run with auto-restart on crash
    max_restarts = 10
    restart_count = 0
    restart_delay = 30  # seconds

    while restart_count < max_restarts:
        try:
            collector = DataCollector(api_key=api_key)
            collector.start()

            # If we get here normally (graceful shutdown), exit
            break

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt - shutting down")
            break

        except Exception as e:
            restart_count += 1
            logger.error(f"Collector crashed: {e}")
            logger.error(f"Restart attempt {restart_count}/{max_restarts}")

            if restart_count < max_restarts:
                logger.info(f"Restarting in {restart_delay} seconds...")
                time.sleep(restart_delay)
            else:
                logger.error("Max restarts exceeded. Exiting.")
                sys.exit(1)

    logger.info("Data collector finished.")


if __name__ == "__main__":
    main()
