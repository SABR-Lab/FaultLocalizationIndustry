#!/usr/bin/env python3
"""
================================================================================
REGRESSION TEST PARSER
================================================================================
Reads the test files saved by regression_test_extractor and parses them to
figure out which source files each test exercises via imports/includes.

Supported file types:
  - .js          → import / require / ChromeUtils.import etc.
  - .cpp/.h      → #include directives
  - .html        → <script src="..."> tags
  - .toml        → test manifest: registered test files, head, support-files

Input:
  outputs/regression_test_extraction/
    bugs_with_regressor_file_overlap/bug_<id>/added/*.txt
    bugs_with_regressor_file_overlap/bug_<id>/modified/*_after.txt
    bugs_without_regressor_file_overlap/bug_<id>/added/*.txt
    bugs_without_regressor_file_overlap/bug_<id>/modified/*_after.txt

Output:
  outputs/regression_test_parsing/
    bugs_with_regressor_file_overlap/
      bug_<id>.json
    bugs_without_regressor_file_overlap/
      bug_<id>.json
    parsing_summary.json
"""

import json
import re
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)

# ---------------------------------------------------------------------------
# C++ patterns
# ---------------------------------------------------------------------------

CPP_INCLUDE_PATTERNS = [
    re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE),   # #include "foo.h"
    re.compile(r'^\s*#include\s+<([^>]+)>', re.MULTILINE),   # #include <foo.h>
]

# ---------------------------------------------------------------------------
# JavaScript patterns
# ---------------------------------------------------------------------------

JS_IMPORT_PATTERNS = [
    re.compile(r'^\s*import\s+.*?\s+from\s+["\']([^"\']+)["\']',  re.MULTILINE),
    re.compile(r'^\s*import\s+["\']([^"\']+)["\']',               re.MULTILINE),
    re.compile(r'(?:^|[^.\w])require\s*\(\s*["\']([^"\']+)["\']\s*\)', re.MULTILINE),
    re.compile(r'ChromeUtils\.import\s*\(\s*["\']([^"\']+)["\']\s*\)', re.MULTILINE),
    re.compile(r'ChromeUtils\.importESModule\s*\(\s*["\']([^"\']+)["\']\s*\)', re.MULTILINE),
    re.compile(r'Cu\.import\s*\(\s*["\']([^"\']+)["\']\s*\)',     re.MULTILINE),
    re.compile(r'Components\.utils\.import\s*\(\s*["\']([^"\']+)["\']\s*\)', re.MULTILINE),
    # XPCOMUtils.defineLazyModuleGetters block — handled separately
    re.compile(r'XPCOMUtils\.defineLazyModuleGetters?\s*\([^,]+,\s*\{([^}]+)\}',
               re.MULTILINE | re.DOTALL),
]
LAZY_MODULE_ENTRY = re.compile(r'["\']([^"\']+\.jsm?)["\']')

# ---------------------------------------------------------------------------
# HTML patterns
# ---------------------------------------------------------------------------

# <script src="..."> and <script src='...'>
HTML_SCRIPT_SRC = re.compile(
    r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.MULTILINE
)
# <link rel="stylesheet" href="..."> — optional but useful
HTML_LINK_HREF  = re.compile(
    r'<link[^>]+href\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.MULTILINE
)

# ---------------------------------------------------------------------------
# TOML manifest patterns
# ---------------------------------------------------------------------------

# head = "head.js" or head = ["head.js", "head2.js"]
TOML_HEAD        = re.compile(r'^\s*head\s*=\s*(.+)$',          re.MULTILINE)
# support-files = ["helper.js", ...]
TOML_SUPPORT     = re.compile(r'^\s*support-files\s*=\s*(.+)$', re.MULTILINE)
# [test_foo.js]  — registered test entries
TOML_TEST_ENTRY  = re.compile(r'^\[([^\]]+\.(js|html|cpp))\]',  re.MULTILINE)
# any quoted string value (to extract file paths from head/support lines)
TOML_QUOTED_VAL  = re.compile(r'["\']([^"\']+)["\']')


