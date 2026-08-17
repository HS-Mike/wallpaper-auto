"""
System tray UI.

Provides a PySide6-based system tray icon and menu for manual wallpaper
selection, mode switching (AUTO/MANUAL), and shutdown.
"""

import logging
import signal
import sys
from collections.abc import Callable
from importlib.resources import files
from types import FrameType

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .task import Mode

logger = logging.getLogger(__name__)


ACTIVATE_COLOR = "#25FF80FF"
ACTIVATE_AUXILIARY_COLOR = "#64DD9675"


class SystemTrayBridge(QObject):
    """
    Full-duplex bridge between the logic layer and the Qt UI layer.

    The logic layer registers callbacks via register_*_handler; the UI layer
    invokes the matching request_* methods, which call those callbacks. The
    update_ui_signal carries state from the logic layer to the UI.
    """

    # Signal: Logic layer -> UI layer (for updating the interface)
    # Params: available_targets, mode, active_rule_id, active_target
    update_ui_signal = Signal(list, object, object, object)

    def __init__(self) -> None:
        super().__init__()
        self._on_set_mode_handler: Callable[[Mode], None] | None = None
        self._on_select_target_handler: Callable[[str], None] | None = None
        self._on_quit_handler: Callable[[], None] | None = None
        self._on_update_ui_handler: Callable[[], None] | None = None

    def update_ui(
        self,
        available_targets: list[str],
        mode: object,
        active_rule_id: str | None,
        active_target: str | None,
    ) -> None:
        """Emit update_ui_signal with the current UI state.

        Args:
            available_targets: Target IDs available for manual selection.
            mode: Current mode.
            active_rule_id: ID of the matching rule, or None.
            active_target: ID of the active target, or None.
        """
        self.update_ui_signal.emit(available_targets, mode, active_rule_id, active_target)

    def register_set_mode_handler(self, cb: Callable[[Mode], None]) -> None:
        """Register the callback invoked by request_set_mode.

        Args:
            cb: Handler receiving the requested mode.
        """
        self._on_set_mode_handler = cb

    def register_select_target_handler(self, cb: Callable[[str], None]) -> None:
        """Register the callback invoked by request_select_target.

        Args:
            cb: Handler receiving the requested target ID.
        """
        self._on_select_target_handler = cb

    def register_quit_handler(self, cb: Callable[[], None]) -> None:
        """Register the callback invoked by request_quit.

        Args:
            cb: Handler called when the UI requests a quit.
        """
        self._on_quit_handler = cb

    def register_update_ui_handler(self, cb: Callable[[], None]) -> None:
        """Register the callback invoked by request_update_ui.

        Args:
            cb: Handler called when the UI requests a refresh.
        """
        self._on_update_ui_handler = cb

    def request_select_target(self, target: str) -> None:
        """Request manual selection of a target through the registered handler.

        Args:
            target: Target ID to select.
        """
        if self._on_select_target_handler:
            self._on_select_target_handler(target)

    def request_set_mode(self, mode: Mode) -> None:
        """Request a mode change through the registered handler.

        Args:
            mode: Mode to switch to.
        """
        if self._on_set_mode_handler:
            self._on_set_mode_handler(mode)

    def request_update_ui(self) -> None:
        """Request a UI refresh through the registered handler."""
        if self._on_update_ui_handler:
            self._on_update_ui_handler()

    def request_quit(self) -> None:
        """Request the application quit through the registered handler."""
        if self._on_quit_handler:
            self._on_quit_handler()


