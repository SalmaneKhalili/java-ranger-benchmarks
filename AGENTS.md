# Agent Notes

## Implementation Plans

Always consult the implementation plans in `gsoc2026/fpsupport/plans/` before starting or modifying work on a task.

## CI Pipeline

```bash
# List suites
python3 scripts/ci.py list-suites

# Run a single suite locally
python3 scripts/ci.py run <suite> <jr-dir> <sv-dir> <output-dir>

# Merge per-suite results
python3 scripts/ci.py merge <output-dir> <xml-files...>

# Generate baseline
python3 scripts/ci.py baseline <xml-dir> > config/baseline.json

# Check regressions against baseline
python3 scripts/ci.py check <xml-dir> --baseline config/baseline.json

# Generate failure analysis page
python3 scripts/ci.py analyze --output failure-analysis.html <xml-files...>
```

## Key architecture notes

- All XML parsing goes through `parse_results_xml()` — single generator, shared across subcommands
- Error classification unified in `classify_failure()` — returns `(category, display_text)`
- `analyze` deduplicates by `(suite, name)` — no overcounting
- CI workflow passes merged XML to `analyze`, not raw artifact XMLs
- `ci.py merge` deduplicates by `name` attribute in `seen_names` set
