"""
Watchdog Script for RIT Data Collector

Ensures the data collector stays running 24/7.
Monitors the collector process and restarts it if it crashes.
Also monitors the RIT Client application and attempts to restart if needed.
"""
import subprocess
import sys
import time
import logging
import psutil
import os
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
COLLECTOR_SCRIPT = Path(__file__).parent / "main.py"
VENV_PYTHON = Path(__file__).parent / "venv" / "Scripts" / "python.exe"
CHECK_INTERVAL = 30  # seconds
MAX_RESTARTS_PER_HOUR = 5
LOG_FILE = Path(__file__).parent / "logs" / "watchdog.log"

# RIT Client detection
RIT_PROCESS_NAMES = ['RIT.exe', 'rit.exe', 'RIT2.exe', '*REMOVED*']

# Setup logging
LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ProcessWatchdog:
    """Monitors and manages the data collector process."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self.collector_process = None
        self.restart_times = []
        self.running = True

    def is_rit_client_running(self) -> bool:
        """Check if RIT Client is running."""
        for proc in psutil.process_iter(['name']):
            try:
                name = proc.info['name']
                if any(rit_name.lower() in name.lower() for rit_name in RIT_PROCESS_NAMES):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def is_collector_running(self) -> bool:
        """Check if the data collector is running."""
        if self.collector_process is None:
            return False

        # Check if process is still alive
        poll = self.collector_process.poll()
        return poll is None

    def start_collector(self) -> bool:
        """Start the data collector process."""
        logger.info("Starting data collector...")

        # Check restart rate limiting
        now = datetime.now()
        self.restart_times = [t for t in self.restart_times if now - t < timedelta(hours=1)]

        if len(self.restart_times) >= MAX_RESTARTS_PER_HOUR:
            logger.error(f"Max restarts per hour ({MAX_RESTARTS_PER_HOUR}) exceeded. "
                        "Manual intervention required.")
            return False

        try:
            # Use venv Python if available, otherwise system Python
            python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
            cmd = [python_exe, str(COLLECTOR_SCRIPT)]
            if self.api_key:
                cmd.extend(['--api-key', self.api_key])

            self.collector_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )

            self.restart_times.append(now)
            logger.info(f"Data collector started with PID: {self.collector_process.pid}")
            return True

        except Exception as e:
            logger.error(f"Failed to start collector: {e}")
            return False

    def stop_collector(self):
        """Stop the data collector process."""
        if self.collector_process:
            logger.info("Stopping data collector...")
            try:
                self.collector_process.terminate()
                self.collector_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.collector_process.kill()
            self.collector_process = None

    def run(self):
        """Main watchdog loop."""
        logger.info("=" * 50)
        logger.info("RIT Data Collector Watchdog Starting")
        logger.info("=" * 50)
        logger.info(f"Monitoring interval: {CHECK_INTERVAL}s")
        logger.info(f"Collector script: {COLLECTOR_SCRIPT}")

        # Initial start
        self.start_collector()

        try:
            while self.running:
                time.sleep(CHECK_INTERVAL)

                # Check RIT Client
                if not self.is_rit_client_running():
                    logger.warning("RIT Client not detected. Collector may not function properly.")

                # Check collector
                if not self.is_collector_running():
                    logger.warning("Data collector is not running!")

                    # Get exit code if available
                    if self.collector_process:
                        exit_code = self.collector_process.poll()
                        logger.info(f"Collector exit code: {exit_code}")

                    # Restart
                    logger.info("Attempting to restart collector...")
                    if not self.start_collector():
                        logger.error("Failed to restart collector. Waiting before retry...")
                        time.sleep(60)
                else:
                    # Log periodic status
                    if datetime.now().minute % 5 == 0 and datetime.now().second < CHECK_INTERVAL:
                        logger.info(f"Collector running OK (PID: {self.collector_process.pid})")

        except KeyboardInterrupt:
            logger.info("Watchdog interrupted by user")

        finally:
            self.stop_collector()
            logger.info("Watchdog stopped")


def create_startup_script():
    """Create a Windows startup script/batch file."""
    batch_content = f'''@echo off
echo Starting RIT Data Collector Watchdog...
cd /d "{Path(__file__).parent}"
call venv\\Scripts\\activate.bat
python watchdog.py
pause
'''

    batch_file = Path(__file__).parent / "start_collector.bat"
    batch_file.write_text(batch_content)
    logger.info(f"Created startup script: {batch_file}")

    # Create a VBS script for hidden startup
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "{batch_file}" & chr(34), 0
Set WshShell = Nothing
'''

    vbs_file = Path(__file__).parent / "start_collector_hidden.vbs"
    vbs_file.write_text(vbs_content)
    logger.info(f"Created hidden startup script: {vbs_file}")

    print(f"""
Startup scripts created!

To run visibly (with console window):
    {batch_file}

To run hidden (no window):
    {vbs_file}

To add to Windows startup:
1. Press Win+R
2. Type: shell:startup
3. Copy the .vbs file to that folder
    """)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='RIT Data Collector Watchdog')
    parser.add_argument('--api-key', '-k', type=str, help='API key for RIT Client')
    parser.add_argument('--create-startup', action='store_true',
                       help='Create Windows startup scripts')

    args = parser.parse_args()

    if args.create_startup:
        create_startup_script()
        return

    watchdog = ProcessWatchdog(api_key=args.api_key)
    watchdog.run()


if __name__ == "__main__":
    main()
