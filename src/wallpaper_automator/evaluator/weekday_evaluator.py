"""
Day-of-week condition evaluator.

Checks whether the current day matches a given list of weekday numbers
(0=Monday, 6=Sunday), using only local ``datetime`` — no external API.
"""

import datetime

from .base_evaluator import BaseEvaluator


class WeekdayEvaluator(BaseEvaluator):
    """Evaluator that checks if today's weekday is in the provided list.

    Param type: ``list[int]`` — weekday numbers where 0=Monday ... 6=Sunday.
    """

    def __call__(self, param: list[int]) -> bool:
        self._validate_param(param)
        today = datetime.datetime.now().weekday()
        return today in param

    @staticmethod
    def _validate_param(param: object) -> None:
        if not isinstance(param, list):
            raise ValueError(
                f"invalid {WeekdayEvaluator.__name__} param: "
                f"expected list[int], got {type(param).__name__}"
            )
        if not param:
            raise ValueError(f"invalid {WeekdayEvaluator.__name__} param: list must not be empty")
        for i, val in enumerate(param):
            if not isinstance(val, int) or isinstance(val, bool):
                raise ValueError(
                    f"invalid {WeekdayEvaluator.__name__} param: "
                    f"element {i} is {type(val).__name__}, expected int"
                )
            if val < 0 or val > 6:
                raise ValueError(
                    f"invalid {WeekdayEvaluator.__name__} param: "
                    f"element {i} has value {val}, expected 0-6"
                )
