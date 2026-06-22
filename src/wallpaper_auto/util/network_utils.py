"""
Network utility functions.

Provides platform-level helpers for querying network state, such as
retrieving the current WiFi SSID.
"""

import re
import subprocess


def get_current_ssid() -> str | None:
    """Return the current WiFi SSID, or None if not connected / on error."""
    encodings = ["utf-8", "mbcs", "gbk", "cp936"]
    result = None
    for enc in encodings:
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            result = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                encoding=enc,
                stderr=subprocess.STDOUT,
                startupinfo=startupinfo,
            )
            break
        except UnicodeDecodeError:
            continue
        except subprocess.CalledProcessError:
            return None
    if result is None:
        return None

    match = re.search(r"^\s*SSID\s*:\s*(.*)$", result, re.MULTILINE)

    if match:
        return match.group(1).strip()
    return None
