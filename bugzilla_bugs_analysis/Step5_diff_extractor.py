#!/usr/bin/env python3
"""
================================================================================
STEP 5: CODE DIFF EXTRACTION
================================================================================

PURPOSE:
--------
Extract actual code diffs for bugs from Step 4 output:
- Fixing commit diff (the code that fixed the bug)
- Regressor commit diffs (the code that introduced the bug)

INPUT:
------
- Step 4 output: outputs/step4_single_commit_regressor_match/
    └── bugs_with_single_commit_regressor_commit/bugs/*.json

OUTPUT:
-------
outputs/step5_extracted_diffs/
├── bug_<ID>/
│   ├── bug_metadata.json
│   ├── fixing_commit/
│   │   └── <short_hash>/
│   │       ├── file1.cpp.diff
│   │       └── file2.h.diff
│   └── regressor_commits/
│       └── regressor_<bug_id>_<short_hash>/
│           ├── file1.cpp.diff
│           └── file2.h.diff
├── extraction_summary.json
└── statistics_report.txt
"""

import json
import requests
import subprocess
from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import os
import re
import shutil
import sys
import argparse
from pathlib import Path

script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))

os.chdir(parent_dir)
print(f"Changed working directory to: {parent_dir}")


class DiffExtractor:
    """Extracts code diffs from commits with file-level granularity"""
    
    def __init__(self, max_workers: int = 8, rate_limit_delay: float = 0.2):
        self.max_workers = max_workers
        self.rate_limit_delay = rate_limit_delay
        self.session = requests.Session()
        
        # Thread safety
        self._rate_lock = threading.Lock()
        self._last_request_time = 0
        self._progress_lock = threading.Lock()
        self._print_lock = threading.Lock()
        self._processed_bugs = 0
        self._total_bugs = 0
        
        # Paths
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Step 4 output
        self.input_dir = self.outputs_base / "step4_single_commit_regressor_match" / "bugs_with_single_commit_regressor_commit" / "bugs"
        
        # OUTPUT directory
        self.output_base = self.outputs_base / "step5_extracted_diffs"
        self.output_base.mkdir(parents=True, exist_ok=True)
        
        # Local repositories
        self.local_repos = {
            'mozilla-central': './mozilla-central',
            'mozilla-autoland': './mozilla-autoland',
            'mozilla-release': './mozilla-release',
            'mozilla-esr115': './mozilla-esr115'
        }
        
        # Remote repositories to try
        self.remote_repos = [
            'mozilla-central',
            'integration/autoland',
            'releases/mozilla-release',
            'releases/mozilla-beta',
            'releases/mozilla-esr128',
            'releases/mozilla-esr115'
        ]
        
        print(f"Input directory (Step 4 output):")
        print(f"  {self.input_dir}")
        print(f"Output directory: {self.output_base}")
        print(f"Max workers: {self.max_workers}")
        print(f"\nLocal repositories:")
        for name, path in self.local_repos.items():
            exists = os.path.exists(path)
            print(f"  {name}: {path} {'✓' if exists else '✗'}")
        print()
    
    def _safe_print(self, message: str):
        with self._print_lock:
            print(message)
    
    def _rate_limited_request(self, url: str, timeout: int = 30) -> Optional[requests.Response]:
        """Make a rate-limited HTTP request"""
        with self._rate_lock:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.rate_limit_delay:
                time.sleep(self.rate_limit_delay - elapsed)
            self._last_request_time = time.time()
        
        try:
            return self.session.get(url, timeout=timeout)
        except requests.exceptions.RequestException:
            return None
    
    def get_commit_diff(self, commit_hash: str) -> Optional[str]:
        """Fetch the diff for a specific commit"""
        # Try local repositories first
        for repo_name, repo_path in self.local_repos.items():
            if not os.path.exists(repo_path):
                continue
            try:
                result = subprocess.run(
                    ['hg', 'diff', '-c', commit_hash],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout
            except Exception:
                continue
        
        # Fall back to remote
        for repo in self.remote_repos:
            url = f"https://hg.mozilla.org/{repo}/raw-rev/{commit_hash}"
            response = self._rate_limited_request(url)
            if response and response.status_code == 200 and response.text.strip():
                return response.text
        
        return None
    
    def parse_diff_by_file(self, diff_content: str) -> Dict[str, str]:
        """Parse a unified diff and split it by file"""
        files_dict = {}
        current_file = None
        current_diff = []
        
        for line in diff_content.split('\n'):
            if line.startswith('diff --git') or line.startswith('diff -r'):
                if current_file and current_diff:
                    files_dict[current_file] = '\n'.join(current_diff)
                
                match = re.search(r'b/(.+?)(?:\s|$)', line)
                current_file = match.group(1) if match else "unknown"
                current_diff = [line]
            
            elif line.startswith('---') and current_file:
                match = re.search(r'---\s+[ab]/(.+?)(?:\s|$)', line)
                if match:
                    current_file = match.group(1)
                current_diff.append(line)
            else:
                if current_file:
                    current_diff.append(line)
        
        if current_file and current_diff:
            files_dict[current_file] = '\n'.join(current_diff)
        
        return files_dict
    
    def create_file_header(self, commit_info: Dict, filepath: str, commit_type: str) -> str:
        """Create a header with metadata"""
        header = "# " + "=" * 78 + "\n"
        header += f"# {commit_type.upper()} COMMIT DIFF\n"
        header += "# " + "=" * 78 + "\n"
        header += f"# File: {filepath}\n"
        header += f"# Commit: {commit_info.get('short_hash', 'Unknown')}\n"
        header += f"# Full Hash: {commit_info.get('commit_hash', 'Unknown')}\n"
        header += f"# Author: {commit_info.get('author', 'Unknown')}\n"
        header += f"# Date: {commit_info.get('pushdate', 'Unknown')}\n"
        
        if commit_type == 'regressor':
            header += f"# Regressor Bug: {commit_info.get('regressor_bug_id', 'Unknown')}\n"
            header += f"# File Overlap Count: {commit_info.get('file_overlap_count', 0)}\n"
            if commit_info.get('overlapping_files'):
                header += f"# Overlapping Files: {', '.join(commit_info['overlapping_files'][:5])}\n"
        
        header += f"# Description:\n"
        description = commit_info.get('description', 'No description')
        for line in description.split('\n')[:5]:
            header += f"#   {line}\n"
        
        header += "# " + "=" * 78 + "\n\n"
        return header
    
    def extract_commit_files(self, commit_dir: Path, commit_info: Dict, commit_type: str) -> int:
        """Extract and save individual file diffs for a commit"""
        commit_hash = commit_info.get('commit_hash', '')
        
        diff_content = self.get_commit_diff(commit_hash)
        if not diff_content:
            return 0
        
        files_dict = self.parse_diff_by_file(diff_content)
        if not files_dict:
            return 0
        
        commit_dir.mkdir(parents=True, exist_ok=True)
        
        files_saved = 0
        for filepath, file_diff in files_dict.items():
            safe_filename = filepath.replace('/', '_').replace('\\', '_')
            if not safe_filename.endswith('.diff'):
                safe_filename += '.diff'
            
            diff_file_path = commit_dir / safe_filename
            
            try:
                header = self.create_file_header(commit_info, filepath, commit_type)
                with open(diff_file_path, 'w', encoding='utf-8') as f:
                    f.write(header)
                    f.write(file_diff)
                files_saved += 1
            except Exception:
                pass
        
        return files_saved
    
    def extract_fixing_commit(self, bug_dir: Path, fixing_commit: Dict) -> Dict:
        """Extract the single fixing commit"""
        if not fixing_commit:
            return {'success': False, 'files': 0}
        
        short_hash = fixing_commit.get('short_hash', fixing_commit.get('commit_hash', '')[:12])
        commit_dir = bug_dir / 'fixing_commit' / short_hash
        
        file_count = self.extract_commit_files(commit_dir, fixing_commit, 'fixing')
        
        return {'success': file_count > 0, 'files': file_count}
    
    def extract_regressor_commits(self, bug_dir: Path, regressor_analysis: List[Dict]) -> Dict:
        """Extract all matching regressor commits"""
        if not regressor_analysis:
            return {'success': False, 'commits': 0, 'files': 0}
        
        regressor_dir = bug_dir / 'regressor_commits'
        total_files = 0
        total_commits = 0
        
        for reg_detail in regressor_analysis:
            regressor_bug_id = reg_detail.get('regressor_bug_id', 'unknown')
            matching_commits = reg_detail.get('matching_commits', [])
            
            for commit in matching_commits:
                short_hash = commit.get('short_hash', commit.get('commit_hash', '')[:12])
                commit_dir_name = f"regressor_{regressor_bug_id}_{short_hash}"
                commit_dir = regressor_dir / commit_dir_name
                
                # Add regressor bug ID to commit info for header
                commit_with_meta = {**commit, 'regressor_bug_id': regressor_bug_id}
                
                file_count = self.extract_commit_files(commit_dir, commit_with_meta, 'regressor')
                
                if file_count > 0:
                    total_files += file_count
                    total_commits += 1
        
        return {'success': total_commits > 0, 'commits': total_commits, 'files': total_files}
    
    def save_bug_metadata(self, bug_dir: Path, bug_data: Dict, 
                          fixing_result: Dict, regressor_result: Dict):
        """Save metadata about the bug"""
        metadata = {
            'bug_id': bug_data.get('bug_id'),
            'summary': bug_data.get('summary', ''),
            'product': bug_data.get('product', ''),
            'component': bug_data.get('component', ''),
            'status': bug_data.get('status', ''),
            'regressed_by': bug_data.get('regressed_by', []),
            'extraction_timestamp': datetime.now().isoformat(),
            'fixing_commit': {
                'hash': bug_data.get('fixing_commit', {}).get('short_hash'),
                'files_extracted': fixing_result['files']
            },
            'regressor_commits': {
                'count': regressor_result['commits'],
                'files_extracted': regressor_result['files']
            },
            'total_matching_regressor_commits': bug_data.get('total_matching_regressor_commits', 0)
        }
        
        with open(bug_dir / 'bug_metadata.json', 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
    
    def process_single_bug(self, bug_id: str, bug_data: Dict) -> Dict:
        """Process a single bug - extract all diffs"""
        bug_dir = self.output_base / f"bug_{bug_id}"
        
        # Extract fixing commit
        fixing_commit = bug_data.get('fixing_commit', {})
        fixing_result = self.extract_fixing_commit(bug_dir, fixing_commit)
        
        if not fixing_result['success']:
            # Clean up if fixing commit failed
            if bug_dir.exists():
                shutil.rmtree(bug_dir, ignore_errors=True)
            return {
                'bug_id': bug_id,
                'success': False,
                'reason': 'Failed to extract fixing commit diff',
                'fixing_files': 0,
                'regressor_commits': 0,
                'regressor_files': 0
            }
        
        # Extract regressor commits
        regressor_analysis = bug_data.get('regressor_analysis', [])
        regressor_result = self.extract_regressor_commits(bug_dir, regressor_analysis)
        
        # Save metadata
        self.save_bug_metadata(bug_dir, bug_data, fixing_result, regressor_result)
        
        # Update progress
        with self._progress_lock:
            self._processed_bugs += 1
            progress = (self._processed_bugs / self._total_bugs) * 100
            self._safe_print(
                f"  [{self._processed_bugs}/{self._total_bugs}] ({progress:.1f}%) "
                f"Bug {bug_id}: {fixing_result['files']} fixing files, "
                f"{regressor_result['commits']} regressor commits ({regressor_result['files']} files)"
            )
        
        return {
            'bug_id': bug_id,
            'success': True,
            'fixing_files': fixing_result['files'],
            'regressor_commits': regressor_result['commits'],
            'regressor_files': regressor_result['files']
        }
    
    def load_input_bugs(self) -> Dict[str, Dict]:
        """Load bugs from Step 4 output"""
        bugs = {}
        
        if not self.input_dir.exists():
            print(f"  ERROR: Input directory not found: {self.input_dir}")
            print(f"  Please run Step 4 first!")
            return bugs
        
        for filepath in self.input_dir.glob("bug_*.json"):
            try:
                with open(filepath, 'r') as f:
                    bug_data = json.load(f)
                    bug_id = str(bug_data.get('bug_id', ''))
                    if bug_id:
                        bugs[bug_id] = bug_data
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  Warning: Failed to load {filepath}: {e}")
        
        return bugs
    
    def extract_all_diffs(self) -> Dict:
        """Main extraction process"""
        print("=" * 80)
        print("STEP 5: CODE DIFF EXTRACTION")
        print("=" * 80 + "\n")
        
        # Load input bugs
        print("Loading bugs from Step 4 output...")
        all_bugs = self.load_input_bugs()
        
        if not all_bugs:
            print("\nERROR: No bugs found. Please run Step 4 first.")
            return {'error': 'No input bugs found', 'summary': {}}
        
        self._total_bugs = len(all_bugs)
        self._processed_bugs = 0
        
        print(f"  Loaded {self._total_bugs} bugs with matched regressors\n")
        
        # Show sample
        sample = list(all_bugs.items())[:3]
        print("Sample bugs:")
        for bug_id, data in sample:
            fix = data.get('fixing_commit', {})
            reg_count = data.get('total_matching_regressor_commits', 0)
            print(f"  Bug {bug_id}: fix={fix.get('short_hash', 'N/A')}, {reg_count} regressor commits")
        
        print(f"\n{'='*80}")
        print(f"EXTRACTING DIFFS (using {self.max_workers} workers)")
        print(f"{'='*80}\n")
        
        start_time = time.time()
        results = []
        
        # Process in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_bug = {
                executor.submit(self.process_single_bug, bug_id, bug_data): bug_id
                for bug_id, bug_data in all_bugs.items()
            }
            
            for future in as_completed(future_to_bug):
                bug_id = future_to_bug[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    self._safe_print(f"  ERROR processing bug {bug_id}: {e}")
                    results.append({
                        'bug_id': bug_id,
                        'success': False,
                        'reason': str(e),
                        'fixing_files': 0,
                        'regressor_commits': 0,
                        'regressor_files': 0
                    })
        
        elapsed = time.time() - start_time
        
        # Calculate summary
        successful = [r for r in results if r['success']]
        failed = [r for r in results if not r['success']]
        
        total_fixing_files = sum(r['fixing_files'] for r in successful)
        total_regressor_commits = sum(r['regressor_commits'] for r in successful)
        total_regressor_files = sum(r['regressor_files'] for r in successful)
        
        # Build results
        extraction_results = {
            'extraction_timestamp': datetime.now().isoformat(),
            'input_source': str(self.input_dir),
            'output_directory': str(self.output_base),
            'elapsed_seconds': round(elapsed, 1),
            'workers_used': self.max_workers,
            'summary': {
                'total_input_bugs': self._total_bugs,
                'successful_extractions': len(successful),
                'failed_extractions': len(failed),
                'total_fixing_files': total_fixing_files,
                'total_regressor_commits': total_regressor_commits,
                'total_regressor_files': total_regressor_files
            },
            'successful_bugs': [r['bug_id'] for r in successful],
            'failed_bugs': [{'bug_id': r['bug_id'], 'reason': r.get('reason', 'Unknown')} for r in failed],
            'detailed_results': results
        }
        
        self._print_summary(extraction_results, elapsed)
        
        return extraction_results
    
    def _print_summary(self, results: Dict, elapsed: float):
        """Print extraction summary"""
        summary = results['summary']
        
        print(f"\n{'='*80}")
        print("EXTRACTION COMPLETE")
        print(f"{'='*80}")
        
        print(f"\nTime elapsed: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"\nBugs processed: {summary['total_input_bugs']}")
        print(f"  ✓ Successful: {summary['successful_extractions']}")
        print(f"  ✗ Failed: {summary['failed_extractions']}")
        
        print(f"\nCode extracted:")
        print(f"  Fixing commits: {summary['successful_extractions']} bugs ({summary['total_fixing_files']} files)")
        print(f"  Regressor commits: {summary['total_regressor_commits']} commits ({summary['total_regressor_files']} files)")
        
        if results['failed_bugs']:
            print(f"\nFailed bugs:")
            for fail in results['failed_bugs'][:5]:
                print(f"  Bug {fail['bug_id']}: {fail['reason']}")
            if len(results['failed_bugs']) > 5:
                print(f"  ... and {len(results['failed_bugs']) - 5} more")
    
    def save_results(self, results: Dict):
        """Save extraction results"""
        print(f"\n{'='*80}")
        print("SAVING RESULTS")
        print(f"{'='*80}\n")
        
        # Save summary JSON
        summary_path = self.output_base / "extraction_summary.json"
        with open(summary_path, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"✓ Saved extraction summary to {summary_path}")
        
        # Save statistics report
        stats_path = self.output_base / "statistics_report.txt"
        self._save_statistics_report(results, stats_path)
        print(f"✓ Saved statistics report to {stats_path}")
    
    def _save_statistics_report(self, results: Dict, output_path: Path):
        """Save human-readable statistics report"""
        with open(output_path, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("STEP 5: CODE DIFF EXTRACTION REPORT\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Generated: {results['extraction_timestamp']}\n")
            f.write(f"Input: {results['input_source']}\n")
            f.write(f"Output: {results['output_directory']}\n")
            f.write(f"Time elapsed: {results['elapsed_seconds']} seconds\n")
            f.write(f"Workers used: {results['workers_used']}\n\n")
            
            summary = results['summary']
            
            f.write("=" * 80 + "\n")
            f.write("SUMMARY\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Total input bugs: {summary['total_input_bugs']}\n")
            f.write(f"Successful extractions: {summary['successful_extractions']}\n")
            f.write(f"Failed extractions: {summary['failed_extractions']}\n\n")
            
            f.write(f"Code extracted:\n")
            f.write(f"  Fixing files: {summary['total_fixing_files']}\n")
            f.write(f"  Regressor commits: {summary['total_regressor_commits']}\n")
            f.write(f"  Regressor files: {summary['total_regressor_files']}\n\n")
            
            if results['failed_bugs']:
                f.write("=" * 80 + "\n")
                f.write("FAILED EXTRACTIONS\n")
                f.write("=" * 80 + "\n\n")
                
                for fail in results['failed_bugs']:
                    f.write(f"Bug {fail['bug_id']}: {fail['reason']}\n")
            
            f.write("\n" + "=" * 80 + "\n")
            f.write("SUCCESSFUL EXTRACTIONS\n")
            f.write("=" * 80 + "\n\n")
            
            for detail in results['detailed_results'][:30]:
                if detail['success']:
                    f.write(f"Bug {detail['bug_id']}:\n")
                    f.write(f"  Fixing files: {detail['fixing_files']}\n")
                    f.write(f"  Regressor commits: {detail['regressor_commits']} ({detail['regressor_files']} files)\n\n")


def main():
    parser = argparse.ArgumentParser(description='Extract code diffs from Step 4 output')
    parser.add_argument('--workers', type=int, default=8,
                        help='Number of parallel workers (default: 8)')
    parser.add_argument('--rate-limit', type=float, default=0.2,
                        help='Delay between remote requests in seconds (default: 0.2)')
    args = parser.parse_args()
    
    extractor = DiffExtractor(
        max_workers=args.workers,
        rate_limit_delay=args.rate_limit
    )
    
    results = extractor.extract_all_diffs()
    
    if 'error' not in results:
        extractor.save_results(results)
        
        print("\n" + "=" * 80)
        print("✓ STEP 5 COMPLETE")
        print("=" * 80)
        print(f"\nOutput: {extractor.output_base}")
        print(f"\nEach bug folder contains:")
        print(f"  - bug_metadata.json")
        print(f"  - fixing_commit/<hash>/*.diff")
        print(f"  - regressor_commits/regressor_<bug>_<hash>/*.diff")
    else:
        print("\n" + "=" * 80)
        print("✗ STEP 5 FAILED")
        print("=" * 80)
        print(f"\nError: {results.get('error')}")
        print(f"\nPlease run Step 4 first.")


if __name__ == "__main__":
    main()