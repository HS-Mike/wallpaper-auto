"""Tests for display_evaluator.py — display model condition check."""

from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.display_evaluator import HaveDisplayEvaluator

_MOD = "wallpaper_auto.evaluator.display_evaluator"


@pytest.fixture
def evaluator():
    return HaveDisplayEvaluator()


MONITOR_SET_SINGLE = {("DEL", "U2719D", r"DISPLAY\DELA123", "ABC123")}
MONITOR_SET_DUAL = {
    ("DEL", "U2719D", r"DISPLAY\DELA123", "ABC123"),
    ("BNQ", "XL2730", r"DISPLAY\BNQ456", "DEF456"),
}


class TestHaveDisplayEvaluator:
    def test_exact_model_match(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert evaluator("U2719D")

    def test_regex_pattern_match(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert evaluator(r"27.*")

    def test_regex_matches_any_display(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_DUAL):
            assert evaluator(r"XL\d+")

    def test_returns_false_when_no_match(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=MONITOR_SET_SINGLE):
            assert not evaluator("NonExistent")

    def test_returns_false_when_no_displays(self, evaluator):
        with patch(f"{_MOD}.get_display_set", return_value=set()):
            assert not evaluator("U2719D")

    def test_raises_when_param_not_string(self, evaluator):
        with pytest.raises(ValueError, match="invalid HaveDisplayEvaluator param"):
            evaluator(123)  # type: ignore[arg-type]

    def test_raises_when_param_is_none(self, evaluator):
        with pytest.raises(ValueError, match="invalid HaveDisplayEvaluator param"):
            evaluator(None)  # type: ignore[arg-type]
