#!/usr/bin/env python3
"""
================================================================================
MULTI FIXING COMMIT FULL BUILD TEST RUNNER
================================================================================

PURPOSE:
--------
For each bug from the ready-to-build manifest:
  1. Try to build with Python 3 (current environment) — no patching, no bootstrap
  2. If build succeeds → run tests, save to successful_builds/
  3. If build fails    → save to needs_python2/ with full context for retry later

PARALLELIZATION:
----------------
8 workers, each with its own mozilla-central clone.
Run each worker in a separate tmux window:
  python3 multi_fixing_commit_final_full_build.py --worker 1
  ...
  python3 multi_fixing_commit_final_full_build.py --worker 8

INPUT:
------
outputs/multi_fixing_commit_ready_to_full_build_manifest/buildable_bugs/
└── <framework_group>/
    └── bug_<ID>.json

OUTPUT STRUCTURE:
-----------------
outputs/multi_fixing_commit_full_build/
├── worker_<N>/
│   ├── successful_builds/
│   │   ├── pipeline_validated/
│   │   │   └── bug_<ID>/test_results.json
│   │   ├── pipeline_not_validated/
│   │   │   └── bug_<ID>/test_results.json
│   │   └── bug_<ID>/test_results.json
│   ├── failed_builds/
│   │   └── bug_<ID>/build_failure.json
│   ├── needs_python2/
│   │   └── bug_<ID>/retry_context.json   ← for python2 retry script later
│   ├── pipeline_summary.json
│   └── statistics_report.txt
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)

print(f"Working directory: {parent_dir}")


# ===========================================================================
# CONSTANTS
# ===========================================================================

INPUT_DIR  = "multi_fixing_commit_ready_to_full_build_manifest/buildable_bugs"
OUTPUT_DIR = "multi_fixing_commit_full_build"

WORKER_REPOS = {
    1: Path("/data/FaultLocalizationIndustry/mozilla-central"),
    2: Path("/data/FaultLocalizationIndustry/mozilla-central-worker2"),
    3: Path("/data/FaultLocalizationIndustry/mozilla-central-worker3"),
    4: Path("/data/FaultLocalizationIndustry/mozilla-central-worker4"),
    5: Path("/data/FaultLocalizationIndustry/mozilla-central-worker5"),
    6: Path("/data/FaultLocalizationIndustry/mozilla-central-worker6"),
    7: Path("/data/FaultLocalizationIndustry/mozilla-central-worker7"),
    8: Path("/data/FaultLocalizationIndustry/mozilla-central-worker8"),
}
NUM_WORKERS = 2

BUILD_TIMEOUT = 5400   # 90 minutes per build
TEST_TIMEOUT  = 1800   # 30 minutes per test run

NON_RUNNABLE_EXTENSIONS = {
    ".ini", ".toml", ".mjs", ".sjs", ".list",
    ".json", ".yaml", ".yml", ".pem", ".certspec",
    ".txt", ".in", ".build",
}
UNSUPPORTED_EXTENSIONS = {
    ".kt", ".java", ".h", ".cpp",
    ".mp3", ".mp4", ".wav", ".adts", ".png",
}
UNSUPPORTED_FRAMEWORKS = {"android", "marionette"}
UNSUPPORTED_PATH_PATTERNS = ["mobile/android/", "geckoview/"]

CRASH_SIGNALS = [
    "Application shut down (without crashing) in the middle of a test",
    "MOZ_DIAGNOSTIC_ASSERT",
    "REFTEST PROCESS-CRASH",
    "TEST_END: CRASH",
    "TEST_END: Test CRASH",
]

# Signals that indicate the failure is Python 2 / environment related
# (not a real build failure — needs python2 retry)
PYTHON2_FAILURE_SIGNALS = [
    "SyntaxError",
    "ImportError: cannot import name",
    "collections.abc",
    "_virtualenvs/mach/bin/python",
    "wasn't found on the system",
    "mach create-mach-environment",
    "MACH_USE_SYSTEM_PYTHON",
    "python2",
    "No module named",
    "from collections import",
    "__builtin__",
    "basestring",
    "has_key",
    "iteritems",
    "itervalues",
    "print ",          # print statement (very old commits)
]


# ===========================================================================
# MOZCONFIG
# ===========================================================================

def ensure_mozconfig(mozilla_central: Path) -> Dict:
    mozconfig = mozilla_central / "mozconfig"
    if not mozconfig.exists():
        print(f"  [mozconfig] creating mozconfig in {mozilla_central.name}")
        mozconfig.write_text(
            "ac_add_options --disable-bootstrap\n"
            "ac_add_options --without-wasm-sandboxed-libraries\n"
            "mk_add_options MOZ_OBJDIR=./objdir-fullBuild\n"
            "mk_add_options AUTOCLOBBER=1\n",
            encoding="utf-8"
        )
        print("  [mozconfig] created ✓")
    else:
        content = mozconfig.read_text(encoding="utf-8")
        print(f"  [mozconfig] found existing mozconfig:\n{content}")

    env = os.environ.copy()
    env["MOZCONFIG"] = str(mozconfig)
    return env


# ===========================================================================
# MERCURIAL HELPERS
# ===========================================================================

def hg_current(mozilla_central: Path, env: Dict) -> str:
    result = subprocess.run(
        ["hg", "log", "-r", ".", "--template", "{node}"],
        cwd=mozilla_central, capture_output=True, text=True, env=env
    )
    return result.stdout.strip()


def hg_update(commit_hash: str, mozilla_central: Path, env: Dict) -> Tuple[bool, str]:
    """Checkout a specific commit. No patching — pure checkout."""
    print(f"    [hg update] → {commit_hash[:12]} ...", end=" ", flush=True)
    result = subprocess.run(
        ["hg", "update", "-r", commit_hash, "--clean"],
        cwd=mozilla_central, capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        print("FAILED")
        return False, result.stderr.strip()
    print("ok")
    return True, ""


# ===========================================================================
# PYTHON2 DETECTION
# ===========================================================================

def is_python2_commit(mozilla_central: Path) -> bool:
    """
    Detect if the currently checked out commit uses the old Python 2 mach.
    Old commits have build/mach_bootstrap.py.
    New commits have mach_initialize.py (Python 3 native).
    """
    return (mozilla_central / "build" / "mach_bootstrap.py").exists()


def classify_build_failure(output: str) -> str:
    """
    Given build output, determine if this is a Python 2 environment issue
    or a genuine build failure.
    Returns: "python2_env" or "genuine_failure"
    """
    for signal in PYTHON2_FAILURE_SIGNALS:
        if signal in output:
            return "python2_env"
    return "genuine_failure"


# ===========================================================================
# MACH BUILD
# ===========================================================================

def mach_build(commit_hash: str, mozilla_central: Path, env: Dict) -> Tuple[bool, str, str]:
    """
    Run ./mach build with Python 3 as-is.
    Returns (success, failure_reason, failure_category)
    failure_category: "python2_env" | "genuine_failure" | ""
    """
    print(f"    [mach build] building {commit_hash[:12]} ...", flush=True)
    start = time.time()
    try:
        result = subprocess.run(
            f"cd {mozilla_central} && ./mach build",
            shell=True, capture_output=True, text=True,
            timeout=BUILD_TIMEOUT, env=env,
        )
        duration = round(time.time() - start, 2)
        output   = result.stdout + result.stderr

        if result.returncode == 0 or \
           "build finally finished successfully" in output or \
           "Your build was successful" in output:
            print(f"    [mach build] ✓ BUILD SUCCESSFUL ({duration}s)")
            return True, "", ""

        output_lines = output.strip().splitlines()
        last_lines   = output_lines[-20:] if len(output_lines) > 20 else output_lines
        category     = classify_build_failure(output)

        print(f"    [mach build] ✗ BUILD FAILED ({duration}s) [{category}]")
        print(f"    [mach build] last output:\n" + "\n".join(last_lines))

        last_line = output_lines[-1] if output_lines else f"rc={result.returncode}"
        return False, last_line, category

    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 2)
        print(f"    [mach build] ✗ BUILD TIMEOUT after {BUILD_TIMEOUT}s")
        return False, f"build timed out after {BUILD_TIMEOUT}s", "genuine_failure"
    except Exception as e:
        print(f"    [mach build] ✗ BUILD ERROR: {e}")
        return False, str(e), "genuine_failure"


# ===========================================================================
# TEST FILE PATCHING
# ===========================================================================

def patch_test_files(
    test_files: List[Dict], fixing_commit: str,
    mozilla_central: Path, env: Dict,
) -> List[Path]:
    patched = []
    for tf in test_files:
        filepath = tf.get("filepath", "")
        if not filepath:
            continue
        dest   = mozilla_central / filepath
        result = subprocess.run(
            ["hg", "cat", "-r", fixing_commit, filepath],
            cwd=mozilla_central, capture_output=True, env=env
        )
        if result.returncode != 0:
            print(f"      [patch] WARNING: could not get {filepath} at {fixing_commit[:12]}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(result.stdout)
        patched.append(dest)
        print(f"      [patch] {filepath}")
    return patched


def remove_patched_files(patched: List[Path]):
    for p in patched:
        try:
            p.unlink()
        except Exception as e:
            print(f"      [unpatch] WARNING: could not remove {p}: {e}")


# ===========================================================================
# GTEST HELPERS
# ===========================================================================

def build_gtest_command(filepath: str) -> str:
    stem = Path(filepath).stem
    return f"./mach gtest *{stem}*"


# ===========================================================================
# RESULT CLASSIFICATION
# ===========================================================================

def extract_unexpected_count(stdout: str) -> Optional[int]:
    last_count = None
    for line in stdout.splitlines():
        if "Unexpected results:" in line:
            try:
                last_count = int(line.split("Unexpected results:")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif line.strip().startswith("Unexpected:"):
            try:
                last_count = int(line.split("Unexpected:")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    return last_count


def classify_failure_reason(stdout: str, stderr: str, returncode: int, framework: str = "") -> str:
    combined   = stdout + stderr
    if framework == "gtest":
        return "genuine" if ("[ FAILED ]" in combined or "[ PASSED ]" in combined) else "technical"

    unexpected = extract_unexpected_count(combined)
    if unexpected is not None:
        return "genuine"

    technical_signals = [
        "Application shut down (without crashing) in the middle of a test",
        "AbortError: Actor 'SpecialPowers' destroyed",
        "No such schema", "UNKNOWN TEST", "shell is not executable",
        "unable to find tests from the given", "could not find any mochitests",
        "Error: No tests to run", "Ran 0 checks", "timed out after",
        "NS_ERROR_ABORT", "Exiting due to channel error",
        "no DISPLAY environment variable", "TestManifestBackend is out of date",
    ]
    for s in technical_signals:
        if s in combined:
            return "technical"

    genuine_signals = [
        "TEST-FAIL", "TEST-UNEXPECTED-FAIL", "expected PASS", "got FAIL",
        "AssertionError", "assertion failed", "MOZ_DIAGNOSTIC_ASSERT",
    ]
    for s in genuine_signals:
        if s in combined:
            return "genuine"

    return "unknown"


def parse_test_result(returncode: int, stdout: str, role: str = "fixing", framework: str = "") -> str:
    if framework == "gtest":
        has_passed = "[ PASSED ]" in stdout or "PASSED" in stdout
        has_failed = "[ FAILED ]" in stdout or "FAILED" in stdout
        if role == "parent":
            return "fail" if (has_failed or returncode != 0) else "pass"
        return "fail" if has_failed else ("pass" if has_passed else "fail")

    if framework == "crashtest":
        has_crash       = "REFTEST PROCESS-CRASH" in stdout
        final_unexpected = extract_unexpected_count(stdout)
        if role == "parent":
            if has_crash or (final_unexpected and final_unexpected > 0) or returncode != 0:
                return "fail"
            return "pass"
        if has_crash or (final_unexpected and final_unexpected > 0):
            return "fail"
        if final_unexpected == 0 or (not has_crash and returncode == 0):
            return "pass"
        return "fail"

    has_crash        = any(s in stdout for s in CRASH_SIGNALS)
    final_unexpected = extract_unexpected_count(stdout)
    ends_with_ok     = "OK\n" in stdout or stdout.strip().endswith("OK")
    ends_with_failed = "FAILED\n" in stdout or stdout.strip().endswith("FAILED")

    if role == "parent":
        if has_crash or (final_unexpected and final_unexpected > 0) or ends_with_failed or returncode != 0:
            return "fail"
        return "pass"

    if has_crash or final_unexpected is None or final_unexpected > 0 or not ends_with_ok:
        return "fail"
    return "pass"


# ===========================================================================
# RUNNABILITY CHECK
# ===========================================================================

def is_runnable(tf: Dict) -> Tuple[bool, str]:
    filepath  = tf.get("filepath", "")
    framework = tf.get("framework", "unknown")
    ext       = Path(filepath).suffix.lower()

    if framework == "gtest" and ext == ".cpp":
        return True, ""
    if ext in NON_RUNNABLE_EXTENSIONS:
        return False, f"non-runnable extension ({ext})"
    if ext in UNSUPPORTED_EXTENSIONS:
        return False, f"unsupported on this platform ({ext})"
    if framework in UNSUPPORTED_FRAMEWORKS:
        return False, f"unsupported framework ({framework})"
    for pattern in UNSUPPORTED_PATH_PATTERNS:
        if pattern in filepath:
            return False, f"unsupported path ({pattern})"
    if not tf.get("mach_command", ""):
        return False, "no mach_command"
    return True, ""


# ===========================================================================
# TEST RUNNER
# ===========================================================================

def kill_leftover_firefox(env: Dict):
    subprocess.run("pkill -f 'firefox' || true", shell=True, capture_output=True, env=env)
    time.sleep(2)


def run_single_test(tf: Dict, env: Dict, role: str, mozilla_central: Path) -> Dict:
    filepath  = tf.get("filepath", "")
    framework = tf.get("framework", "unknown")
    mach_cmd  = tf.get("mach_command", "")

    if framework == "gtest":
        mach_cmd = build_gtest_command(filepath)

    kill_leftover_firefox(env)
    mach       = str(mozilla_central / "mach")
    actual_cmd = mach_cmd.replace("./mach", mach, 1)

    if framework != "gtest":
        parts = actual_cmd.split(" ", 2)
        if len(parts) >= 2:
            actual_cmd = f"{parts[0]} {parts[1]} --headless {parts[2] if len(parts) > 2 else ''}".strip()

    print(f"      [test] {actual_cmd}", flush=True)
    start = time.time()
    try:
        result = subprocess.run(
            f"cd {mozilla_central} && {actual_cmd}",
            shell=True, capture_output=True, text=True,
            timeout=TEST_TIMEOUT, env=env,
        )
        duration       = round(time.time() - start, 2)
        status         = parse_test_result(result.returncode, result.stdout, role, framework)
        failure_reason = (
            classify_failure_reason(result.stdout, result.stderr, result.returncode, framework)
            if status != "pass" else "n/a"
        )
        symbol = "✓" if status == "pass" else "✗"
        print(f"      [test] {symbol} {status.upper()} (rc={result.returncode}, {duration}s, reason={failure_reason})", flush=True)
        return {
            "filepath": filepath, "mach_command": mach_cmd, "framework": framework,
            "result": status, "failure_reason": failure_reason,
            "returncode": result.returncode, "duration_seconds": duration,
            "stdout_tail": result.stdout[-2000:] if result.stdout else "",
            "stderr_tail": result.stderr[-500:]  if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 2)
        kill_leftover_firefox(env)
        print(f"      [test] ✗ TIMEOUT after {TEST_TIMEOUT}s", flush=True)
        return {
            "filepath": filepath, "mach_command": mach_cmd, "framework": framework,
            "result": "timeout", "failure_reason": "technical", "returncode": -1,
            "duration_seconds": duration, "stdout_tail": "",
            "stderr_tail": f"timed out after {TEST_TIMEOUT}s",
        }
    except Exception as e:
        print(f"      [test] ✗ ERROR: {e}", flush=True)
        return {
            "filepath": filepath, "mach_command": mach_cmd, "framework": framework,
            "result": "error", "failure_reason": "technical", "returncode": -1,
            "duration_seconds": 0, "stdout_tail": "", "stderr_tail": str(e),
        }


def run_tests_for_commit(
    test_files: List[Dict], env: Dict, role: str,
    fixing_commit: str, mozilla_central: Path,
) -> List[Dict]:
    results = []
    patched = []
    if role == "parent":
        print(f"      [patch] patching test files from fixing commit {fixing_commit[:12]}...")
        patched = patch_test_files(test_files, fixing_commit, mozilla_central, env)

    for tf in test_files:
        filepath  = tf.get("filepath", "")
        framework = tf.get("framework", "unknown")
        mach_cmd  = tf.get("mach_command", "")
        runnable, reason = is_runnable(tf)
        if not runnable:
            print(f"      [skip] {filepath} — {reason}")
            results.append({
                "filepath": filepath, "mach_command": mach_cmd, "framework": framework,
                "result": "skipped", "failure_reason": "n/a", "returncode": None,
                "duration_seconds": 0, "stdout_tail": "", "stderr_tail": reason,
            })
            continue
        results.append(run_single_test(tf, env, role, mozilla_central))

    if role == "parent" and patched:
        print("      [unpatch] removing patched files...")
        remove_patched_files(patched)

    return results


# ===========================================================================
# OVERALL STATUS
# ===========================================================================

def compute_overall_status(commit_results: List[Dict]) -> str:
    if not commit_results:
        return "skipped"
    if all(not r.get("build_ok", False) for r in commit_results):
        return "skipped"

    any_build_failed = any(not r.get("build_ok", False) for r in commit_results)
    all_tests = [t for r in commit_results for t in r.get("tests", [])]
    if all_tests and all(t["result"] == "skipped" for t in all_tests):
        return "not_validated_skipped"

    validated = True
    for r in commit_results:
        if not r.get("build_ok", False):
            continue
        for t in r.get("tests", []):
            if t["result"] == "skipped":
                continue
            if r["role"] == "fixing" and t["result"] != "pass":
                validated = False
            if r["role"] == "parent" and t["result"] == "pass":
                validated = False

    if any_build_failed:
        return "partial"
    return "pass" if validated else "fail"


# ===========================================================================
# INPUT LOADER
# ===========================================================================

def load_all_bugs(input_base: Path) -> List[Dict]:
    bugs = []
    for group_dir in sorted(input_base.iterdir()):
        if not group_dir.is_dir():
            continue
        for bug_file in sorted(group_dir.glob("bug_*.json")):
            try:
                bugs.append(json.loads(bug_file.read_text(encoding="utf-8")))
            except Exception as e:
                print(f"  WARNING: could not read {bug_file}: {e}")
    print(f"Loaded {len(bugs)} buildable bugs from {input_base}\n")
    return bugs


def get_worker_bugs(bugs: List[Dict], worker_id: int, num_workers: int) -> List[Dict]:
    worker_bugs = [bugs[i] for i in range(len(bugs)) if (i % num_workers) == (worker_id - 1)]
    print(f"Worker {worker_id}: assigned {len(worker_bugs)} bugs out of {len(bugs)} total\n")
    return worker_bugs


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    SUBDIR_SUCCESS       = "successful_builds"
    SUBDIR_VALIDATED     = "successful_builds/pipeline_validated"
    SUBDIR_NOT_VALIDATED = "successful_builds/pipeline_not_validated"
    SUBDIR_FAILED        = "failed_builds"
    SUBDIR_PYTHON2       = "needs_python2"   # ← new: for python2 retry later

    def __init__(self, base_dir: Path, worker_id: int):
        self.base = base_dir / f"worker_{worker_id}"
        self.base.mkdir(parents=True, exist_ok=True)
        for subdir in [
            self.SUBDIR_SUCCESS,
            self.SUBDIR_VALIDATED,
            self.SUBDIR_NOT_VALIDATED,
            self.SUBDIR_FAILED,
            self.SUBDIR_PYTHON2,
        ]:
            (self.base / subdir).mkdir(parents=True, exist_ok=True)

    def save_needs_python2(self, bug: Dict, failing_commits: List[Dict]):
        """
        Save bugs whose commits failed due to Python 2 environment issues.
        Stores the full original bug JSON plus which commits failed and why,
        so the Python 2 retry script can pick them up directly.
        """
        bug_id  = bug.get("bug_id", "unknown")
        bug_dir = self.base / self.SUBDIR_PYTHON2 / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id":           bug_id,
            "framework_group":  bug.get("framework_group", "unknown"),
            "original_bug":     bug,
            "failing_commits":  failing_commits,
            "recorded_at":      datetime.now().isoformat(),
            "retry_with":       "python2",
        }
        (bug_dir / "retry_context.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(f"  [needs_python2] saved retry context for bug {bug_id} ✓")

    def save_build_failure(self, bug_id: str, commit_hash: str, role: str, reason: str, step: str):
        bug_dir = self.base / self.SUBDIR_FAILED / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id": bug_id, "commit_hash": commit_hash, "role": role,
            "failed_step": step, "reason": reason,
            "recorded_at": datetime.now().isoformat(),
        }
        out      = bug_dir / "build_failure.json"
        existing = []
        if out.exists():
            try:
                existing = json.loads(out.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = [existing]
            except Exception:
                existing = []
        existing.append(payload)
        out.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def save_bug_result(self, bug_id: str, result: Dict):
        overall = result.get("overall_status", "skipped")
        bug_dir = self.base / self.SUBDIR_SUCCESS / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        (bug_dir / "test_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

        val_subdir = self.SUBDIR_VALIDATED if overall == "pass" else self.SUBDIR_NOT_VALIDATED
        val_dir    = self.base / val_subdir / f"bug_{bug_id}"
        val_dir.mkdir(parents=True, exist_ok=True)
        (val_dir / "test_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    def save_pipeline_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "statistics": stats,
            "per_bug": {
                bid: {
                    "overall_status":  r.get("overall_status"),
                    "framework_group": r.get("framework_group"),
                    "commits": [
                        {
                            "commit_hash":  c["commit_hash"],
                            "role":         c["role"],
                            "expected":     c["expected"],
                            "build_ok":     c["build_ok"],
                            "build_category": c.get("build_category", ""),
                            "test_count":   len(c.get("tests", [])),
                            "tests_pass":   sum(1 for t in c.get("tests", []) if t["result"] == "pass"),
                            "tests_fail":   sum(1 for t in c.get("tests", []) if t["result"] not in ("pass", "skipped")),
                        }
                        for c in r.get("commits", [])
                    ]
                }
                for bid, r in results.items() if "error" not in r
            }
        }
        sp = self.base / "pipeline_summary.json"
        sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ pipeline_summary.json → {sp}")

    def save_statistics_report(self, stats: Dict, results: Dict):
        rp    = self.base / "statistics_report.txt"
        lines = [
            "=" * 80,
            "MULTI FIXING COMMIT FULL BUILD RUNNER — STATISTICS REPORT",
            "=" * 80,
            f"Generated: {datetime.now().isoformat()}",
            "",
            f"Total bugs processed               : {stats['total_bugs']}",
            f"Pipeline validated (pass)          : {stats['bugs_pass']}",
            f"Not validated (fail)               : {stats['bugs_fail']}",
            f"Partial (some builds failed)       : {stats['bugs_partial']}",
            f"Skipped (all builds failed)        : {stats['bugs_skipped']}",
            f"Not validated (tests skipped)      : {stats['bugs_not_validated_skipped']}",
            f"Sent to needs_python2              : {stats['bugs_needs_python2']}",
            f"Total commits attempted            : {stats['total_commits_attempted']}",
            f"Commits built successfully         : {stats['total_commits_built']}",
            f"Commits failed (python2 env)       : {stats['total_commits_python2']}",
            f"Commits failed (genuine)           : {stats['total_commits_failed_genuine']}",
            f"Total tests run                    : {stats['total_tests_run']}",
            f"Tests passed                       : {stats['total_tests_pass']}",
            f"Tests failed                       : {stats['total_tests_fail']}",
            f"Tests skipped                      : {stats['total_tests_skipped']}",
            "", "=" * 80, "PER-BUG RESULTS", "=" * 80, "",
        ]
        for bid, r in results.items():
            if "error" in r:
                lines.append(f"Bug {bid}  [ERROR: {r.get('error')}]")
            else:
                status_str = r.get('overall_status', '?').upper()
                lines.append(f"Bug {bid}  [{status_str}]  frameworks={r.get('framework_group','?')}")
                for c in r.get("commits", []):
                    build_str = "built" if c["build_ok"] else f"FAILED[{c.get('build_category','')}]"
                    tests     = c.get("tests", [])
                    n_pass    = sum(1 for t in tests if t["result"] == "pass")
                    n_fail    = sum(1 for t in tests if t["result"] not in ("pass", "skipped"))
                    lines.append(
                        f"  [{c['role']:7s}] {c['commit_hash'][:12]}  "
                        f"expected={c['expected']:4s}  build={build_str}  "
                        f"pass={n_pass}  fail={n_fail}"
                    )
                    for t in tests:
                        lines.append(
                            f"           {t['result'].upper():8s}  "
                            f"reason={t.get('failure_reason','n/a'):10s}  "
                            f"{t['mach_command']}"
                        )
            lines.append("")
        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ statistics_report.txt → {rp}")


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class FullBuildRunnerPipeline:

    def __init__(self, worker_id: int):
        self.worker_id       = worker_id
        self.script_dir      = Path(__file__).resolve().parent
        self.outputs_base    = self.script_dir / "outputs"
        self.mozilla_central = WORKER_REPOS[worker_id]

        self.input_base  = self.outputs_base / INPUT_DIR
        self.output_base = self.outputs_base / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.env    = ensure_mozconfig(self.mozilla_central)
        self.writer = OutputWriter(self.output_base, worker_id)

        print(f"Worker:          {worker_id} of {NUM_WORKERS}")
        print(f"Mozilla-central: {self.mozilla_central}")
        print(f"Input:           {self.input_base}")
        print(f"Output:          {self.output_base / f'worker_{worker_id}'}\n")

    def process_bug(self, bug: Dict, idx: int, total: int) -> Dict:
        bug_id        = bug["bug_id"]
        framework_grp = bug.get("framework_group", "unknown")
        commit_pairs  = bug.get("commit_pairs", [])

        print(f"\n{'='*60}", flush=True)
        print(f"[Worker {self.worker_id}] [{idx}/{total}] Bug {bug_id}  frameworks={framework_grp}", flush=True)
        print(f"{'='*60}", flush=True)

        commit_results  = []
        seen_commits    = set()
        python2_commits = []   # track commits that failed due to python2 env

        for pair in commit_pairs:
            fixing_commit = pair["fixing_commit"]
            parent_commit = pair["parent_commit"]
            test_files    = pair["test_files"]

            for role, commit_hash, expected in [
                ("fixing", fixing_commit, "pass"),
                ("parent", parent_commit, "fail"),
            ]:
                if not commit_hash or commit_hash in seen_commits:
                    continue
                if commit_hash in ("no_parent_found", None):
                    print(f"  [{role.upper()}] no commit hash — skipping")
                    continue
                seen_commits.add(commit_hash)

                print(f"\n  [{role.upper()}] {commit_hash[:12]}  (expect {expected.upper()})", flush=True)

                # Step 1 — hg update (no patching)
                update_ok, update_err = hg_update(commit_hash, self.mozilla_central, self.env)
                if not update_ok:
                    print(f"  ✗ hg update FAILED for {commit_hash[:12]}", flush=True)
                    self.writer.save_build_failure(bug_id, commit_hash, role, update_err, "hg_update")
                    commit_results.append({
                        "commit_hash": commit_hash, "role": role, "expected": expected,
                        "build_ok": False, "build_error": update_err,
                        "build_category": "hg_update_failed", "failed_step": "hg_update", "tests": [],
                    })
                    continue

                # Note whether this is an old Python 2 era commit
                is_py2_era = is_python2_commit(self.mozilla_central)
                if is_py2_era:
                    print(f"  [info] old commit — has mach_bootstrap.py (Python 2 era)")

                # Step 2 — mach build (Python 3, no patching)
                build_ok, build_err, build_category = mach_build(
                    commit_hash, self.mozilla_central, self.env
                )

                if not build_ok:
                    print(f"  ✗ BUILD FAILED [{build_category}] for {commit_hash[:12]}", flush=True)
                    self.writer.save_build_failure(bug_id, commit_hash, role, build_err, "mach_build")
                    commit_results.append({
                        "commit_hash": commit_hash, "role": role, "expected": expected,
                        "build_ok": False, "build_error": build_err,
                        "build_category": build_category,
                        "is_python2_era": is_py2_era,
                        "failed_step": "mach_build", "tests": [],
                    })
                    if build_category == "python2_env":
                        python2_commits.append({
                            "commit_hash":  commit_hash,
                            "role":         role,
                            "expected":     expected,
                            "build_error":  build_err,
                            "is_python2_era": is_py2_era,
                            "test_files":   test_files,
                            "fixing_commit": fixing_commit,
                        })
                    continue

                # Step 3 — run tests
                print(f"  Running {len(test_files)} test file(s)...", flush=True)
                test_results = run_tests_for_commit(
                    test_files, self.env, role, fixing_commit, self.mozilla_central
                )
                commit_results.append({
                    "commit_hash": commit_hash, "role": role, "expected": expected,
                    "build_ok": True, "build_error": None,
                    "build_category": "", "is_python2_era": is_py2_era,
                    "failed_step": None, "tests": test_results,
                })

                # Step 4 — clean up objdir to reclaim disk space
                # AUTOCLOBBER will rebuild it fresh for the next commit anyway
                objdir = self.mozilla_central / "objdir-fullBuild"
                if objdir.exists():
                    print(f"  [cleanup] removing objdir to reclaim disk space...")
                    import shutil
                    shutil.rmtree(objdir, ignore_errors=True)
                    print(f"  [cleanup] objdir removed ✓")

        # If any commits need python2, save retry context
        if python2_commits:
            self.writer.save_needs_python2(bug, python2_commits)

        overall    = compute_overall_status(commit_results)
        bug_result = {
            "bug_id":           bug_id,
            "framework_group":  framework_grp,
            "overall_status":   overall,
            "worker_id":        self.worker_id,
            "commits":          commit_results,
            "has_python2_commits": len(python2_commits) > 0,
            "recorded_at":      datetime.now().isoformat(),
        }

        if any(r["build_ok"] for r in commit_results):
            self.writer.save_bug_result(bug_id, bug_result)

        print(
            f"\n  → Bug {bug_id}  status={overall.upper()}  "
            f"built={sum(1 for r in commit_results if r['build_ok'])}/{len(commit_results)} commits  "
            f"python2_commits={len(python2_commits)}",
            flush=True
        )
        return bug_result

    def _ensure_build_environment(self):
        """
        Run bootstrap.py once at startup per worker.
        Step 1 — update to tip so bootstrap.py is guaranteed to exist.
        Step 2 — run bootstrap.py --no-interactive which installs all system
                  deps (clang, Rust, Node, cbindgen, nasm) AND creates the
                  mach virtualenv in ~/.mozbuild/.
        Since ~/.mozbuild/ is machine-wide, the second+ workers to start
        will find everything already installed and bootstrap will exit quickly.
        """
        # Step 1 — update to tip first, always, before any file checks
        print(f"[startup] Updating worker {self.worker_id} to tip before bootstrap...")
        tip_result = subprocess.run(
            ["hg", "update", "-r", "tip", "--clean"],
            cwd=self.mozilla_central, capture_output=True, text=True, env=self.env
        )
        if tip_result.returncode == 0:
            print(f"[startup] ✓ updated to tip")
        else:
            print(f"[startup] WARNING: hg update to tip failed — {tip_result.stderr.strip()}")
            print(f"[startup] continuing anyway with current commit")

        # Step 2 — skip bootstrap if ~/.mozbuild/clang already exists
        # (means worker 1 already ran it — ~/.mozbuild/ is machine-wide)
        clang = Path.home() / ".mozbuild" / "clang" / "bin" / "clang"
        if clang.exists():
            print(f"[startup] ✓ ~/.mozbuild/clang found — bootstrap already done, skipping")
            return

        bootstrap_py = self.mozilla_central / "python" / "mozboot" / "bin" / "bootstrap.py"
        if not bootstrap_py.exists():
            print(f"[startup] WARNING: bootstrap.py not found at {bootstrap_py} — skipping")
            return

        print(f"[startup] Running bootstrap.py --no-interactive for worker {self.worker_id}...")
        result = subprocess.run(
            ["python3", str(bootstrap_py), "--no-interactive"],
            cwd=self.mozilla_central, capture_output=True, text=True,
            timeout=1800, env=self.env,
        )
        if result.returncode == 0:
            print(f"[startup] ✓ bootstrap completed for worker {self.worker_id}")
        else:
            print(f"[startup] WARNING: bootstrap rc={result.returncode} — continuing anyway")
            print(f"[startup] stderr: {result.stderr[-300:]}")

    def run(self):
        print("=" * 80)
        print(f"MULTI FIXING COMMIT FULL BUILD RUNNER — WORKER {self.worker_id}")
        print(f"Mode: Python 3 only — failed commits saved to needs_python2/")
        print("=" * 80 + "\n")

        # Ensure system deps and mach virtualenv are present
        self._ensure_build_environment()

        all_bugs = load_all_bugs(self.input_base)
        if not all_bugs:
            print("No bugs to process.")
            return

        bugs  = get_worker_bugs(all_bugs, self.worker_id, NUM_WORKERS)
        total = len(bugs)

        original_commit = hg_current(self.mozilla_central, self.env)
        print(f"Current commit (will restore at end): {original_commit[:12]}\n")

        stats = {
            "total_bugs": total,
            "bugs_pass": 0, "bugs_fail": 0, "bugs_partial": 0,
            "bugs_skipped": 0, "bugs_not_validated_skipped": 0,
            "bugs_needs_python2": 0,
            "total_commits_attempted": 0, "total_commits_built": 0,
            "total_commits_python2": 0, "total_commits_failed_genuine": 0,
            "total_tests_run": 0, "total_tests_pass": 0,
            "total_tests_fail": 0, "total_tests_skipped": 0,
        }
        all_results = {}

        for idx, bug in enumerate(bugs, 1):
            bug_id = bug.get("bug_id", "unknown")
            try:
                result = self.process_bug(bug, idx, total)
                all_results[bug_id] = result

                status = result["overall_status"]
                stats[f"bugs_{status}"] = stats.get(f"bugs_{status}", 0) + 1
                if result.get("has_python2_commits"):
                    stats["bugs_needs_python2"] += 1

                for cr in result.get("commits", []):
                    stats["total_commits_attempted"] += 1
                    if cr["build_ok"]:
                        stats["total_commits_built"] += 1
                    elif cr.get("build_category") == "python2_env":
                        stats["total_commits_python2"] += 1
                    else:
                        stats["total_commits_failed_genuine"] += 1
                    for t in cr.get("tests", []):
                        if t["result"] == "pass":
                            stats["total_tests_pass"] += 1
                            stats["total_tests_run"]  += 1
                        elif t["result"] == "skipped":
                            stats["total_tests_skipped"] += 1
                        else:
                            stats["total_tests_fail"] += 1
                            stats["total_tests_run"]  += 1

            except Exception as e:
                print(f"  ERROR processing bug {bug_id}: {e}", flush=True)
                all_results[bug_id] = {"status": "error", "error": str(e)}

        print(f"\nRestoring {self.mozilla_central.name} to {original_commit[:12]}...")
        hg_update(original_commit, self.mozilla_central, self.env)

        self._print_summary(stats)
        self.writer.save_pipeline_summary(stats, all_results)
        self.writer.save_statistics_report(stats, all_results)

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 80)
        print(f"WORKER {self.worker_id} — FINAL SUMMARY")
        print("=" * 80)
        print(f"  Total bugs processed               : {s['total_bugs']}")
        print(f"  ── Build results ───────────────────────────────────────")
        print(f"  Commits built successfully         : {s['total_commits_built']}")
        print(f"  Commits failed (python2 env)       : {s['total_commits_python2']}")
        print(f"  Commits failed (genuine error)     : {s['total_commits_failed_genuine']}")
        print(f"  ── Test validation results ─────────────────────────────")
        print(f"  Pipeline validated     (pass)      : {s['bugs_pass']}")
        print(f"  Not validated          (fail)      : {s['bugs_fail']}")
        print(f"  Partial builds                     : {s['bugs_partial']}")
        print(f"  All builds failed      (skipped)   : {s['bugs_skipped']}")
        print(f"  All tests skipped                  : {s['bugs_not_validated_skipped']}")
        print(f"  Saved for python2 retry            : {s['bugs_needs_python2']}")
        print(f"  ── Test counts ─────────────────────────────────────────")
        print(f"  Total tests run                    : {s['total_tests_run']}")
        print(f"  Tests passed                       : {s['total_tests_pass']}")
        print(f"  Tests failed                       : {s['total_tests_fail']}")
        print(f"  Tests skipped                      : {s['total_tests_skipped']}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Full build test runner (Python 3 mode) — failed python2 commits saved for retry"
    )
    parser.add_argument(
        "--worker", type=int, required=True, choices=range(1, NUM_WORKERS + 1),
        help=f"Worker ID (1-{NUM_WORKERS})"
    )
    args   = parser.parse_args()
    pipeline = FullBuildRunnerPipeline(worker_id=args.worker)
    pipeline.run()
    print("\n" + "=" * 80)
    print(f"✓  WORKER {args.worker} COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
