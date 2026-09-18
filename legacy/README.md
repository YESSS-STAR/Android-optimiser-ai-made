# `legacy/` — the baseline

`Optimized.py` is the **original single-file script, copied verbatim**. It is
here for one reason: it is the "before" side of every measurement in this
project.

```
$ sha256sum legacy/Optimized.py
86e7e5ff4e806d71...   legacy/Optimized.py
```

The benchmarks import it directly at runtime:

```python
spec = importlib.util.spec_from_file_location("legacy_optimized", LEGACY)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
```

That matters. If the baseline were a reimplementation, or a "cleaned up" copy,
the comparison would measure the paraphrase rather than the code. Every number
reported for the original — its F1 score, its round-trip count, its failure
behaviour, its injection vulnerability — comes from executing these exact 188
lines.

## Rules for this directory

- **Do not edit `Optimized.py`.** Not formatting, not a typo, not a comment.
  Any change invalidates the hash and every comparison against it.
- **Do not import it from the package.** `src/android_optimiser/` must not
  depend on it in any way. It is a benchmark fixture, not a module.
- **It is not supported.** Do not run it against a real device. It disables
  packages it cannot identify and runs shell commands built from
  device-supplied strings.

## What it does

Nine functions, no classes, no tests:

| Function | What it does |
|---|---|
| `run_cmd` | `subprocess.run(cmd, shell=True)`, returns `stderr` on failure |
| `check_devices` | `adb devices` |
| `fetch_device_info` | four separate `getprop` round-trips |
| `inspect_unneeded_packages` | `pm list packages -3`, then substring keyword match |
| `run_fps_check` | `dumpsys SurfaceFlinger --latency` + 12 × `time.sleep(0.4)` |
| `optimize_light` | animation scales to 0 |
| `optimize_heavy` | calls `optimize_light`, then `pm disable-user` per package |
| `optimize_aggressive` | calls `optimize_heavy`, then trims caches |
| `main` | interactive menu |

The three `optimize_*` functions chain into each other, so choosing "light"
silently performs everything "aggressive" does and more.

## Measured defects

Each of these is reproduced by a benchmark, not inferred from reading the code:

| Defect | Evidence |
|---|---|
| Command injection | `bench_security.py` creates a real file on disk from a package name |
| Silent failure | `bench_reliability.py`: 0% detection, claims success, exits 0 |
| 8% recall | `bench_classification.py`: F1 0.157 on the device corpus |
| Zero generalisation | `bench_classification.py`: F1 0.000 on the held-out corpus |
| Harmful disables | `bench_endtoend.py`: 3 packages disabled that should not be |
| One round-trip per operation | `bench_mechanism.py`: 84 round-trips for 79 packages |
| Redundant re-runs | `bench_endtoend.py`: 16 round-trips on an already-optimised device |
| Hard-coded sleeps | `bench_endtoend.py`: 4.8 s of unconditional sleep |
| Untestable | `bench_structure.py`: 1 file, 0 classes, 0 tests |

## License

CC0 1.0 Universal, as published in the original repository. See `LICENSE`.
