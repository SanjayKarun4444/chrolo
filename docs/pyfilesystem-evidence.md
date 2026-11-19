# PyFilesystem installed-opener validation

The 2026-11-19 screen used all three PyFilesystem trajectories in the previously
retained historical candidate index. Selection preceded execution. This index
was filtered for unresolved runs and nonempty patches; it is not a random sample.
All three full traces matched the saved trajectory IDs and patch artifacts.

| Candidate | Actions | Repeated screening outcome |
| --- | ---: | --- |
| `chatcmpl-aa6d082930c84066c8345f0bd57310ec` | 50 | No target failure, 3/3 |
| `chatcmpl-ef3487aaac5c33c9b1d75fc6a11d52bb` | 56 | No target failure, 3/3 |
| `chatcmpl-4762a556898f208eff83f397373c6ca4` | 59 | Exact target failure, 3/3 |

Every attempt first required both targets and all 31 controls to pass without
actions, in a fresh network-isolated container. Negative cases remain in the
report and were not minimized. Raw traces, frozen selection, per-attempt
reports, and generated regression artifacts remain local under ignored
`results/pyfilesystem-validation/`.

## What failed

The independent oracle injects installed `osfs` plugin failures during loading
and construction. Both should retain an `EntryPointError` whose cause is the
original injected error. The failing agent edit catches that error and returns
a built-in opener instead. The healthy installed-plugin control and all 30
originally passing controls must still pass. Other exceptions, changed causes,
and unrelated results are inconclusive rather than this failure signature.

These tests execute the real repository's registry with deterministic plugin
fixtures. They do not discover real installed plugins, open filesystems through
the new oracle, or establish the original issue's outcome.

## Minimal case and repair

The 59-action failure reduced to four unchanged recorded edits in 52 checks:
`action-0028`, `action-0033`, `action-0044`, and `action-0045`. It is 1-minimal
under tested cascade deletions, not guaranteed globally smallest. Fifteen
candidate executions raised runner errors and were rejected, not accepted as
reproductions. The reduction was 93.22%.

The separate source-repair pilot generates pytest from the validated reduced
case. It fails with `Original failure recurred` before the fix. The repair
preserves an installed plugin's error while retaining fallback when no entry
point was found:

```python
if entry_point_found and entry_point_error is not None:
    raise entry_point_error
should_fallback = not entry_point_found
```

The same generated test passes after this application-source repair and on a
second rerun, with the input, minimized fixture, task, and oracle unchanged.
An invalid repair fails execution. This repairs repository source after agent
edits; it does not repair the coding agent's policy or use a model cassette.

## Reproduce locally

The image is pinned in the [regression manifest](../benchmarks/real_regressions.json).
With its local input/report fixtures available:

```powershell
python -m benchmarks.real_regressions --repeat 3 --output results/real-regression-checks.json
python -m benchmarks.real_source_repair_pilot --case pyfilesystem-installed-opener --use-retained-minimal --output-dir results/new-pyfilesystem-source-pilot
```

The gate checks empty, minimal, and paired action-removal results repeatedly.
Removing `action-0033` is the specified passing control; later replacements
whose expected source is absent remain no-ops under the recorded editor
semantics. Choose a new source-pilot output directory. Raw fixtures are not
distributed in the repository; missing or changed fixtures fail validation.

The downloaded image uses Python 3.9.21 despite task metadata requesting 3.6.
It also includes `pytest-randomly`, which exposed an order-dependent existing
control: `test_user_data_opener` needs the directory created by
`test_open_userdata`. An initial untouched-image run failed that control.
The task builder disables unrelated pytest plugin autoload and fixes the hash
seed, preserving the supplied selector order. No controls or assertions were
removed, and the oracle source remained unchanged.

This adds evidence from a third independent repository task, with a manually
specified independent failure oracle. It does not establish corpus-wide
reliability or an external developer trial.
