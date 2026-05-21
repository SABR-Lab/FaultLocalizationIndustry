#!/usr/bin/env python3
"""
================================================================================
MULTI FIXING COMMIT BUILD TEST RUNNER
================================================================================

PURPOSE:
--------
For each bug from the fixing_commit_parent output:
  1. Save modern mach files from tip (Python 3 compatible)
  2. For the FIXING commit:
     - hg update → restore modern mach → ./mach build → run tests --headless
     - Expect PASS (fix is applied)
  3. For the PARENT commit:
     - hg update → restore modern mach → patch in ALL files from fixing commit
     - ./mach build → run tests --headless → remove patched files
     - Expect FAIL (bug still present)
  4. Save one result JSON per bug

INPUT:
------
outputs/fixing_commit_parent/bugs/
└── bug_<ID>/
    └── <fixing_commit>/
        └── <parent_commit>/
            └── parent_info.json

OUTPUT STRUCTURE:
-----------------
outputs/multi_fixing_commit_build_test_runner/
├── bugs_with_all_prebuilt_binaries/
│   ├── pipeline_validated/
│   │   └── bug_<ID>/test_results.json   ← fixing PASS + parent FAIL (genuine)
│   ├── pipeline_not_validated/
│   │   └── bug_<ID>/test_results.json   ← ran but didn't validate
│   ├── bug_<ID>/test_results.json       ← all bugs also here
│   └── all_prebuilt_binaries_summary.json
├── bugs_with_partial_prebuilt_binaries/
│   ├── bug_<ID>/test_results.json
│   └── partial_prebuilt_binaries_summary.json
├── bugs_with_no_prebuilt_binaries/
│   ├── bug_<ID>/test_results.json
│   └── no_prebuilt_binaries_summary.json
├── pipeline_summary.json
└── statistics_report.txt
"""

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

INPUT_DIR       = "fixing_commit_parent"
OUTPUT_DIR      = "multi_fixing_commit_build_test_runner"
MOZILLA_CENTRAL = Path("/data/FaultLocalizationIndustry/mozilla-central")
MOZCONFIG       = MOZILLA_CENTRAL / "mozconfig"

MODERN_MACH           = Path("/tmp/mach_modern")
MODERN_MACH_BOOTSTRAP = Path("/tmp/mach_bootstrap_modern.py")

TEST_TIMEOUT     = 1800  # 30 minutes per test
ARTIFACT_TIMEOUT = 900   # 15 minutes for ./mach build

# Extensions that are manifest/support files — patch but don't run
NON_RUNNABLE_EXTENSIONS = {
    ".ini", ".toml", ".mjs", ".sjs", ".list",
    ".json", ".yaml", ".yml",
}

# Extensions that can never run on this Linux desktop environment
UNSUPPORTED_EXTENSIONS = {
    ".kt",   # Android/Kotlin — needs Android device
    ".java", # Android Java — needs Android device
    ".h",    # C++ headers — not runnable directly
}

# Frameworks that cannot run with artifact builds on this machine
UNSUPPORTED_FRAMEWORKS = {
    "jit-test",  # needs SpiderMonkey js shell — not in artifact builds
}

# Path patterns that indicate unsupported test types
UNSUPPORTED_PATH_PATTERNS = [
    "mobile/android/",  # Android tests
    "geckoview/",       # GeckoView Android tests
    "/gtest/",          # GTest C++ tests — need ./mach gtest <TestName>
    "/marionette/",     # Marionette — need ./mach marionette-test
]

# Crash signals — universal across all test frameworks
# If any of these appear in the output, the test crashed
CRASH_SIGNALS = [
    "Application shut down (without crashing) in the middle of a test",  # mochitest
    "MOZ_DIAGNOSTIC_ASSERT",      # all frameworks — internal Firefox assertion
    "REFTEST PROCESS-CRASH",      # crashtest/reftest — Firefox crashed loading page
    "TEST_END: CRASH",            # WPT — test ended with a crash
    "TEST_END: Test CRASH",       # mochitest-plain — test itself crashed
]


# ===========================================================================
# MOZCONFIG GUARD
# ===========================================================================

def ensure_mozconfig() -> Dict:
    if not MOZCONFIG.exists():
        print("  [mozconfig] creating mozconfig with artifact builds enabled")
        MOZCONFIG.write_text(
            "ac_add_options --enable-artifact-builds\n"
            "mk_add_options MOZ_OBJDIR=./objdir-artifact\n"
            "mk_add_options AUTOCLOBBER=1\n",
            encoding="utf-8"
        )
    env = os.environ.copy()
    env["MOZCONFIG"] = str(MOZCONFIG)
    return env


# ===========================================================================
# MODERN MACH PRESERVATION
# ===========================================================================

