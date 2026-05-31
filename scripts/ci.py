#!/usr/bin/env python3
"""Java Ranger Benchmark CI Orchestrator.

Usage:
  ci.py list-suites                  Print suite list as JSON array
  ci.py run <suite> <jr-dir> <sv-dir> <out-dir>
                                       Compile + JPF + XML for one suite
  ci.py merge <out-dir> <xml-files..> Merge per-suite XMLs + table-generator
  ci.py baseline <xml-dir>            Generate baseline.json from results
  ci.py check <xml-dir> --baseline F  Check results vs baseline for regressions
  ci.py analyze <xml-files..>         Generate failure-analysis.html
"""

import argparse, bz2, glob, json, os, platform, re, shutil, subprocess, sys
import tempfile, time
from collections import Counter
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape
import xml.etree.ElementTree as ET

try:
    import yaml
except ImportError:
    yaml = None


SUITES = [
    "algorithms", "argv-tasks", "autostub",
    "float-nonlinear-calculation", "float_unboundedloop",
    "java-ranger-regression", "jayhorn-recursive",
    "jbmc-regression", "jdart-regression",
    "jpf-regression", "MinePump", "objects",
    "rtems-lock-model", "securibench",
    "juliet-java",
]

FP_SUITES = frozenset([
    "float-nonlinear-calculation", "float_unboundedloop",
    "autostub", "argv-tasks", "jpf-regression",
    "juliet-java", "jdart-regression", "jbmc-regression",
])

JPF_CONFIG = """\
target=Main
classpath={classpath}
symbolic.dp=z3bitvector
symbolic.bvlength=64
search.depth_limit=13
symbolic.strings=true
symbolic.string_dp=z3str3
symbolic.string_dp_timeout_ms=3000
symbolic.lazy=on
symbolic.arrays=true
listener=.symbc.SymbolicListener
{fp_opts}"""

STATUS_MAP = {
    "CORRECT": "correct", "INCORRECT": "incorrect",
    "UNKNOWN": "unknown", "TIMEOUT": "unknown", "COMPILE_ERR": "unknown",
}

_MATH_FUNCS = re.compile(r'\b(sin|cos|tan|sqrt|log|asin|acos|atan)\b', re.I)


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------

def parse_yaml(path):
    if yaml is not None:
        with open(path) as f:
            return yaml.safe_load(f)
    return _parse_yaml_fallback(path)


def _parse_yaml_fallback(path):
    result = {"input_files": [], "properties": []}
    with open(path) as f:
        lines = f.readlines()
    in_files, in_props = False, False
    for line in lines:
        s = line.strip()
        if s == "input_files:":
            in_files, in_props = True, False
        elif s == "properties:":
            in_files, in_props = False, True
        elif s.startswith("- ") and in_files:
            result["input_files"].append(s[2:].strip())
        elif s.startswith("- property_file:") and in_props:
            result["properties"].append({"property_file": s.split(":", 1)[1].strip()})
        elif s.startswith("expected_verdict:") and in_props and result["properties"]:
            result["properties"][-1]["expected_verdict"] = s.split(":", 1)[1].strip()
        elif s == "options:":
            in_files, in_props = False, False
    return result


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------

def get_system_info():
    info = {"hostname": platform.node(), "os": f"{platform.system()}-{platform.release()}"}
    try:
        with open("/proc/cpuinfo") as f:
            text = f.read()
        model, cores = "", 0
        for line in text.splitlines():
            if line.startswith("model name"):
                model = line.split(":")[1].strip()
            if line.startswith("cpu cores"):
                cores = int(line.split(":")[1].strip())
            if line.startswith("processor"):
                cores = max(cores, int(line.split(":")[1].strip()) + 1)
        info["cpu_model"] = model or "unknown"
        info["cpu_cores"] = str(cores or 1)
    except OSError:
        info["cpu_model"] = info["cpu_cores"] = "unknown"
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "cpu MHz" in line:
                    info["cpu_frequency"] = str(int(float(line.split(":")[1].strip()) * 1_000_000))
                    break
    except (OSError, ValueError):
        info["cpu_frequency"] = "0"
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    info["ram_bytes"] = str(int(line.split()[1]) * 1024)
                    break
    except (OSError, ValueError):
        info["ram_bytes"] = "0"
    return info


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def escape_xml(s):
    return xml_escape(str(s), {'"': "&quot;"})


