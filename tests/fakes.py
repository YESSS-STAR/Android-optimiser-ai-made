"""Test doubles.

``InProcessTransport`` reuses the simulator's *actual* command dispatch
(``adb_sim.run_device_command``) but skips the process boundary.  That keeps
unit tests fast and, more importantly, means the tests exercise the same device
behaviour the benchmarks measure rather than a second, drifting imitation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

SIM = Path(__file__).resolve().parents[1] / "benchmarks" / "simulator"
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))

import adb_sim  # noqa: E402
import device_state as ds  # noqa: E402

from android_optimiser.config import DEFAULT_TIMEOUT  # noqa: E402
from android_optimiser.core.transport import CommandResult  # noqa: E402


class InProcessTransport:
    """A transport backed by an in-memory simulated device."""

    def __init__(self, state: dict | None = None, *, online: bool = True):
        self.state = state if state is not None else ds.default_state()
        self.state["online"] = online
        self.calls: list[tuple[str, ...]] = []
        self.fail_patterns: list[str] = []

    # -- helpers ---------------------------------------------------------
    def package(self, name: str) -> dict | None:
        return ds.package_index(self.state).get(name)

    def is_enabled(self, name: str) -> bool:
        record = self.package(name)
        assert record is not None, f"unknown package {name}"
        return bool(record["enabled"])

    @property
    def round_trips(self) -> int:
        return len(self.calls)

    @property
    def device_commands(self) -> int:
        total = 0
        for argv in self.calls:
            if len(argv) >= 2 and argv[0] == "shell":
                total += len(adb_sim.split_commands(" ".join(argv[1:])))
            else:
                total += 1
        return total

    # -- Transport protocol ----------------------------------------------
    def run(self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
        argv = tuple(argv)
        self.calls.append(argv)

        # Strip the global options adb accepts before the subcommand, exactly as
        # the real binary does, so `-s SERIAL shell ...` is handled correctly.
        rest = adb_sim.strip_global_options(list(argv))
        if not rest:
            return CommandResult(argv=argv, rc=1, stdout="", stderr="", duration=0.001)

        sub, args = rest[0], rest[1:]

        if sub == "devices":
            serial = self.state["serial"]
            status = "device" if self.state.get("online", True) else "offline"
            return CommandResult(
                argv=argv, rc=0,
                stdout=f"List of devices attached\n{serial}\t{status}\n\n",
                stderr="", duration=0.001,
            )

        if sub != "shell":
            return CommandResult(argv=argv, rc=0, stdout="", stderr="", duration=0.001)

        script = " ".join(args)
        for pattern in self.fail_patterns:
            if pattern in script:
                return CommandResult(
                    argv=argv, rc=1, stdout="",
                    stderr="adb: device offline (injected fault)", duration=0.001,
                )

        device = adb_sim.Device(self.state, "<memory>")
        out: list[str] = []
        rc = 0
        for piece in adb_sim.split_commands(script):
            text, rc = adb_sim.run_device_command(device, piece, rc)
            if text:
                out.append(text)

        return CommandResult(
            argv=argv,
            rc=rc,
            stdout=("\n".join(out) + "\n") if out else "",
            stderr="" if rc == 0 else f"adb: shell command failed (rc={rc})\n",
            duration=0.001,
        )
