#!/usr/bin/env python3
"""
================================================================================
REGRESSION TEST EXTRACTOR
================================================================================
Reads single-commit bugs from Step 4 output and extracts regression test files
that were added or modified in the fixing commit.

Input (from Step 4):
  outputs/step4_single_commit_regressor_match/bugs_with_single_commit_regressor_commit/bugs/
  outputs/step4_single_commit_regressor_match/bugs_with_single_commit_no_match/bugs/

Output:
  outputs/regression_test_extraction/
    bugs_with_regressor_file_overlap/
      bug_<id>/
        bug_<id>.json              ← metadata only
        added/
          <test_file>.txt          ← full content
        modified/
          <test_file>_before.txt   ← full content before fix
          <test_file>_after.txt    ← full content after fix
          <test_file>_diff.txt     ← unified diff
    bugs_without_regressor_file_overlap/
      bug_<id>/
        bug_<id>.json
        added/
          <test_file>.txt
        modified/
          <test_file>_before.txt
          <test_file>_after.txt
          <test_file>_diff.txt
"""

import json
import sys
import os
import subprocess
import re
import requests
import time
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HG_WEB_ROOTS = [
    "https://hg.mozilla.org/mozilla-central",
    "https://hg.mozilla.org/integration/autoland",
    "https://hg.mozilla.org/releases/mozilla-release",
    "https://hg.mozilla.org/releases/mozilla-esr115",
]

TEST_FILE_PATTERNS = [
    r'/test/',
    r'/tests/',
    r'/testing/',
    r'/mochitest/',
    r'/reftest/',
    r'/xpcshell/',
    r'/gtest/',
    r'/crashtest/',
    r'test_.*\..*$',
    r'.*_test\..*$',
    r'.*_tests\..*$',
    r'.*\.test\..*$',
    r'/browser_.*\.js$',
    r'/unit/.*\.js$',
    r'\.mochitest\.',
    r'\.reftest\.',
    r'/gtests/',
]

ADDED_STATUS    = 'A'
MODIFIED_STATUS = 'M'


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def is_test_file(filepath: str) -> bool:
    for pattern in TEST_FILE_PATTERNS:
        if re.search(pattern, filepath, re.IGNORECASE):
            return True
    return False


