"""Tests for the :mod:`wallpaper_auto.service` module."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

import pytest

from wallpaper_auto.evaluator.base_evaluator import BaseEvaluator
from wallpaper_auto.models import LoggingConfig
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource_manager import ResourceManager
from wallpaper_auto.rule_engine import RuleEngine
from wallpaper_auto.service import (
    _read_logging_config,
    _setup_logging,
    display_capability,
    init_config,
    run,
)
from wallpaper_auto.trigger.base_trigger import BaseTrigger
from wallpaper_auto.trigger_manager import TriggerManager
from wallpaper_auto.util.display_util import LUID, DisplayCapability, DisplayInfo

_LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class TestSetupLogging:
    def test_configures_root_logger(self) -> None:
        with patch("wallpaper_auto.service.logging.basicConfig") as mock_bc:
            _setup_logging("INFO")
        mock_bc.assert_called_once()

    @pytest.mark.parametrize(
        ("level_str", "expected_level"),
        [
            pytest.param("DEBUG", logging.DEBUG, id="debug"),
            pytest.param("INFO", logging.INFO, id="info"),
            pytest.param("WARNING", logging.WARNING, id="warning"),
            pytest.param("ERROR", logging.ERROR, id="error"),
        ],
    )
    def test_sets_correct_level(self, level_str: _LogLevel, expected_level: int) -> None:
        with patch("wallpaper_auto.service.logging.basicConfig") as mock_bc:
            _setup_logging(level_str)
        assert mock_bc.call_args[1]["level"] == expected_level

    def test_has_format_string(self) -> None:
        with patch("wallpaper_auto.service.logging.basicConfig") as mock_bc:
            _setup_logging("DEBUG")
        fmt = mock_bc.call_args[1]["format"]
        assert "%(asctime)s" in fmt
        assert "%(levelname)" in fmt
        assert "%(thread)" in fmt
        assert "%(message)s" in fmt

    def test_no_file_handler_by_default(self) -> None:
        with (
            patch("wallpaper_auto.service.logging.basicConfig"),
            patch("wallpaper_auto.service.logging.FileHandler") as mock_fh,
        ):
            _setup_logging("INFO")
        mock_fh.assert_not_called()

    def test_adds_file_handler_when_log_file_given(self) -> None:
        with (
            patch("wallpaper_auto.service.logging.basicConfig"),
            patch("wallpaper_auto.service.logging.FileHandler") as mock_fh,
            patch("wallpaper_auto.service.logging.getLogger"),
        ):
            _setup_logging("INFO", "app.log")
        mock_fh.assert_called_once_with("app.log", encoding="utf-8")
        handler = mock_fh.return_value
        handler.setLevel.assert_called_once_with(logging.INFO)
        handler.setFormatter.assert_called_once()


class TestReadLoggingConfig:
    """``_read_logging_config()`` parses the ``logging`` section of a config file."""

    def test_reads_logging_section(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("logging:\n  level: WARNING\n  file: app.log\n", encoding="utf-8")
        assert _read_logging_config(str(cfg)) == LoggingConfig(level="WARNING", file="app.log")

    def test_returns_defaults_when_logging_section_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("resource:\n  black: path.jpg\n", encoding="utf-8")
        assert _read_logging_config(str(cfg)) == LoggingConfig()

    def test_returns_defaults_when_root_is_not_dict(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("- a\n- b\n", encoding="utf-8")
        assert _read_logging_config(str(cfg)) == LoggingConfig()

    def test_returns_defaults_when_logging_is_not_dict(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("logging: INFO\n", encoding="utf-8")
        assert _read_logging_config(str(cfg)) == LoggingConfig()


class _FakeTrigger(BaseTrigger):  # type: ignore[misc]
    """Minimal ``BaseTrigger`` subclass for registration tests."""


class _FakeResource(BaseResource):  # type: ignore[misc]
    """Minimal ``BaseResource`` subclass for registration tests."""

    def mount(self) -> None:
        pass

    def demount(self) -> None:
        pass


class _FakeEvaluator(BaseEvaluator):  # type: ignore[misc]
    """Minimal ``BaseEvaluator`` subclass for registration tests."""

    def __call__(self, _: Any) -> bool:
        return True


class TestRun:
    """``run()`` registers custom components, then boots the controller."""

    @pytest.mark.parametrize(
        ("kwarg_name", "registry", "name", "value"),
        [
            pytest.param(
                "custom_triggers",
                TriggerManager._support_triggers,
                "t1",
                _FakeTrigger,
                id="trigger",
            ),
            pytest.param(
                "custom_resources",
                ResourceManager._support_resources,
                "r1",
                _FakeResource,
                id="resource",
            ),
            pytest.param(
                "custom_evaluators",
                RuleEngine._evaluators,
                "e1",
                _FakeEvaluator(),
                id="evaluator",
            ),
        ],
    )
    def test_custom_component_registered(
        self,
        kwarg_name: str,
        registry: dict[str, Any],
        name: str,
        value: Any,
    ) -> None:
        with (
            patch("wallpaper_auto.service.WallpaperController"),
            patch("wallpaper_auto.service.WallpaperSwitchSystemTray"),
            patch(
                "wallpaper_auto.service._read_logging_config",
                return_value=LoggingConfig(),
            ),
            patch("wallpaper_auto.service._setup_logging"),
        ):
            run_kwargs: Any = {kwarg_name: {name: value}}
            run("cfg.yaml", **run_kwargs)

        try:
            assert registry[name] is value
        finally:
            del registry[name]

    def test_rejects_non_subclass_trigger(self) -> None:
        invalid_cls: Any = object
        with pytest.raises(ValueError, match="inherit from BaseTrigger"):
            TriggerManager.register_trigger("bad", invalid_cls)

    def test_rejects_non_subclass_resource(self) -> None:
        invalid_cls: Any = object
        with pytest.raises(ValueError, match="inherit from BaseResource"):
            ResourceManager.register_resource("bad", invalid_cls)

    def test_rejects_non_instance_evaluator(self) -> None:
        invalid_instance: Any = object
        with pytest.raises(ValueError, match="must be an instance"):
            RuleEngine.register_evaluator("bad", invalid_instance)


class TestInitConfig:
    """``init_config()`` writes a starter config, exiting when the file exists."""

    def test_generates_template(self) -> None:
        with patch("wallpaper_auto.init_config.generate_template") as mock_gen:
            init_config("out.yaml", force=True)
        mock_gen.assert_called_once_with("out.yaml", force=True)

    def test_exits_with_error_when_file_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        error = FileExistsError("out.yaml already exists. Use -f/--force to overwrite.")
        with (
            patch("wallpaper_auto.init_config.generate_template", side_effect=error),
            pytest.raises(SystemExit) as exc_info,
        ):
            init_config("out.yaml")
        assert exc_info.value.code == 1
        assert str(error) in capsys.readouterr().err


class TestDisplayCapability:
    """``display_capability()`` queries display_util and prints a summary."""

    _DISPLAY = DisplayInfo(
        device_name=r"\\.\DISPLAY1",
        monitor_device_path=r"\\?\DISPLAY#DELA012#5&123",
        model="DELL U2723QE",
        source_resolution=(3840, 2160),
        position=(0, 0),
        target_resolution=(3840, 2160),
        adapter_id=LUID(1, 2),
        source_id=0,
        scale=150,
    )

    def test_prints_attributes_and_capability(self, capsys: pytest.CaptureFixture[str]) -> None:
        capability = DisplayCapability(
            scale=(100, 125, 150, 175, 200),
            reference_scale=150,
            resolution=((3840, 2160), (2560, 1440)),
        )
        with (
            patch("wallpaper_auto.service.set_process_dpi_aware") as mock_dpi,
            patch("wallpaper_auto.service.get_display_info", return_value=[self._DISPLAY]),
            patch("wallpaper_auto.service.get_display_capability", return_value=capability),
        ):
            display_capability()

        mock_dpi.assert_called_once_with()
        out = capsys.readouterr().out
        assert "Found 1 active display(s)" in out
        assert "[1]" in out
        assert "'DELL U2723QE'" in out
        assert "3840 x 2160" in out
        assert "0, 0" in out
        assert "150%" in out
        assert "1,2" in out
        assert "reference scale : 150%" in out
        assert "100%, 125%, 150%, 175%, 200%" in out
        assert "3840x2160, 2560x1440" in out

    def test_prints_unavailable_when_capability_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch("wallpaper_auto.service.set_process_dpi_aware"),
            patch("wallpaper_auto.service.get_display_info", return_value=[self._DISPLAY]),
            patch("wallpaper_auto.service.get_display_capability", return_value=None),
        ):
            display_capability()

        out = capsys.readouterr().out
        assert "capability      : unavailable (topology transition / driver)" in out

    def test_prints_unavailable_when_capability_raises(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch("wallpaper_auto.service.set_process_dpi_aware"),
            patch("wallpaper_auto.service.get_display_info", return_value=[self._DISPLAY]),
            patch(
                "wallpaper_auto.service.get_display_capability",
                side_effect=ValueError("bad"),
            ),
        ):
            display_capability()

        out = capsys.readouterr().out
        assert "capability      : unavailable (bad)" in out

    def test_prints_cannot_query_when_get_display_info_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            patch("wallpaper_auto.service.set_process_dpi_aware"),
            patch("wallpaper_auto.service.get_display_info", return_value=None),
        ):
            display_capability()

        assert "cannot query displays" in capsys.readouterr().out