def fmt_iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Shared XML parser — single source for reading result XMLs
# ---------------------------------------------------------------------------

def parse_results_xml(fpath):
    """Yield dicts: {suite, name, status, expected, error, cputime, walltime}."""
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
    suite = root.get("benchmarkname", "unknown")

    for run in root.findall("run"):
        name = (run.get("name") or "").rsplit("/", 1)[-1]
        name = name.replace(".yml", "").replace(".java", "")
        expected = run.get("expectedVerdict", "?")
        error = ""

        cols = {}
        for col in run.findall("column"):
            cols[col.get("title")] = col.get("value")

        status = cols.get("status", "unknown")
        cputime = cols.get("cputime", "0").rstrip("s")
        walltime = cols.get("walltime", "0").rstrip("s")

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
            "cputime": cputime,
            "walltime": walltime,
        }


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def classify_failure(error_text):
    """Return (category, display_text) for a JPF error message."""
    if not error_text:
        return "unknown", "unknown"
    m = error_text.lower()

    if "multianewarray" in m and "symbolic array length" in m:
        return "MULTIANEWARRAY crash", error_text[:300]
    if "selectexpression cannot be cast" in m or "selectexpression" in m and "classcastexception" in m:
        return "SelectExpression crash", error_text[:300]
    if "virtualinvocation.getinvokedmethod" in m:
        return "VirtualInvocation NPE", error_text[:300]
    if "pcparser" in m or "fpadd" in m or "fpsub" in m or "fpmul" in m or "fpdiv" in m:
        return "unsupported FP expression", error_text[:300]
    if "compile" in m or "cannot find symbol" in m:
        return "compile error", error_text[:300]
    if "timeout" in m or "timed out" in m or "exit_code 124" in m or "exit code 124" in m:
        return "timeout", "timeout after 60s"
    if "isnan" in m or ("nan" in m and ("exception" in m or "error" in m or "severe" in m)):
        return "unsupported NaN handling", error_text[:300]
    if _MATH_FUNCS.search(error_text) and ("exception" in m or "error" in m or "severe" in m):
        return "unsupported math function", error_text[:300]
    if "string operation" in m or "z3str3" in m:
        return "string operation unsupported", error_text[:300]
    if "exception" in m or "error" in m or "severe" in m:
        return "runtime error", error_text[:300]
    return "unknown", error_text[:300]


def extract_error(jpf_output):
    """Extract relevant error lines from raw JPF output."""
    lines = jpf_output.splitlines()
    errors = []
    for i, line in enumerate(lines):
        s = line.rstrip()
        if not s or "JavaPathFinder" in s:
            continue
        if "[SEVERE]" in s or "Exception" in s or "Error" in s or "error:" in s:
            errors.append(s[:300])
        elif errors and ("\tat " in s or s.startswith("at ")) and len(errors) < 4:
            errors.append(s[:200])
    if not errors:
        meaningful = [l.rstrip() for l in lines if l.strip() and "JavaPathFinder" not in l]
        errors = meaningful[-5:]
    return " | ".join(errors[:5])


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

class BenchmarkResult:
    def __init__(self):
        self.name = ""
        self.expected = ""
        self.actual = "UNKNOWN"
        self.verdict = "UNKNOWN"
        self.error = ""
        self.cputime = 0.0
        self.walltime = 0.0
        self.exit_code = 0
        self.files = []
        self.property_file = ""
        self.logfile = ""
        self.compile_ok = True


