#!/usr/bin/env python3
"""
Merge multiple benchexec XML result files (one per suite) into a single
combined XML file, then run table-generator — producing one table with
all benchmarks as rows instead of separate column groups per suite.

Usage:
  python3 merge-benchmark-xmls.py <output-dir> <suite-name> <xml-file> [<xml-file>...]

The combined XML is written to <output-dir>/<suite-name>.results.xml.bz2,
and table-generator produces HTML/CSV in the same directory.
"""

import bz2
import os
import subprocess
import sys
import xml.etree.ElementTree as ET


def merge_xmls(xml_files, output_path):
    first = None
    all_runs = []
    seen_names = set()
    columns = None
    systeminfo = None
    benchmarkname = "all-suites"

    for fpath in xml_files:
        try:
            with bz2.open(fpath) as f:
                tree = ET.parse(f)
        except Exception:
            tree = ET.parse(fpath)

        root = tree.getroot()

        if columns is None:
            columns = root.find("columns")
            if columns is not None:
                columns = ET.tostring(columns, encoding="unicode")

        if systeminfo is None:
            si = root.find("systeminfo")
            if si is not None:
                systeminfo = ET.tostring(si, encoding="unicode")

        for run in root.findall("run"):
            name = run.get("name", "")
            if not name or name not in seen_names:
                seen_names.add(name)
                all_runs.append(ET.tostring(run, encoding="unicode"))

    if not all_runs:
        print("No <run> elements found in any XML files", file=sys.stderr)
        return False

    # Build combined XML
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE result',
        '  PUBLIC "+//IDN sosy-lab.org//DTD BenchExec result 3.12//EN"',
        '  "https://www.sosy-lab.org/benchexec/result-3.12.dtd">',
        f'<result benchmarkname="{benchmarkname}" tool="JPF" suite="all-suites">',
    ]
    if columns:
        parts.append(columns)
    if systeminfo:
        parts.append(systeminfo)
    parts.extend(all_runs)
    parts.append("</result>")

    combined = "\n".join(parts)

    with bz2.open(output_path, "wt", encoding="utf-8") as f:
        f.write(combined)

    return True


def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <output-dir> <suite-name> <xml-file> [<xml-file>...]", file=sys.stderr)
        sys.exit(1)

    output_dir = sys.argv[1]
    suite_name = sys.argv[2]
    xml_files = sys.argv[3:]

    os.makedirs(output_dir, exist_ok=True)

    combined_path = os.path.join(output_dir, f"{suite_name}.results.xml.bz2")

    print(f"Merging {len(xml_files)} XML files into {combined_path}")

    if not merge_xmls(xml_files, combined_path):
        sys.exit(1)

    # Run table-generator on the combined file
    try:
        result = subprocess.run(
            ["table-generator", combined_path, "-o", output_dir, "-n", suite_name],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            if "INFO" in line or "WARNING" in line:
                print(line)

        # Normalize output filenames: table-generator omits ".table." for single-input files.
        # Rename html/csv to always include ".table." for consistent references.
        for ext in ("html", "csv"):
            src = os.path.join(output_dir, f"{suite_name}.{ext}")
            dst = os.path.join(output_dir, f"{suite_name}.table.{ext}")
            if os.path.exists(src) and not os.path.exists(dst):
                os.rename(src, dst)
                print(f"Renamed {src} -> {dst}")

        # Inject cache-control meta tags into table-generator HTML
        html_path = os.path.join(output_dir, f"{suite_name}.table.html")
        if os.path.exists(html_path):
            with open(html_path) as f:
                html = f.read()
            cache_tags = (
                '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">\n'
                '<meta http-equiv="Pragma" content="no-cache">\n'
                '<meta http-equiv="Expires" content="0">\n'
            )
            html = html.replace("<head>", f"<head>\n{cache_tags}", 1)
            with open(html_path, "w") as f:
                f.write(html)
            print(f"Injected cache-control into {html_path}")

        print(f"table-generator output: {os.listdir(output_dir)}")
    except subprocess.CalledProcessError as e:
        for line in (e.stdout or "").splitlines():
            print(line)
        print(f"table-generator failed: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("table-generator not found; combined XML written but no HTML generated", file=sys.stderr)


if __name__ == "__main__":
    main()
