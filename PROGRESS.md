# Java Ranger Benchmarks — Progress Tracker

## Goal

Automated daily benchmarking pipeline for Java Ranger's floating-point support improvements, targeting SV-COMP `jpf-regression` and `jbmc-regression` suites. Track per-benchmark correctness, failure analysis, and provide transparent results via GitHub Pages.

---

## Recent Milestones

| Date | Event |
|------|-------|
| 2026-05-25 | **Error extraction overhaul**: JPF log errors now captured verbatim instead of naive grep patterns |
| 2026-05-25 | **Failure analysis classifier updated**: word-boundary math regex, context-aware NaN detection |
| 2026-05-25 | **Summary.txt fallback fixed**: glob depth corrected to avoid double-counting across suites |
| 2026-05-25 | **All changes verified**: 214 benchmarks across 3 suites (jpf-regression, algorithms, argv-tasks) |
| 2026-05-25 | Commit `dcf1f77` pushed to main: "fix: capture actual JPF error messages instead of naive grep patterns" |
| 2026-05-24 | CI expanded to all 14 SV-COMP suites — 566/989 correct (57.2%) |
| 2026-05-24 | Compile errors fixed: argv-tasks (51→0), objects (14 pending — CI uses `svcomp` branch) |
| 2026-05-24 | SPF FP comparison completed: no FP features in SPF that JR is missing |

---

## Repos

| Repo | Visibility | Branch | Purpose |
|------|-----------|--------|---------|
| `SalmaneKhalili/java-ranger` | public | `feature/issue30-comparisons` | Main development branch for FP issues #27–#30 |
| `SalmaneKhalili/java-ranger` | public | `svcomp` | Stable SV-COMP baseline |
| `SalmaneKhalili/java-ranger-benchmarks` | private | `main` | CI pipeline, runner scripts, benchexec configs, results |

