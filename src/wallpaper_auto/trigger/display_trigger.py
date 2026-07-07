"""
Display change trigger for monitor plug/unplug and DPI scale change detection.

Uses WM_DISPLAYCHANGE and WM_SETTINGCHANGE via a hidden Win32 window to detect
connected monitor changes or DPI scaling transitions globally across all
screens.  Uses thread-level DPI awareness isolation to bypass process-level
DPI virtualisation.
"""

import ctypes
import logging
import threading
from typing import override

import pythoncom
import win32con
import win32gui

from ..util.display_utils import (
    DisplayTopologyTransientError,
    get_all_monitors_dpi_snapshot,
    get_display_info,
)
from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)

# Windows DPI API setup — thread-level awareness context switcher
user32 = ctypes.windll.user32
try:
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
except AttributeError:
    pass

class DisplayTrigger(BaseThreadTrigger):
    """Display change trigger.

    Monitors Windows display changes via WM_DISPLAYCHANGE and WM_SETTINGCHANGE
    messages, detecting monitor plug/unplug and DPI scaling transitions, then
    fires callbacks.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hwnd = None
        self._prev_displays: frozenset[tuple[str, str]] = frozenset()
        self._prev_monitor_dpis: frozenset[tuple[tuple[int, int, int, int], int]] = frozenset()

    @staticmethod
    def _display_snapshot() -> frozenset[tuple[str, str]] | None:
        """Return a snapshot of currently connected displays."""
        try:
            display_info = get_display_info()
        except DisplayTopologyTransientError:
            return None
        return frozenset(
            (d.monitor_device_path, d.model or "")
            for d in display_info
        )

    @staticmethod
    def _extract_primary_dpi(dpi_snapshot: frozenset[tuple[tuple[int, int, int, int], int]]) -> int:
        """Extract the primary monitor (origin at 0,0) DPI from the full snapshot."""
        for rect, dpi in dpi_snapshot:
            if rect[0] == 0 and rect[1] == 0:
                return dpi
        return 96

    @override
    def start(self) -> None:
        super().start()
        logger.debug(f"{self.__class__.__name__} start")

    @override
    def stop(self) -> None:
        """Send WM_CLOSE to safely stop PumpMessages from another thread."""
        hwnd = self.hwnd
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        super().stop()
        logger.debug(f"{self.__class__.__name__} stop")

    def _setup_window(self) -> None:
        """Create a hidden watch window in the current thread."""
        className = f"DisplayMonitorClass_{id(self)}"
        hInstance = win32gui.GetModuleHandle(None)

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._msg_proc     # type: ignore
        wc.lpszClassName = className        # type: ignore
        wc.hInstance = hInstance            # type: ignore
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
        # Capture initial state after window creation — by now any transient
        # topology transition from startup is likely resolved, so the snapshot
        # has a better chance of succeeding.
        _prev_displays = self._display_snapshot()
        if _prev_displays is not None:
            self._prev_displays = _prev_displays
        self._prev_monitor_dpis = get_all_monitors_dpi_snapshot()

        logger.debug(f"Window created in thread {threading.get_ident()} and monitoring display/DPI changes")

    def _msg_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """Internal window procedure to handle Windows messages."""

        # Display topology/hotplug events AND system-wide environment (DPI scale) changes.
        # Unified: both message types check hardware + DPI snapshots in a single pass.
        if msg in (win32con.WM_DISPLAYCHANGE, win32con.WM_SETTINGCHANGE):
            curr_display = self._display_snapshot()
            if curr_display is None:
                logger.debug("Discard display trigger signal due to topology transition period")
                return 0

            curr_monitor_dpis = get_all_monitors_dpi_snapshot()

            is_changed = False

            # 1. Check for monitor (un)plug or resolution change
            if curr_display != self._prev_displays:
                logger.debug(f"Display hardware change detected: {curr_display}")
                self._prev_displays = curr_display
                is_changed = True

            # 2. Check for DPI scaling transition on any monitor
            if curr_monitor_dpis != self._prev_monitor_dpis:
                primary_dpi = self._extract_primary_dpi(curr_monitor_dpis)
                self._prev_monitor_dpis = curr_monitor_dpis
                is_changed = True

            # 3. Fire callback once if anything changed
            if is_changed:
                self.trigger()
            return 0

        elif msg == win32con.WM_CLOSE:
            logger.debug("Received WM_CLOSE, destroying window.")
            win32gui.DestroyWindow(hwnd)
            return 0

        elif msg == win32con.WM_DESTROY:
            logger.debug("Received WM_DESTROY, posting quit message to loop.")
            self.hwnd = None
            win32gui.PostQuitMessage(0)
            return 0

        return int(win32gui.DefWindowProc(hwnd, msg, wparam, lparam))

    @override
    def run(self) -> None:
        """Initialize COM and DPI awareness once for the thread's lifetime and run message loop."""
        pythoncom.CoInitialize()

        # [Key fix] Use thread-level (not process-level) DPI awareness isolation.
        # The main process (PySide6) already locks DPI awareness at process level,
        # so SetProcessDpiAwarenessContext would silently fail.  Thread-level
        # switching bypasses this restriction, giving this background thread
        # access to the real per-monitor DPI values from GetDpiForMonitor.
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            logger.debug("Successfully forced background thread DPI awareness (V2)")
        except Exception as e:
            logger.error(f"Failed to set thread DPI awareness: {e}")

        className = f"DisplayMonitorClass_{id(self)}"
        try:
            self._setup_window()
            win32gui.PumpMessages()
        finally:
            self.hwnd = None
            try:
                win32gui.UnregisterClass(className, win32gui.GetModuleHandle(None))     # type: ignore
            except Exception as e:
                logger.error(f"UnregisterClass failed: {e}")
            pythoncom.CoUninitialize()
            logger.debug("DisplayTrigger thread exited safely")
            