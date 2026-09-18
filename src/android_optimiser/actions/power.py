"""Power actions.

Forcing the device into Doze immediately stops background wakeups and alarm
storms.  It is idempotent: if the device is already idle, nothing is emitted.
"""

from __future__ import annotations

from ..domain import DeviceSnapshot, Impact, Risk
from .base import Action, Command

ACTION_ID = "power.force_doze"


def build_power_actions(snapshot: DeviceSnapshot) -> list[Action]:
    if snapshot.doze_state == "idle":
        return []

    return [
        Action(
            id=ACTION_ID,
            title="Force the device into Doze",
            category="power",
            risk=Risk.SAFE,
            commands=[
                Command(
                    shell="dumpsys deviceidle force-idle",
                    label="force-idle",
                    inverse="dumpsys deviceidle unforce",
                    target="deviceidle",
                )
            ],
            impact=Impact.MINOR,
            rationale=(
                "Deep idle suspends background network and alarm activity. "
                "Notifications may be delayed until the device leaves Doze."
            ),
        )
    ]
