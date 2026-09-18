"""Benchmark: how good are the decisions?

Runs the legacy keyword matcher and the new classifier over the same devices and
scores both against ground-truth labels.

The legacy matcher is not reimplemented here — the original module is imported
and its ``inspect_unneeded_packages`` is called directly, with only ``run_cmd``
stubbed so it receives a fixed package list.  That way the comparison measures
the real code, not a paraphrase of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness import ROOT, load_legacy, silence

import device_state as ds  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))

from android_optimiser.analysis.classifier import PackageClassifier  # noqa: E402
from android_optimiser.domain import PackageRecord, Risk, partition_from_path  # noqa: E402


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def score(predicted: set[str], truth: dict[str, bool]) -> dict:
    tp = fp = fn = tn = 0
    for name, should_disable in truth.items():
        got = name in predicted
        if should_disable and got:
            tp += 1
        elif should_disable and not got:
            fn += 1
        elif not should_disable and got:
            fp += 1
        else:
            tn += 1
    result = prf(tp, fp, fn)
    result["tn"] = tn
    result["accuracy"] = round((tp + tn) / max(1, len(truth)), 4)
    return result


# --------------------------------------------------------------------------
# 1. the device the simulator models
# --------------------------------------------------------------------------
def legacy_predictions(packages: list[dict]) -> set[str]:
    """Run the ORIGINAL legacy classifier over the third-party package list."""
    legacy = load_legacy()
    raw = "\n".join(f"package:{p['name']}" for p in packages if p["third_party"])
    legacy.run_cmd = lambda cmd: raw  # type: ignore[assignment]
    with silence():
        flagged = legacy.inspect_unneeded_packages()
    return set(flagged)


def new_predictions(packages: list[dict]) -> tuple[set[str], list]:
    classifier = PackageClassifier()
    records = [
        PackageRecord(
            name=p["name"],
            path=p["path"],
            partition=partition_from_path(p["path"]),
            third_party=p["third_party"],
            installer=p["installer"],
            enabled=p["enabled"],
        )
        for p in packages
    ]
    classifications = classifier.classify_all(records)
    predicted = {c.name for c in classifications if c.risk is Risk.SAFE}
    return predicted, classifications


def bench_device_corpus() -> dict:
    state = ds.default_state()
    packages = state["packages"]
    truth = ds.ground_truth()

    legacy_pred = legacy_predictions(packages)
    new_pred, classifications = new_predictions(packages)

    legacy_score = score(legacy_pred, truth)
    new_score = score(new_pred, truth)

    # Which specific packages does each get wrong, and how badly?
    legacy_fp = sorted(n for n in legacy_pred if not truth.get(n, False))
    legacy_fn = sorted(n for n, ok in truth.items() if ok and n not in legacy_pred)

    return {
        "corpus": "simulated Samsung-class Android 13 handset",
        "packages_total": len(packages),
        "packages_safe_to_disable": sum(1 for v in truth.values() if v),
        "legacy": {**legacy_score, "predicted": len(legacy_pred)},
        "new": {**new_score, "predicted": len(new_pred)},
        "legacy_false_positives": legacy_fp,
        "legacy_false_negatives_count": len(legacy_fn),
        "legacy_false_negatives_sample": legacy_fn[:20],
        "new_classification_bands": {
            risk.value: sum(1 for c in classifications if c.risk is risk)
            for risk in Risk
        },
    }


# --------------------------------------------------------------------------
# 2. held-out corpus
# --------------------------------------------------------------------------
def bench_heldout() -> dict:
    corpus = json.loads(
        (ROOT / "benchmarks" / "corpus" / "heldout_packages.json").read_text(encoding="utf-8")
    )
    entries = corpus["packages"]
    truth = {e["name"]: e["safe"] for e in entries}

    records = [
        PackageRecord(
            name=e["name"],
            path=f"/{e['partition']}/app/X/x.apk",
            partition=partition_from_path(f"/{e['partition']}/app/X/x.apk"),
            third_party=e["partition"] == "data",
            installer=e["installer"],
            enabled=True,
        )
        for e in entries
    ]
    classifier = PackageClassifier()
    classifications = classifier.classify_all(records)
    predicted = {c.name for c in classifications if c.risk is Risk.SAFE}

    result = score(predicted, truth)

    # legacy only ever sees the third-party subset
    legacy_pool = [e for e in entries if e["partition"] == "data"]
    legacy = load_legacy()
    raw = "\n".join(f"package:{e['name']}" for e in legacy_pool)
    legacy.run_cmd = lambda cmd: raw  # type: ignore[assignment]
    with silence():
        legacy_pred = set(legacy.inspect_unneeded_packages())
    legacy_result = score(legacy_pred, truth)

    # attribute each correct verdict to the rule that produced it
    by_rule: dict[str, int] = {}
    for c in classifications:
        if c.name in truth and truth[c.name] == (c.risk is Risk.SAFE):
            by_rule[c.category] = by_rule.get(c.category, 0) + 1

    return {
        "corpus": "held-out corpus: package names absent from the knowledge base",
        "packages_total": len(entries),
        "legacy": {**legacy_result, "predicted": len(legacy_pred)},
        "new": {**result, "predicted": len(predicted)},
        "correct_by_rule": dict(sorted(by_rule.items())),
        "detail": [c.as_dict() for c in classifications],
    }


def run() -> dict:
    return {
        "device_corpus": bench_device_corpus(),
        "heldout_corpus": bench_heldout(),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
