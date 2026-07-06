"""Tests for wallpaper_controller.py — controller lifecycle and task processing."""

import signal
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.models import Rule
from wallpaper_auto.resource_manager import ResourceManager
from wallpaper_auto.rule_engine import RuleEngine
from wallpaper_auto.task import Mode, ModeSwitchTask, QuitTask, TargetSetTask, TaskType
from wallpaper_auto.trigger_manager import TriggerManager
from wallpaper_auto.wallpaper_controller import WallpaperController

# ── Helpers ─────────────────────────────────────────────────────────────────


def _mock_config_for_start(controller):
    """Give the controller a fake config so ``start()`` can read ``fallback_target``."""
    mock_cs = MagicMock()
    mock_cs.fallback_target = "fallback"
    mock_cs.at_shutdown_resource_id = None
    controller._config_store = mock_cs


def _start_controller(controller):
    """Start the controller with default patches applied."""
    _mock_config_for_start(controller)
    with (
        patch("signal.signal"),
        patch.object(controller, "evaluate"),
    ):
        controller.start()


def _make_task(task, priority=5, counter=0):
    """Wrap a task in the (priority, counter, task) tuple expected by PriorityQueue."""
    return (priority, counter, task)


def _cleanup_worker(controller):
    """Safety net: kill the worker thread if ``stop()`` was never reached."""
    if controller._worker_loop_thread is not None:
        controller._task_queue.put(_make_task(QuitTask(), priority=0))
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
        patch.object(controller, "_display_trigger"),
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
        assert controller.active_rule is None

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
    """WallpaperController.load_config() delegates to sub-managers."""

    def test_load_config_calls_config_store_load(self, controller):
        mock_cs = MagicMock(spec=ConfigStore)
        mock_cs.trigger = [MagicMock()]
        mock_cs.rule = [MagicMock()]
        controller._config_store = mock_cs

        with (
            patch.object(controller._trigger_manager, "init"),
            patch.object(controller._rule_engine, "init"),
        ):
            controller.load_config("some/path.yaml")

        mock_cs.load.assert_called_once_with("some/path.yaml")

    def test_load_config_inits_trigger_manager_and_rule_engine(self, controller):
        mock_cs = MagicMock(spec=ConfigStore)
        mock_cs.trigger = [MagicMock()]
        mock_cs.rule = [MagicMock()]
        controller._config_store = mock_cs

        with (
            patch.object(controller._trigger_manager, "init") as tm_init,
            patch.object(controller._rule_engine, "init") as re_init,
        ):
            controller.load_config("p.yaml")

        tm_init.assert_called_once_with(mock_cs.trigger)
        re_init.assert_called_once_with(mock_cs.rule)


# ── Worker loop – task processing ──────────────────────────────────────────


class TestWallpaperControllerWorkerLoop:
    """_worker_loop() processes tasks from the queue until it sees a QUIT."""

    def test_quit_breaks_loop(self, controller):
        """A single QUIT task causes the loop to exit cleanly."""
        controller._task_queue.put(_make_task(QuitTask(), priority=0))
        controller._worker_loop()
        # If we get here without hanging, the test passes.

    def test_mode_switch_auto_resumes_triggers(self, controller):
        controller._trigger_manager = MagicMock()
        controller._config_store = MagicMock(fallback_target="fallback")
        controller._display_manager = MagicMock()
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = {}
        controller._task_queue.put(_make_task(ModeSwitchTask(target_mode=Mode.AUTO)))
        controller._task_queue.put(_make_task(QuitTask(), priority=10))

        controller._worker_loop()

        controller._trigger_manager.resume.assert_called_once()
        assert controller._mode == Mode.AUTO

    def test_mode_switch_manual_pauses_triggers(self, controller):
        controller._trigger_manager = MagicMock()
        controller._task_queue.put(_make_task(ModeSwitchTask(target_mode=Mode.MANUAL)))
        controller._task_queue.put(_make_task(QuitTask(), priority=10))

        controller._worker_loop()

        controller._trigger_manager.pause.assert_called_once()
        assert controller._mode == Mode.MANUAL

    def test_mode_switch_invalid_mode_raises(self, controller):
        controller._task_queue.put(_make_task(ModeSwitchTask(target_mode=Mode.UNSET)))
        controller._task_queue.put(_make_task(QuitTask(), priority=10))

        with pytest.raises(RuntimeError, match="invalid mode"):
            controller._worker_loop()

    def test_target_set_calls_evaluate_target(self, controller):
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = {}
        controller._display_manager = MagicMock()
        controller._task_queue.put(_make_task(TargetSetTask(target="res_x", matched_rule=None)))
        controller._task_queue.put(_make_task(QuitTask(), priority=10))

        controller._worker_loop()

        controller._resource_manager.evaluate_target.assert_called_once_with("res_x")

    def test_update_system_tray_called_after_each_non_quit_task(self, controller):
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = {}
        controller._display_manager = MagicMock()
        controller._task_queue.put(_make_task(TargetSetTask(target="r1", matched_rule=None)))
        controller._task_queue.put(_make_task(QuitTask(), priority=10))

        with patch.object(controller, "update_system_tray") as mock_update:
            controller._worker_loop()

        # Called after TARGET_SET processing, *not* after QUIT.
        mock_update.assert_called_once()