Upstream: `vaibhavbsharma/java-ranger` (public, parent issue [#26](https://github.com/vaibhavbsharma/java-ranger/issues/26))

---

## What's Been Accomplished

### FP Support (Issues #27–#30)
- [x] **#27** IEEE‑754 special values (NaN, infinities, signed zero) — MERGED
- [x] **#28** FP arithmetic with rounding (FADD, FSUB, FMUL, FDIV → FpAdd, FpSub, FpMul, FpDiv)
- [x] **#29** `isNaN` predicate — PR opened
- [x] **#30** FP comparisons with NaN (FCMPG/FCMPL → 4-choice PCChoiceGenerator)

### CI Pipeline
- GitHub Actions workflow with matrix builds across 14 SV-COMP suites (240 min timeout)
- Local `benchexec` runner with systemd timer (daily at 00:26)
- CI runner (`run-ci-benchmarks.sh`) compiles Java → runs JPF → parses SAFE/UNSAFE
- Benchexec-identical XML output + `table-generator` HTML results (`docs/all-suites.table.html`)
- GitHub Pages serves results at `SalmaneKhalili/java-ranger-benchmarks`
- Per-issue analysis notes in `gsoc2026/fpsupport/notes/`

### Failure Analysis
- `generate-failure-analysis.py` produces per-suite and aggregate failure reports
- Classifier uses word-boundary regex for math functions, context-aware NaN detection
- Error extraction captures actual JPF log errors (SEVERE lines, stack traces)
- Summary.txt fallback corrected to `*/summary.txt` (not `**/summary.txt`) to prevent double-counting

### SPF Comparison
- Thorough comparison of java-ranger vs upstream SPF for FP support
- Conclusion: SPF has no dedicated FP support — uses `RealExpression` for all float/double operations
- No SPF FP features that JR is missing — marked as checked per Dr. Soha's request

---

## What's in Progress

### Immediate
1. **Failure reason annotations** per-benchmark (for Google Sheet export)
2. **Google Sheet** with SV-COMP results + per-benchmark failure reasons
3. **Meeting with Dr. Soha and Franck** (potential date: 2026-05-26)

### CI Improvements
4. `generate-benchexec-xml.py` ready — needs CI integration for full XML artifact pipeline
5. Automatic failure reason extraction from JPF logs (recently improved, now in CI)

---

## What's Blocked

1. **`objects` suite (14 compile errors)**: `Verifier.nondetObject()` fix is on `feature/issue30-comparisons` but CI uses `svcomp` branch. Needs merge or CI ref update.
2. **4 FP benchmarks fail in jpf-regression**: `PCParser.getExpression()` doesn't handle `FpBinaryOp` nodes. Fix deferred pending systematic solver integration plan.
3. **`ProblemZ3BitVector` lacks FP theory**: Z3's native `FPA` API not used; solver can't handle `FpBinaryOp` types.
4. **`SolverTranslator.Translator` also missing `FpBinaryOp` handlers**: Affects Green solver path.
5. **Local build broken**: `HashedAllocationContext.java` uses `SharedSecrets.getJavaLangAccess()` unavailable in this JDK 8 build; CI uses `temurin` JDK 8 which works.

---

## Key Decisions

1. **Error extraction**: Use Python inline script in bash to read JPF logs verbatim (SEVERE lines, Exception stack traces) rather than fragile grep patterns — much more maintainable for new error types.
2. **Failure classifier**: Replace grep-based pattern tuples with word-boundary regex (`\b(sin|cos|...)\b`) and context-aware NaN detection (only classify NaN-related errors when paired with Exception/Error/SEVERE keywords) — reduces false positives.
3. **Summary.txt fallback**: Changed from recursive `**/summary.txt` (which double-counted when suites shared parents) to single-level `*/summary.txt`.
4. **Benchexec XML output**: CI now produces DTD-valid XML identical to local benchexec runs, enabling `table-generator` HTML output without requiring cgroups.
5. **`isNaN` as dedicated class**: `RealIsNaN` extends `IntegerExpression` (returns boolean 0/1), justified because IEEE-754 says NaN ≠ NaN, making `x == NaN` impossible to express with existing comparison infrastructure.

---

## Next Steps

1. Meet with Dr. Soha and Franck to align on priorities (FP comparison finalization, isNaN AST design, CI improvements)
2. Create Google Sheet with per-suite + per-benchmark failure reason annotations
3. Integrate `generate-benchexec-xml.py` fully into CI pipeline with `table-generator` artifact upload
4. Resolve `objects` suite compile errors (merge `Verifier.nondetObject()` into `svcomp` or point CI to `feature/issue30-comparisons`)
5. Begin solver integration: extend `ProblemZ3BitVector` with Z3 FP theory API for `FpBinaryOp` handling
6. Narrowing conversions (F2L, D2I, F2I, D2L rounding/overflow)
7. Widening conversions (I2F, L2D, I2D, etc.)
8. Symbolic remainder (`frem`/`drem`)

---

## Relevant Files

| File | Purpose |
|------|---------|
| `scripts/run-ci-benchmarks.sh` | CI runner: compile, JPF, parse results, generate XML |
| `scripts/generate-benchexec-xml.py` | Generate benchexec-identical XML from JSONL results |
| `scripts/generate-failure-analysis.py` | Classify failures by error type, produce HTML report |
| `scripts/run-full-benchexec.sh` | Local benchexec wrapper (one-shot or with `--commit`) |
| `scripts/setup-local-runner.sh` | systemd timer setup for daily runs |
| `.github/workflows/benchmarks.yml` | GitHub Actions CI definition |
| `config/*.xml` | Benchexec configs (1 per suite) |
| `docs/all-suites.table.html` | Merged results table (GitHub Pages) |
| `docs/failure-analysis.html` | Failure analysis report (GitHub Pages) |
| `gsoc2026/fpsupport/PROGRESS.md` | Detailed FP support progress tracker |
| `gsoc2026/fpsupport/DISCUSSION-2026-05-25.md` | Discord conversation transcript |
| `gsoc2026/fpsupport/PLAN-migrate-ci-benchexec-output.md` | Plan for benchexec XML migration |
| `gsoc2026/fpsupport/notes/ISSUE-27-special-values.md` | NaN, infinities, signed zero analysis |
| `gsoc2026/fpsupport/notes/ISSUE-28-fp-arithmetic-rounding.md` | FP arithmetic + narrowing conversions |
| `gsoc2026/fpsupport/notes/ISSUE-29-isNaN.md` | `isNaN` predicate analysis |
| `gsoc2026/fpsupport/notes/ISSUE-argv-tasks-compile-errors.md` | YAML indentation fix |
| `gsoc2026/fpsupport/notes/ISSUE-objects-compile-errors.md` | `Verifier.nondetObject()` fix |
| `gsoc2026/fpsupport/notes/CI-PROGRESS.md` | CI pipeline guide |
| `AGENTS.md` | Agent notes (squashed commit history) |
| `COMMANDS.md` | Command reference |
| `README.md` | Repo overview |
| `results/` | Per-suite benchmark outputs (git-committed) |

---

## Current CI Status

| Suite | Benchmarks | Correct | Incorrect | Unknown | CompileErr | Timeout | Score |
|-------|-----------|---------|-----------|---------|-----------|---------|-------|
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

(Results from CI run `26365804093` — see `gsoc2026/fpsupport/PROGRESS.md` for full breakdown.)
