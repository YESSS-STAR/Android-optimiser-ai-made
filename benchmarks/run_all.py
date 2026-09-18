"""Run the whole benchmark suite and write one consolidated result set.

Usage::

    python benchmarks/run_all.py

Writes::

    benchmarks/results/raw/<name>.json     one file per benchmark, as produced
    benchmarks/results/benchmarks.json     everything, plus environment metadata

Each benchmark is a module exposing ``run() -> dict``.  They are executed in
separate subprocesses so that no benchmark can contaminate another through
imported state, environment variables, or the working directory.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks"
RESULTS = BENCH / "results"
RAW = RESULTS / "raw"

SUITE: list[tuple[str, str]] = [
    ("classification", "bench_classification"),
    ("inspection", "bench_inspection"),
    ("mechanism", "bench_mechanism"),
    ("endtoend", "bench_endtoend"),
    ("reliability", "bench_reliability"),
    ("security", "bench_security"),
    ("structure", "bench_structure"),
]


def _run_one(name: str, module: str) -> dict:
    """Execute one benchmark in a fresh interpreter and parse its JSON stdout."""
    script = (
        "import json, sys;"
        f"sys.path.insert(0, {str(BENCH)!r});"
        f"import {module} as m;"
        "print(json.dumps(m.run()))"
    )
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(BENCH), shell=False,
    )
    elapsed = time.perf_counter() - started

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": (proc.stderr or "").strip()[-4000:],
            "seconds": round(elapsed, 2),
        }

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"unparseable output: {exc}\n{proc.stdout[:2000]}",
            "seconds": round(elapsed, 2),
        }

    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{name}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return {"ok": True, "seconds": round(elapsed, 2), "data": payload}


def _parse_test_count(stdout: str) -> int:
    """Read the collected-test count out of ``pytest --collect-only``.

    Handles both output shapes: the ``N tests collected`` summary, and the
    per-file ``tests/test_x.py: 53`` form that pytest emits at higher
    verbosity settings.
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        if "test collected" in stripped or "tests collected" in stripped:
            head = stripped.split()[0]
            if head.isdigit():
                return int(head)

    total = 0
    for line in stdout.splitlines():
        if not line.startswith("tests/"):
            continue
        tail = line.rsplit(":", 1)[-1].strip()
        if tail.isdigit():
            total += int(tail)
    return total


def _run_tests() -> dict:
    """Collect the test count and coverage figure, best effort.

    Runs in the current interpreter, so it only produces numbers when that
    interpreter has ``pytest`` and ``coverage`` installed.  A missing tool is
    recorded as unavailable rather than reported as a zero.
    """
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src"), str(BENCH / "simulator"), str(ROOT / "tests")]
    )

    probe = subprocess.run(
        [sys.executable, "-c", "import pytest, coverage"],
        capture_output=True, text=True, cwd=str(ROOT), shell=False,
    )
    if probe.returncode != 0:
        return {"available": False, "reason": "pytest/coverage not installed in this interpreter"}

    run = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source=android_optimiser",
         "-m", "pytest", "-q"],
        capture_output=True, text=True, cwd=str(ROOT), shell=False, env=env,
    )
    if run.returncode != 0:
        return {"available": False, "reason": run.stdout.strip()[-2000:]}

    collected = subprocess.run(
        # `-o addopts=` neutralises the project's own `addopts = "-q"`. Without
        # it the two -q flags combine into -qq, which prints per-file counts
        # instead of the "N tests collected" summary line.
        [sys.executable, "-m", "pytest", "-o", "addopts=", "--collect-only", "-q"],
        capture_output=True, text=True, cwd=str(ROOT), shell=False, env=env,
    )
    count = _parse_test_count(collected.stdout)

    covered = subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--format=total"],
        capture_output=True, text=True, cwd=str(ROOT), shell=False, env=env,
    )
    try:
        coverage_pct = float(covered.stdout.strip())
    except ValueError:
        coverage_pct = None

    return {
        "available": True,
        "tests_collected": count,
        "tests_passed": count,
        "line_coverage_pct": coverage_pct,
    }


def _environment() -> dict:
    import os

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "device_latency_ms": int(os.environ.get("BENCH_LATENCY_MS", "25")),
    }


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    benchmarks: dict[str, dict] = {}
    failures: list[str] = []

    for name, module in SUITE:
        print(f"[run_all] {name:<16}", end="", flush=True)
        outcome = _run_one(name, module)
        if outcome["ok"]:
            print(f" ok  ({outcome['seconds']}s)")
            benchmarks[name] = outcome["data"]
        else:
            print(" FAILED")
            failures.append(name)
            benchmarks[name] = {"error": outcome["error"]}

    print(f"[run_all] {'tests':<16}", end="", flush=True)
    tests = _run_tests()
    print(" ok" if tests.get("available") else " skipped")
    (RAW / "tests.json").write_text(json.dumps(tests, indent=2), encoding="utf-8")

    payload = {
        "environment": _environment(),
        "benchmarks": benchmarks,
        "tests": tests,
        "failed": failures,
    }
    (RESULTS / "benchmarks.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    print(f"[run_all] wrote {RESULTS / 'benchmarks.json'}")
    if failures:
        print(f"[run_all] FAILED: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
