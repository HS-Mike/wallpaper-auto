"""
Main wallpaper controller.

Coordinates the resource manager, trigger manager, and rule engine.
Owns the work loop that processes mode-switch and resource-set tasks from the queue.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading

from PIL import Image

from . import at_system_shutdown
from .config_store import ConfigStore
from .display_manager import DisplayManager
from .models import Rule
from .resource.base_resource import BaseResource
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .system_tray import TrayMenuItem, WallpaperSwitchSystemTray
from .task import ApplySceneTask, Mode, ModeSwitchTask, QuitTask, Task, UpdateSceneTask
from .trigger.base_trigger import BaseTrigger
from .trigger.display_trigger import DisplayTrigger
from .trigger_manager import TriggerManager

logger = logging.getLogger(__name__)


# support up to 64K 16:9 resolution wallpapers (61,440x34,560)
# you may set to None to disable the limit
Image.MAX_IMAGE_PIXELS = 61440 * 34560


class WallpaperController:
    def __init__(self) -> None:
        self._work_loop_thread: threading.Thread | None = None
        self._task_queue: queue.PriorityQueue[tuple[int, int, Task]] = queue.PriorityQueue()
        self._task_counter = ThreadSafeCounter()
        self._config_store: ConfigStore = ConfigStore()
        self._resource_manager: ResourceManager = ResourceManager()
        self._display_manager: DisplayManager = DisplayManager()
        BaseResource.register_update_canvas(self._display_manager.update_canvas)
        BaseResource.register_plot_canvas(self.add_apply_scene_task)
        self._trigger_manager: TriggerManager = TriggerManager()
        self._trigger_manager.add_callback(self.evaluate)
        self._rule_engine: RuleEngine = RuleEngine()

        # System tray
        self._tray: WallpaperSwitchSystemTray | None = None
        self._mode: Mode = Mode.UNSET

        self._display_trigger = DisplayTrigger()
        self._display_trigger.add_callback(self.at_display_change)

        self._active_lock = threading.Lock()
        self.active_rule: Rule | None = None
        self.active_target: str | None = None

    def _work_loop(self) -> None:
        logger.debug("wallpaper controller work loop start")

        # Deprecated ApplySceneTasks, finished once the superseding render completes.
        deferred_plots: list[ApplySceneTask] = []

        while True:
            priority, count, task = self._task_queue.get()
            logger.debug(
                f"work loop task: {task.__class__.__name__} (id: {id(task)} priority: {priority})"
            )

            if isinstance(task, QuitTask):
                for deferred in deferred_plots:
                    deferred.mark_finish()
                deferred_plots.clear()
                task.mark_finish()
                self._task_queue.task_done()
                break

            elif isinstance(task, ModeSwitchTask):
                if task.target_mode == Mode.AUTO:
                    self._trigger_manager.resume()
                    self.evaluate()
                elif task.target_mode == Mode.MANUAL:
                    self._trigger_manager.pause()
                else:
                    raise RuntimeError(f"invalid mode {task.target_mode.name}")
                logger.info(f"wallpaper controler mode set to {task.target_mode.name}")
                self._mode = task.target_mode
                self.update_system_tray()
                task.mark_finish()

            elif isinstance(task, UpdateSceneTask):
                try:
                    display_info = self._display_manager.update_display()
                    if display_info is not None:
                        scenes = self._resource_manager.evaluate_target(
                            target=task.target, display_info=display_info
                        )
                        for scene in scenes:
                            self._display_manager.update_display_scene(
                                scene.display_id, scene.resource, scene.resolution, scene.scale
                            )
                        with self._active_lock:
                            self.active_target = task.target
                            self.active_rule = task.matched_rule
                        self.add_apply_scene_task()
                        self.update_system_tray()
                except Exception as e:
                    logger.exception(e)
                finally:
                    task.mark_finish()

            elif isinstance(task, ApplySceneTask):
                # A task takes exactly one of two mutually exclusive branches.
                with self._task_queue.mutex:
                    newest_queued = max(
                        (
                            (c, t)
                            for _p, c, t in self._task_queue.queue
                            if isinstance(t, ApplySceneTask)
                        ),
                        key=lambda pair: pair[0],
                        default=None,
                    )
                if newest_queued is not None and newest_queued[0] > count:
                    # Deprecated branch: a newer ApplySceneTask is already
                    # queued, so this task is skipped and its completion is
                    # deferred to the superseding render.
                    _, newest_task = newest_queued
                    logger.debug(
                        "ApplySceneTask (id: %d) deprecated by newer ApplySceneTask (id: %d)",
                        id(task),
                        id(newest_task),
                    )
                    deferred_plots.append(task)
                else:
                    # Renderer branch: this task is the newest, so it renders,
                    # then finishes the deprecated tasks it served. Plots
                    # enqueued while it ran are left in the queue: their
                    # canvas writes happened after this render read the buffer,
                    # so clearing them would silently drop a newer wallpaper.
                    rendered = False
                    try:
                        self._display_manager.update_display()
                        self._display_manager.apply_display_scene()
                        rendered = True
                    except Exception as e:
                        logger.exception(e)
                    if rendered:
                        for deferred in deferred_plots:
                            logger.debug(
                                "ApplySceneTask (id: %d) deprecated; finished",
                                id(deferred),
                            )
                            deferred.mark_finish()
                        deferred_plots.clear()
                    task.mark_finish()

            self._task_queue.task_done()

        logger.debug("wallpaper controller work loop stop")

    def load_config(self, config_path: str) -> None:
        """Load and verify the YAML config, initializing managers accordingly.

        Args:
            config_path: Path to the YAML config file.
        """
        self._config_store.load(config_path)

        self._trigger_manager.init(self._config_store.trigger)
        self._rule_engine.init(self._config_store.rule)

        cache_cfg = self._config_store.cache
        self._display_manager.init_cache(
            cache_path=self._config_store.cache_path,
            resize_enabled=cache_cfg.resize.enabled,
            max_size_bytes=cache_cfg.resize.max_size_mb * 1024 * 1024,
            evict_ratio=cache_cfg.resize.evict_ratio,
        )

    def update_system_tray(self) -> None:
        """Push the current menu state to the system tray via the bridge.

        Emits one :class:`TrayMenuItem` per configured resource and per
        configured scene (regardless of ``show``). The tray owns the
        rendering policy: it decides which items to render as selectable
        entries, which to render as a non-clickable active-target header,
        and which to omit.
        """
        if self._tray is None:
            return
        items: list[TrayMenuItem] = [
            TrayMenuItem(id=rid, kind="resource", show=rcfg.show)
            for rid, rcfg in self._config_store.resource.items()
        ]
        for sid, scene_cfg in self._config_store.scene.items():
            items.append(TrayMenuItem(id=sid, kind="scene", show=scene_cfg.show))
        with self._active_lock:
            active_rule_name = self.active_rule.name if self.active_rule is not None else None
            active_target = self.active_target
        self._tray.bridge.update_ui(
            items,
            self._mode,
            active_rule_name,
            active_target,
        )

    def add_quit_task(self, priority: int | None = None) -> QuitTask:
        t = QuitTask()
        priority = 0 if priority is None else priority
        self._task_queue.put((priority, next(self._task_counter), t))
        return t

    def add_set_mode_task(self, mode: Mode, priority: int | None = None) -> ModeSwitchTask:
        t = ModeSwitchTask(target_mode=mode)
        priority = 5 if priority is None else priority
        self._task_queue.put((priority, next(self._task_counter), t))
        return t

    def add_update_scene_task(
        self,
        target: str,
        matched_rule: Rule | None = None,
        priority: int | None = None,
    ) -> UpdateSceneTask:
        t = UpdateSceneTask(
            target=target,
            matched_rule=matched_rule,
        )
        priority = 5 if priority is None else priority
        self._task_queue.put((priority, next(self._task_counter), t))
        return t

    def add_apply_scene_task(self, priority: int | None = None) -> ApplySceneTask:
        """Enqueue a :class:`ApplySceneTask` for the work loop to render.

        No coalescing happens here — every request is enqueued unconditionally.
        The work loop owns the decision: if a newer ``ApplySceneTask`` is
        already queued, the older one is deprecated and skipped (the newer one
        renders the latest buffered canvas).
        """
        priority = 10 if priority is None else priority
        t = ApplySceneTask()
        self._task_queue.put((priority, next(self._task_counter), t))
        return t

    def at_display_change(self, _trigger: BaseTrigger) -> None:
        """Re-apply the active target when the display topology changes.

        A display change may add/remove monitors. Re-resolving the current
        active target against the new topology assigns a resource to any
        newly-connected display so the SPAN composite covers the full virtual
        desktop; a bare re-plot leaves the new display without a canvas, so
        Windows stretches the old composite across it. Falls back to a plain
        re-plot when no target is active yet.
        """
        logger.info("Detect display change.")
        with self._active_lock:
            target = self.active_target
            rule = self.active_rule
        if target is not None:
            self.add_update_scene_task(target=target, matched_rule=rule)
        else:
            self.add_apply_scene_task()

    def evaluate(self) -> None:
        """Re-evaluate rules against current conditions and enqueue the resulting target.

        Called by :class:`TriggerManager` when a trigger fires; falls back to
        the configured fallback target when no rule matches.
        """
        active_rule = self._rule_engine.evaluate()
        if active_rule is None:
            target = self._config_store.fallback_target
        else:
            target = active_rule.target

        self.add_update_scene_task(target=target, matched_rule=active_rule)

    def set_tray(self, tray: WallpaperSwitchSystemTray) -> None:
        """Bind the system tray to the controller.

        Registers the tray's mode/target/quit/update handlers against the
        controller.

        Args:
            tray: The system tray to bind.
        """
        self._tray = tray

        def _set_mode(mode: Mode) -> None:
            self.add_set_mode_task(mode)

        def _set_target(target: str) -> None:
            self.add_update_scene_task(target=target)

        self._tray.bridge.register_set_mode_handler(_set_mode)
        self._tray.bridge.register_select_target_handler(_set_target)
        self._tray.bridge.register_quit_handler(self.stop)
        self._tray.bridge.register_update_ui_handler(self.update_system_tray)

    def at_shutdown(self) -> None:
        target = self._config_store.at_shutdown_target
        if target is None:
            return
        logger.info(f"apply at shutdown target {target}")
        update_task = self.add_update_scene_task(target=target, matched_rule=None, priority=1)
        plot_task = self.add_apply_scene_task(priority=2)
        update_task.wait(timeout=5)
        plot_task.wait(timeout=5)

    def start(self) -> None:

        self._mode = Mode.AUTO

        if self._tray is not None:
            self._tray.show()

        self._display_trigger.start()
        self._display_manager.start()
        self._trigger_manager.activate()

        self._work_loop_thread = threading.Thread(target=self._work_loop, daemon=True)
        self._work_loop_thread.start()
        self.evaluate()
        at_system_shutdown.register(self.at_shutdown)

    def stop(self) -> None:
        if self._work_loop_thread is None:
            raise RuntimeError("work loop thread not start yet")

        self.at_shutdown()
        at_system_shutdown.unregister(self.at_shutdown)

        self.add_quit_task()
        self._work_loop_thread.join()
        self._work_loop_thread = None

        self._trigger_manager.deactivate()
        restore_original = self._config_store.at_shutdown_target is None
        self._display_manager.stop(restore_original=restore_original)
        self._display_trigger.stop()

        if self._tray is not None:
            app = self._tray._app
            self._tray.hide()
            if app is not None:
                app.quit()


class ThreadSafeCounter:
    def __init__(self, start: int = 0) -> None:
        self._counter: itertools.count[int] = itertools.count(start)
        self._lock = threading.Lock()

    def __iter__(self) -> ThreadSafeCounter:
        return self

    def __next__(self) -> int:
        with self._lock:
            return next(self._counter)
