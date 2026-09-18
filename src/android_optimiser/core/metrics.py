"""Instrumentation.

The legacy script had no way to answer "how much work did that actually do?".
This collector answers it precisely: it counts every process invocation (i.e.
every device round-trip), every device-side command carried inside those
invocations, cache hits, retries and failures.

Round-trip count is the metric that matters.  It is exact, it is independent of
the host machine's process-spawn cost, and on real hardware it is what the user
actually waits for.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Invocation:
    """One ``adb`` process launch."""

    argv: list[str]
    duration: float
    ok: bool
    batch_size: int = 1
    label: str = ""

    @property
    def command(self) -> str:
        return " ".join(self.argv)


@dataclass
class Metrics:
    """Thread-safe counters for a single optimisation run."""

    invocations: list[Invocation] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    retries: int = 0
    failures: int = 0
    timeouts: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- recording -------------------------------------------------------
    def record(self, invocation: Invocation) -> None:
        with self._lock:
            self.invocations.append(invocation)
            if not invocation.ok:
                self.failures += 1

    def record_cache(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1

    def record_timeout(self) -> None:
        with self._lock:
            self.timeouts += 1

    def reset(self) -> None:
        with self._lock:
            self.invocations.clear()
            self.cache_hits = 0
            self.cache_misses = 0
            self.retries = 0
            self.failures = 0
            self.timeouts = 0

    # -- derived ---------------------------------------------------------
    @property
    def round_trips(self) -> int:
        """Number of adb process launches = number of device round-trips."""
        return len(self.invocations)

    @property
    def device_commands(self) -> int:
        """Number of device-side commands, counting those inside batches."""
        return sum(i.batch_size for i in self.invocations)

    @property
    def batched_invocations(self) -> int:
        return sum(1 for i in self.invocations if i.batch_size > 1)

    @property
    def batching_saving(self) -> int:
        """Round-trips avoided by packing commands into batches."""
        return self.device_commands - self.round_trips

    @property
    def total_seconds(self) -> float:
        """Sum of measured invocation durations (excludes parallel overlap)."""
        return sum(i.duration for i in self.invocations)

    @property
    def batching_efficiency(self) -> float:
        """Fraction of device commands that did NOT cost a round-trip."""
        if not self.device_commands:
            return 0.0
        return self.batching_saving / self.device_commands

    def summary(self) -> dict[str, Any]:
        return {
            "round_trips": self.round_trips,
            "device_commands": self.device_commands,
            "batched_invocations": self.batched_invocations,
            "batching_saving": self.batching_saving,
            "batching_efficiency": round(self.batching_efficiency, 4),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "retries": self.retries,
            "failures": self.failures,
            "timeouts": self.timeouts,
            "total_command_seconds": round(self.total_seconds, 4),
        }
