"""Benchmark: the execution mechanism, isolated from decision quality.

Two separate measurements:

**Same workload.**  Both implementations are given the *identical* package list
and asked to disable it.  This isolates transport efficiency from classification
quality — the legacy side is not penalised for choosing badly here, because it
is handed the new classifier's list.

**Scaling.**  The same operation is repeated for growing package counts, so the
cost per package can be read off as a slope rather than a single point.

The legacy side runs the original module's ``optimize_heavy``; the loop it
contains is the one from the upstream file verbatim::

    for pkg in flagged_bloat:
        res = run_cmd(f"adb shell pm disable-user --user 0 {pkg}")
"""

from __future__ import annotations

import json
import math
import sys

from harness import ROOT, load_legacy, silence, simulated_device

sys.path.insert(0, str(ROOT / "src"))

from android_optimiser.analysis.inspector import Inspector  # noqa: E402
from android_optimiser.core.adb import AdbClient  # noqa: E402
from android_optimiser.core.metrics import Metrics  # noqa: E402
from android_optimiser.core.transport import SubprocessTransport  # noqa: E402
from android_optimiser.domain import Risk  # noqa: E402
from android_optimiser.planner.planner import Planner  # noqa: E402
from android_optimiser.planner.profiles import get_profile  # noqa: E402
from android_optimiser.executor.executor import Executor  # noqa: E402


def _client() -> AdbClient:
    return AdbClient(transport=SubprocessTransport(), metrics=Metrics(),
                     chunk_size=8, workers=4)


def safe_packages(device) -> list[str]:
    """The package list the new classifier recommends disabling."""
    client = _client()
    snapshot = Inspector(client).snapshot()
    return [
        c.name
        for c in snapshot.classifications
        if c.risk is Risk.SAFE and c.record.enabled
    ]


# --------------------------------------------------------------------------
# 1. identical workload
# --------------------------------------------------------------------------
def bench_same_workload() -> dict:
    with simulated_device("mechanism_probe") as device:
        packages = safe_packages(device)
    count = len(packages)

    # -- legacy mechanism -------------------------------------------------
    with simulated_device("mechanism_legacy") as device:
        legacy = load_legacy()
        device.reset_calls()
        with silence():
            legacy.optimize_heavy(packages)
        legacy_rt = device.round_trips
        legacy_dc = device.device_commands()

    # -- new mechanism ----------------------------------------------------
    with simulated_device("mechanism_new") as device:
        client = _client()
        snapshot = Inspector(client).snapshot()
        plan = Planner(client).plan(snapshot, get_profile("heavy"))
        device.reset_calls()
        report = Executor(client, chunk_size=8, workers=4).execute(plan)
        new_rt = device.round_trips
        new_dc = device.device_commands()

    # -- new mechanism, batching only (no force-stop) ----------------------
    with simulated_device("mechanism_new_batchonly") as device:
        client = _client()
        snapshot = Inspector(client).snapshot()
        from android_optimiser.planner.profiles import Profile

        batch_only = Profile(
            key="packages-only",
            name="Packages only",
            description="disable packages with no force-stop, to isolate batching",
            disable_packages=True,
            force_stop=False,
        )
        plan = Planner(client).plan(snapshot, batch_only)
        device.reset_calls()
        Executor(client, chunk_size=8, workers=4).execute(plan)
        batch_only_rt = device.round_trips

    return {
        "packages": count,
        "legacy": {
            "round_trips": legacy_rt,
            "device_commands": legacy_dc,
            "round_trips_per_package": round(legacy_rt / count, 3),
            "applied": report.metrics.get("device_commands", 0),
        },
        "new": {
            "round_trips": new_rt,
            "device_commands": new_dc,
            "round_trips_per_package": round(new_rt / count, 4),
        },
        "new_batching_only": {
            "round_trips": batch_only_rt,
            "round_trips_per_package": round(batch_only_rt / count, 4),
        },
        "reduction_pct": round((legacy_rt - new_rt) / legacy_rt * 100, 1),
        "reduction_pct_batching_only": round(
            (legacy_rt - batch_only_rt) / legacy_rt * 100, 1
        ),
        "speedup_factor": round(legacy_rt / new_rt, 2),
        "speedup_factor_batching_only": round(legacy_rt / batch_only_rt, 2),
    }


# --------------------------------------------------------------------------
# 2. scaling
# --------------------------------------------------------------------------
def bench_scaling(sizes=(4, 8, 16, 32, 64)) -> dict:
    series = []
    for size in sizes:
        with simulated_device(f"scale_probe_{size}") as device:
            pool = safe_packages(device)
        packages = pool[:size]
        if len(packages) < size:
            continue

        with simulated_device(f"scale_legacy_{size}") as device:
            legacy = load_legacy()
            device.reset_calls()
            with silence():
                for pkg in packages:
                    legacy.run_cmd(f"adb shell pm disable-user --user 0 {pkg}")
            legacy_rt = device.round_trips

        with simulated_device(f"scale_new_{size}") as device:
            client = _client()
            device.reset_calls()
            client.shell_batch_parallel(
                [client.disable_package(p) for p in packages],
                chunk_size=8,
                workers=4,
            )
            new_rt = device.round_trips

        series.append(
            {
                "packages": size,
                "legacy_round_trips": legacy_rt,
                "new_round_trips": new_rt,
                "legacy_per_package": round(legacy_rt / size, 3),
                "new_per_package": round(new_rt / size, 4),
                "speedup": round(legacy_rt / new_rt, 2),
                "theoretical_new": math.ceil(size / 8),
            }
        )

    return {"chunk_size": 8, "workers": 4, "series": series}


def run() -> dict:
    return {
        "same_workload": bench_same_workload(),
        "scaling": bench_scaling(),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
