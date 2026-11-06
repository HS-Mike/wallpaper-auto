"""
Process utility functions.

Provides platform-level helpers for inspecting and closing running processes,
such as enumerating them via a Toolhelp32 snapshot, resolving the full
executable path of a process by PID via the Win32 API, and asking a process to
close by posting ``WM_CLOSE`` to its windows.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from pathlib import Path

import pywintypes
import win32api
import win32con
import win32event
import win32gui
import win32process

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


def get_executable_path(pid: int) -> Path | None:
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
            return Path(buffer.value)
        return None
    finally:
        _CloseHandle(handle)


def get_running_processes() -> list[tuple[int, str]]:
    """Enumerate running processes as ``(pid, executable basename)`` pairs.

    Returns:
        A list of ``(pid, exe_name)`` tuples for every process in the current
        snapshot.

    Raises:
        RuntimeError: The Toolhelp32 snapshot could not be taken.
    """
    snapshot = _CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    # ctypes' HANDLE restype can surface as either a wrapped object with
    # `.value` or a raw int depending on the ctypes version; accept both.
    handle_value = snapshot.value if hasattr(snapshot, "value") else snapshot
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        raise RuntimeError("cannot create Toolhelp32 process snapshot")

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


def post_close_message(pid: int) -> None:
    """Post ``WM_CLOSE`` to every top-level window of ``pid``.

    Hidden windows are included on purpose: the only window Wallpaper Engine
    honors the close request on is its hidden ``WPEEventWindow``. Its visible
    window and ``WPETrayWindow`` ignore it, and a running instance started
    without its UI has no visible window at all.

    Does not wait for exit; the target process decides whether to honor the
    close request.

    Args:
        pid: PID whose windows should receive the close request.
    """

    def enum_windows_callback(hwnd: int, extra: object) -> None:
        _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
        if found_pid == pid:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    win32gui.EnumWindows(enum_windows_callback, None)


#: ``OpenProcess`` reports this when the pid no longer exists.
_ERROR_INVALID_PARAMETER = 87


def _open_wait_handle(pid: int) -> int | None:
    """Open ``pid`` so that its exit can be waited on.

    Args:
        pid: Process ID to open.

    Returns:
        A handle that Windows signals once the process has ended, or ``None``
        when the process is already gone or cannot be opened.
    """
    try:
        return win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
    except pywintypes.error as exc:
        if exc.winerror != _ERROR_INVALID_PARAMETER:
            logger.warning(f"cannot wait on process {pid}: {exc}")
        return None


_PROCESS_EXIT_TIMEOUT_SECONDS = 5
_PROCESS_CLOSE_RETRY_SECONDS = 0.5


def close_and_wait_for_exit(pids: list[int]) -> None:
    """Ask each process to close, then wait until every one of them has exited.

    A close request is fire-and-forget, so a process whose windows do not exist
    yet ignores it; the wait is what keeps that from going unnoticed. Pids that
    are already gone are skipped.

    Args:
        pids: Process IDs to close.

    Raises:
        ValueError: More than 64 pids were given, which exceeds the Win32 limit
            on how many objects :func:`win32event.WaitForMultipleObjects` may
            wait on at once.
    """
    unique_pids = set(pids)
    if len(unique_pids) > 64:
        raise ValueError(
            f"Waiting for {len(unique_pids)} processes, which exceeds Win32 limit (64)."
        )

    handles: dict[int, int] = {}
    for pid in unique_pids:
        handle = _open_wait_handle(pid)
        if handle is not None:
            handles[pid] = handle

    if not handles:
        return

    try:
        for pid in handles:
            post_close_message(pid)

        deadline = time.monotonic() + _PROCESS_EXIT_TIMEOUT_SECONDS
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                timed_out = [
                    pid
                    for pid, handle in handles.items()
                    if win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT
                ]
                logger.warning(
                    f"processes {timed_out} did not close within {_PROCESS_EXIT_TIMEOUT_SECONDS}s"
                )
                break

            wait_time = min(remaining_ms, int(_PROCESS_CLOSE_RETRY_SECONDS * 1000))
            result = win32event.WaitForMultipleObjects(list(handles.values()), True, wait_time)
            if result != win32event.WAIT_TIMEOUT:
                break
    finally:
        for handle in handles.values():
            win32api.CloseHandle(handle)
