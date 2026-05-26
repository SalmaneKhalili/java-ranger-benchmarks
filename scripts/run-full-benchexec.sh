#!/bin/bash
# Full Benchexec Runner (Local Machine)
# Runs the full benchmark suite via benchexec and commits results.
#
# Usage:
#   ./run-full-benchexec.sh            # Run all suites
#   ./run-full-benchexec.sh --commit   # Run and commit results

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
RESULTS_DIR="$REPO_DIR/results"
JR_DIR="/home/salmane/IdeaProjects/java-ranger"
SV_BENCH_DIR="/home/salmane/Desktop/OSS/sv-benchmarks"
BENCHCONFIG_DIR="$REPO_DIR/config"

COMMIT="${1:-}"

# Ensure java-ranger is built
echo "=== Building Java Ranger ==="
cd "$JR_DIR"
./gradlew :jpf-core :jpf-symbc :jpf-symbc:buildJars -x test 2>&1 | tail -5

echo ""
echo "=== Running Full Benchexec Suite ==="

run_timestamp=$(date +%Y-%m-%d_%H-%M-%S)
run_suites=()

# Run each benchmark suite
for config in "$BENCHCONFIG_DIR"/*.xml; do
    suite_name=$(basename "$config" .xml)
    echo ""
    echo "--- Suite: $suite_name ---"

    cd "$JR_DIR"
    systemd-run --user --scope --slice=benchexec -p Delegate=yes \
        benchexec "$config" --read-only-dir=/ 2>&1 | tee "$RESULTS_DIR/$suite_name.$run_timestamp.log"

    # Move results to the results directory
    if ls results/jpf-regression.*.xml.bz2 2>/dev/null; then
        mv results/jpf-regression.*.xml.bz2 "$RESULTS_DIR/" 2>/dev/null || true
    fi

    run_suites+=("$suite_name")
done

# Generate HTML tables
cd "$REPO_DIR"
table-generator "$RESULTS_DIR"/*.xml.bz2 -o "$RESULTS_DIR/" 2>/dev/null || true

# Generate a summary file
{
    echo "Benchmark Run: $run_timestamp"
    echo "Suites: ${run_suites[*]}"
    echo "Java Ranger commit: $(cd "$JR_DIR" && git rev-parse HEAD)"
    echo "Java Ranger branch: $(cd "$JR_DIR" && git rev-parse --abbrev-ref HEAD)"
    echo ""
    for logfile in "$RESULTS_DIR"/*."$run_timestamp".log; do
        suite=$(basename "$logfile" .log)
        echo "=== $suite ==="
        grep -E "Statistics:|correct:|incorrect:|Score:" "$logfile" 2>/dev/null || true
        echo ""
    done
} > "$RESULTS_DIR/run-$run_timestamp-summary.txt"

cat "$RESULTS_DIR/run-$run_timestamp-summary.txt"

# Commit and push if --commit flag is set
if [ "$COMMIT" = "--commit" ]; then
    echo ""
    echo "=== Committing results ==="
    cd "$REPO_DIR"
    git add results/
    git commit -m "benchmark: results for $run_timestamp

Suites: ${run_suites[*]}
JR commit: $(cd "$JR_DIR" && git rev-parse HEAD)"
    git push origin main
    echo "Results committed and pushed."
fi

echo ""
echo "Done. Results in $RESULTS_DIR"
