# java-ranger-benchmarks

Automated benchmarking infrastructure for [Java Ranger](https://github.com/SalmaneKhalili/java-ranger).

## TODO

- [ ] Set up GitHub Pages site to browse and visualize benchmark results
- [ ] Track historical trends (per-suite score over time)
- [ ] Add regression alerts when scores drop
- [ ] Expand CI to include `juliet-java` suite (currently excluded due to timeout)
- [ ] Add per-benchmark detail pages (logs, PC output, witness)

## Pipeline Overview

| Pipeline | Trigger | Environment | Suite |
|---|---|---|---|
| CI Benchmarks | Push to `java-ranger` + daily schedule | GitHub Actions | 14 SV-COMP suites |
| Full Benchexec | Daily cron (local machine) | Local (systemd + benchexec) | All suites |

## CI Workflow (GitHub Actions)

Runs on every push to `java-ranger/main` and daily at midnight. Uses the `run-ci-benchmarks.sh` script to execute JPF against each benchmark, parse results, and upload artifacts.

## Prerequisites

### GitHub Personal Access Token (PAT)

The workflow checks out `SalmaneKhalili/java-ranger` during CI. Since this is a cross-repo checkout, you need a PAT:

1. Create a PAT at https://github.com/settings/tokens with `repo` scope
2. Add it as a repository secret named `BENCHMARK_PAT`:
   - Settings → Secrets and variables → Actions → New repository secret
   - Name: `BENCHMARK_PAT`
   - Value: your PAT

### Trigger from java-ranger (optional)

Add this to java-ranger's `.github/workflows/`:

```yaml
name: Trigger Benchmarks
on: [push]
jobs:
  trigger:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/repository-dispatch@v2
        with:
          token: ${{ secrets.BENCHMARK_PAT }}
          repository: SalmaneKhalili/java-ranger-benchmarks
          event-type: java-ranger-push
          client-payload: '{"ref": "${{ github.ref }}", "sha": "${{ github.sha }}"}'
```

## Local Benchexec Runner (Daily)

The `run-full-benchexec.sh` script runs benchexec locally with the full suite, then commits results to this repo.

### Setup

```bash
./scripts/setup-local-runner.sh
```

This installs a systemd timer that runs benchmarks daily at 3:00 AM.

## Results

- CI results are automatically committed to the `results/` directory after each run
- Historical tracking is available via git history
- Full benchexec results can also be committed via `run-full-benchexec.sh --commit`

## Directory Structure

```
config/          - Benchexec XML configurations
scripts/         - CI and local runner scripts
gsoc2026/        - FP support progress tracker and notes
results/         - Benchmark results (created at runtime)
```
