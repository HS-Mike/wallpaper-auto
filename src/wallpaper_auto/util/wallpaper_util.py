import contextvars
import ctypes
import functools
import dataclasses
import logging
import contextlib
from ctypes import wintypes
from enum import IntEnum
from pathlib import Path
from typing import Generator, Optional, Any, Callable, TypeVar, ParamSpec, Tuple


logger = logging.getLogger(__name__)


# IDesktopWallpaper position/style constants (must be defined before WallpaperStyle enum).
DWPOS_CENTER: int = 0
DWPOS_TILE: int = 1
DWPOS_STRETCH: int = 2
DWPOS_FIT: int = 3
DWPOS_FILL: int = 4
DWPOS_SPAN: int = 5


class WallpaperStyle(IntEnum):
    """Wallpaper fit/style matching IDesktopWallpaper position constants."""

    CENTER = DWPOS_CENTER
    TILE = DWPOS_TILE
    STRETCH = DWPOS_STRETCH
    FIT = DWPOS_FIT
    FILL = DWPOS_FILL
    SPAN = DWPOS_SPAN

@dataclasses.dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

DSD_FORWARD: int = 0
DSD_BACKWARD: int = 1

SDB_READY: int = 0x00
SDB_RUNNING: int = 0x01
SDB_PAUSED: int = 0x02

ole32: ctypes.WinDLL = ctypes.windll.ole32
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

def make_guid(s: str) -> ctypes.Array[ctypes.c_byte]:
    g: ctypes.Array[ctypes.c_byte] = (ctypes.c_byte * 16)()
    ole32.CLSIDFromString(s, ctypes.byref(g))
    return g

# --- COM interface vtable prototypes ---
PROTO_RELEASE: Any = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
PROTO_SET_WALLPAPER: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p)
PROTO_GET_WALLPAPER: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar_p))
PROTO_GET_MONITOR_PATH_AT: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_wchar_p))
PROTO_GET_MONITOR_COUNT: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT))
PROTO_GET_MONITOR_BOUNDS: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(RECT))
PROTO_SET_WALLPAPER_POS: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int)
PROTO_GET_WALLPAPER_POS: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
PROTO_ADVANCE_SLIDESHOW: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, wintypes.DWORD)
PROTO_GET_STATUS: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD))
PROTO_ENABLE: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.BOOL)
PROTO_SET_BACKGROUND_COLOR: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.COLORREF)
PROTO_GET_BACKGROUND_COLOR: Any = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.COLORREF))


# --- Implicit pointer context management ---

# thread/coroutine-safe context variable to pass the COM pointer implicitly
_current_p_wallpaper: contextvars.ContextVar[ctypes.c_void_p] = contextvars.ContextVar("_current_p_wallpaper")

def _call_com_vtable(index: int, prototype: Any, *args: Any) -> Any:
    """Helper: extract COM pointer from the current thread context and call a vtable method"""
    p_object: ctypes.c_void_p = _current_p_wallpaper.get()
    ppv = ctypes.cast(p_object, ctypes.POINTER(ctypes.c_void_p))
    vtable = ctypes.cast(ppv.contents, ctypes.POINTER(ctypes.c_void_p))
    func: Any = ctypes.cast(vtable[index], prototype)
    return func(p_object, *args)


P = ParamSpec("P")
R = TypeVar("R")

def com_managed(func: Callable[P, R]) -> Callable[P, R]:
    """
    Smart decorator: if a com_session context already surrounds the call,
    reuse the existing COM pointer; otherwise initialise, execute, and tear down inline.
    """
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        has_context: bool = True
        try:
            _current_p_wallpaper.get()
        except LookupError:
            has_context = False

        # If we already have a COM context, execute func directly.
        # Do NOT wrap in try/except — func's own LookupError must not be caught.
        if has_context:
            return func(*args, **kwargs)

        # missing context -- standalone call, manage lifecycle here
        hr_init: int = ole32.CoInitialize(None)
        com_initialized: bool = hr_init in (0, 1)

        p_wallpaper: ctypes.c_void_p = ctypes.c_void_p()
        token: Optional[contextvars.Token[ctypes.c_void_p]] = None
        try:
            CLSID: ctypes.Array[ctypes.c_byte] = make_guid("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")
            IID: ctypes.Array[ctypes.c_byte] = make_guid("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")

            hr: int = ole32.CoCreateInstance(
                ctypes.byref(CLSID), None, 0x17, ctypes.byref(IID), ctypes.byref(p_wallpaper)
            )
            if hr < 0:
                raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08X}")

            token = _current_p_wallpaper.set(p_wallpaper)
            return func(*args, **kwargs)
        finally:
            if token is not None:
                _current_p_wallpaper.reset(token)
            if p_wallpaper.value:
                ppv = ctypes.cast(p_wallpaper, ctypes.POINTER(ctypes.c_void_p))
                vtable = ctypes.cast(ppv.contents, ctypes.POINTER(ctypes.c_void_p))
                release_func = ctypes.cast(vtable[2], PROTO_RELEASE)
                release_func(p_wallpaper)
            if com_initialized:
                ole32.CoUninitialize()
    return wrapper


