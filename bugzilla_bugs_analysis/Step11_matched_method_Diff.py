#!/usr/bin/env python3
"""
Step 11: Extract Matched Method Diffs AND Full Content from Local Mercurial Repos
For each match in Step 10, fetch diffs and full file content at both commits.

INPUT:  outputs/step10_fixing_regressor_matching/bugs_with_matched_fixing_regressor_commit/bug_<ID>.json
OUTPUT: outputs/step11_matched_method_diffs/
        ├── bugs/
        │   ├── bug_<ID>/
        │   │   └── <filepath>/
        │   │       └── match_<N>/
        │   │           ├── fixing_<hash>.diff
        │   │           ├── fixing_<hash>.full
        │   │           ├── regressor_<hash>.diff
        │   │           ├── regressor_<hash>.full
        │   │           └── match_info.json
        │   └── ...
        ├── extraction_summary.json
        └── extraction_report.txt
"""

import json
import os
import subprocess
import re
from datetime import datetime
from typing import Dict, Optional, Tuple
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
outputs_dir = script_dir / "outputs"

# Input paths
STEP10_DIR = outputs_dir / "step10_fixing_regressor_matching"
STEP10_BUGS_DIR = STEP10_DIR / "bugs_with_matched_fixing_regressor_commit"

# Output paths
OUTPUT_DIR = outputs_dir / "step11_matched_method_diffs"
OUTPUT_BUGS_DIR = OUTPUT_DIR / "bugs"

# Local repositories
LOCAL_REPOS = {
    'mozilla-central': parent_dir / 'mozilla-central',
    'mozilla-release': parent_dir / 'mozilla-release',
    'mozilla-autoland': parent_dir / 'mozilla-autoland',
    'mozilla-esr115': parent_dir / 'mozilla-esr115'
}


