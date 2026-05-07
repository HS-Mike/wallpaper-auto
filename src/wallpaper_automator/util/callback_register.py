"""
Thread-safe callback registry.

Allows registering, removing, and triggering multiple callbacks of a given
signature. Callbacks are invoked synchronously in the triggering thread.
"""

import threading
from collections.abc import Callable
from typing import ParamSpec, TypeVar

cb_P = ParamSpec("cb_P")  # noqa: N816 — generic type var, follows ParamSpec convention
cb_R = TypeVar("cb_R")  # noqa: N816


class CallbackRegister[**cb_P, cb_R]:
    def __init__(self) -> None:
        self._callback_mutex = threading.Lock()
        self._callbacks: list[Callable[cb_P, cb_R]] = []

    def add_callback(self, cb: Callable[cb_P, cb_R]) -> None:
        if not callable(cb):
            raise ValueError("require callable object")
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