@contextlib.contextmanager
def com_session() -> Generator[None, None, None]:
    """
    Batch-session context manager.
    Every function decorated with @com_managed called inside the ``with`` block
    reuses the same COM object instance.
    """
    hr_init: int = ole32.CoInitialize(None)
    com_initialized: bool = hr_init in (0, 1)
    
    p_wallpaper: ctypes.c_void_p = ctypes.c_void_p()
    token: Optional[contextvars.Token[ctypes.c_void_p]] = None
    try:
        CLSID = make_guid("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")
        IID = make_guid("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")
        hr = ole32.CoCreateInstance(ctypes.byref(CLSID), None, 0x17, ctypes.byref(IID), ctypes.byref(p_wallpaper))
        if hr < 0:
            raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08X}")
        
        token = _current_p_wallpaper.set(p_wallpaper)
        yield
    finally:
        if token is not None:
            _current_p_wallpaper.reset(token)
        if p_wallpaper.value:
            ppv = ctypes.cast(p_wallpaper, ctypes.POINTER(ctypes.c_void_p))
            vtable = ctypes.cast(ppv.contents, ctypes.POINTER(ctypes.c_void_p))
            release_func = ctypes.cast(vtable[2], PROTO_RELEASE)
            release_func(p_wallpaper)
        if com_initialized:
            ole32.CoUninitialize()


# --- Public API (clean, no raw pointers) ---

@com_managed
def get_monitor_device_path_count() -> int:
    """Return the number of connected monitors"""
    count: wintypes.UINT = wintypes.UINT()
    hr_call: int = _call_com_vtable(6, PROTO_GET_MONITOR_COUNT, ctypes.byref(count))
    if hr_call < 0:
        raise OSError(f"failed to get monitor count: 0x{hr_call & 0xFFFFFFFF:08X}")
    return int(count.value)


@com_managed
def get_monitor_device_path_at(index: int) -> str:
    """Return the unique device path of the monitor at ``index``"""
    monitor_id: ctypes.c_wchar_p = ctypes.c_wchar_p()
    hr_call: int = _call_com_vtable(5, PROTO_GET_MONITOR_PATH_AT, wintypes.UINT(index), ctypes.byref(monitor_id))
    if hr_call < 0:
        raise OSError(f"failed to get monitor path [index {index}]: 0x{hr_call & 0xFFFFFFFF:08X}")
    if monitor_id.value is None:
        raise OSError(f"monitor path is empty [index {index}]")
    result: str = str(monitor_id.value)
    ole32.CoTaskMemFree(ctypes.cast(monitor_id, ctypes.c_void_p))
    return result


@com_managed
def set_wallpaper(monitor_id: str | None, image_path: str | Path) -> None:
    """Set the wallpaper for a monitor (None = all monitors)"""
    hr_call: int = _call_com_vtable(3, PROTO_SET_WALLPAPER, monitor_id, str(image_path))
    if hr_call != 0:
        raise OSError(f"set_wallpaper failed: 0x{hr_call & 0xFFFFFFFF:08X}")


@com_managed
def get_wallpaper(monitor_id: Optional[str]) -> Path:
    """
    Return the absolute wallpaper path for the given monitor.

    When *monitor_id* is None:
    - If a single wallpaper spans all monitors, the path is returned normally.
    - If different monitors have different wallpapers or a slideshow is running,
      the COM method returns ``S_FALSE`` and the function raises ``OSError``
      (wallpaper path returned Null).
    """
    path: ctypes.c_wchar_p = ctypes.c_wchar_p()
    hr_call: int = _call_com_vtable(4, PROTO_GET_WALLPAPER, monitor_id, ctypes.byref(path))
    if hr_call < 0:
        raise OSError(f"get_wallpaper failed: 0x{hr_call & 0xFFFFFFFF:08X}")
    if path.value is None:
        raise OSError("wallpaper path returned Null")

    result = Path(str(path.value))
    ole32.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))
    return result


