"""
Windows shutdown/logoff detection via a hidden message window.

Listens for ``WM_QUERYENDSESSION`` (broadcast when Windows is shutting down or the
user is logging off) and fires registered callbacks.  Callbacks run in LIFO order,
consistent with ``atexit`` semantics (last registered = first invoked).
"""

from __future__ import annotations

import functools
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

import win32con
import win32gui

logger = logging.getLogger(__name__)


class ShutdownHandler:
    """Hidden window + message pump that catches ``WM_QUERYENDSESSION``."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._listener_thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()

    def register(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Callable[..., Any]:
        """Register *func* to be called at shutdown.

        The listener thread is lazily started on the first registration.
        Extra positional/keyword arguments are bound at registration time.
        """
        cb = functools.partial(func, *args, **kwargs)
        with self._lock:
            self._callbacks.append(cb)
        with self._start_lock:
            if self._listener_thread is None:
                self._start_listener()
        return func

    @staticmethod
    def _is_same_func(a: Callable[..., Any], b: Callable[..., Any]) -> bool:
        """Check whether two callables refer to the same underlying function.

        Handles bound methods where Python creates a new object on every
        attribute access.
        """
        if a is b:
            return True
        # Bound methods: same ``__func__`` on the same ``__self__`` instance
        if hasattr(a, "__self__") and hasattr(b, "__self__"):  # pragma: no branch
            return a.__self__ is b.__self__ and a.__func__ is b.__func__  # type: ignore[attr-defined]
        return False

    def unregister(self, func: Callable[..., Any]) -> None:
        """Remove a previously registered callback."""
        with self._lock:
            self._callbacks = [
                cb
                for cb in self._callbacks
                if not self._is_same_func(cb.func, func)  # type: ignore[attr-defined]
            ]

    def close(self) -> None:
        """Tear down the hidden window and stop the message pump."""
        if self._hwnd is not None:
            win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=3)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_listener(self) -> None:
        """Start a daemon thread that owns the hidden message window."""

        def _loop() -> None:
            wc: Any = win32gui.WNDCLASS()
            wc.lpfnWndProc = self._window_proc
            wc.lpszClassName = f"ShutdownHandler_{os.getpid()}"
            hinst = win32gui.GetModuleHandle(None)

            try:
                class_atom = win32gui.RegisterClass(wc)
                self._hwnd = win32gui.CreateWindow(
                    class_atom, "ShutdownHandler", 0, 0, 0, 0, 0, 0, 0, hinst, None
                )
                self._ready.set()
                win32gui.PumpMessages()
            except Exception:
                logger.exception("Failed to start shutdown listener")

        self._listener_thread = threading.Thread(target=_loop, daemon=True)
        self._listener_thread.start()

    def _window_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:  # noqa: N803
        if msg == win32con.WM_QUERYENDSESSION:
            self._run_callbacks()
            return 1  # TRUE — signal readiness to shut down
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return int(win32gui.DefWindowProc(hwnd, msg, wparam, lparam))

    def _run_callbacks(self) -> None:
        """Invoke all registered callbacks.

        The callback list is snapshot under the lock, then executed outside it
        so callbacks cannot deadlock against ``register``/``unregister``.
        """
        with self._lock:
            callbacks = list(reversed(self._callbacks))
            self._callbacks.clear()
        for cb in callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE001
                logger.exception("Error in shutdown callback")


# Module-level convenience: a single process-wide shutdown handler.
_handler = ShutdownHandler()
register = _handler.register
unregister = _handler.unregister
