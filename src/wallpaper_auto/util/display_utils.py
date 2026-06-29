import time
import ctypes
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class DisplayInfo:
    model: str | None
    source_resolution: tuple[int, int]
    position: tuple[int, int]
    target_resolution: tuple[int, int]
    monitor_device_path: str = ""


ERROR_SUCCESS = 0
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


# 专门用于获取显示器路径和名称的结构体
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
    # 只需定义我们要用到的分辨率部分
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

user32.DisplayConfigGetDeviceInfo.argtypes = [
    ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)
]
user32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG


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
        elif res == ERROR_INSUFFICIENT_BUFFER:
            time.sleep(0.5)
            continue
        else:
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
        else:
            raise OSError(f"DisplayConfigGetDeviceInfo error return: {res}")
        
        res_display_info.append(
            DisplayInfo(
                model=friendly_name, 
                source_resolution=source_resolution, 
                position=position, 
                target_resolution=target_resolution, 
                monitor_device_path=monitor_device_path
            )
        )

    return res_display_info


if __name__ == "__main__":
    for i in get_display_info():
        print(i)
        