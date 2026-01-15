#!/usr/bin/env python3
"""
================================================================================
EXTRACT CODE COVERAGE + FIND TEST FILES (Selenium Web Scraper)
================================================================================

PURPOSE:
--------
1. Scrape coverage data from coverage.moz.tools for files in fixing and regressor
   commits identified in Step 6.
2. Find related test files in mozilla-central repository.

INPUT:
------
- Step 6 output: outputs/step6_overlapping_files/bugs/bug_*.json
- Mozilla-central repository (for test file search)

OUTPUT:
-------
outputs/line_level_coverage_reports/
├── bugs/
│   └── bug_<id>/
│       ├── fixing_commits/
│       │   └── <commit_hash>/
│       │       ├── <filename>_coverage.json
│       │       └── <filename>_tests.json
│       └── regressor_commits/
│           └── <commit_hash>/
│               ├── <filename>_coverage.json
│               └── <filename>_tests.json
├── coverage_summary.json
└── coverage_data.json
"""

import json
import time
import sys
import os
import re
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
from typing import Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)


class CoverageExtractor:
    """Extract coverage data from coverage.moz.tools using Selenium"""
    
    COVERAGE_URL = "https://coverage.moz.tools"
    
    # Test suite patterns - where to look for test files and what patterns to match
    TEST_SUITE_PATTERNS = {
        'build': {
            'directories': ['build', 'test/build'],
            'patterns': [r'^.*\.py$', r'^.*\.sh$']
        },
        'cppunittest': {
            'directories': ['test', 'tests', 'test/cppunittest'],
            'patterns': [r'^Test.*\.cpp$', r'^.*Test\.cpp$']
        },
        'crashtest': {
            'directories': ['crashtests', 'test/crashtests'],
            'patterns': [r'^.*\.html$', r'^.*\.xhtml$']
        },
        'firefox-ui-functional': {
            'directories': ['test/firefox-ui', 'firefox-ui'],
            'patterns': [r'^test_.*\.py$']
        },
        'gtest': {
            'directories': ['gtest', 'tests/gtest', 'test/gtest'],
            'patterns': [r'^.*[Tt]est.*\.cpp$', r'^Test.*\.cpp$']
        },
        'jittest': {
            'directories': ['jit-test', 'test/jit-test'],
            'patterns': [r'^.*\.js$']
        },
        'jsreftest': {
            'directories': ['jsreftest', 'test/jsreftest'],
            'patterns': [r'^.*\.js$']
        },
        'marionette': {
            'directories': ['marionette', 'test/marionette'],
            'patterns': [r'^test_.*\.py$']
        },
        'marionette-integration': {
            'directories': ['marionette/integration', 'test/marionette/integration'],
            'patterns': [r'^test_.*\.py$']
        },
        'marionette-unittest': {
            'directories': ['marionette/unittest', 'test/marionette/unittest'],
            'patterns': [r'^test_.*\.py$']
        },
        'mochitest-a11y': {
            'directories': ['test/a11y', 'tests/a11y'],
            'patterns': [r'^test_.*\.html$', r'^test_.*\.js$']
        },
        'mochitest-browser-a11y': {
            'directories': ['test/browser/a11y', 'browser/test/a11y'],
            'patterns': [r'^browser_.*\.js$']
        },
        'mochitest-browser-chrome': {
            'directories': ['test/browser', 'browser/test', 'tests/browser'],
            'patterns': [r'^browser_.*\.js$']
        },
        'mochitest-browser-media': {
            'directories': ['test/browser/media', 'browser/test/media'],
            'patterns': [r'^browser_.*\.js$']
        },
        'mochitest-browser-translations': {
            'directories': ['test/browser/translations', 'browser/test/translations'],
            'patterns': [r'^browser_.*\.js$']
        },
        'mochitest-chrome': {
            'directories': ['test/chrome', 'tests/chrome'],
            'patterns': [r'^test_.*\.js$', r'^test_.*\.xhtml$']
        },
        'mochitest-chrome-gpu': {
            'directories': ['test/chrome/gpu', 'tests/chrome/gpu'],
            'patterns': [r'^test_.*\.js$', r'^test_.*\.html$']
        },
        'mochitest-devtools-chrome': {
            'directories': ['test/devtools', 'devtools/test'],
            'patterns': [r'^browser_.*\.js$', r'^test_.*\.js$']
        },
        'mochitest-media': {
            'directories': ['test/media', 'tests/media'],
            'patterns': [r'^test_.*\.html$', r'^test_.*\.js$']
        },
        'mochitest-plain': {
            'directories': ['test/mochitest', 'test', 'tests/mochitest'],
            'patterns': [r'^test_.*\.html$', r'^test_.*\.js$', r'^test_.*\.xhtml$']
        },
        'mochitest-plain-gpu': {
            'directories': ['test/gpu', 'tests/gpu'],
            'patterns': [r'^test_.*\.html$', r'^test_.*\.js$']
        },
        'mochitest-remote': {
            'directories': ['test/remote', 'tests/remote'],
            'patterns': [r'^test_.*\.html$', r'^test_.*\.js$']
        },
        'mochitest-webgl1-core': {
            'directories': ['test/webgl1', 'tests/webgl'],
            'patterns': [r'^test_.*\.html$']
        },
        'mochitest-webgl1-ext': {
            'directories': ['test/webgl1/ext', 'tests/webgl/ext'],
            'patterns': [r'^test_.*\.html$']
        },
        'mochitest-webgl2-core': {
            'directories': ['test/webgl2', 'tests/webgl2'],
            'patterns': [r'^test_.*\.html$']
        },
        'mochitest-webgl2-ext': {
            'directories': ['test/webgl2/ext', 'tests/webgl2/ext'],
            'patterns': [r'^test_.*\.html$']
        },
        'mochitest-webgpu': {
            'directories': ['test/webgpu', 'tests/webgpu'],
            'patterns': [r'^test_.*\.html$', r'^test_.*\.js$']
        },
        'reftest': {
            'directories': ['reftests', 'test/reftests', 'reftest'],
            'patterns': [r'^.*\.html$', r'^.*\.xhtml$']
        },
        'source-test': {
            'directories': ['test/source', 'tests/source'],
            'patterns': [r'^.*\.py$', r'^.*\.js$']
        },
        'telemetry-tests-client': {
            'directories': ['test/telemetry', 'tests/telemetry'],
            'patterns': [r'^test_.*\.py$', r'^test_.*\.js$']
        },
        'web-platform-tests': {
            'directories': ['test/wpt', 'testing/web-platform', 'tests/wpt'],
            'patterns': [r'^.*\.html$', r'^.*\.js$', r'^.*\.py$']
        },
        'web-platform-tests-crashtest': {
            'directories': ['test/wpt/crashtest', 'testing/web-platform/crashtest'],
            'patterns': [r'^.*\.html$']
        },
        'web-platform-tests-print-reftest': {
            'directories': ['test/wpt/print-reftest', 'testing/web-platform/print-reftest'],
            'patterns': [r'^.*\.html$']
        },
        'web-platform-tests-reftest': {
            'directories': ['test/wpt/reftest', 'testing/web-platform/reftest'],
            'patterns': [r'^.*\.html$']
        },
        'web-platform-tests-wdspec': {
            'directories': ['test/wpt/wdspec', 'testing/web-platform/wdspec'],
            'patterns': [r'^.*\.py$']
        },
        'xpcshell': {
            'directories': ['test/xpcshell', 'tests/xpcshell', 'test/unit'],
            'patterns': [r'^test_.*\.js$', r'^test_.*\.cpp$']
        }
    }
    

    def __init__(self, headless: bool = True, mozilla_central_path: str = None):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Step 6 output directory
        self.input_dir = self.outputs_base / "step6_overlapping_files" / "bugs"
        
        # OUTPUT: Step 7 output directory
        self.output_dir = self.outputs_base / "line_level_coverage_reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Mozilla-central repository path
        if mozilla_central_path:
            self.mozilla_central = Path(mozilla_central_path)
        else:
            possible_paths = [
                Path.home() / "Mozilla_crashAnalyzer_BugBug" / "mozilla-central",
                Path.home() / "mozilla-central",
                Path("/Users/sidiqafekrat/Mozilla_crashAnalyzer_BugBug/mozilla-central")
            ]
            self.mozilla_central = next((p for p in possible_paths if p.exists()), None)
        
        self.headless = headless
        self.driver = None
        
        # Statistics
        self.stats = {
            'total_bugs': 0,
            'bugs_processed': 0,
            'bugs_with_coverage': 0,

            'total_files': 0,
            'files_with_coverage': 0,
            'files_without_coverage': 0,
            'bugs_without_coverage': 0,
            'covered_lines': 0,
            'uncovered_lines': 0,
            'test_files_found': 0
        }
        self.bugs_with_coverage = []
        self.bugs_without_coverage = []
        self.all_results = []
        
        print(f"Input directory: {self.input_dir}")
        print(f"Output directory: {self.output_dir}")
        print(f"Mozilla-central: {self.mozilla_central or 'NOT FOUND'}")
    
    def setup_driver(self):
        """Setup Chrome browser"""
        print("\nStarting Chrome...")
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.page_load_strategy = 'normal'
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=opts)
        self.driver.set_page_load_timeout(120)
        print("Chrome ready.\n")
    
    def close_driver(self):
        """Close browser"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None
    
    def ensure_driver(self):
        """Ensure driver is running, restart if needed"""
        try:
            if self.driver:
                _ = self.driver.current_url
                return
        except:
            pass
        
        print("Restarting Chrome...")
        self.close_driver()
        time.sleep(2)
        self.setup_driver()
    
    def is_cpp_file(self, filename: str) -> bool:
        """Check if file is a C/C++ source or header file"""
        cpp_extensions = {'.cpp', '.cc', '.cxx', '.c', '.h', '.hpp', '.hxx', '.hh'}
        ext = Path(filename).suffix.lower()
        return ext in cpp_extensions

    # =========================================================================
    # TEST FILE DISCOVERY
    # =========================================================================
    
    def find_test_files(self, source_file: str) -> Dict:
        """Find test files related to a source file."""
        if not self.mozilla_central or not self.mozilla_central.exists():
            return {'error': 'mozilla-central not found', 'tests': []}
        
        source_path = Path(source_file)
        source_name = source_path.stem.lower()
        source_dir = source_path.parent
        
        all_tests = []
        tests_by_suite = {}
        
        for suite_name, suite_info in self.TEST_SUITE_PATTERNS.items():
            suite_tests = []
            
            for test_subdir in suite_info['directories']:
                test_dir = self.mozilla_central / source_dir / test_subdir
                
                if not test_dir.exists():
                    continue
                
                try:
                    for test_file in test_dir.rglob('*'):
                        if not test_file.is_file():
                            continue
                        
                        file_name = test_file.name
                        
                        for pattern in suite_info['patterns']:
                            if re.match(pattern, file_name, re.IGNORECASE):
                                file_name_lower = file_name.lower()
                                
                                if (source_name in file_name_lower or
                                    self._names_related(source_name, file_name_lower)):
                                    
                                    relative_path = str(test_file.relative_to(self.mozilla_central))
                                    test_info = {
                                        'path': relative_path,
                                        'name': file_name,
                                        'suite': suite_name
                                    }
                                    suite_tests.append(test_info)
                                    break
                except:
                    continue
            
            if suite_tests:
                tests_by_suite[suite_name] = suite_tests
                all_tests.extend(suite_tests)
        
        # Search same directory
        additional_tests = self._search_same_directory(source_file)
        if additional_tests:
            tests_by_suite['same_directory'] = additional_tests
            all_tests.extend(additional_tests)
        
        # Remove duplicates
        seen = set()
        unique_tests = []
        for test in all_tests:
            if test['path'] not in seen:
                seen.add(test['path'])
                unique_tests.append(test)
        
        self.stats['test_files_found'] += len(unique_tests)
        
        return {
            'source_file': source_file,
            'tests_by_suite': tests_by_suite,
            'all_tests': unique_tests,
            'total_count': len(unique_tests)
        }
    
    def _names_related(self, source_name: str, test_name: str) -> bool:
        """Check if source and test names are related."""
        source_clean = source_name.replace('_', '').replace('-', '')
        test_clean = test_name.replace('_', '').replace('-', '').replace('test', '')
        
        if source_clean in test_clean or test_clean in source_clean:
            return True
        
        if len(source_clean) >= 6 and source_clean[:6] in test_clean:
            return True
        
        return False
    
    def _search_same_directory(self, source_file: str) -> List[Dict]:
        """Search for test files in the same directory as the source file."""
        if not self.mozilla_central:
            return []
        
        source_path = Path(source_file)
        source_name = source_path.stem.lower()
        source_dir = self.mozilla_central / source_path.parent
        
        tests = []
        
        if not source_dir.exists():
            return []
        
        try:
            for f in source_dir.iterdir():
                if not f.is_file():
                    continue
                
                name = f.name.lower()
                
                if (name.startswith('test_') or name.startswith('test') or
                    '_test.' in name or 'test.' in name):
                    
                    if source_name in name or self._names_related(source_name, name):
                        tests.append({
                            'path': str(f.relative_to(self.mozilla_central)),
                            'name': f.name,
                            'suite': 'same_directory'
                        })
        except:
            pass
        
        return tests

    # =========================================================================
    # COVERAGE EXTRACTION - USING JAVASCRIPT FOR COMPUTED STYLES
    # =========================================================================
    
    def get_coverage_for_file(self, revision: str, file_path: str) -> Optional[Dict]:
        """Get coverage data for a specific file from coverage.moz.tools"""
        self.ensure_driver()
        
        encoded_path = quote(file_path, safe='')
        url = f"{self.COVERAGE_URL}/#revision={revision}&path={encoded_path}&view=file"
        
        try:
            self.driver.get(url)
            
            # Wait for coverage styling to appear
            print(f"          Loading coverage page...")
            max_wait = 30
            check_interval = 2
            coverage_loaded = False
            
            for i in range(max_wait // check_interval):
                time.sleep(check_interval)
                page_source = self.driver.page_source
                
                # Check for coverage styling indicators
                if ('rgb(199, 255, 166)' in page_source or   # Green - covered
                    'rgb(255, 158, 138)' in page_source or   # Red - uncovered
                    '199, 255, 166' in page_source or
                    '255, 158, 138' in page_source):
                    coverage_loaded = True
                    break
            
            if not coverage_loaded:
                # Extra wait as fallback
                time.sleep(5)
            
            # Extract coverage data
            coverage_data = self._extract_coverage_data(file_path, revision)
            return coverage_data
            
        except Exception as e:
            print(f"          Error: {str(e)[:50]}")
            return None
    
    def _extract_coverage_data(self, file_path: str, revision: str) -> Dict:
        """Extract coverage data from the rendered page using JavaScript."""
        result = {
            'file_path': file_path,
            'revision': revision,
            'lines': [],
            'summary': {
                'covered': 0,
                'uncovered': 0,
                'not_instrumented': 0,
                'total': 0,
                'percentage': 0
            }
        }
        
        # Use JavaScript to extract all coverage data at once (much faster)
        js_script = """
        var rows = document.querySelectorAll('tr');
        var results = [];
        
        rows.forEach(function(row) {
            var cells = row.querySelectorAll('td');
            if (cells.length === 0) return;
            
            // Find line number
            var lineNum = null;
            for (var i = 0; i < cells.length; i++) {
                var text = cells[i].textContent.trim();
                if (/^\\d+$/.test(text)) {
                    lineNum = parseInt(text);
                    break;
                }
            }
            
            if (!lineNum) return;
            
            // Check background colors of cells
            var status = 'not_instrumented';
            var hits = null;
            
            for (var i = 0; i < cells.length; i++) {
                var bg = window.getComputedStyle(cells[i]).backgroundColor;
                
                // Green = covered: rgb(199, 255, 166)
                if (bg.indexOf('199, 255, 166') !== -1) {
                    status = 'covered';
                    var cellText = cells[i].textContent.trim();
                    if (cellText && (/\\d/.test(cellText) || cellText.indexOf('k') !== -1 || cellText.indexOf('K') !== -1)) {
                        hits = cellText;
                    }
                    break;
                }
                // Red = uncovered: rgb(255, 158, 138)
                else if (bg.indexOf('255, 158, 138') !== -1) {
                    status = 'uncovered';
                    break;
                }
            }
            
            results.push({line: lineNum, status: status, hits: hits});
        });
        
        return results;
        """
        
        try:
            lines_data = self.driver.execute_script(js_script)
            
            for line_data in lines_data:
                result['lines'].append(line_data)
                self._update_summary(result['summary'], line_data['status'])
            
        except Exception as e:
            print(f"          JS extraction error: {str(e)[:50]}")
            # Fallback to Python-based extraction
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr")
            for row in rows:
                line_data = self._parse_table_row_with_js(row)
                if line_data:
                    result['lines'].append(line_data)
                    self._update_summary(result['summary'], line_data['status'])
        
        # Calculate percentage
        result['summary']['total'] = len(result['lines'])
        covered = result['summary']['covered']
        uncovered = result['summary']['uncovered']
        if covered + uncovered > 0:
            result['summary']['percentage'] = round(covered / (covered + uncovered) * 100, 2)
        
        return result
    
    def _parse_table_row_with_js(self, row) -> Optional[Dict]:
        """Parse a table row using JavaScript to get computed styles."""
        try:
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            if not cells:
                return None
            
            # Get line number
            line_num = None
            for cell in cells:
                text = cell.text.strip()
                if text.isdigit():
                    line_num = int(text)
                    break
            
            if not line_num:
                return None
            
            # Use JavaScript to get computed background color
            status = "not_instrumented"
            hits = None
            
            for cell in cells:
                try:
                    bg_color = self.driver.execute_script(
                        "return window.getComputedStyle(arguments[0]).backgroundColor;",
                        cell
                    )
                    
                    if not bg_color:
                        continue
                    
                    # Green = covered: rgb(199, 255, 166)
                    if '199, 255, 166' in bg_color:
                        status = "covered"
                        text = cell.text.strip()
                        if text and (text.replace(',', '').replace(' ', '').isdigit() or 
                                    'k' in text.lower() or 'm' in text.lower()):
                            hits = text
                        break
                    # Red = uncovered: rgb(255, 158, 138)
                    elif '255, 158, 138' in bg_color:
                        status = "uncovered"
                        break
                except:
                    continue
            
            return {'line': line_num, 'status': status, 'hits': hits}
            
        except:
            return None
    
    def _update_summary(self, summary: Dict, status: str):
        """Update summary counts."""
        if status == 'covered':
            summary['covered'] += 1
        elif status == 'uncovered':
            summary['uncovered'] += 1
        else:
            summary['not_instrumented'] += 1

    # =========================================================================
    # PROCESSING
    # =========================================================================
    
    def process_file(self, bug_id: str, commit_type: str, commit_hash: str,
                     revision: str, filename: str) -> Dict:
        """Process a single file - get coverage and find tests."""
        if not self.is_cpp_file(filename):
            return {'filename': filename, 'skipped': True, 'reason': 'not_cpp'}
        
        print(f"        {filename[:55]}...")
        self.stats['total_files'] += 1
        
        safe_filename = filename.replace('/', '_').replace('\\', '_')
        out_dir = self.bugs_output_dir / f"bug_{bug_id}" / commit_type / commit_hash
        out_dir.mkdir(parents=True, exist_ok=True)
        
        result = {'filename': filename, 'has_coverage': False, 'has_tests': False}
        
        # 1. Get coverage data
        coverage = self.get_coverage_for_file(revision, filename)
        
        if coverage and coverage['summary']['total'] > 0:
            self.stats['files_with_coverage'] += 1
            self.stats['covered_lines'] += coverage['summary']['covered']
            self.stats['uncovered_lines'] += coverage['summary']['uncovered']
            
            pct = coverage['summary']['percentage']
            print(f"          ✓ Coverage: {pct}% ({coverage['summary']['covered']} covered, {coverage['summary']['uncovered']} uncovered)")
            
            coverage_file = out_dir / f"{safe_filename}_coverage.json"
            with open(coverage_file, 'w') as f:
                json.dump(coverage, f, indent=2)
            
            result['has_coverage'] = True
            result['coverage_summary'] = coverage['summary']
            result['coverage_file'] = str(coverage_file)
        else:
            self.stats['files_without_coverage'] += 1
            print(f"          ✗ No coverage data")
        
        # 2. Find related test files
        tests = self.find_test_files(filename)
        
        if tests['total_count'] > 0:
            print(f"          ✓ Tests: {tests['total_count']} test files found")
            
            tests_file = out_dir / f"{safe_filename}_tests.json"
            with open(tests_file, 'w') as f:
                json.dump(tests, f, indent=2)
            
            result['has_tests'] = True
            result['tests_count'] = tests['total_count']
            result['tests_file'] = str(tests_file)
        else:
            print(f"          - No test files found")
        
        return result
    
    def process_commit(self, bug_id: str, commit: Dict, commit_type: str) -> Dict:
        """Process all files in a commit."""
        revision = commit.get('full_hash') or commit.get('commit_hash', '')
        hash_short = commit.get('commit_hash', revision[:12])
        files = commit.get('files', [])
        
        print(f"      Commit: {hash_short} ({len(files)} files)")
        
        results = []
        for filename_data in files:
            if isinstance(filename_data, dict):
                filename = filename_data.get('filename', '')
            else:
                filename = str(filename_data)
            
            if filename:
                result = self.process_file(bug_id, commit_type, hash_short, revision, filename)
                results.append(result)
        
        return {'commit_hash': hash_short, 'full_hash': revision, 'files': results}
    
    def process_bug(self, bug_file: Path) -> Optional[Dict]:
        """Process a single bug file."""
        try:
            with open(bug_file) as f:
                data = json.load(f)
        except Exception as e:
            print(f"    Error reading file: {e}")
            return None
        
        bug_id = data.get('bug_id', 'unknown')
        print(f"\n  Bug {bug_id}")
        
        result = {'bug_id': bug_id, 'fixing_commits': [], 'regressor_commits': [], 'has_coverage': False}
        
        bug_has_coverage = False  # Track if this bug has any file with coverage
        
        print(f"    Processing fixing commits...")
        for commit in data.get('fixing_commits', []):
            commit_result = self.process_commit(bug_id, commit, 'fixing_commits')
            result['fixing_commits'].append(commit_result)
            
            # Check if any file in this commit has coverage
            for file_result in commit_result.get('files', []):
                if file_result.get('has_coverage', False):
                    bug_has_coverage = True
        
        print(f"    Processing regressor commits...")
        for commit in data.get('regressor_commits', []):
            commit_result = self.process_commit(bug_id, commit, 'regressor_commits')
            result['regressor_commits'].append(commit_result)
            
            # Check if any file in this commit has coverage
            for file_result in commit_result.get('files', []):
                if file_result.get('has_coverage', False):
                    bug_has_coverage = True
        
        # Update bug-level coverage tracking
        result['has_coverage'] = bug_has_coverage
        
        if bug_has_coverage:
            self.stats['bugs_with_coverage'] += 1
            self.bugs_with_coverage.append(bug_id)
            print(f"    ✓ Bug {bug_id} has coverage data")
        else:
            self.stats['bugs_without_coverage'] += 1
            self.bugs_without_coverage.append(bug_id)
            print(f"    ✗ Bug {bug_id} has NO coverage data")
        
        self.stats['bugs_processed'] += 1
        self.all_results.append(result)
        
        # Only save bug summary if it has coverage (optional - remove this if you want all)
        bug_summary_file = self.bugs_output_dir / f"bug_{bug_id}" / "summary.json"
        bug_summary_file.parent.mkdir(parents=True, exist_ok=True)
        with open(bug_summary_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        return result
        
    def run(self):
        """Main execution."""
        print("\n" + "=" * 70)
        print("STEP 7: COVERAGE EXTRACTION + TEST FILE DISCOVERY")
        print("=" * 70)
        
        if not self.input_dir.exists():
            print(f"ERROR: Input directory not found: {self.input_dir}")
            print("Please run Step 6 first.")
            return
        
        if not self.mozilla_central or not self.mozilla_central.exists():
            print("WARNING: mozilla-central not found. Test file search will be skipped.")
        
        bug_files = sorted(self.input_dir.glob('bug_*.json'))
        if not bug_files:
            print("No bug files found!")
            return
        
        self.stats['total_bugs'] = len(bug_files)
        print(f"\nFound {len(bug_files)} bugs to process\n")
        
        self.setup_driver()
        
        try:
            for i, bug_file in enumerate(bug_files, 1):
                print(f"[{i}/{len(bug_files)}] {bug_file.name}")
                self.process_bug(bug_file)
        finally:
            self.close_driver()
        
        summary = {'timestamp': datetime.now().isoformat(),'stats': self.stats,'bugs_with_coverage': self.bugs_with_coverage,'bugs_without_coverage': self.bugs_without_coverage
        }

        with open(self.output_dir / 'coverage_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)

        # Save only bugs with coverage to a separate file
        results_with_coverage = [r for r in self.all_results if r.get('has_coverage', False)]
        with open(self.output_dir / 'coverage_data.json', 'w') as f:
            json.dump(results_with_coverage, f, indent=2)

        # Save all results (including those without coverage) to a separate file
        with open(self.output_dir / 'coverage_data_all.json', 'w') as f:
            json.dump(self.all_results, f, indent=2)

        print(f"\n{'=' * 70}")
        print("COMPLETE")
        print(f"{'=' * 70}")
        print(f"  Bugs processed: {self.stats['bugs_processed']}/{self.stats['total_bugs']}")
        print(f"  Bugs WITH coverage: {self.stats['bugs_with_coverage']}")
        print(f"  Bugs WITHOUT coverage: {self.stats['bugs_without_coverage']}")
        print(f"  Files processed: {self.stats['total_files']}")
        print(f"  Files with coverage: {self.stats['files_with_coverage']}")
        print(f"  Files without coverage: {self.stats['files_without_coverage']}")
        print(f"  Total covered lines: {self.stats['covered_lines']}")
        print(f"  Total uncovered lines: {self.stats['uncovered_lines']}")
        print(f"  Test files found: {self.stats['test_files_found']}")

        print(f"\n  Bugs with coverage: {self.bugs_with_coverage}")
        print(f"  Bugs without coverage: {self.bugs_without_coverage}")

        print(f"\nOutput: {self.output_dir}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Extract code coverage and find test files')
    parser.add_argument('--visible', action='store_true',
                        help='Run browser in visible mode (for debugging)')
    parser.add_argument('--mozilla-central', type=str, default=None,
                        help='Path to mozilla-central repository')
    
    args = parser.parse_args()
    
    headless = not args.visible
    
    extractor = CoverageExtractor(
        headless=headless,
        mozilla_central_path=args.mozilla_central
    )
    extractor.run()


if __name__ == "__main__":
    main()
