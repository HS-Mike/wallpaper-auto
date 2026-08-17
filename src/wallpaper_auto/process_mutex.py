"""Windows process-singleton mutex using kernel-level file locking.

Ensures only one instance of a process runs at a time. The lock is
managed via ``msvcrt.locking()`` on the first byte of a lock file and
is automatically released by the OS if the process crashes — leftover
lock files on disk are harmless.
"""

import logging
import msvcrt
import sys
import os
import tempfile
from typing import IO, Any

logger = logging.getLogger(__name__)


class ProcessMutex:
    """A Windows-specific mutual exclusion (mutex) lock.

    Supports both context manager (``with``) and traditional
    ``lock()`` / ``unlock()`` methods.

    Args:
        name: Identifier for the lock file (``{name}.lock``). Use a different
            name for each application you want to guard.
        lock_dir: Directory for the lock file. Defaults to the system
            temporary directory.
        raise_error: When True (the default), a contended lock raises
            ``RuntimeError``. When False, the process exits with status code
            1 instead.
    """

    def __init__(self, name: str, lock_dir: str | None = None, raise_error: bool = True) -> None:
        base_dir = lock_dir or tempfile.gettempdir()
        self.lock_path: str = os.path.join(base_dir, f"{name}.lock")
        self.handle: IO[Any] | None = None
        self.raise_error = raise_error

    def lock(self) -> None:
        """
        Acquire the lock.

        If the lock is already held by this instance or by another
        process, raises ``RuntimeError`` when ``raise_error`` is True
        (the default), or calls ``sys.exit(1)`` when it is False.
        """
        if self.handle is not None:
            raise RuntimeError("lock() called twice without calling unlock()")

        self.handle = open(self.lock_path, "a")

        try:
            # Atomic Windows kernel lock on the first byte
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            self.handle = None
            logger.error("Process mutex is already held for '%s'.", self.lock_path)
            if self.raise_error is True:
                raise RuntimeError(f"Another instance is already running (mutex: {self.lock_path})")
            else:
                sys.exit(1)

    def unlock(self) -> None:
        """Release the lock."""
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "ProcessMutex":
        self.lock()
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: object | None,
    ) -> None:
        self.unlock()

    def __del__(self) -> None:
        """Final safety net: close the handle during garbage collection."""
        self.unlock()
