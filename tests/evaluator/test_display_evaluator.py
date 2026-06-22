"""Tests for display_evaluator.py — display model condition check."""

from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.display_evaluator import DisplayModelEvaluator

_MOD = "wallpaper_auto.evaluator.display_evaluator"


@pytest.fixture
def evaluator():
    return DisplayModelEvaluator()


MONITOR_SET_SINGLE = {("DEL", "U2719D", r"DISPLAY\DELA123", "ABC123")}
MONITOR_SET_DUAL = {
    ("DEL", "U2719D", r"DISPLAY\DELA123", "ABC123"),
    ("BNQ", "XL2730", r"DISPLAY\BNQ456", "DEF456"),
}


class TestDisplayModelEvaluator:
    def test_returns_true_when_model_matches(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert evaluator("U2719D")

    def test_returns_true_when_model_matches_dual(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_DUAL):
            assert evaluator("XL2730")

    def test_returns_false_when_model_does_not_match(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert not evaluator("NonExistent")

    def test_returns_false_when_no_displays(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=set()):
            assert not evaluator("U2719D")

    def test_raises_when_param_not_string(self, evaluator):
        with pytest.raises(ValueError, match="invalid DisplayModelEvaluator param"):
            evaluator(123)  # type: ignore[arg-type]

    def test_raises_when_param_is_none(self, evaluator):
        with pytest.raises(ValueError, match="invalid DisplayModelEvaluator param"):
            evaluator(None)  # type: ignore[arg-type]
