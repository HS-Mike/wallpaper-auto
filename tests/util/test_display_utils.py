"""Tests for display_utils.py — get_display_info() error paths and edge cases."""

import ctypes
import logging
from unittest.mock import MagicMock

import pytest

from wallpaper_auto.util import display_utils
from wallpaper_auto.util.display_utils import (
    DISPLAYCONFIG_PATH_MODE_IDX_INVALID,
    DisplayTopologyTransientError,
    ERROR_INSUFFICIENT_BUFFER,
    ERROR_NOT_SUPPORTED,
    ERROR_SUCCESS,
    get_display_info,
    get_all_monitors_dpi_snapshot,
)


def _install_user32(
    monkeypatch,
    buffer_sizes_result: int = ERROR_SUCCESS,
    query_result: int = ERROR_SUCCESS,
    device_info_result: int = ERROR_SUCCESS,
    num_paths: int = 0,
    num_modes: int = 0,
):
    """Replace user32 with a shim that returns the given HRESULTs and
    writes num_paths/num_modes into the byref'd UINTs."""

    def _buffer_sizes(*args):
        # args = (QDC_DATABASE_CURRENT, byref(num_paths), byref(num_modes))
        ctypes.cast(args[1], ctypes.POINTER(ctypes.c_uint))[0] = num_paths
        ctypes.cast(args[2], ctypes.POINTER(ctypes.c_uint))[0] = num_modes
        return buffer_sizes_result

    def _query(*args):
        return query_result

    def _device_info(*args):
        return device_info_result

    monkeypatch.setattr(ctypes.windll.user32, "GetDisplayConfigBufferSizes", _buffer_sizes)
    monkeypatch.setattr(ctypes.windll.user32, "QueryDisplayConfig", _query)
    monkeypatch.setattr(ctypes.windll.user32, "DisplayConfigGetDeviceInfo", _device_info)
    monkeypatch.setattr(ctypes.windll.user32, "MonitorFromPoint", lambda *_a, **_kw: 0)

    monkeypatch.setattr(display_utils, "user32", ctypes.windll.user32)
    monkeypatch.setattr(display_utils, "shcore", ctypes.windll.shcore)


def _stub_paths_and_modes(monkeypatch, source_idx: int, target_idx: int):
    """Replace DISPLAYCONFIG_PATH_INFO and DISPLAYCONFIG_MODE_INFO so the iteration
    over paths can find valid SOURCE and TARGET mode entries.

    The mode array is allocated by the function as
    ``modes = (DISPLAYCONFIG_MODE_INFO * num_modes.value)()``. We ensure each
    entry has the correct infoType for its expected access pattern.
    """

    path_base = display_utils.DISPLAYCONFIG_PATH_INFO
    mode_base = display_utils.DISPLAYCONFIG_MODE_INFO
    base_meta = type(path_base)
    source_type = display_utils.DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE
    target_type = display_utils.DISPLAYCONFIG_MODE_INFO_TYPE_TARGET

    def _make_meta(base):
        class _Meta(base_meta):
            def __mul__(cls, n):
                class _InitArray(base * n):
                    if base is path_base:
                        def __init__(self):
                            for i in range(len(self)):
                                self[i].sourceInfo.modeInfoIdx = source_idx
                                self[i].targetInfo.modeInfoIdx = target_idx
                    else:
                    # modes array — set each entry's infoType to SOURCE; the function
                    # asserts SOURCE on first read and TARGET on second.
                    # class _InitArray(base * n):
                        def __init__(self):
                            for i in range(len(self)):
                                # Alternate types so reading mode[0] as source and
                                # mode[target_idx] as target both pass the asserts
                                # when target_idx != source_idx.
                                self[i].infoType = (
                                    target_type if i == target_idx else source_type
                                )
                return _InitArray

        return _Meta

    class _PathFactory(path_base, metaclass=_make_meta(path_base)):
        pass

    class _ModeFactory(mode_base, metaclass=_make_meta(mode_base)):
        pass

    monkeypatch.setattr(display_utils, "DISPLAYCONFIG_PATH_INFO", _PathFactory)
    monkeypatch.setattr(display_utils, "DISPLAYCONFIG_MODE_INFO", _ModeFactory)


