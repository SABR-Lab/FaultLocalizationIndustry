#!/usr/bin/env python3
"""
================================================================================
FIND AND EXTRACT TEST FILES (3-Tier Chain Approach)
================================================================================

PURPOSE:
--------
For bugs with line-level coverage, find related test files using a 3-tier chain:

  - Tier 0: Tests from fixing commits (check content for source file references)
  - Tier 1: Component/suite directory search (name match + content validation)
  - Tier 2: Global fallback within same top-level directory (content validation)

STRICT MATCHING RULES:
----------------------
  - C++ source files (.cpp, .h) ONLY match C++ test files
  - JS source files (.js, .jsm) ONLY match JS/HTML test files
  - This prevents false matches like test_eventctors.html matching UIEvent.cpp

INPUT:
------
- line-level coverage output: outputs/line_level_coverage/bugs/bug_*/

OUTPUT:
-------
outputs/complete_test_files/
└── bugs/
    └── bug_<id>/
        └── <fixing_commit_hash>/
            └── <source_filename>/
                └── tests/<test_files>
"""

import json
import shutil
import subprocess
import sys
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict

script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)


class TestFinder:
    """Find and extract test files with 3-tier chain approach"""
    
    TEST_DIR_INDICATORS = [
        '/test/', '/tests/', '/testing/', '/xpcshell/', '/mochitest/',
        '/browser/', '/gtest/', '/reftest/', '/crashtest/', '/marionette/', '/chrome/'
    ]
    
    TEST_SUITE_DIRS = [
        'test', 'tests', 'test/xpcshell', 'tests/xpcshell', 'test/unit',
        'test/browser', 'tests/browser', 'test/mochitest', 'tests/mochitest',
        'test/chrome', 'tests/chrome', 'gtest', 'tests/gtest', 'test/gtest',
        'crashtests', 'reftests', 'test/reftest'
    ]
    
    VALID_TEST_EXTENSIONS = ['.js', '.py', '.cpp', '.html', '.xhtml', '.sh']
    EXCLUDED_EXTENSIONS = ['.json', '.toml', '.ini', '.txt', '.md', '.png', '.jpg']
    
    CPP_EXTENSIONS = ['.cpp', '.cc', '.c', '.h', '.hpp']
    JS_EXTENSIONS = ['.js', '.jsm', '.mjs', '.html', '.xhtml']

    def __init__(self, mozilla_central_path: str = None):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        self.input_dir = self.outputs_base / "line_level_coverage" / "bugs"
        self.output_dir = self.outputs_base / "complete_test_files"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        if mozilla_central_path:
            self.mozilla_central = Path(mozilla_central_path)
        else:
            possible_paths = [
                Path.home() / "mozilla-central",
                Path.home() / "Mozilla_crashAnalyzer_BugBug" / "mozilla-central",
                Path("/root/FaultLocalizationIndustry/mozilla-central"),
            ]
            self.mozilla_central = next((p for p in possible_paths if p.exists()), None)
        
        self.stats = {
            'bugs_processed': 0,
            'bugs_with_tests': 0,
            'bugs_without_tests': 0,
            'commits_processed': 0,
            'source_files_processed': 0,
            'source_files_with_tests': 0,
            'source_files_without_tests': 0,
            'tests_found_tier0': 0,
            'tests_found_tier1_name': 0,
            'tests_found_tier1_content': 0,
            'tests_found_tier2': 0,
            'test_files_extracted': 0,
            'extraction_errors': 0,
        }
        
        self.filtered_bugs = []
        
        print(f"Input directory: {self.input_dir}")
        print(f"Output directory: {self.output_dir}")
        print(f"Mozilla-central: {self.mozilla_central or 'NOT FOUND'}")

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    def _is_cpp_source(self, filename: str) -> bool:
        """Check if file is a C++ source file."""
        return Path(filename).suffix.lower() in self.CPP_EXTENSIONS
    
    def _is_js_source(self, filename: str) -> bool:
        """Check if file is a JavaScript source file."""
        ext = Path(filename).suffix.lower()
        return ext in ['.js', '.jsm', '.mjs'] or filename.endswith('.sys.mjs')
    
    def _is_cpp_test(self, filename: str) -> bool:
        """Check if file is a C++ test file."""
        return Path(filename).suffix.lower() in self.CPP_EXTENSIONS
    
    def _is_js_test(self, filename: str) -> bool:
        """Check if file is a JS/HTML test file."""
        return Path(filename).suffix.lower() in self.JS_EXTENSIONS
    
    def _is_test_file_path(self, filepath: str) -> bool:
        """Check if a file path looks like a test file."""
        path_lower = filepath.lower()
        name = Path(filepath).name.lower()
        
        in_test_dir = any(ind in path_lower for ind in self.TEST_DIR_INDICATORS)
        
        test_name_patterns = [
            name.startswith('test_'),
            name.startswith('test') and not name.startswith('testing'),
            name.startswith('browser_'),
            '_test.' in name,
            name.endswith('_test.js'),
            name.startswith('Test') and name.endswith('.cpp'),
        ]
        has_test_name = any(test_name_patterns)
        
        is_excluded = any(name.endswith(ext) for ext in self.EXCLUDED_EXTENSIONS)
        has_valid_ext = any(name.endswith(ext) for ext in self.VALID_TEST_EXTENSIONS)
        
        return (in_test_dir or has_test_name) and not is_excluded and has_valid_ext

    def _infer_suite_from_path(self, path: str) -> str:
        """Infer test suite from file path."""
        path_lower = path.lower()
        if 'xpcshell' in path_lower:
            return 'xpcshell'
        elif 'browser' in path_lower and 'test' in path_lower:
            return 'mochitest-browser-chrome'
        elif 'mochitest' in path_lower:
            return 'mochitest-plain'
        elif 'gtest' in path_lower:
            return 'gtest'
        elif 'reftest' in path_lower:
            return 'reftest'
        elif 'crashtest' in path_lower:
            return 'crashtest'
        elif 'chrome' in path_lower:
            return 'mochitest-chrome'
        return 'unknown'

    def _get_component_path(self, source_file: str) -> str:
        """Get the full directory path (component) of a source file."""
        return str(Path(source_file).parent)

    def _normalize_filename(self, filepath: str) -> str:
        """Normalize filepath for use as directory name."""
        return filepath.replace('/', '_').replace('\\', '_')

    def _is_name_match(self, source_filename: str, test_filename: str) -> bool:
        """Check if test filename matches source filename pattern with type compatibility."""
        source_ext = Path(source_filename).suffix.lower()
        test_ext = Path(test_filename).suffix.lower()
        
        # Check file type compatibility
        if source_ext in self.CPP_EXTENSIONS:
            if test_ext not in self.CPP_EXTENSIONS:
                return False
        elif self._is_js_source(source_filename):
            if test_ext not in self.JS_EXTENSIONS:
                return False
        
        source_stem = Path(source_filename).stem.lower()
        test_stem = Path(test_filename).stem.lower()
        
        # Remove common prefixes/suffixes from test name
        test_clean = test_stem
        for pattern in ['test_', 'test', 'browser_', '_test', 'tests_']:
            test_clean = test_clean.replace(pattern, '')
        
        if source_stem == test_clean:
            return True
        
        if source_stem in test_clean and len(source_stem) >= 4:
            if len(source_stem) >= len(test_clean) * 0.5:
                return True
        
        return False

    # =========================================================================
    # CONTENT MATCHING
    # =========================================================================
    
    def _remove_comments(self, content: str, file_ext: str) -> str:
        """Remove comments from code content."""
        if file_ext in ['.js', '.jsm', '.mjs', '.cpp', '.cc', '.c', '.h', '.hpp']:
            content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        elif file_ext == '.py':
            content = re.sub(r'#.*$', '', content, flags=re.MULTILINE)
            content = re.sub(r'""".*?"""', '', content, flags=re.DOTALL)
            content = re.sub(r"'''.*?'''", '', content, flags=re.DOTALL)
        elif file_ext in ['.html', '.xhtml', '.xml']:
            content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)
        return content

    def _check_content_for_source_file(self, test_file_path: Path, source_filename: str) -> Tuple[bool, Optional[str]]:
        """Check if test file actually imports/includes the source file with strict type matching."""
        try:
            if test_file_path.stat().st_size > 1_000_000:
                return False, None
            
            content = test_file_path.read_text(encoding='utf-8', errors='replace')
            source_stem = Path(source_filename).stem
            test_ext = test_file_path.suffix.lower()
            content_no_comments = self._remove_comments(content, test_ext)
            
            # C++ source files should ONLY match C++ test files
            if self._is_cpp_source(source_filename):
                if self._is_cpp_test(test_file_path.name):
                    return self._check_cpp_source_reference(content_no_comments, source_filename, source_stem)
                return False, None
            
            # JS source files should match JS/HTML test files
            if self._is_js_source(source_filename):
                if self._is_js_test(test_file_path.name):
                    return self._check_js_source_reference(content_no_comments, source_filename, source_stem)
                return False, None
            
            return False, None
            
        except Exception:
            return False, None

    def _check_cpp_source_reference(self, content: str, source_filename: str, source_stem: str) -> Tuple[bool, Optional[str]]:
        """Check if C++ test references C++ source file."""
        header_name = source_stem + '.h'
        
        # Pattern 1: #include of header
        include_patterns = [
            rf'#include\s*[<"](.*/)?' + re.escape(header_name) + rf'[>"]',
            rf'#include\s*[<"]mozilla/dom/{re.escape(source_stem)}\.h[>"]',
            rf'#include\s*[<"]mozilla/{re.escape(source_stem)}\.h[>"]',
        ]
        for pattern in include_patterns:
            if re.search(pattern, content):
                return True, 'cpp_include'
        
        # Pattern 2: GTest fixtures
        gtest_patterns = [
            rf'TEST\s*\(\s*{re.escape(source_stem)}\s*,',
            rf'TEST_F\s*\(\s*{re.escape(source_stem)}(Test)?\s*,',
            rf'TEST_P\s*\(\s*{re.escape(source_stem)}(Test)?\s*,',
        ]
        for pattern in gtest_patterns:
            if re.search(pattern, content):
                return True, 'gtest_fixture'
        
        # Pattern 3: Class usage (strict)
        class_patterns = [
            rf'(?<!["\'])\b{re.escape(source_stem)}::[A-Z]\w*',
            rf'new\s+{re.escape(source_stem)}\s*\(',
            rf'(?:Make)?RefPtr<\s*{re.escape(source_stem)}\s*>',
        ]
        for pattern in class_patterns:
            if re.search(pattern, content):
                return True, 'cpp_class_usage'
        
        return False, None

    def _check_js_source_reference(self, content: str, source_filename: str, source_stem: str) -> Tuple[bool, Optional[str]]:
        """Check if JS/HTML test references JS source file."""
        # Pattern 1: ChromeUtils imports
        chrome_patterns = [
            rf'ChromeUtils\.import\s*\([^)]*{re.escape(source_stem)}',
            rf'ChromeUtils\.importESModule\s*\([^)]*{re.escape(source_stem)}',
            rf'ChromeUtils\.defineModuleGetter\s*\([^)]*{re.escape(source_stem)}',
            rf'ChromeUtils\.defineESModuleGetters\s*\([^)]*{re.escape(source_stem)}',
        ]
        for pattern in chrome_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True, 'chrome_import'
        
        # Pattern 2: Cu.import
        cu_patterns = [
            rf'Cu\.import\s*\([^)]*{re.escape(source_stem)}',
            rf'Components\.utils\.import\s*\([^)]*{re.escape(source_stem)}',
        ]
        for pattern in cu_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True, 'cu_import'
        
        # Pattern 3: ES6 imports
        es6_patterns = [
            rf'import\s+.*from\s+["\'][^"\']*{re.escape(source_stem)}',
            rf'import\s*\(["\'][^"\']*{re.escape(source_stem)}',
        ]
        for pattern in es6_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True, 'es6_import'
        
        # Pattern 4: require
        if re.search(rf'require\s*\(["\'][^"\']*{re.escape(source_stem)}', content, re.IGNORECASE):
            return True, 'require_import'
        
        # Pattern 5: loadSubScript
        if re.search(rf'loadSubScript\s*\(\s*["\'][^"\']*{re.escape(source_stem)}', content, re.IGNORECASE):
            return True, 'load_script'
        
        return False, None

    # =========================================================================
    # TIER 0: FIXING COMMIT TESTS
    # =========================================================================
    
    def _get_files_from_commit(self, commit_hash: str) -> List[str]:
        """Get all files changed in a commit."""
        if not self.mozilla_central:
            return []
        try:
            result = subprocess.run(
                ['hg', 'log', '-r', commit_hash, '--template', '{files % "{file}\\n"}'],
                cwd=self.mozilla_central, capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
        except Exception as e:
            print(f"        Warning: Could not get files from commit {commit_hash[:12]}: {e}")
        return []

    def find_tests_tier0(self, commit_hash: str, source_files: List[str]) -> Dict[str, List[Dict]]:
        """Tier 0: Find test files from fixing commit that reference source files."""
        results = defaultdict(list)
        
        commit_files = self._get_files_from_commit(commit_hash)
        if not commit_files:
            return results
        
        test_files = [f for f in commit_files if self._is_test_file_path(f)]
        if not test_files:
            return results
        
        print(f"        Tier 0: Found {len(test_files)} test file(s) in commit")
        
        cpp_sources = [f for f in source_files if self._is_cpp_source(f)]
        js_sources = [f for f in source_files if self._is_js_source(f)]
        
        for test_file in test_files:
            test_path = self.mozilla_central / test_file
            if not test_path.exists():
                continue
            
            # Determine compatible sources
            if self._is_cpp_test(test_file):
                candidate_sources = cpp_sources
            elif self._is_js_test(test_file):
                candidate_sources = js_sources
            else:
                continue
            
            for source_file in candidate_sources:
                source_filename = Path(source_file).name
                is_match, match_type = self._check_content_for_source_file(test_path, source_filename)
                
                if is_match:
                    results[source_file].append({
                        'path': test_file,
                        'name': Path(test_file).name,
                        'suite': self._infer_suite_from_path(test_file),
                        'tier': 0,
                        'match_type': f'fixing_commit_{match_type}',
                        'source_commit': commit_hash[:12]
                    })
                    self.stats['tests_found_tier0'] += 1
                    print(f"          ✓ {Path(test_file).name} -> {source_filename} ({match_type})")
        
        return results

    # =========================================================================
    # TIER 1: COMPONENT DIRECTORY SEARCH
    # =========================================================================
    
    def find_tests_tier1(self, source_file: str) -> List[Dict]:
        """Tier 1: Search component directories for tests with strict type matching."""
        results = []
        source_filename = Path(source_file).name
        component_path = self._get_component_path(source_file)
        
        if not self.mozilla_central:
            return results
        
        # Determine compatible test extensions
        if self._is_cpp_source(source_file):
            compatible_exts = self.CPP_EXTENSIONS
        elif self._is_js_source(source_file):
            compatible_exts = self.JS_EXTENSIONS
        else:
            return results
        
        candidate_tests = []
        
        # Search test suite directories
        for suite_dir in self.TEST_SUITE_DIRS:
            test_dir = self.mozilla_central / component_path / suite_dir
            if test_dir.exists():
                try:
                    for test_file in test_dir.rglob('*'):
                        if test_file.is_file() and test_file.suffix.lower() in compatible_exts:
                            if self._is_test_file_path(str(test_file.relative_to(self.mozilla_central))):
                                candidate_tests.append(test_file)
                except Exception:
                    continue
        
        # Search same directory
        source_dir = self.mozilla_central / component_path
        if source_dir.exists():
            try:
                for f in source_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in compatible_exts:
                        if self._is_test_file_path(str(f.relative_to(self.mozilla_central))):
                            if f not in candidate_tests:
                                candidate_tests.append(f)
            except Exception:
                pass
        
        if not candidate_tests:
            return results
        
        print(f"        Tier 1: Found {len(candidate_tests)} compatible test file(s)")
        
        for test_path in candidate_tests:
            test_filename = test_path.name
            relative_path = str(test_path.relative_to(self.mozilla_central))
            
            if self._is_name_match(source_filename, test_filename):
                results.append({
                    'path': relative_path,
                    'name': test_filename,
                    'suite': self._infer_suite_from_path(relative_path),
                    'tier': 1,
                    'match_type': 'name_match'
                })
                self.stats['tests_found_tier1_name'] += 1
                print(f"          ✓ {test_filename} (name match)")
            else:
                is_match, match_type = self._check_content_for_source_file(test_path, source_filename)
                if is_match:
                    results.append({
                        'path': relative_path,
                        'name': test_filename,
                        'suite': self._infer_suite_from_path(relative_path),
                        'tier': 1,
                        'match_type': f'content_{match_type}'
                    })
                    self.stats['tests_found_tier1_content'] += 1
                    print(f"          ✓ {test_filename} ({match_type})")
        
        return results

    # =========================================================================
    # TIER 2: GLOBAL FALLBACK
    # =========================================================================
    
    def find_tests_tier2(self, source_file: str) -> List[Dict]:
        """Tier 2: Global fallback search with strict type matching."""
        results = []
        source_filename = Path(source_file).name
        source_path = Path(source_file)
        
        if not self.mozilla_central:
            return results
        
        if self._is_cpp_source(source_file):
            compatible_exts = self.CPP_EXTENSIONS
        elif self._is_js_source(source_file):
            compatible_exts = self.JS_EXTENSIONS
        else:
            return results
        
        parts = source_path.parts
        if not parts:
            return results
        
        top_level = parts[0]
        top_level_path = self.mozilla_central / top_level
        
        if not top_level_path.exists():
            return results
        
        print(f"        Tier 2: Searching in {top_level}/ for {source_filename}")
        
        candidate_tests = []
        scanned = 0
        
        try:
            for test_file in top_level_path.rglob('*'):
                if scanned >= 5000:
                    break
                if not test_file.is_file():
                    continue
                if test_file.suffix.lower() not in compatible_exts:
                    continue
                relative_path = str(test_file.relative_to(self.mozilla_central))
                if self._is_test_file_path(relative_path):
                    candidate_tests.append(test_file)
                    scanned += 1
        except Exception as e:
            print(f"          Warning: Error scanning {top_level}: {e}")
        
        if not candidate_tests:
            return results
        
        print(f"          Scanning {len(candidate_tests)} compatible test files...")
        
        matches_found = 0
        for test_path in candidate_tests:
            is_match, match_type = self._check_content_for_source_file(test_path, source_filename)
            if is_match:
                relative_path = str(test_path.relative_to(self.mozilla_central))
                results.append({
                    'path': relative_path,
                    'name': test_path.name,
                    'suite': self._infer_suite_from_path(relative_path),
                    'tier': 2,
                    'match_type': f'global_{match_type}'
                })
                self.stats['tests_found_tier2'] += 1
                matches_found += 1
                print(f"          ✓ {test_path.name} ({match_type})")
                if matches_found >= 10:
                    break
        
        return results

    # =========================================================================
    # EXTRACTION
    # =========================================================================
    
    def extract_test_file(self, test_info: Dict, output_dir: Path) -> Optional[Dict]:
        """Extract a single test file."""
        test_path = test_info['path']
        source_path = self.mozilla_central / test_path
        
        if not source_path.exists():
            self.stats['extraction_errors'] += 1
            return {'path': test_path, 'error': 'file_not_found'}
        
        suite = test_info.get('suite', 'unknown')
        suite_dir = output_dir / suite
        suite_dir.mkdir(parents=True, exist_ok=True)
        
        dest_path = suite_dir / Path(test_path).name
        
        try:
            shutil.copy2(source_path, dest_path)
            self.stats['test_files_extracted'] += 1
            return {
                'original_path': test_path,
                'extracted_path': str(dest_path.relative_to(self.output_dir)),
                'suite': suite,
                'tier': test_info.get('tier'),
                'match_type': test_info.get('match_type'),
                'size_bytes': dest_path.stat().st_size
            }
        except Exception as e:
            self.stats['extraction_errors'] += 1
            return {'path': test_path, 'error': str(e)}

    # =========================================================================
    # MAIN PROCESSING
    # =========================================================================
    
    def collect_source_files_with_coverage(self, bug_dir: Path) -> Dict[str, Dict]:
        """Collect source files with coverage from line-level coverage output."""
        summary_file = bug_dir / "summary.json"
        if not summary_file.exists():
            return {}
        
        try:
            with open(summary_file) as f:
                data = json.load(f)
        except Exception:
            return {}
        
        commits_map = defaultdict(dict)
        
        for commit in data.get('fixing_commits', []):
            commit_hash = commit.get('full_hash') or commit.get('commit_hash', '')
            if not commit_hash:
                continue
            
            for file_info in commit.get('files', []):
                if file_info.get('has_coverage', False):
                    filename = file_info.get('filename', '')
                    if filename:
                        commits_map[commit_hash][filename] = {
                            'coverage_data': file_info.get('coverage', {}),
                            'lines_covered': file_info.get('lines_covered', 0),
                            'lines_uncovered': file_info.get('lines_uncovered', 0)
                        }
        
        return dict(commits_map)

    def process_commit(self, bug_id: str, commit_hash: str, 
                      source_files: Dict[str, Dict], bug_output_dir: Path) -> Dict:
        """Process a single fixing commit."""
        print(f"\n    Commit: {commit_hash[:12]}")
        print(f"    Source files with coverage: {len(source_files)}")
        
        commit_output_dir = bug_output_dir / commit_hash[:12]
        source_file_list = list(source_files.keys())
        source_files_needing_tests = set(source_file_list)
        all_results = {}
        
        # TIER 0
        print(f"\n      --- TIER 0: Fixing Commit Tests ---")
        tier0_results = self.find_tests_tier0(commit_hash, source_file_list)
        
        for source_file, tests in tier0_results.items():
            if tests:
                source_files_needing_tests.discard(source_file)
                all_results[source_file] = {'tier': 0, 'tests': tests}
        
        print(f"      Tier 0: {len(source_file_list) - len(source_files_needing_tests)}/{len(source_file_list)} found tests")
        
        # TIER 1
        if source_files_needing_tests:
            print(f"\n      --- TIER 1: Component Directory Search ---")
            for source_file in list(source_files_needing_tests):
                print(f"\n      {Path(source_file).name}:")
                tests = self.find_tests_tier1(source_file)
                if tests:
                    source_files_needing_tests.discard(source_file)
                    all_results[source_file] = {'tier': 1, 'tests': tests}
        
        # TIER 2
        if source_files_needing_tests:
            print(f"\n      --- TIER 2: Global Fallback ---")
            for source_file in list(source_files_needing_tests):
                print(f"\n      {Path(source_file).name}:")
                tests = self.find_tests_tier2(source_file)
                if tests:
                    source_files_needing_tests.discard(source_file)
                    all_results[source_file] = {'tier': 2, 'tests': tests}
        
        # Check if any tests found
        if not all_results:
            print(f"\n      ✗ No tests found for any source file")
            return {
                'commit_hash': commit_hash,
                'total_source_files': len(source_file_list),
                'source_files_with_tests': 0,
                'source_files_without_tests': len(source_file_list),
                'files': {},
                'has_tests': False
            }
        
        # Extract tests
        print(f"\n      --- Extracting Test Files ---")
        commit_output_dir.mkdir(parents=True, exist_ok=True)
        
        commit_summary = {
            'commit_hash': commit_hash,
            'total_source_files': len(source_file_list),
            'source_files_with_tests': len(all_results),
            'source_files_without_tests': len(source_files_needing_tests),
            'files': {},
            'has_tests': True
        }
        
        for source_file, result in all_results.items():
            safe_filename = self._normalize_filename(source_file)
            source_output_dir = commit_output_dir / safe_filename
            tests_dir = source_output_dir / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            
            extracted_tests = []
            for test in result['tests']:
                extracted = self.extract_test_file(test, tests_dir)
                if extracted:
                    extracted_tests.append(extracted)
            
            file_summary = {
                'source_file': source_file,
                'tier_found': result['tier'],
                'tests_found': len(result['tests']),
                'tests_extracted': len(extracted_tests),
                'tests': extracted_tests
            }
            
            commit_summary['files'][source_file] = file_summary
            
            with open(source_output_dir / "test_discovery.json", 'w') as f:
                json.dump(file_summary, f, indent=2)
            
            self.stats['source_files_with_tests'] += 1
            print(f"        {Path(source_file).name}: {len(extracted_tests)} test(s) (Tier {result['tier']})")
        
        for source_file in source_files_needing_tests:
            commit_summary['files'][source_file] = {
                'source_file': source_file,
                'tier_found': None,
                'tests_found': 0,
                'tests_extracted': 0,
                'tests': []
            }
            self.stats['source_files_without_tests'] += 1
            print(f"        {Path(source_file).name}: No tests found")
        
        with open(commit_output_dir / "commit_summary.json", 'w') as f:
            json.dump(commit_summary, f, indent=2)
        
        self.stats['commits_processed'] += 1
        self.stats['source_files_processed'] += len(source_file_list)
        
        return commit_summary

    def process_bug(self, bug_dir: Path) -> Optional[Dict]:
        """Process a single bug."""
        bug_id = bug_dir.name.replace('bug_', '')
        print(f"\n{'=' * 60}")
        print(f"Processing Bug {bug_id}")
        print(f"{'=' * 60}")
        
        commits_map = self.collect_source_files_with_coverage(bug_dir)
        
        if not commits_map:
            print(f"  No fixing commits with coverage found - skipping")
            self.filtered_bugs.append({'bug_id': bug_id, 'reason': 'no_fixing_commits_with_coverage'})
            return None
        
        print(f"  Found {len(commits_map)} fixing commit(s) with coverage")
        
        bug_output_dir = self.bugs_output_dir / f"bug_{bug_id}"
        
        bug_summary = {
            'bug_id': bug_id,
            'total_commits': len(commits_map),
            'commits': {}
        }
        
        total_with_tests = 0
        total_without_tests = 0
        has_any_tests = False
        
        for commit_hash, source_files in commits_map.items():
            commit_result = self.process_commit(bug_id, commit_hash, source_files, bug_output_dir)
            bug_summary['commits'][commit_hash[:12]] = commit_result
            total_with_tests += commit_result['source_files_with_tests']
            total_without_tests += commit_result['source_files_without_tests']
            if commit_result.get('has_tests', False):
                has_any_tests = True
        
        bug_summary['total_source_files_with_tests'] = total_with_tests
        bug_summary['total_source_files_without_tests'] = total_without_tests
        
        if not has_any_tests:
            print(f"\n  ✗ Bug {bug_id}: No tests found - FILTERING OUT")
            self.filtered_bugs.append({
                'bug_id': bug_id,
                'reason': 'no_tests_found',
                'source_files_checked': total_with_tests + total_without_tests
            })
            self.stats['bugs_without_tests'] += 1
            if bug_output_dir.exists():
                shutil.rmtree(bug_output_dir)
            return None
        
        bug_output_dir.mkdir(parents=True, exist_ok=True)
        with open(bug_output_dir / "bug_summary.json", 'w') as f:
            json.dump(bug_summary, f, indent=2)
        
        self.stats['bugs_processed'] += 1
        self.stats['bugs_with_tests'] += 1
        
        print(f"\n  ✓ Bug {bug_id}: {total_with_tests} files with tests, {total_without_tests} without")
        
        return bug_summary

    def run(self, bug_filter: str = None):
        """Main execution."""
        print("\n" + "=" * 70)
        print("STEP 8: FIND AND EXTRACT TEST FILES")
        print("(3-Tier Chain with Strict Type Matching)")
        print("=" * 70)
        
        if not self.mozilla_central or not self.mozilla_central.exists():
            print("ERROR: mozilla-central not found!")
            return
        
        if not self.input_dir.exists():
            print(f"ERROR: Input not found: {self.input_dir}")
            return
        
        bug_dirs = sorted([d for d in self.input_dir.iterdir() if d.is_dir() and d.name.startswith('bug_')])
        
        if bug_filter:
            bug_dirs = [d for d in bug_dirs if bug_filter in d.name]
        
        if not bug_dirs:
            print("No bug directories found!")
            return
        
        print(f"\nFound {len(bug_dirs)} bugs to process.")
        
        all_results = []
        for i, bug_dir in enumerate(bug_dirs, 1):
            print(f"\n[{i}/{len(bug_dirs)}] {bug_dir.name}")
            result = self.process_bug(bug_dir)
            if result:
                all_results.append(result)
        
        self._save_summary(all_results)
        self._print_summary()

    def _save_summary(self, all_results: List[Dict]):
        """Save overall summary."""
        summary = {
            'timestamp': datetime.now().isoformat(),
            'stats': self.stats,
            'bugs_with_tests': [r['bug_id'] for r in all_results],
            'bugs_filtered_out': len(self.filtered_bugs)
        }
        
        with open(self.output_dir / 'summary.json', 'w') as f:
            json.dump(summary, f, indent=2)
        
        with open(self.output_dir / 'filtered_bugs.json', 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'total_filtered': len(self.filtered_bugs),
                'bugs': self.filtered_bugs
            }, f, indent=2)
        
        with open(self.output_dir / 'report.txt', 'w') as f:
            f.write("=" * 70 + "\n")
            f.write("STEP 8: TEST FILE DISCOVERY REPORT\n")
            f.write("(3-Tier Chain with Strict Type Matching)\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n\n")
            f.write("BUG STATISTICS\n" + "-" * 40 + "\n")
            f.write(f"Bugs with tests (kept):       {self.stats['bugs_with_tests']}\n")
            f.write(f"Bugs without tests (filtered): {self.stats['bugs_without_tests']}\n\n")
            f.write("SOURCE FILE STATISTICS\n" + "-" * 40 + "\n")
            f.write(f"Source files processed:     {self.stats['source_files_processed']}\n")
            f.write(f"Source files with tests:    {self.stats['source_files_with_tests']}\n")
            f.write(f"Source files without tests: {self.stats['source_files_without_tests']}\n\n")
            f.write("TEST DISCOVERY BY TIER\n" + "-" * 40 + "\n")
            f.write(f"Tier 0 (fixing commit):   {self.stats['tests_found_tier0']}\n")
            f.write(f"Tier 1 (name match):      {self.stats['tests_found_tier1_name']}\n")
            f.write(f"Tier 1 (content match):   {self.stats['tests_found_tier1_content']}\n")
            f.write(f"Tier 2 (global fallback): {self.stats['tests_found_tier2']}\n\n")
            f.write("EXTRACTION\n" + "-" * 40 + "\n")
            f.write(f"Test files extracted: {self.stats['test_files_extracted']}\n")
            f.write(f"Extraction errors:    {self.stats['extraction_errors']}\n")
        
        print(f"\n✓ Summary saved to {self.output_dir}")

    def _print_summary(self):
        """Print final summary."""
        print(f"\n{'=' * 70}")
        print("COMPLETE")
        print(f"{'=' * 70}")
        print(f"\n  BUGS: kept={self.stats['bugs_with_tests']}, filtered={self.stats['bugs_without_tests']}")
        print(f"  SOURCE FILES: with_tests={self.stats['source_files_with_tests']}, without={self.stats['source_files_without_tests']}")
        print(f"\n  TESTS BY TIER:")
        print(f"    Tier 0 (fixing commit): {self.stats['tests_found_tier0']}")
        print(f"    Tier 1 (name match):    {self.stats['tests_found_tier1_name']}")
        print(f"    Tier 1 (content):       {self.stats['tests_found_tier1_content']}")
        print(f"    Tier 2 (global):        {self.stats['tests_found_tier2']}")
        print(f"\n  EXTRACTED: {self.stats['test_files_extracted']} tests")
        print(f"\nOutput: {self.output_dir}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Find and extract test files (3-tier chain)')
    parser.add_argument('--mozilla-central', type=str, help='Path to mozilla-central')
    parser.add_argument('--bug', type=str, help='Process specific bug ID')
    args = parser.parse_args()
    
    finder = TestFinder(mozilla_central_path=args.mozilla_central)
    finder.run(bug_filter=args.bug)


if __name__ == "__main__":
    main()