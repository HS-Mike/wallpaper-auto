"""
Rule evaluation engine.

Recursively evaluates AND/OR condition trees against built-in evaluators
and returns the first matching rule.
"""
import logging

from .models import ConditionNode, Rule
from .evaluator.base_evaluator import BaseEvaluator
from .evaluator.wifi_ssid_evaluator import WIFISsidEvaluator
from .evaluator.time_range_evaluator import TimeRangeEvaluator
from .evaluator.geo_evaluator import GeoEvaluator
from .evaluator.weekday_evaluator import WeekdayEvaluator


logger = logging.getLogger(__name__)

_BUILTIN_EVALUATORS: dict[str, BaseEvaluator] = {
    "wifi_ssid_is": WIFISsidEvaluator(),
    "in_time_range": TimeRangeEvaluator(),
    "in_geo_range": GeoEvaluator(),
    "day_of_week_is": WeekdayEvaluator(),
}


class RuleEngine:
    """Main engine for evaluating rules against condition trees."""

    _evaluators: dict[str, BaseEvaluator] = _BUILTIN_EVALUATORS.copy()

    def __init__(self):
        self._rules: list[Rule] = []

    @classmethod
    def register_evaluator(cls, name: str, evaluator: BaseEvaluator) -> None:
        """Register a custom condition evaluator.

        Register before starting WallpaperAutomator so the rule engine
        can resolve conditions referencing this evaluator by name.
        """
        if not isinstance(evaluator, BaseEvaluator):
            raise ValueError("evaluator must be an instance of BaseEvaluator")
        cls._evaluators[name] = evaluator

    def init(self, rules: list[Rule]):
        """Load a list of rules to be evaluated."""
        self._rules = rules

    def evaluate(self) -> Rule | None:
        """
        Evaluate all rules in order and return the first matching rule.

        Iterates through each rule and evaluates its condition tree.
        Returns the first rule whose conditions all evaluate to True,
        or None if no rule matches.
        """
        for rule in self._rules:
            if evaluate_node(rule.condition, self._evaluators):
                return rule
        return None

def evaluate_node(node: ConditionNode, evaluators: dict[str, BaseEvaluator]) -> bool:
    """
    Recursively evaluate a condition node tree.

    Handles three node types:
      - AND nodes: all child conditions must be True
      - OR nodes: at least one child condition must be True
      - Leaf nodes: evaluated by the appropriate evaluator
    """
    if node.is_and:
        assert node.and_conditions is not None
        return all(evaluate_node(i, evaluators) for i in node.and_conditions)
    elif node.is_or:
        assert node.or_conditions is not None
        return any(evaluate_node(i, evaluators) for i in node.or_conditions)
    else:
        evaluator = evaluators.get(node.evaluator)
        if evaluator is None:
            raise ValueError(f"evaluator not found: {node.evaluator}")
        return evaluator(node.evaluator_param)
