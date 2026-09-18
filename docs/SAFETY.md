# Safety model

Two independent questions:

1. **Can this tool be made to run something the operator did not intend?**
2. **Can this tool damage a device the operator did not intend to damage?**

Both are answered structurally, not by care and attention.

---

## 1. Command injection

### The vulnerability, demonstrated

Package names are not trusted input. They arrive from `pm list packages` on the
device, and on a rooted or compromised device, or via a malicious app that
registers a crafted package name, they are attacker-controlled.

The original builds its command with an f-string and runs it through a shell:

```python
run_cmd(f"adb shell pm disable-user --user 0 {pkg}")
```

with

```python
subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
```

The shell interprets metacharacters in `pkg` **on the operator's machine**. The
benchmark does not argue this — it plants a package whose name is a payload and
checks whether a file appears on disk:

```
payload:                com.facebook.evil& echo pwned>MARKER.txt& rem
command built:          adb shell pm disable-user --user 0 com.facebook.evil& echo pwned>MARKER.txt& rem
shell used:             True
injected command ran:   True
marker file created:    True
verdict:                VULNERABLE
```

The marker file is real. Reproduce with `python benchmarks/bench_security.py`.

`&` was chosen deliberately because it separates commands in both `cmd.exe` and
POSIX `sh`, so the demonstration is not an artefact of one platform.

### The fix, in two layers

**Layer 1 — never invoke a shell.** `SubprocessTransport` is the only process
launch site in the project, and it passes an argument vector:

```python
subprocess.run(argv, capture_output=True, text=True, shell=False, timeout=timeout)
```

A package name is one element of `argv`. There is no string for a shell to parse
and no shell to parse it. This is verified by AST analysis, not by reading the
code: `bench_security._shell_usage` walks the tree of every source file and
counts real `subprocess.*` calls. Result — legacy: **2 call sites, 2 with
`shell=True`**; this project: **1 call site, 0 with `shell=True`**.

**Layer 2 — reject anything that is not a legal identifier.** Defence in depth,
because the transport is one refactor away from being bypassed:

```python
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
```

This forbids whitespace, quotes, semicolons, backticks, `$`, `&`, `|`, and `/`.
`validate_package()` raises `UnsafePackageError` before a command is ever
constructed. All 5 payloads in the corpus are rejected, including the two that
`&`-based injection would not need (`$(...)` and backticks).

**Layer 3 — the classifier refuses to endorse them.** An illegal identifier is
classified `NEVER` with the rationale `illegal package identifier`, so even if
validation were removed, the planner would not schedule an action for it.

### Why the AST check matters

A grep for `shell=True` would have counted the word inside the docstring in
`transport.py` that explains no shell is used. The static analysis was written
with `ast` specifically to avoid reporting a false signal in either direction,
and an earlier version of it *did* report false positives by matching the bare
method name `run` — which counted `self.transport.run(...)`, this project's own
abstraction, and made the new codebase look worse than the legacy one. It was
fixed to resolve `subprocess.*` and `os.system`/`os.popen` only.

---

## 2. Damaging the device

### Nothing is planned that is not understood

The classifier's final rule is `_unknown → CAUTION`, never `SAFE`. There is no
path by which a package the tool does not understand becomes an action.

Ordered precedence, highest first:

| Rule | Verdict | Reason |
|---|---|---|
| `_illegal_identifier` | NEVER | fails the grammar |
| `_user_installed` | NEVER | store installer; it is the user's app |
| `_protected` | NEVER | launcher, IME, telephony, accessibility, Play Services core |
| `_known_vendor` | SAFE / CAUTION | curated rule |
| `_telemetry_pattern` | SAFE | preinstalled telemetry only |
| `_carrier_provisioning` | CAUTION | carrier-provisioned, uncertain |
| `_unknown` | CAUTION | never safe |

Provenance is checked **before** naming, which is the fix for the original's
central failure: a keyword list cannot tell a preinstalled Facebook stub from
the user's own Facebook install. Provenance can — `installer=com.android.vending`
means the user installed it, full stop.

### Nothing happens without a plan the operator can read

`optimize` prints the plan and asks for confirmation. `--dry-run` prints it and
stops. `inspect` never writes at all. `--yes` exists for scripted use and is
opt-in.

### Everything reversible is recorded

`Command.inverse` carries the undo command. `build_rollback()` reverses the plan
and collects inverses; `--json-report` writes it to disk. `restore --from
run.json` replays it.

Rollback **coverage** is reported honestly. `pm trim-caches` and `logcat -c` have
no inverse, so they appear under `irreversible` and the coverage figure drops
below 1.0 — for example 0.75 for a plan of four commands, three of which can be
undone. The tool never claims a rollback it cannot perform.

### Failures are not swallowed

The original's `run_cmd` returns `e.stderr` on failure, and the caller prints it
under a `-> Disabled <pkg>:` label without checking. With faults injected:

| | legacy | this project |
|---|---|---|
| faults injected | 3 | 8 |
| reported as failures | 0 | 8 |
| silent failures | 3 | **0** |
| claims success | **True** | False |
| exit code | **0** | 1 |

Fault attribution survives batching because of the sentinel markers. A batch
whose third command fails is reported as one failure against that command, not
as a wholesale batch failure, and the other seven are not re-run.

### Transient failures are retried, permanent ones are not

`TRANSIENT_PATTERNS` covers `device offline`, `transport error`, `connection
reset` and similar. Retries are bounded (`DEFAULT_RETRIES = 2`) with backoff, and
failed commands are retried **individually** rather than as a batch.

---

## 3. Operational boundaries

- **`pm disable-user --user 0` is reversible** with `pm enable`. It does not
  uninstall, and it does not touch `/system`.
- **No `rm`, no `mount -o remount`, no root operations.** The command surface is
  `pm`, `settings`, `dumpsys`, `am`, `getprop`, `logcat`. There is no code path
  that writes to a partition.
- **Settings writes are limited to an allow-list** (`ANIMATION_KEYS` in
  `config.py`), in the `global` namespace only.
- **The tool never runs as root and never requests root.**
- **`--include-caution` is opt-in** and prints how many uncertain packages it is
  adding before it does so.

---

## 4. What is still out of scope

- **A compromised `adb` binary.** The tool resolves `adb` from `PATH` or
  `ADB_BINARY` and trusts it. If the binary itself is malicious, nothing here
  helps.
- **A malicious device.** ADB output is parsed defensively, but a device that
  lies about which packages are system packages can cause the tool to plan a
  harmful action. This is why `--dry-run` exists and why the plan is printed.
- **Physically irreversible device changes.** Cache trimming discards data. It
  is marked irreversible, but it cannot be undone.
