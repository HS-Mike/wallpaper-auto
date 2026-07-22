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
        self.id = secrets.randbits(63)
        self.completed_event = completed_event or threading.Event()

    def mark_finish(self) -> None:
        self.completed_event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.completed_event.wait(timeout=timeout)

    def __hash__(self) -> int:
        return self.id


class QuitTask(BaseTask):
    pass


class ModeSwitchTask(BaseTask):
    def __init__(self, target_mode: Mode) -> None:
        super().__init__()
        self.target_mode = target_mode


class TargetSetTask(BaseTask):
    def __init__(
        self,
        target: str,
        matched_rule: Rule | None,
        completed_event: threading.Event | None = None,
    ) -> None:
        super().__init__(completed_event=completed_event)
        self.target = target
        self.matched_rule = matched_rule


class PlotCanvasTask(BaseTask):
    pass


Task = QuitTask | ModeSwitchTask | TargetSetTask | PlotCanvasTask
