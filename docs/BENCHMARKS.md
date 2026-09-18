# Benchmarks

Every number in the README and in `results/report.html` comes from this
directory. Nothing is estimated, modelled, or copied from a previous run.

```bash
python benchmarks/run_all.py    # run everything, write results/benchmarks.json
python benchmarks/report.py     # render results/report.html
```

`run_all.py` executes each benchmark in a **separate interpreter**, so no
benchmark can contaminate another through imported state, environment
variables, or the working directory. Raw per-benchmark output lands in
`results/raw/`; the consolidated document is `results/benchmarks.json`.

---

## Why a simulated device

The obvious alternative — plug in a phone — makes every measurement
irreproducible. Device state drifts between runs, the OS version changes what
`pm` reports, and nobody can re-run your numbers.

Instead, `benchmarks/simulator/` is a complete `adb` CLI emulation:

| File | Role |
|---|---|
| `simulator/device_state.py` | 154 packages across six tiers, 356 properties, partition roots, per-package installer, ground-truth labels |
| `simulator/adb_sim.py` | Emulates `adb devices`, `shell`, `getprop`, `pm list packages` (with `-s/-3/-d/-e/-f/-i`), `settings`, `dumpsys`, `am`, `logcat` |
| `simulator/adb` / `adb.cmd` | PATH shims so the *legacy* script's `shell=True` calls resolve to the simulator rather than to a real `adb.exe` |

The simulator is deterministic: `load()` treats a missing or empty state file as
a factory-fresh device, and `save()` writes atomically through `os.replace`.

It is also correct under concurrency, which took real work:

- The entire read-modify-write cycle is held under a cross-process lock
  (`os.mkdir`, portable and atomic). An earlier version locked only the write,
  so parallel batches clobbered each other — visible as "79 packages disabled"
  followed by "28 still enabled".
- The call log is serialised under its own lock, because Windows `_O_APPEND` is
  seek-then-write and therefore not atomic across processes. Interleaved
  appends produced torn JSON lines.
- Locks older than 5 seconds are broken, so a crashed run cannot wedge the next
  one for the full acquire timeout.

`harness.SimDevice.calls()` raises on a torn log line rather than silently
undercounting, because an undercount would flatter whichever implementation ran
second.

---

## Why round-trips are the headline metric

A device round-trip is exact, deterministic, and platform-independent. Wall
clock is none of those things.

Two facts settle the choice:

1. **On real hardware the round-trip dominates.** A USB ADB round-trip costs
   15–40 ms; the device-side work of `pm disable-user` is microseconds. Reducing
   invocations reduces runtime almost linearly.
2. **On this benchmark host the harness dominates.** Every simulated invocation
   starts a fresh Python interpreter to act as the `adb` binary, costing
   0.4–0.5 s.
   That is paid identically by both implementations, so it cancels out of ratios
   but inflates every absolute wall-clock figure.

`bench_endtoend.measure_spawn_cost` measures that overhead and records it next
to the wall-clock numbers, so nothing in the report is unexplained.

An earlier version of this suite had a genuine methodological flaw: the legacy
implementation was measured first and paid cold-start costs, showing 620 ms per
invocation against 263 ms for the implementation measured second. That made the
new code look slower while doing four times more work. `harness.warm_up()`
now primes both invocation styles before any timed section.

A related false lead: `shell=True` was suspected of costing a process spawn.
Measured, the difference is **0.1 ms** — interpreter startup swamps it. The
suspect was wrong and the number is recorded so nobody re-investigates it.

---

## The benchmarks

### `bench_classification.py` — are the decisions right?

Scores both implementations against hand-assigned ground truth on two corpora.

- **Device corpus**: the 154-package handset the simulator models, of which 91
  packages are safe to disable.
- **Held-out corpus** (`corpus/heldout_packages.json`): 26 package names chosen
  to be *absent* from the knowledge base, testing generalisation rather than
  lookup. A guard test asserts that no safe-to-disable entry appears verbatim in
  the rule tables.

The legacy matcher is **imported and executed**, with only `run_cmd` stubbed to
feed it a fixed package list. It is not reimplemented, so the comparison
measures the real code.

Reports precision, recall, F1, and — for the device corpus — the specific
false positives and false negatives.

