"""Tests for wallpaper_controller.py — controller lifecycle and task processing."""

import signal
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from wallpaper_automator.config_store import ConfigStore
from wallpaper_automator.models import Rule
from wallpaper_automator.resource_manager import ResourceManager
from wallpaper_automator.rule_engine import RuleEngine
from wallpaper_automator.task import Mode, ModeSwitchTask, QuitTask, ResourceSetTask
from wallpaper_automator.trigger_manager import TriggerManager
from wallpaper_automator.wallpaper_controller import WallpaperController

# ── Helpers ─────────────────────────────────────────────────────────────────


def _mock_config_for_start(controller):
    """Give the controller a fake config so ``start()`` can read ``fallback_resource_id``."""
    mock_cs = MagicMock()
    mock_cs.fallback_resource_id = "fallback"
    controller._config_store = mock_cs


def _start_controller(controller):
    """Start the controller with default patches applied."""
    _mock_config_for_start(controller)
    with (
        patch("signal.signal"),
        patch.object(controller._resource_manager, "mount"),
        patch.object(controller, "evaluate"),
    ):
        controller.start()


def _cleanup_worker(controller):
    """Safety net: kill the worker thread if ``stop()`` was never reached."""
    if controller._worker_loop_thread is not None:
        controller._task_queue.put(QuitTask())
        controller._worker_loop_thread.join()


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def controller():
    return WallpaperController()


@pytest.fixture
def started_controller(controller):
    """Start the controller with key behaviours patched; auto-stop after test."""
    _mock_config_for_start(controller)
    with (
        patch.object(controller, "evaluate"),
        patch.object(controller._trigger_manager, "activate"),
        patch.object(controller._resource_manager, "mount"),
        patch("signal.signal"),
    ):
        controller.start()
    yield controller
    # Gracefully stop — the test may have already called stop()
    try:
        controller.stop()
    except RuntimeError:
        pass
    _cleanup_worker(controller)


# ── Initialisation ─────────────────────────────────────────────────────────


class TestWallpaperControllerInit:
    """WallpaperController.__init__ and default state."""

    def test_default_mode_is_unset(self, controller):
        assert controller._mode == Mode.UNSET

    def test_default_active_rule_is_unset(self, controller):
        assert controller.active_rule == Mode.UNSET

    def test_worker_thread_not_started(self, controller):
        assert controller._worker_loop_thread is None

    def test_tray_is_none(self, controller):
        assert controller._tray is None

    def test_task_queue_exists(self, controller):
        assert controller._task_queue is not None

    def test_managers_are_initialised(self, controller):
        assert isinstance(controller._config_store, ConfigStore)
        assert isinstance(controller._resource_manager, ResourceManager)
        assert isinstance(controller._trigger_manager, TriggerManager)
        assert isinstance(controller._rule_engine, RuleEngine)

    def test_evaluate_registered_as_trigger_callback(self, controller):
        assert controller.evaluate in controller._trigger_manager._callbacks


# ── load_config ────────────────────────────────────────────────────────────


class TestWallpaperControllerLoadConfig:
    """WallpaperController.load_config() delegates to every sub-manager."""

    def test_load_config_calls_config_store_load(self, controller):
        mock_cs = MagicMock(spec=ConfigStore)
        mock_cs.resource = {"r1": MagicMock()}
        mock_cs.trigger = [MagicMock()]
        mock_cs.rule = [MagicMock()]
        controller._config_store = mock_cs

        with (
            patch.object(controller._resource_manager, "init"),
            patch.object(controller._trigger_manager, "init"),
            patch.object(controller._rule_engine, "init"),
        ):
            controller.load_config("some/path.yaml")

        mock_cs.load.assert_called_once_with("some/path.yaml")

    def test_load_config_inits_all_managers(self, controller):
        mock_cs = MagicMock(spec=ConfigStore)
        mock_cs.resource = {"w1": MagicMock()}
        mock_cs.trigger = [MagicMock()]
        mock_cs.rule = [MagicMock()]
        controller._config_store = mock_cs

        with (
            patch.object(controller._resource_manager, "init") as rm_init,
            patch.object(controller._trigger_manager, "init") as tm_init,
            patch.object(controller._rule_engine, "init") as re_init,
        ):
            controller.load_config("p.yaml")

        rm_init.assert_called_once_with(mock_cs.resource)
        tm_init.assert_called_once_with(mock_cs.trigger)
        re_init.assert_called_once_with(mock_cs.rule)


# ── Worker loop – task processing ──────────────────────────────────────────


