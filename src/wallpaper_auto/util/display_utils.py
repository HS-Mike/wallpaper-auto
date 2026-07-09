import logging
import time
import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import win32api


logger = logging.getLogger(__name__)


class DisplayTopologyTransientError(OSError):
    """Raised when the system is in a display topology transition period (e.g., RDP switching, sleep/wake)."""
    pass

class RemoteSessionEnvironmentError(OSError):
    """Raised when an operation fails because it is running within an RDP/Virtual Desktop session instead of a physical console."""
    pass


@dataclass(frozen=True)
class DisplayInfo:
    model: str | None
    source_resolution: tuple[int, int]
    position: tuple[int, int]
    target_resolution: tuple[int, int]
    scale: float = 1.0
    monitor_device_path: str = ""


ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5
ERROR_NOT_SUPPORTED = 50
ERROR_INVALID_PARAMETER = 87
ERROR_INSUFFICIENT_BUFFER = 122

QDC_DATABASE_CURRENT = 0x00000004
DISPLAYCONFIG_PATH_MODE_IDX_INVALID = 0xFFFFFFFF
DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE = 1
DISPLAYCONFIG_MODE_INFO_TYPE_TARGET = 2

DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.UINT),
        ("size", wintypes.UINT),
        ("adapterId", LUID),
        ("id", wintypes.UINT),
    ]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("flags", wintypes.UINT),
        ("outputTechnology", wintypes.UINT),
        ("edidManufactureId", wintypes.USHORT),
        ("edidProductCodeId", wintypes.USHORT),
        ("connectorInstance", wintypes.UINT),
        (
            "monitorFriendlyDeviceName",
            wintypes.WCHAR * 64,
        ),
        (
            "monitorDevicePath",
            wintypes.WCHAR * 128,
        ),
    ]


class POINTL(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class DISPLAYCONFIG_SOURCE_MODE(ctypes.Structure):
    _fields_ = [
        ("width", wintypes.UINT),
        ("height", wintypes.UINT),
        ("pixelFormat", wintypes.UINT),
        ("position", POINTL),
    ]

class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class DISPLAYCONFIG_2DREGION(ctypes.Structure):
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]


class DISPLAYCONFIG_VIDEO_SIGNAL_INFO(ctypes.Structure):
    _fields_ = [
        ("pixelRate", wintypes.ULARGE_INTEGER),
        ("hSyncFreq", DISPLAYCONFIG_RATIONAL),
        ("vSyncFreq", DISPLAYCONFIG_RATIONAL),
        ("activeSize", DISPLAYCONFIG_2DREGION),
        ("totalSize", DISPLAYCONFIG_2DREGION),
        ("videoStandard", wintypes.UINT),
        ("scanLineOrdering", wintypes.UINT),
    ]

class DISPLAYCONFIG_TARGET_MODE(ctypes.Structure):
    _fields_ = [("videoSignalInfo", DISPLAYCONFIG_VIDEO_SIGNAL_INFO)]

class _MODE_INFO_UNION(ctypes.Union):
    _fields_ = [
        ("sourceMode", DISPLAYCONFIG_SOURCE_MODE),
        ("targetMode", DISPLAYCONFIG_TARGET_MODE),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("infoType", wintypes.UINT),
        ("id", wintypes.UINT),
        ("adapterId", LUID),
        ("mode", _MODE_INFO_UNION),
    ]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("statusFlags", wintypes.UINT),
    ]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("outputTechnology", wintypes.UINT),
        ("rotation", wintypes.UINT),
        ("scaling", wintypes.UINT),
        ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", wintypes.UINT),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", wintypes.UINT),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", wintypes.UINT),
    ]


user32 = ctypes.windll.user32
shcore = ctypes.windll.shcore

user32.DisplayConfigGetDeviceInfo.argtypes = [
    ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)
]
user32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG

