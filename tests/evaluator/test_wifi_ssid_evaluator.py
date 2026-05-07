"""Tests for wifi_ssid_evaluator.py — SSID detection and matching."""

import subprocess
from unittest.mock import patch

import pytest

from wallpaper_automator.evaluator.wifi_ssid_evaluator import (
    WIFISsidEvaluator,
    get_current_ssid,
)

_MOD = "wallpaper_automator.evaluator.wifi_ssid_evaluator"


@pytest.fixture
def evaluator():
    return WIFISsidEvaluator()


class TestGetCurrentSsid:
    def test_returns_ssid_from_output(self):
        with patch(f"{_MOD}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = "    SSID               : MyNetwork\n"
            assert get_current_ssid() == "MyNetwork"

    def test_returns_ssid_with_spaces(self):
        with patch(f"{_MOD}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = "    SSID               : My Home WiFi\n"
            assert get_current_ssid() == "My Home WiFi"

    def test_returns_none_when_no_ssid_line(self):
        with patch(f"{_MOD}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = "    State              : connected\n"
            assert get_current_ssid() is None

    def test_returns_empty_string_when_ssid_value_is_empty(self):
        with patch(f"{_MOD}.subprocess.check_output") as mock_check_output:
            mock_check_output.return_value = "    SSID               : \n"
            assert get_current_ssid() == ""

    def test_returns_none_when_all_encodings_fail(self):
        with patch(f"{_MOD}.subprocess.check_output") as mock_check_output:
            mock_check_output.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "reason")
            assert get_current_ssid() is None

    def test_returns_none_on_called_process_error(self):
        with patch(f"{_MOD}.subprocess.check_output") as mock_check_output:
            mock_check_output.side_effect = subprocess.CalledProcessError(1, [])
            assert get_current_ssid() is None

    def test_tries_alternative_encoding_on_unicode_error(self):
        with patch(f"{_MOD}.subprocess.check_output") as mock_check_output:
            calls = []

            def side_effect(*args, **kwargs):
                calls.append(kwargs.get("encoding", "unknown"))
                if len(calls) == 1:
                    raise UnicodeDecodeError("utf-8", b"", 0, 1, "reason")
                return "    SSID               : FallbackNetwork\n"

            mock_check_output.side_effect = side_effect
            result = get_current_ssid()
            assert result == "FallbackNetwork"
            assert calls == ["utf-8", "mbcs"]


class TestWIFISsidEvaluator:
    def test_returns_true_when_ssid_matches(self, evaluator):
        with patch(f"{_MOD}.get_current_ssid") as mock_get_ssid:
            mock_get_ssid.return_value = "HomeWiFi"
            assert evaluator("HomeWiFi")

    def test_returns_false_when_ssid_differs(self, evaluator):
        with patch(f"{_MOD}.get_current_ssid") as mock_get_ssid:
            mock_get_ssid.return_value = "HomeWiFi"
            assert not evaluator("WorkWiFi")

    def test_returns_false_when_not_connected(self, evaluator):
        with patch(f"{_MOD}.get_current_ssid") as mock_get_ssid:
            mock_get_ssid.return_value = None
            assert not evaluator("HomeWiFi")

    def test_raises_when_target_is_none(self, evaluator):
        with patch(f"{_MOD}.get_current_ssid"):
            with pytest.raises(ValueError, match="invalid WIFISsidEvaluator param"):
                evaluator(None)

    def test_raises_when_target_not_string(self, evaluator):
        with pytest.raises(ValueError, match="invalid WIFISsidEvaluator param"):
            evaluator(123)
