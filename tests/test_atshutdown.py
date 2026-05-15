"""Tests for atshutdown.py — shutdown detection and callback management."""

from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.atshutdown import ShutdownHandler

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def handler() -> ShutdownHandler:
    """Return a fresh ShutdownHandler (no listener started)."""
    return ShutdownHandler()


@pytest.fixture
def mock_gui():
    """Patch win32gui in the atshutdown module."""
    with patch("wallpaper_auto.atshutdown.win32gui") as gui:
        yield gui


# ── register / unregister ─────────────────────────────────────────────────


class TestShutdownHandlerRegister:
    """ShutdownHandler.register() — callback registration."""

    def test_stores_callback(self, handler):
        callback = MagicMock()
        handler.register(callback)
        assert len(handler._callbacks) == 1

    def test_returns_function_unchanged(self, handler):
        """register should return the original function (decorator support)."""

        def my_func():
            pass

        result = handler.register(my_func)
        assert result is my_func

    def test_lazy_starts_listener_on_first_register(self, handler, mock_gui):
        """Listener thread should be created on the first register call."""
        handler.register(lambda: None)
        assert handler._listener_thread is not None
        assert handler._listener_thread.daemon

    def test_does_not_start_listener_twice(self, handler, mock_gui):
        """Second register should reuse the existing listener thread."""
        handler.register(lambda: None)
        first_thread = handler._listener_thread
        handler.register(lambda: None)
        assert handler._listener_thread is first_thread

    def test_binds_arguments(self, handler, mock_gui):
        """Extra args should be bound via functools.partial."""
        mock_cb = MagicMock()
        handler.register(mock_cb, "arg1", key="val")
        handler._run_callbacks()
        mock_cb.assert_called_once_with("arg1", key="val")

    def test_register_is_idempotent(self, handler, mock_gui):
        """Registering the same function twice should store both entries."""

        def f():
            pass

        handler.register(f)
        handler.register(f)
        assert len(handler._callbacks) == 2


class TestShutdownHandlerUnregister:
    """ShutdownHandler.unregister() — callback removal."""

    def test_removes_callback(self, handler):
        def f():
            pass

        handler.register(f)
        handler.unregister(f)
        assert len(handler._callbacks) == 0

    def test_noop_when_not_registered(self, handler):
        """Unregistering a function that was never registered should not error."""
        handler.unregister(lambda: None)  # should not raise
        assert len(handler._callbacks) == 0

    def test_removes_only_matching(self, handler):
        """Only the matching function should be removed, not others."""

        def f():
            pass

        def g():
            pass

        handler.register(f)
        handler.register(g)
        handler.unregister(f)
        assert len(handler._callbacks) == 1
        registered_func = handler._callbacks[0].func  # type: ignore[attr-defined]
        assert registered_func is g

    def test_removes_bound_method(self, handler):
        """Unregister should work with bound methods (different object each access)."""

        class Fake:
            def method(self):
                pass

        obj = Fake()
        handler.register(obj.method)
        # Access obj.method again → new bound method object
        handler.unregister(obj.method)
        assert len(handler._callbacks) == 0


# ── _run_callbacks ────────────────────────────────────────────────────────


class TestShutdownHandlerRunCallbacks:
    """ShutdownHandler._run_callbacks() — callback invocation."""

    def test_invokes_callbacks_in_lifo_order(self, handler, mock_gui):
        order = []
        handler.register(lambda: order.append(1))
        handler.register(lambda: order.append(2))
        handler._run_callbacks()
        assert order == [2, 1]

    def test_clears_callbacks_after_invocation(self, handler, mock_gui):
        handler.register(lambda: None)
        handler._run_callbacks()
        assert len(handler._callbacks) == 0

    def test_continues_on_callback_error(self, handler, mock_gui):
        """An exception in one callback should not prevent others from running."""
        called = []

        def failing():
            raise ValueError("oops")

        def succeeding():
            called.append(True)

        handler.register(failing)
        handler.register(succeeding)
        handler._run_callbacks()
        assert called == [True]

    def test_empty_list_does_nothing(self, handler):
        """_run_callbacks should not error when no callbacks registered."""
        handler._run_callbacks()  # should not raise


# ── _window_proc ──────────────────────────────────────────────────────────


