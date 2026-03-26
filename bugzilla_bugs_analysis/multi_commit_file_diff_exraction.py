#!/usr/bin/env python3
"""
================================================================================
DOWNLOAD DIFFS FOR FOUND COMMITS
================================================================================

PURPOSE:
--------
Reads the fixing_commits.json and regressor_commits.json produced by multi_commit_extraction
for each bug, then for every commit listed:
  - Fetches the full unified diff (local hg repos first, remote fallback)
  - Splits the diff into per-file sections
  - Saves each file diff under its original path
  - Writes a metadata.json summarising the commit and all files touched

INPUT:
------
outputs/multi_commit_extraction/
└── bug_<ID>/
    ├── fixing_commits.json
    └── regressor_commits.json

OUTPUT STRUCTURE:
-----------------
outputs/multi_commit_diff_extraction/
├── bugs_with_fixing_commits/
│   └── bug_<ID>/
│       ├── fixing_commits/
│       │   └── <commit_hash>/
│       │       ├── <original/path/to/file.cpp>
│       │       └── metadata.json
│       └── regressor_commits/
│           └── <regressor_bug_id>/
│               └── <commit_hash>/
│                   ├── <original/path/to/file.cpp>
│                   └── metadata.json
├── bugs_without_fixing_commits/
│   └── bug_<ID>/
│       └── regressor_commits/
│           └── <regressor_bug_id>/
│               └── <commit_hash>/
│                   ├── <original/path/to/file.cpp>
│                   └── metadata.json
├── pipeline_summary.json
└── statistics_report.txt
"""

import json
import os
import re
import subprocess
import sys
import time
import requests
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

HG_BASE = "https://hg.mozilla.org"

HG_REMOTE_REPOS = [
    "mozilla-central",
    "integration/autoland",
    "releases/mozilla-esr128",
    "releases/mozilla-esr115",
]

LOCAL_REPOS = {
    "mozilla-central":  "./mozilla-central",
    "mozilla-autoland": "./mozilla-autoland",
    "mozilla-release":  "./mozilla-release",
    "mozilla-esr115":   "./mozilla-esr115",
}

REMOTE_TO_LOCAL = {
    "mozilla-central":           "mozilla-central",
    "integration/autoland":      "mozilla-autoland",
    "releases/mozilla-esr128":   "mozilla-esr115",
    "releases/mozilla-esr115":   "mozilla-esr115",
}

HINT_TO_REMOTE = {
    "mozilla-central":           "mozilla-central",
    "central":                   "mozilla-central",
    "autoland":                  "integration/autoland",
    "integration/autoland":      "integration/autoland",
    "mozilla-autoland":          "integration/autoland",
    "mozilla-esr128":            "releases/mozilla-esr128",
    "releases/mozilla-esr128":   "releases/mozilla-esr128",
    "mozilla-esr115":            "releases/mozilla-esr115",
    "releases/mozilla-esr115":   "releases/mozilla-esr115",
}

DIFF_DELAY = 0.25

# Folder names for the two categories
WITH_FIXING    = "bugs_with_fixing_commits"
WITHOUT_FIXING = "bugs_without_fixing_commits"


# ===========================================================================
# LOCAL REPO MANAGER
# ===========================================================================

