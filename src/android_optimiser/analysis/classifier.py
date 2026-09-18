"""Package classification.

The legacy implementation decided what to disable with this:

    known_bloat_keywords = ["facebook", "netflix", "tiktok", "instagram",
                            "bloat", "carrier", "partner", "games",
                            "analytics", "bixby", "duo"]
    if any(keyword in pkg.lower() for keyword in known_bloat_keywords):
        flagged_bloat.append(pkg)

That is a substring test against a hand-typed list, applied only to the output
of ``pm list packages -3``.  It has two structural failures:

* it never sees the ~70% of preloads that live in system partitions, because
  ``-3`` excludes them;
* it cannot tell a preinstalled app from one the user installed, so it happily
  recommends disabling things like ``com.duosecurity.duomobile`` (a work 2FA
  app, caught by the ``"duo"`` keyword).

This classifier instead reasons from evidence, in a fixed precedence order.  Each
rule is a separate method that either returns a verdict or declines to decide, so
the precedence is visible as a list rather than buried in nested conditionals:

1. **Is it a legal identifier?**  A package name is untrusted input.
2. **Did the user install it?**  A Play Store installer record on a non-system
   partition means the user chose this app.  It is never touched, regardless of
   what its name contains.
3. **Is it protected?**  Exact names and namespaces that break the device.
4. **Is it a known preload family?**  Vendor/prefix knowledge base.
5. **Does it look like telemetry?**  Vendor-independent name patterns.
6. **Is it carrier provisioning?**  Carrier namespaces.
7. **Otherwise?**  An unknown preinstalled package is reported as CAUTION, never
   as safe, and no profile disables it automatically.

The precedence is the whole design.  Evidence about *provenance* outranks
evidence about *naming*, which is why the three false positives that the legacy
keyword list produces cannot happen here.
"""

from __future__ import annotations

from ..config import PACKAGE_NAME_RE
from ..domain import Classification, Impact, PackageRecord, Risk
from . import knowledge

#: Score bands.  Higher means safer to disable.
SCORE_NEVER = 0
SCORE_UNKNOWN_SYSTEM = 35
SCORE_UNKNOWN_PRELOAD = 50
SCORE_TELEMETRY_PATTERN = 82
SCORE_CARRIER = 88
SCORE_VENDOR = 92
SCORE_VENDOR_EXACT = 96


