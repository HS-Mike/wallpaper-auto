"""
Base trigger classes.

Provides BaseTrigger (callback-only interface) and BaseThreadTrigger
(a background-thread variant) as the foundation for all trigger implementations.

All resource class must inherit from BaseResource and implement start,
stop, and trigger interface.

In case of BaseThreadTrigger, subclass must override run method and manage a loop inside.
Exit loop according to self.stop_event.
"""

import threading
from abc import ABC, abstractmethod
from typing import TypeVar, override

from ..util import callback_register

T = TypeVar("T", bound="BaseTrigger")


class BaseTrigger(callback_register.CallbackRegister[T, None], ABC):
    def __init__(self) -> None:
        super().__init__()

    def trigger(self) -> None:
        self.trigger_callback(self)

    def start(self) -> None: ...

    def stop(self) -> None: ...


class BaseThreadTrigger(BaseTrigger):
    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        # Set by stop() / _request_stop().  Subclasses should check
        # self.stop_event.is_set() in their run() loop and exit when set.
        self.stop_event = threading.Event()
        self.daemon = True

    @override
    def start(self) -> None:
        self.stop_event.clear()
        self._thread = threading.Thread(target=self.run, daemon=self.daemon)
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
