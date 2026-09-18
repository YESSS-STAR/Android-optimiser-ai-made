"""Optimisation profiles.

Profiles are data, not control flow.  The legacy script encoded its three tiers
as three functions that called each other (``aggressive`` called ``heavy`` called
``light``), which made it impossible to ask "what exactly will the aggressive
profile do?" without reading all three bodies and mentally composing them.

Here, a profile is a value.  It can be printed, diffed, serialised and tested.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    """A named bundle of capabilities."""

    key: str
    name: str
    description: str

    animation_scale: str | None = None
    trim_caches: bool = False
    force_doze: bool = False
    disable_packages: bool = False
    include_caution: bool = False
    force_stop: bool = False
    clear_logcat: bool = False

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "animation_scale": self.animation_scale,
            "trim_caches": self.trim_caches,
            "force_doze": self.force_doze,
            "disable_packages": self.disable_packages,
            "include_caution": self.include_caution,
            "force_stop": self.force_stop,
            "clear_logcat": self.clear_logcat,
        }


LIGHT = Profile(
    key="light",
    name="Light",
    description="Animation scaling and cache trimming. Fully reversible, no app changes.",
    animation_scale="0.5",
    trim_caches=True,
)

HEAVY = Profile(
    key="heavy",
    name="Heavy",
    description="Light, plus Doze enforcement and disabling every confidently-identified preload.",
    animation_scale="0.5",
    trim_caches=True,
    force_doze=True,
    disable_packages=True,
    force_stop=True,
)

AGGRESSIVE = Profile(
    key="aggressive",
    name="Aggressive",
    description="Heavy, plus zero animations and log buffer clearing.",
    animation_scale="0.0",
    trim_caches=True,
    force_doze=True,
    disable_packages=True,
    force_stop=True,
    clear_logcat=True,
)

PROFILES: dict[str, Profile] = {
    LIGHT.key: LIGHT,
    HEAVY.key: HEAVY,
    AGGRESSIVE.key: AGGRESSIVE,
}


def get_profile(key: str) -> Profile:
    try:
        return PROFILES[key.strip().lower()]
    except KeyError as exc:
        raise KeyError(
            f"unknown profile {key!r}; choose one of {', '.join(sorted(PROFILES))}"
        ) from exc
