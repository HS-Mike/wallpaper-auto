"""Tests for display_trigger.py — monitor plug/unplug detection via WM_DISPLAYCHANGE."""

import time
from collections.abc import Iterator
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
import win32con

from wallpaper_auto.trigger.display_trigger import DisplayTrigger
from wallpaper_auto.util.display_utils import (
    LUID,
    DisplayInfo,
)


@pytest.fixture
def mock_display_deps() -> Iterator[dict[str, MagicMock]]:
    """Patch all Win32 / CCD / COM dependencies for DisplayTrigger instance tests."""
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
        patch("wallpaper_auto.trigger.display_trigger.set_process_dpi_aware"),
        patch("wallpaper_auto.trigger.display_trigger.pythoncom") as pythoncom,
    ):
        yield cast(
            dict[str, MagicMock],
            {
                "pqm": pqm,
                "dwp": dwp,
                "get_display_info": get_display_info_fn,
                "postmsg": postmsg,
                "pythoncom": pythoncom,
                "dw": dw,
            },
        )


class TestDisplayTriggerInit:
    """Tests for DisplayTrigger initial state."""

    def test_initial_state(self) -> None:
        """A new trigger has no window handle and an empty display snapshot."""
        trigger = DisplayTrigger()

        assert trigger.hwnd is None
        assert trigger._prev_display == frozenset()


_DEVICE_A = r"\\?\DISPLAY#DELA#{...}"

_DISPLAY_INFO_A = DisplayInfo(
    device_name="\\\\.\\DISPLAY1",
    model="U2719D",
    source_resolution=(1920, 1080),
    position=(0, 0),
    target_resolution=(1920, 1080),
    adapter_id=LUID(1, 2),
    source_id=3,
    scale=100,
    monitor_device_path=_DEVICE_A,
)

# Same display_id as _DISPLAY_INFO_A (volatile fields only differ).
_DISPLAY_INFO_A_SCALED = DisplayInfo(
    device_name="\\\\.\\DISPLAY1",
    model="U2719D",
    source_resolution=(1920, 1080),
    position=(0, 0),
    target_resolution=(1920, 1080),
    adapter_id=LUID(1, 2),
    source_id=3,
    scale=125,
    monitor_device_path=_DEVICE_A,
)

_DISPLAY_INFO_A_RESIZED = DisplayInfo(
    device_name="\\\\.\\DISPLAY1",
    model="U2719D",
    source_resolution=(2560, 1440),
    position=(0, 0),
    target_resolution=(1920, 1080),
    adapter_id=LUID(1, 2),
    source_id=3,
    scale=100,
    monitor_device_path=_DEVICE_A,
)


class TestDisplayTriggerMsgProc:
    """Tests for the window procedure ``_msg_proc``."""

    def test_wm_displaychange_triggers_on_change(self, mock_display_deps) -> None:
        """WM_DISPLAYCHANGE fires callbacks when the display set changes."""
        trigger = DisplayTrigger()
        trigger._prev_display = frozenset()

        # Configure get_display_info to return a known display
        mock_display_deps["get_display_info"].return_value = [_DISPLAY_INFO_A]

        with patch.object(trigger, "trigger") as mock_trigger:
            result = trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

        mock_trigger.assert_called_once()
        assert _DISPLAY_INFO_A in trigger._prev_display
        assert result == 0

    def test_wm_displaychange_skips_on_no_change(self, mock_display_deps) -> None:
        """WM_DISPLAYCHANGE is ignored when the display set is unchanged."""
        trigger = DisplayTrigger()
        trigger._prev_display = frozenset({_DISPLAY_INFO_A})

        with patch.object(trigger, "trigger") as mock_trigger:
            # Configure get_display_info to return SAME displays
            mock_display_deps["get_display_info"].return_value = [_DISPLAY_INFO_A]

            trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

            mock_trigger.assert_not_called()

    def test_wm_displaychange_triggers_on_removal(self, mock_display_deps) -> None:
        """WM_DISPLAYCHANGE fires callbacks when a display is removed."""
        trigger = DisplayTrigger()
        trigger._prev_display = frozenset({_DISPLAY_INFO_A})

        mock_display_deps["get_display_info"].return_value = []

        with patch.object(trigger, "trigger") as mock_trigger:
            result = trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)

        mock_trigger.assert_called_once()
        assert trigger._prev_display == frozenset()
        assert result == 0

    def test_wm_settingchange_triggers_on_scale_change(self, mock_display_deps) -> None:
        """WM_SETTINGCHANGE fires callbacks when a display's DPI scale changes."""
        trigger = DisplayTrigger()
        trigger._prev_display = frozenset({_DISPLAY_INFO_A})

        # display_id is stable across scale changes — the fix relies on this.
        assert _DISPLAY_INFO_A_SCALED.display_id == _DISPLAY_INFO_A.display_id

        mock_display_deps["get_display_info"].return_value = [_DISPLAY_INFO_A_SCALED]

        with patch.object(trigger, "trigger") as mock_trigger:
            result = trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

        mock_trigger.assert_called_once()
        assert trigger._prev_display == frozenset({_DISPLAY_INFO_A_SCALED})
        assert result == 0

    def test_wm_settingchange_triggers_on_resolution_change(self, mock_display_deps) -> None:
        """WM_SETTINGCHANGE fires callbacks when a display's resolution changes."""
        trigger = DisplayTrigger()
        trigger._prev_display = frozenset({_DISPLAY_INFO_A})

        assert _DISPLAY_INFO_A_RESIZED.display_id == _DISPLAY_INFO_A.display_id

        mock_display_deps["get_display_info"].return_value = [_DISPLAY_INFO_A_RESIZED]

        with patch.object(trigger, "trigger") as mock_trigger:
            result = trigger._msg_proc(0, win32con.WM_SETTINGCHANGE, 0, 0)

        mock_trigger.assert_called_once()
        assert trigger._prev_display == frozenset({_DISPLAY_INFO_A_RESIZED})
        assert result == 0

    def test_wm_destroy_posts_quit_message(self, mock_display_deps) -> None:
        """WM_DESTROY posts a quit message to end the message loop."""
        trigger = DisplayTrigger()

        result = trigger._msg_proc(0, win32con.WM_DESTROY, 0, 0)

        mock_display_deps["pqm"].assert_called_once_with(0)
        assert result == 0

    def test_wm_close_destroys_window(self, mock_display_deps) -> None:
        """WM_CLOSE destroys the hidden watch window."""
        trigger = DisplayTrigger()
        trigger.hwnd = 0xABC

        result = trigger._msg_proc(0xABC, win32con.WM_CLOSE, 0, 0)

        mock_display_deps["dw"].assert_called_once_with(0xABC)
        assert result == 0

    def test_unknown_message(self, mock_display_deps) -> None:
        """Unhandled messages fall through to the default window procedure."""
        trigger = DisplayTrigger()
        mock_display_deps["dwp"].return_value = 42

        result = trigger._msg_proc(0xCAFE, 0x0100, 0xDEAD, 0xBEEF)

        mock_display_deps["dwp"].assert_called_once_with(0xCAFE, 0x0100, 0xDEAD, 0xBEEF)
        assert result == 42


