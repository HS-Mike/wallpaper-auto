"""Tests for config_store.py — focuses on config file parsing and validation."""

import pytest
import yaml

from wallpaper_automator.config_store import ConfigStore
from wallpaper_automator.models import ConfigModel, ResourceConfig, Rule


# ── helpers ──────────────────────────────────────────────────────────────

def _make_valid_yaml(**overrides) -> str:
    """Return a valid YAML config string, with optional key overrides."""
    data = {
        "resource": {
            "office_view": {
                "name": "static_wallpaper",
                "config": {"path": "C:/img.png", "style": "fill"},
            },
            "black": "C:/black.jpg",
        },
        "trigger": [
            {"name": "windows_session"},
            {"name": "network"},
        ],
        "rule": [
            {
                "name": "office_mode",
                "condition": {"or": [{"network": "Company_WiFi"}]},
                "target": "office_view",
            },
        ],
        "fallback": "office_view",
    }
    data.update(overrides)
    return yaml.dump(data)


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def store() -> ConfigStore:
    return ConfigStore()


@pytest.fixture
def valid_yaml(tmp_path) -> str:
    """Write a valid config file and return its path."""
    path = tmp_path / "config.yaml"
    path.write_text(_make_valid_yaml(), encoding="utf-8")
    return str(path)


# ── load — happy path ────────────────────────────────────────────────────

