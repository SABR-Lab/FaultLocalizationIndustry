#!/usr/bin/env python3
"""
================================================================================
 MODIFIED METHOD COVERAGE ANALYSIS
================================================================================

PURPOSE:
--------
Combine method-level coverage data with method-diff matching to identify
which MODIFIED methods (fully or partially) are covered, uncovered, or 
not instrumented. Focus specifically on the changed lines within those methods.

INPUT:
------
- Method Level Coverage: outputs/method_level_coverage/bugs/bug_<ID>/
- Method Diff Matching: outputs/step9_method_diff_matching/bugs/bug_<ID>.json

OUTPUT:
-------
outputs/modified_method_coverage/
├── bugs/
│   └── bug_<ID>/
│       ├── fixing_commits/
│       │   └── <commit_hash>/
│       │       └── <filename>_modified_method_coverage.json
│       └── regressor_commits/
│           └── <commit_hash>/
│               └── <filename>_modified_method_coverage.json
├── summary.json
└── report.txt
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)


class ModifiedMethodCoverageAnalyzer:
    """Analyze coverage of modified methods by combining coverage and diff data"""
    
    def __init__(self):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Method Level Coverage
        self.coverage_dir = self.outputs_base / "method_level_coverage" / "bugs"
        
        # INPUT: Method Diff Matching (Step 9)
        self.diff_match_dir = self.outputs_base / "step9_method_diff_matching" / "bugs"
        
        # OUTPUT: Modified Method Coverage
        self.output_dir = self.outputs_base / "modified_method_coverage"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Statistics
        self.stats = {
            'total_bugs_in_coverage': 0,
            'bugs_with_diff_match': 0,
            'bugs_without_diff_match': 0,
            'bugs_processed': 0,
            'bugs_with_modified_covered_methods': 0,
            'total_files_processed': 0,
            'total_modified_methods': 0,
            'modified_methods_covered': 0,
            'modified_methods_uncovered': 0,
            'modified_methods_not_instrumented': 0,
            'modified_methods_no_coverage_data': 0,
            'total_changed_lines': 0,
            'changed_lines_covered': 0,
            'changed_lines_uncovered': 0,
            'changed_lines_not_instrumented': 0
        }
        
        self.processed_bugs = []
        self.skipped_bugs = []
        self.bugs_with_modified_covered = []
        
        print(f"Method Level Coverage input: {self.coverage_dir}")
        print(f"Method Diff Matching input: {self.diff_match_dir}")
        print(f"Output directory: {self.output_dir}")
    
    def normalize_filename(self, filepath: str) -> str:
        """Normalize filepath for comparison"""
        return filepath.replace('/', '_').replace('\\', '_')
    
    def get_bugs_from_coverage(self) -> List[str]:
        """Get list of bug IDs from method_level_coverage"""
        bugs = []
        if self.coverage_dir.exists():
            for bug_dir in self.coverage_dir.iterdir():
                if bug_dir.is_dir() and bug_dir.name.startswith('bug_'):
                    bug_id = bug_dir.name.replace('bug_', '')
                    bugs.append(bug_id)
        return sorted(bugs)
    
    def load_diff_match_data(self, bug_id: str) -> Optional[Dict]:
        """Load diff matching data for a bug from Step 9"""
        diff_file = self.diff_match_dir / f"bug_{bug_id}.json"
        if not diff_file.exists():
            return None
        try:
            with open(diff_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"      Warning: Failed to load diff match data: {e}")
            return None
    
    def load_coverage_data(self, bug_id: str, commit_type: str, 
                           commit_hash: str, filepath: str) -> Optional[Dict]:
        """Load method coverage data for a specific file"""
        safe_filename = self.normalize_filename(filepath)
        
        # Try exact path first
        coverage_file = (self.coverage_dir / f"bug_{bug_id}" / commit_type / 
                        commit_hash / f"{safe_filename}_method_coverage.json")
        
        if coverage_file.exists():
            try:
                with open(coverage_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"      Warning: Failed to load coverage: {e}")
                return None
        
        # Try partial hash match
        commit_type_dir = self.coverage_dir / f"bug_{bug_id}" / commit_type
        if commit_type_dir.exists():
            for hash_dir in commit_type_dir.iterdir():
                if hash_dir.is_dir():
                    if commit_hash in hash_dir.name or hash_dir.name in commit_hash:
                        potential_file = hash_dir / f"{safe_filename}_method_coverage.json"
                        if potential_file.exists():
                            try:
                                with open(potential_file, 'r', encoding='utf-8') as f:
                                    return json.load(f)
                            except:
                                pass
        return None
    
    def find_method_coverage(self, method_name: str, method_start: int, 
                             method_end: int, coverage_methods: List[Dict]) -> Optional[Dict]:
        """Find matching method in coverage data"""
        for cov_method in coverage_methods:
            # Match by name and line range
            if (cov_method['name'] == method_name and 
                cov_method['start_line'] == method_start and
                cov_method['end_line'] == method_end):
                return cov_method
            # Fallback: match by name and overlapping range
            if cov_method['name'] == method_name:
                cov_start = cov_method['start_line']
                cov_end = cov_method['end_line']
                if (method_start <= cov_end and method_end >= cov_start):
                    return cov_method
        return None
    
    def analyze_changed_lines_coverage(self, changed_lines: List[int], 
                                       coverage_method: Dict) -> Dict:
        """Analyze coverage of specific changed lines"""
        covered_lines = set(coverage_method.get('covered_lines', []))
        uncovered_lines = set(coverage_method.get('uncovered_lines', []))
        not_instrumented_lines = set(coverage_method.get('not_instrumented_lines', []))
        
        changed_covered = []
        changed_uncovered = []
        changed_not_instrumented = []
        
        for line in changed_lines:
            if line in covered_lines:
                changed_covered.append(line)
            elif line in uncovered_lines:
                changed_uncovered.append(line)
            else:
                changed_not_instrumented.append(line)
        
        total_changed = len(changed_lines)
        coverage_percentage = 0
        if len(changed_covered) + len(changed_uncovered) > 0:
            coverage_percentage = round(
                len(changed_covered) / (len(changed_covered) + len(changed_uncovered)) * 100, 2
            )
        
        return {
            'total_changed_lines': total_changed,
            'changed_lines_covered': changed_covered,
            'changed_lines_uncovered': changed_uncovered,
            'changed_lines_not_instrumented': changed_not_instrumented,
            'changed_coverage_percentage': coverage_percentage
        }
    
    def process_modified_method(self, modified_method: Dict, 
                                coverage_methods: List[Dict],
                                modification_type: str) -> Dict:
        """Process a single modified method"""
        method_name = modified_method['name']
        method_start = modified_method['start_line']
        method_end = modified_method['end_line']
        changed_lines = modified_method.get('changed_lines', [])
        
        # For fully modified, all lines are changed
        if modification_type == 'fully_modified' and not changed_lines:
            changed_lines = list(range(method_start, method_end + 1))
        
        result = {
            'name': method_name,
            'type': modified_method.get('type', 'function'),
            'start_line': method_start,
            'end_line': method_end,
            'signature': modified_method.get('signature', ''),
            'modification_type': modification_type,
            'overlap_percentage': modified_method.get('overlap_percentage', 100.0 if modification_type == 'fully_modified' else 0),
            'changed_lines': changed_lines,
            'coverage_status': 'no_coverage_data',
            'changed_lines_analysis': None
        }
        
        # Find coverage for this method
        coverage_method = self.find_method_coverage(
            method_name, method_start, method_end, coverage_methods
        )
        
        if coverage_method:
            result['coverage_status'] = coverage_method['status']
            result['method_coverage_summary'] = coverage_method.get('coverage_summary', {})
            
            # Analyze changed lines specifically
            if changed_lines:
                changed_analysis = self.analyze_changed_lines_coverage(
                    changed_lines, coverage_method
                )
                result['changed_lines_analysis'] = changed_analysis
                
                # Update stats
                self.stats['total_changed_lines'] += changed_analysis['total_changed_lines']
                self.stats['changed_lines_covered'] += len(changed_analysis['changed_lines_covered'])
                self.stats['changed_lines_uncovered'] += len(changed_analysis['changed_lines_uncovered'])
                self.stats['changed_lines_not_instrumented'] += len(changed_analysis['changed_lines_not_instrumented'])
        
        return result
    
    def process_file_commits(self, bug_id: str, file_data: Dict, 
                             diff_file_data: Dict, commit_type: str) -> List[Dict]:
        """Process commits for a file"""
        results = []
        
        # Get commits from diff matching
        diff_commits = diff_file_data.get(commit_type, [])
        
        for diff_commit in diff_commits:
            commit_hash = diff_commit.get('commit_hash', '')
            filepath = diff_commit.get('filepath', '')
            matched_methods = diff_commit.get('matched_methods', {})
            
            if not matched_methods:
                continue
            
            fully_modified = matched_methods.get('fully_modified', [])
            partially_modified = matched_methods.get('partially_modified', [])
            
            # Skip if no modified methods
            if not fully_modified and not partially_modified:
                continue
            
            # Load coverage data for this file/commit
            coverage_data = self.load_coverage_data(
                bug_id, commit_type, commit_hash, filepath
            )
            
            coverage_methods = []
            if coverage_data:
                coverage_methods = coverage_data.get('methods', [])
            
            # Process modified methods
            processed_methods = []
            has_covered_modified = False
            
            for method in fully_modified:
                processed = self.process_modified_method(
                    method, coverage_methods, 'fully_modified'
                )
                processed_methods.append(processed)
                self.stats['total_modified_methods'] += 1
                
                if processed['coverage_status'] == 'covered':
                    self.stats['modified_methods_covered'] += 1
                    has_covered_modified = True
                elif processed['coverage_status'] == 'uncovered':
                    self.stats['modified_methods_uncovered'] += 1
                elif processed['coverage_status'] == 'not_instrumented':
                    self.stats['modified_methods_not_instrumented'] += 1
                else:
                    self.stats['modified_methods_no_coverage_data'] += 1
            
            for method in partially_modified:
                processed = self.process_modified_method(
                    method, coverage_methods, 'partially_modified'
                )
                processed_methods.append(processed)
                self.stats['total_modified_methods'] += 1
                
                if processed['coverage_status'] == 'covered':
                    self.stats['modified_methods_covered'] += 1
                    has_covered_modified = True
                elif processed['coverage_status'] == 'uncovered':
                    self.stats['modified_methods_uncovered'] += 1
                elif processed['coverage_status'] == 'not_instrumented':
                    self.stats['modified_methods_not_instrumented'] += 1
                else:
                    self.stats['modified_methods_no_coverage_data'] += 1
            
            # Calculate summary for this file/commit
            covered_count = sum(1 for m in processed_methods if m['coverage_status'] == 'covered')
            uncovered_count = sum(1 for m in processed_methods if m['coverage_status'] == 'uncovered')
            not_instrumented_count = sum(1 for m in processed_methods if m['coverage_status'] == 'not_instrumented')
            no_data_count = sum(1 for m in processed_methods if m['coverage_status'] == 'no_coverage_data')
            
            commit_result = {
                'commit_hash': commit_hash,
                'full_hash': diff_commit.get('full_hash', 'unknown'),
                'filepath': filepath,
                'has_coverage_data': coverage_data is not None,
                'summary': {
                    'total_modified_methods': len(processed_methods),
                    'fully_modified_count': len(fully_modified),
                    'partially_modified_count': len(partially_modified),
                    'covered': covered_count,
                    'uncovered': uncovered_count,
                    'not_instrumented': not_instrumented_count,
                    'no_coverage_data': no_data_count
                },
                'modified_methods': processed_methods,
                'has_covered_modified_methods': has_covered_modified
            }
            
            results.append(commit_result)
            self.stats['total_files_processed'] += 1
        
        return results
    
    def process_bug(self, bug_id: str) -> Optional[Dict]:
        """Process a single bug"""
        # Load diff match data
        diff_data = self.load_diff_match_data(bug_id)
        if not diff_data:
            self.stats['bugs_without_diff_match'] += 1
            self.skipped_bugs.append(bug_id)
            print(f"    ✗ Skipping - no diff match data")
            return None
        
        self.stats['bugs_with_diff_match'] += 1
        
        bug_result = {
            'bug_id': bug_id,
            'analysis_timestamp': datetime.now().isoformat(),
            'fixing_commits': [],
            'regressor_commits': [],
            'summary': {
                'total_fixing_files': 0,
                'total_regressor_files': 0,
                'has_modified_covered_methods': False
            }
        }
        
        has_modified_covered = False
        
        # Process each file in diff data
        for file_data in diff_data.get('files', []):
            filepath = file_data.get('filepath', '')
            
            # Process fixing commits
            fixing_results = self.process_file_commits(
                bug_id, file_data, file_data, 'fixing_commits'
            )
            for result in fixing_results:
                self.save_file_result(bug_id, 'fixing_commits', 
                                     result['commit_hash'], filepath, result)
                bug_result['fixing_commits'].append(result)
                bug_result['summary']['total_fixing_files'] += 1
                if result['has_covered_modified_methods']:
                    has_modified_covered = True
            
            # Process regressor commits
            regressor_results = self.process_file_commits(
                bug_id, file_data, file_data, 'regressor_commits'
            )
            for result in regressor_results:
                self.save_file_result(bug_id, 'regressor_commits',
                                     result['commit_hash'], filepath, result)
                bug_result['regressor_commits'].append(result)
                bug_result['summary']['total_regressor_files'] += 1
                if result['has_covered_modified_methods']:
                    has_modified_covered = True
        
        bug_result['summary']['has_modified_covered_methods'] = has_modified_covered
        
        if bug_result['fixing_commits'] or bug_result['regressor_commits']:
            self.stats['bugs_processed'] += 1
            self.processed_bugs.append(bug_id)
            
            if has_modified_covered:
                self.stats['bugs_with_modified_covered_methods'] += 1
                self.bugs_with_modified_covered.append(bug_id)
            
            total_files = (bug_result['summary']['total_fixing_files'] + 
                          bug_result['summary']['total_regressor_files'])
            print(f"    ✓ Processed {total_files} files, modified+covered: {has_modified_covered}")
            return bug_result
        else:
            print(f"    - No modified methods found")
            return None
    
    def save_file_result(self, bug_id: str, commit_type: str,
                         commit_hash: str, filepath: str, result: Dict):
        """Save individual file result to JSON"""
        safe_filename = self.normalize_filename(filepath)
        
        output_path = (self.bugs_output_dir / f"bug_{bug_id}" / commit_type /
                      commit_hash / f"{safe_filename}_modified_method_coverage.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
    
    def run(self):
        """Main execution"""
        print("\n" + "=" * 70)
        print("STEP 10: MODIFIED METHOD COVERAGE ANALYSIS")
        print("=" * 70)
        
        # Verify input directories exist
        if not self.coverage_dir.exists():
            print(f"ERROR: Method level coverage not found: {self.coverage_dir}")
            return
        
        if not self.diff_match_dir.exists():
            print(f"ERROR: Diff match directory not found: {self.diff_match_dir}")
            return
        
        # Get bugs from coverage data
        bugs = self.get_bugs_from_coverage()
        
        if not bugs:
            print("No bugs found in method level coverage!")
            return
        
        self.stats['total_bugs_in_coverage'] = len(bugs)
        print(f"\nFound {len(bugs)} bugs in method level coverage\n")
        
        # Process each bug
        all_results = []
        for i, bug_id in enumerate(bugs, 1):
            print(f"[{i}/{len(bugs)}] Bug {bug_id}...")
            
            result = self.process_bug(bug_id)
            if result:
                all_results.append(result)
        
        # Save summary
        self.save_summary(all_results)
        
        # Print final summary
        self.print_summary()
    
    def save_summary(self, all_results: List[Dict]):
        """Save summary and report"""
        summary = {
            'analysis_timestamp': datetime.now().isoformat(),
            'input_coverage': str(self.coverage_dir),
            'input_diff_match': str(self.diff_match_dir),
            'output_directory': str(self.output_dir),
            'stats': self.stats,
            'processed_bugs': self.processed_bugs,
            'skipped_bugs': self.skipped_bugs,
            'bugs_with_modified_covered_methods': self.bugs_with_modified_covered
        }
        
        # Save JSON summary
        summary_file = self.output_dir / 'summary.json'
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
        print(f"\n✓ Saved summary to {summary_file}")
        
        # Save text report
        report_file = self.output_dir / 'report.txt'
        self.save_report(report_file)
        print(f"✓ Saved report to {report_file}")
    
    def save_report(self, report_file: Path):
        """Save human-readable report"""
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("STEP 10: MODIFIED METHOD COVERAGE ANALYSIS REPORT\n")
            f.write("=" * 70 + "\n\n")
            
            f.write(f"Analysis Time: {datetime.now().isoformat()}\n")
            f.write(f"Coverage Input: {self.coverage_dir}\n")
            f.write(f"Diff Match Input: {self.diff_match_dir}\n")
            f.write(f"Output: {self.output_dir}\n\n")
            
            f.write("BUG STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Total bugs in coverage: {self.stats['total_bugs_in_coverage']}\n")
            f.write(f"Bugs with diff match data: {self.stats['bugs_with_diff_match']}\n")
            f.write(f"Bugs without diff match (skipped): {self.stats['bugs_without_diff_match']}\n")
            f.write(f"Bugs processed: {self.stats['bugs_processed']}\n")
            f.write(f"Bugs with modified+covered methods: {self.stats['bugs_with_modified_covered_methods']}\n\n")
            
            f.write("METHOD STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Total files processed: {self.stats['total_files_processed']}\n")
            f.write(f"Total modified methods: {self.stats['total_modified_methods']}\n")
            f.write(f"  - Covered: {self.stats['modified_methods_covered']}\n")
            f.write(f"  - Uncovered: {self.stats['modified_methods_uncovered']}\n")
            f.write(f"  - Not instrumented: {self.stats['modified_methods_not_instrumented']}\n")
            f.write(f"  - No coverage data: {self.stats['modified_methods_no_coverage_data']}\n\n")
            
            f.write("CHANGED LINES STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Total changed lines: {self.stats['total_changed_lines']}\n")
            f.write(f"  - Covered: {self.stats['changed_lines_covered']}\n")
            f.write(f"  - Uncovered: {self.stats['changed_lines_uncovered']}\n")
            f.write(f"  - Not instrumented: {self.stats['changed_lines_not_instrumented']}\n\n")
            
            if self.bugs_with_modified_covered:
                f.write("BUGS WITH MODIFIED+COVERED METHODS\n")
                f.write("-" * 40 + "\n")
                for bug_id in self.bugs_with_modified_covered:
                    f.write(f"  Bug {bug_id}\n")
                f.write("\n")
            
            if self.processed_bugs:
                f.write("ALL PROCESSED BUGS\n")
                f.write("-" * 40 + "\n")
                for bug_id in self.processed_bugs:
                    marker = " [has modified+covered]" if bug_id in self.bugs_with_modified_covered else ""
                    f.write(f"  Bug {bug_id}{marker}\n")
                f.write("\n")
            
            if self.skipped_bugs:
                f.write("SKIPPED BUGS (no diff match data)\n")
                f.write("-" * 40 + "\n")
                for bug_id in self.skipped_bugs:
                    f.write(f"  Bug {bug_id}\n")
    
    def print_summary(self):
        """Print final summary"""
        print(f"\n{'=' * 70}")
        print("ANALYSIS COMPLETE")
        print(f"{'=' * 70}")
        print(f"\n  BUG SUMMARY:")
        print(f"  Total bugs in coverage: {self.stats['total_bugs_in_coverage']}")
        print(f"  Bugs with diff match: {self.stats['bugs_with_diff_match']}")
        print(f"  Bugs processed: {self.stats['bugs_processed']}")
        print(f"  Bugs with modified+covered methods: {self.stats['bugs_with_modified_covered_methods']}")
        
        print(f"\n  METHOD SUMMARY:")
        print(f"  Total modified methods: {self.stats['total_modified_methods']}")
        print(f"    - Covered: {self.stats['modified_methods_covered']}")
        print(f"    - Uncovered: {self.stats['modified_methods_uncovered']}")
        print(f"    - Not instrumented: {self.stats['modified_methods_not_instrumented']}")
        print(f"    - No coverage data: {self.stats['modified_methods_no_coverage_data']}")
        
        print(f"\n  CHANGED LINES SUMMARY:")
        print(f"  Total changed lines: {self.stats['total_changed_lines']}")
        print(f"    - Covered: {self.stats['changed_lines_covered']}")
        print(f"    - Uncovered: {self.stats['changed_lines_uncovered']}")
        print(f"    - Not instrumented: {self.stats['changed_lines_not_instrumented']}")
        
        if self.bugs_with_modified_covered:
            print(f"\n  Bugs with modified+covered methods: {self.bugs_with_modified_covered}")
        
        print(f"\nOutput: {self.output_dir}")


def main():
    """Main execution"""
    analyzer = ModifiedMethodCoverageAnalyzer()
    analyzer.run()


if __name__ == "__main__":
    main()