class TestWallpaperControllerWorkerLoop:
    """_worker_loop() processes tasks from the queue until it sees a QUIT."""

    def test_quit_breaks_loop(self, controller):
        """A single QUIT task causes the loop to exit cleanly."""
        controller._task_queue.put(QuitTask())
        controller._worker_loop()
        # If we get here without hanging, the test passes.

    def test_mode_switch_auto_resumes_triggers(self, controller):
        controller._trigger_manager = MagicMock()
        controller._config_store = MagicMock(fallback_resource_id="fallback")
        controller._task_queue.put(ModeSwitchTask(target_mode=Mode.AUTO))
        controller._task_queue.put(QuitTask())

        controller._worker_loop()

        controller._trigger_manager.resume.assert_called_once()
        assert controller._mode == Mode.AUTO

    def test_mode_switch_manual_pauses_triggers(self, controller):
        controller._trigger_manager = MagicMock()
        controller._task_queue.put(ModeSwitchTask(target_mode=Mode.MANUAL))
        controller._task_queue.put(QuitTask())

        controller._worker_loop()

        controller._trigger_manager.pause.assert_called_once()
        assert controller._mode == Mode.MANUAL

    def test_mode_switch_invalid_mode_raises(self, controller):
        controller._task_queue.put(ModeSwitchTask(target_mode=Mode.UNSET))
        controller._task_queue.put(QuitTask())

        with pytest.raises(RuntimeError, match="invalid mode"):
            controller._worker_loop()

    def test_resource_set_demounts_then_mounts(self, controller):
        controller._resource_manager = MagicMock()
        controller._task_queue.put(ResourceSetTask(target_resource_id="res_x"))
        controller._task_queue.put(QuitTask())

        controller._worker_loop()

        controller._resource_manager.demount.assert_called_once()
        controller._resource_manager.mount.assert_called_once_with("res_x")

    def test_update_system_tray_called_after_each_non_quit_task(self, controller):
        controller._resource_manager = MagicMock()
        controller._task_queue.put(ResourceSetTask(target_resource_id="r1"))
        controller._task_queue.put(QuitTask())

        with patch.object(controller, "update_system_tray") as mock_update:
            controller._worker_loop()

        # Called after RESOURCE_SET processing, *not* after QUIT.
        mock_update.assert_called_once()


# ── evaluate ───────────────────────────────────────────────────────────────


class TestWallpaperControllerEvaluate:
    """evaluate() – condition evaluation & resource dispatch."""

    def test_matching_rule_sets_active_rule_and_enqueues_resource(self, controller):
        rule = MagicMock(spec=Rule)
        rule.target = "work_res"

        mock_rm = MagicMock()
        mock_rm.active_resource_id = None
        controller._resource_manager = mock_rm
        controller._rule_engine.evaluate = MagicMock(return_value=rule)
        mock_cs = MagicMock()
        mock_cs.fallback_resource_id = "fallback"
        controller._config_store = mock_cs

        controller.evaluate()

        assert controller.active_rule is rule
        task = controller._task_queue.get_nowait()
        assert isinstance(task, ResourceSetTask)
        assert task.target_resource_id == "work_res"

    def test_no_matching_rule_uses_fallback(self, controller):
        mock_rm = MagicMock()
        mock_rm.active_resource_id = None
        controller._resource_manager = mock_rm
        controller._rule_engine.evaluate = MagicMock(return_value=None)
        mock_cs = MagicMock()
        mock_cs.fallback_resource_id = "fallback_res"
        controller._config_store = mock_cs

        controller.evaluate()

        assert controller.active_rule is None
        task = controller._task_queue.get_nowait()
        assert isinstance(task, ResourceSetTask)
        assert task.target_resource_id == "fallback_res"

    def test_skipped_when_resource_unchanged(self, controller):
        mock_rm = MagicMock()
        mock_rm.active_resource_id = "fallback_res"
        controller._resource_manager = mock_rm
        controller._rule_engine.evaluate = MagicMock(return_value=None)
        mock_cs = MagicMock()
        mock_cs.fallback_resource_id = "fallback_res"
        controller._config_store = mock_cs

        controller.evaluate()

        assert controller._task_queue.qsize() == 0

    def test_matching_rule_skipped_when_same_resource_already_active(self, controller):
        rule = MagicMock(spec=Rule)
        rule.target = "work_res"

        mock_rm = MagicMock()
        mock_rm.active_resource_id = "work_res"
        controller._resource_manager = mock_rm
        controller._rule_engine.evaluate = MagicMock(return_value=rule)

        controller.evaluate()

        assert controller._task_queue.qsize() == 0

    def test_sets_active_rule_none_when_no_rule_and_fallback_active(self, controller):
        """Regression: verify active_rule is updated even when resource is skipped."""
        mock_rm = MagicMock()
        mock_rm.active_resource_id = "fallback"
        controller._resource_manager = mock_rm
        controller._rule_engine.evaluate = MagicMock(return_value=None)
        mock_cs = MagicMock()
        mock_cs.fallback_resource_id = "fallback"
        controller._config_store = mock_cs

        controller.active_rule = Mode.UNSET
        controller.evaluate()

        # active_rule should be None (no rule matched), even though no task was enqueued
        assert controller.active_rule is None


# ── update_system_tray ─────────────────────────────────────────────────────


