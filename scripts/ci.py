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
    "algorithms",
    "argv-tasks",
    "autostub",
    "float-nonlinear-calculation", "float_unboundedloop",
    "java-ranger-regression", "jayhorn-recursive",
    "jbmc-regression", "jdart-regression",
    "jpf-regression", "MinePump",
    "objects",
    "rtems-lock-model", "securibench",
    "juliet-java",
]

def make_jpf_config(classpath, fp_enabled=True):
    return """\
target=Main
classpath={classpath}
symbolic.dp=z3bitvector
symbolic.min_int=-2147483648
symbolic.max_int=2147483647
symbolic.min_double=-10000.0
symbolic.max_double=10000.0
symbolic.min_float=-10000.0
symbolic.max_float=10000.0
symbolic.bvlength=64
search.depth_limit=13
symbolic.strings=true
symbolic.string_dp=z3str3
symbolic.string_dp_timeout_ms=3000
symbolic.lazy=on
symbolic.debug=true
symbolic.jrarrays=true
veritestingMode=5
recursiveDepth=200
singlePathOptimization=true
symbolic.fp={fp}
listener=.symbc.VeritestingListener""".format(classpath=classpath, fp=str(fp_enabled).lower())

STATUS_MAP = {
    "CORRECT": "correct", "INCORRECT": "incorrect",
    "UNKNOWN": "unknown", "TIMEOUT": "unknown", "COMPILE_ERR": "unknown",
}




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

    for run in root.findall("run"):
        suite = run.get("suite") or root.get("benchmarkname", "unknown")
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
    """Extract actual error type from JPF error text. No speculative genres."""
    if not error_text or error_text == "unknown":
        return "unknown", error_text or "unknown"

    m = error_text.lower()
    truncated = error_text[:300]

    if "timeout" in m or "timed out" in m or "exit_code 124" in m or "exit code 124" in m:
        return "timeout", "timeout"
    if "cannot find symbol" in m:
        return "compile error", truncated

    exc = re.search(r'(java\.\S+(?:Exception|Error))', error_text)
    if exc:
        return exc.group(1), truncated

    if m.startswith("[severe]"):
        return "SEVERE", truncated

    first = error_text.split(" | ")[0].strip() if " | " in error_text else error_text.strip()
    return first[:100] or "unknown", truncated


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
# Per-suite XML parser
# ---------------------------------------------------------------------------

def parse_suite_xml(fpath):
    """Parse a per-suite XML and return (suite_name, options, version, date, records).

    Each record is a dict with keys: name, expected, status, cputime, walltime, error.
    """
    try:
        if fpath.endswith(".bz2"):
            with bz2.open(fpath) as f:
                tree = ET.parse(f)
        else:
            tree = ET.parse(fpath)
    except Exception as e:
        print(f"Warning: could not parse {fpath}: {e}", file=sys.stderr)
        return None

    root = tree.getroot()
    suite_name = root.get("benchmarkname", "unknown")
    options = root.get("options", "")
    version = root.get("version", "")
    date = root.get("date", "")

    records = []
    for run in root.findall("run"):
        name = (run.get("name") or "").rsplit("/", 1)[-1]
        name = name.replace(".yml", "").replace(".java", "")
        expected = run.get("expectedVerdict", "?")

        cols = {}
        for col in run.findall("column"):
            cols[col.get("title")] = col.get("value")

        status = cols.get("status", "unknown")
        cputime = cols.get("cputime", "0").rstrip("s")
        walltime = cols.get("walltime", "0").rstrip("s")

        error = ""
        error_el = run.find("column[@title='error']")
        if error_el is not None:
            error = error_el.get("value", "")
        if not error and status != "correct":
            category_el = run.find("column[@title='category']")
            if category_el is not None and category_el.get("value") == "error":
                error = cols.get("error", "")

        records.append({
            "name": name,
            "expected": expected,
            "status": status,
            "cputime": cputime,
            "walltime": walltime,
            "error": error,
        })

    return suite_name, options, version, date, records


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