def save_modern_mach(env: Dict):
    """
    While on tip, copy mach and mach_bootstrap.py to /tmp.
    These are Python 3 compatible. We restore them after every hg update
    to prevent old commits from overwriting them with Python 2 only versions.
    """
    mach_src      = MOZILLA_CENTRAL / "mach"
    bootstrap_src = MOZILLA_CENTRAL / "build" / "mach_bootstrap.py"

    import time as _time
    _time.sleep(1)

    if not mach_src.exists():
        print(f"  [mach-save] WARNING: {mach_src} not found")
        return
    if not bootstrap_src.exists():
        print(f"  [mach-save] WARNING: {bootstrap_src} not found")
        return

    shutil.copy2(mach_src, MODERN_MACH)
    shutil.copy2(bootstrap_src, MODERN_MACH_BOOTSTRAP)
    print(f"  [mach-save] saved modern mach files to /tmp ✓")
    print(f"  [mach-save]   mach:           {MODERN_MACH}")
    print(f"  [mach-save]   mach_bootstrap: {MODERN_MACH_BOOTSTRAP}")


def restore_modern_mach():
    """
    Copy the saved modern mach files back into mozilla-central,
    overwriting whatever old version hg update put there.
    """
    if not MODERN_MACH.exists() or not MODERN_MACH_BOOTSTRAP.exists():
        print("  [mach-restore] WARNING: no saved modern mach files found")
        return

    mach_dst      = MOZILLA_CENTRAL / "mach"
    bootstrap_dst = MOZILLA_CENTRAL / "build" / "mach_bootstrap.py"

    shutil.copy2(MODERN_MACH, mach_dst)
    shutil.copy2(MODERN_MACH_BOOTSTRAP, bootstrap_dst)
    mach_dst.chmod(0o755)
    print(f"  [mach-restore] restored modern mach files ✓")


