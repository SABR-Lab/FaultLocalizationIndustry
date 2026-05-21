#!/usr/bin/env python3
"""
================================================================================
BUILD TEST RUN MANIFEST
================================================================================

PURPOSE:
--------
For each bug that has test files at its fixing commit (from Step 5), collect:
  - The fixing commit(s) that contain test files + those test files
  - ALL regressor commits for that bug (from Step 4B)
  - The correct mach command for each test file based on its framework
  - A ready-to-run manifest per bug

INPUT:
------
Test_extraction output:
  outputs/multi_fixing_commit_tests/
  └── bugs_with_test_files_at_fixing_commit/
      └── bug_<ID>/
          └── <commit_hash>/
              └── test_files.json

file_diff_extraction  output:
  outputs/multi_commit_diff_extraction/
  └── bugs_with_fixing_commits/
      └── bug_<ID>/
          └── regressor_commits/
              └── <regressor_bug_id>/
                  └── <commit_hash>/
                      └── metadata.json

"""



import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

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

TEST_EXTRACTION_INPUT  = "multi_fixing_commit_tests"
FILE_DIFF_INPUT = "multi_commit_diff_extraction"
OUTPUT_DIR   = "multi_commit_test_run_manifest"

WITH_TESTS       = "bugs_with_test_files_at_fixing_commit"
WITH_FIXING   = "bugs_with_fixing_commits"


# ===========================================================================
# FRAMEWORK DETECTOR
# ===========================================================================