class PackageClassifier:
    """Turns :class:`PackageRecord` observations into :class:`Classification` verdicts."""

    def __init__(self, *, treat_unknown_preload_as_caution: bool = True):
        self.treat_unknown_preload_as_caution = treat_unknown_preload_as_caution

    # ------------------------------------------------------------------
    def classify(self, record: PackageRecord) -> Classification:
        """Apply the rule chain in precedence order."""
        for rule in self._rules():
            verdict = rule(record)
            if verdict is not None:
                return verdict
        return self._unknown(record)

    def _rules(self):
        return (
            self._illegal_identifier,
            self._user_installed,
            self._protected,
            self._known_vendor,
            self._telemetry_pattern,
            self._carrier_provisioning,
        )

    def classify_all(self, records: list[PackageRecord]) -> list[Classification]:
        return [self.classify(r) for r in records]

    # ------------------------------------------------------------------
    # rules: each returns a verdict, or None to defer to the next rule
    # ------------------------------------------------------------------
    def _illegal_identifier(self, record: PackageRecord) -> Classification | None:
        """A package name is data read off the device, i.e. untrusted input.

        Rejecting it here means no downstream code ever gets the opportunity to
        interpolate it into a command.
        """
        if PACKAGE_NAME_RE.match(record.name):
            return None
        return Classification(
            record=record,
            risk=Risk.NEVER,
            category="invalid-identifier",
            score=SCORE_NEVER,
            confidence=0.99,
            rationale=(
                "not a legal Android package identifier; refusing to build any "
                "command from it"
            ),
            impact=Impact.NOTABLE,
        )

    def _user_installed(self, record: PackageRecord) -> Classification | None:
        """Provenance outranks naming.  This rule kills the false positives."""
        if not record.is_user_installed:
            return None
        return Classification(
            record=record,
            risk=Risk.NEVER,
            category="user-app",
            score=SCORE_NEVER,
            confidence=0.98,
            rationale=(
                f"installed by the user via {record.installer}; disabling it "
                "would remove an app they chose to have"
            ),
            vendor=self._vendor_of(record.name) or "user",
            impact=Impact.NOTABLE,
        )

    def _protected(self, record: PackageRecord) -> Classification | None:
        protected, reason = knowledge.is_never_disable(record.name)
        if not protected:
            return None
        return Classification(
            record=record,
            risk=Risk.NEVER,
            category="protected",
            score=SCORE_NEVER,
            confidence=0.99,
            rationale=reason,
            vendor=self._vendor_of(record.name) or "",
            impact=Impact.NOTABLE,
        )

    def _known_vendor(self, record: PackageRecord) -> Classification | None:
        rule = knowledge.lookup_vendor(record.name)
        if rule is None:
            return None
        exact = record.name == rule.prefix.rstrip(".")
        return Classification(
            record=record,
            risk=rule.risk,
            category=rule.category,
            score=SCORE_VENDOR_EXACT if exact else SCORE_VENDOR,
            confidence=0.95 if exact else 0.9,
            rationale=rule.rationale or f"{rule.vendor} preload",
            vendor=rule.vendor,
            impact=rule.impact,
        )

    def _telemetry_pattern(self, record: PackageRecord) -> Classification | None:
        token = knowledge.telemetry_hit(record.name)
        if token is None:
            return None
        return Classification(
            record=record,
            risk=Risk.SAFE,
            category="telemetry",
            score=SCORE_TELEMETRY_PATTERN,
            confidence=0.82,
            rationale=f"name contains diagnostics marker {token!r}",
            vendor=self._vendor_of(record.name) or "",
            impact=Impact.NONE,
        )

    def _carrier_provisioning(self, record: PackageRecord) -> Classification | None:
        prefix = knowledge.carrier_hit(record.name)
        if prefix is None:
            return None
        return Classification(
            record=record,
            risk=Risk.SAFE,
            category="carrier",
            score=SCORE_CARRIER,
            confidence=0.85,
            rationale=f"carrier provisioning namespace {prefix!r}",
            vendor="Carrier",
            impact=Impact.MINOR,
        )

    # ------------------------------------------------------------------
    def _unknown(self, record: PackageRecord) -> Classification:
        """Nothing matched.  Never claim safety on a guess."""
        if not record.is_preinstalled:
            return Classification(
                record=record,
                risk=Risk.CAUTION,
                category="unknown",
                score=SCORE_UNKNOWN_SYSTEM,
                confidence=0.25,
                rationale="insufficient evidence to classify",
                impact=Impact.NOTABLE,
            )

        if record.partition.is_system:
            score, confidence = SCORE_UNKNOWN_SYSTEM, 0.3
            where = f"system partition ({record.partition.value})"
        else:
            score, confidence = SCORE_UNKNOWN_PRELOAD, 0.45
            where = "pushed into /data/app with no store installer"

        risk = Risk.CAUTION if self.treat_unknown_preload_as_caution else Risk.SAFE
        return Classification(
            record=record,
            risk=risk,
            category="unknown-preload",
            score=score,
            confidence=confidence,
            rationale=(
                f"preinstalled ({where}) but not in the knowledge base; "
                "reported for review rather than disabled automatically"
            ),
            vendor=self._vendor_of(record.name) or "",
            impact=Impact.NOTABLE,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _vendor_of(package: str) -> str | None:
        rule = knowledge.lookup_vendor(package)
        return rule.vendor if rule else None


def summarise(classifications: list[Classification]) -> dict[str, int]:
    """Counts by risk band, for reporting."""
    out = {Risk.SAFE.value: 0, Risk.CAUTION.value: 0, Risk.NEVER.value: 0}
    for c in classifications:
        out[c.risk.value] += 1
    return out
