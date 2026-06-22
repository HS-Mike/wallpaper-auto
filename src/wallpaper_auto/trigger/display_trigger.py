"""
Display change trigger for monitor plug/unplug detection.

Uses WM_DISPLAYCHANGE via a hidden Win32 window and WMI (WmiMonitorID)
to detect and report connected monitor changes.
"""

import logging
import threading
from typing import override

import pythoncom
import win32con
import win32gui

from ..util.display_utils import get_display_set

from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)


class DisplayTrigger(BaseThreadTrigger):
    """Display change trigger.

    Monitors Windows display changes via the WM_DISPLAYCHANGE message,
    detecting monitor plug/unplug, and fires callbacks on transitions.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hwnd = None
        self._prev_displays: set[tuple[str, str, str, str]] = set()
        self.current_displays: set[tuple[str, str, str, str]] | None = None    # only available in callback

    @override
    def activate(self) -> None:
        super().start()
        logger.debug(f"{self.__class__.__name__} activate")

    @override
    def deactivate(self) -> None:
        """Send WM_CLOSE to safely stop PumpMessages from another thread."""
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        self.join(timeout=3)
        logger.debug(f"{self.__class__.__name__} deactivate")

    def _setup_window(self) -> None:
        """Create a hidden watch window in the current thread."""
        self._prev_displays = get_display_set()

        className = f"DisplayMonitorClass_{id(self)}"  # noqa: N806
        hInstance = win32gui.GetModuleHandle(None)   # noqa: N806

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._msg_proc          # type: ignore[assignment]
        wc.lpszClassName = className            # type: ignore[assignment]
        wc.hInstance = hInstance                # type: ignore[assignment]
        class_atom = win32gui.RegisterClass(wc)

        self.hwnd = win32gui.CreateWindow(
            class_atom,                             # lpszClassName
            f"DisplayMonitor_{id(self)}",           # lpszWindowName
            0,                                      # dwStyle
            0,                                      # x
            0,                                      # y
            0,                                      # nWidth
            0,                                      # nHeight
            0,                                      # hWndParent
            0,                                      # hMenu
            hInstance,                              # hInstance
            None                                    # lpParam
        )
        logger.debug(f"Window created in thread {threading.get_ident()} and monitoring display changes")

    def _msg_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """Internal window procedure to handle Windows messages."""
        if msg == win32con.WM_DISPLAYCHANGE:
            curr_display = get_display_set()
            if curr_display != self._prev_displays:
                logger.debug(f"Display change detected: {curr_display}")
                self._prev_displays = curr_display
                self.current_displays = curr_display
                self.trigger()
                self.current_displays = None
            return 0

        elif msg == win32con.WM_CLOSE:
            logger.debug("Received WM_CLOSE, destroying window.")
            win32gui.DestroyWindow(hwnd)
            return 0

        elif msg == win32con.WM_DESTROY:
            logger.debug("Received WM_DESTROY, posting quit message to loop.")
            win32gui.PostQuitMessage(0)
            return 0

        return int(win32gui.DefWindowProc(hwnd, msg, wparam, lparam))

    @override
    def run(self) -> None:
        """Initialize COM once for the thread's lifetime and run message loop."""
        pythoncom.CoInitialize()
        className = f"DisplayMonitorClass_{id(self)}"
        try:
            self._setup_window()
            win32gui.PumpMessages()
        finally:
            self.hwnd = None
            try:
                win32gui.UnregisterClass(className, win32gui.GetModuleHandle(None))
            except Exception as e:
                logger.error(f"UnregisterClass failed: {e}")
            pythoncom.CoUninitialize()
            logger.debug("DisplayTrigger thread exited safely")