#!/usr/bin/env python3
"""
Step 4: Code Extractor for Regression Analysis (Refactored)
Extracts actual code diffs with file-level granularity
- Saves each commit separately to enable many-to-many file comparison
- Organizes by: bug_id/fixing_commits/commit_hash/file.diff
- Organizes by: bug_id/regressor_commits/commit_hash/file.diff
- FILTERS OUT: Bugs with regressions but no file overlap
"""

import json
import requests
import subprocess
from datetime import datetime
from typing import Dict, List, Optional
import time
import os
import re
import shutil


class CodeExtractor:
    """Extracts code diffs from commits with file-level granularity"""
    
    def __init__(self, base_url: str = "https://hg.mozilla.org", 
                 output_dir: str = "extracted_diffs",
                 local_repos: Dict[str, str] = None):
        """
        Initialize the extractor
        
        Args:
            base_url: Base URL for Mozilla's Mercurial repository
            output_dir: Directory to save extracted diffs
            local_repos: Dictionary of local repository paths
        """
        self.base_url = base_url
        self.session = requests.Session()
        self.rate_limit_delay = 0.5
        self.output_dir = output_dir
        self.local_repos = local_repos or {}
        os.makedirs(output_dir, exist_ok=True)
    
    def get_commit_diff(self, commit_hash: str) -> Optional[str]:
        """
        Fetch the diff for a specific commit from local repos first, then remote
        
        Args:
            commit_hash: Full commit hash
            
        Returns:
            Diff text or None if failed
        """
        # Try local repositories first
        if self.local_repos:
            for repo_name, repo_path in self.local_repos.items():
                try:
                    result = subprocess.run(
                        ['hg', 'diff', '-c', commit_hash],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    if result.returncode == 0 and result.stdout:
                        print(f"    ✓ Found in local repo: {repo_name}")
                        return result.stdout
                except Exception:
                    continue
        
        # Fall back to remote if not found locally
        if self.local_repos:
            print(f"    Trying remote repository...")
        
        # Try mozilla-central first (more stable than hg-edge)
        repos_to_try = [
            'mozilla-central',
            'integration/autoland',
            'releases/mozilla-release',
            'releases/mozilla-beta',
            'releases/mozilla-esr128',
            'releases/mozilla-esr115'
        ]
        
        for repo in repos_to_try:
            url = f"{self.base_url}/{repo}/raw-rev/{commit_hash}"
            
            try:
                time.sleep(self.rate_limit_delay)
                response = self.session.get(url, timeout=30)
                
                if response.status_code == 200:
                    print(f"    ✓ Found in remote repo: {repo}")
                    return response.text
            except requests.exceptions.RequestException:
                continue
        
        print(f"    ✗ Commit not found in any repository")
        return None
    
    def parse_diff_by_file(self, diff_content: str) -> Dict[str, str]:
        """
        Parse a unified diff and split it by file
        
        Args:
            diff_content: Full diff content
            
        Returns:
            Dictionary mapping filename to its diff content
        """
        files_dict = {}
        current_file = None
        current_diff = []
        
        lines = diff_content.split('\n')
        
        for line in lines:
            # Detect new file in diff (both 'diff' and '---' patterns)
            if line.startswith('diff --git') or line.startswith('diff -r'):
                # Save previous file if exists
                if current_file and current_diff:
                    files_dict[current_file] = '\n'.join(current_diff)
                
                # Extract filename from diff header
                # Handle: diff --git a/path/file.py b/path/file.py
                match = re.search(r'b/(.+?)(?:\s|$)', line)
                if match:
                    current_file = match.group(1)
                else:
                    current_file = line.split()[-1] if line.split() else "unknown"
                
                current_diff = [line]
            
            elif line.startswith('---') and current_file:
                # Also detect from --- a/path/file.py
                match = re.search(r'---\s+[ab]/(.+?)(?:\s|$)', line)
                if match:
                    current_file = match.group(1)
                current_diff.append(line)
            
            else:
                # Add line to current file's diff
                if current_file:
                    current_diff.append(line)
        
        # Save last file
        if current_file and current_diff:
            files_dict[current_file] = '\n'.join(current_diff)
        
        return files_dict
    
    def create_file_header(self, commit_info: Dict, filepath: str, 
                          commit_type: str) -> str:
        """
        Create a header with metadata to prepend to each diff file
        
        Args:
            commit_info: Commit information
            filepath: File path being modified
            commit_type: 'fixing' or 'regressor'
            
        Returns:
            Header string with metadata
        """
        header = "# " + "="*78 + "\n"
        header += f"# {commit_type.upper()} COMMIT DIFF\n"
        header += "# " + "="*78 + "\n"
        header += f"# File: {filepath}\n"
        header += f"# Commit: {commit_info.get('short_hash', 'Unknown')}\n"
        header += f"# Full Hash: {commit_info.get('commit_hash', 'Unknown')}\n"
        header += f"# Author: {commit_info.get('author', 'Unknown')}\n"
        header += f"# Date: {commit_info.get('pushdate', 'Unknown')}\n"
        
        if commit_type == 'regressor':
            header += f"# Regressor Bug: {commit_info.get('regressor_bug_id', 'Unknown')}\n"
            header += f"# File Overlap Count: {commit_info.get('file_overlap_count', 0)}\n"
        
        header += f"# Description:\n"
        description = commit_info.get('description', 'No description')
        for line in description.split('\n'):
            header += f"#   {line}\n"
        
        header += "# " + "="*78 + "\n\n"
        return header
    
    def extract_commit_files(self, commit_dir: str, commit_info: Dict, 
                           commit_type: str) -> int:
        """
        Extract and save individual file diffs for a commit
        
        Args:
            commit_dir: Directory to save files
            commit_info: Commit information
            commit_type: 'fixing' or 'regressor'
            
        Returns:
            Number of files extracted
        """
        commit_hash = commit_info['commit_hash']
        short_hash = commit_info['short_hash']
        
        # Fetch commit diff using local repos first
        diff_content = self.get_commit_diff(commit_hash)
        
        if not diff_content:
            print(f"      ✗ Failed to fetch {short_hash}")
            return 0
        
        # Parse diff by file
        files_dict = self.parse_diff_by_file(diff_content)
        
        if not files_dict:
            print(f"      ✗ No files found in diff for {short_hash}")
            return 0
        
        # Save each file's diff separately with metadata header
        files_saved = []
        for filepath, file_diff in files_dict.items():
            # Create safe filename
            safe_filename = filepath.replace('/', '_').replace('\\', '_')
            if not safe_filename.endswith('.diff'):
                safe_filename += '.diff'
            
            diff_file_path = os.path.join(commit_dir, safe_filename)
            
            try:
                # Create header with metadata
                header = self.create_file_header(commit_info, filepath, commit_type)
                
                # Write header + diff content
                with open(diff_file_path, 'w', encoding='utf-8') as f:
                    f.write(header)
                    f.write(file_diff)
                files_saved.append(filepath)
            except Exception as e:
                print(f"      ✗ Error saving {filepath}: {e}")
        
        print(f"      ✓ Extracted {len(files_saved)} file(s) from {short_hash}")
        return len(files_saved)
    
    def extract_fixing_commits(self, bug_id: str, bug_dir: str, analysis: Dict) -> Dict:
        """
        Extract all fixing commits with file-level organization
        
        Args:
            bug_id: Bug ID
            bug_dir: Directory for this bug
            analysis: Bug analysis from Step 3
            
        Returns:
            Summary of extraction
        """
        print(f"\n  Extracting fixing commits for Bug {bug_id}...")
        
        fixing_commits = analysis.get('fixing_commits', [])
        if not fixing_commits:
            print(f"    No fixing commits found")
            return {'total_commits': 0, 'total_files': 0}
        
        print(f"    Found {len(fixing_commits)} fixing commit(s)")
        
        # Create fixing_commits directory
        fixing_dir = os.path.join(bug_dir, 'fixing_commits')
        os.makedirs(fixing_dir, exist_ok=True)
        
        total_files = 0
        successful_commits = 0
        
        for i, commit in enumerate(fixing_commits, 1):
            short_hash = commit['short_hash']
            print(f"    [{i}/{len(fixing_commits)}] Processing {short_hash}...")
            
            # Create directory for this commit
            commit_dir = os.path.join(fixing_dir, short_hash)
            os.makedirs(commit_dir, exist_ok=True)
            
            # Extract files
            file_count = self.extract_commit_files(commit_dir, commit, 'fixing')
            
            if file_count > 0:
                total_files += file_count
                successful_commits += 1
        
        return {
            'total_commits': successful_commits,
            'total_files': total_files
        }
    
    def extract_regressor_commits(self, bug_id: str, bug_dir: str, 
                                 regression_chain: Dict) -> Dict:
        """
        Extract all matching regressor commits with file-level organization
        
        Args:
            bug_id: Bug ID
            bug_dir: Directory for this bug
            regression_chain: Regression chain from analysis
            
        Returns:
            Summary of extraction
        """
        if not regression_chain.get('has_regression'):
            return {'total_commits': 0, 'total_files': 0}
        
        print(f"\n  Extracting regressor commits for Bug {bug_id}...")
        
        # Create regressor_commits directory
        regressor_dir = os.path.join(bug_dir, 'regressor_commits')
        os.makedirs(regressor_dir, exist_ok=True)
        
        total_files = 0
        successful_commits = 0
        
        for reg_detail in regression_chain['regression_details']:
            regressor_bug_id = reg_detail['bug_id']
            matching_commits = reg_detail.get('matching_commits', [])
            
            if not matching_commits:
                continue
            
            print(f"    Processing {len(matching_commits)} commit(s) from regressor Bug {regressor_bug_id}")
            
            for i, commit in enumerate(matching_commits, 1):
                short_hash = commit['short_hash']
                overlap_count = commit['file_overlap_count']
                
                print(f"      [{i}/{len(matching_commits)}] Processing {short_hash} ({overlap_count} overlapping files)...")
                
                # Create directory: regressor_commits/regressor_bugID_commitHash/
                commit_dir_name = f"regressor_{regressor_bug_id}_{short_hash}"
                commit_dir = os.path.join(regressor_dir, commit_dir_name)
                os.makedirs(commit_dir, exist_ok=True)
                
                # Add regressor-specific metadata
                commit['regressor_bug_id'] = regressor_bug_id
                commit['overlapping_files'] = commit.get('overlapping_files', [])
                commit['file_overlap_count'] = overlap_count
                
                # Extract files
                file_count = self.extract_commit_files(commit_dir, commit, 'regressor')
                
                if file_count > 0:
                    total_files += file_count
                    successful_commits += 1
        
        return {
            'total_commits': successful_commits,
            'total_files': total_files
        }
    
    def extract_bug_code(self, bug_id: str, analysis: Dict) -> Dict:
        """
        Extract code for a single bug with file-level granularity
        
        Args:
            bug_id: Bug ID
            analysis: Bug analysis from Step 3
            
        Returns:
            Summary of extraction
        """
        print(f"\n{'='*60}")
        print(f"Extracting code for Bug {bug_id}")
        print(f"{'='*60}")
        print(f"Summary: {analysis.get('summary', 'No summary')}")
        
        # Create bug directory
        bug_dir = os.path.join(self.output_dir, f"bug_{bug_id}")
        os.makedirs(bug_dir, exist_ok=True)
        
        # Extract fixing commits
        fixing_result = self.extract_fixing_commits(bug_id, bug_dir, analysis)
        
        if fixing_result['total_commits'] == 0:
            print(f"    No fixing commits could be extracted for Bug {bug_id}")
            try:
                shutil.rmtree(bug_dir)
            except:
                pass
            return {
                'bug_id': bug_id,
                'success': False,
                'reason': 'No fixing commits extracted',
                'fixing_commits': 0,
                'fixing_files': 0,
                'regressor_commits': 0,
                'regressor_files': 0
            }
        
        # Extract regressor commits
        regression_chain = analysis.get('regression_chain', {})
        regressor_result = self.extract_regressor_commits(bug_id, bug_dir, regression_chain)
        
        # FILTER: Skip if bug has regression but no matching commits
        if (regression_chain.get('has_regression') and 
            regressor_result['total_commits'] == 0):
            print(f"\n  ⚠ FILTERING OUT Bug {bug_id}:")
            print(f"    Reason: Has regression but no regressor commits with file overlap")
            print(f"    (Regression exists but modifies different files)")
            
            try:
                shutil.rmtree(bug_dir)
            except:
                pass
            
            return {
                'bug_id': bug_id,
                'success': False,
                'reason': 'Regression exists but no file overlap with fix',
                'fixing_commits': 0,
                'fixing_files': 0,
                'regressor_commits': 0,
                'regressor_files': 0
            }
        
        result = {
            'bug_id': bug_id,
            'success': True,
            'fixing_commits': fixing_result['total_commits'],
            'fixing_files': fixing_result['total_files'],
            'regressor_commits': regressor_result['total_commits'],
            'regressor_files': regressor_result['total_files']
        }
        
        return result
    
    def save_bug_metadata(self, bug_dir: str, bug_id: str, 
                         analysis: Dict, extraction_result: Dict) -> bool:
        """
        Save metadata about the bug and extraction
        
        Args:
            bug_dir: Bug directory
            bug_id: Bug ID
            analysis: Original analysis
            extraction_result: Extraction results
            
        Returns:
            True if successful
        """
        try:
            metadata = {
                'bug_id': bug_id,
                'summary': analysis.get('summary', 'No summary'),
                'extraction_timestamp': datetime.now().isoformat(),
                'extraction_result': extraction_result,
                'fixing_commits_count': extraction_result['fixing_commits'],
                'regressor_commits_count': extraction_result['regressor_commits'],
                'has_regression': analysis.get('regression_chain', {}).get('has_regression', False)
            }
            
            metadata_file = os.path.join(bug_dir, 'bug_metadata.json')
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
            
            return True
        except Exception as e:
            print(f"    Error saving bug metadata: {e}")
            return False
    
    def extract_from_analysis(self, regression_analysis_file: str) -> Dict:
        """
        Extract code from regression analysis results
        
        Args:
            regression_analysis_file: Path to Step 3 JSON output
            
        Returns:
            Complete extraction summary
        """
        print("\n" + "="*80)
        print("STEP 4: CODE EXTRACTION WITH FILE-LEVEL GRANULARITY")
        print("="*80)
        
        # Load regression analysis
        print(f"\nLoading regression analysis from: {regression_analysis_file}")
        with open(regression_analysis_file, 'r') as f:
            regression_data = json.load(f)
        
        signature = regression_data.get('signature', 'Unknown')
        regression_analyses = regression_data.get('regression_analyses', {})
        
        print(f"Signature: {signature}")
        print(f"Total bugs to process: {len(regression_analyses)}")
        print(f"Output directory: {self.output_dir}/")
        
        # Extract code for each bug
        results = []
        successful_bugs = 0
        filtered_bugs = 0
        failed_bugs = 0
        total_fixing_commits = 0
        total_fixing_files = 0
        total_regressor_commits = 0
        total_regressor_files = 0
        
        for bug_id, analysis in regression_analyses.items():
            result = self.extract_bug_code(bug_id, analysis)
            results.append(result)
            
            if result['success']:
                successful_bugs += 1
                total_fixing_commits += result['fixing_commits']
                total_fixing_files += result['fixing_files']
                total_regressor_commits += result['regressor_commits']
                total_regressor_files += result['regressor_files']
            elif result['reason'] == 'Regression exists but no file overlap with fix':
                filtered_bugs += 1
            else:
                failed_bugs += 1
        
        print(f"\n{'='*80}")
        print("EXTRACTION COMPLETE")
        print(f"{'='*80}")
        print(f"Total bugs processed: {len(regression_analyses)}")
        print(f"Successful extractions: {successful_bugs}")
        print(f"Filtered out (no file overlap): {filtered_bugs}")
        print(f"Failed extractions: {failed_bugs}")
        print(f"\nFixing commits: {total_fixing_commits} ({total_fixing_files} files)")
        print(f"Regressor commits: {total_regressor_commits} ({total_regressor_files} files)")
        print(f"\nAll diffs saved to: {self.output_dir}/")
        
        return {
            'signature': signature,
            'extraction_timestamp': datetime.now().isoformat(),
            'output_directory': self.output_dir,
            'summary': {
                'total_bugs': len(regression_analyses),
                'successful_bugs': successful_bugs,
                'filtered_bugs': filtered_bugs,
                'failed_bugs': failed_bugs,
                'total_fixing_commits': total_fixing_commits,
                'total_fixing_files': total_fixing_files,
                'total_regressor_commits': total_regressor_commits,
                'total_regressor_files': total_regressor_files
            },
            'bug_results': results
        }
    
    def save_summary(self, results: Dict) -> str:
        """
        Save extraction summary to a text file
        
        Args:
            results: Results dictionary
            
        Returns:
            Summary filename
        """
        #timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = os.path.join(self.output_dir, f"extraction_summary.txt")
        
        try:
            with open(filename, 'w') as f:
                f.write("="*80 + "\n")
                f.write("CODE EXTRACTION SUMMARY (FILE-LEVEL GRANULARITY)\n")
                f.write("="*80 + "\n\n")
                f.write(f"Signature: {results['signature']}\n")
                f.write(f"Timestamp: {results['extraction_timestamp']}\n")
                f.write(f"Output Directory: {results['output_directory']}/\n\n")
                
                f.write(f"Total Bugs: {results['summary']['total_bugs']}\n")
                f.write(f"Successful: {results['summary']['successful_bugs']}\n")
                f.write(f"Filtered (no file overlap): {results['summary']['filtered_bugs']}\n")
                f.write(f"Failed: {results['summary']['failed_bugs']}\n\n")
                f.write(f"Total Fixing Commits: {results['summary']['total_fixing_commits']}\n")
                f.write(f"Total Fixing Files: {results['summary']['total_fixing_files']}\n")
                f.write(f"Total Regressor Commits: {results['summary']['total_regressor_commits']}\n")
                f.write(f"Total Regressor Files: {results['summary']['total_regressor_files']}\n\n")
                
                f.write("="*80 + "\n")
                f.write("PER-BUG RESULTS\n")
                f.write("="*80 + "\n\n")
                
                for bug_result in results['bug_results']:
                    f.write(f"Bug {bug_result['bug_id']}:\n")
                    if bug_result['success']:
                        f.write(f"  ✓ Success\n")
                        f.write(f"  Fixing: {bug_result['fixing_commits']} commits, {bug_result['fixing_files']} files\n")
                        f.write(f"  Regressor: {bug_result['regressor_commits']} commits, {bug_result['regressor_files']} files\n")
                    else:
                        f.write(f"  ✗ Filtered/Failed\n")
                        f.write(f"  Reason: {bug_result.get('reason', 'Unknown')}\n")
                    f.write("\n")
                
                f.write("="*80 + "\n")
                f.write("FILTERED BUGS (Regression but no file overlap)\n")
                f.write("="*80 + "\n\n")
                
                filtered = [b for b in results['bug_results'] 
                           if not b['success'] and b.get('reason') == 'Regression exists but no file overlap with fix']
                
                if filtered:
                    for bug in filtered:
                        f.write(f"  • Bug {bug['bug_id']}\n")
                else:
                    f.write("  (None)\n")
                
                f.write("\n" + "="*80 + "\n")
                f.write("DIRECTORY STRUCTURE (Only successful bugs)\n")
                f.write("="*80 + "\n\n")
                f.write("extracted_diffs/\n")
                f.write("├── bug_XXXXX/\n")
                f.write("│   ├── fixing_commits/\n")
                f.write("│   │   ├── commit_hash_1/\n")
                f.write("│   │   │   ├── file1.py.diff (with metadata header)\n")
                f.write("│   │   │   └── file2.cpp.diff (with metadata header)\n")
                f.write("│   │   └── commit_hash_2/\n")
                f.write("│   │       └── ...\n")
                f.write("│   └── regressor_commits/\n")
                f.write("│       ├── regressor_BUGID_commit_hash_1/\n")
                f.write("│       │   ├── file1.py.diff (with metadata header)\n")
                f.write("│       │   └── file3.js.diff (with metadata header)\n")
                f.write("│       └── regressor_BUGID_commit_hash_2/\n")
                f.write("│           └── ...\n")
            
            print(f"\nSummary saved to: {filename}")
            return filename
        except Exception as e:
            print(f"\nFailed to save summary: {e}")
            return None


def main():
    """Main execution function"""
    
    # Define local repositories (same as Step 1)
    local_repos = {
        'autoland': './mozilla-autoland',
        'central': './mozilla-central',
        'release': './mozilla-release',
        'esr115': './mozilla-esr115'
    }
    
    # Initialize extractor with local repos
    extractor = CodeExtractor(
        output_dir="step4_extracted_diffs",
        local_repos=local_repos
    )
    
    # Path to Step 3 output
    regression_analysis_file = "step3_regression_analysis_OOM | small.json"
    
    # Extract code with file-level granularity
    results = extractor.extract_from_analysis(regression_analysis_file)
    
    # Save summary
    extractor.save_summary(results)
    
    # Print detailed summary
    print("\n" + "="*80)
    print("EXAMPLE DIRECTORY STRUCTURE (Successful bugs only)")
    print("="*80)
    
    successful = [r for r in results['bug_results'] if r['success']]
    for bug_result in successful[:3]:  # Show first 3 examples
        bug_id = bug_result['bug_id']
        print(f"\nextracted_diffs/bug_{bug_id}/")
        print(f"  ├── fixing_commits/")
        print(f"  │   └── [commit directories with .diff files]")
        print(f"  │       (each .diff file has metadata header)")
        print(f"  └── regressor_commits/")
        print(f"      └── [commit directories with .diff files]")
        print(f"          (each .diff file has metadata header)")
    
    print("\n" + "="*80)
    print("DONE! Ready for file-level comparison analysis.")
    print("="*80)
    print(f"\nAll diffs saved in: {extractor.output_dir}/")
    print(f"Successful bugs: {results['summary']['successful_bugs']}")
    print(f"Filtered bugs: {results['summary']['filtered_bugs']}")


if __name__ == "__main__":
    main()