# ── evaluate ───────────────────────────────────────────────────────────────


class TestWallpaperControllerEvaluate:
    """evaluate() – condition evaluation & resource dispatch."""

    def test_matching_rule_enqueues_target_with_rule(self, controller):
        rule = MagicMock(spec=Rule)
        rule.target = "work_res"

        controller._rule_engine.evaluate = MagicMock(return_value=rule)
        mock_cs = MagicMock()
        mock_cs.fallback_target = "fallback"
        controller._config_store = mock_cs

        controller.evaluate()

        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, TargetSetTask)
        assert task.target == "work_res"
        assert task.matched_rule is rule

    def test_no_matching_rule_uses_fallback(self, controller):
        controller._rule_engine.evaluate = MagicMock(return_value=None)
        mock_cs = MagicMock()
        mock_cs.fallback_target = "fallback_res"
        controller._config_store = mock_cs

        controller.evaluate()

        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, TargetSetTask)
        assert task.target == "fallback_res"
        assert task.matched_rule is None


# ── update_system_tray ─────────────────────────────────────────────────────


class TestWallpaperControllerUpdateSystemTray:
    """update_system_tray() delegates to the tray bridge."""

    def test_delegates_to_bridge_when_tray_set(self, controller):
        mock_tray = MagicMock()
        controller._tray = mock_tray

        mock_cs = MagicMock()
        mock_cs.resource = {"r1": MagicMock(), "r2": MagicMock()}
        mock_cs.scene = {}
        controller._config_store = mock_cs
        controller._mode = Mode.AUTO
        controller.active_rule = None
        controller.active_target = "r1"

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
        mock_tray.bridge.register_select_target_handler.assert_called_once_with(
            controller.add_set_target_task,
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
        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, ModeSwitchTask)
        assert task.target_mode == Mode.MANUAL

    def test_add_set_target_task_enqueues(self, controller):
        controller.add_set_target_task(target="my_res")
        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, TargetSetTask)
        assert task.target == "my_res"

    def test_add_plot_canvas_task_uses_default_priority(self, controller):
        from wallpaper_auto.task import PlotCanvasTask

        controller.add_plot_canvas_task()
        prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, PlotCanvasTask)
        assert prio == 10  # default priority

    def test_add_plot_canvas_task_respects_explicit_priority(self, controller):
        from wallpaper_auto.task import PlotCanvasTask

        controller.add_plot_canvas_task(priority=3)
        prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, PlotCanvasTask)
        assert prio == 3


