#!/usr/bin/env python3
"""
================================================================================
STEP 3: REGRESSION ANALYZER
================================================================================

SETUP (One-time):
-----------------
* Load ALL commits from BugBug repository (~500k commits)
* For each commit:
  - Extract bug numbers from commit message
  - Store in commits_by_bug[bug_id] = [commit1, commit2, ...]
* Result: O(1) instant lookup of commits by bug ID

ANALYSIS PROCESS:
-----------------
* Load validated bugs from Step 2 JSON file
* For each bug:
  1. Find Fixing Commits:
     - Lookup commits_by_bug[bug_id] (instant)
     - Get all commits that mention this bug
     - Extract files modified by ALL fixing commits
  
  2. Check for Regression:
     - Read bug's regressed_by field
     - If exists: This bug was caused by other bugs
  
  3. Find Regressor Commits (Smart Matching):
     - Get ALL commits from regressor bug
     - Filter: Keep ONLY commits that modified THE SAME FILES as the fix
     - Sort by file overlap count (highest first)
     - Result: Commits that likely introduced the bug
  
  4. Extract Deployment Info:
     - Parse bug comments for uplift information
     - Shows which Firefox versions got the fix/regression

OUTPUT: step3_regression_analysis_*.json
----------------------------------------
* Bugs with regression: Full regression chain + matching commits
* Bugs without regression: Just fixing commits
* For each regressor: Only commits with file overlap (not all commits)

KEY POINTS:
-----------
* Commit index = fast O(1) lookups (no repeated searching)
* Regression matching = file-based overlap (smart filtering)
* Shows ALL fixing commits (no selection)
* Shows ONLY matching regressor commits (filters by file overlap)
"""


from bugbug import repository
from datetime import datetime
import json
import sys
from typing import List, Dict, Optional
from collections import defaultdict
from pathlib import Path

try:
    from bugbug_utils import get_bugbug_cache, BugBugUtils
    UTILS_AVAILABLE = True
except ImportError:
    UTILS_AVAILABLE = False
    print("ERROR: bugbug_utils.py not found!")


