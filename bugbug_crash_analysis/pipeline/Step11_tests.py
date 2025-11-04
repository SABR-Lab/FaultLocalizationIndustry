#!/usr/bin/env python3
"""
Step 11: Extract Related Test Files (REVISED)
For each matched file in Step 9, find test files that reference the source file
at both the fixing commit and regressor commit in local Mercurial repositories.

REVISED OUTPUT FORMAT:
- Each test file is saved with its actual filename (e.g., TestMP3Demuxer.cpp)
- Pure test code only (no headers, separators, or metadata)
- Organized by commit type (fixing/ and regressor/ subdirectories)

STRATEGY - THREE-LAYER FILTERING:
1. Load Step 9 results
2. Filter by component keywords + filename matching (eliminates 99% of false candidates)
3. Filter by actual #include/import statements in file content (eliminates remaining false positives)
4. Only return tests that truly reference the source file
5. Use BugBug data as fallback when commits are not found in local repos
6. Generate a summary report of all findings
"""

import json
import os
import subprocess
import re
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path


class TestFileExtractor:
    """Extract test files that reference matched source files at specific commits"""
    
    TEST_DIR_PATTERNS = [
        'test/',
        'tests/',
        'testing/mochitest/',
        'testing/xpcshell/',
    ]
    
    TEST_INDICATORS = ['test', 'spec', 'mock', 'stub', 'fixture']
    TEST_EXTENSIONS = ['.js', '.cpp', '.h', '.py', '.mochitest', '.xpcshell']
    
    def __init__(self, local_repos: Dict[str, str], output_dir: str = "test_extraction", 
                 bugbug_data_dir: Optional[str] = None, debug: bool = False):
        self.local_repos = local_repos
        self.output_dir = output_dir
        self.debug = debug
        
        os.makedirs(output_dir, exist_ok=True)
        
        self._print_header("Repository Configuration")
        for name, path in self.local_repos.items():
            status = "✓" if os.path.exists(path) else "✗"
            self._print_item(f"{status} {name}: {path}")
        self._print_blank()
        
        self.bugbug_data_dir = None
        self.bugbug_cache = None
        
        if bugbug_data_dir:
            self.bugbug_data_dir = bugbug_data_dir
        else:
            possible_locations = [
                './bugbug_data',
                './bugbug',
                os.path.expanduser('~/.bugbug'),
                os.path.expanduser('~/bugbug_data'),
                './data/bugbug',
                '../bugbug_data',
            ]
            
            for location in possible_locations:
                if os.path.exists(location):
                    self.bugbug_data_dir = location
                    break
        
        self._print_header("BugBug Configuration")
        try:
            from bugbug_utils import get_bugbug_cache
            self.bugbug_cache = get_bugbug_cache()
            self._print_item(f"✓ BugBug package loaded: {self.bugbug_cache.count()} bugs available")
            
            if self.bugbug_data_dir and os.path.exists(self.bugbug_data_dir):
                self._print_item(f"✓ BugBug data directory: {self.bugbug_data_dir}")
            else:
                self._print_item("⚠ BugBug data directory not found (searched common locations)")
                self.bugbug_data_dir = None
        except ImportError:
            self._print_item("⚠ bugbug_utils not available (BugBug fallback disabled)")
            if self.bugbug_data_dir and os.path.exists(self.bugbug_data_dir):
                self._print_item(f"  But found BugBug data directory: {self.bugbug_data_dir}")
        except Exception as e:
            self._print_item(f"⚠ Error loading BugBug: {e}")
        self._print_blank()
    
    # ==================================================================================
    # PRINTING UTILITIES
    # ==================================================================================
    
    def _print_header(self, text: str):
        """Print a section header"""
        print(f"\n{'─' * 80}")
        print(f" {text}")
        print(f"{'─' * 80}")
    
    def _print_section(self, text: str, level: int = 0):
        """Print a subsection"""
        indent = "  " * level
        print(f"{indent}▶ {text}")
    
    def _print_item(self, text: str, level: int = 0):
        """Print an item"""
        indent = "  " * level
        print(f"{indent}{text}")
    
    def _print_match_result(self, match_idx: int, fixing_count: int, regressor_count: int, 
                           fixing_tests: List[str], regressor_tests: List[str], 
                           fixing_repo: str, regressor_repo: str):
        """Print test match results"""
        status = "✓" if (fixing_count > 0 or regressor_count > 0) else "─"
        print(f"\n    [{status}] Match {match_idx}")
        
        self._print_item(f"Fixing Commit: {fixing_count} test(s) [found in: {fixing_repo}]", 2)
        for test in fixing_tests:
            self._print_item(f"→ {test}", 3)
        
        self._print_item(f"Regressor Commit: {regressor_count} test(s) [found in: {regressor_repo}]", 2)
        for test in regressor_tests:
            self._print_item(f"→ {test}", 3)
    
    def _print_blank(self):
        """Print blank line"""
        print()
    
    # ==================================================================================
    # CORE LOGIC
    # ==================================================================================
    
    def _is_test_file(self, filename: str) -> bool:
        """Check if filename is a test file"""
        lower_name = filename.lower()
        
        has_test_ext = any(lower_name.endswith(ext) for ext in self.TEST_EXTENSIONS)
        if not has_test_ext:
            return False
        
        return any(indicator in lower_name for indicator in self.TEST_INDICATORS)
    
    def find_tests_in_bugbug(self, filepath: str, file_base: str, commit_hash: str) -> List[Dict]:
        """Search BugBug data directory for test files related to source file"""
        
        if not self.bugbug_data_dir or not os.path.exists(self.bugbug_data_dir):
            return []
        
        test_files = []
        source_base_lower = file_base.lower()
        
        try:
            bugbug_files = [
                'bugs.json',
                'commits.json', 
                'revisions.json',
                'fixed_comments.json'
            ]
            
            for filename in bugbug_files:
                filepath_full = os.path.join(self.bugbug_data_dir, filename)
                
                if not os.path.exists(filepath_full):
                    continue
                
                try:
                    if filename == 'commits.json':
                        with open(filepath_full, 'r', encoding='utf-8', errors='ignore') as f:
                            chunk_size = 1024 * 1024
                            while True:
                                chunk = f.read(chunk_size)
                                if not chunk:
                                    break
                                
                                if commit_hash[:12] in chunk or source_base_lower in chunk.lower():
                                    test_patterns = [
                                        r'test[^"]*\.(?:js|cpp|h|py)',
                                        r'tests/[^"]+',
                                        r'testing/[^"]+'
                                    ]
                                    
                                    for pattern in test_patterns:
                                        matches = re.findall(pattern, chunk, re.IGNORECASE)
                                        for match in matches[:5]:
                                            if source_base_lower in match.lower():
                                                test_files.append({
                                                    'path': match,
                                                    'type': 'bugbug_commit_test',
                                                    'source': filepath_full,
                                                    'references': [f"Test found in commits.json"],
                                                    'reference_count': 1
                                                })
                                    
                                    if test_files:
                                        break
                    
                    elif filename == 'bugs.json':
                        with open(filepath_full, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(10240)
                            if source_base_lower in content.lower():
                                test_files.append({
                                    'path': filepath_full,
                                    'type': 'bugbug_bug_reference',
                                    'references': [f"Source file referenced in bugs.json"],
                                    'reference_count': 1
                                })
                    
                    elif filename == 'revisions.json':
                        with open(filepath_full, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(10240)
                            if commit_hash[:12] in content:
                                test_matches = re.findall(r'"([^"]*test[^"]*)"', content, re.IGNORECASE)
                                for test_match in test_matches[:3]:
                                    if '.js' in test_match or '.cpp' in test_match:
                                        test_files.append({
                                            'path': test_match,
                                            'type': 'bugbug_revision_test',
                                            'source': filepath_full,
                                            'references': [f"Test in revision {commit_hash[:8]}"],
                                            'reference_count': 1
                                        })
                
                except Exception as e:
                    continue
            
            test_dirs = ['tests', 'test_data', 'test_files']
            for test_dir_name in test_dirs:
                test_dir = os.path.join(self.bugbug_data_dir, test_dir_name)
                if os.path.exists(test_dir) and os.path.isdir(test_dir):
                    for root, dirs, files in os.walk(test_dir):
                        for file in files[:50]:
                            if self._is_test_file(file):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                        content = f.read(5000)
                                        if source_base_lower in content.lower():
                                            test_files.append({
                                                'path': file_path,
                                                'type': 'bugbug_test_file',
                                                'references': [f"Test file in {test_dir_name}"],
                                                'reference_count': 1
                                            })
                                except:
                                    pass
        
        except Exception as e:
            pass
        
        return test_files
    
    def _extract_source_references(self, test_content: str, filepath: str, file_base: str, file_name: str) -> List[str]:
        """Extract DIRECT references to the source file from test content"""
        references = []
        
        source_base = file_base.lower()
        source_name = file_name.lower()
        
        include_pattern = r'#include\s+[<"]([^>"]+)[>"]'
        for match in re.finditer(include_pattern, test_content, re.IGNORECASE):
            include_path = match.group(1).lower()
            
            if (source_base in include_path or 
                source_name in include_path or
                include_path.endswith(f"{source_base}.h") or
                include_path.endswith(f"{source_base}.cpp")):
                references.append(f"include: {match.group(1)}")
        
        import_pattern = r'(?:require|import)\s+[\'"]([^\'"]+)[\'"]'
        for match in re.finditer(import_pattern, test_content, re.IGNORECASE):
            import_path = match.group(1).lower()
            
            if (source_base in import_path or 
                source_name in import_path or
                import_path.endswith(source_base)):
                references.append(f"import: {match.group(1)}")
        
        gtest_pattern = r'require_or_import\s*\(\s*[\'"]([^\'"]+)[\'"]'
        for match in re.finditer(gtest_pattern, test_content, re.IGNORECASE):
            import_path = match.group(1).lower()
            if source_base in import_path or source_name in import_path:
                references.append(f"require_or_import: {match.group(1)}")
        
        return references
    
    def find_tests_at_commit(self, commit_hash: str, filepath: str, repo_path: str) -> List[Dict]:
        """Find test files referencing source file at a specific commit (THREE-LAYER FILTERING)"""
        
        test_files = []
        file_name = os.path.basename(filepath)
        file_base = os.path.splitext(file_name)[0]
        source_path_lower = filepath.lower()
        path_parts = source_path_lower.split('/')[:-1]
        
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
            
            # LAYER 1: Find test files in test directories
            candidates = []
            for test_file in all_files:
                if not any(pattern in test_file.lower() for pattern in self.TEST_DIR_PATTERNS):
                    continue
                
                if not self._is_test_file(test_file):
                    continue
                
                candidates.append(test_file)
            
            # LAYER 2: Keep tests where BOTH component keywords AND source base name match
            filtered = []
            source_base_lower = file_base.lower()
            
            for candidate in candidates:
                candidate_lower = candidate.lower()
                
                has_component = any(comp in candidate_lower for comp in component_keywords)
                
                test_filename = os.path.basename(candidate_lower)
                filename_mentions_source = (source_base_lower in test_filename or
                                           test_filename.startswith("test" + source_base_lower) or
                                           test_filename.startswith(source_base_lower + "test") or
                                           "test" + source_base_lower in test_filename or
                                           source_base_lower + "test" in test_filename)
                
                if has_component and filename_mentions_source:
                    filtered.append(candidate)
            
            # LAYER 3: Read content and verify actual #include/import statements
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
                            'content': content,  # Store the content
                            'references': references,
                            'reference_count': len(references)
                        })
                
                except subprocess.TimeoutExpired:
                    continue
                except Exception as e:
                    continue
        
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            pass
        
        return test_files
    
    def get_tests_for_commit(self, commit_hash: str, filepath: str) -> Dict:
        """Get test files for a file at a specific commit from any available repo or BugBug data"""
        
        tests_found = []
        found_in_repo = None
        file_name = os.path.basename(filepath)
        file_base = os.path.splitext(file_name)[0]
        
        # First try Mercurial repos
        for repo_name, repo_path in self.local_repos.items():
            if not os.path.exists(repo_path):
                continue
            
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
                
            except subprocess.TimeoutExpired:
                pass
            except Exception as e:
                continue
        
        # If no repo had this commit, check BugBug data (fallback only)
        bugbug_tests = self.find_tests_in_bugbug(filepath, file_base, commit_hash)
        if bugbug_tests:
            tests_found.extend(bugbug_tests)
            found_in_repo = "bugbug_data"
        
        return {
            'tests_found': tests_found,
            'test_count': len(tests_found),
            'found_in_repo': found_in_repo
        }
    
    def process_match(self, bug_id: str, filepath: str, match_idx: int, fixing_hash: str, regressor_hash: str) -> Dict:
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
    
    def _save_test_file(self, test_content: str, output_path: str):
        """Save pure test file content (no headers, separators, or metadata)"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(test_content)
    
    def save_test_results(self, filepath: str, bug_id: str, match_idx: int, result: Dict):
        """Save test extraction results with individual test files"""
        
        safe_filepath = filepath.replace('/', '_').replace('.cpp', '').replace('.h', '').replace('.js', '')
        match_dir = os.path.join(self.output_dir, f"bug_{bug_id}", safe_filepath, f"match_{match_idx}")
        os.makedirs(match_dir, exist_ok=True)
        
        # Save metadata JSON
        json_file = os.path.join(match_dir, 'tests_found.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            # Create metadata version without content
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
            json.dump(metadata, f, indent=2)
        
        # Save summary TXT
        txt_file = os.path.join(match_dir, 'tests_summary.txt')
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("TEST FILES EXTRACTION SUMMARY\n")
            f.write("="*80 + "\n\n")
            
            f.write(f"Bug: {bug_id}\n")
            f.write(f"File: {filepath}\n")
            f.write(f"Match: {result['match_idx']}\n\n")
            
            f.write(f"FIXING COMMIT: {result['fixing_commit']['hash']}\n")
            f.write(f"Repository: {result['fixing_commit']['found_in_repo']}\n")
            f.write(f"Tests found: {result['fixing_commit']['test_count']}\n")
            if result['fixing_commit']['tests']:
                for test in result['fixing_commit']['tests']:
                    f.write(f"  - {test['path']}\n")
                    for ref in test.get('references', []):
                        f.write(f"      → {ref}\n")
            else:
                f.write("  (no tests found)\n")
            
            f.write(f"\nREGRESSOR COMMIT: {result['regressor_commit']['hash']}\n")
            f.write(f"Repository: {result['regressor_commit']['found_in_repo']}\n")
            f.write(f"Tests found: {result['regressor_commit']['test_count']}\n")
            if result['regressor_commit']['tests']:
                for test in result['regressor_commit']['tests']:
                    f.write(f"  - {test['path']}\n")
                    for ref in test.get('references', []):
                        f.write(f"      → {ref}\n")
            else:
                f.write("  (no tests found)\n")
        
        # Save test files organized by commit type
        # Fixing commit tests
        if result['fixing_commit']['tests']:
            fixing_dir = os.path.join(match_dir, 'fixing')
            os.makedirs(fixing_dir, exist_ok=True)
            
            for test in result['fixing_commit']['tests']:
                test_filename = os.path.basename(test['path'])
                test_file_path = os.path.join(fixing_dir, test_filename)
                
                if 'content' in test:
                    self._save_test_file(test['content'], test_file_path)
        
        # Regressor commit tests
        if result['regressor_commit']['tests']:
            regressor_dir = os.path.join(match_dir, 'regressor')
            os.makedirs(regressor_dir, exist_ok=True)
            
            for test in result['regressor_commit']['tests']:
                test_filename = os.path.basename(test['path'])
                test_file_path = os.path.join(regressor_dir, test_filename)
                
                if 'content' in test:
                    self._save_test_file(test['content'], test_file_path)
    
    def save_summary_report(self, all_results: Dict):
        """Save a comprehensive summary report of all test findings"""
        
        summary_data = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_bugs': 0,
                'total_files': 0,
                'total_matches': 0,
                'matches_with_tests': 0,
                'matches_with_bugbug_tests': 0,
                'coverage_percentage': 0.0
            },
            'bugs': {}
        }
        
        bugbug_test_count = 0
        
        for bug_id, bug_data in all_results['bugs'].items():
            summary_data['bugs'][bug_id] = {'files': {}}
            
            for file_data in bug_data['files']:
                filepath = file_data['filepath']
                summary_data['bugs'][bug_id]['files'][filepath] = {'matches': []}
                
                for match in file_data['matches']:
                    has_bugbug = False
                    if match['fixing_commit']['found_in_repo'] == 'bugbug_data':
                        has_bugbug = True
                    if match['regressor_commit']['found_in_repo'] == 'bugbug_data':
                        has_bugbug = True
                    
                    if has_bugbug:
                        bugbug_test_count += 1
                    
                    match_summary = {
                        'match_idx': match['match_idx'],
                        'fixing_commit': {
                            'hash': match['fixing_commit']['hash'],
                            'test_count': match['fixing_commit']['test_count'],
                            'found_in': match['fixing_commit']['found_in_repo'],
                            'tests': [t['path'] for t in match['fixing_commit']['tests']]
                        },
                        'regressor_commit': {
                            'hash': match['regressor_commit']['hash'],
                            'test_count': match['regressor_commit']['test_count'],
                            'found_in': match['regressor_commit']['found_in_repo'],
                            'tests': [t['path'] for t in match['regressor_commit']['tests']]
                        }
                    }
                    summary_data['bugs'][bug_id]['files'][filepath]['matches'].append(match_summary)
        
        summary_data['summary']['total_bugs'] = all_results['summary']['bugs']
        summary_data['summary']['total_files'] = all_results['summary']['files']
        summary_data['summary']['total_matches'] = all_results['summary']['matches']
        summary_data['summary']['matches_with_tests'] = all_results['summary']['matches_with_tests']
        summary_data['summary']['matches_with_bugbug_tests'] = bugbug_test_count
        
        if all_results['summary']['matches'] > 0:
            summary_data['summary']['coverage_percentage'] = \
                (all_results['summary']['matches_with_tests'] / all_results['summary']['matches']) * 100
        
        json_summary_file = os.path.join(self.output_dir, 'SUMMARY_tests_found.json')
        with open(json_summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=2)
        
        txt_summary_file = os.path.join(self.output_dir, 'SUMMARY_tests_found.txt')
        with open(txt_summary_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("TEST FILES EXTRACTION - COMPREHENSIVE SUMMARY REPORT\n")
            f.write("="*80 + "\n\n")
            
            f.write("OVERALL STATISTICS:\n")
            f.write("-" * 80 + "\n")
            f.write(f"Total Bugs: {summary_data['summary']['total_bugs']}\n")
            f.write(f"Total Files: {summary_data['summary']['total_files']}\n")
            f.write(f"Total Matches: {summary_data['summary']['total_matches']}\n")
            f.write(f"Matches with Tests: {summary_data['summary']['matches_with_tests']}\n")
            f.write(f"Matches with BugBug Tests: {summary_data['summary']['matches_with_bugbug_tests']}\n")
            f.write(f"Coverage: {summary_data['summary']['coverage_percentage']:.1f}%\n\n")
            
            f.write("="*80 + "\n")
            f.write("DETAILED FINDINGS BY BUG AND FILE:\n")
            f.write("="*80 + "\n\n")
            
            for bug_id, bug_info in summary_data['bugs'].items():
                f.write(f"BUG {bug_id}:\n")
                f.write("-" * 80 + "\n")
                
                for filepath, file_info in bug_info['files'].items():
                    f.write(f"  File: {filepath}\n")
                    
                    for match in file_info['matches']:
                        f.write(f"    Match {match['match_idx']}:\n")
                        
                        f.write(f"      Fixing Commit: {match['fixing_commit']['hash'][:8]}\n")
                        f.write(f"        Found in: {match['fixing_commit']['found_in']}\n")
                        f.write(f"        Tests found: {match['fixing_commit']['test_count']}\n")
                        f.write(f"        Location: fixing/\n")
                        if match['fixing_commit']['tests']:
                            for test in match['fixing_commit']['tests']:
                                f.write(f"          - {test}\n")
                        
                        f.write(f"      Regressor Commit: {match['regressor_commit']['hash'][:8]}\n")
                        f.write(f"        Found in: {match['regressor_commit']['found_in']}\n")
                        f.write(f"        Tests found: {match['regressor_commit']['test_count']}\n")
                        f.write(f"        Location: regressor/\n")
                        if match['regressor_commit']['tests']:
                            for test in match['regressor_commit']['tests']:
                                f.write(f"          - {test}\n")
                        f.write("\n")
    
    def extract_all(self) -> Dict:
        """Extract tests from Step 9 matches"""
        
        self._print_header("STEP 11: TEST FILE EXTRACTION (REVISED)")
        self._print_item("Three-Layer Filtering Strategy + BugBug Data Fallback")
        self._print_item("Output: Pure test files saved with actual filenames")
        self._print_blank()
        
        step9_json = "step9_fixing_regressor_method_matching/Step9_fixing_regressor_matches.json"
        
        if not os.path.exists(step9_json):
            step9_json = "fixing_regressor_analysis/Step9_fixing_regressor_matches.json"
            if not os.path.exists(step9_json):
                self._print_item("✗ ERROR: Step 9 file not found in either location:")
                self._print_item("  - step9_fixing_regressor_method_matching/Step9_fixing_regressor_matches.json")
                self._print_item("  - fixing_regressor_analysis/Step9_fixing_regressor_matches.json")
                return {}
        
        self._print_item(f"Loading: {step9_json}")
        self._print_blank()
        
        with open(step9_json, 'r') as f:
            step9_data = json.load(f)
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'step9_source': step9_json,
            'local_repos': self.local_repos,
            'bugbug_data_dir': self.bugbug_data_dir,
            'output_dir': self.output_dir,
            'summary': {
                'bugs': 0,
                'files': 0,
                'matches': 0,
                'matches_with_tests': 0
            },
            'bugs': {}
        }
        
        total_bugs = len(step9_data['bugs'])
        current_bug = 0
        
        for bug_id, bug_data in step9_data['bugs'].items():
            current_bug += 1
            self._print_header(f"BUG {bug_id} ({current_bug}/{total_bugs})")
            
            bug_results = {'files': []}
            total_files = len(bug_data['files'])
            current_file = 0
            
            for file_data in bug_data['files']:
                current_file += 1
                filepath = file_data['filepath']
                self._print_section(f"File: {filepath} ({current_file}/{total_files})")
                
                file_results = {'filepath': filepath, 'matches': []}
                total_matches = len(file_data['matches'])
                
                for match_idx, match in enumerate(file_data['matches'], 1):
                    fixing_hash = match['fixing_commit']['hash']
                    regressor_hash = match['regressor_commit']['hash']
                    
                    result = self.process_match(bug_id, filepath, match_idx, fixing_hash, regressor_hash)
                    file_results['matches'].append(result)
                    results['summary']['matches'] += 1
                    
                    fixing_cnt = result['fixing_commit']['test_count']
                    regressor_cnt = result['regressor_commit']['test_count']
                    fixing_tests = [t['path'] for t in result['fixing_commit']['tests']]
                    regressor_tests = [t['path'] for t in result['regressor_commit']['tests']]
                    fixing_repo = result['fixing_commit']['found_in_repo']
                    regressor_repo = result['regressor_commit']['found_in_repo']
                    
                    if fixing_cnt > 0 or regressor_cnt > 0:
                        results['summary']['matches_with_tests'] += 1
                    
                    self.save_test_results(filepath, bug_id, match_idx, result)
                    
                    self._print_match_result(match_idx, fixing_cnt, regressor_cnt, 
                                            fixing_tests, regressor_tests,
                                            fixing_repo, regressor_repo)
                
                if file_results['matches']:
                    bug_results['files'].append(file_results)
                    results['summary']['files'] += 1
            
            if bug_results['files']:
                results['bugs'][bug_id] = bug_results
                results['summary']['bugs'] += 1
        
        self.save_summary_report(results)
        
        self._print_header("EXTRACTION COMPLETE")
        print(f"\n   STATISTICS:")
        self._print_item(f"Bugs processed: {results['summary']['bugs']}", 1)
        self._print_item(f"Files analyzed: {results['summary']['files']}", 1)
        self._print_item(f"Total matches: {results['summary']['matches']}", 1)
        self._print_item(f"Matches with tests: {results['summary']['matches_with_tests']}", 1)
        
        if results['summary']['matches'] > 0:
            pct = (results['summary']['matches_with_tests'] / results['summary']['matches']) * 100
            self._print_item(f"Coverage: {pct:.1f}%", 1)
        
        print(f"\n   OUTPUT STRUCTURE:")
        self._print_item(f"test_extraction/", 1)
        self._print_item(f"├── bug_{{bug_id}}/", 2)
        self._print_item(f"│   ├── {{source_file}}/", 3)
        self._print_item(f"│   │   ├── match_1/", 4)
        self._print_item(f"│   │   │   ├── fixing/", 5)
        self._print_item(f"│   │   │   │   ├── TestFile1.cpp (pure code, no headers)", 6)
        self._print_item(f"│   │   │   │   └── TestFile2.js", 6)
        self._print_item(f"│   │   │   ├── regressor/", 5)
        self._print_item(f"│   │   │   │   ├── TestFile1.cpp", 6)
        self._print_item(f"│   │   │   │   └── TestFile2.js", 6)
        self._print_item(f"│   │   │   ├── tests_found.json", 5)
        self._print_item(f"│   │   │   └── tests_summary.txt", 5)
        self._print_item(f"├── SUMMARY_tests_found.json", 2)
        self._print_item(f"└── SUMMARY_tests_found.txt", 2)
        
        print(f"\n   FILES:")
        self._print_item(f"Directory: {self.output_dir}/", 1)
        self._print_item(f"Summary JSON: SUMMARY_tests_found.json", 1)
        self._print_item(f"Summary TXT: SUMMARY_tests_found.txt", 1)
        self._print_item(f"Test files: Organized in fixing/ and regressor/ subdirs", 1)
        
        if self.bugbug_data_dir:
            print(f"\n  🔧 BUGBUG:")
            self._print_item(f"Data directory: {self.bugbug_data_dir}", 1)
        
        self._print_blank()
        
        return results


def main():
    """Main execution"""
    
    local_repos = {
        'mozilla-central': './mozilla-central',
        'mozilla-release': './mozilla-release',
        'mozilla-autoland': './mozilla-autoland',
        'mozilla-esr115': './mozilla-esr115'
    }
    
    bugbug_data_dir = './data'
    
    extractor = TestFileExtractor(
        local_repos,
        output_dir="test_extraction",
        bugbug_data_dir=bugbug_data_dir,
        debug=False
    )
    
    extractor.extract_all()
    
    print("="*80)
    print(" ✓ DONE!")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()