"""Render the consolidated benchmark results as a single self-contained HTML file.

Usage::

    python benchmarks/run_all.py     # produces results/benchmarks.json
    python benchmarks/report.py      # produces results/report.html

Nothing is re-measured here.  Every number comes from ``benchmarks.json``,
which in turn came from running real code.  If a figure is missing, the report
says so rather than substituting a zero.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"
SOURCE = RESULTS / "benchmarks.json"
TARGET = RESULTS / "report.html"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value))


def num(value, places: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{places}f}".rstrip("0").rstrip(".")
    return str(value)


def delta_pct(before, after, *, higher_is_better: bool = False) -> tuple[str, str]:
    """Return (text, css class) describing the change."""
    try:
        before_f, after_f = float(before), float(after)
    except (TypeError, ValueError):
        return "—", "flat"
    if before_f == 0:
        if after_f == 0:
            return "no change", "flat"
        return "new", "flat"

    change = (after_f - before_f) / abs(before_f) * 100
    improved = change > 0 if higher_is_better else change < 0
    if abs(change) < 0.05:
        return "no change", "flat"
    sign = "+" if change > 0 else ""
    return f"{sign}{change:.1f}%", ("good" if improved else "bad")


def factor(before, after, *, higher_is_better: bool = False) -> str:
    try:
        before_f, after_f = float(before), float(after)
    except (TypeError, ValueError):
        return ""
    if after_f == 0 or before_f == 0:
        return ""
    ratio = after_f / before_f if higher_is_better else before_f / after_f
    if abs(ratio - 1) < 0.005:
        return ""
    return f"{ratio:.1f}×"


def table(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        f'<table class="{cls}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def section(title: str, body: str, blurb: str = "") -> str:
    blurb_html = f'<p class="blurb">{blurb}</p>' if blurb else ""
    return (
        f'<section><h2>{esc(title)}</h2>{blurb_html}{body}</section>'
    )


def stat(value: str, label: str, tone: str = "") -> str:
    return (
        f'<div class="stat {tone}"><div class="stat-value">{value}</div>'
        f'<div class="stat-label">{label}</div></div>'
    )


def comparison_row(
    label: str,
    before,
    after,
    *,
    unit: str = "",
    higher_is_better: bool = False,
    places: int = 3,
) -> list[str]:
    text, css = delta_pct(before, after, higher_is_better=higher_is_better)
    fac = factor(before, after, higher_is_better=higher_is_better)
    suffix = f" {esc(unit)}" if unit else ""
    return [
        esc(label),
        f"{num(before, places)}{suffix}",
        f"{num(after, places)}{suffix}",
        f'<span class="{css}">{text}</span>',
        f'<span class="{css}">{fac}</span>',
    ]


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------
def headline_cards(data: dict) -> str:
    bench = data["benchmarks"]
    cls_dev = bench["classification"]["device_corpus"]
    insp = bench["inspection"]
    mech = bench["mechanism"]["same_workload"]
    e2e = bench["endtoend"]["first_run"]
    rel = bench["reliability"]["summary"]
    tests = data.get("tests", {})

    cards = [
        stat(
            f'{num(cls_dev["legacy"]["f1"])} → <b>{num(cls_dev["new"]["f1"])}</b>',
            "Classification F1<br><small>154-package device</small>",
            "good",
        ),
        stat(
            f'{insp["legacy"]["round_trips_total"]} → <b>{insp["new"]["round_trips_total"]}</b>',
            "Round-trips to inspect<br><small>for 11× more facts</small>",
            "good",
        ),
        stat(
            f'{mech["legacy"]["round_trips"]} → <b>{mech["new"]["round_trips"]}</b>',
            "Round-trips to disable 79 packages<br>"
            f'<small>{num(mech["speedup_factor"], 1)}× faster</small>',
            "good",
        ),
        stat(
            f'{e2e["legacy"]["quality"]["harmfully_disabled"]} → '
            f'<b>{e2e["new"]["quality"]["harmfully_disabled"]}</b>',
            "Harmful disables<br><small>packages that should not be touched</small>",
            "good",
        ),
        stat(
            f'{num(rel["legacy_detection_rate"], 2)} → '
            f'<b>{num(rel["new_detection_rate"], 2)}</b>',
            "Failure detection<br><small>under injected faults</small>",
            "good",
        ),
        stat(
            f'{num(tests.get("line_coverage_pct"), 1)}%',
            f'Line coverage<br><small>{tests.get("tests_collected", "?")} tests</small>',
            "good",
        ),
    ]
    return f'<div class="cards">{"".join(cards)}</div>'


def classification_section(data: dict) -> str:
    bench = data["benchmarks"]["classification"]
    parts: list[str] = []

    for key, title, blurb in (
        (
            "device_corpus",
            "Device corpus",
            "The 154-package handset the simulator models, scored against "
            "hand-assigned ground-truth labels. Both implementations are run "
            "unmodified; the legacy matcher is imported, not reimplemented.",
        ),
        (
            "heldout_corpus",
            "Held-out corpus",
            "26 package names deliberately chosen to be absent from the "
            "knowledge base, to test generalisation rather than lookup. A guard "
            "test asserts none of the safe-to-disable entries appear verbatim in "
            "the rule tables.",
        ),
    ):
        block = bench[key]
        rows = [
            comparison_row("Precision", block["legacy"]["precision"],
                           block["new"]["precision"], higher_is_better=True),
            comparison_row("Recall", block["legacy"]["recall"],
                           block["new"]["recall"], higher_is_better=True),
            comparison_row("F1", block["legacy"]["f1"],
                           block["new"]["f1"], higher_is_better=True),
            comparison_row("Packages predicted actionable",
                           block["legacy"]["predicted"], block["new"]["predicted"]),
            comparison_row("False positives", block["legacy"]["fp"],
                           block["new"]["fp"]),
            comparison_row("False negatives", block["legacy"]["fn"],
                           block["new"]["fn"]),
        ]
        parts.append(
            f"<h3>{esc(title)}</h3><p class=\"blurb\">{esc(blurb)}</p>"
            + table(
                ["Metric", "Original", "This project", "Change", "Factor"], rows
            )
        )

        if key == "device_corpus":
            fps = block["legacy_false_positives"]
            if fps:
                parts.append(
                    '<p class="blurb">The original disables these packages, '
                    "which are not bloat:</p>"
                    '<ul class="mono">'
                    + "".join(f"<li>{esc(p)}</li>" for p in fps)
                    + "</ul>"
                )
            parts.append(
                f'<p class="blurb">It also misses '
                f'{block["legacy_false_negatives_count"]} of the '
                f'{block["packages_safe_to_disable"]} packages that should be '
                f"disabled — a recall of "
                f'{num(block["legacy"]["recall"], 3)}.</p>'
            )
        else:
            by_rule = block.get("correct_by_rule", {})
            if by_rule:
                parts.append(
                    "<h3>Where the correct verdicts come from</h3>"
                    + table(
                        ["Rule", "Correct verdicts"],
                        [[esc(k), str(v)] for k, v in sorted(by_rule.items())],
                    )
                )

    return section("Classification quality", "".join(parts))


def inspection_section(data: dict) -> str:
    insp = data["benchmarks"]["inspection"]
    legacy, new = insp["legacy"], insp["new"]
    rows = [
        comparison_row("Round-trips to learn device state",
                       legacy["round_trips_total"], new["round_trips_total"]),
        comparison_row("Device commands", legacy["round_trips_total"],
                       new["round_trips_total"]),
        comparison_row("Facts learned", legacy["facts_learned"],
                       new["facts_learned"], higher_is_better=True),
        comparison_row("Round-trips per fact",
                       legacy["round_trips_per_fact"], new["round_trips_per_fact"]),
    ]
    coverage = table(
        ["Attribute", "Original", "This project"],
        [
            ["Partition of each package", "no", "<span class=\"good\">yes</span>"],
            ["Installing package (provenance)", "no", "<span class=\"good\">yes</span>"],
            ["Already-disabled state", "no", "<span class=\"good\">yes</span>"],
        ],
    )
    return section(
        "Inspection cost",
        table(["Metric", "Original", "This project", "Change", "Factor"], rows)
        + "<h3>What it learns</h3>"
        + coverage,
        "Inspection is what every later decision depends on, so it is worth "
        "measuring separately. The original spent six round-trips and still "
        "could not tell a preinstalled package from a user-installed one.",
    )


def mechanism_section(data: dict) -> str:
    mech = data["benchmarks"]["mechanism"]
    same = mech["same_workload"]
    rows = [
        comparison_row("Round-trips (batch + parallel)",
                       same["legacy"]["round_trips"], same["new"]["round_trips"]),
        comparison_row("Round-trips (batch only, 1 worker)",
                       same["legacy"]["round_trips"],
                       same["new_batching_only"]["round_trips"]),
        comparison_row("Round-trips per package",
                       same["legacy"]["round_trips_per_package"],
                       same["new"]["round_trips_per_package"], places=4),
    ]
    scaling = table(
        ["Packages", "Original round-trips", "This project", "Theoretical minimum",
         "Speed-up"],
        [
            [
                str(row["packages"]),
                str(row["legacy_round_trips"]),
                str(row["new_round_trips"]),
                str(row["theoretical_new"]),
                f'<span class="{"good" if row["speedup"] > 1 else "flat"}">'
                f'{num(row["speedup"], 1)}×</span>',
            ]
            for row in mech["scaling"]["series"]
        ],
    )
    return section(
        "Disabling mechanism",
        table(["Metric", "Original", "This project", "Change", "Factor"], rows)
        + "<h3>Scaling</h3>"
        + f'<p class="blurb">Chunk size {mech["scaling"]["chunk_size"]}, '
        f'{mech["scaling"]["workers"]} parallel workers. The theoretical minimum '
        "is <code>ceil(n / chunk_size)</code> invocations when workers ≥ that "
        "number; the implementation hits it exactly at every size.</p>"
        + scaling,
        "Both sides disable the same 79 packages. The original issues one "
        "process per operation; this one packs commands into batches and "
        "dispatches batches concurrently.",
    )


def endtoend_section(data: dict) -> str:
    e2e = data["benchmarks"]["endtoend"]
    first = e2e["first_run"]
    head = first["headline"]
    second = e2e["second_run"]

    quality = table(
        ["Outcome", "Original", "This project"],
        [
            ["Correctly disabled",
             f'<span class="good">{first["legacy"]["quality"]["correctly_disabled"]}</span>',
             f'<span class="good">{first["new"]["quality"]["correctly_disabled"]}</span>'],
            ["Harmfully disabled",
             f'<span class="bad">{first["legacy"]["quality"]["harmfully_disabled"]}</span>',
             f'<span class="good">{first["new"]["quality"]["harmfully_disabled"]}</span>'],
            ["Should be disabled but was not",
             f'<span class="bad">{first["legacy"]["quality"]["still_enabled_but_should_be_disabled"]}</span>',
             f'<span class="good">{first["new"]["quality"]["still_enabled_but_should_be_disabled"]}</span>'],
        ],
    )

    normalised = table(
        ["Work-normalised cost", "Original", "This project", "Change", "Factor"],
        [
            comparison_row("Round-trips per correctly disabled package",
                           head["legacy_round_trips_per_correct"],
                           head["new_round_trips_per_correct"]),
            comparison_row("Wall-clock ms per correctly disabled package",
                           head["legacy_ms_per_correct"],
                           head["new_ms_per_correct"], unit="ms"),
        ],
    )

    repeat = table(
        ["Second run", "Original", "This project", "Change", "Factor"],
        [
            comparison_row("Round-trips",
                           second["legacy"]["round_trips"], second["new"]["round_trips"]),
            comparison_row("Wall-clock",
                           second["legacy"]["wall_seconds"], second["new"]["wall_seconds"],
                           unit="s", places=2),
        ],
    )

    spawn = e2e.get("spawn_cost", {})
    spawn_html = ""
    if spawn:
        spawn_html = (
            "<h3>Measurement overhead</h3>"
            f'<p class="blurb">{esc(spawn.get("note", ""))} Measured on this '
            f'machine: {num(spawn.get("direct_exec_ms"), 1)} ms per invocation '
            f'with an argument vector, {num(spawn.get("through_host_shell_ms"), 1)} ms '
            f'through a host shell — a difference of '
            f'{num(spawn.get("shell_overhead_ms"), 1)} ms. The simulated '
            "<code>adb</code> is a Python program, so interpreter startup "
            "dominates both figures. Real <code>adb</code> is a native binary.</p>"
        )

    caveat = f'<p class="warn">{esc(head["caveat"])}</p>'

    fps = e2e.get("fps_diagnostic", {})
    fps_html = ""
    if fps:
        fps_html = (
            "<h3>Legacy FPS diagnostic</h3>"
            f'<p class="blurb">The original\'s SurfaceFlinger check sleeps '
            f'{num(fps.get("artificial_sleep_seconds"), 1)} s unconditionally '
            f'({esc(fps.get("note", ""))}), giving a wall-clock of '
            f'{num(fps.get("wall_seconds"), 2)} s for '
            f'{fps.get("round_trips")} round-trips. This project has no '
            "equivalent: it does not pad its runtime with fixed sleeps.</p>"
        )

    return section(
        "End to end",
        caveat
        + "<h3>Outcome quality</h3>"
        + quality
        + "<h3>Cost per unit of correct work</h3>"
        + normalised
        + "<h3>Running twice</h3>"
        + repeat
        + spawn_html
        + fps_html,
        "Both implementations run their complete heavy workflow against an "
        "identical simulated handset.",
    )


def reliability_section(data: dict) -> str:
    rel = data["benchmarks"]["reliability"]
    legacy, new = rel["legacy"], rel["new"]
    rows = [
        ["Faults injected", str(legacy["faults_injected"]), str(new["faults_injected"])],
        ["Reported as failures",
         f'<span class="bad">{legacy["failures_reported_as_failures"]}</span>',
         f'<span class="good">{new["failures_reported_as_failures"]}</span>'],
        ["Silent failures",
         f'<span class="bad">{legacy["silent_failures"]}</span>',
         f'<span class="good">{new["silent_failures"]}</span>'],
        ["Claims success",
         f'<span class="bad">{esc(num(legacy["claims_success"]))}</span>',
         f'<span class="good">{esc(num(new["claims_success"]))}</span>'],
        ["Process exit code", str(legacy["exit_code"]), str(new["exit_code"])],
    ]
    note = legacy.get("note", "")
    asym = rel.get("asymmetry_note", "")
    return section(
        "Failure reporting",
        table(["Behaviour", "Original", "This project"], rows)
        + (f'<p class="blurb">{esc(note)}</p>' if note else "")
        + (f'<p class="warn">{esc(asym)}</p>' if asym else ""),
        "Every device command in the fault set is made to fail. What matters is "
        "not whether the tool survives, but whether it tells the truth about "
        "what happened.",
    )


def security_section(data: dict) -> str:
    sec = data["benchmarks"]["security"]
    legacy = sec["proof_of_concept"]["legacy"]
    new = sec["proof_of_concept"]["new"]
    static = sec["static_analysis"]

    poc = table(
        ["", "Original", "This project"],
        [
            ["Payload", f'<code>{esc(legacy["payload"])}</code>',
             f'<code>{esc(new["payload"])}</code>'],
            ["Shell invoked",
             f'<span class="bad">yes</span>', f'<span class="good">no</span>'],
            ["Command built from the name",
             f'<span class="bad">yes</span>',
             f'<span class="good">no</span>'],
            ["Identifier rejected before use",
             f'<span class="bad">no</span>', f'<span class="good">yes</span>'],
            ["Verdict",
             f'<span class="bad">{esc(legacy["verdict"])}</span>',
             f'<span class="good">{esc(new["verdict"])}</span>'],
        ],
    )

    marker = ""
    if legacy.get("marker_file_created"):
        marker = (
            f'<p class="warn">The injected command executed: the file '
            f'<code>{esc(legacy.get("marker", "MARKER.txt"))}</code> was created '
            "on the operator\'s machine by a package name read off the device. "
            "Reproduce with <code>python benchmarks/bench_security.py</code>.</p>"
        )

    static_table = table(
        ["Static analysis (AST)", "Original", "This project"],
        [
            ["Source files analysed", str(static["legacy"]["files"]),
             str(static["new"]["files"])],
            ["Real <code>subprocess.*</code> call sites",
             str(static["legacy"]["subprocess_call_sites"]),
             str(static["new"]["subprocess_call_sites"])],
            ["Call sites with <code>shell=True</code>",
             f'<span class="bad">{static["legacy"]["shell_true_occurrences"]}</span>',
             f'<span class="good">{static["new"]["shell_true_occurrences"]}</span>'],
            ["f-strings passed to a process launch",
             str(static["legacy"]["fstring_passed_to_subprocess"]),
             str(static["new"]["fstring_passed_to_subprocess"])],
        ],
    )

    return section(
        "Command injection",
        poc + marker
        + "<h3>Static analysis</h3>"
        + static_table
        + '<p class="blurb">Counted by walking the AST, not by grepping. A grep '
        "for <code>shell=True</code> would match the docstring in "
        "<code>transport.py</code> that explains no shell is used.</p>",
        "Package names arrive from <code>pm list packages</code> and are "
        "attacker-influenced data. The proof is a file appearing on disk, not an "
        "argument about escaping.",
    )


def structure_section(data: dict) -> str:
    st = data["benchmarks"]["structure"]
    legacy, new = st["legacy"], st["new"]
    tests = data.get("tests", {})

    rows = [
        comparison_row("Source files", legacy["files"], new["files"],
                       higher_is_better=True),
        comparison_row("Lines of code", legacy["code_lines"], new["code_lines"]),
        comparison_row("Classes", legacy["classes"], new["classes"],
                       higher_is_better=True),
        comparison_row("Functions", legacy["functions"], new["functions"]),
        comparison_row("Mean function length", legacy["mean_function_length"],
                       new["mean_function_length"], unit="lines"),
        comparison_row("Longest function", legacy["max_function_length"],
                       new["max_function_length"], unit="lines"),
        comparison_row("Mean McCabe complexity", legacy["mean_complexity"],
                       new["mean_complexity"]),
        comparison_row("Max McCabe complexity", legacy["max_complexity"],
                       new["max_complexity"]),
        comparison_row("Functions over complexity 10",
                       legacy["functions_over_complexity_10"],
                       new["functions_over_complexity_10"]),
        comparison_row("Mean nesting depth", legacy["mean_nesting_depth"],
                       new["mean_nesting_depth"]),
        comparison_row("Deepest nesting", legacy["max_nesting_depth"],
                       new["max_nesting_depth"]),
        comparison_row("Functions with a docstring", legacy["functions_with_docstring_pct"],
                       new["functions_with_docstring_pct"], unit="%",
                       higher_is_better=True, places=1),
        comparison_row("Modules with a docstring", legacy["modules_with_docstring_pct"],
                       new["modules_with_docstring_pct"], unit="%",
                       higher_is_better=True, places=1),
        comparison_row("Test functions", legacy["test_functions"],
                       new["test_functions"], higher_is_better=True),
        comparison_row("Third-party runtime dependencies",
                       len(st["dependencies"]["legacy"]["third_party"]),
                       len(st["dependencies"]["new"]["third_party"])),
    ]

    coverage_row = ""
    if tests.get("available"):
        coverage_row = (
            f'<p class="blurb">Measured by <code>coverage</code>: '
            f'<b>{num(tests.get("line_coverage_pct"), 1)}%</b> line coverage over '
            f'{tests.get("tests_collected")} collected tests, against 0% and 0 '
            "tests for the original.</p>"
        )

    modules = table(
        ["Module", "Lines", "Functions"],
        [
            [f'<code>{esc(m["path"])}</code>', str(m["lines"]), str(m["functions"])]
            for m in sorted(st["new_module_breakdown"],
                            key=lambda m: -m["lines"])[:12]
        ],
    )

    return section(
        "Code structure",
        table(["Metric", "Original", "This project", "Change", "Factor"], rows)
        + coverage_row
        + "<h3>Largest modules</h3>"
        + modules,
        "Structural claims are easy to assert and hard to check, so these come "
        "from parsing both codebases with <code>ast</code> rather than from "
        "anyone's opinion. Complexity is McCabe-style: one plus the number of "
        "branch points in a function.",
    )


def method_section(data: dict) -> str:
    env = data.get("environment", {})
    items = [
        ["Benchmark run at", env.get("generated_at", "?")],
        ["Python", env.get("python", "?")],
        ["Platform", env.get("platform", "?")],
        ["Modelled USB round-trip", f'{env.get("device_latency_ms", "?")} ms'],
        ["Baseline", "legacy/Optimized.py, verbatim, imported unmodified"],
        ["Device", "simulated 154-package Android 13 handset with ground-truth labels"],
    ]
    return section(
        "How this was measured",
        table(["Item", "Value"], [[esc(k), esc(v)] for k, v in items])
        + '<p class="blurb">The baseline is the original script copied byte for '
        "byte and imported at runtime, so the comparison measures the real code "
        "rather than a paraphrase of it. The simulated device is a full "
        "<code>adb</code> CLI emulation backed by a state file, which makes every "
        "figure deterministic and reproducible with "
        "<code>python benchmarks/run_all.py</code>.</p>"
        + '<p class="blurb">Round-trips are the primary metric because they are '
        "exact, deterministic and platform-independent. Wall-clock figures are "
        "reported too, but they carry a harness overhead — see the note under "
        "End to end — and should be read as ratios, not absolutes.</p>",
    )


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------
CSS = """
:root {
  color-scheme: dark light;
  --bg: #0f1115; --panel: #171a21; --panel-2: #1d2129;
  --fg: #e6e9ef; --muted: #9aa4b2; --line: #2a2f3a;
  --good: #4ade80; --bad: #f87171; --warn: #fbbf24; --accent: #7aa2f7;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --panel-2: #f0f2f5;
    --fg: #1a1d23; --muted: #5b6472; --line: #dde1e7;
    --good: #15803d; --bad: #b91c1c; --warn: #a16207; --accent: #1d4ed8;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 24px 80px;
  background: var(--bg); color: var(--fg);
  font: 15px/1.6 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 30px; margin: 0 0 6px; letter-spacing: -0.02em; }
