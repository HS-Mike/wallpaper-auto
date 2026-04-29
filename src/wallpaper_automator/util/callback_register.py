"""
Thread-safe callback registry.

Allows registering, removing, and triggering multiple callbacks of a given
signature. Callbacks are invoked synchronously in the triggering thread.
"""
import threading
from typing import Callable, ParamSpec, TypeVar, Generic


cb_P = ParamSpec('cb_P')  # Parameter specification
cb_R = TypeVar('cb_R')


class CallbackRegister(Generic[cb_P, cb_R]):
    def __init__(self):
        self._callback_mutex = threading.Lock()
        self._callbacks: list[Callable[cb_P, cb_R]] = []

    def add_callback(self, cb: Callable[cb_P, cb_R]) -> None:
        if not callable(cb):
            raise ValueError(f"require callable object")
        with self._callback_mutex:
            self._callbacks.append(cb)

    def remove_callback(self, callback: Callable[cb_P, cb_R]) -> None:
        with self._callback_mutex:
            self._callbacks.remove(callback)

    def clear_callback(self) -> None:
        with self._callback_mutex:
            self._callbacks.clear()

    def trigger_callback(self, *args: cb_P.args, **kwargs: cb_P.kwargs) -> list[cb_R]:
        with self._callback_mutex:
            callbacks = list(self._callbacks)
            res = []
            for cb in callbacks:
                res.append(cb(*args, **kwargs))
        return res
