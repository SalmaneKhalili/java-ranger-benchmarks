#!/usr/bin/env python3
"""
Generate benchexec-compatible results XML from benchmark run data.
Produces DTD-valid output that table-generator can consume.

Usage:
  python3 generate-benchexec-xml.py \\
    --suite jpf-regression \\
    --results results.jsonl \\
    --starttime 2026-05-25T18:00:00 \\
    --endtime 2026-05-25T19:00:00 \\
    --options "+symbolic.dp=z3bitvector ..." \\
    --output results.xml

Results JSONL format (one JSON object per line):
  {"name": "benchmark", "expected": "true", "actual": "true",
   "cputime": 12.34, "walltime": 15.0, "memory": 12345678,
   "exit_code": 0, "files": ["dir1", "dir2"],
   "property_file": "/path/to/prop.prp", "logfile": "/path/to/jpf.log",
   "error": ""}
"""

import argparse
import json
import os
import platform
import sys
import datetime
from xml.sax.saxutils import escape as xml_escape


STATUS_MAP = {
    "CORRECT": "correct",
    "INCORRECT": "incorrect",
    "UNKNOWN": "unknown",
    "TIMEOUT": "unknown",
    "COMPILE_ERR": "unknown",
    "SKIP": "unknown",
}


def get_system_info():
    info = {}
    info["hostname"] = platform.node()

    os_name = platform.system()
    os_release = platform.release()
    info["os"] = f"{os_name}-{os_release}"

    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()
        model = ""
        cores = 0
        for line in cpuinfo.splitlines():
            if line.startswith("model name"): model = line.split(":")[1].strip()
            if line.startswith("cpu cores"): cores = int(line.split(":")[1].strip())
            if line.startswith("processor"): cores = max(cores, int(line.split(":")[1].strip()) + 1)
        info["cpu_model"] = model
        info["cpu_cores"] = str(cores)
    except OSError:
        info["cpu_model"] = "unknown"
        info["cpu_cores"] = "1"

    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "cpu MHz" in line:
                    freq_mhz = float(line.split(":")[1].strip())
                    info["cpu_frequency"] = str(int(freq_mhz * 1_000_000))
                    break
    except (OSError, ValueError):
        info["cpu_frequency"] = "0"

    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    info["ram_bytes"] = str(kb * 1024)
                    break
    except (OSError, ValueError):
        info["ram_bytes"] = "0"

    return info


def format_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def format_date(dt):
    return dt.strftime("%a, %Y-%m-%d %H:%M:%S %z")


def escape_xml(s):
    return xml_escape(str(s), {'"': '&quot;'})


