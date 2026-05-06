"""
Base trigger classes.

Provides BaseTrigger (callback-only interface) and BaseThreadTrigger
(a background-thread variant) as the foundation for all trigger implementations.

All resource class must inherit from BaseResource and implement activate, deactivate, and trigger interface.

In case of BaseThreadTrigger, subclass must override run method and manage a loop inside. 
Exit loop according to self.stop_event.
"""
import threading
from abc import ABC, abstractmethod
from typing import override, Any

from pydantic import BaseModel

from ..util import callback_register


class BaseTrigger(callback_register.CallbackRegister[["BaseTrigger"], None], ABC):

    def __init__(self):
        super().__init__()

    def trigger(self): 
        self.trigger_callback(self)

    def activate(self): ...

    def deactivate(self): ...


class BaseThreadTrigger(threading.Thread, BaseTrigger):

    def __init__(self):
        threading.Thread.__init__(self)
        BaseTrigger.__init__(self)
        self.daemon = True
        self.stop_event = threading.Event()

    @override
    def activate(self):
        super().start()
        self.stop_event.clear()

    @override
    def deactivate(self):
        self._request_stop()
        super().join(timeout=3)

    @abstractmethod
    def run(self) -> None:
        """Main logic for the trigger thread."""
        ...

    def _request_stop(self) -> None:
        """Request to stop."""
        self.stop_event.set()
