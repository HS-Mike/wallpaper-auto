"""Tests for display_trigger.py — monitor plug/unplug detection via WM_DISPLAYCHANGE."""

import logging
import time
from unittest.mock import patch

import pytest
import win32con

from wallpaper_auto.trigger.display_trigger import DisplayTrigger
from wallpaper_auto.util.display_utils import (
    DisplayInfo,
    DisplayTopologyTransientError,
    get_all_monitors_dpi_snapshot,
)

# Capture the real, unpatched implementation for tests that need to exercise
# the real function's exception-handling branch.
_REAL_GET_DPI_SNAPSHOT = get_all_monitors_dpi_snapshot

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
        patch("wallpaper_auto.trigger.display_trigger.get_display_info") as get_display_info_fn,
        patch("wallpaper_auto.trigger.display_trigger.pythoncom") as pythoncom,
        patch(
            "wallpaper_auto.trigger.display_trigger.get_all_monitors_dpi_snapshot",
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

_SNAPSHOT_A = frozenset([(_DEVICE_A, "U2719D")])

_DISPLAY_INFO_A = DisplayInfo(
    model="U2719D",
    source_resolution=(1920, 1080),
    position=(0, 0),
    target_resolution=(1920, 1080),
    scale=1.0,
    monitor_device_path=_DEVICE_A,
)

_MONITOR_96 = frozenset({((0, 0, 1920, 1080), 96)})
_MONITOR_144 = frozenset({((0, 0, 1920, 1080), 144)})
_MONITOR_DUAL = frozenset(
    {
        ((0, 0, 1920, 1080), 96),
        ((1920, 0, 3840, 1080), 96),
    }
)


class TestDisplayTriggerMsgProc:
    """Tests for the window procedure ``_msg_proc``."""

    def test_wm_displaychange_triggers_on_change(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        trigger._prev_displays = frozenset()

        # Configure get_display_info to return a known display
        mock_display_deps["get_display_info"].return_value = [_DISPLAY_INFO_A]

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
            mock_display_deps["get_display_info"].return_value = [_DISPLAY_INFO_A]

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
# TestDisplayTriggerLifecycle
# ===========================================================================


class TestDisplayTriggerLifecycle:
    """Tests for start and stop lifecycle."""

    def test_stop_skips_post_when_no_hwnd(self, mock_display_deps) -> None:
        trigger = DisplayTrigger()
        assert trigger.hwnd is None

        trigger.stop()

        mock_display_deps["postmsg"].assert_not_called()

    def test_start_stop_full_cycle(self) -> None:
        """Full start → stop cycle with real Windows API calls.

        Verifies the complete chain without mocking any Win32/CCD/COM calls:
        start() → thread creates real hidden window → PumpMessages runs
        → stop() posts WM_CLOSE → _msg_proc handles it → DestroyWindow
        → WM_DESTROY → PostQuitMessage → pump exits → thread joins.
        """

        trigger = DisplayTrigger()
        trigger.start()

        # Wait for the background thread to create the window
        hwnd = None
        for _ in range(50):
            hwnd = trigger.hwnd
            if hwnd is not None:
                break
            time.sleep(0.005)

        assert hwnd is not None, "Window was not created in background thread"
        assert trigger._thread is not None and trigger._thread.is_alive()

        # stop() posts WM_CLOSE and joins the thread
        trigger.stop()

        assert trigger._thread is None  # set to None by stop()
        assert trigger.hwnd is None  # set to None by WM_DESTROY handler


# ===========================================================================
# TestDisplayTriggerRun
# ===========================================================================


class TestDisplayTriggerRun:
    """Tests for the ``run`` method — COM lifecycle, error recovery, and cleanup guarantees."""

    def test_run_happy_path(self, mock_display_deps) -> None:
        """run() initializes COM, captures display info, and cleans up."""
        trigger = DisplayTrigger()

        trigger.run()

        mock_display_deps["pythoncom"].CoInitialize.assert_called_once()
        mock_display_deps["pythoncom"].CoUninitialize.assert_called_once()
        mock_display_deps["get_display_info"].assert_called_once()

    @pytest.mark.parametrize(
        ("patch_target", "patch_attr"),
        [
            pytest.param(
                "wallpaper_auto.trigger.display_trigger.win32gui.PumpMessages",
                None,
                id="pump messages fail",
            ),
            pytest.param(
                None,
                "_setup_window",
                id="setup window fail",
            ),
        ],
    )
    def test_run_cleans_up_com_on_failure(
        self, mock_display_deps, patch_target, patch_attr
    ) -> None:
        """run() always calls CoUninitialize even if an internal step fails."""
        trigger = DisplayTrigger()

        if patch_target:
            patcher = patch(patch_target, side_effect=RuntimeError("fail"))
        else:
            patcher = patch.object(trigger, patch_attr, side_effect=RuntimeError("fail"))

        with patcher:
            with pytest.raises(RuntimeError):
                trigger.run()

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

    def test_initial_dpi_captured_on_setup(self, mock_display_deps) -> None:
        """_setup_window should capture the initial all-monitors DPI snapshot."""
        trigger = DisplayTrigger()
        assert trigger._prev_monitor_dpis == frozenset()

        mock_display_deps["get_dpi_snapshot_fn"].return_value = _MONITOR_96
        trigger._setup_window()

        assert trigger._prev_monitor_dpis == _MONITOR_96

    def test_wm_settingchange_detects_dpi_change(self, mock_display_deps) -> None:
        """WM_SETTINGCHANGE should trigger when DPI changes on any monitor."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = _MONITOR_96
        mock_display_deps["get_dpi_snapshot_fn"].return_value = _MONITOR_144

        callback_called = []
        trigger.add_callback(lambda _t: callback_called.append(True))

        result = trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

        assert callback_called == [True]
        assert trigger._prev_monitor_dpis == _MONITOR_144
        assert result == 0

    @pytest.mark.parametrize(
        ("prev_dpis", "curr_dpis"),
        [
            pytest.param(
                _MONITOR_96,
                _MONITOR_96,
                id="same dpi value",
            ),
            pytest.param(
                frozenset(),
                frozenset(),
                id="empty snapshot",
            ),
        ],
    )
    def test_wm_settingchange_skips_on_no_change(
        self, mock_display_deps, prev_dpis, curr_dpis
    ) -> None:
        """WM_SETTINGCHANGE without DPI change should not trigger."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = prev_dpis
        mock_display_deps["get_dpi_snapshot_fn"].return_value = curr_dpis

        with patch.object(trigger, "trigger") as mock_trigger:
            trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

            mock_trigger.assert_not_called()

    def test_wm_displaychange_also_detects_dpi_change(self, mock_display_deps) -> None:
        """WM_DISPLAYCHANGE should also detect DPI scaling transitions."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = _MONITOR_96
        mock_display_deps["get_dpi_snapshot_fn"].return_value = _MONITOR_144

        callback_called = []

        def on_trigger(t: DisplayTrigger) -> None:
            callback_called.append(True)

        trigger.add_callback(on_trigger)

        trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

        assert callback_called == [True]
        assert trigger._prev_monitor_dpis == _MONITOR_144

    def test_dual_monitor_secondary_dpi_change(self, mock_display_deps) -> None:
        """Changing DPI on secondary monitor (not at origin) still triggers."""
        trigger = DisplayTrigger()
        trigger._prev_monitor_dpis = _MONITOR_DUAL

        # Secondary monitor's DPI changed from 96 to 144
        changed_dual = frozenset(
            {
                ((0, 0, 1920, 1080), 96),
                ((1920, 0, 3840, 1080), 144),
            }
        )
        mock_display_deps["get_dpi_snapshot_fn"].return_value = changed_dual

        callback_called = []
        trigger.add_callback(lambda t: callback_called.append(True))

        trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

        assert callback_called == [True]
        assert trigger._prev_monitor_dpis == changed_dual


# ===========================================================================
# TestDisplayTriggerErrorPaths
# ===========================================================================


class TestDisplayTriggerErrorPaths:
    """Error paths in _display_snapshot, get_all_monitors_dpi_snapshot, _msg_proc, run."""

    def test_display_snapshot_returns_none_on_transient_error(self, mock_display_deps) -> None:
        """DisplayTopologyTransientError → _display_snapshot returns None."""
        trigger = DisplayTrigger()
        mock_display_deps["get_display_info"].side_effect = DisplayTopologyTransientError(
            "topology transition"
        )
        assert trigger._display_snapshot() is None

    def test_setup_window_preserves_empty_on_transient_error(self, mock_display_deps) -> None:
        """_setup_window leaves _prev_displays as empty frozenset when snapshot fails."""
        trigger = DisplayTrigger()
        mock_display_deps["get_display_info"].side_effect = DisplayTopologyTransientError(
            "topology transition"
        )

        trigger._setup_window()

        # Snapshot returned None → _prev_displays stays at the init default
        assert trigger._prev_displays == frozenset()

    def test_get_all_monitors_dpi_snapshot_logs_on_exception(self, caplog) -> None:
        """An exception during DPI enumeration is logged and returns empty frozenset.

        Restores the real function (which the fixture has replaced) so the
        production except branch runs and is covered.
        """
        with patch(
            "wallpaper_auto.trigger.display_trigger.get_all_monitors_dpi_snapshot",
            _REAL_GET_DPI_SNAPSHOT,
        ):
            with patch(
                "wallpaper_auto.util.display_utils.win32api.EnumDisplayMonitors",
                side_effect=OSError("enum failed"),
            ):
                with caplog.at_level(logging.ERROR, logger="wallpaper_auto.util.display_utils"):
                    result = get_all_monitors_dpi_snapshot()

        assert result == frozenset()
        assert "Failed to query all monitors DPI" in caplog.text

    def test_msg_proc_discards_when_curr_display_none(self, mock_display_deps) -> None:
        """If _display_snapshot returns None (transient error), no trigger fires."""
        trigger = DisplayTrigger()
        mock_display_deps["get_display_info"].side_effect = DisplayTopologyTransientError(
            "transition"
        )
        with patch.object(trigger, "trigger") as mock_trigger:
            result = trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)
        mock_trigger.assert_not_called()
        assert result == 0

    def test_run_logs_setthreaddpi_failure(self, mock_display_deps, caplog) -> None:
        """If SetThreadDpiAwarenessContext raises, run logs and continues."""
        trigger = DisplayTrigger()
        with patch("wallpaper_auto.trigger.display_trigger.user32") as fake_user32:
            fake_user32.SetThreadDpiAwarenessContext.side_effect = OSError("dpi failed")
            with caplog.at_level(logging.ERROR, logger="wallpaper_auto.trigger.display_trigger"):
                trigger.run()

        assert "Failed to set thread DPI awareness" in caplog.text
        # COM lifecycle still ran.
        mock_display_deps["pythoncom"].CoUninitialize.assert_called_once()

    def test_module_level_attribute_error_handled(self) -> None:
        """The try/except at module load is exercised by re-importing with the
        SetThreadDpiAwarenessContext attribute absent."""
        import ctypes
        import importlib
        import sys

        # Remove the attribute so the module-level argtypes assignment raises.
        # We patch the windll user32 attribute lookup via a thin wrapper.
        original = ctypes.windll.user32

        class _NoDpiUser32:
            @property
            def DisplayConfigGetDeviceInfo(self):  # noqa: N802
                return original.DisplayConfigGetDeviceInfo

            def __getattr__(self, name):
                if name == "SetThreadDpiAwarenessContext":
                    raise AttributeError(name)
                return getattr(original, name)

        ctypes.windll.user32 = _NoDpiUser32()  # type: ignore[assignment]
        try:
            sys.modules.pop("wallpaper_auto.trigger.display_trigger", None)
            importlib.import_module("wallpaper_auto.trigger.display_trigger")
        finally:
            ctypes.windll.user32 = original  # type: ignore[assignment]
            sys.modules.pop("wallpaper_auto.trigger.display_trigger", None)
            importlib.import_module("wallpaper_auto.trigger.display_trigger")
