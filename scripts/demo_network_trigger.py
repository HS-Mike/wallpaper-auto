"""Network Monitor Demo Script

Demonstrates NetworkMonitor network change detection:
- Monitors WiFi hotspot changes
- Monitors wired/wireless adapter connection changes
- Shows current SSID and network fingerprint

Usage:
    python demo_network_monitor.py
"""
import logging
import signal
import sys
import time
import threading

import pythoncom

from wallpaper_auto.trigger.network_trigger import NetworkTrigger
from wallpaper_auto.evaluator.wifi_ssid_evaluator import get_current_ssid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def on_network_change() -> None:
    """Network change callback"""
    ssid = get_current_ssid()
    print(f"Current SSID: {ssid}")



def main() -> None:
    monitor = NetworkTrigger()
    monitor.add_callback(on_network_change)

    # show initial state
    ssid = get_current_ssid()
    logger.info(f"Initial SSID: {ssid}")

    is_cleared = threading.Event()
    # shutdown handler
    def signal_handler(signum, frame):
        logger.info("Received signal, shutting down...")
        monitor.deactivate()
        is_cleared.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Network monitor started, press Ctrl+C to exit")
    monitor.activate()
    while not is_cleared.is_set():
        time.sleep(0.2)


if __name__ == "__main__":
    main()
