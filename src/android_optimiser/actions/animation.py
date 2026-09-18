"""Animation-scale actions.

Android exposes three independent animation multipliers.  The legacy script
wrote all three unconditionally with three separate ``adb shell`` invocations,
every single run, whether or not they already held the target value.

This builder emits commands only for the keys that are actually wrong, and the
executor packs them into one round-trip.  On an already-optimised device the
action list is empty and the cost is zero.
"""

from __future__ import annotations

from ..config import ANIMATION_KEYS
from ..core.adb import AdbClient
from ..domain import DeviceSnapshot, Impact, Risk
from .base import Action, Command

#: Android's factory defaults, used when a key is absent from the device.
_FACTORY_DEFAULTS = {
    "window_animation_scale": "1.0",
    "transition_animation_scale": "1.0",
    "animator_duration_scale": "1.0",
}

ACTION_ID = "animation.scale"


def build_animation_actions(
    snapshot: DeviceSnapshot,
    client: AdbClient,
    scale: str,
) -> list[Action]:
    """Return an action that sets the three animation scales to ``scale``.

    Returns an empty list when the device is already at the target, which is
    what makes a second run free.
    """
    current = snapshot.settings
    commands: list[Command] = []

    for key in ANIMATION_KEYS:
        present = current.get(key)
        if present == scale:
            continue
        previous = present if present is not None else _FACTORY_DEFAULTS.get(key, "1.0")
        commands.append(
            Command(
                shell=client.put_setting(key, scale),
                label=f"{key}: {previous} -> {scale}",
                inverse=client.put_setting(key, previous),
                target=key,
            )
        )

    if not commands:
        return []

    impact = Impact.NOTABLE if scale == "0.0" else Impact.MINOR
    return [
        Action(
            id=ACTION_ID,
            title=f"Set animation scales to {scale}x",
            category="animation",
            risk=Risk.SAFE,
            commands=commands,
            impact=impact,
            rationale=(
                "Shortening window/transition/animator durations is the single "
                "most perceptible responsiveness change available without root; "
                "it changes perceived latency, not actual throughput."
            ),
        )
    ]