def run_benchmark(yml_path, jr_dir, sv_bench_dir, output_dir, suite, log_dir):
    result = BenchmarkResult()
    result.name = os.path.splitext(os.path.basename(yml_path))[0]
    yml_dir = os.path.dirname(yml_path)

    config = parse_yaml(yml_path)
    if not config or "properties" not in config or not config["properties"]:
        result.error = "malformed YML"
        return result

    first_prop = config["properties"][0]
    raw = first_prop.get("expected_verdict", "true")
    result.expected = str(raw).lower()
    prop_file_rel = first_prop.get("property_file", "../properties/valid-assert.prp")
    result.property_file = os.path.normpath(os.path.join(yml_dir, prop_file_rel))

    src_dirs = []
    files_csv = ""
    for dir_rel in config.get("input_files", []):
        d = os.path.normpath(os.path.join(yml_dir, dir_rel))
        src_dirs.append(d)
        files_csv += "," + d if files_csv else d
    result.files = [files_csv]

    java_files = []
    for d in src_dirs:
        if os.path.isdir(d):
            for root, dirs, fnames in os.walk(d):
                for fn in fnames:
                    if fn.endswith(".java") and fn != "Verifier.java":
                        java_files.append(os.path.join(root, fn))

    if not java_files:
        result.verdict = "UNKNOWN"
        result.error = "no source files"
        return result

    tmp_dir = tempfile.mkdtemp(prefix="jpf-ci-")
    try:
        classes_dir = os.path.join(tmp_dir, "target", "classes")
        os.makedirs(classes_dir)

        jpf_core_jar = os.path.join(jr_dir, "jpf-core", "build", "RunJPF.jar")
        jpf_symbc_classes = os.path.join(jr_dir, "jpf-symbc", "build", "classes")
        jpf_symbc_lib = os.path.join(jr_dir, "jpf-symbc", "lib")

        classpath = f"{jpf_symbc_classes}:{classes_dir}"

        compile_proc = subprocess.run(
            ["javac", "-g", "-cp", classpath, "-d", classes_dir] + java_files,
            capture_output=True, text=True, timeout=120,
        )
        if compile_proc.returncode != 0:
            result.compile_ok = False
            result.verdict = "UNKNOWN"
            result.error = compile_proc.stderr.strip() or compile_proc.stdout.strip() or "compile error"
            return result

        fp_opts = "symbolic.fp=true" if suite in FP_SUITES else ""
        jpf_config_path = os.path.join(tmp_dir, "config.jpf")
        with open(jpf_config_path, "w") as f:
            f.write(JPF_CONFIG.format(classpath=classpath, fp_opts=fp_opts))

        log_path = os.path.join(tmp_dir, "jpf.log")
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = f"{jpf_symbc_lib}:{env.get('LD_LIBRARY_PATH', '')}"

        start_ns = time.time_ns()
        try:
            jpf_proc = subprocess.run(
                ["java", "-Xmx1024m", "-ea",
                 f"-Djava.library.path={jpf_symbc_lib}",
                 "-jar", jpf_core_jar, jpf_config_path],
                cwd=jr_dir, capture_output=True, text=True, timeout=60, env=env,
            )
            exit_code = jpf_proc.returncode
            jpf_output = jpf_proc.stdout + "\n" + jpf_proc.stderr
        except subprocess.TimeoutExpired:
            result.verdict = "UNKNOWN"
            result.error = "timeout after 60s"
            result.cputime = result.walltime = 60.0
            result.exit_code = 124
            return result

        end_ns = time.time_ns()
        elapsed_s = (end_ns - start_ns) / 1_000_000_000
        result.cputime = result.walltime = elapsed_s
        result.exit_code = exit_code

        with open(log_path, "w") as f:
            f.write(jpf_output)

        os.makedirs(log_dir, exist_ok=True)
        persistent_log = os.path.join(log_dir, f"{result.name}.log")
        shutil.copy2(log_path, persistent_log)
        result.logfile = persistent_log

        if "no errors detected" in jpf_output:
            result.actual = "true"
        elif "java.lang.AssertionError" in jpf_output:
            result.actual = "false"
        else:
            result.actual = "UNKNOWN"

        if result.actual == result.expected:
            result.verdict = "CORRECT"
        elif result.actual == "UNKNOWN":
            result.verdict = "UNKNOWN"
        else:
            result.verdict = "INCORRECT"

        if result.verdict != "CORRECT":
            result.error = extract_error(jpf_output)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


