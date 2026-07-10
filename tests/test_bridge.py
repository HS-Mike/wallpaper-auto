"""Unit tests for SystemTrayBridge interface.

Tests all register methods, request methods, signal emission, and edge cases.
Does NOT involve WallpaperSwitchSystemTray — only the bridge layer.
"""

import pytest

from wallpaper_auto.models import ConditionNode, Rule
from wallpaper_auto.system_tray import SystemTrayBridge
from wallpaper_auto.task import Mode


@pytest.fixture
def bridge() -> SystemTrayBridge:
    return SystemTrayBridge()


# ── Handler metadata for parametrized tests ──────────────────────────────

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
        self, bridge: SystemTrayBridge, hid: str, values: list[object],
    ) -> None:
        results = []
        register = getattr(bridge, f"register_{hid}_handler")
        request = getattr(bridge, f"request_{hid}")
        register(lambda v: results.append(v))
        request(values[0])
        assert results == [values[0]]

    @pytest.mark.parametrize("hid,values", _VALUE_HANDLERS)
    def test_multiple_values(
        self, bridge: SystemTrayBridge, hid: str, values: list[object],
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
        self, bridge: SystemTrayBridge, hid: str, values: list[object],
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
        self, bridge: SystemTrayBridge, hid: str, values: list[object],
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
        self, bridge: SystemTrayBridge, hid: str, values: list[object],
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
    def test_emit_signal(  # type: ignore[no-untyped-def]
        self, bridge: SystemTrayBridge, qtbot,
        targets: list[str], mode: Mode, rule: str | None, active: str | None,
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
        self, bridge: SystemTrayBridge, hid: str, arg: object,
    ) -> None:
        if arg is None:
            getattr(bridge, f"request_{hid}")()
        else:
            getattr(bridge, f"request_{hid}")(arg)
