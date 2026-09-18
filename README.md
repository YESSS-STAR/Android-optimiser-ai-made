# android-optimiser

Inspect an Android device over ADB and debloat it with a plan you can read
before it runs, and undo after it has.

This is a ground-up rewrite of a single 188-line script. The script worked, in
the sense that it disabled packages. It also classified 8% of the bloat it
should have, disabled things it shouldn't have, reported success while failing
silently, and executed arbitrary commands from attacker-controlled package
names. Every one of those claims is measured below, and the measurement is
reproducible on your machine in one command.

---

## Before / after

| Dimension | Original script | This project | Δ |
|---|---|---|---|
| **Classification F1** (154-package device) | 0.157 | **1.000** | +0.843 |
| — false positives | 3 packages | **0** | — |
| — false negatives | 83 packages | **0** | — |
| **Classification F1** (26-package held-out corpus) | 0.000 | **1.000** | +1.000 |
| **Inspection cost** | 6 round-trips | **2** | −66.7% |
| — facts learned | 15 | **167** | 11.1× |
| **Disable cost** (79 packages, same workload) | 84 round-trips | **21** | 4.0× faster |
| — with batching only | — | **10** | 8.4× faster |
| **Cost per correctly disabled package** | 1.158 rt | **0.253 rt** | 4.6× cheaper |
| **End-to-end outcome** | 19 correct / 3 harmful / 72 missed | **91 / 0 / 0** | — |
| **Second run** (idempotence) | 16 round-trips | **3** | −81.2% |
| **Failure detection** (fault injection) | 0% | **100%** | — |
| — silent failures | 3 | **0** | — |
| **Command injection** | VULNERABLE | **SAFE** | — |
| **`shell=True` call sites** | 2 | **0** | — |
| **Process launch sites** | 2 (unaudited) | **1** (audited) | — |
| **Source files** | 1 | **30** | — |
| **Tests** | 0 | **253** | — |
| **Line coverage** | 0% | **89%** | — |
| **Module docstrings** | 0% | **100%** | — |
| **Mean function length** | 19.2 lines | **11.0 lines** | −43% |
| **Mean McCabe complexity** | 3.22 | **2.42** | −25% |
| **Mean nesting depth** | 1.33 | **0.63** | −52% |
| **Third-party runtime deps** | 0 | **0** | — |