def write_benchexec_xml(results, suite, start_dt, end_dt, sysinfo, output_path, jr_version_str, jpf_options):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    date_str = end_dt.strftime("%Y-%m-%d %H:%M:%S %z")
    start_iso, end_iso = fmt_iso(start_dt), fmt_iso(end_dt)

    with open(output_path, "w") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        f.write('<!DOCTYPE result\n')
        f.write('  PUBLIC \'+//IDN sosy-lab.org//DTD BenchExec result 3.12//EN\'\n')
        f.write('  \'https://www.sosy-lab.org/benchexec/result-3.12.dtd\'>\n')
        f.write(f'<result benchmarkname="{escape_xml(suite)}" date="{escape_xml(date_str)}"')
        f.write(f' starttime="{start_iso}" endtime="{end_iso}" tool="JPF"')
        f.write(f' version="{escape_xml(jr_version_str)}" toolmodule="scripts/ci.py"')
        f.write(f' generator="java-ranger-benchmarks CI"')
        if jpf_options:
            f.write(f' options="{escape_xml(jpf_options)}"')
        f.write('>\n')
        f.write('  <columns>\n')
        f.write('    <column title="status"/>\n')
        f.write('    <column title="cputime"/>\n')
        f.write('    <column title="walltime"/>\n')
        f.write('  </columns>\n')
        f.write(f'  <systeminfo hostname="{escape_xml(sysinfo.get("hostname", ""))}">\n')
        f.write(f'    <os name="{escape_xml(sysinfo.get("os", ""))}"/>\n')
        f.write(f'    <cpu model="{escape_xml(sysinfo.get("cpu_model", ""))}"')
        f.write(f' cores="{escape_xml(sysinfo.get("cpu_cores", "1"))}"')
        f.write(f' frequency="{escape_xml(sysinfo.get("cpu_frequency", "0"))}"/>\n')
        f.write(f'    <ram size="{escape_xml(sysinfo.get("ram_bytes", "0"))}"/>\n')
        f.write(f'    <environment>\n')
        for key in ("PATH", "HOME", "USER", "JAVA_HOME", "LD_LIBRARY_PATH"):
            val = os.environ.get(key, "")
            if val:
                f.write(f'      <var name="{escape_xml(key)}">{escape_xml(val)}</var>\n')
        f.write(f'    </environment>\n')
        f.write(f'  </systeminfo>\n')

        for r in results:
            name = escape_xml(r.name)
            status = STATUS_MAP.get(r.verdict, "unknown")
            cputime, walltime = r.cputime or 0, r.walltime or 0
            files_attr = escape_xml(json.dumps(r.files)) if r.files else ""
            prop_file = escape_xml(r.property_file)

            f.write(f'  <run name="{name}"')
            if files_attr:
                f.write(f' files="{files_attr}"')
            if prop_file:
                f.write(f' propertyFile="{prop_file}"')
            if r.expected:
                f.write(f' expectedVerdict="{escape_xml(r.expected)}"')
            if r.logfile:
                f.write(f' logfile="{escape_xml(r.logfile)}"')
            f.write('>\n')
            f.write(f'    <column title="cputime" value="{cputime:.6f}s"/>\n')
            f.write(f'    <column title="walltime" value="{walltime:.6f}s"/>\n')
            f.write(f'    <column title="status" value="{status}"/>\n')
            f.write(f'    <column title="returnvalue" value="{r.exit_code or 0}" hidden="true"/>\n')
            category = status if not r.error else "error"
            f.write(f'    <column title="category" value="{category}" hidden="true"/>\n')
            if r.error:
                f.write(f'    <column title="error" value="{escape_xml(r.error)}" hidden="true"/>\n')
            f.write(f'  </run>\n')

        f.write('</result>\n')


