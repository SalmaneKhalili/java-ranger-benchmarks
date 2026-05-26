#!/usr/bin/env python3
"""
Generate failure analysis HTML page from benchexec-compatible XML results.
Extracts all incorrect and unknown benchmarks with failure reasons,
and produces a clean, standalone HTML page for GitHub Pages.

Usage:
  python3 generate-failure-analysis.py <results-dir> <output-html>
"""

import argparse
import bz2
import os
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from collections import Counter


import re

_MATH_FUNCS = re.compile(r'\b(sin|cos|tan|sqrt|log|asin|acos|atan)\b', re.IGNORECASE)


def classify_error(msg):
    if not msg:
        return "unknown"
    m = msg.lower()
    if "pcparser" in m or "fpadd" in m or "fpsub" in m or "fpmul" in m or "fpdiv" in m:
        return "unsupported FP expression in PCParser"
    if "compile" in m or "cannot find symbol" in m:
        return "compile error"
    if "timeout" in m or "timed out" in m or "exit_code 124" in m:
        return "timeout"
    if "isnan" in m or ("nan" in m and ("exception" in m or "error" in m or "severe" in m)):
        return "unsupported NaN handling"
    if _MATH_FUNCS.search(msg) and ("exception" in m or "error" in m or "severe" in m):
        return "unsupported math function"
    if "exception" in m or "error" in m or "severe" in m:
        return "runtime error"
    return "unknown"


def escape(s):
    return xml_escape(str(s))


def generate_html(failures, by_suite, suite_counts, total_benchmarks, build_id=None):
    total_failures = len(failures)
    incorrect = sum(1 for f in failures if f["status"] == "incorrect")
    unknown = sum(1 for f in failures if f["status"] == "unknown")

    failure_reasons = Counter(f["reason"] for f in failures)
    reasons_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{count}</td></tr>\n"
        for label, count in failure_reasons.most_common()
    )

    suite_rows = "".join(
        f"<tr><td>{escape(suite)}</td><td>{counts['total']}</td>"
        f"<td>{counts['incorrect']}</td><td>{counts['unknown']}</td>"
        f"<td>{counts['correct']}</td></tr>\n"
        for suite, counts in sorted(by_suite.items())
    )

    table_rows = "".join(
        f"<tr>"
        f"<td>{escape(f['suite'])}</td>"
        f"<td>{escape(f['name'])}</td>"
        f"<td class=\"{'incorrect' if f['status'] == 'incorrect' else 'unknown'}\">{f['status']}</td>"
        f"<td>{escape(f['expected'])}</td>"
        f"<td>{escape(f['reason'])}</td>"
        f"<td>{f['time']}</td>"
        f"<td><code>{escape(f['error'])}</code></td>"
        f"</tr>\n"
        for f in failures
    )

    v = build_id or __import__('datetime').datetime.now().strftime('%Y%m%d%H%M%S')
    cb = f"?v={v}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta name="build-id" content="{v}">
