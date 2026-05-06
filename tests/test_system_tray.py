"""Tests for system_tray.py — WallpaperSwitchSystemTray and tray menu rendering."""

import pytest
from PySide6.QtWidgets import QApplication

from wallpaper_automator.system_tray import WallpaperSwitchSystemTray
from wallpaper_automator.task import Mode
from wallpaper_automator.models import ConditionNode, Rule


@pytest.fixture(autouse=True)
def ensure_qapp(qtbot, monkeypatch):
    """Ensure a QApplication exists and patch system_tray to reuse it."""
    app = QApplication.instance()
    assert app is not None
    monkeypatch.setattr(
        "wallpaper_automator.system_tray.QApplication",
        lambda *args: app,
    )
    yield


def _make_tray():
    """Create a WallpaperSwitchSystemTray with the shared QApplication."""
    tray = WallpaperSwitchSystemTray()
    tray._app = QApplication.instance()
    return tray


@pytest.fixture
def tray_app():
    """Integration test fixture: creates tray with show/hide lifecycle."""
    tray = _make_tray()
    tray.show()
    yield tray
    tray.hide()


class TestBridgeSignals:
    """Tests for SystemTrayBridge signal emission and delivery."""

    def test_bridge_signals(self, qtbot):
        tray = _make_tray()
        tray.show()
        with qtbot.waitSignal(tray.bridge.update_ui_signal):
            tray.bridge.update_ui(["res1"], Mode.AUTO, None, "res1")
        tray.hide()


class TestMenuRendering:
    """Tests for AUTO and MANUAL mode menu rendering and action state."""

    def test_menu_rendering_auto_mode(self, tray_app, qtbot):
        """Test AUTO mode menu rendering logic."""
        resource_ids = ["wallpaper1", "wallpaper2"]
        active_id = "wallpaper1"

        tray_app.bridge.update_ui(
            resource_ids,
            Mode.AUTO,
            Rule(
                name="Work",
                condition=ConditionNode(**{"random_condition": "random_param"}),  # type: ignore
                target="random_target",
                ),
            active_id
            )

        actions = tray_app._menu.actions()

        action_texts = [a.text() for a in actions]
        assert "AUTO" in action_texts
        assert "wallpaper1" in action_texts

        wp1_action = tray_app._action_groups["wallpaper1"]
        assert not wp1_action.isEnabled()

    def test_menu_rendering_manual_mode_with_active_resource(self, tray_app, qtbot):
        """MANUAL mode renders with active resource highlighted."""
        tray_app.bridge.update_ui(["res1", "res2"], Mode.MANUAL, None, "res1")

        actions = tray_app._menu.actions()
        action_texts = [a.text() for a in actions]
        assert "AUTO" in action_texts

        manual_action = [a for a in actions if a.text() == "MANUAL"][0]
        assert not manual_action.isEnabled()

        res1_action = tray_app._action_groups["res1"]
        assert res1_action.isEnabled()


class TestCallbacks:
    """Tests for UI-to-logic callbacks and bridge handler registration."""

    def test_ui_to_logic_callbacks(self, tray_app, qtbot):
        """Test menu item clicks trigger logic-layer callbacks."""
        mock_called = {"mode": None, "res": None, "quit": False}

        tray_app.bridge.register_set_mode_handler(lambda m: mock_called.update({"mode": m}))
        tray_app.bridge.register_select_resource_handler(lambda r: mock_called.update({"res": r}))
        tray_app.bridge.register_quit_handler(lambda: mock_called.update({"quit": True}))

        # 1. Switch to MANUAL
        tray_app.bridge.update_ui([], Mode.AUTO, None, "")
        manual_action = [a for a in tray_app._menu.actions() if a.text() == "MANUAL"][0]
        manual_action.trigger()
        assert mock_called["mode"] == Mode.MANUAL

        # 2. Select resource
        tray_app.bridge.update_ui(["res_a"], Mode.MANUAL, None, "")
        res_action = tray_app._action_groups["res_a"]
        res_action.trigger()
        assert mock_called["res"] == "res_a"

        # 3. Quit
        quit_action = [a for a in tray_app._menu.actions() if a.text() == "quit"][0]
        quit_action.trigger()
        assert mock_called["quit"]

    def test_bridge_request_update_ui(self, tray_app):
        """Register and invoke update_ui handler through bridge."""
        called = False
        def handler():
            nonlocal called
            called = True
        tray_app.bridge.register_update_ui_handler(handler)
        tray_app.bridge.request_update_ui()
        assert called


class TestUtilityFunctions:
    """Tests for standalone helper/utility functions in system_tray."""

    def test_utility_functions(self):
        """Test helper functions."""
        from wallpaper_automator.system_tray import get_color
        color = get_color("#112233FF")  # FF moved to front -> #FF112233
        assert color.alpha() == 255
        assert color.red() == 0x11


class TestEdgeCases:
    """Tests for edge cases and error handling in system tray operations."""

    def test_on_tray_activated_when_tray_or_menu_none(self):
        """_on_tray_activated handles None _tray/_menu gracefully."""
        from PySide6.QtWidgets import QSystemTrayIcon
        tray = _make_tray()
        tray._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)

    def test_on_tray_activated(self, monkeypatch, tray_app):
        """_on_tray_activated Trigger and DoubleClick both show menu."""
        from PySide6.QtWidgets import QSystemTrayIcon

        exec_called = []
        monkeypatch.setattr(tray_app._menu, 'exec', lambda pos: exec_called.append(True))

        tray_app._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        assert len(exec_called) == 1

        tray_app._on_tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
        assert len(exec_called) == 2

    def test_update_menu_runtime_error(self):
        """update_menu raises RuntimeError before show()."""
        tray = _make_tray()
        with pytest.raises(RuntimeError, match="menu not initialized"):
            tray.update_menu([], Mode.AUTO, None, "")

    def test_exec_starts_event_loop(self, monkeypatch):
        """exec() creates QTimer and calls _app.exec (lines 174-177)."""
        import sys
        from PySide6.QtCore import QTimer

        exec_called = []
        monkeypatch.setattr(sys, 'exit', lambda code: None)

        tray = _make_tray()
        original_exec = tray._app.exec
        monkeypatch.setattr(tray._app, 'exec', lambda: exec_called.append(True))

        tray.exec()
        assert len(exec_called) == 1
