# Week 1 Plan — CI Pipeline Rewrite

## Objective

Replace the fragmented CI pipeline (1 bash script + 3 Python scripts + triple-duplicated suite list) with a single Python orchestrator `scripts/ci.py`. This eliminates duplication, adds per-benchmark logging, and builds regression detection and dashboard analysis on top — all as a unified CLI tool.

## Design: `scripts/ci.py`

```
ci.py list-suites                  Print suite list as JSON array
ci.py run <suite> [--jr-dir]       compile + JPF + logs + benchexec XML
ci.py merge <xml-files...>         combined XML + table-generator
ci.py baseline <xml-dir>           generate baseline.json from results
ci.py check <xml-dir> --baseline F compare vs baseline, exit 1 on regression
ci.py analyze <xml-files...>       generate failure-analysis.html
```

### Implementation phases

---

## Phase 1: `ci.py` with `list-suites`, `run`, `merge` ✅

### Step 1 — Create `scripts/ci.py` ✅

**Suite list** — defined once as a Python constant:

```python
SUITES = [
    "algorithms", "argv-tasks", "autostub",
    "float-nonlinear-calculation", "float_unboundedloop",
    "java-ranger-regression", "jayhorn-recursive",
    "jbmc-regression", "jdart-regression",
    "jpf-regression", "MinePump", "objects",
    "rtems-lock-model", "securibench",
    "juliet-java",
]
```

**`list-suites`**: prints JSON array to stdout.

**`run <suite>`**:
1. Read YML files from `sv-benchmarks/java/<suite>/*.yml`
2. Compile Java sources with `javac -g -cp <jpf-symbc-classes>`
3. Run JPF with `.jpf` config, 60s timeout
4. Save log to `results/<suite>/logs/<benchmark>.log`
5. Parse output: `"no errors detected"` → true, `"AssertionError"` → false
6. Write benchexec-compatible results XML
7. Write `summary.txt`

**`merge <xml-files...>`**:
1. Parse each bzipped XML
2. Deduplicate by benchmark name
3. Write combined XML + run `table-generator`

### Step 2 — Remove redundant files ✅

Remove old scripts no longer needed. Keep `generate-failure-analysis.py` temporarily until Phase 2.

### Step 3 — Rewrite `.github/workflows/benchmarks.yml` ✅

Three-job pipeline: `setup` → `benchmarks` (matrix) → `report`. Matrix generated dynamically by `ci.py list-suites`.

### Step 4 — Add Juliet ✅

Juliet-Java included in SUITES list, config XML created.

### Step 5 — Verify ✅

- `ci.py list-suites` produces valid JSON
- `ci.py run jpf-regression` passes (100/104 = 96.2%)
- Logs created in `results/<suite>/logs/`
- Valid XML output parseable by `table-generator`
- Full CI pipeline triggered via `workflow_dispatch`

### Step 6 — Conditional `symbolic.fp=true` per suite ✅

8 FP-containing suites: `float-nonlinear-calculation`, `float_unboundedloop`, `autostub`, `argv-tasks`, `jpf-regression`, `juliet-java`, `jdart-regression`, `jbmc-regression`. Flag only applied where needed.

---

## Phase 2: `analyze`, `check`, `baseline` — dashboard + regression detection

### Step 1 — `baseline` subcommand ✅

Read all result XMLs, extract `suite/name → status` mapping, write JSON.

### Step 2 — `check` subcommand ✅

Compare current results against baseline JSON. Detects regressions (correct→incorrect/unknown) and improvements. Exits with count of regressions.

### Step 3 — Shared `parse_results_xml()` ✅

Single generator function eliminates 3 of 4 XML-parsing copies. Used by `baseline`, `check`, and `analyze`.

### Step 4 — `analyze` subcommand ✅

Replaces `generate-failure-analysis.py`. Uses shared parser, deduplicates by `(suite, name)`, generates same HTML structure with modular render helpers.

### Step 5 — Unified `classify_failure()` ✅

Merges `extract_error()` (raw JPF output → truncated text) and `classify_error()` (text → category) into one function. Both `run` and `analyze` call it.

### Step 6 — Wire into CI ✅

- `analyze` receives merged XML (deduped by `merge`) — no overcounting
- `check` called if `config/baseline.json` exists
- No `2>/dev/null || true` silencing
- `2>/dev/null || true` only on `git fetch --unshallow`

### Step 7 — Baseline generation (pending next CI run)

```bash
python3 scripts/ci.py baseline merged-results/ > config/baseline.json
git add config/baseline.json && git commit -m "config: initial baseline.json"
```

### Step 8 — Remove `generate-failure-analysis.py` (pending stable CI)

---

## Phase 3: Error taxonomy (deferred to Week 2)

Analyze JPF error messages across all benchmarks, identify recurring patterns, compute impact per category. Add "Biggest Wins" card to dashboard.

---

## File Summary

| File | Action | Status |
|------|--------|--------|
| `scripts/ci.py` | **NEW** (~1000 lines) | ✅ Done |
| `scripts/gen-configs.py` | **NEW** (generator) | ✅ Done |
| `scripts/generate-failure-analysis.py` | **REMOVED** (replaced by `ci.py analyze`) | ✅ Removed |
| `scripts/run-full-benchexec.sh` | **KEPT** (local runner) | ✅ Done |
| `scripts/setup-local-runner.sh` | **KEPT** (local runner) | ✅ Done |
| `.github/workflows/benchmarks.yml` | **NEW** — `analyze` + `check` wired | ✅ Done |
| `config/*.xml` (15 files) | **NEW** — generated | ✅ Done |
| `config/baseline.json` | **NEW** (after first CI run) | ❌ Pending |
| `docs/index.html` | **NEW** — stub | ✅ Done |
| `gsoc2026/fpsupport/plans/Week1.md` | **UPDATED** — reflects full plan | ✅ Done |
| `AGENTS.md` | **NEW** | ✅ Done |
