"""Tests for execution: batching, error isolation, and honest reporting."""

from __future__ import annotations

import pytest

from android_optimiser.actions.base import Action, Command
from android_optimiser.analysis.inspector import Inspector
from android_optimiser.domain import Impact, Risk
from android_optimiser.executor.executor import Executor
from android_optimiser.planner.planner import ActionPlan, Planner
from android_optimiser.planner.profiles import HEAVY, LIGHT, Profile


def simple_plan(commands: list[str], *, inverses: bool = True) -> ActionPlan:
    """Build a one-action plan.

    The target is the command's last token, mirroring how the real action
    builders record the package or setting key a command acts on.
    """
    action = Action(
        id="test",
        title="test action",
        category="test",
        risk=Risk.SAFE,
        impact=Impact.NONE,
        commands=[
            Command(
                shell=c,
                label=c,
                inverse=f"undo {c}" if inverses else None,
                target=c.split()[-1],
            )
            for c in commands
        ],
    )
    return ActionPlan(profile=LIGHT, actions=[action])


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------
def test_dry_run_touches_nothing(client, fake):
    fake.calls.clear()
    report = Executor(client).dry_run(simple_plan(["pm trim-caches 1G"]))
    assert fake.calls == []
    assert report.dry_run
    assert len(report.outcomes) == 1
    assert report.applied


def test_dry_run_reports_the_same_command_count(client, fake):
    plan = simple_plan(["a", "b", "c"])
    assert len(Executor(client).dry_run(plan).outcomes) == 3


# --------------------------------------------------------------------------
# batching
# --------------------------------------------------------------------------
def test_execution_batches_into_fewer_round_trips(client, fake):
    plan = simple_plan([f"getprop ro.product.model" for _ in range(20)])
    fake.calls.clear()
    Executor(client, chunk_size=8, workers=1, retry_failures=False).execute(plan)
    assert len(fake.calls) == 3, "20 commands at chunk size 8 => 3 round-trips"


def test_execution_parallel_reduces_chunk_serialisation(client, fake):
    plan = simple_plan(["getprop ro.product.model" for _ in range(32)])
    fake.calls.clear()
    Executor(client, chunk_size=8, workers=4, retry_failures=False).execute(plan)
    assert len(fake.calls) == 4


def test_outcomes_are_attributed_to_the_right_command(client):
    plan = simple_plan(["getprop ro.product.model", "getprop ro.product.brand"])
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)
    assert report.outcomes[0].output.strip() == "SM-G998B"
    assert report.outcomes[1].output.strip() == "samsung"


# --------------------------------------------------------------------------
# error isolation
# --------------------------------------------------------------------------
def test_one_failure_does_not_abort_the_rest(client, fake):
    """A per-command failure must not poison its batch-mates.

    Disabling a package that does not exist makes ``pm`` return non-zero for
    that command only; the sentinel markers must attribute it correctly.
    """
    plan = simple_plan(
        [
            "pm disable-user --user 0 com.instagram.android",
            "pm disable-user --user 0 com.does.not.exist",
            "pm disable-user --user 0 com.spotify.music",
        ]
    )
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)

    assert len(report.failed) == 1
    assert len(report.applied) == 2
    assert report.failed[0].target == "com.does.not.exist"
    # the two good ones really were applied
    assert not fake.is_enabled("com.instagram.android")
    assert not fake.is_enabled("com.spotify.music")


def test_batch_mates_are_not_lost_when_one_command_fails(client, fake):
    plan = simple_plan(
        [
            "pm disable-user --user 0 com.booking",
            "pm disable-user --user 0 com.nope.nothing",
            "pm disable-user --user 0 com.ebay.mobile",
            "pm disable-user --user 0 com.paypal.android.p2pmobile",
        ]
    )
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)
    assert report.success_rate == 0.75


def test_failures_are_reported_not_swallowed(client, fake):
    """The legacy script printed stderr under a success label and moved on."""
    fake.fail_patterns.append("pm disable-user --user 0 com.facebook.katana")
    plan = simple_plan(["pm disable-user --user 0 com.facebook.katana"])
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)

    assert not report.all_succeeded
    assert report.failed[0].rc != 0
    assert report.failed[0].error
    assert report.success_rate == 0.0


def test_retry_recovers_a_transient_batch_failure(client, fake, monkeypatch):
    """A command that fails inside a batch but succeeds alone is retried.

    Models a transport-level failure that takes down the whole batched
    invocation, where the individual commands were fine all along.
    """
    from android_optimiser.core.transport import CommandResult

    plan = simple_plan(
        [
            "pm disable-user --user 0 com.facebook.katana",
            "pm disable-user --user 0 com.instagram.android",
        ]
    )

    real_run = fake.run
    attempts = {"batches": 0, "singles": 0}

    def flaky(argv, timeout=None):
        joined = " ".join(argv)
        if "###AO_" in joined:
            attempts["batches"] += 1
            return CommandResult(
                argv=tuple(argv), rc=1, stdout="", stderr="transport error", duration=0.0
            )
        attempts["singles"] += 1
        return real_run(argv, timeout)

    monkeypatch.setattr(fake, "run", flaky)
    report = Executor(client, chunk_size=8, retry_failures=True).execute(plan)

    assert attempts["batches"] > 0
    assert attempts["singles"] == 2, "each command should be retried individually"
    assert report.all_succeeded
    assert all(o.retried for o in report.outcomes)


def test_retry_disabled_leaves_failures_visible(client, fake, monkeypatch):
    from android_optimiser.core.transport import CommandResult

    plan = simple_plan(["pm disable-user --user 0 com.facebook.katana"])

    monkeypatch.setattr(
        fake,
        "run",
        lambda argv, timeout=None: CommandResult(
            argv=tuple(argv), rc=1, stdout="", stderr="transport error", duration=0.0
        ),
    )
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)
    assert not report.all_succeeded


# --------------------------------------------------------------------------
# end to end through a real plan
# --------------------------------------------------------------------------
def test_heavy_plan_executes_and_reports_metrics(client):
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, HEAVY)
    report = Executor(client, chunk_size=8, workers=4, retry_failures=False).execute(plan)

    assert report.all_succeeded
    assert len(report.outcomes) == len(plan.commands)
    assert report.metrics["round_trips"] < report.metrics["device_commands"]
    assert report.metrics["batching_saving"] > 0


def test_execution_actually_changes_the_device(client, fake):
    plan = simple_plan(["pm disable-user --user 0 com.facebook.katana"])
    Executor(client, retry_failures=False).execute(plan)
    assert not fake.is_enabled("com.facebook.katana")


def test_empty_plan_executes_without_touching_the_device(client, fake):
    fake.calls.clear()
    report = Executor(client).execute(ActionPlan(profile=LIGHT, actions=[]))
    assert fake.calls == []
    assert report.outcomes == []
    assert report.all_succeeded


def test_progress_callback_is_invoked(client):
    seen: list[tuple[int, int]] = []
    plan = simple_plan(["getprop ro.product.model"] * 3)
    Executor(
        client,
        chunk_size=8,
        retry_failures=False,
        on_progress=lambda done, total, label: seen.append((done, total)),
    ).execute(plan)
    assert seen == [(1, 3), (2, 3), (3, 3)]
