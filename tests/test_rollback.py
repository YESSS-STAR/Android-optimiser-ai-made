"""Tests for rollback: correctness of the inverses, and coverage accounting."""

from __future__ import annotations

import pytest

from android_optimiser.analysis.inspector import Inspector
from android_optimiser.executor.executor import Executor
from android_optimiser.executor.rollback import RollbackExecutor, build_rollback
from android_optimiser.planner.planner import Planner
from android_optimiser.planner.profiles import HEAVY, LIGHT


@pytest.fixture()
def executed(client):
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, HEAVY)
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)
    return snapshot, plan, report


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------
def test_rollback_covers_every_reversible_change(executed):
    _, _, report = executed
    plan = build_rollback(report)
    reversible = [o for o in report.applied if o.inverse is not None]
    assert len(plan.commands) == len(reversible)


def test_rollback_commands_are_the_inverses(executed):
    _, _, report = executed
    plan = build_rollback(report)
    expected = [o.inverse for o in reversed(report.applied) if o.inverse is not None]
    assert plan.commands == expected


def test_rollback_is_ordered_in_reverse(executed):
    _, plan, report = executed
    rollback = build_rollback(report)
    # the last applied animation change should be undone first
    assert rollback.commands[-1] == "settings put global window_animation_scale 1.0"


def test_irreversible_commands_are_declared_not_hidden(executed):
    _, _, report = executed
    rollback = build_rollback(report)
    assert any("trim-caches" in c for c in rollback.irreversible)
    assert all("trim-caches" not in c for c in rollback.commands)


def test_coverage_reflects_what_cannot_be_undone(executed):
    _, _, report = executed
    rollback = build_rollback(report)
    assert 0.99 < rollback.coverage <= 1.0
    assert rollback.summary()["irreversible"] == 1


def test_rollback_of_a_dry_run_describes_what_would_be_undone(client):
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, LIGHT)
    report = Executor(client).dry_run(plan)
    rollback = build_rollback(report)
    assert rollback.commands, "a dry run still describes what would be undone"
    # trim-caches has no inverse and must be declared, not silently dropped
    assert rollback.irreversible == ["pm trim-caches 999G"]
    assert rollback.coverage == pytest.approx(3 / 4)


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------
def test_rollback_restores_the_device(client, fake):
    snapshot = Inspector(client).snapshot()
    original_scale = snapshot.settings["window_animation_scale"]
    assert original_scale == "1.0"

    plan = Planner(client).plan(snapshot, HEAVY)
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)

    assert fake.state["settings"]["global"]["window_animation_scale"] == "0.5"
    assert not fake.is_enabled("com.facebook.katana")

    rollback = build_rollback(report)
    result = RollbackExecutor(client, chunk_size=8, workers=4).apply(rollback)

    assert result.ok
    assert fake.state["settings"]["global"]["window_animation_scale"] == "1.0"
    assert fake.is_enabled("com.facebook.katana")
    assert fake.is_enabled("com.instagram.android")


def test_rollback_restores_doze_state(client, fake):
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, HEAVY)
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)
    assert fake.state["doze"] == "idle"

    RollbackExecutor(client, chunk_size=8, workers=4).apply(build_rollback(report))
    assert fake.state["doze"] == "active"


def test_rollback_is_itself_batched(client, fake):
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, HEAVY)
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)
    rollback = build_rollback(report)

    fake.calls.clear()
    result = RollbackExecutor(client, chunk_size=8, workers=4).apply(rollback)
    assert result.round_trips < result.attempted


def test_empty_rollback_is_a_no_op(client, fake):
    from android_optimiser.executor.rollback import RollbackPlan

    fake.calls.clear()
    result = RollbackExecutor(client).apply(RollbackPlan())
    assert fake.calls == []
    assert result.ok


def test_failed_rollback_commands_are_listed(client, fake):
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, HEAVY)
    report = Executor(client, chunk_size=8, retry_failures=False).execute(plan)
    rollback = build_rollback(report)

    fake.fail_patterns.append("pm enable com.facebook.katana")
    result = RollbackExecutor(client, chunk_size=8, workers=1).apply(rollback)

    assert not result.ok
    assert any("com.facebook.katana" in c for c in result.failed)
