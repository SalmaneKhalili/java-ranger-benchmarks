# CI Pipeline & Benchmarks Repo

## What

This repo (`java-ranger-benchmarks`) houses the benchmarking infrastructure for Java Ranger's floating-point support. It defines CI workflows, benchexec configs, runner scripts, and tracks progress/results.

## Repo Structure

```
java-ranger-benchmarks/
├── .github/workflows/benchmarks.yml      # GitHub Actions CI (14 suites)
├── config/*.xml                           # Benchexec configs (1 per suite)
├── scripts/
│   ├── run-ci-benchmarks.sh               # CI-style runner (no benchexec req'd)
│   ├── run-full-benchexec.sh              # Full benchexec wrapper (local only)
│   ├── generate-benchexec-xml.py          # Produce benchexec-identical XML from JSONL
│   ├── generate-failure-analysis.py       # Parse XML → failure analysis HTML
│   └── setup-local-runner.sh              # systemd timer setup
├── gsoc2026/
│   └── fpsupport/
│       ├── PROGRESS.md                    # Master FP support progress tracker
│       ├── PLAN-migrate-ci-benchexec-output.md  # Migration plan (completed)
│       └── notes/                         # Per-issue analysis files
├── docs/                                  # GitHub Pages (merged tables, failure analysis)
├── results/                               # Local benchmark output
└── PROGRESS.md                            # Top-level progress tracker
```

## CI Workflow

- **Trigger**: push to `main`, schedule (midnight daily), manual (`workflow_dispatch`), or from java-ranger push (`repository_dispatch`)
- **Matrix**: 14 suites run in parallel, each builds java-ranger independently
- **Timeout**: 240 min job-level, 180 min per-suite step, 60s per-benchmark
- **Caching**: sv-benchmarks (full clone cached), Gradle build outputs
- **JDK**: temurin 8 (required for `SharedSecrets.getJavaLangAccess()`)
- **SV-COMP**: sparse-checkout of `java/` only

### Key Fixes (history)
1. `buildJars` not `build` — custom Gradle task
2. Recursive submodules + `fetch-depth: 0` for git-version plugin
3. `-Duser.country=US` prevents NPE in `generateBuildInfo`
4. `jpf.properties` native lib path fix
5. `site.properties` — CWD-relative paths, CI `cd`s to JR_DIR
6. Runner fixed: YML base dir, removed `set -e`, UNSAFE grep
7. Z3 native libs tracked in git at `jpf-symbc/lib/`
8. YAML `argv-tasks` indentation fix (51→0 compile errors)
9. `Verifier.nondetObject()` + `ObjectFactory` for `objects` suite (14→0, pending merge to `svcomp` branch)
10. Benchexec XML output: `generate-benchexec-xml.py` produces DTD-valid XML, `table-generator` produces HTML
11. Failure analysis: `generate-failure-analysis.py` produces standalone HTML with per-suite/per-benchmark detail
12. GitHub Pages: `docs/` directory with merged table, failure analysis, landing page
13. Error extraction: JPF log errors now captured verbatim instead of naive grep patterns
14. Report job checkout: benchmarks repo now checked out in `report` job so scripts are available

## Current Suites (14 total)

`algorithms` `argv-tasks` `autostub` `float-nonlinear-calculation` `float_unboundedloop` `java-ranger-regression` `jayhorn-recursive` `jbmc-regression` `jdart-regression` `jpf-regression` `MinePump` `objects` `rtems-lock-model` `securibench`

Excluded: `juliet-java` (too large for CI timeout)

## Results (Baseline — svcomp branch, no FP support)

| Suite | Benchmarks | Correct | Score |
|-------|-----------|---------|-------|
| jpf-regression | 104 | 98 | 98/104 |
| jbmc-regression | 177 | 145 | 145/177 |
| algorithms | 40 | 24 | 24/40 |
| argv-tasks | 70 | 34 | 34/70 |
| autostub | 244 | 6 | 6/244 |
| float-nonlinear-calculation | 87 | 11 | 11/87 |
| float_unboundedloop | 30 | 29 | 29/30 |
| java-ranger-regression | 6 | 5 | 5/6 |
| jayhorn-recursive | 23 | 19 | 19/23 |
| jdart-regression | 16 | 9 | 9/16 |
| MinePump | 64 | 64 | 64/64 |
| objects | 14 | 0 | 0/14 (14 compile errors) |
| rtems-lock-model | 1 | 0 | 0/1 |
| securibench | 113 | 96 | 96/113 |
| **Total** | **989** | **566** | **566/989 (57.2%)** |

Results from: CI run 26365804093 (svcomp branch), reprocessed with benchexec XML pipeline.
Live at: https://salmanekhalili.github.io/java-ranger-benchmarks/

## Pipeline Output

Each CI run produces:
1. **Per-suite XML** (`.results.xml.bz2`) — benchexec DTD-valid format
2. **Per-suite HTML** via `table-generator` — uploaded as artifact
3. **Merged results** (`all-suites.table.html`) — all 14 suites in one table
4. **Failure analysis** (`failure-analysis.html`) — categorized error breakdown
5. **GitHub Pages** — auto-published from `docs/`

## Key Commands

| Command | Description |
|---------|-------------|
| `scripts/run-ci-benchmarks.sh <jr> <sv> <out> <suites...>` | Run benchmark suite(s) locally |
| `scripts/generate-benchexec-xml.py <jsonl> <output>` | Convert JSONL results to benchexec XML |
| `scripts/generate-failure-analysis.py <dir> <output.html>` | Parse XML → failure analysis HTML |
| `table-generator *.xml.bz2 -o out/ -n name` | Generate HTML tables from benchexec XML |
| `scripts/run-full-benchexec.sh` | Full local benchexec run (cgroups required) |
| `scripts/setup-local-runner.sh` | Install systemd timer for daily local runs |
