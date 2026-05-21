#!/usr/bin/env python3
"""
================================================================================
EXTRACT PARENT COMMITS FOR FIXING COMMITS WITH TEST FILES
================================================================================

PURPOSE:
--------
For each bug that has test files at its fixing commit(s) (multi_fixing_commit_tests), find
the parent commit(s) of each fixing commit from mozilla-central or autoland.
The output JSON also includes enriched test file info (framework + mach command)
pulled from the manifest builder logic so the file is self-contained for running.

Parent lookup tries the local Mercurial repos first, then falls back to the
hg.mozilla.org HTTP API.

INPUT:
------
outputs/multi_fixing_commit_tests/
└── bugs_with_test_files_at_fixing_commit/
    └── bug_<ID>/
        └── <commit_hash>/
            └── test_files.json

OUTPUT STRUCTURE:
-----------------
outputs/multi_fixing_commit_parent/
└── bugs/
    └── bug_<ID>/
        └── <fixing_commit_hash>/
            └── <parent_commit_hash>/
                └── parent_info.json
                    {
                      "bug_id": "...",
                      "fixing_commit": "...",
                      "parent_commit": "...",
                      "repo_found_in": "mozilla-central" | "autoland" | "api:...",
                      "parent_metadata": {
                        "author": "...",
                        "date": "...",
                        "message": "..."
                      },
                      "test_files": [
                        {
                          "filename": "test_foo.js",
                          "filepath": "dom/tests/test_foo.js",
                          "change_type": "modified",
                          "framework": "mochitest",
                          "mach_command": "./mach mochitest dom/tests/test_foo.js"
                        },
                        ...
                      ]
                    }
├── pipeline_summary.json
└── statistics_report.txt
"""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
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

STEP5_INPUT_DIR = "multi_fixing_commit_tests"
WITH_TESTS_DIR  = "bugs_with_test_files_at_fixing_commit"
OUTPUT_DIR      = "fixing_commit_parent"

LOCAL_REPOS: Dict[str, Path] = {
    "mozilla-central": Path("./mozilla-central"),
    "autoland":        Path("./autoland"),
}

API_REPOS: Dict[str, str] = {
    "mozilla-central": "https://hg.mozilla.org/mozilla-central",
    "autoland":        "https://hg.mozilla.org/integration/autoland",
}

HTTP_TIMEOUT = 30


# ===========================================================================
# FRAMEWORK DETECTOR  (mirrors multi_commit_test_run_manifest.py)
# ===========================================================================

