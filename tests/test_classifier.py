"""Tests for the classifier.

The central claim is precedence: evidence about *provenance* beats evidence
about *naming*.  Several of these tests exist purely to pin that down, because
it is the property that eliminates the legacy implementation's false positives.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import device_state as ds

from android_optimiser.analysis.classifier import PackageClassifier
from android_optimiser.domain import PackageRecord, Partition, Risk, partition_from_path

ROOT = Path(__file__).resolve().parents[1]
HELDOUT = ROOT / "benchmarks" / "corpus" / "heldout_packages.json"


def record(name: str, path: str = "/system/app/X/x.apk", installer=None, enabled=True):
    return PackageRecord(
        name=name,
        path=path,
        partition=partition_from_path(path),
        third_party=path.startswith("/data/"),
        installer=installer,
        enabled=enabled,
    )


@pytest.fixture()
def classifier() -> PackageClassifier:
    return PackageClassifier()


# --------------------------------------------------------------------------
# precedence
# --------------------------------------------------------------------------
def test_user_installed_app_wins_over_a_bloat_looking_name(classifier):
    """The exact false positive the legacy keyword list produces."""
    c = classifier.classify(
        record(
            "com.duosecurity.duomobile",
            "/data/app/~~x/com.duosecurity.duomobile-y/base.apk",
            installer="com.android.vending",
        )
    )
    assert c.risk is Risk.NEVER
    assert c.category == "user-app"


def test_user_installed_app_wins_even_with_a_bloat_vendor_prefix(classifier):
    c = classifier.classify(
        record(
            "com.facebook.katana",
            "/data/app/~~x/com.facebook.katana-y/base.apk",
            installer="com.android.vending",
        )
    )
    assert c.risk is Risk.NEVER, "a user-installed Facebook is the user's choice"


def test_preinstalled_package_in_data_without_installer_is_treated_as_bloat(classifier):
    c = classifier.classify(
        record("com.facebook.katana", "/data/app/~~x/com.facebook.katana-y/base.apk")
    )
    assert c.risk is Risk.SAFE
    assert c.vendor == "Meta"


def test_data_partition_without_installer_counts_as_preinstalled(classifier):
    rec = record("com.example.thing", "/data/app/~~x/com.example.thing-y/base.apk")
    assert rec.is_preinstalled
    assert not rec.is_user_installed


# --------------------------------------------------------------------------
# protections
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "com.android.systemui",
        "com.android.settings",
        "com.android.phone",
        "com.android.providers.telephony",
        "com.android.providers.media",
        "com.google.android.gms",
        "com.android.vending",
        "com.sec.android.app.launcher",
        "com.google.android.inputmethod.latin",
        "com.samsung.android.honeyboard",
    ],
)
def test_core_packages_are_never_disable(classifier, name):
    assert classifier.classify(record(name, "/system/priv-app/X/x.apk")).risk is Risk.NEVER


def test_protected_namespace_carve_out(classifier):
    """A telemetry component inside a protected namespace must still be safe."""
    c = classifier.classify(
        record("com.google.android.gms.location.history", "/system/priv-app/X/x.apk")
    )
    assert c.risk is Risk.SAFE
    assert c.category == "telemetry"


def test_illegal_identifier_is_refused_before_anything_else(classifier):
    c = classifier.classify(record("com.facebook.evil& echo pwned"))
    assert c.risk is Risk.NEVER
    assert c.category == "invalid-identifier"


# --------------------------------------------------------------------------
# positive classification
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,vendor",
    [
        ("com.facebook.katana", "Meta"),
        ("com.zhiliaoapp.musically", "ByteDance"),
        ("com.netflix.mediaclient", "Netflix"),
        ("com.amazon.mShop.android.shopping", "Amazon"),
        ("com.samsung.android.bixby.agent", "Samsung"),
        ("com.miui.analytics", "Xiaomi"),
        ("com.motorola.genie", "Motorola"),
    ],
)
def test_known_vendor_families_are_safe(classifier, name, vendor):
    c = classifier.classify(record(name))
    assert c.risk is Risk.SAFE
    assert c.vendor == vendor


def test_tiktok_is_caught_despite_an_unknown_package_name(classifier):
    """TikTok's real identifier contains neither 'tiktok' nor 'musically'."""
    assert classifier.classify(record("com.zhiliaoapp.musically")).risk is Risk.SAFE
    assert classifier.classify(record("com.ss.android.ugc.trill")).risk is Risk.SAFE


