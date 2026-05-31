#!/usr/bin/env python3
"""Generate benchexec config XMLs from suite list."""
import os, sys

TEMPLATE = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE benchmark PUBLIC "+//IDN sosy-lab.org//DTD BenchExec benchmark 1.9//EN"
  "https://www.sosy-lab.org/benchexec/benchmark-1.9.dtd">
<benchmark tool="jpf" timelimit="60" memlimit="4096" hardtimelimit="120">
  <sourcefiles>{suite_dir}/</sourcefiles>
  <option name="-Djava.library.path={jpf_symbc_lib}">{fp_opt}</option>
  <option>+target=Main</option>
  <option>+symbolic.dp=z3bitvector</option>
  <option>+symbolic.bvlength=64</option>
  <option>+search.depth_limit=13</option>
  <option>+symbolic.strings=true</option>
  <option>+symbolic.string_dp=z3str3</option>
  <option>+symbolic.string_dp_timeout_ms=3000</option>
  <option>+symbolic.lazy=on</option>
  <option>+symbolic.arrays=true</option>
  <option>+listener=.symbc.SymbolicListener</option>
  <resultfiles>{jpf_core_jar}</resultfiles>
</benchmark>
'''

SUITES = [
    ("MinePump", False),
    ("algorithms", False),
    ("argv-tasks", True),
    ("autostub", True),
    ("float-nonlinear-calculation", True),
    ("float_unboundedloop", True),
    ("java-ranger-regression", False),
    ("jayhorn-recursive", False),
    ("jbmc-regression", True),
    ("jdart-regression", True),
    ("jpf-regression", True),
    ("juliet-java", True),
    ("objects", False),
    ("rtems-lock-model", False),
    ("securibench", False),
]

# Placeholder paths — update for your local setup
JR_DIR = "/home/salmane/IdeaProjects/java-ranger"
SV_DIR = "/home/salmane/IdeaProjects/sv-benchmarks"

os.makedirs("config", exist_ok=True)

for name, has_fp in SUITES:
    fp_opt = "symbolic.fp=true" if has_fp else ""
    config = TEMPLATE.format(
        suite_dir=f"{SV_DIR}/java/{name}",
        jpf_symbc_lib=f"{JR_DIR}/jpf-symbc/lib",
        jpf_core_jar=f"{JR_DIR}/jpf-core/build/RunJPF.jar",
        fp_opt=f"+{fp_opt}" if fp_opt else "",
    )
    path = f"config/{name}.xml"
    with open(path, "w") as f:
        f.write(config.lstrip('\n'))
    print(f"  {path}")

print("Done: 15 config files")
