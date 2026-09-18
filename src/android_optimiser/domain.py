"""Core domain vocabulary shared by analysis, planning and execution.

Keeping these types in one place is what lets the analysis layer, the planner
and the executor be developed and tested independently: they agree on what a
"package" is and what "risk" means, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Risk(str, Enum):
    """How safe it is to disable something."""

    SAFE = "safe"
    """Well-understood preload.  Disabling it is reversible and low-impact."""

    CAUTION = "caution"
    """Probably fine, but the tool is not confident.  Never auto-applied."""

    NEVER = "never"
    """Disabling this breaks the device or removes something the user chose."""

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class Impact(str, Enum):
    """User-visible consequence of a successful action."""

    NONE = "none"
    MINOR = "minor"
    NOTABLE = "notable"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class Partition(str, Enum):
    """Where an APK physically lives."""

    SYSTEM = "system"
    VENDOR = "vendor"
    PRODUCT = "product"
    SYSTEM_EXT = "system_ext"
    PRELOAD = "preload"
    DATA = "data"
    UNKNOWN = "unknown"

    @property
    def is_system(self) -> bool:
        return self is not Partition.DATA and self is not Partition.UNKNOWN


def partition_from_path(path: str) -> Partition:
    """Classify an APK path into a partition.

    ``pm list packages -f`` gives paths like ``/system/priv-app/Foo/Foo.apk`` or
    ``/data/app/~~abc/com.bar-baz/base.apk``; the leading component is the
    partition.
    """
    if not path:
        return Partition.UNKNOWN
    cleaned = path.strip()
    if not cleaned.startswith("/"):
        return Partition.UNKNOWN
    head = cleaned.lstrip("/").split("/", 1)[0]
    mapping = {
        "system": Partition.SYSTEM,
        "vendor": Partition.VENDOR,
        "product": Partition.PRODUCT,
        "system_ext": Partition.SYSTEM_EXT,
        "preload": Partition.PRELOAD,
        "data": Partition.DATA,
    }
    return mapping.get(head, Partition.UNKNOWN)


@dataclass(frozen=True)
class PackageRecord:
    """One installed package as observed on the device."""

    name: str
    path: str = ""
    partition: Partition = Partition.UNKNOWN
    third_party: bool = False
    installer: str | None = None
    enabled: bool = True

    @property
    def has_store_installer(self) -> bool:
        """True when a real installer package recorded the install."""
        return bool(self.installer) and self.installer not in ("null", "none")

    @property
    def is_preinstalled(self) -> bool:
        """Shipped with the firmware, or pushed there during provisioning.

        A ``/data/app`` package with no installer record was not installed by
        the user through a store; it was placed there by the OEM or carrier.
        """
        if self.partition.is_system:
            return True
        return not self.has_store_installer

    @property
    def is_user_installed(self) -> bool:
        return not self.partition.is_system and self.has_store_installer

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "partition": self.partition.value,
            "third_party": self.third_party,
            "installer": self.installer,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class Classification:
    """The verdict on one package."""

    record: PackageRecord
    risk: Risk
    category: str
    score: int
    confidence: float
    rationale: str
    vendor: str = ""
    impact: Impact = Impact.NONE

    @property
    def name(self) -> str:
        return self.record.name

    @property
    def actionable(self) -> bool:
        """Whether a profile is allowed to disable this automatically."""
        return self.risk is Risk.SAFE

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "risk": self.risk.value,
            "category": self.category,
            "vendor": self.vendor,
            "score": self.score,
            "confidence": round(self.confidence, 3),
            "impact": self.impact.value,
            "rationale": self.rationale,
            "partition": self.record.partition.value,
            "installer": self.record.installer,
            "enabled": self.record.enabled,
        }


@dataclass
class DeviceSnapshot:
    """Everything the planner needs to know about the device, in one object."""

    serial: str
    info: dict = field(default_factory=dict)
    packages: list[PackageRecord] = field(default_factory=list)
    settings: dict[str, str] = field(default_factory=dict)
    doze_state: str = "unknown"
    classifications: list[Classification] = field(default_factory=list)

    def package(self, name: str) -> PackageRecord | None:
        for record in self.packages:
            if record.name == name:
                return record
        return None

    @property
    def enabled_packages(self) -> list[PackageRecord]:
        return [p for p in self.packages if p.enabled]

    @property
    def disabled_packages(self) -> list[PackageRecord]:
        return [p for p in self.packages if not p.enabled]

    def candidates(self, min_risk: Risk = Risk.SAFE) -> list[Classification]:
        allowed = {Risk.SAFE} if min_risk is Risk.SAFE else {Risk.SAFE, Risk.CAUTION}
        return [c for c in self.classifications if c.risk in allowed]