class LocalRepoManager:

    def __init__(self):
        self.available: Dict[str, str] = {}
        print("Local Mercurial repositories:")
        for name, path in LOCAL_REPOS.items():
            if os.path.isdir(path):
                self.available[name] = path
                print(f"  ✓  {name}: {path}")
            else:
                print(f"  ✗  {name}: {path} (not found)")
        print()

    def get_diff(
        self,
        commit_hash: str,
        hint_repo_name: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        order = self._repo_order(hint_repo_name)
        for repo_name in order:
            repo_path = self.available.get(repo_name)
            if not repo_path:
                continue
            try:
                r = subprocess.run(
                    ["hg", "diff", "-c", commit_hash, "-U", "8"],
                    cwd=repo_path,
                    capture_output=True, text=True, timeout=60,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout, repo_name
            except subprocess.TimeoutExpired:
                print(f"        [local diff] timeout — {repo_name}")
            except Exception as e:
                print(f"        [local diff] error — {repo_name}: {e}")
        return None, None

    def _repo_order(self, hint: Optional[str]) -> List[str]:
        order = []
        if hint and hint in self.available:
            order.append(hint)
        for name in self.available:
            if name not in order:
                order.append(name)
        return order


# ===========================================================================
# DIFF FETCHER
# ===========================================================================

class DiffFetcher:

    def __init__(self, local: LocalRepoManager, delay: float = DIFF_DELAY):
        self.local = local
        self.delay = delay
        self.http  = requests.Session()
        self.http.headers.update({"User-Agent": "Mozilla-Crash-Analysis-Research/1.0"})

    def fetch(
        self,
        commit_hash: str,
        hint_repo:   Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], str]:
        local_hint = None
        if hint_repo:
            normalised = HINT_TO_REMOTE.get(hint_repo, hint_repo)
            local_hint = REMOTE_TO_LOCAL.get(normalised, hint_repo)

        raw, repo_name = self.local.get_diff(commit_hash, local_hint)
        if raw:
            return raw, repo_name, "local"

        remote_order = self._remote_order(hint_repo)
        for remote_repo in remote_order:
            time.sleep(self.delay)
            url = f"{HG_BASE}/{remote_repo}/raw-diff/{commit_hash}"
            try:
                r = self.http.get(url, timeout=60)
                if r.status_code == 200 and r.text.strip():
                    return r.text, remote_repo, "remote"
            except Exception as e:
                print(f"        [remote diff] {url} → {e}")

        return None, None, "not_found"

    def _remote_order(self, hint: Optional[str]) -> List[str]:
        order = []
        if hint:
            normalised = HINT_TO_REMOTE.get(hint, hint)
            if normalised not in order:
                order.append(normalised)
        for r in HG_REMOTE_REPOS:
            if r not in order:
                order.append(r)
        return order


# ===========================================================================
# DIFF PARSER
# ===========================================================================

def parse_files_from_diff(raw_diff: str) -> List[Dict]:
    if not raw_diff:
        return []

    files:   List[Dict]     = []
    current: Optional[Dict] = None

    for line in raw_diff.splitlines(keepends=True):
        if line.startswith("diff "):
            if current:
                files.append(_finalise(current))
            current = {"lines": [line], "filepath": None, "change_type": "modified"}

            m = re.search(r"diff -r [0-9a-f]+ (.+)", line)
            if m:
                current["filepath"] = m.group(1).strip()
            m2 = re.search(r"diff --git a/.+ b/(.+)", line)
            if m2:
                current["filepath"] = m2.group(1).strip()

        elif current is None:
            continue
        else:
            current["lines"].append(line)

            if line.startswith("+++ "):
                path = line[4:].strip()
                # Strip any trailing tab + timestamp (e.g. "/dev/null\tThu Jan 01 ...")
                path = path.split("\t")[0].strip()
                if path.startswith("b/"):
                    path = path[2:]
                if path != "/dev/null":
                    current["filepath"] = path

            if line.startswith("--- /dev/null"):
                current["change_type"] = "added"
            if line.startswith("+++ /dev/null"):
                current["change_type"] = "deleted"
            if line.startswith("copy to "):
                current["filepath"]    = line[8:].strip()
                current["change_type"] = "added"

    if current:
        files.append(_finalise(current))

    return [f for f in files if f["filepath"]]


def _finalise(cur: dict) -> dict:
    return {
        "filepath":    cur["filepath"],
        "change_type": cur["change_type"],
        "diff_text":   "".join(cur["lines"]),
    }


# ===========================================================================
# OUTPUT WRITER
# ===========================================================================

class OutputWriter:

    def __init__(self, base_dir: Path):
        self.base = base_dir
        self.base.mkdir(parents=True, exist_ok=True)
        # Pre-create the two category folders
        (self.base / WITH_FIXING).mkdir(exist_ok=True)
        (self.base / WITHOUT_FIXING).mkdir(exist_ok=True)

    def bug_dir(self, bug_id: str, has_fixing: bool) -> Path:
        """Return the correct category folder for this bug."""
        category = WITH_FIXING if has_fixing else WITHOUT_FIXING
        return self.base / category / f"bug_{bug_id}"

    def save_commit(
        self,
        bug_id:      str,
        commit_hash: str,
        commit_role: str,
        file_diffs:  List[Dict],
        meta:        Dict,
        has_fixing:  bool,
    ) -> Path:
        commit_dir = self.bug_dir(bug_id, has_fixing) / commit_role / commit_hash
        commit_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        for fd in file_diffs:
            dest = commit_dir / fd["filepath"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(fd["diff_text"], encoding="utf-8")
            saved.append({
                "filepath":    fd["filepath"],
                "change_type": fd["change_type"],
            })

        added    = sum(1 for f in saved if f["change_type"] == "added")
        modified = sum(1 for f in saved if f["change_type"] == "modified")
        deleted  = sum(1 for f in saved if f["change_type"] == "deleted")

        full_meta = {
            **meta,
            "extraction_time": datetime.now().isoformat(),
            "file_count":      len(saved),
            "files_added":     added,
            "files_modified":  modified,
            "files_deleted":   deleted,
            "files_saved":     saved,
        }
        (commit_dir / "metadata.json").write_text(
            json.dumps(full_meta, indent=2), encoding="utf-8"
        )
        return commit_dir


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

class fileDiffPipeline:

    def __init__(self, diff_delay: float = DIFF_DELAY):
        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        self.input_base  = self.outputs_base / "multi_commit_extraction"
        self.output_base = self.outputs_base / "multi_commit_diff_extraction"
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.local   = LocalRepoManager()
        self.fetcher = DiffFetcher(self.local, delay=diff_delay)
        self.writer  = OutputWriter(self.output_base)

        print(f"Input:  {self.input_base}")
        print(f"Output: {self.output_base}\n")

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_bug_dirs(self) -> List[Path]:
        if not self.input_base.exists():
            print(f"ERROR: {self.input_base} not found — run step4a first.")
            return []
        dirs = sorted(self.input_base.glob("bug_*/"))
        print(f"Found {len(dirs)} bug directories in step4a output\n")
        return dirs

    def load_json(self, path: Path) -> Optional[Dict]:
        try:
            return json.loads(path.read_text())
        except Exception as e:
            print(f"  Warning: could not read {path}: {e}")
            return None

    # ------------------------------------------------------------------
    # Per-commit processing
    # ------------------------------------------------------------------

    def process_commit(
        self,
        bug_id:      str,
        commit:      Dict,
        commit_role: str,
        base_meta:   Dict,
        has_fixing:  bool,
    ) -> Dict:
        commit_hash = commit.get("commit_hash", "")
        hint_repo   = commit.get("hint_repo", "") or None
        short_hash  = commit_hash[:12]

        print(f"        {short_hash}  hint={hint_repo or 'none'} …", end=" ")
        raw_diff, repo_used, diff_source = self.fetcher.fetch(commit_hash, hint_repo)

        if not raw_diff:
            print("✗ not found")
            return {
                "commit_hash": commit_hash,
                "short_hash":  short_hash,
                "repo_used":   None,
                "diff_source": "not_found",
                "status":      "diff_not_found",
                "file_count":  0,
            }

        file_diffs = parse_files_from_diff(raw_diff)
        print(f"✓ [{diff_source}] repo={repo_used} | {len(file_diffs)} file(s)")

        meta = {
            **base_meta,
            "commit_hash":  commit_hash,
            "short_hash":   short_hash,
            "description":  commit.get("description", ""),
            "author":       commit.get("author", ""),
            "pushdate":     commit.get("pushdate", ""),
            "hint_repo":    hint_repo,
            "repo_used":    repo_used,
            "diff_source":  diff_source,
            "find_method":  commit.get("find_method", base_meta.get("find_method", "")),
        }

        self.writer.save_commit(
            bug_id=bug_id,
            commit_hash=commit_hash,
            commit_role=commit_role,
            file_diffs=file_diffs,
            meta=meta,
            has_fixing=has_fixing,
        )

        return {
            "commit_hash": commit_hash,
            "short_hash":  short_hash,
            "repo_used":   repo_used,
            "diff_source": diff_source,
            "status":      "ok",
            "file_count":  len(file_diffs),
        }

    # ------------------------------------------------------------------
    # Per-bug processing
    # ------------------------------------------------------------------

    def process_bug(
        self, bug_dir: Path, idx: int, total: int
    ) -> Dict:
        bug_id = bug_dir.name.replace("bug_", "")
        print(f"\n[{idx}/{total}] Bug {bug_id}")

        result = {
            "bug_id":            bug_id,
            "fixing_results":    [],
            "regressor_results": [],
        }

        # ── Determine whether this bug has fixing commits ───────────────
        fixing_path = bug_dir / "fixing_commits.json"
        fixing_data = self.load_json(fixing_path)
        fixing_commits = fixing_data.get("commits", []) if fixing_data else []
        has_fixing = len(fixing_commits) > 0

        category = WITH_FIXING if has_fixing else WITHOUT_FIXING
        print(f"    Category: {category}")

        # ── Fixing commits ──────────────────────────────────────────────
        if fixing_data:
            find_method = fixing_data.get("find_method", "")
            print(f"    Fixing commits: {len(fixing_commits)}")

            for commit in fixing_commits:
                r = self.process_commit(
                    bug_id=bug_id,
                    commit=commit,
                    commit_role="fixing_commits",
                    base_meta={
                        "role":           "fixing",
                        "crashed_bug_id": bug_id,
                        "find_method":    find_method,
                        "category":       category,
                    },
                    has_fixing=has_fixing,
                )
                result["fixing_results"].append(r)
        else:
            print(f"    No fixing_commits.json found")

        # ── Regressor commits ───────────────────────────────────────────
        regressor_path = bug_dir / "regressor_commits.json"
        regressor_data = self.load_json(regressor_path)

        if regressor_data:
            regressors = regressor_data.get("regressors", [])
            for reg in regressors:
                reg_bug_id  = reg.get("regressor_bug_id", "")
                commits     = reg.get("commits", [])
                find_method = reg.get("find_method", "")
                print(f"    Regressor bug {reg_bug_id}: {len(commits)} commit(s)")

                for commit in commits:
                    r = self.process_commit(
                        bug_id=bug_id,
                        commit=commit,
                        commit_role=f"regressor_commits/{reg_bug_id}",
                        base_meta={
                            "role":             "regressor",
                            "crashed_bug_id":   bug_id,
                            "regressor_bug_id": reg_bug_id,
                            "find_method":      find_method,
                            "category":         category,
                        },
                        has_fixing=has_fixing,
                    )
                    result["regressor_results"].append(
                        {**r, "regressor_bug_id": reg_bug_id}
                    )
        else:
            print(f"    No regressor_commits.json found")

        result["category"] = category
        return result

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> Dict:
        print("=" * 80)
        print("STEP 4B: DOWNLOAD DIFFS FOR FOUND COMMITS")
        print("=" * 80 + "\n")

        bug_dirs = self.load_bug_dirs()
        if not bug_dirs:
            return {}

        total = len(bug_dirs)

        stats = {
            "total_bugs":                    total,
            "bugs_with_fixing_commits":      0,
            "bugs_without_fixing_commits":   0,
            "total_fixing_commits":          0,
            "total_regressor_commits":       0,
            "total_files_in_fixing":         0,
            "total_files_in_regressors":     0,
            "diff_source_counts":            defaultdict(int),
            "diff_not_found_count":          0,
            "bugs_with_fixing_diffs":        0,
            "bugs_with_regressor_diffs":     0,
        }

        all_results = {}

        for idx, bug_dir in enumerate(bug_dirs, 1):
            try:
                res    = self.process_bug(bug_dir, idx, total)
                bug_id = res["bug_id"]
                all_results[bug_id] = res

                has_fixing = res["category"] == WITH_FIXING
                stats["bugs_with_fixing_commits"]    += int(has_fixing)
                stats["bugs_without_fixing_commits"] += int(not has_fixing)

                fix_ok   = [r for r in res["fixing_results"]    if r["status"] == "ok"]
                reg_ok   = [r for r in res["regressor_results"] if r["status"] == "ok"]
                fix_fail = [r for r in res["fixing_results"]    if r["status"] != "ok"]
                reg_fail = [r for r in res["regressor_results"] if r["status"] != "ok"]

                stats["total_fixing_commits"]      += len(res["fixing_results"])
                stats["total_regressor_commits"]   += len(res["regressor_results"])
                stats["total_files_in_fixing"]     += sum(r["file_count"] for r in fix_ok)
                stats["total_files_in_regressors"] += sum(r["file_count"] for r in reg_ok)
                stats["diff_not_found_count"]      += len(fix_fail) + len(reg_fail)
                stats["bugs_with_fixing_diffs"]    += int(bool(fix_ok))
                stats["bugs_with_regressor_diffs"] += int(bool(reg_ok))

                for r in res["fixing_results"] + res["regressor_results"]:
                    stats["diff_source_counts"][r.get("diff_source", "?")] += 1

            except Exception as e:
                print(f"    ERROR processing {bug_dir.name}: {e}")
                all_results[bug_dir.name] = {"status": "error", "error": str(e)}

        stats["diff_source_counts"] = dict(stats["diff_source_counts"])

        self._print_summary(stats)
        self._save_summary(stats, all_results)
        return {"stats": stats, "results": all_results}

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 80)
        print("FILE_DIFF_EXTRACTOR SUMMARY")
        print("=" * 80)
        print(f"  Total bugs processed             : {s['total_bugs']}")
        print(f"  Bugs WITH fixing commits         : {s['bugs_with_fixing_commits']}")
        print(f"  Bugs WITHOUT fixing commits      : {s['bugs_without_fixing_commits']}")
        print(f"  Bugs with fixing diffs           : {s['bugs_with_fixing_diffs']}")
        print(f"  Bugs with regressor diffs        : {s['bugs_with_regressor_diffs']}")
        print(f"  Total fixing commits processed   : {s['total_fixing_commits']}")
        print(f"  Total regressor commits processed: {s['total_regressor_commits']}")
        print(f"  Total files saved (fixing)       : {s['total_files_in_fixing']}")
        print(f"  Total files saved (regressor)    : {s['total_files_in_regressors']}")
        print(f"  Diff fetch failures              : {s['diff_not_found_count']}")
        print(f"\n  Diff sources:")
        for src, c in s["diff_source_counts"].items():
            print(f"    {src:15s}: {c}")

    def _save_summary(self, stats: Dict, results: Dict):
        summary = {
            "pipeline_timestamp": datetime.now().isoformat(),
            "statistics":         stats,
            "per_bug": {
                bid: {
                    "category":          res.get("category", ""),
                    "fixing_results":    res.get("fixing_results", []),
                    "regressor_results": res.get("regressor_results", []),
                }
                for bid, res in results.items()
            },
        }
        sp = self.output_base / "pipeline_summary.json"
        sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n✓ pipeline_summary.json → {sp}")

        rp = self.output_base / "statistics_report.txt"
        lines = [
            "=" * 80, "FILE_DIFF_EXTRACTOR STATISTICS REPORT", "=" * 80,
            f"Generated: {datetime.now().isoformat()}", "",
            f"Total bugs processed             : {stats['total_bugs']}",
            f"Bugs WITH fixing commits         : {stats['bugs_with_fixing_commits']}",
            f"Bugs WITHOUT fixing commits      : {stats['bugs_without_fixing_commits']}",
            f"Bugs with fixing diffs           : {stats['bugs_with_fixing_diffs']}",
            f"Bugs with regressor diffs        : {stats['bugs_with_regressor_diffs']}",
            f"Total fixing commits processed   : {stats['total_fixing_commits']}",
            f"Total regressor commits processed: {stats['total_regressor_commits']}",
            f"Total files saved (fixing)       : {stats['total_files_in_fixing']}",
            f"Total files saved (regressor)    : {stats['total_files_in_regressors']}",
            f"Diff fetch failures              : {stats['diff_not_found_count']}",
            "", "Diff sources:",
        ]
        for src, c in stats["diff_source_counts"].items():
            lines.append(f"  {src:15s}: {c}")

        lines += ["", "=" * 80, "PER-BUG RESULTS", "=" * 80, ""]
        for bid, res in results.items():
            if "error" in res:
                lines.append(f"Bug {bid}  [ERROR: {res.get('error')}]")
                lines.append("")
                continue
            category = res.get("category", "unknown")
            lines.append(f"Bug {bid}  [{category}]")
            for r in res.get("fixing_results", []):
                lines.append(
                    f"  [fix]  {str(r.get('short_hash') or '?'):12s}  "
                    f"repo={str(r.get('repo_used') or '?'):25s}  "
                    f"src={str(r.get('diff_source') or '?'):8s}  "
                    f"files={r.get('file_count') or 0:4d}  [{r.get('status')}]"
                )
            for r in res.get("regressor_results", []):
                lines.append(
                    f"  [reg]  {str(r.get('short_hash') or '?'):12s}  "
                    f"reg_bug={str(r.get('regressor_bug_id') or '?'):10s}  "
                    f"repo={str(r.get('repo_used') or '?'):25s}  "
                    f"src={str(r.get('diff_source') or '?'):8s}  "
                    f"files={r.get('file_count') or 0:4d}  [{r.get('status')}]"
                )
            lines.append("")

        rp.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ statistics_report.txt  → {rp}")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Download diffs for all commits found in multiple-commit_extractor step"
    )
    parser.add_argument(
        "--diff-delay", type=float, default=DIFF_DELAY,
        help=f"Seconds between remote hg diff requests (default: {DIFF_DELAY})"
    )
    args = parser.parse_args()

    pipeline = fileDiffPipeline(diff_delay=args.diff_delay)
    pipeline.run()

    print("\n" + "=" * 80)
    print("✓  STEP COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
