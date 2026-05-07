"""
Trigger manager.

Manages all trigger instances, routes callbacks from any trigger to the wallpaper
controller's evaluation loop, and provides pause/resume functionality.
"""

import logging
import threading

from .models import TriggerConfig
from .trigger.base_trigger import BaseTrigger
from .trigger.network_trigger import NetworkTrigger
from .trigger.time_trigger import TimeTrigger
from .trigger.windows_session_trigger import WindowsSessionTrigger
from .util.callback_register import CallbackRegister

logger = logging.getLogger(__name__)


_BUILTIN_TRIGGERS: dict[str, type[BaseTrigger]] = {
    "network": NetworkTrigger,
    "time": TimeTrigger,
    "windows_session": WindowsSessionTrigger,
}


class TriggerManager(CallbackRegister[[], None]):
    """Manages trigger lifecycle — start, stop, and pause/resume triggers from parsed config."""

    _support_triggers: dict[str, type[BaseTrigger]] = _BUILTIN_TRIGGERS.copy()

    def __init__(self) -> None:
        super().__init__()

        self._triggers: list[BaseTrigger] = []
        self._paused = threading.Event()

    def trigger_callback(self, *args: object, **kwargs: object) -> list[None]:
        # supress triggers when manager pause
        if self._paused.is_set():
            return []
        return super().trigger_callback(*args, **kwargs)

    @classmethod
    def register_trigger(cls, name: str, trigger_cls: type[BaseTrigger]) -> None:
        """
        Register a custom trigger class.
        Register the subclass before starting WallpaperAutomator.
        """
        if not issubclass(trigger_cls, BaseTrigger):
            raise ValueError("trigger cls must inherit from BaseTrigger")
        cls._support_triggers[name] = trigger_cls

    def init(self, trigger_config: list[TriggerConfig]) -> None:
        """Initialize triggers from resource config dictionary."""
        for i in trigger_config:
            trigger_cls = self._support_triggers.get(i.name, None)
            if trigger_cls is None:
                raise ValueError(f"trigger {i.name} not found")
            trigger = trigger_cls(**i.config)

            def _trigger_manager_cb(t: BaseTrigger) -> None:
                logger.debug(f"{t.__class__.__name__} call")
                self.trigger_callback()

            trigger.add_callback(_trigger_manager_cb)
            self._triggers.append(trigger)

    def activate(self) -> None:
        """Start all triggers."""
        for t in self._triggers:
            t.activate()

    def deactivate(self) -> None:
        """Stop all triggers."""
        for t in self._triggers:
            t.deactivate()

    def pause(self) -> None:
        """Pause triggers (keep thread alive but do not trigger callbacks)."""
        self._paused.set()

    def resume(self) -> None:
        """Resume triggers."""
        self._paused.clear()
