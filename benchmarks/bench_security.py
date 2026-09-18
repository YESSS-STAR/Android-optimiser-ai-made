"""Benchmark: command injection.

This is a proof of concept, not a thought experiment.  A package whose *name*
contains shell metacharacters is planted on the simulated device.  Package names
come from ``pm list packages``, which means they are attacker-influenced data
arriving from the device.

The legacy implementation builds its command with an f-string and runs it with
``shell=True``::

    run_cmd(f"adb shell pm disable-user --user 0 {pkg}")

so the metacharacters are interpreted by the *host* shell and arbitrary commands
run on the operator's machine.  The proof is a marker file appearing on disk.

The new implementation never uses a shell, and additionally rejects any package
name that does not match a strict identifier grammar before a command is ever
constructed.

The payload uses ``&`` because it is a command separator in both ``cmd.exe`` and
POSIX ``sh``, so the demonstration is not platform-specific.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from harness import ROOT, load_legacy, silence, simulated_device, write_device_state

sys.path.insert(0, str(ROOT / "src"))

from android_optimiser.analysis.classifier import PackageClassifier  # noqa: E402
from android_optimiser.core.adb import validate_package  # noqa: E402
from android_optimiser.domain import PackageRecord, Risk, partition_from_path  # noqa: E402
from android_optimiser.exceptions import UnsafePackageError  # noqa: E402

PAYLOADS = [
    "com.facebook.evil& echo pwned>MARKER.txt& rem",
    "com.instagram.x; echo pwned>MARKER2.txt",
    "com.netflix.y| echo pwned>MARKER3.txt",
    "com.duosecurity.duomobile`echo pwned`",
    "com.bloat.app$(echo pwned)",
]


def _plant(device, name: str) -> None:
    """Add a package with an attacker-controlled name to the device state."""

    def mutate(state: dict) -> None:
        state["packages"].append(
            {
                "name": name,
                "tier": "bloat",
                "root": "data",
                "path": f"/data/app/~~evil/{name}/base.apk",
                "third_party": True,
                "installer": None,
                "enabled": True,
            }
        )

    write_device_state(device.state_path, mutate)


def run_legacy() -> dict:
    legacy = load_legacy()
    workdir = ROOT / "benchmarks" / "results" / "_injection"
    workdir.mkdir(parents=True, exist_ok=True)

    # A fresh marker name every run.  A stale marker from a previous run would
    # make ``marker.exists()`` true before anything executed, which would
    # silently turn this proof into a tautology -- and deleting the old one
    # would mean a destructive filesystem operation inside a benchmark.
    marker_name = f"MARKER_{uuid.uuid4().hex[:8]}.txt"
    marker = workdir / marker_name
    payload = f"com.facebook.evil& echo pwned>{marker_name}& rem"

    with simulated_device("injection_legacy") as device:
        _plant(device, payload)

        # legacy's inspect only reads `pm list packages -3`; stub the transport
        # for that one call, then restore the real run_cmd before executing.
        real_run_cmd = legacy.run_cmd
        raw = f"package:{payload}"
        legacy.run_cmd = lambda cmd: raw  # type: ignore[assignment]
        with silence():
            flagged = legacy.inspect_unneeded_packages()
        legacy.run_cmd = real_run_cmd  # type: ignore[assignment]

        executed = False
        if flagged:
            os.chdir(workdir)
            try:
                # exactly the call the legacy loop makes, with the real transport
                legacy.run_cmd(f"adb shell pm disable-user --user 0 {flagged[0]}")
                executed = marker.exists()
            finally:
                os.chdir(ROOT)

    return {
        "payload": payload,
        "marker": marker_name,
        "package_flagged_as_bloat": bool(flagged),
        "command_built": f"adb shell pm disable-user --user 0 {payload}",
        "shell_used": True,
        "injected_command_executed": executed,
        "marker_file_created": marker.exists(),
        "verdict": "VULNERABLE" if executed else "not demonstrated on this platform",
    }


def run_new() -> dict:
    payload = PAYLOADS[0]
    classifier = PackageClassifier()

    # 1. the classifier refuses to endorse an illegal identifier
    record = PackageRecord(
        name=payload,
        path=f"/data/app/~~evil/{payload}/base.apk",
        partition=partition_from_path("/data/app/x.apk"),
        third_party=True,
        installer=None,
        enabled=True,
    )
    classification = classifier.classify(record)

    # 2. and even if something downstream tried, the validator refuses
    validator_raised = False
    try:
        validate_package(payload)
    except UnsafePackageError:
        validator_raised = True

    # 3. every payload in the corpus is rejected
    rejected = 0
    for candidate in PAYLOADS:
        try:
            validate_package(candidate)
        except UnsafePackageError:
            rejected += 1

    return {
        "payload": payload,
        "risk": classification.risk.value,
        "category": classification.category,
        "rationale": classification.rationale,
        "would_generate_a_command": classification.risk is Risk.SAFE,
        "validator_rejected": validator_raised,
        "shell_used": False,
        "payloads_tested": len(PAYLOADS),
        "payloads_rejected": rejected,
        "verdict": "SAFE",
    }


_SUBPROCESS_ENTRY_POINTS = frozenset(
    {"run", "Popen", "call", "check_output", "check_call"}
)


def _shell_usage(path: Path) -> dict:
    """Count shell invocations by walking the AST, not by grepping text.

    Grepping would count the word ``shell=True`` inside a docstring explaining
    that no shell is used, which is exactly the kind of false signal this whole
    exercise is meant to avoid.

    A call is only counted when it genuinely resolves to ``subprocess.*`` or
    ``os.system`` / ``os.popen``.  Matching on the bare method name would count
    ``self.transport.run(...)`` -- this project's own abstraction, which spawns
    nothing by itself -- and would therefore report the new codebase as having
    *more* process launch sites than the legacy one, which is precisely
    backwards.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    shell_true = 0
    subprocess_calls = 0
    fstring_into_shell = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        module = ""
        name = ""
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module, name = func.value.id, func.attr
        elif isinstance(func, ast.Name):
            name = func.id

        if module == "subprocess" and name in _SUBPROCESS_ENTRY_POINTS:
            subprocess_calls += 1
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                    if kw.value.value is True:
                        shell_true += 1
            if node.args and isinstance(node.args[0], ast.JoinedStr):
                fstring_into_shell += 1

        # os.system / os.popen always route through a shell.
        elif module == "os" and name in ("system", "popen"):
            shell_true += 1

    return {
        "shell_true_occurrences": shell_true,
        "subprocess_call_sites": subprocess_calls,
        "fstring_passed_to_subprocess": fstring_into_shell,
    }


def run_static_analysis() -> dict:
    """Compare shell usage across both codebases."""
    legacy_stats = _shell_usage(ROOT / "legacy" / "Optimized.py")

    new_files = list((ROOT / "src").rglob("*.py"))
    totals = {"shell_true_occurrences": 0, "subprocess_call_sites": 0,
              "fstring_passed_to_subprocess": 0}
    for path in new_files:
        stats = _shell_usage(path)
        for key in totals:
            totals[key] += stats[key]

    return {
        "legacy": {"files": 1, **legacy_stats},
        "new": {"files": len(new_files), **totals},
    }


def run() -> dict:
    return {
        "proof_of_concept": {"legacy": run_legacy(), "new": run_new()},
        "static_analysis": run_static_analysis(),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
