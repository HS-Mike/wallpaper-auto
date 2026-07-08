"""
Main wallpaper controller.

Coordinates the resource manager, trigger manager, and rule engine.
Owns the worker loop that processes mode-switch and resource-set tasks from the queue.
"""

import itertools
import logging
import queue
import signal
import threading

from PIL import Image

from . import atshutdown
from .config_store import ConfigStore
from .display_manager import DisplayManager
from .models import Rule
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .system_tray import WallpaperSwitchSystemTray
from .task import Mode, ModeSwitchTask, PlotCanvasTask, QuitTask, TargetSetTask, Task, TaskType
from .trigger.display_trigger import DisplayTrigger
from .trigger_manager import TriggerManager
from .util.display_utils import DisplayInfo, get_display_info

logger = logging.getLogger(__name__)


# support up to 64K 16:9 resolution wallpapers (30,720x17,280)
# you may set to None to disable the limit
Image.MAX_IMAGE_PIXELS = 61440 * 34560
# Image.MAX_IMAGE_PIXELS = None


class WallpaperController:

    def __init__(self) -> None:
        self._worker_loop_thread: threading.Thread | None = None
        self._task_queue: queue.PriorityQueue[tuple[int, int, Task]] = queue.PriorityQueue()
        self._task_counter = ThreadSafeCounter()
        self._config_store: ConfigStore = ConfigStore()
        self._resource_manager: ResourceManager = ResourceManager()
        self._display_manager: DisplayManager = DisplayManager()
        self._trigger_manager: TriggerManager = TriggerManager()
        self._trigger_manager.add_callback(self.evaluate)
        self._rule_engine: RuleEngine = RuleEngine()

        # System tray
        self._tray: WallpaperSwitchSystemTray | None = None
        self._mode: Mode = Mode.UNSET

        self._display_trigger = DisplayTrigger()
        self._display_trigger.add_callback(self.at_display_change)

        self.active_rule: Rule | None = None
        self.active_target: str | None = None

    def _worker_loop(self) -> None:
        logger.debug("worker loop thread start")
        while True:
            _priority, _count, task = self._task_queue.get()

            if task.type == TaskType.QUIT:
                logger.debug("worker loop thread receive QUIT signal.")
                break

            elif task.type == TaskType.MODE_SWITCH:
                if task.target_mode == Mode.AUTO:
                    self._trigger_manager.resume()
                    self.evaluate()
                elif task.target_mode == Mode.MANUAL:
                    self._trigger_manager.pause()
                else:
                    raise RuntimeError(f"invalid mode {task.target_mode.name}")
                logger.info(f"mode: {task.target_mode.name}")
                self._mode = task.target_mode

            elif task.type == TaskType.TARGET_SET:
                self.update_display()
                resources = self._resource_manager.evaluate_target(task.target)
                for p, r in resources.items():
                    self._display_manager.update_resource(p, r)
                self.active_target = task.target
                self.active_rule = task.matched_rule
                with self._task_queue.mutex:
                    has_newer_update = any(
                        t.type == TaskType.PLOT_CANVAS
                        for _p, _c, t in self._task_queue.queue
                    )
                if not has_newer_update:
                    self._display_manager.plot_canvas()
                else:
                    logger.debug("Skipping canvas plot; a newer update task is already queued.")

            elif task.type == TaskType.PLOT_CANVAS:
                self.update_display()
                self._display_manager.plot_canvas()

            self.update_system_tray()
            self._task_queue.task_done()

        logger.debug("worker loop thread exit")

    def load_config(self, config_path: str) -> None:
        """
        load and verify config from YAML config file
        init managers accordingly
        """
        self._config_store.load(config_path)

        self._trigger_manager.init(self._config_store.trigger)
        self._rule_engine.init(self._config_store.rule)

    def update_system_tray(self) -> None:
        if self._tray is not None:
            self._tray.bridge.update_ui(
                list(self._config_store.resource.keys()) + list(self._config_store.scene.keys()),
                self._mode,
                self.active_rule.name if self.active_rule is not None else None,
                self.active_target,
            )

    def add_quit_task(self, priority: int | None = None) -> None:
        t = QuitTask()
        priority = 0 if priority is None else priority
        self._task_queue.put((priority, next(self._task_counter), t))

    def add_set_mode_task(self, mode: Mode, priority: int | None = None) -> None:
        t = ModeSwitchTask(target_mode=mode)
        priority = 5 if priority is None else priority
        self._task_queue.put((priority, next(self._task_counter), t))

    def add_set_target_task(
        self, target: str, matched_rule: Rule | None = None, priority: int | None = None
    ) -> None:
        t = TargetSetTask(target=target, matched_rule=matched_rule)
        priority = 5 if priority is None else priority
        self._task_queue.put((priority, next(self._task_counter), t))

    def add_plot_canvas_task(self, priority: int | None = None) -> None:
        t = PlotCanvasTask()
        priority = 10 if priority is None else priority
        self._task_queue.put((priority, next(self._task_counter), t))

    def update_display(self) -> list[DisplayInfo]:
        curr_display_info = get_display_info()
        curr_monitor_device_path = {i.monitor_device_path for i in curr_display_info}
        active_monitor_device_path = self._display_manager.active_monitor_device_path
        plugged_display = curr_monitor_device_path - active_monitor_device_path
        unplugged_display = active_monitor_device_path - curr_monitor_device_path
        for i in plugged_display:
            self._display_manager.add_display(i)
        for i in unplugged_display:
            self._display_manager.remove_display(i)
        return curr_display_info

    def at_display_change(self):
        logger.info("Detect display change.")
        self.add_plot_canvas_task()

    def evaluate(self) -> None:
        """
        callback function of trigger_manager
        evaluate condition according to rule, and then mount resoruce
        """
        active_rule = self._rule_engine.evaluate()
        if active_rule is None:
            target = self._config_store.fallback_target
        else:
            target = active_rule.target

        self.add_set_target_task(target=target, matched_rule=active_rule)

    def set_tray(self, tray: WallpaperSwitchSystemTray) -> None:
        """
        bind system try to controller
        """
        self._tray = tray
        self._tray.bridge.register_set_mode_handler(self.add_set_mode_task)
        self._tray.bridge.register_select_target_handler(self.add_set_target_task)
        self._tray.bridge.register_quit_handler(self.stop)
        self._tray.bridge.register_update_ui_handler(self.update_system_tray)

    def at_shutdown(self) -> None:
        self._display_manager.stop()

    def start(self) -> None:
        logger.info("wallpaper controller start")
        signal.signal(signal.SIGINT, lambda sig, frame: self.stop())
        signal.signal(signal.SIGTERM, lambda sig, frame: self.stop())

        self._mode = Mode.AUTO

        if self._tray is not None:
            self._tray.show()

        self._display_trigger.start()
        self._worker_loop_thread = threading.Thread(target=self._worker_loop)
        self._worker_loop_thread.start()
        self.evaluate()
        self._trigger_manager.activate()
        atshutdown.register(self.at_shutdown)

    def stop(self) -> None:
        logger.info("wallpaper controller stop")
        self._display_trigger.stop()
        if self._worker_loop_thread is None:
            raise RuntimeError("worker loop thread not start yet")
        self.add_quit_task()
        self._worker_loop_thread.join()
        self._worker_loop_thread = None
        self._trigger_manager.deactivate()
        self._display_manager.stop()
        atshutdown.unregister(self.at_shutdown)
        if self._tray is not None:
            app = self._tray._app
            self._tray.hide()
            if app is not None:
                app.quit()


class ThreadSafeCounter:
    def __init__(self, start=0):
        self._counter = itertools.count(start)
        self._lock = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self) -> int:
        with self._lock:
            return next(self._counter)

