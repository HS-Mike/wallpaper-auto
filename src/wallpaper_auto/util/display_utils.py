"""Windows display helpers: topology query, per-monitor DPI, resolution/scale control.

``DisplayInfo`` is a point-in-time snapshot of display topology. Every field --
``device_name``, ``adapter_id``, ``source_id``, resolutions, ``position``,
``scale`` -- is dynamic and can change at runtime (monitor plug/unplug,
resolution/scale changes, RDP, driver reset). Do not hold a ``DisplayInfo``
across a topology change and act on it: re-query ``get_display_info()`` first
(see ``DisplayTrigger`` / ``WM_DISPLAYCHANGE``). ``get_display_info()``
returns ``None`` on transient failures (e.g. topology transitions); pass
``raise_error=True`` to propagate the typed ``OSError`` instead.

DPI queries (``get_monitor_current_scale``, ``get_display_capability``) call
``GetDpiForMonitor``, which returns physical pixels only on a Per-Monitor DPI
Aware thread. Declare it once at startup via ``set_process_dpi_aware()``, or
set thread-level awareness on the worker threads that query DPI.

``DisplayCapability`` lists the scale percentages and resolutions a display
supports. Functions that take ``adapter_id`` / ``source_id`` expect the
current values, supplied by the caller.
"""

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass

import win32api

logger = logging.getLogger(__name__)


class DisplayTopologyTransientError(OSError):
    """Raised when system is in a display topology transition period (RDP switching, sleep/wake)."""

    pass


class RemoteSessionEnvironmentError(OSError):
    """Raised when an operation fails in an RDP or virtual desktop session."""

    pass


ERROR_SUCCESS = 0
ERROR_ACCESS_DENIED = 5
ERROR_NOT_SUPPORTED = 50
ERROR_INVALID_PARAMETER = 87
ERROR_INSUFFICIENT_BUFFER = 122

QDC_ONLY_ACTIVE_PATHS = 0x00000002
QDC_DATABASE_CURRENT = 0x00000004

DISPLAYCONFIG_PATH_MODE_IDX_INVALID = 0xFFFFFFFF
DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE = 1
DISPLAYCONFIG_MODE_INFO_TYPE_TARGET = 2

DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALING = 0xFFFFFFFD  # (UINT)-3
DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALING = 0xFFFFFFFC  # (UINT)-4

DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
ENUM_CURRENT_SETTINGS = -1
CDS_UPDATEREGISTRY = 0x00000001

SM_REMOTESESSION = 0x1000  # 4096


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("type", wintypes.UINT),
        ("size", wintypes.UINT),
        ("adapterId", LUID),
        ("id", wintypes.UINT),
    ]


class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("viewGdiDeviceName", wintypes.WCHAR * 32),
    ]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("flags", wintypes.UINT),
        ("outputTechnology", wintypes.UINT),
        ("edidManufactureId", wintypes.USHORT),
        ("edidProductCodeId", wintypes.USHORT),
        ("connectorInstance", wintypes.UINT),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
    ]


class DISPLAYCONFIG_GET_DPI_SCALING(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("minScaleRel", ctypes.c_int32),
        ("curScaleRel", ctypes.c_int32),
        ("maxScaleRel", ctypes.c_int32),
    ]


class DISPLAYCONFIG_SET_DPI_SCALING(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("scaleRel", ctypes.c_int32),
    ]


class POINTL(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class DEVMODEW(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


class DISPLAYCONFIG_SOURCE_MODE(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("width", wintypes.UINT),
        ("height", wintypes.UINT),
        ("pixelFormat", wintypes.UINT),
        ("position", POINTL),
    ]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):  # noqa: N801
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class DISPLAYCONFIG_2DREGION(ctypes.Structure):  # noqa: N801
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]


class DISPLAYCONFIG_VIDEO_SIGNAL_INFO(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("pixelRate", wintypes.ULARGE_INTEGER),
        ("hSyncFreq", DISPLAYCONFIG_RATIONAL),
        ("vSyncFreq", DISPLAYCONFIG_RATIONAL),
        ("activeSize", DISPLAYCONFIG_2DREGION),
        ("totalSize", DISPLAYCONFIG_2DREGION),
        ("videoStandard", wintypes.UINT),
        ("scanLineOrdering", wintypes.UINT),
    ]


