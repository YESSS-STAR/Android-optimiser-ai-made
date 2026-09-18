"""Device inspection.

Builds a complete :class:`DeviceSnapshot` — device identity, every installed
package with its partition and installer, the current settings, and the doze
state — using **one** batched round-trip after device discovery.

The legacy equivalent issued six round-trips (``adb devices``, four separate
``getprop`` lookups, one ``pm list packages``) and still learned less, because
it never asked for installers, partitions or the disabled set.
"""

from __future__ import annotations

import re

from ..config import (
    INSPECT_DISABLED_PACKAGES,
    INSPECT_DOZE,
    INSPECT_SETTINGS,
    INSPECT_SYSTEM_PACKAGES,
    INSPECT_THIRD_PARTY_PACKAGES,
)
from ..core.adb import AdbClient, parse_props, parse_settings
from ..core.device import DeviceInfo
from ..domain import DeviceSnapshot, PackageRecord, Partition, partition_from_path
from .classifier import PackageClassifier

_INSTALLER_SEP = " installer="
_DOZE_STATE_RE = re.compile(r"mState=(\w+)")

#: Positions of each command inside the single batched inspection call.
_BATCH_INDEX = {
    "props": 0,
    "system_packages": 1,
    "third_party_packages": 2,
    "disabled_packages": 3,
    "settings": 4,
    "doze": 5,
}


def _split_installer(body: str) -> tuple[str, str | None]:
    """Separate a trailing `` installer=<pkg>`` marker from the payload."""
    if _INSTALLER_SEP not in body:
        return body, None
    head, _, raw = body.partition(_INSTALLER_SEP)
    raw = raw.strip()
    return head, None if raw in ("", "null", "none") else raw


def _split_path_and_name(body: str) -> tuple[str, str]:
    """``/data/app/x.apk=com.foo`` -> ``("/data/app/x.apk", "com.foo")``."""
    if "=" not in body:
        return "", body.strip()
    path, _, name = body.rpartition("=")
    return path.strip(), name.strip()


def parse_package_line(line: str, *, third_party: bool) -> PackageRecord | None:
    """Parse one line of ``pm list packages`` output.

    Handles all three shapes the tool asks for::

        package:com.foo
        package:/system/app/Foo/foo.apk=com.foo
        package:/data/app/~~x/com.bar-y/base.apk=com.bar installer=com.android.vending
    """
    line = line.strip()
    if not line.startswith("package:"):
        return None

    body, installer = _split_installer(line[len("package:") :])
    path, name = _split_path_and_name(body)
    if not name:
        return None

    partition = partition_from_path(path) if path else None
    if partition is None:
        partition = Partition.DATA if third_party else Partition.UNKNOWN

    return PackageRecord(
        name=name,
        path=path,
        partition=partition,
        third_party=third_party,
        installer=installer,
        enabled=True,
    )


def parse_disabled_list(raw: str) -> set[str]:
    return {
        line.strip()[len("package:") :]
        for line in raw.splitlines()
        if line.strip().startswith("package:")
    }


def parse_doze_state(raw: str) -> str:
    match = _DOZE_STATE_RE.search(raw)
    if match:
        return match.group(1)
    if "Now forced in to deep idle mode" in raw:
        return "idle"
    return "unknown"


def _collect_packages(batch) -> dict[str, PackageRecord]:
    """Merge the system and third-party listings into one name-keyed map.

    Third-party entries are parsed second so that, for the rare package that
    appears in both listings, the third-party view wins -- it is the one that
    carries the installer.
    """
    records: dict[str, PackageRecord] = {}
    for slot, third_party in (("system_packages", False), ("third_party_packages", True)):
        raw = batch.by_index(_BATCH_INDEX[slot]).stdout
        for line in raw.splitlines():
            record = parse_package_line(line, third_party=third_party)
            if record:
                records[record.name] = record
    return records


def _apply_disabled(
    records: dict[str, PackageRecord], disabled: set[str]
) -> list[PackageRecord]:
    """Mark which packages are already disabled, and sort by name."""
    final = [
        PackageRecord(
            name=record.name,
            path=record.path,
            partition=record.partition,
            third_party=record.third_party,
            installer=record.installer,
            enabled=record.name not in disabled,
        )
        for record in records.values()
    ]
    final.sort(key=lambda r: r.name)
    return final


class Inspector:
    """Reads device state and classifies what it finds."""

    def __init__(self, client: AdbClient, classifier: PackageClassifier | None = None):
        self.client = client
        self.classifier = classifier or PackageClassifier()

    # ------------------------------------------------------------------
    def snapshot(self) -> DeviceSnapshot:
        serial = self.client.require_device()

        batch = self.client.shell_batch(
            [
                "getprop",
                INSPECT_SYSTEM_PACKAGES,
                INSPECT_THIRD_PARTY_PACKAGES,
                INSPECT_DISABLED_PACKAGES,
                INSPECT_SETTINGS,
                INSPECT_DOZE,
            ]
        )

        props_raw = batch.by_index(_BATCH_INDEX["props"]).stdout
        settings_raw = batch.by_index(_BATCH_INDEX["settings"]).stdout

        # Prime the caches from the data we already paid for, so later reads of
        # properties and settings cost nothing.
        props = self.client.prime_props(props_raw)
        settings = self.client.prime_settings(settings_raw)

        records = _collect_packages(batch)
        disabled = parse_disabled_list(batch.by_index(_BATCH_INDEX["disabled_packages"]).stdout)
        packages = _apply_disabled(records, disabled)

        info = DeviceInfo.from_props(serial, props or parse_props(props_raw))

        return DeviceSnapshot(
            serial=serial,
            info=info.as_dict(),
            packages=packages,
            settings=settings or parse_settings(settings_raw),
            doze_state=parse_doze_state(batch.by_index(_BATCH_INDEX["doze"]).stdout),
            classifications=self.classifier.classify_all(packages),
        )
