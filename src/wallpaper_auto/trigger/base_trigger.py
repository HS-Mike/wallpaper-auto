"""
Base trigger classes.

Provides BaseTrigger (callback-only interface) and BaseThreadTrigger
(a background-thread variant) as the foundation for all trigger implementations.

All resource class must inherit from BaseResource and implement start,
stop, and trigger interface.

In case of BaseThreadTrigger, subclass must override run method and manage a loop inside.
Exit loop according to self.stop_event.
"""

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any, override

from ..util.callback_register import CallbackRegister


logger = logging.getLogger(__name__)


class BaseTrigger(CallbackRegister[["BaseTrigger"], None], ABC):
    def __init__(self) -> None:
        super().__init__()

    def trigger(self) -> None:
        logger.debug(f"trigger called on {self.__class__.__name__}")
        self.trigger_callback(self)

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def _format_identity(self):
        return f"{self.__class__.__name__} (id: {id(self)})"

    def __getattribute__(self, name: str) -> Any:
        if name == "start":
            logger.debug(f"trigger lifecycle start called on {self._format_identity()}")
        if name == "stop":
            logger.debug(f"trigger lifecycle stop called on {self._format_identity()}")
        return super().__getattribute__(name)


class BaseThreadTrigger(BaseTrigger):
    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        # Set by stop() / _request_stop().  Subclasses should check
        # self.stop_event.is_set() in their run() loop and exit when set.
        self.stop_event = threading.Event()

    @override
    def start(self) -> None:
        self.stop_event.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    @override
    def stop(self) -> None:
        self._request_stop()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    @abstractmethod
    def run(self) -> None:
        """Main logic for the trigger thread."""

    def _request_stop(self) -> None:
        """Request to stop."""
        self.stop_event.set()
