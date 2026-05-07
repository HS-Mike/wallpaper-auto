"""Tests for the :mod:`wallpaper_automator.service` module."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, call, patch

import pytest

from wallpaper_automator.service import _build_parser, _setup_logging, run_service

# ── Helpers ─────────────────────────────────────────────────────────────────


def _mock_controller_cls():
    """Return a ``WallpaperController``-like mock with the expected interface."""
    ctrl = MagicMock()
    ctrl._worker_loop_thread = None
    return ctrl


def _mock_tray_cls():
    """Return a ``WallpaperSwitchSystemTray``-like mock."""
    return MagicMock()


# ── run_service ─────────────────────────────────────────────────────────────


class TestRunServiceBasic:
    """Default startup flow with no custom components."""

    def test_creates_controller_and_loads_config(self):
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
        ):
            run_service("some_config.yaml")

        controller.load_config.assert_called_once_with("some_config.yaml")

    def test_creates_tray_and_wires_it(self):
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
        ):
            run_service("cfg.yaml")

        controller.set_tray.assert_called_once_with(tray)

    def test_calls_start_and_exec(self):
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
        ):
            run_service("cfg.yaml")

        controller.start.assert_called_once()
        tray.exec.assert_called_once()

    def test_call_order(self):
        """Verify the exact sequence of calls on the controller."""
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
        ):
            run_service("cfg.yaml")

        expected = [
            call.load_config("cfg.yaml"),
            call.set_tray(tray),
            call.start(),
        ]
        controller.assert_has_calls(expected, any_order=False)

    def test_allows_empty_config_path(self):
        """A blank config path is passed through (validation happens later)."""
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
        ):
            run_service("")

        controller.load_config.assert_called_once_with("")


class TestRunServiceCustomComponents:
    """Custom trigger / resource / evaluator registration."""

    def test_custom_triggers_registered(self):
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()
        mock_trigger_cls = MagicMock()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
            patch("wallpaper_automator.service.TriggerManager.register_trigger") as mock_register,
        ):
            run_service("cfg.yaml", custom_triggers={"my_t": mock_trigger_cls})

        mock_register.assert_called_once_with("my_t", mock_trigger_cls)

    def test_custom_resources_registered(self):
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()
        mock_resource_cls = MagicMock()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
            patch("wallpaper_automator.service.ResourceManager.register_resource") as mock_register,
        ):
            run_service("cfg.yaml", custom_resources={"my_r": mock_resource_cls})

        mock_register.assert_called_once_with("my_r", mock_resource_cls)

    def test_custom_evaluators_registered(self):
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()
        mock_evaluator = MagicMock()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
            patch("wallpaper_automator.service.RuleEngine.register_evaluator") as mock_register,
        ):
            run_service("cfg.yaml", custom_evaluators={"my_e": mock_evaluator})

        mock_register.assert_called_once_with("my_e", mock_evaluator)

    def test_all_custom_components_together(self):
        """All three custom registration types work simultaneously."""
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()
        t_cls, r_cls, e_inst = MagicMock(), MagicMock(), MagicMock()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
            patch("wallpaper_automator.service.TriggerManager.register_trigger") as reg_t,
            patch("wallpaper_automator.service.ResourceManager.register_resource") as reg_r,
            patch("wallpaper_automator.service.RuleEngine.register_evaluator") as reg_e,
        ):
            run_service(
                "cfg.yaml",
                custom_triggers={"t1": t_cls},
                custom_resources={"r1": r_cls},
                custom_evaluators={"e1": e_inst},
            )

        reg_t.assert_called_once_with("t1", t_cls)
        reg_r.assert_called_once_with("r1", r_cls)
        reg_e.assert_called_once_with("e1", e_inst)

    def test_no_custom_registrations_by_default(self):
        """No registration calls happen when no custom args are passed."""
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
            patch("wallpaper_automator.service.TriggerManager.register_trigger") as reg_t,
            patch("wallpaper_automator.service.ResourceManager.register_resource") as reg_r,
            patch("wallpaper_automator.service.RuleEngine.register_evaluator") as reg_e,
        ):
            run_service("cfg.yaml")

        reg_t.assert_not_called()
        reg_r.assert_not_called()
        reg_e.assert_not_called()

    def test_multiple_custom_triggers(self):
        """Multiple custom triggers are all registered."""
        controller = _mock_controller_cls()
        tray = _mock_tray_cls()
        t1, t2 = MagicMock(), MagicMock()

        with (
            patch("wallpaper_automator.service.WallpaperController", return_value=controller),
            patch("wallpaper_automator.service.WallpaperSwitchSystemTray", return_value=tray),
            patch("wallpaper_automator.service.TriggerManager.register_trigger") as reg_t,
        ):
            run_service("cfg.yaml", custom_triggers={"a": t1, "b": t2})

        assert reg_t.call_count == 2
        reg_t.assert_has_calls(
            [
                call("a", t1),
                call("b", t2),
            ],
            any_order=True,
        )


class TestMainDelegation:
    """Verify the CLI entry point delegates to :func:`run_service`."""

    def test_module_imports_cleanly(self):
        """The __main__ module can be imported without side effects."""
        import importlib

        import wallpaper_automator.__main__ as main_mod

        importlib.reload(main_mod)
        assert hasattr(main_mod, "run_service")


# ── _setup_logging ───────────────────────────────────────────────────────────


class TestSetupLogging:
    """``_setup_logging()`` configures the root logger."""

    def test_uses_basicConfig(self):  # noqa: N802
        with patch("wallpaper_automator.service.logging.basicConfig") as mock_bc:
            _setup_logging("INFO")
        mock_bc.assert_called_once()

    def test_sets_correct_level(self):
        with patch("wallpaper_automator.service.logging.basicConfig") as mock_bc:
            _setup_logging("WARNING")
        assert mock_bc.call_args[1]["level"] == logging.WARNING

    def test_default_level_is_debug(self):
        with patch("wallpaper_automator.service.logging.basicConfig") as mock_bc:
            _setup_logging("DEBUG")
        assert mock_bc.call_args[1]["level"] == logging.DEBUG

    def test_has_format_string(self):
        with patch("wallpaper_automator.service.logging.basicConfig") as mock_bc:
            _setup_logging("DEBUG")
        fmt = mock_bc.call_args[1]["format"]
        assert "%(asctime)s" in fmt
        assert "%(levelname)" in fmt
        assert "%(thread)" in fmt
        assert "%(message)s" in fmt


# ── _build_parser ────────────────────────────────────────────────────────────


class TestBuildParser:
    """``_build_parser()`` creates the CLI argument parser."""

    def test_prog_name(self):
        parser = _build_parser()
        assert parser.prog == "wallpaper-automator"

    def test_default_config_path(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.config == "config.yaml"

    def test_custom_config_via_c(self):
        parser = _build_parser()
        args = parser.parse_args(["-c", "my_config.yaml"])
        assert args.config == "my_config.yaml"

    def test_custom_config_via_long(self):
        parser = _build_parser()
        args = parser.parse_args(["--config", "prod.yaml"])
        assert args.config == "prod.yaml"

    def test_default_log_level(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.log_level == "DEBUG"

    def test_custom_log_level(self):
        parser = _build_parser()
        args = parser.parse_args(["-l", "INFO"])
        assert args.log_level == "INFO"

    def test_log_level_choices(self):
        parser = _build_parser()
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            args = parser.parse_args(["-l", level])
            assert args.log_level == level

    def test_init_config_subcommand(self):
        parser = _build_parser()
        args = parser.parse_args(["init-config", "out.yaml"])
        assert args.subcommand == "init-config"
        assert args.output == "out.yaml"

    def test_init_config_default_output(self):
        parser = _build_parser()
        args = parser.parse_args(["init-config"])
        assert args.output == "config.yaml"

    def test_init_config_with_force(self):
        parser = _build_parser()
        args = parser.parse_args(["init-config", "out.yaml", "-f"])
        assert args.force is True

    def test_init_config_without_force(self):
        parser = _build_parser()
        args = parser.parse_args(["init-config", "out.yaml"])
        assert args.force is False


# ── run_service CLI mode ─────────────────────────────────────────────────────


class TestRunServiceCLIMode:
    """``run_service()`` with ``config_path=None`` enters CLI mode."""

    def test_cli_defaults(self):
        """No CLI args: uses default config.yaml and DEBUG."""
        with (
            patch("sys.argv", ["wallpaper-automator"]),
            patch("wallpaper_automator.process_mutex.ProcessMutex"),
            patch("wallpaper_automator.service._run_service_impl") as mock_impl,
        ):
            run_service()

        mock_impl.assert_called_once_with(
            "config.yaml",
            None,
            None,
            None,
        )

    def test_cli_config_and_log_level(self):
        with (
            patch("sys.argv", ["wp", "-c", "prod.yaml", "-l", "INFO"]),
            patch("wallpaper_automator.process_mutex.ProcessMutex"),
            patch("wallpaper_automator.service._run_service_impl") as mock_impl,
        ):
            run_service()

        mock_impl.assert_called_once_with(
            "prod.yaml",
            None,
            None,
            None,
        )

    def test_cli_custom_triggers_forwarded(self):
        """Custom component registrations are forwarded to _run_service_impl."""
        t_cls, r_cls, e_inst = MagicMock(), MagicMock(), MagicMock()
        with (
            patch("sys.argv", ["wp"]),
            patch("wallpaper_automator.process_mutex.ProcessMutex"),
            patch("wallpaper_automator.service._run_service_impl") as mock_impl,
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

    def test_cli_init_config(self):
        with (
            patch("sys.argv", ["wp", "init-config", "out.yaml"]),
            patch("wallpaper_automator.init_config.generate_template") as mock_gen,
            patch("wallpaper_automator.service._run_service_impl") as mock_impl,
        ):
            run_service()

        mock_gen.assert_called_once_with("out.yaml", force=False)
        mock_impl.assert_not_called()

    def test_cli_init_config_force(self):
        with (
            patch("sys.argv", ["wp", "init-config", "out.yaml", "-f"]),
            patch("wallpaper_automator.init_config.generate_template") as mock_gen,
        ):
            run_service()

        mock_gen.assert_called_once_with("out.yaml", force=True)

    def test_cli_init_config_default_output(self):
        with (
            patch("sys.argv", ["wp", "init-config"]),
            patch("wallpaper_automator.init_config.generate_template") as mock_gen,
        ):
            run_service()

        mock_gen.assert_called_once_with("config.yaml", force=False)

    def test_mutex_acquired_with_correct_name(self):
        with (
            patch("sys.argv", ["wp"]),
            patch("wallpaper_automator.process_mutex.ProcessMutex") as mock_mutex_cls,
            patch("wallpaper_automator.service._run_service_impl"),
        ):
            run_service()

        mock_mutex_cls.assert_called_once_with("wallpaper_automator")


class TestRunServiceCLIErrors:
    """Error paths in CLI mode."""

    def test_init_config_file_exists_exits(self):
        with (
            patch("sys.argv", ["wp", "init-config", "out.yaml"]),
            patch(
                "wallpaper_automator.init_config.generate_template",
                side_effect=FileExistsError("already there"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            run_service()

        assert exc_info.value.code == 1

    def test_process_mutex_conflict_exits(self):
        with (
            patch("sys.argv", ["wp"]),
            patch(
                "wallpaper_automator.process_mutex.ProcessMutex",
            ) as mock_mutex_cls,
            patch("wallpaper_automator.service._run_service_impl"),
        ):
            mock_mutex_cls.return_value.__enter__.side_effect = RuntimeError("conflict")
            with pytest.raises(SystemExit) as exc_info:
                run_service()

        assert exc_info.value.code == 1
