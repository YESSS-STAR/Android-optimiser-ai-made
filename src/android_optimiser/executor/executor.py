"""Execution.

Runs an :class:`ActionPlan` and reports precisely what happened, per command.

The legacy script's execution loop was:

    res = run_cmd(f"adb shell pm disable-user --user 0 {pkg}")
    print(f"    -> Disabled {pkg}: {res}")

``run_cmd`` returned ``e.stderr`` on failure and the caller printed it as if it
were a success message.  A run in which every single command failed reported
"Optimization sequence completed successfully!".

This executor instead:

* flattens the plan into device commands and dispatches them through the
  batching/parallel transport, so N changes cost far fewer round-trips;
* records an explicit outcome per command, including the exit code;
* isolates failures — one bad package does not abort the run;
* re-runs failed commands individually once, because a command that fails
  inside a batch may succeed on its own (and vice versa) and the distinction
  matters when reporting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ..actions.base import Action, Command
from ..core.adb import AdbClient
from ..core.metrics import Metrics
from ..planner.planner import ActionPlan

ProgressFn = Callable[[int, int, str], None]


@dataclass
class CommandOutcome:
    """What happened to one command."""

    action_id: str
    category: str
    command: str
    label: str
    target: str
    inverse: str | None
    rc: int
    ok: bool
    output: str = ""
    error: str = ""
    batched: bool = False
    retried: bool = False

    @property
    def applied(self) -> bool:
        return self.ok

    def as_dict(self) -> dict:
        return {
            "action": self.action_id,
            "category": self.category,
            "command": self.command,
            "target": self.target,
            "rc": self.rc,
            "ok": self.ok,
            "batched": self.batched,
            "retried": self.retried,
            "error": self.error,
        }


@dataclass
class ExecutionReport:
    """The full record of a run."""

    profile: str
    dry_run: bool = False
    outcomes: list[CommandOutcome] = field(default_factory=list)
    wall_seconds: float = 0.0
    metrics: dict = field(default_factory=dict)
    planned_actions: int = 0

    @property
    def applied(self) -> list[CommandOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[CommandOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def success_rate(self) -> float:
        if not self.outcomes:
            return 1.0
        return len(self.applied) / len(self.outcomes)

    @property
    def all_succeeded(self) -> bool:
        return not self.failed

    def summary(self) -> dict:
        return {
            "profile": self.profile,
            "dry_run": self.dry_run,
            "planned_actions": self.planned_actions,
            "commands": len(self.outcomes),
            "applied": len(self.applied),
            "failed": len(self.failed),
            "success_rate": round(self.success_rate, 4),
            "wall_seconds": round(self.wall_seconds, 3),
            "round_trips": self.metrics.get("round_trips", 0),
            "device_commands": self.metrics.get("device_commands", 0),
            "batching_efficiency": self.metrics.get("batching_efficiency", 0.0),
        }


class Executor:
    """Applies plans to a device."""

    def __init__(
        self,
        client: AdbClient,
        *,
        chunk_size: int | None = None,
        workers: int | None = None,
        retry_failures: bool = True,
        on_progress: ProgressFn | None = None,
    ):
        self.client = client
        self.chunk_size = chunk_size
        self.workers = workers
        self.retry_failures = retry_failures
        self.on_progress = on_progress

    # ------------------------------------------------------------------
    def dry_run(self, plan: ActionPlan) -> ExecutionReport:
        """Report what would happen without touching the device."""
        outcomes = [
            CommandOutcome(
                action_id=action.id,
                category=action.category,
                command=command.shell,
                label=command.label,
                target=command.target,
                inverse=command.inverse,
                rc=0,
                ok=True,
            )
            for action in plan.actions
            for command in action.commands
        ]
        return ExecutionReport(
            profile=plan.profile.key,
            dry_run=True,
            outcomes=outcomes,
            metrics=Metrics().summary(),
            planned_actions=len(plan.actions),
        )

    # ------------------------------------------------------------------
    def execute(self, plan: ActionPlan) -> ExecutionReport:
        started = time.perf_counter()

        flattened: list[tuple[Action, Command]] = [
            (action, command)
            for action in plan.actions
            for command in action.commands
        ]
        if not flattened:
            return ExecutionReport(
                profile=plan.profile.key,
                outcomes=[],
                wall_seconds=time.perf_counter() - started,
                metrics=self.client.metrics.summary(),
                planned_actions=0,
            )

        commands = [command.shell for _, command in flattened]
        batch = self.client.shell_batch_parallel(
            commands, chunk_size=self.chunk_size, workers=self.workers
        )

        outcomes: list[CommandOutcome] = []
        for index, (action, command) in enumerate(flattened):
            result = batch.results[index]
            outcomes.append(
                CommandOutcome(
                    action_id=action.id,
                    category=action.category,
                    command=command.shell,
                    label=command.label,
                    target=command.target,
                    inverse=command.inverse,
                    rc=result.rc,
                    ok=result.ok,
                    output=result.stdout,
                    error=result.stderr,
                    batched=result.shared_round_trip,
                )
            )
            if self.on_progress:
                self.on_progress(index + 1, len(flattened), command.label)

        if self.retry_failures:
            self._retry_failures(outcomes)

        wall = time.perf_counter() - started
        return ExecutionReport(
            profile=plan.profile.key,
            outcomes=outcomes,
            wall_seconds=wall,
            metrics=self.client.metrics.summary(),
            planned_actions=len(plan.actions),
        )

    # ------------------------------------------------------------------
    def _retry_failures(self, outcomes: list[CommandOutcome]) -> None:
        """Re-run failed commands on their own, once.

        A command can fail inside a batch for reasons unrelated to the command
        itself.  Retrying individually separates "this genuinely failed" from
        "the batch was disturbed", which changes what the report should say.
        """
        for outcome in outcomes:
            if outcome.ok:
                continue
            retry = self.client.shell(outcome.command)
            outcome.retried = True
            if retry.ok:
                outcome.rc = 0
                outcome.ok = True
                outcome.output = retry.stdout
                outcome.error = ""
            else:
                outcome.rc = retry.rc
                outcome.error = retry.stderr or outcome.error
