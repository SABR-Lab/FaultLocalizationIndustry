#!/usr/bin/env python3
"""
Step 6: Extract Full File Contents for Overlapping Files (Code Files Only)
Extracts ONLY raw code content from parent commits (no metadata header)
"""

import json
import os
import subprocess
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path


class FullFileExtractor:
    """Extract complete file contents from commits (code files only)"""
    
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
    
    def __init__(self, 
                 step5_file: str,
                 output_dir: str = "full_file_contents",
                 local_repos: Dict[str, str] = None):
        """Initialize the extractor"""
        self.step5_file = step5_file
        self.output_dir = output_dir
        self.local_repos = local_repos or {}
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"Loading Step 5 results from: {step5_file}")
        with open(step5_file, 'r') as f:
            self.step5_data = json.load(f)
        
        print(f"Found {len(self.step5_data['bugs'])} bugs with overlapping files\n")
    
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
    
    def get_parent_commit_hash(self, commit_hash: str) -> Optional[str]:
        """Get the parent commit hash from local repositories"""
        for repo_name, repo_path in self.local_repos.items():
            try:
                result = subprocess.run(
                    ['hg', 'log', '-r', f'parents({commit_hash})', '--template', '{node}'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding='utf-8',
                    errors='replace'
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    parent_hash = result.stdout.strip()
                    print(f"        ✓ Found parent commit in {repo_name}")
                    return parent_hash
                    
            except subprocess.TimeoutExpired:
                print(f"        ✗ Timeout querying parent in {repo_name}")
                continue
            except Exception as e:
                print(f"        ✗ Error querying parent in {repo_name}: {e}")
                continue
        
        return None
    
    def get_parent_commit_info(self, parent_hash: str) -> Optional[Dict]:
        """Get metadata for a parent commit"""
        for repo_name, repo_path in self.local_repos.items():
            try:
                result = subprocess.run(
                    ['hg', 'log', '-r', parent_hash, '--template', 
                     '{node|short}\\n{node}\\n{author}\\n{date|isodate}\\n{desc}'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding='utf-8',
                    errors='replace'
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    lines = result.stdout.strip().split('\n', 4)
                    if len(lines) >= 4:
                        return {
                            'commit_hash': lines[0],
                            'full_hash': lines[1],
                            'author': lines[2],
                            'date': lines[3],
                            'description': lines[4] if len(lines) > 4 else ''
                        }
                    
            except subprocess.TimeoutExpired:
                continue
            except Exception as e:
                continue
        
        return None
    
    def get_file_content_from_commit(self, commit_hash: str, filepath: str) -> Optional[str]:
        """Get the full content of a file from a specific commit"""
        for repo_name, repo_path in self.local_repos.items():
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
                    print(f"      ✓ Found in {repo_name}")
                    return result.stdout
                    
            except subprocess.TimeoutExpired:
                print(f"      ✗ Timeout in {repo_name}")
                continue
            except Exception as e:
                print(f"      ✗ Error in {repo_name}: {e}")
                continue
        
        print(f"      ✗ Not found in any local repository")
        return None
    
    def extract_file_content(self, bug_id: str, bug_data: Dict) -> Dict:
        """Extract full file contents for a single bug"""
        print(f"\n{'='*80}")
        print(f"Bug {bug_id}: {bug_data['total_overlapping_files']} overlapping files")
        print(f"{'='*80}")
        
        file_categories = {}
        for filepath in bug_data['overlapping_files']:
            category = self.categorize_file(filepath)
            file_categories[filepath] = category
        
        category_counts = {}
        for category in file_categories.values():
            category_counts[category] = category_counts.get(category, 0) + 1
        
        print(f"\n  File breakdown:")
        for category, count in sorted(category_counts.items()):
            print(f"    {category}: {count}")
        
        bug_dir = os.path.join(self.output_dir, f"bug_{bug_id}")
        os.makedirs(bug_dir, exist_ok=True)
        
        results = {
            'bug_id': bug_id,
            'all_overlapping_files': bug_data['overlapping_files'],
            'file_categories': file_categories,
            'extracted_files': [],
            'skipped_files': []
        }
        
        for filepath in bug_data['overlapping_files']:
            category = file_categories[filepath]
            
            if not self.is_code_file(filepath):
                print(f"\n  Skipping ({category}): {filepath}")
                results['skipped_files'].append({
                    'filepath': filepath,
                    'category': category,
                    'reason': f'Not a code file ({category})'
                })
                continue
            
            print(f"\n  Processing (code): {filepath}")
            
            file_result = {
                'filepath': filepath,
                'category': category,
                'fixing_commits': [],
                'regressor_commits': []
            }
            
            safe_filename = filepath.replace('/', '_').replace('\\', '_')
            
            # Extract from PARENT of fixing commits
            print(f"    Extracting from PARENT of fixing commits...")
            for fixing_commit in bug_data['fixing_commits']:
                commit_files = [f['filename'] for f in fixing_commit['files']]
                if filepath not in commit_files:
                    continue
                
                commit_hash = fixing_commit['full_hash']
                short_hash = fixing_commit['commit_hash']
                
                print(f"      Looking for parent of commit {short_hash}...")
                parent_commit_hash = self.get_parent_commit_hash(commit_hash)
                if not parent_commit_hash:
                    print(f"      ✗ Could not find parent commit for {short_hash}")
                    continue
                
                print(f"      Getting parent commit metadata...")
                parent_info = self.get_parent_commit_info(parent_commit_hash)
                
                print(f"      Extracting file from parent...")
                content = self.get_file_content_from_commit(parent_commit_hash, filepath)
                
                if content:
                    # Save ONLY raw code, no header
                    output_filename = f"{safe_filename}__fixing_parent_{short_hash}.txt"
                    output_path = os.path.join(bug_dir, output_filename)
                    
                    with open(output_path, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(content)
                    
                    file_result['fixing_commits'].append({
                        'commit_hash': short_hash,
                        'full_hash': commit_hash,
                        'parent_hash': parent_commit_hash,
                        'parent_info': parent_info,
                        'output_file': output_path,
                        'content_length': len(content),
                        'note': 'Raw file content from parent commit'
                    })
                    print(f"        Saved: {output_filename} ({len(content)} bytes)")
            
            # Extract from PARENT of regressor commits
            print(f"    Extracting from PARENT of regressor commits...")
            for regressor_commit in bug_data['regressor_commits']:
                commit_files = [f['filename'] for f in regressor_commit['files']]
                if filepath not in commit_files:
                    continue
                
                commit_hash = regressor_commit['full_hash']
                short_hash = regressor_commit['commit_hash']
                regressor_bug_id = regressor_commit['regressor_bug_id']
                
                print(f"      Looking for parent of commit {short_hash} (Bug {regressor_bug_id})...")
                parent_commit_hash = self.get_parent_commit_hash(commit_hash)
                if not parent_commit_hash:
                    print(f"      ✗ Could not find parent commit for {short_hash}")
                    continue
                
                print(f"      Getting parent commit metadata...")
                parent_info = self.get_parent_commit_info(parent_commit_hash)
                
                print(f"      Extracting file from parent...")
                content = self.get_file_content_from_commit(parent_commit_hash, filepath)
                
                if content:
                    # Save ONLY raw code, no header
                    output_filename = f"{safe_filename}__regressor_parent_{regressor_bug_id}_{short_hash}.txt"
                    output_path = os.path.join(bug_dir, output_filename)
                    
                    with open(output_path, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(content)
                    
                    file_result['regressor_commits'].append({
                        'commit_hash': short_hash,
                        'full_hash': commit_hash,
                        'parent_hash': parent_commit_hash,
                        'parent_info': parent_info,
                        'regressor_bug_id': regressor_bug_id,
                        'output_file': output_path,
                        'content_length': len(content),
                        'note': 'Raw file content from parent commit'
                    })
                    print(f"        Saved: {output_filename} ({len(content)} bytes)")
            
            if file_result['fixing_commits'] or file_result['regressor_commits']:
                results['extracted_files'].append(file_result)
        
        return results
    
    def extract_all_files(self) -> Dict:
        """Extract full file contents for all bugs"""
        print("\n" + "="*80)
        print("EXTRACTING FULL FILE CONTENTS (CODE FILES ONLY)")
        print("FROM PARENT COMMITS (RAW CODE, NO HEADER)")
        print("="*80)
        
        all_results = {
            'extraction_timestamp': datetime.now().isoformat(),
            'step5_file': self.step5_file,
            'output_directory': self.output_dir,
            'extraction_mode': 'parent commits - raw code only',
            'filtering': {
                'code_extensions': list(self.CODE_EXTENSIONS),
                'exclude_patterns': self.EXCLUDE_PATTERNS
            },
            'bugs': {}
        }
        
        total_files_extracted = 0
        total_files_skipped = 0
        bugs_processed = 0
        
        for bug_id, bug_data in self.step5_data['bugs'].items():
            bug_result = self.extract_file_content(bug_id, bug_data)
            all_results['bugs'][bug_id] = bug_result
            
            bugs_processed += 1
            total_files_extracted += len(bug_result['extracted_files'])
            total_files_skipped += len(bug_result['skipped_files'])
        
        all_results['summary'] = {
            'bugs_processed': bugs_processed,
            'total_overlapping_files': sum(
                len(bug['overlapping_files']) 
                for bug in self.step5_data['bugs'].values()
            ),
            'code_files_extracted': total_files_extracted,
            'non_code_files_skipped': total_files_skipped
        }
        
        print(f"\n{'='*80}")
        print("EXTRACTION SUMMARY")
        print(f"{'='*80}")
        print(f"Bugs processed: {bugs_processed}")
        print(f"Total overlapping files: {all_results['summary']['total_overlapping_files']}")
        print(f"Code files extracted: {total_files_extracted}")
        print(f"Non-code files skipped: {total_files_skipped}")
        
        return all_results
    
    def save_results(self, results: Dict) -> str:
        """Save extraction results to JSON"""
        output_file = os.path.join(self.output_dir, f'Step6_extraction_results.json')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved to: {output_file}")
        return output_file
    
    def create_summary_report(self, results: Dict) -> str:
        """Create a human-readable summary report"""
        output_file = os.path.join(self.output_dir, f'Step6_summary_report.txt')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("FULL FILE CONTENT EXTRACTION SUMMARY\n")
            f.write("RAW CODE ONLY (NO HEADER)\n")
            f.write("="*80 + "\n\n")
            f.write(f"Extraction Time: {results['extraction_timestamp']}\n")
            f.write(f"Mode: {results['extraction_mode']}\n")
            f.write(f"Source: {results['step5_file']}\n")
            f.write(f"Output Directory: {results['output_directory']}/\n\n")
            
            f.write(f"Bugs Processed: {results['summary']['bugs_processed']}\n")
            f.write(f"Total Overlapping Files: {results['summary']['total_overlapping_files']}\n")
            f.write(f"Code Files Extracted: {results['summary']['code_files_extracted']}\n")
            f.write(f"Non-Code Files Skipped: {results['summary']['non_code_files_skipped']}\n\n")
            
            f.write("="*80 + "\n")
            f.write("PER-BUG DETAILS\n")
            f.write("="*80 + "\n\n")
            
            for bug_id, bug_result in results['bugs'].items():
                f.write(f"Bug {bug_id}:\n")
                f.write(f"  Total overlapping files: {len(bug_result['all_overlapping_files'])}\n")
                f.write(f"  Code files extracted: {len(bug_result['extracted_files'])}\n")
                f.write(f"  Non-code files skipped: {len(bug_result['skipped_files'])}\n")
                
                if bug_result['extracted_files']:
                    f.write(f"\n  Extracted code files:\n")
                    for file_data in bug_result['extracted_files']:
                        f.write(f"    - {file_data['filepath']}\n")
                        f.write(f"      Fixing: {len(file_data['fixing_commits'])} commits, ")
                        f.write(f"Regressor: {len(file_data['regressor_commits'])} commits\n")
                
                f.write("\n")
        
        print(f"Summary report saved to: {output_file}")
        return output_file


def main():
    """Main execution function"""
    
    local_repos = {
        'central': './mozilla-central',
        'autoland': './mozilla-autoland',
        'release': './mozilla-release',
        'esr115': './mozilla-esr115'
    }
    
    print("Checking local repositories...")
    available_repos = {}
    for name, path in local_repos.items():
        if os.path.exists(path):
            print(f"   {name}: {path}")
            available_repos[name] = path
        else:
            print(f"   {name}: {path} (not found)")
    
    if not available_repos:
        print("\nERROR: No local repositories found!")
        return
    
    print(f"\nUsing {len(available_repos)} local repositories\n")
    
    step5_file = "step5_overlapping_files_output/overlapping_files.json"
    
    if not os.path.exists(step5_file):
        print(f"ERROR: Step 5 file not found: {step5_file}")
        return
    
    extractor = FullFileExtractor(
        step5_file=step5_file,
        output_dir="step6_full_file_contents",
        local_repos=available_repos
    )
    
    print("\nStarting full file content extraction...")
    results = extractor.extract_all_files()
    
    json_file = extractor.save_results(results)
    summary_file = extractor.create_summary_report(results)
    
    print("\n" + "="*80)
    print("EXTRACTION COMPLETE")
    print("="*80)
    print(f"\nJSON results: {json_file}")
    print(f"Summary report: {summary_file}")
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()