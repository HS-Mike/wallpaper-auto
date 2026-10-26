"""
Network change trigger.

Monitors Windows network address changes via the NotifyAddrChange API,
detecting WiFi or wired network switches, and fires callbacks on transitions.
"""

import ctypes
import logging
import platform
import time
from ctypes import wintypes

import pythoncom
import wmi

from ..util.network_util import get_current_ssid
from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)

IPHLPAPI = ctypes.windll.iphlpapi
KERNEL32 = ctypes.windll.kernel32

ULONG_PTR = ctypes.c_uint64 if platform.architecture()[0] == "64bit" else ctypes.c_uint32


class Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ULONG_PTR),
        ("InternalHigh", ULONG_PTR),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


KERNEL32.CreateEventW.argtypes = [
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
KERNEL32.CreateEventW.restype = wintypes.HANDLE
KERNEL32.SetEvent.argtypes = [wintypes.HANDLE]
KERNEL32.SetEvent.restype = wintypes.BOOL
KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL32.CloseHandle.restype = wintypes.BOOL
KERNEL32.WaitForMultipleObjects.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.BOOL,
    wintypes.DWORD,
]
KERNEL32.WaitForMultipleObjects.restype = wintypes.DWORD
KERNEL32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
KERNEL32.WaitForSingleObject.restype = wintypes.DWORD

IPHLPAPI.NotifyAddrChange.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(Overlapped),
]
IPHLPAPI.NotifyAddrChange.restype = wintypes.DWORD
IPHLPAPI.CancelIPChangeNotify.argtypes = [ctypes.POINTER(Overlapped)]
IPHLPAPI.CancelIPChangeNotify.restype = wintypes.BOOL


_INFINITE = 0xFFFFFFFF
# How long to wait for a cancelled NotifyAddrChange to report completion,
# keeping the OVERLAPPED and event alive until the kernel is done with them.
_CANCEL_WAIT_MS = 1000


class NetworkTrigger(BaseThreadTrigger):
    def __init__(self) -> None:
        super().__init__()
        self._exit_event = None
        self._last_gateways: set[str] = set()
        self.current_ssid: str | None = None  # only available in callback

    @staticmethod
    def _get_network_fingerprint() -> set[str]:
        """
        Get current network fingerprint (default gateway and description).
        Caller must ensure COM is initialized on the calling thread.
        """
        try:
            c = wmi.WMI()
            configs = c.Win32_NetworkAdapterConfiguration(IPEnabled=True)
            fingerprint = []
            for config in configs:
                gateways = getattr(config, "DefaultIPGateway", [])
                if gateways and gateways[0]:
                    fingerprint.append(f"{config.Description}_{gateways[0]}")
            return set(fingerprint)
        except Exception as e:
            logger.error(f"Failed to get network fingerprint: {e}")
            return set()

    def run(self) -> None:
        """Initialize COM once for the thread's lifetime"""
        pythoncom.CoInitialize()
        try:
            self._run_impl()
        finally:
            pythoncom.CoUninitialize()

    def _run_impl(self) -> None:
        self._last_gateways = self._get_network_fingerprint()
        net_event = KERNEL32.CreateEventW(None, False, False, None)
        overlap = Overlapped()
        overlap.hEvent = net_event
        handle = wintypes.HANDLE()

        handles = (wintypes.HANDLE * 2)(net_event, self._exit_event)
        # Whether the NotifyAddrChange registered in the current iteration is
        # still outstanding.  The kernel writes the completion into ``overlap``
        # and signals ``net_event``: neither may be freed/closed until it is
        # done with them.
        pending = False

        try:
            while not self.stop_event.is_set():
                res = IPHLPAPI.NotifyAddrChange(ctypes.byref(handle), ctypes.byref(overlap))
                if res != 0 and res != 997:
                    raise ctypes.WinError(res)
                pending = res == 997

                result = KERNEL32.WaitForMultipleObjects(2, handles, False, _INFINITE)

                if result == 0:
                    pending = False
                    time.sleep(0.2)
                    current_gateways = self._get_network_fingerprint()
                    if current_gateways != self._last_gateways:
                        logger.info(f"Network change detected: {current_gateways}")
                        self._last_gateways = current_gateways
                        self.current_ssid = get_current_ssid()
                        self.trigger()
                        self.current_ssid = None
                elif result == 1:
                    break
                else:
                    raise ctypes.WinError()
        finally:
            # An outstanding registration would outlive this frame and complete
            # into freed memory (or signal a closed event), an access violation
            # outside Python that kills the process without running atexit.
            # Cancel it and let the cancellation report completion first.
            if pending:
                IPHLPAPI.CancelIPChangeNotify(ctypes.byref(overlap))
                KERNEL32.WaitForSingleObject(net_event, _CANCEL_WAIT_MS)
            KERNEL32.CloseHandle(net_event)

    def start(self) -> None:
        if self._exit_event:
            raise RuntimeError(f"{type(self).__name__} is already started")
        self._exit_event = KERNEL32.CreateEventW(None, False, False, None)
        super().start()

    def stop(self) -> None:
        if self._exit_event:
            KERNEL32.SetEvent(self._exit_event)
        super().stop()
        if self._exit_event:
            KERNEL32.CloseHandle(self._exit_event)
            self._exit_event = None
