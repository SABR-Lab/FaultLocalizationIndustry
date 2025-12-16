#!/usr/bin/env python3
"""
Step 10: Match Fixing Commits to Regressor Commits
For each bug/file, compare methods changed in fixing commits with
methods changed in regressor commits to identify which fix addresses which regression.

INPUT:  outputs/step9_method_diff_matching/bugs/bug_<ID>.json
OUTPUT: outputs/step10_fixing_regressor_matching/
        ├── bugs_with_matched_fixing_regressor_commit/
        │   ├── bug_<ID>.json
        │   └── ...
        ├── bugs_without_matched_fixing_regressor_commit/
        │   ├── bug_<ID>.json
        │   └── ...
        ├── extraction_summary.json
        └── extraction_report.txt
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Set
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
outputs_dir = script_dir / "outputs"

# Input paths
STEP9_DIR = outputs_dir / "step9_method_diff_matching"
STEP9_BUGS_DIR = STEP9_DIR / "bugs"

# Output paths
OUTPUT_DIR = outputs_dir / "step10_fixing_regressor_matching"
OUTPUT_BUGS_WITH_MATCH_DIR = OUTPUT_DIR / "bugs_with_matched_fixing_regressor_commit"
OUTPUT_BUGS_WITHOUT_MATCH_DIR = OUTPUT_DIR / "bugs_without_matched_fixing_regressor_commit"


class FixingRegressorMatcher:
    """Match fixing commits to regressor commits by comparing changed methods"""
    
    def __init__(self, step9_bugs_dir: str = None, output_dir: str = None):
        self.step9_bugs_dir = Path(step9_bugs_dir) if step9_bugs_dir else STEP9_BUGS_DIR
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.output_bugs_with_match_dir = self.output_dir / "bugs_with_matched_fixing_regressor_commit"
        self.output_bugs_without_match_dir = self.output_dir / "bugs_without_matched_fixing_regressor_commit"
        
        # Create output directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_bugs_with_match_dir.mkdir(parents=True, exist_ok=True)
        self.output_bugs_without_match_dir.mkdir(parents=True, exist_ok=True)
        
        # Load all bug files from Step 9
        self.step9_bugs = self._load_step9_bugs()
        print(f"Found {len(self.step9_bugs)} bugs in Step 9\n")
    
    def _load_step9_bugs(self) -> Dict:
        """Load all bug JSON files from Step 9"""
        bugs = {}
        
        if not self.step9_bugs_dir.exists():
            print(f"ERROR: Step 9 bugs directory not found: {self.step9_bugs_dir}")
            return bugs
        
        print(f"Loading Step 9 bugs from: {self.step9_bugs_dir}")
        
        for bug_file in self.step9_bugs_dir.glob("bug_*.json"):
            try:
                with open(bug_file, 'r') as f:
                    bug_data = json.load(f)
                bug_id = bug_data.get('bug_id', bug_file.stem.replace('bug_', ''))
                bugs[str(bug_id)] = bug_data
            except Exception as e:
                print(f"  Warning: Could not load {bug_file}: {e}")
        
        return bugs
    
    def get_changed_method_names(self, matched_methods: Dict) -> Set[str]:
        """Extract all method names that were changed (fully or partially)"""
        changed = set()
        
        if matched_methods:
            for method in matched_methods.get('fully_modified', []):
                changed.add(method['name'])
            for method in matched_methods.get('partially_modified', []):
                changed.add(method['name'])
        
        return changed
    
    def find_method_overlap(self, fixing_methods: Dict, regressor_methods: Dict) -> Dict:
        """Find methods that changed in both fixing and regressor commits"""
        fixing_changed = self.get_changed_method_names(fixing_methods)
        regressor_changed = self.get_changed_method_names(regressor_methods)
        
        overlapping = fixing_changed & regressor_changed
        
        # Build details for overlapping methods
        fixing_details = {}
        for method in fixing_methods.get('fully_modified', []):
            fixing_details[method['name']] = {
                'type': 'FULLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'signature': method['signature'][:80] if method.get('signature') else ''
            }
        for method in fixing_methods.get('partially_modified', []):
            fixing_details[method['name']] = {
                'type': 'PARTIALLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'overlap_percentage': method['overlap_percentage'],
                'signature': method['signature'][:80] if method.get('signature') else ''
            }
        
        regressor_details = {}
        for method in regressor_methods.get('fully_modified', []):
            regressor_details[method['name']] = {
                'type': 'FULLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'signature': method['signature'][:80] if method.get('signature') else ''
            }
        for method in regressor_methods.get('partially_modified', []):
            regressor_details[method['name']] = {
                'type': 'PARTIALLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'overlap_percentage': method['overlap_percentage'],
                'signature': method['signature'][:80] if method.get('signature') else ''
            }
        
        return {
            'overlap_count': len(overlapping),
            'overlapping_methods': sorted(list(overlapping)),
            'fixing_details': {m: fixing_details[m] for m in overlapping if m in fixing_details},
            'regressor_details': {m: regressor_details[m] for m in overlapping if m in regressor_details}
        }
    
    def process_file(self, bug_id: str, filepath: str, file_data: Dict) -> Dict:
        """Process a single file for a bug"""
        fixing_commits = file_data.get('fixing_commits', [])
        regressor_commits = file_data.get('regressor_commits', [])
        
        file_result = {
            'bug_id': bug_id,
            'filepath': filepath,
            'matches': []
        }
        
        # Compare each fixing commit with each regressor commit
        for fixing_commit in fixing_commits:
            fixing_matched_methods = fixing_commit.get('matched_methods')
            
            if not fixing_matched_methods or not fixing_commit.get('diff_found'):
                continue
            
            for regressor_commit in regressor_commits:
                regressor_matched_methods = regressor_commit.get('matched_methods')
                
                if not regressor_matched_methods or not regressor_commit.get('diff_found'):
                    continue
                
                # Find overlapping methods
                overlap = self.find_method_overlap(fixing_matched_methods, regressor_matched_methods)
                
                if overlap['overlap_count'] > 0:
                    match = {
                        'fixing_commit': {
                            'hash': fixing_commit['commit_hash'],
                            'full_hash': fixing_commit.get('full_hash', 'unknown'),
                            'hunk_count': fixing_commit.get('hunks_count', 0),
                            'methods_total': fixing_commit.get('methods_count', 0),
                            'methods_modified': len(fixing_matched_methods.get('fully_modified', [])) + \
                                               len(fixing_matched_methods.get('partially_modified', []))
                        },
                        'regressor_commit': {
                            'hash': regressor_commit['commit_hash'],
                            'full_hash': regressor_commit.get('full_hash', 'unknown'),
                            'regressor_bug_id': regressor_commit.get('regressor_bug_id'),
                            'hunk_count': regressor_commit.get('hunks_count', 0),
                            'methods_total': regressor_commit.get('methods_count', 0),
                            'methods_modified': len(regressor_matched_methods.get('fully_modified', [])) + \
                                               len(regressor_matched_methods.get('partially_modified', []))
                        },
                        'overlap': overlap
                    }
                    
                    file_result['matches'].append(match)
        
        return file_result
    
    def process_single_bug(self, bug_id: str, bug_data: Dict) -> Dict:
        """Process a single bug and return results"""
        bug_results = {
            'bug_id': bug_id,
            'processing_timestamp': datetime.now().isoformat(),
            'has_matched_fixing_regressor_commit': False,
            'files': [],
            'summary': {
                'total_files_processed': 0,
                'files_with_matches': 0,
                'total_matching_pairs': 0,
                'total_method_overlaps': 0
            }
        }
        
        files_data = bug_data.get('files', [])
        bug_results['summary']['total_files_processed'] = len(files_data)
        
        for file_data in files_data:
            filepath = file_data['filepath']
            
            file_result = self.process_file(bug_id, filepath, file_data)
            
            if file_result['matches']:
                bug_results['files'].append(file_result)
                bug_results['summary']['files_with_matches'] += 1
                bug_results['summary']['total_matching_pairs'] += len(file_result['matches'])
                
                for match in file_result['matches']:
                    bug_results['summary']['total_method_overlaps'] += match['overlap']['overlap_count']
        
        # Set flag if any matches found
        bug_results['has_matched_fixing_regressor_commit'] = bug_results['summary']['total_matching_pairs'] > 0
        
        return bug_results
    
    def save_bug_result(self, bug_id: str, bug_results: Dict) -> str:
        """Save individual bug results to appropriate directory based on match status"""
        if bug_results['has_matched_fixing_regressor_commit']:
            output_file = self.output_bugs_with_match_dir / f"bug_{bug_id}.json"
        else:
            output_file = self.output_bugs_without_match_dir / f"bug_{bug_id}.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(bug_results, f, indent=2)
        
        return str(output_file)
    
    def analyze_all_bugs(self) -> Dict:
        """Analyze all bugs and find fixing/regressor commit matches"""
        print("\n" + "="*80)
        print("STEP 10: MATCH FIXING TO REGRESSOR COMMITS")
        print("="*80 + "\n")
        
        global_summary = {
            'analysis_timestamp': datetime.now().isoformat(),
            'step9_source': str(self.step9_bugs_dir),
            'bugs_processed': 0,
            'bugs_successful': 0,
            'bugs_failed': 0,
            'bugs_with_matched_fixing_regressor_commit': 0,
            'bugs_without_matched_fixing_regressor_commit': 0,
            'total_matching_pairs': 0,
            'total_method_overlaps': 0,
            'bugs_with_match_list': [],
            'bugs_without_match_list': [],
            'bug_details': {}
        }
        
        for bug_id, bug_data in self.step9_bugs.items():
            print(f"\nProcessing Bug {bug_id}...")
            
            try:
                bug_results = self.process_single_bug(bug_id, bug_data)
                output_file = self.save_bug_result(bug_id, bug_results)
                
                # Update global summary
                global_summary['bugs_processed'] += 1
                global_summary['bugs_successful'] += 1
                
                has_matches = bug_results['has_matched_fixing_regressor_commit']
                if has_matches:
                    global_summary['bugs_with_matched_fixing_regressor_commit'] += 1
                    global_summary['bugs_with_match_list'].append(bug_id)
                else:
                    global_summary['bugs_without_matched_fixing_regressor_commit'] += 1
                    global_summary['bugs_without_match_list'].append(bug_id)
                
                global_summary['total_matching_pairs'] += bug_results['summary']['total_matching_pairs']
                global_summary['total_method_overlaps'] += bug_results['summary']['total_method_overlaps']
                
                global_summary['bug_details'][bug_id] = {
                    'output_file': output_file,
                    'has_matched_fixing_regressor_commit': has_matches,
                    'files_with_matches': bug_results['summary']['files_with_matches'],
                    'matching_pairs': bug_results['summary']['total_matching_pairs'],
                    'method_overlaps': bug_results['summary']['total_method_overlaps']
                }
                
                if has_matches:
                    print(f"   Saved to: bugs_with_matched_fixing_regressor_commit/")
                    print(f"    Matching pairs: {bug_results['summary']['total_matching_pairs']}, "
                          f"Method overlaps: {bug_results['summary']['total_method_overlaps']}")
                else:
                    print(f"   Saved to: bugs_without_matched_fixing_regressor_commit/")
                
            except Exception as e:
                print(f"   Error processing bug {bug_id}: {e}")
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
            f.write("STEP 10: FIXING TO REGRESSOR COMMIT MATCHING REPORT\n")
            f.write("="*80 + "\n\n")
            f.write(f"Timestamp: {summary['analysis_timestamp']}\n")
            f.write(f"Step 9 Source: {summary['step9_source']}\n\n")
            
            f.write("="*80 + "\n")
            f.write("MATCHING SUMMARY\n")
            f.write("="*80 + "\n\n")
            
            total = summary['bugs_with_matched_fixing_regressor_commit'] + summary['bugs_without_matched_fixing_regressor_commit']
            with_match = summary['bugs_with_matched_fixing_regressor_commit']
            without_match = summary['bugs_without_matched_fixing_regressor_commit']
            
            f.write(f"Total bugs processed: {total}\n\n")
            f.write(f"┌─────────────────────────────────────────────────────────┐\n")
            f.write(f"│  BUGS WITH MATCHED FIXING & REGRESSOR COMMIT: {with_match:<9}│\n")
            f.write(f"│  BUGS WITHOUT MATCHED FIXING & REGRESSOR COMMIT: {without_match:<6}│\n")
            f.write(f"└─────────────────────────────────────────────────────────┘\n\n")
            
            if total > 0:
                match_percentage = (with_match / total) * 100
                f.write(f"Match rate: {match_percentage:.1f}%\n\n")
            
            f.write(f"Total matching commit pairs: {summary['total_matching_pairs']}\n")
            f.write(f"Total overlapping methods: {summary['total_method_overlaps']}\n\n")
            
            f.write("-"*80 + "\n")
            f.write("BUGS WITH MATCHED FIXING & REGRESSOR COMMIT\n")
            f.write("-"*80 + "\n")
            f.write(f"Directory: bugs_with_matched_fixing_regressor_commit/\n")
            f.write(f"Count: {with_match}\n\n")
            
            if summary['bugs_with_match_list']:
                for bug_id in sorted(summary['bugs_with_match_list'], key=lambda x: int(x) if x.isdigit() else x):
                    details = summary['bug_details'].get(bug_id, {})
                    f.write(f"  Bug {bug_id}:\n")
                    f.write(f"    - Files with matches: {details.get('files_with_matches', 0)}\n")
                    f.write(f"    - Matching pairs: {details.get('matching_pairs', 0)}\n")
                    f.write(f"    - Method overlaps: {details.get('method_overlaps', 0)}\n\n")
            else:
                f.write("  (none)\n\n")
            
            f.write("-"*80 + "\n")
            f.write("BUGS WITHOUT MATCHED FIXING & REGRESSOR COMMIT\n")
            f.write("-"*80 + "\n")
            f.write(f"Directory: bugs_without_matched_fixing_regressor_commit/\n")
            f.write(f"Count: {without_match}\n\n")
            
            if summary['bugs_without_match_list']:
                bug_ids = sorted(summary['bugs_without_match_list'], key=lambda x: int(x) if x.isdigit() else x)
                # Display in columns for compactness
                for i in range(0, len(bug_ids), 5):
                    chunk = bug_ids[i:i+5]
                    f.write("  " + ", ".join(f"Bug {bid}" for bid in chunk) + "\n")
            else:
                f.write("  (none)\n")
        
        print(f"Report saved to: {report_file}")


def main():
    """Main execution"""
    # Verify input directory exists
    if not STEP9_BUGS_DIR.exists():
        print(f"ERROR: Step 9 bugs directory not found: {STEP9_BUGS_DIR}")
        print("Please run Step 9 first.")
        sys.exit(1)
    
    matcher = FixingRegressorMatcher()
    summary = matcher.analyze_all_bugs()
    matcher.save_summary(summary)
    
    print("\n" + "="*80)
    print("FINAL SUMMARY")
    print("="*80)
    print(f"\nTotal bugs processed: {summary['bugs_processed']}")
    print(f"\n┌─────────────────────────────────────────────────────────┐")
    print(f"│  BUGS WITH MATCHED FIXING & REGRESSOR COMMIT: {summary['bugs_with_matched_fixing_regressor_commit']:<9}│")
    print(f"│  BUGS WITHOUT MATCHED FIXING & REGRESSOR COMMIT: {summary['bugs_without_matched_fixing_regressor_commit']:<6}│")
    print(f"└─────────────────────────────────────────────────────────┘")
    print(f"\nTotal matching commit pairs: {summary['total_matching_pairs']}")
    print(f"Total overlapping methods: {summary['total_method_overlaps']}")
    
    print("\n" + "="*80)
    print("✓ STEP 10 COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()