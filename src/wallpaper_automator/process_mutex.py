"""Windows process-singleton mutex using kernel-level file locking.

Ensures only one instance of a process runs at a time. The lock is
managed via ``msvcrt.locking()`` on the first byte of a lock file and
is automatically released by the OS if the process crashes — leftover
lock files on disk are harmless.
"""

import logging
import msvcrt
import os
import tempfile
from typing import IO, Any

logger = logging.getLogger(__name__)


class ProcessMutex:
    """
    A Windows-specific mutual exclusion (mutex) lock.

    Supports both context manager (``with``) and traditional
    ``lock()`` / ``unlock()`` methods.

    Parameters
    ----------
    name:
        Identifier for the lock file (``{name}.lock``). Use a different
        name for each application you want to guard.
    lock_dir:
        Directory for the lock file. Defaults to the system temporary
        directory.
    """

    def __init__(self, name: str, lock_dir: str | None = None) -> None:
        base_dir = lock_dir or tempfile.gettempdir()
        self.lock_path: str = os.path.join(base_dir, f"{name}.lock")
        self.handle: IO[Any] | None = None

    def lock(self) -> bool:
        """
        Acquire the lock.

        Returns True if the lock was acquired.
        Raises RuntimeError if the lock is already held by this instance
        or by another process.
        """
        if self.handle is not None:
            raise RuntimeError("lock() called twice without calling unlock()")

        self.handle = open(self.lock_path, "a")

        try:
            # Atomic Windows kernel lock on the first byte
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            logger.error("Process mutex is already held for '%s'.", self.lock_path)
            raise RuntimeError(f"Another instance is already running (mutex: {self.lock_path})")

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
