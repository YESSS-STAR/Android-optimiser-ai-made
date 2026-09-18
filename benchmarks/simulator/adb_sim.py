#!/usr/bin/env python3
"""A simulated ``adb`` executable.

Usage: put this directory on ``PATH`` (the ``adb.cmd`` / ``adb`` shims do that)
and any tool that shells out to ``adb`` will talk to a deterministic fake
handset instead of a real one.

Environment variables
---------------------
``FAKE_ADB_STATE``
    Path to the JSON file holding device state.  Deleted => factory reset.
``FAKE_ADB_LATENCY_MS``
    Artificial delay per invocation, modelling USB/ADB round-trip cost.
``FAKE_ADB_CALLS``
    Path to a newline-delimited JSON call log (one record per invocation).
``FAKE_ADB_FAIL_PATTERNS``
    Comma-separated substrings; any device command containing one of them exits
    non-zero.  Used to measure how well each implementation detects failures.
``FAKE_ADB_SERIAL``
    Override the reported device serial.
"""

from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import device_state as ds  # noqa: E402

# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------


def _state_path() -> str:
    return os.environ.get("FAKE_ADB_STATE") or os.path.join(_HERE, ".device_state.json")


def _latency() -> float:
    try:
        return float(os.environ.get("FAKE_ADB_LATENCY_MS", "0")) / 1000.0
    except ValueError:
        return 0.0


def _log_call(cmd: str, argv: list[str]) -> None:
    path = os.environ.get("FAKE_ADB_CALLS")
    if not path:
        return
    record = json.dumps(
        {"t": time.time(), "cmd": cmd, "argv": argv}, separators=(",", ":")
    )
    # Windows implements O_APPEND as seek-to-end followed by write, which is not
    # atomic across processes: parallel adb calls would interleave fragments and
    # corrupt the log.  Serialise the append explicitly.
    with _Lock(path + ".loglock", timeout=15.0):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, (record + "\n").encode("utf-8"))
        finally:
            os.close(fd)


class _Lock:
    """Cross-process mutex so parallel adb calls cannot corrupt state.

    Locks are held for microseconds, so anything older than ``stale_after``
    seconds was left behind by a process that died mid-write.  Breaking those
    immediately matters: without it, one crashed run leaves a lock directory
    behind and every subsequent invocation stalls until the acquire timeout.
    """

    STALE_AFTER = 5.0

    def __init__(self, target: str, timeout: float = 20.0):
        self._dir = target + ".lock"
        self._timeout = timeout

    def __enter__(self):
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                os.mkdir(self._dir)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self._dir) > self.STALE_AFTER:
                        os.rmdir(self._dir)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    try:
                        os.rmdir(self._dir)
                    except OSError:
                        pass
                    deadline = time.monotonic() + 1.0
                time.sleep(0.001)

    def __exit__(self, *exc):
        try:
            os.rmdir(self._dir)
        except OSError:
            pass
        return False


