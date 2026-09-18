"""Tests for machine-readable reporting.

The JSON payload is not decoration: it is the input to
``android-optimiser restore``.  If a field silently changes shape, rollback
stops working, so these tests pin the contract rather than the formatting.
"""

from __future__ import annotations

import json

from android_optimiser.analysis.inspector import Inspector
from android_optimiser.executor.executor import Executor
from android_optimiser.executor.rollback import build_rollback
from android_optimiser.planner.planner import Planner
from android_optimiser.planner.profiles import get_profile
from android_optimiser.reporting import report as reporting


def _run(client):
    """Produce a real payload from a real (simulated) run."""
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, get_profile("heavy"))
    execution = Executor(client, chunk_size=8, workers=4).execute(plan)
    rollback = build_rollback(execution)
    return reporting.build_payload(snapshot, plan, execution, rollback)


# --------------------------------------------------------------------------
# payload shape
# --------------------------------------------------------------------------
def test_payload_has_every_top_level_section(client):
    payload = _run(client)
    for key in (
        "tool", "version", "generated_at", "device", "profile",
        "plan", "execution", "rollback", "classification",
    ):
        assert key in payload, f"missing section: {key}"


def test_payload_device_identifies_the_handset(client):
    payload = _run(client)
    assert payload["device"]["serial"]
    assert payload["device"]["model"]


def test_payload_plan_reports_actions_and_commands(client):
    payload = _run(client)
    assert payload["plan"]["summary"]["actions"] > 0
    assert payload["plan"]["summary"]["commands"] >= payload["plan"]["summary"]["actions"]
    assert payload["plan"]["actions"]


def test_payload_rollback_commands_are_undo_commands(client):
    payload = _run(client)
    commands = payload["rollback"]["commands"]
    assert commands
    assert all(isinstance(c, str) and c for c in commands)
    # A disable is undone by an enable, never by another disable.
    assert any(c.startswith("pm enable") for c in commands)
    assert not any(c.startswith("pm disable-user") for c in commands)


def test_payload_marks_irreversible_commands_and_reports_honest_coverage(client):
    payload = _run(client)
    rollback = payload["rollback"]
    # The heavy profile always trims caches, which cannot be undone.
    assert "pm trim-caches 999G" in rollback["irreversible"]
    assert 0.0 < rollback["summary"]["coverage"] < 1.0


def test_payload_classification_covers_the_device(client):
    payload = _run(client)
    assert payload["classification"]
    for entry in payload["classification"]:
        assert entry["risk"] in ("safe", "caution", "never")
        assert entry["name"]


def test_payload_is_json_serialisable(client):
    payload = _run(client)
    # Would raise on a stray dataclass or enum.
    assert json.loads(json.dumps(payload)) == payload


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
def test_write_then_load_round_trips(client, tmp_path):
    payload = _run(client)
    target = reporting.write_json(tmp_path / "nested" / "run.json", payload)
    assert target.exists()
    assert reporting.load_json(target) == payload


def test_write_json_creates_missing_parents(client, tmp_path):
    payload = _run(client)
    target = reporting.write_json(tmp_path / "a" / "b" / "c" / "run.json", payload)
    assert target.parent.is_dir()


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def test_markdown_has_the_expected_sections(client):
    text = reporting.render_markdown(_run(client))
    for heading in ("# android-optimiser report", "## Device", "## Plan",
                    "## Execution", "## Rollback"):
        assert heading in text


def test_markdown_lists_the_skipped_packages(client):
    text = reporting.render_markdown(_run(client))
    assert "## Left alone" in text
    assert "| Package | Risk | Reason |" in text


def test_markdown_truncates_a_very_long_skip_list():
    """A 1000-package device should not produce a 1000-row table."""
    payload = {
        "device": {}, "profile": {}, "plan": {
            "summary": {},
            "skipped": [
                {"package": f"com.example.p{i}", "risk": "never", "reason": "protected"}
                for i in range(75)
            ],
        },
        "execution": {"summary": {}}, "rollback": {"summary": {}},
    }
    text = reporting.render_markdown(payload)
    assert "15 more" in text
    assert "com.example.p59" in text
    assert "com.example.p60" not in text


def test_markdown_tolerates_a_payload_with_no_skips(client):
    payload = _run(client)
    payload["plan"]["skipped"] = []
    assert "## Left alone" not in reporting.render_markdown(payload)


def test_markdown_tolerates_a_payload_missing_optional_sections():
    """A truncated or hand-edited report must not crash the renderer."""
    text = reporting.render_markdown({})
    assert "# android-optimiser report" in text
    assert "Generated ? by v?" in text
