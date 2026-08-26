"""
Process utility functions.

Provides platform-level helpers for inspecting running processes, such as
enumerating running processes via a Toolhelp32 snapshot and resolving the
full executable path of a process by PID via the Win32 API.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
# INVALID_HANDLE_VALUE is (HANDLE)-1, surfaced by ctypes as the unsigned value
# of -1 for the platform's pointer size (e.g. 2**64 - 1 on x64).
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
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


class PROCESSENTRY32W(ctypes.Structure):  # noqa: N801
    """Snapshot entry describing a single running process (``PROCESSENTRY32W``)."""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


_CreateToolhelp32Snapshot = _kernel32.CreateToolhelp32Snapshot
_CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_CreateToolhelp32Snapshot.restype = wintypes.HANDLE

_Process32FirstW = _kernel32.Process32FirstW
_Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_Process32FirstW.restype = wintypes.BOOL

_Process32NextW = _kernel32.Process32NextW
_Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_Process32NextW.restype = wintypes.BOOL


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


def get_running_processes() -> list[tuple[int, str]] | None:
    """Enumerate running processes as ``(pid, executable basename)`` pairs.

    Returns:
        A list of ``(pid, exe_name)`` tuples for every process in the current
        snapshot, or ``None`` (logged) when the Toolhelp32 snapshot cannot be
        taken — a transient failure callers should skip rather than act on.
    """
    snapshot = _CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    # ctypes' HANDLE restype can surface as either a wrapped object with
    # `.value` or a raw int depending on the ctypes version; accept both.
    handle_value = snapshot.value if hasattr(snapshot, "value") else snapshot
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        logger.warning("cannot create Toolhelp32 process snapshot")
        return None

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        processes: list[tuple[int, str]] = []
        ok = _Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            processes.append((int(entry.th32ProcessID), entry.szExeFile))
            ok = _Process32NextW(snapshot, ctypes.byref(entry))
        return processes
    finally:
        _CloseHandle(snapshot)
