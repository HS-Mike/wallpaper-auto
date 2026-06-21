import logging
import ctypes

import pythoncom
import win32com.client
import win32con
import win32gui

from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)

KERNEL32 = ctypes.windll.kernel32


def decode_wmi_string(char_array: bytes) -> str:
    """Convert WMI uint16 array to a readable ASCII string."""
    if not char_array:
        return "Unknown"
    try:
        return "".join(chr(char) for char in char_array if char != 0).strip()
    except Exception:
        return "Unknown"


class DisplayTrigger(BaseThreadTrigger):
    """Display change trigger.

    Monitors Windows display changes via the WM_DISPLAYCHANGE message,
    detecting monitor plug/unplug, and fires callbacks on transitions.
    """

    def __init__(self) -> None:
        super().__init__()
        self._exit_event = None
        self._hwnd = None
        self._prev_displays: set[tuple[str, str, str, str]] = set()

    @staticmethod
    def _get_display_set() -> set[tuple[str, str, str, str]]:
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

    def _msg_proc(self, hwnd, msg, wparam, lparam):
        """Internal window procedure to handle Windows messages."""
        if msg == win32con.WM_DISPLAYCHANGE:
            curr_display = self._get_display_set()
            if curr_display != self._prev_displays:
                logger.info(f"Display change detected: {curr_display}")
                self._prev_displays = curr_display
                # Trigger base class callback
                self.trigger()
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
        self._prev_displays = self._get_display_set()

        # 1. Register window class
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._msg_proc                         # type: ignore[assignment]
        wc.lpszClassName = f"DisplayMonitorClass_{id(self)}"    # type: ignore[assignment]
        class_atom = win32gui.RegisterClass(wc)

        # 2. Create hidden message window
        self._hwnd = win32gui.CreateWindow(
            class_atom,
            "DisplayMonitorWindow",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            win32gui.GetModuleHandle(None),
            None,
        )

        logger.debug("DisplayTrigger message loop started")

        try:
            # 3. Message loop: run while _stop_event is not set and no external stop signal
            # Use non-blocking Pump with timeout-based event wait to avoid busy-looping
            while not self._stop_event.is_set():
                win32gui.PumpWaitingMessages()

                # Check exit signal via WaitForSingleObject without blocking the message loop
                # 10 ms timeout balances responsiveness and CPU usage
                res = KERNEL32.WaitForSingleObject(self._exit_event, 10)
                if res == 0:  # WAIT_OBJECT_0
                    logger.debug("Exit signal received, stopping loop")
                    break
        finally:
            # 4. Destroy window
            if self._hwnd:
                win32gui.DestroyWindow(self._hwnd)
                self._hwnd = None
            win32gui.UnregisterClass(class_atom, win32gui.GetModuleHandle(None))
            logger.info("DisplayTrigger thread exited safely")

    def activate(self) -> None:
        if self._exit_event:
            KERNEL32.CloseHandle(self._exit_event)
        self._exit_event = KERNEL32.CreateEventW(None, False, False, None)
        super().activate()
        logger.debug(f"{self.__class__.__name__} activate")

    def deactivate(self) -> None:
        if self._exit_event:
            KERNEL32.SetEvent(self._exit_event)
        super().deactivate()
        if self._exit_event:
            KERNEL32.CloseHandle(self._exit_event)
            self._exit_event = None
        logger.debug(f"{self.__class__.__name__} deactivate")