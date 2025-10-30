#!/usr/bin/env python3
"""
Step 11: Extract Related Test Files
For each matched file in Step 9, find test files that reference the source file
at both the fixing commit and regressor commit in local Mercurial repositories.

Test discovery strategy:
1. Load Step 9 results (matches with overlapping methods)
2. For each file + commit pair, find tests in component's test directories
3. Read test file content at that specific commit
4. Search for references to the source file
5. Include only tests that actually mention the source file

Input:  fixing_regressor_analysis/Step9_fixing_regressor_matches.json
        Local Mercurial repos (mozilla-central, mozilla-release, mozilla-autoland, mozilla-esr115)

Output: test_extraction/
        ├── bug_XXXXX/
        │   ├── file_name/
        │   │   ├── match_1/
        │   │   │   ├── tests_found.json
        │   │   │   └── tests_summary.txt
"""

import json
import os
import subprocess
from datetime import datetime
from typing import Dict, List


class TestFileExtractor:
    """Extract test files that reference matched source files at specific commits"""
    
    TEST_DIRS = ['test', 'tests', 'testing', '__tests__', 'spec']
    TEST_INDICATORS = ['test', 'spec', 'mock', 'stub', 'fixture']
    
    def __init__(self, local_repos: Dict[str, str], output_dir: str = "test_extraction", debug: bool = False):
        self.local_repos = local_repos
        self.output_dir = output_dir
        self.debug = debug
        
        os.makedirs(output_dir, exist_ok=True)
        
        print("Validating local repositories:")
        for name, path in self.local_repos.items():
            status = "✓" if os.path.exists(path) else "✗"
            print(f"  {status} {name}: {path}")
        print()
    
    def _is_test_file(self, filename: str) -> bool:
        """Check if filename is a test file"""
        lower_name = filename.lower()
        return any(indicator in lower_name for indicator in self.TEST_INDICATORS)
    
    def _test_mentions_source(self, test_filename: str, file_base: str, file_name: str) -> bool:
        """Check if test filename mentions the source file"""
        lower_test = test_filename.lower()
        lower_base = file_base.lower()
        
        return (lower_base in lower_test or 
                file_name.lower().replace('.cpp', '').replace('.h', '').replace('.js', '') in lower_test)
    
    def find_tests_at_commit(self, commit_hash: str, filepath: str, repo_path: str) -> List[str]:
        """Find test files referencing source file at a specific commit"""
        
        test_files = []
        file_dir = os.path.dirname(filepath)
        file_name = os.path.basename(filepath)
        file_base = os.path.splitext(file_name)[0]
        
        if self.debug:
            print(f"          [DEBUG] Searching commit {commit_hash[:8]} for tests of {file_name}")
        
        try:
            # List files at this specific commit
            result = subprocess.run(
                ['hg', 'files', '-r', commit_hash],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                if self.debug:
                    print(f"          [DEBUG] Could not list files at commit")
                return []
            
            all_files = result.stdout.strip().split('\n')
            if self.debug:
                print(f"          [DEBUG] Listed {len(all_files)} files at this commit")
            
            # Find candidate test files (name-based filtering)
            candidates = []
            for test_dir in self.TEST_DIRS:
                test_base = os.path.join(file_dir, test_dir)
                for test_file in all_files:
                    if not test_file.startswith(test_base + '/'):
                        continue
                    if not self._is_test_file(os.path.basename(test_file)):
                        continue
                    if self._test_mentions_source(os.path.basename(test_file), file_base, file_name):
                        candidates.append(test_file)
            
            if self.debug:
                print(f"          [DEBUG] Found {len(candidates)} candidate test files")
            
            # Verify candidates by checking content
            for test_file in candidates:
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
                    
                    content = result.stdout.lower()
                    
                    # Check if test content references source file
                    refs = [
                        file_base.lower(),
                        file_name.lower(),
                        file_name.replace('.cpp', '').replace('.h', '').replace('.js', '').lower(),
                    ]
                    
                    if any(ref and len(ref) > 3 and ref in content for ref in refs):
                        test_files.append(test_file)
                        if self.debug:
                            print(f"          [DEBUG] ✓ Verified: {os.path.basename(test_file)}")
                
                except Exception:
                    continue
        
        except subprocess.TimeoutExpired:
            if self.debug:
                print(f"          [DEBUG] Timeout listing files")
        except Exception as e:
            if self.debug:
                print(f"          [DEBUG] Error: {e}")
        
        return list(set(test_files))
    
    def get_tests_for_commit(self, commit_hash: str, filepath: str) -> Dict:
        """Get test files for a file at a specific commit from any available repo"""
        
        tests_found = []
        found_in_repo = None
        
        for repo_name, repo_path in self.local_repos.items():
            if not os.path.exists(repo_path):
                continue
            
            try:
                # Check if commit exists in this repo
                result = subprocess.run(
                    ['hg', 'log', '-r', commit_hash, '--template', 'x'],
                    cwd=repo_path,
                    capture_output=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    if self.debug:
                        print(f"        [DEBUG] Commit found in {repo_name}")
                    
                    tests = self.find_tests_at_commit(commit_hash, filepath, repo_path)
                    if tests:
                        tests_found.extend(tests)
                        found_in_repo = repo_name
                        if self.debug:
                            print(f"        [DEBUG] Found {len(tests)} test(s)")
                
            except subprocess.TimeoutExpired:
                if self.debug:
                    print(f"        [DEBUG] Timeout in {repo_name}")
            except Exception:
                continue
        
        return {
            'tests_found': list(set(tests_found)),
            'test_count': len(set(tests_found)),
            'found_in_repo': found_in_repo
        }
    
    def process_match(self, bug_id: str, filepath: str, match_idx: int, fixing_hash: str, regressor_hash: str) -> Dict:
        """Process a match: extract tests for both commits"""
        
        if self.debug:
            print(f"      [DEBUG] Processing match {match_idx}: {filepath}")
        
        fixing_tests = self.get_tests_for_commit(fixing_hash, filepath)
        regressor_tests = self.get_tests_for_commit(regressor_hash, filepath)
        
        return {
            'match_idx': match_idx,
            'fixing_commit': {
                'hash': fixing_hash,
                'tests': fixing_tests['tests_found'],
                'test_count': fixing_tests['test_count']
            },
            'regressor_commit': {
                'hash': regressor_hash,
                'tests': regressor_tests['tests_found'],
                'test_count': regressor_tests['test_count']
            }
        }
    
    def save_test_results(self, filepath: str, bug_id: str, match_idx: int, result: Dict):
        """Save test extraction results"""
        
        safe_filepath = filepath.replace('/', '_').replace('.cpp', '').replace('.h', '').replace('.js', '')
        match_dir = os.path.join(self.output_dir, f"bug_{bug_id}", safe_filepath, f"match_{match_idx}")
        os.makedirs(match_dir, exist_ok=True)
        
        # Save JSON
        json_file = os.path.join(match_dir, 'tests_found.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        
        # Save text summary
        txt_file = os.path.join(match_dir, 'tests_summary.txt')
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("TEST FILES EXTRACTION SUMMARY\n")
            f.write("="*80 + "\n\n")
            
            f.write(f"Bug: {bug_id}\n")
            f.write(f"File: {filepath}\n")
            f.write(f"Match: {result['match_idx']}\n\n")
            
            f.write(f"FIXING COMMIT: {result['fixing_commit']['hash']}\n")
            f.write(f"Tests found: {result['fixing_commit']['test_count']}\n")
            if result['fixing_commit']['tests']:
                for test in result['fixing_commit']['tests']:
                    f.write(f"  - {test}\n")
            else:
                f.write("  (no tests found)\n")
            
            f.write(f"\nREGRESSOR COMMIT: {result['regressor_commit']['hash']}\n")
            f.write(f"Tests found: {result['regressor_commit']['test_count']}\n")
            if result['regressor_commit']['tests']:
                for test in result['regressor_commit']['tests']:
                    f.write(f"  - {test}\n")
            else:
                f.write("  (no tests found)\n")
    
    def extract_all(self) -> Dict:
        """Extract tests from Step 9 matches"""
        
        print("="*80)
        print("STEP 11: EXTRACT TEST FILES FROM STEP 9 MATCHES")
        print("="*80 + "\n")
        
        # Load Step 9 results
        step9_json = "fixing_regressor_analysis/Step9_fixing_regressor_matches.json"
        
        if not os.path.exists(step9_json):
            print(f"ERROR: Step 9 file not found: {step9_json}")
            return {}
        
        print(f"Loading: {step9_json}\n")
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
                'matches_with_tests': 0
            },
            'bugs': {}
        }
        
        # Process each bug from Step 9
        for bug_id, bug_data in step9_data['bugs'].items():
            print(f"Bug {bug_id}:")
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
                    
                    if result['fixing_commit']['test_count'] > 0 or result['regressor_commit']['test_count'] > 0:
                        results['summary']['matches_with_tests'] += 1
                    
                    self.save_test_results(filepath, bug_id, match_idx, result)
                    
                    fixing_cnt = result['fixing_commit']['test_count']
                    regressor_cnt = result['regressor_commit']['test_count']
                    status = "✓" if (fixing_cnt > 0 or regressor_cnt > 0) else "-"
                    print(f"    Match {match_idx}: {status} ({fixing_cnt} fixing, {regressor_cnt} regressor)")
                
                if file_results['matches']:
                    bug_results['files'].append(file_results)
                    results['summary']['files'] += 1
            
            if bug_results['files']:
                results['bugs'][bug_id] = bug_results
                results['summary']['bugs'] += 1
        
        # Print summary
        print(f"\n{'='*80}")
        print("EXTRACTION COMPLETE")
        print(f"{'='*80}")
        print(f"Bugs: {results['summary']['bugs']}")
        print(f"Files: {results['summary']['files']}")
        print(f"Matches: {results['summary']['matches']}")
        print(f"Matches with tests: {results['summary']['matches_with_tests']}")
        
        if results['summary']['matches'] > 0:
            pct = (results['summary']['matches_with_tests'] / results['summary']['matches']) * 100
            print(f"Coverage: {pct:.1f}%")
        
        print(f"\nOutput: {self.output_dir}/")
        
        return results


def main():
    """Main execution"""
    
    local_repos = {
        'mozilla-central': './mozilla-central',
        'mozilla-release': './mozilla-release',
        'mozilla-autoland': './mozilla-autoland',
        'mozilla-esr115': './mozilla-esr115'
    }
    
    extractor = TestFileExtractor(
        local_repos,
        output_dir="test_extraction",
        debug=True
    )
    
    extractor.extract_all()
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()