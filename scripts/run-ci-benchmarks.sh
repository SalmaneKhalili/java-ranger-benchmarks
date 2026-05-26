#!/bin/bash
# CI Benchmark Runner for GitHub Actions
# Runs JPF benchmarks and produces benchexec-compatible XML output.
#
# Usage:
#   ./run-ci-benchmarks.sh <java-ranger-dir> <sv-benchmarks-dir> <output-dir> [benchmark-sets...]
#
# Example:
#   ./run-ci-benchmarks.sh /path/to/java-ranger /path/to/sv-benchmarks ./results jpf-regression

set -uo pipefail

JR_DIR="$1"
SV_BENCH_DIR="$2"
OUTPUT_DIR="$3"
shift 3

# Script directory (for finding the XML generator)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

BENCHMARK_SETS=("$@")
if [ ${#BENCHMARK_SETS[@]} -eq 0 ]; then
    BENCHMARK_SETS=("algorithms" "argv-tasks" "autostub" "float-nonlinear-calculation" "float_unboundedloop" "java-ranger-regression" "jayhorn-recursive" "jbmc-regression" "jdart-regression" "jpf-regression" "MinePump" "objects" "rtems-lock-model" "securibench")
fi

# JPF paths
JPF_CORE_JAR="$JR_DIR/jpf-core/build/RunJPF.jar"
JPF_SYMBC_CLASSES="$JR_DIR/jpf-symbc/build/classes"
JPF_SYMBC_LIB="$JR_DIR/jpf-symbc/lib"
CLASSPATH_ADDED="$JPF_SYMBC_CLASSES"

# Git version for tool version metadata
JR_VERSION="$(cd "$JR_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "N/A")"
JR_BRANCH="$(cd "$JR_DIR" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "N/A")"
JR_VERSION_STR="$JR_BRANCH@$JR_VERSION"

# JPF options string (used in XML metadata)
JPF_OPTIONS="+target=Main +symbolic.dp=z3bitvector +symbolic.bvlength=64 +search.depth_limit=13 +symbolic.strings=true +symbolic.string_dp=z3str3 +symbolic.string_dp_timeout_ms=3000 +symbolic.lazy=on +symbolic.arrays=true +listener=.symbc.SymbolicListener"

# Overall run timestamp
RUN_TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
RUN_START_ISO=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)

# Output files (per suite run, but we set them per-suite inside the loop)
SUMMARY="$OUTPUT_DIR/summary.txt"

# Counters (across all suites in this invocation)
TOTAL=0
CORRECT=0
INCORRECT=0
UNKNOWN=0
COMPILE_ERR=0
TIMEOUT=0

