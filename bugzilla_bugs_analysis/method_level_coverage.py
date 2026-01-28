#!/usr/bin/env python3
"""
================================================================================
METHOD LEVEL COVERAGE ANALYSIS
================================================================================

PURPOSE:
--------
Combine Step 8 method extraction data with line-level coverage data to produce
method-level coverage analysis.

INPUT:
------
- Step 8 output: outputs/step8_method_extraction/bugs/bug_<ID>.json
- Coverage output: outputs/coverage_reports/bugs/bug_<ID>/

OUTPUT:
-------
outputs/method_level_coverage/
├── bugs/
│   └── bug_<ID>/
│       ├── fixing_commits/
│       │   └── <commit_hash>/
│       │       └── <filename>_method_coverage.json
│       └── regressor_commits/
│           └── <commit_hash>/
│               └── <filename>_method_coverage.json
├── summary.json
└── report.txt
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Set
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)


class MethodLevelCoverageAnalyzer:
    """Analyze method-level coverage by combining Step 8 and coverage data"""
    
    def __init__(self):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Step 8 method extraction
        self.step8_dir = self.outputs_base / "step8_method_extraction" / "bugs"
        
        # INPUT: Coverage reports
        self.coverage_dir = self.outputs_base / "line_level_overage" / "bugs"
        
        # OUTPUT: Method level coverage
        self.output_dir = self.outputs_base / "method_level_coverage"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Statistics
        self.stats = {
            'total_bugs_in_step8': 0,
            'bugs_with_coverage': 0,
            'bugs_without_coverage': 0,
            'bugs_processed': 0,
            'total_files_processed': 0,
            'total_methods_analyzed': 0,
            'methods_covered': 0,
            'methods_uncovered': 0,
            'methods_not_instrumented': 0
        }
        
        self.processed_bugs = []
        self.skipped_bugs = []
        
        print(f"Step 8 input: {self.step8_dir}")
        print(f"Coverage input: {self.coverage_dir}")
        print(f"Output directory: {self.output_dir}")
    
    def normalize_filename(self, filepath: str) -> str:
        """Normalize filepath for comparison (replace / and \\ with _)"""
        return filepath.replace('/', '_').replace('\\', '_')
    
    def find_coverage_file(self, bug_id: str, commit_type: str, 
                           commit_hash: str, filepath: str) -> Optional[Path]:
        """Find the coverage JSON file for a specific file"""
        safe_filename = self.normalize_filename(filepath)
        
        # Path: coverage_reports/bugs/bug_<ID>/<commit_type>/<commit_hash>/<filename>_coverage.json
        coverage_path = (self.coverage_dir / f"bug_{bug_id}" / commit_type / 
                        commit_hash / f"{safe_filename}_coverage.json")
        
        if coverage_path.exists():
            return coverage_path
        
        # Try shorter hash match
        bug_coverage_dir = self.coverage_dir / f"bug_{bug_id}" / commit_type
        if bug_coverage_dir.exists():
            for hash_dir in bug_coverage_dir.iterdir():
                if hash_dir.is_dir():
                    # Check if hashes match (partial match)
                    if commit_hash in hash_dir.name or hash_dir.name in commit_hash:
                        potential_file = hash_dir / f"{safe_filename}_coverage.json"
                        if potential_file.exists():
                            return potential_file
        
        return None
    
    def load_coverage_data(self, coverage_file: Path) -> Optional[Dict]:
        """Load coverage data from JSON file"""
        try:
            with open(coverage_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"      Warning: Failed to load {coverage_file}: {e}")
            return None
    
    def build_line_coverage_map(self, coverage_data: Dict) -> Dict[int, str]:
        """Build a map of line number -> coverage status"""
        line_map = {}
        
        for line_info in coverage_data.get('lines', []):
            line_num = line_info.get('line')
            status = line_info.get('status', 'not_instrumented')
            if line_num:
                line_map[line_num] = status
        
        return line_map
    
    def analyze_method_coverage(self, method: Dict, line_coverage_map: Dict[int, str]) -> Dict:
        """Analyze coverage for a single method"""
        start_line = method['start_line']
        end_line = method['end_line']
        
        covered_lines = []
        uncovered_lines = []
        not_instrumented_lines = []
        
        for line_num in range(start_line, end_line + 1):
            status = line_coverage_map.get(line_num, 'not_instrumented')
            
            if status == 'covered':
                covered_lines.append(line_num)
            elif status == 'uncovered':
                uncovered_lines.append(line_num)
            else:
                not_instrumented_lines.append(line_num)
        
        # Determine overall method status
        if len(covered_lines) > 0:
            method_status = 'covered'
        elif len(uncovered_lines) > 0:
            method_status = 'uncovered'
        else:
            method_status = 'not_instrumented'
        
        total_lines = end_line - start_line + 1
        coverage_percentage = 0
        if len(covered_lines) + len(uncovered_lines) > 0:
            coverage_percentage = round(
                len(covered_lines) / (len(covered_lines) + len(uncovered_lines)) * 100, 2
            )
        
        return {
            'name': method['name'],
            'type': method.get('type', 'function'),
            'start_line': start_line,
            'end_line': end_line,
            'signature': method.get('signature', ''),
            'status': method_status,
            'coverage_summary': {
                'covered': len(covered_lines),
                'uncovered': len(uncovered_lines),
                'not_instrumented': len(not_instrumented_lines),
                'total': total_lines,
                'percentage': coverage_percentage
            },
            'covered_lines': covered_lines,
            'uncovered_lines': uncovered_lines,
            'not_instrumented_lines': not_instrumented_lines
        }
    
    def process_file(self, bug_id: str, filepath: str, commit_hash: str,
                     commit_type: str, methods: List[Dict]) -> Optional[Dict]:
        """Process a single file - combine methods with coverage data"""
        
        # Find coverage file
        coverage_file = self.find_coverage_file(bug_id, commit_type, commit_hash, filepath)
        
        if not coverage_file:
            return None
        
        # Load coverage data
        coverage_data = self.load_coverage_data(coverage_file)
        if not coverage_data:
            return None
        
        # Build line coverage map
        line_coverage_map = self.build_line_coverage_map(coverage_data)
        
        if not line_coverage_map:
            return None
        
        # Analyze each method
        method_coverages = []
        for method in methods:
            method_coverage = self.analyze_method_coverage(method, line_coverage_map)
            method_coverages.append(method_coverage)
            
            # Update stats
            self.stats['total_methods_analyzed'] += 1
            if method_coverage['status'] == 'covered':
                self.stats['methods_covered'] += 1
            elif method_coverage['status'] == 'uncovered':
                self.stats['methods_uncovered'] += 1
            else:
                self.stats['methods_not_instrumented'] += 1
        
        # Calculate file-level summary
        total_covered = sum(m['coverage_summary']['covered'] for m in method_coverages)
        total_uncovered = sum(m['coverage_summary']['uncovered'] for m in method_coverages)
        total_not_instrumented = sum(m['coverage_summary']['not_instrumented'] for m in method_coverages)
        
        file_percentage = 0
        if total_covered + total_uncovered > 0:
            file_percentage = round(total_covered / (total_covered + total_uncovered) * 100, 2)
        
        return {
            'filepath': filepath,
            'commit_hash': commit_hash,
            'commit_type': commit_type,
            'total_methods': len(method_coverages),
            'file_coverage_summary': {
                'covered_lines': total_covered,
                'uncovered_lines': total_uncovered,
                'not_instrumented_lines': total_not_instrumented,
                'percentage': file_percentage
            },
            'methods': method_coverages
        }
    
    def process_commit(self, bug_id: str, commit_data: Dict, 
                       commit_type: str, filepath: str) -> Optional[Dict]:
        """Process a single commit for a file"""
        commit_hash = commit_data.get('commit_hash', '')
        methods = commit_data.get('methods', [])
        
        if not methods:
            return None
        
        return self.process_file(bug_id, filepath, commit_hash, commit_type, methods)
    
    def bug_has_coverage(self, bug_id: str) -> bool:
        """Check if a bug has any coverage data"""
        bug_coverage_dir = self.coverage_dir / f"bug_{bug_id}"
        return bug_coverage_dir.exists()
    
    def process_bug(self, bug_file: Path) -> Optional[Dict]:
        """Process a single bug"""
        try:
            with open(bug_file, 'r', encoding='utf-8') as f:
                bug_data = json.load(f)
        except Exception as e:
            print(f"    Error reading {bug_file}: {e}")
            return None
        
        bug_id = bug_data.get('bug_id', bug_file.stem.replace('bug_', ''))
        
        # Check if bug has coverage data
        if not self.bug_has_coverage(bug_id):
            self.stats['bugs_without_coverage'] += 1
            self.skipped_bugs.append(bug_id)
            print(f"    ✗ Skipping - no coverage data")
            return None
        
        self.stats['bugs_with_coverage'] += 1
        
        bug_result = {
            'bug_id': bug_id,
            'analysis_timestamp': datetime.now().isoformat(),
            'fixing_commits': [],
            'regressor_commits': []
        }
        
        files_processed = 0
        
        # Process each file in the bug
        for file_data in bug_data.get('files', []):
            filepath = file_data.get('filepath', '')
            
            # Process fixing commits
            for commit_data in file_data.get('fixing_commits', []):
                result = self.process_commit(bug_id, commit_data, 'fixing_commits', filepath)
                
                if result:
                    # Save individual file result
                    commit_hash = commit_data.get('commit_hash', '')
                    self.save_file_result(bug_id, 'fixing_commits', commit_hash, filepath, result)
                    bug_result['fixing_commits'].append(result)
                    files_processed += 1
                    self.stats['total_files_processed'] += 1
            
            # Process regressor commits
            for commit_data in file_data.get('regressor_commits', []):
                result = self.process_commit(bug_id, commit_data, 'regressor_commits', filepath)
                
                if result:
                    # Save individual file result
                    commit_hash = commit_data.get('commit_hash', '')
                    self.save_file_result(bug_id, 'regressor_commits', commit_hash, filepath, result)
                    bug_result['regressor_commits'].append(result)
                    files_processed += 1
                    self.stats['total_files_processed'] += 1
        
        if files_processed > 0:
            self.stats['bugs_processed'] += 1
            self.processed_bugs.append(bug_id)
            print(f"    ✓ Processed {files_processed} files")
            return bug_result
        else:
            print(f"    - No files with coverage data")
            return None
    
    def save_file_result(self, bug_id: str, commit_type: str, 
                         commit_hash: str, filepath: str, result: Dict):
        """Save individual file result to JSON"""
        safe_filename = self.normalize_filename(filepath)
        
        output_path = (self.bugs_output_dir / f"bug_{bug_id}" / commit_type / 
                      commit_hash / f"{safe_filename}_method_coverage.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
    
    def run(self):
        """Main execution"""
        print("\n" + "=" * 70)
        print("METHOD LEVEL COVERAGE ANALYSIS")
        print("=" * 70)
        
        # Verify input directories exist
        if not self.step8_dir.exists():
            print(f"ERROR: Step 8 directory not found: {self.step8_dir}")
            print("Please run Step 8 first.")
            return
        
        if not self.coverage_dir.exists():
            print(f"ERROR: Coverage directory not found: {self.coverage_dir}")
            print("Please run coverage extraction first.")
            return
        
        # Find all bug files from Step 8
        bug_files = sorted(self.step8_dir.glob('bug_*.json'))
        
        if not bug_files:
            print("No bug files found in Step 8 output!")
            return
        
        self.stats['total_bugs_in_step8'] = len(bug_files)
        print(f"\nFound {len(bug_files)} bugs in Step 8 output\n")
        
        # Process each bug
        all_results = []
        for i, bug_file in enumerate(bug_files, 1):
            bug_id = bug_file.stem.replace('bug_', '')
            print(f"[{i}/{len(bug_files)}] Bug {bug_id}...")
            
            result = self.process_bug(bug_file)
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
            'input_step8': str(self.step8_dir),
            'input_coverage': str(self.coverage_dir),
            'output_directory': str(self.output_dir),
            'stats': self.stats,
            'processed_bugs': self.processed_bugs,
            'skipped_bugs': self.skipped_bugs
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
            f.write("METHOD LEVEL COVERAGE ANALYSIS REPORT\n")
            f.write("=" * 70 + "\n\n")
            
            f.write(f"Analysis Time: {datetime.now().isoformat()}\n")
            f.write(f"Step 8 Input: {self.step8_dir}\n")
            f.write(f"Coverage Input: {self.coverage_dir}\n")
            f.write(f"Output: {self.output_dir}\n\n")
            
            f.write("STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"Total bugs in Step 8: {self.stats['total_bugs_in_step8']}\n")
            f.write(f"Bugs with coverage data: {self.stats['bugs_with_coverage']}\n")
            f.write(f"Bugs without coverage (skipped): {self.stats['bugs_without_coverage']}\n")
            f.write(f"Bugs processed: {self.stats['bugs_processed']}\n\n")
            
            f.write(f"Total files processed: {self.stats['total_files_processed']}\n")
            f.write(f"Total methods analyzed: {self.stats['total_methods_analyzed']}\n")
            f.write(f"Methods covered: {self.stats['methods_covered']}\n")
            f.write(f"Methods uncovered: {self.stats['methods_uncovered']}\n")
            f.write(f"Methods not instrumented: {self.stats['methods_not_instrumented']}\n\n")
            
            if self.processed_bugs:
                f.write("PROCESSED BUGS\n")
                f.write("-" * 40 + "\n")
                for bug_id in self.processed_bugs:
                    f.write(f"  Bug {bug_id}\n")
                f.write("\n")
            
            if self.skipped_bugs:
                f.write("SKIPPED BUGS (no coverage data)\n")
                f.write("-" * 40 + "\n")
                for bug_id in self.skipped_bugs:
                    f.write(f"  Bug {bug_id}\n")
    
    def print_summary(self):
        """Print final summary"""
        print(f"\n{'=' * 70}")
        print("ANALYSIS COMPLETE")
        print(f"{'=' * 70}")
        print(f"  Total bugs in Step 8: {self.stats['total_bugs_in_step8']}")
        print(f"  Bugs with coverage: {self.stats['bugs_with_coverage']}")
        print(f"  Bugs without coverage (skipped): {self.stats['bugs_without_coverage']}")
        print(f"  Bugs processed: {self.stats['bugs_processed']}")
        print(f"\n  Files processed: {self.stats['total_files_processed']}")
        print(f"  Methods analyzed: {self.stats['total_methods_analyzed']}")
        print(f"    - Covered: {self.stats['methods_covered']}")
        print(f"    - Uncovered: {self.stats['methods_uncovered']}")
        print(f"    - Not instrumented: {self.stats['methods_not_instrumented']}")
        print(f"\n  Processed bugs: {self.processed_bugs}")
        print(f"  Skipped bugs: {self.skipped_bugs}")
        print(f"\nOutput: {self.output_dir}")


def main():
    """Main execution"""
    analyzer = MethodLevelCoverageAnalyzer()
    analyzer.run()


if __name__ == "__main__":
    main()