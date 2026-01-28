#!/usr/bin/env python3
"""
================================================================================
EXTRACT CODE COVERAGE (Selenium Web Scraper)
================================================================================

PURPOSE:
--------
Scrape coverage data from coverage.moz.tools for files in fixing and regressor
commits identified in Step 6.

INPUT:
------
- Step 6 output: outputs/step6_overlapping_files/bugs/bug_*.json

OUTPUT:
-------
outputs/line_level_coverage/
├── bugs/
│   └── bug_<id>/
│       ├── fixing_commits/
│       │   └── <commit_hash>/
│       │       └── <filename>_coverage.json
│       └── regressor_commits/
│           └── <commit_hash>/
│               └── <filename>_coverage.json
├── coverage_summary.json
└── coverage_data.json
"""

import json
import time
import sys
import os
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
from typing import Dict, Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)


class CoverageExtractor:
    """Extract coverage data from coverage.moz.tools using Selenium"""
    
    COVERAGE_URL = "https://coverage.moz.tools"

    def __init__(self, headless: bool = True):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Step 6 output directory
        self.input_dir = self.outputs_base / "step6_overlapping_files" / "bugs"
        
        # OUTPUT: Step 7 output directory
        self.output_dir = self.outputs_base / "line_level_coverage"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        self.headless = headless
        self.driver = None
        
        # Statistics
        self.stats = {
            'total_bugs': 0,
            'bugs_processed': 0,
            'bugs_with_coverage': 0,
            'bugs_without_coverage': 0,
            'total_files': 0,
            'files_with_coverage': 0,
            'files_without_coverage': 0,
            'covered_lines': 0,
            'uncovered_lines': 0,
        }
        self.bugs_with_coverage = []
        self.bugs_without_coverage = []
        self.all_results = []
        
        print(f"Input directory: {self.input_dir}")
        print(f"Output directory: {self.output_dir}")
    
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
    # COVERAGE EXTRACTION
    # =========================================================================
    
    def get_coverage_for_file(self, revision: str, file_path: str) -> Optional[Dict]:
        """Get coverage data for a specific file from coverage.moz.tools"""
        self.ensure_driver()
        
        encoded_path = quote(file_path, safe='')
        url = f"{self.COVERAGE_URL}/#revision={revision}&path={encoded_path}&view=file"
        
        try:
            self.driver.get(url)
            
            print(f"          Loading coverage page...")
            max_wait = 30
            check_interval = 2
            coverage_loaded = False
            
            for i in range(max_wait // check_interval):
                time.sleep(check_interval)
                page_source = self.driver.page_source
                
                if ('rgb(199, 255, 166)' in page_source or
                    'rgb(255, 158, 138)' in page_source or
                    '199, 255, 166' in page_source or
                    '255, 158, 138' in page_source):
                    coverage_loaded = True
                    break
            
            if not coverage_loaded:
                time.sleep(5)
            
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
        
        js_script = """
        var rows = document.querySelectorAll('tr');
        var results = [];
        
        rows.forEach(function(row) {
            var cells = row.querySelectorAll('td');
            if (cells.length === 0) return;
            
            var lineNum = null;
            for (var i = 0; i < cells.length; i++) {
                var text = cells[i].textContent.trim();
                if (/^\\d+$/.test(text)) {
                    lineNum = parseInt(text);
                    break;
                }
            }
            
            if (!lineNum) return;
            
            var status = 'not_instrumented';
            var hits = null;
            
            for (var i = 0; i < cells.length; i++) {
                var bg = window.getComputedStyle(cells[i]).backgroundColor;
                
                if (bg.indexOf('199, 255, 166') !== -1) {
                    status = 'covered';
                    var cellText = cells[i].textContent.trim();
                    if (cellText && (/\\d/.test(cellText) || cellText.indexOf('k') !== -1 || cellText.indexOf('K') !== -1)) {
                        hits = cellText;
                    }
                    break;
                }
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
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr")
            for row in rows:
                line_data = self._parse_table_row_with_js(row)
                if line_data:
                    result['lines'].append(line_data)
                    self._update_summary(result['summary'], line_data['status'])
        
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
            
            line_num = None
            for cell in cells:
                text = cell.text.strip()
                if text.isdigit():
                    line_num = int(text)
                    break
            
            if not line_num:
                return None
            
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
                    
                    if '199, 255, 166' in bg_color:
                        status = "covered"
                        text = cell.text.strip()
                        if text and (text.replace(',', '').replace(' ', '').isdigit() or 
                                    'k' in text.lower() or 'm' in text.lower()):
                            hits = text
                        break
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
        """Process a single file - get coverage only."""
        if not self.is_cpp_file(filename):
            return {'filename': filename, 'skipped': True, 'reason': 'not_cpp'}
        
        print(f"        {filename[:55]}...")
        self.stats['total_files'] += 1
        
        safe_filename = filename.replace('/', '_').replace('\\', '_')
        out_dir = self.bugs_output_dir / f"bug_{bug_id}" / commit_type / commit_hash
        out_dir.mkdir(parents=True, exist_ok=True)
        
        result = {
            'filename': filename,
            'safe_filename': safe_filename,
            'has_coverage': False
        }
        
        # Get coverage data
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
        
        return {
            'commit_hash': hash_short,
            'full_hash': revision,
            'files': results
        }
    
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
        
        result = {
            'bug_id': bug_id,
            'fixing_commits': [],
            'regressor_commits': [],
            'has_coverage': False
        }
        
        bug_has_coverage = False
        
        print(f"    Processing fixing commits...")
        for commit in data.get('fixing_commits', []):
            commit_result = self.process_commit(bug_id, commit, 'fixing_commits')
            result['fixing_commits'].append(commit_result)
            
            for file_result in commit_result.get('files', []):
                if file_result.get('has_coverage', False):
                    bug_has_coverage = True
        
        print(f"    Processing regressor commits...")
        for commit in data.get('regressor_commits', []):
            commit_result = self.process_commit(bug_id, commit, 'regressor_commits')
            result['regressor_commits'].append(commit_result)
            
            for file_result in commit_result.get('files', []):
                if file_result.get('has_coverage', False):
                    bug_has_coverage = True
        
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
        
        # Save bug summary
        bug_summary_file = self.bugs_output_dir / f"bug_{bug_id}" / "summary.json"
        bug_summary_file.parent.mkdir(parents=True, exist_ok=True)
        with open(bug_summary_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        return result
        
    def run(self):
        """Main execution."""
        print("\n" + "=" * 70)
        print("STEP 7: COVERAGE EXTRACTION")
        print("=" * 70)
        
        if not self.input_dir.exists():
            print(f"ERROR: Input directory not found: {self.input_dir}")
            print("Please run Step 6 first.")
            return
        
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
        
        # Save summary
        summary = {
            'timestamp': datetime.now().isoformat(),
            'stats': self.stats,
            'bugs_with_coverage': self.bugs_with_coverage,
            'bugs_without_coverage': self.bugs_without_coverage
        }

        with open(self.output_dir / 'coverage_summary.json', 'w') as f:
            json.dump(summary, f, indent=2)

        # Save bugs with coverage
        results_with_coverage = [r for r in self.all_results if r.get('has_coverage', False)]
        with open(self.output_dir / 'coverage_data.json', 'w') as f:
            json.dump(results_with_coverage, f, indent=2)

        # Save all results
        with open(self.output_dir / 'coverage_data_all.json', 'w') as f:
            json.dump(self.all_results, f, indent=2)

        self._print_summary()

    def _print_summary(self):
        """Print final summary."""
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
        print(f"\n  Bugs with coverage: {self.bugs_with_coverage}")
        print(f"  Bugs without coverage: {self.bugs_without_coverage}")
        print(f"\nOutput: {self.output_dir}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Extract code coverage data')
    parser.add_argument('--visible', action='store_true',
                        help='Run browser in visible mode (for debugging)')
    
    args = parser.parse_args()
    
    extractor = CoverageExtractor(headless=not args.visible)
    extractor.run()


if __name__ == "__main__":
    main()