run_benchmark() {
    local yml_file="$1"
    local benchmark_name
    benchmark_name=$(basename "$yml_file" .yml)

    local yml_dir
    yml_dir=$(dirname "$yml_file")

    # Parse expected verdicts and input files from YML
    local expected_verdict
    expected_verdict=$(grep "expected_verdict:" "$yml_file" | head -1 | awk '{print $2}')

    local property_file
    property_file=$(grep "property_file:" "$yml_file" | head -1 | sed 's/.*property_file:[[:space:]]*//' | tr -d ' ')

    local input_dirs
    input_dirs=$(grep -E "^[[:space:]]*- " "$yml_file" | grep -v "property_file" | grep -v "expected_verdict" | sed 's/^[[:space:]]*- //')

    # Build source directories relative to the YML file's location
    local src_dirs=()
    local files_csv=""
    while IFS= read -r dir; do
        dir=$(echo "$dir" | xargs)
        src_dirs+=("$yml_dir/$dir")
        if [ -n "$files_csv" ]; then
            files_csv="$files_csv,"
        fi
        files_csv="$files_csv$yml_dir/$dir"
    done <<< "$input_dirs"

    # Resolve property file path
    local prop_file_path=""
    if [ -n "$property_file" ]; then
        prop_file_path="$yml_dir/$property_file"
        # Normalize path
        prop_file_path=$(cd "$(dirname "$prop_file_path")" 2>/dev/null && pwd)/$(basename "$prop_file_path") 2>/dev/null || echo "$prop_file_path"
    fi

    # Find all Java files in the source directories (skip only Verifier.java, use jpf-symbc's)
    local java_files=()
    for dir in "${src_dirs[@]}"; do
        if [ -d "$dir" ]; then
            while IFS= read -r f; do
                case "$(basename "$f")" in
                    Verifier.java) ;;
                    *) java_files+=("$f") ;;
                esac
            done < <(find "$dir" -name "*.java" 2>/dev/null)
        fi
    done

    if [ ${#java_files[@]} -eq 0 ]; then
        python3 -c "
import json, sys
with open('$RESULTS_JSONL', 'a') as f:
    json.dump({'name': '$benchmark_name', 'expected': '$expected_verdict', 'actual': 'UNKNOWN', 'verdict': 'UNKNOWN', 'error': 'no source files', 'cputime': 0, 'walltime': 0, 'exit_code': 0, 'files': [], 'property_file': '$prop_file_path'}, f)
    f.write('\n')
" 2>/dev/null
        echo "  SKIP  $benchmark_name (no source files)"
        ((UNKNOWN++))
        ((TOTAL++))
        return
    fi

    # Create temp directory for compilation
    local tmp_dir
    tmp_dir=$(mktemp -d -t jpf-ci-XXXXXX)
    trap "rm -rf $tmp_dir" RETURN

    mkdir -p "$tmp_dir/target/classes"

    # Compile
    local compile_ok=true
    if ! javac -g -cp "$CLASSPATH_ADDED":"$tmp_dir/target/classes" \
         -d "$tmp_dir/target/classes" "${java_files[@]}" 2>"$tmp_dir/compile.log"; then
        compile_ok=false
        python3 -c "
import json, sys
with open('$RESULTS_JSONL', 'a') as f:
    err = open('$tmp_dir/compile.log').read().strip()
    json.dump({'name': '$benchmark_name', 'expected': '$expected_verdict', 'actual': 'UNKNOWN', 'verdict': 'UNKNOWN', 'error': err, 'cputime': 0, 'walltime': 0, 'exit_code': 1, 'files': ['$files_csv'], 'property_file': '$prop_file_path'}, f)
    f.write('\n')
" 2>/dev/null
        echo "  COMPILE_ERR  $benchmark_name"
        ((COMPILE_ERR++))
        ((UNKNOWN++))
        ((TOTAL++))
        return
    fi

    # Create JPF config
    local jpf_config="$tmp_dir/config.jpf"
    cat > "$jpf_config" << EOF
target=Main
classpath=$JPF_SYMBC_CLASSES:$tmp_dir/target/classes
symbolic.dp=z3bitvector
symbolic.bvlength=64
search.depth_limit=13
symbolic.strings=true
symbolic.string_dp=z3str3
symbolic.string_dp_timeout_ms=3000
symbolic.lazy=on
symbolic.arrays=true
listener=.symbc.SymbolicListener
EOF

    # Run JPF with timeout (60s per benchmark)
    local log_file="$tmp_dir/jpf.log"
    local start_time_ns
    start_time_ns=$(date +%s%N)

    local timeout_sec=60
    local exit_code=0

    # Run JPF with timeout from the java-ranger directory
    if (cd "$JR_DIR" && timeout "$timeout_sec" env LD_LIBRARY_PATH="$JPF_SYMBC_LIB:${LD_LIBRARY_PATH:-}" \
         java -Xmx1024m -ea \
         -Djava.library.path="$JPF_SYMBC_LIB" \
         -jar "$JPF_CORE_JAR" "$jpf_config") > "$log_file" 2>&1; then
        : # JPF completed normally
    else
        exit_code=$?
        if [ $exit_code -eq 124 ]; then
            python3 -c "
import json, sys
with open('$RESULTS_JSONL', 'a') as f:
    json.dump({'name': '$benchmark_name', 'expected': '$expected_verdict', 'actual': 'UNKNOWN', 'verdict': 'UNKNOWN', 'error': 'timeout after ${timeout_sec}s', 'cputime': ${timeout_sec}, 'walltime': ${timeout_sec}, 'exit_code': 124, 'files': ['$files_csv'], 'property_file': '$prop_file_path'}, f)
    f.write('\n')
" 2>/dev/null
            echo "  TIMEOUT  $benchmark_name"
            ((TIMEOUT++))
            ((UNKNOWN++))
            ((TOTAL++))
            return
        fi
    fi

    local end_time_ns
    end_time_ns=$(date +%s%N)
    local elapsed_ms=$(( (end_time_ns - start_time_ns) / 1000000 ))
    local elapsed_s
    elapsed_s=$(echo "scale=6; $elapsed_ms / 1000" | bc 2>/dev/null || echo "0")

    # Parse result
    local actual="UNKNOWN"
    if grep "no errors detected" "$log_file" > /dev/null 2>&1; then
        actual="true"
    elif grep "java.lang.AssertionError" "$log_file" > /dev/null 2>&1; then
        actual="false"
    fi

    # Determine correctness
    local verdict="UNKNOWN"
    if [ "$actual" = "$expected_verdict" ]; then
        verdict="CORRECT"
    elif [ "$actual" = "UNKNOWN" ]; then
        verdict="UNKNOWN"
    else
        verdict="INCORRECT"
    fi

    # Extract actual error from JPF log for UNKNOWN/INCORRECT
    local error_msg=""
    if [ "$verdict" != "CORRECT" ] && [ -s "$log_file" ]; then
        error_msg=$(python3 -c "
import json, sys
with open('$log_file') as f:
    lines = f.readlines()
# Extract relevant error lines (SEVERE, Exception, stack trace)
errors = []
for i, line in enumerate(lines):
    s = line.rstrip()
    if not s or 'JavaPathFinder' in s:
        continue
    # Exception/error lines
    if '[SEVERE]' in s or 'Exception' in s or 'Error' in s or 'error:' in s:
        errors.append(s[:300])
    # Stack trace frames (tab or space indented 'at ')
    elif len(errors) > 0 and ('\tat ' in s or s.lstrip().startswith('at ')) and len(errors) < 4:
        errors.append(s[:200])
if not errors:
    # Last 5 meaningful lines as fallback
    meaningful = [l.rstrip() for l in lines if l.strip() and 'JavaPathFinder' not in l]
    errors = meaningful[-5:]
sys.stdout.write(' | '.join(errors[:5]))
" 2>/dev/null || echo "")
    fi

    # Write result as JSONL
    python3 -c "
import json, sys
with open('$RESULTS_JSONL', 'a') as f:
    json.dump({
        'name': '$benchmark_name',
        'expected': '$expected_verdict',
        'actual': '$actual',
        'verdict': '$verdict',
        'error': '$error_msg',
        'cputime': $elapsed_s,
        'walltime': $elapsed_s,
        'exit_code': $exit_code,
        'files': ['$files_csv'],
        'property_file': '$prop_file_path',
        'logfile': '$log_file'
    }, f)
    f.write('\n')
" 2>/dev/null

    printf "  %-10s %s (%ss)\n" "$verdict" "$benchmark_name" "$elapsed_s"

    # Update counters
    case "$verdict" in
        CORRECT)   ((CORRECT++)) ;;
        INCORRECT) ((INCORRECT++)) ;;
        *)         ((UNKNOWN++)) ;;
    esac
    ((TOTAL++))
}

