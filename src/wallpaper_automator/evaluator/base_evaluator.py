"""
Base evaluator interface.

All condition evaluators must implement this interface and be callable
with a parameter, returning a boolean result.
"""

from typing import Any


class BaseEvaluator:
    def __call__(self, param: Any) -> bool: ...
