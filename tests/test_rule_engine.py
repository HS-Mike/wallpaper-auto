"""Tests for rule_engine.py — covers evaluate_node, RuleEngine, and register_evaluator."""

import pytest
from unittest.mock import patch

from wallpaper_automator.models import ConditionNode, Rule
from wallpaper_automator.rule_engine import RuleEngine, evaluate_node, BaseEvaluator


@pytest.fixture
def preserve_evaluators():
    original = RuleEngine._evaluators.copy()
    yield
    RuleEngine._evaluators.clear()
    RuleEngine._evaluators.update(original)


# ── helpers ──────────────────────────────────────────────────────────────

class _MockEval(BaseEvaluator):
    """A simple fake evaluator that returns a configured bool."""
    def __init__(self, return_value: bool = True):
        self.return_value = return_value
        self.last_param: dict | None = None

    def __call__(self, param: dict) -> bool:
        self.last_param = param
        return self.return_value


class _NotAnEvaluator:
    """A callable that does NOT inherit from BaseEvaluator."""
    def __call__(self, param: dict) -> bool:
        return True


def make_leaf(evaluator_name: str, **params) -> ConditionNode:
    data = {evaluator_name: params or {}}
    return ConditionNode.model_validate(data)


def make_and(*children: ConditionNode) -> ConditionNode:
    return ConditionNode.model_validate({"and": list(children)})


def make_or(*children: ConditionNode) -> ConditionNode:
    return ConditionNode.model_validate({"or": list(children)})


# ── ConditionNode model validation ──────────────────────────────────────

class TestConditionNodeValidation:
    """Tests for edge cases in ConditionNode model validation."""

    def test_non_dict_input_passes_through(self):
        """Non-dict input hits the early return in validate_single_key (line 35)."""
        with pytest.raises((TypeError, ValueError)):
            ConditionNode.model_validate(["not", "a", "dict"])

    def test_empty_node_raises(self):
        """ConditionNode with no conditions and no extra fields raises ValueError (line 44).

        {"and": None} passes the before-validator (1 key), but Pydantic sets
        and_conditions=None, leaving no and/or conditions and no extra fields.
        """
        with pytest.raises(ValueError, match="empty node"):
            ConditionNode.model_validate({"and": None})

    def test_evaluator_property_on_and_node_raises(self):
        """Line 64: accessing .evaluator on an AND node raises ValueError."""
        node = ConditionNode.model_validate({"and": [{"dummy": {}}]})
        with pytest.raises(ValueError, match="and/or node invalid access"):
            _ = node.evaluator

    def test_evaluator_param_property_on_or_node_raises(self):
        """Line 70: accessing .evaluator_param on an OR node raises ValueError."""
        node = ConditionNode.model_validate({"or": [{"dummy": {}}]})
        with pytest.raises(ValueError, match="and/or node invalid access"):
            _ = node.evaluator_param


# ── evaluate_node ────────────────────────────────────────────────────────