def run_benchmark(yml_path, jr_dir, sv_bench_dir, output_dir, suite, log_dir,
                  timeout=30, fp_enabled=True):
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

        jpf_config_path = os.path.join(tmp_dir, "config.jpf")
        with open(jpf_config_path, "w") as f:
            f.write(make_jpf_config(classpath, fp_enabled=fp_enabled))

        log_path = os.path.join(tmp_dir, "jpf.log")
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = f"{jpf_symbc_lib}:{env.get('LD_LIBRARY_PATH', '')}"

        start_ns = time.time_ns()
        try:
            jpf_proc = subprocess.run(
                ["java", "-Xmx1024m", "-ea",
                 f"-Djava.library.path={jpf_symbc_lib}",
                 "-jar", jpf_core_jar, jpf_config_path],
                 cwd=jr_dir, capture_output=True, text=True, timeout=timeout, env=env,
            )
            exit_code = jpf_proc.returncode
            jpf_output = jpf_proc.stdout + "\n" + jpf_proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout
            err = exc.stderr
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            jpf_output = (out or "") + "\n" + (err or "")
            exit_code = 124

        end_ns = time.time_ns()
        elapsed_s = (end_ns - start_ns) / 1_000_000_000
        result.cputime = result.walltime = elapsed_s
        result.exit_code = exit_code

        os.makedirs(log_dir, exist_ok=True)
        persistent_log = os.path.join(log_dir, f"{result.name}.log")
        result.logfile = persistent_log
        try:
            lines = jpf_output.splitlines()
            if len(lines) > 200:
                jpf_output = "\n".join(lines[-200:])
            with open(persistent_log, "w") as f:
                f.write(jpf_output)
        except OSError:
            pass

        if exit_code == 124:
            result.verdict = "UNKNOWN"
            result.error = f"timeout after {timeout}s"
            return result

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


def run_suite(suite, jr_dir, sv_bench_dir, output_dir, jr_version_str,
              timeout=30, fp_enabled=True, max_benchmarks=0):
    suite_dir = os.path.join(sv_bench_dir, "java", suite)
    if not os.path.isdir(suite_dir):
        print(f"Error: Suite directory not found: {suite_dir}", file=sys.stderr)
        sys.exit(1)

    yml_files = sorted(glob.glob(os.path.join(suite_dir, "*.yml")))
    if not yml_files:
        print(f"No YML files found in {suite_dir}", file=sys.stderr)
        return

    if max_benchmarks > 0:
        yml_files = yml_files[:max_benchmarks]

    os.makedirs(output_dir, exist_ok=True)
    log_dir = os.path.join(output_dir, "logs")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    start_dt = datetime.now()

    fp_str = "true" if fp_enabled else "false"
    jpf_options = ("+target=Main +symbolic.dp=z3bitvector "
                   "+symbolic.min_int=-2147483648 +symbolic.max_int=2147483647 "
                   "+symbolic.min_double=-10000.0 +symbolic.max_double=10000.0 "
                   "+symbolic.min_float=-10000.0 +symbolic.max_float=10000.0 "
                   "+symbolic.bvlength=64 +search.depth_limit=13 "
                   "+symbolic.strings=true +symbolic.string_dp=z3str3 "
                   "+symbolic.string_dp_timeout_ms=3000 +symbolic.lazy=on "
                   "+symbolic.debug=true +symbolic.jrarrays=true "
                   "+veritestingMode=5 +recursiveDepth=200 "
                   "+singlePathOptimization=true "
                   f"+symbolic.fp={fp_str} "
                   "+listener=.symbc.VeritestingListener")

    results = []
    counters = {"total": 0, "correct": 0, "incorrect": 0, "unknown": 0, "compile_err": 0, "timeout": 0}

    print(f"--- {suite} ({len(yml_files)} benchmarks, timeout={timeout}s, fp={fp_str}) ---")

    for yml_path in yml_files:
        result = run_benchmark(yml_path, jr_dir, sv_bench_dir, output_dir, suite, log_dir,
                               timeout=timeout, fp_enabled=fp_enabled)
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

    # Remove old result files for this suite
    for f in glob.glob(os.path.join(output_dir, f"{suite}.*.results.xml*")):
        os.remove(f)

    sysinfo = get_system_info()
    xml_path = os.path.join(output_dir, f"{suite}.{timestamp}.results.xml")
    write_benchexec_xml(results, suite, start_dt, end_dt, sysinfo, xml_path, jr_version_str, jpf_options)

    xml_bz2 = xml_path + ".bz2"
    with open(xml_path, "rb") as fin:
        with bz2.open(xml_bz2, "wb") as fout:
            shutil.copyfileobj(fin, fout)

    print(f"Results: {xml_bz2}")
    os.remove(xml_path)


