# Architecture

## The problem with the original

The original is 188 lines in one file. That is not a style complaint — it has
concrete consequences that the benchmarks measure:

| Symptom | Cause |
|---|---|
| 8% recall on bloat | Substring keyword list, no notion of provenance |
| 3 packages harmfully disabled | Same list fires on `com.duosecurity.duomobile` |
| 1 round-trip per operation | No batching; one `adb` process per package |
| Command injection | f-string into `shell=True` |
| Every injected fault reported as success | `run_cmd` returns `stderr`, caller never checks |
| 4.8 s of hard-coded sleeps | `time.sleep(0.4)` × 12 in the FPS check |
| No rollback | No record of what changed |
| Untestable | Logic, I/O, presentation and CLI in the same functions |

Every one of these is fixed by a specific structural decision below.

---

## Layers

```
        cli.py
          │  argparse + wiring only
          ▼
      planner/  ──────────►  analysis/  ──────►  domain.py
     profiles, plan          knowledge,          PackageRecord
                             classifier,         Classification
                             inspector           Risk, Impact
          │
          ▼
      executor/  ──────────►  core/
     execute, rollback        transport, adb, device, metrics
```

Dependencies point **one way**. `analysis` and `planner` never import
`executor` or `core.transport`. That is what allows the entire planning layer to
be tested without a device, a subprocess, or a filesystem.

The direction is enforced by convention and by a test that walks the import
graph; it is not merely documented.

---

## Layer by layer

### `core/` — talking to the device

**`transport.py`** is the only module in the codebase that launches a process.

```python
class Transport(Protocol):
    def run(self, argv: Sequence[str], timeout: float) -> CommandResult: ...

class SubprocessTransport:
    def run(self, argv, timeout):
        proc = subprocess.run(argv, capture_output=True, text=True,
                              shell=False, timeout=timeout)   # ← the only one
```

It takes an **argument vector**, never a string. There is no code path by which
a package name can become a shell token. `resolve_adb_binary()` exists because
Windows does not honour `PATHEXT` when `shell=False`, so a bare `"adb"` would
silently find the wrong binary — it always returns a fully-resolved path.

`RecordingTransport` wraps another transport and records every invocation, which
is how tests assert on the *shape* of traffic rather than on mocks.

**`adb.py`** turns the transport into an ADB client. Its three interesting jobs:

*Batching with sentinel exit codes.* A chunk of commands is joined into one
`adb shell` invocation, each followed by `echo "###AO_<i>=$?###"`. The output is
split on those markers to recover per-command status. This is why batching does
not cost error attribution: the original could not even tell a failure from a
success, and the batched version can tell you exactly which of eight commands
failed.

*Caching.* `prime_props()` reads the entire `getprop` output once;
`prime_settings()` reads `settings list global` once. Afterwards
`props("ro.product.model")` is a dict lookup with zero round-trips. Inspection
went from 6 round-trips for 15 facts to 2 round-trips for 167 facts.

*Validation.* `validate_package()` is called before any identifier enters a
command, and raises `UnsafePackageError` on anything not matching
`PACKAGE_NAME_RE`.

**`metrics.py`** counts round-trips, device commands, batching savings, cache
hits and retries. The benchmarks read their numbers from here rather than
inferring them, so the reported cost is the measured cost.

### `analysis/` — deciding what is safe

**`knowledge.py`** holds data, not logic: the never-disable list, prefix rules,
force-safe overrides, telemetry tokens, carrier prefixes, and 82 vendor rules
across 25 vendors.

**`classifier.py`** is an **ordered rule chain**. Each rule returns a
`Classification` or `None`; the first non-`None` wins.

```
_illegal_identifier   → NEVER      (fails the grammar; never becomes a command)
_user_installed       → NEVER      (installer is a store; it is the user's app)
_protected            → NEVER      (launcher/IME/telephony/system-critical)
_known_vendor         → SAFE/CAUTION
_telemetry_pattern    → SAFE       (only when preinstalled)
_carrier_provisioning → CAUTION
_unknown              → CAUTION    (never SAFE)
```

