"""Tests for WeekdayEvaluator."""

import datetime
from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.weekday_evaluator import WeekdayEvaluator


class TestWeekdayEvaluator:
    @pytest.mark.parametrize("today_weekday", [0, 1, 2, 3, 4, 5, 6])
    def test_single_weekday_match(self, today_weekday):
        """Single-element list matching today should return True."""
        evaluator = WeekdayEvaluator()
        with patch.object(datetime, "datetime") as mock_dt:
            mock_dt.now.return_value.weekday.return_value = today_weekday
            assert evaluator([today_weekday])

    @pytest.mark.parametrize(
        "today_weekday, candidates",
        [
            (0, [0, 2, 4]),
            (5, [5, 6]),
            (6, [0, 6]),
        ],
    )
    def test_list_match(self, today_weekday, candidates):
        """List containing today should return True."""
        evaluator = WeekdayEvaluator()
        with patch.object(datetime, "datetime") as mock_dt:
            mock_dt.now.return_value.weekday.return_value = today_weekday
            assert evaluator(candidates)

    @pytest.mark.parametrize(
        "today_weekday, candidates",
        [
            (0, [1, 2]),
            (5, [0, 1, 2, 3, 4]),
            (6, [5]),
        ],
    )
    def test_no_match(self, today_weekday, candidates):
        """List not containing today should return False."""
        evaluator = WeekdayEvaluator()
        with patch.object(datetime, "datetime") as mock_dt:
            mock_dt.now.return_value.weekday.return_value = today_weekday
            assert not evaluator(candidates)


class TestWeekdayEvaluatorValidation:
    def test_empty_list_raises(self):
        evaluator = WeekdayEvaluator()
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator([])

    def test_int_out_of_range_raises(self):
        evaluator = WeekdayEvaluator()
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator([7])
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator([-1])

    def test_non_list_param_raises(self):
        evaluator = WeekdayEvaluator()
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator("not_a_list")
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator(5)
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator(None)

    def test_non_int_element_raises(self):
        evaluator = WeekdayEvaluator()
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator([0, "1"])
        with pytest.raises(ValueError, match="invalid WeekdayEvaluator param"):
            evaluator([1, 2.0])