class DISPLAYCONFIG_TARGET_MODE(ctypes.Structure):  # noqa: N801
    _fields_ = [("videoSignalInfo", DISPLAYCONFIG_VIDEO_SIGNAL_INFO)]


class _MODE_INFO_UNION(ctypes.Union):  # noqa: N801
    _fields_ = [
        ("sourceMode", DISPLAYCONFIG_SOURCE_MODE),
        ("targetMode", DISPLAYCONFIG_TARGET_MODE),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("infoType", wintypes.UINT),
        ("id", wintypes.UINT),
        ("adapterId", LUID),
        ("mode", _MODE_INFO_UNION),
    ]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("adapterId", LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("statusFlags", wintypes.UINT),
    ]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):  # noqa: N801
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


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", wintypes.UINT),
    ]


user32 = ctypes.windll.user32
shcore = ctypes.windll.shcore

user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)]
user32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG

user32.DisplayConfigSetDeviceInfo.argtypes = [ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)]
user32.DisplayConfigSetDeviceInfo.restype = wintypes.LONG

user32.MonitorFromPoint.argtypes = [POINTL, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE

shcore.GetDpiForMonitor.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(wintypes.UINT),
]
shcore.GetDpiForMonitor.restype = wintypes.LONG

user32.GetDisplayConfigBufferSizes.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(wintypes.UINT),
]
user32.GetDisplayConfigBufferSizes.restype = wintypes.LONG

user32.QueryDisplayConfig.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
    ctypes.POINTER(wintypes.UINT),
]
user32.QueryDisplayConfig.restype = wintypes.LONG

user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
user32.EnumDisplaySettingsW.restype = wintypes.BOOL

user32.ChangeDisplaySettingsExW.argtypes = [
    wintypes.LPCWSTR,
    ctypes.POINTER(DEVMODEW),
    wintypes.HWND,
    wintypes.DWORD,
    wintypes.LPVOID,
]
user32.ChangeDisplaySettingsExW.restype = wintypes.LONG

user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL


def set_process_dpi_aware() -> bool:
    """Declare Per-Monitor DPI Aware V2 so display APIs return physical pixels.

    Returns True if the process DPI awareness was set, False if it was already
    set by someone else (e.g. Qt's ``QApplication``) or the call failed.
    """
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    return bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))


def is_remote_session() -> bool:
    """Check if current process is running under an RDP or Remote Desktop session."""
    return bool(user32.GetSystemMetrics(SM_REMOTESESSION))


@dataclass(frozen=True)
class DisplayInfo:
    device_name: str  # GDI device path, e.g. \\.\DISPLAY1
    model: str | None
    source_resolution: tuple[int, int]
    position: tuple[int, int]
    target_resolution: tuple[int, int]
    scale: int = 100  # scale percentage (e.g. 175)
    monitor_device_path: str = ""  # IDesktopWallpaper monitorDevicePath
    adapter_id: LUID | None = None
    source_id: int = 0


@dataclass(frozen=True)
class DisplayCapability:
    scale: tuple[int, ...]  # supported scale percentages, ascending
    reference_scale: int  # monitor's recommended scale percentage
    resolution: tuple[tuple[int, int], ...]  # supported resolutions, descending


def get_display_info(raise_error: bool = False) -> list[DisplayInfo] | None:
    """Return the list of connected displays."""
    try:
        return _get_display_info()
    except OSError as e:
        if raise_error:
            raise
        logger.warning(f"cannot query displays: {e}")
        return None


