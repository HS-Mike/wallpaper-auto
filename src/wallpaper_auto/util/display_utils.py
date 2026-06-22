"""
Display utility functions.

Provides helpers for querying connected monitor information via WMI,
such as enumerating display PnP IDs, manufacturer, model, and serial.
"""

import logging

import win32com.client

logger = logging.getLogger(__name__)


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
