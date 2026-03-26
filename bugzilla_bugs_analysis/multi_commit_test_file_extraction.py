#!/usr/bin/env python3
"""
================================================================================
STEP 5: EXTRACT TEST FILES FROM FIXING COMMITS
================================================================================

PURPOSE:
--------
For each bug, look at its fixing commits from Step 4B output, identify which
files modified or added in those commits are test files, and organize bugs into:
  - bugs_with_test_files_at_fixing_commit
  - bugs_with_no_test_files_at_fixing_commit

INPUT:
------
outputs/multi_commit_diff_extraction/
├── bugs_with_fixing_commits/
│   └── bug_<ID>/
│       ├── fixing_commits/
│       │   └── <commit_hash>/
│       │       └── metadata.json
│       └── regressor_commits/
│           └── <regressor_bug_id>/
│               └── <commit_hash>/
│                   └── metadata.json
└── bugs_without_fixing_commits/
    └── bug_<ID>/  (skipped — no fixing commits to analyze)

OUTPUT STRUCTURE:
-----------------
outputs/step5_test_extraction/
├── bugs_with_test_files_at_fixing_commit/
│   └── bug_<ID>/
│       └── <commit_hash>/
│           └── test_files.json
│               {
│                 "bug_id": "...",
│                 "commit_hash": "...",
│                 "test_files": [
│                   { "filename": "test_foo.cpp", "filepath": "gfx/tests/test_foo.cpp", "change_type": "modified" },
│                   ...
│                 ]
│               }
├── bugs_with_no_test_files_at_fixing_commit/
│   └── bug_<ID>/
│       └── no_test_files.json
│           {
│             "bug_id": "...",
│             "fixing_commits_checked": [...],
│             "reason": "no test files found in any fixing commit"
│           }
├── pipeline_summary.json
└── statistics_report.txt
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
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

# Input categories from Step 4B
WITH_FIXING_INPUT    = "bugs_with_fixing_commits"
WITHOUT_FIXING_INPUT = "bugs_without_fixing_commits"

# Output categories for Step 5
WITH_TESTS    = "bugs_with_test_files_at_fixing_commit"
WITHOUT_TESTS = "bugs_with_no_test_files_at_fixing_commit"

# ---------------------------------------------------------------------------
# Mozilla/Firefox test file patterns
# These cover the main test frameworks used in the Firefox codebase:
#   - GTest        : C++ unit tests
#   - Mochitest    : browser-based JS tests
#   - XPCShell     : JS unit tests
#   - Crashtest    : crash regression tests
#   - Reftest      : rendering correctness tests
#   - WPT          : web platform tests
#   - Talos/AWSY   : performance tests
# ---------------------------------------------------------------------------
TEST_DIR_PATTERNS = [
    r"(^|/)test[s]?/",
    r"(^|/)testing/",
    r"(^|/)mochitest/",
    r"(^|/)xpcshell/",
    r"(^|/)gtest/",
    r"(^|/)gtests/",
    r"(^|/)unit/",
    r"(^|/)browser/",           # browser chrome tests
    r"(^|/)crashtest[s]?/",
    r"(^|/)reftest[s]?/",
    r"(^|/)web-platform/",
    r"(^|/)wpt/",
    r"(^|/)talos/",
    r"(^|/)awsy/",
    r"(^|/)perftests?/",
]

TEST_FILE_PATTERNS = [
    r"(^|/)test_[^/]+\.(cpp|c|h|js|py|html|xhtml|xml|ini)$",
    r"(^|/)[^/]+_test\.(cpp|c|h|js|py)$",
    r"(^|/)[^/]+\.spec\.(js|ts)$",
    r"(^|/)[^/]+Test\.(cpp|h|java)$",        # CamelCase test files
    r"(^|/)Test[^/]+\.(cpp|h|java)$",
    r"(^|/)test[^/]*\.html$",
    r"(^|/)test[^/]*\.js$",
    r"(^|/)browser_[^/]+\.js$",              # browser chrome test naming
    r"(^|/)test[^/]*\.py$",
    r"(^|/)check_[^/]+\.(cpp|c|py)$",
]

# Compile all patterns once
_TEST_DIR_RE  = [re.compile(p, re.IGNORECASE) for p in TEST_DIR_PATTERNS]
_TEST_FILE_RE = [re.compile(p, re.IGNORECASE) for p in TEST_FILE_PATTERNS]


# ===========================================================================
# TEST FILE DETECTOR
# ===========================================================================

def is_test_file(filepath: str) -> bool:
    """
    Return True if the filepath looks like a test file based on:
      1. The file lives inside a known test directory
      2. The filename matches a known test file naming convention
    """
    # Check directory patterns
    for pattern in _TEST_DIR_RE:
        if pattern.search(filepath):
            return True
    # Check filename patterns
    for pattern in _TEST_FILE_RE:
        if pattern.search(filepath):
            return True
    return False


def extract_filename(filepath: str) -> str:
    """Return just the filename from a full filepath."""
    return Path(filepath).name


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / WITH_TESTS).mkdir(exist_ok=True)
        (self.base / WITHOUT_TESTS).mkdir(exist_ok=True)

    def save_with_tests(
        self,
        bug_id:      str,
        commit_hash: str,
        test_files:  List[Dict],
    ):
        commit_dir = self.base / WITH_TESTS / f"bug_{bug_id}" / commit_hash
        commit_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id":       bug_id,
            "commit_hash":  commit_hash,
            "test_count":   len(test_files),
            "test_files":   test_files,
        }
        (commit_dir / "test_files.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def save_without_tests(
        self,
        bug_id:             str,
        commits_checked:    List[str],
        total_files_checked: int,
    ):
        bug_dir = self.base / WITHOUT_TESTS / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id":                  bug_id,
            "fixing_commits_checked":  commits_checked,
            "total_files_checked":     total_files_checked,
            "reason":                  "no test files found in any fixing commit",
        }
        (bug_dir / "no_test_files.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class testExtractorPipeline:

    def __init__(self):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.input_base  = self.outputs_base / "multi_commit_diff_extraction"
        self.output_base = self.outputs_base / "step5_test_extraction"
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.writer = OutputWriter(self.output_base)

        print(f"Input:  {self.input_base}")
        print(f"Output: {self.output_base}\n")

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_bug_dirs(self) -> List[Path]:
        """
        Only process bugs that HAD fixing commits (from bugs_with_fixing_commits).
        Bugs without fixing commits are skipped — nothing to analyze.
        """
        base = self.input_base / WITH_FIXING_INPUT
        if not base.exists():
            print(f"ERROR: {base} not found — run step4b first.")
            return []
        dirs = sorted(base.glob("bug_*/"))
        print(f"Found {len(dirs)} bugs with fixing commits to analyze\n")
        return dirs

    def load_metadata(self, path: Path) -> Optional[Dict]:
        try:
            return json.loads(path.read_text())
        except Exception as e:
            print(f"  Warning: could not read {path}: {e}")
            return None

    # ------------------------------------------------------------------
    # Per-bug processing
    # ------------------------------------------------------------------

    def process_bug(self, bug_dir: Path, idx: int, total: int) -> Dict:
        bug_id = bug_dir.name.replace("bug_", "")
        print(f"\n[{idx}/{total}] Bug {bug_id}")

        fixing_base = bug_dir / "fixing_commits"
        if not fixing_base.exists():
            print(f"    No fixing_commits directory found — skipping")
            return {
                "bug_id":              bug_id,
                "has_test_files":      False,
                "commits_with_tests":  [],
                "commits_checked":     [],
                "total_files_checked": 0,
                "total_test_files":    0,
            }

        commit_dirs       = sorted(fixing_base.iterdir())
        commits_checked   = []
        commits_with_tests = []
        total_files_checked = 0
        total_test_files    = 0

        for commit_dir in commit_dirs:
            if not commit_dir.is_dir():
                continue

            commit_hash  = commit_dir.name
            meta_path    = commit_dir / "metadata.json"
            meta         = self.load_metadata(meta_path)

            if not meta:
                print(f"    [{commit_hash[:12]}] no metadata.json — skipping")
                continue

            commits_checked.append(commit_hash)
            files_saved = meta.get("files_saved", [])
            total_files_checked += len(files_saved)

            # Filter to added or modified files only (not deleted)
            # then check if they are test files
            test_files = []
            for f in files_saved:
                filepath    = f.get("filepath", "")
                change_type = f.get("change_type", "")

                if change_type == "deleted":
                    continue   # deleted test files are not runnable

                if is_test_file(filepath):
                    test_files.append({
                        "filename":    extract_filename(filepath),
                        "filepath":    filepath,
                        "change_type": change_type,
                    })

            print(
                f"    [{commit_hash[:12]}] "
                f"{len(files_saved)} file(s) total → "
                f"{len(test_files)} test file(s)"
            )

            if test_files:
                total_test_files += len(test_files)
                commits_with_tests.append(commit_hash)
                self.writer.save_with_tests(bug_id, commit_hash, test_files)

        has_tests = len(commits_with_tests) > 0

        if not has_tests:
            self.writer.save_without_tests(
                bug_id, commits_checked, total_files_checked
            )
            print(f"    → No test files found across {len(commits_checked)} fixing commit(s)")
        else:
            print(
                f"    → Test files found in "
                f"{len(commits_with_tests)}/{len(commits_checked)} fixing commit(s) "
                f"({total_test_files} test file(s) total)"
            )

        return {
            "bug_id":               bug_id,
            "has_test_files":       has_tests,
            "commits_with_tests":   commits_with_tests,
            "commits_checked":      commits_checked,
            "total_files_checked":  total_files_checked,
            "total_test_files":     total_test_files,
        }

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> Dict:
        print("=" * 80)
        print(" EXTRACT TEST FILES FROM FIXING COMMITS")
        print("=" * 80 + "\n")

        bug_dirs = self.load_bug_dirs()
        if not bug_dirs:
            return {}

        total = len(bug_dirs)

        stats = {
            "total_bugs_analyzed":              total,
            "bugs_with_test_files":             0,
            "bugs_without_test_files":          0,
            "total_fixing_commits_checked":     0,
            "total_fixing_commits_with_tests":  0,
            "total_files_checked":              0,
            "total_test_files_found":           0,
        }

        all_results = {}

        for idx, bug_dir in enumerate(bug_dirs, 1):
            try:
                res    = self.process_bug(bug_dir, idx, total)
                bug_id = res["bug_id"]
                all_results[bug_id] = res

                stats["bugs_with_test_files"]            += int(res["has_test_files"])
                stats["bugs_without_test_files"]         += int(not res["has_test_files"])
                stats["total_fixing_commits_checked"]    += len(res["commits_checked"])
                stats["total_fixing_commits_with_tests"] += len(res["commits_with_tests"])
                stats["total_files_checked"]             += res["total_files_checked"]
                stats["total_test_files_found"]          += res["total_test_files"]

            except Exception as e:
                print(f"    ERROR processing {bug_dir.name}: {e}")
                all_results[bug_dir.name] = {"status": "error", "error": str(e)}

        self._print_summary(stats)
        self._save_summary(stats, all_results)
        return {"stats": stats, "results": all_results}

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self, s: Dict):
        total = s["total_bugs_analyzed"]
        with_pct    = (s["bugs_with_test_files"]    / total * 100) if total else 0
        without_pct = (s["bugs_without_test_files"] / total * 100) if total else 0

        print("\n" + "=" * 80)
        print("TEST EXTRACTOR SUMMARY")
        print("=" * 80)
        print(f"  Total bugs analyzed                    : {total}")
        print(f"  Bugs WITH test files at fixing commit  : {s['bugs_with_test_files']} ({with_pct:.1f}%)")
        print(f"  Bugs WITHOUT test files at fixing commit: {s['bugs_without_test_files']} ({without_pct:.1f}%)")
        print(f"  Total fixing commits checked           : {s['total_fixing_commits_checked']}")
        print(f"  Fixing commits that had test files     : {s['total_fixing_commits_with_tests']}")
        print(f"  Total files checked across all commits : {s['total_files_checked']}")
        print(f"  Total test files found                 : {s['total_test_files_found']}")

    def _save_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "statistics":         stats,
            "per_bug":            results,
        }
        sp = self.output_base / "pipeline_summary.json"
        sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ pipeline_summary.json → {sp}")

        rp = self.output_base / "statistics_report.txt"
        total = stats["total_bugs_analyzed"]
        with_pct    = (stats["bugs_with_test_files"]    / total * 100) if total else 0
        without_pct = (stats["bugs_without_test_files"] / total * 100) if total else 0

        lines = [
            "=" * 80, "TEST_EXTRACTOR STATISTICS REPORT", "=" * 80,
            f"Generated: {datetime.now().isoformat()}", "",
            f"Total bugs analyzed                     : {total}",
            f"Bugs WITH test files at fixing commit   : {stats['bugs_with_test_files']} ({with_pct:.1f}%)",
            f"Bugs WITHOUT test files at fixing commit: {stats['bugs_without_test_files']} ({without_pct:.1f}%)",
            f"Total fixing commits checked            : {stats['total_fixing_commits_checked']}",
            f"Fixing commits that had test files      : {stats['total_fixing_commits_with_tests']}",
            f"Total files checked across all commits  : {stats['total_files_checked']}",
            f"Total test files found                  : {stats['total_test_files_found']}",
            "", "=" * 80, "PER-BUG RESULTS", "=" * 80, "",
        ]

        for bid, res in results.items():
            if "error" in res:
                lines.append(f"Bug {bid}  [ERROR: {res.get('error')}]")
                lines.append("")
                continue

            status = "WITH_TESTS" if res.get("has_test_files") else "NO_TESTS"
            lines.append(
                f"Bug {bid}  [{status}]  "
                f"commits_checked={len(res.get('commits_checked', []))}  "
                f"commits_with_tests={len(res.get('commits_with_tests', []))}  "
                f"test_files={res.get('total_test_files', 0)}"
            )
            for ch in res.get("commits_with_tests", []):
                lines.append(f"    commit: {ch[:12]}")
            lines.append("")

        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ statistics_report.txt  → {rp}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    pipeline = testExtractorPipeline()
    pipeline.run()

    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)
    


if __name__ == "__main__":
    main()
