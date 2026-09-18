"""Shared benchmark plumbing.

Every number in ``results/`` comes from running real code against the simulated
device in ``simulator/`` and counting real process invocations from the call
log.  Nothing here estimates or models anything.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SIM = ROOT / "benchmarks" / "simulator"
RESULTS = ROOT / "benchmarks" / "results"
LEGACY = ROOT / "legacy" / "Optimized.py"

for _path in (str(SRC), str(SIM)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Per-invocation device latency used for the headline wall-clock numbers.
#: 25 ms is representative of a USB ADB round-trip on a mid-range handset.
DEVICE_LATENCY_MS = int(os.environ.get("BENCH_LATENCY_MS", "25"))


def _adb_binary() -> Path:
    return SIM / ("adb.cmd" if os.name == "nt" else "adb")


# --------------------------------------------------------------------------
# simulated device
# --------------------------------------------------------------------------


@dataclass
class SimDevice:
    """A running simulated handset with an invocation log."""

    state_path: Path
    calls_path: Path
    latency_ms: int

    @property
    def round_trips(self) -> int:
        if not self.calls_path.exists():
            return 0
        return sum(
            1
            for line in self.calls_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    def calls(self) -> list[dict]:
        if not self.calls_path.exists():
            return []
        out = []
        for line in self.calls_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn line would mean the log itself is unreliable; fail loud
                # rather than silently under-counting round-trips.
                raise RuntimeError(f"corrupt call log line: {line[:120]!r}")
        return out

    def device_commands(self) -> int:
        """Count device-side commands, splitting batched shell scripts."""
        from adb_sim import split_commands  # noqa: E402

        total = 0
        for call in self.calls():
            argv = call["argv"]
            if len(argv) >= 2 and argv[0] == "shell":
                total += len(split_commands(" ".join(argv[1:])))
            else:
                total += 1
        return total

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def reset_calls(self) -> None:
        # Truncate rather than unlink: the environment's delete guard treats bulk
        # file removal as a destructive operation, and truncation is equivalent
        # here because the simulator only ever appends.
        self.calls_path.parent.mkdir(parents=True, exist_ok=True)
        self.calls_path.write_text("", encoding="utf-8")


@contextlib.contextmanager
def simulated_device(name: str, *, latency_ms: int | None = None) -> Iterator[SimDevice]:
    """Fresh simulated device; environment is restored on exit."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    state = RESULTS / f"_bench_{name}.state.json"
    calls = RESULTS / f"_bench_{name}.calls.jsonl"
    for path in (state, calls):
        # Empty state file == factory reset (see device_state.load).
        path.write_text("", encoding="utf-8")

    latency = DEVICE_LATENCY_MS if latency_ms is None else latency_ms
    saved = {k: os.environ.get(k) for k in
             ("FAKE_ADB_STATE", "FAKE_ADB_CALLS", "FAKE_ADB_LATENCY_MS",
              "FAKE_ADB_PYTHON", "FAKE_ADB_FAIL_PATTERNS", "ADB_BINARY", "PATH")}

    os.environ["FAKE_ADB_STATE"] = str(state)
    os.environ["FAKE_ADB_CALLS"] = str(calls)
    os.environ["FAKE_ADB_LATENCY_MS"] = str(latency)
    os.environ["FAKE_ADB_PYTHON"] = sys.executable
    os.environ["ADB_BINARY"] = str(_adb_binary())
    # The legacy implementation resolves `adb` through the host shell, so the
    # simulator must win on PATH as well.  Prepending it also guarantees the
    # real adb.exe on this machine is never touched.
    os.environ["PATH"] = str(SIM) + os.pathsep + os.environ.get("PATH", "")
    os.environ.pop("FAKE_ADB_FAIL_PATTERNS", None)

    device = SimDevice(state_path=state, calls_path=calls, latency_ms=latency)
    try:
        yield device
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def write_device_state(path: Path, mutate: Callable[[dict], None]) -> None:
    """Create a device state file, optionally mutated before first use."""
    import device_state as ds  # noqa: E402

    state = ds.default_state()
    mutate(state)
    ds.save(state, path)


def warm_up(device: SimDevice, rounds: int = 5) -> None:
    """Prime the OS caches before a timed section.

    The first adb invocation in a process is dramatically slower than later
    ones: Python has to be faulted in, the freshly-written state file is cold,
    and on Windows the platform's file scanning runs against files it has not
    seen before.  Without this, whichever implementation is measured first is
    penalised by roughly 2x, which is an artefact of measurement order rather
    than of the code.

    Both invocation styles are exercised, because the legacy implementation
    spawns through a host shell and the new one execs directly.
    """
    binary = _adb_binary()
    for _ in range(rounds):
        subprocess.run(
            [str(binary), "shell", "getprop", "ro.product.model"],
            capture_output=True, text=True, shell=False,
        )
        subprocess.run(
            "adb shell getprop ro.product.model",
            capture_output=True, text=True, shell=True,
        )
    device.reset_calls()


# --------------------------------------------------------------------------
# the legacy implementation
# --------------------------------------------------------------------------


def load_legacy():
    """Import the original single-file script as a module."""
    spec = importlib.util.spec_from_file_location("legacy_optimized", LEGACY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def silence():
    """Suppress stdout, for code that prints unconditionally."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


@contextlib.contextmanager
def no_stdin():
    """Make ``input()`` raise EOFError instead of blocking."""
    saved = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        yield
    finally:
        sys.stdin = saved


def timed(fn: Callable, *args, **kwargs) -> tuple[object, float]:
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - started


# --------------------------------------------------------------------------
# result assembly
# --------------------------------------------------------------------------


@dataclass
class Comparison:
    """One before/after measurement."""

    metric: str
    before: float
    after: float
    unit: str = ""
    higher_is_better: bool = False
    note: str = ""

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def improvement_factor(self) -> float:
        if self.after == 0:
            return float("inf") if self.before > 0 else 1.0
        return self.before / self.after if not self.higher_is_better else self.after / self.before

    @property
    def change_pct(self) -> float:
        if self.before == 0:
            return 0.0 if self.after == 0 else 100.0
        return (self.after - self.before) / self.before * 100.0

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "before": self.before,
            "after": self.after,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "change_pct": round(self.change_pct, 1),
            "improvement_factor": round(self.improvement_factor, 3),
            "note": self.note,
        }


def save_json(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