# Ensure output directory exists and is clean (no stale files from retried jobs)
rm -f "$OUTPUT_DIR"/*.results.* 2>/dev/null || true
mkdir -p "$OUTPUT_DIR"

# Main loop
echo "=== Java Ranger CI Benchmarks ==="
echo "Started: $(date)"
echo "JR: $JR_DIR ($JR_VERSION_STR)"
echo "SV-Benchmarks: $SV_BENCH_DIR"
echo ""

for suite in "${BENCHMARK_SETS[@]}"; do
    suite_dir="$SV_BENCH_DIR/java/$suite"
    if [ ! -d "$suite_dir" ]; then
        echo "Warning: Suite directory not found: $suite_dir"
        continue
    fi

    # Per-suite output files
    RESULTS_JSONL="$OUTPUT_DIR/results.jsonl"
    RESULTS_XML="$OUTPUT_DIR/$suite.$RUN_TIMESTAMP.results.xml"
    RESULTS_XML_BZ2="$RESULTS_XML.bz2"

    count=$(ls "$suite_dir"/*.yml 2>/dev/null | wc -l)
    echo "--- $suite ($count benchmarks) ---"

    # Clear previous JSONL for this suite
    : > "$RESULTS_JSONL"

    for yml in "$suite_dir"/*.yml; do
        [ -f "$yml" ] || continue
        run_benchmark "$yml"
    done

    echo ""

    # Generate benchexec XML
    if [ -s "$RESULTS_JSONL" ]; then
        RUN_END_ISO=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)

        python3 "$SCRIPT_DIR/generate-benchexec-xml.py" \
            --suite "$suite" \
            --results "$RESULTS_JSONL" \
            --starttime "$RUN_START_ISO" \
            --endtime "$RUN_END_ISO" \
            --options "$JPF_OPTIONS" \
            --tool-version "$JR_VERSION_STR" \
            --output "$RESULTS_XML"

        # Compress with bzip2
        bzip2 -f "$RESULTS_XML"
        echo "Results: $RESULTS_XML_BZ2"

        # Generate HTML table via table-generator if available
        if command -v table-generator &>/dev/null; then
            echo "Generating HTML table..."
            table-generator "$RESULTS_XML_BZ2" -o "$OUTPUT_DIR/" 2>/dev/null || true
            # Also generate CSV
            table-generator "$RESULTS_XML_BZ2" -o "$OUTPUT_DIR/" -f csv 2>/dev/null || true
        else
            echo "Warning: table-generator not found, skipping HTML table generation"
        fi
    fi
done

# Generate summary
{
    echo "=== Results Summary ==="
    echo "Total:      $TOTAL"
    echo "Correct:    $CORRECT"
    echo "Incorrect:  $INCORRECT"
    echo "Unknown:    $UNKNOWN"
    echo "CompileErr: $COMPILE_ERR"
    echo "Timeout:    $TIMEOUT"
    echo ""
    echo "Score: $CORRECT / $TOTAL"
} > "$SUMMARY"

cat "$SUMMARY"

echo ""
echo "Results saved to $OUTPUT_DIR"
echo "Summary saved to $SUMMARY"