user32.MonitorFromPoint.argtypes = [POINTL, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE

shcore.GetDpiForMonitor.argtypes = [
    wintypes.HANDLE, ctypes.c_int,
    ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
]
shcore.GetDpiForMonitor.restype = wintypes.LONG


def get_display_info() -> list[DisplayInfo]:
    num_paths = wintypes.UINT(0)
    num_modes = wintypes.UINT(0)

    res_display_info = []

    for i in range(5):
        res = user32.GetDisplayConfigBufferSizes(
        QDC_DATABASE_CURRENT, ctypes.byref(num_paths), ctypes.byref(num_modes)
        )

        if res != ERROR_SUCCESS:
            raise OSError(f"GetDisplayConfigBufferSizes error return: {res}")

        paths = (DISPLAYCONFIG_PATH_INFO * num_paths.value)()
        modes = (DISPLAYCONFIG_MODE_INFO * num_modes.value)()
        topo_id = wintypes.UINT(0)
        
        res = user32.QueryDisplayConfig(
            QDC_DATABASE_CURRENT,
            ctypes.byref(num_paths),
            paths,
            ctypes.byref(num_modes),
            modes,
            ctypes.byref(topo_id),
        )
        if res == ERROR_SUCCESS:
            break
        if res == ERROR_ACCESS_DENIED:
            return []
        elif res == ERROR_INSUFFICIENT_BUFFER:
            time.sleep(0.5)
            continue
        elif res == ERROR_NOT_SUPPORTED:
            raise DisplayTopologyTransientError
        else:
            if res == ERROR_INVALID_PARAMETER and is_remote_session():
                raise RemoteSessionEnvironmentError("QueryDisplayConfig is unavailable under remote sessions.")
            raise OSError(f"QueryDisplayConfig error return: {res}")
    else:
        raise OSError(f"QueryDisplayConfig error return: {ERROR_INSUFFICIENT_BUFFER} - retry time exceed")

    for i in range(num_paths.value):
        p = paths[i]

        # 1. extract soruce mode resolution and display position in canvas
        source_mode_idx = p.sourceInfo.modeInfoIdx
        source_resolution = None
        position = None
        if source_mode_idx == DISPLAYCONFIG_PATH_MODE_IDX_INVALID:
            raise OSError(f"DISPLAYCONFIG_PATH_INFO.sourceInfo.modeInfoIdx not available")
        mode = modes[source_mode_idx]
        assert mode.infoType == DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE
        w: int = mode.mode.sourceMode.width
        h: int = mode.mode.sourceMode.height
        source_resolution = (w, h)
        pos_x: int = mode.mode.sourceMode.position.x
        pos_y: int = mode.mode.sourceMode.position.y
        position = (pos_x, pos_y)

        # 2. extract target mode resolution
        target_mode_idx = p.targetInfo.modeInfoIdx
        target_resolution = None
        if target_mode_idx == DISPLAYCONFIG_PATH_MODE_IDX_INVALID:
            raise OSError(f"DISPLAYCONFIG_PATH_INFO.targetInfo.modeInfoIdx not available")
        mode = modes[target_mode_idx]
        assert mode.infoType == DISPLAYCONFIG_MODE_INFO_TYPE_TARGET
        w: int = mode.mode.targetMode.videoSignalInfo.activeSize.cx
        h: int = mode.mode.targetMode.videoSignalInfo.activeSize.cy
        target_resolution = (w, h)

        # 3. use adapterId and targetId extract monitor name and monitor device path
        device_name_info = DISPLAYCONFIG_TARGET_DEVICE_NAME()
        device_name_info.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
        device_name_info.header.size = ctypes.sizeof(
            DISPLAYCONFIG_TARGET_DEVICE_NAME
        )
        device_name_info.header.adapterId = p.targetInfo.adapterId
        device_name_info.header.id = p.targetInfo.id

        res = user32.DisplayConfigGetDeviceInfo(
            ctypes.byref(device_name_info.header)
        )

        if res == ERROR_SUCCESS:
            friendly_name = device_name_info.monitorFriendlyDeviceName
            if friendly_name == "":
                friendly_name = None
            monitor_device_path = device_name_info.monitorDevicePath
        elif res == ERROR_NOT_SUPPORTED:
            raise DisplayTopologyTransientError("Windows QueryDisplayConfig returned 50: Not Supported.")
        else:
            raise OSError(f"DisplayConfigGetDeviceInfo error return: {res}")
        
        # 4. compute per-monitor DPI scale factor via position-based HMONITOR lookup
        pt = POINTL(pos_x, pos_y)
        h_monitor = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        scale = 1.0
        if h_monitor:
            dpi_x = wintypes.UINT(0)
            dpi_y = wintypes.UINT(0)
            if shcore.GetDpiForMonitor(h_monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                scale = round(dpi_x.value / 96.0, 2)

        res_display_info.append(
            DisplayInfo(
                model=friendly_name,
                source_resolution=source_resolution,
                position=position,
                target_resolution=target_resolution,
                scale=scale,
                monitor_device_path=monitor_device_path
            )
        )

    return res_display_info


def is_remote_session() -> bool:
    """Check if the current process is running under an RDP or virtual remote session."""
    return win32api.GetSystemMetrics(4096) != 0


MDT_EFFECTIVE_DPI = 0


def get_all_monitors_dpi_snapshot() -> frozenset[tuple[tuple[int, int, int, int], int]]:
    """Return a frozenset of (bounding_rect, dpi) tuples for all active monitors."""
    dpi_snapshot: list[tuple[tuple[int, int, int, int], int]] = []
    try:
        for hmonitor, _hdc, rect in win32api.EnumDisplayMonitors():
            rect_tuple = (rect[0], rect[1], rect[2], rect[3])
            dpi_x = wintypes.UINT(0)
            dpi_y = wintypes.UINT(0)
            # int(hmonitor) is required — win32api.EnumDisplayMonitors()
            # returns PyHANDLE wrappers, not primitive ints.  ctypes can't
            # auto-convert PyHANDLE and raises a silent ArgumentError.
            hr = shcore.GetDpiForMonitor(
                int(hmonitor),
                MDT_EFFECTIVE_DPI,
                ctypes.byref(dpi_x),
                ctypes.byref(dpi_y),
            )
            if hr == 0:
                dpi_snapshot.append((rect_tuple, dpi_x.value))
    except Exception as e:
        logger.error(f"Failed to query all monitors DPI: {e}")
    return frozenset(dpi_snapshot)
        