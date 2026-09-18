"""Planning.

The planner turns a device snapshot plus a profile into a concrete
:class:`ActionPlan`: an ordered list of actions, each carrying its own inverse,
plus an audit trail of everything it declined to do and why.

Two properties matter:

**Idempotency.**  Planning is a pure function of (snapshot, profile).  Run it
twice against the same device and the second plan is empty, because every
builder emits commands only for state that differs from the target.

**Auditability.**  Every package the classifier saw is accounted for.  A package
that is not in the plan is there in ``skipped`` with a reason — protected,
user-installed, already disabled, or not confident enough.  The legacy script
simply printed a list and moved on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..actions import animation as animation_actions
from ..actions import packages as package_actions
from ..actions import power as power_actions
from ..actions import storage as storage_actions
from ..actions.base import Action
from ..core.adb import AdbClient
from ..domain import DeviceSnapshot, Risk
from .profiles import Profile


@dataclass
class SkipRecord:
    package: str
    risk: str
    reason: str

    def as_dict(self) -> dict:
        return {"package": self.package, "risk": self.risk, "reason": self.reason}


@dataclass
class ActionPlan:
    """An ordered, reviewable set of changes."""

    profile: Profile
    actions: list[Action] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)

    @property
    def commands(self) -> list[str]:
        return [c.shell for a in self.actions for c in a.commands]

    @property
    def reversible(self) -> bool:
        return all(a.reversible for a in self.actions)

    @property
    def empty(self) -> bool:
        return not self.actions

    def by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.category] = counts.get(action.category, 0) + 1
        return dict(sorted(counts.items()))

    def summary(self) -> dict:
        return {
            "profile": self.profile.key,
            "actions": len(self.actions),
            "commands": len(self.commands),
            "reversible": self.reversible,
            "by_category": self.by_category(),
            "skipped": len(self.skipped),
        }


class Planner:
    """Builds :class:`ActionPlan` objects."""

    def __init__(self, client: AdbClient):
        self.client = client

    def plan(self, snapshot: DeviceSnapshot, profile: Profile) -> ActionPlan:
        actions: list[Action] = []
        skipped: list[SkipRecord] = []

        if profile.animation_scale is not None:
            actions.extend(
                animation_actions.build_animation_actions(
                    snapshot, self.client, profile.animation_scale
                )
            )

        if profile.trim_caches or profile.clear_logcat:
            actions.extend(
                storage_actions.build_storage_actions(
                    snapshot,
                    trim_caches=profile.trim_caches,
                    clear_logcat=profile.clear_logcat,
                )
            )

        if profile.force_doze:
            actions.extend(power_actions.build_power_actions(snapshot))

        if profile.disable_packages:
            include = Risk.CAUTION if profile.include_caution else Risk.SAFE
            actions.extend(
                package_actions.build_package_actions(
                    snapshot,
                    snapshot.classifications,
                    self.client,
                    include=include,
                    force_stop=profile.force_stop,
                )
            )
            skipped.extend(self._explain_skips(snapshot, include))
        else:
            skipped.extend(
                SkipRecord(
                    package=c.name,
                    risk=c.risk.value,
                    reason="profile does not modify packages",
                )
                for c in snapshot.classifications
                if c.risk is not Risk.NEVER
            )

        return ActionPlan(profile=profile, actions=actions, skipped=skipped)

    # ------------------------------------------------------------------
    @staticmethod
    def _skip_reason(classification, allowed: set[Risk]) -> str | None:
        """Why this package is not in the plan, or ``None`` if it is.

        Ordered the same way the classifier is: a hard protection is reported
        as itself, and only then does confidence or profile filtering apply.
        """
        if classification.risk is Risk.NEVER:
            return classification.rationale
        if classification.risk not in allowed:
            if classification.risk is Risk.CAUTION:
                return "classifier is not confident enough for automatic action"
            return "excluded by profile"
        if not classification.record.enabled:
            return "already disabled by a previous run"
        return None

    @staticmethod
    def _explain_skips(snapshot: DeviceSnapshot, include: Risk) -> list[SkipRecord]:
        allowed = {Risk.SAFE} if include is Risk.SAFE else {Risk.SAFE, Risk.CAUTION}
        out: list[SkipRecord] = []
        for classification in snapshot.classifications:
            reason = Planner._skip_reason(classification, allowed)
            if reason is None:
                continue
            out.append(
                SkipRecord(
                    package=classification.record.name,
                    risk=classification.risk.value,
                    reason=reason,
                )
            )
        return out
