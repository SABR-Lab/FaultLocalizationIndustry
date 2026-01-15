#!/usr/bin/env python3
"""
================================================================================
TEST FILE EXTRACTION: Group by Common Files Across Commits
================================================================================

PURPOSE:
--------
Extract test files for source files, avoiding duplicate extraction when the same
source file appears in multiple commits. Groups commits by common files.

INPUT:
------
- coverage output: outputs/coverage_reports/bugs/bug_*/

OUTPUT:
-------
outputs/test_files/
├── bugs/
│   └── bug_<id>/
│       ├── <commits_group>/
│       │   ├── commits_info.json
│       │   ├── <source_filename>/
│       │   │   ├── tests/
│       │   │   │   └── <suite>/<test_files>
│       │   │   ├── test_file_sources.json      # which commit has which test file
│       │   │   └── dependencies/
│       │   └── run_tests.sh
│       └── extraction_summary.json
├── summary.json
└── report.txt
"""

import json
import shutil
import sys
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict


class TestExtractor:
    """Extract test files grouped by common files across commits"""
    
    SUPPORT_PATTERNS = [
        r'^head.*\.js$', r'^helper.*\.js$', r'^common.*\.js$',
        r'^shared.*\.js$', r'^utils?\.js$', r'^fixture.*',
        r'^data.*', r'^resource.*', r'.*_helper\.js$', r'.*_common\.js$',
    ]
    
    MANIFEST_PATTERNS = [
        'xpcshell.toml', 'xpcshell.ini', 'mochitest.toml', 'mochitest.ini',
        'browser.toml', 'browser.ini', 'chrome.toml', 'chrome.ini', 'moz.build',
    ]
    
    def __init__(self, mozilla_central_path: str = None):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: coverage_reports
        self.input_dir = self.outputs_base / "line_level_coverage_reports" / "bugs"
        
        # OUTPUT
        self.output_dir = self.outputs_base / "test_files"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Mozilla-central path
        if mozilla_central_path:
            self.mozilla_central = Path(mozilla_central_path)
        else:
            possible_paths = [
                Path.home() / "mozilla-central",
                Path.home() / "Mozilla_crashAnalyzer_BugBug" / "mozilla-central",
                Path("/root/FaultLocalizationIndustry/mozilla-central"),
            ]
            self.mozilla_central = next((p for p in possible_paths if p.exists()), None)
        
        # Statistics
        self.stats = {
            'bugs_processed': 0,
            'bugs_with_test_files': 0,
            'total_unique_files_with_tests': 0,
            'files_shared_fixing_regressor': 0,
            'files_only_in_fixing': 0,
            'files_only_in_regressor': 0,
            'files_with_same_tests_across_commits': 0,
            'files_with_different_tests_across_commits': 0,
            'test_files_extracted': 0,
            'support_files_extracted': 0,
            'manifests_extracted': 0,
            'extraction_errors': 0,
            'total_bytes_extracted': 0,
        }
        
        self.bugs_with_tests = []
        
        print(f"Input directory: {self.input_dir}")
        print(f"Output directory: {self.output_dir}")
        print(f"Mozilla-central: {self.mozilla_central or 'NOT FOUND'}")
    
    def normalize_filename(self, filepath: str) -> str:
        """Normalize filepath for directory naming"""
        return filepath.replace('/', '_').replace('\\', '_')
    
    def get_commits_from_summary(self, bug_dir: Path) -> Dict:
        """Read summary.json to get commit information"""
        summary_file = bug_dir / "summary.json"
        if not summary_file.exists():
            return {'fixing_commits': [], 'regressor_commits': []}
        
        try:
            with open(summary_file) as f:
                data = json.load(f)
            return {
                'fixing_commits': data.get('fixing_commits', []),
                'regressor_commits': data.get('regressor_commits', [])
            }
        except:
            return {'fixing_commits': [], 'regressor_commits': []}
    
    def find_test_json_files(self, commit_dir: Path) -> List[Dict]:
        """Find all *_tests.json files in a commit directory"""
        test_json_files = []
        
        if not commit_dir.exists():
            return []
        
        for tests_json in commit_dir.glob("*_tests.json"):
            try:
                with open(tests_json) as f:
                    data = json.load(f)
                
                source_file = data.get('source_file', '')
                if not source_file:
                    json_name = tests_json.name
                    source_file = json_name.replace('_tests.json', '').replace('_', '/')
                
                if data.get('all_tests'):
                    test_json_files.append({
                        'source_file': source_file,
                        'safe_filename': self.normalize_filename(source_file),
                        'tests': data['all_tests'],
                        'json_path': str(tests_json)
                    })
            except Exception as e:
                print(f"      Error reading {tests_json.name}: {e}")
        
        return test_json_files
    
    def collect_files_across_commits(self, bug_dir: Path, commits_info: Dict) -> Dict:
        """
        Collect all source files and their test files across all commits.
        Returns: {source_file: {
            'fixing_commits': [{'hash': ..., 'full_hash': ..., 'tests': [...]}],
            'regressor_commits': [{'hash': ..., 'full_hash': ..., 'tests': [...]}]
        }}
        """
        files_map = defaultdict(lambda: {'fixing_commits': [], 'regressor_commits': []})
        
        # Process fixing commits
        for commit in commits_info.get('fixing_commits', []):
            commit_hash = commit.get('commit_hash', '')
            full_hash = commit.get('full_hash', commit_hash)
            
            commit_dir = bug_dir / 'fixing_commits' / commit_hash
            test_jsons = self.find_test_json_files(commit_dir)
            
            for tj in test_jsons:
                source_file = tj['source_file']
                files_map[source_file]['fixing_commits'].append({
                    'hash': commit_hash,
                    'full_hash': full_hash,
                    'tests': tj['tests']
                })
        
        # Process regressor commits
        for commit in commits_info.get('regressor_commits', []):
            commit_hash = commit.get('commit_hash', '')
            full_hash = commit.get('full_hash', commit_hash)
            
            commit_dir = bug_dir / 'regressor_commits' / commit_hash
            test_jsons = self.find_test_json_files(commit_dir)
            
            for tj in test_jsons:
                source_file = tj['source_file']
                files_map[source_file]['regressor_commits'].append({
                    'hash': commit_hash,
                    'full_hash': full_hash,
                    'tests': tj['tests']
                })
        
        return dict(files_map)
    
    def create_commit_group_name(self, fixing_commits: List[Dict], 
                                  regressor_commits: List[Dict]) -> str:
        """Create directory name from commit hashes"""
        parts = []
        
        if fixing_commits:
            fixing_hashes = '_'.join([c['full_hash'][:12] for c in fixing_commits])
            parts.append(f"fixing_{fixing_hashes}")
        
        if regressor_commits:
            regressor_hashes = '_'.join([c['full_hash'][:12] for c in regressor_commits])
            parts.append(f"regressor_{regressor_hashes}")
        
        return '_'.join(parts) if parts else 'unknown'
    
    def analyze_test_differences(self, fixing_commits: List[Dict], 
                                  regressor_commits: List[Dict]) -> Dict:
        """
        Analyze if test files are the same or different across commits.
        Returns info about which tests are in which commits.
        """
        # Collect all unique test paths and track which commits have them
        test_to_commits = defaultdict(lambda: {'fixing': [], 'regressor': []})
        
        for commit in fixing_commits:
            for test in commit['tests']:
                test_path = test['path']
                test_to_commits[test_path]['fixing'].append({
                    'hash': commit['hash'],
                    'full_hash': commit['full_hash']
                })
        
        for commit in regressor_commits:
            for test in commit['tests']:
                test_path = test['path']
                test_to_commits[test_path]['regressor'].append({
                    'hash': commit['hash'],
                    'full_hash': commit['full_hash']
                })
        
        # Categorize tests
        all_tests = []
        tests_in_both = []
        tests_only_fixing = []
        tests_only_regressor = []
        
        for test_path, commits in test_to_commits.items():
            test_info = {
                'path': test_path,
                'in_fixing_commits': commits['fixing'],
                'in_regressor_commits': commits['regressor']
            }
            all_tests.append(test_info)
            
            if commits['fixing'] and commits['regressor']:
                tests_in_both.append(test_info)
            elif commits['fixing']:
                tests_only_fixing.append(test_info)
            else:
                tests_only_regressor.append(test_info)
        
        are_tests_same = (len(tests_only_fixing) == 0 and len(tests_only_regressor) == 0)
        
        return {
            'are_tests_same_across_commits': are_tests_same,
            'all_tests': all_tests,
            'tests_in_both_commit_types': tests_in_both,
            'tests_only_in_fixing': tests_only_fixing,
            'tests_only_in_regressor': tests_only_regressor,
            'total_unique_tests': len(all_tests)
        }
    
    def extract_test_file(self, test_path: str, output_dir: Path, 
                          suite: str = 'unknown') -> Optional[Dict]:
        """Extract a single test file"""
        source_path = self.mozilla_central / test_path
        
        if not source_path.exists():
            return {'path': test_path, 'error': 'file_not_found'}
        
        suite_dir = output_dir / suite
        suite_dir.mkdir(parents=True, exist_ok=True)
        
        dest_path = suite_dir / Path(test_path).name
        
        try:
            shutil.copy2(source_path, dest_path)
            file_size = dest_path.stat().st_size
            self.stats['test_files_extracted'] += 1
            self.stats['total_bytes_extracted'] += file_size
            
            # Extract dependencies
            deps = self.extract_dependencies(source_path, suite_dir)
            
            return {
                'original_path': test_path,
                'extracted_path': str(dest_path.relative_to(self.output_dir)),
                'suite': suite,
                'size_bytes': file_size,
                'dependencies': deps
            }
        except Exception as e:
            self.stats['extraction_errors'] += 1
            return {'path': test_path, 'error': str(e)}
    
    def extract_dependencies(self, test_file: Path, output_dir: Path) -> Dict:
        """Extract support files and manifests"""
        deps = {'manifests': [], 'support_files': [], 'imports': []}
        test_dir = test_file.parent
        
        # Manifests
        for manifest_name in self.MANIFEST_PATTERNS:
            manifest_path = test_dir / manifest_name
            if manifest_path.exists():
                dest = output_dir / manifest_name
                if not dest.exists():
                    try:
                        shutil.copy2(manifest_path, dest)
                        deps['manifests'].append(manifest_name)
                        self.stats['manifests_extracted'] += 1
                    except:
                        pass
        
        # Support files
        try:
            for f in test_dir.iterdir():
                if not f.is_file():
                    continue
                for pattern in self.SUPPORT_PATTERNS:
                    if re.match(pattern, f.name, re.IGNORECASE):
                        dest = output_dir / f.name
                        if not dest.exists():
                            try:
                                shutil.copy2(f, dest)
                                deps['support_files'].append(f.name)
                                self.stats['support_files_extracted'] += 1
                            except:
                                pass
                        break
        except:
            pass
        
        # JS imports
        if test_file.suffix == '.js':
            deps['imports'] = self.parse_js_imports(test_file)
        
        return deps
    
    def parse_js_imports(self, js_file: Path) -> List[str]:
        """Parse import statements from JS file"""
        imports = []
        try:
            with open(js_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            patterns = [
                r'ChromeUtils\.import[^\(]*\(["\']([^"\']+)["\']',
                r'Cu\.import[^\(]*\(["\']([^"\']+)["\']',
                r'import\s+.*?from\s+["\']([^"\']+)["\']',
                r'loadSubScript[^\(]*\(["\']([^"\']+)["\']',
            ]
            for pattern in patterns:
                imports.extend(re.findall(pattern, content))
        except:
            pass
        return list(set(imports))
    
    def create_run_script(self, bug_id: str, source_file: str,
                          fixing_commits: List[Dict], regressor_commits: List[Dict],
                          test_analysis: Dict, output_dir: Path):
        """Create run script that runs tests at both fixing and regressor commits"""
        script_file = output_dir / "run_tests.sh"
        
        # Group tests by suite
        tests_by_suite = defaultdict(list)
        for test_info in test_analysis['all_tests']:
            test_path = test_info['path']
            # Infer suite from path
            if 'xpcshell' in test_path.lower():
                suite = 'xpcshell'
            elif 'browser' in test_path.lower():
                suite = 'browser'
            elif 'mochitest' in test_path.lower():
                suite = 'mochitest'
            else:
                suite = 'unknown'
            tests_by_suite[suite].append(test_path)
        
        with open(script_file, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write(f"# Test runner for bug {bug_id}\n")
            f.write(f"# Source file: {source_file}\n")
            f.write("# Run from mozilla-central directory\n\n")
            f.write("set -e\n\n")
            
            f.write("ORIGINAL_REV=$(hg id -i)\n")
            f.write(f'RESULTS_FILE="bug_{bug_id}_{self.normalize_filename(source_file)}_results.txt"\n')
            f.write('echo "Test Results - $(date)" > $RESULTS_FILE\n\n')
            
            # Run at fixing commits
            if fixing_commits:
                f.write('echo "=== FIXING COMMITS ===" | tee -a $RESULTS_FILE\n')
                for commit in fixing_commits:
                    full_hash = commit['full_hash']
                    f.write(f'\necho "Commit: {full_hash}" | tee -a $RESULTS_FILE\n')
                    f.write(f"hg update -r {full_hash}\n")
                    
                    for suite, test_paths in tests_by_suite.items():
                        paths_str = ' '.join(test_paths)
                        if suite == 'xpcshell':
                            cmd = f"./mach xpcshell-test {paths_str}"
                        elif suite == 'browser':
                            cmd = f"./mach mochitest --flavor browser {paths_str}"
                        elif suite == 'mochitest':
                            cmd = f"./mach mochitest {paths_str}"
                        else:
                            cmd = f"./mach test {paths_str}"
                        
                        f.write(f'echo "  Running {suite}..." | tee -a $RESULTS_FILE\n')
                        f.write(f'if {cmd}; then\n')
                        f.write(f'  echo "    {suite}: PASS" | tee -a $RESULTS_FILE\n')
                        f.write('else\n')
                        f.write(f'  echo "    {suite}: FAIL" | tee -a $RESULTS_FILE\n')
                        f.write('fi\n')
            
            # Run at regressor commits
            if regressor_commits:
                f.write('\necho "" | tee -a $RESULTS_FILE\n')
                f.write('echo "=== REGRESSOR COMMITS ===" | tee -a $RESULTS_FILE\n')
                for commit in regressor_commits:
                    full_hash = commit['full_hash']
                    f.write(f'\necho "Commit: {full_hash}" | tee -a $RESULTS_FILE\n')
                    f.write(f"hg update -r {full_hash}\n")
                    
                    for suite, test_paths in tests_by_suite.items():
                        paths_str = ' '.join(test_paths)
                        if suite == 'xpcshell':
                            cmd = f"./mach xpcshell-test {paths_str}"
                        elif suite == 'browser':
                            cmd = f"./mach mochitest --flavor browser {paths_str}"
                        elif suite == 'mochitest':
                            cmd = f"./mach mochitest {paths_str}"
                        else:
                            cmd = f"./mach test {paths_str}"
                        
                        f.write(f'echo "  Running {suite}..." | tee -a $RESULTS_FILE\n')
                        f.write(f'if {cmd}; then\n')
                        f.write(f'  echo "    {suite}: PASS" | tee -a $RESULTS_FILE\n')
                        f.write('else\n')
                        f.write(f'  echo "    {suite}: FAIL" | tee -a $RESULTS_FILE\n')
                        f.write('fi\n')
            
            f.write('\n# Return to original revision\n')
            f.write('echo "Returning to original revision..." | tee -a $RESULTS_FILE\n')
            f.write("hg update -r $ORIGINAL_REV\n\n")
            f.write('echo "Results saved to: $RESULTS_FILE"\n')
        
        script_file.chmod(0o755)
    
    def process_source_file(self, bug_id: str, source_file: str, 
                            file_data: Dict, bug_output_dir: Path) -> Dict:
        """Process a single source file and its tests"""
        fixing_commits = file_data['fixing_commits']
        regressor_commits = file_data['regressor_commits']
        
        # Create commit group directory name
        group_name = self.create_commit_group_name(fixing_commits, regressor_commits)
        group_dir = bug_output_dir / group_name
        
        # Create source file directory
        safe_filename = self.normalize_filename(source_file)
        file_dir = group_dir / safe_filename
        tests_dir = file_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        
        # Analyze test differences across commits
        test_analysis = self.analyze_test_differences(fixing_commits, regressor_commits)
        
        # Update stats
        if fixing_commits and regressor_commits:
            self.stats['files_shared_fixing_regressor'] += 1
        elif fixing_commits:
            self.stats['files_only_in_fixing'] += 1
        else:
            self.stats['files_only_in_regressor'] += 1
        
        if test_analysis['are_tests_same_across_commits']:
            self.stats['files_with_same_tests_across_commits'] += 1
        else:
            self.stats['files_with_different_tests_across_commits'] += 1
        
        # Save commits info
        commits_info = {
            'source_file': source_file,
            'fixing_commits': [{'hash': c['hash'], 'full_hash': c['full_hash']} 
                              for c in fixing_commits],
            'regressor_commits': [{'hash': c['hash'], 'full_hash': c['full_hash']} 
                                 for c in regressor_commits]
        }
        with open(group_dir / "commits_info.json", 'w') as f:
            json.dump(commits_info, f, indent=2)
        
        # Save test file sources (which commit has which test)
        with open(file_dir / "test_file_sources.json", 'w') as f:
            json.dump(test_analysis, f, indent=2)
        
        # Extract test files (only once per unique test path)
        extracted_tests = []
        seen_paths = set()
        
        for test_info in test_analysis['all_tests']:
            test_path = test_info['path']
            if test_path in seen_paths:
                continue
            seen_paths.add(test_path)
            
            # Get suite from first commit that has this test
            suite = 'unknown'
            all_commits = fixing_commits + regressor_commits
            for commit in all_commits:
                for t in commit['tests']:
                    if t['path'] == test_path:
                        suite = t.get('suite', 'unknown')
                        break
                if suite != 'unknown':
                    break
            
            result = self.extract_test_file(test_path, tests_dir, suite)
            if result:
                result['in_fixing_commits'] = test_info['in_fixing_commits']
                result['in_regressor_commits'] = test_info['in_regressor_commits']
                extracted_tests.append(result)
        
        # Create run script
        self.create_run_script(bug_id, source_file, fixing_commits, 
                               regressor_commits, test_analysis, file_dir)
        
        self.stats['total_unique_files_with_tests'] += 1
        
        return {
            'source_file': source_file,
            'group_name': group_name,
            'fixing_commits': commits_info['fixing_commits'],
            'regressor_commits': commits_info['regressor_commits'],
            'test_analysis': {
                'are_tests_same': test_analysis['are_tests_same_across_commits'],
                'total_unique_tests': test_analysis['total_unique_tests'],
                'tests_in_both': len(test_analysis['tests_in_both_commit_types']),
                'tests_only_fixing': len(test_analysis['tests_only_in_fixing']),
                'tests_only_regressor': len(test_analysis['tests_only_in_regressor'])
            },
            'extracted_tests': len(extracted_tests)
        }
    
    def process_bug(self, bug_dir: Path) -> Optional[Dict]:
        """Process a single bug"""
        bug_id = bug_dir.name.replace('bug_', '')
        print(f"\n  Processing bug {bug_id}...")
        
        commits_info = self.get_commits_from_summary(bug_dir)
        
        # Collect all files across all commits
        files_map = self.collect_files_across_commits(bug_dir, commits_info)
        
        if not files_map:
            print(f"    No files with test files found")
            return None
        
        print(f"    Found {len(files_map)} unique files with tests")
        
        bug_output_dir = self.bugs_output_dir / f"bug_{bug_id}"
        bug_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Process each source file
        file_results = []
        for source_file, file_data in files_map.items():
            print(f"      Processing: {source_file[:50]}...")
            result = self.process_source_file(bug_id, source_file, file_data, bug_output_dir)
            file_results.append(result)
        
        # Save bug summary
        bug_summary = {
            'bug_id': bug_id,
            'total_unique_files': len(file_results),
            'files': file_results
        }
        
        with open(bug_output_dir / "extraction_summary.json", 'w') as f:
            json.dump(bug_summary, f, indent=2)
        
        self.stats['bugs_processed'] += 1
        self.stats['bugs_with_test_files'] += 1
        self.bugs_with_tests.append(bug_id)
        
        return bug_summary
    
    def run(self, bug_filter: str = None):
        """Main execution"""
        print("\n" + "=" * 70)
        print("TEST FILE EXTRACTION V2 - Group by Common Files")
        print("=" * 70)
        
        if not self.mozilla_central or not self.mozilla_central.exists():
            print("ERROR: mozilla-central repository not found!")
            return
        
        if not self.input_dir.exists():
            print(f"ERROR: Input directory not found: {self.input_dir}")
            return
        
        bug_dirs = sorted([
            d for d in self.input_dir.iterdir()
            if d.is_dir() and d.name.startswith('bug_')
        ])
        
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
        
        # Save overall summary
        self.save_summary(all_results)
        self.print_summary()
    
    def save_summary(self, all_results: List[Dict]):
        """Save summary and report"""
        summary = {
            'timestamp': datetime.now().isoformat(),
            'stats': self.stats,
            'bugs_with_test_files': self.bugs_with_tests
        }
        
        with open(self.output_dir / 'summary.json', 'w') as f:
            json.dump(summary, f, indent=2)
        
        # Save report
        report_file = self.output_dir / 'report.txt'
        with open(report_file, 'w') as f:
            f.write("=" * 70 + "\n")
            f.write("TEST FILE EXTRACTION V2 REPORT\n")
            f.write("=" * 70 + "\n\n")
            
            f.write(f"Timestamp: {datetime.now().isoformat()}\n\n")
            
            f.write("BUG STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Bugs processed: {self.stats['bugs_processed']}\n")
            f.write(f"Bugs with test files: {self.stats['bugs_with_test_files']}\n\n")
            
            f.write("FILE STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Total unique files with tests: {self.stats['total_unique_files_with_tests']}\n")
            f.write(f"Files shared in both fixing & regressor: {self.stats['files_shared_fixing_regressor']}\n")
            f.write(f"Files only in fixing commits: {self.stats['files_only_in_fixing']}\n")
            f.write(f"Files only in regressor commits: {self.stats['files_only_in_regressor']}\n\n")
            
            f.write("TEST FILE CONSISTENCY\n")
            f.write("-" * 40 + "\n")
            f.write(f"Files with same tests across commits: {self.stats['files_with_same_tests_across_commits']}\n")
            f.write(f"Files with different tests across commits: {self.stats['files_with_different_tests_across_commits']}\n\n")
            
            f.write("EXTRACTION STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Test files extracted: {self.stats['test_files_extracted']}\n")
            f.write(f"Support files extracted: {self.stats['support_files_extracted']}\n")
            f.write(f"Manifests extracted: {self.stats['manifests_extracted']}\n")
            f.write(f"Extraction errors: {self.stats['extraction_errors']}\n")
            f.write(f"Total bytes extracted: {self.stats['total_bytes_extracted'] / 1024:.1f} KB\n")
        
        print(f"\n✓ Saved summary to {self.output_dir / 'summary.json'}")
        print(f"✓ Saved report to {report_file}")
    
    def print_summary(self):
        """Print final summary"""
        print(f"\n{'=' * 70}")
        print("EXTRACTION COMPLETE")
        print(f"{'=' * 70}")
        
        print(f"\n  BUG STATISTICS:")
        print(f"  Bugs processed: {self.stats['bugs_processed']}")
        print(f"  Bugs with test files: {self.stats['bugs_with_test_files']}")
        
        print(f"\n  FILE STATISTICS:")
        print(f"  Total unique files with tests: {self.stats['total_unique_files_with_tests']}")
        print(f"  Files shared in both fixing & regressor: {self.stats['files_shared_fixing_regressor']}")
        print(f"  Files only in fixing commits: {self.stats['files_only_in_fixing']}")
        print(f"  Files only in regressor commits: {self.stats['files_only_in_regressor']}")
        
        print(f"\n  TEST FILE CONSISTENCY:")
        print(f"  Files with same tests across commits: {self.stats['files_with_same_tests_across_commits']}")
        print(f"  Files with different tests across commits: {self.stats['files_with_different_tests_across_commits']}")
        
        print(f"\n  EXTRACTION:")
        print(f"  Test files extracted: {self.stats['test_files_extracted']}")
        print(f"  Support files: {self.stats['support_files_extracted']}")
        print(f"  Manifests: {self.stats['manifests_extracted']}")
        print(f"  Errors: {self.stats['extraction_errors']}")
        print(f"  Total size: {self.stats['total_bytes_extracted'] / 1024:.1f} KB")
        
        print(f"\nOutput: {self.output_dir}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Extract test files grouped by common files')
    parser.add_argument('--mozilla-central', type=str, default=None,
                        help='Path to mozilla-central repository')
    parser.add_argument('--bug', type=str, default=None,
                        help='Process only a specific bug ID')
    
    args = parser.parse_args()
    
    extractor = TestExtractor(mozilla_central_path=args.mozilla_central)
    extractor.run(bug_filter=args.bug)


if __name__ == "__main__":
    main()