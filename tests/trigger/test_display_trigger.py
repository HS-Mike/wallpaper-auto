"""Tests for display_trigger.py — monitor plug/unplug detection via WM_DISPLAYCHANGE."""
# ruff: noqa: N806 — mock names may match Win32 constant naming conventions

from unittest.mock import patch

import pytest
import win32con

from wallpaper_auto.util.display_utils import DisplayInfo
from wallpaper_auto.trigger.display_trigger import DisplayTrigger

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_display_deps():
    """Patch all Win32 / CCD / COM / DPI dependencies for DisplayTrigger instance tests."""
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
        patch("wallpaper_auto.trigger.display_trigger.win32gui.DestroyWindow") as dw,
        patch("wallpaper_auto.trigger.display_trigger.win32gui.UnregisterClass"),
        patch(
            "wallpaper_auto.trigger.display_trigger.win32gui.GetModuleHandle",
            return_value=0xDEF,
        ),
        patch("wallpaper_auto.trigger.display_trigger.win32gui.PostMessage") as postmsg,
        patch("wallpaper_auto.trigger.display_trigger.win32gui.PostQuitMessage") as pqm,
        patch("wallpaper_auto.trigger.display_trigger.win32gui.DefWindowProc") as dwp,
        patch(
            "wallpaper_auto.trigger.display_trigger.get_display_info"
        ) as get_display_info_fn,
        patch("wallpaper_auto.trigger.display_trigger.pythoncom") as pythoncom,
        patch.object(
            DisplayTrigger, "_get_all_monitors_dpi_snapshot",
            return_value=frozenset(),
        ) as get_dpi_snapshot_fn,
    ):
        yield {
            "pqm": pqm,
            "dwp": dwp,
            "get_display_info": get_display_info_fn,
            "postmsg": postmsg,
            "pythoncom": pythoncom,
            "dw": dw,
            "get_dpi_snapshot_fn": get_dpi_snapshot_fn,
        }


# ===========================================================================
# TestDisplayTriggerInit
# ===========================================================================


