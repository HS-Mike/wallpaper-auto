"""
Display condition evaluator.

Checks whether a connected display model name matches a given pattern.

Uses :func:`get_display_info` from ``util.display_util`` for its data source.
"""

import re

from ..util.display_util import get_display_info
from .base_evaluator import BaseEvaluator


class HaveDisplayEvaluator(BaseEvaluator):
    """Check if a connected display matches a given model name or regex pattern."""

    def __call__(self, param: str) -> bool:
        """Check whether any connected display matches the given model pattern.

        Args:
            param: Model name or regex pattern (e.g. ``"U2719D"`` or ``"27.*"``).

        Returns:
            True if any connected display's model matches *param*.
        """
        if not isinstance(param, str):
            raise ValueError(f"invalid {self.__class__.__name__} param")
        displays = get_display_info()
        if displays is None:
            return False
        return any(re.search(param, d.model or "") for d in displays)
