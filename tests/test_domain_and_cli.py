"""Tests for the domain model and the CLI surface."""

from __future__ import annotations

import pytest

from android_optimiser.cli import build_parser, main
from android_optimiser.domain import (
    Classification,
    Impact,
    PackageRecord,
    Partition,
    Risk,
    partition_from_path,
)
from android_optimiser.planner.profiles import PROFILES


# --------------------------------------------------------------------------
# domain
# --------------------------------------------------------------------------
def test_partition_from_path_handles_every_partition():
    assert partition_from_path("/system/priv-app/A/a.apk") is Partition.SYSTEM
    assert partition_from_path("/vendor/app/A/a.apk") is Partition.VENDOR
    assert partition_from_path("/product/app/A/a.apk") is Partition.PRODUCT
    assert partition_from_path("/system_ext/app/A/a.apk") is Partition.SYSTEM_EXT
    assert partition_from_path("/data/app/A/a.apk") is Partition.DATA
    assert partition_from_path("") is Partition.UNKNOWN


def test_system_partitions_are_not_data():
    assert Partition.SYSTEM.is_system
    assert Partition.PRODUCT.is_system
    assert not Partition.DATA.is_system
    assert not Partition.UNKNOWN.is_system


def test_preinstalled_and_user_installed_are_mutually_exclusive():
    system = PackageRecord(name="com.a.b", partition=Partition.SYSTEM)
    assert system.is_preinstalled
    assert not system.is_user_installed

    user = PackageRecord(
        name="com.a.b",
        partition=Partition.DATA,
        third_party=True,
        installer="com.android.vending",
    )
    assert user.is_user_installed
    assert not user.is_preinstalled

    provisioned = PackageRecord(name="com.a.b", partition=Partition.DATA, third_party=True)
    assert provisioned.is_preinstalled
    assert not provisioned.is_user_installed


def test_installer_null_is_not_a_store_installer():
    record = PackageRecord(
        name="com.a.b", partition=Partition.DATA, third_party=True, installer="null"
    )
    assert not record.has_store_installer
    assert record.is_preinstalled


def test_classification_actionable_only_when_safe():
    record = PackageRecord(name="com.a.b")
    for risk, expected in ((Risk.SAFE, True), (Risk.CAUTION, False), (Risk.NEVER, False)):
        assert Classification(record, risk, "x", 50, 0.5, "why").actionable is expected


def test_classification_serialises():
    record = PackageRecord(name="com.a.b", partition=Partition.SYSTEM)
    payload = Classification(record, Risk.SAFE, "telemetry", 82, 0.8, "why", "V").as_dict()
    assert payload["name"] == "com.a.b"
    assert payload["risk"] == "safe"
    assert payload["impact"] == Impact.NONE.value


# --------------------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------------------
def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


@pytest.mark.parametrize("command", ["doctor", "profiles", "inspect", "optimize", "restore"])
def test_every_subcommand_is_registered(command):
    parser = build_parser()
    assert command in parser._subparsers._group_actions[0].choices


def test_optimize_accepts_every_profile():
    parser = build_parser()
    for key in PROFILES:
        args = parser.parse_args(["optimize", "--profile", key])
        assert args.profile == key


def test_optimize_rejects_an_unknown_profile():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["optimize", "--profile", "nonsense"])


def test_restore_requires_a_source_report():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["restore"])


def test_defaults_are_sane():
    args = build_parser().parse_args(["optimize"])
    assert args.profile == "heavy"
    assert args.dry_run is False
    assert args.yes is False
    assert args.chunk_size > 0
    assert args.workers > 0


def test_profiles_command_succeeds_without_a_device(capsys):
    assert main(["profiles"]) == 0
    assert "light" in capsys.readouterr().out


def test_doctor_reports_a_wedged_device_distinctly(monkeypatch, capsys):
    """A device that times out is attached, so do not tell the operator to plug one in."""
    import android_optimiser.cli as cli
    from android_optimiser.exceptions import CommandTimeoutError

    class _Wedged:
        transport = type("T", (), {"binary": "/usr/bin/adb"})()

        def devices(self):
            raise CommandTimeoutError("adb devices", 20.0)

    monkeypatch.setattr(cli, "make_client", lambda args: _Wedged())
    assert main(["doctor"]) == 1

    out = capsys.readouterr().out
    assert "did not respond" in out
    assert "wedged" in out
    # The generic "no device attached" guidance must not appear.
    assert "no authorised device attached" not in out


def test_doctor_reports_no_device(monkeypatch, capsys):
    import android_optimiser.cli as cli

    class _NoDevice:
        transport = type("T", (), {"binary": "/usr/bin/adb"})()

        def devices(self):
            return []

    monkeypatch.setattr(cli, "make_client", lambda args: _NoDevice())
    assert main(["doctor"]) == 1
    assert "no authorised device attached" in capsys.readouterr().out


def test_version_flag():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0


# --------------------------------------------------------------------------
# CLI against a device
# --------------------------------------------------------------------------
def test_inspect_writes_json(client, tmp_path, monkeypatch, capsys):
    import android_optimiser.cli as cli

    monkeypatch.setattr(cli, "make_client", lambda args: client)
    target = tmp_path / "out.json"
    assert main(["inspect", "--json", str(target)]) == 0
    assert target.exists()
    assert "classification" in target.read_text(encoding="utf-8")


def test_optimize_dry_run_changes_nothing(client, fake, monkeypatch, capsys):
    import android_optimiser.cli as cli

    monkeypatch.setattr(cli, "make_client", lambda args: client)
    before = fake.state["settings"]["global"]["window_animation_scale"]
    assert main(["optimize", "--dry-run", "--yes"]) == 0
    assert fake.state["settings"]["global"]["window_animation_scale"] == before
    assert fake.is_enabled("com.facebook.katana")


def test_optimize_applies_and_reports(client, fake, monkeypatch, tmp_path, capsys):
    import android_optimiser.cli as cli

    monkeypatch.setattr(cli, "make_client", lambda args: client)
    report = tmp_path / "report.json"
    rc = main(
        ["optimize", "--profile", "heavy", "--yes", "--json-report", str(report)]
    )
    assert rc == 0
    assert not fake.is_enabled("com.facebook.katana")
    assert report.exists()


def test_restore_dry_run_lists_commands(client, tmp_path, monkeypatch, capsys):
    import android_optimiser.cli as cli

    monkeypatch.setattr(cli, "make_client", lambda args: client)
    report = tmp_path / "report.json"
    main(["optimize", "--profile", "heavy", "--yes", "--json-report", str(report)])
    capsys.readouterr()

    assert main(["restore", "--from", str(report), "--dry-run"]) == 0
    assert "pm enable" in capsys.readouterr().out