@com_managed
def get_monitor_bounds(monitor_id: str) -> Rect:
    """Return the bounding rect of a monitor"""
    bounds: RECT = RECT()
    hr_call: int = _call_com_vtable(7, PROTO_GET_MONITOR_BOUNDS, monitor_id, ctypes.byref(bounds))
    if hr_call < 0:
        raise OSError(f"get_monitor_bounds failed: 0x{hr_call & 0xFFFFFFFF:08X}")
    return Rect(
        left=int(bounds.left),
        top=int(bounds.top),
        right=int(bounds.right),
        bottom=int(bounds.bottom)
    )


@com_managed
def set_background_color(color: int | Tuple[int, int, int]) -> None:
    """Set the background color used when wallpaper style is Center or Fit.

    Accepts a raw COLORREF int (0x00BBGGRR) or an (R, G, B) tuple.
    """
    if isinstance(color, tuple):
        r, g, b = color
        color_ref = int(r | (g << 8) | (b << 16))
    else:
        color_ref = int(color)
    hr_call: int = _call_com_vtable(8, PROTO_SET_BACKGROUND_COLOR, wintypes.COLORREF(color_ref))
    if hr_call < 0:
        raise OSError(f"set_background_color failed: 0x{hr_call & 0xFFFFFFFF:08X}")


@com_managed
def get_background_color() -> Tuple[int, int, int]:
    """Return the background color as an (R, G, B) tuple."""
    color_ref: wintypes.COLORREF = wintypes.COLORREF()
    hr_call: int = _call_com_vtable(9, PROTO_GET_BACKGROUND_COLOR, ctypes.byref(color_ref))
    if hr_call < 0:
        raise OSError(f"get_background_color failed: 0x{hr_call & 0xFFFFFFFF:08X}")
    val: int = color_ref.value
    r: int = val & 0xFF
    g: int = (val >> 8) & 0xFF
    b: int = (val >> 16) & 0xFF
    return (r, g, b)


@com_managed
def set_wallpaper_style(style: int | WallpaperStyle) -> None:
    """Set global wallpaper style/fit for all monitors.

    Values: DWPOS_CENTER, DWPOS_TILE, DWPOS_STRETCH, DWPOS_FIT, DWPOS_FILL, DWPOS_SPAN.
    """
    hr_call: int = _call_com_vtable(10, PROTO_SET_WALLPAPER_POS, int(style))
    if hr_call < 0:
        raise OSError(f"set_wallpaper_style failed: 0x{hr_call & 0xFFFFFFFF:08X}")


@com_managed
def get_wallpaper_style() -> WallpaperStyle:
    """Get the current global wallpaper style/fit"""
    style: ctypes.c_int = ctypes.c_int()
    hr_call: int = _call_com_vtable(11, PROTO_GET_WALLPAPER_POS, ctypes.byref(style))
    if hr_call < 0:
        raise OSError(f"get_wallpaper_style failed: 0x{hr_call & 0xFFFFFFFF:08X}")
    return WallpaperStyle(style.value)


@com_managed
def advance_slideshow(monitor_id: Optional[str], forward: bool = True) -> None:
    """Advance to the next/previous slideshow image"""
    direction: int = DSD_FORWARD if forward else DSD_BACKWARD
    hr_call: int = _call_com_vtable(14, PROTO_ADVANCE_SLIDESHOW, monitor_id, wintypes.DWORD(direction))
    if hr_call < 0:
        raise OSError(f"advance_slideshow failed: 0x{hr_call & 0xFFFFFFFF:08X}")


@com_managed
def get_status() -> int:
    """Return the slideshow status"""
    status: wintypes.DWORD = wintypes.DWORD()
    hr_call: int = _call_com_vtable(15, PROTO_GET_STATUS, ctypes.byref(status))
    if hr_call < 0:
        raise OSError(f"get_status failed: 0x{hr_call & 0xFFFFFFFF:08X}")
    return int(status.value)


@com_managed
def enable(enable_: bool) -> None:
    """Enable or disable wallpaper rendering"""
    hr_call: int = _call_com_vtable(16, PROTO_ENABLE, wintypes.BOOL(enable_))
    if hr_call < 0:
        raise OSError(f"enable failed: 0x{hr_call & 0xFFFFFFFF:08X}")

