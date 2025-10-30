#!/usr/bin/env python3
"""
Step 9: Match Fixing Commits to Regressor Commits
For each bug/file, compare methods changed in fixing commits with
methods changed in regressor commits to identify which fix addresses
which regression.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Set, Tuple


class FixingRegressorMatcher:
    """Match fixing commits to regressor commits by comparing changed methods"""
    
    def __init__(self, step8_json_file: str, output_dir: str = "fixing_regressor_analysis"):
        self.step8_json_file = step8_json_file
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"Loading Step 8 results from: {step8_json_file}")
        with open(step8_json_file, 'r') as f:
            self.step8_data = json.load(f)
        
        print(f"Found {len(self.step8_data['bugs'])} bugs\n")
    
    def get_changed_method_names(self, matched_methods: Dict) -> Set[str]:
        """
        Extract all method names that were changed (fully or partially)
        """
        changed = set()
        
        if matched_methods:
            # Add fully modified methods
            for method in matched_methods.get('fully_modified', []):
                changed.add(method['name'])
            
            # Add partially modified methods
            for method in matched_methods.get('partially_modified', []):
                changed.add(method['name'])
        
        return changed
    
    def find_method_overlap(self, fixing_methods: Dict, regressor_methods: Dict) -> Dict:
        """
        Find methods that changed in both fixing and regressor commits
        
        Returns: {
            'overlap_count': number,
            'overlapping_methods': [list of method names],
            'fixing_details': {method_name: modification_info},
            'regressor_details': {method_name: modification_info}
        }
        """
        fixing_changed = self.get_changed_method_names(fixing_methods)
        regressor_changed = self.get_changed_method_names(regressor_methods)
        
        overlapping = fixing_changed & regressor_changed
        
        # Build details for overlapping methods
        fixing_details = {}
        for method in fixing_methods.get('fully_modified', []):
            fixing_details[method['name']] = {
                'type': 'FULLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'signature': method['signature'][:80] + '...' if len(method['signature']) > 80 else method['signature']
            }
        
        for method in fixing_methods.get('partially_modified', []):
            fixing_details[method['name']] = {
                'type': 'PARTIALLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'overlap_percentage': method['overlap_percentage'],
                'signature': method['signature'][:80] + '...' if len(method['signature']) > 80 else method['signature']
            }
        
        regressor_details = {}
        for method in regressor_methods.get('fully_modified', []):
            regressor_details[method['name']] = {
                'type': 'FULLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'signature': method['signature'][:80] + '...' if len(method['signature']) > 80 else method['signature']
            }
        
        for method in regressor_methods.get('partially_modified', []):
            regressor_details[method['name']] = {
                'type': 'PARTIALLY_MODIFIED',
                'lines': f"{method['start_line']}-{method['end_line']}",
                'overlap_percentage': method['overlap_percentage'],
                'signature': method['signature'][:80] + '...' if len(method['signature']) > 80 else method['signature']
            }
        
        return {
            'overlap_count': len(overlapping),
            'overlapping_methods': sorted(list(overlapping)),
            'fixing_details': {m: fixing_details[m] for m in overlapping if m in fixing_details},
            'regressor_details': {m: regressor_details[m] for m in overlapping if m in regressor_details}
        }
    
    def process_file(self, bug_id: str, filepath: str, file_data: Dict) -> Dict:
        """
        Process a single file for a bug
        Match fixing commits to regressor commits
        """
        fixing_commits = file_data['fixing_commits']
        regressor_commits = file_data['regressor_commits']
        
        file_result = {
            'bug_id': bug_id,
            'filepath': filepath,
            'matches': []
        }
        
        # Compare each fixing commit with each regressor commit
        for fixing_commit in fixing_commits:
            fixing_matched_methods = fixing_commit.get('matched_methods')
            
            if not fixing_matched_methods or fixing_commit['diff_found'] == False:
                continue
            
            for regressor_commit in regressor_commits:
                regressor_matched_methods = regressor_commit.get('matched_methods')
                
                if not regressor_matched_methods or regressor_commit['diff_found'] == False:
                    continue
                
                # Find overlapping methods
                overlap = self.find_method_overlap(fixing_matched_methods, regressor_matched_methods)
                
                # Only record if there's overlap
                if overlap['overlap_count'] > 0:
                    match = {
                        'fixing_commit': {
                            'hash': fixing_commit['commit_hash'],
                            'full_hash': fixing_commit['full_hash'],
                            'hunk_count': fixing_commit['hunks_count'],
                            'methods_total': fixing_commit['methods_count'],
                            'methods_modified': (
                                len(fixing_matched_methods['fully_modified']) +
                                len(fixing_matched_methods['partially_modified'])
                            )
                        },
                        'regressor_commit': {
                            'hash': regressor_commit['commit_hash'],
                            'full_hash': regressor_commit['full_hash'],
                            'regressor_bug_id': regressor_commit.get('regressor_bug_id'),
                            'hunk_count': regressor_commit['hunks_count'],
                            'methods_total': regressor_commit['methods_count'],
                            'methods_modified': (
                                len(regressor_matched_methods['fully_modified']) +
                                len(regressor_matched_methods['partially_modified'])
                            )
                        },
                        'overlap': overlap
                    }
                    
                    file_result['matches'].append(match)
        
        return file_result
    
    def analyze_all_bugs(self) -> Dict:
        """
        Analyze all bugs and find fixing/regressor commit matches
        """
        print("\n" + "="*80)
        print("STEP 9: MATCH FIXING TO REGRESSOR COMMITS")
        print("="*80 + "\n")
        
        all_results = {
            'analysis_timestamp': datetime.now().isoformat(),
            'step8_source': self.step8_json_file,
            'bugs': {},
            'summary': {
                'bugs_analyzed': 0,
                'bugs_with_matches': 0,
                'bugs_without_matches': 0,
                'total_bugs': 0,
                'files_analyzed': 0,
                'matching_pairs': 0,
                'total_method_overlaps': 0
            }
        }
        
        total_matching_pairs = 0
        total_method_overlaps = 0
        
        for bug_id, bug_data in self.step8_data['bugs'].items():
            print(f"Bug {bug_id}:")
            bug_results = {
                'bug_id': bug_id,
                'files': []
            }
            
            for file_data in bug_data['files']:
                filepath = file_data['filepath']
                print(f"  File: {filepath}")
                
                file_result = self.process_file(bug_id, filepath, file_data)
                
                if file_result['matches']:
                    bug_results['files'].append(file_result)
                    print(f"    Found {len(file_result['matches'])} matching commit pair(s)")
                    
                    for i, match in enumerate(file_result['matches'], 1):
                        methods_overlap = match['overlap']['overlap_count']
                        fixing_hash = match['fixing_commit']['hash'][:8]
                        regressor_hash = match['regressor_commit']['hash'][:8]
                        
                        print(f"      Bug {bug_id}: {fixing_hash} (fixing) vs {regressor_hash} (regressor)")
                        print(f"        Methods matched: {methods_overlap}")
                        for method_name in match['overlap']['overlapping_methods'][:3]:
                            print(f"          - {method_name}")
                        if len(match['overlap']['overlapping_methods']) > 3:
                            print(f"          ... and {len(match['overlap']['overlapping_methods']) - 3} more")
                        
                        total_matching_pairs += 1
                        total_method_overlaps += methods_overlap
                else:
                    print(f"    No matching commit pairs found")
            
            if bug_results['files']:
                all_results['bugs'][bug_id] = bug_results
                all_results['summary']['bugs_analyzed'] += 1
            
            all_results['summary']['files_analyzed'] += len(bug_data['files'])
        
        all_results['summary']['matching_pairs'] = total_matching_pairs
        all_results['summary']['total_method_overlaps'] = total_method_overlaps
        
        print(f"\n{'='*80}")
        print("ANALYSIS SUMMARY")
        print(f"{'='*80}")
        print(f"Bugs analyzed: {all_results['summary']['bugs_analyzed']}")
        print(f"Files analyzed: {all_results['summary']['files_analyzed']}")
        print(f"Matching commit pairs found: {total_matching_pairs}")
        print(f"Total method overlaps: {total_method_overlaps}")
        
        # Calculate bugs with and without matches
        bugs_with_matches = len(all_results['bugs'])
        total_bugs_in_step8 = len(self.step8_data['bugs'])
        bugs_without_matches = total_bugs_in_step8 - bugs_with_matches
        
        # Save these back to summary
        all_results['summary']['bugs_with_matches'] = bugs_with_matches
        all_results['summary']['bugs_without_matches'] = bugs_without_matches
        all_results['summary']['total_bugs'] = total_bugs_in_step8
        
        print(f"\n{'='*80}")
        print("BUGS WITH MATCHING METHODS")
        print(f"{'='*80}")
        print(f"Bugs with method overlaps: {bugs_with_matches}")
        print(f"Bugs without method overlaps: {bugs_without_matches}")
        print(f"Total bugs: {total_bugs_in_step8}")
        print(f"Percentage with matches: {round((bugs_with_matches/total_bugs_in_step8)*100, 1)}%")
        
        if all_results['bugs']:
            print(f"\nBugs with matches:")
            for bug_id in sorted(all_results['bugs'].keys()):
                print(f"  ✓ Bug {bug_id}")
        
        # Find bugs without matches
        bugs_without = [b for b in self.step8_data['bugs'].keys() if b not in all_results['bugs']]
        if bugs_without:
            print(f"\nBugs without matches:")
            for bug_id in sorted(bugs_without):
                print(f"  ✗ Bug {bug_id}")
        
        return all_results
    
    def save_results(self, results: Dict) -> str:
        """Save results to JSON"""
        output_file = os.path.join(self.output_dir, 'Step9_fixing_regressor_matches.json')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nJSON Results: {output_file}")
        return output_file
    
    def create_summary_report(self, results: Dict) -> str:
        """Create human-readable summary report"""
        output_file = os.path.join(self.output_dir, 'Step9_summary_report.txt')
        
        # Get values from summary
        bugs_analyzed = results['summary'].get('bugs_analyzed', 0)
        bugs_with_matches = results['summary'].get('bugs_with_matches', 0)
        bugs_without_matches = results['summary'].get('bugs_without_matches', 0)
        total_bugs = results['summary'].get('total_bugs', 0)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("FIXING TO REGRESSOR COMMIT MATCHING SUMMARY\n")
            f.write("="*80 + "\n\n")
            f.write(f"Timestamp: {results['analysis_timestamp']}\n")
            f.write(f"Step 8 Source: {results['step8_source']}\n\n")
            
            f.write(f"Bugs Analyzed: {bugs_analyzed}\n")
            f.write(f"Bugs with Matching Methods: {bugs_with_matches}\n")
            f.write(f"Bugs without Matching Methods: {bugs_without_matches}\n")
            match_pct = round((bugs_with_matches/total_bugs)*100, 1) if total_bugs > 0 else 0
            f.write(f"Match Percentage: {match_pct}%\n")
            f.write(f"Files Analyzed: {results['summary'].get('files_analyzed', 0)}\n")
            f.write(f"Matching Commit Pairs: {results['summary'].get('matching_pairs', 0)}\n")
            f.write(f"Total Method Overlaps: {results['summary'].get('total_method_overlaps', 0)}\n\n")
            
            f.write("="*80 + "\n")
            f.write("DETAILED RESULTS BY BUG\n")
            f.write("="*80 + "\n\n")
            
            for bug_id, bug_data in results['bugs'].items():
                f.write(f"BUG {bug_id}\n")
                
                for file_data in bug_data['files']:
                    f.write(f"\n  FILE: {file_data['filepath']}\n")
                    f.write(f"  Matching Pairs: {len(file_data['matches'])}\n\n")
                    
                    for pair_idx, match in enumerate(file_data['matches'], 1):
                        f.write(f"  MATCH {pair_idx}\n")
                        f.write(f"  {'-'*76}\n")
                        
                        # Fixing commit info
                        f.write(f"  FIXING COMMIT:\n")
                        f.write(f"    Hash: {match['fixing_commit']['hash']}\n")
                        f.write(f"    Full Hash: {match['fixing_commit']['full_hash']}\n")
                        f.write(f"    Hunks: {match['fixing_commit']['hunk_count']}\n")
                        f.write(f"    Methods Modified: {match['fixing_commit']['methods_modified']} / {match['fixing_commit']['methods_total']}\n")
                        
                        # Regressor commit info
                        f.write(f"\n  REGRESSOR COMMIT (Introduced Bug):\n")
                        f.write(f"    Hash: {match['regressor_commit']['hash']}\n")
                        f.write(f"    Full Hash: {match['regressor_commit']['full_hash']}\n")
                        f.write(f"    Regressor Bug ID: {match['regressor_commit']['regressor_bug_id']}\n")
                        f.write(f"    Hunks: {match['regressor_commit']['hunk_count']}\n")
                        f.write(f"    Methods Modified: {match['regressor_commit']['methods_modified']} / {match['regressor_commit']['methods_total']}\n")
                        
                        # Overlapping methods
                        f.write(f"\n  METHODS CHANGED IN BOTH:\n")
                        f.write(f"    Total: {match['overlap']['overlap_count']}\n\n")
                        
                        for method_name in match['overlap']['overlapping_methods']:
                            f.write(f"     {method_name}\n")
                            
                            if method_name in match['overlap']['fixing_details']:
                                fixing_info = match['overlap']['fixing_details'][method_name]
                                f.write(f"      Fixing: {fixing_info['type']} (lines {fixing_info['lines']})\n")
                            
                            if method_name in match['overlap']['regressor_details']:
                                regressor_info = match['overlap']['regressor_details'][method_name]
                                f.write(f"      Regressor: {regressor_info['type']} (lines {regressor_info['lines']})\n")
                        
                        f.write(f"\n  {'-'*76}\n\n")
            
            # Add summary of bugs with and without matches
            f.write("\n" + "="*80 + "\n")
            f.write("BUGS WITH MATCHING METHODS SUMMARY\n")
            f.write("="*80 + "\n\n")
            
            f.write(f"Total Bugs Analyzed: {bugs_analyzed}\n")
            f.write(f"Total Bugs: {total_bugs}\n")
            f.write(f"Bugs with Method Overlaps: {bugs_with_matches}\n")
            f.write(f"Bugs without Method Overlaps: {bugs_without_matches}\n\n")
            
            if results['bugs']:
                f.write(f"✓ BUGS WITH MATCHING METHODS ({bugs_with_matches}):\n")
                f.write("-" * 80 + "\n")
                for bug_id in sorted(results['bugs'].keys()):
                    file_count = len(results['bugs'][bug_id]['files'])
                    match_count = sum(len(file_data['matches']) for file_data in results['bugs'][bug_id]['files'])
                    total_overlaps = sum(sum(m['overlap']['overlap_count'] for m in file_data['matches']) for file_data in results['bugs'][bug_id]['files'])
                    f.write(f"  Bug {bug_id}: {file_count} file(s), {match_count} pair(s), {total_overlaps} method(s) changed in both\n")
                f.write("\n")
            
            if bugs_without_matches > 0:
                f.write(f"✗ BUGS WITHOUT MATCHING METHODS ({bugs_without_matches}):\n")
                f.write("-" * 80 + "\n")
                f.write("  These bugs have fixing and regressor commits but no common methods\n")
                f.write("  were changed in both commits.\n\n")
        
        print(f"Text Report: {output_file}")
        return output_file


def main():
    """Main execution"""
    
    step8_json = "step8_method_diff_matching/Step8_method_diff_matching.json"
    
    if not os.path.exists(step8_json):
        print(f"ERROR: Step 8 JSON file not found: {step8_json}")
        return
    
    matcher = FixingRegressorMatcher(
        step8_json_file=step8_json,
        output_dir="step9_fixing_regressor_method_matching"
    )
    
    results = matcher.analyze_all_bugs()
    
    json_file = matcher.save_results(results)
    report_file = matcher.create_summary_report(results)
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()