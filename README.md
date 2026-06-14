# Java Ranger Benchmarks

[Latest CI results](https://salmanekhalili.github.io/java-ranger-benchmarks/vef529cd7e5e03ed9524e8038d08548697c128400/index.html)

Automated benchmarking of [Java Ranger](https://github.com/SalmaneKhalili/java-ranger) against the [SV-Benchmarks](https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks) Java corpus. Each push to `main` triggers a full run across 15 benchmark suites via GitHub Actions.

## Suites

| Suite | Category |
|-------|----------|
| `algorithms` | Standard algorithms |
| `argv-tasks` | Command-line argument tasks |
| `autostub` | Auto-generated stubs |
| `float-nonlinear-calculation` | Float nonlinear math |
| `float_unboundedloop` | Float with unbounded loops |
| `java-ranger-regression` | Java Ranger regression tests |
| `jayhorn-recursive` | JayHorn recursive benchmarks |
| `jbmc-regression` | JBMC regression tests |
| `jdart-regression` | JDart regression tests |
| `jpf-regression` | JPF regression tests |
| `juliet-java` | Juliet test suite (Java) |
| `MinePump` | Mine pump control system |
| `objects` | Object-oriented patterns |
| `rtems-lock-model` | RTEMS lock model |
| `securibench` | Security benchmarks |

## Directory Structure

- `config/` — Per-suite configuration files (BenchExec XML)
- `scripts/ci.py` — CI orchestrator script
- `results/` — Per-suite raw results (bz2 compressed XML)
- `merged-results/` — Deduplicated merged results
- `docs/` — Generated HTML dashboard (landing page, per-suite pages, failure analysis)
- `.github/workflows/benchmarks.yml` — CI pipeline definition

## Running Locally

```bash
# List available suites
python3 scripts/ci.py list-suites

# Run a single suite
python3 scripts/ci.py run <suite> <jr-dir> <sv-dir> <output-dir>

# Merge per-suite results
python3 scripts/ci.py merge <output-dir> <xml-files...>

# Generate baseline
python3 scripts/ci.py baseline <xml-dir> > config/baseline.json

# Check regressions against baseline
python3 scripts/ci.py check <xml-dir> --baseline config/baseline.json

# Generate HTML report
python3 scripts/ci.py report <xml-files...> --output-dir docs [--build-id SHA]
```

## Results

Results are available at `docs/` (served via GitHub Pages) and archived in `results/` by suite.
