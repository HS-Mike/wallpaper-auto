"""Tests for process_util.py — Win32 path resolution, enumeration and close requests."""

import ctypes
import logging
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
import pywintypes
import win32con
import win32event

from wallpaper_auto.util.process_util import (
    PROCESSENTRY32W,
    _open_wait_handle,
    close_and_wait_for_exit,
    get_executable_path,
    get_running_processes,
    post_close_message,
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
            assert get_executable_path(1234) == Path(expected_path)
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

    def test_snapshot_failure_raises(self) -> None:
        """An ``INVALID_HANDLE_VALUE`` snapshot raises without enumerating or closing."""
        with (
            patch(
                f"{_PROCESS_UTIL}._CreateToolhelp32Snapshot",
                return_value=ctypes.c_void_p(-1),
            ),
            patch(f"{_PROCESS_UTIL}._Process32FirstW") as mock_first,
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            with pytest.raises(RuntimeError, match="Toolhelp32"):
                get_running_processes()
        mock_first.assert_not_called()
        mock_close.assert_not_called()

    def test_null_snapshot_raises(self) -> None:
        """A NULL (``None``-valued) snapshot handle is also treated as a failure."""
        with (
            patch(f"{_PROCESS_UTIL}._CreateToolhelp32Snapshot", return_value=ctypes.c_void_p()),
            patch(f"{_PROCESS_UTIL}._Process32FirstW") as mock_first,
            patch(f"{_PROCESS_UTIL}._CloseHandle") as mock_close,
        ):
            with pytest.raises(RuntimeError, match="Toolhelp32"):
                get_running_processes()
        mock_first.assert_not_called()
        mock_close.assert_not_called()


class TestPostCloseMessage:
    """Tests for the ``WM_CLOSE`` broadcaster."""

    def test_posts_close_to_every_window_of_target_pid(self) -> None:
        """Posts ``WM_CLOSE`` to each window of the pid, skipping windows of other pids."""
        windows = {0x100: 1234, 0x200: 9999, 0x300: 1234}

        def fake_enum_windows(callback: Any, extra: object) -> None:
            for hwnd, owner_pid in windows.items():
                callback(hwnd, extra)

        with (
            patch(f"{_PROCESS_UTIL}.win32gui.EnumWindows", side_effect=fake_enum_windows),
            patch(
                f"{_PROCESS_UTIL}.win32process.GetWindowThreadProcessId",
                side_effect=lambda hwnd: (0, windows[hwnd]),
            ),
            patch(f"{_PROCESS_UTIL}.win32gui.PostMessage") as mock_post,
        ):
            post_close_message(1234)

        assert [call.args for call in mock_post.call_args_list] == [
            (0x100, win32con.WM_CLOSE, 0, 0),
            (0x300, win32con.WM_CLOSE, 0, 0),
        ]


class TestOpenWaitHandle:
    """Tests for opening a process handle that can be waited on."""

    def test_returns_synchronize_handle_for_openable_process(self) -> None:
        """A live process is opened for ``SYNCHRONIZE`` access and its handle returned."""
        with patch("win32api.OpenProcess", return_value=0xCAFE) as mock_open:
            assert _open_wait_handle(1234) == 0xCAFE
        mock_open.assert_called_once_with(win32con.SYNCHRONIZE, False, 1234)

    def test_already_exited_process_returns_none_without_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A pid that no longer exists is the expected case and yields no warning."""
        caplog.set_level(logging.WARNING)
        error = pywintypes.error(87, "OpenProcess", "The parameter is incorrect.")
        with patch("win32api.OpenProcess", side_effect=error):
            assert _open_wait_handle(1234) is None
        assert caplog.records == []

    def test_unexpected_open_error_returns_none_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Any other failure still yields ``None`` but is surfaced as a warning naming the pid."""
        caplog.set_level(logging.WARNING)
        error = pywintypes.error(5, "OpenProcess", "Access is denied.")
        with patch("win32api.OpenProcess", side_effect=error):
            assert _open_wait_handle(1234) is None
        assert "cannot wait on process 1234" in caplog.text


class TestCloseAndWaitForExit:
    """Tests for asking a batch of processes to close and waiting for them to exit."""

    @staticmethod
    def _patch_clock(*readings: float) -> Any:
        """Patch the module's ``time`` with a clock returning ``readings`` in order."""
        fake_time = Mock()
        fake_time.monotonic.side_effect = list(readings)
        return patch(f"{_PROCESS_UTIL}.time", fake_time)

    def test_more_than_64_pids_raises_before_opening_handles(self) -> None:
        """Exceeding the Win32 wait-object limit fails before any handle is opened."""
        with patch(f"{_PROCESS_UTIL}._open_wait_handle") as mock_open:
            with pytest.raises(ValueError, match="exceeds Win32 limit"):
                close_and_wait_for_exit(list(range(65)))
        mock_open.assert_not_called()

    def test_all_pids_already_gone_skips_post_and_wait(self) -> None:
        """When no handle can be opened, nothing is posted, waited on or closed."""
        with (
            patch(f"{_PROCESS_UTIL}._open_wait_handle", return_value=None),
            patch(f"{_PROCESS_UTIL}.post_close_message") as mock_post,
            patch("win32event.WaitForMultipleObjects") as mock_wait,
            patch("win32api.CloseHandle") as mock_close,
        ):
            close_and_wait_for_exit([1, 2])

        mock_post.assert_not_called()
        mock_wait.assert_not_called()
        mock_close.assert_not_called()

    def test_waits_until_all_processes_exit(self) -> None:
        """Opens one handle per distinct pid, asks each to close, then closes the handles."""
        handles = {1: 0xAA, 2: 0xBB}
        with (
            patch(f"{_PROCESS_UTIL}._open_wait_handle", side_effect=handles.get) as mock_open,
            patch(f"{_PROCESS_UTIL}.post_close_message") as mock_post,
            patch(
                "win32event.WaitForMultipleObjects",
                return_value=win32event.WAIT_OBJECT_0,
            ) as mock_wait,
            patch("win32api.CloseHandle") as mock_close,
            self._patch_clock(0.0, 0.5),
        ):
            close_and_wait_for_exit([1, 1, 2])

        assert sorted(call.args[0] for call in mock_open.call_args_list) == [1, 2]
        assert sorted(call.args[0] for call in mock_post.call_args_list) == [1, 2]
        waited_handles, wait_all, wait_ms = mock_wait.call_args.args
        assert (sorted(waited_handles), wait_all, wait_ms) == ([0xAA, 0xBB], True, 500)
        assert sorted(call.args[0] for call in mock_close.call_args_list) == [0xAA, 0xBB]

    def test_retries_wait_after_initial_timeout(self) -> None:
        """A wait that times out while time remains is retried until a process exits."""
        handles = {1: 0xAA}
        with (
            patch(f"{_PROCESS_UTIL}._open_wait_handle", side_effect=handles.get),
            patch(f"{_PROCESS_UTIL}.post_close_message"),
            patch(
                "win32event.WaitForMultipleObjects",
                side_effect=[win32event.WAIT_TIMEOUT, win32event.WAIT_OBJECT_0],
            ) as mock_wait,
            patch("win32api.CloseHandle") as mock_close,
            self._patch_clock(0.0, 0.5, 1.0),
        ):
            close_and_wait_for_exit([1])

        assert mock_wait.call_count == 2
        mock_close.assert_called_once_with(0xAA)

    def test_warns_only_about_processes_that_survive_the_timeout(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """At the deadline only the still-running pid is reported, and handles still close."""
        caplog.set_level(logging.WARNING)
        handles = {1: 0xAA, 2: 0xBB}
        with (
            patch(f"{_PROCESS_UTIL}._open_wait_handle", side_effect=handles.get),
            patch(f"{_PROCESS_UTIL}.post_close_message"),
            patch("win32event.WaitForMultipleObjects", return_value=win32event.WAIT_TIMEOUT),
            patch(
                "win32event.WaitForSingleObject",
                side_effect=lambda handle, _ms: (
                    win32event.WAIT_OBJECT_0 if handle == 0xAA else win32event.WAIT_TIMEOUT
                ),
            ),
            patch("win32api.CloseHandle") as mock_close,
            self._patch_clock(0.0, 0.5, 5.0),
        ):
            close_and_wait_for_exit([1, 2])

        assert caplog.messages == ["processes [2] did not close within 5s"]
        assert sorted(call.args[0] for call in mock_close.call_args_list) == [0xAA, 0xBB]
