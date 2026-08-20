"""Tests for process_util.py — Win32 executable path resolution and enumeration."""

import ctypes
from typing import Any
from unittest.mock import patch

from wallpaper_auto.util.process_util import (
    PROCESSENTRY32W,
    get_executable_path,
    get_running_processes,
)

_PROCESS_UTIL = "wallpaper_auto.util.process_util"


class TestGetExecutablePath:
    """Tests for the Win32 fallback helper used to resolve ``ExecutablePath``."""

    @staticmethod
    def _write_to_buf(buf: Any, path: str) -> None:
        """Write a Python string into the wide-char buffer the Win32 API would fill.

        ``buf`` is a ``c_wchar_p`` parameter that aliases the original
        ``ctypes.create_unicode_buffer`` storage. Writing through
        ``ctypes.memmove`` mirrors what ``QueryFullProcessImageNameW`` does
        in kernel: copy the path as UTF-16 LE followed by a wide-null
        terminator.
        """
        addr = ctypes.cast(buf, ctypes.c_void_p).value
        assert addr is not None  # the create_unicode_buffer always has an address
        encoded = (path + "\0").encode("utf-16-le")
        ctypes.memmove(addr, encoded, len(encoded))

    def test_invalid_pid_returns_none(self) -> None:
        """Non-positive PIDs short-circuit without opening a handle."""
        with patch(f"{_PROCESS_UTIL}._OpenProcess") as mock_open:
            assert get_executable_path(0) is None
            assert get_executable_path(-1) is None
        mock_open.assert_not_called()

    def test_open_process_failure_returns_none(self) -> None:
        """When the handle can't be opened (process exited / integrity mismatch), returns None."""
        with (
            patch(f"{_PROCESS_UTIL}._OpenProcess", return_value=0) as mock_open,
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            assert get_executable_path(1234) is None
        mock_open.assert_called_once()
        mock_close.assert_not_called()

    def test_successful_query_returns_path(self) -> None:
        """When ``QueryFullProcessImageNameW`` succeeds, returns the path and closes the handle."""
        fake_handle = 0xCAFE
        expected_path = r"C:\Windows\System32\notepad.exe"

        def fake_query(handle: Any, flags: Any, buf: Any, size_ptr: Any) -> int:
            self._write_to_buf(buf, expected_path)
            return 1

        with (
            patch(
                f"{_PROCESS_UTIL}._OpenProcess",
                return_value=fake_handle,
            ),
            patch(
                f"{_PROCESS_UTIL}._QueryFullProcessImageNameW",
                side_effect=fake_query,
            ),
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            assert get_executable_path(1234) == expected_path
        mock_close.assert_called_once_with(fake_handle)

    def test_query_failure_returns_none_and_closes_handle(self) -> None:
        """When ``QueryFullProcessImageNameW`` fails, returns None and still closes the handle."""
        fake_handle = 0xCAFE
        with (
            patch(
                f"{_PROCESS_UTIL}._OpenProcess",
                return_value=fake_handle,
            ),
            patch(
                f"{_PROCESS_UTIL}._QueryFullProcessImageNameW",
                return_value=0,
            ),
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            assert get_executable_path(1234) is None
        mock_close.assert_called_once_with(fake_handle)


class TestGetRunningProcesses:
    """Tests for the Toolhelp32 enumeration helper."""

    @staticmethod
    def _write_entry(entry_ptr: Any, pid: int, name: str) -> None:
        """Fill the ``PROCESSENTRY32W`` struct the snapshot APIs would populate.

        ``entry_ptr`` is the ``ctypes.byref`` pointer passed to
        ``Process32FirstW``/``Process32NextW``; casting it back and writing the
        ``th32ProcessID``/``szExeFile`` fields mirrors what kernel32 does.
        """
        entry = ctypes.cast(entry_ptr, ctypes.POINTER(PROCESSENTRY32W)).contents
        entry.th32ProcessID = pid
        entry.szExeFile = name

    def test_enumerates_processes(self) -> None:
        """Walks the snapshot entries and returns ``(pid, name)`` pairs, closing the handle."""
        fake_snapshot = ctypes.c_void_p(0xCAFE)
        entries = [(100, "notepad.exe"), (200, "chrome.exe")]

        def fake_first(handle: Any, entry_ptr: Any) -> int:
            self._write_entry(entry_ptr, *entries[0])
            return 1

        state = {"index": 0}

        def fake_next(handle: Any, entry_ptr: Any) -> int:
            next_index = state["index"] + 1
            if next_index >= len(entries):
                return 0
            self._write_entry(entry_ptr, *entries[next_index])
            state["index"] = next_index
            return 1

        with (
            patch(
                f"{_PROCESS_UTIL}._CreateToolhelp32Snapshot",
                return_value=fake_snapshot,
            ),
            patch(f"{_PROCESS_UTIL}._Process32FirstW", side_effect=fake_first),
            patch(f"{_PROCESS_UTIL}._Process32NextW", side_effect=fake_next),
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            assert get_running_processes() == entries
        mock_close.assert_called_once_with(fake_snapshot)

    def test_snapshot_failure_returns_none(self) -> None:
        """An ``INVALID_HANDLE_VALUE`` snapshot returns None without enumerating or closing."""
        with (
            patch(
                f"{_PROCESS_UTIL}._CreateToolhelp32Snapshot",
                return_value=ctypes.c_void_p(-1),
            ),
            patch(f"{_PROCESS_UTIL}._Process32FirstW") as mock_first,
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            assert get_running_processes() is None
        mock_first.assert_not_called()
        mock_close.assert_not_called()

    def test_null_snapshot_returns_none(self) -> None:
        """A NULL (``None``-valued) snapshot handle is also treated as a failure."""
        with (
            patch(f"{_PROCESS_UTIL}._CreateToolhelp32Snapshot", return_value=ctypes.c_void_p()),
            patch(f"{_PROCESS_UTIL}._Process32FirstW") as mock_first,
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            assert get_running_processes() is None
        mock_first.assert_not_called()
        mock_close.assert_not_called()