class TestGetDisplayInfoBufferSizesError:
    def test_buffer_sizes_failure_raises(self, monkeypatch):
        _install_user32(monkeypatch, buffer_sizes_result=1)
        with pytest.raises(OSError, match="GetDisplayConfigBufferSizes error return"):
            get_display_info()


class TestGetDisplayInfoQueryConfigError:
    def test_query_config_non_buffer_error_raises(self, monkeypatch):
        _install_user32(monkeypatch, query_result=1)
        with pytest.raises(OSError, match="QueryDisplayConfig error return"):
            get_display_info()

    def test_query_config_insufficient_buffer_times_out(self, monkeypatch):
        _install_user32(monkeypatch, query_result=ERROR_INSUFFICIENT_BUFFER)
        monkeypatch.setattr(display_utils.time, "sleep", lambda *_a, **_kw: None)
        with pytest.raises(OSError, match="retry time exceed"):
            get_display_info()

    def test_query_config_recovers_after_retry(self, monkeypatch):
        _install_user32(monkeypatch, query_result=ERROR_INSUFFICIENT_BUFFER)
        # Patch the call to return SUCCESS on the second invocation.
        original = display_utils.user32.QueryDisplayConfig
        calls = {"n": 0}

        def _side(*_a, **_kw):
            calls["n"] += 1
            return ERROR_INSUFFICIENT_BUFFER if calls["n"] == 1 else ERROR_SUCCESS

        monkeypatch.setattr(display_utils.user32, "QueryDisplayConfig", _side)
        monkeypatch.setattr(display_utils.time, "sleep", lambda *_a, **_kw: None)
        # Returns [] because num_paths.value was never set (stays 0).
        assert get_display_info() == []


class TestGetDisplayInfoPathValidation:
    def test_invalid_source_mode_idx_raises(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=1)
        _stub_paths_and_modes(
            monkeypatch, source_idx=DISPLAYCONFIG_PATH_MODE_IDX_INVALID, target_idx=0
        )
        with pytest.raises(OSError, match="sourceInfo.modeInfoIdx not available"):
            get_display_info()

    def test_invalid_target_mode_idx_raises(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=2)
        _stub_paths_and_modes(
            monkeypatch, source_idx=0, target_idx=DISPLAYCONFIG_PATH_MODE_IDX_INVALID
        )
        with pytest.raises(OSError, match="targetInfo.modeInfoIdx not available"):
            get_display_info()


class TestGetDisplayInfoDeviceInfoErrors:
    def test_device_info_not_supported_raises_transient(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=2, device_info_result=ERROR_NOT_SUPPORTED)
        _stub_paths_and_modes(monkeypatch, source_idx=0, target_idx=1)
        with pytest.raises(DisplayTopologyTransientError, match="Not Supported"):
            get_display_info()

    def test_device_info_other_error_raises(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=2, device_info_result=1)
        _stub_paths_and_modes(monkeypatch, source_idx=0, target_idx=1)
        with pytest.raises(OSError, match="DisplayConfigGetDeviceInfo error return"):
            get_display_info()

    def test_empty_friendly_name_becomes_none(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=2)
        _stub_paths_and_modes(monkeypatch, source_idx=0, target_idx=1)

        def _device_info_empty(*args):
            ptr = ctypes.cast(
                args[0],
                ctypes.POINTER(display_utils.DISPLAYCONFIG_TARGET_DEVICE_NAME),
            )
            ptr[0].monitorFriendlyDeviceName = ""
            return ERROR_SUCCESS

        monkeypatch.setattr(
            display_utils.user32, "DisplayConfigGetDeviceInfo", _device_info_empty
        )

        result = get_display_info()
        assert len(result) == 1
        assert result[0].model is None


