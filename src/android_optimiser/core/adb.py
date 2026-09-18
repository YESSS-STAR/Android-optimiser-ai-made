"""The ADB client.

This is the only place in the package that talks to a device.  It exists to make
four things true that were not true before:

**Batching.**
    A single ``adb shell`` invocation can carry many device commands separated by
    ``;``.  Exit codes are recovered per command by echoing a sentinel after each
    one.  This turns N round-trips into 1, and N is usually the whole cost of a
    debloat run.

**Parallelism.**
    Independent batches are dispatched across a thread pool.  ADB handles
    concurrent shell sessions fine; the legacy script issued everything strictly
    one-at-a-time.

**Caching.**
    ``getprop`` is a full property dump the first time and a dict lookup
    afterwards.  The legacy script paid a fresh round-trip for every single
    property it wanted, one at a time.

**Validation.**
    Nothing reaches a command string without passing a strict grammar check.
    Combined with ``shell=False`` at the transport layer this makes command
    injection structurally impossible rather than merely unlikely.
"""

from __future__ import annotations

import re
import shlex
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..config import (
    BATCH_CHUNK_SIZE,
    BATCH_MARKER_PREFIX,
    BATCH_MARKER_SUFFIX,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    PACKAGE_NAME_RE,
    PARALLEL_WORKERS,
    RETRY_BACKOFF,
    SETTINGS_NAMESPACE,
    TRANSIENT_PATTERNS,
)
from ..exceptions import CommandTimeoutError, DeviceCommandError, NoDeviceError, UnsafePackageError
from .metrics import Invocation, Metrics
from .transport import CommandResult, SubprocessTransport, Transport

_MARKER_RE = re.compile(
    re.escape(BATCH_MARKER_PREFIX) + r"(\d+)=" + r"(-?\d+)" + re.escape(BATCH_MARKER_SUFFIX)
)

_SETTINGS_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_SETTINGS_VALUE_RE = re.compile(r"^[A-Za-z0-9_.\-]*$")


def validate_package(name: str) -> str:
    """Return ``name`` if it is a legal package identifier, else raise.

    This is the security boundary.  A package name arriving from a device is
    untrusted input; treating it as one is what makes the difference between
    "a tool that can be tricked into running arbitrary commands" and one that
    cannot.
    """
    if not isinstance(name, str) or not PACKAGE_NAME_RE.match(name):
        raise UnsafePackageError(str(name))
    return name


# --------------------------------------------------------------------------
# batching helpers
#
# These live at module level rather than as methods so that the methods read as
# a sequence of named steps.  The batching code carries the most intricate
# parsing in the package, and keeping each step separately testable is worth
# more than keeping it inside the class.
# --------------------------------------------------------------------------
def _clean_commands(commands: Iterable[str]) -> list[str]:
    """Drop blank entries; a blank command would break the sentinel indexing."""
    return [c for c in commands if c and c.strip()]


def _build_batch_script(commands: Sequence[str]) -> str:
    """Join commands with a sentinel echo after each one.

    The sentinel captures ``$?`` immediately after its command, which is the
    only way to recover per-command exit status from a single shell session.
    """
    parts: list[str] = []
    for index, command in enumerate(commands):
        parts.append(command)
        parts.append(f'echo "{BATCH_MARKER_PREFIX}{index}=$?{BATCH_MARKER_SUFFIX}"')
    return "; ".join(parts)


def _unattributable_failure(
    script: str, commands: Sequence[str], result: CommandResult
) -> BatchResult:
    """Build the result for a batch whose whole invocation died.

    No sentinel came back, so there is nothing to attribute: every command is
    reported as having failed with the transport's own exit code.
    """
    stderr = result.stderr.strip()
    return BatchResult(
        script=script,
        results=[
            ShellResult(command=c, rc=result.rc, stderr=stderr) for c in commands
        ],
        transport_ok=False,
        stderr=stderr,
    )


def _attribute_results(
    commands: Sequence[str],
    parsed: Sequence[tuple[int, str]],
    result: CommandResult,
) -> list[ShellResult]:
    """Turn parsed sentinel output into one ``ShellResult`` per command."""
    share = result.duration / len(commands)
    outcomes: list[ShellResult] = []
    for command, (rc, text) in zip(commands, parsed):
        body = text.strip()
        outcomes.append(
            ShellResult(
                command=command,
                rc=rc,
                stdout=body,
                stderr="" if rc == 0 else body,
                duration=share,
                shared_round_trip=True,
            )
        )
    return outcomes


def _merge_batches(batches: Sequence[BatchResult]) -> BatchResult:
    """Flatten concurrently-executed batches into one result."""
    merged: list[ShellResult] = []
    for batch in batches:
        merged.extend(batch.results)
    return BatchResult(
        script=" ;; ".join(b.script for b in batches),
        results=merged,
        transport_ok=all(b.transport_ok for b in batches),
        stderr="\n".join(b.stderr for b in batches if b.stderr),
    )