# ---------------------------------------------------------------------------
# Parser functions
# ---------------------------------------------------------------------------

def parse_cpp(content: str) -> Dict:
    found = []
    for pat in CPP_INCLUDE_PATTERNS:
        found.extend(pat.findall(content))
    includes = sorted(set(found))
    return {
        'language': 'cpp',
        'includes': includes,
        'import_count': len(includes),
    }


def parse_js(content: str) -> Dict:
    found = set()
    for i, pat in enumerate(JS_IMPORT_PATTERNS):
        matches = pat.findall(content)
        if i == len(JS_IMPORT_PATTERNS) - 1:   # lazy module getters block
            for block in matches:
                found.update(LAZY_MODULE_ENTRY.findall(block))
        else:
            found.update(matches)
    imports = sorted(found)
    return {
        'language': 'javascript',
        'imports':  imports,
        'import_count': len(imports),
    }


def parse_html(content: str) -> Dict:
    scripts = sorted(set(HTML_SCRIPT_SRC.findall(content)))
    links   = sorted(set(HTML_LINK_HREF.findall(content)))
    return {
        'language':         'html',
        'script_sources':   scripts,
        'linked_resources': links,
        'import_count':     len(scripts) + len(links),
    }


def parse_toml(content: str) -> Dict:
    """
    Extract from a .toml test manifest:
      - registered_tests  : [test_foo.js, test_bar.html, ...]
      - head_files        : shared setup scripts
      - support_files     : helper files declared in the manifest
    """
    registered = sorted(set(TOML_TEST_ENTRY.findall(content) and
                            [m[0] for m in TOML_TEST_ENTRY.findall(content)]))

    head_files    = []
    support_files = []

    for m in TOML_HEAD.findall(content):
        head_files.extend(TOML_QUOTED_VAL.findall(m))

    for m in TOML_SUPPORT.findall(content):
        support_files.extend(TOML_QUOTED_VAL.findall(m))

    head_files    = sorted(set(head_files))
    support_files = sorted(set(support_files))

    return {
        'language':         'toml_manifest',
        'registered_tests': registered,
        'head_files':       head_files,
        'support_files':    support_files,
        # import_count = all referenced files (for summary consistency)
        'import_count':     len(registered) + len(head_files) + len(support_files),
    }


