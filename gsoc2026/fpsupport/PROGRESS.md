# Java Ranger FP Support — Progress Tracker

## Goal
Establish automated daily benchmarking pipeline for Java Ranger's floating-point support improvements, targeting SV-COMP `jpf-regression` and `jbmc-regression` suites.

---

## Repos

| Repo | Visibility | Branch | Purpose |
|------|-----------|--------|---------|
| `SalmaneKhalili/java-ranger` | public | `feature/issue30-comparisons` | Main development branch for FP issues #27–#30 |
| `SalmaneKhalili/java-ranger` | public | `svcomp` | Stable SV-COMP baseline |
| `SalmaneKhalili/java-ranger-benchmarks` | private | `main` | CI pipeline, runner scripts, benchexec configs, results |

Upstream: `vaibhavbsharma/java-ranger` (public, parent issue [#26](https://github.com/vaibhavbsharma/java-ranger/issues/26))

---

## FP Support Work Items (from Issue #26)

### Done
- [x] **#27** IEEE‑754 special values (NaN, infinities, signed zero) — MERGED
- [x] **#28** FP arithmetic with rounding (FADD, FSUB, FMUL, FDIV → FpAdd, FpSub, FpMul, FpDiv)
- [x] **#29** `isNaN` predicate — PR opened
- [x] **#30** FP comparisons with NaN (FCMPG/FCMPL → 4-choice PCChoiceGenerator: NaN/LT/EQ/GT)

### Not Yet Started
- [ ] Narrowing conversions (rounding, overflow) — F2L, D2I, F2I, D2L float→int narrowing semantic model (see `notes/ISSUE-28-fp-arithmetic-rounding.md#narrowing-conversions`)
- [ ] Widening conversions (rounding) — I2F, L2D, I2D, etc.
- [ ] Symbolic remainder (`frem`/`drem`)
- [ ] Extend `ProblemZ3BitVector` for Z3 FP theory API
- [ ] Extend expression system with FP operators
- [ ] Unit tests for FP bytecode
- [ ] Update AST visitors for new expression types
- [ ] Constant folding for FP
- [ ] Tune path-merging heuristics for FP
- [ ] Math library functions (sin, cos, tan, pow, sqrt, etc.)

---

## Benchmarking Pipeline

### Infrastructure
- `java-ranger-benchmarks` repo with GitHub Actions CI
- CI runner (`run-ci-benchmarks.sh`) compiles Java benchmarks → runs JPF jar → parses SAFE/UNSAFE
- Local runner (`run-full-benchexec.sh`) uses benchexec with systemd timer (00:26 daily)
- Benchmarks: 14 SV-COMP suites (jpf-regression, jbmc-regression, algorithms, argv-tasks, autostub, float-nonlinear-calculation, float_unboundedloop, java-ranger-regression, jayhorn-recursive, jdart-regression, MinePump, objects, rtems-lock-model, securibench)
- **Issue notes**: `gsoc2026/fpsupport/notes/` directory tracks per-issue analysis (ISSUE-27, ISSUE-28, ISSUE-29, etc.)
- **Results**: CI automatically commits results to `results/` directory after each run

### Key Fixes Applied
1. **`buildJars` not `build`**: CI must run `./gradlew :jpf-core:buildJars :jpf-symbc:buildJars` (Gradle uses custom `buildJars` task)
2. **Submodule checkout**: CI needs `submodules: recursive` + `fetch-depth: 0` (for `git-version` plugin)
3. **`-Duser.country=US`**: Prevents NPE in `generateBuildInfo`
4. **`jpf-symbc/jpf.properties` native lib path**: `${jpf-symbc}/lib/64bit/libz3.so` → `${jpf-symbc}/lib/libz3.so`
5. **`site.properties`**: Uses CWD-relative `./jpf-core`; CI runner `cd`s to JR_DIR before JPF invocation — `${config_path}` doesn't work for `site.properties`
6. **CI runner**: Fixed YML base dir, removed `set -e`, fixed UNSAFE grep (3-line AssertionError output), excluded `sv-benchmarks/common/Verifier.java`
7. **Z3 native libs**: `libz3.so` and `libz3java.so` tracked in git at `jpf-symbc/lib/`; `LD_LIBRARY_PATH` and `java.library.path` both set
8. **YAML grep flexibilization**: Changed `grep "  - "` (only matches `  - ` / 2-space indent YAML lists) → `grep -E "^[[:space:]]*- "` (handles both `- ` and `  - ` formats). Fixes 51 compile errors in `argv-tasks` where YAML files use no-indent list format.
9. **Added `Verifier.nondetObject()` + `ObjectFactory` to jpf-symbc**: The `objects` suite calls `Verifier.nondetObject()` which only existed in the reference sv-benchmarks Verifier, not in jpf-symbc's. Added the method and the `ObjectFactory<T>` interface to `jpf-symbc/src/classes/org/sosy_lab/sv_benchmarks/`. Fixes 14 compile errors in `objects`.

### Current CI Status
- **CI run 26365804093 completed** (triggered by push to main, 14 suites)
- **566/989 CORRECT (57.2%)** — up from 521 (52.7%) due to YAML fix
- **argv-tasks compile errors resolved** (51→0), **objects still pending** (see note below)
- **java-ranger-regression**: 6 benchmarks confirmed (matches sv-benchmarks content)

| Suite | Benchmarks | Correct | Incorrect | Unknown | CompileErr | Timeout | Score |
|---|---|---|---|---|---|---|---|
| jpf-regression | 104 | 100 | 4 | 0 | 0 | 0 | 100/104 |
| jbmc-regression | 177 | 145 | 1 | 31 | 0 | 1 | 145/177 |
| algorithms | 40 | 24 | 6 | 10 | 0 | 0 | 24/40 |
| argv-tasks | 70 | 34 | 21 | 15 | 0 | 2 | 34/70 |
| autostub | 244 | 6 | 0 | 238 | 0 | 0 | 6/244 |
| float-nonlinear-calculation | 87 | 11 | 28 | 48 | 0 | 0 | 11/87 |
| float_unboundedloop | 30 | 29 | 1 | 0 | 0 | 0 | 29/30 |
| java-ranger-regression | 6 | 5 | 1 | 0 | 0 | 0 | 5/6 |
| jayhorn-recursive | 23 | 19 | 1 | 3 | 0 | 3 | 19/23 |
| jdart-regression | 16 | 9 | 4 | 3 | 0 | 0 | 9/16 |
| MinePump | 64 | 64 | 0 | 0 | 0 | 0 | 64/64 |
| objects | 14 | 0 | 0 | 0 | 14 | 0 | 0/14 |
| rtems-lock-model | 1 | 0 | 1 | 0 | 0 | 0 | 0/1 |
| securibench | 113 | 96 | 4 | 13 | 0 | 1 | 96/113 |
| **Total** | **989** | **566** | **72** | **375** | **14** | **7** | **566/989** |

- **Compile errors**: `objects` (14) still failing — Verifier fix is on `feature/issue30-comparisons` but CI uses `svcomp` branch. Both branches need the fix merged or CI ref updated.
- **argv-tasks now compiles all 70** — major improvement: 34 correct (was 13), 21 incorrect (reveals FP-handling gaps), 15 unknown (non-FP issues)
- **Most unknown**: `autostub` (238); `float-nonlinear-calculation` (48); `securibench` (13)

---

## Root Causes

### 1. FP Benchmarks Fail: PCParser Missing FpBinaryOp Handlers

**Affected**: `jpf-regression` (4 benchmarks: ExSymExeF2L, FNEG, I2D, D2L)

**Symptom**: All 4 `_false` (UNSAFE) benchmarks return `true` (SAFE — no assertion error detected).

**Diagnosis (2026-05-24)**: The solver translator (`PCParser.getExpression(RealExpression)`) does **not** handle any `FpBinaryOp` subclass (`FpAdd`, `FpSub`, `FpMul`, `FpDiv`). When a path constraint contains one of these expression types — e.g., `MixedConstraint(FpAdd(x,1.0), EQ, SymbolicInteger)` from `(long)++x` — the translator throws `RuntimeException("## Error: Expression " + eRef)`.

The exception propagates through `pc.simplify()` → `isSatisfiable()` → JPF instruction execution. JPF records an unhandled exception, the CI runner doesn't match `java.lang.AssertionError`, and the benchmark is scored as SAFE.

The `FNEG_false` case has a different root — `BinaryRealExpression` IS handled by `getExpression`, but the FNEG interaction with FCMPG + path constraint produces unsatisfiable PCs.

**Fix**: See `notes/ISSUE-28-fp-arithmetic-rounding.md#narrowing-conversions` for full analysis and two-phase plan.

### 2. argv-tasks Compile Errors: YAML Indentation Mismatch

**Fixed** in `run-ci-benchmarks.sh` — see `gsoc2026/fpsupport/notes/ISSUE-argv-tasks-compile-errors.md`

### 3. objects Compile Errors: Missing `Verifier.nondetObject()`

**Fixed** in jpf-symbc Verifier — see `gsoc2026/fpsupport/notes/ISSUE-objects-compile-errors.md`

---

## Timeline

| Date | Event |
|------|-------|
| 2026-05-24 | CI pipeline fully functional (100/104) |
| 2026-05-24 | 4 failing benchmarks diagnosed (missing FpBinaryOp solver support) |
| 2026-05-24 | All pending fixes pushed to `feature/issue30-comparisons` |
| 2026-05-24 | CI triggered on `feature/issue30-comparisons` — results: 100/104 |
| 2026-05-24 | Cleanup: removed unused `FpCmp` visitors in `daf17cb` |
| 2026-05-24 | Expanded CI to all 14 SV-COMP suites (excluding juliet-java) |
| 2026-05-24 | Diagnosed & fixed 65 compile errors in `argv-tasks` (YAML grep bug) and `objects` (missing `Verifier.nondetObject()`) |
| 2026-05-24 | CI run 26365804093: 566/989 (57.2%) — argv-tasks 0 compile errors confirmed; objects still 14 (CI uses `svcomp` branch, fix on `feature/issue30-comparisons`) |

---

## How to Run

### CI (GitHub Actions)
```
# Manual trigger with custom branch (runs all 14 suites):
gh workflow run benchmarks.yml --ref main -f jr_ref=feature/issue30-comparisons -R SalmaneKhalili/java-ranger-benchmarks
```

### Available Suites
`algorithms` `argv-tasks` `autostub` `float-nonlinear-calculation` `float_unboundedloop` `java-ranger-regression` `jayhorn-recursive` `jbmc-regression` `jdart-regression` `jpf-regression` `MinePump` `objects` `rtems-lock-model` `securibench`

(Excluded: `juliet-java` — too large for CI timeout)

### Local (full benchexec)
```bash
cd java-ranger-benchmarks
bash scripts/run-full-benchexec.sh
```

### Local (CI runner, no benchexec)
```bash
cd java-ranger-benchmarks
bash scripts/run-ci-benchmarks.sh \
  /path/to/java-ranger \
  /path/to/sv-benchmarks \
  ./results \
  jpf-regression
```

---

## Key Configs

- `site.properties` — CWD-relative `./jpf-core`, `./jpf-symbc` (on `svcomp` and `feature/issue30-comparisons`)
- CI JPF config (generated per benchmark):
  ```
  target=Main
  classpath=<jpf-symbc-classes>:<compiled-benchmark-classes>
  symbolic.dp=z3bitvector
  symbolic.bvlength=64
  search.depth_limit=13
  symbolic.strings=true
  symbolic.string_dp=z3str3
  symbolic.string_dp_timeout_ms=3000
  symbolic.lazy=on
  symbolic.arrays=true
  listener=.symbc.SymbolicListener
  ```

---

## Known Issues

1. **4 FP benchmarks fail** — diagnosed, fix deferred pending systematic plan
2. **`ProblemZ3BitVector` lacks FP theory** — Z3's native `FPA` API not used; solver can't handle FpBinaryOp types
3. **`SolverTranslator.Translator` also missing FpBinaryOp handlers** — affects Green solver path
4. **`fstore` preserves operand attrs** — confirmed working in jpf-core, so symbolic flow through local variables is intact
5. **Local build broken** — `HashedAllocationContext.java` uses `SharedSecrets.getJavaLangAccess()` unavailable in this JDK 8 build; CI uses `temurin` JDK 8 which works
