"""
Windows Session Monitor Demo Script

Demonstrates WindowsSessionMonitor session event monitoring:
- Monitors user logon/logoff
- Monitors workstation lock/unlock
- Monitors remote connect/disconnect

Usage:
    python demo_window_session_monitor.py
"""

import logging
import threading
from datetime import datetime

from wallpaper_auto.trigger.windows_session_trigger import (
    WindowsSessionEvent,
    WindowsSessionTrigger,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def on_session_change(trigger: WindowsSessionTrigger) -> None:
    """Session change callback"""
    session_id = trigger.current_session_id
    event = trigger.current_event
    if event is None:
        logger.info(f"[Trigger] Session {session_id}: Unknown event")
    else:
        event_names = {
            WindowsSessionEvent.WTS_SESSION_LOGON: "User logged on",
            WindowsSessionEvent.WTS_SESSION_LOGOFF: "User logged off",
            WindowsSessionEvent.WTS_SESSION_LOCK: "Workstation locked",
            WindowsSessionEvent.WTS_SESSION_UNLOCK: "Workstation unlocked",
            WindowsSessionEvent.WTS_REMOTE_CONNECT: "Remote connected",
            WindowsSessionEvent.WTS_REMOTE_DISCONNECT: "Remote disconnected",
        }
        desc = event_names.get(event)
        if desc is None:
            logger.warning(f"[Trigger] Session {session_id}: Unknown event {event}")
        else:
            print(f"\n=== {datetime.now():%H:%M:%S} Session Change Detected ===")
            print(f"  Session ID: {session_id}")
            print(f"  Event: {event.name} ({desc})")
            print("=========================================\n")


def main() -> None:
    monitor = WindowsSessionTrigger()
    monitor.daemon = True
    monitor.add_callback(on_session_change)

    shutdown_event = threading.Event()

    monitor.activate()
    logger.info("Windows session monitor started, press Ctrl+C to exit")

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=0.5)
            
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
    finally:
        logger.info("Shutting down WindowsSessionTrigger...")
        monitor.deactivate()
        shutdown_event.set()
        logger.info("Demo script exited safely.")


if __name__ == "__main__":
    main()