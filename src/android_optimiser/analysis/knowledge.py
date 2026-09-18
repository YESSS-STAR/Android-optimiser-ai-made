"""The curated knowledge base.

A debloat tool without a knowledge base is guessing.  This module holds the
distilled, auditable policy: which package families are known preloads, which
are telemetry agents, which vendor prefixes identify carrier provisioning, and —
most importantly — which packages must never be touched.

Everything here is data, not code.  That matters for two reasons: it can be
reviewed and corrected by someone who is not a Python programmer, and the
classifier that consumes it stays small enough to test exhaustively.

The knowledge base is deliberately *vendor- and pattern-oriented* rather than a
flat list of package names, so that it generalises to handsets and firmware
revisions it has never seen.  See ``docs/CLASSIFIER.md`` for the evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import Impact, Risk

# ==========================================================================
# 1. Absolute protections
# ==========================================================================

#: Exact package names that must never be disabled.  Disabling any of these
#: breaks core functionality: the launcher, telephony, the keyboard, the
#: package manager, Play Services, or the Settings app itself.
NEVER_DISABLE_EXACT: frozenset[str] = frozenset(
    {
        "com.android.systemui",
        "com.android.settings",
        "com.android.phone",
        "com.android.providers.settings",
        "com.android.providers.telephony",
        "com.android.providers.contacts",
        "com.android.providers.calendar",
        "com.android.providers.media",
        "com.android.providers.downloads",
        "com.android.server.telecom",
        "com.android.bluetooth",
        "com.android.nfc",
        "com.android.shell",
        "com.android.location.fused",
        "com.android.se",
        "com.android.keychain",
        "com.android.certinstaller",
        "com.android.packageinstaller",
        "com.android.permissioncontroller",
        "com.android.externalstorage",
        "com.android.mtp",
        "com.android.deskclock",
        "com.android.emergency",
        "com.android.wallpaper",
        "com.android.wallpaper.livepicker",
        "com.android.documentsui",
        "com.android.carrierconfig",
        "com.android.carrierdefaultapp",
        "com.google.android.gms",
        "com.google.android.gsf",
        "com.android.vending",
        "com.samsung.android.incallui",
        "com.samsung.android.providers.context",
        "com.samsung.android.dialer",
        "com.samsung.android.messaging",
        "com.samsung.android.calendar",
        "com.samsung.android.app.contacts",
        "com.samsung.android.forest",
        "com.sec.android.app.launcher",
        "com.sec.android.app.camera",
        "com.sec.android.gallery3d",
        "com.sec.android.app.myfiles",
        "com.google.android.inputmethod.latin",
        "com.samsung.android.honeyboard",
    }
)

#: Prefixes that mark an entire family as untouchable.
NEVER_DISABLE_PREFIXES: tuple[str, ...] = (
    "com.android.providers.",
    "com.android.server.",
    "com.android.internal.",
    "com.android.systemui",
    "com.android.settings",
    "com.android.phone",
    "com.google.android.gms",
    "com.android.vending",
    "com.samsung.android.providers.",
    "com.sec.android.provider.",
    "com.android.launcher",
    "com.android.inputmethod",
)

#: Explicit carve-outs that a broad NEVER prefix would otherwise swallow.
#: ``com.google.android.gms.location.history`` is a telemetry component that
#: merely happens to live under the Play Services namespace.
FORCE_SAFE: frozenset[str] = frozenset(
    {
        "com.google.android.gms.location.history",
    }
)

#: Name fragments that indicate a diagnostics or analytics agent.  These are
#: safe to disable on every vendor, which is what makes the rule generalise.
TELEMETRY_TOKENS: tuple[str, ...] = (
    "telemetry",
    "analytics",
    "diagmon",
    "diagnostic",
    "dqagent",
    "iqagent",
    "feedback",
    "crashreport",
    "crashlytics",
    "bugreport",
    "usage_stats",
    "logservice",
    "logging",
    "statsd",
    "datacollector",
    "metricservice",
    "traceur",
    "location.history",
)

#: Carrier provisioning markers.
CARRIER_PREFIXES: tuple[str, ...] = (
    "com.carrier.",
    "com.tmobile.",
    "com.vzw.",
    "com.verizon.",
    "com.att.",
    "com.sprint.",
    "com.metro.",
    "com.orange.",
    "com.vodafone.",
    "com.telefonica.",
    "com.telstra.",
    "com.ee.",
    "com.o2.",
    "com.three.",
    "com.android.partnerbrowsercustomizations",
)


# ==========================================================================
# 2. Vendor rules
# ==========================================================================


@dataclass(frozen=True)
class VendorRule:
    """A prefix match that identifies a known preload family."""

    prefix: str
    vendor: str
    category: str
    risk: Risk
    impact: Impact = Impact.MINOR
    rationale: str = ""

    def matches(self, package: str) -> bool:
        return package.startswith(self.prefix)


#: Longest-prefix wins, so ordering here is for readability only.
VENDOR_RULES: tuple[VendorRule, ...] = (
    # ---------------- third-party preloads ----------------
    VendorRule("com.facebook.", "Meta", "social-preload", Risk.SAFE, Impact.MINOR,
               "Meta app pushed onto the device by the OEM or carrier"),
    VendorRule("com.instagram.", "Meta", "social-preload", Risk.SAFE, Impact.MINOR,
               "Meta app pushed onto the device by the OEM or carrier"),
    VendorRule("com.zhiliaoapp.", "ByteDance", "social-preload", Risk.SAFE, Impact.MINOR,
               "TikTok, preinstalled by the vendor"),
    VendorRule("com.ss.android.", "ByteDance", "social-preload", Risk.SAFE, Impact.MINOR,
               "ByteDance app family"),
    VendorRule("com.netflix.", "Netflix", "media-preload", Risk.SAFE, Impact.MINOR,
               "Netflix preload and its partner activation stub"),
    VendorRule("com.amazon.", "Amazon", "commerce-preload", Risk.SAFE, Impact.MINOR,
               "Amazon shopping/Appstore preload"),
    VendorRule("com.linkedin.", "LinkedIn", "social-preload", Risk.SAFE, Impact.MINOR,
               "LinkedIn preload"),
    VendorRule("com.microsoft.skydrive", "Microsoft", "cloud-preload", Risk.SAFE,
               Impact.MINOR, "OneDrive preload"),
    VendorRule("com.microsoft.office.officehubrow", "Microsoft", "office-preload",
               Risk.SAFE, Impact.MINOR, "Office preload"),
    VendorRule("com.spotify.music", "Spotify", "media-preload", Risk.SAFE, Impact.MINOR,
               "Spotify preload"),
    VendorRule("com.booking", "Booking", "commerce-preload", Risk.SAFE, Impact.MINOR,
               "Booking.com preload"),
    VendorRule("com.ebay.", "eBay", "commerce-preload", Risk.SAFE, Impact.MINOR,
               "eBay preload"),
    VendorRule("com.paypal.", "PayPal", "commerce-preload", Risk.SAFE, Impact.MINOR,
               "PayPal preload"),
    VendorRule("com.tripadvisor.", "TripAdvisor", "commerce-preload", Risk.SAFE,
               Impact.MINOR, "TripAdvisor preload"),

    # ---------------- Google preloads ----------------
    VendorRule("com.google.android.apps.tachyon", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Google Meet/Duo preload"),
    VendorRule("com.google.android.apps.youtube", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "YouTube preload"),
    VendorRule("com.google.android.apps.docs", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Drive preload"),
    VendorRule("com.google.android.apps.maps", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Maps preload"),
    VendorRule("com.google.android.apps.podcasts", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Podcasts preload"),
    VendorRule("com.google.android.apps.turbo", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Device Health Services"),
    VendorRule("com.google.android.apps.wellbeing", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Digital Wellbeing"),
    VendorRule("com.google.android.youtube", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "YouTube preload"),
    VendorRule("com.google.android.videos", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Google TV/Play Movies preload"),
    VendorRule("com.google.android.music", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Play Music preload"),
    VendorRule("com.google.android.printservice", "Google", "google-preload", Risk.SAFE,
               Impact.NONE, "Cloud print service"),
    VendorRule("com.google.android.projection", "Google", "google-preload", Risk.SAFE,
               Impact.MINOR, "Android Auto projection"),
    VendorRule("com.google.android.feedback", "Google", "telemetry", Risk.SAFE,
               Impact.NONE, "Google feedback/telemetry agent"),
    VendorRule("com.google.android.partnersetup", "Google", "telemetry", Risk.SAFE,
               Impact.NONE, "Partner setup/telemetry agent"),
    VendorRule("com.google.android.gms.location.history", "Google", "telemetry",
               Risk.SAFE, Impact.NONE,
               "Location History telemetry component (carved out of the Play "
               "Services namespace, which is otherwise protected)"),
    VendorRule("com.android.traceur", "AOSP", "telemetry", Risk.SAFE, Impact.NONE,
               "System Tracing developer utility"),

    # ---------------- Samsung / OEM preloads ----------------
    VendorRule("com.samsung.android.bixby", "Samsung", "oem-assistant", Risk.SAFE,
               Impact.MINOR, "Bixby assistant stack"),
    VendorRule("com.samsung.android.game.", "Samsung", "oem-gaming", Risk.SAFE,
               Impact.MINOR, "Game Launcher / Game Tools / GOS"),
    VendorRule("com.samsung.android.ar", "Samsung", "oem-ar", Risk.SAFE, Impact.MINOR,
               "AR Zone / AR Emoji / AR Doodle"),
    VendorRule("com.samsung.android.app.spage", "Samsung", "oem-assistant", Risk.SAFE,
               Impact.MINOR, "Bixby Home / Samsung Free"),
    VendorRule("com.samsung.android.app.tips", "Samsung", "oem-promo", Risk.SAFE,
               Impact.NONE, "Tips promotional app"),
    VendorRule("com.samsung.android.app.social", "Samsung", "oem-social", Risk.SAFE,
               Impact.NONE, "Samsung Social promo app"),
    VendorRule("com.samsung.android.kidsinstaller", "Samsung", "oem-promo", Risk.SAFE,
               Impact.NONE, "Kids Mode installer stub"),
    VendorRule("com.samsung.android.visionintelligence", "Samsung", "oem-ai", Risk.SAFE,
               Impact.MINOR, "Bixby Vision"),
    VendorRule("com.samsung.android.mdx", "Samsung", "oem-sync", Risk.SAFE, Impact.MINOR,
               "Link to Windows / cross-device sync"),
    VendorRule("com.samsung.android.scloud", "Samsung", "oem-cloud", Risk.SAFE,
               Impact.MINOR, "Samsung Cloud"),
    VendorRule("com.samsung.android.mobileservice", "Samsung", "oem-cloud", Risk.SAFE,
               Impact.MINOR, "Samsung account service"),
    VendorRule("com.samsung.android.oneconnect", "Samsung", "oem-iot", Risk.SAFE,
               Impact.MINOR, "SmartThings"),
    VendorRule("com.samsung.android.voc", "Samsung", "oem-promo", Risk.SAFE, Impact.NONE,
               "Samsung Members / feedback"),
    VendorRule("com.samsung.android.themestore", "Samsung", "oem-store", Risk.SAFE,
               Impact.MINOR, "Galaxy Themes store"),
    VendorRule("com.samsung.android.themecenter", "Samsung", "oem-store", Risk.SAFE,
               Impact.MINOR, "Galaxy Themes centre"),
    VendorRule("com.samsung.android.stickercenter", "Samsung", "oem-content", Risk.SAFE,
               Impact.NONE, "Sticker centre"),
    VendorRule("com.samsung.android.livestickers", "Samsung", "oem-content", Risk.SAFE,
               Impact.NONE, "Live stickers"),
    VendorRule("com.samsung.android.app.reminder", "Samsung", "oem-app", Risk.SAFE,
               Impact.MINOR, "Reminder app"),
    VendorRule("com.samsung.android.app.notes", "Samsung", "oem-app", Risk.SAFE,
               Impact.NOTABLE, "Samsung Notes — check for saved notes first"),
    VendorRule("com.samsung.android.app.cocktailbarservice", "Samsung", "oem-ui",
               Risk.SAFE, Impact.MINOR, "Edge panel service"),
    VendorRule("com.samsung.android.app.routines", "Samsung", "oem-automation", Risk.SAFE,
               Impact.MINOR, "Bixby Routines"),
    VendorRule("com.samsung.android.app.sharelive", "Samsung", "oem-app", Risk.SAFE,
               Impact.MINOR, "Quick Share"),
    VendorRule("com.samsung.android.app.watchmanager", "Samsung", "oem-stub",
               Risk.SAFE, Impact.MINOR, "Galaxy Wearable installer stub"),
    VendorRule("com.samsung.android.samsungpass", "Samsung", "oem-security", Risk.SAFE,
               Impact.NOTABLE, "Samsung Pass — verify you do not rely on stored logins"),
    VendorRule("com.samsung.android.app.aodservice", "Samsung", "oem-ui", Risk.SAFE,
               Impact.NOTABLE, "Always On Display"),
    VendorRule("com.samsung.android.dqagent", "Samsung", "telemetry", Risk.SAFE,
               Impact.NONE, "Samsung diagnostics agent"),
    VendorRule("com.samsung.android.smartswitchassistant", "Samsung", "oem-stub",
               Risk.SAFE, Impact.NONE, "Smart Switch stub"),
    VendorRule("com.samsung.android.shealth", "Samsung", "oem-app", Risk.SAFE,
               Impact.NOTABLE, "Samsung Health — check for health data first"),
    VendorRule("com.samsung.android.tvplus", "Samsung", "oem-promo", Risk.SAFE,
               Impact.NONE, "Samsung TV Plus"),
    VendorRule("com.samsung.android.app.news", "Samsung", "oem-promo", Risk.SAFE,
               Impact.NONE, "Samsung News promo"),
    VendorRule("com.samsung.android.svoiceime", "Samsung", "oem-input", Risk.SAFE,
               Impact.MINOR, "Samsung voice input"),
    VendorRule("com.samsung.android.dialer.telemetry", "Samsung", "telemetry", Risk.SAFE,
               Impact.NONE, "Dialer telemetry agent"),
    VendorRule("com.samsung.android.knox.analytics", "Samsung", "telemetry", Risk.SAFE,
               Impact.NONE, "Knox analytics uploader"),
    VendorRule("com.sec.android.diagmonagent", "Samsung", "telemetry", Risk.SAFE,
               Impact.NONE, "Samsung diagnostics monitor"),
    VendorRule("com.sec.android.log", "Samsung", "telemetry", Risk.SAFE, Impact.NONE,
               "Samsung system log agent"),
    VendorRule("com.sec.android.app.sbrowser", "Samsung", "oem-browser", Risk.SAFE,
               Impact.NOTABLE, "Samsung Internet — check it is not your default browser"),
    VendorRule("com.sec.android.app.chromecustomizations", "Samsung", "oem-browser",
               Risk.SAFE, Impact.NONE, "Chrome customisation hooks"),
    VendorRule("com.sec.android.easyMoverAgent", "Samsung", "oem-migration", Risk.SAFE,
               Impact.NONE, "Smart Switch migration agent"),

    # ---------------- silicon vendor telemetry ----------------
    VendorRule("com.qualcomm.qti.telemetry", "Qualcomm", "telemetry", Risk.SAFE,
               Impact.NONE, "Qualcomm telemetry agent"),
    VendorRule("com.qualcomm.qti.qms.service.telemetry", "Qualcomm", "telemetry",
               Risk.SAFE, Impact.NONE, "Qualcomm QMS telemetry"),
    VendorRule("com.qualcomm.qti.workloadclassifier", "Qualcomm", "telemetry", Risk.SAFE,
               Impact.NONE, "Qualcomm workload profiling"),

    # ---------------- other OEM families (generalisation) ----------------
    VendorRule("com.miui.", "Xiaomi", "oem-preload", Risk.SAFE, Impact.MINOR,
               "MIUI preload"),
    VendorRule("com.xiaomi.", "Xiaomi", "oem-preload", Risk.SAFE, Impact.MINOR,
               "Xiaomi preload"),
    VendorRule("com.oneplus.", "OnePlus", "oem-preload", Risk.SAFE, Impact.MINOR,
               "OnePlus preload"),
    VendorRule("com.oppo.", "OPPO", "oem-preload", Risk.SAFE, Impact.MINOR,
               "ColorOS preload"),
    VendorRule("com.vivo.", "vivo", "oem-preload", Risk.SAFE, Impact.MINOR,
               "OriginOS preload"),
    VendorRule("com.motorola.", "Motorola", "oem-preload", Risk.SAFE, Impact.MINOR,
               "Motorola preload"),
    VendorRule("com.huawei.", "Huawei", "oem-preload", Risk.SAFE, Impact.MINOR,
               "Huawei preload"),
    VendorRule("com.hihonor.", "Honor", "oem-preload", Risk.SAFE, Impact.MINOR,
               "Honor preload"),
    VendorRule("com.lge.", "LG", "oem-preload", Risk.SAFE, Impact.MINOR, "LG preload"),
    VendorRule("com.sonyericsson.", "Sony", "oem-preload", Risk.SAFE, Impact.MINOR,
               "Sony preload"),
    VendorRule("com.transsion.", "Transsion", "oem-preload", Risk.SAFE, Impact.MINOR,
               "Tecno/Infinix preload"),
)

#: Sorted longest-prefix-first, computed once.
_SORTED_RULES: tuple[VendorRule, ...] = tuple(
    sorted(VENDOR_RULES, key=lambda r: len(r.prefix), reverse=True)
)


def lookup_vendor(package: str) -> VendorRule | None:
    """Return the most specific vendor rule matching ``package``."""
    for rule in _SORTED_RULES:
        if rule.matches(package):
            return rule
    return None


def is_never_disable(package: str) -> tuple[bool, str]:
    """Absolute protection check.  Returns ``(protected, reason)``."""
    if package in FORCE_SAFE:
        return False, ""
    if package in NEVER_DISABLE_EXACT:
        return True, "core system component"
    for prefix in NEVER_DISABLE_PREFIXES:
        if package.startswith(prefix):
            return True, f"protected namespace {prefix}*"
    return False, ""


def telemetry_hit(package: str) -> str | None:
    """Return the telemetry fragment that matched, if any."""
    lowered = package.lower()
    for token in TELEMETRY_TOKENS:
        if token in lowered:
            return token
    return None


def carrier_hit(package: str) -> str | None:
    lowered = package.lower()
    for prefix in CARRIER_PREFIXES:
        if lowered.startswith(prefix):
            return prefix
    return None
