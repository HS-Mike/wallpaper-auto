"""Tests for the :mod:`wallpaper_auto.cli` module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wallpaper_auto.cli import _build_parser, cli


class TestBuildParser:
    def test_program_name(self) -> None:
        parser = _build_parser()
        assert parser.prog == "wallpaper-auto"

    @pytest.mark.parametrize(
        ("cli_args", "expected_config"),
        [
            pytest.param(["run"], "config.yaml", id="run_default"),
            pytest.param(["run", "-c", "my_config.yaml"], "my_config.yaml", id="short_opt"),
            pytest.param(["run", "--config", "prod.yaml"], "prod.yaml", id="long_opt"),
        ],
    )
    def test_config_path(self, cli_args: list[str], expected_config: str) -> None:
        parser = _build_parser()
        args = parser.parse_args(cli_args)
        assert args.config == expected_config

    @pytest.mark.parametrize(
        "cli_args",
        [
            pytest.param(["-c", "prod.yaml"], id="no_subcommand"),
            pytest.param(["init-config", "out.yaml", "-c", "prod.yaml"], id="init_config"),
        ],
    )
    def test_config_flag_rejected_outside_run(self, cli_args: list[str]) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(cli_args)

    @pytest.mark.parametrize(
        ("cli_args", "expected_level"),
        [
            pytest.param(["run"], None, id="default"),
            pytest.param(["run", "-l", "INFO"], "INFO", id="custom"),
            pytest.param(["run", "-l", "DEBUG"], "DEBUG", id="choice_debug"),
            pytest.param(["run", "-l", "WARNING"], "WARNING", id="choice_warning"),
            pytest.param(["run", "-l", "ERROR"], "ERROR", id="choice_error"),
        ],
    )
    def test_log_level(self, cli_args: list[str], expected_level: str | None) -> None:
        parser = _build_parser()
        args = parser.parse_args(cli_args)
        assert args.log_level == expected_level

    @pytest.mark.parametrize(
        ("cli_args", "expected_log_file"),
        [
            pytest.param(["run"], None, id="default"),
            pytest.param(["run", "--log-file", "app.log"], "app.log", id="long_opt"),
        ],
    )
    def test_log_file(self, cli_args: list[str], expected_log_file: str | None) -> None:
        parser = _build_parser()
        args = parser.parse_args(cli_args)
        assert args.log_file == expected_log_file

    def test_subcommand_required(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_run_flags_after_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["run", "-c", "prod.yaml", "-l", "INFO", "--log-file", "app.log"])
        assert args.subcommand == "run"
        assert args.config == "prod.yaml"
        assert args.log_level == "INFO"
        assert args.log_file == "app.log"

    def test_log_flags_after_init_config(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["init-config", "out.yaml", "-l", "INFO", "--log-file", "app.log"])
        assert args.subcommand == "init-config"
        assert args.output == "out.yaml"
        assert args.log_level == "INFO"
        assert args.log_file == "app.log"

    def test_display_capability_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["display-capability"])
        assert args.subcommand == "display-capability"

    def test_log_flags_after_display_capability(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["display-capability", "-l", "INFO", "--log-file", "app.log"])
        assert args.subcommand == "display-capability"
        assert args.log_level == "INFO"
        assert args.log_file == "app.log"

    def test_log_flags_before_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["-l", "INFO", "--log-file", "app.log", "run", "-c", "prod.yaml"])
        assert args.subcommand == "run"
        assert args.config == "prod.yaml"
        assert args.log_level == "INFO"
        assert args.log_file == "app.log"

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

    def test_run_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["run"])
        assert args.subcommand == "run"
        assert args.config == "config.yaml"

    def test_run_with_config_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["run", "-c", "prod.yaml"])
        assert args.subcommand == "run"
        assert args.config == "prod.yaml"


class TestCli:
    """``cli()`` parses ``sys.argv`` and dispatches to a subcommand entry."""

    @pytest.mark.parametrize(
        ("sys_argv", "expected_config", "expected_log_level", "expected_log_file"),
        [
            pytest.param(["wallpaper-auto", "run"], "config.yaml", None, None, id="run"),
            pytest.param(
                ["wp", "run", "-c", "prod.yaml", "-l", "INFO"],
                "prod.yaml",
                "INFO",
                None,
                id="custom",
            ),
        ],
    )
    def test_start_dispatches_to_run(
        self,
        sys_argv: list[str],
        expected_config: str,
        expected_log_level: str | None,
        expected_log_file: str | None,
    ) -> None:
        with (
            patch("sys.argv", sys_argv),
            patch("wallpaper_auto.process_mutex.ProcessMutex"),
            patch("wallpaper_auto.cli.run") as mock_run,
        ):
            cli()

        mock_run.assert_called_once_with(
            expected_config,
            log_level=expected_log_level,
            log_file=expected_log_file,
        )

    @pytest.mark.parametrize(
        ("sys_argv", "expected_config"),
        [
            pytest.param(["wp", "run"], "config.yaml", id="default"),
            pytest.param(["wp", "run", "-c", "prod.yaml"], "prod.yaml", id="with_config"),
        ],
    )
    def test_run_dispatches_to_run(
        self,
        sys_argv: list[str],
        expected_config: str,
    ) -> None:
        with (
            patch("sys.argv", sys_argv),
            patch("wallpaper_auto.process_mutex.ProcessMutex"),
            patch("wallpaper_auto.cli.run") as mock_run,
        ):
            cli()

        mock_run.assert_called_once_with(expected_config, log_level=None, log_file=None)

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
    def test_init_config_dispatches_to_init_config(
        self,
        sys_argv: list[str],
        expected_output: str,
        expected_force: bool,
    ) -> None:
        with (
            patch("sys.argv", sys_argv),
            patch("wallpaper_auto.cli.init_config") as mock_init,
            patch("wallpaper_auto.cli.run") as mock_run,
        ):
            cli()

        mock_init.assert_called_once_with(expected_output, force=expected_force)
        mock_run.assert_not_called()

    def test_display_capability_dispatches_to_entry(self) -> None:
        with (
            patch("sys.argv", ["wp", "display-capability"]),
            patch("wallpaper_auto.cli.display_capability") as mock_entry,
        ):
            cli()

        mock_entry.assert_called_once_with()

    def test_run_acquires_mutex_with_exit_on_conflict(self) -> None:
        with (
            patch("sys.argv", ["wp", "run"]),
            patch("wallpaper_auto.process_mutex.ProcessMutex") as mock_mutex_cls,
            patch("wallpaper_auto.cli.run"),
        ):
            cli()

        mock_mutex_cls.assert_called_once_with("wallpaper_auto", raise_error=False)

    def test_process_mutex_conflict_exits(self) -> None:
        with (
            patch("sys.argv", ["wp", "run"]),
            patch("wallpaper_auto.process_mutex.ProcessMutex") as mock_mutex_cls,
            patch("wallpaper_auto.cli.run"),
        ):
            mock_mutex_cls.return_value.__enter__.side_effect = SystemExit(1)
            with pytest.raises(SystemExit) as exc_info:
                cli()

        assert exc_info.value.code == 1

    def test_non_runtime_error_propagates(self) -> None:
        with (
            patch("sys.argv", ["wp", "run"]),
            patch("wallpaper_auto.process_mutex.ProcessMutex"),
            patch(
                "wallpaper_auto.cli.run",
                side_effect=ValueError("something went wrong"),
            ),
            pytest.raises(ValueError, match="something went wrong"),
        ):
            cli()

    def test_unknown_subcommand_raises(self) -> None:
        with (
            patch("sys.argv", ["wp", "run"]),
            patch("wallpaper_auto.cli._build_parser") as mock_build,
        ):
            mock_build.return_value.parse_args.return_value = SimpleNamespace(subcommand="bogus")
            with pytest.raises(RuntimeError, match="invalid args"):
                cli()