Full numbers, methodology and raw JSON: [`benchmarks/results/`](benchmarks/results/)
and [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

---

## Quick start

```bash
pip install -e ".[dev]"

android-optimiser doctor                      # is adb there, is a device attached
android-optimiser inspect                     # classify every package, change nothing
android-optimiser optimize --dry-run          # show the plan
android-optimiser optimize --yes \
    --json-report run.json                    # apply it, record how to undo it
android-optimiser restore --from run.json     # undo it
```

Nothing is written to the device without a confirmation prompt, and `inspect`
never writes at all.

---

## Why it is faster

The dominant cost of any ADB debloat run is **round-trips**, not device work. A
USB round-trip costs 15–40 ms; the device-side work is microseconds. The
original script issued one `adb` process per operation. This one:

1. **Batches.** Up to 8 device commands are packed into a single `adb shell`
   invocation. Per-command exit codes are recovered from sentinel markers
   (`echo "###AO_3=$?###"`), so batching costs nothing in error reporting.
2. **Parallelises.** Batches are dispatched across a thread pool.
3. **Caches.** One `getprop` dump and one `settings list` prime in-memory
   caches; every later property read is a dict lookup costing zero round-trips.
4. **Plans before acting.** The plan is a pure function of (device snapshot,
   profile). A second run against an already-optimised device emits 3
   round-trips instead of 16.

Scaling is not approximate. At 4/8/16/32/64 packages the implementation issues
1/1/2/4/8 round-trips — the theoretical minimum for 8 commands per batch with 4
workers, and a flat 8× improvement over one-invocation-per-operation at every
size from 8 upward.

---

## Why it is safer

**No shell, ever.** The original built commands with f-strings and ran them
through `shell=True`:

```python
run_cmd(f"adb shell pm disable-user --user 0 {pkg}")   # pkg comes from the device
```

Package names come from `pm list packages`, which means they are
attacker-influenced data. A package named `com.facebook.evil& echo pwned>MARKER.txt`
executes on the *operator's machine*. The benchmark plants that package and the
original script creates the file. This project never invokes a shell, and
additionally validates every identifier against a strict grammar before a
command is constructed.

**Every process launch goes through one audited function.** `SubprocessTransport`
is the only place in the codebase that calls `subprocess`. It takes an argument
vector, not a string.

**Actions declare their own inverse.** Each `Command` carries the command that
undoes it. Rollback is derived mechanically by reversing the plan, not written
by hand, so it cannot drift out of sync with the forward path.

**Failures are reported as failures.** The original's `run_cmd` returned
`stderr` on failure, and the caller printed it under a `-> Disabled <pkg>:`
label without checking. With faults injected, it reported success while
every command failed, and exited 0. This project detects 100% of them and exits non-zero.

Details: [`docs/SAFETY.md`](docs/SAFETY.md).

---

## Why the classification is better

The original matched a substring keyword list:

```python
BLOAT_KEYWORDS = ["facebook", "netflix", "tiktok", "bloat", "carrier", "games", ...]
```

Substring matching cannot distinguish a preinstalled Facebook stub from the
user's own installed Facebook, and it fires on any name containing the
substring — `com.duosecurity.duomobile` contains `duo`, `com.samsung.android.game.partner`
contains `partner`.

This project decides from **provenance first**, naming second:

1. **Provenance.** `pm list packages -f -3 -i` yields the APK path and installer.
   A `/data/app` package with `installer=com.android.vending` was installed by
   the user and is never touched. A `/data/app` package with a null installer
   was pushed by the OEM or carrier and is a preload.
2. **Protection.** An explicit never-disable list and prefix list covering
   launchers, IMEs, telephony, accessibility, and the Play Services components
   the system depends on.
3. **Vendor knowledge.** 82 curated rules across 25 vendors for known
   preload families.
4. **Telemetry patterns.** Names matching `*.analytics.*`, `*.metrics.*`, and
   friends, only when they are preinstalled.
5. **Carrier provisioning**, then **unknown → CAUTION**, never SAFE.

Precedence is explicit and ordered; there is no fallthrough where an unknown
package accidentally becomes safe.

On the held-out corpus — 26 package names chosen to be absent from the
knowledge base — the original scores F1 = 0.000 and this scores 1.000.

Details: [`docs/CLASSIFIER.md`](docs/CLASSIFIER.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Reproducing the numbers

The benchmarks run against a **simulated device**, not hardware, so they are
deterministic and CI-friendly. The simulator is a full `adb` CLI emulation
(`benchmarks/simulator/`) backed by a 154-package device state file with
ground-truth labels. The original script is imported and executed unmodified,
so the comparison measures the real code rather than a paraphrase of it.

```bash
python benchmarks/run_all.py        # run every benchmark, write results/benchmarks.json
python benchmarks/report.py         # render the before/after HTML report
pytest --cov                        # 253 tests, 89% coverage
python tools/smoke.py               # end-to-end smoke test against the simulator
```

Per-invocation cost is dominated by the simulated `adb` binary starting a fresh
Python interpreter (0.4–0.5 s on the author's machine); real adb is a native
binary starting in tens of milliseconds. That overhead is paid identically by
both implementations, so it cancels out of ratios but inflates absolute
wall-clock figures. `bench_endtoend.measure_spawn_cost` records it, and the
report prints it next to every wall-clock number.

---

## Layout

```
src/android_optimiser/
    config.py          tunables, and the package-name grammar that is the security boundary
    domain.py          PackageRecord, Classification, Risk, DeviceSnapshot
    exceptions.py      typed error hierarchy

    core/              transport and protocol
        transport.py   the single audited subprocess call site
        adb.py         batching, sentinel parsing, caching, retries
        device.py      device identity and capability probing
        metrics.py     round-trip and batching accounting

    analysis/          deciding what is safe
        knowledge.py   curated vendor/protection/telemetry tables
        classifier.py  ordered rule chain, provenance first
        inspector.py   one-round-trip device snapshot

    actions/           declarative, invertible operations
        base.py        Command (with inverse), Action, ActionBuilder
        packages.py animation.py power.py storage.py

    planner/           snapshot + profile -> plan
        profiles.py    LIGHT / HEAVY / AGGRESSIVE as data
        planner.py     pure planning, with a reason for every skip

    executor/          plan -> device
        executor.py    batched, parallel, retrying execution
        rollback.py    rollback derived from command inverses

    reporting/         human and machine output
    cli.py             argument parsing and wiring only
```

Dependencies point one way: `cli → planner → analysis → domain`, and
`executor → core → transport`. Nothing in `analysis` or `planner` imports
`executor`, which is what makes the planner testable without a device.

---

## Compatibility

- Python 3.10+ (standard library only — no runtime dependencies)
- Windows, macOS, Linux
- Android 8.0+ (uses `pm disable-user`, `settings global`, `dumpsys deviceidle`)

Disabling a package is reversible with `pm enable`. Cache trimming
(`pm trim-caches`) and log clearing are not reversible; the plan marks these
explicitly and reports a rollback coverage figure below 100% when they are
present.

---

## License

CC0 1.0 Universal, inherited from the original repository. See [`LICENSE`](LICENSE).

The original script is preserved verbatim in [`legacy/`](legacy/) and is used as
the baseline in every benchmark. It is never imported by the package.
