"""
Time range condition evaluator.

Checks whether the current local time falls within a specified HH:MM range.
Supports overnight ranges (e.g., 23:00 to 05:00) that cross midnight.
"""
import datetime

from .base_evaluator import BaseEvaluator


class TimeRangeEvaluator(BaseEvaluator):

    def __call__(
        self,
        param: list[str]
    ):
        if not isinstance(param, (list, tuple)) or len(param) != 2:
            raise ValueError("param must be a list/tuple of exactly 2 time strings (HH:MM)")
        if not all(isinstance(s, str) for s in param):
            raise TypeError("param elements must be strings in HH:MM format")
        try:
            start_time, end_time = [datetime.datetime.strptime(t, "%H:%M").time() for t in param]
        except ValueError as e:
            raise ValueError("time strings must be in HH:MM format") from e
        curr = datetime.datetime.now().time()
        if start_time <= end_time:
            return start_time <= curr <= end_time
        else:  # overnight
            return curr >= start_time or curr <= end_time
        