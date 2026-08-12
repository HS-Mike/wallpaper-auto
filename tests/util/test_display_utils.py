"""Tests for display_utils.py — get_display_info() error paths and edge cases."""

import ctypes
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from wallpaper_auto.util import display_utils
from wallpaper_auto.util.display_utils import (
    CDS_UPDATEREGISTRY,
    DISPLAYCONFIG_PATH_MODE_IDX_INVALID,
    ERROR_ACCESS_DENIED,
    ERROR_INSUFFICIENT_BUFFER,
    ERROR_INVALID_PARAMETER,
    ERROR_NOT_SUPPORTED,
    ERROR_SUCCESS,
    DisplayTopologyTransientError,
    RemoteSessionEnvironmentError,
    get_display_capability,
    get_display_info,
    get_display_resolution,
    _get_hmonitor_by_device_name,
    get_display_scale,
    is_remote_session,
    resolve_target_resolution,
    resolve_target_scale_step,
    set_display_resolution,
    set_display_scale,
    set_process_dpi_aware,
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
            def __mul__(self, n):
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
                                self[i].infoType = target_type if i == target_idx else source_type

                return _InitArray

        return _Meta

    class _PathFactory(path_base, metaclass=_make_meta(path_base)):
        pass

    class _ModeFactory(mode_base, metaclass=_make_meta(mode_base)):
        pass

    monkeypatch.setattr(display_utils, "DISPLAYCONFIG_PATH_INFO", _PathFactory)
    monkeypatch.setattr(display_utils, "DISPLAYCONFIG_MODE_INFO", _ModeFactory)


def _install_user32_get_dpi_scaling(
    monkeypatch,
    get_dpi_result: int = ERROR_SUCCESS,
    cur_scale_rel: int = 0,
    min_scale_rel: int = -2,
    max_scale_rel: int = 2,
):
    """Install a user32 mock whose DisplayConfigGetDeviceInfo writes the given
    scaleRel bounds into a DISPLAYCONFIG_GET_DPI_SCALING struct."""
    mock_user32 = MagicMock()

    def _get_device_info(ptr):
        info = ctypes.cast(ptr, ctypes.POINTER(display_utils.DISPLAYCONFIG_GET_DPI_SCALING))[0]
        info.curScaleRel = cur_scale_rel
        info.minScaleRel = min_scale_rel
        info.maxScaleRel = max_scale_rel
        return get_dpi_result

    mock_user32.DisplayConfigGetDeviceInfo.side_effect = _get_device_info
    monkeypatch.setattr(display_utils, "user32", mock_user32)
    return mock_user32


class TestGetDisplayInfoBufferSizesError:
    def test_buffer_sizes_failure_raises(self, monkeypatch):
        _install_user32(monkeypatch, buffer_sizes_result=1)
        with pytest.raises(OSError, match="GetDisplayConfigBufferSizes error return"):
            get_display_info(raise_error=True)


class TestGetDisplayInfoQueryConfigError:
    def test_query_config_non_buffer_error_raises(self, monkeypatch):
        _install_user32(monkeypatch, query_result=1)
        with pytest.raises(OSError, match="QueryDisplayConfig error return"):
            get_display_info(raise_error=True)

    def test_query_config_insufficient_buffer_times_out(self, monkeypatch):
        _install_user32(monkeypatch, query_result=ERROR_INSUFFICIENT_BUFFER)
        monkeypatch.setattr(display_utils.time, "sleep", lambda *_a, **_kw: None)
        with pytest.raises(OSError, match="retry time exceed"):
            get_display_info(raise_error=True)

    def test_query_config_recovers_after_retry(self, monkeypatch):
        _install_user32(monkeypatch, query_result=ERROR_INSUFFICIENT_BUFFER)
        # Patch the call to return SUCCESS on the second invocation.
        calls = {"n": 0}

        def _side(*_a, **_kw):
            calls["n"] += 1
            return ERROR_INSUFFICIENT_BUFFER if calls["n"] == 1 else ERROR_SUCCESS

        monkeypatch.setattr(display_utils.user32, "QueryDisplayConfig", _side)
        monkeypatch.setattr(display_utils.time, "sleep", lambda *_a, **_kw: None)
        # Returns [] because num_paths.value was never set (stays 0).
        assert get_display_info() == []

    def test_query_config_access_denied_raises_transient(self, monkeypatch):
        """ERROR_ACCESS_DENIED from QueryDisplayConfig raises DisplayTopologyTransientError"""
        _install_user32(monkeypatch, query_result=ERROR_ACCESS_DENIED)
        with pytest.raises(DisplayTopologyTransientError):
            get_display_info(raise_error=True)

    def test_query_config_not_supported_raises_transient(self, monkeypatch):
        """ERROR_NOT_SUPPORTED from QueryDisplayConfig raises DisplayTopologyTransientError"""
        _install_user32(monkeypatch, query_result=ERROR_NOT_SUPPORTED)
        with pytest.raises(DisplayTopologyTransientError):
            get_display_info(raise_error=True)

    def test_query_config_invalid_param_remote_session(self, monkeypatch):
        """ERROR_INVALID_PARAMETER in remote session raises RemoteSessionEnvironmentError"""
        _install_user32(monkeypatch, query_result=ERROR_INVALID_PARAMETER)
        monkeypatch.setattr(display_utils, "is_remote_session", lambda: True)
        with pytest.raises(
            RemoteSessionEnvironmentError, match="QueryDisplayConfig is unavailable"
        ):
            get_display_info(raise_error=True)

    def test_query_config_invalid_param_non_remote_raises(self, monkeypatch):
        """ERROR_INVALID_PARAMETER outside remote session raises plain OSError"""
        _install_user32(monkeypatch, query_result=ERROR_INVALID_PARAMETER)
        monkeypatch.setattr(display_utils, "is_remote_session", lambda: False)
        with pytest.raises(OSError, match="QueryDisplayConfig error return"):
            get_display_info(raise_error=True)


class TestGetDisplayInfoPathValidation:
    def test_invalid_source_mode_idx_raises(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=1)
        _stub_paths_and_modes(
            monkeypatch, source_idx=DISPLAYCONFIG_PATH_MODE_IDX_INVALID, target_idx=0
        )
        with pytest.raises(OSError, match="sourceInfo.modeInfoIdx not available"):
            get_display_info(raise_error=True)

    def test_invalid_target_mode_idx_raises(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=2)
        _stub_paths_and_modes(
            monkeypatch, source_idx=0, target_idx=DISPLAYCONFIG_PATH_MODE_IDX_INVALID
        )
        with pytest.raises(OSError, match="targetInfo.modeInfoIdx not available"):
            get_display_info(raise_error=True)


class TestGetDisplayInfoDeviceInfoErrors:
    def test_device_info_not_supported_tolerated(self, monkeypatch):
        # ERROR_NOT_SUPPORTED (no WDDM driver / remote session) leaves the
        # model and monitor device path empty instead of raising.
        _install_user32(
            monkeypatch, num_paths=1, num_modes=2, device_info_result=ERROR_NOT_SUPPORTED
        )
        _stub_paths_and_modes(monkeypatch, source_idx=0, target_idx=1)
        result = get_display_info(raise_error=True)
        assert result is not None
        assert len(result) == 1
        assert result[0].model is None
        assert result[0].monitor_device_path == ""

    def test_device_info_other_error_raises(self, monkeypatch):
        _install_user32(monkeypatch, num_paths=1, num_modes=2, device_info_result=1)
        _stub_paths_and_modes(monkeypatch, source_idx=0, target_idx=1)
        with pytest.raises(OSError, match="DisplayConfigGetDeviceInfo error return"):
            get_display_info(raise_error=True)

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

        monkeypatch.setattr(display_utils.user32, "DisplayConfigGetDeviceInfo", _device_info_empty)

        result = get_display_info()
        assert result is not None
        assert len(result) == 1
        assert result[0].model is None

    def test_dpi_scale_calculation_coverage(self, monkeypatch):
        """Normal DPI scale calculation flow: cover the if h_monitor branch."""
        _install_user32(monkeypatch, num_paths=1, num_modes=2)
        _stub_paths_and_modes(monkeypatch, source_idx=0, target_idx=1)

        # make the monitor-handle lookup return a valid non-zero handle
        monkeypatch.setattr(
            display_utils, "_get_hmonitor_by_device_name", lambda _device_name: 12345
        )

        # Mock GetDpiForMonitor to write 144 DPI into the C pointer
        # 144 / 96.0 = 150% scale
        def _mock_get_dpi(hmonitor, dpi_type, dpi_x_ptr, dpi_y_ptr):
            assert hmonitor == 12345
            ctypes.cast(dpi_x_ptr, ctypes.POINTER(ctypes.c_uint))[0] = 144
            ctypes.cast(dpi_y_ptr, ctypes.POINTER(ctypes.c_uint))[0] = 144
            return 0  # S_OK

        monkeypatch.setattr(ctypes.windll.shcore, "GetDpiForMonitor", _mock_get_dpi)

        result = get_display_info()
        assert result is not None

        assert len(result) == 1
        assert result[0].scale == 150


class TestGetDisplayInfoTolerantDefault:
    """get_display_info() default behavior: log a warning and return None on error."""

    def test_buffer_sizes_failure_returns_none(self, monkeypatch, caplog):
        _install_user32(monkeypatch, buffer_sizes_result=1)
        with caplog.at_level(logging.WARNING, logger="wallpaper_auto.util.display_utils"):
            assert get_display_info() is None
        assert "cannot query displays" in caplog.text

    def test_query_config_access_denied_returns_none(self, monkeypatch):
        _install_user32(monkeypatch, query_result=ERROR_ACCESS_DENIED)
        assert get_display_info() is None

    def test_query_config_not_supported_returns_none(self, monkeypatch):
        _install_user32(monkeypatch, query_result=ERROR_NOT_SUPPORTED)
        assert get_display_info() is None


class TestIsRemoteSession:
    """Tests for is_remote_session()"""

    def test_returns_false_for_local_session(self, monkeypatch):
        """Local session: GetSystemMetrics(SM_REMOTESESSION) returns 0"""
        mock_user32 = MagicMock()
        mock_user32.GetSystemMetrics.return_value = 0
        monkeypatch.setattr(display_utils, "user32", mock_user32)
        assert is_remote_session() is False

    def test_returns_true_for_remote_session(self, monkeypatch):
        """Remote session: GetSystemMetrics(SM_REMOTESESSION) returns non-zero"""
        mock_user32 = MagicMock()
        mock_user32.GetSystemMetrics.return_value = 1
        monkeypatch.setattr(display_utils, "user32", mock_user32)
        assert is_remote_session() is True


class TestSetProcessDpiAware:
    """Tests for set_process_dpi_aware()."""

    def test_success(self, monkeypatch):
        mock_user32 = MagicMock()
        mock_user32.SetProcessDpiAwarenessContext.return_value = 1
        monkeypatch.setattr(display_utils, "user32", mock_user32)
        assert set_process_dpi_aware() is True
        mock_user32.SetProcessDpiAwarenessContext.assert_called_once()
        # c_void_p compares by identity, so compare the underlying value
        # (Per-Monitor DPI Aware V2 context = -4).
        assert (
            mock_user32.SetProcessDpiAwarenessContext.call_args.args[0].value
            == ctypes.c_void_p(-4).value
        )

    def test_already_set_returns_false(self, monkeypatch):
        # Process DPI context already claimed (e.g. by Qt) -> API returns FALSE.
        mock_user32 = MagicMock()
        mock_user32.SetProcessDpiAwarenessContext.return_value = 0
        monkeypatch.setattr(display_utils, "user32", mock_user32)
        assert set_process_dpi_aware() is False


class TestGetHMonitorByDeviceName:
    """Tests for _get_hmonitor_by_device_name()."""

    @staticmethod
    def _install(monkeypatch, device_name_to_write: str):
        mock_win32api = MagicMock()
        mock_win32api.EnumDisplayMonitors.return_value = [(12345, None, (0, 0, 1920, 1080))]
        mock_win32api.GetMonitorInfo.return_value = {"Device": device_name_to_write}
        monkeypatch.setattr(display_utils, "win32api", mock_win32api)

    def test_found_returns_handle(self, monkeypatch):
        self._install(monkeypatch, r"\\.\DISPLAY1")
        assert _get_hmonitor_by_device_name(r"\\.\DISPLAY1") == 12345

    def test_not_found_returns_none(self, monkeypatch):
        self._install(monkeypatch, r"\\.\DISPLAY2")
        assert _get_hmonitor_by_device_name(r"\\.\DISPLAY1") is None


class TestGetMonitorCurrentScale:
    """Tests for get_display_scale()."""

    @staticmethod
    def _install_shcore(monkeypatch, dpi: int, result: int = 0):
        mock_shcore = MagicMock()

        def _get_dpi(_hmon, _dpi_type, dpi_x_ptr, dpi_y_ptr):
            ctypes.cast(dpi_x_ptr, ctypes.POINTER(ctypes.c_uint))[0] = dpi
            ctypes.cast(dpi_y_ptr, ctypes.POINTER(ctypes.c_uint))[0] = dpi
            return result

        mock_shcore.GetDpiForMonitor.side_effect = _get_dpi
        monkeypatch.setattr(display_utils, "shcore", mock_shcore)

    def test_returns_percent(self, monkeypatch):
        monkeypatch.setattr(display_utils, "_get_hmonitor_by_device_name", lambda _d: 12345)
        self._install_shcore(monkeypatch, dpi=120)
        assert get_display_scale(r"\\.\DISPLAY1") == 125

    def test_snaps_to_nearest_supported_scale(self, monkeypatch):
        # 140 DPI -> 145.8% -> nearest supported step is 150
        monkeypatch.setattr(display_utils, "_get_hmonitor_by_device_name", lambda _d: 12345)
        self._install_shcore(monkeypatch, dpi=140)
        assert get_display_scale(r"\\.\DISPLAY1") == 150

    def test_no_handle_returns_none(self, monkeypatch):
        monkeypatch.setattr(display_utils, "_get_hmonitor_by_device_name", lambda _d: None)
        assert get_display_scale(r"\\.\DISPLAY1") is None

    def test_dpi_query_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(display_utils, "_get_hmonitor_by_device_name", lambda _d: 12345)
        self._install_shcore(monkeypatch, dpi=96, result=0x80004005)
        assert get_display_scale(r"\\.\DISPLAY1") is None


class TestSetDisplayResolution:
    """Tests for set_display_resolution()."""

    @staticmethod
    def _mock_user32(monkeypatch, enum_result=1, change_result=ERROR_SUCCESS):
        mock_user32 = MagicMock()
        mock_user32.EnumDisplaySettingsW.return_value = enum_result
        mock_user32.ChangeDisplaySettingsExW.return_value = change_result
        monkeypatch.setattr(display_utils, "user32", mock_user32)
        return mock_user32

    def test_success_default_persistent(self, monkeypatch):
        mock = self._mock_user32(monkeypatch)
        assert set_display_resolution(r"\\.\DISPLAY1", 1920, 1080) is True
        assert mock.ChangeDisplaySettingsExW.call_args.args[3] == CDS_UPDATEREGISTRY

    def test_enum_read_failure_returns_false(self, monkeypatch):
        self._mock_user32(monkeypatch, enum_result=0)
        assert set_display_resolution(r"\\.\DISPLAY1", 1920, 1080) is False

    def test_change_failure_returns_false(self, monkeypatch):
        self._mock_user32(monkeypatch, change_result=ERROR_ACCESS_DENIED)
        assert set_display_resolution(r"\\.\DISPLAY1", 1920, 1080) is False


class TestGetDisplayResolution:
    """Tests for get_display_resolution()."""

    @staticmethod
    def _mock_user32(monkeypatch, enum_result=1, width=1920, height=1080):
        mock_user32 = MagicMock()

        def _enum_settings(_name, _idx, devmode_ptr):
            if not enum_result:
                return False
            devmode = ctypes.cast(devmode_ptr, ctypes.POINTER(display_utils.DEVMODEW))[0]
            devmode.dmPelsWidth = width
            devmode.dmPelsHeight = height
            return True

        mock_user32.EnumDisplaySettingsW.side_effect = _enum_settings
        monkeypatch.setattr(display_utils, "user32", mock_user32)
        return mock_user32

    def test_success_returns_resolution(self, monkeypatch):
        self._mock_user32(monkeypatch, width=2560, height=1440)
        assert get_display_resolution(r"\\.\DISPLAY1") == (2560, 1440)

    def test_read_failure_returns_none(self, monkeypatch, caplog):
        self._mock_user32(monkeypatch, enum_result=0)
        with caplog.at_level(logging.ERROR, logger="wallpaper_auto.util.display_utils"):
            assert get_display_resolution(r"\\.\DISPLAY1") is None
        assert "cannot read current display resolution" in caplog.text


class TestSetDisplayScale:
    """Tests for set_display_scale()."""

    @staticmethod
    def _mock_user32(
        monkeypatch,
        get_dpi_result=ERROR_SUCCESS,
        set_dpi_result=ERROR_SUCCESS,
        min_scale_rel=-2,
        max_scale_rel=2,
    ):
        mock_user32 = _install_user32_get_dpi_scaling(
            monkeypatch,
            get_dpi_result=get_dpi_result,
            min_scale_rel=min_scale_rel,
            max_scale_rel=max_scale_rel,
        )
        mock_user32.DisplayConfigSetDeviceInfo.return_value = set_dpi_result
        return mock_user32

    def test_success(self, monkeypatch):
        mock = self._mock_user32(monkeypatch)
        assert set_display_scale(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1, 1) is True
        ptr = mock.DisplayConfigSetDeviceInfo.call_args.args[0]
        set_dpi = ctypes.cast(ptr, ctypes.POINTER(display_utils.DISPLAYCONFIG_SET_DPI_SCALING))[0]
        assert set_dpi.scaleRel == 1  # unclamped value passed through

    def test_get_dpi_failure(self, monkeypatch):
        self._mock_user32(monkeypatch, get_dpi_result=ERROR_ACCESS_DENIED)
        assert set_display_scale(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1, 1) is False

    def test_out_of_range_clamps_high(self, monkeypatch):
        mock = self._mock_user32(monkeypatch, max_scale_rel=0)
        assert set_display_scale(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1, 1) is True
        ptr = mock.DisplayConfigSetDeviceInfo.call_args.args[0]
        set_dpi = ctypes.cast(ptr, ctypes.POINTER(display_utils.DISPLAYCONFIG_SET_DPI_SCALING))[0]
        assert set_dpi.scaleRel == 0  # clamped from 1 down to max 0

    def test_out_of_range_clamps_low(self, monkeypatch):
        mock = self._mock_user32(monkeypatch, min_scale_rel=0, max_scale_rel=2)
        assert set_display_scale(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1, -1) is True
        ptr = mock.DisplayConfigSetDeviceInfo.call_args.args[0]
        set_dpi = ctypes.cast(ptr, ctypes.POINTER(display_utils.DISPLAYCONFIG_SET_DPI_SCALING))[0]
        assert set_dpi.scaleRel == 0  # clamped from -1 up to min 0

    def test_set_dpi_failure(self, monkeypatch):
        self._mock_user32(monkeypatch, set_dpi_result=ERROR_ACCESS_DENIED)
        assert set_display_scale(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1, 1) is False


class TestGetDisplayCapability:
    """Tests for get_display_capability()."""

    @staticmethod
    def _mock_current_factor(monkeypatch, current_pct):
        monkeypatch.setattr(display_utils, "get_display_scale", lambda _d: current_pct)

    @staticmethod
    def _mock_user32(
        monkeypatch,
        get_dpi_result=ERROR_SUCCESS,
        cur_scale_rel=0,
        min_scale_rel=-2,
        max_scale_rel=2,
        modes=(),
    ):
        mock_user32 = _install_user32_get_dpi_scaling(
            monkeypatch,
            get_dpi_result=get_dpi_result,
            cur_scale_rel=cur_scale_rel,
            min_scale_rel=min_scale_rel,
            max_scale_rel=max_scale_rel,
        )

        def _enum_settings(_name, idx, devmode_ptr):
            if idx >= len(modes):
                return False
            devmode = ctypes.cast(devmode_ptr, ctypes.POINTER(display_utils.DEVMODEW))[0]
            devmode.dmPelsWidth, devmode.dmPelsHeight = modes[idx]
            return True

        mock_user32.EnumDisplaySettingsW.side_effect = _enum_settings
        return mock_user32

    def test_success(self, monkeypatch):
        self._mock_current_factor(monkeypatch, 100)
        self._mock_user32(monkeypatch, modes=[(2560, 1440), (1920, 1080), (1920, 1080)])
        cap = get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1)
        assert cap is not None
        assert cap.scale == (100, 125, 150)
        assert cap.reference_scale == 100
        assert cap.resolution == ((2560, 1440), (1920, 1080))

    def test_remote_session_returns_none(self, monkeypatch):
        # Empty GDI device name (no physical monitor) — not queryable.
        assert get_display_capability("", display_utils.LUID(1, 2), 1) is None

    def test_dpi_query_failure_returns_none(self, monkeypatch):
        self._mock_current_factor(monkeypatch, 100)
        self._mock_user32(monkeypatch, get_dpi_result=ERROR_ACCESS_DENIED)
        assert get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1) is None

    def test_dpi_query_failure_raises(self, monkeypatch):
        self._mock_current_factor(monkeypatch, 100)
        self._mock_user32(monkeypatch, get_dpi_result=ERROR_ACCESS_DENIED)
        with pytest.raises(OSError):
            get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1, raise_error=True)

    def test_current_factor_unknown(self, monkeypatch):
        self._mock_current_factor(monkeypatch, None)
        self._mock_user32(monkeypatch)
        assert get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1) is None

    def test_clamps_scale_range_high(self, monkeypatch):
        self._mock_current_factor(monkeypatch, 100)
        self._mock_user32(monkeypatch, max_scale_rel=0)
        cap = get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1)
        assert cap is not None
        assert cap.scale == (100,)
        assert cap.reference_scale == 100

    def test_clamps_scale_range_low(self, monkeypatch):
        # reference index 1; min_scale_rel=-10 would give index -9, clamped up to 0
        self._mock_current_factor(monkeypatch, 125)
        self._mock_user32(monkeypatch, min_scale_rel=-10)
        cap = get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1)
        assert cap is not None
        assert cap.scale == (100, 125, 150, 175)
        assert cap.reference_scale == 125

    def test_reference_scale_with_nonzero_cur_scale_rel(self, monkeypatch):
        # factor 1.5 -> index 2; cur_scale_rel=1 -> reference index 1 -> 125%
        self._mock_current_factor(monkeypatch, 150)
        self._mock_user32(monkeypatch, cur_scale_rel=1)
        cap = get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1)
        assert cap is not None
        assert cap.reference_scale == 125

    def test_reference_index_out_of_bounds_raises(self, monkeypatch):
        # current 100% (index 0) with cur_scale_rel=1 -> reference index -1
        self._mock_current_factor(monkeypatch, 100)
        self._mock_user32(monkeypatch, cur_scale_rel=1)
        with pytest.raises(ValueError, match="out of bounds"):
            get_display_capability(r"\\.\DISPLAY1", display_utils.LUID(1, 2), 1)


