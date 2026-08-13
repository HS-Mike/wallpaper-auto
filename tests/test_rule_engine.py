"""Tests for rule_engine.py — covers evaluate_node, RuleEngine, and register_evaluator."""

from unittest.mock import patch

import pytest

from wallpaper_auto.models import ConditionNode, Rule
from wallpaper_auto.rule_engine import BaseEvaluator, RuleEngine, evaluate_node


@pytest.fixture
def preserve_evaluators():
    original = RuleEngine._evaluators.copy()
    yield
    RuleEngine._evaluators.clear()
    RuleEngine._evaluators.update(original)


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


class TestConditionNodeValidation:
    """Tests for edge cases in ConditionNode model validation."""

    def test_non_dict_input_passes_through(self):
        with pytest.raises((TypeError, ValueError)):
            ConditionNode.model_validate(["not", "a", "dict"])

    def test_empty_node_raises(self):
        with pytest.raises(ValueError, match="empty node"):
            ConditionNode.model_validate({})

    def test_null_and_raises(self):
        with pytest.raises(ValueError, match="'and' must not be null"):
            ConditionNode.model_validate({"and": None})

    def test_null_or_raises(self):
        with pytest.raises(ValueError, match="'or' must not be null"):
            ConditionNode.model_validate({"or": None})

    def test_empty_and_raises(self):
        with pytest.raises(ValueError, match="'and' must have at least one element"):
            ConditionNode.model_validate({"and": []})

    def test_empty_or_raises(self):
        with pytest.raises(ValueError, match="'or' must have at least one element"):
            ConditionNode.model_validate({"or": []})

    def test_evaluator_property_on_and_node_raises(self):
        node = ConditionNode.model_validate({"and": [{"dummy": {}}]})
        with pytest.raises(ValueError, match="and/or node invalid access"):
            _ = node.evaluator

    def test_evaluator_param_property_on_or_node_raises(self):
        node = ConditionNode.model_validate({"or": [{"dummy": {}}]})
        with pytest.raises(ValueError, match="and/or node invalid access"):
            _ = node.evaluator_param


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

    def test_and_all_true(self):
        evaluator = _MockEval(return_value=True)
        node = make_and(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": evaluator, "b": evaluator}) is True

    def test_and_one_false(self):
        true_ev = _MockEval(return_value=True)
        false_ev = _MockEval(return_value=False)
        node = make_and(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": true_ev, "b": false_ev}) is False

    def test_or_all_false(self):
        evaluator = _MockEval(return_value=False)
        node = make_or(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": evaluator, "b": evaluator}) is False

    def test_or_one_true(self):
        true_ev = _MockEval(return_value=True)
        false_ev = _MockEval(return_value=False)
        node = make_or(make_leaf("a"), make_leaf("b"))
        assert evaluate_node(node, {"a": true_ev, "b": false_ev}) is True

    # ── nested ──

    @pytest.mark.parametrize(
        ("a_val", "b_val", "expected"),
        [
            (True, False, True),
            (False, False, False),
        ],
    )
    def test_nested_and_or(self, a_val, b_val, expected):
        true_ev = _MockEval(return_value=True)
        a_ev = _MockEval(return_value=a_val)
        b_ev = _MockEval(return_value=b_val)
        node = make_and(make_or(make_leaf("a"), make_leaf("b")), make_leaf("c"))
        result = evaluate_node(node, {"a": a_ev, "b": b_ev, "c": true_ev})
        assert result is expected


class TestRuleEngine:
    """Tests for RuleEngine using patched _evaluators."""

    @pytest.mark.parametrize(
        "rules", [None, [Rule(name="r", condition=make_leaf("dummy"), target="t")]]
    )
    def test_init(self, rules):
        engine = RuleEngine()
        if rules is None:
            assert engine._rules == []
        else:
            engine.init(rules)
            assert engine._rules == rules

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

    @pytest.mark.parametrize(
        ("first_matches", "expected_idx"),
        [
            (True, 0),
            (False, 1),
        ],
    )
    def test_evaluate_rule_ordering(self, first_matches, expected_idx):
        engine = RuleEngine()
        r1 = Rule(name="r1", condition=make_leaf("a"), target="t1")
        r2 = Rule(name="r2", condition=make_leaf("b"), target="t2")
        engine.init([r1, r2])
        ev_a = _MockEval(return_value=first_matches)
        ev_b = _MockEval(return_value=not first_matches)
        with patch.object(RuleEngine, "_evaluators", {"a": ev_a, "b": ev_b}):
            result = engine.evaluate()
            assert result is [r1, r2][expected_idx]

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
            RuleEngine,
            "_evaluators",
            {"x": true_ev, "y": true_ev, "z": true_ev},
        ):
            assert engine.evaluate() is rule

    @pytest.mark.parametrize(
        ("name", "key_check"),
        [
            ("custom_check", "custom_check"),
            ("wifi_ssid_is", "wifi_ssid_is"),
        ],
    )
    def test_register_evaluator(self, preserve_evaluators, name, key_check):
        ev = _MockEval(return_value=True)
        RuleEngine.register_evaluator(name, ev)
        assert RuleEngine._evaluators[key_check] is ev

    def test_register_evaluator_rejects_non_base(self):
        with pytest.raises(ValueError, match="must be an instance of BaseEvaluator"):
            RuleEngine.register_evaluator("bad", _NotAnEvaluator())  # type: ignore[arg-type]

    def test_register_evaluator_used_during_evaluate(self, preserve_evaluators):
        ev = _MockEval(return_value=True)
        RuleEngine.register_evaluator("my_evaluator", ev)

        engine = RuleEngine()
        rule = Rule(name="r", condition=make_leaf("my_evaluator", foo=1), target="t")
        engine.init([rule])
        assert engine.evaluate() is rule
        assert ev.last_param == {"foo": 1}