def test_telemetry_pattern_generalises_across_vendors(classifier):
    c = classifier.classify(record("com.nobody.knows.this.telemetry"))
    assert c.risk is Risk.SAFE
    assert c.category == "telemetry"


def test_carrier_namespace_is_safe(classifier):
    c = classifier.classify(record("com.vodafone.mCare"))
    assert c.risk is Risk.SAFE


def test_unknown_preinstalled_package_is_caution_not_safe(classifier):
    c = classifier.classify(record("com.unknownvendor.mystery"))
    assert c.risk is Risk.CAUTION
    assert c.category == "unknown-preload"


# --------------------------------------------------------------------------
# held-out generalisation
# --------------------------------------------------------------------------
def test_heldout_corpus_is_classified_correctly(classifier):
    entries = json.loads(HELDOUT.read_text(encoding="utf-8"))["packages"]
    wrong = []
    for entry in entries:
        rec = record(
            entry["name"],
            f"/{entry['partition']}/app/X/x.apk",
            installer=entry["installer"],
        )
        verdict = classifier.classify(rec)
        predicted = verdict.risk is Risk.SAFE
        if predicted != entry["safe"]:
            wrong.append((entry["name"], entry["safe"], verdict.risk.value, verdict.category))
    assert wrong == [], f"held-out misclassifications: {wrong}"


def test_heldout_positive_cases_are_not_in_the_knowledge_base():
    """Guard against the corpus being memorised rather than reasoned about.

    Only the ``safe: true`` entries are checked.  The corpus also contains a few
    deliberately-protected package names; a protection list has to name the
    packages it protects, so those are expected to appear.
    """
    from android_optimiser.analysis import knowledge

    source = Path(knowledge.__file__).read_text(encoding="utf-8")
    entries = json.loads(HELDOUT.read_text(encoding="utf-8"))["packages"]
    positives = [e["name"] for e in entries if e["safe"]]
    leaked = [name for name in positives if name in source]
    assert leaked == [], f"held-out positive packages appear verbatim in the KB: {leaked}"


def test_heldout_corpus_contains_no_vendor_rule_for_its_unknown_vendors():
    """The unknown-vendor entries must not be covered by a prefix rule either."""
    from android_optimiser.analysis import knowledge

    entries = json.loads(HELDOUT.read_text(encoding="utf-8"))["packages"]
    unknown_vendor = [e["name"] for e in entries if "unknown to the KB" in e["tests"]]
    assert unknown_vendor, "corpus should contain unknown-vendor cases"
    covered = [n for n in unknown_vendor if knowledge.lookup_vendor(n) is not None]
    assert covered == [], f"unknown-vendor cases matched a vendor rule: {covered}"


# --------------------------------------------------------------------------
# whole-device behaviour
# --------------------------------------------------------------------------
def test_full_device_classification_matches_ground_truth(classifier):
    state = ds.default_state()
    truth = ds.ground_truth()
    records = [
        PackageRecord(
            name=p["name"],
            path=p["path"],
            partition=partition_from_path(p["path"]),
            third_party=p["third_party"],
            installer=p["installer"],
            enabled=p["enabled"],
        )
        for p in state["packages"]
    ]
    predicted = {c.name for c in classifier.classify_all(records) if c.risk is Risk.SAFE}
    expected = {name for name, ok in truth.items() if ok}
    assert predicted == expected


def test_classifier_never_marks_a_protected_package_as_safe(classifier):
    state = ds.default_state()
    truth = ds.ground_truth()
    for p in state["packages"]:
        if truth[p["name"]]:
            continue
        rec = PackageRecord(
            name=p["name"],
            path=p["path"],
            partition=partition_from_path(p["path"]),
            third_party=p["third_party"],
            installer=p["installer"],
            enabled=p["enabled"],
        )
        assert classifier.classify(rec).risk is not Risk.SAFE, p["name"]