class TestWallpaperControllerTargetSetBranches:
    """TARGET_SET branches covering resource dispatch and the plot-canvas coalescing."""

    def _setup(self, controller, resources_per_monitor=None):
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = (
            resources_per_monitor or {}
        )
        controller._display_manager = MagicMock()
        return controller

    def test_target_set_dispatches_each_resource(self, controller):
        r1, r2 = MagicMock(), MagicMock()
        self._setup(controller, {"monA": r1, "monB": r2})

        controller._task_queue.put(_make_task(TargetSetTask(target="r1", matched_rule=None)))
        controller._task_queue.put(_make_task(QuitTask(), priority=10))
        controller._worker_loop()

        # Both displays got their resource via update_resource.
        controller._display_manager.update_resource.assert_any_call("monA", r1)
        controller._display_manager.update_resource.assert_any_call("monB", r2)
        assert controller.active_target == "r1"
        # No newer PLOT_CANVAS queued → plot_canvas() is called.
        controller._display_manager.plot_canvas.assert_called_once()

    def test_target_set_skips_plot_when_newer_canvas_queued(self, controller):
        """If a PLOT_CANVAS is already pending, the controller skips its own plot call."""
        from wallpaper_auto.task import PlotCanvasTask

        self._setup(controller, {"monA": MagicMock()})

        # Lower numbers = higher priority. PLOT_CANVAS at prio 3 sits ahead of
        # the TARGET_SET at prio 5 in the queue at the time the TARGET_SET is
        # processed.  Wait — that means PLOT_CANVAS is consumed first; we need
        # it queued AFTER TARGET_SET.  PriorityQueue serves lower numbers first,
        # so to put a task behind another we use a higher priority number.
        # TARGET_SET at prio 5, PLOT_CANVAS at prio 8, QUIT at prio 100.
        controller._task_queue.put(_make_task(TargetSetTask(target="r1", matched_rule=None), priority=5))
        controller._task_queue.put(_make_task(PlotCanvasTask(), priority=8))
        controller._task_queue.put(_make_task(QuitTask(), priority=100))

        with patch("wallpaper_auto.wallpaper_controller.logger") as mock_logger:
            controller._worker_loop()

        # The TARGET_SET should NOT have called plot_canvas (PLOT_CANVAS pending).
        # (PLOT_CANVAS itself does call plot_canvas once, so the final assert
        # below checks that exactly one call came from PLOT_CANVAS, not TARGET_SET.)
        assert controller._display_manager.plot_canvas.call_count == 1
        assert any(
            "Skipping canvas plot" in str(c)
            for c in mock_logger.debug.call_args_list
        )

    def test_plot_canvas_task_calls_display_manager(self, controller):
        from wallpaper_auto.task import PlotCanvasTask

        controller._display_manager = MagicMock()
        controller._task_queue.put(_make_task(PlotCanvasTask(), priority=5))
        controller._task_queue.put(_make_task(QuitTask(), priority=100))
        controller._worker_loop()

        controller._display_manager.plot_canvas.assert_called_once()


class TestWallpaperControllerUpdateDisplay:
    """update_display() reconciles live topology with the display manager."""

    def test_update_display_adds_new_and_removes_stale(self, controller):
        from wallpaper_auto.util.display_utils import DisplayInfo

        # Two displays are live but only one is currently managed.
        live = [
            DisplayInfo(
                model="m1",
                source_resolution=(10, 10),
                position=(0, 0),
                target_resolution=(10, 10),
                monitor_device_path="monA",
            ),
            DisplayInfo(
                model="m2",
                source_resolution=(10, 10),
                position=(0, 0),
                target_resolution=(10, 10),
                monitor_device_path="monB",
            ),
        ]
        controller._display_manager = MagicMock()
        # active_monitor_device_path is a property; configure the MagicMock to
        # return {"monA"} for any call to it.
        controller._display_manager.active_monitor_device_path = {"monA"}
        with patch(
            "wallpaper_auto.wallpaper_controller.get_display_info", return_value=live
        ):
            result = controller.update_display()

        controller._display_manager.add_display.assert_called_once_with("monB")
        # monA is still in both sets, so no remove.
        controller._display_manager.remove_display.assert_not_called()
        assert result == live

    def test_update_display_removes_unplugged(self, controller):
        from wallpaper_auto.util.display_utils import DisplayInfo

        live = [
            DisplayInfo(
                model="m1",
                source_resolution=(10, 10),
                position=(0, 0),
                target_resolution=(10, 10),
                monitor_device_path="monA",
            ),
        ]
        controller._display_manager = MagicMock()
        controller._display_manager.active_monitor_device_path = {"monA", "monB"}
        with patch(
            "wallpaper_auto.wallpaper_controller.get_display_info", return_value=live
        ):
            controller.update_display()

        controller._display_manager.add_display.assert_not_called()
        controller._display_manager.remove_display.assert_called_once_with("monB")


