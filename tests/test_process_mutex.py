"""Tests for process_mutex.py — ProcessMutex Windows singleton lock."""

from __future__ import annotations

import multiprocessing
import os
import tempfile
from unittest.mock import patch

import pytest

from wallpaper_auto.process_mutex import ProcessMutex


class TestInit:
    """ProcessMutex.__init__ — lock path construction."""

    def test_default_lock_path(self):
        mutex = ProcessMutex(name="test")
        assert mutex.lock_path == os.path.join(tempfile.gettempdir(), "test.lock")
        assert mutex.handle is None

    def test_custom_lock_dir(self, tmp_path):
        mutex = ProcessMutex(name="test", lock_dir=str(tmp_path))
        assert mutex.lock_path == os.path.join(str(tmp_path), "test.lock")

    def test_raise_error_defaults_to_true(self):
        mutex = ProcessMutex(name="test")
        assert mutex.raise_error is True

    def test_raise_error_false(self):
        mutex = ProcessMutex(name="test", raise_error=False)
        assert mutex.raise_error is False


class TestLock:
    """ProcessMutex.lock() — acquiring the lock."""

    def test_success(self):
        with patch("wallpaper_auto.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test")
            mutex.lock()
            assert mutex.handle is not None

    def test_double_lock_raises(self):
        with patch("wallpaper_auto.process_mutex.msvcrt.locking") as mock_lock:
            mutex = ProcessMutex(name="test")
            mutex.lock()
            mock_lock.assert_called_once()

            with pytest.raises(RuntimeError, match="called twice without calling unlock"):
                mutex.lock()
            assert mutex.handle is not None
            mock_lock.assert_called_once()

    def test_contended_raises(self):
        with patch(
            "wallpaper_auto.process_mutex.msvcrt.locking",
            side_effect=OSError(32, "Lock violation"),
        ):
            mutex = ProcessMutex(name="test")
            with pytest.raises(RuntimeError, match="already running"):
                mutex.lock()
            assert mutex.handle is None

    def test_contended_exits_when_raise_error_false(self):
        with (
            patch(
                "wallpaper_auto.process_mutex.msvcrt.locking",
                side_effect=OSError(32, "Lock violation"),
            ),
            patch("wallpaper_auto.process_mutex.sys.exit") as mock_exit,
        ):
            mutex = ProcessMutex(name="test", raise_error=False)
            mutex.lock()
            mock_exit.assert_called_once_with(1)
            assert mutex.handle is None


class TestUnlock:
    """ProcessMutex.unlock() — releasing the lock."""

    def test_clears_handle(self):
        with patch("wallpaper_auto.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test")
            mutex.lock()
            mutex.unlock()
            assert mutex.handle is None

    def test_noop_when_not_locked(self):
        mutex = ProcessMutex(name="test")
        mutex.unlock()  # should not raise
        assert mutex.handle is None


class TestContextManager:
    """ProcessMutex as a context manager."""

    def test_enter_exit(self):
        with patch("wallpaper_auto.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test")
            with mutex as m:
                assert m is mutex
                assert mutex.handle is not None
            assert mutex.handle is None


def _child_take_lock(
    name: str,
    lock_dir: str,
    child_ready: multiprocessing.Queue,
    parent_done: multiprocessing.Queue,
) -> None:
    """Run in a subprocess: acquire lock, signal parent, wait, release."""
    mutex = ProcessMutex(name, lock_dir)
    mutex.lock()
    child_ready.put("locked")
    parent_done.get()
    mutex.unlock()
    child_ready.put("unlocked")
    parent_done.get()


def _child_try_lock(
    name: str,
    lock_dir: str,
    result_queue: multiprocessing.Queue,
) -> None:
    """Run in a subprocess: attempt to acquire lock, report outcome."""
    try:
        with ProcessMutex(name, lock_dir):
            result_queue.put("locked")
    except RuntimeError:
        result_queue.put("denied")


def _child_try_lock_exit(name: str, lock_dir: str) -> None:
    """Run in a subprocess: contend with raise_error=False; exit code 1 if denied."""
    ProcessMutex(name, lock_dir, raise_error=False).lock()


class TestMultiProcess:
    """Cross-process mutex integration tests using real ``msvcrt.locking``."""

    def test_exclusion_works_across_processes(self, tmp_path):
        name = "integ_test"
        lock_dir = str(tmp_path)
        child_ready: multiprocessing.Queue[str] = multiprocessing.Queue()
        parent_done: multiprocessing.Queue[str] = multiprocessing.Queue()

        p = multiprocessing.Process(
            target=_child_take_lock,
            args=(name, lock_dir, child_ready, parent_done),
        )
        p.start()

        try:
            # Child holds lock → parent denied
            assert child_ready.get(timeout=5) == "locked"
            parent_mutex = ProcessMutex(name, lock_dir)
            with pytest.raises(RuntimeError, match="already running"):
                parent_mutex.lock()

            # Child releases → parent succeeds
            parent_done.put("release")
            assert child_ready.get(timeout=5) == "unlocked"
            parent_mutex.lock()
            assert parent_mutex.handle is not None
            parent_mutex.unlock()
        finally:
            parent_done.put("done")
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()

    def test_parent_holds_lock_child_denied(self, tmp_path):
        name = "integ_test"
        lock_dir = str(tmp_path)
        result_queue: multiprocessing.Queue[str] = multiprocessing.Queue()

        # Parent acquires first
        parent_mutex = ProcessMutex(name, lock_dir)
        parent_mutex.lock()

        p = multiprocessing.Process(
            target=_child_try_lock,
            args=(name, lock_dir, result_queue),
        )
        p.start()

        try:
            assert result_queue.get(timeout=5) == "denied"
        finally:
            parent_mutex.unlock()
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()

    def test_child_exits_code_1_when_raise_error_false(self, tmp_path):
        name = "integ_test"
        lock_dir = str(tmp_path)

        parent_mutex = ProcessMutex(name, lock_dir)
        parent_mutex.lock()

        p = multiprocessing.Process(target=_child_try_lock_exit, args=(name, lock_dir))
        p.start()
        try:
            p.join(timeout=5)
            assert p.exitcode == 1
        finally:
            parent_mutex.unlock()
            if p.is_alive():
                p.terminate()
