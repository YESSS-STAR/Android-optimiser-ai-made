"""Tests for the ADB client: batching, caching, retries, validation."""

from __future__ import annotations

import pytest

from android_optimiser.core.adb import AdbClient, parse_props, parse_settings, validate_package
from android_optimiser.core.metrics import Metrics
from android_optimiser.core.transport import CommandResult
from android_optimiser.exceptions import (
    CommandTimeoutError,
    NoDeviceError,
    UnsafePackageError,
)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "com.android.systemui",
        "com.facebook.katana",
        "org.thoughtcrime.securesms",
        "com.Slack",
        "a.b",
        "com.example_app.v2",
    ],
)
def test_validate_package_accepts_legal_identifiers(name):
    assert validate_package(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "com.evil; rm -rf /",
        "com.evil && echo pwned",
        "com.evil|cat /etc/passwd",
        "com.evil`whoami`",
        "com.evil$(whoami)",
        "com.evil\ncom.other",
        "com.evil app",
        "'com.evil'",
        '"com.evil"',
        "../../etc/passwd",
        "com.evil>out",
        "com.evil<in",
        "com.evil&",
        "-flag",
        ".leading",
        "trailing.",
        None,
        123,
    ],
)
def test_validate_package_rejects_everything_else(name):
    with pytest.raises(UnsafePackageError):
        validate_package(name)


# --------------------------------------------------------------------------
# batching
# --------------------------------------------------------------------------
def test_batch_costs_one_round_trip(client, fake):
    fake.calls.clear()
    batch = client.shell_batch(["getprop ro.product.model", "getprop ro.product.brand"])
    assert len(fake.calls) == 1
    assert batch.ok
    assert len(batch.results) == 2


def test_batch_attributes_output_per_command(client):
    batch = client.shell_batch(["getprop ro.product.model", "getprop ro.product.brand"])
    assert batch.by_index(0).stdout.strip() == "SM-G998B"
    assert batch.by_index(1).stdout.strip() == "samsung"


def test_batch_recovers_individual_exit_codes(client):
    batch = client.shell_batch(
        ["getprop ro.product.model", "pm disable-user --user 0 com.does.not.exist"]
    )
    assert batch.by_index(0).rc == 0
    assert batch.by_index(1).rc != 0
    assert not batch.ok


def test_missing_sentinel_is_reported_as_failure(client, monkeypatch):
    """A command whose marker never appeared must not be counted as a success."""
    from android_optimiser.core import adb as adb_module

    def fake_run(argv, timeout=None):
        from android_optimiser.core.transport import CommandResult

        return CommandResult(
            argv=tuple(argv), rc=0, stdout="partial output\n", stderr="", duration=0.0
        )

    monkeypatch.setattr(client.transport, "run", fake_run)
    batch = client.shell_batch(["cmd-a", "cmd-b"])
    assert all(r.rc == -1 for r in batch.results)
    assert not batch.ok


def test_batch_empty_input_makes_no_calls(client, fake):
    fake.calls.clear()
    batch = client.shell_batch([])
    assert fake.calls == []
    assert batch.results == []


def test_single_command_batch_does_not_add_markers(client, fake):
    fake.calls.clear()
    client.shell_batch(["getprop ro.product.model"])
    assert len(fake.calls) == 1
    assert "###AO_" not in " ".join(fake.calls[0])


def test_parallel_batch_chunks_and_merges_in_order(client, fake):
    commands = [f"getprop ro.product.model" for _ in range(10)]
    fake.calls.clear()
    batch = client.shell_batch_parallel(commands, chunk_size=4, workers=4)
    # ceil(10/4) == 3 chunks == 3 round-trips
    assert len(fake.calls) == 3
    assert len(batch.results) == 10
    assert [r.command for r in batch.results] == commands


def test_parallel_batch_preserves_order_for_distinct_commands(client):
    commands = [
        "getprop ro.product.model",
        "getprop ro.product.brand",
        "getprop ro.build.version.release",
        "getprop ro.product.device",
        "getprop ro.build.version.sdk",
    ]
    batch = client.shell_batch_parallel(commands, chunk_size=2, workers=4)
    assert [r.stdout.strip() for r in batch.results] == [
        "SM-G998B", "samsung", "13", "o1s", "33",
    ]


# --------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------
def test_props_fetched_once_then_cached(client, fake):
    fake.calls.clear()
    first = client.props()
    after_first = len(fake.calls)
    second = client.props()

    assert first == second
    assert after_first == 1
    assert len(fake.calls) == 1, "second props() must not touch the device"
    assert client.metrics.cache_hits == 1


