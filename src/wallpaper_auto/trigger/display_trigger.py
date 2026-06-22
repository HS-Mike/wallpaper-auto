"""
Display change trigger for monitor plug/unplug detection.

Uses WM_DISPLAYCHANGE via a hidden Win32 window and WMI (WmiMonitorID)
to detect and report connected monitor changes.
"""

import logging
import threading

import pythoncom
import win32com.client
import win32con
import win32gui

from .base_trigger import BaseThreadTrigger


logger = logging.getLogger(__name__)

WM_USER_DISPLAY_TRIGGER_QUIT = win32gui.RegisterWindowMessage("DisplayTrigger_Internal_Quit_Message")


def decode_wmi_string(char_array: bytes) -> str:
    """Convert WMI uint16 array to a readable ASCII string."""
    if not char_array:
        return "Unknown"
    try:
        return "".join(chr(char) for char in char_array if char != 0).strip()
    except Exception:
        return "Unknown"
    

def get_display_set() -> set[tuple[str, str, str, str]]:
    """Query WmiMonitorID to get a set of unique PnP IDs for all connected monitors.

    Caller must ensure COM is initialized on the calling thread.
    """
    try:
        wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
        monitors = wmi.ExecQuery("SELECT * FROM WmiMonitorID")
        pnp_ids = set()
        for monitor in monitors:
            raw_instance = monitor.InstanceName
            true_pnp_id = (
                raw_instance.rsplit("_", 1)[0]
                if "_" in raw_instance
                else raw_instance
            )
            manufacturer = decode_wmi_string(monitor.ManufacturerName)
            model_name = decode_wmi_string(monitor.UserFriendlyName)
            serial_num = decode_wmi_string(monitor.SerialNumberID)
            pnp_ids.add((manufacturer, model_name, true_pnp_id, serial_num))
        return pnp_ids
    except Exception as e:
        logger.error(f"WMI query failed: {e}")
        return set()


class DisplayTrigger(BaseThreadTrigger):
    """Display change trigger.

    Monitors Windows display changes via the WM_DISPLAYCHANGE message,
    detecting monitor plug/unplug, and fires callbacks on transitions.
    """

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = None     # not used in this implementation
        self._window_lock = threading.Lock()
        self._hwnd = None
        self._prev_displays: set[tuple[str, str, str, str]] = set()
        self.current_displays: set[tuple[str, str, str, str]] | None = None    # only avaliable in callback

    def _msg_proc(self, hwnd, msg, wparam, lparam):
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

        if msg == WM_USER_DISPLAY_TRIGGER_QUIT:
            logger.debug("Received WM_USER_DISPLAY_TRIGGER_QUIT, posting quit message to loop.")
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def run(self) -> None:
        """Initialize COM once for the thread's lifetime and run message loop"""
        pythoncom.CoInitialize()
        try:
            self._run_impl()
        finally:
            pythoncom.CoUninitialize()

    def _run_impl(self) -> None:
        self._prev_displays = get_display_set()

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._msg_proc                         # type: ignore[assignment]
        wc.lpszClassName = f"DisplayMonitorClass_{id(self)}"    # type: ignore[assignment]
        class_atom = win32gui.RegisterClass(wc)

        with self._window_lock:
            self._hwnd = win32gui.CreateWindow(
                class_atom,                             # lpszClassName
                f"DisplayMonitor_{id(self)}",           # lpszWindowName
                0,                                      # dwStyle
                0,                                      # x
                0,                                      # y
                0,                                      # nWidth
                0,                                      # nHeight
                0,                                      # hWndParent
                0,                                      # hMenu
                win32gui.GetModuleHandle(None),         # hInstance
                None,                                   # lpParam
            )

        logger.debug("DisplayTrigger pure event-driven message loop started")

        try:
            win32gui.PumpMessages()
        finally:
            with self._window_lock:
                if self._hwnd:
                    win32gui.DestroyWindow(self._hwnd)
                    self._hwnd = None
            win32gui.UnregisterClass(class_atom, win32gui.GetModuleHandle(None))
            logger.debug("DisplayTrigger thread exited safely")

    def activate(self) -> None:
        self.start()

    def deactivate(self) -> None:
        with self._window_lock:
            if self._hwnd:
                win32gui.PostMessage(self._hwnd, WM_USER_DISPLAY_TRIGGER_QUIT, 0, 0)
        self.join(timeout=3)
        logger.debug(f"{self.__class__.__name__} deactivate")
        