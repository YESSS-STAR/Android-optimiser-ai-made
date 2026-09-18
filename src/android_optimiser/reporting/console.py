"""Console rendering.

Kept separate from the CLI so that the CLI is argument parsing and wiring, and
nothing else.  All presentation decisions live here.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, TextIO

from ..config import REPORT_WIDTH
from ..domain import Classification, DeviceSnapshot, Risk
from ..executor.executor import ExecutionReport
from ..executor.rollback import RollbackPlan
from ..planner.planner import ActionPlan

_RISK_COLOUR = {
    Risk.SAFE: "\033[32m",
    Risk.CAUTION: "\033[33m",
    Risk.NEVER: "\033[31m",
}


class Style:
    """Minimal ANSI styling that degrades gracefully."""

    def __init__(self, enabled: bool | None = None):
        if enabled is None:
            enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"{code}{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("\033[1m", text)

    def dim(self, text: str) -> str:
        return self._wrap("\033[2m", text)

    def green(self, text: str) -> str:
        return self._wrap("\033[32m", text)

    def yellow(self, text: str) -> str:
        return self._wrap("\033[33m", text)

    def red(self, text: str) -> str:
        return self._wrap("\033[31m", text)

    def cyan(self, text: str) -> str:
        return self._wrap("\033[36m", text)

    def risk(self, risk: Risk, text: str) -> str:
        return self._wrap(_RISK_COLOUR.get(risk, ""), text)


def rule(style: Style, title: str = "") -> str:
    if not title:
        return style.dim("-" * REPORT_WIDTH)
    pad = max(0, REPORT_WIDTH - len(title) - 3)
    return style.bold(title) + " " + style.dim("-" * pad)


def banner(style: Style, subtitle: str = "") -> str:
    lines = [
        style.bold("Android Optimiser"),
        style.dim(subtitle) if subtitle else "",
    ]
    return "\n".join(line for line in lines if line)


def render_device(style: Style, snapshot: DeviceSnapshot) -> str:
    info = snapshot.info
    rows = [
        ("Brand", info.get("brand", "?")),
        ("Model", info.get("model", "?")),
        ("Codename", info.get("device", "?")),
        ("Android", f"{info.get('android_version', '?')} (SDK {info.get('sdk', '?')})"),
        ("Patch", info.get("security_patch", "?")),
        ("ABI", info.get("abi", "?")),
        ("Serial", snapshot.serial),
    ]
    width = max(len(k) for k, _ in rows)
    body = "\n".join(f"  {k.ljust(width)}  {v}" for k, v in rows)

    counts = {
        "total": len(snapshot.packages),
        "enabled": len(snapshot.enabled_packages),
        "disabled": len(snapshot.disabled_packages),
    }
    stats = (
        f"  packages   {counts['total']} installed, "
        f"{counts['enabled']} enabled, {counts['disabled']} already disabled"
    )
    return "\n".join([rule(style, "Device"), body, "", stats])


def render_classification(
    style: Style, classifications: Iterable[Classification], *, limit: int | None = None
) -> str:
    items = sorted(classifications, key=lambda c: (-c.score, c.name))
    if limit is not None:
        items = items[:limit]

    groups: dict[str, list[Classification]] = {}
    for c in items:
        groups.setdefault(c.risk.value, []).append(c)

    out: list[str] = []
    order = [Risk.SAFE, Risk.CAUTION, Risk.NEVER]
    for risk in order:
        bucket = groups.get(risk.value, [])
        if not bucket:
            continue
        out.append("")
        out.append(
            "  "
            + style.risk(risk, risk.value.upper().ljust(8))
            + style.dim(f"{len(bucket)} package(s)")
        )
        for c in bucket:
            marker = "*" if c.record.enabled else " "
            out.append(
                f"   {marker} {c.name.ljust(46)} "
                + style.dim(f"{c.category:<18} {c.confidence:.2f}")
            )
    return "\n".join(out)


def render_plan(style: Style, plan: ActionPlan) -> str:
    lines = [rule(style, f"Plan — {plan.profile.name}"), f"  {plan.profile.description}", ""]

    if plan.empty:
        lines.append("  " + style.green("Nothing to do: the device already matches this profile."))
    else:
        for action in plan.actions:
            lines.append(f"  {style.cyan(action.title)}")
            for command in action.commands:
                undo = "" if command.inverse else style.yellow("  (no inverse)")
                lines.append(f"      {style.dim('$')} {command.shell}{undo}")

    counts = plan.by_category()
    if counts:
        lines.append("")
        lines.append(
            "  "
            + style.dim(
                "  ".join(f"{k}={v}" for k, v in counts.items())
            )
        )

    if plan.skipped:
        lines.append("")
        lines.append("  " + style.dim(f"{len(plan.skipped)} package(s) deliberately left alone"))
    return "\n".join(lines)


def render_execution(style: Style, report: ExecutionReport) -> str:
    lines = [rule(style, "Result")]

    if report.dry_run:
        lines.append("  " + style.yellow("DRY RUN — no changes were made."))

    applied = report.applied
    failed = report.failed
    lines.append(f"  applied    {len(applied)}/{len(report.outcomes)}")
    if failed:
        lines.append("  " + style.red(f"failed     {len(failed)}"))
        for outcome in failed[:10]:
            lines.append(
                "      " + style.red("x") + f" {outcome.command}"
                + style.dim(f"  rc={outcome.rc}")
            )
            if outcome.error:
                lines.append("        " + style.dim(outcome.error.splitlines()[0][:90]))
        if len(failed) > 10:
            lines.append(style.dim(f"      ... and {len(failed) - 10} more"))
    else:
        lines.append("  " + style.green("failed     0"))

    metrics = report.metrics
    lines.append("")
    lines.append(rule(style, "Cost"))
    lines.append(
        f"  round-trips {metrics.get('round_trips', 0)}"
        + style.dim(
            f"   ({metrics.get('device_commands', 0)} device commands, "
            f"batching saved {metrics.get('batching_saving', 0)})"
        )
    )
    lines.append(f"  wall clock  {report.wall_seconds:.2f}s")
    if metrics.get("cache_hits"):
        lines.append(style.dim(f"  cache hits  {metrics['cache_hits']}"))
    if metrics.get("retries"):
        lines.append(style.dim(f"  retries     {metrics['retries']}"))
    return "\n".join(lines)


def render_rollback(style: Style, plan: RollbackPlan) -> str:
    if plan.empty:
        return ""
    lines = [rule(style, "Rollback available")]
    lines.append(f"  undoable     {len(plan.commands)}")
    if plan.irreversible:
        lines.append(
            "  " + style.yellow(f"irreversible {len(plan.irreversible)}")
            + style.dim("  (cache trim / log clear cannot be undone)")
        )
    lines.append(style.dim("  run `android-optimiser restore --from <report.json>` to revert"))
    return "\n".join(lines)


def write(stream: TextIO, text: str) -> None:
    stream.write(text + "\n")