def test_get_prop_is_free_after_the_dump(client, fake):
    client.props()
    fake.calls.clear()
    for key in ("ro.product.model", "ro.product.brand", "ro.build.version.sdk"):
        client.get_prop(key)
    assert fake.calls == []
    assert client.get_prop("ro.product.model") == "SM-G998B"


def test_settings_fetched_once_then_cached(client, fake):
    fake.calls.clear()
    client.global_settings()
    assert len(fake.calls) == 1
    client.global_settings()
    assert len(fake.calls) == 1


def test_put_setting_validates_key_and_value(client):
    assert "window_animation_scale" in client.put_setting("window_animation_scale", "0.5")
    with pytest.raises(ValueError):
        client.put_setting("bad key; rm", "0.5")
    with pytest.raises(ValueError):
        client.put_setting("good_key", "0.5; rm -rf /")


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def test_devices_lists_attached_serial(client):
    assert client.devices() == ["R58NA0ABCDE"]


def test_require_device_raises_when_none_attached(fake):
    fake.state["online"] = False
    client = AdbClient(transport=fake, metrics=Metrics())
    with pytest.raises(NoDeviceError):
        client.require_device()


# --------------------------------------------------------------------------
# retries
# --------------------------------------------------------------------------
def test_transient_failure_is_retried(client, fake, monkeypatch):
    from android_optimiser.core.transport import CommandResult

    calls = {"n": 0}
    real_run = fake.run

    def flaky(argv, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return CommandResult(
                argv=tuple(argv), rc=1, stdout="",
                stderr="error: device offline", duration=0.0,
            )
        return real_run(argv, timeout)

    monkeypatch.setattr(fake, "run", flaky)
    result = client.shell("getprop ro.product.model")
    assert result.ok
    assert client.metrics.retries == 1


def test_permanent_failure_is_not_retried_forever(client, fake):
    fake.fail_patterns.append("getprop ro.product.model")
    result = client.shell("getprop ro.product.model")
    assert not result.ok
    # one attempt plus DEFAULT_RETRIES retries
    assert len(fake.calls) == 3


# --------------------------------------------------------------------------
# device discovery failures
# --------------------------------------------------------------------------
class _StubTransport:
    """Returns one canned result, whatever it is asked."""

    def __init__(self, result: CommandResult):
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, timeout=None):
        self.calls.append(tuple(argv))
        return self.result


def test_a_wedged_device_is_not_reported_as_a_missing_device():
    """A timeout and an absent device need different fixes from the operator."""
    stub = _StubTransport(
        CommandResult(
            argv=("adb", "devices"),
            rc=124,
            stdout="",
            stderr="timed out",
            duration=20.0,
            timed_out=True,
        )
    )
    client = AdbClient(transport=stub, metrics=Metrics(), retries=0, timeout=20.0)

    with pytest.raises(CommandTimeoutError) as exc:
        client.devices()
    assert "adb devices" in str(exc.value)
    assert exc.value.timeout == 20.0


def test_a_failed_devices_call_raises_no_device_error():
    stub = _StubTransport(
        CommandResult(
            argv=("adb", "devices"),
            rc=1,
            stdout="",
            stderr="adb server is out of date",
            duration=0.1,
        )
    )
    client = AdbClient(transport=stub, metrics=Metrics(), retries=0)

    with pytest.raises(NoDeviceError):
        client.devices()


def test_devices_parses_authorised_devices_only():
    stub = _StubTransport(
        CommandResult(
            argv=("adb", "devices"),
            rc=0,
            stdout=(
                "List of devices attached\n"
                "R58NA0ABCDE\tdevice\n"
                "EMULATOR555\tunauthorized\n"
                "OFFLINE123\toffline\n"
            ),
            stderr="",
            duration=0.05,
        )
    )
    client = AdbClient(transport=stub, metrics=Metrics(), retries=0)
    assert client.devices() == ["R58NA0ABCDE"]


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------
def test_parse_props_handles_the_getprop_format():
    raw = "[ro.product.model]: [SM-G998B]\n[ro.build.type]: [user]\nnot a prop line\n"
    assert parse_props(raw) == {"ro.product.model": "SM-G998B", "ro.build.type": "user"}


def test_parse_settings_handles_the_settings_format():
    raw = "window_animation_scale=0.5\nanimator_duration_scale=1.0\n"
    assert parse_settings(raw) == {
        "window_animation_scale": "0.5",
        "animator_duration_scale": "1.0",
    }