class LocalRepoExtractor:
    """Extract diffs and full content from local Mercurial repositories"""
    
    def __init__(self, local_repos: Dict[str, str] = None, output_dir: str = None, debug: bool = False):
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.output_bugs_dir = self.output_dir / "bugs"
        self.debug = debug
        
        # Create output directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_bugs_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup local repos
        self.local_repos = {}
        repos = local_repos or LOCAL_REPOS
        
        print("Validating local Mercurial repositories:")
        for name, path in repos.items():
            path = Path(path)
            if path.exists():
                self.local_repos[name] = str(path)
                print(f"   {name}: {path}")
            else:
                print(f"   {name}: {path} (NOT FOUND)")
        print()
        
        # Load all bug files from Step 10 (only bugs with matches)
        self.step10_bugs = self._load_step10_bugs()
        print(f"Found {len(self.step10_bugs)} bugs with matches in Step 10\n")
    
    def _load_step10_bugs(self) -> Dict:
        """Load all bug JSON files from Step 10 (bugs with matches only)"""
        bugs = {}
        
        if not STEP10_BUGS_DIR.exists():
            print(f"ERROR: Step 10 bugs directory not found: {STEP10_BUGS_DIR}")
            return bugs
        
        print(f"Loading Step 10 bugs from: {STEP10_BUGS_DIR}")
        
        for bug_file in STEP10_BUGS_DIR.glob("bug_*.json"):
            try:
                with open(bug_file, 'r') as f:
                    bug_data = json.load(f)
                bug_id = bug_data.get('bug_id', bug_file.stem.replace('bug_', ''))
                bugs[str(bug_id)] = bug_data
            except Exception as e:
                print(f"  Warning: Could not load {bug_file}: {e}")
        
        return bugs
    
    def find_repo_for_commit(self, commit_hash: str) -> Optional[Tuple[str, str]]:
        """Find which repo contains this commit"""
        for repo_name, repo_path in self.local_repos.items():
            try:
                result = subprocess.run(
                    ['hg', 'log', '-r', commit_hash, '--template', 'x'],
                    cwd=repo_path,
                    capture_output=True,
                    timeout=10
                )
                if result.returncode == 0:
                    return (repo_name, repo_path)
            except:
                continue
        return None
    
    def get_commit_diff(self, commit_hash: str, filepath: str, repo_path: str) -> Optional[str]:
        """Fetch diff for a specific file from a commit"""
        try:
            result = subprocess.run(
                ['hg', 'diff', '-c', commit_hash],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0 and result.stdout:
                file_diff = self._extract_file_diff(result.stdout, filepath)
                return file_diff
        except Exception as e:
            if self.debug:
                print(f"        Error fetching diff: {e}")
        return None
    
    def get_full_file_content(self, commit_hash: str, filepath: str, repo_path: str) -> Optional[str]:
        """Fetch full file content at a specific commit"""
        try:
            result = subprocess.run(
                ['hg', 'cat', '-r', commit_hash, filepath],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            if self.debug:
                print(f"        Error fetching content: {e}")
        return None
    
    def _extract_file_diff(self, full_diff: str, filepath: str) -> Optional[str]:
        """Extract file-specific diff from full commit diff"""
        lines = full_diff.split('\n')
        in_file = False
        file_diff_lines = []
        
        for line in lines:
            if line.startswith('diff --git'):
                match = re.search(r'b/(.+?)(?:\s|$)', line)
                if match:
                    current_file = match.group(1)
                    if current_file == filepath:
                        in_file = True
                        file_diff_lines = [line]
                    elif in_file:
                        break
            elif line.startswith('diff -r'):
                if filepath in line:
                    in_file = True
                    file_diff_lines = [line]
                elif in_file:
                    break
            elif in_file:
                if line.startswith('diff'):
                    break
                file_diff_lines.append(line)
        
        return '\n'.join(file_diff_lines) if file_diff_lines else None
    
    def _save_match_metadata(self, dest_dir: Path, match: Dict, filepath: str,
                             fixing_diff_found: bool, regressor_diff_found: bool,
                             fixing_content_found: bool, regressor_content_found: bool):
        """Save match metadata to JSON"""
        metadata = {
            'filepath': filepath,
            'overlapping_methods': match['overlap'],
            'fixing_commit': {
                'hash': match['fixing_commit']['hash'],
                'full_hash': match['fixing_commit']['full_hash'],
                'diff_found': fixing_diff_found,
                'full_content_found': fixing_content_found
            },
            'regressor_commit': {
                'hash': match['regressor_commit']['hash'],
                'full_hash': match['regressor_commit']['full_hash'],
                'regressor_bug_id': match['regressor_commit']['regressor_bug_id'],
                'diff_found': regressor_diff_found,
                'full_content_found': regressor_content_found
            }
        }
        
        with open(dest_dir / 'match_info.json', 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
    
    def process_match(self, bug_id: str, filepath: str, match_idx: int, match: Dict) -> Dict:
        """Process single match: fetch diffs and full content"""
        fixing_commit = match['fixing_commit']
        regressor_commit = match['regressor_commit']
        
        # Find repos for commits
        fixing_repo = self.find_repo_for_commit(fixing_commit['hash'])
        regressor_repo = self.find_repo_for_commit(regressor_commit['hash'])
        
        if not fixing_repo or not regressor_repo:
            return None
        
        fixing_repo_name, fixing_repo_path = fixing_repo
        regressor_repo_name, regressor_repo_path = regressor_repo
        
        # Fetch diffs and content
        fixing_diff = self.get_commit_diff(fixing_commit['hash'], filepath, fixing_repo_path)
        regressor_diff = self.get_commit_diff(regressor_commit['hash'], filepath, regressor_repo_path)
        fixing_content = self.get_full_file_content(fixing_commit['hash'], filepath, fixing_repo_path)
        regressor_content = self.get_full_file_content(regressor_commit['hash'], filepath, regressor_repo_path)
        
        # Create match directory
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        match_dir = self.output_bugs_dir / f"bug_{bug_id}" / safe_filepath / f"match_{match_idx}"
        match_dir.mkdir(parents=True, exist_ok=True)
        
        files_saved = 0
        
        if fixing_diff:
            with open(match_dir / f"fixing_{fixing_commit['hash']}.diff", 'w', encoding='utf-8') as f:
                f.write(fixing_diff)
            files_saved += 1
        
        if fixing_content:
            with open(match_dir / f"fixing_{fixing_commit['hash']}.full", 'w', encoding='utf-8') as f:
                f.write(fixing_content)
            files_saved += 1
        
        if regressor_diff:
            with open(match_dir / f"regressor_{regressor_commit['hash']}.diff", 'w', encoding='utf-8') as f:
                f.write(regressor_diff)
            files_saved += 1
        
        if regressor_content:
            with open(match_dir / f"regressor_{regressor_commit['hash']}.full", 'w', encoding='utf-8') as f:
                f.write(regressor_content)
            files_saved += 1
        
        # Save metadata
        self._save_match_metadata(match_dir, match, filepath,
                                  fixing_diff is not None, regressor_diff is not None,
                                  fixing_content is not None, regressor_content is not None)
        
        return {
            'match_idx': match_idx,
            'fixing_diff_found': fixing_diff is not None,
            'regressor_diff_found': regressor_diff is not None,
            'fixing_content_found': fixing_content is not None,
            'regressor_content_found': regressor_content is not None,
            'files_saved': files_saved,
            'methods_count': len(match['overlap']['overlapping_methods'])
        }
    
    def process_single_bug(self, bug_id: str, bug_data: Dict) -> Dict:
        """Process a single bug and return results"""
        bug_results = {
            'bug_id': bug_id,
            'processing_timestamp': datetime.now().isoformat(),
            'files': [],
            'summary': {
                'total_files': 0,
                'total_matches': 0,
                'complete_pairs': 0,
                'partial_pairs': 0,
                'no_diffs': 0
            }
        }
        
        for file_data in bug_data.get('files', []):
            filepath = file_data['filepath']
            
            file_results = {'filepath': filepath, 'matches': []}
            
            for match_idx, match in enumerate(file_data.get('matches', []), 1):
                result = self.process_match(bug_id, filepath, match_idx, match)
                
                if result:
                    file_results['matches'].append(result)
                    bug_results['summary']['total_matches'] += 1
                    
                    if result['fixing_diff_found'] and result['regressor_diff_found']:
                        bug_results['summary']['complete_pairs'] += 1
                    elif result['fixing_diff_found'] or result['regressor_diff_found']:
                        bug_results['summary']['partial_pairs'] += 1
                    else:
                        bug_results['summary']['no_diffs'] += 1
            
            if file_results['matches']:
                bug_results['files'].append(file_results)
                bug_results['summary']['total_files'] += 1
        
        return bug_results
    
    def save_bug_result(self, bug_id: str, bug_results: Dict) -> str:
        """Save individual bug results to JSON file"""
        bug_dir = self.output_bugs_dir / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        
        output_file = bug_dir / f"bug_{bug_id}_extraction.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(bug_results, f, indent=2)
        
        return str(output_file)
    
    def extract_all(self) -> Dict:
        """Extract all matched method diffs and full content"""
        print("="*80)
        print("STEP 11: EXTRACT MATCHED METHOD DIFFS + FULL CONTENT")
        print("="*80 + "\n")
        
        global_summary = {
            'extraction_timestamp': datetime.now().isoformat(),
            'step10_source': str(STEP10_BUGS_DIR),
            'output_dir': str(self.output_dir),
            'bugs_processed': 0,
            'bugs_successful': 0,
            'bugs_failed': 0,
            'total_files': 0,
            'total_matches': 0,
            'complete_pairs': 0,
            'partial_pairs': 0,
            'no_diffs': 0,
            'bug_summaries': {}
        }
        
        for bug_id, bug_data in self.step10_bugs.items():
            print(f"\nProcessing Bug {bug_id}...")
            
            try:
                bug_results = self.process_single_bug(bug_id, bug_data)
                output_file = self.save_bug_result(bug_id, bug_results)
                
                # Update global summary
                global_summary['bugs_processed'] += 1
                global_summary['bugs_successful'] += 1
                global_summary['total_files'] += bug_results['summary']['total_files']
                global_summary['total_matches'] += bug_results['summary']['total_matches']
                global_summary['complete_pairs'] += bug_results['summary']['complete_pairs']
                global_summary['partial_pairs'] += bug_results['summary']['partial_pairs']
                global_summary['no_diffs'] += bug_results['summary']['no_diffs']
                
                global_summary['bug_summaries'][bug_id] = {
                    'output_file': output_file,
                    'files': bug_results['summary']['total_files'],
                    'matches': bug_results['summary']['total_matches'],
                    'complete_pairs': bug_results['summary']['complete_pairs'],
                    'partial_pairs': bug_results['summary']['partial_pairs']
                }
                
                print(f"  ✓ Saved: {output_file}")
                print(f"    Files: {bug_results['summary']['total_files']}, "
                      f"Matches: {bug_results['summary']['total_matches']}, "
                      f"Complete pairs: {bug_results['summary']['complete_pairs']}")
                
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
            f.write("STEP 11: MATCHED METHOD DIFFS EXTRACTION REPORT\n")
            f.write("="*80 + "\n\n")
            f.write(f"Timestamp: {summary['extraction_timestamp']}\n")
            f.write(f"Step 10 Source: {summary['step10_source']}\n")
            f.write(f"Output Directory: {summary['output_dir']}\n\n")
            
            f.write("-"*40 + "\n")
            f.write("OVERALL STATISTICS\n")
            f.write("-"*40 + "\n")
            f.write(f"Bugs processed: {summary['bugs_processed']}\n")
            f.write(f"Bugs successful: {summary['bugs_successful']}\n")
            f.write(f"Bugs failed: {summary['bugs_failed']}\n")
            f.write(f"Total files: {summary['total_files']}\n")
            f.write(f"Total matches: {summary['total_matches']}\n")
            f.write(f"Complete pairs (both diffs found): {summary['complete_pairs']}\n")
            f.write(f"Partial pairs (one diff found): {summary['partial_pairs']}\n")
            f.write(f"No diffs found: {summary['no_diffs']}\n\n")
            
            f.write("-"*40 + "\n")
            f.write("PER-BUG SUMMARY\n")
            f.write("-"*40 + "\n")
            for bug_id, bug_sum in summary['bug_summaries'].items():
                f.write(f"\nBug {bug_id}:\n")
                f.write(f"  Files: {bug_sum['files']}\n")
                f.write(f"  Matches: {bug_sum['matches']}\n")
                f.write(f"  Complete pairs: {bug_sum['complete_pairs']}\n")
                f.write(f"  Partial pairs: {bug_sum['partial_pairs']}\n")
        
        print(f"Report saved to: {report_file}")


def main():
    """Main execution"""
    if not STEP10_BUGS_DIR.exists():
        print(f"ERROR: Step 10 bugs directory not found: {STEP10_BUGS_DIR}")
        print("Please run Step 10 first.")
        sys.exit(1)
    
    extractor = LocalRepoExtractor(debug=True)
    summary = extractor.extract_all()
    extractor.save_summary(summary)
    
    print("\n" + "="*80)
    print("EXTRACTION SUMMARY")
    print("="*80)
    print(f"Bugs processed: {summary['bugs_processed']}")
    print(f"Bugs successful: {summary['bugs_successful']}")
    print(f"Total files: {summary['total_files']}")
    print(f"Total matches: {summary['total_matches']}")
    print(f"Complete pairs: {summary['complete_pairs']}")
    
    print("\n" + "="*80)
    print("✓ STEP 11 COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()