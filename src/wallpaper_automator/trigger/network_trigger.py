"""
Network change trigger.

Monitors the Windows routing table for changes in default gateway or active IP,
indicating a WiFi or wired network switch, and trigger callbacks on network transitions.
"""
import logging
import re
import subprocess

import wmi
import pythoncom

from .base_trigger import BaseThreadTrigger
from ..util.callback_register import CallbackRegister


logger = logging.getLogger(__name__)


class NetworkTrigger(BaseThreadTrigger):

    def __init__(self) -> None:
        super().__init__()
        self._last_gateways: set[str] = set()
        self.c = None

    def _get_network_fingerprint(self) -> set[str]:
        """Get current network fingerprint (default gateway and active IP)."""
        configs = self.c.Win32_NetworkAdapterConfiguration(IPEnabled=True)  #type: ignore
        fingerprint = []
        for config in configs:
            gateways = getattr(config, 'DefaultIPGateway', [])
            if gateways:
                fingerprint.append(f"{config.Description}_{gateways[0]}")
        return set(sorted(fingerprint))

    def run(self) -> None:
        pythoncom.CoInitialize()
        self.c = wmi.WMI()
        logger.info("start monitoring WiFi switches and cable changes")
        self._last_gateways = self._get_network_fingerprint()
        watcher = self.c.watch_for(
            notification_type="Operation",
            wmi_class="Win32_IP4RouteTable"
        )
        while not self.stop_event.is_set():
            try:
                watcher(timeout_ms=1000)
                current_gateways = self._get_network_fingerprint()
                if current_gateways != self._last_gateways:
                    logger.debug(f"current gateway(s): {", ".join(current_gateways) if current_gateways else None}")
                    self.trigger()
                    self._last_gateways = current_gateways
            except wmi.x_wmi_timed_out:
                continue

        del watcher
        self.c = None
        pythoncom.CoUninitialize()

        logger.info("NetworkTrigger stopped")

