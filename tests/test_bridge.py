"""Unit tests for SystemTrayBridge interface.

Tests all register methods, request methods, signal emission, and edge cases.
Does NOT involve WallpaperSwitchSystemTray — only the bridge layer.
"""

import pytest
from PySide6.QtCore import QObject

from wallpaper_automator.system_tray import SystemTrayBridge
from wallpaper_automator.task import Mode
from wallpaper_automator.models import ConditionNode, Rule


@pytest.fixture
def bridge():
    return SystemTrayBridge()


class TestBridgeInitialState:
    """Verify the bridge starts in a clean state."""

    def test_is_qobject(self, bridge):
        assert isinstance(bridge, QObject)

    def test_initial_handlers_are_none(self, bridge):
        assert bridge._on_set_mode_handler is None
        assert bridge._on_select_resource_handler is None
        assert bridge._on_quit_handler is None
        assert bridge._on_update_ui_handler is None

    def test_bridge_has_update_ui_signal(self, bridge):
        assert hasattr(bridge, "update_ui_signal")


class TestRegisterSetModeHandler:
    """register_set_mode_handler + request_set_mode."""

    def test_register_and_invoke(self, bridge):
        results = []
        bridge.register_set_mode_handler(lambda m: results.append(m))
        bridge.request_set_mode(Mode.AUTO)
        assert results == [Mode.AUTO]

    def test_invoke_with_all_modes(self, bridge):
        results = []
        bridge.register_set_mode_handler(lambda m: results.append(m))
        bridge.request_set_mode(Mode.AUTO)
        bridge.request_set_mode(Mode.MANUAL)
        bridge.request_set_mode(Mode.UNSET)
        assert results == [Mode.AUTO, Mode.MANUAL, Mode.UNSET]

    def test_overwrite_replaces_old_handler(self, bridge):
        results = []
        bridge.register_set_mode_handler(lambda m: results.append("old"))
        bridge.register_set_mode_handler(lambda m: results.append("new"))
        bridge.request_set_mode(Mode.AUTO)
        assert results == ["new"]

    def test_register_none_disables(self, bridge):
        results = []
        bridge.register_set_mode_handler(lambda m: results.append(m))
        bridge.register_set_mode_handler(None)
        bridge.request_set_mode(Mode.MANUAL)
        assert results == []

    def test_unregistered_is_noop(self, bridge):
        bridge.request_set_mode(Mode.AUTO)  # should not crash


class TestRegisterSelectResourceHandler:
    """register_select_resource_handler + request_select_resource."""

    def test_register_and_invoke(self, bridge):
        results = []
        bridge.register_select_resource_handler(lambda r: results.append(r))
        bridge.request_select_resource("wallpaper-1")
        assert results == ["wallpaper-1"]

    def test_invoke_multiple_resources(self, bridge):
        results = []
        bridge.register_select_resource_handler(lambda r: results.append(r))
        bridge.request_select_resource("a")
        bridge.request_select_resource("b")
        bridge.request_select_resource("")
        assert results == ["a", "b", ""]

    def test_overwrite_replaces_old_handler(self, bridge):
        results = []
        bridge.register_select_resource_handler(lambda r: results.append("old:" + r))
        bridge.register_select_resource_handler(lambda r: results.append("new:" + r))
        bridge.request_select_resource("x")
        assert results == ["new:x"]

    def test_register_none_disables(self, bridge):
        results = []
        bridge.register_select_resource_handler(lambda r: results.append(r))
        bridge.register_select_resource_handler(None)
        bridge.request_select_resource("x")
        assert results == []

    def test_unregistered_is_noop(self, bridge):
        bridge.request_select_resource("x")  # should not crash


class TestRegisterQuitHandler:
    """register_quit_handler + request_quit."""

    def test_register_and_invoke(self, bridge):
        called = False
        def handler():
            nonlocal called
            called = True
        bridge.register_quit_handler(handler)
        bridge.request_quit()
        assert called

    def test_called_only_once_per_request(self, bridge):
        count = 0
        def handler():
            nonlocal count
            count += 1
        bridge.register_quit_handler(handler)
        bridge.request_quit()
        assert count == 1

    def test_register_none_disables(self, bridge):
        called = False
        def handler():
            nonlocal called
            called = True
        bridge.register_quit_handler(handler)
        bridge.register_quit_handler(None)
        bridge.request_quit()
        assert not called

    def test_unregistered_is_noop(self, bridge):
        bridge.request_quit()  # should not crash


class TestRegisterUpdateUiHandler:
    """register_update_ui_handler + request_update_ui."""

    def test_register_and_invoke(self, bridge):
        called = False
        def handler():
            nonlocal called
            called = True
        bridge.register_update_ui_handler(handler)
        bridge.request_update_ui()
        assert called

    def test_invoke_multiple_times(self, bridge):
        count = 0
        def handler():
            nonlocal count
            count += 1
        bridge.register_update_ui_handler(handler)
        bridge.request_update_ui()
        bridge.request_update_ui()
        bridge.request_update_ui()
        assert count == 3

    def test_register_none_disables(self, bridge):
        called = False
        def handler():
            nonlocal called
            called = True
        bridge.register_update_ui_handler(handler)
        bridge.register_update_ui_handler(None)
        bridge.request_update_ui()
        assert not called

    def test_unregistered_is_noop(self, bridge):
        bridge.request_update_ui()  # should not crash