class TestShutdownHandlerWindowProc:
    """ShutdownHandler._window_proc() — Win32 message handling."""

    def test_wm_queryendsession_runs_callbacks(self, handler, mock_gui):
        callback = MagicMock()
        handler.register(callback)

        result = handler._window_proc(0, 0x0011, 0, 0)  # WM_QUERYENDSESSION

        callback.assert_called_once()
        assert result == 1

    def test_wm_close_destroys_window(self, handler, mock_gui):
        handler._hwnd = 12345

        result = handler._window_proc(12345, 0x0010, 0, 0)  # WM_CLOSE

        mock_gui.DestroyWindow.assert_called_once_with(12345)
        assert result == 0

    def test_wm_destroy_posts_quit(self, handler, mock_gui):
        result = handler._window_proc(0, 0x0002, 0, 0)  # WM_DESTROY

        mock_gui.PostQuitMessage.assert_called_once_with(0)
        assert result == 0

    def test_other_messages_defers_to_defwindowproc(self, handler, mock_gui):
        mock_gui.DefWindowProc.return_value = 42

        result = handler._window_proc(0, 0x0100, 0, 0)  # WM_KEYDOWN

        mock_gui.DefWindowProc.assert_called_once_with(0, 0x0100, 0, 0)
        assert result == 42


# ── close ─────────────────────────────────────────────────────────────────


class TestShutdownHandlerClose:
    """ShutdownHandler.close() — cleanup."""

    def test_posts_close_when_hwnd_exists(self, handler, mock_gui):
        handler._hwnd = 12345

        handler.close()

        mock_gui.PostMessage.assert_called_once()

    def test_noop_when_no_hwnd(self, handler, mock_gui):
        handler._hwnd = None
        handler.close()  # should not raise

    def test_joins_listener_thread(self, handler, mock_gui):
        handler._hwnd = 42
        mock_thread = MagicMock()
        handler._listener_thread = mock_thread

        handler.close()

        mock_thread.join.assert_called_once_with(timeout=3)


# ── _start_listener ───────────────────────────────────────────────────────


class TestShutdownHandlerStartListener:
    """ShutdownHandler._start_listener() — listener thread."""

    def test_creates_daemon_thread(self, handler, mock_gui):
        handler._start_listener()
        assert handler._listener_thread is not None
        assert handler._listener_thread.daemon

    def test_sets_ready_event(self, handler, mock_gui):
        handler._start_listener()
        # After _loop completes (mocked PumpMessages returns immediately)
        # _ready should be set because the mock doesn't block
        handler._ready.wait(timeout=2)
        assert handler._ready.is_set()

    def test_catches_exception_gracefully(self, handler, mock_gui):
        mock_gui.RegisterClass.side_effect = RuntimeError("boom")
        handler._start_listener()
        handler._listener_thread.join(timeout=2)
        # Should not crash; _ready should not be set
        assert not handler._ready.is_set()

    def test_sets_hwnd_on_success(self, handler, mock_gui):
        mock_gui.CreateWindow.return_value = 999
        handler._start_listener()
        handler._listener_thread.join(timeout=2)
        assert handler._hwnd == 999


# ── Module-level convenience API ──────────────────────────────────────────


class TestShutdownHandlerModuleAPI:
    """Module-level register/unregister convenience functions."""

    def test_module_register_and_unregister_are_bound(self):
        from wallpaper_auto.atshutdown import register, unregister

        assert callable(register)
        assert callable(unregister)
        # They should be bound methods of the module-level handler
        assert register.__self__ is unregister.__self__  # type: ignore[union-attr]


# ── Integration: callback fires through window proc ───────────────────────


class TestShutdownHandlerIntegration:
    """End-to-end: register → WM_QUERYENDSESSION → callback fires."""

    def test_callback_fires_via_window_proc(self, handler, mock_gui):
        callback = MagicMock()
        handler.register(callback)

        handler._window_proc(0, 0x0011, 0, 0)  # WM_QUERYENDSESSION

        callback.assert_called_once()
        # Callback list should be cleared after firing
        assert len(handler._callbacks) == 0

    def test_unregister_prevents_callback_firing(self, handler, mock_gui):
        callback = MagicMock()
        handler.register(callback)
        handler.unregister(callback)

        handler._window_proc(0, 0x0011, 0, 0)  # WM_QUERYENDSESSION

        callback.assert_not_called()
