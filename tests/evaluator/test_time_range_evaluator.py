import datetime as real_dt
from unittest.mock import patch

import pytest

from wallpaper_automator.evaluator.time_range_evaluator import TimeRangeEvaluator


@pytest.fixture
def evaluator():
    return TimeRangeEvaluator()


class TestTimeRangeEvaluator:

    # ── Normal range (start <= end) ──

    @pytest.mark.parametrize(
        "now_time,expected",
        [
            (real_dt.time(8, 0), False),   # before
            (real_dt.time(9, 0), True),    # at start (inclusive)
            (real_dt.time(12, 0), True),   # within
            (real_dt.time(17, 30), True),  # at end (inclusive)
            (real_dt.time(17, 31), False), # after (minute granularity)
            (real_dt.time(18, 0), False),  # after
        ],
    )
    def test_normal_range(self, evaluator, now_time, expected):
        fake_now = real_dt.datetime.combine(real_dt.date.today(), now_time)
        with patch(
            "wallpaper_automator.evaluator.time_range_evaluator.datetime.datetime",
            wraps=real_dt.datetime,
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            assert evaluator(["09:00", "17:30"]) is expected

    # ── Overnight range (start > end) ──

    @pytest.mark.parametrize(
        "now_time,expected",
        [
            (real_dt.time(6, 30), True),   # at end (inclusive)
            (real_dt.time(6, 31), False),  # after end, before start
            (real_dt.time(12, 0), False),  # outside gap
            (real_dt.time(21, 0), False),  # before start
            (real_dt.time(22, 0), True),   # at start (inclusive)
            (real_dt.time(23, 0), True),   # after start
            (real_dt.time(3, 0), True),    # before end
        ],
    )
    def test_overnight_range(self, evaluator, now_time, expected):
        fake_now = real_dt.datetime.combine(real_dt.date.today(), now_time)
        with patch(
            "wallpaper_automator.evaluator.time_range_evaluator.datetime.datetime",
            wraps=real_dt.datetime,
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            assert evaluator(["22:00", "06:30"]) is expected

    # ── Edge cases ──

    @pytest.mark.parametrize(
        "now_time,expected",
        [
            (real_dt.time(12, 0), True),   # matches the exact time
            (real_dt.time(12, 1), False),  # one minute off
            (real_dt.time(11, 59), False), # one minute off the other way
        ],
    )
    def test_start_equals_end(self, evaluator, now_time, expected):
        """When start==end, only that exact time should match."""
        fake_now = real_dt.datetime.combine(real_dt.date.today(), now_time)
        with patch(
            "wallpaper_automator.evaluator.time_range_evaluator.datetime.datetime",
            wraps=real_dt.datetime,
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            assert evaluator(["12:00", "12:00"]) is expected

    def test_invalid_param_not_list_raises(self, evaluator):
        with pytest.raises(ValueError, match="param must be a list/tuple"):
            evaluator("not a list")

    def test_invalid_param_wrong_length_raises(self, evaluator):
        with pytest.raises(ValueError, match="param must be a list/tuple"):
            evaluator(["09:00"])

    def test_invalid_time_string_raises(self, evaluator):
        with pytest.raises(ValueError, match="HH:MM"):
            evaluator(["09:00", "invalid"])

    def test_non_string_elements_raises_typeerror(self, evaluator):
        """Line 21: TypeError when param elements are not strings."""
        with pytest.raises(TypeError, match="strings in HH:MM format"):
            evaluator([9, 17])
