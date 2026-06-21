"""Display Trigger Demo Script

Demonstrates DisplayTrigger display change detection:
- Monitors monitor plug/unplug events
- Shows current display set on change
- Shows initial display state

Usage:
    python demo_display_trigger.py
"""
import logging
import signal
import sys
import time
import threading

from wallpaper_auto.trigger.display_trigger import DisplayTrigger

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def on_display_change(trigger: DisplayTrigger) -> None:
    """Display change callback — print current display set from trigger."""
    displays = trigger.curr_displays
    if displays is None:
        logger.warning("No display data available")
        return
    print(f"\n=== Display change detected! Connected displays: ===")
    for i, (manufacturer, model, pnp_id, serial) in enumerate(sorted(displays), 1):
        print(f"  {i}. {manufacturer} {model} (SN: {serial})")
    print()


def main() -> None:
    monitor = DisplayTrigger()
    monitor.add_callback(on_display_change)

    shutdown_event = threading.Event()

    monitor.activate()
    logger.info("DisplayTrigger started, press Ctrl+C to exit")
    
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=0.2)
            
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
    finally:
        logger.info("Shutting down DisplayTrigger...")
        monitor.deactivate()
        shutdown_event.set()
        logger.info("Demo script exited safely.")


if __name__ == "__main__":
    main()
