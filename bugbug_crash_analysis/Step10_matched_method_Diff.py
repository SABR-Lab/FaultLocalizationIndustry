#!/usr/bin/env python3
"""
Step 10: Extract Matched Method Diffs from Local Mercurial Repos
For each match in Step 9, fetch diffs directly from local Mercurial repositories
and extract only the file-specific diff.

Input:  Step 9 results (matches with overlapping methods)
        Local Mercurial repos (mozilla-central, mozilla-release, mozilla-autoland, mozilla-esr115)

Output: matched_methodDiffs/
        ├── bug_XXXXX/
        │   ├── file_path_1/
        │   │   ├── match_1/
        │   │   │   ├── fixing_HASH.diff
        │   │   │   ├── regressor_HASH.diff
        │   │   │   └── match_info.json
"""

import json
import os
import subprocess
import re
from datetime import datetime
from typing import Dict, Optional


class LocalRepoExtractor:
    """Extract diffs from local Mercurial repositories"""
    
    def __init__(self, local_repos: Dict[str, str], output_dir: str = "matched_methodDiffs", 
                 debug: bool = False):
        self.local_repos = local_repos
        self.output_dir = output_dir
        self.debug = debug
        
        os.makedirs(output_dir, exist_ok=True)
        
        print("Validating local Mercurial repositories:")
        for name, path in self.local_repos.items():
            if os.path.exists(path):
                print(f"  ✓ {name}: {path}")
            else:
                print(f"  ✗ {name}: {path} (NOT FOUND)")
        print()
    
    def get_commit_diff(self, commit_hash: str, filepath: str) -> Optional[str]:
        """
        Fetch diff for a specific file from a commit in local repos
        Returns the file-specific diff content
        """
        
        if self.debug:
            print(f"        [DEBUG] Fetching {filepath} from commit {commit_hash}")
        
        for repo_name, repo_path in self.local_repos.items():
            if not os.path.exists(repo_path):
                continue
            
            try:
                # Get full diff for the commit
                result = subprocess.run(
                    ['hg', 'diff', '-c', commit_hash],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0 and result.stdout:
                    if self.debug:
                        print(f"        [DEBUG] Found in {repo_name}")
                    
                    # Extract file-specific diff
                    file_diff = self._extract_file_diff(result.stdout, filepath)
                    if file_diff:
                        if self.debug:
                            print(f"        [DEBUG] ✓ File diff extracted")
                        return file_diff
                    else:
                        if self.debug:
                            print(f"        [DEBUG] File not in this commit's diff")
            
            except subprocess.TimeoutExpired:
                if self.debug:
                    print(f"        [DEBUG] Timeout for {repo_name}")
                continue
            except Exception as e:
                if self.debug:
                    print(f"        [DEBUG] Error in {repo_name}: {e}")
                continue
        
        if self.debug:
            print(f"        [DEBUG] ✗ NOT FOUND in any repo")
        return None
    
    def _extract_file_diff(self, full_diff: str, filepath: str) -> Optional[str]:
        """
        Extract file-specific diff from full commit diff
        Handles both 'diff --git' and 'diff -r' formats
        """
        
        lines = full_diff.split('\n')
        in_file = False
        file_diff_lines = []
        
        for i, line in enumerate(lines):
            # Check for file diff header
            if line.startswith('diff --git'):
                # Format: diff --git a/path/file b/path/file
                match = re.search(r'b/(.+?)(?:\s|$)', line)
                if match:
                    current_file = match.group(1)
                    if current_file == filepath:
                        in_file = True
                        file_diff_lines = [line]
                    elif in_file:
                        # End of our file's diff
                        break
            
            elif line.startswith('diff -r'):
                # Format: diff -r hash1 hash2 path/file
                if filepath in line:
                    in_file = True
                    file_diff_lines = [line]
                elif in_file:
                    break
            
            elif in_file:
                # Continue collecting diff lines
                if line.startswith('diff'):
                    # Start of next file
                    break
                file_diff_lines.append(line)
        
        if file_diff_lines:
            return '\n'.join(file_diff_lines)
        
        return None
    
    def _safe_filename(self, filepath: str) -> str:
        """Convert filepath to safe filename"""
        safe = filepath.replace('/', '_').replace('\\', '_')
        if not safe.endswith('.diff'):
            safe += '.diff'
        return safe
    
    def _save_match_metadata(self, dest_dir: str, match: Dict, filepath: str,
                             fixing_found: bool, regressor_found: bool):
        """Save match metadata to JSON"""
        
        metadata = {
            'filepath': filepath,
            'overlapping_methods': {
                'count': match['overlap']['overlap_count'],
                'methods': match['overlap']['overlapping_methods'],
                'fixing_details': match['overlap']['fixing_details'],
                'regressor_details': match['overlap']['regressor_details']
            },
            'fixing_commit': {
                'hash': match['fixing_commit']['hash'],
                'full_hash': match['fixing_commit']['full_hash'],
                'methods_modified': match['fixing_commit']['methods_modified'],
                'diff_found': fixing_found
            },
            'regressor_commit': {
                'hash': match['regressor_commit']['hash'],
                'full_hash': match['regressor_commit']['full_hash'],
                'regressor_bug_id': match['regressor_commit']['regressor_bug_id'],
                'methods_modified': match['regressor_commit']['methods_modified'],
                'diff_found': regressor_found
            }
        }
        
        metadata_file = os.path.join(dest_dir, 'match_info.json')
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
    
    def process_match(self, bug_id: str, filepath: str, match_idx: int, 
                     match: Dict) -> Dict:
        """Process single match: fetch diffs from local repos"""
        
        fixing_commit = match['fixing_commit']
        regressor_commit = match['regressor_commit']
        
        if self.debug:
            print(f"        [DEBUG] Processing match {match_idx}")
        
        # Fetch diffs from local repos
        fixing_diff = self.get_commit_diff(fixing_commit['hash'], filepath)
        regressor_diff = self.get_commit_diff(regressor_commit['hash'], filepath)
        
        # Create match directory
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        match_dir = os.path.join(
            self.output_dir,
            f"bug_{bug_id}",
            safe_filepath,
            f"match_{match_idx}"
        )
        os.makedirs(match_dir, exist_ok=True)
        
        # Save diffs
        files_saved = 0
        
        if fixing_diff:
            diff_file = os.path.join(match_dir, f"fixing_{fixing_commit['hash']}.diff")
            with open(diff_file, 'w', encoding='utf-8') as f:
                f.write(fixing_diff)
            files_saved += 1
        
        if regressor_diff:
            diff_file = os.path.join(match_dir, f"regressor_{regressor_commit['hash']}.diff")
            with open(diff_file, 'w', encoding='utf-8') as f:
                f.write(regressor_diff)
            files_saved += 1
        
        # Save metadata
        self._save_match_metadata(
            match_dir, match, filepath,
            fixing_diff is not None,
            regressor_diff is not None
        )
        
        return {
            'match_idx': match_idx,
            'fixing_found': fixing_diff is not None,
            'regressor_found': regressor_diff is not None,
            'files_saved': files_saved,
            'methods_count': len(match['overlap']['overlapping_methods'])
        }
    
    def extract_all(self, step9_json: str) -> Dict:
        """Extract all matched method diffs from local repos"""
        
        print("="*80)
        print("STEP 10: EXTRACT MATCHED METHOD DIFFS FROM LOCAL REPOS")
        print("="*80 + "\n")
        
        # Load Step 9 results
        print(f"Loading Step 9 results from: {step9_json}\n")
        with open(step9_json, 'r') as f:
            step9_data = json.load(f)
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'step9_source': step9_json,
            'local_repos': self.local_repos,
            'output_dir': self.output_dir,
            'summary': {
                'bugs': 0,
                'files': 0,
                'matches': 0,
                'complete_pairs': 0,
                'partial_pairs': 0,
                'no_diffs': 0
            },
            'bugs': {}
        }
        
        for bug_id, bug_data in step9_data['bugs'].items():
            print(f"Bug {bug_id}:")
            bug_results = {'files': []}
            
            for file_data in bug_data['files']:
                filepath = file_data['filepath']
                print(f"  File: {filepath}")
                
                file_results = {'filepath': filepath, 'matches': []}
                
                for match_idx, match in enumerate(file_data['matches'], 1):
                    result = self.process_match(bug_id, filepath, match_idx, match)
                    file_results['matches'].append(result)
                    
                    results['summary']['matches'] += 1
                    
                    if result['fixing_found'] and result['regressor_found']:
                        results['summary']['complete_pairs'] += 1
                        status = "✓"
                    elif result['fixing_found'] or result['regressor_found']:
                        results['summary']['partial_pairs'] += 1
                        status = "~"
                    else:
                        results['summary']['no_diffs'] += 1
                        status = "✗"
                    
                    print(f"    Match {match_idx}: {status} ({result['methods_count']} methods)")
                
                bug_results['files'].append(file_results)
                results['summary']['files'] += 1
            
            results['bugs'][bug_id] = bug_results
            results['summary']['bugs'] += 1
        
        # Print summary
        print(f"\n{'='*80}")
        print("EXTRACTION COMPLETE")
        print(f"{'='*80}")
        print(f"Bugs: {results['summary']['bugs']}")
        print(f"Files: {results['summary']['files']}")
        print(f"Matches: {results['summary']['matches']}")
        print(f"  ✓ Complete pairs (both diffs): {results['summary']['complete_pairs']}")
        print(f"  ~ Partial pairs (one diff): {results['summary']['partial_pairs']}")
        print(f"  ✗ No diffs: {results['summary']['no_diffs']}")
        
        completion_pct = (results['summary']['complete_pairs'] / results['summary']['matches'] * 100) \
            if results['summary']['matches'] > 0 else 0
        print(f"Completion: {completion_pct:.1f}%")
        print(f"\nOutput: {self.output_dir}/")
        
        return results


def main():
    """Main execution"""
    
    # Define local repositories
    local_repos = {
        'mozilla-central': './mozilla-central',
        'mozilla-release': './mozilla-release',
        'mozilla-autoland': './mozilla-autoland',
        'mozilla-esr115': './mozilla-esr115'
    }
    
    step9_json = "step9_fixing_regressor_method_matching/Step9_fixing_regressor_matches.json"
    
    # Validate Step 9 file exists
    if not os.path.exists(step9_json):
        print(f"ERROR: Step 9 file not found: {step9_json}")
        return
    
    # Extract diffs from local repos
    extractor = LocalRepoExtractor(
        local_repos,
        output_dir="step10_matched_methodDiffs",
        debug=True
    )
    
    results = extractor.extract_all(step9_json)
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()