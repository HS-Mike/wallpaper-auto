import time

import win32con

from wallpaper_automator.trigger.windows_session_trigger import (
    WindowsSessionEvent,
    WindowsSessionTrigger,
)


class TestWindowsSessionTriggerProcessEvent:
    """Tests for event processing logic via mocked Win32 API."""

    def test_process_event_lock(self, mock_win32):
        trigger = WindowsSessionTrigger()
        callback_called = []
        trigger.add_callback(lambda _: callback_called.append(True))

        trigger.process_event(0, 0x7)

        assert trigger.last_session_id == 0
        assert trigger.last_event == WindowsSessionEvent.WTS_SESSION_LOCK
        assert callback_called

    def test_process_event_unlock(self, mock_win32):
        trigger = WindowsSessionTrigger()
        trigger.add_callback(lambda _: None)

        trigger.process_event(0, 0x8)

        assert trigger.last_session_id == 0
        assert trigger.last_event is not None
        assert trigger.last_event.value == 0x8

    def test_process_event_with_session_id(self, mock_win32):
        trigger = WindowsSessionTrigger()
        trigger.add_callback(lambda _: None)

        trigger.process_event(1234, 0x7)

        assert trigger.last_session_id == 1234

    def test_process_event_unknown_code_triggers_callback_with_none(self, mock_win32):
        trigger = WindowsSessionTrigger()
        trigger.add_callback(lambda _: None)

        trigger.process_event(0x99, 999)

        assert trigger.last_session_id == 0x99
        assert trigger.last_event is None


class TestWindowsSessionTriggerWndProc:
    """Tests for window message processing."""

    def test_wndproc_handles_session_change_message(self, mock_win32):
        trigger = WindowsSessionTrigger()
        trigger.add_callback(lambda _: None)

        trigger.wnd_proc(0, 0x02B1, 0x7, 1234)

        assert trigger.last_session_id == 1234
        assert trigger.last_event == WindowsSessionEvent.WTS_SESSION_LOCK

    def test_wndproc_ignores_other_messages(self, mock_win32):
        trigger = WindowsSessionTrigger()
        callback_called = []
        trigger.add_callback(lambda _: callback_called.append(True))

        trigger.wnd_proc(0, 0x0100, 0, 0)

        assert not callback_called


class TestWindowsSessionTriggerSetup:
    """Tests for _setup_window and run methods."""

    def test_setup_window_creates_hidden_window(self, mock_win32):
        mock_gui, mock_ts = mock_win32
        trigger = WindowsSessionTrigger()
        trigger._setup_window()

        mock_gui.RegisterClass.assert_called_once()
        mock_gui.CreateWindow.assert_called_once()
        mock_ts.WTSRegisterSessionNotification.assert_called_once()

    def test_setup_window_called_in_run(self, mock_win32):
        trigger = WindowsSessionTrigger()
        call_order = []

        original_setup = trigger._setup_window

        def track_setup():
            call_order.append("_setup_window")
            original_setup()

        trigger._setup_window = track_setup

        def run_tracked():
            track_setup()
            call_order.append("run")

        trigger.run = run_tracked

        trigger._setup_window()

        assert "_setup_window" in call_order
        assert trigger.hwnd is not None


class TestWindowsSessionTriggerStop:
    """Tests for the stop/exit mechanism."""

    def test_stop_sends_close_message(self, mock_win32):
        mock_gui, _mock_ts = mock_win32
        trigger = WindowsSessionTrigger()
        trigger._setup_window()
        trigger.activate()
        trigger.deactivate()

        mock_gui.PostMessage.assert_called_with(trigger.hwnd, win32con.WM_CLOSE, 0, 0)

    def test_wndproc_handles_wm_close(self, mock_win32):
        mock_gui, mock_ts = mock_win32
        trigger = WindowsSessionTrigger()
        trigger._setup_window()

        result = trigger.wnd_proc(trigger.hwnd, win32con.WM_CLOSE, 0, 0)

        assert result == 0
        mock_ts.WTSUnRegisterSessionNotification.assert_called_with(trigger.hwnd)
        mock_gui.DestroyWindow.assert_called_with(trigger.hwnd)

    def test_wndproc_handles_wm_destroy(self, mock_win32):
        mock_gui, _mock_ts = mock_win32
        trigger = WindowsSessionTrigger()
        trigger._setup_window()

        result = trigger.wnd_proc(trigger.hwnd, win32con.WM_DESTROY, 0, 0)

        assert result == 0
        mock_gui.PostQuitMessage.assert_called_with(0)

    def test_activate_then_deactivate_exits_thread(self, mock_win32):
        trigger = WindowsSessionTrigger()
        trigger.activate()

        time.sleep(0.1)
        assert trigger.hwnd is not None

        trigger.deactivate()
        trigger.join(timeout=2)

        assert not trigger.is_alive()
