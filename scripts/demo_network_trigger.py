"""
Network Monitor Demo Script

Demonstrates NetworkMonitor network change detection:
- Monitors WiFi hotspot changes
- Monitors wired/wireless adapter connection changes
- Shows current SSID and network fingerprint

Usage:
    python demo_network_monitor.py
"""
import logging
import threading
import time
from datetime import datetime

import pythoncom

from wallpaper_auto.trigger.network_trigger import NetworkTrigger, get_current_ssid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def on_network_change(trigger: NetworkTrigger) -> None:
    """Network change callback — print current SSID from trigger."""
    ssid = trigger.current_ssid
    print(f"\n=== {datetime.now():%H:%M:%S} Network Change Detected ===")
    print(f"  Current SSID: {ssid}")
    print("=========================================\n")


def main() -> None:
    monitor = NetworkTrigger()
    monitor.add_callback(on_network_change)

    shutdown_event = threading.Event()

    monitor.activate()
    logger.info("NetworkTrigger started, press Ctrl+C to exit")

    initial_ssid = get_current_ssid()
    logger.info(f"Initial SSID: {initial_ssid}")
    
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
    finally:
        logger.info("Shutting down NetworkTrigger...")
        monitor.deactivate()
        shutdown_event.set()
        logger.info("Demo script exited safely.")


if __name__ == "__main__":
    main()