# ---------------------------------------------------------------------------
# Subcommand: list-suites
# ---------------------------------------------------------------------------

def cmd_list_suites(suite_filter=None):
    suites = SUITES
    if suite_filter:
        requested = [s.strip() for s in suite_filter.split(",") if s.strip()]
        if requested:
            suites = [s for s in suites if s in requested]
            missing = set(requested) - set(SUITES)
            if missing:
                print(f"Warning: unknown suites: {', '.join(sorted(missing))}", file=sys.stderr)
    print(json.dumps({"suite": suites}))


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

    run_suite(args.suite, args.jr_dir, args.sv_dir, args.output_dir, jr_version_str,
              timeout=args.timeout, fp_enabled=not args.no_fp,
              max_benchmarks=args.max_benchmarks)


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

        suite_name = root.get("benchmarkname", "unknown")
        for run in root.findall("run"):
            name = run.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                run.set("suite", suite_name)
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

def _render_header(cb):
    return f"""\
  <div class="nav">
    <a href="index.html{cb}">&larr; Back to overview</a>
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


def _render_error_cell(error):
    if not error:
        return ""
    title = escape_xml(error[:500])
    disp = escape_xml(error[:200])
    return f'<span class="error-text" title="{title}">{disp}</span>'


def _render_failures_table(failures):
    rows = "".join(
        f"<tr data-suite=\"{escape_xml(f['suite'])}\""
        f" data-status=\"{f['status']}\""
        f" data-reason=\"{escape_xml(f['reason'])}\">"
        f"<td>{escape_xml(f['suite'])}</td>"
        f"<td>{escape_xml(f['name'])}</td>"
        f"<td class=\"{'incorrect' if f['status'] == 'incorrect' else 'unknown'}\">{f['status']}</td>"
        f"<td>{escape_xml(f['expected'])}</td>"
        f"<td>{escape_xml(f['reason'])}</td>"
        f"<td>{f['time']}</td>"
        f"<td>{_render_error_cell(f['error'])}</td>"
        f"</tr>\n"
        for f in failures
    )
    return f"""\
  <h2>All Failed Benchmarks</h2>
  <div class="filter-bar">
    <select id="filterSuite" onchange="filterTable()">
      <option value="">All Suites</option>
    </select>
    <select id="filterStatus" onchange="filterTable()">
      <option value="">All Results</option>
      <option value="incorrect">Incorrect</option>
      <option value="unknown">Unknown</option>
    </select>
    <select id="filterReason" onchange="filterTable()">
      <option value="">All Reasons</option>
    </select>
    <input type="text" id="filterInput" placeholder="Search text..." onkeyup="filterTable()">
    <span id="filterCount" class="filter-count"></span>
  </div>
  <div class="table-wrapper">
    <table id="failures-table">
      <thead>
        <tr>
          <th>Suite</th><th>Benchmark</th><th>Result</th>
          <th>Expected</th><th>Failure Reason</th><th>Time (s)</th><th>Details</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>"""


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
table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 8px; margin: 20px 0; }}
th {{ background: #3498db; color: white; padding: 12px; text-align: left; font-weight: 600; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px; position: sticky; top: 0; z-index: 1; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #eef; font-size: 0.9em; }}
tbody tr:nth-child(even) {{ background: #fafbfc; }}
tr:hover {{ background: #f0f4ff; }}
.incorrect {{ color: #e74c3c; font-weight: bold; }}
.unknown {{ color: #f39c12; font-weight: bold; }}
.error-text {{ font-size: 0.85em; color: #666; max-width: 350px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: middle; }}
.nav {{ margin: 20px 0; }}
.nav a {{ color: #3498db; text-decoration: none; margin-right: 20px; }}
.nav a:hover {{ text-decoration: underline; }}
.filter-bar {{ display: flex; gap: 8px; margin: 20px 0; flex-wrap: wrap; align-items: center; }}
.filter-bar select, .filter-bar input {{ padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 0.9em; background: white; }}
.filter-bar input {{ flex: 1; min-width: 200px; }}
.filter-count {{ font-size: 0.85em; color: #888; white-space: nowrap; }}
.table-wrapper {{ overflow-x: auto; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.table-wrapper table {{ box-shadow: none; margin: 0; }}
</style>
</head>
<body>
<div class="container">
{chr(10).join(v for v in sections.values() if v)}
</div>
<script>
function buildFilterOptions() {{
  var tbody = document.querySelector('#failures-table tbody');
  if (!tbody) return;
  var rows = tbody.getElementsByTagName('tr');
  if (!rows.length) return;
  var suites = {{}}, reasons = {{}};
  for (var i = 0; i < rows.length; i++) {{
    suites[rows[i].getAttribute('data-suite')] = true;
    reasons[rows[i].getAttribute('data-reason')] = true;
  }}
  var suiteSel = document.getElementById('filterSuite');
  var reasonSel = document.getElementById('filterReason');
  Object.keys(suites).sort().forEach(function(s) {{
    var opt = document.createElement('option');
    opt.value = s; opt.textContent = s;
    suiteSel.appendChild(opt);
  }});
  Object.keys(reasons).sort().forEach(function(r) {{
    var opt = document.createElement('option');
    opt.value = r; opt.textContent = r;
    reasonSel.appendChild(opt);
  }});
  filterTable();
}}

function filterTable() {{
  var suiteVal = document.getElementById('filterSuite').value;
  var statusVal = document.getElementById('filterStatus').value;
  var reasonVal = document.getElementById('filterReason').value;
  var input = document.getElementById('filterInput');
  var filter = input.value.toUpperCase();
  var tbody = document.querySelector('#failures-table tbody');
  if (!tbody) return;
  var rows = tbody.getElementsByTagName('tr');
  var visible = 0;
  for (var i = 0; i < rows.length; i++) {{
    var show = true;
    if (suiteVal && rows[i].getAttribute('data-suite') !== suiteVal) show = false;
    if (show && statusVal && rows[i].getAttribute('data-status') !== statusVal) show = false;
    if (show && reasonVal && rows[i].getAttribute('data-reason') !== reasonVal) show = false;
    if (show && filter) {{
      var text = rows[i].textContent || rows[i].innerText;
      if (text.toUpperCase().indexOf(filter) === -1) show = false;
    }}
    rows[i].style.display = show ? '' : 'none';
    if (show) visible++;
  }}
  var count = document.getElementById('filterCount');
  if (count) count.textContent = visible + ' of ' + rows.length + ' failures';
}}

window.addEventListener('DOMContentLoaded', buildFilterOptions);
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

    cb = f"?v={args.build_id}" if args.build_id else ""
    sections = {
        "header": _render_header(cb),
        "cards": _render_summary_cards(total_benchmarks, incorrect, unknown, correct_rate),
        "reasons": _render_reasons_table(reasons),
        "suite_table": _render_suite_table(by_suite),
        "failures": _render_failures_table(failures),
    }
    html = _assemble_html(sections, args.build_id)

    # Write to versioned subdirectory when build_id is known
    if args.build_id:
        base_dir = os.path.dirname(args.output) or "."
        base_name = os.path.basename(args.output)
        args.output = os.path.join(base_dir, f"v{args.build_id}", base_name)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        f.write(html)

    print(f"Failure analysis written to {args.output}")
    print(f"  {total_benchmarks} total, {incorrect} incorrect, {unknown} unknown ({correct_rate:.1f}% correct)")


# ---------------------------------------------------------------------------
# Report generation — per-suite config + results pages
# ---------------------------------------------------------------------------

_REPORT_STYLE = """\
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; background: #f5f5f5; color: #333; }
.header { background: #2c3e50; color: white; padding: 24px 40px; }
.header h1 { margin: 0; font-size: 1.6em; }
.header p { margin: 4px 0 0; opacity: 0.8; font-size: 0.85em; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
.nav { margin: 16px 0; }
.nav a { color: #3498db; text-decoration: none; margin-right: 20px; font-size: 0.9em; }
.nav a:hover { text-decoration: underline; }
h2 { font-size: 1.15em; color: #555; border-bottom: 1px solid #ddd; padding-bottom: 6px; margin: 24px 0 12px; }
pre.config { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 12px 16px; overflow-x: auto; font-size: 0.85em; line-height: 1.5; white-space: pre-wrap; word-break: break-all; }
.summary-cards { display: flex; gap: 16px; margin: 16px 0; flex-wrap: wrap; }
.card { background: white; border-radius: 8px; padding: 16px 20px; flex: 1; min-width: 120px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.card h3 { margin: 0 0 6px; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.5px; color: #888; }
.card .number { font-size: 1.8em; font-weight: bold; }
.card .number.correct { color: #2ecc71; }
.card .number.incorrect { color: #e74c3c; }
.card .number.unknown { color: #f39c12; }
.card .number.total { color: #3498db; }
.card .pct { font-size: 0.55em; font-weight: normal; color: #999; margin-left: 4px; }
table { border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 8px; overflow: hidden; font-size: 0.9em; }
th { background: #3498db; color: white; padding: 10px 12px; text-align: left; font-weight: 600; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.5px; }
td { padding: 8px 12px; border-bottom: 1px solid #eef; }
tr:hover { background: #f8f9ff; }
.status-correct { color: #2ecc71; font-weight: bold; }
.status-incorrect { color: #e74c3c; font-weight: bold; }
.status-unknown { color: #f39c12; font-weight: bold; }
.error-text { font-size: 0.85em; color: #666; max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: middle; }
.footer { text-align: center; padding: 30px; color: #999; font-size: 0.85em; }
.index-table th:nth-child(2), .index-table td:nth-child(2),
.index-table th:nth-child(3), .index-table td:nth-child(3),
.index-table th:nth-child(4), .index-table td:nth-child(4),
.index-table th:nth-child(5), .index-table td:nth-child(5),
.index-table th:nth-child(6), .index-table td:nth-child(6) { text-align: center; }
.index-table a { color: #3498db; text-decoration: none; font-weight: 600; }
.index-table a:hover { text-decoration: underline; }
"""


def _render_suite_report_page(records, suite, options, version, run_date, build_id):
    total = len(records)
    correct = sum(1 for r in records if r["status"] == "correct")
    incorrect = sum(1 for r in records if r["status"] == "incorrect")
    unknown = total - correct - incorrect
    correct_rate = correct / total * 100 if total else 0
    cb = f"?v={build_id}" if build_id else ""

    _, summary_date = (run_date.split(" | ") + [""])[:2] if " | " in run_date else ("", run_date)

    rows = ""
    for r in records:
        status_class = f"status-{r['status']}"
        reason, disp = classify_failure(r["error"])
        error_cell = f'<span class="error-text" title="{escape_xml(r["error"][:500])}">{escape_xml(disp[:200])}</span>' if r["error"] else ""
        rows += (
            f"<tr>"
            f"<td>{escape_xml(r['name'])}</td>"
            f"<td>{escape_xml(r['expected'])}</td>"
            f"<td class=\"{status_class}\">{r['status']}</td>"
            f"<td>{r['walltime'] or r['cputime'] or ''}</td>"
            f"<td>{error_cell}</td>"
            f"</tr>\n"
        )

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape_xml(suite)} — Java Ranger Benchmarks</title>
<style>{_REPORT_STYLE}</style>
</head>
<body>
<div class="header">
  <h1>{escape_xml(suite)}</h1>
  <p>JR: {escape_xml(version)} | Date: {escape_xml(summary_date)}</p>
</div>
<div class="container">
<div class="nav">
  <a href="../index.html{cb}">&larr; Back to overview</a>
  <a href="../failure-analysis.html{cb}">Failure Analysis</a>
</div>

<h2>Config</h2>
<pre class="config">{escape_xml(options)}</pre>

<h2>Summary</h2>
<div class="summary-cards">
  <div class="card">
    <h3>Total</h3>
    <div class="number total">{total}</div>
  </div>
  <div class="card">
    <h3>Correct</h3>
    <div class="number correct">{correct}<span class="pct">({correct_rate:.1f}%)</span></div>
  </div>
  <div class="card">
    <h3>Incorrect</h3>
    <div class="number incorrect">{incorrect}</div>
  </div>
  <div class="card">
    <h3>Unknown</h3>
    <div class="number unknown">{unknown}</div>
  </div>
</div>

<h2>Results</h2>
<table>
  <tr><th>Benchmark</th><th>Expected</th><th>Status</th><th>Time (s)</th><th>Error</th></tr>
  {rows}
</table>
</div>
<div class="footer">
  Generated by java-ranger-benchmarks CI
</div>
</body>
</html>"""
    return html


def _render_landing_page(suites_info, run_id, build_id):
    cb = f"?v={build_id}" if build_id else ""

    rows = ""
    total_all = correct_all = 0
    for suite_name, info in sorted(suites_info.items()):
        total = len(info["records"])
        correct = sum(1 for r in info["records"] if r["status"] == "correct")
        incorrect = sum(1 for r in info["records"] if r["status"] == "incorrect")
        unknown = total - correct - incorrect
        rate = correct / total * 100 if total else 0
        total_all += total
        correct_all += correct
        rows += (
            f"<tr>"
            f"<td><a href=\"suites/{escape_xml(suite_name)}/index.html{cb}\">{escape_xml(suite_name)}</a></td>"
            f"<td>{total}</td>"
            f"<td>{correct}</td>"
            f"<td>{incorrect}</td>"
            f"<td>{unknown}</td>"
            f"<td>{rate:.1f}%</td>"
            f"</tr>\n"
        )

    # Pick a representative version/date from the first suite
    first_info = next(iter(suites_info.values())) if suites_info else {"version": "", "date": ""}
    version_str = first_info.get("version", "")
    run_date = first_info.get("date", "")

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Java Ranger Benchmarks</title>
<style>{_REPORT_STYLE}</style>
</head>
<body>
<div class="header">
  <h1>Java Ranger Benchmarks</h1>
  <p>JR: {escape_xml(version_str)} | Date: {escape_xml(run_date)}</p>
</div>
<div class="container">
<div class="summary-cards">
  <div class="card">
    <h3>Total Benchmarks</h3>
    <div class="number total">{total_all}</div>
  </div>
  <div class="card">
    <h3>Overall Correct</h3>
    <div class="number correct">{correct_all}<span class="pct">({correct_all / max(total_all, 1) * 100:.1f}%)</span></div>
  </div>
</div>

<h2>Suites</h2>
<table class="index-table">
  <tr><th>Suite</th><th>Total</th><th>Correct</th><th>Incorrect</th><th>Unknown</th><th>Correct Rate</th></tr>
  {rows}
</table>

<h2>Analysis</h2>
<ul>
  <li><a href="failure-analysis.html{cb}">Failure Analysis</a> — overall accuracy and failure reasons breakdown</li>
</ul>
</div>
<div class="footer">
  Generated by java-ranger-benchmarks CI &mdash; Run {escape_xml(str(run_id or ""))}
</div>
</body>
</html>"""
    return html


def _render_redirect_page(build_id):
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="0; url=v{build_id}/index.html">
<title>Java Ranger Benchmarks</title>
</head>
<body>
<p>Redirecting to <a href="v{build_id}/index.html">latest results</a>...</p>
</body>
</html>"""


def cmd_report(args):
    xml_files = [f for f in args.xml_files if os.path.isfile(f)]
    if not xml_files:
        print("No XML files found", file=sys.stderr)
        sys.exit(1)

    build_id = args.build_id or datetime.now().strftime("%Y%m%d%H%M%S")
    output_dir = os.path.join(args.output_dir, f"v{build_id}")
    os.makedirs(output_dir, exist_ok=True)

    # Remove stale versioned docs (keep only current build)
    for entry in os.listdir(args.output_dir):
        entry_path = os.path.join(args.output_dir, entry)
        if entry.startswith("v") and os.path.isdir(entry_path) and entry != f"v{build_id}":
            shutil.rmtree(entry_path, ignore_errors=True)
            print(f"  Removed stale docs: {entry_path}")

    # Group by suite, deduplicating by benchmark name
    suites = {}
    for fpath in sorted(xml_files):
        parsed = parse_suite_xml(fpath)
        if parsed is None:
            continue
        suite_name, options, version, run_date, records = parsed
        if suite_name not in suites:
            suites[suite_name] = {
                "records": [],
                "options": options,
                "version": version,
                "date": run_date,
            }
        seen_names = set(r["name"] for r in suites[suite_name]["records"])
        for rec in records:
            if rec["name"] not in seen_names:
                seen_names.add(rec["name"])
                suites[suite_name]["records"].append(rec)

    if not suites:
        print("No suite data found in XML files", file=sys.stderr)
        sys.exit(1)

    build_id = args.build_id or datetime.now().strftime("%Y%m%d%H%M%S")

    # Generate per-suite pages
    for suite_name, info in sorted(suites.items()):
        suite_dir = os.path.join(output_dir, "suites", suite_name)
        os.makedirs(suite_dir, exist_ok=True)
        output_path = os.path.join(suite_dir, "index.html")
        html = _render_suite_report_page(
            info["records"], suite_name, info["options"],
            info["version"], info["date"], build_id,
        )
        with open(output_path, "w") as f:
            f.write(html)
        print(f"  {suite_name}: {len(info['records'])} benchmarks → {output_path}")

    # Generate landing page
    index_path = os.path.join(output_dir, "index.html")
    html = _render_landing_page(suites, args.run_id, build_id)
    with open(index_path, "w") as f:
        f.write(html)
    print(f"Landing page: {index_path}")

    # Generate root redirect
    redirect_path = os.path.join(args.output_dir, "index.html")
    html = _render_redirect_page(build_id)
    with open(redirect_path, "w") as f:
        f.write(html)
    print(f"Root redirect: {redirect_path}")
    print(f"Total: {len(suites)} suites, {sum(len(info['records']) for info in suites.values())} benchmarks")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Java Ranger Benchmark CI Orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, **kwargs):
        return sub.add_parser(name, **kwargs)

    p = add("list-suites", help="Print suite list as JSON array")
    p.add_argument("filter", nargs="?", default=None,
                   help="Comma-separated suite names (default: all suites)")

    p = add("run", help="Run benchmarks for one suite")
    p.add_argument("suite")
    p.add_argument("jr_dir")
    p.add_argument("sv_dir")
    p.add_argument("output_dir")
    p.add_argument("--timeout", type=int, default=30,
                   help="Per-benchmark JPF timeout in seconds (default: 30)")
    p.add_argument("--no-fp", action="store_true",
                   help="Disable symbolic.fp (floating-point theory)")
    p.add_argument("--max-benchmarks", type=int, default=0,
                   help="Limit benchmarks per suite (0 = all, default: 0)")

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

    p = add("report", help="Generate per-suite report pages + index.html")
    p.add_argument("xml_files", nargs="+")
    p.add_argument("--output-dir", default="docs")
    p.add_argument("--build-id", default=None)
    p.add_argument("--run-id", default=None)

    args = parser.parse_args()

    if args.command == "list-suites":
        cmd_list_suites(args.filter)
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
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
