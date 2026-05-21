#!/usr/bin/env python3
"""
================================================================================
PYTHON 3 RETRY RUNNER
================================================================================

PURPOSE:
--------
Retry building and testing bugs that failed in the initial Python 3 run.
Reads from classification/needs_python3/ which contains bugs that failed due to:
  - OOM kills          (now fixed: 31 GiB RAM + -j4)
  - Rust pin conflicts (now fixed: RUSTUP_TOOLCHAIN=stable)
  - configure failures
  - cbindgen TOML issues
  - pipeline errors
  - genuine build failures

For each bug:
  1. Build fixing commit  (expect PASS)
  2. Build parent commit  (expect FAIL)
  3. Run tests on both commits
  4. Save results to python3_full_build/

FIXES APPLIED vs INITIAL RUN:
------------------------------
  - RUSTUP_TOOLCHAIN=stable  → overrides rust-toolchain.toml pin
  - MOZ_MAKE_FLAGS=-j4       → reduces parallel jobs to avoid OOM
  - 31 GiB RAM instance      → eliminates OOM kills
  - No bootstrap step        → already done, ~/.mozbuild exists

USAGE:
------
  python3 python3_retry_runner.py --worker 1

INPUT:
------
  outputs/multi_fixing_commit_full_build/classification/needs_python3/
  └── bug_<ID>/classification.json

OUTPUT:
-------
  outputs/python3_full_build/
  └── worker_<N>/
      ├── successful_builds/
      │   ├── pipeline_validated/
      │   │   └── bug_<ID>/test_results.json
      │   ├── pipeline_not_validated/
      │   │   └── bug_<ID>/test_results.json
      │   └── bug_<ID>/test_results.json
      ├── failed_builds/
      │   └── bug_<ID>/build_failure.json
      ├── pipeline_summary.json
      └── statistics_report.txt
"""

import argparse
import json
import os
import shutil
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

INPUT_DIR  = "bugzilla_bugs_analysis/outputs/multi_fixing_commit_full_build/classification/needs_python3"
OUTPUT_DIR = "bugzilla_bugs_analysis/outputs/python3_full_build"

WORKER_REPOS = {
    1: Path("/data/FaultLocalizationIndustry/mozilla-central"),
    2: Path("/data/FaultLocalizationIndustry/mozilla-central-worker2"),
}
NUM_WORKERS = 1

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
            "mk_add_options AUTOCLOBBER=1\n"
            "mk_add_options MOZ_MAKE_FLAGS=-j4\n",
            encoding="utf-8"
        )
        print("  [mozconfig] created ✓")
    else:
        content = mozconfig.read_text(encoding="utf-8")
        print(f"  [mozconfig] found existing mozconfig:\n{content}")

    env = os.environ.copy()
    env["MOZCONFIG"] = str(mozconfig)
    env["RUSTUP_TOOLCHAIN"] = "stable"   # override rust-toolchain.toml pin
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
# MACH BUILD
# ===========================================================================