@dataclass
class ShellResult:
    """Outcome of one device-side command."""

    command: str
    rc: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    shared_round_trip: bool = False

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def raise_for_status(self) -> "ShellResult":
        if not self.ok:
            raise DeviceCommandError(self.command, self.rc, self.stderr)
        return self


@dataclass
class BatchResult:
    """Outcome of one batched shell invocation."""

    script: str
    results: list[ShellResult] = field(default_factory=list)
    transport_ok: bool = True
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.transport_ok and all(r.ok for r in self.results)

    def by_index(self, index: int) -> ShellResult:
        return self.results[index]


class AdbClient:
    """Batched, cached, thread-safe-ish ADB facade."""

    def __init__(
        self,
        transport: Transport | None = None,
        metrics: Metrics | None = None,
        *,
        serial: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        chunk_size: int = BATCH_CHUNK_SIZE,
        workers: int = PARALLEL_WORKERS,
    ):
        self.transport: Transport = transport or SubprocessTransport()
        self.metrics = metrics or Metrics()
        self.serial = serial
        self.timeout = timeout
        self.retries = retries
        self.chunk_size = max(1, chunk_size)
        self.workers = max(1, workers)

        self._props: dict[str, str] | None = None
        self._settings: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # low level
    # ------------------------------------------------------------------
    def _prefix(self) -> list[str]:
        return ["-s", self.serial] if self.serial else []

    def _invoke(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        batch_size: int = 1,
        label: str = "",
    ) -> CommandResult:
        """Run one adb process, with bounded retries on transient failure."""
        full = [*self._prefix(), *argv]
        timeout = self.timeout if timeout is None else timeout

        last: CommandResult | None = None
        for attempt in range(self.retries + 1):
            result = self.transport.run(full, timeout=timeout)
            self.metrics.record(
                Invocation(
                    argv=list(result.argv),
                    duration=result.duration,
                    ok=result.ok,
                    batch_size=batch_size,
                    label=label,
                )
            )
            if result.ok:
                return result

            last = result
            if result.timed_out:
                self.metrics.record_timeout()

            if attempt < self.retries and self._is_transient(result):
                self.metrics.record_retry()
                time.sleep(RETRY_BACKOFF * (2**attempt))
                continue
            break

        assert last is not None
        return last

    @staticmethod
    def _is_transient(result: CommandResult) -> bool:
        if result.timed_out:
            return True
        blob = f"{result.stdout}\n{result.stderr}".lower()
        return any(p in blob for p in TRANSIENT_PATTERNS)

    # ------------------------------------------------------------------
    # shell
    # ------------------------------------------------------------------
    def shell(self, command: str, *, timeout: float | None = None) -> ShellResult:
        """Run one device command as its own round-trip."""
        result = self._invoke(["shell", command], timeout=timeout, batch_size=1)
        return ShellResult(
            command=command,
            rc=result.rc,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            duration=result.duration,
        )

    def shell_batch(
        self, commands: Sequence[str], *, timeout: float | None = None
    ) -> BatchResult:
        """Run several device commands inside ONE ``adb shell`` invocation.

        Per-command exit codes are recovered from sentinel markers, so callers
        still learn exactly which command failed and why.
        """
        commands = _clean_commands(commands)
        if not commands:
            return BatchResult(script="", results=[])

        if len(commands) == 1:
            single = self.shell(commands[0], timeout=timeout)
            return BatchResult(script=commands[0], results=[single])

        script = _build_batch_script(commands)
        budget = timeout if timeout is not None else self.timeout + 5.0 * len(commands)
        result = self._invoke(
            ["shell", script], timeout=budget, batch_size=len(commands), label="batch"
        )

        if not result.ok and not _MARKER_RE.search(result.stdout):
            return _unattributable_failure(script, commands, result)

        parsed = _split_batch_output(result.stdout, len(commands))
        return BatchResult(
            script=script,
            results=_attribute_results(commands, parsed, result),
            stderr=result.stderr.strip(),
        )

    def shell_batch_parallel(
        self,
        commands: Sequence[str],
        *,
        chunk_size: int | None = None,
        workers: int | None = None,
        timeout: float | None = None,
    ) -> BatchResult:
        """Chunk commands, batch each chunk, run chunks concurrently.

        Combines both levers: round-trips drop by the chunk factor and the
        remaining chunks overlap.
        """
        commands = _clean_commands(commands)
        if not commands:
            return BatchResult(script="", results=[])

        size = chunk_size or self.chunk_size
        worker_count = min(workers or self.workers, len(commands))
        chunks = [commands[i : i + size] for i in range(0, len(commands), size)]

        if len(chunks) == 1:
            return self.shell_batch(chunks[0], timeout=timeout)

        return _merge_batches(self._dispatch(chunks, worker_count, timeout))

    def _dispatch(
        self, chunks: Sequence[Sequence[str]], worker_count: int, timeout: float | None
    ) -> list[BatchResult]:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [
                pool.submit(self.shell_batch, chunk, timeout=timeout)
                for chunk in chunks
            ]
            return [future.result() for future in futures]

    # ------------------------------------------------------------------
    # device discovery
    # ------------------------------------------------------------------
    def devices(self) -> list[str]:
        result = self._invoke(["devices"], label="devices")
        if result.timed_out:
            # Distinct from "no device": a wedged device is attached and
            # authorised, and telling the operator to check the USB cable would
            # send them down the wrong path entirely.
            raise CommandTimeoutError("adb devices", self.timeout)
        if not result.ok:
            raise NoDeviceError(result.stderr.strip() or "adb devices failed")
        found: list[str] = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                found.append(parts[0])
        return found

    def require_device(self) -> str:
        found = self.devices()
        if not found:
            raise NoDeviceError("no authorised device attached")
        if self.serial and self.serial not in found:
            raise NoDeviceError(f"device {self.serial!r} is not attached")
        self.serial = self.serial or found[0]
        return self.serial

    # ------------------------------------------------------------------
    # cached views
    # ------------------------------------------------------------------
    def props(self, refresh: bool = False) -> dict[str, str]:
        """Full property table, fetched once and cached.

        One round-trip replaces the legacy script's one-round-trip-per-property.
        """
        if self._props is not None and not refresh:
            self.metrics.record_cache(True)
            return self._props
        self.metrics.record_cache(False)
        result = self.shell("getprop")
        parsed = parse_props(result.stdout)
        if parsed:
            self._props = parsed
        return parsed

    def prime_props(self, raw: str) -> dict[str, str]:
        """Populate the property cache from output already fetched in a batch."""
        parsed = parse_props(raw)
        if parsed:
            self._props = parsed
        return parsed

    def get_prop(self, key: str, default: str = "") -> str:
        return self.props().get(key, default)

    def global_settings(self, refresh: bool = False) -> dict[str, str]:
        """The whole ``global`` settings namespace, fetched once and cached."""
        if self._settings is not None and not refresh:
            self.metrics.record_cache(True)
            return self._settings
        self.metrics.record_cache(False)
        result = self.shell(f"settings list {SETTINGS_NAMESPACE}")
        parsed = parse_settings(result.stdout)
        if parsed:
            self._settings = parsed
        return parsed

    def prime_settings(self, raw: str) -> dict[str, str]:
        """Populate the settings cache from output already fetched in a batch."""
        parsed = parse_settings(raw)
        if parsed:
            self._settings = parsed
        return parsed

    def get_setting(self, key: str, default: str = "") -> str:
        return self.global_settings().get(key, default)

    def invalidate(self) -> None:
        self._props = None
        self._settings = None

    # ------------------------------------------------------------------
    # convenience wrappers that keep validation in one place
    # ------------------------------------------------------------------
    def disable_package(self, package: str) -> str:
        return f"pm disable-user --user 0 {shlex.quote(validate_package(package))}"

    def enable_package(self, package: str) -> str:
        return f"pm enable {shlex.quote(validate_package(package))}"

    def put_setting(self, key: str, value: str) -> str:
        if not _SETTINGS_KEY_RE.match(key):
            raise ValueError(f"illegal settings key: {key!r}")
        if not _SETTINGS_VALUE_RE.match(str(value)):
            raise ValueError(f"illegal settings value: {value!r}")
        return f"settings put {SETTINGS_NAMESPACE} {key} {value}"