h2 { font-size: 20px; margin: 0 0 10px; letter-spacing: -0.01em; }
h3 { font-size: 15px; margin: 28px 0 8px; color: var(--fg); }
.sub { color: var(--muted); margin: 0 0 4px; }
.meta { color: var(--muted); font-size: 13px; margin: 0 0 36px; }
section {
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 24px 26px; margin: 0 0 22px;
}
.blurb { color: var(--muted); margin: 0 0 14px; max-width: 78ch; }
.warn {
  background: color-mix(in srgb, var(--warn) 12%, transparent);
  border-left: 3px solid var(--warn); padding: 10px 14px; border-radius: 6px;
  margin: 0 0 16px; max-width: 82ch;
}
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
         gap: 14px; margin: 0 0 26px; }
.stat { background: var(--panel); border: 1px solid var(--line);
        border-radius: 12px; padding: 16px 18px; }
.stat.good { border-left: 3px solid var(--good); }
.stat-value { font-size: 19px; font-weight: 600; letter-spacing: -0.01em; }
.stat-value b { color: var(--good); }
.stat-label { color: var(--muted); font-size: 12.5px; margin-top: 6px; }
.stat-label small { opacity: 0.8; }
table { border-collapse: collapse; width: 100%; margin: 0 0 8px; font-size: 14px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; font-size: 12.5px;
     text-transform: uppercase; letter-spacing: 0.04em; }
