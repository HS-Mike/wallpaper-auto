"""
Task classes transmit across components.
"""

from __future__ import annotations

import secrets
import threading
from enum import Enum

from .models import Rule


class Mode(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    UNSET = "unset"


class BaseTask:
    """Base class for all tasks with a unique id and completion signaling."""

    def __init__(self, completed_event: threading.Event | None = None) -> None:
        """Initialize the task with a unique id and a completion event.

        Args:
            completed_event: Optional event signaled when the task finishes;
                a new event is created when omitted.
        """
        self.id = secrets.randbits(63)
        self.completed_event = completed_event or threading.Event()

    def mark_finish(self) -> None:
        self.completed_event.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until the task finishes or the timeout elapses.

        Args:
            timeout: Maximum seconds to wait, or None to wait indefinitely.

        Returns:
            True if the task finished before the timeout, False otherwise.
        """
        return self.completed_event.wait(timeout=timeout)

    def __hash__(self) -> int:
        return self.id


class QuitTask(BaseTask):
    pass


class ModeSwitchTask(BaseTask):
    def __init__(self, target_mode: Mode) -> None:
        super().__init__()
        self.target_mode = target_mode


class UpdateSceneTask(BaseTask):
    def __init__(
        self,
        target: str,
        matched_rule: Rule | None,
        completed_event: threading.Event | None = None,
    ) -> None:
        """Initialize a scene-update task.

        Args:
            target: Target resource or scene id to apply.
            matched_rule: Rule that selected the target, or None for a
                fallback or explicit selection.
            completed_event: Optional completion event, forwarded to
                :class:`BaseTask`.
        """
        super().__init__(completed_event=completed_event)
        self.target = target
        self.matched_rule = matched_rule


class ApplySceneTask(BaseTask):
    pass


Task = QuitTask | ModeSwitchTask | UpdateSceneTask | ApplySceneTask