# Each entry: (filepath_substring, framework_name, mach_command_template)
# Checked in order — first match wins
"""FRAMEWORK_RULES = [
    # jit-test
    ("jit-test/",           "jit-test",   "./mach jit-test {path}"),
    ("jit-tests/",          "jit-test",   "./mach jit-test {path}"),

    # xpcshell
    ("xpcshell/",           "xpcshell",   "./mach xpcshell-test {path}"),
    ("xpcshell-",           "xpcshell",   "./mach xpcshell-test {path}"),

    # mochitest browser chrome
    ("browser/",            "mochitest-browser", "./mach mochitest {path}"),

    # mochitest plain
    ("mochitest/",          "mochitest",  "./mach mochitest {path}"),

    # crashtest
    ("crashtests/",         "crashtest",  "./mach crashtest {path}"),
    ("crashtest/",          "crashtest",  "./mach crashtest {path}"),

    # reftest
    ("reftests/",           "reftest",    "./mach reftest {path}"),
    ("reftest/",            "reftest",    "./mach reftest {path}"),

    # web platform tests
    ("web-platform/",       "wpt",        "./mach wpt {path}"),
    ("wpt/",                "wpt",        "./mach wpt {path}"),

    # python unit tests
    ("python/",             "python-test","./mach python-test {path}"),

    # talos / performance
    ("talos/",              "talos",      "./mach talos-test {path}"),

    # generic test directory fallback
    ("tests/",              "unknown",    "./mach test {path}"),
    ("testing/",            "unknown",    "./mach test {path}"),
]
"""
FRAMEWORK_RULES = [
    # ── jit-test ────────────────────────────────────────────────────
    ("jit-test/",           "jit-test",           "./mach jit-test {path}"),
    ("jit-tests/",          "jit-test",           "./mach jit-test {path}"),

    # ── xpcshell ────────────────────────────────────────────────────
    ("xpcshell/",           "xpcshell",           "./mach xpcshell-test {path}"),
    ("xpcshell-",           "xpcshell",           "./mach xpcshell-test {path}"),
    # unit/ directories are almost always xpcshell
    ("/test/unit/",         "xpcshell",           "./mach xpcshell-test {path}"),
    ("/tests/unit/",        "xpcshell",           "./mach xpcshell-test {path}"),

    # ── GTest (C++ unit tests) ───────────────────────────────────────
    ("/gtest/",             "gtest",              "./mach gtest {path}"),
    ("test/gtest/",         "gtest",              "./mach gtest {path}"),
    # standalone C++ test files that are clearly gtest
    ("TestingFunctions.cpp","gtest",              "./mach gtest {path}"),
    ("test_duplex.cpp",     "gtest",              "./mach gtest {path}"),

    # ── Marionette ───────────────────────────────────────────────────
    ("marionette/",         "marionette",         "./mach marionette-test {path}"),

    # ── Android / GeckoView (not runnable on Linux desktop) ──────────
    ("mobile/android/",     "android",            "NOT_RUNNABLE"),
    ("geckoview/",          "android",            "NOT_RUNNABLE"),
    (".kt",                 "android",            "NOT_RUNNABLE"),

    # ── mochitest browser chrome ─────────────────────────────────────
    ("browser/",            "mochitest-browser",  "./mach mochitest {path}"),
    # browser_ prefix js files are browser chrome tests
    ("/test/browser_",      "mochitest-browser",  "./mach mochitest {path}"),

    # ── mochitest plain ──────────────────────────────────────────────
    ("mochitest/",          "mochitest",          "./mach mochitest {path}"),
    # .html test files in well-known DOM/toolkit/layout/widget test dirs
    ("dom/base/test/",      "mochitest",          "./mach mochitest {path}"),
    ("dom/canvas/test/",    "mochitest",          "./mach mochitest {path}"),
    ("dom/media/test/",     "mochitest",          "./mach mochitest {path}"),
    ("dom/midi/tests/",     "mochitest",          "./mach mochitest {path}"),
    ("dom/performance/tests/", "mochitest",       "./mach mochitest {path}"),
    ("dom/serviceworkers/test/", "mochitest",     "./mach mochitest {path}"),
    ("dom/webauthn/tests/", "mochitest",          "./mach mochitest {path}"),
    ("layout/base/tests/",  "mochitest",          "./mach mochitest {path}"),
    ("toolkit/content/tests/", "mochitest",       "./mach mochitest {path}"),
    ("toolkit/components/prompts/test/", "mochitest", "./mach mochitest {path}"),
    ("widget/tests/",       "mochitest",          "./mach mochitest {path}"),

    # ── crashtest ────────────────────────────────────────────────────
    ("crashtests/",         "crashtest",          "./mach crashtest {path}"),
    ("crashtest/",          "crashtest",          "./mach crashtest {path}"),

    # ── reftest ──────────────────────────────────────────────────────
    ("reftests/",           "reftest",            "./mach reftest {path}"),
    ("reftest/",            "reftest",            "./mach reftest {path}"),

    # ── web platform tests ───────────────────────────────────────────
    ("web-platform/",       "wpt",                "./mach wpt {path}"),
    ("wpt/",                "wpt",                "./mach wpt {path}"),

    # ── python / marionette support ──────────────────────────────────
    ("python/",             "python-test",        "./mach python-test {path}"),

    # ── talos ────────────────────────────────────────────────────────
    ("talos/",              "talos",              "./mach talos-test {path}"),

    # ── generic fallback (last resort) ───────────────────────────────
    ("tests/",              "unknown",            "./mach test {path}"),
    ("testing/",            "unknown",            "./mach test {path}"),
]
def detect_framework(filepath: str) -> Tuple[str, str]:
    """
    Detect the test framework from a filepath.
    Returns (framework_name, mach_command) with {path} substituted.
    Falls back to ('unknown', './mach test <path>') if nothing matches.
    """
    fp_lower = filepath.lower()
    for substring, framework, cmd_template in FRAMEWORK_RULES:
        if substring in fp_lower:
            cmd = cmd_template.replace("{path}", filepath)
            return framework, cmd

    # absolute fallback
    return "unknown", f"./mach test {filepath}"


# ===========================================================================
# MANIFEST BUILDER
# ===========================================================================

