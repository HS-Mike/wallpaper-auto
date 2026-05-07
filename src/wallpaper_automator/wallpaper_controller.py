"""
Main wallpaper controller.

Coordinates the resource manager, trigger manager, and rule engine.
Owns the worker loop that processes mode-switch and resource-set tasks from the queue.
"""

import logging
import queue
import signal
import threading

from .config_store import ConfigStore
from .models import Rule
from .resource_manager import ResourceManager
from .rule_engine import RuleEngine
from .system_tray import WallpaperSwitchSystemTray
from .task import Mode, ModeSwitchTask, QuitTask, ResourceSetTask, Task, TaskType
from .trigger_manager import TriggerManager

logger = logging.getLogger(__name__)


class WallpaperController:
    active_rule: Rule | None

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._worker_loop_thread: threading.Thread | None = None
        self._task_queue: queue.Queue[Task] = queue.Queue()
        self.active_rule = None
        self._config_path: str | None = None
        self._config_store: ConfigStore = ConfigStore()
        self._resource_manager: ResourceManager = ResourceManager()
        self._trigger_manager: TriggerManager = TriggerManager()
        self._trigger_manager.add_callback(self.evaluate)
        self._rule_engine: RuleEngine = RuleEngine()

        # System tray
        self._tray: WallpaperSwitchSystemTray | None = None
        self._mode: Mode = Mode.UNSET

    def _worker_loop(self) -> None:
        logger.debug("worker loop thread start")
        while True:
            task = self._task_queue.get()

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

            elif task.type == TaskType.RESOURCE_SET:
                self._resource_manager.demount()
                self._resource_manager.mount(task.target_resource_id)

            self.update_system_tray()
            self._task_queue.task_done()

        logger.debug("worker loop thread exit")

    def load_config(self, config_path: str) -> None:
        """
        load and verify config from YAML config file
        init managers accordingly
        """
        self._config_store.load(config_path)
        self._resource_manager.init(self._config_store.resource)
        self._trigger_manager.init(self._config_store.trigger)
        self._rule_engine.init(self._config_store.rule)

    def update_system_tray(self) -> None:
        if self._tray is not None:
            self._tray.bridge.update_ui(
                self._resource_manager.resource_ids,
                self._mode,
                self.active_rule,
                self._resource_manager.active_resource_id,
            )

    def add_set_mode_task(self, mode: Mode) -> None:
        self._task_queue.put(ModeSwitchTask(target_mode=mode))

    def add_set_resource_id_task(self, resource_id: str) -> None:
        self._task_queue.put(ResourceSetTask(target_resource_id=resource_id))

    def evaluate(self) -> None:
        """
        callback function of trigger_manager
        evaluate condition according to rule, and then mount resoruce
        """
        self.active_rule = self._rule_engine.evaluate()
        if self.active_rule is None:
            resource_id = self._config_store.fallback_resource_id
        else:
            resource_id = self.active_rule.target

        if self._resource_manager.active_resource_id != resource_id:
            self.add_set_resource_id_task(resource_id=resource_id)

    def set_tray(self, tray: WallpaperSwitchSystemTray) -> None:
        """
        bind system try to controller
        """
        self._tray = tray
        self._tray.bridge.register_set_mode_handler(self.add_set_mode_task)
        self._tray.bridge.register_select_resource_handler(self.add_set_resource_id_task)
        self._tray.bridge.register_quit_handler(self.stop)
        self._tray.bridge.register_update_ui_handler(self.update_system_tray)

    def start(self) -> None:
        logger.info("wallpaper controller start")
        signal.signal(signal.SIGINT, lambda sig, frame: self.stop())
        signal.signal(signal.SIGTERM, lambda sig, frame: self.stop())

        self._mode = Mode.AUTO

        if self._tray is not None:
            self._tray.show()

        self._worker_loop_thread = threading.Thread(target=self._worker_loop)
        self._worker_loop_thread.start()
        self.evaluate()
        self._trigger_manager.activate()

    def stop(self) -> None:
        logger.info("wallpaper controller stop")
        if self._worker_loop_thread is None:
            raise RuntimeError("worker loop thread not start yet")
        self._task_queue.put(QuitTask())
        self._worker_loop_thread.join()
        self._worker_loop_thread = None
        self._trigger_manager.deactivate()
        self._resource_manager.demount()
        if self._tray is not None:
            app = self._tray._app
            self._tray.hide()
            if app is not None:
                app.quit()
