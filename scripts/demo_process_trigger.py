"""
Process Trigger Demo Script

Demonstrates ProcessTrigger process lifecycle detection:
- Monitors start/stop events for a configurable list of executables via WMI
- Prints each event with PID, basename, and event type
- Shows initial state

Edit ``WATCHED_EXECUTABLES`` below to watch different processes.

Usage:
    python demo_process_trigger.py
"""

import logging
import threading

from wallpaper_auto.trigger.base_trigger import BaseTrigger
from wallpaper_auto.trigger.process_trigger import ProcessEvent, ProcessTrigger

# Names that appear in the process list when launching the bundled Calculator:
#   - Windows 10/11: ``calc.exe`` is a stub that exits immediately and spawns
#     the UWP ``CalculatorApp.exe`` (the real process). Watching ``calc.exe``
#     alone will never fire on modern Windows.
#   - Windows 8.x and earlier: ``calc.exe`` itself is the Win32 binary.
# Listing both keeps the demo working on any modern Windows version. Names
# are OR-joined into a single WMI subscription, so unused entries add only
# a cheap filter clause.
#
# Entries may be a plain basename (matches any process with that name) or
# a full path (matches only processes launched from that exact path, with
# case- and separator-insensitive comparison).
WATCHED_EXECUTABLES = [
    "notepad.exe",
    "calc.exe",
    "CalculatorApp.exe",
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def on_process_event(trigger: BaseTrigger) -> None:
    """Process lifecycle callback — log the most recent event."""
    event: ProcessEvent | None = trigger.last_event
    if event is None:
        return
    logger.info(
        "%s: %s (pid=%s, path=%s)",
        event.event_type.name,
        event.exe_name,
        event.pid,
        event.exe_path or "<unresolved>",
    )


def main() -> None:
    trigger = ProcessTrigger(exe_names=WATCHED_EXECUTABLES)
    trigger.add_callback(on_process_event)
    trigger.start()

    logger.info(f"ProcessTrigger started, watching: {WATCHED_EXECUTABLES}")
    logger.info("Press Ctrl+C to exit")

    shutdown_event = threading.Event()
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
    finally:
        logger.info("Shutting down ProcessTrigger...")
        trigger.stop()
        logger.info("Demo script exited safely.")


if __name__ == "__main__":
    main()
