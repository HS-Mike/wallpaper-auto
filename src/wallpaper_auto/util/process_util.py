"""
Process utility functions.

Provides platform-level helpers for inspecting running processes, such as
resolving the full executable path of a process by PID via the Win32 API.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32 = ctypes.windll.kernel32

_OpenProcess = _kernel32.OpenProcess
_OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_OpenProcess.restype = wintypes.HANDLE

_QueryFullProcessImageNameW = _kernel32.QueryFullProcessImageNameW
_QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_QueryFullProcessImageNameW.restype = wintypes.BOOL

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


def get_executable_path(pid: int) -> str | None:
    """Query the full executable path of an active process via Win32 API.

    Returns ``None`` if the PID is invalid, the handle can't be opened (the
    process has already exited or runs at a higher integrity level), or the
    path query itself fails. The opened handle is always closed before
    return.
    """
    if pid <= 0:
        return None

    handle = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None

    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        _CloseHandle(handle)
