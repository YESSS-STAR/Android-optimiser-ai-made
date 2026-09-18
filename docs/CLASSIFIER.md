# The classifier

The classifier decides which packages are safe to disable. It is the component
most responsible for the difference between the two implementations: F1 0.157 →
1.000 on the device corpus, and 0.000 → 1.000 on a held-out corpus.

---

## What was wrong before

```python
BLOAT_KEYWORDS = [
    "facebook", "netflix", "tiktok", "instagram", "bloat", "carrier",
    "partner", "games", "analytics", "bixby", "duo",
]
```

Then, for each third-party package:

```python
if any(keyword in package_name.lower() for keyword in BLOAT_KEYWORDS):
    flagged.append(package_name)
```

Three independent failures, all measured:

**1. It cannot see provenance.** `pm list packages -3` returns third-party
packages, which includes both the preinstalled Facebook stub and the Facebook
the user installed themselves. The keyword matcher treats them identically. It
disables user-installed apps.

**2. Substring matching fires on anything.** From the actual measured output:

| Package | Matched on | Reality |
|---|---|---|
| `com.duosecurity.duomobile` | `duo` | Two-factor auth the user installed |
| `com.samsung.android.game.partner` | `games`, `partner` | OEM game-services component |
| `com.analytics.dashboard.pro` | `analytics` | A user-installed developer tool |

All three are false positives, all three get disabled.

**3. It only sees a fraction of the device.** `-3` excludes every system
partition. On the simulated handset 91 packages are safe to disable; the
matcher finds 11, of which 3 are wrong. Recall: 0.088.

---

## The rule chain

Classification is an **ordered chain**. Each rule returns a `Classification` or
`None`; the first non-`None` result wins. Precedence is the design.

```
_illegal_identifier    → NEVER      fails the identifier grammar
_user_installed        → NEVER      installer is a store; it is the user's app
_protected             → NEVER      launcher, IME, telephony, system-critical
_known_vendor          → SAFE or CAUTION
_telemetry_pattern     → SAFE       only when preinstalled
_carrier_provisioning  → CAUTION
_unknown               → CAUTION    never SAFE
```

### 1. `_illegal_identifier` → NEVER

A name failing `PACKAGE_NAME_RE` is classified `NEVER` with the rationale
"not a legal Android package identifier". This is the third layer of the
injection defence: even if identifier validation were removed, the planner would
never schedule an action for such a package.

### 2. `_user_installed` → NEVER

Checked **before** any naming rule, and this ordering is the single most
important decision in the file.

```python
if record.has_store_installer:
    return Classification(..., Risk.NEVER, "user-app",
                          rationale="installed by the user (<installer>)")
```

`has_store_installer` is true when the package is third-party and its installer
is a known store (`com.android.vending`, `com.amazon.venezia`, `com.aurora.store`,
`org.fdroid.fdroid`, …). Such a package belongs to the user, whatever its name
contains.

`com.duosecurity.duomobile` is a false positive for the keyword matcher and a
correct `NEVER` here, because provenance outranks naming.

### 3. `_protected` → NEVER

An explicit never-disable list, plus prefix rules, covering things a device
stops working without:

- **44 exact identifiers** — launchers, IMEs, telephony, the Play Services
  components other apps bind to, accessibility services, package installer.
- **12 prefix rules** — whole families such as `com.android.providers.`,
  `com.android.internal.`, `com.google.android.gms.`.
- **1 force-safe override** — `com.google.android.gms.location.history` matches
  a telemetry token but is a user-facing feature, so it is explicitly protected
  rather than trusted to the ordering.

A package disabled by mistake here is a device that boots to a black screen.

### 4. `_known_vendor` → SAFE or CAUTION

**82 curated rules across 25 vendors**, matched by longest prefix. Each carries
a category, a risk, an impact, and a rationale:

```python
VendorRule(
    prefix="com.facebook.",
    vendor="Meta",
    category="social-preload",
    risk=Risk.SAFE,
    impact=Impact.MINOR,
    rationale="Meta app pushed onto the device by the OEM or carrier",
)
```

| Vendor | Rules | Vendor | Rules |
|---|---|---|---|
| Samsung | 38 | ByteDance | 2 |
| Google | 15 | Microsoft | 2 |
| Qualcomm | 3 | Xiaomi | 2 |
| Meta | 2 | 18 others | 1 each |

Longest-prefix matching means a more specific rule beats a broader one, so a
vendor can carve out an exception without restructuring the table.

These rules are only reached for packages that are **not** user-installed — the
provenance rule has already filtered those out.

### 5. `_telemetry_pattern` → SAFE

18 tokens (`telemetry`, `analytics`, `diagmon`, `crashlytics`, `statsd`,
`datacollector`, …) matched against the package name, and only when the package
is preinstalled. A user-installed app named `*.analytics.*` is caught by rule 2
first.

### 6. `_carrier_provisioning` → CAUTION

15 carrier prefixes (`com.carrier.`, `com.vzw.`, `com.att.`, `com.orange.`, …).
`CAUTION` rather than `SAFE` because carrier provisioning packages are sometimes
load-bearing for SIM activation, and the tool has no way to tell.

### 7. `_unknown` → CAUTION

The fallback is **never** `SAFE`. A package the tool does not recognise is a
package it does not understand, and an unrecognised package is exactly the case
where disabling it is a guess.

---

## Held-out evaluation

The device corpus is the device the simulator models, so a knowledge base tuned
against it proves little. `corpus/heldout_packages.json` contains 26 package
names chosen to be absent from the rule tables.

| | Legacy | This project |
|---|---|---|
| Precision | 0.000 | 1.000 |
| Recall | 0.000 | 1.000 |
| F1 | 0.000 | 1.000 |
| Predicted actionable | 0 | 18 |

The legacy matcher predicts nothing, because none of the held-out names contain
a keyword from its list. The new classifier gets all 26 right, attributed as:

| Rule that produced the verdict | Correct verdicts |
|---|---|
| OEM preload | 12 |
| Telemetry pattern | 6 |
| Protected | 3 |
| User-installed app | 3 |
| Unknown-vendor preload | 2 |

Note the last row: two packages from a vendor with no rule at all are still
classified correctly, because provenance alone is sufficient to tell a preload
from a user app.

### The guard test

`tests/test_classifier.py::test_heldout_corpus_is_not_secretly_in_the_knowledge_base`
asserts that no safe-to-disable held-out entry appears verbatim in the rule
tables.

Writing it caught an over-claim. The first version asserted that *no* held-out
entry appears in the knowledge base — which failed, correctly, because
`com.google.android.gms`, `com.android.providers.media` and
`com.sec.android.app.launcher` are necessarily in the protection list. The test
was narrowed to the claim that actually matters (no `safe: true` entry is
memorised), and a second test was added asserting that no unknown-vendor entry
matches any prefix rule. The corpus description was corrected to state the
precise claim.

---

## Adding a rule

Add a `VendorRule` to `VENDOR_RULES`:

```python
VendorRule(
    prefix="com.example.bloat.",
    vendor="Example",
    category="oem-promo",
    risk=Risk.SAFE,
    impact=Impact.MINOR,
    rationale="Example promo app shipped in /product on Example-brand devices",
),
```

Then add a case to `tests/test_classifier.py`. Rules are data, so no code
changes and no control-flow changes are involved.

If the package is one that must **never** be disabled, add it to
`NEVER_DISABLE_EXACT` instead — it will be caught by rule 3, before any vendor
rule is consulted.