def parse_file(filename: str, content: str) -> Dict:
    """Dispatch to the right parser based on file extension."""
    ext = Path(filename).suffix.lower()

    if ext in ('.cpp', '.cc', '.cxx', '.h', '.hpp'):
        return parse_cpp(content)
    elif ext == '.js':
        return parse_js(content)
    elif ext in ('.html', '.htm'):
        return parse_html(content)
    elif ext == '.toml':
        return parse_toml(content)
    else:
        return {'language': 'unknown', 'import_count': 0}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_test_txt_files(bug_dir: Path) -> List[Dict]:
    """
    Walk bug_dir/added/ and bug_dir/modified/ and return entries to parse.
    For modified files we only parse *_after.txt (the post-fix version).
    """
    entries = []

    added_dir    = bug_dir / "added"
    modified_dir = bug_dir / "modified"

    if added_dir.exists():
        for txt in sorted(added_dir.glob("*.txt")):
            entries.append({
                'txt_path':      txt,
                'repo_filename': txt.stem,          # strip .txt
                'status':        'added',
            })

    if modified_dir.exists():
        for txt in sorted(modified_dir.glob("*_after.txt")):
            original = txt.name.replace("_after.txt", "")
            entries.append({
                'txt_path':      txt,
                'repo_filename': original,
                'status':        'modified',
            })

    return entries


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class RegressionTestParser:

    CATEGORIES = [
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

        self.input_base  = self.outputs_base / "regression_test_extraction"
        self.output_base = self.outputs_base / "regression_test_parsing"
        self.output_base.mkdir(parents=True, exist_ok=True)

        for cat in self.CATEGORIES:
            (self.output_base / cat).mkdir(exist_ok=True)

        print(f"Input  : {self.input_base}")
        print(f"Output : {self.output_base}\n")

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, msg: str):
        with self.print_lock:
            print(msg)

    def _dbg(self, msg: str):
        if self.debug:
            self._log(f"  [DEBUG] {msg}")

    # ── Discover bugs ─────────────────────────────────────────────────────

    def discover_bugs(self) -> List[Dict]:
        bugs = []
        for cat in self.CATEGORIES:
            cat_dir = self.input_base / cat
            if not cat_dir.exists():
                print(f"  WARNING: not found: {cat_dir}")
                continue
            cat_bugs = []
            for bug_dir in sorted(cat_dir.iterdir()):
                if bug_dir.is_dir() and bug_dir.name.startswith("bug_"):
                    bug_id = bug_dir.name.replace("bug_", "")
                    cat_bugs.append({
                        'bug_id':   bug_id,
                        'category': cat,
                        'bug_dir':  bug_dir,
                    })
            bugs.extend(cat_bugs)
            print(f"  Found {len(cat_bugs):4d} bugs in [{cat}]")
        return bugs

    # ── Process one bug ───────────────────────────────────────────────────

    def process_bug(self, bug_info: Dict, idx: int, total: int) -> Dict:
        bug_id   = bug_info['bug_id']
        category = bug_info['category']
        bug_dir  = bug_info['bug_dir']

        result = {
            'bug_id':     bug_id,
            'category':   category,
            'test_files': [],
            'summary': {
                'total_test_files_parsed': 0,
                'total_imports_found':     0,
                'files_with_no_imports':   0,
                'cpp_files':               0,
                'js_files':                0,
                'html_files':              0,
                'toml_files':              0,
                'unknown_files':           0,
            }
        }

        entries = find_test_txt_files(bug_dir)
        if not entries:
            self._log(f"[{idx}/{total}] Bug {bug_id}: ○ No test files found")
            return result

        parsed_files = []
        for entry in entries:
            try:
                content = entry['txt_path'].read_text(
                    encoding='utf-8', errors='replace')
            except Exception as e:
                self._dbg(f"Read error {entry['txt_path']}: {e}")
                continue

            parsed = parse_file(entry['repo_filename'], content)

            record = {
                'test_filename': entry['repo_filename'],
                'status':        entry['status'],
                'language':      parsed['language'],
                'import_count':  parsed['import_count'],
            }

            # Attach language-specific fields
            if parsed['language'] == 'cpp':
                record['includes'] = parsed.get('includes', [])
            elif parsed['language'] == 'javascript':
                record['imports']  = parsed.get('imports', [])
            elif parsed['language'] == 'html':
                record['script_sources']   = parsed.get('script_sources', [])
                record['linked_resources'] = parsed.get('linked_resources', [])
            elif parsed['language'] == 'toml_manifest':
                record['registered_tests'] = parsed.get('registered_tests', [])
                record['head_files']        = parsed.get('head_files', [])
                record['support_files']     = parsed.get('support_files', [])

            parsed_files.append(record)

        # Summary
        s = {
            'total_test_files_parsed': len(parsed_files),
            'total_imports_found':     sum(f['import_count'] for f in parsed_files),
            'files_with_no_imports':   sum(1 for f in parsed_files if f['import_count'] == 0),
            'cpp_files':               sum(1 for f in parsed_files if f['language'] == 'cpp'),
            'js_files':                sum(1 for f in parsed_files if f['language'] == 'javascript'),
            'html_files':              sum(1 for f in parsed_files if f['language'] == 'html'),
            'toml_files':              sum(1 for f in parsed_files if f['language'] == 'toml_manifest'),
            'unknown_files':           sum(1 for f in parsed_files if f['language'] == 'unknown'),
        }

        result['test_files'] = parsed_files
        result['summary']    = s

        self._log(
            f"[{idx}/{total}] Bug {bug_id}: ✓ {s['total_test_files_parsed']} files "
            f"(cpp={s['cpp_files']} js={s['js_files']} "
            f"html={s['html_files']} toml={s['toml_files']}) "
            f"→ {s['total_imports_found']} imports"
        )
        return result

    # ── Run ───────────────────────────────────────────────────────────────

    def run(self) -> Dict:
        print("=" * 70)
        print("REGRESSION TEST PARSER")
        print("=" * 70 + "\n")

        bugs = self.discover_bugs()
        if not bugs:
            print("ERROR: No bugs found — run regression_test_extractor first.")
            return {}

        total = len(bugs)
        print(f"\nTotal bugs to parse: {total}\n")
        print("=" * 70)
        print("PROCESSING")
        print("=" * 70 + "\n")

        all_results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.process_bug, b, i + 1, total): b['bug_id']
                for i, b in enumerate(bugs)
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

        return self._finalise(all_results, bugs, total)

    # ── Save + summary ────────────────────────────────────────────────────

    def _finalise(self, all_results: Dict, bugs: List[Dict], total: int) -> Dict:
        saved = 0
        for bug_info in bugs:
            bug_id   = bug_info['bug_id']
            category = bug_info['category']
            if bug_id not in all_results:
                continue
            out = self.output_base / category / f"bug_{bug_id}.json"
            with open(out, 'w') as fp:
                json.dump(all_results[bug_id], fp, indent=2)
            saved += 1

        bugs_with    = {bid: r for bid, r in all_results.items()
                        if r.get('summary', {}).get('total_imports_found', 0) > 0}
        bugs_without = {bid: r for bid, r in all_results.items()
                        if r.get('summary', {}).get('total_imports_found', 0) == 0}

        summary = {
            'timestamp':               datetime.now().isoformat(),
            'total_bugs_processed':    total,
            'bugs_with_imports':       len(bugs_with),
            'bugs_without_imports':    len(bugs_without),
            'total_test_files_parsed': sum(
                r.get('summary', {}).get('total_test_files_parsed', 0)
                for r in all_results.values()),
            'total_imports_found':     sum(
                r.get('summary', {}).get('total_imports_found', 0)
                for r in all_results.values()),
            'total_cpp_files':         sum(
                r.get('summary', {}).get('cpp_files', 0)
                for r in all_results.values()),
            'total_js_files':          sum(
                r.get('summary', {}).get('js_files', 0)
                for r in all_results.values()),
            'total_html_files':        sum(
                r.get('summary', {}).get('html_files', 0)
                for r in all_results.values()),
            'total_toml_files':        sum(
                r.get('summary', {}).get('toml_files', 0)
                for r in all_results.values()),
        }

        self._print_summary(summary)

        with open(self.output_base / "parsing_summary.json", 'w') as fp:
            json.dump({
                'summary':             summary,
                'bugs_with_imports':   sorted(bugs_with.keys()),
                'bugs_without_imports':sorted(bugs_without.keys()),
            }, fp, indent=2)

        print(f"\n✓ Saved {saved} bug JSONs + parsing_summary.json → {self.output_base}")
        return {'summary': summary, 'results': all_results}

    def _print_summary(self, s: Dict):
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total bugs processed     : {s['total_bugs_processed']}")
        print(f"Bugs with imports        : {s['bugs_with_imports']}")
        print(f"Bugs without imports     : {s['bugs_without_imports']}")
        print(f"Total test files parsed  : {s['total_test_files_parsed']}")
        print(f"Total imports found      : {s['total_imports_found']}")
        print(f"  → C++ files            : {s['total_cpp_files']}")
        print(f"  → JS files             : {s['total_js_files']}")
        print(f"  → HTML files           : {s['total_html_files']}")
        print(f"  → TOML manifests       : {s['total_toml_files']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Parse regression test files to extract imports/includes"
    )
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--debug',   action='store_true')
    args = parser.parse_args()

    p = RegressionTestParser(max_workers=args.workers, debug=args.debug)
    results = p.run()

    if results:
        s = results['summary']
        print(f"\n✓ DONE")
        print(f"  Bugs with imports    : {s['bugs_with_imports']}")
        print(f"  Total imports found  : {s['total_imports_found']}")


if __name__ == "__main__":
    main()