"""FRAMEWORK_RULES = [
    ("jit-test/",      "jit-test",           "./mach jit-test {path}"),
    ("jit-tests/",     "jit-test",           "./mach jit-test {path}"),
    ("xpcshell/",      "xpcshell",           "./mach xpcshell-test {path}"),
    ("xpcshell-",      "xpcshell",           "./mach xpcshell-test {path}"),
    ("browser/",       "mochitest-browser",  "./mach mochitest {path}"),
    ("mochitest/",     "mochitest",          "./mach mochitest {path}"),
    ("crashtests/",    "crashtest",          "./mach crashtest {path}"),
    ("crashtest/",     "crashtest",          "./mach crashtest {path}"),
    ("reftests/",      "reftest",            "./mach reftest {path}"),
    ("reftest/",       "reftest",            "./mach reftest {path}"),
    ("web-platform/",  "wpt",               "./mach wpt {path}"),
    ("wpt/",           "wpt",               "./mach wpt {path}"),
    ("python/",        "python-test",        "./mach python-test {path}"),
    ("talos/",         "talos",              "./mach talos-test {path}"),
    ("tests/",         "unknown",            "./mach test {path}"),
    ("testing/",       "unknown",            "./mach test {path}"),
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
    fp_lower = filepath.lower()
    for substring, framework, cmd_template in FRAMEWORK_RULES:
        if substring in fp_lower:
            return framework, cmd_template.replace("{path}", filepath)
    return "unknown", f"./mach test {filepath}"


def enrich_test_files(raw_test_files: List[Dict]) -> List[Dict]:
    """
    Take the raw test_files list from test_files.json and add
    'framework' and 'mach_command' to each entry.
    """
    enriched = []
    for tf in raw_test_files:
        filepath = tf.get("filepath", "")
        framework, mach_cmd = detect_framework(filepath)
        enriched.append({
            "filename":     tf.get("filename", ""),
            "filepath":     filepath,
            "change_type":  tf.get("change_type", ""),
            "framework":    framework,
            "mach_command": mach_cmd,
        })
    return enriched


# ===========================================================================
# MERCURIAL HELPERS
# ===========================================================================

def _hg_local(repo_path: Path, commit_hash: str) -> Optional[List[Dict]]:
    if not repo_path.exists():
        return None
    template = "{node}\\x1f{author}\\x1f{date|isodate}\\x1f{desc}\\x1e"
    try:
        result = subprocess.run(
            ["hg", "log", "--rev", f"parents({commit_hash})",
             "--template", template, "-R", str(repo_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return _parse_hg_template_output(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _hg_api(repo_name: str, commit_hash: str) -> Optional[List[Dict]]:
    base = API_REPOS.get(repo_name)
    if not base:
        return None
    rev_data = _fetch_json(f"{base}/json-rev/{commit_hash}")
    if not rev_data:
        return None
    raw_parents = rev_data.get("parents", [])
    if not raw_parents:
        return None
    parents = []
    for p_hash in raw_parents:
        if all(c == "0" for c in p_hash):
            continue
        meta = _fetch_parent_meta_api(base, p_hash)
        if meta:
            parents.append(meta)
    return parents if parents else None


def _fetch_parent_meta_api(base_url: str, parent_hash: str) -> Optional[Dict]:
    data = _fetch_json(f"{base_url}/json-rev/{parent_hash}")
    if not data:
        return None
    return {
        "hash":    data.get("node", parent_hash),
        "author":  data.get("user", ""),
        "date":    data.get("date", [None])[0],
        "message": data.get("desc", ""),
    }


def _fetch_json(url: str) -> Optional[Dict]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _parse_hg_template_output(raw: str) -> Optional[List[Dict]]:
    records = [r for r in raw.split("\x1e") if r.strip()]
    if not records:
        return None
    parents = []
    for rec in records:
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        parents.append({
            "hash":    parts[0].strip(),
            "author":  parts[1].strip(),
            "date":    parts[2].strip(),
            "message": parts[3].strip(),
        })
    return parents if parents else None


def find_parents(commit_hash: str) -> Tuple[Optional[List[Dict]], str]:
    for repo_name, repo_path in LOCAL_REPOS.items():
        parents = _hg_local(repo_path, commit_hash)
        if parents:
            print(f"      [local:{repo_name}] found {len(parents)} parent(s)")
            return parents, repo_name
    for repo_name in API_REPOS:
        parents = _hg_api(repo_name, commit_hash)
        if parents:
            print(f"      [api:{repo_name}] found {len(parents)} parent(s)")
            return parents, f"api:{repo_name}"
    print(f"      [not found] no parent(s) resolved for {commit_hash[:12]}")
    return None, "not_found"


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    def __init__(self, base_dir: Path):
        self.bugs_dir = base_dir / "bugs"
        self.bugs_dir.mkdir(parents=True, exist_ok=True)

    def save_parent(
        self,
        bug_id:        str,
        fixing_commit: str,
        parent:        Dict,
        repo_label:    str,
        test_files:    List[Dict],   # already enriched
    ):
        out_dir = self.bugs_dir / f"bug_{bug_id}" / fixing_commit / parent["hash"]
        out_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "bug_id":          bug_id,
            "fixing_commit":   fixing_commit,
            "parent_commit":   parent["hash"],
            "repo_found_in":   repo_label,
            "parent_metadata": {
                "author":  parent.get("author", ""),
                "date":    parent.get("date", ""),
                "message": parent.get("message", ""),
            },
            "test_files": test_files,
        }
        (out_dir / "parent_info.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def save_no_parent(
        self,
        bug_id:        str,
        fixing_commit: str,
        test_files:    List[Dict],   # already enriched
        reason:        str,
    ):
        out_dir = self.bugs_dir / f"bug_{bug_id}" / fixing_commit / "no_parent_found"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "bug_id":        bug_id,
            "fixing_commit": fixing_commit,
            "parent_commit": None,
            "reason":        reason,
            "test_files":    test_files,
        }
        (out_dir / "parent_info.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class FixingCommitParentPipeline:

    def __init__(self):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.input_base  = self.outputs_base / STEP5_INPUT_DIR / WITH_TESTS_DIR
        self.output_base = self.outputs_base / OUTPUT_DIR
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.writer = OutputWriter(self.output_base)

        print(f"Input:  {self.input_base}")
        print(f"Output: {self.output_base}\n")

    def load_bug_dirs(self) -> List[Path]:
        if not self.input_base.exists():
            print(f"ERROR: {self.input_base} not found — run Step 5 first.")
            return []
        dirs = sorted(self.input_base.glob("bug_*/"))
        print(f"Found {len(dirs)} bug(s) with test files to process\n")
        return dirs

    def load_test_files_json(self, path: Path) -> Optional[Dict]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  Warning: could not read {path}: {e}")
            return None

    def process_bug(self, bug_dir: Path, idx: int, total: int) -> Dict:
        bug_id = bug_dir.name.replace("bug_", "")
        print(f"\n[{idx}/{total}] Bug {bug_id}")

        bug_stats = {
            "bug_id":                   bug_id,
            "fixing_commits_processed": 0,
            "parents_found":            0,
            "parents_not_found":        0,
        }

        for commit_dir in sorted(bug_dir.iterdir()):
            if not commit_dir.is_dir():
                continue

            fixing_commit = commit_dir.name
            tf_path = commit_dir / "test_files.json"
            if not tf_path.exists():
                print(f"  [{fixing_commit[:12]}] no test_files.json — skipping")
                continue

            data = self.load_test_files_json(tf_path)
            if not data:
                continue

            # Enrich test files with framework + mach_command
            enriched_files = enrich_test_files(data.get("test_files", []))

            print(
                f"  [{fixing_commit[:12]}] "
                f"{len(enriched_files)} test file(s) → looking up parent(s)..."
            )

            bug_stats["fixing_commits_processed"] += 1

            parents, repo_label = find_parents(fixing_commit)

            if parents:
                for p in parents:
                    self.writer.save_parent(
                        bug_id, fixing_commit, p, repo_label, enriched_files
                    )
                    print(f"      saved parent {p['hash'][:12]}")
                bug_stats["parents_found"] += len(parents)
            else:
                self.writer.save_no_parent(
                    bug_id, fixing_commit, enriched_files,
                    reason="parent not found in local repos or API"
                )
                bug_stats["parents_not_found"] += 1

        return bug_stats

    def run(self) -> Dict:
        print("=" * 80)
        print("EXTRACT PARENT COMMITS FOR FIXING COMMITS WITH TEST FILES")
        print("=" * 80 + "\n")

        bug_dirs = self.load_bug_dirs()
        if not bug_dirs:
            return {}

        total = len(bug_dirs)
        stats = {
            "total_bugs_processed":         total,
            "total_fixing_commits_checked": 0,
            "total_parents_found":          0,
            "total_parents_not_found":      0,
        }
        all_results = {}

        for idx, bug_dir in enumerate(bug_dirs, 1):
            try:
                res    = self.process_bug(bug_dir, idx, total)
                bug_id = res["bug_id"]
                all_results[bug_id] = res

                stats["total_fixing_commits_checked"] += res["fixing_commits_processed"]
                stats["total_parents_found"]          += res["parents_found"]
                stats["total_parents_not_found"]      += res["parents_not_found"]

            except Exception as e:
                print(f"  ERROR processing {bug_dir.name}: {e}")
                all_results[bug_dir.name] = {"status": "error", "error": str(e)}

        self._print_summary(stats)
        self._save_summary(stats, all_results)
        return {"stats": stats, "results": all_results}

    def _print_summary(self, s: Dict):
        total_fc  = s["total_fixing_commits_checked"]
        found_pct = (s["total_parents_found"] / total_fc * 100) if total_fc else 0
        print("\n" + "=" * 80)
        print("FIXING COMMIT PARENT EXTRACTION SUMMARY")
        print("=" * 80)
        print(f"  Total bugs processed              : {s['total_bugs_processed']}")
        print(f"  Total fixing commits checked      : {total_fc}")
        print(f"  Parents found                     : {s['total_parents_found']} ({found_pct:.1f}%)")
        print(f"  Fixing commits with no parent     : {s['total_parents_not_found']}")

    def _save_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "statistics":         stats,
            "per_bug":            results,
        }
        sp = self.output_base / "pipeline_summary.json"
        sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ pipeline_summary.json → {sp}")

        rp    = self.output_base / "statistics_report.txt"
        total_fc  = stats["total_fixing_commits_checked"]
        found_pct = (stats["total_parents_found"] / total_fc * 100) if total_fc else 0
        lines = [
            "=" * 80, "FIXING COMMIT PARENT EXTRACTION — STATISTICS REPORT", "=" * 80,
            f"Generated: {datetime.now().isoformat()}", "",
            f"Total bugs processed              : {stats['total_bugs_processed']}",
            f"Total fixing commits checked      : {total_fc}",
            f"Parents found                     : {stats['total_parents_found']} ({found_pct:.1f}%)",
            f"Fixing commits with no parent     : {stats['total_parents_not_found']}",
            "", "=" * 80, "PER-BUG RESULTS", "=" * 80, "",
        ]
        for bid, res in results.items():
            if "error" in res:
                lines.append(f"Bug {bid}  [ERROR: {res.get('error')}]")
            else:
                lines.append(
                    f"Bug {bid}  "
                    f"fixing_commits={res.get('fixing_commits_processed', 0)}  "
                    f"parents_found={res.get('parents_found', 0)}  "
                    f"no_parent={res.get('parents_not_found', 0)}"
                )
            lines.append("")
        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ statistics_report.txt  → {rp}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    pipeline = FixingCommitParentPipeline()
    pipeline.run()
    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