class TestEvaluateNode:
    """Direct tests for the module-level evaluate_node function."""

    def test_leaf_true(self):
        evaluator = _MockEval(return_value=True)
        node = make_leaf("dummy", foo=1)
        assert evaluate_node(node, {"dummy": evaluator}) is True

    def test_leaf_false(self):
        evaluator = _MockEval(return_value=False)
        node = make_leaf("dummy")
        assert evaluate_node(node, {"dummy": evaluator}) is False

    def test_leaf_passes_params(self):
        evaluator = _MockEval(return_value=True)
        node = make_leaf("dummy", a=10, b="x")
        evaluate_node(node, {"dummy": evaluator})
        assert evaluator.last_param == {"a": 10, "b": "x"}

    def test_leaf_unknown_evaluator_raises(self):
        node = make_leaf("unknown_evaluator")
        with pytest.raises(ValueError, match="evaluator not found"):
            evaluate_node(node, {})

    # ── AND ──

    def test_and_all_true(self):
        evaluator = _MockEval(return_value=True)
        node = make_and(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": evaluator, "b": evaluator}) is True

    def test_and_one_false(self):
        true_ev = _MockEval(return_value=True)
        false_ev = _MockEval(return_value=False)
        node = make_and(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": true_ev, "b": false_ev}) is False

    def test_and_empty(self):
        node = make_and()
        assert evaluate_node(node, {}) is True

    # ── OR ──

    def test_or_all_false(self):
        evaluator = _MockEval(return_value=False)
        node = make_or(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": evaluator, "b": evaluator}) is False

    def test_or_one_true(self):
        true_ev = _MockEval(return_value=True)
        false_ev = _MockEval(return_value=False)
        node = make_or(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": true_ev, "b": false_ev}) is True

    def test_or_empty(self):
        node = make_or()
        assert evaluate_node(node, {}) is False

    # ── nested ──

    def test_nested_and_or_true(self):
        true_ev = _MockEval(return_value=True)
        false_ev = _MockEval(return_value=False)
        node = make_and(make_or(make_leaf("a"), make_leaf("b")), make_leaf("c"))
        assert evaluate_node(node, {"a": true_ev, "b": false_ev, "c": true_ev}) is True

    def test_nested_and_or_false(self):
        true_ev = _MockEval(return_value=True)
        false_ev = _MockEval(return_value=False)
        node = make_and(make_or(make_leaf("a"), make_leaf("b")), make_leaf("c"))
        assert evaluate_node(node, {"a": false_ev, "b": false_ev, "c": true_ev}) is False


# ── RuleEngine ───────────────────────────────────────────────────────────

class TestRuleEngine:
    """Tests for RuleEngine using patched _evaluators."""

    def test_init_empty_rules(self):
        engine = RuleEngine()
        assert engine._rules == []

    def test_init_sets_rules(self):
        engine = RuleEngine()
        rule = Rule(name="r", condition=make_leaf("dummy"), target="t")
        engine.init([rule])
        assert engine._rules == [rule]

    def test_evaluate_no_rules(self):
        engine = RuleEngine()
        assert engine.evaluate() is None

    def test_evaluate_no_match(self):
        engine = RuleEngine()
        rule = Rule(name="r", condition=make_leaf("dummy"), target="t")
        engine.init([rule])
        mock_evaluator = _MockEval(return_value=False)
        with patch.object(RuleEngine, "_evaluators", {"dummy": mock_evaluator}):
            assert engine.evaluate() is None

    def test_evaluate_first_rule_matches(self):
        engine = RuleEngine()
        r1 = Rule(name="r1", condition=make_leaf("a"), target="t1")
        r2 = Rule(name="r2", condition=make_leaf("b"), target="t2")
        engine.init([r1, r2])
        match_ev = _MockEval(return_value=True)
        no_match_ev = _MockEval(return_value=False)
        with patch.object(
            RuleEngine, "_evaluators",
            {"a": match_ev, "b": no_match_ev},
        ):
            assert engine.evaluate() is r1

    def test_evaluate_second_rule_matches(self):
        engine = RuleEngine()
        r1 = Rule(name="r1", condition=make_leaf("a"), target="t1")
        r2 = Rule(name="r2", condition=make_leaf("b"), target="t2")
        engine.init([r1, r2])
        no_match_ev = _MockEval(return_value=False)
        match_ev = _MockEval(return_value=True)
        with patch.object(
            RuleEngine, "_evaluators",
            {"a": no_match_ev, "b": match_ev},
        ):
            assert engine.evaluate() is r2

    def test_evaluate_passes_through_condition_tree(self):
        engine = RuleEngine()
        condition = make_and(
            make_or(make_leaf("x"), make_leaf("y")),
            make_leaf("z"),
        )
        rule = Rule(name="r", condition=condition, target="t")
        engine.init([rule])
        true_ev = _MockEval(return_value=True)
        with patch.object(
            RuleEngine, "_evaluators",
            {"x": true_ev, "y": true_ev, "z": true_ev},
        ):
            assert engine.evaluate() is rule

    def test_evaluate_returns_none_when_all_false(self):
        engine = RuleEngine()
        condition = make_or(
            make_and(make_leaf("a"), make_leaf("b")),
            make_leaf("c"),
        )
        rule = Rule(name="r", condition=condition, target="t")
        engine.init([rule])
        false_ev = _MockEval(return_value=False)
        with patch.object(
            RuleEngine, "_evaluators",
            {"a": false_ev, "b": false_ev, "c": false_ev},
        ):
            assert engine.evaluate() is None

    # ── register_evaluator ──

    def test_register_evaluator_new_name(self, preserve_evaluators):
        """Register a brand-new evaluator name."""
        ev = _MockEval(return_value=True)
        RuleEngine.register_evaluator("custom_check", ev)
        assert "custom_check" in RuleEngine._evaluators
        assert RuleEngine._evaluators["custom_check"] is ev

    def test_register_evaluator_overwrite(self, preserve_evaluators):
        """Overwrite an existing evaluator name."""
        ev = _MockEval(return_value=True)
        RuleEngine.register_evaluator("wifi_ssid_is", ev)
        assert RuleEngine._evaluators["wifi_ssid_is"] is ev

    def test_register_evaluator_rejects_non_base(self):
        """Only BaseEvaluator instances are accepted."""
        with pytest.raises(ValueError, match="must be an instance of BaseEvaluator"):
            RuleEngine.register_evaluator("bad", _NotAnEvaluator())

    def test_register_evaluator_used_during_evaluate(self, preserve_evaluators):
        """A registered evaluator is resolved when evaluating a rule."""
        ev = _MockEval(return_value=True)
        RuleEngine.register_evaluator("my_evaluator", ev)

        engine = RuleEngine()
        rule = Rule(name="r", condition=make_leaf("my_evaluator", foo=1), target="t")
        engine.init([rule])
        assert engine.evaluate() is rule
        assert ev.last_param == {"foo": 1}

    def test_register_evaluator_class_shared_across_instances(self, preserve_evaluators):
        """register_evaluator on the class affects all RuleEngine instances."""
        ev = _MockEval(return_value=True)
        RuleEngine.register_evaluator("shared_eval", ev)

        e1 = RuleEngine()
        e2 = RuleEngine()

        r1 = Rule(name="r1", condition=make_leaf("shared_eval"), target="t")
        r2 = Rule(name="r2", condition=make_leaf("shared_eval"), target="t")
        e1.init([r1])
        e2.init([r2])

        assert e1.evaluate() is r1
        assert e2.evaluate() is r2