def warmup_mach(env: Dict):
    """
    Update to tip, save modern mach files, and run ./mach build
    to confirm mach works with Python 3 before processing historical commits.
    """
    print("\n[warmup] updating to tip...")
    result = subprocess.run(
        ["hg", "update", "-r", "tip", "--clean"],
        cwd=MOZILLA_CENTRAL, capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        print(f"  [warmup] WARNING: hg update to tip failed: {result.stderr.strip()}")
        return

    save_modern_mach(env)

    print("  [warmup] confirming mach works at tip...")
    result = subprocess.run(
        f"cd {MOZILLA_CENTRAL} && ./mach build",
        shell=True, capture_output=True, text=True,
        timeout=ARTIFACT_TIMEOUT, env=env
    )
    if result.returncode == 0:
        print("  [warmup] mach environment ready ✓\n")
    else:
        print(f"  [warmup] WARNING: mach warmup failed (rc={result.returncode})\n")


# ===========================================================================
# MERCURIAL HELPERS
# ===========================================================================

def hg_current(env: Dict) -> str:
    result = subprocess.run(
        ["hg", "log", "-r", ".", "--template", "{node}"],
        cwd=MOZILLA_CENTRAL, capture_output=True, text=True, env=env
    )
    return result.stdout.strip()


def hg_update(commit_hash: str, env: Dict) -> bool:
    print(f"    [hg update] → {commit_hash[:12]}")
    result = subprocess.run(
        ["hg", "update", "-r", commit_hash, "--clean"],
        cwd=MOZILLA_CENTRAL, capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        print(f"    [hg update] FAILED: {result.stderr.strip()}")
        return False
    print(f"    [hg update] ok — {result.stdout.strip()}")
    restore_modern_mach()
    return True


# ===========================================================================
# MACH BUILD
# ===========================================================================

def mach_build(commit_hash: str, env: Dict) -> Tuple[bool, str]:
    """
    Run ./mach build for the current commit.
    Uses artifact builds — downloads pre-built C++ binary and sets up objdir.
    Falls back to need_full_build if no artifact is available.
    """
    print(f"    [mach build] running...")
    try:
        result = subprocess.run(
            f"cd {MOZILLA_CENTRAL} && ./mach build",
            shell=True, capture_output=True, text=True,
            timeout=ARTIFACT_TIMEOUT, env=env,
        )
        output = result.stdout + result.stderr

        if "No candidate" in output or "Unable to find" in output or "no built artifacts found" in output.lower():
            print(f"    [mach build] no artifacts found — need_full_build")
            return False, "no candidate artifacts found"

        if result.returncode != 0:
            print(f"    [mach build] failed (rc={result.returncode})")
            return False, f"mach build exited with code {result.returncode}"

        if "Your build was successful" in output:
            print(f"    [mach build] build successful ✓")
            return True, ""

        print(f"    [mach build] ok (rc=0) ✓")
        return True, ""

    except subprocess.TimeoutExpired:
        print(f"    [mach build] timed out after {ARTIFACT_TIMEOUT}s")
        return False, "mach build timed out"
    except Exception as e:
        print(f"    [mach build] error: {e}")
        return False, str(e)


# ===========================================================================
# TEST FILE PATCHING  (for parent commit)
# ===========================================================================

def patch_test_files(test_files: List[Dict], fixing_commit: str, env: Dict) -> List[Path]:
    """
    For the parent commit run, read ALL test files (runnable and non-runnable)
    at the fixing commit using 'hg cat -r <fixing_commit>' and write them into
    the current working directory (checked out at the parent commit).

    Patching everything — including .toml, .ini, .list manifests — ensures the
    test framework can discover and run the test correctly on the parent commit.

    Returns list of patched file paths so we can clean them up afterward.
    """
    patched = []
    for tf in test_files:
        filepath = tf.get("filepath", "")
        if not filepath:
            continue

        dest = MOZILLA_CENTRAL / filepath

        result = subprocess.run(
            ["hg", "cat", "-r", fixing_commit, filepath],
            cwd=MOZILLA_CENTRAL, capture_output=True, env=env
        )
        if result.returncode != 0:
            print(f"      [patch] WARNING: could not get {filepath} at {fixing_commit[:12]}")
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(result.stdout)
        patched.append(dest)
        print(f"      [patch] patched {filepath} from fixing commit {fixing_commit[:12]}")

    return patched


def remove_patched_files(patched: List[Path]):
    """Remove test files that were patched in for the parent commit run."""
    for p in patched:
        try:
            p.unlink()
            print(f"      [unpatch] removed {p.name}")
        except Exception as e:
            print(f"      [unpatch] WARNING: could not remove {p}: {e}")


# ===========================================================================
# RESULT CLASSIFICATION
# ===========================================================================

def extract_unexpected_count(stdout: str) -> Optional[int]:
    """
    Extract the LAST Unexpected results count from test output.
    Uses last occurrence because WPT prints per-test then overall counts.

    Handles all framework formats:
      mochitest/xpcshell/wpt: "Unexpected results: N"
      crashtest/reftest:      "Unexpected: N (0 unexpected fail...)"
    """
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


def classify_failure_reason(stdout: str, stderr: str, returncode: int) -> str:
    """
    Classify why a test failed — genuine bug-related vs technical/infrastructure.
    Used only for reporting purposes, not for pass/fail decision.

    Returns:
      "genuine"   — test explicitly failed due to bug-related assertion
      "technical" — infrastructure/environment issue
      "unknown"   — cannot determine
    """
    combined = stdout + stderr

    # Explicit unexpected count takes highest priority
    unexpected = extract_unexpected_count(combined)
    if unexpected is not None:
        return "genuine"  # whether 0 or >0, test ran and reported

    technical_signals = [
        "Application shut down (without crashing) in the middle of a test",
        "AbortError: Actor 'SpecialPowers' destroyed",
        "No such schema",
        "UNKNOWN TEST",
        "shell is not executable",
        "I can't run those tests yet",
        "unable to find tests from the given",
        "could not find any mochitests",
        "Error: No tests to run",
        "Ran 0 checks",
        "timed out after",
        "NS_ERROR_ABORT",
        "Exiting due to channel error",
        "no DISPLAY environment variable",
        "TestManifestBackend is out of date",
    ]
    for signal in technical_signals:
        if signal in combined:
            return "technical"

    genuine_signals = [
        "TEST-FAIL", "TEST-UNEXPECTED-FAIL",
        "expected PASS", "got FAIL",
        "AssertionError", "assertion failed",
        "MOZ_DIAGNOSTIC_ASSERT",
    ]
    for signal in genuine_signals:
        if signal in combined:
            return "genuine"

    return "unknown"


def parse_test_result(returncode: int, stdout: str, role: str = "fixing", filepath: str = "") -> str:
    """
    Universal pass/fail determination for ALL test frameworks.

    FIXING COMMIT — ALL three must be true for pass:
      1. No crash signals in output
      2. Final "Unexpected results: N" = 0  (last occurrence)
      3. Output ends with "OK"

    PARENT COMMIT — ANY one means fail (bug is present):
      1. Any crash signal in output
      2. Final "Unexpected results: N" > 0
      3. Output ends with "FAILED"
      4. rc != 0

    Crash signals (universal across all frameworks):
      - "Application shut down ... in the middle of a test"  → mochitest
      - "MOZ_DIAGNOSTIC_ASSERT"                              → all frameworks
      - "REFTEST PROCESS-CRASH"                              → crashtest/reftest
      - "TEST_END: CRASH"                                    → WPT
      - "TEST_END: Test CRASH"                               → mochitest-plain
    """
    has_crash        = any(signal in stdout for signal in CRASH_SIGNALS)
    final_unexpected = extract_unexpected_count(stdout)
    ends_with_ok     = "OK\n" in stdout or stdout.strip().endswith("OK")
    ends_with_failed = "FAILED\n" in stdout or stdout.strip().endswith("FAILED")

    # ----------------------------------------------------------------
    # PARENT COMMIT — any failure = bug is present = expected
    # ----------------------------------------------------------------
    if role == "parent":
        if has_crash:
            return "fail"
        if final_unexpected is not None and final_unexpected > 0:
            return "fail"
        if ends_with_failed:
            return "fail"
        if returncode != 0:
            return "fail"
        return "pass"

    # ----------------------------------------------------------------
    # FIXING COMMIT — all three conditions must be true for pass
    # ----------------------------------------------------------------
    if has_crash:
        return "fail"
    if final_unexpected is None or final_unexpected > 0:
        return "fail"
    if not ends_with_ok:
        return "fail"
    return "pass"


# ===========================================================================
# TEST RUNNER
# ===========================================================================

def is_runnable(tf: Dict) -> Tuple[bool, str]:
    """
    Skip based on file extension, framework, or path pattern.
    Never skip based on 'unknown' framework alone.
    """
    filepath  = tf.get("filepath", "")
    framework = tf.get("framework", "unknown")
    ext       = Path(filepath).suffix.lower()

    if ext in NON_RUNNABLE_EXTENSIONS:
        return False, f"non-runnable extension ({ext})"
    if ext in UNSUPPORTED_EXTENSIONS:
        return False, f"unsupported extension on this platform ({ext})"
    if framework in UNSUPPORTED_FRAMEWORKS:
        return False, f"unsupported framework ({framework}) — needs full build"
    for pattern in UNSUPPORTED_PATH_PATTERNS:
        if pattern in filepath:
            return False, f"unsupported path pattern ({pattern})"
    if not tf.get("mach_command", ""):
        return False, "no mach_command available"

    return True, ""


def kill_leftover_firefox(env: Dict):
    """Kill any leftover Firefox processes from a previous crashed test run."""
    subprocess.run(
        "pkill -f 'firefox' || true",
        shell=True, capture_output=True, env=env
    )
    time.sleep(2)


def run_test(mach_cmd: str, filepath: str, env: Dict, role: str = "fixing") -> Dict:
    """
    Run a single test with --headless flag.
    Inserts --headless after the subcommand so Firefox runs without a display.
    Kills leftover Firefox processes before running to avoid interference.
    """
    kill_leftover_firefox(env)

    mach       = str(MOZILLA_CENTRAL / "mach")
    actual_cmd = mach_cmd.replace("./mach", mach, 1)

    parts = actual_cmd.split(" ", 2)
    if len(parts) >= 2:
        actual_cmd = f"{parts[0]} {parts[1]} --headless {parts[2] if len(parts) > 2 else ''}".strip()

    print(f"      [test] {actual_cmd}")
    start = time.time()
    try:
        result = subprocess.run(
            f"cd {MOZILLA_CENTRAL} && {actual_cmd}",
            shell=True, capture_output=True, text=True,
            timeout=TEST_TIMEOUT, env=env,
        )
        duration       = round(time.time() - start, 2)
        status         = parse_test_result(result.returncode, result.stdout, role, filepath)
        failure_reason = (
            classify_failure_reason(result.stdout, result.stderr, result.returncode)
            if status != "pass" else "n/a"
        )
        print(f"      [test] {status.upper()} (rc={result.returncode}, {duration}s, reason={failure_reason})")
        return {
            "filepath":         filepath,
            "mach_command":     mach_cmd,
            "result":           status,
            "failure_reason":   failure_reason,
            "returncode":       result.returncode,
            "duration_seconds": duration,
            "stdout_tail":      result.stdout[-2000:] if result.stdout else "",
            "stderr_tail":      result.stderr[-2000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 2)
        kill_leftover_firefox(env)
        print(f"      [test] TIMEOUT after {TEST_TIMEOUT}s")
        return {
            "filepath":         filepath,
            "mach_command":     mach_cmd,
            "result":           "timeout",
            "failure_reason":   "technical",
            "returncode":       -1,
            "duration_seconds": duration,
            "stdout_tail":      "",
            "stderr_tail":      f"timed out after {TEST_TIMEOUT}s",
        }
    except Exception as e:
        print(f"      [test] ERROR: {e}")
        return {
            "filepath":         filepath,
            "mach_command":     mach_cmd,
            "result":           "error",
            "failure_reason":   "technical",
            "returncode":       -1,
            "duration_seconds": 0,
            "stdout_tail":      "",
            "stderr_tail":      str(e),
        }


def run_tests_for_commit(
    test_files:    List[Dict],
    env:           Dict,
    role:          str,
    fixing_commit: str,
) -> List[Dict]:
    """
    Run all runnable test files for a commit.
    For the parent commit, patch in ALL files from the fixing commit first
    (including manifests), then clean up afterward.
    """
    results = []
    patched = []

    if role == "parent":
        patched = patch_test_files(test_files, fixing_commit, env)

    for tf in test_files:
        filepath  = tf.get("filepath", "")
        framework = tf.get("framework", "unknown")
        mach_cmd  = tf.get("mach_command", "")

        runnable, reason = is_runnable(tf)
        if not runnable:
            print(f"      [skip] {filepath} — {reason}")
            results.append({
                "filepath":         filepath,
                "mach_command":     mach_cmd,
                "framework":        framework,
                "result":           "skipped",
                "failure_reason":   "n/a",
                "returncode":       None,
                "duration_seconds": 0,
                "stdout_tail":      "",
                "stderr_tail":      reason,
            })
            continue

        res = run_test(mach_cmd, filepath, env, role)
        res["framework"] = framework
        results.append(res)

    if role == "parent" and patched:
        remove_patched_files(patched)

    return results


# ===========================================================================
# INPUT READER
# ===========================================================================

def load_bug_commits(bug_dir: Path) -> List[Dict]:
    records = []
    seen    = set()

    for fixing_dir in sorted(bug_dir.iterdir()):
        if not fixing_dir.is_dir():
            continue
        fixing_commit = fixing_dir.name

        for parent_dir_path in sorted(fixing_dir.iterdir()):
            if not parent_dir_path.is_dir():
                continue

            info_path = parent_dir_path / "parent_info.json"
            if not info_path.exists():
                continue

            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"    Warning: could not read {info_path}: {e}")
                continue

            test_files    = info.get("test_files", [])
            parent_commit = info.get("parent_commit")

            if fixing_commit not in seen:
                seen.add(fixing_commit)
                records.append({
                    "commit_hash":   fixing_commit,
                    "role":          "fixing",
                    "expected":      "pass",
                    "fixing_commit": fixing_commit,
                    "test_files":    test_files,
                })

            if (parent_commit
                    and parent_dir_path.name != "no_parent_found"
                    and parent_commit not in seen):
                seen.add(parent_commit)
                records.append({
                    "commit_hash":   parent_commit,
                    "role":          "parent",
                    "expected":      "fail",
                    "fixing_commit": fixing_commit,
                    "test_files":    test_files,
                })

    return records


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    SUBDIR_ALL     = "bugs_with_all_prebuilt_binaries"
    SUBDIR_PARTIAL = "bugs_with_partial_prebuilt_binaries"
    SUBDIR_NONE    = "bugs_with_no_prebuilt_binaries"

    SUBDIR_VALIDATED     = "bugs_with_all_prebuilt_binaries/pipeline_validated"
    SUBDIR_NOT_VALIDATED = "bugs_with_all_prebuilt_binaries/pipeline_not_validated"

    SUMMARY_NAME_MAP = {
        "bugs_with_all_prebuilt_binaries":     "all_prebuilt_binaries_summary.json",
        "bugs_with_partial_prebuilt_binaries": "partial_prebuilt_binaries_summary.json",
        "bugs_with_no_prebuilt_binaries":      "no_prebuilt_binaries_summary.json",
    }

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)
        for subdir in [
            self.SUBDIR_ALL,
            self.SUBDIR_PARTIAL,
            self.SUBDIR_NONE,
            self.SUBDIR_VALIDATED,
            self.SUBDIR_NOT_VALIDATED,
        ]:
            (self.base / subdir).mkdir(parents=True, exist_ok=True)

    def _artifact_category(self, commit_results: List[Dict]) -> str:
        statuses     = [c["artifact_status"] for c in commit_results]
        n_downloaded = sum(1 for s in statuses if s == "downloaded")
        n_need_build = sum(1 for s in statuses if s in ("need_full_build", "error"))
        if n_downloaded == 0:
            return "no_artifacts"
        elif n_need_build == 0:
            return "all_artifacts"
        else:
            return "partial_artifacts"

    def save_bug_result(self, bug_id: str, result: Dict) -> str:
        category       = self._artifact_category(result.get("commits", []))
        overall_status = result.get("overall_status", "skipped")

        subdir_map = {
            "all_artifacts":     self.SUBDIR_ALL,
            "partial_artifacts": self.SUBDIR_PARTIAL,
            "no_artifacts":      self.SUBDIR_NONE,
        }

        bug_dir = self.base / subdir_map[category] / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        result["artifact_category"] = category
        (bug_dir / "test_results.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )

        if category == "all_artifacts":
            val_subdir = (
                self.SUBDIR_VALIDATED
                if overall_status == "pass"
                else self.SUBDIR_NOT_VALIDATED
            )
            val_dir = self.base / val_subdir / f"bug_{bug_id}"
            val_dir.mkdir(parents=True, exist_ok=True)
            (val_dir / "test_results.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )

        return category

    def write_subfolder_summaries(self, all_results: Dict):
        subdir_map = {
            "all_artifacts":     self.SUBDIR_ALL,
            "partial_artifacts": self.SUBDIR_PARTIAL,
            "no_artifacts":      self.SUBDIR_NONE,
        }
        grouped: Dict[str, List] = {
            self.SUBDIR_ALL:     [],
            self.SUBDIR_PARTIAL: [],
            self.SUBDIR_NONE:    [],
        }
        for bug_id, res in all_results.items():
            if "error" in res:
                continue
            cat        = res.get("artifact_category", "no_artifacts")
            subdir_key = subdir_map.get(cat, self.SUBDIR_NONE)
            if subdir_key in grouped:
                grouped[subdir_key].append((bug_id, res))

        for subdir_key, entries in grouped.items():
            subdir       = self.base / subdir_key
            summary_name = self.SUMMARY_NAME_MAP.get(subdir_key, "summary.json")

            total_bugs = total_commits = total_commits_downloaded = 0
            total_commits_need_build = total_tests_run = 0
            total_tests_pass = total_tests_fail = 0
            bug_entries = []

            for bug_id, res in entries:
                commits      = res.get("commits", [])
                n_commits    = len(commits)
                n_downloaded = sum(1 for c in commits if c["artifact_status"] == "downloaded")
                n_need_build = sum(1 for c in commits if c["artifact_status"] in ("need_full_build", "error"))
                n_tests      = sum(len(c.get("tests", [])) for c in commits)
                n_pass       = sum(1 for c in commits for t in c.get("tests", []) if t["result"] == "pass")
                n_fail       = sum(1 for c in commits for t in c.get("tests", []) if t["result"] not in ("pass", "skipped"))

                total_bugs               += 1
                total_commits            += n_commits
                total_commits_downloaded += n_downloaded
                total_commits_need_build += n_need_build
                total_tests_run          += n_tests
                total_tests_pass         += n_pass
                total_tests_fail         += n_fail

                bug_entries.append({
                    "bug_id":                  bug_id,
                    "overall_status":          res.get("overall_status"),
                    "total_commits":           n_commits,
                    "commits_downloaded":      n_downloaded,
                    "commits_need_full_build": n_need_build,
                    "total_tests_run":         n_tests,
                    "tests_passed":            n_pass,
                    "tests_failed":            n_fail,
                    "commits": [
                        {
                            "commit_hash":     c["commit_hash"],
                            "role":            c["role"],
                            "expected":        c["expected"],
                            "artifact_status": c["artifact_status"],
                            "tests": [
                                {
                                    "filepath":       t["filepath"],
                                    "result":         t["result"],
                                    "failure_reason": t.get("failure_reason", "n/a"),
                                    "mach_command":   t["mach_command"],
                                }
                                for t in c.get("tests", [])
                            ]
                        }
                        for c in commits
                    ]
                })

            summary = {
                "category":   subdir_key,
                "total_bugs": total_bugs,
                "aggregate": {
                    "total_commits":           total_commits,
                    "commits_downloaded":      total_commits_downloaded,
                    "commits_need_full_build": total_commits_need_build,
                    "total_tests_run":         total_tests_run,
                    "total_tests_passed":      total_tests_pass,
                    "total_tests_failed":      total_tests_fail,
                },
                "bugs": bug_entries,
            }
            (subdir / summary_name).write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            print(f"✓ {summary_name} → {subdir / summary_name}")


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class TestRunnerPipeline:

    def __init__(self):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.input_base  = self.outputs_base / INPUT_DIR / "bugs"
        self.output_base = self.outputs_base / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.writer = OutputWriter(self.output_base)
        self.env    = ensure_mozconfig()

        print("Saving modern mach files at startup...")
        save_modern_mach(self.env)

        print(f"Input:           {self.input_base}")
        print(f"Output:          {self.output_base}")
        print(f"Mozilla-central: {MOZILLA_CENTRAL}\n")

    def load_bug_dirs(self) -> List[Path]:
        if not self.input_base.exists():
            print(f"ERROR: {self.input_base} not found — run step5b first.")
            return []
        dirs = sorted(self.input_base.glob("bug_*/"))
        print(f"Found {len(dirs)} bug(s) to process\n")
        return dirs

    def process_bug(self, bug_dir: Path, idx: int, total: int) -> Dict:
        bug_id = bug_dir.name.replace("bug_", "")
        print(f"\n{'='*60}")
        print(f"[{idx}/{total}] Bug {bug_id}")
        print(f"{'='*60}")

        commit_records = load_bug_commits(bug_dir)
        if not commit_records:
            print(f"  No commit records found — skipping")
            return {
                "bug_id": bug_id, "overall_status": "skipped",
                "reason": "no parent_info.json found", "commits": [],
            }

        commit_results = []

        for rec in commit_records:
            commit_hash   = rec["commit_hash"]
            role          = rec["role"]
            expected      = rec["expected"]
            test_files    = rec["test_files"]
            fixing_commit = rec["fixing_commit"]

            print(f"\n  [{role.upper()}] {commit_hash[:12]} (expect {expected.upper()})")

            if not hg_update(commit_hash, self.env):
                commit_results.append({
                    "commit_hash": commit_hash, "role": role,
                    "expected": expected, "artifact_status": "error",
                    "reason": "hg update failed", "tests": [],
                })
                continue

            build_ok, reason = mach_build(commit_hash, self.env)
            if not build_ok:
                print(f"  → marking as need_full_build")
                commit_results.append({
                    "commit_hash": commit_hash, "role": role,
                    "expected": expected, "artifact_status": "need_full_build",
                    "reason": reason, "tests": [],
                })
                continue

            print(f"  Running {len(test_files)} test file(s)...")
            test_results = run_tests_for_commit(
                test_files, self.env, role, fixing_commit
            )

            commit_results.append({
                "commit_hash": commit_hash, "role": role,
                "expected": expected, "artifact_status": "downloaded",
                "reason": None, "tests": test_results,
            })

        overall    = self._compute_overall_status(commit_results)
        bug_result = {
            "bug_id": bug_id, "overall_status": overall,
            "commits": commit_results,
        }
        category = self.writer.save_bug_result(bug_id, bug_result)
        bug_result["artifact_category"] = category
        print(f"\n  → Bug {bug_id} overall_status: {overall.upper()}  category: {category}")
        return bug_result

    def _compute_overall_status(self, commit_results: List[Dict]) -> str:
        if not commit_results:
            return "skipped"

        all_skipped = all(
            r["artifact_status"] in ("need_full_build", "error")
            for r in commit_results
        )
        if all_skipped:
            return "skipped"

        any_skipped = any(
            r["artifact_status"] in ("need_full_build", "error")
            for r in commit_results
        )

        # All runnable tests skipped — unsupported framework/platform
        all_tests = [t for r in commit_results for t in r.get("tests", [])]
        if all_tests and all(t["result"] == "skipped" for t in all_tests):
            return "not_validated_skipped"

        validated = True
        for r in commit_results:
            if r["artifact_status"] != "downloaded":
                continue
            for t in r.get("tests", []):
                result   = t["result"]
                expected = r["expected"]

                if result == "skipped":
                    continue

                # Fixing commit must pass
                if expected == "pass" and result != "pass":
                    validated = False

                # Parent commit must not pass
                if expected == "fail" and result == "pass":
                    validated = False

        if any_skipped:
            return "partial"
        return "pass" if validated else "fail"

    def run(self) -> Dict:
        print("=" * 80)
        print("MULTI FIXING COMMIT BUILD TEST RUNNER")
        print("=" * 80 + "\n")

        original_commit = hg_current(self.env)
        print(f"Current commit (will restore at end): {original_commit[:12]}\n")

        warmup_mach(self.env)

        bug_dirs = self.load_bug_dirs()
        if not bug_dirs:
            return {}

        total = len(bug_dirs)
        stats = {
            "total_bugs":                 total,
            "bugs_pass":                  0,
            "bugs_fail":                  0,
            "bugs_partial":               0,
            "bugs_skipped":               0,
            "bugs_not_validated_skipped": 0,
            "bugs_all_artifacts":         0,
            "bugs_partial_artifacts":     0,
            "bugs_no_artifacts":          0,
            "total_commits_run":          0,
            "total_need_full_build":      0,
            "total_tests_run":            0,
            "total_tests_pass":           0,
            "total_tests_fail":           0,
        }
        all_results = {}

        for idx, bug_dir in enumerate(bug_dirs, 1):
            try:
                res    = self.process_bug(bug_dir, idx, total)
                bug_id = res["bug_id"]
                all_results[bug_id] = res

                status   = res["overall_status"]
                category = res.get("artifact_category", "no_artifacts")
                stats[f"bugs_{status}"]   = stats.get(f"bugs_{status}", 0) + 1
                stats[f"bugs_{category}"] = stats.get(f"bugs_{category}", 0) + 1

                for cr in res.get("commits", []):
                    if cr["artifact_status"] == "need_full_build":
                        stats["total_need_full_build"] += 1
                    else:
                        stats["total_commits_run"] += 1
                    for t in cr.get("tests", []):
                        stats["total_tests_run"] += 1
                        if t["result"] == "pass":
                            stats["total_tests_pass"] += 1
                        elif t["result"] != "skipped":
                            stats["total_tests_fail"] += 1

            except Exception as e:
                print(f"  ERROR processing {bug_dir.name}: {e}")
                all_results[bug_dir.name] = {"status": "error", "error": str(e)}

        print(f"\nRestoring mozilla-central to {original_commit[:12]}...")
        hg_update(original_commit, self.env)

        self._print_summary(stats)
        self._save_summary(stats, all_results)
        return {"stats": stats, "results": all_results}

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 80)
        print("MULTI FIXING COMMIT BUILD TEST RUNNER — SUMMARY")
        print("=" * 80)
        print(f"  Total bugs processed               : {s['total_bugs']}")
        print(f"  ── Artifact availability ───────────────────────────────")
        print(f"  Bugs with ALL prebuilt binaries    : {s['bugs_all_artifacts']}")
        print(f"  Bugs with PARTIAL prebuilt binaries: {s['bugs_partial_artifacts']}")
        print(f"  Bugs with NO prebuilt binaries     : {s['bugs_no_artifacts']}")
        print(f"  ── Test results ────────────────────────────────────────")
        print(f"  Pipeline validated (pass)          : {s['bugs_pass']}")
        print(f"  Unexpected results (fail)          : {s['bugs_fail']}")
        print(f"  Partial (some need build)          : {s['bugs_partial']}")
        print(f"  Skipped (all need build)           : {s['bugs_skipped']}")
        print(f"  Not validated (all tests skipped)  : {s['bugs_not_validated_skipped']}")
        print(f"  ── Commit & test counts ────────────────────────────────")
        print(f"  Commits run                        : {s['total_commits_run']}")
        print(f"  Commits need full build            : {s['total_need_full_build']}")
        print(f"  Total tests run                    : {s['total_tests_run']}")
        print(f"  Tests passed                       : {s['total_tests_pass']}")
        print(f"  Tests failed                       : {s['total_tests_fail']}")

    def _save_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "statistics":         stats,
            "per_bug": {
                bid: {
                    "overall_status": r.get("overall_status"),
                    "commits": [
                        {
                            "commit_hash":     c["commit_hash"],
                            "role":            c["role"],
                            "expected":        c["expected"],
                            "artifact_status": c["artifact_status"],
                            "test_count":      len(c.get("tests", [])),
                            "tests_pass":      sum(1 for t in c.get("tests", []) if t["result"] == "pass"),
                            "tests_fail":      sum(1 for t in c.get("tests", []) if t["result"] not in ("pass", "skipped")),
                        }
                        for c in r.get("commits", [])
                    ]
                }
                for bid, r in results.items()
                if "error" not in r
            }
        }
        sp = self.output_base / "pipeline_summary.json"
        sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ pipeline_summary.json → {sp}")

        rp    = self.output_base / "statistics_report.txt"
        lines = [
            "=" * 80, "MULTI FIXING COMMIT BUILD TEST RUNNER — STATISTICS REPORT", "=" * 80,
            f"Generated: {datetime.now().isoformat()}", "",
            f"Total bugs processed               : {stats['total_bugs']}",
            f"Bugs with ALL prebuilt binaries    : {stats['bugs_all_artifacts']}",
            f"Bugs with PARTIAL prebuilt binaries: {stats['bugs_partial_artifacts']}",
            f"Bugs with NO prebuilt binaries     : {stats['bugs_no_artifacts']}",
            f"Pipeline validated (pass)          : {stats['bugs_pass']}",
            f"Unexpected results (fail)          : {stats['bugs_fail']}",
            f"Partial (some need build)          : {stats['bugs_partial']}",
            f"Skipped (all need build)           : {stats['bugs_skipped']}",
            f"Not validated (all tests skipped)  : {stats['bugs_not_validated_skipped']}",
            f"Commits run                        : {stats['total_commits_run']}",
            f"Commits need full build            : {stats['total_need_full_build']}",
            f"Total tests run                    : {stats['total_tests_run']}",
            f"Tests passed                       : {stats['total_tests_pass']}",
            f"Tests failed                       : {stats['total_tests_fail']}",
            "", "=" * 80, "PER-BUG RESULTS", "=" * 80, "",
        ]
        for bid, r in results.items():
            if "error" in r:
                lines.append(f"Bug {bid}  [ERROR: {r.get('error')}]")
            else:
                lines.append(f"Bug {bid}  [{r.get('overall_status','?').upper()}]")
                for c in r.get("commits", []):
                    tests  = c.get("tests", [])
                    n_pass = sum(1 for t in tests if t["result"] == "pass")
                    n_fail = sum(1 for t in tests if t["result"] not in ("pass", "skipped"))
                    lines.append(
                        f"  [{c['role']:7s}] {c['commit_hash'][:12]}  "
                        f"expected={c['expected']:4s}  "
                        f"artifact={c['artifact_status']}  "
                        f"pass={n_pass}  fail={n_fail}"
                    )
                    for t in tests:
                        lines.append(
                            f"           {t['result'].upper():7s}  "
                            f"reason={t.get('failure_reason','n/a'):10s}  "
                            f"{t['mach_command']}"
                        )
            lines.append("")

        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ statistics_report.txt  → {rp}")

        self.writer.write_subfolder_summaries(results)


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    pipeline = TestRunnerPipeline()
    pipeline.run()
    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