def _get_display_info() -> list[DisplayInfo]:
    num_paths = wintypes.UINT(0)
    num_modes = wintypes.UINT(0)

    res_display_info = []

    for i in range(5):
        res = user32.GetDisplayConfigBufferSizes(
            QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), ctypes.byref(num_modes)
        )

        if res != ERROR_SUCCESS:
            raise OSError(f"GetDisplayConfigBufferSizes error return: {res}")

        paths = (DISPLAYCONFIG_PATH_INFO * num_paths.value)()
        modes = (DISPLAYCONFIG_MODE_INFO * num_modes.value)()

        res = user32.QueryDisplayConfig(
            QDC_ONLY_ACTIVE_PATHS,
            ctypes.byref(num_paths),
            paths,
            ctypes.byref(num_modes),
            modes,
            None,  # pCurrentTopologyId must be NULL for QDC_ONLY_ACTIVE_PATHS
        )
        if res == ERROR_SUCCESS:
            break
        if res == ERROR_ACCESS_DENIED:
            raise DisplayTopologyTransientError(
                "QueryDisplayConfig: ERROR_ACCESS_DENIED (topology transition in progress)"
            )
        elif res == ERROR_INSUFFICIENT_BUFFER:
            time.sleep(0.5)
            continue
        elif res == ERROR_NOT_SUPPORTED:
            raise DisplayTopologyTransientError
        else:
            if res == ERROR_INVALID_PARAMETER and is_remote_session():
                raise RemoteSessionEnvironmentError(
                    "QueryDisplayConfig is unavailable under remote sessions."
                )
            raise OSError(f"QueryDisplayConfig error return: {res}")
    else:
        raise OSError(
            f"QueryDisplayConfig error return: {ERROR_INSUFFICIENT_BUFFER} - retry time exceed"
        )

    for i in range(num_paths.value):
        p = paths[i]

        # 1. extract source mode resolution and display position in canvas
        source_mode_idx = p.sourceInfo.modeInfoIdx
        if source_mode_idx == DISPLAYCONFIG_PATH_MODE_IDX_INVALID:
            raise OSError("DISPLAYCONFIG_PATH_INFO.sourceInfo.modeInfoIdx not available")
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
        if target_mode_idx == DISPLAYCONFIG_PATH_MODE_IDX_INVALID:
            raise OSError("DISPLAYCONFIG_PATH_INFO.targetInfo.modeInfoIdx not available")
        mode = modes[target_mode_idx]
        assert mode.infoType == DISPLAYCONFIG_MODE_INFO_TYPE_TARGET
        w = mode.mode.targetMode.videoSignalInfo.activeSize.cx
        h = mode.mode.targetMode.videoSignalInfo.activeSize.cy
        target_resolution = (w, h)

        # 3.1 get the Source device name (i.e. the GDI device name, e.g. \\.\DISPLAY1)
        source_name_info = DISPLAYCONFIG_SOURCE_DEVICE_NAME()
        source_name_info.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
        source_name_info.header.size = ctypes.sizeof(DISPLAYCONFIG_SOURCE_DEVICE_NAME)
        source_name_info.header.adapterId = p.sourceInfo.adapterId
        source_name_info.header.id = p.sourceInfo.id

        device_name = ""
        if (
            user32.DisplayConfigGetDeviceInfo(ctypes.byref(source_name_info.header))
            == ERROR_SUCCESS
        ):
            device_name = source_name_info.viewGdiDeviceName

        # 3.2 get Target friendly name and monitor device path
        target_name_info = DISPLAYCONFIG_TARGET_DEVICE_NAME()
        target_name_info.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
        target_name_info.header.size = ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME)
        target_name_info.header.adapterId = p.targetInfo.adapterId
        target_name_info.header.id = p.targetInfo.id

        res = user32.DisplayConfigGetDeviceInfo(ctypes.byref(target_name_info.header))

        friendly_name = None
        monitor_device_path = ""

        if res == ERROR_SUCCESS:
            friendly_name = target_name_info.monitorFriendlyDeviceName or None
            monitor_device_path = target_name_info.monitorDevicePath
        elif res == ERROR_NOT_SUPPORTED:
            pass  # no WDDM driver / remote session: model & monitor path unavailable
        else:
            raise OSError(f"DisplayConfigGetDeviceInfo error return: {res}")

        # 4. compute per-monitor DPI scale percentage (snapped to supported steps)
        pt = POINTL(pos_x + w // 2, pos_y + h // 2)
        h_monitor = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        scale = 100
        if h_monitor:
            dpi_x = wintypes.UINT(0)
            dpi_y = wintypes.UINT(0)
            if shcore.GetDpiForMonitor(h_monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                scale = min(_SCALE_PERCENTS, key=lambda p: abs(p - round(dpi_x.value / 96.0 * 100)))

        res_display_info.append(
            DisplayInfo(
                device_name=device_name,
                model=friendly_name,
                source_resolution=source_resolution,
                position=position,
                target_resolution=target_resolution,
                scale=scale,
                monitor_device_path=monitor_device_path,
                adapter_id=p.sourceInfo.adapterId,
                source_id=p.sourceInfo.id,
            )
        )

    return res_display_info


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


def get_hmonitor_by_device_name(device_name: str) -> int | None:
    """Return the HMONITOR handle for a GDI device name, or ``None``.

    Enumerates active monitors via ``win32api`` and matches ``GetMonitorInfo``'s
    ``Device`` string against *device_name*.
    """
    for h_monitor, _, _ in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(int(h_monitor))
        if info.get("Device") == device_name:
            return int(h_monitor)
    return None


def resolve_target_resolution(
    target_resolution: tuple[int, int], support_resolutions: list[tuple[int, int]]
) -> tuple[int, int]:
    """Return the supported resolution closest to *target_resolution*.

    Minimizes the summed relative difference of width and height, so both
    dimensions are weighted equally regardless of absolute size.
    """
    return min(
        support_resolutions,
        key=lambda r: abs(target_resolution[0] / r[0] - 1) + abs(target_resolution[1] / r[1] - 1),
    )


def set_display_resolution(
    device_name: str,
    width: int,
    height: int,
    refresh_rate: int | None = None,
    persistent: bool = True,
) -> bool:
    """Set the display's resolution, optionally changing the refresh rate.

    Args:
        device_name: GDI device name, e.g. ``\\\\.\\DISPLAY1``.
        width: New pixel width.
        height: New pixel height.
        refresh_rate: Refresh rate in Hz; ``None`` keeps the current value.
        persistent: When True, writes the change to the registry.

    Returns:
        True on success; False (logged) on failure.
    """
    devmode = DEVMODEW()
    devmode.dmSize = ctypes.sizeof(DEVMODEW)

    if not user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode)):
        logger.error(f"cannot read current display settings for {device_name}")
        return False

    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields |= DM_PELSWIDTH | DM_PELSHEIGHT

    if refresh_rate is not None:
        devmode.dmDisplayFrequency = refresh_rate
        devmode.dmFields |= DM_DISPLAYFREQUENCY

    flags = CDS_UPDATEREGISTRY if persistent else 0
    res = user32.ChangeDisplaySettingsExW(device_name, ctypes.byref(devmode), None, flags, None)
    if res != ERROR_SUCCESS:
        logger.error(f"cannot change display settings for {device_name}: code {res}")
        return False
    suffix = f" @ {refresh_rate}Hz" if refresh_rate else ""
    logger.info(f"[{device_name}] resolution set to {width}x{height}{suffix}")
    return True