def run_suite(suite, jr_dir, sv_bench_dir, output_dir, jr_version_str):
    suite_dir = os.path.join(sv_bench_dir, "java", suite)
    if not os.path.isdir(suite_dir):
        print(f"Error: Suite directory not found: {suite_dir}", file=sys.stderr)
        sys.exit(1)

    yml_files = sorted(glob.glob(os.path.join(suite_dir, "*.yml")))
    if not yml_files:
        print(f"No YML files found in {suite_dir}", file=sys.stderr)
        return

    os.makedirs(output_dir, exist_ok=True)
    log_dir = os.path.join(output_dir, "logs")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    start_dt = datetime.now()

    jpf_options = ("+target=Main +symbolic.dp=z3bitvector +symbolic.bvlength=64 "
                   "+search.depth_limit=13 +symbolic.strings=true "
                   "+symbolic.string_dp=z3str3 +symbolic.string_dp_timeout_ms=3000 "
                   "+symbolic.lazy=on +symbolic.arrays=true +listener=.symbc.SymbolicListener")
    if suite in FP_SUITES:
        jpf_options += " +symbolic.fp=true"

    results = []
    counters = {"total": 0, "correct": 0, "incorrect": 0, "unknown": 0, "compile_err": 0, "timeout": 0}

    print(f"--- {suite} ({len(yml_files)} benchmarks) ---")

    for yml_path in yml_files:
        result = run_benchmark(yml_path, jr_dir, sv_bench_dir, output_dir, suite, log_dir)
        results.append(result)
        counters["total"] += 1

        if result.verdict == "CORRECT":
            counters["correct"] += 1
            status_str = "CORRECT"
        elif result.verdict == "INCORRECT":
            counters["incorrect"] += 1
            status_str = "INCORRECT"
        elif "timeout" in result.error.lower():
            counters["timeout"] += 1
            status_str = "TIMEOUT"
        elif not result.compile_ok:
            counters["compile_err"] += 1
            status_str = "COMPILE_ERR"
        else:
            counters["unknown"] += 1
            status_str = "UNKNOWN"

        print(f"  {status_str:>11}  {result.name} ({result.walltime:.2f}s)")

    end_dt = datetime.now()

    summary_path = os.path.join(output_dir, "summary.txt")
    with open(summary_path, "w") as f:
        for k in ("total", "correct", "incorrect", "unknown", "compile_err", "timeout"):
            f.write(f"{k.capitalize():>10}: {counters[k]}\n")
        f.write(f"\nScore: {counters['correct']} / {counters['total']}\n")
    with open(summary_path) as f:
        print(f.read())

    sysinfo = get_system_info()
    xml_path = os.path.join(output_dir, f"{suite}.{timestamp}.results.xml")
    write_benchexec_xml(results, suite, start_dt, end_dt, sysinfo, xml_path, jr_version_str, jpf_options)

    xml_bz2 = xml_path + ".bz2"
    with open(xml_path, "rb") as fin:
        with bz2.open(xml_bz2, "wb") as fout:
            shutil.copyfileobj(fin, fout)

    print(f"Results: {xml_bz2}")
    os.remove(xml_path)

    try:
        subprocess.run(["table-generator", xml_bz2, "-o", output_dir], capture_output=True, timeout=60)
    except FileNotFoundError:
        print("Warning: table-generator not found, skipping HTML table generation")


# ---------------------------------------------------------------------------
# Subcommand: list-suites
# ---------------------------------------------------------------------------

def cmd_list_suites():
    print(json.dumps({"suite": SUITES}))


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------

def cmd_run(args):
    jr_version_str = "N/A"
    try:
        p = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=args.jr_dir,
                           capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            sha = p.stdout.strip()
            b = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=args.jr_dir,
                               capture_output=True, text=True, timeout=10)
            branch = b.stdout.strip() if b.returncode == 0 else "HEAD"
            jr_version_str = f"{branch}@{sha}"
    except Exception:
        pass

    run_suite(args.suite, args.jr_dir, args.sv_dir, args.output_dir, jr_version_str)


# ---------------------------------------------------------------------------
# Subcommand: merge
# ---------------------------------------------------------------------------

