#!/usr/bin/env python3
"""
================================================================================
COMPLETE ANALYSIS - Group by Common Files with Modified Methods + Coverage + Tests
================================================================================

PURPOSE:
--------
Find files that have modified method coverage in BOTH fixing and regressor commits,
AND have test files. Group output by common file.

INPUT:
------
- Modified Method Coverage: outputs/modified_method_coverage/bugs/
- Test Files: outputs/test_files_v2/bugs/

OUTPUT:
-------
outputs/complete_analysis/
├── bugs/
│   └── bug_<ID>/
│       └── <filename>/                            # common file
│           ├── fixing_commits/
│           │   └── <full_commit_hash>/
│           │       └── <filename>_modified_method_coverage.json
│           ├── regressor_commits/
│           │   └── <full_commit_hash>/
│           │       └── <filename>_modified_method_coverage.json
│           ├── tests/                             # test files ONCE
│           │   └── <suite>/<test_files>
│           └── file_summary.json
├── summary.json
└── report.txt
"""

import json
import shutil
import os
from datetime import datetime
from typing import Dict, List, Optional, Set
from pathlib import Path
import sys
from collections import defaultdict

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)


class CompleteAnalyzer:
    """Combine modified method coverage with test files, grouped by common files"""
    
    def __init__(self, mozilla_central_path: str = None):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Modified Method Coverage
        self.modified_coverage_dir = self.outputs_base / "modified_method_coverage" / "bugs"
        
        # INPUT: Test Files V2
        self.test_files_dir = self.outputs_base / "test_files" / "bugs"
        
        # OUTPUT
        self.output_dir = self.outputs_base / "complete_analysis"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Mozilla-central for copying test files
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
            'total_bugs_in_modified_coverage': 0,
            'total_bugs_in_test_files': 0,
            'bugs_with_both_sources': 0,
            'bugs_with_complete_data': 0,
            'total_common_files': 0,
            'total_fixing_commits': 0,
            'total_regressor_commits': 0,
            'total_test_files_copied': 0,
            'modified_covered_methods_with_tests': 0,
            'modified_uncovered_methods_with_tests': 0,
            'modified_not_instrumented_methods_with_tests': 0,
        }
        
        self.bugs_with_complete_data = []
        
        print(f"Modified Coverage input: {self.modified_coverage_dir}")
        print(f"Test Files input: {self.test_files_dir}")
        print(f"Mozilla-central: {self.mozilla_central or 'NOT FOUND'}")
        print(f"Output directory: {self.output_dir}")
    
    def normalize_filename(self, filepath: str) -> str:
        """Normalize filepath for directory naming"""
        return filepath.replace('/', '_').replace('\\', '_')
    
    def get_bugs_from_modified_coverage(self) -> List[str]:
        """Get list of bug IDs from modified_method_coverage"""
        bugs = []
        if self.modified_coverage_dir.exists():
            for bug_dir in self.modified_coverage_dir.iterdir():
                if bug_dir.is_dir() and bug_dir.name.startswith('bug_'):
                    bug_id = bug_dir.name.replace('bug_', '')
                    bugs.append(bug_id)
        return sorted(bugs)
    
    def get_bugs_from_test_files(self) -> Set[str]:
        """Get set of bug IDs from test_files_v2"""
        bugs = set()
        if self.test_files_dir.exists():
            for bug_dir in self.test_files_dir.iterdir():
                if bug_dir.is_dir() and bug_dir.name.startswith('bug_'):
                    bug_id = bug_dir.name.replace('bug_', '')
                    bugs.add(bug_id)
        return bugs
    
    def get_modified_coverage_files_by_source(self, bug_id: str) -> Dict:
        """
        Get all modified coverage files organized by source file.
        Returns: {
            source_file: {
                'fixing_commits': {full_hash: coverage_data},
                'regressor_commits': {full_hash: coverage_data}
            }
        }
        """
        result = defaultdict(lambda: {'fixing_commits': {}, 'regressor_commits': {}})
        
        bug_dir = self.modified_coverage_dir / f"bug_{bug_id}"
        if not bug_dir.exists():
            return result
        
        for commit_type in ['fixing_commits', 'regressor_commits']:
            commit_type_dir = bug_dir / commit_type
            if not commit_type_dir.exists():
                continue
            
            for hash_dir in commit_type_dir.iterdir():
                if not hash_dir.is_dir():
                    continue
                
                commit_hash = hash_dir.name
                
                for coverage_file in hash_dir.glob("*_modified_method_coverage.json"):
                    try:
                        with open(coverage_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        filepath = data.get('filepath', '')
                        full_hash = data.get('full_hash', commit_hash)
                        
                        if filepath:
                            result[filepath][commit_type][full_hash] = {
                                'data': data,
                                'source_file_path': str(coverage_file)
                            }
                    except Exception as e:
                        print(f"      Warning: Failed to load {coverage_file}: {e}")
                        continue
        
        return dict(result)
    
    def get_test_files_for_source(self, bug_id: str, source_file: str) -> Optional[Dict]:
        """
        Find test files for a specific source file from test_files_v2.
        Returns test file info if found.
        """
        bug_dir = self.test_files_dir / f"bug_{bug_id}"
        if not bug_dir.exists():
            return None
        
        safe_filename = self.normalize_filename(source_file)
        
        # Search through all commit group directories
        for group_dir in bug_dir.iterdir():
            if not group_dir.is_dir():
                continue
            
            # Check if this group has our source file
            source_dir = group_dir / safe_filename
            if not source_dir.exists():
                continue
            
            # Load test file sources
            test_sources_file = source_dir / "test_file_sources.json"
            if test_sources_file.exists():
                try:
                    with open(test_sources_file, 'r', encoding='utf-8') as f:
                        test_data = json.load(f)
                    
                    # Also get the extracted tests directory path
                    tests_dir = source_dir / "tests"
                    
                    return {
                        'test_data': test_data,
                        'tests_dir': tests_dir if tests_dir.exists() else None,
                        'all_tests': test_data.get('all_tests', [])
                    }
                except:
                    continue
        
        return None
    
    def copy_test_files(self, test_info: Dict, output_tests_dir: Path) -> int:
        """Copy test files to output directory. Returns count of files copied."""
        copied = 0
        
        # First try to copy from already extracted tests in test_files_v2
        if test_info.get('tests_dir') and test_info['tests_dir'].exists():
            try:
                if output_tests_dir.exists():
                    shutil.rmtree(output_tests_dir)
                shutil.copytree(test_info['tests_dir'], output_tests_dir)
                # Count files
                for f in output_tests_dir.rglob('*'):
                    if f.is_file():
                        copied += 1
                return copied
            except Exception as e:
                print(f"        Warning: Failed to copy from test_files_v2: {e}")
        
        # Fallback: copy from mozilla-central
        if not self.mozilla_central or not self.mozilla_central.exists():
            return 0
        
        output_tests_dir.mkdir(parents=True, exist_ok=True)
        
        for test in test_info.get('all_tests', []):
            test_path = test.get('path', '')
            suite = test.get('suite', 'unknown')
            
            if not test_path:
                continue
            
            source_path = self.mozilla_central / test_path
            if not source_path.exists():
                continue
            
            suite_dir = output_tests_dir / suite
            suite_dir.mkdir(parents=True, exist_ok=True)
            
            dest_path = suite_dir / Path(test_path).name
            
            try:
                shutil.copy2(source_path, dest_path)
                copied += 1
            except:
                pass
        
        return copied
    
    def copy_coverage_file(self, source_path: str, dest_path: Path):
        """Copy modified method coverage JSON file"""
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        except Exception as e:
            print(f"        Warning: Failed to copy coverage file: {e}")
    
    def count_methods_by_status(self, coverage_data: Dict) -> Dict:
        """Count methods by coverage status"""
        counts = {'covered': 0, 'uncovered': 0, 'not_instrumented': 0, 'no_coverage_data': 0}
        
        for method in coverage_data.get('modified_methods', []):
            status = method.get('coverage_status', 'no_coverage_data')
            if status in counts:
                counts[status] += 1
            else:
                counts['no_coverage_data'] += 1
        
        return counts
    
    def process_bug(self, bug_id: str) -> Optional[Dict]:
        """Process a single bug"""
        # Get all files with modified coverage
        coverage_by_source = self.get_modified_coverage_files_by_source(bug_id)
        
        if not coverage_by_source:
            print(f"    ✗ No modified coverage data")
            return None
        
        bug_result = {
            'bug_id': bug_id,
            'analysis_timestamp': datetime.now().isoformat(),
            'common_files': [],
            'summary': {
                'total_common_files': 0,
                'total_fixing_commits': 0,
                'total_regressor_commits': 0,
                'total_test_files': 0
            }
        }
        
        has_complete_data = False
        
        # Find files that have BOTH fixing and regressor commits AND have test files
        for source_file, commit_data in coverage_by_source.items():
            fixing_commits = commit_data['fixing_commits']
            regressor_commits = commit_data['regressor_commits']
            
            # Must have both fixing and regressor commits
            if not fixing_commits or not regressor_commits:
                continue
            
            # Must have test files
            test_info = self.get_test_files_for_source(bug_id, source_file)
            if not test_info or not test_info.get('all_tests'):
                continue
            
            # This file has everything!
            print(f"       Common file: {source_file[:50]}...")
            print(f"        Fixing commits: {len(fixing_commits)}, Regressor commits: {len(regressor_commits)}")
            print(f"        Test files: {len(test_info['all_tests'])}")
            
            safe_filename = self.normalize_filename(source_file)
            file_output_dir = self.bugs_output_dir / f"bug_{bug_id}" / safe_filename
            file_output_dir.mkdir(parents=True, exist_ok=True)
            
            file_result = {
                'source_file': source_file,
                'fixing_commits': [],
                'regressor_commits': [],
                'test_files_count': len(test_info['all_tests'])
            }
            
            method_counts = {'covered': 0, 'uncovered': 0, 'not_instrumented': 0}
            
            # Copy fixing commit coverage files
            fixing_dir = file_output_dir / "fixing_commits"
            for full_hash, info in fixing_commits.items():
                commit_dir = fixing_dir / full_hash
                commit_dir.mkdir(parents=True, exist_ok=True)
                
                dest_file = commit_dir / f"{safe_filename}_modified_method_coverage.json"
                self.copy_coverage_file(info['source_file_path'], dest_file)
                
                file_result['fixing_commits'].append({
                    'full_hash': full_hash,
                    'coverage_file': str(dest_file.relative_to(self.output_dir))
                })
                
                # Count methods
                counts = self.count_methods_by_status(info['data'])
                method_counts['covered'] += counts['covered']
                method_counts['uncovered'] += counts['uncovered']
                method_counts['not_instrumented'] += counts['not_instrumented']
                
                self.stats['total_fixing_commits'] += 1
            
            # Copy regressor commit coverage files
            regressor_dir = file_output_dir / "regressor_commits"
            for full_hash, info in regressor_commits.items():
                commit_dir = regressor_dir / full_hash
                commit_dir.mkdir(parents=True, exist_ok=True)
                
                dest_file = commit_dir / f"{safe_filename}_modified_method_coverage.json"
                self.copy_coverage_file(info['source_file_path'], dest_file)
                
                file_result['regressor_commits'].append({
                    'full_hash': full_hash,
                    'coverage_file': str(dest_file.relative_to(self.output_dir))
                })
                
                # Count methods
                counts = self.count_methods_by_status(info['data'])
                method_counts['covered'] += counts['covered']
                method_counts['uncovered'] += counts['uncovered']
                method_counts['not_instrumented'] += counts['not_instrumented']
                
                self.stats['total_regressor_commits'] += 1
            
            # Copy test files ONCE
            tests_output_dir = file_output_dir / "tests"
            copied = self.copy_test_files(test_info, tests_output_dir)
            print(f"        Copied {copied} test files")
            
            self.stats['total_test_files_copied'] += copied
            self.stats['modified_covered_methods_with_tests'] += method_counts['covered']
            self.stats['modified_uncovered_methods_with_tests'] += method_counts['uncovered']
            self.stats['modified_not_instrumented_methods_with_tests'] += method_counts['not_instrumented']
            
            # Save file summary
            file_summary = {
                'source_file': source_file,
                'fixing_commits': file_result['fixing_commits'],
                'regressor_commits': file_result['regressor_commits'],
                'test_files': test_info['all_tests'],
                'method_counts': method_counts
            }
            
            with open(file_output_dir / "file_summary.json", 'w', encoding='utf-8') as f:
                json.dump(file_summary, f, indent=2)
            
            bug_result['common_files'].append(file_result)
            bug_result['summary']['total_common_files'] += 1
            bug_result['summary']['total_fixing_commits'] += len(fixing_commits)
            bug_result['summary']['total_regressor_commits'] += len(regressor_commits)
            bug_result['summary']['total_test_files'] += len(test_info['all_tests'])
            
            self.stats['total_common_files'] += 1
            has_complete_data = True
        
        if has_complete_data:
            self.stats['bugs_with_complete_data'] += 1
            self.bugs_with_complete_data.append(bug_id)
            
            # Save bug summary
            bug_summary_file = self.bugs_output_dir / f"bug_{bug_id}" / "bug_summary.json"
            with open(bug_summary_file, 'w', encoding='utf-8') as f:
                json.dump(bug_result, f, indent=2)
            
            print(f"    ✓ Bug {bug_id}: {bug_result['summary']['total_common_files']} common files with complete data")
            return bug_result
        else:
            print(f"    - No common files with complete data")
            return None
    
    def run(self):
        """Main execution"""
        print("\n" + "=" * 70)
        print("COMPLETE ANALYSIS")
        print("Common Files with Modified Methods + Coverage + Tests")
        print("=" * 70)
        
        # Verify input directories
        if not self.modified_coverage_dir.exists():
            print(f"ERROR: Modified coverage not found: {self.modified_coverage_dir}")
            return
        
        if not self.test_files_dir.exists():
            print(f"ERROR: Test files not found: {self.test_files_dir}")
            return
        
        # Get bugs from both sources
        bugs_modified_coverage = self.get_bugs_from_modified_coverage()
        bugs_test_files = self.get_bugs_from_test_files()
        
        self.stats['total_bugs_in_modified_coverage'] = len(bugs_modified_coverage)
        self.stats['total_bugs_in_test_files'] = len(bugs_test_files)
        
        # Find intersection
        bugs_with_both = [b for b in bugs_modified_coverage if b in bugs_test_files]
        self.stats['bugs_with_both_sources'] = len(bugs_with_both)
        
        print(f"\nBugs in modified coverage: {len(bugs_modified_coverage)}")
        print(f"Bugs in test files: {len(bugs_test_files)}")
        print(f"Bugs in both: {len(bugs_with_both)}\n")
        
        if not bugs_with_both:
            print("No bugs found in both sources!")
            return
        
        # Process each bug
        all_results = []
        for i, bug_id in enumerate(bugs_with_both, 1):
            print(f"\n[{i}/{len(bugs_with_both)}] Bug {bug_id}...")
            
            result = self.process_bug(bug_id)
            if result:
                all_results.append(result)
        
        # Save summary
        self.save_summary(all_results)
        self.print_summary()
    
    def save_summary(self, all_results: List[Dict]):
        """Save summary and report"""
        summary = {
            'analysis_timestamp': datetime.now().isoformat(),
            'input_modified_coverage': str(self.modified_coverage_dir),
            'input_test_files': str(self.test_files_dir),
            'output_directory': str(self.output_dir),
            'stats': self.stats,
            'bugs_with_complete_data': self.bugs_with_complete_data
        }
        
        summary_file = self.output_dir / 'summary.json'
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
        print(f"\n✓ Saved summary to {summary_file}")
        
        report_file = self.output_dir / 'report.txt'
        self.save_report(report_file)
        print(f"✓ Saved report to {report_file}")
    
    def save_report(self, report_file: Path):
        """Save human-readable report"""
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write("COMPLETE ANALYSIS REPORT\n")
            f.write("Common Files with Modified Methods + Coverage + Tests\n")
            f.write("=" * 70 + "\n\n")
            
            f.write(f"Analysis Time: {datetime.now().isoformat()}\n\n")
            
            f.write("INPUT STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Bugs in modified coverage: {self.stats['total_bugs_in_modified_coverage']}\n")
            f.write(f"Bugs in test files: {self.stats['total_bugs_in_test_files']}\n")
            f.write(f"Bugs in both sources: {self.stats['bugs_with_both_sources']}\n\n")
            
            f.write("COMPLETE DATA STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Bugs with complete data: {self.stats['bugs_with_complete_data']}\n")
            f.write(f"Total common files: {self.stats['total_common_files']}\n")
            f.write(f"Total fixing commits: {self.stats['total_fixing_commits']}\n")
            f.write(f"Total regressor commits: {self.stats['total_regressor_commits']}\n")
            f.write(f"Total test files copied: {self.stats['total_test_files_copied']}\n\n")
            
            f.write("METHOD STATISTICS (in files with tests)\n")
            f.write("-" * 40 + "\n")
            f.write(f"Modified+covered methods: {self.stats['modified_covered_methods_with_tests']}\n")
            f.write(f"Modified+uncovered methods: {self.stats['modified_uncovered_methods_with_tests']}\n")
            f.write(f"Modified+not_instrumented methods: {self.stats['modified_not_instrumented_methods_with_tests']}\n\n")
            
            if self.bugs_with_complete_data:
                f.write("BUGS WITH COMPLETE DATA\n")
                f.write("-" * 40 + "\n")
                for bug_id in self.bugs_with_complete_data:
                    f.write(f"  Bug {bug_id}\n")
    
    def print_summary(self):
        """Print final summary"""
        print(f"\n{'=' * 70}")
        print("ANALYSIS COMPLETE")
        print(f"{'=' * 70}")
        
        print(f"\n  INPUT:")
        print(f"  Bugs in modified coverage: {self.stats['total_bugs_in_modified_coverage']}")
        print(f"  Bugs in test files: {self.stats['total_bugs_in_test_files']}")
        print(f"  Bugs in both: {self.stats['bugs_with_both_sources']}")
        
        print(f"\n  COMPLETE DATA:")
        print(f"  Bugs with complete data: {self.stats['bugs_with_complete_data']}")
        print(f"  Total common files: {self.stats['total_common_files']}")
        print(f"  Total fixing commits: {self.stats['total_fixing_commits']}")
        print(f"  Total regressor commits: {self.stats['total_regressor_commits']}")
        print(f"  Total test files copied: {self.stats['total_test_files_copied']}")
        
        print(f"\n  METHODS WITH TESTS:")
        print(f"  Modified+covered: {self.stats['modified_covered_methods_with_tests']}")
        print(f"  Modified+uncovered: {self.stats['modified_uncovered_methods_with_tests']}")
        print(f"  Modified+not_instrumented: {self.stats['modified_not_instrumented_methods_with_tests']}")
        
        if self.bugs_with_complete_data:
            print(f"\n  Bugs with complete data: {self.bugs_with_complete_data}")
        
        print(f"\nOutput: {self.output_dir}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Complete analysis - combine modified methods, coverage, and tests')
    parser.add_argument('--mozilla-central', type=str, default=None,
                        help='Path to mozilla-central repository')
    
    args = parser.parse_args()
    
    analyzer = CompleteAnalyzer(mozilla_central_path=args.mozilla_central)
    analyzer.run()


if __name__ == "__main__":
    main()