"""End-to-end smoke test against the simulated device.

Run:  python tools/smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "benchmarks" / "simulator"):
    sys.path.insert(0, str(path))

SIM = ROOT / "benchmarks" / "simulator"
STATE = ROOT / "benchmarks" / "results" / "_smoke_state.json"
CALLS = ROOT / "benchmarks" / "results" / "_smoke_calls.jsonl"

# Truncate rather than unlink.  The simulator treats a missing *or empty* state
# file as a factory-fresh device, so truncation is equivalent here -- and it
# avoids a destructive filesystem operation in a script whose whole job is to
# be safe to run repeatedly.
STATE.parent.mkdir(parents=True, exist_ok=True)
for target in (STATE, CALLS):
    target.write_text("", encoding="utf-8")

os.environ["FAKE_ADB_STATE"] = str(STATE)
os.environ["FAKE_ADB_CALLS"] = str(CALLS)
os.environ["FAKE_ADB_LATENCY_MS"] = "0"
os.environ["FAKE_ADB_PYTHON"] = sys.executable
os.environ["ADB_BINARY"] = str(SIM / ("adb.cmd" if os.name == "nt" else "adb"))

from android_optimiser.cli import main  # noqa: E402

print("=" * 78)
print("SMOKE 1: doctor")
print("=" * 78)
print("exit:", main(["doctor"]))

print()
print("=" * 78)
print("SMOKE 2: inspect")
print("=" * 78)
print("exit:", main(["inspect", "--limit", "6"]))

print()
print("=" * 78)
print("SMOKE 3: optimize --profile heavy --dry-run")
print("=" * 78)
print("exit:", main(["optimize", "--profile", "heavy", "--dry-run", "--yes"]))

print()
print("=" * 78)
print("SMOKE 4: optimize --profile heavy (apply)")
print("=" * 78)
rc = main(
    [
        "optimize",
        "--profile",
        "heavy",
        "--yes",
        "--json-report",
        str(ROOT / "benchmarks" / "results" / "_smoke_report.json"),
    ]
)
print("exit:", rc)

print()
print("=" * 78)
print("SMOKE 5: re-run same profile (idempotency check)")
print("=" * 78)
print("exit:", main(["optimize", "--profile", "heavy", "--yes"]))

print()
print("=" * 78)
print("SMOKE 6: restore from report")
print("=" * 78)
print(
    "exit:",
    main(
        [
            "restore",
            "--from",
            str(ROOT / "benchmarks" / "results" / "_smoke_report.json"),
            "--dry-run",
        ]
    ),
)

if CALLS.exists():
    lines = CALLS.read_text(encoding="utf-8").strip().splitlines()
    print()
    print(f"total adb invocations across all smoke steps: {len(lines)}")
