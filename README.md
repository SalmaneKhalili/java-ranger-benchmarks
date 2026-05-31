# java-ranger-benchmarks

Automated benchmarking infrastructure for [Java Ranger](https://github.com/SalmaneKhalili/java-ranger).

## CI Pipeline

Runs 15 SV-COMP benchmark suites via GitHub Actions, triggered by pushes, daily schedule, or manual dispatch. Results published to GitHub Pages.

## Usage

```bash
python3 scripts/ci.py list-suites
python3 scripts/ci.py run jpf-regression <jr-dir> <sv-dir> <out-dir>
python3 scripts/ci.py merge <out-dir> <xml-files...>
python3 scripts/ci.py analyze --output analysis.html <xml-files...>
```

See `AGENTS.md` for detailed developer notes.