def resolve_target_scale_step(
    reference_scale: int, target_scale: int, support_scales: list[int]
) -> int:
    """Return the relative step from the *reference_scale* to *target_scale*.

    The result is a ``scaleRel`` value suitable for :func:`set_display_scale`: an
    offset from the monitor's reference scale within *support_scales*.
    *reference_scale* comes from :attr:`DisplayCapability.reference_scale`; the
    target is snapped to the nearest supported entry.
    """
    idx_reference = support_scales.index(reference_scale)
    idx_target = min(
        range(len(support_scales)), key=lambda i: abs(support_scales[i] - target_scale)
    )
    return idx_target - idx_reference


def set_display_scale(
    device_name: str,
    adapter_id: LUID,
    source_id: int,
    scale_rel: int,
) -> bool:
    """Set the display's DPI scale to a relative step from the recommended scale.

    Windows' DPI API addresses scale as a discrete relative step from the
    monitor's recommended scale (``scaleRel``): ``0`` is the recommended step,
    negative values move below it, positive values above it. If the requested
    step falls outside the monitor's supported range ``[minScaleRel,
    maxScaleRel]``, it is clamped to the nearest supported step.

    Args:
        device_name: GDI device name, e.g. ``\\\\.\\DISPLAY1``.
        adapter_id: The display path's adapter LUID (see :attr:`DisplayInfo.adapter_id`).
        source_id: The display path's source id (see :attr:`DisplayInfo.source_id`).
        scale_rel: Desired relative step from the recommended scale.

    Returns:
        True on success (clamped if needed); False (logged) on failure.
    """
    get_dpi = DISPLAYCONFIG_GET_DPI_SCALING()
    get_dpi.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALING
    get_dpi.header.size = ctypes.sizeof(DISPLAYCONFIG_GET_DPI_SCALING)
    get_dpi.header.adapterId = adapter_id
    get_dpi.header.id = source_id

    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(get_dpi.header)) != ERROR_SUCCESS:
        logger.error(f"cannot query DPI scaling for {device_name}")
        return False

    clamped = max(get_dpi.minScaleRel, min(get_dpi.maxScaleRel, scale_rel))
    if clamped != scale_rel:
        logger.warning(
            f"scale step {scale_rel} out of range "
            f"[{get_dpi.minScaleRel}, {get_dpi.maxScaleRel}]; clamping to {clamped}"
        )
        scale_rel = clamped

    set_dpi = DISPLAYCONFIG_SET_DPI_SCALING()
    set_dpi.header.type = DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALING
    set_dpi.header.size = ctypes.sizeof(DISPLAYCONFIG_SET_DPI_SCALING)
    set_dpi.header.adapterId = adapter_id
    set_dpi.header.id = source_id
    set_dpi.scaleRel = scale_rel

    res = user32.DisplayConfigSetDeviceInfo(ctypes.byref(set_dpi.header))
    if res != ERROR_SUCCESS:
        logger.error(f"cannot set DPI scaling for {device_name}: code {res}")
        return False
    logger.info(f"[{device_name}] DPI scale step set to {scale_rel}")
    return True


