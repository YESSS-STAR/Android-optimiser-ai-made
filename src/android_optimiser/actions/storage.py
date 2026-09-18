"""Storage and I/O actions.

``pm trim-caches`` asks the package manager to evict cached data across all
apps.  It is not reversible in the sense of "put the bytes back", but it is also
not destructive: caches are rebuilt on demand.  The action is therefore marked
non-reversible and is excluded from rollback plans rather than pretending an
inverse exists.

``logcat -c`` clears the kernel/radio/system log ring buffers.  It reduces
background I/O but destroys diagnostic history, so it is only offered in the
aggressive profile.
"""

from __future__ import annotations

from ..config import CACHE_TRIM_TARGET
from ..domain import DeviceSnapshot, Impact, Risk
from .base import Action, Command

ACTION_TRIM = "storage.trim_caches"
ACTION_LOGCAT = "storage.clear_logcat"


def build_storage_actions(
    snapshot: DeviceSnapshot, *, trim_caches: bool = True, clear_logcat: bool = False
) -> list[Action]:
    actions: list[Action] = []

    if trim_caches:
        actions.append(
            Action(
                id=ACTION_TRIM,
                title="Trim system package caches",
                category="storage",
                risk=Risk.SAFE,
                commands=[
                    Command(
                        shell=f"pm trim-caches {CACHE_TRIM_TARGET}",
                        label=f"trim-caches {CACHE_TRIM_TARGET}",
                        inverse=None,
                        target="trim-caches",
                    )
                ],
                impact=Impact.NONE,
                rationale=(
                    "Evicts cached data across all packages, freeing storage and "
                    "reducing background write pressure. Caches rebuild on demand, "
                    "so there is nothing to undo."
                ),
            )
        )

    if clear_logcat:
        actions.append(
            Action(
                id=ACTION_LOGCAT,
                title="Clear the log buffers",
                category="storage",
                risk=Risk.SAFE,
                commands=[
                    Command(
                        shell="logcat -c",
                        label="logcat -c",
                        inverse=None,
                        target="logcat",
                    )
                ],
                impact=Impact.MINOR,
                rationale=(
                    "Drops buffered log data to reduce background I/O. This "
                    "destroys diagnostic history, which is why it is opt-in."
                ),
            )
        )

    return actions
