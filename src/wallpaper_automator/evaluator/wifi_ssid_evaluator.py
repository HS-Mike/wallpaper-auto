"""
WiFi SSID condition evaluator.

Checks whether the system is currently connected to a specific WiFi network
by parsing the output of `netsh wlan show interfaces`.
"""
import subprocess
import re

from .base_evaluator import BaseEvaluator


def get_current_ssid() -> str | None:
    encodings = ['utf-8', 'mbcs', 'gbk', 'cp936']
    result = None
    for enc in encodings:
        try:
            result = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                encoding=enc,
                stderr=subprocess.STDOUT
            )
            break
        except UnicodeDecodeError:
            continue
        except subprocess.CalledProcessError:
            return None
    if result is None:
        return None

    match = re.search(r'^\s*SSID\s*:\s*(.*)$', result, re.MULTILINE)

    if match:
        return match.group(1).strip()
    return None


class WIFISsidEvaluator(BaseEvaluator):

    def __call__(self, param: str):
        if not isinstance(param, str):
            raise ValueError(f"invalid {self.__class__.__name__} param")
        return param == get_current_ssid()