class TestGetAllMonitorsDpiSnapshot:
    
    def test_success_multiple_monitors(self, monkeypatch):
        """Normal multi-monitor flow: successfully get coordinates and DPI for all monitors."""
        # 1. Mock win32api.EnumDisplayMonitors to return two monitors
        # Use plain integer handles (12345 and 67890) so int(hmonitor)
        # works without PyHANDLE complexity.
        mock_monitors = [
            (12345, None, (0, 0, 1920, 1080)),
            (67890, None, (1920, 0, 2560, 1440)),
        ]
        mock_win32api = MagicMock()
        mock_win32api.EnumDisplayMonitors.return_value = mock_monitors
        monkeypatch.setattr(display_utils, "win32api", mock_win32api)

        # 2. Mock shcore.GetDpiForMonitor to write data into C pointers
        # Build a mapping from handles to expected DPI values
        dpi_map = {12345: 96, 67890: 144}

        def _mock_get_dpi(hmonitor, dpi_type, dpi_x_ptr, dpi_y_ptr):
            dpi = dpi_map.get(hmonitor, 96)
            # Write test data into the provided UINT pointer via ctypes.cast
            ctypes.cast(dpi_x_ptr, ctypes.POINTER(ctypes.c_uint))[0] = dpi
            ctypes.cast(dpi_y_ptr, ctypes.POINTER(ctypes.c_uint))[0] = dpi
            return 0  # S_OK (success)

        mock_shcore = MagicMock()
        mock_shcore.GetDpiForMonitor = _mock_get_dpi
        monkeypatch.setattr(display_utils, "shcore", mock_shcore)

        # 3. Call the target function and assert
        result = get_all_monitors_dpi_snapshot()
        
        expected = frozenset([
            ((0, 0, 1920, 1080), 96),
            ((1920, 0, 2560, 1440), 144)
        ])
        assert result == expected

    def test_get_dpi_partially_fails(self, monkeypatch):
        """Partial failure flow: one monitor fails to get DPI, that monitor should be skipped."""
        mock_monitors = [
            (12345, None, (0, 0, 1920, 1080)),
            (67890, None, (1920, 0, 2560, 1440)),
        ]
        mock_win32api = MagicMock()
        mock_win32api.EnumDisplayMonitors.return_value = mock_monitors
        monkeypatch.setattr(display_utils, "win32api", mock_win32api)

        def _mock_get_dpi(hmonitor, dpi_type, dpi_x_ptr, dpi_y_ptr):
            if hmonitor == 67890:
                return 0x80004005  # Simulate Win32 error code E_FAIL
            
            ctypes.cast(dpi_x_ptr, ctypes.POINTER(ctypes.c_uint))[0] = 96
            return 0

        mock_shcore = MagicMock()
        mock_shcore.GetDpiForMonitor = _mock_get_dpi
        monkeypatch.setattr(display_utils, "shcore", mock_shcore)

        # Execute: 67890 should be filtered out, leaving only 12345
        result = get_all_monitors_dpi_snapshot()
        
        expected = frozenset([
            ((0, 0, 1920, 1080), 96)
        ])
        assert result == expected

    def test_enum_monitors_exception_returns_empty(self, monkeypatch, caplog):
        """Exception flow: when win32api raises, the function should catch, log, and return an empty set."""
        mock_win32api = MagicMock()
        # Force the iterator to raise an exception
        mock_win32api.EnumDisplayMonitors.side_effect = Exception("OS driver detached")
        monkeypatch.setattr(display_utils, "win32api", mock_win32api)

        # Use caplog to capture log output
        with caplog.at_level(logging.ERROR):
            result = get_all_monitors_dpi_snapshot()
        
        # Assert result is an empty set with expected error message in the log
        assert result == frozenset()
        assert "Failed to query all monitors DPI" in caplog.text