def _fail_patterns() -> list[str]:
    raw = os.environ.get("FAKE_ADB_FAIL_PATTERNS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


# --------------------------------------------------------------------------
# device command parsing
# --------------------------------------------------------------------------


def split_commands(s: str) -> list[str]:
    """Split a device shell string on top-level ``;``, honouring quotes."""
    parts: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ";":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def tokenize(s: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch.isspace():
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


# --------------------------------------------------------------------------
# device command implementations
# --------------------------------------------------------------------------


class Device:
    def __init__(self, state: dict, state_path: str):
        self.state = state
        self.path = state_path
        self._dirty = False

    def flush(self):
        """Persist state.  Only valid while the caller holds the device lock."""
        if self._dirty:
            ds.save(self.state, self.path)
            self._dirty = False

    # -- helpers ---------------------------------------------------------
    def _pkg(self, name: str) -> dict | None:
        for p in self.state["packages"]:
            if p["name"] == name:
                return p
        return None

    # -- getprop / setprop ----------------------------------------------
    def getprop(self, key: str | None) -> str:
        if key:
            return self.state["props"].get(key, "")
        return "\n".join(
            f"[{k}]: [{v}]" for k, v in sorted(self.state["props"].items())
        )

    def setprop(self, key: str, value: str) -> str:
        self.state["props"][key] = value
        self._dirty = True
        return ""

    # -- pm --------------------------------------------------------------
    def pm_list_packages(self, flags: list[str]) -> tuple[str, int]:
        want_system = "-s" in flags
        want_third = "-3" in flags
        want_disabled = "-d" in flags
        want_enabled = "-e" in flags
        show_path = "-f" in flags
        show_installer = "-i" in flags

        rows = []
        for p in self.state["packages"]:
            if want_system and not want_third and p["third_party"]:
                continue
            if want_third and not want_system and not p["third_party"]:
                continue
            if want_system and want_third:
                continue
            if want_disabled and p["enabled"]:
                continue
            if want_enabled and not p["enabled"]:
                continue
            rows.append(p)

        rows.sort(key=lambda r: r["name"])
        lines = []
        for r in rows:
            if show_path:
                line = f"package:{r['path']}={r['name']}"
            else:
                line = f"package:{r['name']}"
            if show_installer:
                line += f" installer={r.get('installer') or 'null'}"
            lines.append(line)
        return "\n".join(lines), 0

    def pm_disable(self, target: str) -> tuple[str, int]:
        p = self._pkg(target)
        if p is None:
            return f"Error: package {target} not found", 1
        p["enabled"] = False
        self._dirty = True
        return f"Package {target} new state: disabled-user", 0

    def pm_enable(self, target: str) -> tuple[str, int]:
        p = self._pkg(target)
        if p is None:
            return f"Error: package {target} not found", 1
        p["enabled"] = True
        self._dirty = True
        return f"Package {target} new state: enabled", 0

    def pm_trim_caches(self, size: str) -> tuple[str, int]:
        self.state["cache_trimmed"] += 1
        self._dirty = True
        return "Success", 0

    def pm_clear(self, target: str) -> tuple[str, int]:
        if self._pkg(target) is None:
            return f"Failed", 1
        return "Success", 0

    # -- settings --------------------------------------------------------
    def settings_get(self, ns: str, key: str) -> tuple[str, int]:
        val = self.state["settings"].get(ns, {}).get(key)
        return ("null" if val is None else val), 0

    def settings_put(self, ns: str, key: str, value: str) -> tuple[str, int]:
        self.state["settings"].setdefault(ns, {})[key] = value
        self._dirty = True
        return "", 0

    def settings_list(self, ns: str) -> tuple[str, int]:
        out = "\n".join(
            f"{k}={v}" for k, v in sorted(self.state["settings"].get(ns, {}).items())
        )
        return out, 0

    # -- dumpsys ---------------------------------------------------------
    def dumpsys(self, args: list[str]) -> tuple[str, int]:
        topic = args[0] if args else ""
        if topic == "deviceidle":
            if "force-idle" in args:
                self.state["doze"] = "idle"
                self._dirty = True
                return "Now forced in to deep idle mode", 0
            if "unforce" in args or "step" in args:
                self.state["doze"] = "active"
                self._dirty = True
                return "Now forced in to normal mode", 0
            return (
                "  mEnabled=true\n"
                f"  mState={self.state['doze']}\n"
                "  mForceIdle=true\n"
                "  mScreenOn=false\n"
                "  mCharging=false\n",
                0,
            )
        if topic == "SurfaceFlinger":
            # 16.67 ms at 60 Hz, expressed in nanoseconds, then 128 frame rows.
            lines = ["16666666"]
            base = 1_000_000_000_000
            for i in range(128):
                a = base + i * 16_666_666
                lines.append(f"{a}\t{a + 2_000_000}\t{a + 16_000_000}")
            return "\n".join(lines), 0
        if topic == "meminfo":
            return (
                "Total RAM: 7,845,120K\n"
                " Free RAM: 2,101,336K\n"
                " Used RAM: 4,556,784K\n"
                " Lost RAM:   186,996K\n",
                0,
            )
        if topic == "battery":
            return "  level: 78\n  status: 3\n  plugged: 0\n  temperature: 291\n", 0
        if topic == "package":
            return "  Package [%s] (flags=0x0)" % (args[1] if len(args) > 1 else ""), 0
        return f"Unknown dumpsys topic: {topic}", 1

    # -- misc ------------------------------------------------------------
    def logcat_clear(self) -> tuple[str, int]:
        self.state["logcat_cleared"] += 1
        self._dirty = True
        return "", 0

    def force_stop(self, target: str) -> tuple[str, int]:
        self.state["forced_stops"].append(target)
        self._dirty = True
        return "", 0


# --------------------------------------------------------------------------
# command dispatch
# --------------------------------------------------------------------------


def strip_global_options(argv: list[str]) -> list[str]:
    """Drop adb's global flags, returning ``[subcommand, *args]``.

    Handles ``-s SERIAL``, ``-d``, ``-e``, and the value-taking ``-H``/``-P``/
    ``-L``/``-t`` options that appear before the subcommand.
    """
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-s" and i + 1 < len(argv):
            i += 2
        elif a in ("-d", "-e"):
            i += 1
        elif a in ("-H", "-P", "-L", "-t") and i + 1 < len(argv):
            i += 2
        else:
            break
    return argv[i:]


def run_device_command(dev: Device, cmd: str, last_rc: int) -> tuple[str, int]:
    toks = [t.replace("$?", str(last_rc)) for t in tokenize(cmd)]
    if not toks:
        return "", 0

    head = toks[0]

    if head == "getprop":
        return dev.getprop(toks[1] if len(toks) > 1 else None), 0
    if head == "setprop":
        return dev.setprop(toks[1], toks[2] if len(toks) > 2 else ""), 0

    if head == "pm":
        sub = toks[1] if len(toks) > 1 else ""
        rest = [t for t in toks[2:] if t != "--user" and t != "0"]
        if sub == "list":
            return dev.pm_list_packages(rest)
        if sub in ("disable-user", "disable"):
            return dev.pm_disable(rest[-1]) if rest else ("Error: no package", 1)
        if sub == "enable":
            return dev.pm_enable(rest[-1]) if rest else ("Error: no package", 1)
        if sub == "trim-caches":
            return dev.pm_trim_caches(rest[0] if rest else "0")
        if sub == "clear":
            return dev.pm_clear(rest[-1]) if rest else ("Error: no package", 1)
        if sub == "uninstall":
            return dev.pm_disable(rest[-1]) if rest else ("Error: no package", 1)
        return f"Unknown pm subcommand: {sub}", 1

    if head == "settings":
        sub = toks[1] if len(toks) > 1 else ""
        if sub == "get" and len(toks) >= 4:
            return dev.settings_get(toks[2], toks[3])
        if sub == "put" and len(toks) >= 5:
            return dev.settings_put(toks[2], toks[3], toks[4])
        if sub == "list" and len(toks) >= 3:
            return dev.settings_list(toks[2])
        return "Unknown settings usage", 1

    if head == "dumpsys":
        return dev.dumpsys(toks[1:])

    if head == "logcat":
        if "-c" in toks:
            return dev.logcat_clear()
        return "", 0

    if head == "am":
        if len(toks) > 1 and toks[1] == "force-stop" and len(toks) > 2:
            return dev.force_stop(toks[2])
        return "", 0

    if head == "cmd":
        if len(toks) > 1 and toks[1] == "package":
            if "compile" in toks:
                return "Success", 0
            if "list" in toks:
                return dev.pm_list_packages([t for t in toks[2:] if t.startswith("-")])
        return "", 0

    if head == "echo":
        return " ".join(toks[1:]), 0

    if head in ("id", "whoami"):
        return "shell", 0

    if head in ("true", ":"):
        return "", 0

    return f"/system/bin/sh: {head}: inaccessible or not found", 127


def main(argv: list[str]) -> int:
    delay = _latency()
    if delay:
        time.sleep(delay)

    _log_call("adb", argv)

    rest = strip_global_options(list(argv))
    serial_override = None
    for index, token in enumerate(argv):
        if token == "-s" and index + 1 < len(argv):
            serial_override = argv[index + 1]
            break

    if not rest:
        sys.stderr.write("Android Debug Bridge version 1.0.41\n")
        return 1

    sub = rest[0]
    args = rest[1:]
    state_path = _state_path()

    if sub in ("start-server", "kill-server", "wait-for-device", "reconnect"):
        return 0
    if sub == "version":
        sys.stdout.write("Android Debug Bridge version 1.0.41\n")
        return 0

    if sub == "devices":
        state = ds.load(state_path)
        serial = serial_override or os.environ.get("FAKE_ADB_SERIAL") or state["serial"]
        if state.get("online", True):
            sys.stdout.write(
                "List of devices attached\n" + serial + "\tdevice\n\n"
            )
        else:
            sys.stdout.write("List of devices attached\n" + serial + "\toffline\n\n")
        return 0

    if sub == "get-state":
        state = ds.load(state_path)
        sys.stdout.write("device\n" if state.get("online", True) else "offline\n")
        return 0

    if sub == "get-serialno":
        state = ds.load(state_path)
        sys.stdout.write(serial_override or state["serial"])
        return 0

    if sub != "shell":
        sys.stderr.write(f"adb: unknown command {sub}\n")
        return 1

    device_cmd = " ".join(args)
    if not device_cmd.strip():
        return 0

    patterns = _fail_patterns()
    if any(p in device_cmd for p in patterns):
        sys.stderr.write(f"adb: device offline (injected fault)\n")
        return 1

    out: list[str] = []
    rc = 0
    # The whole read-modify-write cycle must be atomic.  Holding the lock only
    # around the write is not enough: two parallel adb calls would each load the
    # same starting state, apply their own change, and the second write would
    # silently discard the first one's work.
    with _Lock(state_path):
        state = ds.load(state_path)
        dev = Device(state, state_path)
        for piece in split_commands(device_cmd):
            text, rc = run_device_command(dev, piece, rc)
            if text:
                out.append(text)
        dev.flush()
    if out:
        sys.stdout.write("\n".join(out) + "\n")
    if rc != 0:
        sys.stderr.write(f"adb: shell command failed (rc={rc})\n")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:
        sys.exit(0)