def write_xml(args, results, sysinfo, start_dt, end_dt, f):
    f.write('<?xml version="1.0" encoding="utf-8"?>\n')
    f.write('<!DOCTYPE result\n')
    f.write('  PUBLIC \'+//IDN sosy-lab.org//DTD BenchExec result 3.12//EN\'\n')
    f.write('  \'https://www.sosy-lab.org/benchexec/result-3.12.dtd\'>\n')

    date_str = end_dt.strftime("%Y-%m-%d %H:%M:%S %z")
    start_iso = format_iso(start_dt)
    end_iso = format_iso(end_dt)

    options = escape_xml(args.options) if args.options else ""
    tool_version = escape_xml(args.tool_version) if args.tool_version else "N/A"
    suite = escape_xml(args.suite)

    f.write(f'<result benchmarkname="{suite}"')
    f.write(f' date="{escape_xml(date_str)}"')
    f.write(f' starttime="{start_iso}"')
    f.write(f' endtime="{end_iso}"')
    f.write(f' tool="JPF"')
    f.write(f' version="{tool_version}"')
    f.write(f' toolmodule="scripts/run-ci-benchmarks.sh"')
    f.write(f' generator="java-ranger-benchmarks CI"')
    if options:
        f.write(f' options="{options}"')
    f.write(f'>\n')

    # Columns
    f.write('  <columns>\n')
    f.write('    <column title="status"/>\n')
    f.write('    <column title="cputime"/>\n')
    f.write('    <column title="walltime"/>\n')
    f.write('  </columns>\n')

    # System info
    f.write(f'  <systeminfo hostname="{escape_xml(sysinfo.get("hostname", ""))}">\n')
    f.write(f'    <os name="{escape_xml(sysinfo.get("os", ""))}"/>\n')
    f.write(f'    <cpu model="{escape_xml(sysinfo.get("cpu_model", ""))}" cores="{escape_xml(sysinfo.get("cpu_cores", "1"))}" frequency="{escape_xml(sysinfo.get("cpu_frequency", "0"))}"/>\n')
    f.write(f'    <ram size="{escape_xml(sysinfo.get("ram_bytes", "0"))}"/>\n')
    f.write(f'    <environment>\n')
    for key, val in sorted(os.environ.items()):
        if key in ("PATH", "HOME", "USER", "SHELL", "JAVA_HOME", "LD_LIBRARY_PATH", "CLASSPATH_ADDED"):
            f.write(f'      <var name="{escape_xml(key)}">{escape_xml(val)}</var>\n')
    f.write(f'    </environment>\n')
    f.write(f'  </systeminfo>\n')

    # Per-benchmark runs
    for r in results:
        name = escape_xml(r.get("name", ""))
        expected = escape_xml(r.get("expected", ""))
        status = STATUS_MAP.get(r.get("verdict", ""), "unknown")
        cputime = r.get("cputime", 0) or 0
        walltime = r.get("walltime", 0) or 0
        memory = r.get("memory", 0) or 0
        exit_code = r.get("exit_code", 0) or 0
        files = r.get("files", [])
        prop_file = escape_xml(r.get("property_file", ""))
        logfile = escape_xml(r.get("logfile", ""))
        err_msg = r.get("error", "")

        files_attr = escape_xml(json.dumps(files)) if files else ""

        f.write(f'  <run name="{name}"')
        if files_attr:
            f.write(f' files="{files_attr}"')
        if prop_file:
            f.write(f' propertyFile="{prop_file}"')
        if expected:
            f.write(f' expectedVerdict="{expected}"')
        if logfile:
            f.write(f' logfile="{logfile}"')
        f.write('>\n')

        f.write(f'    <column title="cputime" value="{cputime:.6f}s"/>\n')
        f.write(f'    <column title="memory" value="{int(memory)}B"/>\n')
        f.write(f'    <column title="status" value="{status}"/>\n')
        f.write(f'    <column title="walltime" value="{walltime:.6f}s"/>\n')
        f.write(f'    <column title="returnvalue" value="{exit_code}" hidden="true"/>\n')

        category = status
        if err_msg:
            category = "error"
        f.write(f'    <column title="category" value="{category}" hidden="true"/>\n')

        if err_msg:
            f.write(f'    <column title="error" value="{escape_xml(err_msg)}" hidden="true"/>\n')

        f.write(f'  </run>\n')

    f.write('</result>\n')


def main():
    parser = argparse.ArgumentParser(description="Generate benchexec-compatible results XML")
    parser.add_argument("--suite", required=True, help="Benchmark suite name")
    parser.add_argument("--results", required=True, help="Path to JSONL results file")
    parser.add_argument("--starttime", required=True, help="ISO start timestamp")
    parser.add_argument("--endtime", required=True, help="ISO end timestamp")
    parser.add_argument("--options", default="", help="JPF options string")
    parser.add_argument("--tool-version", default="", help="Tool version string")
    parser.add_argument("--output", required=True, help="Output XML file path")
    args = parser.parse_args()

    # Parse timestamps
    try:
        start_dt = datetime.datetime.fromisoformat(args.starttime)
        end_dt = datetime.datetime.fromisoformat(args.endtime)
    except ValueError:
        start_dt = datetime.datetime.now()
        end_dt = start_dt

    # Read results
    results = []
    seen = set()
    with open(args.results) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                # Deduplicate by name
                name = r.get("name", "")
                if name and name not in seen:
                    seen.add(name)
                    results.append(r)
                elif name:
                    pass  # skip duplicate
            except json.JSONDecodeError as e:
                print(f"Warning: skipping malformed JSON line: {e}", file=sys.stderr)
                continue

    # System info
    sysinfo = get_system_info()

    # Write XML
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        write_xml(args, results, sysinfo, start_dt, end_dt, f)

    # Quick stats
    correct = sum(1 for r in results if r.get("verdict") == "CORRECT")
    incorrect = sum(1 for r in results if r.get("verdict") == "INCORRECT")
    unknown = sum(1 for r in results if r.get("verdict") not in ("CORRECT", "INCORRECT"))
    print(f"XML written to {args.output} ({len(results)} benchmarks: {correct} correct, {incorrect} incorrect, {unknown} unknown)", file=sys.stderr)


if __name__ == "__main__":
    main()
