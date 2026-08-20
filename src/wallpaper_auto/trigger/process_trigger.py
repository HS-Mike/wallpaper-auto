"""
Process lifecycle trigger.

Monitors Windows process creation and deletion events for a configurable list
of executable basenames via WMI's ``__InstanceOperationEvent`` subscription
against ``Win32_Process`` and fires callbacks on each transition. The most
recent event is exposed via ``last_event`` for downstream evaluators to query
during callback handling.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import override

import pythoncom
import wmi

from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)


DEFAULT_WITHIN_SECONDS: int = 2
WATCHER_TIMEOUT_MS: int = 1000


class ProcessEventType(Enum):
    """The kind of process lifecycle transition a ``ProcessEvent`` represents."""

    STARTED = "started"
    STOPPED = "stopped"


@dataclass(frozen=True)
class ProcessEvent:
    """Immutable snapshot of a process lifecycle transition."""

    exe_name: str  # basename of the process executable, original WMI casing preserved
    pid: int  # process ID at the time of the event
    event_type: ProcessEventType  # whether the event was a process start or stop


class ProcessTrigger(BaseThreadTrigger):
    """Trigger fired on process start and stop events for watched executables.

    Subscribes to WMI's ``__InstanceOperationEvent`` for ``Win32_Process``
    instances whose ``Name`` (case-insensitive) matches one of the configured
    basenames. Each creation or deletion sets ``last_event`` and fires the
    registered callbacks.
    """

    def __init__(
        self,
        exe_names: Iterable[str | Path],
    ) -> None:
        """Create a process trigger.

        Args:
            exe_names: Executable basenames to watch. Each item may be a plain
                filename like ``"app.exe"`` or a full path; only the basename
                is matched. Comparison is case-insensitive. Blank entries are
                skipped.

        Raises:
            ValueError: If ``exe_names`` is empty or contains only blank entries.
        """
        super().__init__()

        normalized = {Path(item).name.strip().lower() for item in exe_names}
        normalized.discard("")
        if not normalized:
            raise ValueError("exe_names must contain at least one non-empty executable basename")

        self.exe_names: list[str] = sorted(normalized)
        self.last_event: ProcessEvent | None = None

    def _build_wql(self) -> str:
        """Build the WMI event subscription query with proper name escaping.

        WMI's ``=`` operator uses a case-insensitive collation for string
        comparisons, so explicit ``LOWER()`` is unnecessary (and rejected by
        the parser for extrinsic event queries like this one). Single quotes
        in filenames are escaped by doubling (WQL convention).

        Returns:
            The WQL string for ``__InstanceOperationEvent`` filtering on
            ``Win32_Process`` and the configured basenames.
        """
        quote = "'"
        conditions = " OR ".join(
            f"TargetInstance.Name = '{name.replace(quote, quote * 2)}'" for name in self.exe_names
        )
        return (
            f"SELECT * FROM __InstanceOperationEvent WITHIN {DEFAULT_WITHIN_SECONDS} "
            f"WHERE TargetInstance ISA 'Win32_Process' AND ({conditions})"
        )

    @override
    def run(self) -> None:
        """Subscribe to WMI process events and dispatch callbacks until stopped."""
        pythoncom.CoInitialize()
        try:
            wql = self._build_wql()
            watcher = wmi.WMI().watch_for(raw_wql=wql)
            logger.debug(
                f"{self._format_identity()} started process monitoring for: {self.exe_names}"
            )

            while not self.stop_event.is_set():
                try:
                    event = watcher(timeout_ms=WATCHER_TIMEOUT_MS)
                    event_class = event.event_type
                    if event_class not in ("creation", "deletion"):
                        continue
                    event_type: ProcessEventType = (
                        ProcessEventType.STARTED
                        if event_class == "creation"
                        else ProcessEventType.STOPPED
                    )
                    self.last_event = ProcessEvent(
                        exe_name=event.Name,
                        pid=int(event.ProcessId),
                        event_type=event_type,
                    )
                    self.trigger()
                except wmi.x_wmi_timed_out:
                    continue
        finally:
            pythoncom.CoUninitialize()