<title>Java Ranger — Failure Analysis</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #f5f5f5; }}
h1, h2, h3 {{ color: #333; }}
.container {{ max-width: 1400px; margin: auto; }}
.summary-cards {{ display: flex; gap: 20px; margin: 20px 0; }}
.card {{ background: white; border-radius: 8px; padding: 20px; flex: 1; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.card h3 {{ margin: 0 0 10px 0; }}
.card .number {{ font-size: 2em; font-weight: bold; }}
.card.incorrect {{ border-left: 4px solid #e74c3c; }}
.card.unknown {{ border-left: 4px solid #f39c12; }}
.card.total {{ border-left: 4px solid #3498db; }}
.card.rate {{ border-left: 4px solid #2ecc71; }}
.card .number.incorrect {{ color: #e74c3c; }}
.card .number.unknown {{ color: #f39c12; }}
.card .number.total {{ color: #3498db; }}
.card .number.rate {{ color: #2ecc71; }}
table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-radius: 8px; overflow: hidden; margin: 20px 0; }}
th {{ background: #3498db; color: white; padding: 12px; text-align: left; font-weight: 600; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #eef; font-size: 0.9em; }}
tr:hover {{ background: #f8f9ff; }}
.incorrect {{ color: #e74c3c; font-weight: bold; }}
.unknown {{ color: #f39c12; font-weight: bold; }}
code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-size: 0.85em; }}
.nav {{ margin: 20px 0; }}
.nav a {{ color: #3498db; text-decoration: none; margin-right: 20px; }}
.nav a:hover {{ text-decoration: underline; }}
.filter-section {{ margin: 20px 0; }}
.filter-section input {{ padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 1em; width: 300px; }}
</style>
</head>
<body>
<div class="container">
  <div class="nav">
    <a href="index.html{cb}">&larr; Back to overview</a>
    <a href="all-suites.table.html{cb}">Full results table</a>
  </div>
  <h1>Java Ranger — Failure Analysis</h1>
  <p>Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

  <div class="summary-cards">
    <div class="card total">
      <h3>Total Benchmarks</h3>
      <div class="number total">{total_benchmarks}</div>
    </div>
    <div class="card incorrect">
      <h3>Incorrect</h3>
      <div class="number incorrect">{incorrect}</div>
    </div>
    <div class="card unknown">
      <h3>Unknown</h3>
      <div class="number unknown">{unknown}</div>
    </div>
    <div class="card rate">
      <h3>Correct Rate</h3>
      <div class="number rate">{((total_benchmarks - total_failures) / max(total_benchmarks, 1) * 100):.1f}%</div>
    </div>
  </div>

  <h2>Failure Reasons Breakdown</h2>
  <table>
    <tr><th>Reason</th><th>Count</th></tr>
    {reasons_rows}
  </table>

  <h2>Per-Suite Breakdown</h2>
  <table>
    <tr><th>Suite</th><th>Total</th><th>Incorrect</th><th>Unknown</th><th>Correct</th></tr>
    {suite_rows}
  </table>

  <h2>All Failed Benchmarks</h2>
  <div class="filter-section">
    <input type="text" id="filterInput" placeholder="Filter by name, suite, or reason..." onkeyup="filterTable()">
  </div>
  <table id="failures-table">
    <tr>
      <th>Suite</th>
      <th>Benchmark</th>
      <th>Result</th>
      <th>Expected</th>
      <th>Failure Reason</th>
      <th>Time (s)</th>
      <th>Details</th>
    </tr>
    {table_rows}
  </table>
</div>
<script>
function filterTable() {{
  var input = document.getElementById('filterInput');
  var filter = input.value.toUpperCase();
  var table = document.getElementById('failures-table');
  var rows = table.getElementsByTagName('tr');
  for (var i = 1; i < rows.length; i++) {{
    var text = rows[i].textContent || rows[i].innerText;
    rows[i].style.display = text.toUpperCase().indexOf(filter) > -1 ? '' : 'none';
  }}
}}
</script>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(
        description="Generate failure analysis HTML",
        epilog="Pass explicit .xml.bz2 file paths (one per suite).")
    parser.add_argument("--build-id", default=None, help="Build ID for cache busting (e.g. commit SHA)")
    parser.add_argument("output", help="Output HTML file path")
    parser.add_argument("xml_files", nargs="+", help="Per-suite .xml.bz2 result files")
    args = parser.parse_args()

    xml_files = [f for f in args.xml_files if os.path.isfile(f)]
    if not xml_files:
        print("No valid XML files found")
        return 1

    print(f"Analyzing {len(xml_files)} result files")

    failures = []
    by_suite = {}
    total_benchmarks = 0

    for fpath in sorted(xml_files):
        suite = os.path.basename(fpath).split(".")[0]
        if suite not in by_suite:
            by_suite[suite] = {"total": 0, "correct": 0, "incorrect": 0, "unknown": 0}

        try:
            with bz2.open(fpath) as f:
                tree = ET.parse(f)
        except Exception as e:
            print(f"  Error parsing {fpath}: {e}")
            continue

        root = tree.getroot()
        for run in root.findall("run"):
            name = run.get("name", "?").rsplit("/", 1)[-1].replace(".yml", "").replace(".java", "")
            expected = run.get("expectedVerdict", "?")

            cols = {c.get("title"): c.get("value") for c in run.findall("column")}
            status = cols.get("status", "unknown")
            cputime = cols.get("cputime", "0")
            error = cols.get("error", "")

            by_suite[suite]["total"] += 1
            total_benchmarks += 1

            if status == "correct":
                by_suite[suite]["correct"] += 1
            elif status == "incorrect":
                by_suite[suite]["incorrect"] += 1
                reason = classify_error(error)
                failures.append({
                    "suite": suite, "name": name, "status": "incorrect",
                    "expected": expected, "reason": reason, "error": error,
                    "time": cputime.rstrip("s"),
                })
            else:
                by_suite[suite]["unknown"] += 1
                reason = classify_error(error)
                failures.append({
                    "suite": suite, "name": name, "status": "unknown",
                    "expected": expected, "reason": reason, "error": error,
                    "time": cputime.rstrip("s"),
                })

    print(f"  {total_benchmarks} total, {len(failures)} failures ({sum(1 for f in failures if f['status']=='incorrect')} incorrect, {sum(1 for f in failures if f['status']=='unknown')} unknown)")

    html = generate_html(failures, by_suite, {s: by_suite[s] for s in sorted(by_suite)}, total_benchmarks, args.build_id)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        f.write(html)

    print(f"Failure analysis written to {args.output}")


if __name__ == "__main__":
    main()