class TestLoad:
    """Successful config file parsing."""

    def test_load_valid_file(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert isinstance(store.config, ConfigModel)

    def test_load_sets_config_model(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.config is not None
        assert store.config.fallback == "office_view"
        assert "office_view" in store.config.resource
        assert "black" in store.config.resource
        assert len(store.config.trigger) == 2
        assert len(store.config.rule) == 1

    def test_load_resource_string_shorthand(self, store: ConfigStore, valid_yaml: str):
        """String shorthand resources are expanded to ResourceConfig."""
        store.load(valid_yaml)
        assert store.config is not None
        black = store.config.resource["black"]
        assert isinstance(black, ResourceConfig)
        assert black.name == "static_wallpaper"
        assert black.config["path"] == "C:/black.jpg"

    def test_load_resource_full_dict(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.config is not None
        office = store.config.resource["office_view"]
        assert isinstance(office, ResourceConfig)
        assert office.name == "static_wallpaper"
        assert office.config["path"] == "C:/img.png"
        assert office.config["style"] == "fill"

    def test_load_trigger_list(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.config is not None
        names = [t.name for t in store.config.trigger]
        assert names == ["windows_session", "network"]

    def test_load_rule_with_condition_tree(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.config is not None
        rule = store.config.rule[0]
        assert isinstance(rule, Rule)
        assert rule.name == "office_mode"
        assert rule.target == "office_view"
        assert rule.condition.is_or
        assert rule.condition.or_conditions is not None
        assert rule.condition.or_conditions[0].evaluator == "network"

    def test_load_complex_nested_conditions(self, store: ConfigStore, tmp_path):
        """Load a config with nested and/or condition structure."""
        yaml_str = yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "rule": [{
                "name": "complex",
                "condition": {
                    "or": [
                        {"network": "WiFi"},
                        {"and": [
                            {"location": {"lat": 31.23, "lon": 121.47, "radius": 0.5}},
                            {"workday_only": True},
                        ]},
                    ],
                },
                "target": "a",
            }],
            "fallback": "a",
        })
        path = tmp_path / "complex.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        rule = store.config.rule[0]
        assert rule.condition.is_or
        assert rule.condition.or_conditions is not None
        assert len(rule.condition.or_conditions) == 2

        inner_and = rule.condition.or_conditions[1]
        assert inner_and.is_and
        assert inner_and.and_conditions is not None
        assert len(inner_and.and_conditions) == 2
        assert inner_and.and_conditions[0].evaluator == "location"
        assert inner_and.and_conditions[1].evaluator == "workday_only"


# ── load — edge cases & error handling ───────────────────────────────────

class TestLoadErrors:
    """Config file parsing failure scenarios."""

    def test_file_not_found(self, store: ConfigStore):
        with pytest.raises(FileNotFoundError):
            store.load("/nonexistent/path/config.yaml")

    def test_invalid_yaml_syntax(self, store: ConfigStore, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("{invalid: yaml: [}", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            store.load(str(path))

    def test_empty_file(self, store: ConfigStore, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        with pytest.raises(TypeError):
            store.load(str(path))

    def test_missing_fallback(self, store: ConfigStore, tmp_path):
        """load should fail when fallback key is missing."""
        path = tmp_path / "no_fallback.yaml"
        path.write_text(yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "rule": [],
        }), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_missing_resource(self, store: ConfigStore, tmp_path):
        path = tmp_path / "no_resource.yaml"
        path.write_text(yaml.dump({
            "trigger": [{"name": "windows_session"}],
            "rule": [],
            "fallback": "a",
        }), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_missing_trigger(self, store: ConfigStore, tmp_path):
        path = tmp_path / "no_trigger.yaml"
        path.write_text(yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "rule": [],
            "fallback": "a",
        }), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_missing_rule(self, store: ConfigStore, tmp_path):
        path = tmp_path / "no_rule.yaml"
        path.write_text(yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "fallback": "a",
        }), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_fallback_target_not_found(self, store: ConfigStore, tmp_path):
        path = tmp_path / "bad_fallback.yaml"
        path.write_text(yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "rule": [],
            "fallback": "nonexistent_resource",
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="Fallback target.*not found"):
            store.load(str(path))

    def test_rule_target_not_found(self, store: ConfigStore, tmp_path):
        path = tmp_path / "bad_rule_target.yaml"
        path.write_text(yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "rule": [{"name": "bad", "condition": {"network": "WiFi"}, "target": "unknown"}],
            "fallback": "a",
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="targets unknown resource"):
            store.load(str(path))


# ── load — validation of structural rules ────────────────────────────────

class TestLoadValidation:
    """ConfigModel cross-field validation rules."""

    def test_condition_invalid_single_key(self, store: ConfigStore, tmp_path):
        """A condition dict with more than one key should be rejected."""
        path = tmp_path / "bad_condition.yaml"
        path.write_text(yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "rule": [{
                "name": "r",
                "condition": {"network": "WiFi", "time_range": ["00:00", "12:00"]},
                "target": "a",
            }],
            "fallback": "a",
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="only one key"):
            store.load(str(path))

    def test_empty_condition_node(self, store: ConfigStore, tmp_path):
        """An empty condition dict should be rejected."""
        path = tmp_path / "empty_cond.yaml"
        path.write_text(yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "rule": [{
                "name": "r",
                "condition": {},
                "target": "a",
            }],
            "fallback": "a",
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="only one key"):
            store.load(str(path))

    def test_trigger_with_config(self, store: ConfigStore, tmp_path):
        """A trigger with extra config data should parse correctly."""
        yaml_str = yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "time_range", "config": {"start": "09:00", "end": "17:00"}}],
            "rule": [{"name": "r", "condition": {"time_range": ["09:00", "17:00"]}, "target": "a"}],
            "fallback": "a",
        })
        path = tmp_path / "trigger_config.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        t = store.config.trigger[0]
        assert t.name == "time_range"
        assert t.config == {"start": "09:00", "end": "17:00"}

    def test_multiple_rules_evaluated_in_order(self, store: ConfigStore, tmp_path):
        """Multiple rules should be loaded and maintain order."""
        yaml_str = yaml.dump({
            "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
            "trigger": [{"name": "windows_session"}],
            "rule": [
                {"name": "first", "condition": {"network": "W"}, "target": "a"},
                {"name": "second", "condition": {"network": "X"}, "target": "a"},
                {"name": "third", "condition": {"network": "Y"}, "target": "a"},
            ],
            "fallback": "a",
        })
        path = tmp_path / "multi_rule.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        assert [r.name for r in store.config.rule] == ["first", "second", "third"]


# ── properties ───────────────────────────────────────────────────────────

class TestProperties:
    """Accessor properties after config load."""

    def test_fallback_resource_id(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.fallback_resource_id == "office_view"

    def test_resource(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        resources = store.resource
        assert isinstance(resources, dict)
        assert "office_view" in resources
        assert "black" in resources

    def test_rule(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        rules = store.rule
        assert isinstance(rules, list)
        assert len(rules) == 1
        assert rules[0].name == "office_mode"

    def test_trigger(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        triggers = store.trigger
        assert isinstance(triggers, list)
        assert len(triggers) == 2

    def test_properties_before_load_raises(self, store: ConfigStore):
        """Accessing properties before load() should raise AssertionError."""
        with pytest.raises(AssertionError):
            _ = store.fallback_resource_id
        with pytest.raises(AssertionError):
            _ = store.resource
        with pytest.raises(AssertionError):
            _ = store.rule
        with pytest.raises(AssertionError):
            _ = store.trigger
