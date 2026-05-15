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
    def __init__(
        self,
        interval: int | float | None = None,
        times: list[str | datetime.time] | None = None,
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._update_event = threading.Event()

        self._fixed_times: list[datetime.time] = []
        self._interval: datetime.timedelta | None = None
        self._reference_time: datetime.datetime | None = None

        if times is not None:
            t = [datetime.time.fromisoformat(t) if isinstance(t, str) else t for t in times]
            self.update_fixed_times(t)

        if interval is not None:
            self.set_interval(datetime.timedelta(seconds=interval))

    def update_fixed_times(self, times: list[datetime.time]) -> None:
        """update fixed daily trigger time points. Call with [] to clear all."""
        with self._lock:
            # Merge and deduplicate, keeping order
            self._fixed_times = sorted(list(set(times)))
        logger.info(f"time trigger fixed times update: {self._fixed_times}")
        self._update_event.set()

    def set_interval(
        self,
        interval: datetime.timedelta,
        reference_time: datetime.datetime | None = None,
    ) -> None:
        """
        Set fixed interval triggering with reference time point.
        If reference_time is not provided, use current time reference_time
        """
        with self._lock:
            self._interval = interval
            self._reference_time = reference_time or datetime.datetime.now()
            ref_time = self._reference_time
        logger.info(
            "time trigger interval set to %s starting from %s",
            interval,
            ref_time.strftime("%y-%m-%d %H:%M:%S"),
        )
        self._update_event.set()

    def clear_interval(self) -> None:
        """Clear set interval"""
        with self._lock:
            self._interval = None
            self._reference_time = None
        logger.info("time trigger interval clear")

    def _get_next_wait_time(self) -> float | None:
        """
        Compute seconds until the next trigger event.

        Collects candidate datetimes for all fixed times and (if set) the next
        periodic interval, picks the earliest, and returns the delta in seconds.
        Returns None if no candidates exist.
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
            return (next_event - now).total_seconds()

    def activate(self) -> None:
        super().activate()
        logger.debug(f"{self.__class__.__name__} activate")

    def deactivate(self) -> None:
        self._request_stop()
        self._update_event.set()
        super().join(timeout=3)
        logger.debug(f"{self.__class__.__name__} deactivate")

    def run(self) -> None:
        """
        Main loop: waits for the next scheduled trigger time, then fires.
        Respects both fixed-time and interval scheduling.
        """
        while not self._stop_event.is_set():
            self._update_event.clear()
            wait_time = self._get_next_wait_time()
            if wait_time is None:
                self._update_event.wait()
            else:
                interrupted = self._update_event.wait(timeout=wait_time)
                if self._stop_event.is_set():
                    break
                if not interrupted:
                    self.trigger()
