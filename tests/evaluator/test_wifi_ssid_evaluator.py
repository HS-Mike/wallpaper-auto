"""Tests for wifi_ssid_evaluator.py — SSID matching."""

from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.wifi_ssid_evaluator import WIFISsidEvaluator

_EVAL = "wallpaper_auto.evaluator.wifi_ssid_evaluator"


@pytest.fixture
def evaluator():
    return WIFISsidEvaluator()


class TestWIFISsidEvaluator:
    def test_returns_true_when_ssid_matches(self, evaluator):
        with patch(f"{_EVAL}.get_current_ssid") as mock_get_ssid:
            mock_get_ssid.return_value = "HomeWiFi"
            assert evaluator("HomeWiFi")

    def test_returns_false_when_ssid_differs(self, evaluator):
        with patch(f"{_EVAL}.get_current_ssid") as mock_get_ssid:
            mock_get_ssid.return_value = "HomeWiFi"
            assert not evaluator("WorkWiFi")

    def test_returns_false_when_not_connected(self, evaluator):
        with patch(f"{_EVAL}.get_current_ssid") as mock_get_ssid:
            mock_get_ssid.return_value = None
            assert not evaluator("HomeWiFi")

    def test_raises_when_target_is_none(self, evaluator):
        with patch(f"{_EVAL}.get_current_ssid"):
            with pytest.raises(ValueError, match="invalid WIFISsidEvaluator param"):
                evaluator(None)

    def test_raises_when_target_not_string(self, evaluator):
        with pytest.raises(ValueError, match="invalid WIFISsidEvaluator param"):
            evaluator(123)
