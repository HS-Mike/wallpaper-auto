"""
Display Trigger Demo Script

Demonstrates DisplayTrigger display change detection:
- Monitors monitor plug/unplug events via WM_DISPLAYCHANGE
- Shows current display set on change
- Shows initial display state

Usage:
    python demo_display_trigger.py
"""
import logging
import threading

from wallpaper_auto.util.display_utils import DisplayInfo, get_display_info
from wallpaper_auto.trigger.display_trigger import DisplayTrigger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _print_all_displays(header: str = "") -> None:
    """Query and print all current displays in unified format."""
    if header:
        print(f"\n=== {header} ===")
    displays = get_display_info()
    if displays is None:
        print("  <could not query displays>")
        return
    for i, d in enumerate(displays, 1):
        print(f"  {i}. {d.model or 'Unknown'} — source {d.source_resolution} -> target {d.target_resolution} @ {d.position}  scale {d.scale:.0%}  [{d.monitor_device_path}]")


def on_display_change(_trigger: DisplayTrigger) -> None:
    """Display change callback — print current display set."""
    _print_all_displays("Connected displays changed")
    print()


def print_initial_displays() -> None:
    """Print initial display state."""
    _print_all_displays("Initial connected displays")
    print()


def main() -> None:
    print_initial_displays()

    monitor = DisplayTrigger()
    monitor.add_callback(on_display_change)
    monitor.start()

    logger.info("DisplayTrigger started, press Ctrl+C to exit")

    shutdown_event = threading.Event()
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
    finally:
        logger.info("Shutting down DisplayTrigger...")
        monitor.stop()
        logger.info("Demo script exited safely.")


if __name__ == "__main__":
    main()
