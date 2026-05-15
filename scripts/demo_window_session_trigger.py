"""Windows Session Monitor Demo Script

Demonstrates WindowsSessionMonitor session event monitoring:
- Monitors user logon/logoff
- Monitors workstation lock/unlock
- Monitors remote connect/disconnect

Usage:
    python demo_window_session_monitor.py
"""

import logging
import signal
import sys
import threading

from wallpaper_auto.trigger.windows_session_trigger import (
    WindowsSessionEvent,
    WindowsSessionTrigger,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def on_session_change(session_id: int, event: WindowsSessionEvent | None) -> None:
    """Session change callback"""
    if event is None:
        print(f"[Trigger] Session {session_id}: Unknown event")
    else:
        event_names = {
            WindowsSessionEvent.WTS_SESSION_LOGON: "User logged on",
            WindowsSessionEvent.WTS_SESSION_LOGOFF: "User logged off",
            WindowsSessionEvent.WTS_SESSION_LOCK: "Workstation locked",
            WindowsSessionEvent.WTS_SESSION_UNLOCK: "Workstation unlocked",
            WindowsSessionEvent.WTS_REMOTE_CONNECT: "Remote connected",
            WindowsSessionEvent.WTS_REMOTE_DISCONNECT: "Remote disconnected",
        }
        desc = event_names.get(event, "Unknown")
        print(f"[Trigger] Session {session_id}: {event.name} ({desc})")


def main() -> None:
    monitor = WindowsSessionTrigger()

    # Graceful shutdown handler
    def signal_handler(signum, frame):
        logger.info("Received signal, shutting down...")
        monitor.deactivate()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    monitor.add_callback(
        lambda _: on_session_change(monitor.last_session_id, monitor.last_event)
    )
    logger.info("Windows session monitor started, press Ctrl+C to exit")
    monitor.activate()

    threading.Event().wait()


if __name__ == "__main__":
    main()