### `bench_inspection.py` — what does it cost to learn the state?

Runs `check_devices` → `fetch_device_info` → `inspect_unneeded_packages` for the
legacy side and `Inspector.snapshot()` for the new side, counting invocations
from the call log. Also records what each side *learns*: partition, installer,
disabled state.

### `bench_mechanism.py` — where does the speed come from?

Two measurements:

1. **Same workload.** Both sides disable the same 79 packages. The new side is
   measured twice: batching + parallel dispatch, and batching alone (1 worker),
   so the two levers are attributed separately.
2. **Scaling.** 4/8/16/32/64 packages, compared against the theoretical minimum
   `ceil(n / chunk_size)`.

### `bench_endtoend.py` — the whole job

Runs each implementation's complete heavy workflow, then compares outcome
quality against ground truth (correctly disabled / harmfully disabled / missed).

Also measures the **second run** against an already-optimised device, which is
the idempotence check, and the legacy FPS diagnostic's hard-coded sleeps.

Raw round-trip totals are *not* comparable between the two sides here, because
they do different amounts of work. The benchmark computes work-normalised
figures (round-trips and milliseconds per correctly disabled package) and states
the caveat in the payload itself.

### `bench_reliability.py` — does it tell the truth when things break?

Injects device-command failures and records what each implementation reports:
detection rate, silent failures, whether it claims success, and its exit code.

The two sides are given **different numbers of faults**, and that asymmetry is
inherent rather than an artefact. The fault set covers 8 packages, but the
legacy matcher only ever targets 11 packages, of which just 3 are in the fault
set — it never touches the other 5, so it cannot be observed failing on them.
The comparable figure is therefore the **detection rate**, which is normalised
by the number of faults each side was actually given (0.0 vs 1.0). The benchmark
records this explanation in its own payload so the raw counts cannot be
misread.

### `bench_security.py` — command injection

Plants a package whose *name* is a shell payload, runs the legacy code path, and
checks whether a marker file appears on disk. Then runs the same payloads
through the new validator.

Static analysis is done by walking the AST, not by grepping. A grep for
`shell=True` would match the docstring in `transport.py` explaining that no shell
is used. An earlier version of this function matched on the bare method name
`run`, which counted `self.transport.run(...)` — the project's own abstraction —
and made the new codebase look worse than the legacy one. It now resolves
`subprocess.*` and `os.system`/`os.popen` only.

### `bench_structure.py` — is the code actually better?

Parses both codebases with `ast` and measures files, functions, classes, line
counts, McCabe complexity, nesting depth, docstring coverage, and third-party
dependencies.

Two methodology notes:

- Complexity is McCabe-style: one plus the number of branch points. Boolean
  operators and comprehension conditions count, which is why a function with
  `if a and b` scores higher than a naive reading suggests.
- Annotation ratio is reported as `None` for zero-argument functions. Counting
  them as "fully annotated" would flatter code that simply takes no parameters.

This benchmark found a real regression in this project's own code: `classify()`
was 127 lines with a complexity of 17, and three functions exceeded the legacy
maximum of 8. They were refactored. Max complexity is now 8 with zero functions
over 10.

---

## Reproducing

```bash
git clone <repo> && cd android-optimiser
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
python benchmarks/run_all.py
python benchmarks/report.py
```

Expect roughly six minutes. `BENCH_LATENCY_MS` changes the modelled per-invocation
device latency (default 25 ms); it affects the projected USB figures only, not
any round-trip count.

To verify a single claim in isolation:

```bash
cd benchmarks
python bench_security.py       # the injection proof
python bench_classification.py # F1 on both corpora
```

---

## What these benchmarks do not prove

- **Real-device behaviour.** The simulator models `adb` faithfully enough for
  round-trip accounting, but it is not Android. Vendor ROMs differ in which
  packages exist and which `pm` flags they honour.
- **That the knowledge base is complete.** It covers 82 rules across 25
  vendors. A device from an unmodelled OEM will produce `CAUTION` verdicts
  rather than wrong `SAFE` ones — which is the intended failure direction, but
  it is still a limitation.
- **That wall-clock savings scale linearly on real hardware.** They should,
  because round-trips dominate, but the projection in `bench_endtoend.py` is a
  model (`round_trips × 25 ms`), not a measurement.
