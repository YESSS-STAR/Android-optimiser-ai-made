"""Shared fixtures.

Two flavours of device are available:

``fake`` / ``client``
    In-process.  Fast, no subprocess spawn.  Used for the bulk of the suite.
``device``
    Out-of-process, through the real ``adb`` shim and a real state file.  Used
    for the handful of tests that need to prove end-to-end behaviour.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SIM = ROOT / "benchmarks" / "simulator"

for path in (str(SRC), str(SIM), str(ROOT / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

from fakes import InProcessTransport  # noqa: E402

from android_optimiser.core.adb import AdbClient  # noqa: E402
from android_optimiser.core.metrics import Metrics  # noqa: E402
from android_optimiser.core.transport import SubprocessTransport  # noqa: E402


@pytest.fixture()
def fake() -> InProcessTransport:
    """An in-memory simulated device."""
    return InProcessTransport()


@pytest.fixture()
def client(fake: InProcessTransport) -> AdbClient:
    """An AdbClient bound to the in-memory device."""
    return AdbClient(transport=fake, metrics=Metrics(), chunk_size=4, workers=4)


@pytest.fixture()
def device(tmp_path, monkeypatch):
    """An out-of-process simulated device, for end-to-end tests."""
    state = tmp_path / "state.json"
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_ADB_STATE", str(state))
    monkeypatch.setenv("FAKE_ADB_CALLS", str(calls))
    monkeypatch.setenv("FAKE_ADB_LATENCY_MS", "0")
    monkeypatch.setenv("FAKE_ADB_PYTHON", sys.executable)
    monkeypatch.setenv(
        "ADB_BINARY", str(SIM / ("adb.cmd" if os.name == "nt" else "adb"))
    )
    return AdbClient(
        transport=SubprocessTransport(), metrics=Metrics(), chunk_size=8, workers=4
    )
