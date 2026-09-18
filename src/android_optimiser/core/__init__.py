"""Transport and device primitives.  Knows nothing about optimisation."""

from .adb import AdbClient, BatchResult, ShellResult, validate_package
from .device import DeviceInfo, DeviceManager
from .metrics import Invocation, Metrics
from .transport import CommandResult, SubprocessTransport, Transport, resolve_adb_binary

__all__ = [
    "AdbClient",
    "BatchResult",
    "ShellResult",
    "validate_package",
    "DeviceInfo",
    "DeviceManager",
    "Invocation",
    "Metrics",
    "CommandResult",
    "SubprocessTransport",
    "Transport",
    "resolve_adb_binary",
]
