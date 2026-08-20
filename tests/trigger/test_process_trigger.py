"""Tests for trigger/process_trigger.py — Windows process lifecycle monitoring via WMI."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
import wmi

from wallpaper_auto.trigger.base_trigger import BaseTrigger
from wallpaper_auto.trigger.process_trigger import (
    DEFAULT_WITHIN_SECONDS,
    ProcessEvent,
    ProcessEventType,
    ProcessTrigger,
)


@pytest.fixture
def mock_pythoncom() -> Iterator[MagicMock]:
    with patch("wallpaper_auto.trigger.process_trigger.pythoncom") as m:
        yield m


def _make_event(
    event_class: str,
    name: str = "notepad.exe",
    pid: int = 1234,
    exe_path: str | None = None,
) -> MagicMock:
    """Build a mock WMI event with the given class and target process fields.

    The wmi library unwraps the TargetInstance reference, so Name,
    ProcessId, and ExecutablePath live directly on the event object.

    Args:
        event_class: The wmi event_type string: "creation", "deletion",
            or "modification". The ProcessTrigger maps "creation" to
            "started" and "deletion" to "stopped".
        name: ``Win32_Process.Name`` — the basename of the executable.
        pid: ``Win32_Process.ProcessId``.
        exe_path: ``Win32_Process.ExecutablePath``. Set explicitly (rather
            than relying on auto-spec attributes) so the trigger's
            ``getattr(..., None) or None`` check sees ``None`` rather than
            an auto-created MagicMock.
    """
    event = MagicMock()
    event.event_type = event_class
    event.Name = name
    event.ProcessId = pid
    event.ExecutablePath = exe_path
    return event


def _run_until_first_callback(
    trigger: ProcessTrigger, event: MagicMock, mock_wmi_cls: MagicMock
) -> None:
    """Run the trigger loop with a single mocked event, breaking after the first callback.

    Wires the trigger's own callback to set stop_event on the first call, so the
    loop sees the event, processes it, and exits on the next iteration when the
    watcher raises x_wmi_timed_out.
    """

    def on_callback(_t: BaseTrigger) -> None:
        trigger.stop_event.set()

    trigger.add_callback(on_callback)

    watcher = MagicMock(side_effect=[event, wmi.x_wmi_timed_out()])
    mock_wmi_cls.return_value.watch_for.return_value = watcher

    with patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"):
        trigger.run()


class TestProcessTriggerInit:
    """Tests for ProcessTrigger initial state and config validation."""

    def test_empty_exe_names_raises(self) -> None:
        """Constructor raises ValueError when exe_names is empty."""
        with pytest.raises(ValueError, match="at least one non-empty"):
            ProcessTrigger(exe_names=[])

    def test_blank_exe_names_raises(self) -> None:
        """Constructor raises ValueError when exe_names contains only blank entries."""
        with pytest.raises(ValueError, match="at least one non-empty"):
            ProcessTrigger(exe_names=["", "  "])

    def test_mixed_inputs_classified_into_both_sets(self) -> None:
        """Bare names go to ``exe_names``; full-path entries go only to ``exe_paths``."""
        trigger = ProcessTrigger(
            exe_names=[
                "notepad.exe",
                "C:\\Windows\\System32\\notepad.exe",
                "mspaint.exe",
            ]
        )

        assert trigger.exe_names == ["mspaint.exe", "notepad.exe"]
        assert trigger.exe_paths == ["c:\\windows\\system32\\notepad.exe"]

    def test_path_normalization_lowercases_and_uses_backslashes(self) -> None:
        """Path entries are lowercased and forward slashes converted to backslashes."""
        trigger = ProcessTrigger(
            exe_names=["C:/Program Files/SomeApp/App.EXE", "D:/Tools/TOOL.EXE"]
        )

        assert trigger.exe_paths == [
            "c:\\program files\\someapp\\app.exe",
            "d:\\tools\\tool.exe",
        ]

    def test_names_are_lowercased_and_deduped(self) -> None:
        """Mixed-case names are lowercased; duplicates collapse to one entry."""
        trigger = ProcessTrigger(exe_names=["APP.EXE", "App.exe", "app.exe"])

        assert trigger.exe_names == ["app.exe"]

    def test_blank_entries_skipped(self) -> None:
        """Blank entries are discarded; remaining names are kept."""
        trigger = ProcessTrigger(exe_names=["", "chrome.exe", "  "])

        assert trigger.exe_names == ["chrome.exe"]

    def test_initial_last_event_is_none(self) -> None:
        """last_event starts as None before any event is observed."""
        trigger = ProcessTrigger(exe_names=["app.exe"])

        assert trigger.last_event is None

    def test_process_event_is_frozen(self) -> None:
        """ProcessEvent rejects field assignment so callbacks can't mutate captured events."""
        event = ProcessEvent(exe_name="notepad.exe", pid=1234, event_type=ProcessEventType.STARTED)

        with pytest.raises((AttributeError, Exception)):
            event.pid = 9999  # type: ignore[misc]