# Discrete Windows DPI scale steps, ascending (percentages). Relative
# DISPLAYCONFIG scale steps (``scaleRel``) are deltas from the recommended
# step's index into this table.
_SCALE_PERCENTS: tuple[int, ...] = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)


def get_monitor_current_scale(device_name: str) -> int | None:
    """Return the monitor's current effective DPI scale as a percentage, or ``None``."""
    h_monitor = get_hmonitor_by_device_name(device_name)
    if not h_monitor:
        logger.error(f"cannot find monitor handle for {device_name}")
        return None
    dpi_x = wintypes.UINT(0)
    dpi_y = wintypes.UINT(0)
    res = shcore.GetDpiForMonitor(
        h_monitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
    )
    if res != ERROR_SUCCESS:
        return None
    return min(_SCALE_PERCENTS, key=lambda p: abs(p - round(dpi_x.value / 96.0 * 100)))


def get_display_capability(
    device_name: str,
    adapter_id: LUID,
    source_id: int,
    raise_error: bool = False,
) -> DisplayCapability | None:
    """Return the display's supported scale percentages and resolutions.

    Args:
        device_name: GDI device name, e.g. ``\\\\.\\DISPLAY1``.
        adapter_id: The display path's source adapter LUID (see :attr:`DisplayInfo.adapter_id`).
        source_id: The display path's source id (see :attr:`DisplayInfo.source_id`).
        raise_error: When True, raise the ``OSError`` on API failure instead of returning None.

    Returns:
        A :class:`DisplayCapability`, or None (logged) if the capability cannot be
        queried (e.g. topology transition, monitor not found).
    """
    get_dpi = DISPLAYCONFIG_GET_DPI_SCALING()
    get_dpi.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALING
    get_dpi.header.size = ctypes.sizeof(DISPLAYCONFIG_GET_DPI_SCALING)
    get_dpi.header.adapterId = adapter_id
    get_dpi.header.id = source_id

    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(get_dpi.header)) != ERROR_SUCCESS:
        logger.error(f"cannot query DPI scaling for {device_name}")
        if raise_error:
            raise OSError(f"cannot query DPI scaling for {device_name}")
        return None

    current_pct = get_monitor_current_scale(device_name)
    if current_pct is None:
        return None

    current_index = min(
        range(len(_SCALE_PERCENTS)), key=lambda i: abs(_SCALE_PERCENTS[i] - current_pct)
    )
    reference_index = current_index - get_dpi.curScaleRel
    if not (0 <= reference_index < len(_SCALE_PERCENTS)):
        raise ValueError(f"reference_index {reference_index} out of bounds")
    reference_scale = _SCALE_PERCENTS[reference_index]
    min_idx = max(0, reference_index + get_dpi.minScaleRel)
    max_idx = min(len(_SCALE_PERCENTS) - 1, reference_index + get_dpi.maxScaleRel)
    supported_scales = _SCALE_PERCENTS[min_idx : max_idx + 1]

    resolutions_set: set[tuple[int, int]] = set()
    devmode = DEVMODEW()
    devmode.dmSize = ctypes.sizeof(DEVMODEW)
    i = 0
    while user32.EnumDisplaySettingsW(device_name, i, ctypes.byref(devmode)):
        resolutions_set.add((devmode.dmPelsWidth, devmode.dmPelsHeight))
        i += 1
    resolutions = tuple(sorted(resolutions_set, key=lambda r: (r[0], r[1]), reverse=True))

    return DisplayCapability(
        scale=supported_scales, reference_scale=reference_scale, resolution=resolutions
    )
