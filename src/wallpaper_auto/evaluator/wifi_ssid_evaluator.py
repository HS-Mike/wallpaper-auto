"""
WiFi SSID condition evaluator.

Checks whether the system is currently connected to a specific WiFi network
by parsing the output of `netsh wlan show interfaces`.
"""

from ..util.network_util import get_current_ssid
from .base_evaluator import BaseEvaluator


class WIFISsidEvaluator(BaseEvaluator):
    def __call__(self, param: str) -> bool:
        if not isinstance(param, str):
            raise ValueError(f"invalid {self.__class__.__name__} param")
        return param == get_current_ssid()
