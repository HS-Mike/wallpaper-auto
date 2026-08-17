"""Tests for the :mod:`wallpaper_auto.service` module."""

from __future__ import annotations

import logging
from typing import Any, Literal
from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.evaluator.base_evaluator import BaseEvaluator
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource_manager import ResourceManager
from wallpaper_auto.rule_engine import RuleEngine
from wallpaper_auto.service import _build_parser, _setup_logging, run_service
from wallpaper_auto.trigger.base_trigger import BaseTrigger
from wallpaper_auto.trigger_manager import TriggerManager

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


class TestBuildParser:
    def test_program_name(self) -> None:
        parser = _build_parser()
        assert parser.prog == "wallpaper-auto"

    @pytest.mark.parametrize(
        ("cli_args", "expected_config"),
        [
            pytest.param([], "config.yaml", id="default"),
            pytest.param(["-c", "my_config.yaml"], "my_config.yaml", id="short_opt"),
            pytest.param(["--config", "prod.yaml"], "prod.yaml", id="long_opt"),
        ],
    )
    def test_config_path(self, cli_args: list[str], expected_config: str) -> None:
        parser = _build_parser()
        args = parser.parse_args(cli_args)
        assert args.config == expected_config

    @pytest.mark.parametrize(
        ("cli_args", "expected_level"),
        [
            pytest.param([], "DEBUG", id="default"),
            pytest.param(["-l", "INFO"], "INFO", id="custom"),
            pytest.param(["-l", "DEBUG"], "DEBUG", id="choice_debug"),
            pytest.param(["-l", "WARNING"], "WARNING", id="choice_warning"),
            pytest.param(["-l", "ERROR"], "ERROR", id="choice_error"),
        ],
    )
    def test_log_level(self, cli_args: list[str], expected_level: str) -> None:
        parser = _build_parser()
        args = parser.parse_args(cli_args)
        assert args.log_level == expected_level

    @pytest.mark.parametrize(
        ("cli_args", "expected_log_file"),
        [
            pytest.param([], None, id="default"),
            pytest.param(["--log-file", "app.log"], "app.log", id="long_opt"),
        ],
    )
    def test_log_file(self, cli_args: list[str], expected_log_file: str | None) -> None:
        parser = _build_parser()
        args = parser.parse_args(cli_args)
        assert args.log_file == expected_log_file

    def test_init_config_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["init-config", "out.yaml"])
        assert args.subcommand == "init-config"
        assert args.output == "out.yaml"

    def test_init_config_default_output(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["init-config"])
        assert args.output == "config.yaml"

    @pytest.mark.parametrize(
        ("cli_args", "expected_force"),
        [
            pytest.param(["init-config", "out.yaml"], False, id="without_force"),
            pytest.param(["init-config", "out.yaml", "-f"], True, id="with_short"),
            pytest.param(["init-config", "out.yaml", "--force"], True, id="with_long"),
        ],
    )
    def test_init_config_force(self, cli_args: list[str], expected_force: bool) -> None:
        parser = _build_parser()
        args = parser.parse_args(cli_args)
        assert args.force == expected_force


class TestRunServiceCLIMode:
    """``run_service()`` with ``config_path=None`` enters CLI mode."""

    @pytest.mark.parametrize(
        ("sys_argv", "expected_config"),
        [
            pytest.param(["wallpaper-auto"], "config.yaml", id="defaults"),
            pytest.param(["wp", "-c", "prod.yaml", "-l", "INFO"], "prod.yaml", id="custom"),
        ],
    )
    def test_cli_forwards_config_to_impl(
        self,
        sys_argv: list[str],
        expected_config: str,
    ) -> None:
        with (
            patch("sys.argv", sys_argv),
            patch("wallpaper_auto.process_mutex.ProcessMutex"),
            patch("wallpaper_auto.service._run_service_impl") as mock_impl,
        ):
            run_service()

        mock_impl.assert_called_once_with(
            expected_config,
            None,
            None,
            None,
        )

    @pytest.mark.parametrize(
        ("sys_argv", "expected_output", "expected_force"),
        [
            pytest.param(
                ["wp", "init-config", "out.yaml"],
                "out.yaml",
                False,
                id="with_output",
            ),
            pytest.param(["wp", "init-config"], "config.yaml", False, id="default_output"),
            pytest.param(
                ["wp", "init-config", "out.yaml", "-f"],
                "out.yaml",
                True,
                id="with_force",
            ),
        ],
    )
    def test_cli_init_config(
        self,
        sys_argv: list[str],
        expected_output: str,
        expected_force: bool,
    ) -> None:
        with (
            patch("sys.argv", sys_argv),
            patch("wallpaper_auto.init_config.generate_template") as mock_gen,
            patch("wallpaper_auto.service._run_service_impl") as mock_impl,
        ):
            run_service()

        mock_gen.assert_called_once_with(expected_output, force=expected_force)
        mock_impl.assert_not_called()

    def test_cli_custom_triggers_forwarded(self) -> None:
        t_cls: Any = MagicMock()
        r_cls: Any = MagicMock()
        e_inst: Any = MagicMock()
        with (
            patch("sys.argv", ["wp"]),
            patch("wallpaper_auto.process_mutex.ProcessMutex"),
            patch("wallpaper_auto.service._run_service_impl") as mock_impl,
        ):
            run_service(
                custom_triggers={"t1": t_cls},
                custom_resources={"r1": r_cls},
                custom_evaluators={"e1": e_inst},
            )

        mock_impl.assert_called_once_with(
            "config.yaml",
            {"t1": t_cls},
            {"r1": r_cls},
            {"e1": e_inst},
        )

    def test_mutex_acquired_with_correct_name(self) -> None:
        with (
            patch("sys.argv", ["wp"]),
            patch("wallpaper_auto.process_mutex.ProcessMutex") as mock_mutex_cls,
            patch("wallpaper_auto.service._run_service_impl"),
        ):
            run_service()

        mock_mutex_cls.assert_called_once_with("wallpaper_auto")


class TestRunServiceCLIErrors:
    def test_init_config_file_exists_exits(self) -> None:
        with (
            patch("sys.argv", ["wp", "init-config", "out.yaml"]),
            patch(
                "wallpaper_auto.init_config.generate_template",
                side_effect=FileExistsError("already there"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_service()

        assert exc_info.value.code == 1

    def test_process_mutex_conflict_exits(self) -> None:
        with (
            patch("sys.argv", ["wp"]),
            patch("wallpaper_auto.process_mutex.ProcessMutex") as mock_mutex_cls,
            patch("wallpaper_auto.service._run_service_impl"),
        ):
            mock_mutex_cls.return_value.__enter__.side_effect = RuntimeError("conflict")
            with pytest.raises(SystemExit) as exc_info:
                run_service()

        assert exc_info.value.code == 1

    def test_non_runtime_error_propagates(self) -> None:
        with (
            patch("sys.argv", ["wp"]),
            patch("wallpaper_auto.process_mutex.ProcessMutex"),
            patch(
                "wallpaper_auto.service._run_service_impl",
                side_effect=ValueError("something went wrong"),
            ),
            pytest.raises(ValueError, match="something went wrong"),
        ):
            run_service()


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


class TestRunServiceImpl:
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
        ):
            run_kwargs: Any = {kwarg_name: {name: value}}
            run_service("cfg.yaml", **run_kwargs)

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
