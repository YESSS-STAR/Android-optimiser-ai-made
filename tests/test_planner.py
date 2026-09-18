"""Tests for planning: idempotency, profile composition, and audit trail."""

from __future__ import annotations

import pytest

from android_optimiser.analysis.inspector import Inspector
from android_optimiser.domain import Risk
from android_optimiser.planner.planner import Planner
from android_optimiser.planner.profiles import AGGRESSIVE, HEAVY, LIGHT, get_profile


@pytest.fixture()
def snapshot(client):
    return Inspector(client).snapshot()


@pytest.fixture()
def planner(client) -> Planner:
    return Planner(client)


# --------------------------------------------------------------------------
# profile composition
# --------------------------------------------------------------------------
def test_light_touches_only_animation_and_storage(planner, snapshot):
    plan = planner.plan(snapshot, LIGHT)
    assert set(plan.by_category()) <= {"animation", "storage"}
    assert plan.by_category().get("animation") == 1


def test_heavy_adds_doze_and_packages(planner, snapshot):
    plan = planner.plan(snapshot, HEAVY)
    categories = set(plan.by_category())
    assert {"animation", "storage", "power"} <= categories
    assert plan.by_category().get("power") == 1
    assert len(plan.actions) > 20


def test_aggressive_adds_logcat_clearing(planner, snapshot):
    plan = planner.plan(snapshot, AGGRESSIVE)
    commands = plan.commands
    assert any("logcat -c" in c for c in commands)
    assert all("0.0" in c for c in commands if "animation_scale" in c)


def test_profiles_are_ordered_by_scope(planner, snapshot):
    light = len(planner.plan(snapshot, LIGHT).actions)
    heavy = len(planner.plan(snapshot, HEAVY).actions)
    aggressive = len(planner.plan(snapshot, AGGRESSIVE).actions)
    assert light < heavy <= aggressive


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        get_profile("nonsense")


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------
def test_plan_is_empty_when_the_device_already_matches(planner, snapshot, client):
    """The core idempotency guarantee."""
    from android_optimiser.executor.executor import Executor

    Executor(client, retry_failures=False).execute(planner.plan(snapshot, HEAVY))

    second = planner.plan(Inspector(client).snapshot(), HEAVY)
    package_actions = [a for a in second.actions if a.category != "storage"]
    assert package_actions == [], "second run must not re-issue package changes"


def test_second_plan_reissues_nothing_but_storage(planner, snapshot, client):
    from android_optimiser.executor.executor import Executor

    Executor(client, retry_failures=False).execute(planner.plan(snapshot, HEAVY))
    second = planner.plan(Inspector(client).snapshot(), HEAVY)

    remaining = [c for c in second.commands]
    assert all("trim-caches" in c for c in remaining), remaining


def test_animation_action_is_skipped_when_already_correct(client, planner):
    client.shell("settings put global window_animation_scale 0.5")
    client.shell("settings put global transition_animation_scale 0.5")
    client.shell("settings put global animator_duration_scale 0.5")
    client.invalidate()

    plan = planner.plan(Inspector(client).snapshot(), LIGHT)
    assert "animation" not in plan.by_category(), (
        "no animation command should be issued when the values already match"
    )


# --------------------------------------------------------------------------
# safety
# --------------------------------------------------------------------------
def test_no_protected_package_ever_appears_in_a_plan(planner, snapshot):
    import device_state as ds

    truth = ds.ground_truth()
    protected = {name for name, safe in truth.items() if not safe}

    for profile in (LIGHT, HEAVY, AGGRESSIVE):
        plan = planner.plan(snapshot, profile)
        for target in plan.actions:
            for name in target.targets:
                assert name not in protected, f"{name} appeared in a {profile.key} plan"


def test_caution_packages_are_excluded_by_default(planner, snapshot):
    plan = planner.plan(snapshot, HEAVY)
    caution = {c.name for c in snapshot.classifications if c.risk is Risk.CAUTION}
    assert not (set(plan.commands) & {f"pm disable-user --user 0 {n}" for n in caution})


# --------------------------------------------------------------------------
# audit trail
# --------------------------------------------------------------------------
def test_every_package_is_accounted_for(planner, snapshot):
    """Anything not in the plan must be in `skipped` with a reason."""
    plan = planner.plan(snapshot, HEAVY)
    planned = {t for a in plan.actions for t in a.targets}
    skipped = {s.package for s in plan.skipped}

    for classification in snapshot.classifications:
        name = classification.name
        assert name in planned or name in skipped, f"{name} vanished from the plan"


def test_skips_carry_a_reason(planner, snapshot):
    plan = planner.plan(snapshot, HEAVY)
    assert plan.skipped
    for record in plan.skipped:
        assert record.reason, f"{record.package} skipped without explanation"
        assert record.risk in {r.value for r in Risk}


def test_user_apps_are_skipped_with_the_provenance_reason(planner, snapshot):
    plan = planner.plan(snapshot, HEAVY)
    reasons = {s.package: s.reason for s in plan.skipped}
    reason = reasons["com.duosecurity.duomobile"]
    assert reason.startswith("installed by the user")
    assert "com.android.vending" in reason


def test_plan_summary_shape(planner, snapshot):
    summary = planner.plan(snapshot, HEAVY).summary()
    assert summary["profile"] == "heavy"
    assert summary["actions"] > 0
    assert summary["commands"] >= summary["actions"]


# --------------------------------------------------------------------------
# reversibility
# --------------------------------------------------------------------------
def test_every_planned_command_except_storage_has_an_inverse(planner, snapshot):
    plan = planner.plan(snapshot, HEAVY)
    for action in plan.actions:
        if action.category == "storage":
            continue
        for command in action.commands:
            assert command.inverse is not None, command.shell


def test_animation_commands_restore_the_previous_value(planner, snapshot):
    plan = planner.plan(snapshot, HEAVY)
    animation = next(a for a in plan.actions if a.category == "animation")
    for command in animation.commands:
        assert "1.0" in command.inverse, "factory default should be restored"