class TestWallpaperControllerAtDisplayChange:
    def test_at_display_change_enqueues_plot_canvas(self, controller):
        from wallpaper_auto.task import PlotCanvasTask

        with patch.object(controller, "add_plot_canvas_task") as mock_add:
            controller.at_display_change()
        mock_add.assert_called_once()
        # And it actually queues the right task.
        controller.add_plot_canvas_task()
        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, PlotCanvasTask)


class TestThreadSafeCounter:
    """The ThreadSafeCounter helper used to break ties in the priority queue."""

    def test_iter_returns_self(self):
        from wallpaper_auto.wallpaper_controller import ThreadSafeCounter

        c = ThreadSafeCounter()
        assert iter(c) is c

    def test_next_increments(self):
        from wallpaper_auto.wallpaper_controller import ThreadSafeCounter

        c = ThreadSafeCounter()
        assert next(c) == 0
        assert next(c) == 1
        assert next(c) == 2

    def test_start_value(self):
        from wallpaper_auto.wallpaper_controller import ThreadSafeCounter

        c = ThreadSafeCounter(start=10)
        assert next(c) == 10
        assert next(c) == 11


# ── start / stop lifecycle ─────────────────────────────────────────────────


def _safe_stop(controller):
    """Call stop() but ignore errors from unstarted components."""
    try:
        with patch.object(controller._display_manager, "stop"):
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
        with patch.object(controller._display_trigger, "deactivate"):
            with pytest.raises(RuntimeError, match="worker loop thread not start"):
                controller.stop()

    def test_stop_joins_thread_and_cleans_up(self, controller):
        _start_controller(controller)
        with patch.object(controller._display_manager, "stop"):
            controller.stop()
        assert controller._worker_loop_thread is None
        # After stop the queue should be processed; no leftover tasks.
        assert controller._task_queue.qsize() == 0
        _cleanup_worker(controller)

    def test_stop_deactivates_triggers_and_stops_display(self, controller):
        _start_controller(controller)
        with (
            patch.object(controller._trigger_manager, "deactivate") as mock_deact,
            patch.object(controller._display_manager, "stop") as mock_display_stop,
        ):
            controller.stop()
        mock_deact.assert_called_once()
        mock_display_stop.assert_called_once()
        _cleanup_worker(controller)

    @pytest.mark.parametrize("app", [MagicMock(), None], ids=["with_app", "without_app"])
    def test_stop_hides_tray_and_optionally_quits_app(self, controller, app):
        mock_tray = MagicMock()
        mock_tray._app = app
        controller._tray = mock_tray
        _start_controller(controller)
        with patch.object(controller._display_manager, "stop"):
            controller.stop()
        mock_tray.hide.assert_called_once()
        if app is not None:
            app.quit.assert_called_once()
        _cleanup_worker(controller)

    def test_stop_unregisters_shutdown_callback(self, controller):
        """stop() should unregister the at-shutdown callback to prevent restart leaks."""
        _start_controller(controller)
        with (
            patch("wallpaper_auto.wallpaper_controller.atshutdown") as mock_atsd,
            patch.object(controller._display_manager, "stop"),
        ):
            controller.stop()
        mock_atsd.unregister.assert_called_once_with(controller.at_shutdown)
        _cleanup_worker(controller)


# -- at_shutdown ----------------------------------------------------------


class TestWallpaperControllerAtShutdown:
    """at_shutdown() – shutdown handling."""

    def test_stops_display_manager(self, controller):
        """at_shutdown() should stop the display manager."""
        with patch.object(controller._display_manager, "stop") as mock_stop:
            controller.at_shutdown()
        mock_stop.assert_called_once()