class TestDisplayTriggerLifecycle:
    """Tests for start and stop lifecycle."""

    def test_stop_skips_wm_close_when_no_hwnd(self, mock_display_deps) -> None:
        """stop() does not post WM_CLOSE when no window has been created."""
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


class TestDisplayTriggerRun:
    """Tests for the ``run`` method — COM lifecycle and error propagation."""

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
    def test_run_propagates_internal_failure(
        self,
        mock_display_deps: dict[str, MagicMock],
        patch_target: str | None,
        patch_attr: str | None,
    ) -> None:
        """run() propagates exceptions raised by internal steps."""
        trigger = DisplayTrigger()

        if patch_target:
            patcher = patch(patch_target, side_effect=RuntimeError("fail"))
        else:
            assert patch_attr is not None
            patcher = patch.object(trigger, patch_attr, side_effect=RuntimeError("fail"))

        with patcher:
            with pytest.raises(RuntimeError):
                trigger.run()

        mock_display_deps["pythoncom"].CoInitialize.assert_called_once()

    def test_run_unregisterclass_failure_propagates(self, mock_display_deps) -> None:
        """run() propagates a failure from UnregisterClass."""
        trigger = DisplayTrigger()

        with patch(
            "wallpaper_auto.trigger.display_trigger.win32gui.UnregisterClass",
            side_effect=OSError("unregister failed"),
        ):
            with pytest.raises(OSError):
                trigger.run()

        mock_display_deps["pythoncom"].CoInitialize.assert_called_once()


class TestDisplayTriggerErrorPaths:
    """Error paths in _msg_proc and run."""

    def test_setup_window_empty_snapshot_on_query_fail(self, mock_display_deps) -> None:
        """_setup_window leaves _prev_display as an empty frozenset when snapshot fails."""
        trigger = DisplayTrigger()
        mock_display_deps["get_display_info"].return_value = None

        trigger._setup_window()

        # Snapshot returned None → _prev_display becomes empty frozenset
        assert trigger._prev_display == frozenset()

    def test_msg_proc_no_trigger_when_query_fails(self, mock_display_deps) -> None:
        """If get_display_info returns None (query failed), no trigger fires."""
        trigger = DisplayTrigger()
        trigger._prev_display = frozenset({_DISPLAY_INFO_A})
        mock_display_deps["get_display_info"].return_value = None
        with patch.object(trigger, "trigger") as mock_trigger:
            result = trigger._msg_proc(0, win32con.WM_DISPLAYCHANGE, 0, 0)
        mock_trigger.assert_not_called()
        # The failed query must not be treated as "all displays removed".
        assert trigger._prev_display == frozenset({_DISPLAY_INFO_A})
        assert result == 0
