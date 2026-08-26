"""
Windows session change trigger.

Monitors Windows session events (logon, logoff, lock, unlock, remote connect/disconnect)
via a hidden window message pump and fires callbacks when any session event occurs.
"""

import logging
from enum import Enum
from typing import override

import win32con
import win32gui
import win32ts

from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)


WM_WTSSESSION_CHANGE = 0x02B1

WTS_SESSION_LOGON = 0x5
WTS_SESSION_LOGOFF = 0x6
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WTS_REMOTE_CONNECT = 0x1
WTS_REMOTE_DISCONNECT = 0x2


class WindowsSessionEvent(Enum):
    WTS_SESSION_LOGON = 0x5
    WTS_SESSION_LOGOFF = 0x6
    WTS_SESSION_LOCK = 0x7
    WTS_SESSION_UNLOCK = 0x8
    WTS_REMOTE_CONNECT = 0x1
    WTS_REMOTE_DISCONNECT = 0x2


class WindowsSessionTrigger(BaseThreadTrigger):
    """Trigger fired on Windows session events (logon, logoff, lock, unlock).

    Uses a hidden window registered with WTSRegisterSessionNotification to
    receive WM_WTSSESSION_CHANGE messages, then fires callbacks. The latest
    event and its session ID are exposed via ``current_event`` and
    ``current_session_id``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hwnd: int | None = None
        self.current_session_id = 0
        self.current_event: WindowsSessionEvent | None = None

    @override
    def start(self) -> None:
        super().start()

    @override
    def stop(self) -> None:
        """Send WM_CLOSE to unblock the message pump and stop the trigger."""
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        super().stop()

    def _setup_window(self) -> None:
        """Create the hidden watch window and register for session notifications."""
        className = f"{self.__class__.__name__}_{id(self)}"  # noqa: N806
        hInstance = win32gui.GetModuleHandle(None)  # noqa: N806

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._msg_proc  # type: ignore[misc]
        wc.lpszClassName = className  # type: ignore[misc]
        wc.hInstance = hInstance  # type: ignore[misc]
        win32gui.RegisterClass(wc)

        hwnd = win32gui.CreateWindow(
            className,  # lpszClassName
            "SessionEventTool",  # lpszWindowName
            0,  # dwStyle
            0,  # x
            0,  # y
            0,  # nWidth
            0,  # nHeight
            0,  # hWndParent
            0,  # hMenu
            wc.hInstance,  # hInstance
            None,  # lpParam
        )
        self.hwnd = hwnd
        win32ts.WTSRegisterSessionNotification(hwnd, 1)

    def _msg_proc(self, hwnd: int, msg: int, wParam: int, lParam: int) -> int:  # noqa: N803
        """Handle Windows messages for the hidden watch window.

        Args:
            hwnd: Handle of the watch window.
            msg: Windows message identifier.
            wParam: First message parameter; for WM_WTSSESSION_CHANGE it is the
                session event code.
            lParam: Second message parameter; for WM_WTSSESSION_CHANGE it is
                the session ID.

        Returns:
            Zero when the message was handled; otherwise the result of the
            default window procedure.
        """
        if msg == WM_WTSSESSION_CHANGE:
            self.process_event(lParam, wParam)
        elif msg == win32con.WM_CLOSE:
            win32ts.WTSUnRegisterSessionNotification(hwnd)
            win32gui.DestroyWindow(hwnd)
            return 0
        elif msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return int(win32gui.DefWindowProc(hwnd, msg, wParam, lParam))

    def process_event(self, session_id: int, event_code: int) -> None:
        """Record the session event state and fire the trigger callbacks.

        Args:
            session_id: ID of the session that changed.
            event_code: Raw WTS session event code; unknown codes are recorded
                as ``None``.
        """
        try:
            event = WindowsSessionEvent(event_code)
        except ValueError:
            event = None

        self.current_session_id = session_id
        self.current_event = event
        self.trigger()

    @override
    def run(self) -> None:
        self._setup_window()
        win32gui.PumpMessages()
