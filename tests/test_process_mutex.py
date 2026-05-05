"""Tests for process_mutex.py — ProcessMutex Windows singleton lock."""

import os
import tempfile
from unittest.mock import patch

import pytest

from wallpaper_automator.process_mutex import ProcessMutex


class TestInit:
    """ProcessMutex.__init__ — lock path construction and initial state."""

    def test_default_lock_path_uses_tempdir(self):
        mutex = ProcessMutex(name="test")
        expected = os.path.join(tempfile.gettempdir(), "test.lock")
        assert mutex.lock_path == expected

    def test_custom_name(self):
        mutex = ProcessMutex(name="my_app")
        expected = os.path.join(tempfile.gettempdir(), "my_app.lock")
        assert mutex.lock_path == expected

    def test_custom_lock_dir(self, tmp_path):
        mutex = ProcessMutex(name="test", lock_dir=str(tmp_path))
        assert mutex.lock_path == os.path.join(str(tmp_path), "test.lock")

    def test_handle_starts_none(self):
        mutex = ProcessMutex(name="test")
        assert mutex.handle is None


class TestLock:
    """ProcessMutex.lock() — acquiring the lock."""

    def test_success_returns_true(self):
        with patch("wallpaper_automator.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test_success")
            result = mutex.lock()
            assert result is True
            assert mutex.handle is not None

    def test_double_lock_raises_runtime_error(self):
        with patch("wallpaper_automator.process_mutex.msvcrt.locking") as mock_lock:
            mutex = ProcessMutex(name="test_double_lock")
            mutex.lock()
            mock_lock.assert_called_once()

            msg = r"lock\(\) called twice without calling unlock\(\)"
            with pytest.raises(RuntimeError, match=msg):
                mutex.lock()
            # handle should still be valid (we didn't unlock)
            assert mutex.handle is not None
            mock_lock.assert_called_once()

    def test_oserror_raises_runtime_error(self):
        with patch(
            "wallpaper_automator.process_mutex.msvcrt.locking",
            side_effect=OSError(32, "Lock violation"),
        ):
            mutex = ProcessMutex(name="test_oserror")
            with pytest.raises(RuntimeError, match="already running"):
                mutex.lock()
            assert mutex.handle is None


class TestUnlock:
    """ProcessMutex.unlock() — releasing the lock."""

    def test_unlock_clears_handle(self):
        with patch("wallpaper_automator.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test_unlock")
            mutex.lock()
            assert mutex.handle is not None
            mutex.unlock()
            assert mutex.handle is None

    def test_unlock_when_not_locked_is_noop(self):
        mutex = ProcessMutex(name="test")
        mutex.unlock()  # should not raise
        assert mutex.handle is None


class TestContextManager:
    """ProcessMutex as a context manager (``with`` statement)."""

    def test_enter_returns_self(self):
        with patch("wallpaper_automator.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test_ctx")
            with mutex as m:
                assert m is mutex
                assert mutex.handle is not None

    def test_exit_releases_lock(self):
        with patch("wallpaper_automator.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test_ctx_exit")
            with mutex:
                assert mutex.handle is not None
            assert mutex.handle is None


class TestDel:
    """ProcessMutex.__del__ — cleanup during garbage collection."""

    def test_del_when_never_locked_is_noop(self):
        mutex = ProcessMutex(name="test")
        mutex.__del__()  # should not raise

    def test_del_cleans_up_locked_handle(self):
        with patch("wallpaper_automator.process_mutex.msvcrt.locking"):
            mutex = ProcessMutex(name="test_del")
            mutex.lock()
            assert mutex.handle is not None
            mutex.__del__()
            assert mutex.handle is None