class TestDisplayTriggerInit:
    """Tests for DisplayTrigger initial state."""

    def test_initial_state(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()

        assert trigger.hwnd is None
        assert trigger._prev_displays == frozenset()
        assert trigger._prev_monitor_dpis == frozenset()


# ===========================================================================
# TestDisplayTriggerMsgProc
# ===========================================================================


_DEVICE_A = r"\\?\DISPLAY#DELA#{...}"
_DEVICE_B = r"\\?\DISPLAY#INT#{...}"

_SNAPSHOT_A = frozenset([(_DEVICE_A, "U2719D")])
_SNAPSHOT_B = frozenset([(_DEVICE_A, "U2719D"), (_DEVICE_B, "internal")])


class TestDisplayTriggerMsgProc:
    """Tests for the window procedure ``_msg_proc``."""

    def test_wm_displaychange_triggers_on_change(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        trigger._prev_displays = frozenset()

        # Configure get_display_info to return a known display
        mock_display_deps["get_display_info"].return_value = [
            DisplayInfo(
                model="U2719D", source_resolution=(1920, 1080),
                position=(0, 0), target_resolution=(1920, 1080),
                scale=1.0, monitor_device_path=_DEVICE_A,
            ),
        ]

        callback_called = []
        trigger.add_callback(lambda _t: callback_called.append(True))

        result = trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

        assert callback_called == [True]
        assert trigger._prev_displays == _SNAPSHOT_A
        assert result == 0

    def test_wm_displaychange_skips_on_no_change(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        trigger._prev_displays = _SNAPSHOT_A

        with patch.object(trigger, "trigger") as mock_trigger:
            # Configure get_display_info to return SAME displays
            mock_display_deps["get_display_info"].return_value = [
                DisplayInfo(
                    model="U2719D", source_resolution=(1920, 1080),
                    position=(0, 0), target_resolution=(1920, 1080),
                    scale=1.0, monitor_device_path=_DEVICE_A,
                ),
            ]

            trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

            mock_trigger.assert_not_called()

    def test_wm_destroy_posts_quit_message(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()

        result = trigger._msg_proc(0, win32con.WM_DESTROY, 0, 0)

        mock_display_deps["pqm"].assert_called_once_with(0)
        assert result == 0

    def test_wm_close_destroys_window(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        trigger.hwnd = 0xABC

        result = trigger._msg_proc(0xABC, win32con.WM_CLOSE, 0, 0)

        mock_display_deps["dw"].assert_called_once_with(0xABC)
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

    def test_activate_deactivate_full_cycle(self) -> None:
        """Full activate → deactivate cycle with real Windows API calls.

        Verifies the complete chain without mocking any Win32/CCD/COM calls:
        activate() → thread creates real hidden window → PumpMessages runs
        → deactivate() posts WM_CLOSE → _msg_proc handles it → DestroyWindow
        → WM_DESTROY → PostQuitMessage → pump exits → thread joins.
        """
        import time

        trigger = DisplayTrigger()
        trigger.activate()

        # Wait for the background thread to create the window
        hwnd = None
        for _ in range(500):
            hwnd = trigger.hwnd
            if hwnd is not None:
                break
            time.sleep(0.005)

        assert hwnd is not None, "Window was not created in background thread"
        assert trigger.is_alive()

        # deactivate() posts WM_CLOSE and joins the thread
        trigger.deactivate()

        assert not trigger.is_alive()
        assert trigger.hwnd is None  # set to None by WM_DESTROY handler


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

        # _display_snapshot was called initially to populate _prev_displays
        mock_display_deps["get_display_info"].assert_called_once()

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

    def test_run_logs_unregisterclass_failure(self, mock_display_deps, caplog) -> None:
        trigger = DisplayTrigger()

        with patch(
            "wallpaper_auto.trigger.display_trigger.win32gui.UnregisterClass",
            side_effect=OSError("unregister failed"),
        ):
            trigger.run()

        # CoUninitialize must still be called despite the UnregisterClass error
        mock_display_deps["pythoncom"].CoUninitialize.assert_called_once()
        assert "UnregisterClass failed" in caplog.text


# ===========================================================================
# TestDisplayTriggerDpi
# ===========================================================================


class TestDisplayTriggerDpi:
    """Tests for DPI scaling change detection via all-monitors snapshot."""

    _MONITOR_96 = frozenset({((0, 0, 1920, 1080), 96)})
    _MONITOR_144 = frozenset({((0, 0, 1920, 1080), 144)})
    _MONITOR_DUAL = frozenset({
        ((0, 0, 1920, 1080), 96),
        ((1920, 0, 3840, 1080), 96),
    })

    def test_initial_dpi_captured_on_setup(self, mock_display_deps) -> None:
        """_setup_window should capture the initial all-monitors DPI snapshot."""
        trigger = DisplayTrigger()
        assert trigger._prev_monitor_dpis == frozenset()

        mock_display_deps["get_dpi_snapshot_fn"].return_value = self._MONITOR_96
        trigger._setup_window()

        assert trigger._prev_monitor_dpis == self._MONITOR_96

    def test_wm_settingchange_detects_dpi_change(self, mock_display_deps) -> None:
        """WM_SETTINGCHANGE should trigger when DPI changes on any monitor."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = self._MONITOR_96
        mock_display_deps["get_dpi_snapshot_fn"].return_value = self._MONITOR_144

        callback_called = []
        trigger.add_callback(lambda _t: callback_called.append(True))

        result = trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

        assert callback_called == [True]
        assert trigger._prev_monitor_dpis == self._MONITOR_144
        assert result == 0

    def test_wm_settingchange_skips_on_no_dpi_change(self, mock_display_deps) -> None:
        """WM_SETTINGCHANGE without DPI change should not trigger."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = self._MONITOR_96
        mock_display_deps["get_dpi_snapshot_fn"].return_value = self._MONITOR_96

        with patch.object(trigger, "trigger") as mock_trigger:
            trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

            mock_trigger.assert_not_called()

    def test_wm_settingchange_handles_empty_snapshot(self, mock_display_deps) -> None:
        """WM_SETTINGCHANGE with empty snapshot and prev should not trigger."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = frozenset()

        with patch.object(trigger, "trigger") as mock_trigger:
            trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

            mock_trigger.assert_not_called()

    def test_wm_displaychange_also_detects_dpi_change(self, mock_display_deps) -> None:
        """WM_DISPLAYCHANGE should also detect DPI scaling transitions."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = self._MONITOR_96
        mock_display_deps["get_dpi_snapshot_fn"].return_value = self._MONITOR_144

        callback_called = []

        def on_trigger(t: DisplayTrigger) -> None:
            callback_called.append(True)

        trigger.add_callback(on_trigger)

        trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

        assert callback_called == [True]
        assert trigger._prev_monitor_dpis == self._MONITOR_144

    def test_dual_monitor_secondary_dpi_change(self, mock_display_deps) -> None:
        """Changing DPI on secondary monitor (not at origin) still triggers."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = self._MONITOR_DUAL

        # Secondary monitor's DPI changed from 96 to 144
        changed_dual = frozenset({
            ((0, 0, 1920, 1080), 96),
            ((1920, 0, 3840, 1080), 144),
        })
        mock_display_deps["get_dpi_snapshot_fn"].return_value = changed_dual

        callback_called = []
        trigger.add_callback(lambda t: callback_called.append(True))

        trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

        assert callback_called == [True]
        assert trigger._prev_monitor_dpis == changed_dual
