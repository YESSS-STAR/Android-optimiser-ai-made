# Changelog

## 2.0.0 — rewrite

The original was a single 188-line script. This release is a layered package
with a device simulator, a benchmark suite and a test suite. Every claim below
is backed by a measurement in `benchmarks/`; see `docs/BENCHMARKS.md`.

### Added

- **Command batching.** Up to 8 device commands per `adb shell` invocation,
  with per-command exit codes recovered from sentinel markers. Round-trips for
  a fixed workload drop 8.4×.
- **Parallel batch dispatch.** Independent batches run across a thread pool.
  Combined with batching, the same workload drops 4.0× on wall-clock and hits
  the theoretical minimum round-trip count at every tested size.
- **Property and setting caching.** One `getprop` dump and one `settings list`
  prime in-memory caches. Inspection now costs 2 round-trips for 167 facts,
  against 6 round-trips for 15 facts.
- **Provenance-based classification.** `pm list packages -f -3 -i` yields the
  APK path and installer, so a user-installed app is never confused with an
  OEM preload. F1 on the device corpus goes from 0.157 to 1.000; on a held-out
  corpus from 0.000 to 1.000.
- **Ordered rule chain** with explicit precedence: illegal identifier →
  user-installed → protected → known vendor → telemetry pattern → carrier
  provisioning → unknown. The unknown case is `CAUTION`, never `SAFE`.
- **Curated vendor knowledge base** of 82 rules across 25 vendors.
- **Declarative, invertible actions.** Every `Command` carries the command that
  undoes it, so rollback is derived mechanically instead of written by hand.
- **`restore` subcommand** that replays a JSON report's rollback commands.
- **Rollback coverage reporting.** Irreversible operations (`pm trim-caches`,
  `logcat -c`) are listed explicitly and lower the coverage figure rather than
  being silently omitted.
- **Idempotent planning.** The plan is a pure function of (snapshot, profile).
  A second run against an already-optimised device issues 3 round-trips
  instead of 16.
- **Audit trail of skipped packages**, each with a reason. The original simply
  printed a list and moved on, which is why nobody could tell it had missed 83
  packages.
- **Profiles as data** (`light` / `heavy` / `aggressive`) instead of three
  functions that call each other.
- **Typed exception hierarchy** with distinct exit codes.
- **Bounded retries** on transient transport failures, applied per command
  rather than per batch.
- **JSON and Markdown reports** capturing device, classification, plan,
  execution and rollback.
- **Device simulator** — a full `adb` CLI emulation over a 154-package state
  file with ground-truth labels, making every benchmark deterministic and
  reproducible without hardware.
- **Benchmark suite** of 7 benchmarks producing `results/benchmarks.json` and
  a self-contained HTML before/after report.
- **253 tests**, 89% line coverage.
- **`pyproject.toml`**, packaging, and a `android-optimiser` console entry
  point.

### Changed

- **No shell, anywhere.** The original built commands with f-strings and ran
  them through `shell=True`. Every process launch now goes through one audited
  function that takes an argument vector. `shell=True` call sites: 2 → 0.
- **Package identifiers are validated** against a strict grammar before any
  command is constructed.
- **Failures are reported as failures.** Fault-injection detection goes from
  0% to 100%; silent failures from 3 to 0; exit code from 0 to 1.
- **Mean function length** 19.2 → 11.7 lines; **mean nesting depth** 1.33 →
  0.63; **max McCabe complexity** 8 → 8 with zero functions over 10;
  **max nesting depth** 3 → 3.
- **Module docstrings** 0% → 100%.
- **Zero third-party runtime dependencies.**

### Removed

- **The 12 × `time.sleep(0.4)` in the FPS check.** 4.8 s of unconditional
  sleeping, regardless of device speed.
- **Substring keyword matching** (`"facebook"`, `"games"`, `"partner"`, …),
  which fired on `com.duosecurity.duomobile` and
  `com.samsung.android.game.partner`.
- **Function chaining** between `optimize_light` / `optimize_heavy` /
  `optimize_aggressive`.
- **`.capitalize()` brand mangling** in user-facing output.

### Fixed

- Round-trip count for a fixed 79-package workload: 84 → 21.
- Harmful disables in the end-to-end run: 3 → 0.
- Missed packages in the end-to-end run: 72 → 0.
- Correctly disabled packages: 19 → 91.

### Preserved

- `legacy/Optimized.py` — the original script, byte-identical, used as the
  benchmark baseline. Never imported by the package.
- CC0 1.0 Universal license, inherited from the original repository.
