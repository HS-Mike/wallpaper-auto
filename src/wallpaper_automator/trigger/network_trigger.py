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


class NetworkTrigger(BaseThreadTrigger):
    def __init__(self) -> None:
        super().__init__()
        self._exit_event = None
        self._last_gateways: set[str] = set()

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

    def run(self):
        """Initialize COM once for the thread's lifetime"""
        pythoncom.CoInitialize()
        try:
            self._run_impl()
        finally:
            pythoncom.CoUninitialize()

    def _run_impl(self):
        self._last_gateways = self._get_network_fingerprint()
        net_event = KERNEL32.CreateEventW(None, False, False, None)
        overlap = Overlapped()
        overlap.hEvent = net_event
        handle = wintypes.HANDLE()

        handles = (wintypes.HANDLE * 2)(net_event, self._exit_event)

        try:
            while not self._stop_event.is_set():
                res = IPHLPAPI.NotifyAddrChange(ctypes.byref(handle), ctypes.byref(overlap))
                if res != 0 and res != 997:
                    logger.error(f"NotifyAddrChange registration failed: {res}")
                    break

                result = KERNEL32.WaitForMultipleObjects(2, handles, False, -1)

                if result == 0:
                    time.sleep(0.2)
                    current_gateways = self._get_network_fingerprint()
                    if current_gateways != self._last_gateways:
                        logger.info(f"Network change detected: {current_gateways}")
                        self._last_gateways = current_gateways
                        self.trigger()
                elif result == 1:
                    logger.debug("Exit signal received, stopping")
                    break
        finally:
            KERNEL32.CloseHandle(net_event)
            logger.info("NetworkTrigger thread exited safely")

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
