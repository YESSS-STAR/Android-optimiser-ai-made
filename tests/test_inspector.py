"""Tests for parsing device output and building the snapshot."""

from __future__ import annotations

import pytest

from android_optimiser.analysis.inspector import (
    Inspector,
    parse_disabled_list,
    parse_doze_state,
    parse_package_line,
)
from android_optimiser.domain import Partition


# --------------------------------------------------------------------------
# package line parsing
# --------------------------------------------------------------------------
def test_parse_plain_package_line():
    record = parse_package_line("package:com.example.app", third_party=False)
    assert record is not None
    assert record.name == "com.example.app"
    assert record.path == ""
    assert record.installer is None


def test_parse_path_form():
    record = parse_package_line(
        "package:/system/priv-app/SystemUI/SystemUI.apk=com.android.systemui",
        third_party=False,
    )
    assert record.name == "com.android.systemui"
    assert record.path == "/system/priv-app/SystemUI/SystemUI.apk"
    assert record.partition is Partition.SYSTEM


def test_parse_path_and_installer_form():
    record = parse_package_line(
        "package:/data/app/~~abc/com.whatsapp-def/base.apk=com.whatsapp "
        "installer=com.android.vending",
        third_party=True,
    )
    assert record.name == "com.whatsapp"
    assert record.installer == "com.android.vending"
    assert record.partition is Partition.DATA
    assert record.third_party
    assert record.is_user_installed


def test_parse_installer_null_means_no_installer():
    record = parse_package_line(
        "package:/data/app/~~abc/com.facebook.katana-def/base.apk=com.facebook.katana "
        "installer=null",
        third_party=True,
    )
    assert record.installer is None
    assert record.is_preinstalled
    assert not record.is_user_installed


def test_parse_package_line_rejects_non_package_lines():
    assert parse_package_line("garbage", third_party=False) is None
    assert parse_package_line("", third_party=False) is None
    assert parse_package_line("package:", third_party=False) is None


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/system/app/Foo/foo.apk", Partition.SYSTEM),
        ("/system/priv-app/Foo/foo.apk", Partition.SYSTEM),
        ("/system_ext/app/Foo/foo.apk", Partition.SYSTEM_EXT),
        ("/product/app/Foo/foo.apk", Partition.PRODUCT),
        ("/vendor/app/Foo/foo.apk", Partition.VENDOR),
        ("/data/app/~~x/com.y-z/base.apk", Partition.DATA),
        ("relative/path.apk", Partition.UNKNOWN),
        ("", Partition.UNKNOWN),
    ],
)
def test_partition_detection(path, expected):
    record = parse_package_line(f"package:{path}=com.example.app", third_party=False)
    assert record.partition is expected


def test_parse_disabled_list():
    raw = "package:com.a\npackage:com.b\n"
    assert parse_disabled_list(raw) == {"com.a", "com.b"}


def test_parse_doze_state():
    assert parse_doze_state("  mState=idle\n") == "idle"
    assert parse_doze_state("  mState=active\n") == "active"
    assert parse_doze_state("Now forced in to deep idle mode") == "idle"
    assert parse_doze_state("nonsense") == "unknown"


# --------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------
def test_snapshot_uses_two_round_trips(client, fake):
    fake.calls.clear()
    snapshot = Inspector(client).snapshot()
    assert fake.round_trips == 2, "one for `devices`, one for the batched inspection"
    assert snapshot.serial == "R58NA0ABCDE"


def test_snapshot_collects_every_package(client):
    snapshot = Inspector(client).snapshot()
    assert len(snapshot.packages) == 154


def test_snapshot_records_disabled_state(client):
    snapshot = Inspector(client).snapshot()
    assert len(snapshot.disabled_packages) == 12
    assert not snapshot.package("com.facebook.orca").enabled
    assert snapshot.package("com.facebook.katana").enabled


def test_snapshot_primes_the_property_cache(client, fake):
    snapshot = Inspector(client).snapshot()
    assert snapshot.info["model"] == "SM-G998B"

    fake.calls.clear()
    assert client.get_prop("ro.build.version.release") == "13"
    assert fake.calls == [], "properties must come from the cache, not the device"


def test_snapshot_primes_the_settings_cache(client, fake):
    Inspector(client).snapshot()
    fake.calls.clear()
    assert client.get_setting("window_animation_scale") == "1.0"
    assert fake.calls == []


def test_snapshot_classifies_everything(client):
    snapshot = Inspector(client).snapshot()
    assert len(snapshot.classifications) == len(snapshot.packages)
    assert all(c.record.name for c in snapshot.classifications)


def test_snapshot_reads_doze_state(client):
    snapshot = Inspector(client).snapshot()
    assert snapshot.doze_state == "active"
    client.shell("dumpsys deviceidle force-idle")
    assert Inspector(client).snapshot().doze_state == "idle"


def test_snapshot_does_not_mutate_the_device(client, fake):
    before = fake.state["settings"]["global"]["window_animation_scale"]
    Inspector(client).snapshot()
    assert fake.state["settings"]["global"]["window_animation_scale"] == before
