"""Tests for display_trigger.py — monitor plug/unplug detection via WM_DISPLAYCHANGE."""
# ruff: noqa: N806 — mock names may match Win32 constant naming conventions

from unittest.mock import MagicMock, patch

import pytest
import win32con

from wallpaper_auto.util.display_utils import decode_wmi_string, get_display_set
from wallpaper_auto.trigger.display_trigger import DisplayTrigger

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_wmi_getobject():
    """Patch ``win32com.client.GetObject`` for ``get_display_set`` tests."""
    with patch(
        "wallpaper_auto.util.display_utils.win32com.client.GetObject"
    ) as mock_getobj:
        yield mock_getobj


@pytest.fixture
def mock_display_deps():
    """Patch all Win32 / WMI / COM dependencies for DisplayTrigger instance tests."""
    with (
        patch("wallpaper_auto.trigger.display_trigger.win32gui.WNDCLASS"),
        patch(
            "wallpaper_auto.trigger.display_trigger.win32gui.RegisterClass",
            return_value=1,
        ),
        patch(
            "wallpaper_auto.trigger.display_trigger.win32gui.CreateWindow",
            return_value=0xABC,
        ),
        patch("wallpaper_auto.trigger.display_trigger.win32gui.PumpMessages"),
        patch("wallpaper_auto.trigger.display_trigger.win32gui.DestroyWindow"),
        patch("wallpaper_auto.trigger.display_trigger.win32gui.UnregisterClass"),
        patch(
            "wallpaper_auto.trigger.display_trigger.win32gui.GetModuleHandle",
            return_value=0xDEF,
        ),
        patch("wallpaper_auto.trigger.display_trigger.win32gui.PostMessage") as postmsg,
        patch("wallpaper_auto.trigger.display_trigger.win32gui.PostQuitMessage") as pqm,
        patch("wallpaper_auto.trigger.display_trigger.win32gui.DefWindowProc") as dwp,
        patch(
            "wallpaper_auto.util.display_utils.win32com.client.GetObject"
        ) as getobj,
        patch("wallpaper_auto.trigger.display_trigger.pythoncom") as pythoncom,
    ):
        yield {
            "pqm": pqm,
            "dwp": dwp,
            "getobj": getobj,
            "postmsg": postmsg,
            "pythoncom": pythoncom,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_monitor(
    instance_name: str = r"DISPLAY\DELA123\5&123&0&UID43520_0",
    manufacturer: bytes = b"DEL",
    model: bytes = b"U2719D",
    serial: bytes = b"ABC123",
) -> MagicMock:
    monitor = MagicMock()
    monitor.InstanceName = instance_name
    monitor.ManufacturerName = manufacturer
    monitor.UserFriendlyName = model
    monitor.SerialNumberID = serial
    return monitor


# ===========================================================================
# TestDecodeWmiString
# ===========================================================================


class TestDecodeWmiString:
    """Tests for the pure function ``decode_wmi_string``."""

    def test_normal_ascii(self) -> None:
        assert decode_wmi_string(b"DELL") == "DELL"

    def test_with_null_terminator(self) -> None:
        assert decode_wmi_string(b"DELL\x00\x00") == "DELL"

    def test_with_trailing_spaces(self) -> None:
        assert decode_wmi_string(b"DELL ") == "DELL"

    def test_empty_input(self) -> None:
        assert decode_wmi_string(b"") == "Unknown"

    def test_none_input(self) -> None:
        assert decode_wmi_string(None) == "Unknown"  # type: ignore[arg-type]

    def test_non_iterable_raises_exception(self) -> None:
        class _RaisesOnIter:
            def __iter__(self):
                raise ValueError("test")

        result = decode_wmi_string(_RaisesOnIter())  # type: ignore[arg-type]
        assert result == "Unknown"


# ===========================================================================
# TestGetDisplaySet
# ===========================================================================


class TestGetDisplaySet:
    """Tests for the WMI-dependent function ``get_display_set``."""

    def test_single_monitor(self, mock_wmi_getobject) -> None:
        mock_wmi = MagicMock()
        mock_wmi.ExecQuery.return_value = [_make_mock_monitor()]
        mock_wmi_getobject.return_value = mock_wmi

        result = get_display_set()

        expected_pnp = r"DISPLAY\DELA123\5&123&0&UID43520"
        assert result == {("DEL", "U2719D", expected_pnp, "ABC123")}

    def test_multiple_monitors(self, mock_wmi_getobject) -> None:
        mock_wmi = MagicMock()
        mock_wmi.ExecQuery.return_value = [
            _make_mock_monitor(
                instance_name=r"DISPLAY\MON1\5&1_0",
                manufacturer=b"DEL",
                model=b"U2719D",
                serial=b"001",
            ),
            _make_mock_monitor(
                instance_name=r"DISPLAY\MON2\5&2_0",
                manufacturer=b"BNQ",
                model=b"XL2730",
                serial=b"002",
            ),
        ]
        mock_wmi_getobject.return_value = mock_wmi

        result = get_display_set()

        assert result == {
            ("DEL", "U2719D", r"DISPLAY\MON1\5&1", "001"),
            ("BNQ", "XL2730", r"DISPLAY\MON2\5&2", "002"),
        }

    def test_instance_name_without_underscore(self, mock_wmi_getobject) -> None:
        mock_wmi = MagicMock()
        mock_wmi.ExecQuery.return_value = [
            _make_mock_monitor(instance_name=r"DISPLAY\DELA123")
        ]
        mock_wmi_getobject.return_value = mock_wmi

        result = get_display_set()

        pnp_id = next(iter(result))[2]
        assert pnp_id == r"DISPLAY\DELA123"

    def test_wmi_failure_returns_empty_set(self, mock_wmi_getobject) -> None:
        mock_wmi_getobject.side_effect = Exception("COM error")

        result = get_display_set()

        assert result == set()

    def test_empty_wmi_result(self, mock_wmi_getobject) -> None:
        mock_wmi = MagicMock()
        mock_wmi.ExecQuery.return_value = []
        mock_wmi_getobject.return_value = mock_wmi

        result = get_display_set()

        assert result == set()


# ===========================================================================
# TestDisplayTriggerInit
# ===========================================================================


class TestDisplayTriggerInit:
    """Tests for DisplayTrigger initial state."""

    def test_initial_state(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()

        assert trigger.hwnd is None
        assert trigger._prev_displays == set()
        assert trigger.current_displays is None


# ===========================================================================
# TestDisplayTriggerMsgProc
# ===========================================================================


class TestDisplayTriggerMsgProc:
    """Tests for the window procedure ``_msg_proc``."""

    def test_wm_displaychange_triggers_on_change(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        callback_called = []
        trigger._prev_displays = set()

        # Configure get_display_set to return a known set
        mock_wmi = MagicMock()
        mock_wmi.ExecQuery.return_value = [_make_mock_monitor(serial=b"ABC")]
        mock_display_deps["getobj"].return_value = mock_wmi

        expected_pnp = r"DISPLAY\DELA123\5&123&0&UID43520"
        new_displays = {("DEL", "U2719D", expected_pnp, "ABC")}

        # Capture current_displays during callback
        captured_displays = []

        def on_trigger(t):
            captured_displays.append(t.current_displays)
            callback_called.append(True)

        trigger.add_callback(on_trigger)

        result = trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

        assert callback_called == [True]
        assert captured_displays == [new_displays]
        assert trigger._prev_displays == new_displays
        assert trigger.current_displays is None  # cleared after callback
        assert result == 0

    def test_wm_displaychange_skips_on_no_change(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        expected_pnp = r"DISPLAY\DELA123\5&123&0&UID43520"
        existing = {("DEL", "U2719D", expected_pnp, "ABC")}
        trigger._prev_displays = existing

        with patch.object(trigger, "trigger") as mock_trigger:
            # Configure get_display_set to return SAME set
            mock_wmi = MagicMock()
            mock_wmi.ExecQuery.return_value = [_make_mock_monitor(serial=b"ABC")]
            mock_display_deps["getobj"].return_value = mock_wmi

            trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

            mock_trigger.assert_not_called()
            assert trigger.current_displays is None

    def test_wm_destroy_posts_quit_message(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()

        result = trigger._msg_proc(0, win32con.WM_DESTROY, 0, 0)

        mock_display_deps["pqm"].assert_called_once_with(0)
        assert result == 0

    def test_unknown_message(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        mock_display_deps["dwp"].return_value = 42

        result = trigger._msg_proc(0xCAFE, 0x0100, 0xDEAD, 0xBEEF)

        mock_display_deps["dwp"].assert_called_once_with(0xCAFE, 0x0100, 0xDEAD, 0xBEEF)
        assert result == 42


# ===========================================================================
# TestDisplayTriggerActivateDeactivate
# ===========================================================================


class TestDisplayTriggerActivateDeactivate:
    """Tests for activate and deactivate lifecycle."""

    def test_activate_starts_thread(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()

        with patch("threading.Thread.start") as mock_start:
            trigger.activate()

            mock_start.assert_called_once()

    def test_deactivate_posts_quit_and_joins(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        trigger.hwnd = 0xABC

        with patch("threading.Thread.join") as mock_join:
            trigger.deactivate()

            mock_display_deps["postmsg"].assert_called_once_with(
                0xABC, win32con.WM_CLOSE, 0, 0
            )
            mock_join.assert_called_once_with(timeout=3)

    def test_deactivate_skips_post_when_no_hwnd(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        assert trigger.hwnd is None

        with patch("threading.Thread.join") as mock_join:
            trigger.deactivate()

            mock_display_deps["postmsg"].assert_not_called()
            mock_join.assert_called_once_with(timeout=3)


# ===========================================================================
# TestDisplayTriggerRun
# ===========================================================================


class TestDisplayTriggerRun:
    """Tests for ``run``."""

    def test_run_initializes_and_uninitializes_com(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()

        trigger.run()

        mock_display_deps["pythoncom"].CoInitialize.assert_called_once()
        mock_display_deps["pythoncom"].CoUninitialize.assert_called_once()

    def test_run_creates_window_and_pumps(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()

        trigger.run()

        # get_display_set was called initially to populate _prev_displays
        mock_display_deps["getobj"].assert_called_once()

    def test_run_cleans_up_after_pumpmessages_exception(
        self, mock_display_deps
    ) -> None:
        trigger = DisplayTrigger()

        # Make PumpMessages raise — cleanup should still run
        with patch(
            "wallpaper_auto.trigger.display_trigger.win32gui.PumpMessages",
            side_effect=RuntimeError("pump failed"),
        ):
            with pytest.raises(RuntimeError):
                trigger.run()

        # CoUninitialize is called in the finally block of run()
        mock_display_deps["pythoncom"].CoUninitialize.assert_called_once()

    def test_run_com_cleanup_after_setup_window_exception(
        self, mock_display_deps
    ) -> None:
        """When _setup_window raises, run() still calls CoUninitialize."""
        trigger = DisplayTrigger()

        with patch.object(
            trigger, "_setup_window", side_effect=RuntimeError("setup failed")
        ):
            with pytest.raises(RuntimeError):
                trigger.run()

        mock_display_deps["pythoncom"].CoInitialize.assert_called_once()
        mock_display_deps["pythoncom"].CoUninitialize.assert_called_once()