class TestWallpaperControllerUpdateSystemTray:
    """update_system_tray() delegates to the tray bridge."""

    def test_delegates_to_bridge_when_tray_set(self, controller):
        mock_tray = MagicMock()
        controller._tray = mock_tray

        mock_rm = MagicMock()
        mock_rm.resource_ids = ["r1", "r2"]
        mock_rm.active_resource_id = "r1"
        controller._resource_manager = mock_rm
        controller._mode = Mode.AUTO
        controller.active_rule = None

        controller.update_system_tray()

        mock_tray.bridge.update_ui.assert_called_once_with(
            ["r1", "r2"],
            Mode.AUTO,
            None,
            "r1",
        )

    def test_noop_when_tray_is_none(self, controller):
        controller._tray = None
        controller.update_system_tray()  # should not raise


# ── set_tray ───────────────────────────────────────────────────────────────


class TestWallpaperControllerSetTray:
    """set_tray() wires up all tray → controller callbacks."""

    def test_registers_all_handlers(self, controller):
        mock_tray = MagicMock()
        controller.set_tray(mock_tray)

        assert controller._tray is mock_tray
        mock_tray.bridge.register_set_mode_handler.assert_called_once_with(
            controller.add_set_mode_task,
        )
        mock_tray.bridge.register_select_resource_handler.assert_called_once_with(
            controller.add_set_resource_id_task,
        )
        mock_tray.bridge.register_quit_handler.assert_called_once_with(controller.stop)
        mock_tray.bridge.register_update_ui_handler.assert_called_once_with(
            controller.update_system_tray,
        )


# ── Task-queue helpers ─────────────────────────────────────────────────────


class TestWallpaperControllerTaskHelpers:
    """add_set_mode_task / add_set_resource_id_task enqueue correct tasks."""

    def test_add_set_mode_task_enqueues(self, controller):
        controller.add_set_mode_task(Mode.MANUAL)
        task = controller._task_queue.get_nowait()
        assert isinstance(task, ModeSwitchTask)
        assert task.target_mode == Mode.MANUAL

    def test_add_set_resource_id_task_enqueues(self, controller):
        controller.add_set_resource_id_task("my_res")
        task = controller._task_queue.get_nowait()
        assert isinstance(task, ResourceSetTask)
        assert task.target_resource_id == "my_res"


# ── start / stop lifecycle ─────────────────────────────────────────────────


def _safe_stop(controller):
    """Call stop() but ignore the error if the thread wasn't started."""
    try:
        controller.stop()
    except RuntimeError:
        pass


class TestWallpaperControllerStart:
    """start() – signal handlers, thread start, initial evaluation."""

    def test_starts_worker_thread_and_sets_mode_auto(self, started_controller):
        controller = started_controller
        assert controller._worker_loop_thread is not None
        assert controller._worker_loop_thread.is_alive()
        assert controller._mode == Mode.AUTO

    def test_registers_signal_handlers(self, controller):
        _mock_config_for_start(controller)
        with (
            patch.object(controller._resource_manager, "mount"),
            patch.object(controller, "evaluate"),
            patch("signal.signal") as mock_signal,
        ):
            controller.start()
        assert mock_signal.call_count == 2
        mock_signal.assert_has_calls(
            [
                call(signal.SIGINT, ANY),
                call(signal.SIGTERM, ANY),
            ]
        )
        _safe_stop(controller)

    def test_shows_tray_when_set(self, controller):
        mock_tray = MagicMock()
        controller._tray = mock_tray
        _start_controller(controller)
        mock_tray.show.assert_called_once()
        _safe_stop(controller)

    def test_calls_evaluate_and_activates_triggers(self, controller):
        _mock_config_for_start(controller)
        with (
            patch.object(controller._resource_manager, "mount"),
            patch.object(controller, "evaluate") as mock_eval,
            patch.object(controller._trigger_manager, "activate") as mock_activate,
            patch("signal.signal"),
        ):
            controller.start()
        mock_eval.assert_called_once()
        mock_activate.assert_called_once()
        _safe_stop(controller)


class TestWallpaperControllerStop:
    """stop() – clean shutdown."""

    def test_stop_raises_when_thread_not_started(self, controller):
        with pytest.raises(RuntimeError, match="worker loop thread not start"):
            controller.stop()

    def test_stop_joins_thread_and_cleans_up(self, controller):
        _start_controller(controller)
        controller.stop()
        assert controller._worker_loop_thread is None
        # After stop the queue should be processed; no leftover tasks.
        assert controller._task_queue.qsize() == 0
        _cleanup_worker(controller)

    def test_stop_deactivates_triggers_and_demounts_resource(self, controller):
        _start_controller(controller)
        with (
            patch.object(controller._trigger_manager, "deactivate") as mock_deact,
            patch.object(controller._resource_manager, "demount") as mock_demount,
        ):
            controller.stop()
        mock_deact.assert_called_once()
        mock_demount.assert_called_once()
        _cleanup_worker(controller)

    @pytest.mark.parametrize("app", [MagicMock(), None], ids=["with_app", "without_app"])
    def test_stop_hides_tray_and_optionally_quits_app(self, controller, app):
        mock_tray = MagicMock()
        mock_tray._app = app
        controller._tray = mock_tray
        _start_controller(controller)
        controller.stop()
        mock_tray.hide.assert_called_once()
        if app is not None:
            app.quit.assert_called_once()
        _cleanup_worker(controller)
