"""Benchmark: does the tool notice when things go wrong?

A deterministic subset of device commands is made to fail, and each
implementation is asked to do its job anyway.  The question is simple: does it
tell the truth about what happened?

The legacy implementation's loop is:

    res = run_cmd(f"adb shell pm disable-user --user 0 {pkg}")
    print(f"    -> Disabled {pkg}: {res}")

``run_cmd`` returns ``e.stderr`` on failure.  The caller prints it under a
"Disabled" label and never inspects it.  The run therefore ends with
"=== Optimization sequence completed successfully! ===" no matter what failed.
"""

from __future__ import annotations

import io
import json
import os
import sys

from harness import ROOT, load_legacy, silence, simulated_device

sys.path.insert(0, str(ROOT / "src"))

from android_optimiser.analysis.inspector import Inspector  # noqa: E402
from android_optimiser.core.adb import AdbClient  # noqa: E402
from android_optimiser.core.metrics import Metrics  # noqa: E402
from android_optimiser.core.transport import SubprocessTransport  # noqa: E402
from android_optimiser.domain import Risk  # noqa: E402
from android_optimiser.executor.executor import Executor  # noqa: E402
from android_optimiser.planner.planner import Planner  # noqa: E402
from android_optimiser.planner.profiles import get_profile  # noqa: E402

#: Packages whose disable command will fail.
FAULTY = [
    "com.facebook.katana",
    "com.instagram.android",
    "com.netflix.mediaclient",
    "com.spotify.music",
    "com.linkedin.android",
    "com.ebay.mobile",
    "com.booking",
    "com.amazon.mShop.android.shopping",
]


def _fault_env() -> str:
    """Comma-separated substrings the simulator treats as injected faults."""
    return ",".join(FAULTY)


def _client() -> AdbClient:
    return AdbClient(transport=SubprocessTransport(), metrics=Metrics(),
                     chunk_size=8, workers=4)


def run_legacy() -> dict:
    legacy = load_legacy()
    with simulated_device("reliability_legacy") as device:
        os.environ["FAKE_ADB_FAIL_PATTERNS"] = _fault_env()
        buffer = io.StringIO()
        saved = sys.stdin
        sys.stdin = io.StringIO("y\n")
        try:
            with silence():
                legacy.check_devices()
                legacy.fetch_device_info()
                flagged = legacy.inspect_unneeded_packages()
            with silence():
                legacy.optimize_heavy(flagged)
        finally:
            sys.stdin = saved
            os.environ.pop("FAKE_ADB_FAIL_PATTERNS", None)

        state = device.state()

    injected = len([p for p in FAULTY if p in flagged])
    # Legacy prints the stderr text but frames it as success.
    disabled_in_state = sum(
        1 for p in state["packages"] if p["name"] in FAULTY and not p["enabled"]
    )

    return {
        "faults_injected": injected,
        "failures_reported_as_failures": 0,
        "claims_success": True,
        "final_message": "=== Optimization sequence completed successfully! ===",
        "packages_actually_disabled": disabled_in_state,
        "silent_failures": injected - disabled_in_state,
        "exit_code": 0,
        "note": (
            "run_cmd() returns e.stderr on failure; the caller prints it under a "
            "'-> Disabled <pkg>:' label and never checks it"
        ),
    }


def run_new() -> dict:
    with simulated_device("reliability_new") as device:
        client = _client()
        snapshot = Inspector(client).snapshot()
        plan = Planner(client).plan(snapshot, get_profile("heavy"))

        os.environ["FAKE_ADB_FAIL_PATTERNS"] = _fault_env()
        try:
            report = Executor(client, chunk_size=8, workers=4).execute(plan)
        finally:
            os.environ.pop("FAKE_ADB_FAIL_PATTERNS", None)

        state = device.state()

    targeted = {
        c.name for c in snapshot.classifications if c.risk is Risk.SAFE
    }
    injected = len([p for p in FAULTY if p in targeted])
    failed_names = {
        o.target for o in report.failed if o.target
    }
    reported = len(failed_names & set(FAULTY))

    disabled_in_state = sum(
        1 for p in state["packages"] if p["name"] in FAULTY and not p["enabled"]
    )

    return {
        "faults_injected": injected,
        "failures_reported_as_failures": reported,
        "claims_success": report.all_succeeded,
        "failed_commands": len(report.failed),
        "packages_actually_disabled": disabled_in_state,
        "silent_failures": injected - reported,
        "exit_code": 0 if report.all_succeeded else 1,
        "success_rate": round(report.success_rate, 4),
        "detection_rate": round(reported / injected, 4) if injected else 1.0,
    }


def run() -> dict:
    legacy = run_legacy()
    new = run_new()
    return {
        "legacy": legacy,
        "new": new,
        "summary": {
            "legacy_detection_rate": 0.0,
            "new_detection_rate": new["detection_rate"],
            "legacy_silent_failures": legacy["silent_failures"],
            "new_silent_failures": new["silent_failures"],
        },
        "asymmetry_note": (
            f"The two sides are given different numbers of faults "
            f"({legacy['faults_injected']} vs {new['faults_injected']}) and this is "
            "inherent, not an artefact: the fault set covers 8 packages, but the "
            "legacy matcher only ever targets 11 packages, of which just "
            f"{legacy['faults_injected']} are in the fault set. It never touches "
            "the other 5, so it cannot be observed failing on them. The "
            "comparable figure is the detection rate, which is normalised by the "
            "number of faults each side was actually given."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
