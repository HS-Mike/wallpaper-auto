"""Tests for display_evaluator.py — display topology condition checks."""

from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.display_evaluator import (
    DisplayCountEvaluator,
    DisplayModelEvaluator,
)

_MOD = "wallpaper_auto.evaluator.display_evaluator"


@pytest.fixture
def model_evaluator():
    return DisplayModelEvaluator()


@pytest.fixture
def count_evaluator():
    return DisplayCountEvaluator()


MONITOR_SET_SINGLE = {("DEL", "U2719D", r"DISPLAY\DELA123", "ABC123")}
MONITOR_SET_DUAL = {
    ("DEL", "U2719D", r"DISPLAY\DELA123", "ABC123"),
    ("BNQ", "XL2730", r"DISPLAY\BNQ456", "DEF456"),
}


class TestDisplayModelEvaluator:
    def test_returns_true_when_model_matches(self, model_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert model_evaluator("U2719D")

    def test_returns_true_when_model_matches_dual(self, model_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_DUAL):
            assert model_evaluator("XL2730")

    def test_returns_false_when_model_does_not_match(self, model_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert not model_evaluator("NonExistent")

    def test_returns_false_when_no_displays(self, model_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=set()):
            assert not model_evaluator("U2719D")

    def test_raises_when_param_not_string(self, model_evaluator):
        with pytest.raises(ValueError, match="invalid DisplayModelEvaluator param"):
            model_evaluator(123)  # type: ignore[arg-type]

    def test_raises_when_param_is_none(self, model_evaluator):
        with pytest.raises(ValueError, match="invalid DisplayModelEvaluator param"):
            model_evaluator(None)  # type: ignore[arg-type]


class TestDisplayCountEvaluator:
    def test_returns_true_when_count_matches(self, count_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert count_evaluator(1)

    def test_returns_true_for_dual_displays(self, count_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_DUAL):
            assert count_evaluator(2)

    def test_returns_false_when_count_differs(self, count_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert not count_evaluator(2)

    def test_returns_false_when_no_displays(self, count_evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=set()):
            assert not count_evaluator(1)

    def test_raises_when_param_not_int(self, count_evaluator):
        with pytest.raises(ValueError, match="invalid DisplayCountEvaluator param"):
            count_evaluator("2")  # type: ignore[arg-type]

    def test_raises_when_param_is_bool(self, count_evaluator):
        with pytest.raises(ValueError, match="invalid DisplayCountEvaluator param"):
            count_evaluator(True)  # type: ignore[arg-type]

    def test_raises_when_param_is_none(self, count_evaluator):
        with pytest.raises(ValueError, match="invalid DisplayCountEvaluator param"):
            count_evaluator(None)  # type: ignore[arg-type]