def safe_filename(filepath: str) -> str:
    """Convert a repo filepath to a safe flat filename (replaces / with __)."""
    return Path(filepath).name


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class RegressionTestExtractor:

    def __init__(self, max_workers: int = 4, debug: bool = False,
                 request_delay: float = 0.5):
        self.max_workers   = max_workers
        self.debug         = debug
        self.request_delay = request_delay
        self.print_lock    = threading.Lock()
        self.result_lock   = threading.Lock()
        self._web_lock     = threading.Lock()

        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        # ── Input dirs from Step 4 ────────────────────────────────────────
        step4_root = (self.outputs_base
                      / "step4_single_commit_regressor_match")

        self.input_dirs = {
            "bugs_with_regressor_file_overlap": (
                step4_root
                / "bugs_with_single_commit_regressor_commit"
                / "bugs"
            ),
            "bugs_without_regressor_file_overlap": (
                step4_root
                / "bugs_with_single_commit_no_match"
                / "bugs"
            ),
        }

        # ── Output root (same level as step4 inside outputs/) ────────────
        self.output_base = self.outputs_base / "regression_test_extraction"
        self.output_base.mkdir(parents=True, exist_ok=True)

        # ── Local hg repos ────────────────────────────────────────────────
        self.local_repos = {
            'mozilla-central':  './mozilla-central',
            'mozilla-autoland': './mozilla-autoland',
            'mozilla-release':  './mozilla-release',
            'mozilla-esr115':   './mozilla-esr115',
        }
        self.available_repos = {
            name: path
            for name, path in self.local_repos.items()
            if os.path.exists(path)
        }

        print(f"Output : {self.output_base}")
        print(f"\nLocal repos:")
        for name, path in self.local_repos.items():
            mark = "✓" if name in self.available_repos else "✗"
            print(f"  {mark} {name}: {path}")
        print(f"\nDebug: {self.debug}  Workers: {self.max_workers}\n")

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg: str):
        with self.print_lock:
            print(msg)

    def _dbg(self, msg: str):
        if self.debug:
            self._log(f"    [DEBUG] {msg}")

    # ── Load Step 4 bug JSONs ─────────────────────────────────────────────

    def load_bugs(self) -> Dict[str, Dict]:
        """
        Returns {bug_id: bug_data} where bug_data includes
        '_category' key to know which output folder it belongs to.
        """
        bugs = {}
        for category, directory in self.input_dirs.items():
            if not directory.exists():
                print(f"  WARNING: directory not found: {directory}")
                continue
            count = 0
            for f in directory.glob("bug_*.json"):
                try:
                    with open(f) as fp:
                        bug = json.load(fp)
                    bid = str(bug.get('bug_id', ''))
                    if bid:
                        bug['_category'] = category
                        bugs[bid] = bug
                        count += 1
                except Exception as e:
                    print(f"  Warning: {f.name}: {e}")
            print(f"  Loaded {count:4d} bugs  ←  {category}")
        return bugs

    # ── File status at a commit ───────────────────────────────────────────

    def _file_statuses_local(self, commit_hash: str) -> Optional[Dict[str, str]]:
        for repo_name, repo_path in self.available_repos.items():
            try:
                r = subprocess.run(
                    ['hg', 'status', '--change', commit_hash, '-A'],
                    cwd=repo_path, capture_output=True,
                    text=True, timeout=30
                )
                if r.returncode == 0 and r.stdout.strip():
                    statuses = {}
                    for line in r.stdout.strip().splitlines():
                        if len(line) >= 3:
                            statuses[line[2:].strip()] = line[0]
                    if statuses:
                        self._dbg(f"statuses via local {repo_name} "
                                  f"({commit_hash[:12]}): {len(statuses)} files")
                        return statuses
            except Exception as e:
                self._dbg(f"hg status error ({repo_name}): {e}")
        return None

    def _file_statuses_web(self, commit_hash: str) -> Optional[Dict[str, str]]:
        for root in HG_WEB_ROOTS:
            url = f"{root}/json-rev/{commit_hash}"
            try:
                with self._web_lock:
                    resp = requests.get(url, timeout=30)
                    time.sleep(self.request_delay)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                statuses = {}
                for f in data.get('added', []):
                    statuses[f] = ADDED_STATUS
                for f in data.get('modified', []):
                    statuses[f] = MODIFIED_STATUS
                for f in data.get('removed', []):
                    statuses[f] = 'R'
                # fallback if server only returns 'files'
                for f in data.get('files', []):
                    if f not in statuses:
                        statuses[f] = MODIFIED_STATUS
                if statuses:
                    self._dbg(f"statuses via web ({root}) ({commit_hash[:12]})")
                    return statuses
            except Exception as e:
                self._dbg(f"web json-rev error ({root}): {e}")
        return None

    def get_file_statuses(self, commit_hash: str) -> Dict[str, str]:
        return (self._file_statuses_local(commit_hash)
                or self._file_statuses_web(commit_hash)
                or {})

    # ── Fetch file content ────────────────────────────────────────────────

    def _content_local(self, commit_hash: str, filepath: str) -> Optional[str]:
        for repo_name, repo_path in self.available_repos.items():
            try:
                r = subprocess.run(
                    ['hg', 'cat', '-r', commit_hash, filepath],
                    cwd=repo_path, capture_output=True,
                    text=True, timeout=30, errors='replace'
                )
                if r.returncode == 0:
                    return r.stdout
            except Exception as e:
                self._dbg(f"hg cat error ({repo_name}, {filepath}): {e}")
        return None

    def _content_web(self, commit_hash: str, filepath: str) -> Optional[str]:
        for root in HG_WEB_ROOTS:
            url = f"{root}/raw-file/{commit_hash}/{filepath}"
            try:
                with self._web_lock:
                    resp = requests.get(url, timeout=30)
                    time.sleep(self.request_delay)
                if resp.status_code == 200:
                    return resp.text
            except Exception as e:
                self._dbg(f"web raw-file error ({root}, {filepath}): {e}")
        return None

    def fetch_content(self, commit_hash: str, filepath: str) -> Optional[str]:
        return (self._content_local(commit_hash, filepath)
                or self._content_web(commit_hash, filepath))

    # ── Fetch unified diff ────────────────────────────────────────────────

    def _diff_local(self, commit_hash: str, filepath: str) -> Optional[str]:
        for repo_name, repo_path in self.available_repos.items():
            try:
                r = subprocess.run(
                    ['hg', 'diff', '-c', commit_hash, '-U', '5', filepath],
                    cwd=repo_path, capture_output=True,
                    text=True, timeout=30, errors='replace'
                )
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout
            except Exception as e:
                self._dbg(f"hg diff error ({repo_name}, {filepath}): {e}")
        return None

    def _diff_web(self, commit_hash: str, filepath: str) -> Optional[str]:
        for root in HG_WEB_ROOTS:
            url = f"{root}/raw-diff/{commit_hash}/{filepath}"
            try:
                with self._web_lock:
                    resp = requests.get(url, timeout=30)
                    time.sleep(self.request_delay)
                if resp.status_code == 200 and resp.text.strip():
                    return resp.text
            except Exception as e:
                self._dbg(f"web raw-diff error ({root}, {filepath}): {e}")
        return None

    def fetch_diff(self, commit_hash: str, filepath: str) -> Optional[str]:
        return (self._diff_local(commit_hash, filepath)
                or self._diff_web(commit_hash, filepath))

    # ── Fetch content before fix (parent revision) ────────────────────────

    def _parent_hash_local(self, commit_hash: str) -> Optional[str]:
        for repo_name, repo_path in self.available_repos.items():
            try:
                r = subprocess.run(
                    ['hg', 'log', '-r', f'p1({commit_hash})',
                     '--template', '{node}'],
                    cwd=repo_path, capture_output=True,
                    text=True, timeout=15
                )
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except Exception as e:
                self._dbg(f"parent hash error ({repo_name}): {e}")
        return None

    def _parent_hash_web(self, commit_hash: str) -> Optional[str]:
        for root in HG_WEB_ROOTS:
            try:
                with self._web_lock:
                    resp = requests.get(f"{root}/json-rev/{commit_hash}",
                                        timeout=30)
                    time.sleep(self.request_delay)
                if resp.status_code == 200:
                    parents = resp.json().get('parents', [])
                    if parents:
                        return parents[0]
            except Exception as e:
                self._dbg(f"web parent hash error ({root}): {e}")
        return None

    def fetch_before_content(self, commit_hash: str,
                             filepath: str) -> Optional[str]:
        parent = (self._parent_hash_local(commit_hash)
                  or self._parent_hash_web(commit_hash))
        if not parent:
            return None
        return self.fetch_content(parent, filepath)

    # ── Save files for one test file ──────────────────────────────────────

    def _save_test_file(self, bug_dir: Path, filepath: str,
                        status: str, commit_hash: str) -> Dict:
        """
        Fetches and saves content/diff files to disk.
        Returns metadata dict for the bug JSON.
        """
        fname = safe_filename(filepath)
        errors = []

        if status == ADDED_STATUS:
            # ── Added: full content only ──────────────────────────────────
            added_dir = bug_dir / "added"
            added_dir.mkdir(exist_ok=True)

            content = self.fetch_content(commit_hash, filepath)
            if content is not None:
                (added_dir / f"{fname}.txt").write_text(content, encoding='utf-8')
            else:
                errors.append("content_unavailable")

            return {
                "filepath": filepath,
                "status":   "added",
                "files_saved": {
                    "content": f"added/{fname}.txt" if content else None,
                },
                "fetch_errors": errors,
            }

        else:
            # ── Modified: before + after + diff ──────────────────────────
            modified_dir = bug_dir / "modified"
            modified_dir.mkdir(exist_ok=True)

            saved = {}

            content_after = self.fetch_content(commit_hash, filepath)
            if content_after is not None:
                (modified_dir / f"{fname}_after.txt").write_text(
                    content_after, encoding='utf-8')
                saved['content_after'] = f"modified/{fname}_after.txt"
            else:
                errors.append("content_after_unavailable")
                saved['content_after'] = None

            content_before = self.fetch_before_content(commit_hash, filepath)
            if content_before is not None:
                (modified_dir / f"{fname}_before.txt").write_text(
                    content_before, encoding='utf-8')
                saved['content_before'] = f"modified/{fname}_before.txt"
            else:
                errors.append("content_before_unavailable")
                saved['content_before'] = None

            unified_diff = self.fetch_diff(commit_hash, filepath)
            if unified_diff:
                (modified_dir / f"{fname}_diff.txt").write_text(
                    unified_diff, encoding='utf-8')
                saved['diff'] = f"modified/{fname}_diff.txt"
            else:
                errors.append("diff_unavailable")
                saved['diff'] = None

            return {
                "filepath":    filepath,
                "status":      "modified",
                "files_saved": saved,
                "fetch_errors": errors,
            }

    # ── Process a single bug ──────────────────────────────────────────────

    def process_bug(self, bug_id: str, bug_data: Dict,
                    idx: int, total: int) -> Dict:
        category    = bug_data.get('_category', 'bugs_without_regressor_file_overlap')
        fixing      = bug_data.get('fixing_commit', {})
        commit_hash = fixing.get('commit_hash', '')

        # Output dir for this bug
        bug_dir = self.output_base / category / f"bug_{bug_id}"

        base_meta = {
            'bug_id':          bug_id,
            'category':        category,
            'fixing_commit':   commit_hash,
            'short_hash':      commit_hash[:12] if commit_hash else '',
            'commit_desc':     fixing.get('description', ''),
            'commit_author':   fixing.get('author', ''),
            'commit_pushdate': fixing.get('pushdate', ''),
            'test_files':      [],
            'summary': {
                'total_test_files': 0,
                'added_count':      0,
                'modified_count':   0,
                'fetch_errors':     0,
            }
        }

        if not commit_hash:
            self._log(f"[{idx}/{total}] Bug {bug_id}: ✗ No commit hash")
            return base_meta

        # Get file statuses — fall back to Step 4 test_files list
        statuses = self.get_file_statuses(commit_hash)
        if not statuses:
            fallback = fixing.get('test_files', [])
            statuses = {f: MODIFIED_STATUS for f in fallback}
            self._dbg(f"Bug {bug_id}: using Step4 test_files fallback "
                      f"({len(statuses)} files)")

        # Filter to test files that were added or modified
        test_statuses = {
            f: s for f, s in statuses.items()
            if s in (ADDED_STATUS, MODIFIED_STATUS) and is_test_file(f)
        }

        if not test_statuses:
            self._log(f"[{idx}/{total}] Bug {bug_id} ({commit_hash[:12]}): "
                      f"○ No test files")
            return base_meta

        # Create bug output directory
        bug_dir.mkdir(parents=True, exist_ok=True)

        # Fetch + save each test file
        test_file_meta = []
        for filepath, status in test_statuses.items():
            meta = self._save_test_file(bug_dir, filepath, status, commit_hash)
            test_file_meta.append(meta)

        added_count    = sum(1 for m in test_file_meta if m['status'] == 'added')
        modified_count = sum(1 for m in test_file_meta if m['status'] == 'modified')
        error_count    = sum(len(m['fetch_errors']) for m in test_file_meta)

        result = {
            **base_meta,
            'test_files': test_file_meta,
            'summary': {
                'total_test_files': len(test_file_meta),
                'added_count':      added_count,
                'modified_count':   modified_count,
                'fetch_errors':     error_count,
            }
        }

        # Save metadata JSON inside bug dir
        with open(bug_dir / f"bug_{bug_id}.json", 'w') as fp:
            json.dump(result, fp, indent=2)

        self._log(
            f"[{idx}/{total}] Bug {bug_id} ({commit_hash[:12]}): "
            f"✓ {len(test_file_meta)} test files "
            f"(+{added_count} added, ~{modified_count} modified, "
            f"{error_count} errors)"
        )
        return result

    # ── Main orchestrator ─────────────────────────────────────────────────

    def run(self) -> Dict:
        print("=" * 70)
        print("REGRESSION TEST EXTRACTOR")
        print("=" * 70 + "\n")

        all_bugs = self.load_bugs()
        if not all_bugs:
            print("ERROR: No bugs loaded — check Step 4 output dirs exist.")
            return {}

        print(f"\nTotal bugs to process: {len(all_bugs)}\n")
        print("=" * 70)
        print("PROCESSING")
        print("=" * 70 + "\n")

        total      = len(all_bugs)
        all_results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self.process_bug, bid, bdata, i + 1, total
                ): bid
                for i, (bid, bdata) in enumerate(all_bugs.items())
            }
            for future in as_completed(futures):
                bug_id = futures[future]
                try:
                    res = future.result()
                    with self.result_lock:
                        all_results[bug_id] = res
                except Exception as e:
                    self._log(f"  ERROR bug {bug_id}: {e}")
                    all_results[bug_id] = {'bug_id': bug_id, 'error': str(e)}

        return self._finalise(all_results, total)

    # ── Summary + index ───────────────────────────────────────────────────

    def _finalise(self, all_results: Dict, total: int) -> Dict:
        bugs_with    = {bid: r for bid, r in all_results.items()
                        if r.get('summary', {}).get('total_test_files', 0) > 0}
        bugs_without = {bid: r for bid, r in all_results.items()
                        if r.get('summary', {}).get('total_test_files', 0) == 0}

        summary = {
            'timestamp':               datetime.now().isoformat(),
            'total_input_bugs':        total,
            'bugs_with_test_files':    len(bugs_with),
            'bugs_without_test_files': len(bugs_without),
            'total_test_files_found':  sum(
                r['summary']['total_test_files']
                for r in bugs_with.values()),
            'total_added':    sum(r['summary']['added_count']
                                  for r in bugs_with.values()),
            'total_modified': sum(r['summary']['modified_count']
                                  for r in bugs_with.values()),
            'total_fetch_errors': sum(
                r['summary']['fetch_errors']
                for r in all_results.values() if 'summary' in r),
        }

        self._print_summary(summary)

        index = {
            'summary': summary,
            'bugs_with_test_files':    sorted(bugs_with.keys()),
            'bugs_without_test_files': sorted(bugs_without.keys()),
        }
        with open(self.output_base / "extraction_summary.json", 'w') as fp:
            json.dump(index, fp, indent=2)
        print(f"\n✓ Saved extraction_summary.json → {self.output_base}")

        return {'summary': summary, 'bugs_with': bugs_with,
                'bugs_without': bugs_without}

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total input bugs         : {s['total_input_bugs']}")
        print(f"Bugs with test files     : {s['bugs_with_test_files']}")
        print(f"Bugs without test files  : {s['bugs_without_test_files']}")
        print(f"Total test files found   : {s['total_test_files_found']}")
        print(f"  → Added                : {s['total_added']}")
        print(f"  → Modified             : {s['total_modified']}")
        print(f"Fetch errors             : {s['total_fetch_errors']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Extract regression test files from fixing commits"
    )
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--debug',   action='store_true')
    parser.add_argument('--delay',   type=float, default=0.5,
                        help='Seconds between web API calls (default: 0.5)')
    args = parser.parse_args()

    extractor = RegressionTestExtractor(
        max_workers=args.workers,
        debug=args.debug,
        request_delay=args.delay,
    )
    results = extractor.run()

    if results:
        s = results['summary']
        print(f"\n✓ DONE")
        print(f"  Bugs with test files : {s['bugs_with_test_files']}")
        print(f"  Test files extracted : {s['total_test_files_found']} "
              f"({s['total_added']} added, {s['total_modified']} modified)")
        print(f"  Fetch errors         : {s['total_fetch_errors']}")


if __name__ == "__main__":
    main()