class RegressionAnalyzer:
    """Analyzes regression information for validated crash bugs"""
    
    def __init__(self):
        if not UTILS_AVAILABLE:
            raise ImportError("bugbug_utils.py is required but not found!")
        
        self.commits_by_bug = defaultdict(list)
        self.bug_cache = get_bugbug_cache()
        print(f"Loaded BugBug cache: {self.bug_cache.count()} bugs\n")
        
        print("Building commit index...")
        self._build_commit_index()
    
    def _build_commit_index(self):
        """Build an index of commits by bug ID for fast lookup"""
        commit_count = 0
        
        try:
            for commit in repository.get_commits():
                commit_count += 1
                
                if commit_count % 10000 == 0:
                    print(f"  Indexed {commit_count} commits...")
                
                desc = commit.get('desc', '')
                bug_ids = BugBugUtils.extract_bug_ids_from_desc(desc)
                
                for bug_id in bug_ids:
                    self.commits_by_bug[bug_id].append({
                        'node': commit.get('node', 'Unknown'),
                        'desc': commit.get('desc', ''),
                        'author': commit.get('author', 'Unknown'),
                        'pushdate': commit.get('pushdate', 'Unknown'),
                        'files': commit.get('files', []),
                        'components': commit.get('components', [])
                    })
            
            print(f"✓ Indexed {commit_count} commits for {len(self.commits_by_bug)} bugs\n")
        except Exception as e:
            print(f"Error building commit index: {e}")
    
    def load_step2_results(self, step2_file: str) -> Dict:
        """Load Step 2 results from JSON file"""
        print(f"Loading Step 2 results: {step2_file}")
        
        with open(step2_file, 'r') as f:
            step2_results = json.load(f)
        
        validated_bugs = step2_results.get('validated_bugs', {})
        print(f"  Found {len(validated_bugs)} validated bugs\n")
        
        return step2_results
    
    def get_matching_regression_commits(self, regression_commits: List[Dict], 
                                       fixing_files: List[str]) -> List[Dict]:
        """Get regression commits that modified the same files as the fix"""
        if not regression_commits or not fixing_files:
            return []
        
        fixing_files_set = set(fixing_files)
        matching_commits = []
        
        for commit in regression_commits:
            commit_files = set(commit.get('files', []))
            overlap = len(fixing_files_set & commit_files)
            overlapping_files = list(fixing_files_set & commit_files)
            
            if overlap > 0:
                matching_commits.append({
                    'commit_hash': commit['node'],
                    'short_hash': commit['node'][:12],
                    'description': commit['desc'],
                    'author': commit['author'],
                    'files_modified': commit['files'],
                    'components': commit['components'],
                    'pushdate': commit.get('pushdate', 'Unknown'),
                    'file_overlap_count': overlap,
                    'overlapping_files': overlapping_files
                })
        
        matching_commits.sort(key=lambda x: x['file_overlap_count'], reverse=True)
        return matching_commits
    
    def analyze_regression_chain(self, bug: Dict, bug_id: str, 
                                 fixing_files: List[str]) -> Dict:
        """Analyze the regression chain to find introducing commits"""
        regression_info = {
            'has_regression': False,
            'regressed_by_bugs': [],
            'regression_details': []
        }
        
        regressed_by = bug.get('regressed_by', [])
        if not regressed_by:
            return regression_info
        
        regression_info['has_regression'] = True
        regression_info['regressed_by_bugs'] = regressed_by
        
        regressing_bugs = self.bug_cache.get_bugs_batch(regressed_by)
        
        for regressing_bug in regressing_bugs:
            regressing_bug_id = str(regressing_bug['id'])
            
            all_regressing_commits = self.commits_by_bug.get(regressing_bug_id, [])
            matching_commits = self.get_matching_regression_commits(
                all_regressing_commits, 
                fixing_files
            )
            
            uplift_info = BugBugUtils.extract_uplift_information(regressing_bug)
            
            regression_detail = {
                'bug_id': regressing_bug_id,
                'summary': regressing_bug.get('summary', 'N/A'),
                'status': regressing_bug.get('status', 'N/A'),
                'resolution': regressing_bug.get('resolution', 'N/A'),
                'all_commits_count': len(all_regressing_commits),
                'matching_commits': matching_commits,
                'uplifts': uplift_info
            }
            
            regression_info['regression_details'].append(regression_detail)
        
        return regression_info
    
    def analyze_bug(self, bug_id: str) -> Optional[Dict]:
        """Analyze a single bug for regression information"""
        bug = self.bug_cache.get_bug(bug_id)
        if not bug:
            return None
        
        fixing_commits = self.commits_by_bug.get(bug_id, [])
        uplift_info = BugBugUtils.extract_uplift_information(bug)
        
        all_fixing_files = []
        for commit in fixing_commits:
            all_fixing_files.extend(commit.get('files', []))
        all_fixing_files = list(set(all_fixing_files))
        
        regression_chain = self.analyze_regression_chain(bug, bug_id, all_fixing_files)
        
        return {
            'bug_id': bug_id,
            'summary': bug.get('summary', 'N/A'),
            'status': bug.get('status', 'N/A'),
            'resolution': bug.get('resolution', 'N/A'),
            'product': bug.get('product', 'N/A'),
            'component': bug.get('component', 'N/A'),
            'severity': bug.get('bug_severity', 'N/A'),
            'fixing_commits': [
                {
                    'commit_hash': c['node'],
                    'short_hash': c['node'][:12],
                    'description': c['desc'],
                    'author': c['author'],
                    'files': c['files'],
                    'pushdate': c.get('pushdate', 'Unknown')
                }
                for c in fixing_commits
            ],
            'fixing_files': all_fixing_files,
            'fix_uplifts': uplift_info,
            'regression_chain': regression_chain
        }
    
    def analyze_from_step2_file(self, step2_file: str) -> Dict:
        """Main function: Analyze bugs from Step 2 JSON file"""
        
        step2_results = self.load_step2_results(step2_file)
        validated_bugs = step2_results.get('validated_bugs', {})
        
        print("="*80)
        print("REGRESSION ANALYSIS")
        print("="*80)
        
        regression_analyses = {}
        regression_count = 0
        non_regression_count = 0
        
        for bug_id in validated_bugs.keys():
            print(f"Analyzing Bug {bug_id}...", end=' ')
            
            analysis = self.analyze_bug(bug_id)
            
            if analysis:
                if analysis['regression_chain']['has_regression']:
                    regression_analyses[bug_id] = analysis
                    regression_count += 1
                    print(" (has regression)")
                else:
                    non_regression_count += 1
                    print(" (no regression)")
            else:
                print(" (not found)")
        
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"{'='*80}")
        print(f"Bugs with regression: {regression_count}")
        print(f"Bugs without regression: {non_regression_count}")
        
        # Print detailed results
        for bug_id, analysis in regression_analyses.items():
            self._print_bug_summary(bug_id, analysis)
        
        return {
            'signature': step2_results.get('signature'),
            'step2_file': step2_file,
            'analysis_timestamp': datetime.now().isoformat(),
            'summary': {
                'total_validated_bugs': len(validated_bugs),
                'regression_bugs': regression_count,
                'non_regression_bugs': non_regression_count
            },
            'regression_analyses': regression_analyses
        }
    
    def _print_bug_summary(self, bug_id: str, analysis: Dict):
        """Print summary for a single bug"""
        print(f"\n{'='*80}")
        print(f"BUG {bug_id}: {analysis['summary']}")
        print(f"{'='*80}")
        print(f"Status: {analysis['status']} / {analysis['resolution']}")
        print(f"Product: {analysis['product']} - {analysis['component']}")
        print(f"URL: https://bugzilla.mozilla.org/show_bug.cgi?id={bug_id}")
        
        if analysis.get('fixing_commits'):
            print(f"\nFIXING COMMITS ({len(analysis['fixing_commits'])})")
            for i, commit in enumerate(analysis['fixing_commits'], 1):
                print(f"\n  Fix #{i}:")
                print(f"    Commit: {commit['short_hash']}")
                print(f"    Author: {commit['author']}")
                print(f"    Description: {commit['description'][:80]}...")
                if commit['files']:
                    print(f"    Files ({len(commit['files'])}): {', '.join(commit['files'][:3])}...")
        
        reg_chain = analysis['regression_chain']
        if reg_chain['has_regression']:
            print(f"\nREGRESSED BY: {reg_chain['regressed_by_bugs']}")
            
            for reg_detail in reg_chain['regression_details']:
                print(f"\n  Regressor Bug {reg_detail['bug_id']}: {reg_detail['summary']}")
                print(f"  Status: {reg_detail['status']} / {reg_detail['resolution']}")
                print(f"  Total commits: {reg_detail['all_commits_count']}")
                
                matching_commits = reg_detail.get('matching_commits', [])
                if matching_commits:
                    print(f"\n    MATCHING COMMITS ({len(matching_commits)} modified same files)")
                    
                    for idx, commit in enumerate(matching_commits[:3], 1):
                        print(f"\n    Match #{idx}:")
                        print(f"      Commit: {commit['short_hash']}")
                        print(f"      File Overlap: {commit['file_overlap_count']} files")
                        print(f"      Description: {commit['description'][:60]}...")
                        if commit['overlapping_files']:
                            print(f"      Files: {', '.join(commit['overlapping_files'][:3])}...")
                else:
                    print(f"\n     No commits modified the same files as the fix")
    
    def save_results(self, results: Dict, filename: str = None) -> str:
        """Save regression analysis results to JSON"""
        if not filename:
            safe_sig = results['signature'].replace(':', '_').replace('/', '_')[:50]
            #timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = f"step3_regression_analysis_{safe_sig}.json"  #_{timestamp}.json
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved: {filename}")
        return filename


def main():
    """Main execution"""
    
    if not UTILS_AVAILABLE:
        print("ERROR: bugbug_utils.py not found!")
        return
    
    # Get Step 2 file
    if len(sys.argv) > 1:
        step2_file = sys.argv[1]
    else:
        step2_file = "step2_bugbug_analysis_OOM | small.json"
        print(f"Using default file: {step2_file}")
        print(f"Usage: python {sys.argv[0]} <step2_results.json>\n")
    
    # Check file exists
    if not Path(step2_file).exists():
        print(f"ERROR: File '{step2_file}' not found!")
        return
    
    # Run analysis
    try:
        analyzer = RegressionAnalyzer()
        results = analyzer.analyze_from_step2_file(step2_file)
        filename = analyzer.save_results(results)
        
        print("\n" + "="*80)
        print("STEP 3 COMPLETE")
        print("="*80)
        print(f"Step 2 file: {step2_file}")
        print(f"Regression bugs: {results['summary']['regression_bugs']}")
        print(f"Output file: {filename}")
        
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()