"""Tests for system_tray.py — SystemTrayBridge and WallpaperSwitchSystemTray."""

import signal
import sys
from collections.abc import Callable
from types import FrameType

import pytest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from wallpaper_auto.models import ConditionNode, Rule
from wallpaper_auto.system_tray import SystemTrayBridge, WallpaperSwitchSystemTray, get_color
from wallpaper_auto.task import Mode


@pytest.fixture
def bridge() -> SystemTrayBridge:
    """Create a SystemTrayBridge with no handlers registered."""
    return SystemTrayBridge()


@pytest.fixture(autouse=True, scope="function")
def ensure_qapp(qtbot, monkeypatch):
    """Ensure a QApplication exists and patch system_tray to reuse it."""
    app = QApplication.instance()
    assert app is not None
    monkeypatch.setattr(
        "wallpaper_auto.system_tray.QApplication",
        lambda *args: app,
    )
    yield


def _make_tray():
    """Create a WallpaperSwitchSystemTray with the shared QApplication."""
    tray = WallpaperSwitchSystemTray()
    app = QApplication.instance()
    assert app is not None
    tray._app = app
    return tray


@pytest.fixture
def tray_app():
    """Integration test fixture: creates tray with show/hide lifecycle."""
    tray = _make_tray()
    tray.show()
    yield tray
    tray.hide()


# (handler_id, sample_values) for handlers that take an argument
_VALUE_HANDLERS = [
    pytest.param("set_mode", [Mode.AUTO, Mode.MANUAL, Mode.UNSET], id="set_mode"),
    pytest.param("select_target", ["a", "b", ""], id="select_target"),
]

# (handler_id, sentinel) for handlers that take no argument
_NOARG_HANDLERS = [
    pytest.param("quit", id="quit"),
    pytest.param("update_ui", id="update_ui"),
]

_ALL_HANDLERS = [
    pytest.param("set_mode", Mode.AUTO, id="set_mode"),
    pytest.param("select_target", "x", id="select_target"),
    pytest.param("quit", None, id="quit"),
    pytest.param("update_ui", None, id="update_ui"),
]


class TestHandlerLifecycle:
    """Parametrized tests for all four register/request handler pairs."""

    @pytest.mark.parametrize("hid,values", _VALUE_HANDLERS)
    def test_register_and_invoke(
        self,
        bridge: SystemTrayBridge,
        hid: str,
        values: list[object],
    ) -> None:
        results = []
        register = getattr(bridge, f"register_{hid}_handler")
        request = getattr(bridge, f"request_{hid}")
        register(lambda v: results.append(v))
        request(values[0])
        assert results == [values[0]]

    @pytest.mark.parametrize("hid,values", _VALUE_HANDLERS)
    def test_request_delivers_each_value_in_order(
        self,
        bridge: SystemTrayBridge,
        hid: str,
        values: list[object],
    ) -> None:
        results = []
        register = getattr(bridge, f"register_{hid}_handler")
        request = getattr(bridge, f"request_{hid}")
        register(lambda v: results.append(v))
        for v in values:
            request(v)
        assert results == list(values)

    @pytest.mark.parametrize("hid,values", _VALUE_HANDLERS)
    def test_overwrite_replaces_old_handler(
        self,
        bridge: SystemTrayBridge,
        hid: str,
        values: list[object],
    ) -> None:
        results = []
        register = getattr(bridge, f"register_{hid}_handler")
        request = getattr(bridge, f"request_{hid}")
        register(lambda v: results.append("old"))
        register(lambda v: results.append("new"))
        request(values[0])
        assert results == ["new"]

    @pytest.mark.parametrize("hid,values", _VALUE_HANDLERS)
    def test_register_none_disables(
        self,
        bridge: SystemTrayBridge,
        hid: str,
        values: list[object],
    ) -> None:
        results = []
        register = getattr(bridge, f"register_{hid}_handler")
        request = getattr(bridge, f"request_{hid}")
        register(lambda v: results.append(v))
        register(None)
        request(values[0])
        assert results == []

    @pytest.mark.parametrize("hid,values", _VALUE_HANDLERS)
    def test_unregistered_is_noop(
        self,
        bridge: SystemTrayBridge,
        hid: str,
        values: list[object],
    ) -> None:
        getattr(bridge, f"request_{hid}")(values[0])  # should not crash

    # ── No-arg handlers ──

    @pytest.mark.parametrize("hid", _NOARG_HANDLERS)
    def test_noarg_register_and_invoke(self, bridge: SystemTrayBridge, hid: str) -> None:
        results = []
        getattr(bridge, f"register_{hid}_handler")(lambda: results.append("ok"))
        getattr(bridge, f"request_{hid}")()
        assert results == ["ok"]

    @pytest.mark.parametrize("hid", _NOARG_HANDLERS)
    def test_noarg_multiple_invocations(self, bridge: SystemTrayBridge, hid: str) -> None:
        results = []
        register = getattr(bridge, f"register_{hid}_handler")
        request = getattr(bridge, f"request_{hid}")
        register(lambda: results.append("ok"))
        request()
        request()
        request()
        assert results == ["ok", "ok", "ok"]

    @pytest.mark.parametrize("hid", _NOARG_HANDLERS)
    def test_noarg_register_none_disables(self, bridge: SystemTrayBridge, hid: str) -> None:
        results = []
        register = getattr(bridge, f"register_{hid}_handler")
        request = getattr(bridge, f"request_{hid}")
        register(lambda: results.append("ok"))
        register(None)
        request()
        assert results == []

    @pytest.mark.parametrize("hid", _NOARG_HANDLERS)
    def test_noarg_unregistered_is_noop(self, bridge: SystemTrayBridge, hid: str) -> None:
        getattr(bridge, f"request_{hid}")()  # should not crash


