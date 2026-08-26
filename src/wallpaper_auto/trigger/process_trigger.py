"""
Process lifecycle trigger.

Monitors Windows process creation and deletion events for a configurable list
of executable identifiers via WMI's ``__InstanceOperationEvent`` subscription
against ``Win32_Process`` and fires callbacks on each transition. The most
recent event is exposed via ``last_event`` for downstream evaluators to query
during callback handling.

Each entry of the watched list may be either a plain basename (e.g.
``"notepad.exe"``), which matches any process with that name regardless of
location, or a full path (e.g. ``"C:\\Windows\\System32\\notepad.exe"``),
which matches only processes launched from that exact path. Mixed lists are
supported and each entry is classified by whether it contains a directory
component.

Path resolution uses a hybrid strategy: WMI's ``ExecutablePath`` field is
preferred (already populated for most processes), and
``util.process_util.get_executable_path`` (via ``OpenProcess`` +
``QueryFullProcessImageNameW``) provides a fallback for ``started`` events
where WMI omits the field (typically when the target runs under a higher
integrity level). Stopped events, which can't be queried after the process
exits, reuse the path recorded when the same PID was observed starting.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import override

import pythoncom
import wmi

from wallpaper_auto.util.process_util import get_executable_path

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
    exe_path: str | None = None  # full path via WMI ``ExecutablePath`` or Win32 fallback


class ProcessTrigger(BaseThreadTrigger):
    """Trigger fired on process start and stop events for watched executables."""

    def __init__(
        self,
        exe_names: Iterable[str | Path],
    ) -> None:
        """Create a process trigger.

        Args:
            exe_names: Executable identifiers to watch. Each item may be:
                a plain basename (``"app.exe"``) matched against any process
                with that name; or a full path
                (``"C:\\Windows\\System32\\app.exe"``) matched only against
                processes launched from that exact path. Entries are matched
                case-insensitively on Windows. Blank entries are skipped.

        Raises:
            ValueError: If ``exe_names`` is empty or contains only blank entries.
        """
        super().__init__()

        basenames: set[str] = set()
        full_paths: set[str] = set()

        for item in exe_names:
            s = str(item).strip()
            if not s:
                continue
            p = Path(s)
            # An entry with no directory component is treated as a basename;
            # anything with at least one separator is treated as a full path.
            if p.parent == Path(".") or str(p.parent) == "":
                basenames.add(p.name.lower())
            else:
                full_paths.add(os.path.normpath(os.path.normcase(s)))

        if not basenames and not full_paths:
            raise ValueError("exe_names must contain at least one non-empty executable identifier")

        self.exe_names: list[str] = sorted(basenames)
        self.exe_paths: list[str] = sorted(full_paths)
        self.last_event: ProcessEvent | None = None

    def _build_wql(self) -> str:
        """Build the WMI event subscription query with proper name escaping.

        WMI's ``=`` operator uses a case-insensitive collation for string
        comparisons, so explicit ``LOWER()`` is unnecessary (and rejected by
        the parser for extrinsic event queries like this one). Single quotes
        in filenames are escaped by doubling (WQL convention).

        The subscription is built over bare basename inputs plus basenames
        extracted from full-path inputs — WMI extrinsic events only reliably
        carry ``Win32_Process.Name``, so the listener must subscribe over
        all basenames to be notified of full-path matches.

        Returns:
            The WQL string for ``__InstanceOperationEvent`` filtering on
            ``Win32_Process`` and the configured basenames (including
            basenames extracted from full-path inputs).
        """
        quote = "'"
        conditions = " OR ".join(
            f"TargetInstance.Name = '{name.replace(quote, quote * 2)}'"
            for name in set(self.exe_names) | {Path(p).name.lower() for p in self.exe_paths}
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
                f"{self._format_identity()} started process monitoring for: "
                f"basenames={self.exe_names}, paths={self.exe_paths}"
            )

            # PID -> resolved executable path captured at process start. A
            # stopped event can't query the path (the handle is gone) and WMI
            # may omit ``ExecutablePath`` on deletion, so the path recorded at
            # start is reused to keep full-path matching working for stops.
            seen_paths: dict[int, str] = {}

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
                    pid = int(event.ProcessId)
                    exe_name = event.Name

                    # Hybrid path resolution: WMI's ExecutablePath first; the
                    # Win32 fallback is only meaningful for started events
                    # since the process handle closes on exit. Started events
                    # cache the resolved path by PID; stopped events that lack
                    # one reuse the cached path and always drop the entry.
                    exe_path = getattr(event, "ExecutablePath", None)
                    if event_type == ProcessEventType.STARTED:
                        if not exe_path:
                            exe_path = get_executable_path(pid)
                        if exe_path:
                            seen_paths[pid] = exe_path
                    else:
                        exe_path = exe_path or seen_paths.pop(pid, None)

                    # Skip unless the event matches a watched basename (any
                    # path) or a watched full path. An unresolved path never
                    # matches a full-path entry.
                    matches = exe_name.lower() in self.exe_names or (
                        exe_path and os.path.normpath(os.path.normcase(exe_path)) in self.exe_paths
                    )
                    if not matches:
                        continue

                    self.last_event = ProcessEvent(
                        exe_name=exe_name,
                        pid=pid,
                        event_type=event_type,
                        exe_path=exe_path,
                    )
                    self.trigger()
                except wmi.x_wmi_timed_out:
                    continue
        finally:
            pythoncom.CoUninitialize()