class TestAllCallbacksIndependent:
    """All four callback types should work independently without interfering."""

    def test_all_register_and_request(self, bridge):
        set_mode_results = []
        select_res_results = []
        quit_count = 0
        ui_count = 0

        def on_set_mode(m):
            set_mode_results.append(m)

        def on_select_resource(r):
            select_res_results.append(r)

        def on_quit():
            nonlocal quit_count
            quit_count += 1

        def on_update_ui():
            nonlocal ui_count
            ui_count += 1

        bridge.register_set_mode_handler(on_set_mode)
        bridge.register_select_resource_handler(on_select_resource)
        bridge.register_quit_handler(on_quit)
        bridge.register_update_ui_handler(on_update_ui)

        bridge.request_set_mode(Mode.AUTO)
        bridge.request_select_resource("res-A")
        bridge.request_set_mode(Mode.MANUAL)
        bridge.request_select_resource("res-B")
        bridge.request_update_ui()
        bridge.request_quit()
        bridge.request_update_ui()

        assert set_mode_results == [Mode.AUTO, Mode.MANUAL]
        assert select_res_results == ["res-A", "res-B"]
        assert ui_count == 2
        assert quit_count == 1

    def test_partial_registration(self, bridge):
        mode_called = False
        def on_mode(m):
            nonlocal mode_called
            mode_called = True

        bridge.register_set_mode_handler(on_mode)
        # Do NOT register other handlers

        bridge.request_set_mode(Mode.AUTO)
        bridge.request_select_resource("x")   # should be noop
        bridge.request_update_ui()             # should be noop
        bridge.request_quit()                  # should be noop

        assert mode_called


class TestBridgeUpdateUiMethod:
    """The update_ui() method emits signals. Tests may need qtbot."""

    def test_emit_signal_with_all_args(self, bridge, qtbot):
        rule = Rule(name="test", condition=ConditionNode.model_validate({"wifi_ssid_is": "OfficeWiFi"}), target="tgt")

        with qtbot.waitSignal(bridge.update_ui_signal, timeout=1000) as blocker:
            bridge.update_ui(["r1", "r2"], Mode.AUTO, rule, "r1")

        args = blocker.args
        assert len(args) == 4
        assert args[0] == ["r1", "r2"]
        assert args[1] == Mode.AUTO
        assert args[2] is rule
        assert args[3] == "r1"

    def test_emit_signal_with_no_rule(self, bridge, qtbot):
        with qtbot.waitSignal(bridge.update_ui_signal, timeout=1000) as blocker:
            bridge.update_ui([], Mode.MANUAL, None, "")

        args = blocker.args
        assert args[0] == []
        assert args[1] == Mode.MANUAL
        assert args[2] is None
        assert args[3] == ""

    def test_emit_with_unset_mode(self, bridge, qtbot):
        with qtbot.waitSignal(bridge.update_ui_signal, timeout=1000) as blocker:
            bridge.update_ui(["a"], Mode.UNSET, None, "")

        args = blocker.args
        assert args[1] == Mode.UNSET

    def test_multiple_emissions_collected(self, qtbot):
        bridge = SystemTrayBridge()
        received = []

        def collect(*args):
            received.append(args)

        bridge.update_ui_signal.connect(collect)

        bridge.update_ui(["a"], Mode.AUTO, None, "a")
        bridge.update_ui(["b", "c"], Mode.MANUAL, None, "b")

        assert len(received) == 2
        assert received[0] == (["a"], Mode.AUTO, None, "a")
        assert received[1] == (["b", "c"], Mode.MANUAL, None, "b")

    def test_signal_is_connectable_to_slot(self, bridge, qtbot):
        """Signal should be deliverable to an arbitrary slot."""
        received = []

        def slot(r_ids, mode, rule, active_id):
            received.append((r_ids, mode, rule, active_id))

        bridge.update_ui_signal.connect(slot)
        bridge.update_ui(["test"], Mode.AUTO, None, "test")

        assert len(received) == 1
        assert received[0] == (["test"], Mode.AUTO, None, "test")


class TestBridgeEdgeCases:
    """Edge cases for the bridge interface."""

    def test_handler_raises_exception(self, bridge):
        """A handler raising should propagate to the caller."""
        bridge.register_set_mode_handler(lambda m: 1/0)
        with pytest.raises(ZeroDivisionError):
            bridge.request_set_mode(Mode.AUTO)

    def test_request_without_any_registration(self, bridge):
        """All request_* methods should be safe without registration."""
        for i in range(5):
            bridge.request_set_mode(Mode.AUTO)
            bridge.request_select_resource(str(i))
            bridge.request_update_ui()
            bridge.request_quit()
