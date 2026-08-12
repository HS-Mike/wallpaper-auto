"""Tests for trigger/base_trigger.py — base trigger interface and thread lifecycle."""

import logging

from wallpaper_auto.trigger.base_trigger import BaseThreadTrigger, BaseTrigger


class _StubThreadTrigger(BaseThreadTrigger):
    """Minimal concrete subclass: run loop waits on the stop_event."""

    def run(self) -> None:
        self.stop_event.wait()


class TestBaseTrigger:
    """Tests for the callback-only ``BaseTrigger`` interface."""

    def test_trigger_invokes_registered_callback(self) -> None:
        trigger = BaseTrigger()
        received = []

        trigger.add_callback(lambda t: received.append(t))
        trigger.trigger()

        assert received == [trigger]

    def test_start_and_stop_are_noops(self) -> None:
        trigger = BaseTrigger()

        trigger.start()
        trigger.stop()

    def test_getattribute_logs_lifecycle(self, caplog) -> None:
        trigger = BaseTrigger()

        with caplog.at_level(logging.DEBUG, logger="wallpaper_auto.trigger.base_trigger"):
            trigger.start
            trigger.stop
            trigger.trigger  # non-lifecycle attribute falls through

        assert "lifecycle start called" in caplog.text
        assert "lifecycle stop called" in caplog.text


class TestBaseThreadTrigger:
    """Tests for the background-thread ``BaseThreadTrigger`` lifecycle."""

    def test_initial_state(self) -> None:
        trigger = _StubThreadTrigger()

        assert trigger._thread is None
        assert not trigger.stop_event.is_set()

    def test_start_launches_daemon_thread(self) -> None:
        trigger = _StubThreadTrigger()

        trigger.start()

        assert trigger._thread is not None
        assert trigger._thread.is_alive()
        assert trigger._thread.daemon
        assert not trigger.stop_event.is_set()

        trigger.stop()

    def test_stop_signals_and_joins(self) -> None:
        trigger = _StubThreadTrigger()
        trigger.start()

        trigger.stop()

        assert trigger.stop_event.is_set()
        assert trigger._thread is None

    def test_stop_without_start(self) -> None:
        trigger = _StubThreadTrigger()

        trigger.stop()

        assert trigger.stop_event.is_set()
        assert trigger._thread is None

    def test_request_stop_sets_event(self) -> None:
        trigger = _StubThreadTrigger()

        trigger._request_stop()

        assert trigger.stop_event.is_set()
