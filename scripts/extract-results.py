#!/usr/bin/env python3
"""Extract failed-benchmark details from JRBench result XMLs + logs into an Excel sheet.

Usage:
  extract-results.py <results-dir> [--output FILE.xlsx]
"""

import argparse, bz2, glob, os, re, sys
import xml.etree.ElementTree as ET

try:
    from openpyxl import Workbook
except ImportError:
    print("openpyxl is required. Install with: pip install openpyxl", file=sys.stderr)
    sys.exit(1)


def parse_results_xml(fpath):
    """Yield dicts: {suite, name, status, expected, error, logfile}."""
    try:
        if fpath.endswith(".bz2"):
            with bz2.open(fpath) as f:
                tree = ET.parse(f)
        else:
            tree = ET.parse(fpath)
    except Exception as e:
        print(f"  Warning: could not parse {fpath}: {e}", file=sys.stderr)
        return

    root = tree.getroot()
    for run in root.findall("run"):
        suite = run.get("suite") or root.get("benchmarkname", "unknown")
        name = (run.get("name") or "").rsplit("/", 1)[-1]
        name = name.replace(".yml", "").replace(".java", "")
        expected = run.get("expectedVerdict", "?")

        cols = {}
        for col in run.findall("column"):
            cols[col.get("title")] = col.get("value")

        status = cols.get("status", "unknown")
        error = ""
        error_el = run.find("column[@title='error']")
        if error_el is not None:
            error = error_el.get("value", "")
        if not error and status != "correct":
            category_el = run.find("column[@title='category']")
            if category_el is not None and category_el.get("value") == "error":
                error = cols.get("error", "")

        yield {
            "suite": suite,
            "name": name,
            "status": status,
            "expected": expected,
            "error": error,
            "logfile": run.get("logfile", ""),
        }


def extract_full_error(log_path):
    """Read the full error/exception trace from a JPF .log file."""
    if not log_path or not os.path.isfile(log_path):
        return ""
    try:
        with open(log_path, errors="replace") as f:
            text = f.read()
    except OSError:
        return ""

    lines = text.splitlines()
    error_lines = []
    in_error = False
    for line in lines:
        s = line.rstrip()
        if not s:
            if in_error:
                break
            continue
        if "[SEVERE]" in s:
            error_lines.append(s)
            in_error = True
        elif in_error:
            error_lines.append(s)
        elif "Exception" in s or "Error" in s or "error:" in s:
            error_lines.append(s)
            in_error = True
        elif "at " == s[:3].strip():
            error_lines.append(s)
        elif error_lines and not s.startswith(" ") and not s.startswith("\t"):
            break

    return "\n".join(error_lines) if error_lines else ""


def safe_str(v, maxlen=32767):
    s = str(v) if v is not None else ""
    return s[:maxlen]


def main():
    parser = argparse.ArgumentParser(
        description="Extract failed benchmark results to Excel"
    )
    parser.add_argument("results_dir", help="Directory containing result XMLs and logs/")
    parser.add_argument("--output", "-o", default="results.xlsx",
                        help="Output Excel file path (default: results.xlsx)")
    args = parser.parse_args()

    results_dir = args.results_dir
    if not os.path.isdir(results_dir):
        print(f"Error: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    xml_files = sorted(glob.glob(os.path.join(results_dir, "**", "*.results.xml*"),
                                 recursive=True))
    if not xml_files:
        xml_files = sorted(glob.glob(os.path.join(results_dir, "*.results.xml*")))

    if not xml_files:
        print(f"No .results.xml files found in {results_dir}", file=sys.stderr)
        sys.exit(1)

    wb = Workbook()
    ws = wb.active
    ws.title = "Failures"
    ws.append(["Suite", "Benchmark", "Failure", "Error Message"])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 100

    failure_count = 0
    for fpath in xml_files:
        for rec in parse_results_xml(fpath):
            if rec["status"] == "correct":
                continue
            failure_count += 1

            log_path = rec["logfile"]
            if not log_path:
                log_path = os.path.join(results_dir, "logs", f"{rec['name']}.log")

            full_error = extract_full_error(log_path)
            if not full_error:
                full_error = rec["error"]

            ws.append([
                safe_str(rec["suite"]),
                safe_str(rec["name"]),
                safe_str(rec["status"]),
                safe_str(full_error),
            ])

    out_path = args.output
    wb.save(out_path)
    print(f"Wrote {failure_count} failures to {out_path}")


if __name__ == "__main__":
    main()
