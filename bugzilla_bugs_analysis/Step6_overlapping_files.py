#!/usr/bin/env python3
"""
================================================================================
STEP 6: EXTRACT OVERLAPPING FILES WITH DIFF PATHS
================================================================================

PURPOSE:
--------
From Step 5 extracted diffs, find files that appear in BOTH:
- The fixing commit
- The regressor commits
And output only these overlapping files with paths to their diffs.

INPUT:
------
- Step 5 output: outputs/step5_extracted_diffs/bug_*/

OUTPUT:
-------
outputs/step6_overlapping_files/
├── step6_overlapping_files.json
└── step6_debug_report.txt
"""

import json
import os
from datetime import datetime
from typing import Dict, List
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))

os.chdir(parent_dir)
print(f"Changed working directory to: {parent_dir}")


class OverlappingFilesExtractor:
    """Extract overlapping files with their diff paths"""
    
    def __init__(self):
        # Paths
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Step 5 output directory (contains bug_* folders)
        self.input_dir = self.outputs_base / "step5_extracted_diffs"
        
        # OUTPUT: Step 6 output directory
        self.output_dir = self.outputs_base / "step6_overlapping_files"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Input directory (Step 5 output):")
        print(f"  {self.input_dir}")
        print(f"Output directory:")
        print(f"  {self.output_dir}")
        
        self.debug_info = []
    
    def normalize_filename(self, filename: str) -> str:
        """Normalize a filename by removing .diff extension and converting underscores"""
        if filename.endswith('.diff'):
            filename = filename[:-5]
        return filename.replace('_', '/')
    
    def get_files_from_commit_dir(self, commit_dir: Path) -> Dict[str, str]:
        """Extract files from a commit directory with their actual paths"""
        files_map = {}
        
        if not commit_dir.exists():
            return files_map
        
        for diff_file in commit_dir.iterdir():
            if diff_file.suffix == '.diff':
                original_filename = None
                
                # Try to extract original filename from diff header
                try:
                    with open(diff_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.startswith('# File: '):
                                original_filename = line.replace('# File: ', '').strip()
                                break
                            if line.startswith('diff --git') or line.startswith('diff -r'):
                                break
                except Exception as e:
                    print(f"    Warning: Could not read {diff_file}: {e}")
                
                if original_filename:
                    files_map[original_filename] = str(diff_file)
                else:
                    # Fallback: normalize the filename
                    normalized = self.normalize_filename(diff_file.name)
                    files_map[normalized] = str(diff_file)
        
        return files_map
    
    def extract_commit_metadata(self, diff_path: str) -> Dict:
        """Extract metadata from a diff file header"""
        metadata = {
            'commit_hash': 'Unknown',
            'short_hash': 'Unknown',
            'author': 'Unknown',
            'date': 'Unknown',
            'description': 'No description'
        }
        
        desc_lines = []
        in_description = False
        
        try:
            with open(diff_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('# Full Hash: '):
                        metadata['commit_hash'] = line.replace('# Full Hash: ', '').strip()
                    elif line.startswith('# Commit: '):
                        metadata['short_hash'] = line.replace('# Commit: ', '').strip()
                    elif line.startswith('# Author: '):
                        metadata['author'] = line.replace('# Author: ', '').strip()
                    elif line.startswith('# Date: '):
                        metadata['date'] = line.replace('# Date: ', '').strip()
                    elif line.startswith('# Description:'):
                        in_description = True
                        continue
                    elif line.startswith('#   ') and in_description:
                        desc_lines.append(line.replace('#   ', '').strip())
                    elif line.startswith('# ==='):
                        in_description = False
                    
                    # Stop at actual diff content
                    if line.startswith('diff --git') or line.startswith('diff -r'):
                        break
                
                if desc_lines:
                    metadata['description'] = '\n'.join(desc_lines)
        except Exception as e:
            print(f"    Warning: Could not extract metadata from {diff_path}: {e}")
        
        return metadata
    
    def analyze_bug_overlaps(self, bug_id: str, bug_dir: Path) -> Dict:
        """Analyze overlaps for a bug and extract only overlapping files"""
        fixing_dir = bug_dir / 'fixing_commit'
        regressor_dir = bug_dir / 'regressor_commits'
        
        debug_entry = {
            'bug_id': bug_id,
            'fixing_dir_exists': fixing_dir.exists(),
            'regressor_dir_exists': regressor_dir.exists(),
            'reason': None
        }
        
        if not fixing_dir.exists():
            debug_entry['reason'] = 'fixing_commit directory not found'
            self.debug_info.append(debug_entry)
            print(f"    ✗ SKIPPED: fixing_commit directory not found")
            return None
            
        if not regressor_dir.exists():
            debug_entry['reason'] = 'regressor_commits directory not found'
            self.debug_info.append(debug_entry)
            print(f"    ✗ SKIPPED: regressor_commits directory not found")
            return None
        
        # Collect all files from fixing commits
        fixing_commits_data = {}
        for commit_hash_dir in fixing_dir.iterdir():
            if commit_hash_dir.is_dir():
                files_map = self.get_files_from_commit_dir(commit_hash_dir)
                if files_map:
                    fixing_commits_data[commit_hash_dir.name] = files_map
        
        debug_entry['fixing_commits_count'] = len(fixing_commits_data)
        debug_entry['fixing_files_total'] = sum(len(f) for f in fixing_commits_data.values())
        
        if not fixing_commits_data:
            debug_entry['reason'] = 'no files in fixing commits'
            self.debug_info.append(debug_entry)
            print(f"    ✗ SKIPPED: No files found in fixing commits")
            return None
        
        # Collect all files from regressor commits
        regressor_commits_data = {}
        for regressor_commit_dir in regressor_dir.iterdir():
            if regressor_commit_dir.is_dir():
                files_map = self.get_files_from_commit_dir(regressor_commit_dir)
                if files_map:
                    # Parse directory name: regressor_<bug_id>_<hash>
                    parts = regressor_commit_dir.name.split('_')
                    if len(parts) >= 3:
                        regressor_bug_id = parts[1]
                        regressor_hash = '_'.join(parts[2:])
                    else:
                        regressor_bug_id = "unknown"
                        regressor_hash = regressor_commit_dir.name
                    
                    regressor_commits_data[regressor_commit_dir.name] = {
                        'regressor_bug_id': regressor_bug_id,
                        'commit_hash': regressor_hash,
                        'files': files_map
                    }
        
        debug_entry['regressor_commits_count'] = len(regressor_commits_data)
        debug_entry['regressor_files_total'] = sum(len(d['files']) for d in regressor_commits_data.values())
        
        if not regressor_commits_data:
            debug_entry['reason'] = 'no files in regressor commits'
            self.debug_info.append(debug_entry)
            print(f"     SKIPPED: No files found in regressor commits")
            return None
        
        # Find overlapping files
        all_fixing_files = set()
        for files_map in fixing_commits_data.values():
            all_fixing_files.update(files_map.keys())
        
        all_regressor_files = set()
        for reg_data in regressor_commits_data.values():
            all_regressor_files.update(reg_data['files'].keys())
        
        debug_entry['fixing_unique_files'] = len(all_fixing_files)
        debug_entry['regressor_unique_files'] = len(all_regressor_files)
        
        overlapping_files = all_fixing_files.intersection(all_regressor_files)
        debug_entry['overlapping_files_count'] = len(overlapping_files)
        
        if not overlapping_files:
            debug_entry['reason'] = 'no overlapping files found'
            debug_entry['fixing_files_sample'] = list(all_fixing_files)[:5]
            debug_entry['regressor_files_sample'] = list(all_regressor_files)[:5]
            self.debug_info.append(debug_entry)
            print(f"     SKIPPED: No overlapping files")
            print(f"      Fixing files: {list(all_fixing_files)[:3]}")
            print(f"      Regressor files: {list(all_regressor_files)[:3]}")
            return None
        
        debug_entry['reason'] = 'success'
        self.debug_info.append(debug_entry)
        
        # Build filtered fixing commits (only overlapping files)
        filtered_fixing_commits = []
        for commit_hash, files_map in fixing_commits_data.items():
            overlapping_in_commit = {f: p for f, p in files_map.items() if f in overlapping_files}
            
            if overlapping_in_commit:
                sample_diff = next(iter(overlapping_in_commit.values()))
                metadata = self.extract_commit_metadata(sample_diff)
                
                filtered_fixing_commits.append({
                    'commit_hash': commit_hash,
                    'full_hash': metadata['commit_hash'],
                    'author': metadata['author'],
                    'date': metadata['date'],
                    'description': metadata['description'],
                    'overlapping_file_count': len(overlapping_in_commit),
                    'files': [
                        {'filename': f, 'diff_path': p} 
                        for f, p in sorted(overlapping_in_commit.items())
                    ]
                })
        
        # Build filtered regressor commits (only overlapping files)
        filtered_regressor_commits = []
        for reg_dir, reg_data in regressor_commits_data.items():
            overlapping_in_commit = {f: p for f, p in reg_data['files'].items() if f in overlapping_files}
            
            if overlapping_in_commit:
                sample_diff = next(iter(overlapping_in_commit.values()))
                metadata = self.extract_commit_metadata(sample_diff)
                
                filtered_regressor_commits.append({
                    'regressor_bug_id': reg_data['regressor_bug_id'],
                    'commit_hash': reg_data['commit_hash'],
                    'full_hash': metadata['commit_hash'],
                    'author': metadata['author'],
                    'date': metadata['date'],
                    'description': metadata['description'],
                    'overlapping_file_count': len(overlapping_in_commit),
                    'files': [
                        {'filename': f, 'diff_path': p} 
                        for f, p in sorted(overlapping_in_commit.items())
                    ]
                })
        
        if not filtered_fixing_commits or not filtered_regressor_commits:
            return None
        
        return {
            'bug_id': bug_id,
            'total_overlapping_files': len(overlapping_files),
            'overlapping_files': sorted(list(overlapping_files)),
            'fixing_commits': filtered_fixing_commits,
            'regressor_commits': filtered_regressor_commits,
            'summary': {
                'fixing_commits_count': len(filtered_fixing_commits),
                'regressor_commits_count': len(filtered_regressor_commits),
                'total_fixing_files': sum(len(c['files']) for c in filtered_fixing_commits),
                'total_regressor_files': sum(len(c['files']) for c in filtered_regressor_commits)
            }
        }
    
    def extract_all_overlapping_files(self) -> Dict:
        """Extract overlapping files for all bugs - saves each bug separately"""
        print("\n" + "=" * 80)
        print("STEP 6: EXTRACTING OVERLAPPING FILES WITH DIFF PATHS")
        print("=" * 80 + "\n")
        
        if not self.input_dir.exists():
            print(f"ERROR: Input directory not found: {self.input_dir}")
            print("Please run Step 5 first.")
            return {'error': 'Input directory not found'}
        
        # Create bugs output directory
        bugs_output_dir = self.output_dir / 'bugs'
        bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        total_bugs_processed = 0
        bugs_with_overlaps = 0
        bugs_without_overlaps = 0
        total_overlapping_files = 0
        successful_bug_ids = []
        failed_bug_ids = []
        
        # Get all bug directories
        bug_dirs = sorted([d for d in self.input_dir.iterdir() if d.is_dir() and d.name.startswith('bug_')])
        
        print(f"Found {len(bug_dirs)} bug directories to process\n")
        
        for bug_dir in bug_dirs:
            bug_id = bug_dir.name.replace('bug_', '')
            total_bugs_processed += 1
            
            print(f"[{total_bugs_processed}/{len(bug_dirs)}] Processing Bug {bug_id}...")
            
            overlap_data = self.analyze_bug_overlaps(bug_id, bug_dir)
            
            if overlap_data:
                # Save individual bug file immediately (memory efficient)
                bug_file = bugs_output_dir / f'bug_{bug_id}.json'
                with open(bug_file, 'w', encoding='utf-8') as f:
                    json.dump(overlap_data, f, indent=2)
                
                bugs_with_overlaps += 1
                total_overlapping_files += overlap_data['total_overlapping_files']
                successful_bug_ids.append(bug_id)
                print(f"    ✓ Found {overlap_data['total_overlapping_files']} overlapping files → saved")
            else:
                bugs_without_overlaps += 1
                failed_bug_ids.append(bug_id)
        
        print(f"\n{'=' * 80}")
        print("SUMMARY")
        print(f"{'=' * 80}")
        print(f"Total bugs processed: {total_bugs_processed}")
        print(f"Bugs with overlapping files: {bugs_with_overlaps}")
        print(f"Bugs without overlapping files: {bugs_without_overlaps}")
        print(f"Total overlapping files: {total_overlapping_files}")
        
        # Return only summary (not all bug data)
        return {
            'extraction_timestamp': datetime.now().isoformat(),
            'input_directory': str(self.input_dir),
            'output_directory': str(self.output_dir),
            'summary': {
                'total_bugs_processed': total_bugs_processed,
                'bugs_with_overlaps': bugs_with_overlaps,
                'bugs_without_overlaps': bugs_without_overlaps,
                'total_overlapping_files': total_overlapping_files
            },
            'successful_bug_ids': successful_bug_ids,
            'failed_bug_ids': failed_bug_ids
        }
    
    def save_results(self, results: Dict):
        """Save summary results (individual bugs already saved during processing)"""
        print(f"\n{'=' * 80}")
        print("SAVING RESULTS")
        print(f"{'=' * 80}\n")
        
        # Save summary JSON (lightweight - no full bug data)
        summary_file = self.output_dir / 'extraction_summary.json'
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        print(f" Saved extraction summary to {summary_file}")
        
        # Save debug report
        debug_file = self.output_dir / 'debug_report.txt'
        self._save_debug_report(results, debug_file)
        print(f"✓ Saved debug report to {debug_file}")
        
        # Individual bug files already saved during processing
        bugs_dir = self.output_dir / 'bugs'
        print(f" Individual bug files saved to {bugs_dir}")
    
    def _save_debug_report(self, results: Dict, output_path: Path):
        """Save a detailed debug report"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("STEP 6: OVERLAPPING FILES DEBUG REPORT\n")
            f.write("=" * 80 + "\n\n")
            
            summary = results.get('summary', {})
            f.write(f"Total bugs processed: {summary.get('total_bugs_processed', 0)}\n")
            f.write(f"Bugs with overlaps: {summary.get('bugs_with_overlaps', 0)}\n")
            f.write(f"Bugs without overlaps: {summary.get('bugs_without_overlaps', 0)}\n")
            f.write(f"Total overlapping files: {summary.get('total_overlapping_files', 0)}\n\n")
            
            # Skipped bugs
            skipped_bugs = [d for d in self.debug_info if d['reason'] != 'success']
            if skipped_bugs:
                f.write("=" * 80 + "\n")
                f.write("SKIPPED BUGS\n")
                f.write("=" * 80 + "\n\n")
                
                for entry in skipped_bugs:
                    f.write(f"Bug {entry['bug_id']}: {entry['reason']}\n")
                    if entry.get('fixing_files_sample'):
                        f.write(f"  Fixing files sample: {entry['fixing_files_sample']}\n")
                    if entry.get('regressor_files_sample'):
                        f.write(f"  Regressor files sample: {entry['regressor_files_sample']}\n")
                    f.write("\n")
            
            # Successful bugs
            successful_bugs = [d for d in self.debug_info if d['reason'] == 'success']
            if successful_bugs:
                f.write("=" * 80 + "\n")
                f.write("SUCCESSFUL BUGS\n")
                f.write("=" * 80 + "\n\n")
                
                for entry in successful_bugs:
                    f.write(f"Bug {entry['bug_id']}:\n")
                    f.write(f"  Fixing commits: {entry.get('fixing_commits_count', 0)}\n")
                    f.write(f"  Regressor commits: {entry.get('regressor_commits_count', 0)}\n")
                    f.write(f"  Overlapping files: {entry.get('overlapping_files_count', 0)}\n\n")


def main():
    """Main execution function"""
    print("=" * 80)
    print("STEP 6: EXTRACT OVERLAPPING FILES")
    print("=" * 80 + "\n")
    
    extractor = OverlappingFilesExtractor()
    
    results = extractor.extract_all_overlapping_files()
    
    if 'error' not in results:
        extractor.save_results(results)
        
        print("\n" + "=" * 80)
        print("✓ STEP 6 COMPLETE")
        print("=" * 80)
        print(f"\nOutput: {extractor.output_dir}")
        print(f"\nResults:")
        print(f"  Bugs with overlaps: {results['summary']['bugs_with_overlaps']}")
        print(f"  Total overlapping files: {results['summary']['total_overlapping_files']}")
    else:
        print("\n" + "=" * 80)
        print("✗ STEP 6 FAILED")
        print("=" * 80)
        print(f"\nPlease run Step 5 first.")


if __name__ == "__main__":
    main()