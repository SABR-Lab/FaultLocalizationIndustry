#!/usr/bin/env python3
"""
================================================================================
PARTIAL BUILD RETRY RUNNER
================================================================================

PURPOSE:
--------
Retry building and testing bugs that were partially built in previous runs.
Reads partial build results from two sources:
  1. multi_fixing_commit_full_build/classification/partial_builds/
  2. python3_full_build/worker_1/partial_builds/

For each bug, looks up full commit/test info from:
  multi_fixing_commit_ready_to_full_build_manifest/buildable_bugs/

For each bug:
  - Skips commits that already built AND tested successfully
  - Retries commits that failed, timed out, or were never attempted
  - Preserves existing successful results in final output
  - Wipes objdir before every hg update to prevent symlink collisions

FIXES APPLIED vs PREVIOUS RUNS:
---------------------------------
  - BUILD_TIMEOUT increased to 9600s (160 minutes)             [fix #10]
  - objdir wiped before every hg update                        [symlink fix]
  - head_* helper files filtered out before test invocation    [fix #1]
  - WPT crashtest files filtered from standalone invocation    [fix #2]
  - reftest.tests is: null detected as technical failure       [fix #5]
  - browser_all_files_referenced.js filtered out               [fix #8]
  - Non-runnable extensions filtered earlier in pipeline       [fix #9]
  - RUSTUP_TOOLCHAIN=stable                                    [rust pin fix]
  - MOZ_MAKE_FLAGS=-j4                                         [OOM fix]
  - ac_add_options --enable-js-shell in mozconfig              [fix #4 - do manually]

NOT RESOLVED IN CODE (require external steps):
  - fix #3: non-deterministic crash reproduction — documented only
  - fix #4: add --enable-js-shell to mozconfig manually
  - fix #6: sudo apt-get install gsettings-desktop-schemas
  - fix #7: pip install aioquic pyOpenSSL

USAGE:
------
  python3 partial_build_retry_runner.py
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

PARTIAL_SOURCE_1 = "bugzilla_bugs_analysis/outputs/multi_fixing_commit_full_build/classification/partial_builds"
PARTIAL_SOURCE_2 = "bugzilla_bugs_analysis/outputs/python3_full_build/worker_1/partial_builds"
MANIFEST_DIR     = "bugzilla_bugs_analysis/outputs/multi_fixing_commit_ready_to_full_build_manifest/buildable_bugs"
OUTPUT_DIR       = "bugzilla_bugs_analysis/outputs/partial_build_retry"

MOZILLA_CENTRAL  = Path("/data/FaultLocalizationIndustry/mozilla-central")
SAVED_BUILDS_DIR = Path("/data/FaultLocalizationIndustry/saved_builds")

BUILD_TIMEOUT = 9600   # 160 minutes — fix #10
TEST_TIMEOUT  = 1800   # 30 minutes per test run

# fix #9 — filter these out early, before classification or invocation
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

# fix #8 — filter out packaging validation tests incompatible with our build
FILTERED_TEST_FILENAMES = {
    "browser_all_files_referenced.js",
}

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
            "ac_add_options --enable-js-shell\n"   # fix #4 — build JS shell for jit-tests
            "mk_add_options MOZ_OBJDIR=./objdir-fullBuild\n"
            "mk_add_options AUTOCLOBBER=1\n"
            "mk_add_options MOZ_MAKE_FLAGS=-j4\n",
            encoding="utf-8"
        )
        print("  [mozconfig] created ✓")
    else:
        content = mozconfig.read_text(encoding="utf-8")
        print(f"  [mozconfig] found existing mozconfig:\n{content}")
        # Warn if --enable-js-shell is missing from existing mozconfig
        if "--enable-js-shell" not in content:
            print("  [mozconfig] WARNING: --enable-js-shell not found — jit-tests may fail. Add it manually.")

    env = os.environ.copy()
    env["MOZCONFIG"] = str(mozconfig)
    env["RUSTUP_TOOLCHAIN"] = "stable"
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
# SAVED BUILDS MANAGER
# Saves objdir after a successful build so it can be restored later
# instead of rebuilding from scratch. Saves hours of build time.
# Index file tracks what is saved and how much space is used.
# ===========================================================================

class SavedBuildsManager:

    INDEX_FILE = "index.json"

    def __init__(self, saved_builds_dir: Path):
        self.base = saved_builds_dir
        self.base.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base / self.INDEX_FILE
        self.index = self._load_index()

    def _load_index(self) -> Dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_index(self):
        self.index_path.write_text(json.dumps(self.index, indent=2), encoding="utf-8")

    def has_saved_build(self, commit_hash: str) -> bool:
        if commit_hash not in self.index:
            return False
        saved_path = self.base / commit_hash
        return saved_path.exists()

    def save_build(self, commit_hash: str, mozilla_central: Path):
        """Move objdir to saved_builds/<commit_hash>/ after a successful build."""
        objdir = mozilla_central / "objdir-fullBuild"
        if not objdir.exists():
            print(f"  [saved_builds] WARNING: objdir not found, cannot save build for {commit_hash[:12]}")
            return
        dest = self.base / commit_hash
        if dest.exists():
            print(f"  [saved_builds] build for {commit_hash[:12]} already saved, skipping")
            return
        print(f"  [saved_builds] saving build {commit_hash[:12]} → {dest} ...", flush=True)
        try:
            # use os.rename for atomic move that preserves symlinks
            os.rename(str(objdir), str(dest))
            size_gb = round(sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e9, 2)
            self.index[commit_hash] = {
                "path":       str(dest),
                "saved_at":   datetime.now().isoformat(),
                "size_gb":    size_gb,
            }
            self._save_index()
            print(f"  [saved_builds] saved ✓ ({size_gb} GB)", flush=True)
        except Exception as e:
            print(f"  [saved_builds] ERROR saving build: {e}", flush=True)

    def restore_build(self, commit_hash: str, mozilla_central: Path) -> bool:
        """
        Restore a saved build for rerunning tests without rebuilding.
        Uses os.rename for instant, reliable move on same filesystem.
        NOTE: This removes the build from saved_builds — call save_build
        again after tests if you want to keep it saved.
        """
        saved_path = self.base / commit_hash
        if not saved_path.exists():
            print(f"  [saved_builds] no saved build for {commit_hash[:12]}")
            return False
        objdir = mozilla_central / "objdir-fullBuild"
        if objdir.exists():
            print(f"  [saved_builds] wiping existing objdir before restore...", flush=True)
            shutil.rmtree(objdir, ignore_errors=True)
        print(f"  [saved_builds] restoring build {commit_hash[:12]} ...", flush=True)
        try:
            os.rename(str(saved_path), str(objdir))
            del self.index[commit_hash]
            self._save_index()
            print(f"  [saved_builds] restored ✓", flush=True)
            return True
        except Exception as e:
            print(f"  [saved_builds] ERROR restoring build: {e}", flush=True)
            return False

    def print_index(self):
        if not self.index:
            print("  [saved_builds] no saved builds yet")
            return
        total_gb = sum(v.get("size_gb", 0) for v in self.index.values())
        print(f"  [saved_builds] {len(self.index)} saved builds, {total_gb:.1f} GB total:")
        for h, info in self.index.items():
            print(f"    {h[:12]}  {info.get('size_gb', '?')} GB  saved={info.get('saved_at', '?')[:10]}")


# ===========================================================================
# OBJDIR WIPE
# ===========================================================================

def wipe_objdir(mozilla_central: Path):
    objdir = mozilla_central / "objdir-fullBuild"
    if objdir.exists():
        print(f"  [objdir] wiping before hg update...", flush=True)
        shutil.rmtree(objdir, ignore_errors=True)
        print(f"  [objdir] wiped ✓", flush=True)
    else:
        print(f"  [objdir] already clean ✓", flush=True)


# ===========================================================================
# MACH BUILD
# ===========================================================================

def mach_build(commit_hash: str, mozilla_central: Path, env: Dict) -> Tuple[bool, str]:
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
        "reftest.tests is: null",           # fix #2 / fix #5
        "webtranport_h3_server_is_running", # fix #7 — aioquic/pyOpenSSL missing
        "from aioquic.asyncio import",      # fix #7 — aioquic not installed
        "from typing_extensions import deprecated",  # fix #7 — typing_extensions mismatch
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
    # fix #5 — detect WPT crashtest false pass when reftest runner finds no tests
    if "REFTEST ERROR" in stdout and "reftest.tests is: null" in stdout:
        return "technical"

    # fix #7 — WPT server startup failure due to missing aioquic/pyOpenSSL
    if "webtranport_h3_server_is_running" in stdout or "from aioquic.asyncio import" in stdout:
        return "technical"

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
# RUNNABILITY CHECK  — fixes #1, #2, #8, #9
# ===========================================================================

def is_runnable(tf: Dict) -> Tuple[bool, str]:
    filepath  = tf.get("filepath", "")
    framework = tf.get("framework", "unknown")
    filename  = Path(filepath).name
    ext       = Path(filepath).suffix.lower()

    # fix #1 — head_* files are shared test helpers, not runnable tests
    if filename.startswith("head_"):
        return False, "head_* helper file (not a runnable test)"

    # fix #8 — packaging validation test, incompatible with our build pipeline
    if filename in FILTERED_TEST_FILENAMES:
        return False, f"filtered test ({filename}) — requires packaged build"

    # fix #2 — WPT crashtests cannot be run standalone with ./mach crashtest
    # They live under testing/web-platform/ and need a manifest, not direct invocation
    if framework == "crashtest" and "testing/web-platform/" in filepath:
        return False, "WPT crashtest — cannot invoke standalone (requires manifest)"

    # fix #9 — filter non-runnable and unsupported extensions early
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
            if status not in ("pass", "technical") else "n/a" if status == "pass" else "technical"
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

    # treat "technical" results same as skipped for overall status purposes
    non_technical = [t for t in all_tests if t["result"] not in ("skipped", "technical")]
    if all_tests and not non_technical:
        return "not_validated_skipped"

    validated = True
    for r in commit_results:
        if not r.get("build_ok", False):
            continue
        for t in r.get("tests", []):
            if t["result"] in ("skipped", "technical"):
                continue
            if r["role"] == "fixing" and t["result"] != "pass":
                validated = False
            if r["role"] == "parent" and t["result"] == "pass":
                validated = False

    if any_build_failed:
        return "partial"
    return "pass" if validated else "fail"


# ===========================================================================
# MANIFEST LOADER
# ===========================================================================

def load_manifest(manifest_base: Path) -> Dict[str, Dict]:
    manifest = {}
    for framework_dir in sorted(manifest_base.iterdir()):
        if not framework_dir.is_dir():
            continue
        for bug_file in sorted(framework_dir.glob("bug_*.json")):
            try:
                data   = json.loads(bug_file.read_text(encoding="utf-8"))
                bug_id = str(data.get("bug_id", ""))
                if not bug_id or bug_id in manifest:
                    continue
                manifest[bug_id] = data
            except Exception as e:
                print(f"  WARNING: could not read {bug_file}: {e}")
    print(f"Loaded {len(manifest)} unique bugs from manifest\n")
    return manifest


# ===========================================================================
# PARTIAL BUILD LOADERS
# ===========================================================================

def load_partial_source1(base: Path) -> Dict[str, Dict]:
    results = {}
    for bug_dir in sorted(base.iterdir()):
        if not bug_dir.is_dir() or not bug_dir.name.startswith("bug_"):
            continue
        f = bug_dir / "classification.json"
        if not f.exists():
            f = bug_dir / "test_results.json"
        if not f.exists():
            continue
        try:
            data   = json.loads(f.read_text(encoding="utf-8"))
            bug_id = str(data.get("bug_id", ""))
            if bug_id:
                data["_source"] = "source1"
                results[bug_id] = data
        except Exception as e:
            print(f"  WARNING: could not read {f}: {e}")
    print(f"Source 1: loaded {len(results)} partial bugs\n")
    return results


def load_partial_source2(base: Path) -> Dict[str, Dict]:
    results = {}
    for bug_dir in sorted(base.iterdir()):
        if not bug_dir.is_dir() or not bug_dir.name.startswith("bug_"):
            continue
        f = bug_dir / "test_results.json"
        if not f.exists():
            continue
        try:
            data   = json.loads(f.read_text(encoding="utf-8"))
            bug_id = str(data.get("bug_id", ""))
            if bug_id:
                data["_source"] = "source2"
                results[bug_id] = data
        except Exception as e:
            print(f"  WARNING: could not read {f}: {e}")
    print(f"Source 2: loaded {len(results)} partial bugs\n")
    return results


def merge_partial_sources(src1: Dict, src2: Dict) -> Dict[str, Dict]:
    merged = {**src1, **src2}  # src2 wins on duplicates (richer data)
    print(f"Merged: {len(merged)} unique partial bugs total\n")
    return merged


# ===========================================================================
# PRIOR RESULT INSPECTOR
# ===========================================================================

def get_successful_commits(prior: Dict) -> Dict[str, Dict]:
    successful = {}
    for cr in prior.get("commits", []):
        build_ok = cr.get("build_ok", False)
        tests    = cr.get("tests", [])
        has_real_result = any(t.get("result") not in ("skipped", "technical", None) for t in tests)
        if build_ok and has_real_result:
            successful[cr["commit_hash"]] = cr
    return successful


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    SUBDIR_SUCCESS       = "successful_builds"
    SUBDIR_VALIDATED     = "successful_builds/pipeline_validated"
    SUBDIR_NOT_VALIDATED = "successful_builds/pipeline_not_validated"
    SUBDIR_PARTIAL       = "partial_builds"
    SUBDIR_FAILED        = "failed_builds"

    def __init__(self, base_dir: Path):
        self.base = base_dir / "worker_1"
        self.base.mkdir(parents=True, exist_ok=True)
        for subdir in [
            self.SUBDIR_SUCCESS,
            self.SUBDIR_VALIDATED,
            self.SUBDIR_NOT_VALIDATED,
            self.SUBDIR_PARTIAL,
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

        if overall == "partial":
            bug_dir = self.base / self.SUBDIR_PARTIAL / f"bug_{bug_id}"
            bug_dir.mkdir(parents=True, exist_ok=True)
            (bug_dir / "test_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            return

        if overall in ("pass", "fail", "not_validated_skipped"):
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
            "PARTIAL BUILD RETRY RUNNER — STATISTICS REPORT",
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
            f"Commits reused from prior run      : {stats['total_commits_reused']}",
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
                status_str = r.get('overall_status', '?').upper()
                source     = r.get('source', '?')
                lines.append(f"Bug {bid}  [{status_str}]  frameworks={r.get('framework_group','?')}  source={source}")
                for c in r.get("commits", []):
                    reused    = "reused" if c.get("reused_from_prior") else ("built" if c["build_ok"] else "FAILED")
                    tests     = c.get("tests", [])
                    n_pass    = sum(1 for t in tests if t["result"] == "pass")
                    n_fail    = sum(1 for t in tests if t["result"] not in ("pass", "skipped", "technical"))
                    n_tech    = sum(1 for t in tests if t["result"] == "technical")
                    lines.append(
                        f"  [{c['role']:7s}] {c['commit_hash'][:12]}  "
                        f"expected={c['expected']:4s}  build={reused}  "
                        f"pass={n_pass}  fail={n_fail}  technical={n_tech}"
                    )
                    for t in tests:
                        lines.append(
                            f"           {t['result'].upper():10s}  "
                            f"reason={t.get('failure_reason','n/a'):10s}  "
                            f"{t['mach_command']}"
                        )
            lines.append("")
        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ statistics_report.txt → {rp}")

    def save_pipeline_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "runner": "partial_build_retry_runner",
            "statistics": stats,
            "per_bug": {
                bid: {
                    "overall_status":  r.get("overall_status"),
                    "framework_group": r.get("framework_group"),
                    "source":          r.get("source"),
                    "commits": [
                        {
                            "commit_hash":       c["commit_hash"],
                            "role":              c["role"],
                            "expected":          c["expected"],
                            "build_ok":          c["build_ok"],
                            "reused_from_prior": c.get("reused_from_prior", False),
                            "test_count":        len(c.get("tests", [])),
                            "tests_pass":        sum(1 for t in c.get("tests", []) if t["result"] == "pass"),
                            "tests_fail":        sum(1 for t in c.get("tests", []) if t["result"] not in ("pass", "skipped", "technical")),
                            "tests_technical":   sum(1 for t in c.get("tests", []) if t["result"] == "technical"),
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

class PartialBuildRetryPipeline:

    def __init__(self):
        self.mozilla_central = MOZILLA_CENTRAL
        self.output_base     = Path(parent_dir) / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)
        self.env             = ensure_mozconfig(self.mozilla_central)
        self.writer          = OutputWriter(self.output_base)
        self.saved_builds    = SavedBuildsManager(SAVED_BUILDS_DIR)

        print(f"Mozilla-central: {self.mozilla_central}")
        print(f"Output:          {self.output_base / 'worker_1'}")
        print(f"Saved builds:    {SAVED_BUILDS_DIR}")
        print(f"RUSTUP_TOOLCHAIN: {self.env.get('RUSTUP_TOOLCHAIN', 'NOT SET')}")
        self.saved_builds.print_index()
        print()

    def process_bug(self, bug_id: str, manifest_bug: Dict, prior: Dict, idx: int, total: int) -> Dict:
        framework_grp    = manifest_bug.get("framework_group", "unknown")
        commit_pairs     = manifest_bug.get("commit_pairs", [])
        prior_source     = prior.get("_source", "unknown")
        successful_prior = get_successful_commits(prior)

        print(f"\n{'='*60}", flush=True)
        print(f"[{idx}/{total}] Bug {bug_id}  frameworks={framework_grp}  prior_source={prior_source}", flush=True)
        if successful_prior:
            print(f"  Reusing {len(successful_prior)} already-successful commit(s): "
                  f"{[h[:12] for h in successful_prior]}", flush=True)
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

                # ── Reuse prior successful result ──────────────────────────
                if commit_hash in successful_prior:
                    print(f"\n  [{role.upper()}] {commit_hash[:12]}  ✓ REUSED from prior run", flush=True)
                    prior_cr = successful_prior[commit_hash]
                    commit_results.append({
                        "commit_hash":       commit_hash,
                        "role":              role,
                        "expected":          expected,
                        "build_ok":          True,
                        "build_error":       None,
                        "failed_step":       None,
                        "reused_from_prior": True,
                        "tests":             prior_cr.get("tests", []),
                    })
                    continue

                print(f"\n  [{role.upper()}] {commit_hash[:12]}  (expect {expected.upper()})", flush=True)

                # Step 1 — wipe objdir before hg update
                wipe_objdir(self.mozilla_central)

                # Step 2 — hg update
                update_ok, update_err = hg_update(commit_hash, self.mozilla_central, self.env)
                if not update_ok:
                    print(f"  ✗ hg update FAILED for {commit_hash[:12]}", flush=True)
                    self.writer.save_build_failure(bug_id, commit_hash, role, update_err, "hg_update")
                    commit_results.append({
                        "commit_hash": commit_hash, "role": role, "expected": expected,
                        "build_ok": False, "build_error": update_err,
                        "failed_step": "hg_update", "reused_from_prior": False, "tests": [],
                    })
                    continue

                # Step 3 — restore saved build or rebuild from scratch
                if self.saved_builds.has_saved_build(commit_hash):
                    print(f"  [saved_builds] restoring saved build for {commit_hash[:12]} — skipping mach build", flush=True)
                    restored = self.saved_builds.restore_build(commit_hash, self.mozilla_central)
                    if not restored:
                        print(f"  [saved_builds] restore failed, rebuilding from scratch...", flush=True)
                        build_ok, build_err = mach_build(commit_hash, self.mozilla_central, self.env)
                    else:
                        build_ok, build_err = True, ""
                else:
                    build_ok, build_err = mach_build(commit_hash, self.mozilla_central, self.env)

                if not build_ok:
                    print(f"  ✗ BUILD FAILED for {commit_hash[:12]}", flush=True)
                    self.writer.save_build_failure(bug_id, commit_hash, role, build_err, "mach_build")
                    commit_results.append({
                        "commit_hash": commit_hash, "role": role, "expected": expected,
                        "build_ok": False, "build_error": build_err,
                        "failed_step": "mach_build", "reused_from_prior": False, "tests": [],
                    })
                    continue

                # Step 4 — run tests against live objdir (no moving needed)
                print(f"  Running {len(test_files)} test file(s)...", flush=True)
                test_results = run_tests_for_commit(
                    test_files, self.env, role, fixing_commit, self.mozilla_central
                )
                commit_results.append({
                    "commit_hash": commit_hash, "role": role, "expected": expected,
                    "build_ok": True, "build_error": None,
                    "failed_step": None, "reused_from_prior": False,
                    "tests": test_results,
                })

                # Step 5 — save build for future reruns, then wipe for next commit
                # os.rename is instant and 100% reliable (same filesystem)
                if not self.saved_builds.has_saved_build(commit_hash):
                    self.saved_builds.save_build(commit_hash, self.mozilla_central)
                else:
                    wipe_objdir(self.mozilla_central)

        overall    = compute_overall_status(commit_results)
        bug_result = {
            "bug_id":          bug_id,
            "framework_group": framework_grp,
            "overall_status":  overall,
            "source":          prior_source,
            "commits":         commit_results,
            "recorded_at":     datetime.now().isoformat(),
        }

        self.writer.save_bug_result(bug_id, bug_result)

        built   = sum(1 for r in commit_results if r["build_ok"])
        reused  = sum(1 for r in commit_results if r.get("reused_from_prior"))
        total_c = len(commit_results)
        print(
            f"\n  → Bug {bug_id}  status={overall.upper()}  "
            f"built={built}/{total_c} commits  reused={reused}",
            flush=True
        )
        return bug_result

    def run(self):
        print("=" * 80)
        print("PARTIAL BUILD RETRY RUNNER")
        print(f"BUILD_TIMEOUT={BUILD_TIMEOUT}s | objdir wiped before each hg update | RUSTUP_TOOLCHAIN=stable")
        print("=" * 80 + "\n")

        manifest_base = Path(parent_dir) / MANIFEST_DIR
        manifest      = load_manifest(manifest_base)

        src1     = load_partial_source1(Path(parent_dir) / PARTIAL_SOURCE_1)
        src2     = load_partial_source2(Path(parent_dir) / PARTIAL_SOURCE_2)
        partials = merge_partial_sources(src1, src2)

        bugs_to_process = {
            bug_id: (manifest[bug_id], prior)
            for bug_id, prior in partials.items()
            if bug_id in manifest
        }
        missing = [bid for bid in partials if bid not in manifest]
        if missing:
            print(f"WARNING: {len(missing)} partial bugs not found in manifest: {missing}\n")

        total = len(bugs_to_process)
        print(f"Bugs to process: {total}\n")

        original_commit = hg_current(self.mozilla_central, self.env)
        print(f"Current commit (will restore at end): {original_commit[:12]}\n")

        stats = {
            "total_bugs": total,
            "bugs_pass": 0, "bugs_fail": 0, "bugs_partial": 0,
            "bugs_skipped": 0, "bugs_not_validated_skipped": 0,
            "total_commits_attempted": 0, "total_commits_reused": 0,
            "total_commits_built": 0, "total_commits_failed": 0,
            "total_tests_run": 0, "total_tests_pass": 0,
            "total_tests_fail": 0, "total_tests_skipped": 0,
            "total_tests_technical": 0,
        }
        all_results = {}

        for idx, (bug_id, (manifest_bug, prior)) in enumerate(bugs_to_process.items(), 1):
            try:
                result = self.process_bug(bug_id, manifest_bug, prior, idx, total)
                all_results[bug_id] = result

                status = result["overall_status"]
                stats[f"bugs_{status}"] = stats.get(f"bugs_{status}", 0) + 1

                for cr in result.get("commits", []):
                    stats["total_commits_attempted"] += 1
                    if cr.get("reused_from_prior"):
                        stats["total_commits_reused"] += 1
                    elif cr["build_ok"]:
                        stats["total_commits_built"] += 1
                    else:
                        stats["total_commits_failed"] += 1
                    for t in cr.get("tests", []):
                        if t["result"] == "pass":
                            stats["total_tests_pass"] += 1
                            stats["total_tests_run"]  += 1
                        elif t["result"] == "skipped":
                            stats["total_tests_skipped"] += 1
                        elif t["result"] == "technical":
                            stats["total_tests_technical"] += 1
                        else:
                            stats["total_tests_fail"] += 1
                            stats["total_tests_run"]  += 1

            except Exception as e:
                print(f"  ERROR processing bug {bug_id}: {e}", flush=True)
                all_results[bug_id] = {"status": "error", "error": str(e)}

        print(f"\nRestoring mozilla-central to {original_commit[:12]}...")
        wipe_objdir(self.mozilla_central)
        hg_update(original_commit, self.mozilla_central, self.env)

        self._print_summary(stats)
        self.writer.save_pipeline_summary(stats, all_results)
        self.writer.save_statistics_report(stats, all_results)

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 80)
        print("PARTIAL BUILD RETRY RUNNER — FINAL SUMMARY")
        print("=" * 80)
        print(f"  Total bugs processed               : {s['total_bugs']}")
        print(f"  Pipeline validated     (pass)      : {s['bugs_pass']}")
        print(f"  Not validated          (fail)      : {s['bugs_fail']}")
        print(f"  Partial builds                     : {s['bugs_partial']}")
        print(f"  All builds failed      (skipped)   : {s['bugs_skipped']}")
        print(f"  All tests skipped                  : {s['bugs_not_validated_skipped']}")
        print(f"  Commits reused from prior run      : {s['total_commits_reused']}")
        print(f"  Commits built successfully         : {s['total_commits_built']}")
        print(f"  Commits failed                     : {s['total_commits_failed']}")
        print(f"  Total tests run                    : {s['total_tests_run']}")
        print(f"  Tests passed                       : {s['total_tests_pass']}")
        print(f"  Tests failed                       : {s['total_tests_fail']}")
        print(f"  Tests technical (filtered)         : {s['total_tests_technical']}")
        print(f"  Tests skipped                      : {s['total_tests_skipped']}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    pipeline = PartialBuildRetryPipeline()
    pipeline.run()
    print("\n" + "=" * 80)
    print("✓  PARTIAL BUILD RETRY RUNNER COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
