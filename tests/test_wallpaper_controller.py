"""Tests for wallpaper_controller.py — controller lifecycle and task processing."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.models import Rule
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.task import ApplySceneTask, Mode, ModeSwitchTask, QuitTask, UpdateSceneTask
from wallpaper_auto.wallpaper_controller import WallpaperController


def _mock_config_store(controller, **overrides):
    """Replace ``_config_store`` with a MagicMock with sensible defaults."""
    mock_cs = MagicMock()
    mock_cs.fallback_target = "fallback"
    mock_cs.at_shutdown_target = None
    for k, v in overrides.items():
        setattr(mock_cs, k, v)
    controller._config_store = mock_cs


def _start_controller(controller):
    """Start the controller with all real side-effects patched out."""
    _mock_config_store(controller)
    with (
        patch("signal.signal"),
        patch.object(controller, "evaluate"),
        patch.object(controller, "_display_trigger"),
        patch.object(controller._display_manager, "start"),
        patch.object(controller._trigger_manager, "activate"),
    ):
        controller.start()


def _make_task(task, priority=5, counter=0):
    """Wrap a task in the (priority, counter, task) tuple expected by PriorityQueue."""
    return (priority, counter, task)


def _quit_after(*tasks):
    """Append a QuitTask after *tasks for terminating the work loop."""
    return [*tasks, _make_task(QuitTask(), priority=100)]


def _cleanup_work(controller):
    """Safety net: kill the work thread if ``stop()`` was never reached."""
    if controller._work_loop_thread is not None:
        controller._task_queue.put(_make_task(QuitTask(), priority=0))
        controller._work_loop_thread.join()


@pytest.fixture
def controller():
    return WallpaperController()


class TestWallpaperControllerInit:
    """WallpaperController.__init__ and default state."""

    @pytest.mark.parametrize(
        "attr,expected",
        [
            ("_mode", Mode.UNSET),
            ("active_rule", None),
            ("_work_loop_thread", None),
            ("_tray", None),
        ],
    )
    def test_default_state(self, controller, attr, expected):
        assert getattr(controller, attr) == expected

    def test_evaluate_registered_as_trigger_callback(self, controller):
        assert controller.evaluate in controller._trigger_manager._callbacks

    def test_registers_canvas_callbacks_on_resource_class(self, monkeypatch):
        """Controller __init__ registers the class-wide canvas callbacks."""
        registered: dict[str, object] = {}
        monkeypatch.setattr(
            BaseResource,
            "register_update_canvas",
            lambda cb: registered.setdefault("update_canvas", cb),
        )
        monkeypatch.setattr(
            BaseResource,
            "register_plot_canvas",
            lambda cb: registered.setdefault("plot_canvas", cb),
        )

        controller = WallpaperController()

        assert registered["update_canvas"] == controller._display_manager.update_canvas
        assert registered["plot_canvas"] == controller.add_apply_scene_task


class TestWallpaperControllerLoadConfig:
    """WallpaperController.load_config() delegates to sub-managers."""

    @pytest.fixture
    def _mock_cs_with_triggers(self, controller):
        _mock_config_store(controller, trigger=[MagicMock()], rule=[MagicMock()])

    def test_load_config_calls_config_store_load(self, controller, _mock_cs_with_triggers):
        with (
            patch.object(controller._trigger_manager, "init"),
            patch.object(controller._rule_engine, "init"),
        ):
            controller.load_config("some/path.yaml")

        controller._config_store.load.assert_called_once_with("some/path.yaml")

    def test_load_config_inits_trigger_manager_and_rule_engine(
        self, controller, _mock_cs_with_triggers
    ):
        with (
            patch.object(controller._trigger_manager, "init") as tm_init,
            patch.object(controller._rule_engine, "init") as re_init,
        ):
            controller.load_config("p.yaml")

        tm_init.assert_called_once_with(controller._config_store.trigger)
        re_init.assert_called_once_with(controller._config_store.rule)

    def test_load_config_with_empty_lists_does_not_crash(self, controller):
        """load_config handles empty trigger/rule lists gracefully."""
        _mock_config_store(controller, trigger=[], rule=[])
        with (
            patch.object(controller._trigger_manager, "init") as tm_init,
            patch.object(controller._rule_engine, "init") as re_init,
        ):
            controller.load_config("p.yaml")

        tm_init.assert_called_once_with([])
        re_init.assert_called_once_with([])


class TestWallpaperControllerWorkLoop:
    """_work_loop() processes tasks from the queue until it sees a QUIT."""

    def test_quit_breaks_loop(self, controller):
        """A single QUIT task causes the loop to exit cleanly."""
        controller._task_queue.put(_make_task(QuitTask(), priority=0))
        controller._work_loop()
        # If we get here without hanging, the test passes.

    def test_mode_switch_auto_resumes_triggers(self, controller):
        controller._trigger_manager = MagicMock()
        _mock_config_store(controller)
        controller._display_manager = MagicMock()
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = []
        for t in _quit_after(_make_task(ModeSwitchTask(target_mode=Mode.AUTO))):
            controller._task_queue.put(t)

        controller._work_loop()

        controller._trigger_manager.resume.assert_called_once()
        assert controller._mode == Mode.AUTO

    def test_mode_switch_manual_pauses_triggers(self, controller):
        controller._trigger_manager = MagicMock()
        for t in _quit_after(_make_task(ModeSwitchTask(target_mode=Mode.MANUAL))):
            controller._task_queue.put(t)

        controller._work_loop()

        controller._trigger_manager.pause.assert_called_once()
        assert controller._mode == Mode.MANUAL

    def test_mode_switch_invalid_mode_raises(self, controller):
        for t in _quit_after(_make_task(ModeSwitchTask(target_mode=Mode.UNSET))):
            controller._task_queue.put(t)

        with pytest.raises(RuntimeError, match="invalid mode"):
            controller._work_loop()

    def test_target_set_calls_evaluate_target(self, controller):
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = []
        controller._display_manager = MagicMock()
        controller._display_manager.update_display.return_value = []
        for t in _quit_after(_make_task(UpdateSceneTask(target="res_x", matched_rule=None))):
            controller._task_queue.put(t)

        controller._work_loop()

        controller._resource_manager.evaluate_target.assert_called_once_with(
            target="res_x", display_info=[]
        )

    def test_update_system_tray_called_after_each_non_quit_task(self, controller):
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = []
        controller._display_manager = MagicMock()
        for t in _quit_after(_make_task(UpdateSceneTask(target="r1", matched_rule=None))):
            controller._task_queue.put(t)

        with patch.object(controller, "update_system_tray") as mock_update:
            controller._work_loop()

        # Called after TARGET_SET processing, *not* after QUIT.
        mock_update.assert_called_once()


class TestWallpaperControllerEvaluate:
    """evaluate() – condition evaluation & resource dispatch."""

    def test_matching_rule_enqueues_target_with_rule(self, controller):
        rule = MagicMock(spec=Rule)
        rule.target = "work_res"

        controller._rule_engine.evaluate = MagicMock(return_value=rule)
        _mock_config_store(controller)

        controller.evaluate()

        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, UpdateSceneTask)
        assert task.target == "work_res"
        assert task.matched_rule is rule

    def test_no_matching_rule_uses_fallback(self, controller):
        controller._rule_engine.evaluate = MagicMock(return_value=None)
        _mock_config_store(controller, fallback_target="fallback_res")

        controller.evaluate()

        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, UpdateSceneTask)
        assert task.target == "fallback_res"
        assert task.matched_rule is None

    def test_evaluate_with_null_fallback_does_not_crash(self, controller):
        """evaluate() handles fallback_target=None gracefully."""
        controller._rule_engine.evaluate = MagicMock(return_value=None)
        _mock_config_store(controller, fallback_target=None)

        controller.evaluate()  # should not raise

        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert task.target is None


class TestWallpaperControllerUpdateSystemTray:
    """update_system_tray() delegates to the tray bridge."""

    def test_delegates_to_bridge_when_tray_set(self, controller):
        mock_tray = MagicMock()
        controller._tray = mock_tray

        _mock_config_store(controller, resource={"r1": MagicMock(), "r2": MagicMock()}, scene={})
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

    def test_passes_rule_name_when_active_rule_is_set(self, controller):
        rule = MagicMock(spec=Rule)
        rule.name = "my rule"
        mock_tray = MagicMock()
        controller._tray = mock_tray
        _mock_config_store(controller, resource={"r1": MagicMock()}, scene={})
        controller._mode = Mode.AUTO
        controller.active_rule = rule
        controller.active_target = "r1"

        controller.update_system_tray()

        mock_tray.bridge.update_ui.assert_called_once_with(
            ["r1"],
            Mode.AUTO,
            "my rule",
            "r1",
        )

    def test_noop_when_tray_is_none(self, controller):
        controller._tray = None
        controller.update_system_tray()  # should not raise


class TestWallpaperControllerSetTray:
    """set_tray() wires up all tray → controller callbacks."""

    def test_registers_all_handlers(self, controller):
        mock_tray = MagicMock()
        controller.set_tray(mock_tray)

        assert controller._tray is mock_tray
        mock_tray.bridge.register_set_mode_handler.assert_called_once()
        mock_tray.bridge.register_select_target_handler.assert_called_once()
        mock_tray.bridge.register_quit_handler.assert_called_once()
        mock_tray.bridge.register_update_ui_handler.assert_called_once()

    def test_set_mode_handler_wraps_add_set_mode_task(self, controller):
        mock_tray = MagicMock()
        controller.set_tray(mock_tray)
        handler = mock_tray.bridge.register_set_mode_handler.call_args[0][0]
        handler(Mode.MANUAL)
        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, ModeSwitchTask)
        assert task.target_mode == Mode.MANUAL

    def test_select_target_handler_wraps_add_update_scene_task(self, controller):
        mock_tray = MagicMock()
        controller.set_tray(mock_tray)
        handler = mock_tray.bridge.register_select_target_handler.call_args[0][0]
        handler("some_target")
        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, UpdateSceneTask)
        assert task.target == "some_target"


class TestWallpaperControllerTaskHelpers:
    """add_set_mode_task / add_update_scene_task enqueue correct tasks."""

    def test_add_set_mode_task_enqueues(self, controller):
        controller.add_set_mode_task(Mode.MANUAL)
        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, ModeSwitchTask)
        assert task.target_mode == Mode.MANUAL

    def test_add_update_scene_task_enqueues(self, controller):
        controller.add_update_scene_task(target="my_res")
        _prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, UpdateSceneTask)
        assert task.target == "my_res"

    def test_add_apply_scene_task_respects_explicit_priority(self, controller):
        controller.add_apply_scene_task(priority=3)
        prio, _cnt, task = controller._task_queue.get_nowait()
        assert isinstance(task, ApplySceneTask)
        assert prio == 3


class TestWallpaperControllerTargetSetBranches:
    """TARGET_SET branches covering resource dispatch and the plot-canvas coalescing."""

    def _setup(self, controller, targets=None):
        controller._resource_manager = MagicMock()
        controller._resource_manager.evaluate_target.return_value = (
            targets if targets is not None else []
        )
        controller._display_manager = MagicMock()
        controller._display_manager.update_display.return_value = []
        # Mirror the real apply_display_scene(), which composites (plot_canvas) at the end.
        controller._display_manager.apply_display_scene.side_effect = lambda: (
            controller._display_manager.plot_canvas()
        )
        return controller

    def test_target_set_dispatches_each_resource(self, controller):
        from wallpaper_auto.resource_manager import DisplayScene
        from wallpaper_auto.util.display_util import DisplayId

        t1 = DisplayScene(
            display_id=DisplayId(1), resource=MagicMock(name="r1"), resolution=None, scale=None
        )
        t2 = DisplayScene(
            display_id=DisplayId(2), resource=MagicMock(name="r2"), resolution=None, scale=None
        )
        self._setup(controller, [t1, t2])

        for t in _quit_after(_make_task(UpdateSceneTask(target="r1", matched_rule=None))):
            controller._task_queue.put(t)
        controller._work_loop()

        controller._display_manager.update_display_scene.assert_any_call(
            t1.display_id, t1.resource, t1.resolution, t1.scale
        )
        controller._display_manager.update_display_scene.assert_any_call(
            t2.display_id, t2.resource, t2.resolution, t2.scale
        )
        assert controller.active_target == "r1"
        controller._display_manager.plot_canvas.assert_called_once()

    def test_target_set_skips_plot_when_newer_canvas_queued(self, controller):
        """If a PLOT_CANVAS is already pending, the controller skips its own plot call."""
        self._setup(controller, [MagicMock()])

        for t in _quit_after(
            _make_task(ApplySceneTask(), priority=8),
            _make_task(UpdateSceneTask(target="r1", matched_rule=None), priority=5),
        ):
            controller._task_queue.put(t)

        with patch("wallpaper_auto.wallpaper_controller.logger") as mock_logger:
            controller._work_loop()

        assert controller._display_manager.plot_canvas.call_count == 1
        assert any("deprecated by newer" in str(c) for c in mock_logger.debug.call_args_list)

    def test_target_set_error_is_logged(self, controller):
        """An exception while resolving a target is logged; the task still finishes."""
        self._setup(controller)
        controller._resource_manager.evaluate_target.side_effect = RuntimeError("boom")

        for t in _quit_after(_make_task(UpdateSceneTask(target="r1", matched_rule=None))):
            controller._task_queue.put(t)

        with patch("wallpaper_auto.wallpaper_controller.logger") as mock_logger:
            controller._work_loop()

        mock_logger.exception.assert_called_once()

    def test_render_error_is_logged(self, controller):
        """An exception during rendering is logged; the task still finishes."""
        self._setup(controller)
        controller._display_manager.apply_display_scene.side_effect = RuntimeError("boom")

        for t in _quit_after(_make_task(ApplySceneTask(), priority=5)):
            controller._task_queue.put(t)

        with patch("wallpaper_auto.wallpaper_controller.logger") as mock_logger:
            controller._work_loop()

        mock_logger.exception.assert_called_once()

    def test_older_plot_is_deprecated_by_newer(self, controller):
        """A plot superseded by a newer queued plot is deprecated, not rendered."""
        self._setup(controller)
        older = ApplySceneTask()
        newer = ApplySceneTask()

        for t in _quit_after(
            _make_task(older, priority=5, counter=0),
            _make_task(newer, priority=10, counter=1),
        ):
            controller._task_queue.put(t)

        with patch("wallpaper_auto.wallpaper_controller.logger") as mock_logger:
            controller._work_loop()

        assert any("deprecated by newer" in str(c) for c in mock_logger.debug.call_args_list)
        # Only the newest plot renders; the deprecated one never reaches the renderer.
        assert controller._display_manager.plot_canvas.call_count == 1
        assert older.wait(timeout=0)
        assert newer.wait(timeout=0)

    def test_renderer_finishes_deprecated_and_pops_redundant_plots(self, controller):
        """The newest plot renders, finishing deprecated ones and popping redundant ones."""
        self._setup(controller)
        deprecated = ApplySceneTask()
        renderer = ApplySceneTask()
        redundant = ApplySceneTask()

        def _enqueue_redundant_plot() -> None:
            controller._task_queue.put(_make_task(redundant, priority=10, counter=2))

        controller._display_manager.apply_display_scene.side_effect = _enqueue_redundant_plot

        controller._task_queue.put(_make_task(deprecated, priority=5, counter=0))
        controller._task_queue.put(_make_task(renderer, priority=10, counter=1))

        thread = threading.Thread(target=controller._work_loop)
        thread.start()
        try:
            # The renderer marks itself finished only after the cleanup; wait for
            # that, then enqueue QUIT so the loop exits instead of blocking.
            assert renderer.wait(timeout=5)
            controller._task_queue.put(_make_task(QuitTask(), priority=100))
            thread.join(timeout=5)
        finally:
            if thread.is_alive():
                controller._task_queue.put(_make_task(QuitTask(), priority=0))
                thread.join(timeout=1)

        assert not thread.is_alive()
        assert deprecated.wait(timeout=0)
        assert renderer.wait(timeout=0)
        assert redundant.wait(timeout=0)
        assert controller._task_queue.qsize() == 0


class TestWallpaperControllerAtDisplayChange:
    def test_at_display_change_enqueues_plot_canvas(self, controller):
        with patch.object(controller, "add_apply_scene_task") as mock_add:
            controller.at_display_change(MagicMock())
        mock_add.assert_called_once()


class TestThreadSafeCounter:
    """The ThreadSafeCounter helper used to break ties in the priority queue."""

    def test_next_increments(self):
        from wallpaper_auto.wallpaper_controller import ThreadSafeCounter

        c = ThreadSafeCounter()
        assert next(c) == 0
        assert next(c) == 1
        assert next(c) == 2

    def test_iter_returns_self(self):
        from wallpaper_auto.wallpaper_controller import ThreadSafeCounter

        assert iter(ThreadSafeCounter()) is not None

    def test_start_value(self):
        from wallpaper_auto.wallpaper_controller import ThreadSafeCounter

        c = ThreadSafeCounter(start=10)
        assert next(c) == 10
        assert next(c) == 11


class TestWallpaperControllerLifecycle:
    """start() and stop() – controller lifecycle."""

    def test_starts_work_thread_and_sets_mode_auto(self, controller):
        _start_controller(controller)
        assert controller._work_loop_thread is not None
        assert controller._work_loop_thread.is_alive()
        assert controller._mode == Mode.AUTO
        with patch.object(controller._display_manager, "stop"):
            controller.stop()

    def test_shows_tray_when_set(self, controller):
        mock_tray = MagicMock()
        controller._tray = mock_tray
        _start_controller(controller)
        mock_tray.show.assert_called_once()
        with patch.object(controller._display_manager, "stop"):
            controller.stop()

    def test_stop_raises_when_thread_not_started(self, controller):
        with patch.object(controller._display_trigger, "stop"):
            with pytest.raises(RuntimeError, match="work loop thread not start"):
                controller.stop()

    def test_stop_joins_thread_and_cleans_up(self, controller):
        _start_controller(controller)
        with patch.object(controller._display_manager, "stop"):
            controller.stop()
        assert controller._work_loop_thread is None
        assert controller._task_queue.qsize() == 0
        _cleanup_work(controller)

    def test_stop_deactivates_triggers_and_stops_display(self, controller):
        _start_controller(controller)
        with (
            patch.object(controller._trigger_manager, "deactivate") as mock_deact,
            patch.object(controller._display_manager, "stop") as mock_display_stop,
        ):
            controller.stop()
        mock_deact.assert_called_once()
        mock_display_stop.assert_called_once()
        _cleanup_work(controller)

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
        _cleanup_work(controller)

    def test_stop_unregisters_shutdown_callback(self, controller):
        """stop() should unregister the at-shutdown callback to prevent restart leaks."""
        _start_controller(controller)
        with (
            patch("wallpaper_auto.wallpaper_controller.at_system_shutdown") as mock_atsd,
            patch.object(controller._display_manager, "stop"),
        ):
            controller.stop()
        mock_atsd.unregister.assert_called_once_with(controller.at_shutdown)
        _cleanup_work(controller)

    def test_stop_called_twice_raises_on_second_call(self, controller):
        """After a successful stop(), a second stop() raises RuntimeError."""
        _start_controller(controller)
        with patch.object(controller._display_manager, "stop"):
            controller.stop()
        with patch.object(controller._display_manager, "stop"):
            with pytest.raises(RuntimeError, match="work loop thread not start"):
                controller.stop()
        _cleanup_work(controller)


class TestWallpaperControllerAtShutdown:
    """at_shutdown() – shutdown handling."""

    def test_skips_when_no_target_configured(self, controller):
        """at_shutdown() does nothing when no shutdown resource is configured."""
        _mock_config_store(controller, at_shutdown_target=None)
        controller.at_shutdown()
        # No crash = success

    def test_queues_target_task_on_shutdown(self, controller):
        """at_shutdown() queues an UpdateSceneTask at priority 1."""
        _mock_config_store(controller, at_shutdown_target="shutdown_res")
        with (
            patch.object(controller, "add_update_scene_task") as mock_add,
            patch("threading.Event.wait"),
        ):
            controller.at_shutdown()
        mock_add.assert_called_once_with(
            target="shutdown_res",
            matched_rule=None,
            priority=1,
        )
