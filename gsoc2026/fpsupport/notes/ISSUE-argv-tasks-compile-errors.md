# Root Cause: argv-tasks Compile Errors (51/70)

## Symptom
51 of 70 benchmarks in the `argv-tasks` suite report `COMPILE_ERR`.

## Diagnosis (2026-05-24)

### Bug Location
`scripts/run-ci-benchmarks.sh` line 55 (original):
```bash
input_dirs=$(grep "  - " "$yml_file" | grep -v "property_file" | grep -v "expected_verdict" | sed 's/  - //')
```

### Root Cause
The `grep "  - "` pattern only matches YAML list entries indented with exactly **2 spaces** before the dash (`  - `). However, the sv-benchmarks YAML files use **inconsistent indentation**:

- Some files use 2-space indent: `  - ../common/` — **43 files (works)**
- Some files use no indent: `- ../common/` — **51 files (broken)**

When `grep "  - "` matches nothing:
1. `input_dirs` is empty string
2. `while read` still runs once with empty `dir`
3. `src_dirs` gets `"$yml_dir/"` (the benchmark suite directory itself)
4. `find "$yml_dir/" -name "*.java"` recursively finds ALL 70 `Main.java` files
5. All 70 are passed to a single `javac` invocation
6. `javac` errors with `duplicate class: Main` (all define `public class Main`)
7. Result: COMPILE_ERR

### Verified
- Local compilation of individual failing benchmarks succeeds (confirmed no language compatibility issues)
- Running the full suite with the fixed grep reproduces the exact 51/70 compile error pattern

### Fix Applied
Changed to:
```bash
input_dirs=$(grep -E "^[[:space:]]*- " "$yml_file" | grep -v "property_file" | grep -v "expected_verdict" | sed 's/^[[:space:]]*- //')
```

The POSIX `[[:space:]]*` matches zero or more whitespace characters, handling both `- ` and `  - ` formats.

### Files Changed
- `benchmarks/scripts/run-ci-benchmarks.sh` — YAML grep pattern

### Regression Risk
The new pattern `^[[:space:]]*- ` could potentially match YAML property lines that start with `- ` (like `- property_file:`), but those are already filtered by `grep -v "property_file"`. Safe.

### Resolution
Verified: 0 compile errors in argv-tasks with the fix applied.