class TestBridgeInitialState:
    """Verify the bridge starts in a clean state."""

    def test_initial_handlers_are_none(self, bridge: SystemTrayBridge) -> None:
        assert bridge._on_set_mode_handler is None
        assert bridge._on_select_target_handler is None
        assert bridge._on_quit_handler is None
        assert bridge._on_update_ui_handler is None


class TestBridgeSignals:
    """Tests for SystemTrayBridge signal emission and delivery."""

    def test_update_ui_emits_update_ui_signal(self, qtbot):
        tray = _make_tray()
        tray.show()
        try:
            with qtbot.waitSignal(tray.bridge.update_ui_signal):
                tray.bridge.update_ui(["res1"], Mode.AUTO, None, "res1")
        finally:
            tray.hide()


class TestAllCallbacksIndependent:
    """All four callback types should work independently without interfering."""

    def test_all_register_and_request(self, bridge: SystemTrayBridge) -> None:
        set_mode_results = []
        select_res_results = []
        quit_results = []
        ui_results = []

        bridge.register_set_mode_handler(lambda m: set_mode_results.append(m))
        bridge.register_select_target_handler(lambda r: select_res_results.append(r))
        bridge.register_quit_handler(lambda: quit_results.append("ok"))
        bridge.register_update_ui_handler(lambda: ui_results.append("ok"))

        bridge.request_set_mode(Mode.AUTO)
        bridge.request_select_target("res-A")
        bridge.request_set_mode(Mode.MANUAL)
        bridge.request_select_target("res-B")
        bridge.request_update_ui()
        bridge.request_quit()
        bridge.request_update_ui()

        assert set_mode_results == [Mode.AUTO, Mode.MANUAL]
        assert select_res_results == ["res-A", "res-B"]
        assert ui_results == ["ok", "ok"]
        assert quit_results == ["ok"]

    def test_partial_registration(self, bridge: SystemTrayBridge) -> None:
        mode_results = []
        bridge.register_set_mode_handler(lambda m: mode_results.append(m))
        # Other handlers intentionally not registered
        bridge.request_set_mode(Mode.AUTO)
        bridge.request_select_target("x")  # should be noop
        bridge.request_update_ui()  # should be noop
        bridge.request_quit()  # should be noop
        assert mode_results == [Mode.AUTO]


