"""Command line interface.

This module does argument parsing and wiring only.  It contains no device logic,
no classification logic and no presentation logic — those live in
``core``, ``analysis``, ``planner``, ``executor`` and ``reporting``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .analysis.inspector import Inspector
from .core.adb import AdbClient
from .core.transport import SubprocessTransport
from .domain import Risk
from .exceptions import (
    AdbNotFoundError,
    CommandTimeoutError,
    DeviceNotConfirmedError,
    NoDeviceError,
    OptimiserError,
)
from .executor.executor import Executor
from .executor.rollback import RollbackExecutor, build_rollback
from .planner.planner import Planner
from .planner.profiles import PROFILES, get_profile
from .reporting import console, report as reporting

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_ABORTED = 2


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------
def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", "-d", metavar="SERIAL", default=None,
                        help="target a specific device serial")
    parser.add_argument("--adb", metavar="PATH", default=None,
                        help="path to the adb binary (or set ADB_BINARY)")


def _add_tuning_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chunk-size", type=int, default=config.BATCH_CHUNK_SIZE,
                        help="device commands packed into one adb round-trip "
                             f"(default {config.BATCH_CHUNK_SIZE})")
    parser.add_argument("--workers", type=int, default=config.PARALLEL_WORKERS,
                        help=f"parallel batch workers (default {config.PARALLEL_WORKERS})")
    parser.add_argument("--no-parallel", action="store_true",
                        help="disable parallel batch dispatch")


def make_client(args: argparse.Namespace) -> AdbClient:
    transport = SubprocessTransport(getattr(args, "adb", None))
    return AdbClient(
        transport=transport,
        serial=getattr(args, "device", None),
        chunk_size=getattr(args, "chunk_size", config.BATCH_CHUNK_SIZE),
        workers=1 if getattr(args, "no_parallel", False)
        else getattr(args, "workers", config.PARALLEL_WORKERS),
    )


def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    style = console.Style()
    try:
        client = make_client(args)
    except AdbNotFoundError as exc:
        console.write(sys.stdout, style.red(f"adb not found: {exc}"))
        return EXIT_ERROR

    console.write(sys.stdout, style.green(f"adb resolved: {getattr(client.transport, 'binary', '?')}"))
    try:
        devices = client.devices()
    except CommandTimeoutError as exc:
        console.write(sys.stdout, style.red(f"device did not respond: {exc}"))
        console.write(
            sys.stdout,
            style.dim("the device is attached but wedged — try replugging the "
                      "cable or restarting the adb server"),
        )
        return EXIT_ERROR
    except NoDeviceError as exc:
        console.write(sys.stdout, style.red(f"device query failed: {exc}"))
        return EXIT_ERROR

    if not devices:
        console.write(sys.stdout, style.yellow("no authorised device attached"))
        console.write(sys.stdout, style.dim("enable USB debugging and accept the RSA prompt"))
        return EXIT_ERROR

    console.write(sys.stdout, style.green(f"devices: {', '.join(devices)}"))
    return EXIT_OK


def cmd_profiles(args: argparse.Namespace) -> int:
    style = console.Style()
    console.write(sys.stdout, console.rule(style, "Profiles"))
    for profile in PROFILES.values():
        console.write(sys.stdout, f"  {style.bold(profile.key.ljust(11))}{profile.description}")
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    style = console.Style()
    client = make_client(args)
    inspector = Inspector(client)
    snapshot = inspector.snapshot()

    console.write(sys.stdout, console.banner(style, f"v{config.VERSION}"))
    console.write(sys.stdout, console.render_device(style, snapshot))

    limit = None if args.all else args.limit
    console.write(sys.stdout, console.rule(style, "Classification"))
    console.write(
        sys.stdout,
        console.render_classification(style, snapshot.classifications, limit=limit),
    )
    console.write(sys.stdout, "")
    console.write(
        sys.stdout,
        style.dim(f"  inspection cost: {client.metrics.round_trips} round-trip(s), "
                  f"{client.metrics.device_commands} device command(s)"),
    )

    if args.json:
        payload = {
            "device": {"serial": snapshot.serial, **snapshot.info},
            "classification": [c.as_dict() for c in snapshot.classifications],
            "metrics": client.metrics.summary(),
        }
        path = reporting.write_json(args.json, payload)
        console.write(sys.stdout, style.dim(f"  written: {path}"))
    return EXIT_OK


def _note_caution(style: console.Style, args: argparse.Namespace, snapshot) -> None:
    """Warn when --include-caution is about to widen the blast radius."""
    if not args.include_caution:
        return
    caution = [c for c in snapshot.classifications if c.risk is Risk.CAUTION]
    if not caution:
        return
    console.write(sys.stdout, "")
    console.write(
        sys.stdout,
        style.yellow(f"  --include-caution adds {len(caution)} uncertain package(s)"),
    )


def _write_reports(
    args: argparse.Namespace,
    style: console.Style,
    snapshot,
    plan,
    execution,
    rollback,
) -> None:
    """Emit whichever machine-readable reports were asked for."""
    if not (args.json_report or args.markdown_report):
        return

    payload = reporting.build_payload(snapshot, plan, execution, rollback)

    if args.json_report:
        path = reporting.write_json(args.json_report, payload)
        console.write(sys.stdout, style.dim(f"\n  JSON report: {path}"))

    if args.markdown_report:
        target = Path(args.markdown_report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(reporting.render_markdown(payload), encoding="utf-8")
        console.write(sys.stdout, style.dim(f"  Markdown report: {target}"))


def _apply_plan(
    args: argparse.Namespace,
    style: console.Style,
    client: AdbClient,
    snapshot,
    plan,
) -> int:
    """Confirm, execute, and report one plan."""
    if not _confirm(
        f"\n  Apply {len(plan.commands)} command(s)? [y/N] ", assume_yes=args.yes
    ):
        console.write(sys.stdout, style.yellow("  aborted by operator"))
        return EXIT_ABORTED

    executor = Executor(
        client,
        chunk_size=args.chunk_size,
        workers=1 if args.no_parallel else args.workers,
        retry_failures=not args.no_retry,
    )
    execution = executor.execute(plan)
    rollback = build_rollback(execution)

    console.write(sys.stdout, "")
    console.write(sys.stdout, console.render_execution(style, execution))
    console.write(sys.stdout, "")
    console.write(sys.stdout, console.render_rollback(style, rollback))
    _write_reports(args, style, snapshot, plan, execution, rollback)

    return EXIT_OK if execution.all_succeeded else EXIT_ERROR


def cmd_optimize(args: argparse.Namespace) -> int:
    style = console.Style()
    profile = get_profile(args.profile)

    client = make_client(args)
    snapshot = Inspector(client).snapshot()

    console.write(sys.stdout, console.banner(style, f"v{config.VERSION}"))
    console.write(sys.stdout, console.render_device(style, snapshot))

    if args.device and snapshot.serial != args.device:
        console.write(sys.stdout, style.red("attached device does not match --device"))
        return EXIT_ERROR

    if not _confirm(
        f"\n  Target {snapshot.info.get('model', '?')} ({snapshot.serial})? [y/N] ",
        assume_yes=args.yes,
    ):
        raise DeviceNotConfirmedError("device not confirmed by operator")

    plan = Planner(client).plan(snapshot, profile)
    console.write(sys.stdout, "")
    console.write(sys.stdout, console.render_plan(style, plan))
    _note_caution(style, args, snapshot)

    if plan.empty:
        console.write(sys.stdout, "")
        console.write(sys.stdout, style.green("  Already optimised — nothing to do."))
        return EXIT_OK

    if args.dry_run:
        execution = Executor(client).dry_run(plan)
        console.write(sys.stdout, "")
        console.write(sys.stdout, console.render_execution(style, execution))
        return EXIT_OK

    return _apply_plan(args, style, client, snapshot, plan)


def _show_rollback_preview(style: console.Style, commands: list[str]) -> None:
    for command in commands:
        console.write(sys.stdout, style.dim(f"    $ {command}"))


def cmd_restore(args: argparse.Namespace) -> int:
    style = console.Style()
    payload = reporting.load_json(args.source)
    commands = payload.get("rollback", {}).get("commands", [])
    irreversible = payload.get("rollback", {}).get("irreversible", [])

    console.write(sys.stdout, console.banner(style, "restore"))
    console.write(
        sys.stdout,
        f"  report from {payload.get('generated_at', '?')} "
        f"({payload.get('device', {}).get('model', '?')})",
    )
    console.write(sys.stdout, f"  undoable commands: {len(commands)}")
    if irreversible:
        console.write(
            sys.stdout,
            style.yellow(f"  cannot be undone: {len(irreversible)}"),
        )

    if not commands:
        console.write(sys.stdout, style.green("  nothing to restore"))
        return EXIT_OK

    if args.dry_run:
        _show_rollback_preview(style, commands)
        return EXIT_OK

    if not _confirm("\n  Apply rollback? [y/N] ", assume_yes=args.yes):
        console.write(sys.stdout, style.yellow("  aborted by operator"))
        return EXIT_ABORTED

    client = make_client(args)
    client.require_device()
    result = RollbackExecutor(
        client,
        chunk_size=args.chunk_size,
        workers=1 if args.no_parallel else args.workers,
    ).apply(rollback=_rebuild_plan(payload))

    console.write(sys.stdout, "")
    console.write(
        sys.stdout,
        f"  restored   {result.succeeded}/{result.attempted}"
        + ("" if result.ok else style.red(f"  ({len(result.failed)} failed)")),
    )
    console.write(sys.stdout, style.dim(f"  round-trips {result.round_trips}"))
    return EXIT_OK if result.ok else EXIT_ERROR


def _rebuild_plan(payload: dict):
    from .executor.rollback import RollbackPlan

    section = payload.get("rollback", {})
    return RollbackPlan(
        commands=section.get("commands", []),
        irreversible=section.get("irreversible", []),
        source_profile=payload.get("profile", {}).get("key", ""),
    )


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=config.APP_NAME,
        description="Inspect and debloat an Android device over ADB.",
    )
    parser.add_argument("--version", action="version",
                        version=f"{config.APP_NAME} {config.VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="check adb and device connectivity")
    _add_connection_options(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_profiles = sub.add_parser("profiles", help="list optimisation profiles")
    p_profiles.set_defaults(func=cmd_profiles)

    p_inspect = sub.add_parser("inspect", help="classify every installed package")
    _add_connection_options(p_inspect)
    p_inspect.add_argument("--json", metavar="PATH", default=None,
                           help="write the classification to a JSON file")
    p_inspect.add_argument("--all", action="store_true", help="show every package")
    p_inspect.add_argument("--limit", type=int, default=25,
                           help="packages to show per risk band (default 25)")
    p_inspect.set_defaults(func=cmd_inspect)

    p_opt = sub.add_parser("optimize", help="plan and apply an optimisation profile")
    _add_connection_options(p_opt)
    _add_tuning_options(p_opt)
    p_opt.add_argument("--profile", "-p", default="heavy", choices=sorted(PROFILES),
                       help="optimisation profile (default heavy)")
    p_opt.add_argument("--dry-run", action="store_true",
                       help="show the plan without touching the device")
    p_opt.add_argument("--yes", "-y", action="store_true", help="skip confirmation prompts")
    p_opt.add_argument("--include-caution", action="store_true",
                       help="also act on packages the classifier is unsure about")
    p_opt.add_argument("--no-retry", action="store_true",
                       help="do not re-run failed commands individually")
    p_opt.add_argument("--json-report", metavar="PATH", default=None)
    p_opt.add_argument("--markdown-report", metavar="PATH", default=None)
    p_opt.set_defaults(func=cmd_optimize)

    p_restore = sub.add_parser("restore", help="undo a previous run from its JSON report")
    _add_connection_options(p_restore)
    _add_tuning_options(p_restore)
    p_restore.add_argument("--from", dest="source", required=True, metavar="REPORT.json")
    p_restore.add_argument("--dry-run", action="store_true")
    p_restore.add_argument("--yes", "-y", action="store_true")
    p_restore.set_defaults(func=cmd_restore)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    style = console.Style()
    try:
        return args.func(args)
    except DeviceNotConfirmedError as exc:
        console.write(sys.stdout, style.yellow(f"aborted: {exc}"))
        return EXIT_ABORTED
    except NoDeviceError as exc:
        console.write(sys.stderr, style.red(f"no device: {exc}"))
        return EXIT_ERROR
    except AdbNotFoundError as exc:
        console.write(sys.stderr, style.red(f"adb not found: {exc}"))
        return EXIT_ERROR
    except OptimiserError as exc:
        console.write(sys.stderr, style.red(f"error: {exc}"))
        return EXIT_ERROR
    except KeyboardInterrupt:
        console.write(sys.stderr, style.yellow("\ninterrupted"))
        return EXIT_ABORTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
