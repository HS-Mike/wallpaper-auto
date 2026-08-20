"""
Display change trigger for monitor plug/unplug and DPI scale change detection.

Uses WM_DISPLAYCHANGE and WM_SETTINGCHANGE via a hidden Win32 window to detect
connected monitor changes or DPI scaling transitions globally across all
screens.  Declares Per-Monitor DPI awareness via ``set_process_dpi_aware()`` so
display APIs return physical pixels.
"""

import logging
from typing import override

import pythoncom
import win32con
import win32gui

from ..util.display_util import DisplayInfo, get_display_info, set_process_dpi_aware
from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)


class DisplayTrigger(BaseThreadTrigger):
    """Display change trigger.

    Monitors Windows display changes via WM_DISPLAYCHANGE and WM_SETTINGCHANGE
    messages, detecting monitor plug/unplug and DPI scaling transitions, then
    fires callbacks.
    """

    def __init__(self) -> None:
        """Initialize the trigger with a unique window class name and an empty display snapshot."""
        super().__init__()
        self.hwnd: int | None = None
        self._prev_display: frozenset[DisplayInfo] = frozenset()
        self._window_class_name = f"{self.__class__.__name__}_{id(self)}"

    @override
    def start(self) -> None:
        """Start the background thread that runs the window message loop."""
        super().start()

    @override
    def stop(self) -> None:
        """Send WM_CLOSE to safely stop PumpMessages from another thread."""
        hwnd = self.hwnd
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        super().stop()

    def _setup_window(self) -> None:
        """Create a hidden watch window in the current thread."""
        class_name = self._window_class_name
        h_instance = win32gui.GetModuleHandle(None)

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._msg_proc  # type: ignore
        wc.lpszClassName = class_name  # type: ignore
        wc.hInstance = h_instance  # type: ignore
        class_atom = win32gui.RegisterClass(wc)

        self.hwnd = win32gui.CreateWindow(
            class_atom,  # lpszClassName
            self._window_class_name,  # lpszWindowName
            0,  # dwStyle
            0,  # x
            0,  # y
            0,  # nWidth
            0,  # nHeight
            0,  # hWndParent
            0,  # hMenu
            h_instance,  # hInstance
            None,  # lpParam
        )

        display_info = get_display_info(raise_error=False) or []
        self._prev_display = frozenset(display_info)

    def _msg_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """Handle Windows messages for the hidden watch window.

        Args:
            hwnd: Handle of the watch window.
            msg: Windows message identifier.
            wparam: First message parameter.
            lparam: Second message parameter.

        Returns:
            Zero when the message was handled; otherwise the result of the
            default window procedure.
        """

        if msg in (win32con.WM_DISPLAYCHANGE, win32con.WM_SETTINGCHANGE):
            display_info = get_display_info(raise_error=False)
            if display_info is None:
                # Transient query failure — discard the signal so a failed query
                # never looks like "all displays removed".
                return 0
            curr_display = frozenset(display_info)
            if curr_display != self._prev_display:
                if {d.display_id for d in curr_display} != {
                    d.display_id for d in self._prev_display
                }:
                    removed_displays = self._prev_display - curr_display
                    added_displays = curr_display - self._prev_display
                    self._log_display_changes(removed_displays, added_displays)
                self._prev_display = curr_display
                self.trigger()
            return 0

        elif msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0

        elif msg == win32con.WM_DESTROY:
            self.hwnd = None
            win32gui.PostQuitMessage(0)
            return 0

        return int(win32gui.DefWindowProc(hwnd, msg, wparam, lparam))

    def _log_display_changes(
        self,
        removed: frozenset[DisplayInfo],
        added: frozenset[DisplayInfo],
    ) -> None:
        """Log the displays removed and added in a topology change.

        Args:
            removed: Displays present before the change but not after.
            added: Displays present after the change but not before.
        """
        log_info = ""
        if removed:
            log_info += "Removed displays: \n"
            for d_info in removed:
                log_info += (
                    f"  {d_info.model or 'UNKNOWN MODEL'}"
                    f" ({d_info.device_name or 'UNKNOW DEVICE NAME'})\n"
                )
        if added:
            log_info += "Added displays: \n"
            for d_info in added:
                log_info += (
                    f"  {d_info.model or 'UNKNOWN MODEL'}"
                    f" ({d_info.device_name or 'UNKNOW DEVICE NAME'})\n"
                )
        logger.debug(log_info.strip())

    @override
    def run(self) -> None:
        """Initialize COM and DPI awareness once for the thread's lifetime and run message loop."""
        pythoncom.CoInitialize()
        set_process_dpi_aware()

        self._setup_window()
        win32gui.PumpMessages()

        self.hwnd = None
        win32gui.UnregisterClass(self._window_class_name, win32gui.GetModuleHandle(None))  # type: ignore
        pythoncom.CoUninitialize()
