"""Tests for display_evaluator.py — display model condition check."""

from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.display_evaluator import HaveDisplayEvaluator
from wallpaper_auto.util.display_utils import DisplayInfo

_MOD = "wallpaper_auto.evaluator.display_evaluator"


@pytest.fixture
def evaluator():
    return HaveDisplayEvaluator()


_SINGLE = [
    DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        model="U2719D",
        source_resolution=(1920, 1080),
        position=(0, 0),
        target_resolution=(1920, 1080),
        scale=1.0,
    ),
]
_DUAL = [
    DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        model="U2719D",
        source_resolution=(1920, 1080),
        position=(0, 0),
        target_resolution=(1920, 1080),
        scale=1.0,
    ),
    DisplayInfo(
        device_name="\\\\.\\DISPLAY2",
        model="XL2730",
        source_resolution=(2560, 1440),
        position=(1920, 0),
        target_resolution=(2560, 1440),
        scale=1.0,
    ),
]


class TestHaveDisplayEvaluator:
    def test_exact_model_match(self, evaluator):
        with patch(f"{_MOD}.get_display_info", return_value=_SINGLE):
            assert evaluator("U2719D")

    def test_regex_pattern_match(self, evaluator):
        with patch(f"{_MOD}.get_display_info", return_value=_SINGLE):
            assert evaluator(r"27.*")

    def test_regex_matches_any_display(self, evaluator):
        with patch(f"{_MOD}.get_display_info", return_value=_DUAL):
            assert evaluator(r"XL\d+")

    def test_returns_false_when_no_match(self, evaluator):
        with patch(f"{_MOD}.get_display_info", return_value=_SINGLE):
            assert not evaluator("NonExistent")

    def test_returns_false_when_no_displays(self, evaluator):
        with patch(f"{_MOD}.get_display_info", return_value=[]):
            assert not evaluator("U2719D")

    def test_returns_false_when_query_fails(self, evaluator):
        with patch(f"{_MOD}.get_display_info", return_value=None):
            assert not evaluator("U2719D")

    def test_returns_false_when_model_is_none(self, evaluator):
        with patch(
            f"{_MOD}.get_display_info",
            return_value=[
                DisplayInfo(
                    device_name="\\\\.\\DISPLAY1",
                    model=None,
                    source_resolution=(1920, 1080),
                    position=(0, 0),
                    target_resolution=(1920, 1080),
                    scale=1.0,
                ),
            ],
        ):
            assert not evaluator("U2719D")

    def test_raises_when_param_not_string(self, evaluator):
        with pytest.raises(ValueError, match="invalid HaveDisplayEvaluator param"):
            evaluator(123)  # type: ignore[arg-type]

    def test_raises_when_param_is_none(self, evaluator):
        with pytest.raises(ValueError, match="invalid HaveDisplayEvaluator param"):
            evaluator(None)  # type: ignore[arg-type]
