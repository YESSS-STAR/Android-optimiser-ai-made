"""Package actions.

One action per package, so that a failure can be attributed to a specific
package and rolled back on its own.

Two things the legacy script got wrong here:

* it re-issued ``pm disable-user`` for every flagged package on every run,
  including the ones it had already disabled — pure wasted round-trips;
* it never stopped the running process, so a "disabled" app could keep
  executing until the next reboot.

Both are fixed: already-disabled packages produce no commands at all, and each
disable is paired with a force-stop so the change takes effect immediately.
"""

from __future__ import annotations

import shlex

from ..core.adb import AdbClient, validate_package
from ..domain import Classification, DeviceSnapshot, Impact, Risk
from .base import Action, Command

#: The OS restarts an application on demand, so stopping one needs no inverse.
_NO_OP_INVERSE = "true"


def build_package_actions(
    snapshot: DeviceSnapshot,
    classifications: list[Classification],
    client: AdbClient,
    *,
    include: Risk = Risk.SAFE,
    force_stop: bool = True,
    include_disabled: bool = False,
) -> list[Action]:
    """Build one disable action per actionable, currently-enabled package."""
    allowed = {Risk.SAFE} if include is Risk.SAFE else {Risk.SAFE, Risk.CAUTION}
    actions: list[Action] = []

    for classification in classifications:
        if classification.risk not in allowed:
            continue
        record = classification.record
        if not record.enabled and not include_disabled:
            # Already disabled by a previous run.  Issuing the command again
            # would cost a round-trip and change nothing.
            continue

        name = validate_package(record.name)
        commands = [
            Command(
                shell=client.disable_package(name),
                label=f"disable {name}",
                inverse=client.enable_package(name),
                target=name,
            )
        ]
        if force_stop:
            commands.append(
                Command(
                    shell=f"am force-stop {shlex.quote(name)}",
                    label=f"stop {name}",
                    inverse=_NO_OP_INVERSE,
                    target=name,
                )
            )

        actions.append(
            Action(
                id=f"package.disable.{name}",
                title=f"Disable {name}",
                category=classification.category,
                risk=classification.risk,
                commands=commands,
                impact=classification.impact,
                rationale=classification.rationale,
            )
        )

    return actions


def build_restore_actions(
    packages: list[str], client: AdbClient
) -> list[Action]:
    """Re-enable packages, for the standalone restore command."""
    actions: list[Action] = []
    for package in packages:
        name = validate_package(package)
        actions.append(
            Action(
                id=f"package.enable.{name}",
                title=f"Re-enable {name}",
                category="restore",
                risk=Risk.SAFE,
                commands=[
                    Command(
                        shell=client.enable_package(name),
                        label=f"enable {name}",
                        inverse=client.disable_package(name),
                        target=name,
                    )
                ],
                impact=Impact.NONE,
                rationale="restoring a package disabled by a previous run",
            )
        )
    return actions
