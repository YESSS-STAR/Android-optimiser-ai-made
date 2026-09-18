"""Device identification.

Replaces the legacy ``fetch_device_info()``, which issued four separate
``adb shell getprop <key>`` round-trips and then called ``.capitalize()`` on the
brand (turning ``samsung`` into ``Samsung`` but also mangling anything with
internal capitals).

Here the whole property table is fetched once and every field is a dict lookup,
so identification costs one round-trip no matter how many attributes are wanted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .adb import AdbClient

#: Property -> DeviceInfo attribute.
_FIELD_MAP: dict[str, str] = {
    "ro.product.brand": "brand",
    "ro.product.manufacturer": "manufacturer",
    "ro.product.model": "model",
    "ro.product.device": "device",
    "ro.product.name": "product_name",
    "ro.build.version.release": "android_version",
    "ro.build.version.sdk": "sdk",
    "ro.build.version.security_patch": "security_patch",
    "ro.build.fingerprint": "fingerprint",
    "ro.build.type": "build_type",
    "ro.product.cpu.abi": "abi",
    "ro.sf.lcd_density": "density",
    "ro.debuggable": "debuggable",
}


@dataclass(frozen=True)
class DeviceInfo:
    """A stable, immutable view of the attached handset."""

    serial: str
    brand: str = "unknown"
    manufacturer: str = "unknown"
    model: str = "unknown"
    device: str = "unknown"
    product_name: str = "unknown"
    android_version: str = "unknown"
    sdk: str = "unknown"
    security_patch: str = "unknown"
    fingerprint: str = "unknown"
    build_type: str = "unknown"
    abi: str = "unknown"
    density: str = "unknown"
    debuggable: str = "0"

    @classmethod
    def from_props(cls, serial: str, props: Mapping[str, str]) -> "DeviceInfo":
        values = {attr: props.get(prop, "unknown") or "unknown" for prop, attr in _FIELD_MAP.items()}
        return cls(serial=serial, **values)

    @property
    def display_name(self) -> str:
        brand = self.brand
        pretty_brand = brand[:1].upper() + brand[1:] if brand else "Unknown"
        return f"{pretty_brand} {self.model}"

    @property
    def is_debuggable_build(self) -> bool:
        return self.debuggable == "1"

    def as_dict(self) -> dict[str, str]:
        return {
            "serial": self.serial,
            "brand": self.brand,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "device": self.device,
            "product_name": self.product_name,
            "android_version": self.android_version,
            "sdk": self.sdk,
            "security_patch": self.security_patch,
            "fingerprint": self.fingerprint,
            "build_type": self.build_type,
            "abi": self.abi,
            "density": self.density,
        }


class DeviceManager:
    """Discovers and describes devices via an :class:`AdbClient`."""

    def __init__(self, client: AdbClient):
        self.client = client

    def list_devices(self) -> list[str]:
        return self.client.devices()

    def identify(self) -> DeviceInfo:
        """One round-trip to enumerate every property, then describe the device."""
        serial = self.client.require_device()
        props = self.client.props()
        return DeviceInfo.from_props(serial, props)

    @staticmethod
    def matches(info: DeviceInfo, expected: str) -> bool:
        """Case-insensitive match against serial, model or device codename."""
        needle = expected.strip().lower()
        if not needle:
            return True
        return needle in {
            info.serial.lower(),
            info.model.lower(),
            info.device.lower(),
            info.display_name.lower(),
        }
