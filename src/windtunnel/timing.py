"""Laufzeit- und Restzeitschaetzung fuer die Simulation."""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta
from typing import Callable


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    s = max(0, int(round(seconds)))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class RunTimer:
    """Misst die Gesamtlaufzeit und schaetzt die Restzeit aus der Iterationsgeschwindigkeit."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, window: int = 60):
        self._clock = clock
        self._start: float | None = None
        self._end: float | None = None
        self._samples: deque[tuple[float, int]] = deque(maxlen=window)
        self.iteration = 0
        self.total = 0

    def start(self) -> None:
        self._start, self._end = self._clock(), None
        self._samples.clear()
        self.iteration = self.total = 0

    def stop(self) -> None:
        if self._start is not None and self._end is None:
            self._end = self._clock()

    @property
    def running(self) -> bool:
        return self._start is not None and self._end is None

    @property
    def elapsed(self) -> float:
        if self._start is None:
            return 0.0
        return (self._end if self._end is not None else self._clock()) - self._start

    def update_iteration(self, iteration: int, total: int) -> None:
        self.iteration, self.total = iteration, total
        self._samples.append((self._clock(), iteration))

    def seconds_per_iteration(self) -> float | None:
        if len(self._samples) < 5:
            return None
        (t0, i0), (t1, i1) = self._samples[0], self._samples[-1]
        if i1 <= i0 or t1 <= t0:
            return None
        return (t1 - t0) / (i1 - i0)

    def eta(self) -> float | None:
        """Maximale Restzeit bis zur letzten Iteration (Konvergenz kann frueher beenden)."""
        rate = self.seconds_per_iteration()
        if rate is None or self.total <= 0:
            return None
        return rate * max(self.total - self.iteration, 0)

    def finish_time(self) -> datetime | None:
        eta = self.eta()
        return None if eta is None else datetime.now() + timedelta(seconds=eta)