Precedence is the whole design. The original's failure mode was that a package
containing the substring `duo` was bloat; here, `com.duosecurity.duomobile`
carries `installer=com.android.vending`, so `_user_installed` fires first and it
is never touched, regardless of what any later rule thinks.

This was originally one 127-line function with a McCabe complexity of 17. The
structure benchmark flagged it, and it was refactored into six rules plus a
dispatcher. It now scores under the project's own complexity ceiling.

**`inspector.py`** produces a `DeviceSnapshot` in a single batched call:
system packages, third-party packages with installers, disabled packages,
settings, and doze state. It then primes the caches. Everything downstream reads
the snapshot; nothing downstream talks to the device to make a decision.

### `actions/` — operations that know how to undo themselves

```python
@dataclass(frozen=True)
class Command:
    shell: str          # device-side command
    label: str          # human description
    inverse: str | None # the command that undoes it, or None if irreversible
    target: str         # the package or setting it affects

@dataclass(frozen=True)
class Action:
    id: str
    title: str
    category: str
    risk: Risk
    commands: tuple[Command, ...]
    impact: Impact
    rationale: str
```

Two properties fall out of this shape:

1. **Rollback is derived, not written.** Reversing a plan and collecting
   `inverse` fields is mechanical. There is no second code path to keep in sync,
   which is the usual way rollback silently rots.
2. **Irreversibility is explicit.** `pm trim-caches` has `inverse=None`. The
   rollback report lists it under `irreversible` and reports coverage as
   `0.75` rather than pretending to undo it.

Builders emit **nothing** when the device already matches the desired state.
That is what makes the second run cost 3 round-trips instead of 16.

### `planner/` — pure decision making

`Profiles` are data, not code paths:

```python
Profile(key="heavy", disable_bloat=True, disable_telemetry=True,
        disable_carrier=False, animation_scale=None, force_doze=False,
        trim_caches=True, ...)
```

The original had three functions (`optimize_aggressive` → `optimize_heavy` →
`optimize_light`) that called each other, so "light" silently did everything
"aggressive" did plus more. Profiles as data make that class of bug
unrepresentable.

`Planner.plan(snapshot, profile)` is a **pure function**: same snapshot and
profile, same plan. It returns an `ActionPlan` carrying both the actions to take
and a `SkipRecord` for everything it declined to touch, with a reason. The
original's silent skips are why nobody could tell it had missed 83 packages.

### `executor/` — applying a plan

`Executor.execute()` chunks commands, dispatches chunks across a thread pool,
parses sentinel markers, and retries failures individually. Retrying
individually matters: a batch can fail because one command failed, and re-running
the whole batch would repeat the seven that succeeded.

`rollback.py` derives the undo plan and applies it.

### `reporting/` and `cli.py`

`reporting/` owns every byte of output — ANSI styling (respecting `NO_COLOR`),
JSON payloads, Markdown. `cli.py` does argument parsing and wiring and nothing
else. Neither imports device logic.

---

## Determinism and testability

The design goal was that the interesting logic is testable without a device:

| Component | Tested with |
|---|---|
| Classifier | Plain dataclasses, no I/O |
| Planner | A `DeviceSnapshot` literal |
| Executor | `RecordingTransport` |
| AdbClient | `InProcessTransport`, which reuses the real simulator |
| CLI | Captured stdout + injected client |

253 tests, 89% line coverage, no monkeypatching of the network or the
filesystem. `tests/fakes.py` is 40 lines because the transport boundary is one
function wide.

---

## What was deliberately not done

- **No async.** A thread pool is sufficient for 4–16 concurrent subprocess
  calls, and `subprocess` releases the GIL while waiting. Async would add
  colouring to the whole call graph for no measurable gain at this scale.
- **No plugin system.** The rule chain is a tuple of functions; adding a rule is
  adding a function. A registry would be indirection without benefit.
- **No third-party dependencies.** The tool runs on a bare Python install, which
  keeps the install surface and the audit surface small.
