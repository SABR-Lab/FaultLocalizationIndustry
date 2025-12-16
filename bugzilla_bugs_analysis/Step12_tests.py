#!/usr/bin/env python3
"""
Step 12: Extract Related Test Files
For each matched file in Step 10, find test files that reference the source file.

INPUT:  method2_outputs/step10_fixing_regressor_matching/step10_fixing_regressor_matches.json
OUTPUT: method2_outputs/step12_test_extraction/
"""

import json
import os
import subprocess
import re
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
method2_outputs = script_dir / "method2_outputs"

# Input/Output paths
INPUT_FILE = method2_outputs / "step10_fixing_regressor_matching" / "step10_fixing_regressor_matches.json"
OUTPUT_DIR = method2_outputs / "step12_test_extraction"

# Local repositories
LOCAL_REPOS = {
    'mozilla-central': parent_dir / 'mozilla-central',
    'mozilla-release': parent_dir / 'mozilla-release',
    'mozilla-autoland': parent_dir / 'mozilla-autoland',
    'mozilla-esr115': parent_dir / 'mozilla-esr115'
}


class TestFileExtractor:
    """Extract test files that reference matched source files"""
    
    TEST_DIR_PATTERNS = ['test/', 'tests/', 'testing/mochitest/', 'testing/xpcshell/']
    TEST_INDICATORS = ['test', 'spec', 'mock', 'stub', 'fixture']
    TEST_EXTENSIONS = ['.js', '.cpp', '.h', '.py', '.mochitest', '.xpcshell']
    
    def __init__(self, local_repos: Dict[str, str] = None, output_dir: str = None, debug: bool = False):
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.debug = debug
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup local repos
        self.local_repos = {}
        repos = local_repos or LOCAL_REPOS
        
        print("Checking local repositories:")
        for name, path in repos.items():
            path = Path(path)
            if path.exists():
                self.local_repos[name] = str(path)
                print(f"  ✓ {name}: {path}")
            else:
                print(f"  ✗ {name}: {path} (NOT FOUND)")
        print()
    
    def _is_test_file(self, filename: str) -> bool:
        """Check if filename is a test file"""
        lower_name = filename.lower()
        has_test_ext = any(lower_name.endswith(ext) for ext in self.TEST_EXTENSIONS)
        if not has_test_ext:
            return False
        return any(indicator in lower_name for indicator in self.TEST_INDICATORS)
    
    def _extract_source_references(self, test_content: str, filepath: str, file_base: str, file_name: str) -> List[str]:
        """Extract direct references to the source file from test content"""
        references = []
        source_base = file_base.lower()
        source_name = file_name.lower()
        
        # Check #include directives
        include_pattern = r'#include\s+[<"]([^>"]+)[>"]'
        for match in re.finditer(include_pattern, test_content, re.IGNORECASE):
            include_path = match.group(1).lower()
            if source_base in include_path or source_name in include_path:
                references.append(f"include: {match.group(1)}")
        
        # Check import directives
        import_pattern = r'(?:require|import)\s+[\'"]([^\'"]+)[\'"]'
        for match in re.finditer(import_pattern, test_content, re.IGNORECASE):
            import_path = match.group(1).lower()
            if source_base in import_path or source_name in import_path:
                references.append(f"import: {match.group(1)}")
        
        return references
    
    def find_tests_at_commit(self, commit_hash: str, filepath: str, repo_path: str) -> List[Dict]:
        """Find test files referencing source file at a specific commit"""
        test_files = []
        file_name = os.path.basename(filepath)
        file_base = os.path.splitext(file_name)[0]
        path_parts = filepath.lower().split('/')[:-1]
        component_keywords = [part for part in path_parts if len(part) > 2]
        
        try:
            result = subprocess.run(
                ['hg', 'files', '-r', commit_hash],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return []
            
            all_files = result.stdout.strip().split('\n')
            
            # Layer 1: Find test files in test directories
            candidates = []
            for test_file in all_files:
                if not any(p in test_file.lower() for p in self.TEST_DIR_PATTERNS):
                    continue
                if not self._is_test_file(test_file):
                    continue
                candidates.append(test_file)
            
            # Layer 2: Filter by component keywords and source base name
            filtered = []
            source_base_lower = file_base.lower()
            
            for candidate in candidates:
                candidate_lower = candidate.lower()
                has_component = any(comp in candidate_lower for comp in component_keywords)
                test_filename = os.path.basename(candidate_lower)
                filename_mentions_source = source_base_lower in test_filename
                
                if has_component and filename_mentions_source:
                    filtered.append(candidate)
            
            # Layer 3: Verify actual #include/import statements
            for test_file in filtered:
                try:
                    result = subprocess.run(
                        ['hg', 'cat', '-r', commit_hash, test_file],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    
                    if result.returncode != 0:
                        continue
                    
                    content = result.stdout
                    references = self._extract_source_references(content, filepath, file_base, file_name)
                    
                    if references:
                        test_files.append({
                            'path': test_file,
                            'content': content,
                            'references': references,
                            'reference_count': len(references)
                        })
                except:
                    continue
        except Exception as e:
            if self.debug:
                print(f"        Error finding tests: {e}")
        
        return test_files
    
    def get_tests_for_commit(self, commit_hash: str, filepath: str) -> Dict:
        """Get test files for a file at a specific commit"""
        tests_found = []
        found_in_repo = None
        
        for repo_name, repo_path in self.local_repos.items():
            try:
                result = subprocess.run(
                    ['hg', 'log', '-r', commit_hash, '--template', 'x'],
                    cwd=repo_path,
                    capture_output=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    tests = self.find_tests_at_commit(commit_hash, filepath, repo_path)
                    if tests:
                        tests_found.extend(tests)
                        found_in_repo = repo_name
                        return {
                            'tests_found': tests_found,
                            'test_count': len(tests_found),
                            'found_in_repo': found_in_repo
                        }
                    else:
                        return {
                            'tests_found': [],
                            'test_count': 0,
                            'found_in_repo': repo_name
                        }
            except:
                continue
        
        return {
            'tests_found': tests_found,
            'test_count': len(tests_found),
            'found_in_repo': found_in_repo
        }
    
    def process_match(self, bug_id: str, filepath: str, match_idx: int, 
                      fixing_hash: str, regressor_hash: str) -> Dict:
        """Process a match: extract tests for both commits"""
        fixing_tests = self.get_tests_for_commit(fixing_hash, filepath)
        regressor_tests = self.get_tests_for_commit(regressor_hash, filepath)
        
        return {
            'match_idx': match_idx,
            'fixing_commit': {
                'hash': fixing_hash,
                'tests': fixing_tests['tests_found'],
                'test_count': fixing_tests['test_count'],
                'found_in_repo': fixing_tests['found_in_repo']
            },
            'regressor_commit': {
                'hash': regressor_hash,
                'tests': regressor_tests['tests_found'],
                'test_count': regressor_tests['test_count'],
                'found_in_repo': regressor_tests['found_in_repo']
            }
        }
    
    def save_test_results(self, filepath: str, bug_id: str, match_idx: int, result: Dict):
        """Save test extraction results"""
        safe_filepath = filepath.replace('/', '_').replace('.cpp', '').replace('.h', '').replace('.js', '')
        match_dir = self.output_dir / f"bug_{bug_id}" / safe_filepath / f"match_{match_idx}"
        match_dir.mkdir(parents=True, exist_ok=True)
        
        # Save metadata JSON
        metadata = {
            'match_idx': result['match_idx'],
            'fixing_commit': {
                'hash': result['fixing_commit']['hash'],
                'test_count': result['fixing_commit']['test_count'],
                'found_in_repo': result['fixing_commit']['found_in_repo'],
                'tests': [{'path': t['path'], 'references': t.get('references', [])} 
                         for t in result['fixing_commit']['tests']]
            },
            'regressor_commit': {
                'hash': result['regressor_commit']['hash'],
                'test_count': result['regressor_commit']['test_count'],
                'found_in_repo': result['regressor_commit']['found_in_repo'],
                'tests': [{'path': t['path'], 'references': t.get('references', [])} 
                         for t in result['regressor_commit']['tests']]
            }
        }
        
        with open(match_dir / 'tests_found.json', 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        
        # Save test files
        if result['fixing_commit']['tests']:
            fixing_dir = match_dir / 'fixing'
            fixing_dir.mkdir(exist_ok=True)
            for test in result['fixing_commit']['tests']:
                if 'content' in test:
                    test_filename = os.path.basename(test['path'])
                    with open(fixing_dir / test_filename, 'w', encoding='utf-8') as f:
                        f.write(test['content'])
        
        if result['regressor_commit']['tests']:
            regressor_dir = match_dir / 'regressor'
            regressor_dir.mkdir(exist_ok=True)
            for test in result['regressor_commit']['tests']:
                if 'content' in test:
                    test_filename = os.path.basename(test['path'])
                    with open(regressor_dir / test_filename, 'w', encoding='utf-8') as f:
                        f.write(test['content'])
    
    def extract_all(self, step10_json: str = None) -> Dict:
        """Extract tests from Step 10 matches"""
        step10_file = Path(step10_json) if step10_json else INPUT_FILE
        
        print("="*80)
        print("STEP 12: TEST FILE EXTRACTION")
        print("="*80 + "\n")
        
        print(f"Loading Step 10 results from: {step10_file}\n")
        with open(step10_file, 'r') as f:
            step10_data = json.load(f)
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'step10_source': str(step10_file),
            'output_dir': str(self.output_dir),
            'summary': {
                'bugs': 0,
                'files': 0,
                'matches': 0,
                'matches_with_tests': 0
            },
            'bugs': {}
        }
        
        for bug_id, bug_data in step10_data['bugs'].items():
            print(f"\nBug {bug_id}:")
            bug_results = {'files': []}
            
            for file_data in bug_data['files']:
                filepath = file_data['filepath']
                print(f"  File: {filepath}")
                
                file_results = {'filepath': filepath, 'matches': []}
                
                for match_idx, match in enumerate(file_data['matches'], 1):
                    fixing_hash = match['fixing_commit']['hash']
                    regressor_hash = match['regressor_commit']['hash']
                    
                    result = self.process_match(bug_id, filepath, match_idx, fixing_hash, regressor_hash)
                    file_results['matches'].append(result)
                    results['summary']['matches'] += 1
                    
                    fixing_cnt = result['fixing_commit']['test_count']
                    regressor_cnt = result['regressor_commit']['test_count']
                    
                    if fixing_cnt > 0 or regressor_cnt > 0:
                        results['summary']['matches_with_tests'] += 1
                        self.save_test_results(filepath, bug_id, match_idx, result)
                    
                    status = "✓" if (fixing_cnt > 0 or regressor_cnt > 0) else "─"
                    print(f"    {status} Match {match_idx}: {fixing_cnt} fixing tests, {regressor_cnt} regressor tests")
                
                if file_results['matches']:
                    bug_results['files'].append(file_results)
                    results['summary']['files'] += 1
            
            if bug_results['files']:
                results['bugs'][bug_id] = bug_results
                results['summary']['bugs'] += 1
        
        # Save summary
        summary_file = self.output_dir / 'step12_test_extraction_summary.json'
        with open(summary_file, 'w', encoding='utf-8') as f:
            # Remove content from summary to keep file small
            summary_results = json.loads(json.dumps(results))
            for bug_data in summary_results.get('bugs', {}).values():
                for file_data in bug_data.get('files', []):
                    for match in file_data.get('matches', []):
                        for commit_type in ['fixing_commit', 'regressor_commit']:
                            if commit_type in match:
                                for test in match[commit_type].get('tests', []):
                                    test.pop('content', None)
            json.dump(summary_results, f, indent=2)
        
        print(f"\n{'='*80}")
        print("EXTRACTION COMPLETE")
        print(f"{'='*80}")
        print(f"Bugs: {results['summary']['bugs']}")
        print(f"Files: {results['summary']['files']}")
        print(f"Matches: {results['summary']['matches']}")
        print(f"Matches with tests: {results['summary']['matches_with_tests']}")
        print(f"\nResults saved to: {summary_file}")
        
        return results


def main():
    """Main execution"""
    if not INPUT_FILE.exists():
        print(f"ERROR: Step 10 file not found: {INPUT_FILE}")
        print("Please run Step 10 first.")
        sys.exit(1)
    
    extractor = TestFileExtractor(debug=False)
    extractor.extract_all()
    
    print("\n" + "="*80)
    print(" STEP 12 COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()