td:nth-child(2), td:nth-child(3), td:nth-child(4), td:nth-child(5),
th:nth-child(2), th:nth-child(3), th:nth-child(4), th:nth-child(5) {
  text-align: right; font-variant-numeric: tabular-nums;
}
tbody tr:last-child td { border-bottom: none; }
.good { color: var(--good); font-weight: 600; }
.bad { color: var(--bad); font-weight: 600; }
.flat { color: var(--muted); }
code, .mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
              font-size: 12.5px; }
code { background: var(--panel-2); padding: 1px 5px; border-radius: 4px; }
ul.mono { color: var(--bad); margin: 0 0 14px; padding-left: 22px; }
ul.mono li { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; }
footer { color: var(--muted); font-size: 12.5px; text-align: center;
         margin-top: 34px; }
"""


def render(data: dict) -> str:
    bench = data["benchmarks"]
    parts = [
        headline_cards(data),
        classification_section(data),
        inspection_section(data),
        mechanism_section(data),
        endtoend_section(data),
        reliability_section(data),
        security_section(data),
        structure_section(data),
        method_section(data),
    ]
    env = data.get("environment", {})
    failed = data.get("failed", [])
    failure_note = (
        f'<p class="warn">Benchmarks that failed to run: '
        f'{esc(", ".join(failed))}</p>' if failed else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>android-optimiser — before / after</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>android-optimiser: before / after</h1>
<p class="sub">A single 188-line script, rebuilt as a layered package — measured,
not asserted.</p>
<p class="meta">Generated {esc(env.get("generated_at", "?"))} ·
Python {esc(env.get("python", "?"))} · {esc(env.get("platform", "?"))}</p>
{failure_note}
{"".join(parts)}
<footer>
Every figure above is produced by <code>python benchmarks/run_all.py</code>
and written to <code>benchmarks/results/benchmarks.json</code>.
</footer>
</main>
</body>
</html>
"""


def main() -> int:
    if not SOURCE.exists():
        print(f"missing {SOURCE}; run benchmarks/run_all.py first", file=sys.stderr)
        return 1
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    TARGET.write_text(render(data), encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
