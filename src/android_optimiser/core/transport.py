"""Process transport.

The single responsibility of this module is "turn an argv list into a process
result".  It knows nothing about Android, nothing about optimisation and
nothing about safety policy, which means it can be swapped for a fake in tests
and for a different execution backend later.

Two properties are non-negotiable here:

1. **No shell.**  Commands are passed as argument vectors.  There is no
   ``shell=True`` anywhere in this package, so no value that reaches this layer
   can be reinterpreted as a shell metacharacter.
2. **Always bounded.**  Every launch has a timeout, so a wedged device cannot
   hang the tool forever.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol, Sequence

from ..config import DEFAULT_TIMEOUT, EXIT_TIMEOUT
from ..exceptions import AdbNotFoundError


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one process launch."""

    argv: tuple[str, ...]
    rc: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out

    @property
    def command(self) -> str:
        return " ".join(self.argv)


class Transport(Protocol):
    """Anything that can execute an argument vector."""

    def run(self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
        ...


def _looks_like_a_path(candidate: str) -> bool:
    return os.path.isabs(candidate) or os.sep in candidate or "/" in candidate


def _resolve_explicit(candidate: str) -> str:
    """Resolve a caller-supplied path or name, or raise."""
    if _looks_like_a_path(candidate):
        if not os.path.exists(candidate):
            raise AdbNotFoundError(f"adb binary not found at {candidate!r}")
        return candidate

    found = shutil.which(candidate)
    if not found:
        raise AdbNotFoundError(f"adb binary not found on PATH: {candidate!r}")
    return os.path.abspath(found)


def resolve_adb_binary(explicit: str | None = None) -> str:
    """Locate the ``adb`` executable.

    Resolution order:
      1. an explicit path (CLI flag or ``ADB_BINARY`` environment variable)
      2. the first ``adb`` on ``PATH``
      3. the bare name, letting the OS decide

    Resolving to a concrete path matters on Windows: ``subprocess`` with
    ``shell=False`` does not honour ``PATHEXT``, so a bare ``"adb"`` would skip
    an ``adb.cmd``/``adb.bat`` shim and could silently pick up a different
    ``adb.exe``.
    """
    candidate = explicit or os.environ.get("ADB_BINARY")
    if candidate:
        return _resolve_explicit(candidate)

    found = shutil.which("adb")
    return os.path.abspath(found) if found else "adb"


def _as_text(value: str | bytes | None) -> str:
    """Coerce a captured stream to text.

    ``subprocess.TimeoutExpired`` can hand back ``bytes`` even when the call
    asked for text, so the timeout path needs this; the normal path never does.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


class SubprocessTransport:
    """Executes argument vectors as real child processes."""

    def __init__(self, binary: str | None = None):
        self.binary = resolve_adb_binary(binary)

    def run(self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
        full = (self.binary, *argv)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                list(full),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
            return CommandResult(
                argv=tuple(full),
                rc=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration=time.perf_counter() - started,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                argv=tuple(full),
                rc=EXIT_TIMEOUT,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr) or f"timed out after {timeout:.1f}s",
                duration=time.perf_counter() - started,
                timed_out=True,
            )
        except FileNotFoundError as exc:
            raise AdbNotFoundError(str(exc)) from exc


class RecordingTransport:
    """Wraps a transport and records every argv it was asked to run.

    Used by the benchmark harness to count round-trips independently of the
    metrics collector, so the two can be cross-checked.
    """

    def __init__(self, inner: Transport):
        self.inner = inner
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
        self.calls.append(tuple(argv))
        return self.inner.run(argv, timeout=timeout)
