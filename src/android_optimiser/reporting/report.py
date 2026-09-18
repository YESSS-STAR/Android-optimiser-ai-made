"""Machine-readable reporting.

A run produces a JSON document that captures the device, the classification
verdicts, the plan, what actually happened and how to undo it.  That document is
the input to ``android-optimiser restore``, so rollback does not depend on the
user having kept the terminal open.

Markdown rendering is included because the same payload is what a human wants to
read in a bug report.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..domain import DeviceSnapshot
from ..executor.executor import ExecutionReport
from ..executor.rollback import RollbackPlan
from ..planner.planner import ActionPlan


def build_payload(
    snapshot: DeviceSnapshot,
    plan: ActionPlan,
    report: ExecutionReport,
    rollback: RollbackPlan,
) -> dict:
    return {
        "tool": config.APP_NAME,
        "version": config.VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": {"serial": snapshot.serial, **snapshot.info},
        "profile": plan.profile.as_dict(),
        "plan": {
            "actions": [a.as_dict() for a in plan.actions],
            "skipped": [s.as_dict() for s in plan.skipped],
            "summary": plan.summary(),
        },
        "execution": {
            "summary": report.summary(),
            "outcomes": [o.as_dict() for o in report.outcomes],
        },
        "rollback": {
            "summary": rollback.summary(),
            "commands": rollback.commands,
            "irreversible": rollback.irreversible,
        },
        "classification": [c.as_dict() for c in snapshot.classifications],
    }


def write_json(path: str | Path, payload: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_markdown(payload: dict) -> str:
    device = payload.get("device", {})
    plan = payload.get("plan", {})
    execution = payload.get("execution", {}).get("summary", {})
    rollback = payload.get("rollback", {}).get("summary", {})

    lines: list[str] = []
    lines.append(f"# {config.APP_NAME} report")
    lines.append("")
    lines.append(f"Generated {payload.get('generated_at', '?')} by v{payload.get('version', '?')}")
    lines.append("")
    lines.append("## Device")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    for key in ("serial", "brand", "model", "device", "android_version", "sdk", "security_patch"):
        if key in device:
            lines.append(f"| {key} | {device[key]} |")
    lines.append("")

    lines.append("## Plan")
    lines.append("")
    lines.append(f"- profile: **{payload.get('profile', {}).get('key', '?')}**")
    lines.append(f"- actions: {plan.get('summary', {}).get('actions', 0)}")
    lines.append(f"- commands: {plan.get('summary', {}).get('commands', 0)}")
    lines.append(f"- fully reversible: {plan.get('summary', {}).get('reversible', False)}")
    lines.append("")

    lines.append("## Execution")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    for key, value in execution.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    lines.append("## Rollback")
    lines.append("")
    for key, value in rollback.items():
        lines.append(f"- {key}: {value}")
    lines.append("")

    skipped = plan.get("skipped", [])
    if skipped:
        lines.append(f"## Left alone ({len(skipped)})")
        lines.append("")
        lines.append("| Package | Risk | Reason |")
        lines.append("| --- | --- | --- |")
        for item in skipped[:60]:
            lines.append(f"| `{item['package']}` | {item['risk']} | {item['reason']} |")
        if len(skipped) > 60:
            lines.append(f"| ... | | {len(skipped) - 60} more |")
        lines.append("")

    return "\n".join(lines)
