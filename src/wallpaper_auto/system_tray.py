"""
System tray UI.

Provides a PySide6-based system tray icon and menu for manual wallpaper
selection, mode switching (AUTO/MANUAL), and shutdown.
"""

import logging
import sys
from collections.abc import Callable
from importlib.resources import files

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .models import Rule
from .task import Mode

logger = logging.getLogger(__name__)


AUTO_MODE_COLOR = "#35FF89FF"
MANUAL_MODE_COLOR = "#E9B200FF"


class SystemTrayBridge(QObject):
    """
    Full-duplex compatibility layer.
    """

    # Signal: Logic layer -> UI layer (for updating the interface)
    # Params: resource_ids, mode, active_rule, active_resource_id
    update_ui_signal = Signal(list, object, object, object)

    def __init__(self) -> None:
        super().__init__()
        # Callback container: UI layer -> Logic layer (for executing actions)
        self._on_set_mode_handler: Callable[[Mode], None] | None = None
        self._on_select_resource_handler: Callable[[str], None] | None = None
        self._on_quit_handler: Callable[[], None] | None = None
        self._on_update_ui_handler: Callable[[], None] | None = None

    # --- external communication to tray (Thread-Safe) ---

    def update_ui(self, r_ids: object, mode: object, rule: object, active_id: str | None) -> None:
        self.update_ui_signal.emit(r_ids, mode, rule, active_id)

    def register_set_mode_handler(self, cb: Callable[[Mode], None]) -> None:
        self._on_set_mode_handler = cb

    def register_select_resource_handler(self, cb: Callable[[str], None]) -> None:
        self._on_select_resource_handler = cb

    def register_quit_handler(self, cb: Callable[[], None]) -> None:
        self._on_quit_handler = cb

    def register_update_ui_handler(self, cb: Callable[[], None]) -> None:
        self._on_update_ui_handler = cb

    # --- internal communication to external (Thread-Safe) ---

    def request_select_resource(self, resource_id: str) -> None:
        if self._on_select_resource_handler:
            self._on_select_resource_handler(resource_id)

    def request_set_mode(self, mode: Mode) -> None:
        if self._on_set_mode_handler:
            self._on_set_mode_handler(mode)

    def request_update_ui(self) -> None:
        if self._on_update_ui_handler:
            self._on_update_ui_handler()

    def request_quit(self) -> None:
        if self._on_quit_handler:
            self._on_quit_handler()


class WallpaperSwitchSystemTray:
    """
    System tray interface.
    """

    def __init__(self) -> None:
        self._app: QCoreApplication = QApplication(sys.argv)
        self._tray: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None

        self.bridge = SystemTrayBridge()
        self.bridge.update_ui_signal.connect(self.update_menu)

        self._action_groups: dict[str, QAction] = {}

    def show(self) -> None:
        self._tray = QSystemTrayIcon()
        self._menu = QMenu()
        self._menu.setToolTipsVisible(True)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setToolTip("wallpaper auto")

        icon_path = str(files("wallpaper_auto").joinpath("icon.svg"))
        icon = QIcon(icon_path)
        self._tray.setIcon(icon)

        self._tray.show()
        logger.debug("system tray show")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if self._tray is None or self._menu is None:
            return
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._menu.exec(QCursor.pos())
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._menu.exec(QCursor.pos())

    def hide(self) -> None:
        if self._tray is not None:
            self._tray.hide()
            self._tray = None
        if self._menu is not None:
            self._menu = None

    def update_menu(
        self,
        resource_ids: list[str],
        mode: Mode,
        active_rule: Rule | None,
        active_resource_id: str | None,
    ) -> None:
        if self._menu is None:
            raise RuntimeError("menu not initialized")
        self._menu.clear()
        self._action_groups.clear()

        auto_switch_action = QAction("AUTO", self._menu)
        auto_switch_action.triggered.connect(lambda: self.bridge.request_set_mode(Mode.AUTO))
        self._menu.addAction(auto_switch_action)
        if mode == Mode.AUTO:
            tip = f"{'fallback' if active_rule is None else active_rule.name}"
            auto_switch_action.setToolTip(tip)
            auto_switch_action.setIcon(create_dot_icon(get_color(AUTO_MODE_COLOR)))
            auto_switch_action.setEnabled(False)
        manual_switch_action = QAction("MANUAL", self._menu)
        manual_switch_action.triggered.connect(lambda: self.bridge.request_set_mode(Mode.MANUAL))
        if mode == Mode.MANUAL:
            manual_switch_action.setIcon(create_dot_icon(get_color(MANUAL_MODE_COLOR)))
            manual_switch_action.setEnabled(False)
        self._menu.addAction(manual_switch_action)

        self._menu.addSeparator()

        for rid in resource_ids:
            action = QAction(f"{rid}")
            action.triggered.connect(lambda checked, r=rid: self.bridge.request_select_resource(r))
            if rid == active_resource_id:
                if mode == Mode.AUTO:
                    action.setIcon(create_dot_icon(get_color(AUTO_MODE_COLOR)))
                elif mode == Mode.MANUAL:
                    action.setIcon(create_dot_icon(get_color(MANUAL_MODE_COLOR)))
            if mode == Mode.AUTO:
                action.setEnabled(False)
            self._menu.addAction(action)
            self._action_groups[rid] = action

        self._menu.addSeparator()

        quit_action = QAction("quit", self._menu)
        quit_action.triggered.connect(self.bridge.request_quit)
        self._menu.addAction(quit_action)

    def exec(self) -> None:
        #
        timer = QTimer()
        timer.start(1000)
        timer.timeout.connect(lambda: None)
        sys.exit(self._app.exec())


def create_dot_icon(color: QColor, size: int = 10) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)

    margin = size // 4
    painter.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.end()

    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(pixmap, QIcon.Mode.Disabled, QIcon.State.On)

    return icon


def get_color(hex_str: str) -> QColor:
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 8:
        hex_str = hex_str[6:] + hex_str[:6]
    return QColor(f"#{hex_str}")
