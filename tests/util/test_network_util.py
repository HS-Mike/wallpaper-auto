"""Tests for network_util.py — SSID detection."""

import subprocess
from unittest.mock import patch

from wallpaper_auto.util.network_util import get_current_ssid

_NET_UTIL = "wallpaper_auto.util.network_util"


class TestGetCurrentSsid:
    def test_returns_ssid_from_output(self):
        with patch(f"{_NET_UTIL}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = b"    SSID               : MyNetwork\n"
            assert get_current_ssid() == "MyNetwork"

    def test_returns_ssid_with_spaces(self):
        with patch(f"{_NET_UTIL}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = b"    SSID               : My Home WiFi\n"
            assert get_current_ssid() == "My Home WiFi"

    def test_returns_none_when_no_ssid_line(self):
        with patch(f"{_NET_UTIL}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = b"    State              : connected\n"
            assert get_current_ssid() is None

    def test_returns_empty_string_when_ssid_value_is_empty(self):
        with patch(f"{_NET_UTIL}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = b"    SSID               : \n"
            assert get_current_ssid() == ""

    def test_returns_none_when_all_encodings_fail(self):
        with patch(f"{_NET_UTIL}.subprocess.check_output") as mock_check_output:

            class AlwaysFail:
                @staticmethod
                def decode(_encoding: str) -> str:
                    raise UnicodeDecodeError("utf-8", b"", 0, 1, "mock")

            mock_check_output.return_value = AlwaysFail()
            assert get_current_ssid() is None

    def test_returns_none_on_called_process_error(self):
        with patch(f"{_NET_UTIL}.subprocess.check_output") as mock_check_output:
            mock_check_output.side_effect = subprocess.CalledProcessError(1, [])
            assert get_current_ssid() is None

    def test_tries_alternative_encoding_on_unicode_error(self):
        with patch(f"{_NET_UTIL}.subprocess.check_output") as mock_check_output:
            calls: list[str] = []

            class FirstFails:
                @staticmethod
                def decode(encoding: str) -> str:
                    calls.append(encoding)
                    if len(calls) == 1:
                        raise UnicodeDecodeError(encoding, b"", 0, 1, "mock")
                    return "    SSID               : FallbackNetwork\n"

            mock_check_output.return_value = FirstFails()
            result = get_current_ssid()
            assert result == "FallbackNetwork"
            assert calls == ["utf-8", "mbcs"]