class ManifestBuilder:

    def __init__(self, test_extraction_base: Path, file_diff_base: Path):
        self.test_extraction_base  = test_extraction_base
        self.file_diff_base = file_diff_base

    def get_fixing_commits_with_tests(self, bug_id: str) -> List[Dict]:
        """
        Read all commit hash directories under Step 5 output for this bug.
        Returns list of {commit_hash, test_files (enriched with framework + mach_command)}.
        """
        bug_dir = self.test_extraction_base / WITH_TESTS / f"bug_{bug_id}"
        if not bug_dir.exists():
            return []

        result = []
        for commit_dir in sorted(bug_dir.iterdir()):
            if not commit_dir.is_dir():
                continue

            tf_path = commit_dir / "test_files.json"
            if not tf_path.exists():
                continue

            try:
                data = json.loads(tf_path.read_text())
            except Exception as e:
                print(f"    Warning: could not read {tf_path}: {e}")
                continue

            enriched_files = []
            for tf in data.get("test_files", []):
                filepath  = tf.get("filepath", "")
                framework, mach_cmd = detect_framework(filepath)
                enriched_files.append({
                    "filename":     tf.get("filename", ""),
                    "filepath":     filepath,
                    "change_type":  tf.get("change_type", ""),
                    "framework":    framework,
                    "mach_command": mach_cmd,
                })

            if enriched_files:
                result.append({
                    "commit_hash": commit_dir.name,
                    "test_files":  enriched_files,
                })

        return result

    def get_regressor_commits(self, bug_id: str) -> List[Dict]:
        """
        Read all regressor commit metadata for this bug from Step 4B output.
        Returns list of {regressor_bug_id, commit_hash, description, pushdate}.
        """
        reg_base = (
            self.file_diff_base
            / WITH_FIXING
            / f"bug_{bug_id}"
            / "regressor_commits"
        )
        if not reg_base.exists():
            return []

        result = []
        seen   = set()

        for reg_bug_dir in sorted(reg_base.iterdir()):
            if not reg_bug_dir.is_dir():
                continue
            reg_bug_id = reg_bug_dir.name

            for commit_dir in sorted(reg_bug_dir.iterdir()):
                if not commit_dir.is_dir():
                    continue

                commit_hash = commit_dir.name
                if commit_hash in seen:
                    continue
                seen.add(commit_hash)

                # Try to read metadata for extra context
                meta_path = commit_dir / "metadata.json"
                meta      = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                    except Exception:
                        pass

                result.append({
                    "regressor_bug_id": reg_bug_id,
                    "commit_hash":      commit_hash,
                    "description":      meta.get("description", ""),
                    "pushdate":         meta.get("pushdate", ""),
                })

        return result

    def build_manifest(self, bug_id: str) -> Optional[Dict]:
        """Build the complete run manifest for one bug."""
        fixing_commits    = self.get_fixing_commits_with_tests(bug_id)
        regressor_commits = self.get_regressor_commits(bug_id)

        if not fixing_commits:
            return None   # no test files found for this bug

        # Collect all unique frameworks across all test files
        frameworks = sorted(set(
            tf["framework"]
            for fc in fixing_commits
            for tf in fc["test_files"]
        ))

        total_test_files = sum(
            len(fc["test_files"]) for fc in fixing_commits
        )

        return {
            "bug_id":                  bug_id,
            "total_test_files":        total_test_files,
            "frameworks_found":        frameworks,
            "total_fixing_commits":    len(fixing_commits),
            "total_regressor_commits": len(regressor_commits),
            "fixing_commits":          fixing_commits,
            "regressor_commits":       regressor_commits,
        }


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)

    def save_manifest(self, bug_id: str, manifest: Dict):
        bug_dir = self.base / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        (bug_dir / "run_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

    def save_all_manifests(self, manifests: Dict):
        (self.base / "all_bugs_manifest.json").write_text(
            json.dumps(manifests, indent=2), encoding="utf-8"
        )


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class testManifestPipeline:

    def __init__(self):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.test_extraction_base  = self.outputs_base / TEST_EXTRACTION_INPUT
        self.file_diff_base = self.outputs_base / FILE_DIFF_INPUT
        self.output_base = self.outputs_base / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.builder = ManifestBuilder(self.test_extraction_base, self.file_diff_base)
        self.writer  = OutputWriter(self.output_base)

        print(f"test_extraction input : {self.test_extraction_base}")
        print(f"file_diff input: {self.file_diff_base}")
        print(f"Output       : {self.output_base}\n")

    def load_bug_ids(self) -> List[str]:
        """Load bug IDs from test_extraction bugs_with_test_files directory."""
        base = self.test_extraction_base / WITH_TESTS
        if not base.exists():
            print(f"ERROR: {base} not found — run step5 first.")
            return []
        ids = sorted(d.name.replace("bug_", "") for d in base.glob("bug_*/") if d.is_dir())
        print(f"Found {len(ids)} bugs with test files from test extractor\n")
        return ids

    def run(self) -> Dict:
        print("=" * 80)
        print(" BUILD TEST RUN MANIFEST")
        print("=" * 80 + "\n")

        bug_ids = self.load_bug_ids()
        if not bug_ids:
            return {}

        total = len(bug_ids)

        stats = {
            "total_bugs_processed":          total,
            "bugs_with_manifests":           0,
            "bugs_skipped_no_tests":         0,
            "total_test_files":              0,
            "total_fixing_commits":          0,
            "total_regressor_commits":       0,
            "bugs_with_no_regressor":        0,
            "framework_counts":              defaultdict(int),
            # detailed breakdown
            "framework_to_bugs":             defaultdict(list),   # framework → [bug_ids]
            "framework_to_files":            defaultdict(list),   # framework → [filepaths]
            "change_type_counts":            defaultdict(int),    # added/modified
            "extension_counts":              defaultdict(int),    # .js/.cpp/.html etc
        }

        all_manifests = {}

        for idx, bug_id in enumerate(bug_ids, 1):
            print(f"[{idx}/{total}] Bug {bug_id} …", end=" ")
            try:
                manifest = self.builder.build_manifest(bug_id)

                if manifest is None:
                    print("skipped (no test files)")
                    stats["bugs_skipped_no_tests"] += 1
                    continue

                self.writer.save_manifest(bug_id, manifest)
                all_manifests[bug_id] = manifest

                stats["bugs_with_manifests"]     += 1
                stats["total_test_files"]        += manifest["total_test_files"]
                stats["total_fixing_commits"]    += manifest["total_fixing_commits"]
                stats["total_regressor_commits"] += manifest["total_regressor_commits"]
                stats["bugs_with_no_regressor"]  += int(
                    manifest["total_regressor_commits"] == 0
                )
                for fw in manifest["frameworks_found"]:
                    stats["framework_counts"][fw] += 1
                    if bug_id not in stats["framework_to_bugs"][fw]:
                        stats["framework_to_bugs"][fw].append(bug_id)

                # collect per-file details
                for fc in manifest["fixing_commits"]:
                    for tf in fc["test_files"]:
                        fw  = tf["framework"]
                        fp  = tf["filepath"]
                        ext = Path(fp).suffix.lower() or "no_ext"
                        stats["framework_to_files"][fw].append(fp)
                        stats["change_type_counts"][tf["change_type"]] += 1
                        stats["extension_counts"][ext] += 1

                print(
                    f"✓  tests={manifest['total_test_files']}  "
                    f"frameworks={manifest['frameworks_found']}  "
                    f"regressors={manifest['total_regressor_commits']}"
                )

            except Exception as e:
                print(f"ERROR: {e}")
                all_manifests[bug_id] = {"status": "error", "error": str(e)}

        stats["framework_counts"] = dict(stats["framework_counts"])
        stats["framework_to_bugs"]  = dict(stats["framework_to_bugs"])
        stats["framework_to_files"] = dict(stats["framework_to_files"])
        stats["change_type_counts"] = dict(stats["change_type_counts"])
        stats["extension_counts"]   = dict(stats["extension_counts"])

        self.writer.save_all_manifests(all_manifests)
        self._print_summary(stats)
        self._save_report(stats, all_manifests)
        return {"stats": stats, "manifests": all_manifests}

    def _print_summary(self, s: Dict):
        total = s["total_bugs_processed"]
        print("\n" + "=" * 80)
        print("STEP SUMMARY")
        print("=" * 80)
        print(f"  Total bugs processed             : {total}")
        print(f"  Bugs with run manifests          : {s['bugs_with_manifests']}")
        print(f"  Bugs skipped (no tests)          : {s['bugs_skipped_no_tests']}")
        print(f"  Total test files                 : {s['total_test_files']}")
        print(f"  Total fixing commits             : {s['total_fixing_commits']}")
        print(f"  Total regressor commits          : {s['total_regressor_commits']}")
        print(f"  Bugs with no regressor commits   : {s['bugs_with_no_regressor']}")

        print(f"\n  ── Test frameworks (by bug count) ──────────────────────────")
        for fw, count in sorted(s["framework_counts"].items(), key=lambda x: -x[1]):
            file_count = len(s["framework_to_files"].get(fw, []))
            print(f"    {fw:25s}: {count:4d} bug(s)   {file_count:4d} file(s)")

        print(f"\n  ── File change types ───────────────────────────────────────")
        for ct, count in sorted(s["change_type_counts"].items(), key=lambda x: -x[1]):
            print(f"    {ct:25s}: {count:4d}")

        print(f"\n  ── File extensions ─────────────────────────────────────────")
        for ext, count in sorted(s["extension_counts"].items(), key=lambda x: -x[1]):
            print(f"    {ext:25s}: {count:4d}")

    def _save_report(self, stats: Dict, manifests: Dict):
        rp = self.output_base / "statistics_report.txt"
        lines = [
            "=" * 80, "TEST MANIFEST STATISTICS REPORT", "=" * 80,
            f"Generated: {datetime.now().isoformat()}", "",
            f"Total bugs processed             : {stats['total_bugs_processed']}",
            f"Bugs with run manifests          : {stats['bugs_with_manifests']}",
            f"Bugs skipped (no tests)          : {stats['bugs_skipped_no_tests']}",
            f"Total test files                 : {stats['total_test_files']}",
            f"Total fixing commits             : {stats['total_fixing_commits']}",
            f"Total regressor commits          : {stats['total_regressor_commits']}",
            f"Bugs with no regressor commits   : {stats['bugs_with_no_regressor']}",
            "", "Test frameworks found:",
        ]
        for fw, count in sorted(stats["framework_counts"].items(), key=lambda x: -x[1]):
            lines.append(f"  {fw:25s}: {count}")

        lines += ["", "=" * 80, "PER-BUG RESULTS", "=" * 80, ""]
        for bug_id, m in manifests.items():
            if "error" in m:
                lines.append(f"Bug {bug_id}  [ERROR: {m.get('error')}]")
            else:
                lines.append(
                    f"Bug {bug_id}  "
                    f"frameworks={m.get('frameworks_found')}  "
                    f"test_files={m.get('total_test_files')}  "
                    f"fixing_commits={m.get('total_fixing_commits')}  "
                    f"regressor_commits={m.get('total_regressor_commits')}"
                )
                for fc in m.get("fixing_commits", []):
                    lines.append(f"  [fix] {fc['commit_hash'][:12]}")
                    for tf in fc.get("test_files", []):
                        lines.append(f"        {tf['mach_command']}")
                for rc in m.get("regressor_commits", []):
                    lines.append(
                        f"  [reg] {rc['commit_hash'][:12]}  "
                        f"(bug {rc['regressor_bug_id']})"
                    )
            lines.append("")

        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n✓ statistics_report.txt  → {rp}")
        print(f"✓ all_bugs_manifest.json → {self.output_base / 'all_bugs_manifest.json'}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    pipeline = testManifestPipeline()
    pipeline.run()

    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()

