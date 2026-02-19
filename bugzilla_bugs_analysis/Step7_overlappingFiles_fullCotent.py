#!/usr/bin/env python3
"""
================================================================================
STEP 7: EXTRACT FULL FILE CONTENTS FOR OVERLAPPING FILES (CODE FILES ONLY)
================================================================================

PURPOSE:
--------
Extract the FULL source code content from commits for overlapping files.
This gives you the code AFTER the change was made.

- For fixing commits: Get code from the commit (state after fix was applied)
- For regressor commits: Get code from the commit (state after regression was introduced)

Only extracts actual code files (skips tests, configs, docs, etc.)

INPUT:
------
- Step 6 output: outputs/step6_overlapping_files/bugs/*.json

OUTPUT:
-------
outputs/step7_full_file_contents/
├── bug_<ID>/
│   ├── <filename>__fixing_<hash>.txt
│   └── <filename>__regressor_<bug>_<hash>.txt
├── extraction_summary.json
└── extraction_report.txt
"""

import json
import os
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))

os.chdir(parent_dir)
print(f"Changed working directory to: {parent_dir}")


class FullFileExtractor:
    """Extract complete file contents from p commits (code files only)"""
    
    CODE_EXTENSIONS = {
        '.c', '.cc', '.cpp', '.cxx', '.h', '.hh', '.hpp', '.hxx',
        '.js', '.jsx', '.ts', '.tsx', '.mjs',
        '.py', '.pyx', '.pyi',
        '.java', '.rs', '.go', '.cs', '.swift', '.kt', '.kts',
        '.rb', '.php', '.m', '.mm', '.scala', '.sh', '.bash',
        '.pl', '.pm', '.html', '.htm', '.css', '.wasm', '.wat', '.vue'
    }
    
    EXCLUDE_PATTERNS = [
        '/test/', '/tests/', '/testing/',
        'test_', '_test.', 'Test.', 'Tests.',
        'mochitest', 'reftest', 'crashtest', 'gtest',
        'moz.build', 'Makefile', 'makefile', 'CMakeLists.txt',
        '.ini', '.toml', '.yaml', '.yml', '.json', '.xml',
        'configure.in', 'configure.ac',
        'README', 'CHANGELOG', 'LICENSE', 'AUTHORS',
        '.md', '.rst', '.txt',
        '.csv', '.dat', '.sql',
        'generated/', 'gen/', '__generated__',
        '.properties', '.strings',
        '.patch', '.diff', '.log'
    ]
    
    def __init__(self):
        """Initialize the extractor"""
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Step 6 output (individual bug files)
        self.input_dir = self.outputs_base / "step6_overlapping_files" / "bugs"
        
        # OUTPUT: Step 7 output
        self.output_dir = self.outputs_base / "step7_full_file_contents"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Local repositories
        self.local_repos = {
            'mozilla-central': './mozilla-central',
            'mozilla-autoland': './mozilla-autoland',
            'mozilla-release': './mozilla-release',
            'mozilla-esr115': './mozilla-esr115'
        }
        
        # Filter to available repos
        self.available_repos = {
            name: path for name, path in self.local_repos.items() 
            if os.path.exists(path)
        }
        
        print(f"Input directory (Step 6 output):")
        print(f"  {self.input_dir}")
        print(f"Output directory:")
        print(f"  {self.output_dir}")
        print(f"\nLocal repositories:")
        for name, path in self.local_repos.items():
            exists = name in self.available_repos
            print(f"  {name}: {path} {'✓' if exists else '✗'}")
        print()
    
    def is_code_file(self, filepath: str) -> bool:
        """Determine if a file is parseable source code"""
        filepath_lower = filepath.lower()
        
        for pattern in self.EXCLUDE_PATTERNS:
            if pattern in filepath_lower:
                return False
        
        file_ext = Path(filepath).suffix.lower()
        return file_ext in self.CODE_EXTENSIONS
    
    def categorize_file(self, filepath: str) -> str:
        """Categorize a file for reporting purposes"""
        filepath_lower = filepath.lower()
        
        if any(pattern in filepath_lower for pattern in ['/test/', '/tests/', 'test_', 'mochitest', 'reftest']):
            return 'test'
        elif filepath_lower.endswith(('.ini', '.toml', '.yaml', '.yml', '.json', '.xml')):
            return 'config'
        elif filepath_lower.endswith(('.md', '.rst', '.txt')) or 'README' in filepath:
            return 'documentation'
        elif 'moz.build' in filepath_lower or 'Makefile' in filepath_lower:
            return 'build'
        elif self.is_code_file(filepath):
            return 'code'
        else:
            return 'other'
    

    
    def get_file_content_from_commit(self, commit_hash: str, filepath: str) -> Optional[str]:
        """Get the full content of a file from a specific commit"""
        for repo_name, repo_path in self.available_repos.items():
            try:
                result = subprocess.run(
                    ['hg', 'cat', '-r', commit_hash, filepath],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding='utf-8',
                    errors='replace'
                )
                
                if result.returncode == 0 and result.stdout:
                    return result.stdout
                    
            except Exception:
                continue
        
        return None
    
    def load_bug_file(self, bug_file: Path) -> Optional[Dict]:
        """Load a single bug JSON file"""
        try:
            with open(bug_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  Warning: Failed to load {bug_file}: {e}")
            return None
    
    def extract_file_content(self, bug_id: str, bug_data: Dict) -> Tuple[Dict, List[Dict]]:
        """Extract full file contents for a single bug"""
        print(f"\n  Processing {len(bug_data['overlapping_files'])} overlapping files...")
        
        # Categorize files
        file_categories = {}
        for filepath in bug_data['overlapping_files']:
            file_categories[filepath] = self.categorize_file(filepath)
        
        # Count categories
        category_counts = {}
        for category in file_categories.values():
            category_counts[category] = category_counts.get(category, 0) + 1
        
        print(f"    File breakdown: {category_counts}")
        
        # Create bug output directory
        bug_dir = self.output_dir / f"bug_{bug_id}"
        bug_dir.mkdir(parents=True, exist_ok=True)
        # Separate code files from non-code files
        code_files = [f for f in bug_data['overlapping_files'] if self.is_code_file(f)]
        filtered_files = [
            {'filepath': f, 'category': file_categories[f]} 
            for f in bug_data['overlapping_files'] if not self.is_code_file(f)
        ]
 
        results = {
            'bug_id': bug_id,
            'all_overlapping_files': code_files,
            'file_categories': {f: c for f, c in file_categories.items() if self.is_code_file(f)},
            'extracted_files': []
            
        }
        
        for filepath in bug_data['overlapping_files']:
            category = file_categories[filepath]
            
            # Skip non-code files
            if not self.is_code_file(filepath):
                continue
            
            file_result = {
                'filepath': filepath,
                'category': category,
                'fixing_commits': [],
                'regressor_commits': []
            }
            
            safe_filename = filepath.replace('/', '_').replace('\\', '_')
            
            # Extract from fixing commits
            for fixing_commit in bug_data.get('fixing_commits', []):
                commit_files = [f['filename'] for f in fixing_commit.get('files', [])]
                if filepath not in commit_files:
                    continue
                
                commit_hash = fixing_commit.get('full_hash', '')
                short_hash = fixing_commit.get('commit_hash', '')
                
                content = self.get_file_content_from_commit(commit_hash, filepath)
                
                if content:
                    output_filename = f"{safe_filename}__fixing_{short_hash}.txt"
                    output_path = bug_dir / output_filename
                    
                    with open(output_path, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(content)
                    
                    file_result['fixing_commits'].append({
                        'commit_hash': short_hash,
                        'full_hash': commit_hash,
                        'output_file': str(output_path),
                        'content_length': len(content)
                    })
            
            # Extract from regressor commits
            for regressor_commit in bug_data.get('regressor_commits', []):
                commit_files = [f['filename'] for f in regressor_commit.get('files', [])]
                if filepath not in commit_files:
                    continue
                
                commit_hash = regressor_commit.get('full_hash', '')
                short_hash = regressor_commit.get('commit_hash', '')
                regressor_bug_id = regressor_commit.get('regressor_bug_id', 'unknown')
                
                content = self.get_file_content_from_commit(commit_hash, filepath)
                
                if content:
                    output_filename = f"{safe_filename}__regressor_{regressor_bug_id}_{short_hash}.txt"
                    output_path = bug_dir / output_filename
                    
                    with open(output_path, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(content)
                    
                    file_result['regressor_commits'].append({
                        'commit_hash': short_hash,
                        'full_hash': commit_hash,
                        'regressor_bug_id': regressor_bug_id,
                        'output_file': str(output_path),
                        'content_length': len(content)
                    })
            
            if file_result['fixing_commits'] or file_result['regressor_commits']:
                results['extracted_files'].append(file_result)
        
        return results, filtered_files
    
    def extract_all_files(self) -> Dict:
        """Extract full file contents for all bugs"""
        print("=" * 80)
        print("STEP 7: EXTRACT FULL FILE CONTENTS (CODE FILES ONLY)")
        print("=" * 80 + "\n")
        
        if not self.input_dir.exists():
            print(f"ERROR: Input directory not found: {self.input_dir}")
            print("Please run Step 6 first.")
            return {'error': 'Input directory not found'}
        
        if not self.available_repos:
            print("ERROR: No local repositories found!")
            print("This step requires local Mozilla repositories.")
            return {'error': 'No local repositories found'}
        
        # Get all bug files
        bug_files = sorted(self.input_dir.glob("bug_*.json"))
        
        if not bug_files:
            print(f"ERROR: No bug files found in {self.input_dir}")
            return {'error': 'No bug files found'}
        
        print(f"Found {len(bug_files)} bug files to process")
        print(f"Using {len(self.available_repos)} local repositories: {list(self.available_repos.keys())}\n")
        
        total_files_extracted = 0
        total_files_skipped = 0
        bugs_processed = 0
        successful_bug_ids = []
        failed_bug_ids = []
        filtered_files_by_bug = {}
        
        for i, bug_file in enumerate(bug_files, 1):
            bug_id = bug_file.stem.replace('bug_', '')
            print(f"[{i}/{len(bug_files)}] Bug {bug_id}...")
            
            bug_data = self.load_bug_file(bug_file)
            if not bug_data:
                failed_bug_ids.append(bug_id)
                continue
            
            bug_result, filtered_files = self.extract_file_content(bug_id, bug_data)
            # Track filtered files for summary
            if filtered_files:
                filtered_files_by_bug[bug_id] = {
                    'filtered_files': filtered_files,
                    'fixing_commits': [c.get('commit_hash', '') for c in bug_data.get('fixing_commits', [])],
                    'regressor_commits': [c.get('commit_hash', '') for c in bug_data.get('regressor_commits', [])]
                }

            
            # Save individual bug result
            bugs_processed += 1
            extracted_count = len(bug_result['extracted_files'])
            filtered_count = len(filtered_files)
            total_files_extracted += extracted_count
            total_files_skipped += filtered_count

            if extracted_count > 0:
                # Only save bugs that have extracted code files
                result_file = self.output_dir / f"bug_{bug_id}" / "extraction_metadata.json"
                result_file.parent.mkdir(parents=True, exist_ok=True)
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(bug_result, f, indent=2)
                successful_bug_ids.append(bug_id)
                print(f"     Extracted {extracted_count} code files, filtered {filtered_count}")
            else:
                failed_bug_ids.append(bug_id)
                print(f"     No code files extracted (skipped)")   


        if failed_bug_ids:
            import shutil
            print(f"\nCleaning up {len(failed_bug_ids)} bugs without extractions...")
            for bug_id in failed_bug_ids:
                bug_dir = self.output_dir / f"bug_{bug_id}"
                if bug_dir.exists():
                    shutil.rmtree(bug_dir)
                    print(f"  Removed bug_{bug_id}/")
        
        # Build summary
        summary = {
            'extraction_timestamp': datetime.now().isoformat(),
            'input_directory': str(self.input_dir),
            'output_directory': str(self.output_dir),
            'local_repos_used': list(self.available_repos.keys()),
            'summary': {
                'bugs_processed': bugs_processed,
                'bugs_with_extractions': len(successful_bug_ids),
                'bugs_without_extractions': len(failed_bug_ids),
                'total_code_files_extracted': total_files_extracted,
                'total_non_code_files_skipped': total_files_skipped
            },
            'successful_bug_ids': successful_bug_ids,
            'failed_bug_ids': failed_bug_ids,
            'filtered_files_by_bug': filtered_files_by_bug
        }
        
        self._print_summary(summary)
        
        return summary
    
    def _print_summary(self, summary: Dict):
        """Print extraction summary"""
        print(f"\n{'=' * 80}")
        print("EXTRACTION SUMMARY")
        print(f"{'=' * 80}")
        
        s = summary['summary']
        print(f"\nBugs processed: {s['bugs_processed']}")
        print(f"   With extractions: {s['bugs_with_extractions']}")
        print(f"   Without extractions: {s['bugs_without_extractions']}")
        print(f"\nFiles:")
        print(f"  Code files extracted: {s['total_code_files_extracted']}")
        print(f"  Non-code files skipped: {s['total_non_code_files_skipped']}")
    
    def save_results(self, results: Dict):
        """Save extraction summary"""
        print(f"\n{'=' * 80}")
        print("SAVING RESULTS")
        print(f"{'=' * 80}\n")
        
        # Save summary JSON
        summary_file = self.output_dir / 'extraction_summary.json'
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        print(f" Saved extraction summary to {summary_file}")
        
        # Save report
        report_file = self.output_dir / 'extraction_report.txt'
        self._save_report(results, report_file)
        print(f" Saved extraction report to {report_file}")
    
    def _save_report(self, results: Dict, output_path: Path):
        """Save human-readable report"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("STEP 7: FULL FILE CONTENT EXTRACTION REPORT\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Extraction Time: {results['extraction_timestamp']}\n")
            f.write(f"Input: {results['input_directory']}\n")
            f.write(f"Output: {results['output_directory']}\n")
            f.write(f"Repositories: {', '.join(results['local_repos_used'])}\n\n")
            
            s = results['summary']
            f.write("SUMMARY\n")
            f.write("-" * 40 + "\n")
            f.write(f"Bugs processed: {s['bugs_processed']}\n")
            f.write(f"Bugs with extractions: {s['bugs_with_extractions']}\n")
            f.write(f"Bugs without extractions: {s['bugs_without_extractions']}\n")
            f.write(f"Code files extracted: {s['total_code_files_extracted']}\n")
            f.write(f"Non-code files skipped: {s['total_non_code_files_skipped']}\n\n")
            
            if results['successful_bug_ids']:
                f.write("SUCCESSFUL BUGS\n")
                f.write("-" * 40 + "\n")
                for bug_id in results['successful_bug_ids']:
                    f.write(f"  Bug {bug_id}\n")
                f.write("\n")
            
            if results['failed_bug_ids']:
                f.write("FAILED/SKIPPED BUGS\n")
                f.write("-" * 40 + "\n")
                for bug_id in results['failed_bug_ids']:
                    f.write(f"  Bug {bug_id}\n")


def main():
    """Main execution function"""
    print("=" * 80)
    print("STEP 7: EXTRACT FULL FILE CONTENTS")
    print("=" * 80 + "\n")
    
    extractor = FullFileExtractor()
    
    results = extractor.extract_all_files()
    
    if 'error' not in results:
        extractor.save_results(results)
        
        print("\n" + "=" * 80)
        print("✓ STEP 7 COMPLETE")
        print("=" * 80)
        print(f"\nOutput: {extractor.output_dir}")
        print(f"\nEach bug folder contains:")
        print(f"  - extraction_metadata.json")
        print(f"  - <filename>__fixing_<hash>.txt")
        print(f"  - <filename>__regressor_<bug>_<hash>.txt")
    else:
        print("\n" + "=" * 80)
        print(" STEP 7 FAILED")
        print("=" * 80)
        print(f"\nError: {results.get('error')}")


if __name__ == "__main__":
    main()
