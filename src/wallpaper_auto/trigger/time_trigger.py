"""
Time-based trigger.

Fires callbacks at configured fixed times of day or at regular intervals,
supporting both one-shot time points and periodic intervals.
"""

import datetime
import logging
import math
import threading

from .base_trigger import BaseThreadTrigger

logger = logging.getLogger(__name__)


class TimeTrigger(BaseThreadTrigger):
    """Trigger that fires callbacks at fixed daily times and/or periodic intervals."""

    def __init__(
        self,
        interval: int | float | None = None,
        times: list[str | datetime.time] | None = None,
    ) -> None:
        """Create a time trigger.

        Args:
            interval: Seconds between periodic triggers.
            times: Daily trigger times, each an ISO time string or time object.
        """
        super().__init__()
        self._lock = threading.Lock()
        self._update_event = threading.Event()
        self.trigger_time: datetime.datetime | None = (
            None  # this attribute shall only be avaliable in callback
        )

        self._fixed_times: list[datetime.time] = []
        self._interval: datetime.timedelta | None = None
        self._reference_time: datetime.datetime | None = None

        if times is not None:
            t = [datetime.time.fromisoformat(t) if isinstance(t, str) else t for t in times]
            self.update_fixed_times(t)

        if interval is not None:
            self.set_interval(datetime.timedelta(seconds=interval))

    def update_fixed_times(self, times: list[datetime.time]) -> None:
        """Set the fixed daily trigger times; call with [] to clear all."""
        with self._lock:
            self._fixed_times = sorted(list(set(times)))
        self._update_event.set()

    def set_interval(
        self,
        interval: datetime.timedelta,
        reference_time: datetime.datetime | None = None,
    ) -> None:
        """Set periodic interval triggering with an optional anchor time.

        Args:
            interval: Delay between triggers.
            reference_time: Anchor for the interval cycle; defaults to now.
        """
        with self._lock:
            self._interval = interval
            self._reference_time = reference_time or datetime.datetime.now()
        self._update_event.set()

    def _log_status(self):
        with self._lock:
            parts = []
            if self._interval is not None and self._reference_time is not None:
                reference = self._reference_time.strftime("%Y-%m-%d %H:%M:%S")
                parts.append(
                    f"interval: {self._interval.total_seconds()}s (reference time {reference})"
                )
            if self._fixed_times is not None:
                points = ", ".join(i.strftime("%H:%M:%S") for i in self._fixed_times)
                parts.append(f"time points: [{points}]")
            content = f"{self.__class__.__name__} schedule - " + "  ".join(parts)
            logger.info(content.strip())

    def clear_interval(self) -> None:
        with self._lock:
            self._interval = None
            self._reference_time = None

    def _get_next_wait_time(self) -> tuple[float, datetime.datetime] | None:
        """Compute the delay until the next scheduled trigger event.

        Collects candidate datetimes for all fixed times and, if set, the next
        periodic interval, picks the earliest, and returns the delay in seconds
        together with the target time.

        Returns:
            A (wait_seconds, target_time) pair, or None if no candidates exist.
        """
        with self._lock:
            now = datetime.datetime.now()
            candidates: list[datetime.datetime] = []
            tolerance = datetime.timedelta(seconds=0.1)

            for t in self._fixed_times:
                target_dt = datetime.datetime.combine(now.date(), t)
                # If today's time point has passed, calculate tomorrow's
                if target_dt <= now + tolerance:
                    target_dt += datetime.timedelta(days=1)
                candidates.append(target_dt)

            if self._interval and self._interval.total_seconds() > 0:
                ref = self._reference_time or now
                diff_seconds = (now - ref).total_seconds()
                interval_seconds = self._interval.total_seconds()

                n = math.ceil(diff_seconds / interval_seconds)
                next_periodic = ref + datetime.timedelta(seconds=max(0, n) * interval_seconds)

                if next_periodic <= now + tolerance:
                    next_periodic += self._interval
                candidates.append(next_periodic)

            if not candidates:
                return None

            next_event = min(candidates)
            return (next_event - now).total_seconds(), next_event

    def start(self) -> None:
        super().start()

    def stop(self) -> None:
        self._update_event.set()
        super().stop()

    def run(self) -> None:
        """
        Main loop: waits for the next scheduled trigger time, then fires.
        Respects both fixed-time and interval scheduling.
        """
        self._log_status()
        while not self.stop_event.is_set():
            self._update_event.clear()
            wait_task = self._get_next_wait_time()
            if wait_task is None:
                self._update_event.wait()
                if self.stop_event.is_set():
                    break
                if self._get_next_wait_time() is not None:
                    self._log_status()
            else:
                wait_second, target_time = wait_task
                interrupted = self._update_event.wait(timeout=wait_second)
                if self.stop_event.is_set():
                    break
                if not interrupted:
                    self.trigger_time = target_time
                    self.trigger()
                    self.trigger_time = None
                else:
                    self._log_status()