_PROP_LINE_RE = re.compile(r"^\[(?P<key>[^\]]+)\]:\s*\[(?P<value>.*)\]$")


def parse_props(raw: str) -> dict[str, str]:
    """Parse the ``[key]: [value]`` format that ``getprop`` emits."""
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        match = _PROP_LINE_RE.match(line.strip())
        if match:
            parsed[match.group("key")] = match.group("value")
    return parsed


def parse_settings(raw: str) -> dict[str, str]:
    """Parse the ``key=value`` format that ``settings list`` emits."""
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            parsed[key.strip()] = value.strip()
    return parsed


def _split_batch_output(stdout: str, count: int) -> list[tuple[int, str]]:
    """Attribute batched stdout back to individual commands.

    Returns ``count`` ``(rc, output)`` pairs.  Any command whose sentinel never
    appeared is reported as a failure rather than silently counted as a success.
    """
    slots: list[tuple[int, str] | None] = [None] * count
    cursor = 0
    for match in _MARKER_RE.finditer(stdout):
        index = int(match.group(1))
        rc = int(match.group(2))
        if 0 <= index < count:
            chunk = stdout[cursor : match.start()]
            slots[index] = (rc, chunk.lstrip("\n").rstrip("\n"))
        cursor = match.end()

    return [slot if slot is not None else (-1, "") for slot in slots]


def commands_to_run(iterable: Iterable[str]) -> list[str]:
    return [c for c in iterable if c]
