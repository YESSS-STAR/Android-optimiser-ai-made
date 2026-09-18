"""Deterministic model of an Android device's observable state.

This module exists so that every benchmark number in this repository is
reproducible on any machine, with or without a physical phone attached.

The package table mirrors a realistic Samsung-class Android 13 handset: core
system services, OEM/carrier preloads, telemetry agents, and apps the user
genuinely installed.  Two signals are modelled explicitly because they are the
signals that actually separate "preinstalled junk" from "an app the user chose":

``root``
    The APK's partition, as ``pm list packages -f`` reports it.  ``/data/app``
    means it is not a system app; every other root shipped with the firmware.

``installer``
    As ``pm list packages -i`` reports it.  ``com.android.vending`` means the
    user installed it from the Play Store.  ``null`` on a ``/data/app`` package
    means it was pushed there by provisioning (carrier or OEM), i.e. it is
    preload junk that merely happens to live outside a system partition.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# --------------------------------------------------------------------------
# Filesystem roots.  `data` means "not a system app"; everything else shipped
# with the firmware.
# --------------------------------------------------------------------------
ROOTS: dict[str, str] = {
    "system": "/system/app",
    "priv": "/system/priv-app",
    "product": "/product/app",
    "vendor": "/vendor/app",
    "system_ext": "/system_ext/app",
    "preload": "/system/preload",
    "data": "/data/app",
}

THIRD_PARTY_ROOTS = {"data"}

VENDING = "com.android.vending"

# Ground-truth tiers.  These are LABELS, not knowledge the classifier is given:
#   core      - critical system service, disabling bricks or cripples the device
#   user      - an app the user installed on purpose, leave it alone
#   oem       - OEM preload, safe to disable
#   carrier   - carrier preload, safe to disable
#   telemetry - diagnostics/analytics agent, safe to disable
#   bloat     - preinstalled third-party app the user never asked for
SAFE_TIERS = {"oem", "carrier", "telemetry", "bloat"}
NEVER_TIERS = {"core", "user"}

# (package, tier, root, enabled)
PACKAGES: list[tuple[str, str, str, bool]] = [
    # ================= core system: must NEVER be disabled =================
    ("com.android.systemui", "core", "priv", True),
    ("com.android.settings", "core", "priv", True),
    ("com.android.phone", "core", "priv", True),
    ("com.android.providers.telephony", "core", "priv", True),
    ("com.android.providers.settings", "core", "priv", True),
    ("com.android.server.telecom", "core", "priv", True),
    ("com.android.bluetooth", "core", "priv", True),
    ("com.android.nfc", "core", "priv", True),
    ("com.android.shell", "core", "priv", True),
    ("com.android.location.fused", "core", "priv", True),
    ("com.google.android.gms", "core", "priv", True),
    ("com.google.android.gsf", "core", "priv", True),
    ("com.android.vending", "core", "priv", True),
    ("com.samsung.android.incallui", "core", "priv", True),
    ("com.samsung.android.providers.context", "core", "priv", True),
    ("com.sec.android.app.launcher", "core", "priv", True),
    ("com.google.android.inputmethod.latin", "core", "priv", True),
    ("com.samsung.android.honeyboard", "core", "priv", True),
    ("com.android.carrierconfig", "core", "priv", True),
    ("com.android.carrierdefaultapp", "core", "priv", True),
    ("com.android.se", "core", "priv", True),
    ("com.android.wallpaper", "core", "system", True),
    ("com.android.keychain", "core", "priv", True),
    ("com.android.certinstaller", "core", "priv", True),
    ("com.android.packageinstaller", "core", "priv", True),
    ("com.android.permissioncontroller", "core", "priv", True),
    ("com.android.providers.contacts", "core", "priv", True),
    ("com.android.providers.calendar", "core", "priv", True),
    ("com.android.providers.media", "core", "priv", True),
    ("com.android.providers.downloads", "core", "priv", True),
    ("com.android.externalstorage", "core", "priv", True),
    ("com.android.mtp", "core", "priv", True),
    ("com.android.deskclock", "core", "system", True),
    ("com.android.emergency", "core", "priv", True),
    ("com.android.wallpaper.livepicker", "core", "system", True),
    ("com.sec.android.app.camera", "core", "priv", True),
    ("com.sec.android.gallery3d", "core", "priv", True),
    ("com.samsung.android.app.contacts", "core", "priv", True),
    ("com.samsung.android.dialer", "core", "priv", True),
    ("com.samsung.android.messaging", "core", "priv", True),
    ("com.samsung.android.calendar", "core", "system", True),
    ("com.sec.android.app.myfiles", "core", "priv", True),
    ("com.android.documentsui", "core", "priv", True),
    ("com.samsung.android.forest", "core", "system", True),

    # ================= OEM preloads: safe to disable =================
    ("com.samsung.android.bixby.agent", "oem", "priv", True),
    ("com.samsung.android.bixby.wakeup", "oem", "priv", True),
    ("com.samsung.android.bixbyvision.framework", "oem", "priv", True),
    ("com.samsung.android.app.spage", "oem", "priv", True),
    ("com.samsung.android.app.tips", "oem", "preload", True),
    ("com.samsung.android.app.social", "oem", "preload", True),
    ("com.samsung.android.kidsinstaller", "oem", "preload", True),
    ("com.samsung.android.game.gamehome", "oem", "preload", True),
    ("com.samsung.android.game.gametools", "oem", "preload", True),
    ("com.samsung.android.game.gos", "oem", "priv", True),
    ("com.samsung.android.aremoji", "oem", "preload", True),
    ("com.samsung.android.ardrawing", "oem", "preload", True),
    ("com.samsung.android.arzone", "oem", "preload", True),
    ("com.samsung.android.visionintelligence", "oem", "priv", True),
    ("com.samsung.android.mdx", "oem", "priv", True),
    ("com.samsung.android.samsungpass", "oem", "priv", True),
    ("com.samsung.android.app.watchmanagerstub", "oem", "preload", True),
    ("com.samsung.android.scloud", "oem", "priv", True),
    ("com.samsung.android.mobileservice", "oem", "priv", True),
    ("com.samsung.android.oneconnect", "oem", "priv", True),
    ("com.samsung.android.voc", "oem", "preload", True),
    ("com.sec.android.app.sbrowser", "oem", "preload", True),
    ("com.sec.android.app.chromecustomizations", "oem", "system", True),
    ("com.sec.android.easyMoverAgent", "oem", "system", True),
    ("com.samsung.android.app.reminder", "oem", "system", True),
    ("com.samsung.android.app.notes", "oem", "system", True),
    ("com.samsung.android.themestore", "oem", "system", True),
    ("com.samsung.android.themecenter", "oem", "system", True),
    ("com.samsung.android.stickercenter", "oem", "system", True),
    ("com.samsung.android.app.cocktailbarservice", "oem", "system", True),
    ("com.samsung.android.app.aodservice", "oem", "priv", True),
    ("com.samsung.android.livestickers", "oem", "system", True),
    ("com.samsung.android.app.routines", "oem", "system", True),
    ("com.samsung.android.app.sharelive", "oem", "system", True),

    # ================= telemetry / diagnostics: safe to disable =================
    ("com.samsung.android.dqagent", "telemetry", "priv", True),
    ("com.sec.android.diagmonagent", "telemetry", "priv", True),
    ("com.sec.android.log", "telemetry", "system", True),
    ("com.qualcomm.qti.qms.service.telemetry", "telemetry", "priv", True),
    ("com.qualcomm.qti.telemetry", "telemetry", "priv", True),
    ("com.qualcomm.qti.workloadclassifier", "telemetry", "priv", True),
    ("com.google.android.gms.location.history", "telemetry", "priv", True),
    ("com.google.android.feedback", "telemetry", "system", True),
    ("com.google.android.partnersetup", "telemetry", "priv", True),
    ("com.android.traceur", "telemetry", "system", True),
    ("com.samsung.android.dialer.telemetry", "telemetry", "system", True),
    ("com.samsung.android.knox.analytics.uploader", "telemetry", "priv", False),

    # ================= carrier preloads: safe to disable =================
    ("com.carrier.iqagent", "carrier", "preload", True),
    ("com.tmobile.pr.mytmobile", "carrier", "preload", True),
    ("com.vzw.hs.android.modlite", "carrier", "preload", True),
    ("com.att.dh", "carrier", "preload", True),
    ("com.orange.rouber", "carrier", "preload", True),
    ("com.vodafone.mCare", "carrier", "preload", True),
    ("com.android.partnerbrowsercustomizations", "carrier", "system", True),

    # ========= preinstalled third-party bloat, pushed into /data/app =========
    # This is the group that defeats naive keyword matchers: it lives outside a
    # system partition, so it looks exactly like a user app except that it has
    # no Play Store installer recorded.
    ("com.facebook.katana", "bloat", "data", True),
    ("com.facebook.appmanager", "bloat", "data", True),
    ("com.facebook.services", "bloat", "data", True),
    ("com.facebook.system", "bloat", "data", True),
    ("com.instagram.android", "bloat", "data", True),
    ("com.zhiliaoapp.musically", "bloat", "data", True),
    ("com.ss.android.ugc.trill", "bloat", "data", True),
    ("com.netflix.partner.activation", "bloat", "data", True),
    ("com.netflix.mediaclient", "bloat", "data", True),
    ("com.linkedin.android", "bloat", "data", True),
    ("com.amazon.mShop.android.shopping", "bloat", "data", True),
    ("com.amazon.appmanager", "bloat", "data", True),
    ("com.microsoft.skydrive", "bloat", "data", True),
    ("com.microsoft.office.officehubrow", "bloat", "data", True),
    ("com.booking", "bloat", "data", True),
    ("com.ebay.mobile", "bloat", "data", True),
    ("com.paypal.android.p2pmobile", "bloat", "data", True),
    ("com.tripadvisor.tripadvisor", "bloat", "data", True),
    ("com.google.android.apps.tachyon", "bloat", "data", True),
    ("com.google.android.videos", "bloat", "data", True),
    ("com.google.android.music", "bloat", "data", True),
    ("com.google.android.apps.podcasts", "bloat", "data", True),
    ("com.google.android.apps.youtube.music", "bloat", "data", True),
    ("com.google.android.youtube", "bloat", "data", True),
    ("com.google.android.apps.docs", "bloat", "data", True),
    ("com.google.android.apps.maps", "bloat", "data", True),
    ("com.spotify.music", "bloat", "data", True),
    # Already debloated by an earlier pass: still installed, already disabled.
    ("com.facebook.orca", "bloat", "data", False),
    ("com.amazon.kindle", "bloat", "data", False),
    ("com.samsung.android.shealth", "oem", "preload", False),
    ("com.samsung.android.smartswitchassistant", "oem", "preload", False),
    ("com.samsung.android.app.news", "oem", "preload", False),
    ("com.samsung.android.tvplus", "oem", "preload", False),
    ("com.samsung.android.svoiceime", "oem", "preload", False),
    ("com.samsung.android.bixby.settings", "oem", "preload", False),
    ("com.samsung.android.app.watchmanager", "oem", "preload", False),
    ("com.samsung.android.game.gamehome.launcher", "oem", "preload", False),
    ("com.google.android.apps.tachyon.telemetry", "telemetry", "preload", False),

    # ========= genuinely user-installed apps: must be LEFT ALONE =========
    # Several are deliberately named to collide with the keyword list the
    # legacy implementation uses, because that collision is the whole point.
    ("com.whatsapp", "user", "data", True),
    ("org.thoughtcrime.securesms", "user", "data", True),
    ("org.telegram.messenger", "user", "data", True),
    ("com.duosecurity.duomobile", "user", "data", True),
    ("com.discord", "user", "data", True),
    ("com.Slack", "user", "data", True),
    ("com.github.android", "user", "data", True),
    ("com.termux", "user", "data", True),
    ("org.mozilla.firefox", "user", "data", True),
    ("com.brave.browser", "user", "data", True),
    ("com.microsoft.office.outlook", "user", "data", True),
    ("com.khanacademy.android", "user", "data", True),
    ("com.coursera.android", "user", "data", True),
    ("com.audible.application", "user", "data", True),
    ("com.kingsoft.office.pro", "user", "data", True),
    ("com.fastmail.mail", "user", "data", True),
    ("com.protonvpn.android", "user", "data", True),
    ("com.samsung.android.game.partner", "user", "data", True),
    ("com.analytics.dashboard.pro", "user", "data", True),
]

# --------------------------------------------------------------------------
# Default global settings, matching Android factory defaults.
# --------------------------------------------------------------------------
DEFAULT_SETTINGS: dict[str, dict[str, str]] = {
    "global": {
        "window_animation_scale": "1.0",
        "transition_animation_scale": "1.0",
        "animator_duration_scale": "1.0",
        "development_settings_enabled": "1",
        "adb_enabled": "1",
    },
    "system": {},
    "secure": {},
}

DEFAULT_PROPS: dict[str, str] = {
    "ro.product.brand": "samsung",
    "ro.product.manufacturer": "samsung",
    "ro.product.model": "SM-G998B",
    "ro.product.device": "o1s",
    "ro.product.name": "o1sxeea",
    "ro.product.board": "exynos2100",
    "ro.build.version.release": "13",
    "ro.build.version.sdk": "33",
    "ro.build.version.security_patch": "2023-08-01",
    "ro.build.id": "TP1A.220624.014",
    "ro.build.display.id": "TP1A.220624.014.G998BXXU9EWH3",
    "ro.build.type": "user",
    "ro.build.tags": "release-keys",
    "ro.build.fingerprint": (
        "samsung/o1sxeea/o1s:13/TP1A.220624.014/G998BXXU9EWH3:user/release-keys"
    ),
    "ro.product.cpu.abi": "arm64-v8a",
    "ro.product.cpu.abilist": "arm64-v8a,armeabi-v7a,armeabi",
    "ro.sf.lcd_density": "420",
    "ro.hardware": "exynos2100",
    "ro.board.platform": "exynos2100",
    "ro.boot.hardware": "exynos2100",
    "ro.serialno": "R58NA0ABCDE",
    "ro.debuggable": "0",
    "ro.secure": "1",
    "persist.sys.locale": "en-GB",
    "persist.sys.timezone": "Europe/London",
    "dalvik.vm.heapsize": "512m",
    "dalvik.vm.heapgrowthlimit": "256m",
    "dalvik.vm.heapstartsize": "8m",
    "dalvik.vm.heapmaxfree": "8m",
    "dalvik.vm.heapminfree": "512k",
    "gsm.version.baseband": "G998BXXU9EWH3",
    "gsm.sim.operator.alpha": "EE",
    "gsm.network.type": "LTE",
    "net.dns1": "8.8.8.8",
    "sys.usb.state": "mtp,adb",
    "service.adb.tcp.port": "-1",
}


def _noise_props() -> dict[str, str]:
    """Emit the long tail of vendor properties a real handset exposes.

    A stock Samsung dump is 250-350 lines.  Modelling that volume matters: it is
    the reason a single ``getprop`` dump beats four targeted lookups even though
    it transfers more bytes.
    """
    prefixes = [
        "ro.vendor.product", "ro.vendor.qti", "ro.vendor.display",
        "persist.vendor.radio", "persist.vendor.audio", "ro.vendor.camera",
        "ro.vendor.bluetooth", "ro.vendor.wifi", "ro.vendor.thermal",
        "ro.vendor.power", "ro.separate.soft", "ro.csc", "ro.config",
        "ro.com.google", "ro.samsung", "ro.sec",
    ]
    suffixes = [
        "config", "feature", "support", "enabled", "version", "mode",
        "capability", "profile", "index", "threshold", "level", "type",
        "path", "size", "count", "mask", "flag", "policy", "limit", "state",
    ]
    props: dict[str, str] = {}
    n = 0
    for prefix in prefixes:
        for suffix in suffixes:
            props[f"{prefix}.{suffix}.{n:03d}"] = f"cfg_{n:04d}"
            n += 1
    return props


def build_props() -> dict[str, str]:
    props = dict(DEFAULT_PROPS)
    props.update(_noise_props())
    return props


def apk_path(package: str, root: str) -> str:
    """Return a realistic APK path for a package."""
    leaf = package.split(".")[-1]
    folder = leaf[:1].upper() + leaf[1:]
    if root == "data":
        digest = hashlib.sha1(package.encode()).hexdigest()[:12]
        digest2 = hashlib.sha1((package + "b").encode()).hexdigest()[:12]
        return f"/data/app/~~{digest}/{package}-{digest2}/base.apk"
    return f"{ROOTS[root]}/{folder}/{leaf}.apk"


def installer_for(package: str, tier: str) -> str | None:
    """Only tier ``user`` carries a Play Store installer record."""
    return VENDING if tier == "user" else None


def is_third_party(root: str) -> bool:
    return root in THIRD_PARTY_ROOTS


def default_state() -> dict:
    """Fresh device state, as a plain JSON-serialisable dict."""
    packages = [
        {
            "name": name,
            "tier": tier,
            "root": root,
            "path": apk_path(name, root),
            "third_party": is_third_party(root),
            "installer": installer_for(name, tier),
            "enabled": enabled,
        }
        for name, tier, root, enabled in PACKAGES
    ]
    return {
        "serial": "R58NA0ABCDE",
        "online": True,
        "props": build_props(),
        "packages": packages,
        "settings": {k: dict(v) for k, v in DEFAULT_SETTINGS.items()},
        "doze": "active",
        "logcat_cleared": 0,
        "cache_trimmed": 0,
        "hwui_profile": None,
        "forced_stops": [],
    }


def load(path: str | Path) -> dict:
    """Read device state.

    A missing file, or an empty one, means "factory fresh" — the benchmark
    harness resets a device by truncating its state file.  Anything else that
    fails to parse is a genuine corruption and raises, rather than silently
    handing back a factory-fresh device and hiding the problem.
    """
    p = Path(path)
    if not p.exists():
        return default_state()
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return default_state()
    return json.loads(text)


def save(state: dict, path: str | Path) -> None:
    """Write device state atomically."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def package_index(state: dict) -> dict[str, dict]:
    return {p["name"]: p for p in state["packages"]}


def ground_truth(state: dict | None = None) -> dict[str, bool]:
    """Label every package with whether disabling it is correct."""
    return {name: (tier in SAFE_TIERS) for name, tier, _root, _en in PACKAGES}
