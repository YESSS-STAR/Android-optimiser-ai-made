"""Benchmark: what does it cost to learn the device's state?

Both implementations are run against a freshly reset simulated device, and the
invocation log is counted.  The legacy side calls the real
``check_devices``/``fetch_device_info``/``inspect_unneeded_packages``; the new
side calls the real ``Inspector.snapshot``.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from harness import ROOT, load_legacy, no_stdin, silence, simulated_device

sys.path.insert(0, str(ROOT / "src"))

from android_optimiser.analysis.inspector import Inspector  # noqa: E402
from android_optimiser.core.adb import AdbClient  # noqa: E402
from android_optimiser.core.metrics import Metrics  # noqa: E402
from android_optimiser.core.transport import SubprocessTransport  # noqa: E402


def legacy_inspect(device) -> dict:
    legacy = load_legacy()
    device.reset_calls()

    with silence():
        devices = legacy.check_devices()
    after_devices = device.round_trips

    # fetch_device_info() blocks on input(); feed it a confirmation.
    saved_stdin = sys.stdin
    sys.stdin = io.StringIO("y\n")
    try:
        with silence():
            legacy.fetch_device_info()
    finally:
        sys.stdin = saved_stdin
    after_info = device.round_trips

    with silence():
        flagged = legacy.inspect_unneeded_packages()
    after_packages = device.round_trips

    # the four properties fetch_device_info actually reads
    facts = 4 + len(flagged)

    return {
        "round_trips_total": after_packages,
        "round_trips_devices": after_devices,
        "round_trips_device_info": after_info - after_devices,
        "round_trips_packages": after_packages - after_info,
        "device_attributes_learned": 4,
        "packages_learned": len(flagged),
        "facts_learned": facts,
        "round_trips_per_fact": round(after_packages / max(1, facts), 3),
        "knows_partition": False,
        "knows_installer": False,
        "knows_disabled_state": False,
    }


def new_inspect(device) -> dict:
    client = AdbClient(transport=SubprocessTransport(), metrics=Metrics())
    device.reset_calls()

    snapshot = Inspector(client).snapshot()
    total = device.round_trips

    facts = len(snapshot.info) + len(snapshot.packages)
    return {
        "round_trips_total": total,
        "round_trips_devices": 1,
        "round_trips_device_info": 0,
        "round_trips_packages": total - 1,
        "device_attributes_learned": len(snapshot.info),
        "packages_learned": len(snapshot.packages),
        "facts_learned": facts,
        "round_trips_per_fact": round(total / max(1, facts), 4),
        "knows_partition": True,
        "knows_installer": True,
        "knows_disabled_state": True,
        "metrics": client.metrics.summary(),
    }


def run() -> dict:
    with simulated_device("inspection") as device:
        legacy = legacy_inspect(device)

    with simulated_device("inspection") as device:
        new = new_inspect(device)

    return {
        "legacy": legacy,
        "new": new,
        "round_trip_reduction_pct": round(
            (legacy["round_trips_total"] - new["round_trips_total"])
            / legacy["round_trips_total"] * 100,
            1,
        ),
        "facts_gained": new["facts_learned"] - legacy["facts_learned"],
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
