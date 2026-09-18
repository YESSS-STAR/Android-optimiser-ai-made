"""Tunables and invariants.

Everything that used to be a magic string or a magic number buried inside a
function lives here, so behaviour can be reasoned about (and tested) in one
place instead of by reading the whole program.
"""

from __future__ import annotations

import re

APP_NAME = "android-optimiser"
VERSION = "2.0.0"

# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
RETRY_BACKOFF = 0.15

#: Exit code reported when a device command exceeds its timeout.  Matches the
#: shell convention for a command killed by a timeout.
EXIT_TIMEOUT = 124

#: Substrings that indicate a transient transport problem worth retrying.
TRANSIENT_PATTERNS = (
    "device offline",
    "device not found",
    "device still connecting",
    "closed",
    "transport error",
    "protocol fault",
    "connection reset",
)

# --------------------------------------------------------------------------
# Command batching / concurrency
#
# On real hardware a single `adb` round-trip costs roughly 15-40 ms over USB and
# considerably more over wireless ADB.  The dominant cost of any debloat run is
# therefore the NUMBER of round-trips, not the work done on the device.  These
# two knobs attack exactly that.
# --------------------------------------------------------------------------
BATCH_CHUNK_SIZE = 8
PARALLEL_WORKERS = 4
MAX_PARALLEL_WORKERS = 16

#: Sentinel used to recover per-command exit codes from a batched shell call.
BATCH_MARKER_PREFIX = "###AO_"
BATCH_MARKER_SUFFIX = "_###"

# --------------------------------------------------------------------------
# Package identifier grammar
#
# This regex is the security boundary.  Only identifiers matching it are ever
# placed into a device command; everything else raises UnsafePackageError.
# It deliberately forbids whitespace, quotes, semicolons, backticks, dollar
# signs, ampersands, pipes and slashes.
# --------------------------------------------------------------------------
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")

# --------------------------------------------------------------------------
# Android settings this tool is permitted to touch.
# --------------------------------------------------------------------------
ANIMATION_KEYS = (
    "window_animation_scale",
    "transition_animation_scale",
    "animator_duration_scale",
)

#: Settings namespaces the tool will read or write.
SETTINGS_NAMESPACE = "global"

#: Value used to disable animation entirely.
ANIMATION_OFF = "0.0"

#: Default cache-trim target passed to `pm trim-caches`.
CACHE_TRIM_TARGET = "999G"

# --------------------------------------------------------------------------
# Inspection commands.
#
# Each entry is one device command.  They are issued as a single batched shell
# invocation, so the whole inspection costs one round-trip regardless of how
# many entries there are.
# --------------------------------------------------------------------------
INSPECT_SYSTEM_PACKAGES = "pm list packages -f -s"
INSPECT_THIRD_PARTY_PACKAGES = "pm list packages -f -3 -i"
INSPECT_DISABLED_PACKAGES = "pm list packages -d"
INSPECT_SETTINGS = f"settings list {SETTINGS_NAMESPACE}"
INSPECT_DOZE = "dumpsys deviceidle"

# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
REPORT_WIDTH = 72
