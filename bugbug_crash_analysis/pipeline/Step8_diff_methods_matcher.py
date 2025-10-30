#!/usr/bin/env python3
"""
Step 8: Match Methods to Diff Hunks (Enhanced with detailed summary statistics)
Starting from Step 7 (parsed methods), find corresponding diffs in Step 4
and match methods to changed lines.

Flow:
  Step 7 (parsed methods) → Find commit/file → Find diff in Step 4 → Match methods to diff hunks
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path


class DiffHunkParser:
    """Parse unified diff hunks"""
    
    @staticmethod
    def parse_hunk_header(header: str) -> Optional[Dict]:
        """
        Parse a hunk header like: @@ -846,46 +846,6 @@
        Returns the OLD line numbers (what was changed from the parent)
        """
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
        """
        Parse a diff file and extract all hunks
        Returns list of hunks with their OLD line ranges (from parent commit)
        """
        hunks = []
        
        try:
            with open(diff_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            current_hunk = None
            
            for line in lines:
                # Skip metadata header
                if line.startswith('# '):
                    continue
                
                # Detect hunk header
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
    """Match methods to diff hunks using Step 7 as the source of truth"""
    
    def __init__(self, step4_diffs_dir: str, step7_file: str,
                 output_dir: str = "method_diff_matching", debug: bool = False):
        self.step4_diffs_dir = step4_diffs_dir
        self.step7_file = step7_file
        self.output_dir = output_dir
        self.debug = debug
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"Loading Step 7 (parsed methods with specific commits)...")
        with open(step7_file, 'r') as f:
            self.step7_data = json.load(f)
        
        print(f"Found {len(self.step7_data['bugs'])} bugs in Step 7\n")
    
    def find_diff_in_step4(self, bug_id: str, commit_hash: str, filepath: str, 
                           is_fixing: bool, regressor_bug_id: str = None) -> Optional[str]:
        """
        Find the diff file in Step 4 directory structure
        
        Step 4 structure:
          Fixing: extracted_diffs/bug_ID/fixing_commits/commit_hash/file.diff
          Regressor: extracted_diffs/bug_ID/regressor_commits/regressor_BUGID_commit_hash/file.diff
        """
        commit_type = "fixing_commits" if is_fixing else "regressor_commits"
        bug_dir = os.path.join(self.step4_diffs_dir, f"bug_{bug_id}")
        commit_base = os.path.join(bug_dir, commit_type)
        
        if self.debug:
            print(f"        [DEBUG] Looking in: {commit_base}")
        
        if not os.path.exists(commit_base):
            if self.debug:
                print(f"        [DEBUG] Base directory not found")
            return None
        
        # For REGRESSOR commits, construct the expected directory name
        if not is_fixing:
            # Try: regressor_BUGID_commit_hash
            if regressor_bug_id:
                expected_dir = f"regressor_{regressor_bug_id}_{commit_hash}"
                commit_dir = os.path.join(commit_base, expected_dir)
                if self.debug:
                    print(f"        [DEBUG] Trying regressor dir: {expected_dir}")
                if os.path.exists(commit_dir):
                    if self.debug:
                        print(f"        [DEBUG] Found regressor dir!")
                    safe_filename = filepath.replace('/', '_').replace('\\', '_')
                    if not safe_filename.endswith('.diff'):
                        safe_filename += '.diff'
                    diff_path = os.path.join(commit_dir, safe_filename)
                    if self.debug:
                        print(f"        [DEBUG] Looking for file: {safe_filename}")
                    if os.path.exists(diff_path):
                        if self.debug:
                            print(f"        [DEBUG] ✓ FOUND!")
                        return diff_path
        
        # Try exact hash match for FIXING commits
        if is_fixing:
            commit_dir = os.path.join(commit_base, commit_hash)
            if self.debug:
                print(f"        [DEBUG] Trying fixing dir: {commit_hash}")
            if os.path.exists(commit_dir):
                if self.debug:
                    print(f"        [DEBUG] Found fixing dir!")
                safe_filename = filepath.replace('/', '_').replace('\\', '_')
                if not safe_filename.endswith('.diff'):
                    safe_filename += '.diff'
                diff_path = os.path.join(commit_dir, safe_filename)
                if self.debug:
                    print(f"        [DEBUG] Looking for file: {safe_filename}")
                if os.path.exists(diff_path):
                    if self.debug:
                        print(f"        [DEBUG] ✓ FOUND!")
                    return diff_path
        
        # Fallback: search by commit hash in directory names
        if self.debug:
            print(f"        [DEBUG] Fallback search for hash in directory names")
        for dir_name in os.listdir(commit_base):
            if commit_hash in dir_name:
                commit_dir = os.path.join(commit_base, dir_name)
                if os.path.isdir(commit_dir):
                    if self.debug:
                        print(f"        [DEBUG] Found dir by hash: {dir_name}")
                    safe_filename = filepath.replace('/', '_').replace('\\', '_')
                    if not safe_filename.endswith('.diff'):
                        safe_filename += '.diff'
                    diff_path = os.path.join(commit_dir, safe_filename)
                    if os.path.exists(diff_path):
                        if self.debug:
                            print(f"        [DEBUG] ✓ FOUND!")
                        return diff_path
        
        if self.debug:
            print(f"        [DEBUG] ✗ NOT FOUND")
        return None
    
    def match_methods_to_hunks(self, methods: List[Dict], hunks: List[Dict]) -> Dict:
        """
        Match methods to diff hunks
        
        For each method, check if its line range overlaps with changed lines
        """
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
                # Method not touched
                unmodified.append({
                    'name': method['name'],
                    'type': method['type'],
                    'start_line': method_start,
                    'end_line': method_end,
                    'line_count': method['line_count'],
                    'signature': method.get('signature', '')
                })
            else:
                # Method was modified
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
        """
        Process a single commit's file from Step 7
        Find its diff in Step 4 and match methods
        """
        commit_hash = commit_data['commit_hash']
        commit_type = "fixing" if is_fixing else "regressor"
        methods = commit_data.get('methods', [])
        regressor_bug_id = commit_data.get('regressor_bug_id') if not is_fixing else None
        
        # Find diff in Step 4
        diff_path = self.find_diff_in_step4(bug_id, commit_hash, filepath, is_fixing, regressor_bug_id)
        
        result = {
            'commit_hash': commit_hash,
            'full_hash': commit_data.get('full_hash', 'unknown'),
            'parent_hash': commit_data.get('parent_hash', 'unknown'),
            'commit_type': commit_type,
            'filepath': filepath,
            'diff_found': diff_path is not None,
            'diff_path': diff_path,
            'methods_count': len(methods),
            'matched_methods': None,
            'hunks_count': 0,
            'hunk_ranges': []  # Store hunk line ranges
        }
        
        if diff_path:
            hunks = DiffHunkParser.parse_diff_file(diff_path)
            result['hunks_count'] = len(hunks)
            
            # Store hunk line ranges in readable format
            for i, hunk in enumerate(hunks, 1):
                hunk_range = f"@@ -{hunk['old_start']},{hunk['old_count']} @@"
                result['hunk_ranges'].append(hunk_range)
            
            result['matched_methods'] = self.match_methods_to_hunks(methods, hunks)
        
        return result
    
    def process_all_bugs(self) -> Dict:
        """
        Main processing: iterate through Step 7 data
        For each parsed commit, find diffs in Step 4 and match methods
        """
        print("\n" + "="*80)
        print("STEP 8: MATCH METHODS TO DIFF HUNKS")
        print("Using Step 7 parsed commits as source of truth")
        print("="*80 + "\n")
        
        all_results = {
            'matching_timestamp': datetime.now().isoformat(),
            'step4_source': self.step4_diffs_dir,
            'step7_source': self.step7_file,
            'bugs': {}
        }
        
        total_files_processed = 0
        total_commits_processed = 0
        total_diffs_found = 0
        total_methods_modified = 0
        bugs_with_matches = 0
        bugs_with_fully_modified = 0
        bugs_with_partially_modified = 0
        total_fully_modified_methods = 0
        total_partially_modified_methods = 0
        
        for bug_id, bug_data in self.step7_data['bugs'].items():
            print(f"\nBug {bug_id}:")
            bug_results = {
                'bug_id': bug_id,
                'files_processed': 0,
                'commits_processed': 0,
                'diffs_found': 0,
                'files': []
            }
            
            # Tracking for this specific bug
            bug_has_matches = False
            bug_has_fully_modified = False
            bug_has_partially_modified = False
            
            # Process each file
            for file_data in bug_data['files']:
                filepath = file_data['filepath']
                print(f"  File: {filepath}")
                
                file_result = {
                    'filepath': filepath,
                    'fixing_commits': [],
                    'regressor_commits': []
                }
                
                # Process FIXING commits for this file
                print(f"    Fixing commits: {len(file_data['fixing_commits'])}")
                for commit in file_data['fixing_commits']:
                    commit_result = self.process_commit_file_pair(
                        bug_id, filepath, commit, is_fixing=True
                    )
                    file_result['fixing_commits'].append(commit_result)
                    
                    total_commits_processed += 1
                    if commit_result['diff_found']:
                        total_diffs_found += 1
                        bug_results['diffs_found'] += 1  # Count per bug
                        if commit_result['matched_methods']:
                            modified = (
                                len(commit_result['matched_methods']['fully_modified']) +
                                len(commit_result['matched_methods']['partially_modified'])
                            )
                            total_methods_modified += modified
                            
                            # Track if this bug has matches
                            if modified > 0:
                                bug_has_matches = True
                            
                            # Track fully and partially modified
                            fully_count = len(commit_result['matched_methods']['fully_modified'])
                            partially_count = len(commit_result['matched_methods']['partially_modified'])
                            
                            if fully_count > 0:
                                bug_has_fully_modified = True
                                total_fully_modified_methods += fully_count
                            if partially_count > 0:
                                bug_has_partially_modified = True
                                total_partially_modified_methods += partially_count
                            
                            print(f"      ✓ {commit['commit_hash'][:8]}: {modified} methods modified")
                        else:
                            print(f"      ✓ {commit['commit_hash'][:8]}: diff found but no methods matched")
                    else:
                        print(f"      ✗ {commit['commit_hash'][:8]}: diff not found in Step 4")
                
                # Process REGRESSOR commits for this file
                print(f"    Regressor commits: {len(file_data['regressor_commits'])}")
                for commit in file_data['regressor_commits']:
                    commit_result = self.process_commit_file_pair(
                        bug_id, filepath, commit, is_fixing=False
                    )
                    file_result['regressor_commits'].append(commit_result)
                    
                    total_commits_processed += 1
                    if commit_result['diff_found']:
                        total_diffs_found += 1
                        bug_results['diffs_found'] += 1  # Count per bug
                        if commit_result['matched_methods']:
                            modified = (
                                len(commit_result['matched_methods']['fully_modified']) +
                                len(commit_result['matched_methods']['partially_modified'])
                            )
                            total_methods_modified += modified
                            
                            # Track if this bug has matches
                            if modified > 0:
                                bug_has_matches = True
                            
                            # Track fully and partially modified
                            fully_count = len(commit_result['matched_methods']['fully_modified'])
                            partially_count = len(commit_result['matched_methods']['partially_modified'])
                            
                            if fully_count > 0:
                                bug_has_fully_modified = True
                                total_fully_modified_methods += fully_count
                            if partially_count > 0:
                                bug_has_partially_modified = True
                                total_partially_modified_methods += partially_count
                            
                            print(f"      ✓ {commit['commit_hash'][:8]} (Bug {commit.get('regressor_bug_id')}): {modified} methods modified")
                        else:
                            print(f"      ✓ {commit['commit_hash'][:8]}: diff found but no methods matched")
                    else:
                        print(f"      ✗ {commit['commit_hash'][:8]}: diff not found in Step 4")
                
                if file_result['fixing_commits'] or file_result['regressor_commits']:
                    bug_results['files'].append(file_result)
                    bug_results['files_processed'] += 1
                    total_files_processed += 1
            
            bug_results['commits_processed'] = (
                len(bug_results['files']) and
                sum(len(f['fixing_commits']) + len(f['regressor_commits']) 
                    for f in bug_results['files'])
            )
            
            all_results['bugs'][bug_id] = bug_results
            
            # Count bugs with matches
            if bug_has_matches:
                bugs_with_matches += 1
            if bug_has_fully_modified:
                bugs_with_fully_modified += 1
            if bug_has_partially_modified:
                bugs_with_partially_modified += 1
        
        all_results['summary'] = {
            'bugs_processed': len(all_results['bugs']),
            'bugs_with_method_diff_matches': bugs_with_matches,
            'bugs_with_fully_modified_methods': bugs_with_fully_modified,
            'bugs_with_partially_modified_methods': bugs_with_partially_modified,
            'files_processed': total_files_processed,
            'commits_processed': total_commits_processed,
            'diffs_found': total_diffs_found,
            'methods_modified': total_methods_modified,
            'fully_modified_methods': total_fully_modified_methods,
            'partially_modified_methods': total_partially_modified_methods
        }
        
        print(f"\n{'='*80}")
        print("PROCESSING SUMMARY")
        print(f"{'='*80}")
        print(f"Bugs processed: {all_results['summary']['bugs_processed']}")
        print(f"Bugs with method-diff matches: {bugs_with_matches} ({self._percentage(bugs_with_matches, len(all_results['bugs']))}%)")
        print(f"  ├─ With fully modified methods: {bugs_with_fully_modified}")
        print(f"  └─ With partially modified methods: {bugs_with_partially_modified}")
        print(f"\nFiles processed: {total_files_processed}")
        print(f"Commits from Step 7: {total_commits_processed}")
        print(f"Diffs found in Step 4: {total_diffs_found} / {total_commits_processed} ({self._percentage(total_diffs_found, total_commits_processed)}%)")
        print(f"\nMethods matched to changes: {total_methods_modified}")
        print(f"  ├─ Fully modified: {total_fully_modified_methods}")
        print(f"  └─ Partially modified: {total_partially_modified_methods}")
        
        return all_results
    
    @staticmethod
    def _percentage(count: int, total: int) -> str:
        """Calculate percentage safely"""
        if total == 0:
            return "0"
        return f"{(count / total) * 100:.1f}"
    
    def save_results(self, results: Dict) -> str:
        """Save results to JSON"""
        output_file = os.path.join(self.output_dir, 'Step8_method_diff_matching.json')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nJSON Results: {output_file}")
        return output_file
    
    def create_summary_report(self, results: Dict) -> str:
        """Create human-readable summary report"""
        output_file = os.path.join(self.output_dir, 'Step8_summary_report.txt')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("STEP 8: METHOD-TO-DIFF MATCHING SUMMARY\n")
            f.write("="*80 + "\n\n")
            f.write(f"Timestamp: {results['matching_timestamp']}\n")
            f.write(f"Step 4 Source: {results['step4_source']}\n")
            f.write(f"Step 7 Source: {results['step7_source']}\n\n")
            
            f.write("="*80 + "\n")
            f.write("SUMMARY STATISTICS\n")
            f.write("="*80 + "\n\n")
            
            f.write(f"Total Bugs Processed: {results['summary']['bugs_processed']}\n")
            f.write(f"Bugs with Method-Diff Matches: {results['summary']['bugs_with_method_diff_matches']} / {results['summary']['bugs_processed']}\n")
            f.write(f"  ├─ Bugs with Fully Modified Methods: {results['summary']['bugs_with_fully_modified_methods']}\n")
            f.write(f"  └─ Bugs with Partially Modified Methods: {results['summary']['bugs_with_partially_modified_methods']}\n\n")
            
            f.write(f"Files Processed: {results['summary']['files_processed']}\n")
            f.write(f"Commits from Step 7: {results['summary']['commits_processed']}\n")
            f.write(f"Diffs Found in Step 4: {results['summary']['diffs_found']} / {results['summary']['commits_processed']}\n\n")
            
            f.write(f"Total Methods Matched to Changes: {results['summary']['methods_modified']}\n")
            f.write(f"  ├─ Fully Modified: {results['summary']['fully_modified_methods']}\n")
            f.write(f"  └─ Partially Modified: {results['summary']['partially_modified_methods']}\n\n")
            
            f.write("="*80 + "\n")
            f.write("DETAILED RESULTS BY BUG\n")
            f.write("="*80 + "\n\n")
            
            for bug_id, bug_data in results['bugs'].items():
                f.write(f"BUG {bug_id}\n")
                f.write(f"  Files processed: {bug_data['files_processed']}\n\n")
                
                for file_data in bug_data['files']:
                    f.write(f"  FILE: {file_data['filepath']}\n\n")
                    
                    # FIXING COMMITS
                    if file_data['fixing_commits']:
                        f.write(f"    FIXING COMMITS:\n")
                        for commit in file_data['fixing_commits']:
                            f.write(f"      Commit: {commit['commit_hash']}\n")
                            f.write(f"        Diff found: {commit['diff_found']}\n")
                            f.write(f"        Hunks: {commit['hunks_count']}\n")
                            
                            if commit['matched_methods']:
                                # Show hunk line ranges
                                f.write(f"        Changed lines:\n")
                                for i, hunk_lines in enumerate(commit.get('hunk_ranges', []), 1):
                                    f.write(f"          Hunk {i}: {hunk_lines}\n")
                            
                            f.write(f"        Methods parsed: {commit['methods_count']}\n")
                            
                            if commit['matched_methods']:
                                match = commit['matched_methods']
                                f.write(f"        FULLY MODIFIED: {len(match['fully_modified'])}\n")
                                for method in match['fully_modified']:
                                    f.write(f"          ✓ {method['name']} (lines {method['start_line']}-{method['end_line']})\n")
                                
                                f.write(f"        PARTIALLY MODIFIED: {len(match['partially_modified'])}\n")
                                for method in match['partially_modified']:
                                    f.write(f"          ~ {method['name']} (lines {method['start_line']}-{method['end_line']}) - {method['overlap_percentage']}% changed\n")
                                
                                f.write(f"        UNMODIFIED: {len(match['unmodified'])}\n")
                            f.write("\n")
                    
                    # REGRESSOR COMMITS
                    if file_data['regressor_commits']:
                        f.write(f"    REGRESSOR COMMITS:\n")
                        for commit in file_data['regressor_commits']:
                            f.write(f"      Commit: {commit['commit_hash']} (Bug {commit.get('regressor_bug_id')})\n")
                            f.write(f"        Diff found: {commit['diff_found']}\n")
                            f.write(f"        Hunks: {commit['hunks_count']}\n")
                            
                            if commit['matched_methods']:
                                # Show hunk line ranges
                                f.write(f"        Changed lines:\n")
                                for i, hunk_lines in enumerate(commit.get('hunk_ranges', []), 1):
                                    f.write(f"          Hunk {i}: {hunk_lines}\n")
                            
                            f.write(f"        Methods parsed: {commit['methods_count']}\n")
                            
                            if commit['matched_methods']:
                                match = commit['matched_methods']
                                f.write(f"        FULLY MODIFIED: {len(match['fully_modified'])}\n")
                                for method in match['fully_modified']:
                                    f.write(f"          ✓ {method['name']} (lines {method['start_line']}-{method['end_line']})\n")
                                
                                f.write(f"        PARTIALLY MODIFIED: {len(match['partially_modified'])}\n")
                                for method in match['partially_modified']:
                                    f.write(f"          ~ {method['name']} (lines {method['start_line']}-{method['end_line']}) - {method['overlap_percentage']}% changed\n")
                                
                                f.write(f"        UNMODIFIED: {len(match['unmodified'])}\n")
                            f.write("\n")
        
        print(f"Text Report: {output_file}")
        return output_file


def main():
    """Main execution"""
    
    step4_diffs_dir = "step4_extracted_diffs"
    step7_file = "step7_method_extraction/Step7_method_extraction.json"
    
    # Validate inputs
    if not os.path.exists(step7_file):
        print(f"ERROR: Step 7 file not found: {step7_file}")
        return
    
    if not os.path.exists(step4_diffs_dir):
        print(f"ERROR: Step 4 directory not found: {step4_diffs_dir}")
        return
    
    # Enable debug mode to see what's happening
    matcher = MethodDiffMatcher(
        step4_diffs_dir=step4_diffs_dir,
        step7_file=step7_file,
        output_dir="step8_method_diff_matching",
        debug=False  # Set to False once working
    )
    
    results = matcher.process_all_bugs()
    
    json_file = matcher.save_results(results)
    report_file = matcher.create_summary_report(results)
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()