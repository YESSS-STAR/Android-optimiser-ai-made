"""Exception hierarchy.

Every failure mode this tool can hit has a dedicated type so that callers can
decide between "retry", "skip this action" and "abort the run" instead of
guessing from a string.  The legacy implementation collapsed all of these into
"return stderr text and carry on", which is why it silently reported success
for commands that had failed.
"""

from __future__ import annotations


class OptimiserError(Exception):
    """Base class for everything this package raises."""


class AdbNotFoundError(OptimiserError):
    """The ``adb`` binary could not be located."""


class NoDeviceError(OptimiserError):
    """No usable device is attached."""


class DeviceNotConfirmedError(OptimiserError):
    """The operator declined to confirm the target device."""


class DeviceCommandError(OptimiserError):
    """A device-side command exited non-zero."""

    def __init__(self, command: str, rc: int, stderr: str = ""):
        self.command = command
        self.rc = rc
        self.stderr = stderr
        super().__init__(f"command failed (rc={rc}): {command}: {stderr}".strip())


class CommandTimeoutError(OptimiserError):
    """A device command exceeded its timeout."""

    def __init__(self, command: str, timeout: float):
        self.command = command
        self.timeout = timeout
        super().__init__(f"command timed out after {timeout:.1f}s: {command}")


class UnsafePackageError(OptimiserError):
    """A package name failed strict validation.

    This is the gate that makes shell injection impossible: package names are
    validated against a strict grammar and anything else is refused outright
    rather than being interpolated into a command string.
    """

    def __init__(self, package: str):
        self.package = package
        super().__init__(f"refusing unsafe package identifier: {package!r}")


class UnsafeActionError(OptimiserError):
    """An action targets something the safety policy forbids."""
