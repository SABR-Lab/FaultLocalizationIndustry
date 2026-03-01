#!/usr/bin/env python3
"""
================================================================================
REGRESSION TEST CROSS-REFERENCE
================================================================================
For each bug, cross-references:
  - The files imported/included by test files at the fixing commit
    (from regression_test_parser output)
  - The overlapping files between the fixing and regressor commits
    (from step6 output)

If a test imports/includes a file that is also in the overlapping set,
that test is directly exercising the regressed code.

Matching strategy:
  1. Exact match   : import path == overlapping file path
  2. Filename match: basename of import == basename of overlapping file

Input:
  outputs/regression_test_parsing/
    bugs_with_regressor_file_overlap/bug_<id>.json
    bugs_without_regressor_file_overlap/bug_<id>.json
  outputs/step6_overlapping_files/bugs/bug_<id>.json

Output:
  outputs/regression_test_cross_reference/
    bugs_with_hits/bug_<id>.json       ← bugs where tests import overlapping files
    bugs_without_hits/bug_<id>.json    ← bugs with no cross-reference match
    cross_reference_summary.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def extract_all_imports(test_files: List[Dict]) -> List[Dict]:
    """
    Flatten all imports/includes from all test files into a single list.
    Each entry carries which test file the import came from.
    """
    entries = []
    for tf in test_files:
        lang     = tf.get('language', 'unknown')
        filename = tf.get('test_filename', '')
        status   = tf.get('status', '')

        paths = []
        if lang == 'cpp':
            paths = tf.get('includes', [])
        elif lang == 'javascript':
            paths = tf.get('imports', [])
        elif lang == 'html':
            paths = tf.get('script_sources', []) + tf.get('linked_resources', [])
        elif lang == 'toml_manifest':
            paths = (tf.get('registered_tests', []) +
                     tf.get('head_files', []) +
                     tf.get('support_files', []))

        for p in paths:
            entries.append({
                'import_path':     p,
                'from_test_file':  filename,
                'test_status':     status,
                'language':        lang,
            })
    return entries


def match_import_against_overlapping(
    import_path: str,
    overlapping_files: List[str]
) -> Tuple[Optional[str], str]:
    """
    Try to match an import path against the list of overlapping files.

    Returns (matched_overlapping_file, match_type) where match_type is:
      'exact'    - full path matched exactly
      'filename' - only the basename matched
      None       - no match
    """
    import_path_clean = import_path.strip()

    # ── Pass 1: exact match ───────────────────────────────────────────────
    for ovf in overlapping_files:
        if import_path_clean == ovf:
            return ovf, 'exact'
        # also check if the overlapping file ends with the import path
        # e.g. import "dom/base/nsDocument.h" matches "dom/base/nsDocument.h"
        if ovf.endswith(import_path_clean) or import_path_clean.endswith(ovf):
            return ovf, 'exact'

    # ── Pass 2: basename match ────────────────────────────────────────────
    import_basename = Path(import_path_clean).name.lower()
    if not import_basename:
        return None, 'none'

    for ovf in overlapping_files:
        ovf_basename = Path(ovf).name.lower()
        if import_basename == ovf_basename:
            return ovf, 'filename'

    return None, 'none'


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class RegressionTestCrossReference:

    PARSER_CATEGORIES = [
        "bugs_with_regressor_file_overlap",
        "bugs_without_regressor_file_overlap",
    ]

    def __init__(self, max_workers: int = 4, debug: bool = False):
        self.max_workers = max_workers
        self.debug       = debug
        self.print_lock  = threading.Lock()
        self.result_lock = threading.Lock()

        self.script_dir   = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"

        # ── Inputs ────────────────────────────────────────────────────────
        self.parser_base = self.outputs_base / "regression_test_parsing"
        self.step6_bugs  = self.outputs_base / "step6_overlapping_files" / "bugs"

        # ── Output ────────────────────────────────────────────────────────
        self.output_base = self.outputs_base / "regression_test_cross_reference"
        self.hits_dir = self.output_base / "bugs_with_hits"
        self.hits_dir.mkdir(parents=True, exist_ok=True)

        print(f"Parser input : {self.parser_base}")
        print(f"Step6 input  : {self.step6_bugs}")
        print(f"Output       : {self.output_base}\n")

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg: str):
        with self.print_lock:
            print(msg)

    def _dbg(self, msg: str):
        if self.debug:
            self._log(f"  [DEBUG] {msg}")

    # ── Load inputs ───────────────────────────────────────────────────────

    def load_parser_bugs(self) -> Dict[str, Dict]:
        """Load all bug JSONs from regression_test_parser output."""
        bugs = {}
        for cat in self.PARSER_CATEGORIES:
            cat_dir = self.parser_base / cat
            if not cat_dir.exists():
                print(f"  WARNING: not found: {cat_dir}")
                continue
            count = 0
            for f in sorted(cat_dir.glob("bug_*.json")):
                try:
                    with open(f) as fp:
                        data = json.load(fp)
                    bid = str(data.get('bug_id', ''))
                    if bid:
                        data['_parser_category'] = cat
                        bugs[bid] = data
                        count += 1
                except Exception as e:
                    print(f"  Warning: {f.name}: {e}")
            print(f"  Loaded {count:4d} parser bugs from [{cat}]")
        return bugs

    def load_step6_bug(self, bug_id: str) -> Optional[Dict]:
        """Load step6 overlapping files data for a single bug."""
        path = self.step6_bugs / f"bug_{bug_id}.json"
        if not path.exists():
            return None
        try:
            with open(path) as fp:
                return json.load(fp)
        except Exception as e:
            self._dbg(f"Could not load step6 bug {bug_id}: {e}")
            return None

    # ── Core cross-reference logic ────────────────────────────────────────

    def cross_reference_bug(self, bug_id: str, parser_data: Dict,
                            idx: int, total: int) -> Dict:
        """
        Cross-reference test imports against overlapping files for one bug.
        """
        category    = parser_data.get('_parser_category', '')
        test_files  = parser_data.get('test_files', [])

        result = {
            'bug_id':            bug_id,
            'parser_category':   category,
            'has_step6_data':    False,
            'overlapping_files': [],
            'test_imports':      [],
            'matches':           [],
            'has_hits':          False,
            'summary': {
                'total_test_files':       len(test_files),
                'total_imports':          0,
                'total_overlapping_files':0,
                'exact_matches':          0,
                'filename_matches':       0,
                'total_matches':          0,
                'matching_test_files':    0,
            }
        }

        # ── Load step6 data ───────────────────────────────────────────────
        step6 = self.load_step6_bug(bug_id)
        if not step6:
            self._log(f"[{idx}/{total}] Bug {bug_id}: ○ No step6 data")
            return result

        result['has_step6_data']    = True
        overlapping_files: List[str] = step6.get('overlapping_files', [])
        result['overlapping_files'] = overlapping_files

        if not overlapping_files:
            self._log(f"[{idx}/{total}] Bug {bug_id}: ○ Step6 has no overlapping files")
            return result

        # ── Flatten all imports from all test files ───────────────────────
        all_imports = extract_all_imports(test_files)
        result['test_imports'] = all_imports
        result['summary']['total_imports']           = len(all_imports)
        result['summary']['total_overlapping_files'] = len(overlapping_files)

        if not all_imports:
            self._log(f"[{idx}/{total}] Bug {bug_id}: ○ No imports in test files")
            return result

        # ── Match each import against overlapping files ───────────────────
        matches      = []
        exact_count  = 0
        fname_count  = 0
        matched_tests: Set[str] = set()

        for imp in all_imports:
            matched_file, match_type = match_import_against_overlapping(
                imp['import_path'], overlapping_files
            )
            if matched_file:
                matches.append({
                    'import_path':          imp['import_path'],
                    'from_test_file':       imp['from_test_file'],
                    'test_status':          imp['test_status'],
                    'language':             imp['language'],
                    'matched_overlapping':  matched_file,
                    'match_type':           match_type,
                })
                matched_tests.add(imp['from_test_file'])
                if match_type == 'exact':
                    exact_count += 1
                else:
                    fname_count += 1

        result['matches']  = matches
        result['has_hits'] = len(matches) > 0
        result['summary']['exact_matches']       = exact_count
        result['summary']['filename_matches']    = fname_count
        result['summary']['total_matches']       = len(matches)
        result['summary']['matching_test_files'] = len(matched_tests)

        if matches:
            self._log(
                f"[{idx}/{total}] Bug {bug_id}: ✓ {len(matches)} matches "
                f"(exact={exact_count}, filename={fname_count}) "
                f"across {len(matched_tests)} test file(s)"
            )
        else:
            self._log(
                f"[{idx}/{total}] Bug {bug_id}: ✗ No matches "
                f"({len(all_imports)} imports vs "
                f"{len(overlapping_files)} overlapping files)"
            )

        return result

    # ── Run ───────────────────────────────────────────────────────────────

    def run(self) -> Dict:
        print("=" * 70)
        print("REGRESSION TEST CROSS-REFERENCE")
        print("=" * 70 + "\n")

        parser_bugs = self.load_parser_bugs()
        if not parser_bugs:
            print("ERROR: No parser bugs found.")
            return {}

        total = len(parser_bugs)
        print(f"\nTotal bugs to cross-reference: {total}\n")
        print("=" * 70)
        print("PROCESSING")
        print("=" * 70 + "\n")

        all_results: Dict[str, Dict] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self.cross_reference_bug, bid, bdata, i + 1, total
                ): bid
                for i, (bid, bdata) in enumerate(parser_bugs.items())
            }
            for future in as_completed(futures):
                bug_id = futures[future]
                try:
                    res = future.result()
                    with self.result_lock:
                        all_results[bug_id] = res
                except Exception as e:
                    self._log(f"  ERROR bug {bug_id}: {e}")
                    all_results[bug_id] = {
                        'bug_id': bug_id, 'error': str(e), 'has_hits': False
                    }

        return self._finalise(all_results, total)

    # ── Save + summary ────────────────────────────────────────────────────

    def _finalise(self, all_results: Dict, total: int) -> Dict:
        hits    = {bid: r for bid, r in all_results.items() if r.get('has_hits')}
        no_hits = {bid: r for bid, r in all_results.items() if not r.get('has_hits')}

        # Save individual bug JSONs — only bugs with hits
        for bug_id, data in hits.items():
            with open(self.hits_dir / f"bug_{bug_id}.json", 'w') as fp:
                json.dump(data, fp, indent=2)

        summary = {
            'timestamp':                  datetime.now().isoformat(),
            'total_bugs_processed':       total,
            'bugs_with_hits':             len(hits),
            'bugs_without_hits':          len(no_hits),
            'total_exact_matches':        sum(
                r.get('summary', {}).get('exact_matches', 0)
                for r in hits.values()),
            'total_filename_matches':     sum(
                r.get('summary', {}).get('filename_matches', 0)
                for r in hits.values()),
            'total_matches':              sum(
                r.get('summary', {}).get('total_matches', 0)
                for r in hits.values()),
            'bugs_with_no_step6_data':    sum(
                1 for r in all_results.values()
                if not r.get('has_step6_data')),
            'bugs_with_no_imports':       sum(
                1 for r in all_results.values()
                if r.get('summary', {}).get('total_imports', 0) == 0),
        }

        self._print_summary(summary)

        with open(self.output_base / "cross_reference_summary.json", 'w') as fp:
            json.dump({
                'summary':          summary,
                'bugs_with_hits':   sorted(hits.keys()),
                'bugs_without_hits':sorted(no_hits.keys()),
            }, fp, indent=2)

        print(f"\n✓ Saved {len(hits)} bug JSONs → {self.hits_dir}")
        print(f"✓ Saved cross_reference_summary.json → {self.output_base}")

        return {'summary': summary, 'hits': hits, 'no_hits': no_hits}

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total bugs processed      : {s['total_bugs_processed']}")
        print(f"Bugs WITH hits            : {s['bugs_with_hits']}")
        print(f"Bugs WITHOUT hits         : {s['bugs_without_hits']}")
        print(f"Total matches             : {s['total_matches']}")
        print(f"  → Exact matches         : {s['total_exact_matches']}")
        print(f"  → Filename matches      : {s['total_filename_matches']}")
        print(f"Bugs with no step6 data   : {s['bugs_with_no_step6_data']}")
        print(f"Bugs with no imports      : {s['bugs_with_no_imports']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Cross-reference regression test imports against "
                    "overlapping files from fixing and regressor commits"
    )
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--debug',   action='store_true')
    args = parser.parse_args()

    xref = RegressionTestCrossReference(
        max_workers=args.workers,
        debug=args.debug,
    )
    results = xref.run()

    if results:
        s = results['summary']
        print(f"\n✓ DONE")
        print(f"  Bugs with hits   : {s['bugs_with_hits']}")
        print(f"  Total matches    : {s['total_matches']} "
              f"(exact={s['total_exact_matches']}, "
              f"filename={s['total_filename_matches']})")


if __name__ == "__main__":
    main()
