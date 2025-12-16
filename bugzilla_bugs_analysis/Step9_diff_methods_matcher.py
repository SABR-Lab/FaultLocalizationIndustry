#!/usr/bin/env python3
"""
Step 9: Match Methods to Diff Hunks
Starting from Step 8 (parsed methods), find corresponding diffs in Step 5
and match methods to changed lines.

INPUT:  outputs/step8_method_extraction/bugs/bug_<ID>.json
        outputs/step5_extracted_diffs/bug_<ID>/
OUTPUT: outputs/step9_method_diff_matching/
        ├── bugs/
        │   ├── bug_<ID>.json
        │   └── ...
        ├── extraction_summary.json
        └── extraction_report.txt
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
outputs_dir = script_dir / "outputs"

# Input paths
STEP5_DIFFS_DIR = outputs_dir / "step5_extracted_diffs"
STEP8_DIR = outputs_dir / "step8_method_extraction"
STEP8_BUGS_DIR = STEP8_DIR / "bugs"

# Output paths
OUTPUT_DIR = outputs_dir / "step9_method_diff_matching"
OUTPUT_BUGS_DIR = OUTPUT_DIR / "bugs"


class DiffHunkParser:
    """Parse unified diff hunks"""
    
    @staticmethod
    def parse_hunk_header(header: str) -> Optional[Dict]:
        """Parse a hunk header like: @@ -846,46 +846,6 @@"""
        match = re.search(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', header)
        if not match:
            return None
        
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) else 1
        
        return {
            'old_start': old_start,
            'old_count': old_count,
            'old_end': old_start + old_count - 1,
            'old_lines': list(range(old_start, old_start + old_count))
        }
    
    @staticmethod
    def parse_diff_file(diff_path: str) -> List[Dict]:
        """Parse a diff file and extract all hunks"""
        hunks = []
        
        try:
            with open(diff_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            current_hunk = None
            
            for line in lines:
                if line.startswith('# '):
                    continue
                
                if line.startswith('@@'):
                    if current_hunk:
                        hunks.append(current_hunk)
                    
                    hunk_info = DiffHunkParser.parse_hunk_header(line)
                    if hunk_info:
                        current_hunk = {
                            'hunk_header': line.strip(),
                            'old_start': hunk_info['old_start'],
                            'old_end': hunk_info['old_end'],
                            'old_count': hunk_info['old_count'],
                            'old_lines': hunk_info['old_lines']
                        }
            
            if current_hunk:
                hunks.append(current_hunk)
        
        except Exception as e:
            print(f"      Error parsing diff: {e}")
            return []
        
        return hunks


class MethodDiffMatcher:
    """Match methods to diff hunks"""
    
    def __init__(self, step5_diffs_dir: str = None, step8_bugs_dir: str = None, 
                 output_dir: str = None, debug: bool = False):
        self.step5_diffs_dir = Path(step5_diffs_dir) if step5_diffs_dir else STEP5_DIFFS_DIR
        self.step8_bugs_dir = Path(step8_bugs_dir) if step8_bugs_dir else STEP8_BUGS_DIR
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.output_bugs_dir = self.output_dir / "bugs"
        self.debug = debug
        
        # Create output directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_bugs_dir.mkdir(parents=True, exist_ok=True)
        
        # Load all bug files from Step 8
        self.step8_bugs = self._load_step8_bugs()
        print(f"Found {len(self.step8_bugs)} bugs in Step 8\n")
    
    def _load_step8_bugs(self) -> Dict:
        """Load all bug JSON files from Step 8"""
        bugs = {}
        
        if not self.step8_bugs_dir.exists():
            print(f"ERROR: Step 8 bugs directory not found: {self.step8_bugs_dir}")
            return bugs
        
        print(f"Loading Step 8 bugs from: {self.step8_bugs_dir}")
        
        for bug_file in self.step8_bugs_dir.glob("bug_*.json"):
            try:
                with open(bug_file, 'r') as f:
                    bug_data = json.load(f)
                bug_id = bug_data.get('bug_id', bug_file.stem.replace('bug_', ''))
                bugs[str(bug_id)] = bug_data
            except Exception as e:
                print(f"  Warning: Could not load {bug_file}: {e}")
        
        return bugs
    
    def find_diff_in_step5(self, bug_id: str, commit_hash: str, filepath: str, 
                           is_fixing: bool, regressor_bug_id: str = None) -> Optional[str]:
        """Find the diff file in Step 5 directory structure"""
        bug_dir = self.step5_diffs_dir / f"bug_{bug_id}"
        
        if not bug_dir.exists():
            return None
        
        # Convert filepath to safe filename
        safe_filename = filepath.replace('/', '_').replace('\\', '_')
        if not safe_filename.endswith('.diff'):
            safe_filename += '.diff'
        
        # For FIXING commits: outputs/step5_extracted_diffs/bug_<ID>/fixing_commit/<short_hash>/
        if is_fixing:
            commit_dir = bug_dir / "fixing_commit" / commit_hash
            if commit_dir.exists():
                diff_path = commit_dir / safe_filename
                if diff_path.exists():
                    return str(diff_path)
            
            # Try searching by partial hash match
            fixing_base = bug_dir / "fixing_commit"
            if fixing_base.exists():
                for dir_name in os.listdir(fixing_base):
                    if commit_hash in dir_name or dir_name in commit_hash:
                        commit_dir = fixing_base / dir_name
                        if commit_dir.is_dir():
                            diff_path = commit_dir / safe_filename
                            if diff_path.exists():
                                return str(diff_path)
        
        # For REGRESSOR commits: outputs/step5_extracted_diffs/bug_<ID>/regressor_commits/regressor_<bug_id>_<short_hash>/
        else:
            regressor_base = bug_dir / "regressor_commits"
            if not regressor_base.exists():
                return None
            
            # Try exact match with regressor_bug_id
            if regressor_bug_id:
                expected_dir = f"regressor_{regressor_bug_id}_{commit_hash}"
                commit_dir = regressor_base / expected_dir
                if commit_dir.exists():
                    diff_path = commit_dir / safe_filename
                    if diff_path.exists():
                        return str(diff_path)
            
            # Fallback: search by hash in directory names
            for dir_name in os.listdir(regressor_base):
                if commit_hash in dir_name:
                    commit_dir = regressor_base / dir_name
                    if commit_dir.is_dir():
                        diff_path = commit_dir / safe_filename
                        if diff_path.exists():
                            return str(diff_path)
        
        return None
    
    def match_methods_to_hunks(self, methods: List[Dict], hunks: List[Dict]) -> Dict:
        """Match methods to diff hunks"""
        fully_modified = []
        partially_modified = []
        unmodified = []
        
        all_changed_lines = set()
        for hunk in hunks:
            all_changed_lines.update(hunk['old_lines'])
        
        for method in methods:
            method_start = method['start_line']
            method_end = method['end_line']
            method_lines = set(range(method_start, method_end + 1))
            
            overlapping_lines = method_lines & all_changed_lines
            
            if not overlapping_lines:
                unmodified.append({
                    'name': method['name'],
                    'type': method['type'],
                    'start_line': method_start,
                    'end_line': method_end,
                    'line_count': method['line_count'],
                    'signature': method.get('signature', '')
                })
            else:
                is_fully = (overlapping_lines == method_lines)
                
                method_info = {
                    'name': method['name'],
                    'type': method['type'],
                    'start_line': method_start,
                    'end_line': method_end,
                    'line_count': method['line_count'],
                    'signature': method.get('signature', ''),
                    'changed_lines': sorted(list(overlapping_lines)),
                    'overlap_count': len(overlapping_lines),
                    'overlap_percentage': round((len(overlapping_lines) / len(method_lines)) * 100, 1)
                }
                
                if is_fully:
                    fully_modified.append(method_info)
                else:
                    partially_modified.append(method_info)
        
        return {
            'fully_modified': fully_modified,
            'partially_modified': partially_modified,
            'unmodified': unmodified
        }
    
    def process_commit_file_pair(self, bug_id: str, filepath: str,
                                 commit_data: Dict, is_fixing: bool) -> Dict:
        """Process a single commit's file from Step 8"""
        commit_hash = commit_data['commit_hash']
        methods = commit_data.get('methods', [])
        regressor_bug_id = commit_data.get('regressor_bug_id') if not is_fixing else None
        
        diff_path = self.find_diff_in_step5(bug_id, commit_hash, filepath, is_fixing, regressor_bug_id)
        
        result = {
            'commit_hash': commit_hash,
            'full_hash': commit_data.get('full_hash', 'unknown'),
            'parent_hash': commit_data.get('parent_hash', 'unknown'),
            'commit_type': "fixing" if is_fixing else "regressor",
            'filepath': filepath,
            'diff_found': diff_path is not None,
            'diff_path': diff_path,
            'methods_count': len(methods),
            'matched_methods': None,
            'hunks_count': 0,
            'hunk_ranges': []
        }
        
        if diff_path:
            hunks = DiffHunkParser.parse_diff_file(diff_path)
            result['hunks_count'] = len(hunks)
            
            for hunk in hunks:
                result['hunk_ranges'].append(f"@@ -{hunk['old_start']},{hunk['old_count']} @@")
            
            result['matched_methods'] = self.match_methods_to_hunks(methods, hunks)
        
        return result
    
    def process_single_bug(self, bug_id: str, bug_data: Dict) -> Dict:
        """Process a single bug and return results"""
        bug_results = {
            'bug_id': bug_id,
            'processing_timestamp': datetime.now().isoformat(),
            'files': [],
            'summary': {
                'total_files': 0,
                'total_commits': 0,
                'diffs_found': 0,
                'methods_fully_modified': 0,
                'methods_partially_modified': 0,
                'methods_unmodified': 0
            }
        }
        
        files_data = bug_data.get('files', [])
        bug_results['summary']['total_files'] = len(files_data)
        
        for file_data in files_data:
            filepath = file_data['filepath']
            
            file_result = {
                'filepath': filepath,
                'fixing_commits': [],
                'regressor_commits': []
            }
            
            # Process fixing commits
            for commit in file_data.get('fixing_commits', []):
                commit_result = self.process_commit_file_pair(bug_id, filepath, commit, is_fixing=True)
                file_result['fixing_commits'].append(commit_result)
                bug_results['summary']['total_commits'] += 1
                
                if commit_result['diff_found']:
                    bug_results['summary']['diffs_found'] += 1
                    if commit_result['matched_methods']:
                        bug_results['summary']['methods_fully_modified'] += len(commit_result['matched_methods']['fully_modified'])
                        bug_results['summary']['methods_partially_modified'] += len(commit_result['matched_methods']['partially_modified'])
                        bug_results['summary']['methods_unmodified'] += len(commit_result['matched_methods']['unmodified'])
            
            # Process regressor commits
            for commit in file_data.get('regressor_commits', []):
                commit_result = self.process_commit_file_pair(bug_id, filepath, commit, is_fixing=False)
                file_result['regressor_commits'].append(commit_result)
                bug_results['summary']['total_commits'] += 1
                
                if commit_result['diff_found']:
                    bug_results['summary']['diffs_found'] += 1
                    if commit_result['matched_methods']:
                        bug_results['summary']['methods_fully_modified'] += len(commit_result['matched_methods']['fully_modified'])
                        bug_results['summary']['methods_partially_modified'] += len(commit_result['matched_methods']['partially_modified'])
                        bug_results['summary']['methods_unmodified'] += len(commit_result['matched_methods']['unmodified'])
            
            bug_results['files'].append(file_result)
        
        return bug_results
    
    def save_bug_result(self, bug_id: str, bug_results: Dict) -> str:
        """Save individual bug results to JSON file"""
        output_file = self.output_bugs_dir / f"bug_{bug_id}.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(bug_results, f, indent=2)
        
        return str(output_file)
    
    def process_all_bugs(self) -> Dict:
        """Process all bugs"""
        print("\n" + "="*80)
        print("STEP 9: MATCH METHODS TO DIFF HUNKS")
        print("="*80 + "\n")
        
        global_summary = {
            'matching_timestamp': datetime.now().isoformat(),
            'step5_source': str(self.step5_diffs_dir),
            'step8_source': str(self.step8_bugs_dir),
            'bugs_processed': 0,
            'bugs_successful': 0,
            'bugs_failed': 0,
            'total_commits_processed': 0,
            'total_diffs_found': 0,
            'total_methods_fully_modified': 0,
            'total_methods_partially_modified': 0,
            'total_methods_unmodified': 0,
            'bug_summaries': {}
        }
        
        for bug_id, bug_data in self.step8_bugs.items():
            print(f"\nProcessing Bug {bug_id}...")
            
            try:
                bug_results = self.process_single_bug(bug_id, bug_data)
                output_file = self.save_bug_result(bug_id, bug_results)
                
                # Update global summary
                global_summary['bugs_processed'] += 1
                global_summary['bugs_successful'] += 1
                global_summary['total_commits_processed'] += bug_results['summary']['total_commits']
                global_summary['total_diffs_found'] += bug_results['summary']['diffs_found']
                global_summary['total_methods_fully_modified'] += bug_results['summary']['methods_fully_modified']
                global_summary['total_methods_partially_modified'] += bug_results['summary']['methods_partially_modified']
                global_summary['total_methods_unmodified'] += bug_results['summary']['methods_unmodified']
                
                global_summary['bug_summaries'][bug_id] = {
                    'output_file': output_file,
                    'files_count': bug_results['summary']['total_files'],
                    'commits_count': bug_results['summary']['total_commits'],
                    'diffs_found': bug_results['summary']['diffs_found'],
                    'methods_modified': bug_results['summary']['methods_fully_modified'] + 
                                       bug_results['summary']['methods_partially_modified']
                }
                
                modified = bug_results['summary']['methods_fully_modified'] + bug_results['summary']['methods_partially_modified']
                print(f"  ✓ Saved: {output_file}")
                print(f"    Files: {bug_results['summary']['total_files']}, "
                      f"Commits: {bug_results['summary']['total_commits']}, "
                      f"Diffs found: {bug_results['summary']['diffs_found']}, "
                      f"Methods modified: {modified}")
                
            except Exception as e:
                print(f"  ✗ Error processing bug {bug_id}: {e}")
                global_summary['bugs_processed'] += 1
                global_summary['bugs_failed'] += 1
        
        return global_summary
    
    def save_summary(self, summary: Dict) -> None:
        """Save extraction summary and report"""
        # Save JSON summary
        summary_file = self.output_dir / "extraction_summary.json"
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2)
        print(f"\nSummary saved to: {summary_file}")
        
        # Save text report
        report_file = self.output_dir / "extraction_report.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("STEP 9: METHOD-DIFF MATCHING REPORT\n")
            f.write("="*80 + "\n\n")
            f.write(f"Timestamp: {summary['matching_timestamp']}\n")
            f.write(f"Step 5 Source: {summary['step5_source']}\n")
            f.write(f"Step 8 Source: {summary['step8_source']}\n\n")
            
            f.write("-"*40 + "\n")
            f.write("OVERALL STATISTICS\n")
            f.write("-"*40 + "\n")
            f.write(f"Bugs processed: {summary['bugs_processed']}\n")
            f.write(f"Bugs successful: {summary['bugs_successful']}\n")
            f.write(f"Bugs failed: {summary['bugs_failed']}\n")
            f.write(f"Total commits processed: {summary['total_commits_processed']}\n")
            f.write(f"Total diffs found: {summary['total_diffs_found']}\n")
            f.write(f"Total methods fully modified: {summary['total_methods_fully_modified']}\n")
            f.write(f"Total methods partially modified: {summary['total_methods_partially_modified']}\n")
            f.write(f"Total methods unmodified: {summary['total_methods_unmodified']}\n\n")
            
            f.write("-"*40 + "\n")
            f.write("PER-BUG SUMMARY\n")
            f.write("-"*40 + "\n")
            for bug_id, bug_sum in summary['bug_summaries'].items():
                f.write(f"\nBug {bug_id}:\n")
                f.write(f"  Files: {bug_sum['files_count']}\n")
                f.write(f"  Commits: {bug_sum['commits_count']}\n")
                f.write(f"  Diffs found: {bug_sum['diffs_found']}\n")
                f.write(f"  Methods modified: {bug_sum['methods_modified']}\n")
        
        print(f"Report saved to: {report_file}")


def main():
    """Main execution"""
    # Verify input directories exist
    if not STEP8_BUGS_DIR.exists():
        print(f"ERROR: Step 8 bugs directory not found: {STEP8_BUGS_DIR}")
        print("Please run Step 8 first.")
        sys.exit(1)
    
    if not STEP5_DIFFS_DIR.exists():
        print(f"ERROR: Step 5 directory not found: {STEP5_DIFFS_DIR}")
        print("Please run Step 5 first.")
        sys.exit(1)
    
    matcher = MethodDiffMatcher()
    summary = matcher.process_all_bugs()
    matcher.save_summary(summary)
    
    print("\n" + "="*80)
    print("PROCESSING SUMMARY")
    print("="*80)
    print(f"Bugs processed: {summary['bugs_processed']}")
    print(f"Bugs successful: {summary['bugs_successful']}")
    print(f"Bugs failed: {summary['bugs_failed']}")
    print(f"Total commits processed: {summary['total_commits_processed']}")
    print(f"Total diffs found: {summary['total_diffs_found']}")
    print(f"Total methods modified: {summary['total_methods_fully_modified'] + summary['total_methods_partially_modified']}")
    
    print("\n" + "="*80)
    print("✓ STEP 9 COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()