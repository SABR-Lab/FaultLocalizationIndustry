#!/usr/bin/env python3
"""
Step 5: Extract Overlapping Files with Diff Paths (WITH DEBUGGING)
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Set
from pathlib import Path
import re


class OverlappingFilesExtractor:
    """Extract overlapping files with their diff paths"""
    
    def __init__(self, extracted_diffs_dir: str = "extracted_diffs", 
                 output_dir: str = "overlapping_files_output"):
        """
        Initialize the extractor
        
        Args:
            extracted_diffs_dir: Directory containing Step 4 extracted diffs
            output_dir: Directory to save results
        """
        self.extracted_diffs_dir = extracted_diffs_dir
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        # Add debug tracking
        self.debug_info = []
    
    def normalize_filename(self, filename: str) -> str:
        """
        Normalize a filename by removing .diff extension and converting underscores
        
        Args:
            filename: Original filename
            
        Returns:
            Normalized filename
        """
        # Remove .diff extension
        if filename.endswith('.diff'):
            filename = filename[:-5]
        
        # Convert underscores back to slashes for path comparison
        return filename.replace('_', '/')
    
    def get_files_from_commit_dir(self, commit_dir: str) -> Dict[str, str]:
        """
        Extract files from a commit directory with their actual paths
        
        Args:
            commit_dir: Path to commit directory
            
        Returns:
            Dictionary mapping normalized filename to actual diff file path
        """
        files_map = {}
        
        if not os.path.exists(commit_dir):
            return files_map
        
        for diff_file in os.listdir(commit_dir):
            if diff_file.endswith('.diff'):
                diff_path = os.path.join(commit_dir, diff_file)
                
                # Try to get the original filename from the diff header
                original_filename = None
                try:
                    with open(diff_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.startswith('# File: '):
                                original_filename = line.replace('# File: ', '').strip()
                                break
                            # Stop reading after header section
                            if line.startswith('diff --git') or line.startswith('diff -r'):
                                break
                except Exception as e:
                    print(f"    Warning: Could not read {diff_path}: {e}")
                
                # If we found it in the header, use that; otherwise normalize the filename
                if original_filename:
                    files_map[original_filename] = diff_path
                else:
                    normalized = self.normalize_filename(diff_file)
                    files_map[normalized] = diff_path
        
        return files_map
    
    def extract_commit_metadata(self, diff_path: str) -> Dict:
        """
        Extract metadata from a diff file header
        
        Args:
            diff_path: Path to diff file
            
        Returns:
            Dictionary with metadata
        """
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
                    
                    # Stop after header
                    if line.startswith('diff --git') or line.startswith('diff -r'):
                        break
                
                if desc_lines:
                    metadata['description'] = '\n'.join(desc_lines)
        except Exception as e:
            print(f"    Warning: Could not extract metadata from {diff_path}: {e}")
        
        return metadata
    
    def analyze_bug_overlaps(self, bug_id: str, bug_dir: str) -> Dict:
        """
        Analyze overlaps for a bug and extract only overlapping files
        
        Args:
            bug_id: Bug ID
            bug_dir: Path to bug directory
            
        Returns:
            Dictionary with filtered overlapping data
        """
        fixing_dir = os.path.join(bug_dir, 'fixing_commits')
        regressor_dir = os.path.join(bug_dir, 'regressor_commits')
        
        debug_entry = {
            'bug_id': bug_id,
            'fixing_dir_exists': os.path.exists(fixing_dir),
            'regressor_dir_exists': os.path.exists(regressor_dir),
            'reason': None
        }
        
        # Check if both directories exist
        if not os.path.exists(fixing_dir):
            debug_entry['reason'] = 'fixing_dir does not exist'
            self.debug_info.append(debug_entry)
            print(f"    ✗ SKIPPED: fixing_commits directory not found")
            return None
            
        if not os.path.exists(regressor_dir):
            debug_entry['reason'] = 'regressor_dir does not exist'
            self.debug_info.append(debug_entry)
            print(f"    ✗ SKIPPED: regressor_commits directory not found")
            return None
        
        # Collect all files from fixing commits with their paths
        fixing_commits_data = {}
        
        for commit_hash in os.listdir(fixing_dir):
            commit_path = os.path.join(fixing_dir, commit_hash)
            if os.path.isdir(commit_path):
                files_map = self.get_files_from_commit_dir(commit_path)
                if files_map:
                    fixing_commits_data[commit_hash] = files_map
        
        debug_entry['fixing_commits_count'] = len(fixing_commits_data)
        debug_entry['fixing_files_total'] = sum(len(f) for f in fixing_commits_data.values())
        
        if not fixing_commits_data:
            debug_entry['reason'] = 'no files in fixing commits'
            self.debug_info.append(debug_entry)
            print(f"    ✗ SKIPPED: No files found in fixing commits")
            return None
        
        # Collect all files from regressor commits with their paths
        regressor_commits_data = {}
        
        for regressor_commit_dir in os.listdir(regressor_dir):
            commit_path = os.path.join(regressor_dir, regressor_commit_dir)
            if os.path.isdir(commit_path):
                files_map = self.get_files_from_commit_dir(commit_path)
                if files_map:
                    # Extract regressor bug ID and commit hash
                    parts = regressor_commit_dir.split('_')
                    if len(parts) >= 3:
                        regressor_bug_id = parts[1]
                        regressor_hash = '_'.join(parts[2:])
                    else:
                        regressor_bug_id = "unknown"
                        regressor_hash = regressor_commit_dir
                    
                    regressor_commits_data[regressor_commit_dir] = {
                        'regressor_bug_id': regressor_bug_id,
                        'commit_hash': regressor_hash,
                        'files': files_map
                    }
        
        debug_entry['regressor_commits_count'] = len(regressor_commits_data)
        debug_entry['regressor_files_total'] = sum(len(d['files']) for d in regressor_commits_data.values())
        
        if not regressor_commits_data:
            debug_entry['reason'] = 'no files in regressor commits'
            self.debug_info.append(debug_entry)
            print(f"    ✗ SKIPPED: No files found in regressor commits")
            return None
        
        # Find all overlapping filenames
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
            print(f"    ✗ SKIPPED: No overlapping files")
            print(f"       Fixing files: {debug_entry['fixing_unique_files']} unique")
            print(f"       Regressor files: {debug_entry['regressor_unique_files']} unique")
            print(f"       Sample fixing: {debug_entry['fixing_files_sample']}")
            print(f"       Sample regressor: {debug_entry['regressor_files_sample']}")
            return None
        
        debug_entry['reason'] = 'success'
        self.debug_info.append(debug_entry)
        
        # Build filtered fixing commits - only overlapping files
        filtered_fixing_commits = []
        for commit_hash, files_map in fixing_commits_data.items():
            overlapping_in_commit = {}
            for filename, diff_path in files_map.items():
                if filename in overlapping_files:
                    overlapping_in_commit[filename] = diff_path
            
            if overlapping_in_commit:
                # Get metadata from one of the diff files
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
                        {
                            'filename': filename,
                            'diff_path': diff_path
                        }
                        for filename, diff_path in sorted(overlapping_in_commit.items())
                    ]
                })
        
        # Build filtered regressor commits - only overlapping files
        filtered_regressor_commits = []
        for reg_dir, reg_data in regressor_commits_data.items():
            overlapping_in_commit = {}
            for filename, diff_path in reg_data['files'].items():
                if filename in overlapping_files:
                    overlapping_in_commit[filename] = diff_path
            
            if overlapping_in_commit:
                # Get metadata from one of the diff files
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
                        {
                            'filename': filename,
                            'diff_path': diff_path
                        }
                        for filename, diff_path in sorted(overlapping_in_commit.items())
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
        """
        Extract overlapping files for all bugs
        
        Returns:
            Complete dataset with overlapping files
        """
        print("\n" + "="*80)
        print("EXTRACTING OVERLAPPING FILES WITH DIFF PATHS (DEBUG MODE)")
        print("="*80)
        
        overlapping_bugs = {}
        total_bugs_processed = 0
        bugs_with_overlaps = 0
        total_overlapping_files = 0
        
        # Process each bug directory
        for bug_dir_name in sorted(os.listdir(self.extracted_diffs_dir)):
            if bug_dir_name.startswith('bug_'):
                bug_id = bug_dir_name.replace('bug_', '')
                bug_path = os.path.join(self.extracted_diffs_dir, bug_dir_name)
                
                if os.path.isdir(bug_path):
                    total_bugs_processed += 1
                    print(f"\nProcessing Bug {bug_id}...")
                    
                    overlap_data = self.analyze_bug_overlaps(bug_id, bug_path)
                    
                    if overlap_data:
                        overlapping_bugs[bug_id] = overlap_data
                        bugs_with_overlaps += 1
                        total_overlapping_files += overlap_data['total_overlapping_files']
                        
                        print(f"  ✓ Found {overlap_data['total_overlapping_files']} overlapping files")
                        print(f"    Fixing commits: {overlap_data['summary']['fixing_commits_count']} "
                              f"({overlap_data['summary']['total_fixing_files']} files)")
                        print(f"    Regressor commits: {overlap_data['summary']['regressor_commits_count']} "
                              f"({overlap_data['summary']['total_regressor_files']} files)")
        
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"{'='*80}")
        print(f"Total bugs processed: {total_bugs_processed}")
        print(f"Bugs with overlapping files: {bugs_with_overlaps}")
        print(f"Bugs WITHOUT overlapping files: {total_bugs_processed - bugs_with_overlaps}")
        print(f"Total overlapping files across all bugs: {total_overlapping_files}")
        
        # Print debug summary
        print(f"\n{'='*80}")
        print("DEBUG: REASONS FOR SKIPPED BUGS")
        print(f"{'='*80}")
        
        skipped_bugs = [d for d in self.debug_info if d['reason'] != 'success']
        
        if skipped_bugs:
            reason_counts = {}
            for entry in skipped_bugs:
                reason = entry['reason']
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            
            for reason, count in reason_counts.items():
                print(f"  {reason}: {count} bugs")
            
            print(f"\nDetailed breakdown:")
            for entry in skipped_bugs:
                print(f"\n  Bug {entry['bug_id']}: {entry['reason']}")
                if entry.get('fixing_files_sample'):
                    print(f"    Fixing files sample: {entry['fixing_files_sample']}")
                if entry.get('regressor_files_sample'):
                    print(f"    Regressor files sample: {entry['regressor_files_sample']}")
        
        return {
            'extraction_timestamp': datetime.now().isoformat(),
            'source_directory': self.extracted_diffs_dir,
            'summary': {
                'total_bugs_processed': total_bugs_processed,
                'bugs_with_overlaps': bugs_with_overlaps,
                'total_overlapping_files': total_overlapping_files
            },
            'bugs': overlapping_bugs,
            'debug_info': self.debug_info
        }
    
    def save_results(self, results: Dict) -> str:
        """
        Save results to JSON file
        
        Args:
            results: Results dictionary
            
        Returns:
            Path to saved file
        """
        #timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(self.output_dir, f'overlapping_files.json')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved to: {output_file}")
        return output_file
    
    def save_debug_report(self, results: Dict) -> str:
        """
        Save a detailed debug report
        
        Args:
            results: Results dictionary
            
        Returns:
            Path to saved file
        """
        #timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(self.output_dir, f'debug_report.txt')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("DEBUG REPORT: OVERLAPPING FILES EXTRACTION\n")
            f.write("="*80 + "\n\n")
            
            f.write(f"Total bugs processed: {results['summary']['total_bugs_processed']}\n")
            f.write(f"Bugs with overlaps: {results['summary']['bugs_with_overlaps']}\n")
            f.write(f"Bugs skipped: {results['summary']['total_bugs_processed'] - results['summary']['bugs_with_overlaps']}\n\n")
            
            f.write("="*80 + "\n")
            f.write("SKIPPED BUGS DETAILS\n")
            f.write("="*80 + "\n\n")
            
            for entry in results['debug_info']:
                if entry['reason'] != 'success':
                    f.write(f"Bug {entry['bug_id']}:\n")
                    f.write(f"  Reason: {entry['reason']}\n")
                    f.write(f"  Fixing dir exists: {entry['fixing_dir_exists']}\n")
                    f.write(f"  Regressor dir exists: {entry['regressor_dir_exists']}\n")
                    
                    if 'fixing_commits_count' in entry:
                        f.write(f"  Fixing commits: {entry['fixing_commits_count']}\n")
                        f.write(f"  Fixing files: {entry['fixing_files_total']}\n")
                    
                    if 'regressor_commits_count' in entry:
                        f.write(f"  Regressor commits: {entry['regressor_commits_count']}\n")
                        f.write(f"  Regressor files: {entry['regressor_files_total']}\n")
                    
                    if 'fixing_unique_files' in entry:
                        f.write(f"  Unique fixing files: {entry['fixing_unique_files']}\n")
                        f.write(f"  Unique regressor files: {entry['regressor_unique_files']}\n")
                    
                    if entry.get('fixing_files_sample'):
                        f.write(f"  Fixing files sample: {entry['fixing_files_sample']}\n")
                    if entry.get('regressor_files_sample'):
                        f.write(f"  Regressor files sample: {entry['regressor_files_sample']}\n")
                    
                    f.write("\n")
        
        print(f"Debug report saved to: {output_file}")
        return output_file


def main():
    """Main execution function"""
    
    # Initialize extractor
    extractor = OverlappingFilesExtractor(
        extracted_diffs_dir="step4_extracted_diffs",
        output_dir="step5_overlapping_files_output"
    )
    
    # Extract overlapping files
    print("\nStarting extraction of overlapping files...")
    results = extractor.extract_all_overlapping_files()
    
    # Save results
    json_file = extractor.save_results(results)
    debug_file = extractor.save_debug_report(results)
    
    # Print final summary
    print("\n" + "="*80)
    print("EXTRACTION COMPLETE")
    print("="*80)
    print(f"\nJSON results: {json_file}")
    print(f"Debug report: {debug_file}")
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()