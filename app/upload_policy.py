"""Upload deadline and sustained slow-transfer policy."""

import time
from collections.abc import Callable


class UploadPolicy:
    """Reject a stalled or slow upload using actual cumulative bytes."""

    def __init__(
        self,
        total_size: int,
        clock: Callable[[], float] = time.monotonic,
        max_seconds: float = 3600,
        grace: float = 120,
        slow_seconds: float = 60,
    ):
        if total_size <= 0 or max_seconds <= 0 or grace < 0 or slow_seconds < 0:
            raise ValueError("Upload size and timing limits must be valid")
        self.total_size = total_size
        self.max_seconds = max_seconds
        self.grace = grace
        self.slow_seconds = slow_seconds
        self._clock = clock
        self._started_at = clock()
        self._slow_since: float | None = None
        self._received_bytes = 0

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    @property
    def remaining_seconds(self) -> float:
        """Time until the absolute deadline, independent of progress."""
        return max(0.0, self.max_seconds - self.elapsed)

    def note_received(self, cumulative_bytes: int) -> None:
        """Check progress; may also be called with unchanged bytes on a timer.

        Slow means the whole-file duration projected from measured average
        throughput exceeds the deadline. It must persist after the grace period.
        """
        if cumulative_bytes < self._received_bytes or cumulative_bytes < 0:
            raise ValueError("Upload received byte count cannot decrease")
        if cumulative_bytes > self.total_size:
            raise ValueError("Upload exceeded the declared file size")
        self._received_bytes = cumulative_bytes
        elapsed = self.elapsed
        if elapsed >= self.max_seconds:
            raise ValueError("Upload exceeded the maximum duration")
        if elapsed < self.grace:
            return
        projected = (
            self.total_size * elapsed / cumulative_bytes
            if cumulative_bytes
            else float("inf")
        )
        if projected <= self.max_seconds:
            self._slow_since = None
            return
        if self._slow_since is None:
            self._slow_since = elapsed
        if elapsed - self._slow_since >= self.slow_seconds:
            raise ValueError("Upload is too slow to finish within the time limit")