class TestResolveTargetScaleStep:
    """Tests for resolve_target_scale_step()."""

    @pytest.mark.parametrize(
        "reference_scale,target_scale,support_scales,expected",
        [
            (125, 125, [100, 125, 150], 0),  # target equals reference
            (125, 100, [100, 125, 150], -1),  # target below reference
            (125, 140, [100, 125, 150], 1),  # 140 is closer to 150 than to 125
            (100, 400, [100, 125, 150], 2),  # out of range snaps to end
            (125, 150, (100, 125, 150), 1),  # tuple input, target directly supported
            (125, 140, (100, 125, 150), 1),  # tuple input, non-supported snaps
        ],
    )
    def test_resolves_step(self, reference_scale, target_scale, support_scales, expected):
        assert resolve_target_scale_step(reference_scale, target_scale, support_scales) == expected


class TestResolveTargetResolution:
    """Tests for resolve_target_resolution()."""

    @pytest.mark.parametrize(
        "target_resolution,support_resolutions,expected",
        [
            ((1920, 1080), [(1920, 1080), (2560, 1440)], (1920, 1080)),  # exact match
            # summed relative deviation favors 1920x1080 over 1280x720
            ((2560, 1440), [(1920, 1080), (1280, 720)], (1920, 1080)),
        ],
    )
    def test_resolves_resolution(self, target_resolution, support_resolutions, expected):
        assert resolve_target_resolution(target_resolution, support_resolutions) == expected


