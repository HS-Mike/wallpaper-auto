"""Tests for the :mod:`wallpaper_automator.service` module."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from wallpaper_automator.service import run_service

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
        assert hasattr(main_mod, "_setup_logging")