def cmd_merge(args):
    output_dir = args.output_dir
    xml_files = args.xml_files
    if not xml_files:
        print("No XML files to merge", file=sys.stderr)
        return

    os.makedirs(output_dir, exist_ok=True)
    combined_path = os.path.join(output_dir, "all-suites.results.xml.bz2")

    all_runs = []
    seen_names = set()
    columns = None
    systeminfo = None

    for fpath in xml_files:
        try:
            with bz2.open(fpath) as f:
                tree = ET.parse(f)
        except Exception:
            try:
                tree = ET.parse(fpath)
            except Exception as e:
                print(f"Warning: could not parse {fpath}: {e}", file=sys.stderr)
                continue

        root = tree.getroot()
        if columns is None:
            cols_elem = root.find("columns")
            if cols_elem is not None:
                columns = ET.tostring(cols_elem, encoding="unicode")
        if systeminfo is None:
            si = root.find("systeminfo")
            if si is not None:
                systeminfo = ET.tostring(si, encoding="unicode")

        for run in root.findall("run"):
            name = run.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                all_runs.append(ET.tostring(run, encoding="unicode"))

    if not all_runs:
        print("No <run> elements found", file=sys.stderr)
        return

    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE result',
        '  PUBLIC "+//IDN sosy-lab.org//DTD BenchExec result 3.12//EN"',
        '  "https://www.sosy-lab.org/benchexec/result-3.12.dtd">',
        '<result benchmarkname="all-suites" tool="JPF" suite="all-suites">',
    ]
    if columns:
        parts.append(columns)
    if systeminfo:
        parts.append(systeminfo)
    parts.extend(all_runs)
    parts.append("</result>")

    with bz2.open(combined_path, "wt", encoding="utf-8") as f:
        f.write("\n".join(parts))

    print(f"Merged {len(xml_files)} files into {combined_path} ({len(all_runs)} benchmarks)")

    try:
        result = subprocess.run(
            ["table-generator", combined_path, "-o", output_dir, "-n", "all-suites"],
            capture_output=True, text=True, timeout=120,
        )
        for line in result.stdout.splitlines():
            if "INFO" in line or "WARNING" in line:
                print(line)
        for ext in ("html", "csv"):
            src = os.path.join(output_dir, f"all-suites.{ext}")
            dst = os.path.join(output_dir, f"all-suites.table.{ext}")
            if os.path.exists(src) and not os.path.exists(dst):
                os.rename(src, dst)
        html_path = os.path.join(output_dir, "all-suites.table.html")
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
    except subprocess.TimeoutExpired:
        print("table-generator timed out", file=sys.stderr)
    except FileNotFoundError:
        print("table-generator not found; combined XML written but no HTML", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"table-generator failed: {e.stderr}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommand: baseline
# ---------------------------------------------------------------------------

def cmd_baseline(args):
    baseline = {}
    xml_dir = args.xml_dir

    xml_files = []
    for f in os.listdir(xml_dir):
        if f.endswith(".results.xml.bz2") or f.endswith(".results.xml"):
            xml_files.append(os.path.join(xml_dir, f))
    xml_files.sort()

    if not xml_files:
        print("No result XML files found", file=sys.stderr)
        sys.exit(1)

    for fpath in xml_files:
        for rec in parse_results_xml(fpath):
            baseline[f"{rec['suite']}/{rec['name']}"] = rec["status"]

    print(json.dumps(baseline, indent=2))


# ---------------------------------------------------------------------------
# Subcommand: check
# ---------------------------------------------------------------------------

def cmd_check(args):
    xml_dir = args.xml_dir
    baseline_path = args.baseline

    if not os.path.exists(baseline_path):
        print(f"Baseline not found: {baseline_path}", file=sys.stderr)
        sys.exit(1)

    with open(baseline_path) as f:
        baseline = json.load(f)

    current = {}
    xml_files = []
    for fname in os.listdir(xml_dir):
        if fname.endswith(".results.xml.bz2") or fname.endswith(".results.xml"):
            xml_files.append(os.path.join(xml_dir, fname))

    for fpath in xml_files:
        for rec in parse_results_xml(fpath):
            current[f"{rec['suite']}/{rec['name']}"] = rec["status"]

    regressions = improvements = untracked = ok_count = 0
    worst_order = {"correct": 0, "incorrect": 1, "unknown": 2}

    for key, cur_status in sorted(current.items()):
        if key not in baseline:
            print(f"  UNTRACKED  {key}  {cur_status}")
            untracked += 1
            continue
        base_status = baseline[key]
        co, bo = worst_order.get(cur_status, 2), worst_order.get(base_status, 2)
        if cur_status == base_status:
            ok_count += 1
        elif co > bo:
            print(f"  REGRESSION  {key}: {base_status} -> {cur_status}")
            regressions += 1
        else:
            print(f"  IMPROVEMENT {key}: {base_status} -> {cur_status}")
            improvements += 1

    total = len(current)
    print(f"\n{total} total, {ok_count} unchanged, {improvements} improved, {regressions} regressed, {untracked} untracked")

    if regressions > 0:
        sys.exit(regressions)


# ---------------------------------------------------------------------------
# Subcommand: analyze
# ---------------------------------------------------------------------------

def _render_header(build_id, cb):
    return f"""\
  <div class="nav">
    <a href="index.html{cb}">&larr; Back to overview</a>
    <a href="all-suites.table.html{cb}">Full results table</a>
  </div>
  <h1>Java Ranger &mdash; Failure Analysis</h1>
  <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>"""


def _render_summary_cards(total, incorrect, unknown, correct_rate):
    return f"""\
  <div class="summary-cards">
    <div class="card total">
      <h3>Total Benchmarks</h3>
      <div class="number total">{total}</div>
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
      <div class="number rate">{correct_rate:.1f}%</div>
    </div>
  </div>"""


def _render_reasons_table(reasons_counter):
    rows = "".join(
        f"<tr><td>{escape_xml(label)}</td><td>{count}</td></tr>\n"
        for label, count in reasons_counter.most_common()
    )
    return f"""\
  <h2>Failure Reasons Breakdown</h2>
  <table>
    <tr><th>Reason</th><th>Count</th></tr>
    {rows}
  </table>"""


def _render_suite_table(by_suite):
    rows = "".join(
        f"<tr><td>{escape_xml(s)}</td><td>{c['total']}</td>"
        f"<td>{c['incorrect']}</td><td>{c['unknown']}</td><td>{c['correct']}</td></tr>\n"
        for s, c in sorted(by_suite.items())
    )
    return f"""\
  <h2>Per-Suite Breakdown</h2>
  <table>
    <tr><th>Suite</th><th>Total</th><th>Incorrect</th><th>Unknown</th><th>Correct</th></tr>
    {rows}
  </table>"""


def _render_failures_table(failures):
    rows = "".join(
        f"<tr>"
        f"<td>{escape_xml(f['suite'])}</td>"
        f"<td>{escape_xml(f['name'])}</td>"
        f"<td class=\"{'incorrect' if f['status'] == 'incorrect' else 'unknown'}\">{f['status']}</td>"
        f"<td>{escape_xml(f['expected'])}</td>"
        f"<td>{escape_xml(f['reason'])}</td>"
        f"<td>{f['time']}</td>"
        f"<td><code>{escape_xml(f['error'])}</code></td>"
        f"</tr>\n"
        for f in failures
    )
    return f"""\
  <h2>All Failed Benchmarks</h2>
  <div class="filter-section">
    <input type="text" id="filterInput" placeholder="Filter by name, suite, or reason..." onkeyup="filterTable()">
  </div>
  <table id="failures-table">
    <tr>
      <th>Suite</th><th>Benchmark</th><th>Result</th>
      <th>Expected</th><th>Failure Reason</th><th>Time (s)</th><th>Details</th>
    </tr>
    {rows}
  </table>"""


def _assemble_html(sections, build_id):
    cb = f"?v={build_id or datetime.now().strftime('%Y%m%d%H%M%S')}"
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta name="build-id" content="{escape_xml(build_id or '')}">
<title>Java Ranger &mdash; Failure Analysis</title>
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
.inherit-border {{}} .card .number.incorrect {{ color: #e74c3c; }}
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
{chr(10).join(v for v in sections.values() if v)}
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


def cmd_analyze(args):
    xml_files = [f for f in args.xml_files if os.path.isfile(f)]
    if not xml_files:
        print("No valid XML files found", file=sys.stderr)
        sys.exit(1)

    failures = []
    by_suite = {}
    total_benchmarks = 0
    seen = set()

    for fpath in sorted(xml_files):
        for rec in parse_results_xml(fpath):
            key = (rec["suite"], rec["name"])
            if key in seen:
                continue
            seen.add(key)
            total_benchmarks += 1

            if rec["suite"] not in by_suite:
                by_suite[rec["suite"]] = {"total": 0, "correct": 0, "incorrect": 0, "unknown": 0}
            by_suite[rec["suite"]]["total"] += 1

            status = rec["status"]
            if status == "correct":
                by_suite[rec["suite"]]["correct"] += 1
            elif status == "incorrect":
                by_suite[rec["suite"]]["incorrect"] += 1
                reason, display_error = classify_failure(rec["error"])
                failures.append({
                    "suite": rec["suite"], "name": rec["name"],
                    "status": "incorrect", "expected": rec["expected"],
                    "reason": reason, "error": display_error,
                    "time": rec["cputime"] or rec["walltime"],
                })
            else:
                by_suite[rec["suite"]]["unknown"] += 1
                reason, display_error = classify_failure(rec["error"])
                failures.append({
                    "suite": rec["suite"], "name": rec["name"],
                    "status": "unknown", "expected": rec["expected"],
                    "reason": reason, "error": display_error,
                    "time": rec["cputime"] or rec["walltime"],
                })

    total_failures = len(failures)
    incorrect = sum(1 for f in failures if f["status"] == "incorrect")
    unknown = sum(1 for f in failures if f["status"] == "unknown")
    correct_rate = (total_benchmarks - total_failures) / max(total_benchmarks, 1) * 100

    reasons = Counter(f["reason"] for f in failures)

    sections = {
        "header": _render_header(args.build_id, args.build_id),
        "cards": _render_summary_cards(total_benchmarks, incorrect, unknown, correct_rate),
        "reasons": _render_reasons_table(reasons),
        "suite_table": _render_suite_table(by_suite),
        "failures": _render_failures_table(failures),
    }
    html = _assemble_html(sections, args.build_id)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        f.write(html)

    print(f"Failure analysis written to {args.output}")
    print(f"  {total_benchmarks} total, {incorrect} incorrect, {unknown} unknown ({correct_rate:.1f}% correct)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Java Ranger Benchmark CI Orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, **kwargs):
        return sub.add_parser(name, **kwargs)

    add("list-suites", help="Print suite list as JSON array")

    p = add("run", help="Run benchmarks for one suite")
    p.add_argument("suite")
    p.add_argument("jr_dir")
    p.add_argument("sv_dir")
    p.add_argument("output_dir")

    p = add("merge", help="Merge per-suite XMLs")
    p.add_argument("output_dir")
    p.add_argument("xml_files", nargs="+")

    p = add("baseline", help="Generate baseline.json")
    p.add_argument("xml_dir")

    p = add("check", help="Check results vs baseline")
    p.add_argument("xml_dir")
    p.add_argument("--baseline", required=True)

    p = add("analyze", help="Generate failure analysis HTML")
    p.add_argument("xml_files", nargs="+")
    p.add_argument("--output", default="failure-analysis.html")
    p.add_argument("--build-id", default=None)

    args = parser.parse_args()

    if args.command == "list-suites":
        cmd_list_suites()
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "merge":
        cmd_merge(args)
    elif args.command == "baseline":
        cmd_baseline(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "analyze":
        cmd_analyze(args)


if __name__ == "__main__":
    main()
