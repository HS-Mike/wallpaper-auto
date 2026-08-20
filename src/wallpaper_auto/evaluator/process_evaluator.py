"""
Process condition evaluator.

Checks whether a process matching an executable basename or full path is
currently running. Uses :func:`get_running_processes` from
``util.process_util`` for its data source and :func:`get_executable_path` to
resolve full paths on demand.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..util.process_util import get_executable_path, get_running_processes
from .base_evaluator import BaseEvaluator


class ProcessEvaluator(BaseEvaluator):
    """Check if a process identified by basename or full path is running."""

    def __call__(self, param: str) -> bool:
        """Check whether the given process identifier is currently running.

        Args:
            param: An executable basename (``"notepad.exe"``, matched against
                any process with that name regardless of location) or a full
                path (``"C:\\Windows\\System32\\notepad.exe"``, matched only
                against processes launched from that exact path). Matching is
                case-insensitive.

        Returns:
            True if a matching process is running, False otherwise. Also False
            when the process snapshot cannot be taken (transient failure).

        Raises:
            ValueError: If ``param`` is not a string.
        """
        if not isinstance(param, str):
            raise ValueError(f"invalid {self.__class__.__name__} param")
        identifier = param.strip()
        if not identifier:
            return False
        p = Path(identifier)
        processes = get_running_processes()
        if processes is None:
            return False
        # An entry with no directory component is treated as a basename;
        # anything with at least one separator is treated as a full path.
        if p.parent == Path(".") or str(p.parent) == "":
            basename = p.name.lower()
            return any(name.lower() == basename for _, name in processes)

        target = os.path.normpath(os.path.normcase(identifier))
        target_base = p.name.lower()
        for pid, name in processes:
            if name.lower() == target_base:
                exe = get_executable_path(pid)
                if exe and os.path.normpath(os.path.normcase(exe)) == target:
                    return True
        return False
