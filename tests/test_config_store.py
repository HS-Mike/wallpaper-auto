"""Tests for config_store.py — focuses on config file parsing and validation."""

import pytest
import yaml

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.models import CacheConfig, ConfigModel, ResourceConfig, Rule


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
        "fallback_target": "office_view",
    }
    data.update(overrides)
    return yaml.dump(data)


_MINIMAL = {
    "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
    "trigger": [{"name": "windows_session"}],
    "rule": [],
    "fallback_target": "a",
}


def _make_minimal_yaml(**overrides) -> str:
    """Return a minimal valid YAML config string, with optional key overrides.

    Tests that vary a single field can pass the modified key as an override
    instead of repeating the full boilerplate dict.
    Pass ``key=None`` to omit a key entirely from the generated YAML.
    """
    data = dict(_MINIMAL)
    for k, v in overrides.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    return yaml.dump(data)


@pytest.fixture
def store() -> ConfigStore:
    return ConfigStore()


@pytest.fixture
def valid_yaml(tmp_path) -> str:
    """Write a valid config file and return its path."""
    path = tmp_path / "config.yaml"
    path.write_text(_make_valid_yaml(), encoding="utf-8")
    return str(path)


class TestLoad:
    """Successful config file parsing."""

    def test_load_valid_file(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert isinstance(store.config, ConfigModel)

    def test_load_sets_config_model(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.config is not None
        assert store.config.fallback_target == "office_view"
        assert "office_view" in store.config.resource
        assert "black" in store.config.resource
        assert len(store.config.trigger) == 2
        assert len(store.config.rule) == 1

    def test_load_resource_string_shorthand(self, store: ConfigStore, valid_yaml: str):
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

    def test_resource_config_invalid_type_raises(self):
        with pytest.raises(TypeError, match="ResourceConfig data must be a dict or string"):
            ResourceConfig.model_validate(42)

    def test_load_complex_nested_conditions(self, store: ConfigStore, tmp_path):
        yaml_str = yaml.dump(
            {
                "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
                "trigger": [{"name": "windows_session"}],
                "rule": [
                    {
                        "name": "complex",
                        "condition": {
                            "or": [
                                {"network": "WiFi"},
                                {
                                    "and": [
                                        {"location": {"lat": 31.23, "lon": 121.47, "radius": 0.5}},
                                        {"workday_only": True},
                                    ]
                                },
                            ],
                        },
                        "target": "a",
                    }
                ],
                "fallback_target": "a",
            }
        )
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
        path = tmp_path / "no_fallback.yaml"
        path.write_text(_make_minimal_yaml(fallback_target=None), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_missing_resource(self, store: ConfigStore, tmp_path):
        path = tmp_path / "no_resource.yaml"
        path.write_text(_make_minimal_yaml(resource=None), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_missing_trigger(self, store: ConfigStore, tmp_path):
        path = tmp_path / "no_trigger.yaml"
        path.write_text(_make_minimal_yaml(trigger=None), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_missing_rule(self, store: ConfigStore, tmp_path):
        path = tmp_path / "no_rule.yaml"
        path.write_text(_make_minimal_yaml(rule=None), encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(str(path))

    def test_fallback_target_not_found(self, store: ConfigStore, tmp_path):
        path = tmp_path / "bad_fallback.yaml"
        path.write_text(
            _make_minimal_yaml(fallback_target="nonexistent_resource"), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="Fallback target.*not found"):
            store.load(str(path))

    def test_rule_target_not_found(self, store: ConfigStore, tmp_path):
        path = tmp_path / "bad_rule_target.yaml"
        path.write_text(
            _make_minimal_yaml(
                rule=[{"name": "bad", "condition": {"network": "WiFi"}, "target": "unknown"}]
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="targets unknown resource"):
            store.load(str(path))


class TestLoadValidation:
    """ConfigModel cross-field validation rules."""

    def test_condition_invalid_single_key(self, store: ConfigStore, tmp_path):
        path = tmp_path / "bad_condition.yaml"
        path.write_text(
            _make_minimal_yaml(
                rule=[
                    {
                        "name": "r",
                        "condition": {"network": "WiFi", "time_range": ["00:00", "12:00"]},
                        "target": "a",
                    }
                ]
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="only one key"):
            store.load(str(path))

    def test_empty_condition_node(self, store: ConfigStore, tmp_path):
        path = tmp_path / "empty_cond.yaml"
        path.write_text(
            _make_minimal_yaml(
                rule=[
                    {
                        "name": "r",
                        "condition": {},
                        "target": "a",
                    }
                ]
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="empty node"):
            store.load(str(path))

    def test_trigger_with_config(self, store: ConfigStore, tmp_path):
        yaml_str = yaml.dump(
            {
                "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
                "trigger": [{"name": "time_range", "config": {"start": "09:00", "end": "17:00"}}],
                "rule": [
                    {"name": "r", "condition": {"time_range": ["09:00", "17:00"]}, "target": "a"}
                ],
                "fallback_target": "a",
            }
        )
        path = tmp_path / "trigger_config.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        t = store.config.trigger[0]
        assert t.name == "time_range"
        assert t.config == {"start": "09:00", "end": "17:00"}

    def test_multiple_rules_evaluated_in_order(self, store: ConfigStore, tmp_path):
        yaml_str = yaml.dump(
            {
                "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
                "trigger": [{"name": "windows_session"}],
                "rule": [
                    {"name": "first", "condition": {"network": "W"}, "target": "a"},
                    {"name": "second", "condition": {"network": "X"}, "target": "a"},
                    {"name": "third", "condition": {"network": "Y"}, "target": "a"},
                ],
                "fallback_target": "a",
            }
        )
        path = tmp_path / "multi_rule.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        assert [r.name for r in store.config.rule] == ["first", "second", "third"]


class TestProperties:
    """Accessor properties after config load."""

    def test_fallback_target(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.fallback_target == "office_view"

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

    def test_cache_returns_cache_config(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert isinstance(store.cache, CacheConfig)
        assert store.cache.path is None
        assert store.cache.resize.enabled is True

    def test_cache_reflects_configured_path(self, store: ConfigStore, tmp_path):
        cache_dir = tmp_path / "custom_cache"
        yaml_str = _make_valid_yaml(cache={"path": str(cache_dir)})
        path = tmp_path / "with_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.cache.path == str(cache_dir)

    def test_properties_before_load_raises(self, store: ConfigStore):
        with pytest.raises(AssertionError):
            _ = store.fallback_target
        with pytest.raises(AssertionError):
            _ = store.resource
        with pytest.raises(AssertionError):
            _ = store.rule
        with pytest.raises(AssertionError):
            _ = store.trigger
        with pytest.raises(AssertionError):
            _ = store.at_shutdown_target
        with pytest.raises(AssertionError):
            _ = store.cache

    def test_at_shutdown_target_none_by_default(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.at_shutdown_target is None

    def test_at_shutdown_target_from_yaml(self, store: ConfigStore, tmp_path):
        yaml_str = _make_valid_yaml(at_shutdown="office_view")
        path = tmp_path / "with_atsd.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.at_shutdown_target == "office_view"

    def test_cache_path_default_when_cache_is_none(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.cache_path.name == "cache"

    def test_cache_path_with_valid_directory(self, store: ConfigStore, tmp_path):
        cache_dir = tmp_path / "my_cache"
        cache_dir.mkdir()
        yaml_str = _make_valid_yaml(cache={"path": str(cache_dir)})
        path = tmp_path / "with_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.cache_path == cache_dir

    def test_cache_path_allows_missing_directory(self, store: ConfigStore, tmp_path):
        missing = tmp_path / "does_not_exist"
        yaml_str = _make_valid_yaml(cache={"path": str(missing)})
        path = tmp_path / "missing_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.cache_path == missing

    def test_cache_path_raises_when_not_a_directory(self, store: ConfigStore, tmp_path):
        cache_file = tmp_path / "not_a_dir"
        cache_file.write_text("", encoding="utf-8")
        yaml_str = _make_valid_yaml(cache={"path": str(cache_file)})
        path = tmp_path / "file_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        with pytest.raises(NotADirectoryError, match="is not a directory"):
            _ = store.cache_path

    def test_cache_defaults_without_cache_block(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.config is not None
        assert store.config.cache.resize.enabled is True
        assert store.config.cache.resize.max_size_mb * 1024 * 1024 == 200 * 1024 * 1024
        assert store.config.cache.resize.evict_ratio == 0.9

    def test_cache_max_size_mb_with_value(self, store: ConfigStore, tmp_path):
        cache_dir = tmp_path / "size_cache"
        cache_dir.mkdir()
        yaml_str = _make_valid_yaml(
            cache={"path": str(cache_dir), "resize": {"max_size_mb": 42}},
        )
        path = tmp_path / "size_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        assert store.config.cache.resize.max_size_mb * 1024 * 1024 == 42 * 1024 * 1024
        assert store.config.cache.resize.evict_ratio == 0.9

    def test_cache_evict_ratio_with_value(self, store: ConfigStore, tmp_path):
        cache_dir = tmp_path / "ratio_cache"
        cache_dir.mkdir()
        yaml_str = _make_valid_yaml(
            cache={"path": str(cache_dir), "resize": {"evict_ratio": 0.5}},
        )
        path = tmp_path / "ratio_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        assert store.config.cache.resize.evict_ratio == 0.5
        assert store.config.cache.resize.max_size_mb * 1024 * 1024 == 200 * 1024 * 1024

    def test_cache_resize_enabled_false_when_disabled(self, store: ConfigStore, tmp_path):
        cache_dir = tmp_path / "disable_cache"
        cache_dir.mkdir()
        yaml_str = _make_valid_yaml(
            cache={"path": str(cache_dir), "resize": {"enabled": False}},
        )
        path = tmp_path / "disabled_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        assert store.config.cache.resize.enabled is False

    def test_cache_resize_enabled_true_when_explicit(self, store: ConfigStore, tmp_path):
        cache_dir = tmp_path / "enable_cache"
        cache_dir.mkdir()
        yaml_str = _make_valid_yaml(
            cache={"path": str(cache_dir), "resize": {"enabled": True}},
        )
        path = tmp_path / "enabled_cache.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.config is not None
        assert store.config.cache.resize.enabled is True

    def test_scene_returns_dict_from_yaml(self, store: ConfigStore, tmp_path):
        yaml_str = yaml.dump(
            {
                "resource": {"a": {"name": "static_wallpaper", "config": {"path": "x"}}},
                "trigger": [{"name": "windows_session"}],
                "rule": [],
                "fallback_target": "a",
                "scene": {
                    "office": [{"display_model": "Dell U27", "resource": "a"}],
                },
            }
        )
        path = tmp_path / "with_scene.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        scenes = store.scene
        assert "office" in scenes
        assert scenes["office"][0].display_model == "Dell U27"
        assert scenes["office"][0].resource == "a"

    def test_scene_binding_parses_match_display_model(self, store: ConfigStore, tmp_path):
        yaml_str = _make_valid_yaml(
            scene={"office": [{"match_display_model": "27.*", "resource": "office_view"}]}
        )
        path = tmp_path / "scene_regex.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        binding = store.scene["office"][0]
        assert binding.match_display_model == "27.*"
        assert binding.display_model is None

    def test_scene_binding_parses_resolution_string(self, store: ConfigStore, tmp_path):
        yaml_str = _make_valid_yaml(
            scene={
                "office": [
                    {
                        "display_model": "U2719D",
                        "resource": "office_view",
                        "resolution": "1920x1080",
                    }
                ]
            }
        )
        path = tmp_path / "scene_resolution.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.scene["office"][0].resolution == (1920, 1080)

    def test_scene_binding_parses_resolution_list(self, store: ConfigStore, tmp_path):
        yaml_str = _make_valid_yaml(
            scene={
                "office": [
                    {
                        "display_model": "U2719D",
                        "resource": "office_view",
                        "resolution": [2560, 1440],
                    }
                ]
            }
        )
        path = tmp_path / "scene_resolution_list.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.scene["office"][0].resolution == (2560, 1440)

    def test_scene_binding_normalizes_decimal_scale(self, store: ConfigStore, tmp_path):
        yaml_str = _make_valid_yaml(
            scene={"office": [{"display_model": "U2719D", "resource": "office_view", "scale": 1.5}]}
        )
        path = tmp_path / "scene_scale_decimal.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.scene["office"][0].scale == 150

    def test_scene_binding_passes_through_integer_scale(self, store: ConfigStore, tmp_path):
        yaml_str = _make_valid_yaml(
            scene={"office": [{"display_model": "U2719D", "resource": "office_view", "scale": 150}]}
        )
        path = tmp_path / "scene_scale_int.yaml"
        path.write_text(yaml_str, encoding="utf-8")
        store.load(str(path))
        assert store.scene["office"][0].scale == 150

    def test_scene_returns_empty_dict_when_unset(self, store: ConfigStore, valid_yaml: str):
        store.load(valid_yaml)
        assert store.scene == {}

    def test_scene_raises_before_load(self, store: ConfigStore):
        with pytest.raises(AssertionError):
            _ = store.scene


class TestAtShutdownValidation:
    """Validation of at_shutdown target in ConfigModel."""

    def test_at_shutdown_target_not_found_raises(self):
        data = dict(_MINIMAL)
        data["at_shutdown"] = "nonexistent"
        with pytest.raises(ValueError, match="at_shutdown target.*not found"):
            ConfigModel(**data)

    def test_at_shutdown_target_exists_passes(self):
        data = dict(_MINIMAL)
        data["at_shutdown"] = "a"
        model = ConfigModel(**data)
        assert model.at_shutdown == "a"


class TestSceneValidation:
    """Validation of scene binding fields in ConfigModel."""

    def test_binding_requires_display_key(self):
        data = dict(_MINIMAL)
        data["scene"] = {"office": [{"resource": "a"}]}
        with pytest.raises(ValueError, match="Either display_model or match_display_model"):
            ConfigModel(**data)

    def test_binding_rejects_both_display_keys(self):
        data = dict(_MINIMAL)
        data["scene"] = {
            "office": [
                {"display_model": "Dell U27", "match_display_model": "Dell.*", "resource": "a"}
            ]
        }
        with pytest.raises(ValueError, match="Only one of display_model or match_display_model"):
            ConfigModel(**data)

    def test_scene_rejects_duplicate_display_model(self):
        data = dict(_MINIMAL)
        data["scene"] = {
            "office": [
                {"display_model": "Dell U27", "resource": "a"},
                {"display_model": "Dell U27", "resource": "a"},
            ]
        }
        with pytest.raises(ValueError, match="duplicate display_model"):
            ConfigModel(**data)

    def test_scene_rejects_duplicate_match_display_model(self):
        data = dict(_MINIMAL)
        data["scene"] = {
            "office": [
                {"match_display_model": "Dell.*", "resource": "a"},
                {"match_display_model": "Dell.*", "resource": "a"},
            ]
        }
        with pytest.raises(ValueError, match="duplicate match_display_model"):
            ConfigModel(**data)

    @pytest.mark.parametrize("scene", [None, {}])
    def test_scene_empty_passes(self, scene):
        """An empty scene (None or {}) is accepted and preserved as-is."""
        data = dict(_MINIMAL)
        data["scene"] = scene
        model = ConfigModel(**data)
        assert model.scene == scene
