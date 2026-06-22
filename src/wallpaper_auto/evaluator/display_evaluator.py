"""
Display condition evaluator.

Checks whether a specific display model is currently connected.

Uses :func:`get_display_set` from ``util.display_utils`` for its data source.
"""

from ..util.display_utils import get_display_set

from .base_evaluator import BaseEvaluator


class DisplayModelEvaluator(BaseEvaluator):
    """Check if a connected display matches a given model name.

    YAML usage: ``display_model_is: "U2719D"``
    """

    def __call__(self, param: str) -> bool:
        if not isinstance(param, str):
            raise ValueError(f"invalid {self.__class__.__name__} param")
        displays = get_display_set()
        return any(model == param for _, model, _, _ in displays)
