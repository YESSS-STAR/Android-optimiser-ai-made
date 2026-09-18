"""Rollback.

The legacy script had no undo of any kind.  Every change it made — animation
scales, disabled packages, forced Doze, cleared logs — was permanent unless the
user remembered the previous value and restored it by hand.

Because every :class:`Command` carries an explicit ``inverse``, a rollback plan
can be derived mechanically from an execution report: take the commands that
actually succeeded, in reverse order, and run their inverses.

Commands with no inverse (cache trimming, log clearing) are reported as
:attr:`RollbackPlan.irreversible` rather than being silently omitted, so the user
sees exactly what cannot be undone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..core.adb import AdbClient
from .executor import CommandOutcome, ExecutionReport


@dataclass
class RollbackPlan:
    """A concrete plan for undoing a previous run."""

    commands: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    irreversible: list[str] = field(default_factory=list)
    source_profile: str = ""

    @property
    def empty(self) -> bool:
        return not self.commands

    @property
    def coverage(self) -> float:
        """Fraction of the original changes that can be undone."""
        total = len(self.commands) + len(self.irreversible)
        if total == 0:
            return 1.0
        return len(self.commands) / total

    def summary(self) -> dict:
        return {
            "source_profile": self.source_profile,
            "undoable": len(self.commands),
            "irreversible": len(self.irreversible),
            "coverage": round(self.coverage, 4),
        }


@dataclass
class RollbackReport:
    attempted: int = 0
    succeeded: int = 0
    failed: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0
    round_trips: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> dict:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "failed": len(self.failed),
            "round_trips": self.round_trips,
            "wall_seconds": round(self.wall_seconds, 3),
        }


def build_rollback(report: ExecutionReport) -> RollbackPlan:
    """Derive the inverse of everything that succeeded."""
    plan = RollbackPlan(source_profile=report.profile)

    applied: list[CommandOutcome] = report.applied
    for outcome in reversed(applied):
        if outcome.inverse is None:
            plan.irreversible.append(outcome.command)
            continue
        plan.commands.append(outcome.inverse)
        if outcome.target:
            plan.targets.append(outcome.target)

    for outcome in report.failed:
        if outcome.inverse is None and outcome.command not in plan.irreversible:
            plan.irreversible.append(outcome.command)

    return plan


class RollbackExecutor:
    """Applies a :class:`RollbackPlan`."""

    def __init__(self, client: AdbClient, *, chunk_size: int | None = None,
                 workers: int | None = None):
        self.client = client
        self.chunk_size = chunk_size
        self.workers = workers

    def apply(self, plan: RollbackPlan) -> RollbackReport:
        started = time.perf_counter()
        before = self.client.metrics.round_trips

        if plan.empty:
            return RollbackReport(wall_seconds=time.perf_counter() - started)

        batch = self.client.shell_batch_parallel(
            plan.commands, chunk_size=self.chunk_size, workers=self.workers
        )

        failed = [r.command for r in batch.results if not r.ok]
        return RollbackReport(
            attempted=len(batch.results),
            succeeded=len(batch.results) - len(failed),
            failed=failed,
            wall_seconds=time.perf_counter() - started,
            round_trips=self.client.metrics.round_trips - before,
        )
