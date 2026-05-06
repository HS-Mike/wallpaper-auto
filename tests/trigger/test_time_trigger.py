import threading
from datetime import time, timedelta, datetime as dt
from unittest.mock import patch

import pytest

from wallpaper_automator.trigger.time_trigger import TimeTrigger


@pytest.fixture
def trigger():
    return TimeTrigger()


@pytest.fixture
def freeze_now():
    with patch("wallpaper_automator.trigger.time_trigger.datetime") as mock_dt:
        mock_dt.timedelta = timedelta
        mock_dt.time = time
        mock_dt.datetime.combine = dt.combine
        yield mock_dt


class TestTimeTriggerInit:
    def test_initial_state(self, trigger):
        assert trigger._fixed_times == []
        assert trigger._interval is None
        assert trigger._reference_time is None
        assert hasattr(trigger._lock, "acquire")
        assert isinstance(trigger._update_event, threading.Event)


class TestTimeTriggerInitWithConfig:
    def test_init_with_interval(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger(interval=60)
        assert trigger._interval == timedelta(seconds=60)
        assert trigger._reference_time == dt(2024, 1, 1, 10, 0, 0)

    def test_init_with_times(self):
        trigger = TimeTrigger(times=["09:00", "17:30"])
        assert trigger._fixed_times == [time(9, 0), time(17, 30)]

    def test_init_with_both_params(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger(interval=120, times=["12:00"])
        assert trigger._interval == timedelta(seconds=120)
        assert trigger._fixed_times == [time(12, 0)]

    def test_init_with_invalid_time_format(self):
        with pytest.raises(ValueError):
            TimeTrigger(times=["bad"])


class TestTimeTriggerFixedTimes:
    def test_add_fixed_times_sorted_deduplicated(self, trigger):
        trigger.update_fixed_times([time(10, 0), time(8, 0), time(10, 0)])
        assert trigger._fixed_times == [time(8, 0), time(10, 0)]

    def test_add_fixed_times_sets_update_event(self, trigger):
        trigger._update_event.clear()
        trigger.update_fixed_times([time(10, 0)])
        assert trigger._update_event.is_set()

    def test_clear_fixed_times(self, trigger):
        trigger.update_fixed_times([time(10, 0)])
        trigger._update_event.clear()
        trigger.update_fixed_times([])
        assert trigger._fixed_times == []
        assert trigger._update_event.is_set()


class TestTimeTriggerInterval:
    def test_set_interval_without_reference(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger.set_interval(timedelta(hours=1))
        assert trigger._interval == timedelta(hours=1)
        assert trigger._reference_time == dt(2024, 1, 1, 10, 0, 0)

    def test_set_interval_sets_update_event(self, trigger):
        trigger._update_event.clear()
        trigger.set_interval(timedelta(minutes=30), reference_time=dt(2024, 1, 1, 8, 0, 0))
        assert trigger._update_event.is_set()

    def test_set_interval_with_reference(self, trigger):
        ref = dt(2024, 1, 1, 8, 0, 0)
        trigger.set_interval(timedelta(minutes=30), reference_time=ref)
        assert trigger._interval == timedelta(minutes=30)
        assert trigger._reference_time == ref

    def test_clear_interval(self, trigger):
        trigger.set_interval(timedelta(hours=1))
        trigger.clear_interval()
        assert trigger._interval is None
        assert trigger._reference_time is None


class TestGetNextWaitTime:
    def test_no_times_no_interval_returns_none(self, trigger):
        assert trigger._get_next_wait_time() is None

    def test_fixed_time_today_future(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger.update_fixed_times([time(11, 0)])
        assert trigger._get_next_wait_time() == pytest.approx(3600, abs=0.1)

    def test_fixed_time_today_past(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger.update_fixed_times([time(9, 0)])
        assert trigger._get_next_wait_time() == pytest.approx(82800, abs=0.1)

    def test_multiple_fixed_times_returns_min(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger.update_fixed_times([time(13, 0), time(10, 30)])
        assert trigger._get_next_wait_time() == pytest.approx(1800, abs=0.1)

    def test_interval_calculation(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 5, 0)
        trigger = TimeTrigger()
        trigger._interval = timedelta(minutes=15)
        trigger._reference_time = dt(2024, 1, 1, 10, 0, 0)
        assert trigger._get_next_wait_time() == pytest.approx(600, abs=0.1)

    def test_interval_without_reference_uses_now(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger._interval = timedelta(hours=1)
        trigger._reference_time = None
        assert trigger._get_next_wait_time() == pytest.approx(3600, abs=0.1)

    def test_interval_when_now_before_reference(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 9, 50, 0)
        trigger = TimeTrigger()
        trigger._interval = timedelta(minutes=15)
        trigger._reference_time = dt(2024, 1, 1, 10, 0, 0)
        assert trigger._get_next_wait_time() == pytest.approx(600, abs=0.1)

    def test_zero_interval_excluded(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger._interval = timedelta(seconds=0)
        trigger._reference_time = dt(2024, 1, 1, 10, 0, 0)
        assert trigger._get_next_wait_time() is None

    def test_both_fixed_and_interval_returns_min(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger.set_interval(timedelta(hours=2), reference_time=dt(2024, 1, 1, 9, 0, 0))
        trigger.update_fixed_times([time(10, 30)])
        assert trigger._get_next_wait_time() == pytest.approx(1800, abs=0.1)


class TestActivateDeactivate:
    def test_activate_starts_thread(self):
        trigger = TimeTrigger()
        with patch("threading.Thread.start") as mock_start:
            trigger.activate()
            mock_start.assert_called_once()

    def test_deactivate_sets_stop_and_update_events(self):
        trigger = TimeTrigger()
        trigger.start()
        trigger.deactivate()
        assert trigger._stop_event.is_set()
        assert trigger._update_event.is_set()


class TestBaseThreadTriggerDeactivate:
    """Tests for BaseThreadTrigger.deactivate() (not overridden by subclasses)."""

    def test_deactivate_stops_thread_and_joins(self):
        """BaseThreadTrigger.deactivate() stops thread and joins."""
        from wallpaper_automator.trigger.base_trigger import BaseThreadTrigger

        class _MinimalTrigger(BaseThreadTrigger):
            def run(self):
                while not self._stop_event.is_set():
                    threading.Event().wait(0.05)

        trigger = _MinimalTrigger()
        trigger.start()
        assert trigger.is_alive()

        trigger.deactivate()
        assert trigger._stop_event.is_set()
        assert not trigger.is_alive()


class TestRunLoop:
    def test_run_exits_immediately_when_already_stopped(self):
        """Thread exits on first iteration when already stopped."""
        trigger = TimeTrigger()
        trigger._request_stop()
        trigger._update_event.set()

        t = threading.Thread(target=trigger.run, daemon=True)
        t.start()
        t.join(timeout=0.5)
        assert not t.is_alive()

    def test_run_calls_trigger_on_timeout(self):
        """Natural timeout -> trigger() invoked."""
        trigger = TimeTrigger()
        with patch.object(trigger, "_get_next_wait_time", return_value=0.02):
            with patch.object(trigger, "trigger") as mock_trigger:
                t = threading.Thread(target=trigger.run, daemon=True)
                t.start()
                threading.Event().wait(0.15)
                trigger._request_stop()
                trigger._update_event.set()
                t.join(timeout=0.5)
                mock_trigger.assert_called()

    def test_run_skips_trigger_on_interrupt(self):
        """Event set before timeout -> trigger() NOT called."""
        trigger = TimeTrigger()
        with patch.object(trigger, "_get_next_wait_time", return_value=10):
            with patch.object(trigger, "trigger") as mock_trigger:
                t = threading.Thread(target=trigger.run, daemon=True)
                t.start()
                threading.Event().wait(0.1)
                trigger._update_event.set()
                threading.Event().wait(0.1)
                trigger._request_stop()
                trigger._update_event.set()
                t.join(timeout=0.5)
                mock_trigger.assert_not_called()

    def test_run_waits_indefinitely_when_no_next_time(self):
        """None wait -> blocks on _update_event -> interrupt exits loop."""
        trigger = TimeTrigger()
        with patch.object(trigger, "_get_next_wait_time", return_value=None):
            with patch.object(trigger, "trigger") as mock_trigger:
                t = threading.Thread(target=trigger.run, daemon=True)
                t.start()
                threading.Event().wait(0.1)
                trigger._request_stop()
                trigger._update_event.set()
                t.join(timeout=0.5)
                assert not t.is_alive()
                mock_trigger.assert_not_called()
