"""
Windows session change trigger.

Monitors Windows session events (logon, logoff, lock, unlock, remote connect/disconnect)
via a hidden window message pump and fires callbacks when any session event occurs.
"""

import logging
import queue
import threading
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
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.hwnd = None
        self.message_queue: queue.Queue[tuple[int, WindowsSessionEvent | None]] = queue.Queue(10)

    @override
    def activate(self) -> None:
        super().start()
        logger.debug(f"{self.__class__.__name__} activate")

    @override
    def deactivate(self) -> None:
        """send WM_CLOSE to stop PumpMessages"""
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        self.join(timeout=3)
        logger.debug(f"{self.__class__.__name__} deactivate")

    def _setup_window(self):
        """create a watch window in current threadi"""
        className = f"{self.__class__.__name__}_{id(self)}"  # noqa: N806
        hInstance = win32gui.GetModuleHandle(None)  # noqa: N806

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self.wnd_proc  # type: ignore
        wc.lpszClassName = className  # type: ignore
        wc.hInstance = hInstance  # type: ignore
        win32gui.RegisterClass(wc)

        self.hwnd = win32gui.CreateWindow(
            className, "SessionEventTool", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )

        win32ts.WTSRegisterSessionNotification(self.hwnd, 1)
        logger.debug(f"window created in thread {threading.get_ident()} and monitor session change")

    def wnd_proc(self, hwnd, msg, wParam, lParam):  # noqa: N803
        if msg == WM_WTSSESSION_CHANGE:
            self.process_event(lParam, wParam)
        elif msg == win32con.WM_CLOSE:
            win32ts.WTSUnRegisterSessionNotification(hwnd)
            win32gui.DestroyWindow(hwnd)
            return 0
        elif msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wParam, lParam)

    def process_event(self, session_id, event_code):
        try:
            event = WindowsSessionEvent(event_code)
            logger.debug(f"Session {session_id} event={event.name}")
        except ValueError:
            event = None
            logger.debug(f"Session {session_id} OTHER EVENT {event_code}")

        if self.message_queue.full:
            with self.message_queue.mutex:
                self.message_queue.queue.clear()
        self.message_queue.put((session_id, event))
        self.trigger()

    @override
    def run(self):
        self._setup_window()
        win32gui.PumpMessages()
