"""Tests for time_trigger.py — interval and fixed-time scheduling."""

import threading
from datetime import datetime as dt
from datetime import time, timedelta
from unittest.mock import patch

import pytest

from wallpaper_auto.trigger.time_trigger import TimeTrigger


@pytest.fixture
def trigger():
    return TimeTrigger()


@pytest.fixture
def freeze_now():
    with patch("wallpaper_auto.trigger.time_trigger.datetime") as mock_dt:
        mock_dt.timedelta = timedelta
        mock_dt.time = time
        mock_dt.datetime.combine = dt.combine
        yield mock_dt


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

    @pytest.mark.parametrize(
        "now,fixed_times,interval,ref_time,wait_sec,target",
        [
            pytest.param(
                dt(2024, 1, 1, 10, 0, 0),
                [time(11, 0)],
                None,
                None,
                3600,
                dt(2024, 1, 1, 11, 0),
                id="fixed_time_future",
            ),
            pytest.param(
                dt(2024, 1, 1, 10, 0, 0),
                [time(9, 0)],
                None,
                None,
                82800,
                dt(2024, 1, 2, 9, 0),
                id="fixed_time_past",
            ),
            pytest.param(
                dt(2024, 1, 1, 10, 0, 0),
                [time(13, 0), time(10, 30)],
                None,
                None,
                1800,
                dt(2024, 1, 1, 10, 30),
                id="multiple_fixed_returns_min",
            ),
            pytest.param(
                dt(2024, 1, 1, 10, 5, 0),
                [],
                timedelta(minutes=15),
                dt(2024, 1, 1, 10, 0, 0),
                600,
                dt(2024, 1, 1, 10, 15),
                id="interval_calculation",
            ),
            pytest.param(
                dt(2024, 1, 1, 10, 0, 0),
                [],
                timedelta(hours=1),
                None,
                3600,
                dt(2024, 1, 1, 11, 0),
                id="interval_without_reference",
            ),
            pytest.param(
                dt(2024, 1, 1, 9, 50, 0),
                [],
                timedelta(minutes=15),
                dt(2024, 1, 1, 10, 0, 0),
                600,
                dt(2024, 1, 1, 10, 0),
                id="interval_now_before_reference",
            ),
            pytest.param(
                dt(2024, 1, 1, 10, 0, 0),
                [time(10, 30)],
                timedelta(hours=2),
                dt(2024, 1, 1, 9, 0, 0),
                1800,
                dt(2024, 1, 1, 10, 30),
                id="both_fixed_and_interval_returns_min",
            ),
        ],
    )
    def test_get_next_wait_time(
        self, freeze_now, now, fixed_times, interval, ref_time, wait_sec, target
    ):
        freeze_now.datetime.now.return_value = now
        trigger = TimeTrigger()
        if fixed_times:
            trigger.update_fixed_times(fixed_times)
        if interval:
            trigger._interval = interval
            trigger._reference_time = ref_time
        next_target = trigger._get_next_wait_time()
        assert next_target is not None
        assert next_target[0] == pytest.approx(wait_sec, abs=0.1)
        assert next_target[1] == target

    def test_zero_interval_excluded(self, freeze_now):
        freeze_now.datetime.now.return_value = dt(2024, 1, 1, 10, 0, 0)
        trigger = TimeTrigger()
        trigger._interval = timedelta(seconds=0)
        trigger._reference_time = dt(2024, 1, 1, 10, 0, 0)
        assert trigger._get_next_wait_time() is None


class TestLifecycle:
    def test_lifecycle_start_stop(self):
        trigger = TimeTrigger()
        trigger.start()
        assert trigger._thread is not None
        assert trigger._thread.is_alive()

        trigger.stop()
        assert trigger.stop_event.is_set()
        assert trigger._update_event.is_set()
        assert trigger._thread is None


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
        target = dt(2024, 1, 1, 10, 0, 0)
        with patch.object(trigger, "_get_next_wait_time", return_value=(0.02, target)):
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
        target = dt(2024, 1, 1, 10, 0, 10)
        with patch.object(trigger, "_get_next_wait_time", return_value=(10, target)):
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
