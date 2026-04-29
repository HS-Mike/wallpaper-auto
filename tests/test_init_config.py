"""Tests for the ``init-config`` subcommand — template generation."""

import pytest
import yaml

from wallpaper_automator.config_store import ConfigStore
from wallpaper_automator.init_config import generate_template
from wallpaper_automator.models import ConfigModel


# ── helpers ──────────────────────────────────────────────────────────────

def _collect_evaluator_keys(conditions: list) -> set[str]:
    """Recursively collect all leaf evaluator keys from a list of condition
    dicts (the YAML-parsed ``and`` / ``or`` branches).

    Raises ``ValueError`` if any entry is not a single-key dict (to avoid
    silently skipping malformed condition nodes).
    """
    keys: set[str] = set()
    for cond in conditions:
        assert isinstance(cond, dict) and len(cond) == 1, (
            f"Expected single-key condition dict, got: {cond!r}"
        )
        key = next(iter(cond))
        val = cond[key]
        if key in ("and", "or") and isinstance(val, list):
            keys.update(_collect_evaluator_keys(val))
        else:
            keys.add(key)
    return keys


def _root_children(rule: dict) -> list:
    """Return the child list of a rule's top-level ``and``/``or`` condition,
    or ``None`` if the root condition is a leaf evaluator."""
    branch = rule["condition"]
    key = next(iter(branch))
    val = branch[key]
    return val if key in ("and", "or") and isinstance(val, list) else None


# ── file creation ────────────────────────────────────────────────────────

class TestFileCreation:
    """Verify that generate_template writes a file in various scenarios."""

    def test_creates_file_at_default_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        generate_template("config.yaml")
        assert (tmp_path / "config.yaml").exists()

    def test_creates_file_at_custom_path(self, tmp_path):
        path = str(tmp_path / "custom.yaml")
        generate_template(path)
        assert (tmp_path / "custom.yaml").exists()

    def test_creates_parent_directory(self, tmp_path):
        path = str(tmp_path / "subdir" / "nested" / "config.yaml")
        generate_template(path)
        assert (tmp_path / "subdir" / "nested" / "config.yaml").exists()

    def test_errors_when_file_exists(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        with open(path, "w") as f:
            f.write("existing")
        with pytest.raises(FileExistsError):
            generate_template(path)
        with open(path) as f:
            assert f.read() == "existing"

    def test_force_overwrites_existing_file(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        with open(path, "w") as f:
            f.write("original")
        generate_template(path, force=True)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert content != "original"
        assert "wifi_ssid_is" in content


# ── config validity ──────────────────────────────────────────────────────

class TestConfigValidity:
    """Verify the generated template is a valid wallpaper-automator config."""

    def test_generated_yaml_is_valid_config(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        generate_template(path)
        store = ConfigStore()
        store.load(path)  # should not raise

    def test_template_roundtrips_through_config_model(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        generate_template(path)
        store = ConfigStore()
        store.load(path)
        assert isinstance(store.config, ConfigModel)

    def test_template_uses_all_evaluators(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        generate_template(path)
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # Collect all leaf evaluator keys across all rules
        all_keys: set[str] = set()
        for rule in raw["rule"]:
            children = _root_children(rule)
            if children is not None:
                all_keys.update(_collect_evaluator_keys(children))
            else:
                branch = rule["condition"]
                all_keys.add(next(iter(branch)))

        expected = {"wifi_ssid_is", "is_today_workday", "in_time_range", "in_geo_range"}
        assert expected.issubset(all_keys), f"Missing evaluators: {expected - all_keys}"

    def test_template_has_and_or_conditions(self, tmp_path):
        """Verify the template has nested combinator conditions
        (e.g. ``or`` containing an ``and``)."""
        path = str(tmp_path / "config.yaml")
        generate_template(path)
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        def _has_nested_combinator(conditions: list) -> bool:
            """Return True if any entry in *conditions* is itself an
            ``and``/``or`` combinator."""
            for cond in conditions:
                key = next(iter(cond))
                if key in ("and", "or") and isinstance(cond[key], list):
                    return True
            return False

        assert any(
            _has_nested_combinator(children)
            for rule in raw["rule"]
            if (children := _root_children(rule)) is not None
        ), "No nested and/or combinator found in template conditions"