class TestDisplayId:
    """Tests for DisplayId — a standalone, opaque display identity type."""

    def test_is_a_standalone_runtime_type(self):
        did = display_utils.DisplayId(42)
        # A real class, not an int alias/subclass — visible to isinstance/the debugger.
        assert isinstance(did, display_utils.DisplayId)
        assert not isinstance(did, int)
        assert repr(did) == "DisplayId(42)"
        # Still usable as a dictionary key.
        assert {did: "value"}[did] == "value"
        assert did == display_utils.DisplayId(42)


class TestMakeDisplayId:
    """Tests for make_display_id() — stability, change detection, volatility."""

    def _info(self, **overrides) -> display_utils.DisplayInfo:
        defaults: dict[str, Any] = dict(
            device_name=r"\\.\DISPLAY1",
            model="test",
            source_resolution=(1920, 1080),
            position=(0, 0),
            target_resolution=(1920, 1080),
            scale=100,
            monitor_device_path=r"\\?\DISPLAY#TEST#1",
            adapter_id=display_utils.LUID(1, 2),
            source_id=3,
        )
        defaults.update(overrides)
        return display_utils.DisplayInfo(**defaults)

    def test_stable_for_identical_identifiers(self):
        assert self._info().display_id == self._info().display_id

    @pytest.mark.parametrize(
        "override",
        [
            {"monitor_device_path": r"\\?\DISPLAY#TEST#2"},
            {"device_name": r"\\.\DISPLAY2"},
            {"adapter_id": display_utils.LUID(9, 9)},
            {"source_id": 7},
        ],
    )
    def test_changes_when_identifier_field_changes(self, override):
        assert self._info(**override).display_id != self._info().display_id

    def test_ignores_volatile_snapshot_fields(self):
        # Resolution/scale/position changes must not invalidate the id.
        volatile = self._info(source_resolution=(1280, 720), scale=150, position=(100, 0))
        assert volatile.display_id == self._info().display_id