class TestProcessTriggerWql:
    """Tests for the WQL subscription query construction."""

    def test_wql_lowercases_configured_names(self) -> None:
        """Configured names appear lowercased in the query, matching their stored form."""
        trigger = ProcessTrigger(exe_names=["Notepad.EXE"])

        wql = trigger._build_wql()

        assert "TargetInstance.Name = 'notepad.exe'" in wql

    def test_wql_does_not_use_lower_function(self) -> None:
        """The SQL LOWER() function is not used; WMI's collation is already case-insensitive.

        WMI's ``=`` operator compares strings case-insensitively, so explicit
        ``LOWER()`` is unnecessary — and is rejected by the parser for
        extrinsic event queries like ``__InstanceOperationEvent``.
        """
        trigger = ProcessTrigger(exe_names=["Notepad.EXE"])

        wql = trigger._build_wql()

        assert "LOWER(" not in wql

    def test_wql_escapes_single_quotes(self) -> None:
        """Single quotes in names are doubled to escape the WQL string literal."""
        trigger = ProcessTrigger(exe_names=["it's.exe"])

        wql = trigger._build_wql()

        assert "TargetInstance.Name = 'it''s.exe'" in wql
        assert "it' 's.exe" not in wql  # raw splitting must not occur

    def test_wql_combines_multiple_names_with_or(self) -> None:
        """Multiple names produce OR-joined conditions inside a single WHERE clause."""
        trigger = ProcessTrigger(exe_names=["alpha.exe", "beta.exe"])

        wql = trigger._build_wql()

        assert "TargetInstance.Name = 'alpha.exe'" in wql
        assert "TargetInstance.Name = 'beta.exe'" in wql
        assert " OR " in wql
        assert wql.count("(") == 1  # the parenthesized condition group
        assert wql.count(")") == 1

    def test_wql_includes_within_clause(self) -> None:
        """The default polling interval flows through to the WQL WITHIN clause."""
        trigger = ProcessTrigger(exe_names=["app.exe"])

        wql = trigger._build_wql()

        assert f"WITHIN {DEFAULT_WITHIN_SECONDS}" in wql

    def test_wql_filters_to_win32_process(self) -> None:
        """WQL restricts events to Win32_Process instances only."""
        trigger = ProcessTrigger(exe_names=["app.exe"])

        wql = trigger._build_wql()

        assert "TargetInstance ISA 'Win32_Process'" in wql
        assert "__InstanceOperationEvent" in wql

    def test_wql_includes_basename_extracted_from_full_path(self) -> None:
        """A full-path entry's basename appears in the WQL subscription.

        WMI extrinsic events only reliably carry ``Win32_Process.Name``,
        so the subscription is over basenames; full-path entries still
        notify the listener (so they can be path-filtered in Python).
        """
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\notepad.exe"])

        wql = trigger._build_wql()

        assert "TargetInstance.Name = 'notepad.exe'" in wql


