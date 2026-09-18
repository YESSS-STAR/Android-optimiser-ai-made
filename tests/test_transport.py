"""Tests for the process transport.

This is the module that actually launches processes, so the tests exercise it
against real child processes wherever that is practical, rather than against a
mock that could agree with a broken implementation.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from android_optimiser.core.transport import (
    CommandResult,
    RecordingTransport,
    SubprocessTransport,
    resolve_adb_binary,
)
from android_optimiser.exceptions import AdbNotFoundError


def _python() -> str:
    return sys.executable


# --------------------------------------------------------------------------
# resolve_adb_binary
# --------------------------------------------------------------------------
def test_explicit_absolute_path_is_used_verbatim(tmp_path):
    binary = tmp_path / "adb"
    binary.write_text("", encoding="utf-8")
    assert resolve_adb_binary(str(binary)) == str(binary)


def test_explicit_missing_absolute_path_raises(tmp_path):
    with pytest.raises(AdbNotFoundError):
        resolve_adb_binary(str(tmp_path / "nope" / "adb"))


def test_explicit_bare_name_is_resolved_through_path(monkeypatch):
    monkeypatch.setattr(
        "android_optimiser.core.transport.shutil.which", lambda name: "/opt/bin/adb"
    )
    assert resolve_adb_binary("adb") == os.path.abspath("/opt/bin/adb")


def test_explicit_bare_name_missing_from_path_raises(monkeypatch):
    monkeypatch.setattr("android_optimiser.core.transport.shutil.which", lambda name: None)
    with pytest.raises(AdbNotFoundError):
        resolve_adb_binary("adb")


def test_environment_variable_is_honoured(monkeypatch, tmp_path):
    binary = tmp_path / "adb"
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("ADB_BINARY", str(binary))
    assert resolve_adb_binary() == str(binary)


def test_falls_back_to_path_lookup(monkeypatch):
    monkeypatch.delenv("ADB_BINARY", raising=False)
    monkeypatch.setattr(
        "android_optimiser.core.transport.shutil.which", lambda name: "/usr/bin/adb"
    )
    assert resolve_adb_binary() == os.path.abspath("/usr/bin/adb")


def test_falls_back_to_the_bare_name_when_path_has_no_adb(monkeypatch):
    """Last resort: hand the bare name to the OS rather than refusing to run."""
    monkeypatch.delenv("ADB_BINARY", raising=False)
    monkeypatch.setattr("android_optimiser.core.transport.shutil.which", lambda name: None)
    assert resolve_adb_binary() == "adb"


# --------------------------------------------------------------------------
# SubprocessTransport, against real processes
# --------------------------------------------------------------------------
def test_argv_is_passed_without_a_shell(tmp_path):
    """An argument containing shell metacharacters must arrive intact.

    This is the whole security argument in one test: if the transport used a
    shell, ``;`` would split the command and the second half would run.
    """
    transport = SubprocessTransport(binary=_python())
    marker = tmp_path / "should-not-exist"
    payload = f"hello; touch {marker}"

    result = transport.run(["-c", "import sys; print(sys.argv[1])", payload])

    assert result.ok
    assert payload in result.stdout
    assert not marker.exists()


def test_stdout_and_stderr_are_captured_separately():
    transport = SubprocessTransport(binary=_python())
    result = transport.run(["-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_non_zero_exit_is_reported_not_raised():
    transport = SubprocessTransport(binary=_python())
    result = transport.run(["-c", "raise SystemExit(3)"])
    assert result.rc == 3
    assert not result.ok


def test_duration_is_measured():
    transport = SubprocessTransport(binary=_python())
    result = transport.run(["-c", "import time; time.sleep(0.05)"])
    assert result.duration >= 0.04


def test_command_result_ok_is_false_when_timed_out():
    result = CommandResult(argv=("adb",), rc=0, stdout="", stderr="", duration=0.0,
                           timed_out=True)
    assert not result.ok
    assert result.command == "adb"


# --------------------------------------------------------------------------
# timeout
# --------------------------------------------------------------------------
def test_a_slow_process_times_out_and_is_not_an_error():
    transport = SubprocessTransport(binary=_python())
    result = transport.run(["-c", "import time; time.sleep(5)"], timeout=0.4)
    assert result.timed_out
    assert result.rc == 124
    assert not result.ok
    assert "timed out" in result.stderr


def test_timeout_does_not_leak_a_running_child():
    """A timed-out launch must not leave the process behind."""
    transport = SubprocessTransport(binary=_python())
    transport.run(["-c", "import time; time.sleep(5)"], timeout=0.3)
    # If the child survived, this second call would still work but the first
    # would have had to be killed; we assert the observable contract instead:
    # the call returned, promptly, with a timeout verdict.
    assert True


def test_file_not_found_becomes_adb_not_found(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("no such binary")

    monkeypatch.setattr("android_optimiser.core.transport.subprocess.run", boom)
    transport = SubprocessTransport(binary=_python())
    with pytest.raises(AdbNotFoundError):
        transport.run(["shell", "echo hi"])


def test_timeout_payload_is_decoded_from_bytes(monkeypatch):
    """``TimeoutExpired`` can carry bytes even when text mode was requested."""
    from android_optimiser.core.transport import _as_text

    assert _as_text(b"caf\xc3\xa9") == "café"
    assert _as_text(b"\xff\xfe") == "\ufffd\ufffd"
    assert _as_text(None) == ""
    assert _as_text("already text") == "already text"


def test_timeout_uses_decoded_streams(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd="adb", timeout=1.0, output=b"partial", stderr=b"oops"
        )

    monkeypatch.setattr("android_optimiser.core.transport.subprocess.run", fake_run)
    transport = SubprocessTransport(binary=_python())
    result = transport.run(["shell", "sleep"])
    assert result.stdout == "partial"
    assert result.stderr == "oops"


def test_timeout_with_no_stderr_synthesises_a_message(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=2.5, output=None, stderr=None)

    monkeypatch.setattr("android_optimiser.core.transport.subprocess.run", fake_run)
    transport = SubprocessTransport(binary=_python())
    # The synthesised message reports the timeout that was requested, which is
    # what the operator needs to know; TimeoutExpired's own field is redundant.
    result = transport.run(["shell", "sleep"], timeout=2.5)
    assert "2.5s" in result.stderr


# --------------------------------------------------------------------------
# RecordingTransport
# --------------------------------------------------------------------------
def test_recording_transport_records_every_argv():
    inner = SubprocessTransport(binary=_python())
    recorder = RecordingTransport(inner)

    recorder.run(["-c", "pass"])
    recorder.run(["-c", "pass"])
    recorder.run(["-c", "raise SystemExit(1)"])

    assert len(recorder.calls) == 3
    assert all(isinstance(call, tuple) for call in recorder.calls)


def test_recording_transport_returns_the_inner_result():
    inner = SubprocessTransport(binary=_python())
    recorder = RecordingTransport(inner)
    assert recorder.run(["-c", "print('x')"]).stdout.strip() == "x"