class TestBridgeUpdateUiSignal:
    """The update_ui() method emits signals with correct payload."""

    @pytest.mark.parametrize(
        "targets,mode,rule,active",
        [
            pytest.param(
                ["r1", "r2"],
                Mode.AUTO,
                "test",
                "r1",
                id="all_args",
            ),
            pytest.param([], Mode.MANUAL, None, "", id="no_rule"),
        ],
    )
    def test_update_ui_emits_signal_with_payload(  # type: ignore[no-untyped-def]
        self,
        bridge: SystemTrayBridge,
        qtbot,
        targets: list[str],
        mode: Mode,
        rule: str | None,
        active: str | None,
    ) -> None:
        with qtbot.waitSignal(bridge.update_ui_signal, timeout=200) as blocker:
            bridge.update_ui(targets, mode, rule, active)
        args = blocker.args
        assert args[0] == targets
        assert args[1] == mode
        assert args[2] is rule
        assert args[3] == active

    def test_multiple_emissions_collected(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        bridge = SystemTrayBridge()
        received: list[object] = []

        def collect(*args: object) -> None:
            received.append(args)

        bridge.update_ui_signal.connect(collect)
        bridge.update_ui(["a"], Mode.AUTO, None, "a")
        bridge.update_ui(["b", "c"], Mode.MANUAL, None, "b")

        assert len(received) == 2
        assert received[0] == (["a"], Mode.AUTO, None, "a")
        assert received[1] == (["b", "c"], Mode.MANUAL, None, "b")


class TestBridgeEdgeCases:
    """Edge cases for the bridge interface."""

    def test_handler_raises_exception(self, bridge: SystemTrayBridge) -> None:
        def error_cb(_) -> None:
            raise RuntimeError("custom error raise")
            return None

        bridge.register_set_mode_handler(error_cb)
        with pytest.raises(RuntimeError):
            bridge.request_set_mode(Mode.AUTO)

    @pytest.mark.parametrize("hid,arg", _ALL_HANDLERS)
    def test_request_without_registration(
        self,
        bridge: SystemTrayBridge,
        hid: str,
        arg: object,
    ) -> None:
        if arg is None:
            getattr(bridge, f"request_{hid}")()
        else:
            getattr(bridge, f"request_{hid}")(arg)


class TestMenuRendering:
    """Tests for AUTO and MANUAL mode menu rendering and action state."""

    def test_menu_rendering_auto_mode(self, tray_app, qtbot):
        available_targets = ["wallpaper1", "wallpaper2"]
        active_target = "wallpaper1"

        tray_app.bridge.update_ui(
            available_targets,
            Mode.AUTO,
            Rule(
                name="Work",
                condition=ConditionNode(**{"random_condition": "random_param"}),  # type: ignore[arg-type]
                target="random_target",
            ).name,
            active_target,
        )

        actions = tray_app._menu.actions()

        action_texts = [a.text() for a in actions]
        assert "AUTO" in action_texts
        assert "wallpaper1" in action_texts

        auto_action = [a for a in actions if a.text() == "AUTO"][0]
        assert not auto_action.isEnabled()

        wp1_action = tray_app._action_groups["wallpaper1"]
        assert wp1_action.isEnabled()

    def test_menu_rendering_manual_mode_with_active_target(self, tray_app, qtbot):
        tray_app.bridge.update_ui(["res1", "res2"], Mode.MANUAL, None, "res1")

        actions = tray_app._menu.actions()
        action_texts = [a.text() for a in actions]
        assert "AUTO" in action_texts
        assert "MANUAL" not in action_texts

        res1_action = tray_app._action_groups["res1"]
        assert not res1_action.isEnabled()

        res2_action = tray_app._action_groups["res2"]
        assert res2_action.isEnabled()


class TestCallbacks:
    """Tests for UI-to-logic callbacks and bridge handler registration."""

    def test_ui_to_logic_callbacks(self, tray_app, qtbot):
        mock_called: dict[str, Mode | str | bool | None] = {
            "mode": None,
            "target": None,
            "quit": False,
        }

        tray_app.bridge.register_set_mode_handler(lambda m: mock_called.update({"mode": m}))
        tray_app.bridge.register_select_target_handler(lambda r: mock_called.update({"target": r}))
        tray_app.bridge.register_quit_handler(lambda: mock_called.update({"quit": True}))

        # 1. In MANUAL mode, click a resource action (also sets mode to MANUAL)
        tray_app.bridge.update_ui(["res_a"], Mode.MANUAL, None, None)
        res_action = tray_app._action_groups["res_a"]
        res_action.trigger()
        assert mock_called["mode"] == Mode.MANUAL
        assert mock_called["target"] == "res_a"

        # 2. Click AUTO to switch mode
        auto_action = [a for a in tray_app._menu.actions() if a.text() == "AUTO"][0]
        auto_action.trigger()
        assert mock_called["mode"] == Mode.AUTO  # type: ignore[comparison-overlap]

        # 3. Quit
        quit_action = [a for a in tray_app._menu.actions() if a.text() == "quit"][0]
        quit_action.trigger()
        assert mock_called["quit"]

    def test_bridge_request_update_ui(self, tray_app):
        called = False

        def handler():
            nonlocal called
            called = True

        tray_app.bridge.register_update_ui_handler(handler)
        tray_app.bridge.request_update_ui()
        assert called


class TestUtilityFunctions:
    """Tests for standalone helper/utility functions in system_tray."""

    def test_get_color_moves_alpha_to_front(self):
        color = get_color("#112233FF")  # FF moved to front -> #FF112233
        assert color.alpha() == 255
        assert color.red() == 0x11


class TestEdgeCases:
    """Tests for edge cases and error handling in system tray operations."""

    def test_on_tray_activated_when_tray_or_menu_none(self):
        tray = _make_tray()
        tray._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)

    def test_on_tray_activated_trigger_and_double_click_show_menu(self, monkeypatch, tray_app):
        exec_called = []
        monkeypatch.setattr(tray_app._menu, "exec", lambda pos: exec_called.append(True))

        tray_app._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
        assert len(exec_called) == 1

        tray_app._on_tray_activated(QSystemTrayIcon.ActivationReason.DoubleClick)
        assert len(exec_called) == 2

    def test_update_menu_runtime_error(self):
        tray = _make_tray()
        with pytest.raises(RuntimeError, match="menu not initialized"):
            tray.update_menu([], Mode.AUTO, None, "")

    def test_exec_starts_event_loop(self, monkeypatch):
        exec_called = []
        monkeypatch.setattr(sys, "exit", lambda code: None)

        tray = _make_tray()
        monkeypatch.setattr(tray._app, "exec", lambda: exec_called.append(True))

        tray.exec()
        assert len(exec_called) == 1

    def test_exec_sigint_handler_quits_and_propagates(self, monkeypatch):
        registered: dict[int, Callable[[int, FrameType | None], None]] = {}

        def capture_signal(signum, handler):
            registered[signum] = handler

        monkeypatch.setattr("wallpaper_auto.system_tray.signal.signal", capture_signal)
        monkeypatch.setattr(sys, "exit", lambda code: None)

        tray = _make_tray()
        quit_calls = []

        class FakeQApp:
            def __call__(self, *args):
                return tray._app

            @staticmethod
            def quit():
                quit_calls.append(True)

        monkeypatch.setattr("wallpaper_auto.system_tray.QApplication", FakeQApp)
        monkeypatch.setattr(tray._app, "exec", lambda: None)

        tray.exec()

        handler = registered[signal.SIGINT]
        assert handler is not None

        default_calls = []
        monkeypatch.setattr(
            "wallpaper_auto.system_tray.signal.default_int_handler",
            lambda sig, frame: default_calls.append((sig, frame)),
        )

        handler(signal.SIGINT, None)

        assert quit_calls == [True]
        assert default_calls == [(signal.SIGINT, None)]
