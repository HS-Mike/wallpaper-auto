import time
from unittest.mock import patch

import pytest

from wallpaper_auto.trigger.windows_session_trigger import (
    WindowsSessionEvent,
    WindowsSessionTrigger,
)


@pytest.fixture
def mock_win32():
    with (
        patch("wallpaper_auto.trigger.windows_session_trigger.win32gui") as gui,
        patch("wallpaper_auto.trigger.windows_session_trigger.win32ts") as ts,
    ):
        yield gui, ts


class TestWindowsSessionTriggerLifecycle:
    """Tests for the stop/exit mechanism."""

    def test_lifecycle(self):
        trigger = WindowsSessionTrigger()
        trigger.start()

        assert trigger._thread is not None and trigger._thread.is_alive()
        for _ in range(50):
            if trigger.hwnd is not None:
                break
            time.sleep(0.01)
        assert trigger.hwnd is not None

        trigger.stop()

        assert trigger._thread is None


class TestWindowsSessionTriggerProcessEvent:
    """Tests for event processing logic via mocked Win32 API."""

    @pytest.mark.parametrize(
        ("session_id", "event_code", "expected_session_id", "expected_event"),
        [
            (0, 0x7, 0, WindowsSessionEvent.WTS_SESSION_LOCK),
            (0, 0x8, 0, WindowsSessionEvent.WTS_SESSION_UNLOCK),
            (1234, 0x7, 1234, WindowsSessionEvent.WTS_SESSION_LOCK),
            (0x99, 999, 0x99, None),
        ],
    )
    def test_process_event(
        self,
        mock_win32,
        session_id,
        event_code,
        expected_session_id,
        expected_event,
    ):
        trigger = WindowsSessionTrigger()
        callback_called = []
        trigger.add_callback(lambda _: callback_called.append(True))

        trigger.process_event(session_id, event_code)

        assert trigger.current_session_id == expected_session_id
        assert trigger.current_event == expected_event
        assert callback_called


class TestWindowsSessionTriggerWndProc:
    """Tests for window message processing."""

    def test_wndproc_handles_session_change_message(self, mock_win32):
        trigger = WindowsSessionTrigger()
        with patch.object(trigger, "process_event") as mock_process_event:
            trigger.wnd_proc(0, 0x02B1, 0x7, 1234)

        mock_process_event.assert_called_once_with(1234, 0x7)

    def test_wndproc_ignores_other_messages(self, mock_win32):
        trigger = WindowsSessionTrigger()
        callback_called = []
        trigger.add_callback(lambda _: callback_called.append(True))

        trigger.wnd_proc(0, 0x0100, 0, 0)

        assert not callback_called