def mach_build(commit_hash: str, mozilla_central: Path, env: Dict) -> Tuple[bool, str]:
    """
    Run ./mach build.
    Returns (success, failure_reason)
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
            return True, ""

        output_lines = output.strip().splitlines()
        last_lines   = output_lines[-20:] if len(output_lines) > 20 else output_lines
        last_line    = output_lines[-1] if output_lines else f"rc={result.returncode}"

        print(f"    [mach build] ✗ BUILD FAILED ({duration}s)")
        print(f"    [mach build] last output:\n" + "\n".join(last_lines))
        return False, last_line

    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 2)
        print(f"    [mach build] ✗ BUILD TIMEOUT after {BUILD_TIMEOUT}s")
        return False, f"build timed out after {BUILD_TIMEOUT}s"
    except Exception as e:
        print(f"    [mach build] ✗ BUILD ERROR: {e}")
        return False, str(e)


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
    combined = stdout + stderr
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
        has_crash        = "REFTEST PROCESS-CRASH" in stdout
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

def load_needs_python3_bugs(input_base: Path) -> List[Dict]:
    """
    Load all bugs from classification/needs_python3/.
    Each classification.json contains the original_bug with full commit pair info.
    """
    bugs = []
    seen_ids = set()
    for bug_dir in sorted(input_base.iterdir()):
        if not bug_dir.is_dir() or not bug_dir.name.startswith("bug_"):
            continue
        f = bug_dir / "classification.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            bug_id = data.get("bug_id", "unknown")
            if bug_id in seen_ids:
                continue
            seen_ids.add(bug_id)
            # Extract original_bug which has commit_pairs and test_files
            original = data.get("original_bug", {})
            if not original:
                print(f"  WARNING: no original_bug in {f} — skipping")
                continue
            # Attach classification metadata for reporting
            original["_python3_category"] = data.get("summary", {}).get("python3_categories", [])
            original["_retry_with"]       = data.get("retry_with", "")
            bugs.append(original)
        except Exception as e:
            print(f"  WARNING: could not read {f}: {e}")
    print(f"Loaded {len(bugs)} bugs from needs_python3/\n")
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

    def __init__(self, base_dir: Path, worker_id: int):
        self.base = base_dir / f"worker_{worker_id}"
        self.base.mkdir(parents=True, exist_ok=True)
        for subdir in [
            self.SUBDIR_SUCCESS,
            self.SUBDIR_VALIDATED,
            self.SUBDIR_NOT_VALIDATED,
            self.SUBDIR_FAILED,
        ]:
            (self.base / subdir).mkdir(parents=True, exist_ok=True)

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

    def save_statistics_report(self, stats: Dict, results: Dict):
        rp    = self.base / "statistics_report.txt"
        lines = [
            "=" * 80,
            "PYTHON 3 RETRY RUNNER — STATISTICS REPORT",
            "=" * 80,
            f"Generated: {datetime.now().isoformat()}",
            "",
            f"Total bugs processed               : {stats['total_bugs']}",
            f"Pipeline validated (pass)          : {stats['bugs_pass']}",
            f"Not validated (fail)               : {stats['bugs_fail']}",
            f"Partial (some builds failed)       : {stats['bugs_partial']}",
            f"Skipped (all builds failed)        : {stats['bugs_skipped']}",
            f"Not validated (tests skipped)      : {stats['bugs_not_validated_skipped']}",
            f"Total commits attempted            : {stats['total_commits_attempted']}",
            f"Commits built successfully         : {stats['total_commits_built']}",
            f"Commits failed                     : {stats['total_commits_failed']}",
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
                status_str  = r.get('overall_status', '?').upper()
                py3_cats    = r.get('python3_categories', [])
                lines.append(f"Bug {bid}  [{status_str}]  frameworks={r.get('framework_group','?')}  py3_cats={py3_cats}")
                for c in r.get("commits", []):
                    build_str = "built" if c["build_ok"] else "FAILED"
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

    def save_pipeline_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "runner": "python3_retry_runner",
            "statistics": stats,
            "per_bug": {
                bid: {
                    "overall_status":    r.get("overall_status"),
                    "framework_group":   r.get("framework_group"),
                    "python3_categories": r.get("python3_categories", []),
                    "commits": [
                        {
                            "commit_hash": c["commit_hash"],
                            "role":        c["role"],
                            "expected":    c["expected"],
                            "build_ok":    c["build_ok"],
                            "test_count":  len(c.get("tests", [])),
                            "tests_pass":  sum(1 for t in c.get("tests", []) if t["result"] == "pass"),
                            "tests_fail":  sum(1 for t in c.get("tests", []) if t["result"] not in ("pass", "skipped")),
                        }
                        for c in r.get("commits", [])
                    ]
                }
                for bid, r in results.items() if "error" not in r
            }
        }
        sp = self.base / "pipeline_summary.json"
        sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"✓ pipeline_summary.json → {sp}")


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class Python3RetryPipeline:

    def __init__(self, worker_id: int):
        self.worker_id       = worker_id
        self.mozilla_central = WORKER_REPOS[worker_id]
        self.input_base      = Path(parent_dir) / INPUT_DIR
        self.output_base     = Path(parent_dir) / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)
        self.env             = ensure_mozconfig(self.mozilla_central)
        self.writer          = OutputWriter(self.output_base, worker_id)

        print(f"Worker:          {worker_id} of {NUM_WORKERS}")
        print(f"Mozilla-central: {self.mozilla_central}")
        print(f"Input:           {self.input_base}")
        print(f"Output:          {self.output_base / f'worker_{worker_id}'}")
        print(f"RUSTUP_TOOLCHAIN: {self.env.get('RUSTUP_TOOLCHAIN', 'NOT SET')}\n")

    def process_bug(self, bug: Dict, idx: int, total: int) -> Dict:
        bug_id        = bug["bug_id"]
        framework_grp = bug.get("framework_group", "unknown")
        commit_pairs  = bug.get("commit_pairs", [])
        py3_cats      = bug.get("_python3_category", [])

        print(f"\n{'='*60}", flush=True)
        print(f"[Worker {self.worker_id}] [{idx}/{total}] Bug {bug_id}  frameworks={framework_grp}  py3_cats={py3_cats}", flush=True)
        print(f"{'='*60}", flush=True)

        commit_results = []
        seen_commits   = set()

        for pair in commit_pairs:
            fixing_commit = pair["fixing_commit"]
            parent_commit = pair["parent_commit"]
            test_files    = pair.get("runnable_test_files") or pair.get("test_files", [])

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

                # Step 1 — hg update
                update_ok, update_err = hg_update(commit_hash, self.mozilla_central, self.env)
                if not update_ok:
                    print(f"  ✗ hg update FAILED for {commit_hash[:12]}", flush=True)
                    self.writer.save_build_failure(bug_id, commit_hash, role, update_err, "hg_update")
                    commit_results.append({
                        "commit_hash": commit_hash, "role": role, "expected": expected,
                        "build_ok": False, "build_error": update_err,
                        "failed_step": "hg_update", "tests": [],
                    })
                    continue

                # Step 2 — mach build
                build_ok, build_err = mach_build(commit_hash, self.mozilla_central, self.env)

                if not build_ok:
                    print(f"  ✗ BUILD FAILED for {commit_hash[:12]}", flush=True)
                    self.writer.save_build_failure(bug_id, commit_hash, role, build_err, "mach_build")
                    commit_results.append({
                        "commit_hash": commit_hash, "role": role, "expected": expected,
                        "build_ok": False, "build_error": build_err,
                        "failed_step": "mach_build", "tests": [],
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
                    "failed_step": None, "tests": test_results,
                })

                # Step 4 — clean up objdir
                objdir = self.mozilla_central / "objdir-fullBuild"
                if objdir.exists():
                    print(f"  [cleanup] removing objdir to reclaim disk space...")
                    shutil.rmtree(objdir, ignore_errors=True)
                    print(f"  [cleanup] objdir removed ✓")

        overall    = compute_overall_status(commit_results)
        bug_result = {
            "bug_id":            bug_id,
            "framework_group":   framework_grp,
            "overall_status":    overall,
            "worker_id":         self.worker_id,
            "python3_categories": py3_cats,
            "commits":           commit_results,
            "recorded_at":       datetime.now().isoformat(),
        }

        if any(r["build_ok"] for r in commit_results):
            self.writer.save_bug_result(bug_id, bug_result)

        print(
            f"\n  → Bug {bug_id}  status={overall.upper()}  "
            f"built={sum(1 for r in commit_results if r['build_ok'])}/{len(commit_results)} commits",
            flush=True
        )
        return bug_result

    def run(self):
        print("=" * 80)
        print(f"PYTHON 3 RETRY RUNNER — WORKER {self.worker_id}")
        print(f"Fixes applied: RUSTUP_TOOLCHAIN=stable, MOZ_MAKE_FLAGS=-j4, 31 GiB RAM")
        print("=" * 80 + "\n")

        bugs  = load_needs_python3_bugs(self.input_base)
        if not bugs:
            print("No bugs to process.")
            return

        worker_bugs = get_worker_bugs(bugs, self.worker_id, NUM_WORKERS)
        total       = len(worker_bugs)

        original_commit = hg_current(self.mozilla_central, self.env)
        print(f"Current commit (will restore at end): {original_commit[:12]}\n")

        stats = {
            "total_bugs": total,
            "bugs_pass": 0, "bugs_fail": 0, "bugs_partial": 0,
            "bugs_skipped": 0, "bugs_not_validated_skipped": 0,
            "total_commits_attempted": 0, "total_commits_built": 0,
            "total_commits_failed": 0,
            "total_tests_run": 0, "total_tests_pass": 0,
            "total_tests_fail": 0, "total_tests_skipped": 0,
        }
        all_results = {}

        for idx, bug in enumerate(worker_bugs, 1):
            bug_id = bug.get("bug_id", "unknown")
            try:
                result = self.process_bug(bug, idx, total)
                all_results[bug_id] = result

                status = result["overall_status"]
                stats[f"bugs_{status}"] = stats.get(f"bugs_{status}", 0) + 1

                for cr in result.get("commits", []):
                    stats["total_commits_attempted"] += 1
                    if cr["build_ok"]:
                        stats["total_commits_built"] += 1
                    else:
                        stats["total_commits_failed"] += 1
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
        print(f"  Pipeline validated     (pass)      : {s['bugs_pass']}")
        print(f"  Not validated          (fail)      : {s['bugs_fail']}")
        print(f"  Partial builds                     : {s['bugs_partial']}")
        print(f"  All builds failed      (skipped)   : {s['bugs_skipped']}")
        print(f"  All tests skipped                  : {s['bugs_not_validated_skipped']}")
        print(f"  Commits built successfully         : {s['total_commits_built']}")
        print(f"  Commits failed                     : {s['total_commits_failed']}")
        print(f"  Total tests run                    : {s['total_tests_run']}")
        print(f"  Tests passed                       : {s['total_tests_pass']}")
        print(f"  Tests failed                       : {s['total_tests_fail']}")
        print(f"  Tests skipped                      : {s['total_tests_skipped']}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Python 3 retry runner — builds and tests bugs from needs_python3/"
    )
    parser.add_argument(
        "--worker", type=int, required=True, choices=range(1, NUM_WORKERS + 1),
        help=f"Worker ID (1-{NUM_WORKERS})"
    )
    args     = parser.parse_args()
    pipeline = Python3RetryPipeline(worker_id=args.worker)
    pipeline.run()
    print("\n" + "=" * 80)
    print(f"✓  WORKER {args.worker} COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