class TestProcessTriggerRun:
    """Tests for the run() loop — event dispatch, error paths, and lifecycle."""

    def test_creation_event_fires_as_started(self, mock_pythoncom: MagicMock) -> None:
        """event_type='creation' fires the callback with last_event.event_type='started'."""
        trigger = ProcessTrigger(exe_names=["notepad.exe"])
        callback_calls: list[BaseTrigger] = []

        def on_callback(t: BaseTrigger) -> None:
            callback_calls.append(t)
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        event = _make_event(
            "creation",
            "Notepad.exe",
            4321,
            exe_path="C:\\Windows\\System32\\Notepad.exe",
        )

        with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
            _run_until_first_callback(trigger, event, mock_wmi_cls)

        assert len(callback_calls) == 1
        assert trigger.last_event == ProcessEvent(
            exe_name="Notepad.exe",
            pid=4321,
            event_type=ProcessEventType.STARTED,
            exe_path="C:\\Windows\\System32\\Notepad.exe",
        )

    def test_deletion_event_fires_as_stopped(self, mock_pythoncom: MagicMock) -> None:
        """event_type='deletion' fires the callback with last_event.event_type='stopped'."""
        trigger = ProcessTrigger(exe_names=["notepad.exe"])

        def on_callback(_t: BaseTrigger) -> None:
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        event = _make_event("deletion", "notepad.exe", 4321)

        with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
            _run_until_first_callback(trigger, event, mock_wmi_cls)

        assert trigger.last_event is not None
        assert trigger.last_event.event_type == ProcessEventType.STOPPED
        assert trigger.last_event.pid == 4321

    def test_creation_event_with_matching_full_path_fires(self, mock_pythoncom: MagicMock) -> None:
        """A creation event whose resolved path matches a watched full path fires."""
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\System32\\notepad.exe"])
        callback_calls: list[BaseTrigger] = []

        def on_callback(t: BaseTrigger) -> None:
            callback_calls.append(t)
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        event = _make_event(
            "creation",
            "notepad.exe",
            4321,
            exe_path="C:\\Windows\\System32\\notepad.exe",
        )

        with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
            _run_until_first_callback(trigger, event, mock_wmi_cls)

        assert len(callback_calls) == 1
        assert trigger.last_event is not None
        assert trigger.last_event.event_type == ProcessEventType.STARTED

    def test_creation_event_with_non_matching_full_path_is_skipped(
        self, mock_pythoncom: MagicMock
    ) -> None:
        """A creation event whose resolved path differs from a watched full path is ignored.

        The basename still feeds the WQL subscription (so the listener
        is notified), but the path filter rejects the event because the
        resolved path doesn't match the watched entry.
        """
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\System32\\notepad.exe"])
        callback_calls: list[BaseTrigger] = []

        def on_callback(t: BaseTrigger) -> None:
            callback_calls.append(t)
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        # Different path with the same basename — should be filtered out.
        event = _make_event(
            "creation",
            "notepad.exe",
            4321,
            exe_path="C:\\NotepadPortable\\notepad.exe",
        )

        with patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"):
            with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
                watcher = MagicMock(side_effect=[event, RuntimeError("forced exit")])
                mock_wmi_cls.return_value.watch_for.return_value = watcher

                with pytest.raises(RuntimeError, match="forced exit"):
                    trigger.run()

        assert callback_calls == []
        assert trigger.last_event is None

    def test_creation_event_falls_back_to_win32_for_path(self, mock_pythoncom: MagicMock) -> None:
        """Started events use ``QueryFullProcessImageNameW`` when WMI omits ``ExecutablePath``.

        ``Win32_Process.ExecutablePath`` is empty for some permission-protected
        processes; the trigger queries the kernel directly as a fallback.
        """
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\System32\\notepad.exe"])
        callback_calls: list[BaseTrigger] = []

        def on_callback(t: BaseTrigger) -> None:
            callback_calls.append(t)
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        event = _make_event("creation", "notepad.exe", 4321, exe_path=None)

        with (
            patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls,
            patch(
                "wallpaper_auto.trigger.process_trigger.get_executable_path",
                return_value="C:\\Windows\\System32\\notepad.exe",
            ) as mock_get_path,
        ):
            _run_until_first_callback(trigger, event, mock_wmi_cls)

        mock_get_path.assert_called_once_with(4321)
        assert len(callback_calls) == 1
        assert trigger.last_event is not None
        assert trigger.last_event.exe_path == "C:\\Windows\\System32\\notepad.exe"

    def test_deletion_event_does_not_call_win32_fallback(self, mock_pythoncom: MagicMock) -> None:
        """Stopped events skip the Win32 fallback (the handle is already gone)."""
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\System32\\notepad.exe"])

        def on_callback(_t: BaseTrigger) -> None:
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        event = _make_event("deletion", "notepad.exe", 4321, exe_path=None)

        with (
            patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"),
            patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls,
            patch(
                "wallpaper_auto.trigger.process_trigger.get_executable_path",
                return_value="C:\\Windows\\System32\\notepad.exe",
            ) as mock_get_path,
        ):
            watcher = MagicMock(side_effect=[event, RuntimeError("forced exit")])
            mock_wmi_cls.return_value.watch_for.return_value = watcher

            with pytest.raises(RuntimeError, match="forced exit"):
                trigger.run()

        mock_get_path.assert_not_called()
        # Without WMI path and with only full-path entries watched, the
        # event cannot be verified and is skipped.
        assert trigger.last_event is None

    def test_deletion_event_reuses_path_from_started_event(self, mock_pythoncom: MagicMock) -> None:
        """A stopped event without ``ExecutablePath`` reuses the path captured at start.

        When WMI omits the path on a deletion event (typical for
        permission-protected processes), the trigger reuses the path it
        recorded when the same PID was observed starting, so full-path
        matching still works for stops.
        """
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\System32\\notepad.exe"])

        def on_callback(_t: BaseTrigger) -> None:
            if trigger.last_event and trigger.last_event.event_type == ProcessEventType.STOPPED:
                trigger.stop_event.set()

        trigger.add_callback(on_callback)

        start_event = _make_event("creation", "notepad.exe", 4321, exe_path=None)
        stop_event = _make_event("deletion", "notepad.exe", 4321, exe_path=None)

        with (
            patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"),
            patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls,
            patch(
                "wallpaper_auto.trigger.process_trigger.get_executable_path",
                return_value="C:\\Windows\\System32\\notepad.exe",
            ) as mock_get_path,
        ):
            watcher = MagicMock(side_effect=[start_event, stop_event, wmi.x_wmi_timed_out()])
            mock_wmi_cls.return_value.watch_for.return_value = watcher

            trigger.run()

        mock_get_path.assert_called_once_with(4321)
        assert trigger.last_event is not None
        assert trigger.last_event.event_type == ProcessEventType.STOPPED
        assert trigger.last_event.exe_path == "C:\\Windows\\System32\\notepad.exe"

    def test_deletion_event_with_wmi_path_fires_for_full_path_match(
        self, mock_pythoncom: MagicMock
    ) -> None:
        """A deletion event carrying a matching ``ExecutablePath`` fires for path-only triggers.

        Some WMI implementations populate ``ExecutablePath`` on deletion
        events; when present, it suffices for full-path matching without a
        Win32 fallback.
        """
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\System32\\notepad.exe"])

        def on_callback(_t: BaseTrigger) -> None:
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        event = _make_event(
            "deletion",
            "notepad.exe",
            4321,
            exe_path="C:\\Windows\\System32\\notepad.exe",
        )

        with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
            _run_until_first_callback(trigger, event, mock_wmi_cls)

        assert trigger.last_event is not None
        assert trigger.last_event.event_type == ProcessEventType.STOPPED

    def test_full_path_comparison_is_case_insensitive(self, mock_pythoncom: MagicMock) -> None:
        """Full-path comparison normalizes case so ``C:\\Windows`` matches ``c:\\WINDOWS``."""
        trigger = ProcessTrigger(exe_names=["C:\\Windows\\System32\\notepad.exe"])

        def on_callback(_t: BaseTrigger) -> None:
            trigger.stop_event.set()

        trigger.add_callback(on_callback)

        event = _make_event(
            "creation",
            "notepad.exe",
            4321,
            exe_path="c:\\WINDOWS\\system32\\NOTEPAD.exe",
        )

        with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
            _run_until_first_callback(trigger, event, mock_wmi_cls)

        assert trigger.last_event is not None

    def test_modification_events_do_not_fire_trigger(self, mock_pythoncom: MagicMock) -> None:
        """event_type='modification' is ignored — callback not invoked, last_event unchanged.

        The trailing ``RuntimeError`` is the loop's exit signal in this test
        (genuine WMI errors propagate; the test catches the propagation).
        """
        trigger = ProcessTrigger(exe_names=["notepad.exe"])

        callback_calls: list[BaseTrigger] = []

        def on_callback(t: BaseTrigger) -> None:
            callback_calls.append(t)

        trigger.add_callback(on_callback)

        event = _make_event("modification")

        with patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"):
            with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
                watcher = MagicMock(side_effect=[event, RuntimeError("forced exit")])
                mock_wmi_cls.return_value.watch_for.return_value = watcher

                with pytest.raises(RuntimeError, match="forced exit"):
                    trigger.run()

        assert callback_calls == []
        assert trigger.last_event is None

    def test_timeout_exceptions_continue_loop(self, mock_pythoncom: MagicMock) -> None:
        """wmi.x_wmi_timed_out is swallowed so the loop can re-check stop_event."""
        trigger = ProcessTrigger(exe_names=["notepad.exe"])

        callback_calls: list[BaseTrigger] = []
        trigger.add_callback(lambda t: callback_calls.append(t))

        call_count = {"n": 0}

        def watcher(*_args: object, **_kwargs: object) -> MagicMock:
            """Always time out; after a few calls, request stop so the loop exits."""
            call_count["n"] += 1
            if call_count["n"] >= 3:
                trigger.stop_event.set()
            raise wmi.x_wmi_timed_out()

        with patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"):
            with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
                mock_wmi_cls.return_value.watch_for.return_value = watcher
                trigger.run()

        assert callback_calls == []
        assert call_count["n"] >= 3

    def test_unexpected_exception_propagates(self, mock_pythoncom: MagicMock) -> None:
        """Genuine WMI errors propagate out of run(); COM is still cleaned up."""
        trigger = ProcessTrigger(exe_names=["notepad.exe"])

        with patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"):
            with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
                watcher = MagicMock(side_effect=RuntimeError("WMI failure"))
                mock_wmi_cls.return_value.watch_for.return_value = watcher

                with pytest.raises(RuntimeError, match="WMI failure"):
                    trigger.run()

        # The finally block still runs to release COM.
        mock_pythoncom.CoUninitialize.assert_called_once()

    def test_run_initializes_and_uninitializes_com(self, mock_pythoncom: MagicMock) -> None:
        """COM is initialized once at entry and uninitialized in the finally block."""
        trigger = ProcessTrigger(exe_names=["notepad.exe"])

        with patch.object(trigger, "_build_wql", return_value="SELECT * FROM dummy"):
            with patch("wallpaper_auto.trigger.process_trigger.wmi.WMI") as mock_wmi_cls:
                watcher = MagicMock(side_effect=[wmi.x_wmi_timed_out()])
                mock_wmi_cls.return_value.watch_for.return_value = watcher
                trigger.stop_event.set()

                trigger.run()

        mock_pythoncom.CoInitialize.assert_called_once()
        mock_pythoncom.CoUninitialize.assert_called_once()
