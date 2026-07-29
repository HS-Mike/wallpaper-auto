"""
Display condition evaluator.

Checks whether a connected display model name matches a given pattern.

Uses :func:`get_display_info` from ``util.display_utils`` for its data source.
"""

import re

from ..util.display_utils import get_display_info
from .base_evaluator import BaseEvaluator


class HaveDisplayEvaluator(BaseEvaluator):
    """Check if a connected display matches a given model name or regex pattern.

    YAML usage: ``have_display: "U2719D"`` or ``have_display: "27.*"``
    """

    def __call__(self, param: str) -> bool:
        if not isinstance(param, str):
            raise ValueError(f"invalid {self.__class__.__name__} param")
        displays = get_display_info()
        return any(re.search(param, d.model or "") for d in displays)