class WallpaperSwitchSystemTray:
    """
    PySide6 system tray icon and context menu.

    Provides manual wallpaper selection, AUTO/MANUAL mode switching, and quit,
    bridging menu actions to the logic layer via SystemTrayBridge.
    """

    def __init__(self) -> None:
        self._app: QCoreApplication = QApplication(sys.argv)
        self._tray: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None

        self.bridge = SystemTrayBridge()
        self.bridge.update_ui_signal.connect(self.update_menu)

        self._action_groups: dict[str, QAction] = {}

    def show(self) -> None:
        """Create and display the tray icon with its context menu."""
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
        """Open the context menu on left-click and double-click activation.

        Args:
            reason: Qt tray activation reason.
        """
        if self._tray is None or self._menu is None:
            return
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._menu.exec(QCursor.pos())
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._menu.exec(QCursor.pos())

    def hide(self) -> None:
        """Hide and discard the tray icon and its context menu."""
        if self._tray is not None:
            self._tray.hide()
            self._tray = None
        if self._menu is not None:
            self._menu = None

    def update_menu(
        self,
        available_targets: list[str],
        mode: Mode,
        active_rule_id: str | None,
        active_target: str | None,
    ) -> None:
        """Rebuild the context menu from the current UI state.

        Args:
            available_targets: Target IDs available for manual selection.
            mode: Current mode.
            active_rule_id: ID of the matching rule, or None.
            active_target: ID of the active target, or None.

        Raises:
            RuntimeError: If show() has not been called to initialize the menu.
        """
        if self._menu is None:
            raise RuntimeError("menu not initialized")
        self._menu.clear()
        self._action_groups.clear()

        auto_switch_action = QAction("AUTO", self._menu)
        auto_switch_action.triggered.connect(lambda: self.bridge.request_set_mode(Mode.AUTO))
        self._menu.addAction(auto_switch_action)

        self._menu.addSeparator()

        for t in available_targets:
            action = QAction(f"{t}")
            action.triggered.connect(lambda: self.bridge.request_set_mode(Mode.MANUAL))
            action.triggered.connect(lambda checked, t=t: self.bridge.request_select_target(t))
            self._menu.addAction(action)
            self._action_groups[t] = action

        if mode == Mode.AUTO:
            tip = active_rule_id or "fallback"
            auto_switch_action.setToolTip(tip)
            auto_switch_action.setIcon(create_dot_icon(get_color(ACTIVATE_COLOR)))
            auto_switch_action.setEnabled(False)
            if active_target is not None:
                active_action = self._action_groups[active_target]
                active_action.setIcon(create_dot_icon(get_color(ACTIVATE_AUXILIARY_COLOR)))

        if mode == Mode.MANUAL:
            if active_target is not None:
                active_action = self._action_groups[active_target]
                active_action.setIcon(create_dot_icon(get_color(ACTIVATE_COLOR)))
                active_action.setEnabled(False)

        self._menu.addSeparator()

        quit_action = QAction("quit", self._menu)
        quit_action.triggered.connect(self.bridge.request_quit)
        self._menu.addAction(quit_action)

    def exec(self) -> None:
        """Run the Qt event loop until the application quits.

        Installs a SIGINT handler so Ctrl+C quits the app and then forwards to
        the default handler. Blocks the calling thread.
        """
        timer = QTimer()
        timer.start(1000)
        timer.timeout.connect(lambda: None)

        def handle_sigint(sig: int, frame: FrameType | None) -> None:
            """Quit the Qt application, then forward to the default SIGINT handler.

            Args:
                sig: Signal number.
                frame: Current stack frame at signal time.
            """
            QApplication.quit()
            signal.default_int_handler(sig, frame)

        signal.signal(signal.SIGINT, handle_sigint)

        sys.exit(self._app.exec())


def create_dot_icon(color: QColor, size: int = 10) -> QIcon:
    """Create a solid circular icon used to mark active menu actions.

    Args:
        color: Fill color for the dot.
        size: Diameter of the dot in pixels.

    Returns:
        An icon with normal and disabled variants of the dot.
    """
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
    """Parse a hex color string into a QColor.

    An 8-digit value is treated as #RRGGBBAA and reordered to Qt's #AARRGGBB
    layout; a 6-digit value is used as-is.

    Args:
        hex_str: Hex color string, optionally prefixed with '#'.

    Returns:
        The parsed QColor.
    """
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 8:
        hex_str = hex_str[6:] + hex_str[:6]
    return QColor(f"#{hex_str}")
