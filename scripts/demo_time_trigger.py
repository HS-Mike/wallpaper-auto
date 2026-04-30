"""Time Monitor Demo Script

Demonstrates DynamicTimeMonitor with two trigger modes:
1. Fixed time triggers - fires at specified times daily
2. Interval triggers - fires at regular intervals

Usage:
    python demo_time_monitor.py
"""
import datetime
import logging
import signal
import time
import sys

from wallpaper_automator.trigger.time_trigger import TimeTrigger

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def on_time_trigger() -> None:
    """Time trigger callback"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[Trigger] Current time: {now}")


def main() -> None:
    monitor = TimeTrigger()
    monitor.add_callback(on_time_trigger)

    # Mode 1: Fixed time triggers (daily at 09:00 and 18:00)
    monitor.update_fixed_times([
        datetime.time(9, 0),
        datetime.time(18, 0),
    ])
    logger.info("Fixed time triggers set: 09:00, 18:00")

    # Mode 2: Interval trigger (every 10 seconds for demo)
    # In production use datetime.timedelta(minutes=30) etc.
    monitor.set_interval(datetime.timedelta(seconds=10))
    logger.info("Interval trigger set: every 10 seconds")

    # Graceful shutdown handler
    def signal_handler(signum, frame):
        logger.info("Received signal, shutting down...")
        monitor.deactivate()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Time monitor started, press Ctrl+C to exit")
    monitor.activate()

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
