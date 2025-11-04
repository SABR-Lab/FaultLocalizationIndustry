#!/usr/bin/env python3
"""
Step 12: Test Validation Against Regression/Fix Commits (REVISED)
Uses Step 11 (test files in organized directories) and Step 10 (full source content).

REVISED FOR STEP 11's NEW OUTPUT FORMAT:
- Test files are now in: test_extraction/bug_X/file/match_Y/{fixing|regressor}/TestFile.ext
- Each test file contains pure code (no headers or metadata)
- Test files have their actual filenames

Data Flow:
1. Load Step 11 results → Get test file paths and commit info
2. Read test files directly from fixing/ and regressor/ subdirectories
3. Load source content from Step 10 (full source at both commits)
4. For each test:
   - If test is identical at both commits → use just one
   - If different → use fixing commit version
   - Create temp environment with source code from Step 10
   - Run test against regressor version (expect FAIL)
   - Run test against fixing version (expect PASS)
5. Compare results and report

- Uses clang++ for compilation
- Searches for missing headers in local Mozilla repository
- Gracefully handles missing external dependencies
- Better error reporting and status categorization
"""

import json
import os
import subprocess
import tempfile
import shutil
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import logging


class TestValidator:
    """Validate test behavior against regression and fix commits"""
    
    def __init__(self,
                 step11_results_dir: str,
                 step10_results_dir: str,
                 output_dir: str = "step12_test_validation",
                 repo_root: str = "./mozilla-central",
                 verbose: bool = False,
                 timeout: int = 60,
                 local_repos: Dict[str, str] = None):
        """
        Initialize test validator.
        
        Args:
            step11_results_dir: Directory containing Step 11 results
            step10_results_dir: Directory containing Step 10 results
            output_dir: Directory for output files
            repo_root: Path to Mozilla repo root (for mach commands)
            verbose: Enable verbose logging
            timeout: Test execution timeout in seconds
            local_repos: Dict of local Mozilla repos to search
        """
        self.step11_results_dir = step11_results_dir
        self.step10_results_dir = step10_results_dir
        self.output_dir = output_dir
        self.repo_root = repo_root
        self.verbose = verbose
        self.timeout = timeout
        
        # Local repos to search for headers
        self.local_repos = local_repos or {
            'mozilla-central': './mozilla-central',
            'mozilla-release': './mozilla-release',
            'mozilla-autoland': './mozilla-autoland',
            'mozilla-esr115': './mozilla-esr115'
        }
        
        self._setup_logging()
        os.makedirs(output_dir, exist_ok=True)
        
        # Cache for header file locations
        self.header_cache = {}
        self._build_header_cache()
        
        self.step11_summary = self._load_step11_summary()
    
    def _setup_logging(self):
        """Configure logging"""
        level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(level=level, format='%(message)s')
        self.logger = logging.getLogger(__name__)
    
    def _build_header_cache(self):
        """Pre-scan all local repositories for common header files"""
        self.logger.info("Building header file cache from local repositories...")
        
        scan_dirs = []
        
        # Add all local repos
        for repo_name, repo_path in self.local_repos.items():
            if os.path.exists(repo_path):
                self.logger.info(f"  Scanning {repo_name} at {repo_path}")
                scan_dirs.append(repo_path)
            else:
                self.logger.warning(f"  Repository not found: {repo_path}")
        
        if not scan_dirs:
            self.logger.warning("No local repositories found to scan!")
            return
        
        # Common Mozilla source directories to scan
        source_subdirs = [
            '',  # Root
            'media',
            'dom',
            'gfx',
            'layout',
            'widget',
            'netwerk',
            'third_party',
            'js',
            'xpcom',
            'accessible',
            'storage',
            'toolkit',
            'devtools',
        ]
        
        headers_found = 0
        repos_scanned = 0
        
        for repo_path in scan_dirs:
            repos_scanned += 1
            
            # Scan specific subdirectories for faster indexing
            for subdir in source_subdirs:
                if subdir:
                    scan_path = os.path.join(repo_path, subdir)
                else:
                    scan_path = repo_path
                
                if not os.path.exists(scan_path):
                    continue
                
                try:
                    for root, dirs, files in os.walk(scan_path):
                        # Skip hidden, build, and node_modules directories
                        dirs[:] = [d for d in dirs if not d.startswith('.') 
                                  and d not in ['obj-*', '__pycache__', 'node_modules', 'build']]
                        
                        for file in files:
                            if file.endswith(('.h', '.hpp', '.hxx')):
                                # Cache by filename
                                if file not in self.header_cache:
                                    self.header_cache[file] = []
                                
                                file_path = os.path.join(root, file)
                                self.header_cache[file].append(file_path)
                                headers_found += 1
                except Exception as e:
                    self.logger.debug(f"Error scanning {scan_path}: {e}")
        
        self.logger.info(f"✓ Indexed {headers_found} header files from {repos_scanned} repositories")
        self.logger.info(f"  Found {len(self.header_cache)} unique header names")
    
    def _load_step11_summary(self) -> Optional[Dict]:
        """Load Step 11 summary results"""
        step11_summary = os.path.join(self.step11_results_dir, 'SUMMARY_tests_found.json')
        
        if not os.path.exists(step11_summary):
            self.logger.error(f"Step 11 summary not found: {step11_summary}")
            return None
        
        self.logger.info(f"Loading Step 11 results from: {step11_summary}")
        with open(step11_summary, 'r') as f:
            return json.load(f)
    
    def _extract_test_content_from_step11(self, 
                                         bug_id: str, 
                                         filepath: str, 
                                         match_idx: int,
                                         test_path: str,
                                         commit_type: str) -> Optional[str]:
        """Extract test file content from Step 11's organized directory structure.
        
        Step 11 now saves test files in:
        test_extraction/bug_{bug_id}/{safe_filepath}/match_{match_idx}/{commit_type}/{test_filename}
        """
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        safe_filepath = safe_filepath.replace('.cpp', '').replace('.h', '').replace('.js', '').replace('.py', '')
        
        # Get test filename from path
        test_filename = os.path.basename(test_path)
        
        # Build path to test file
        test_file_path = os.path.join(
            self.step11_results_dir,
            f"bug_{bug_id}",
            safe_filepath,
            f"match_{match_idx}",
            commit_type.lower(),  # 'fixing' or 'regressor'
            test_filename
        )
        
        if not os.path.exists(test_file_path):
            self.logger.warning(f"Step 11 test file not found: {test_file_path}")
            return None
        
        try:
            with open(test_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            if not content or len(content.strip()) == 0:
                self.logger.warning(f"Empty test content for {test_filename}")
                return None
            
            self.logger.debug(f"Extracted test content for {test_filename} ({len(content)} bytes)")
            return content
        
        except Exception as e:
            self.logger.error(f"Error reading Step 11 test file: {e}")
            return None
    
    def _find_source_content_in_step10(self, 
                                      bug_id: str, 
                                      filepath: str, 
                                      commit_hash: str) -> Optional[str]:
        """Find full source content from Step 10 directory structure."""
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        bug_dir = os.path.join(self.step10_results_dir, f"bug_{bug_id}", safe_filepath)
        
        if not os.path.exists(bug_dir):
            self.logger.warning(f"Step 10 bug dir not found: {bug_dir}")
            return None
        
        for root, dirs, files in os.walk(bug_dir):
            for file in files:
                if file.endswith('.full') and commit_hash in file:
                    full_file_path = os.path.join(root, file)
                    try:
                        with open(full_file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.logger.debug(f"Found source content in Step 10: {full_file_path}")
                        return content
                    except Exception as e:
                        self.logger.warning(f"Error reading Step 10 file: {e}")
        
        self.logger.warning(f"Could not find Step 10 content for {bug_id}/{filepath}/{commit_hash[:8]}")
        return None
    
    def _detect_test_type(self, test_path: str) -> str:
        """Detect test type from path"""
        test_lower = test_path.lower()
        
        if 'mochitest' in test_lower:
            return 'mochitest'
        elif 'xpcshell' in test_lower:
            return 'xpcshell'
        elif test_lower.endswith('.cpp') or test_lower.endswith('.h'):
            return 'cpp'
        elif test_lower.endswith('.js'):
            return 'javascript'
        elif test_lower.endswith('.py'):
            return 'python'
        elif test_lower.endswith('.html'):
            return 'html'
        
        return 'unknown'
    
    def _can_run_cpp_test(self, test_content: str, source_content: str) -> bool:
        """Check if test can realistically be compiled and run"""
        # If test contains gtest framework markers, it's likely a real test
        if 'TEST(' in test_content or 'TEST_F(' in test_content or '#include <gtest' in test_content:
            return True
        
        # If it has main() function, it's executable
        if 'int main' in test_content or 'int main(' in source_content:
            return True
        
        # Otherwise, it's likely just source code, not a compilable test
        return False
    
    def _find_header_in_cache(self, header_name: str) -> List[str]:
        """Find all header paths from cache across all repos"""
        if header_name in self.header_cache:
            return self.header_cache[header_name]
        return []
    
    def _find_header_in_repos(self, header_name: str, header_subdir: str = None) -> str:
        """Find header file in local repos with fallback search"""
        # Standard C++ library headers - don't need to be found
        cpp_std_headers = {
            'algorithm', 'array', 'atomic', 'bitset', 'cassert', 'cctype', 'cerrno',
            'cfloat', 'chrono', 'cinttypes', 'ciso646', 'climits', 'clocale', 'cmath',
            'codecvt', 'complex', 'condition_variable', 'cstdarg', 'cstddef', 'cstdint',
            'cstdio', 'cstdlib', 'cstring', 'ctypeinfo', 'ctime', 'cwchar', 'cwctype',
            'deque', 'exception', 'execution', 'fstream', 'functional', 'future',
            'iomanip', 'ios', 'iosfwd', 'iostream', 'istream', 'iterator', 'limits',
            'list', 'locale', 'map', 'memory', 'memory_resource', 'mutex', 'new',
            'numeric', 'optional', 'ostream', 'queue', 'random', 'ratio', 'regex',
            'scoped_allocator', 'set', 'shared_mutex', 'sstream', 'stack', 'stdexcept',
            'streambuf', 'string', 'string_view', 'strstream', 'thread', 'typeindex',
            'typeinfo', 'unordered_map', 'unordered_set', 'utility', 'valarray',
            'variant', 'vector', 'inttypes.h', 'assert.h', 'ctype.h', 'errno.h',
            'float.h', 'limits.h', 'locale.h', 'math.h', 'setjmp.h', 'signal.h',
            'stdarg.h', 'stddef.h', 'stdint.h', 'stdio.h', 'stdlib.h', 'string.h',
            'time.h', 'wchar.h', 'wctype.h'
        }
        
        if header_name in cpp_std_headers:
            self.logger.debug(f"Standard C++ library header: {header_name}")
            return None  # Will be found by system compiler
        
        # Check cache first
        cached_paths = self._find_header_in_cache(header_name)
        if cached_paths:
            self.logger.debug(f"Found {header_name} at {cached_paths}")
            # Prefer exact subdir match if provided
            if header_subdir:
                for path in cached_paths:
                    if header_subdir in path:
                        # Convert to absolute path
                        abs_path = os.path.abspath(os.path.dirname(path))
                        return abs_path
            # Otherwise return directory of first match (absolute path)
            abs_path = os.path.abspath(os.path.dirname(cached_paths[0]))
            return abs_path
        
        # Fallback: search each repo
        for repo_name, repo_path in self.local_repos.items():
            if not os.path.exists(repo_path):
                continue
            
            # Try exact path if subdir is specified
            if header_subdir:
                possible_path = os.path.join(repo_path, header_subdir, header_name)
                if os.path.exists(possible_path):
                    return os.path.abspath(os.path.dirname(possible_path))
            
            # Try recursive search with depth limit
            try:
                for root, dirs, files in os.walk(repo_path):
                    depth = root.count(os.sep) - repo_path.count(os.sep)
                    if depth > 5:  # Limit search depth
                        dirs[:] = []
                        continue
                    
                    if header_name in files:
                        return os.path.abspath(root)
            except Exception:
                continue
        
        return None
    
    def _find_header_paths(self, source_content: str, temp_dir: str, source_filepath: str = "") -> List[str]:
        """Find and collect all #include paths needed for compilation."""
        include_paths = set()
        found_headers = {}
        missing_headers = []
        
        # Standard system paths
        standard_paths = set([
            f'-I{temp_dir}',  # Current temp directory
            '-I/opt/homebrew/include',
            '-I/usr/local/include',
            '-I/usr/include',
        ])
        
        # Add source file's directory
        if source_filepath:
            source_dir = os.path.dirname(source_filepath)
            if source_dir:
                standard_paths.add(f'-I{source_dir}')
        
        # Extract #include directives from source
        include_pattern = re.compile(r'#include\s+[<"]([^>"]+)[>"]')
        includes = include_pattern.findall(source_content)
        
        self.logger.debug(f"Found {len(includes)} #include directives")
        
        # Search for each included file
        for include_file in includes:
            # Parse include path
            if '/' in include_file:
                parts = include_file.split('/')
                header_name = parts[-1]
                header_subdir = '/'.join(parts[:-1])
            else:
                header_name = include_file
                header_subdir = None
            
            # Try to find the header using the repo search
            header_dir = self._find_header_in_repos(header_name, header_subdir)
            
            if header_dir:
                standard_paths.add(f'-I{header_dir}')
                found_headers[include_file] = header_dir
                self.logger.debug(f"✓ Located: {include_file} → {header_dir}")
            else:
                # Check if it's a standard C++ header (will be found by compiler)
                cpp_std_headers = {
                    'algorithm', 'array', 'atomic', 'bitset', 'cassert', 'cctype', 'cerrno',
                    'cfloat', 'chrono', 'cinttypes', 'ciso646', 'climits', 'clocale', 'cmath',
                    'codecvt', 'complex', 'condition_variable', 'cstdarg', 'cstddef', 'cstdint',
                    'cstdio', 'cstdlib', 'cstring', 'ctypeinfo', 'ctime', 'cwchar', 'cwctype',
                    'deque', 'exception', 'execution', 'fstream', 'functional', 'future',
                    'iomanip', 'ios', 'iosfwd', 'iostream', 'istream', 'iterator', 'limits',
                    'list', 'locale', 'map', 'memory', 'memory_resource', 'mutex', 'new',
                    'numeric', 'optional', 'ostream', 'queue', 'random', 'ratio', 'regex',
                    'scoped_allocator', 'set', 'shared_mutex', 'sstream', 'stack', 'stdexcept',
                    'streambuf', 'string', 'string_view', 'strstream', 'thread', 'typeindex',
                    'typeinfo', 'unordered_map', 'unordered_set', 'utility', 'valarray',
                    'variant', 'vector', 'inttypes.h', 'assert.h', 'ctype.h', 'errno.h',
                    'float.h', 'limits.h', 'locale.h', 'math.h', 'setjmp.h', 'signal.h',
                    'stdarg.h', 'stddef.h', 'stdint.h', 'stdio.h', 'stdlib.h', 'string.h',
                    'time.h', 'wchar.h', 'wctype.h'
                }
                
                if header_name not in cpp_std_headers:
                    missing_headers.append(include_file)
                    self.logger.debug(f"✗ Missing local header: {include_file}")
        
        if missing_headers:
            self.logger.warning(f"Could not locate headers: {missing_headers}")
        
        return list(standard_paths)
    
    def _get_clang_include_paths(self) -> List[str]:
        """Get system include paths from clang - Apple version"""
        try:
            # Ask clang where it looks for headers
            result = subprocess.run(
                ['clang++', '-E', '-v', '-'],
                input='',
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # Parse the include paths from clang's output
            include_paths = []
            in_search_list = False
            
            for line in result.stderr.split('\n'):
                if '#include <...> search starts here:' in line:
                    in_search_list = True
                    continue
                if 'End of search list' in line:
                    break
                if in_search_list and line.strip() and not line.strip().endswith('(framework directory)'):
                    path = line.strip().replace(' (framework directory)', '')
                    if path and not path.startswith('#'):
                        include_paths.append(f'-I{path}')
            
            self.logger.debug(f"Found {len(include_paths)} clang system include paths:")
            for p in include_paths[:5]:
                self.logger.debug(f"  {p}")
            
            return include_paths
        except Exception as e:
            self.logger.warning(f"Could not get clang include paths: {e}")
            # Fallback for Apple's system clang
            return [
                '-I/usr/local/include',
                '-I/Library/Developer/CommandLineTools/usr/lib/clang/17/include',
                '-I/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include',
                '-I/Library/Developer/CommandLineTools/usr/include',
            ]
    
    def _create_stub_headers(self, missing_headers: List[str], temp_dir: str):
        """Create stub header files for missing dependencies"""
        created_count = 0
        for header_path in missing_headers:
            # Create the directory structure
            full_path = os.path.join(temp_dir, header_path)
            header_dir = os.path.dirname(full_path)
            
            try:
                # Ensure directory exists
                os.makedirs(header_dir, exist_ok=True)
                
                # Create a minimal stub header with proper guard
                guard_name = f"STUB_{header_path.upper().replace('/', '_').replace('.', '_').replace('-', '_')}"
                
                stub_content = f"""/* Auto-generated stub for: {header_path} */
#ifndef {guard_name}
#define {guard_name}

/* This is a minimal stub header for compilation */

#endif /* {guard_name} */
"""
                
                with open(full_path, 'w') as f:
                    f.write(stub_content)
                
                # Verify file was created
                if os.path.exists(full_path):
                    created_count += 1
                    self.logger.debug(f"✓ Stub created: {header_path}")
                else:
                    self.logger.warning(f"✗ Failed to verify stub for {header_path}")
                    
            except Exception as e:
                self.logger.warning(f"✗ Error creating stub for {header_path}: {e}")
        
        if created_count > 0:
            self.logger.debug(f"Successfully created {created_count} stub headers")
    
    def _create_test_env(self, 
                        test_content: str,
                        source_path: str,
                        source_content: str,
                        test_path: str) -> Tuple[str, str, str]:
        """Create isolated test environment with source and test files."""
        temp_dir = tempfile.mkdtemp(prefix='test_validate_')
        
        # Put all files in same directory for simpler include resolution
        source_filename = os.path.basename(source_path)
        test_filename = os.path.basename(test_path)
        
        source_file_path = os.path.join(temp_dir, source_filename)
        test_file_path = os.path.join(temp_dir, test_filename)
        
        try:
            with open(source_file_path, 'w', encoding='utf-8') as f:
                f.write(source_content)
        except Exception as e:
            self.logger.debug(f"Error writing source file: {e}")
            shutil.rmtree(temp_dir)
            return None, None, None
        
        try:
            with open(test_file_path, 'w', encoding='utf-8') as f:
                f.write(test_content)
        except Exception as e:
            self.logger.debug(f"Error writing test file: {e}")
            shutil.rmtree(temp_dir)
            return None, None, None
        
        return temp_dir, test_file_path, source_file_path
    
    def _run_javascript_test(self, test_file: str, temp_dir: str) -> Tuple[bool, str]:
        """Run JavaScript test"""
        commands = [
            ['mocha', test_file],
            ['node', test_file],
            ['jest', test_file]
        ]
        
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout
                )
                
                if result.returncode == 0 or 'not found' not in result.stderr.lower():
                    return result.returncode == 0, result.stdout + result.stderr
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
        
        return False, "No JavaScript test runner available"
    
    def _run_python_test(self, test_file: str, temp_dir: str) -> Tuple[bool, str]:
        """Run Python test"""
        commands = [
            ['python', '-m', 'pytest', test_file, '-v'],
            ['python', '-m', 'unittest', 'discover', '-s', os.path.dirname(test_file)],
            ['python', test_file]
        ]
        
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout
                )
                
                if result.returncode == 0 or 'not found' not in result.stderr.lower():
                    return result.returncode == 0, result.stdout + result.stderr
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
        
        return False, "No Python test runner available"
    
    def _run_cpp_test(self, test_file: str, source_file: str, temp_dir: str) -> Tuple[bool, str]:
        """Run C++ test (compile and run) with preemptive stub creation."""
        try:
            out_file = os.path.join(temp_dir, 'test_exe')
            
            # Read source and test to find all includes
            try:
                with open(source_file, 'r', encoding='utf-8', errors='ignore') as f:
                    source_content = f.read()
            except:
                source_content = ""
            
            try:
                with open(test_file, 'r', encoding='utf-8', errors='ignore') as f:
                    test_content = f.read()
            except:
                test_content = ""
            
            # Extract ALL includes
            include_pattern = re.compile(r'#include\s+[<"]([^>"]+)[>"]')
            all_includes = include_pattern.findall(source_content + "\n" + test_content)
            
            self.logger.debug(f"Found {len(all_includes)} total #include directives")
            
            # Standard C++ library headers that compiler provides
            cpp_std_headers = {
                'algorithm', 'array', 'atomic', 'bitset', 'cassert', 'cctype', 'cerrno',
                'cfloat', 'chrono', 'cinttypes', 'ciso646', 'climits', 'clocale', 'cmath',
                'codecvt', 'complex', 'condition_variable', 'cstdarg', 'cstddef', 'cstdint',
                'cstdio', 'cstdlib', 'cstring', 'ctypeinfo', 'ctime', 'cwchar', 'cwctype',
                'deque', 'exception', 'execution', 'fstream', 'functional', 'future',
                'iomanip', 'ios', 'iosfwd', 'iostream', 'istream', 'iterator', 'limits',
                'list', 'locale', 'map', 'memory', 'memory_resource', 'mutex', 'new',
                'numeric', 'optional', 'ostream', 'queue', 'random', 'ratio', 'regex',
                'scoped_allocator', 'set', 'shared_mutex', 'sstream', 'stack', 'stdexcept',
                'streambuf', 'string', 'string_view', 'strstream', 'thread', 'typeindex',
                'typeinfo', 'unordered_map', 'unordered_set', 'utility', 'valarray',
                'variant', 'vector', 'inttypes.h', 'assert.h', 'ctype.h', 'errno.h',
                'float.h', 'limits.h', 'locale.h', 'math.h', 'setjmp.h', 'signal.h',
                'stdarg.h', 'stddef.h', 'stdint.h', 'stdio.h', 'stdlib.h', 'string.h',
                'time.h', 'wchar.h', 'wctype.h'
            }
            
            # Separate headers that need to be found vs stubs (exclude standard library)
            local_headers_needed = []
            for inc in all_includes:
                inc_name = inc.split('/')[-1]
                # If it's NOT a standard header and it's a header file, mark for processing
                if inc_name not in cpp_std_headers and inc_name.endswith(('.h', '.hpp', '.hxx')):
                    local_headers_needed.append(inc)
            
            # Create stubs for ALL local headers (found or not)
            # This ensures compilation can proceed even with missing dependencies
            stubs_created = []
            for inc in local_headers_needed:
                header_name = inc.split('/')[-1]
                # Create stub for ANY local header we might not find completely
                stubs_created.append(inc)
            
            if stubs_created:
                self.logger.debug(f"Creating {len(stubs_created)} stub headers to ensure compilation")
                self._create_stub_headers(stubs_created, temp_dir)
            
            # Find all necessary include paths
            include_paths = self._find_header_paths(source_content + "\n" + test_content, temp_dir, source_file)
            
            # Get system include paths from clang
            system_paths = self._get_clang_include_paths()
            
            self.logger.debug(f"Using {len(include_paths)} repo paths + {len(system_paths)} system paths")
            for sp in system_paths[:3]:
                self.logger.debug(f"  System: {sp}")
            
            # Build compile command with proper order
            compile_cmd = [
                'clang++',
                f'-I{temp_dir}',  # Temp dir FIRST - has stubs
                f'-I{os.path.dirname(source_file)}',  # Source dir
            ] + system_paths + [  # System paths (important!)
                '-std=c++17',
                '-fPIC',
                '-Wno-all',
                '-Wno-error',
                f'-o{out_file}',
                test_file,
                source_file
            ] + include_paths  # Repo paths last
            
            self.logger.debug(f"Attempting compilation with clang++...")
            self.logger.debug(f"Command: clang++ [temp] [source] [system_paths] [flags] [repo_paths]")
            self.logger.debug(f"  Temp dir: {temp_dir}")
            self.logger.debug(f"  Source dir: {os.path.dirname(source_file)}")
            self.logger.debug(f"  System paths: {len(system_paths)}")
            self.logger.debug(f"  Repo paths: {len(include_paths)}")
            
            result = subprocess.run(
                compile_cmd,
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            if result.returncode != 0:
                error_msg = result.stderr
                self.logger.debug(f"Compilation stderr (first 300 chars): {error_msg[:300]}")
                
                # Check for missing headers or other compilation issues
                if 'file not found' in error_msg.lower() or 'undefined reference' in error_msg.lower():
                    missing = re.findall(r"'([^']+)'", error_msg)
                    self.logger.warning(f"Missing headers or undefined references: {missing[:2]}")
                    # Return a special marker that this test can't be compiled
                    return False, "CANNOT_COMPILE_TEST"
                
                # Check for actual errors
                if 'error:' in error_msg.lower():
                    errors = [l.strip() for l in error_msg.split('\n') if 'error:' in l.lower()][:1]
                    error_detail = errors[0] if errors else error_msg[:200]
                    return False, f"CANNOT_COMPILE_TEST"
                
                return False, "CANNOT_COMPILE_TEST"
            
            self.logger.debug(f"✓ Compilation successful, running executable...")
            
            # Run the test executable
            result = subprocess.run(
                [out_file],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            return result.returncode == 0, result.stdout + result.stderr
        
        except subprocess.TimeoutExpired:
            return False, f"C++ test timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"C++ test error: {str(e)}"
    
    def _run_mochitest(self, test_file: str) -> Tuple[bool, str]:
        """Run Mochitest"""
        try:
            result = subprocess.run(
                ['./mach', 'mochitest', test_file],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, f"Mochitest error: {str(e)}"
    
    def _run_xpcshell_test(self, test_file: str) -> Tuple[bool, str]:
        """Run xpcshell test"""
        try:
            result = subprocess.run(
                ['./mach', 'xpcshell-test', test_file],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, f"xpcshell error: {str(e)}"
    
    def _run_test(self, test_file: str, test_type: str, 
                 source_file: str = None, temp_dir: str = None) -> Tuple[bool, str]:
        """Run test based on type"""
        try:
            if test_type == 'javascript':
                return self._run_javascript_test(test_file, temp_dir or os.path.dirname(test_file))
            elif test_type == 'python':
                return self._run_python_test(test_file, temp_dir or os.path.dirname(test_file))
            elif test_type == 'cpp':
                return self._run_cpp_test(test_file, source_file, temp_dir or os.path.dirname(test_file))
            elif test_type == 'mochitest':
                return self._run_mochitest(test_file)
            elif test_type == 'xpcshell':
                return self._run_xpcshell_test(test_file)
            else:
                return False, "Unknown test type"
        except subprocess.TimeoutExpired:
            return False, f"Test timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"Test error: {str(e)}"
    
    def validate_test_pair(self,
                          bug_id: str,
                          filepath: str,
                          match_idx: int,
                          test_path_fixing: str,
                          test_path_regressor: str,
                          fixing_hash: str,
                          regressor_hash: str) -> Dict:
        """Validate test against both fixing and regressor versions."""
        result = {
            'source_file': filepath,
            'test_file': test_path_fixing,
            'test_type': self._detect_test_type(test_path_fixing),
            'fixing_commit': fixing_hash[:8],
            'regressor_commit': regressor_hash[:8],
            'fixing_result': None,
            'regressor_result': None,
            'status': 'unknown',
            'confirms_regression': False
        }
        
        # Extract test content from Step 11's organized directories
        self.logger.info(f"        Loading test files from Step 11...")
        test_content_fixing = self._extract_test_content_from_step11(
            bug_id, filepath, match_idx, test_path_fixing, 'fixing'
        )
        test_content_regressor = self._extract_test_content_from_step11(
            bug_id, filepath, match_idx, test_path_regressor, 'regressor'
        )
        
        # Decide which test content to use
        if test_content_fixing and test_content_regressor and test_content_fixing == test_content_regressor:
            test_content = test_content_fixing
            self.logger.info(f"      ✓ Using single test (identical at both commits)")
        elif test_content_fixing:
            test_content = test_content_fixing
            self.logger.info(f"      ✓ Using fixing commit test")
        elif test_content_regressor:
            test_content = test_content_regressor
            self.logger.info(f"      ✓ Using regressor commit test")
        else:
            result['status'] = 'test_content_error'
            self.logger.error(f"      ✗ Could not extract test content")
            return result
        
        # Get source code from Step 10
        self.logger.info(f"        Loading source code from Step 10...")
        fixing_content = self._find_source_content_in_step10(bug_id, filepath, fixing_hash)
        regressor_content = self._find_source_content_in_step10(bug_id, filepath, regressor_hash)
        
        if not fixing_content or not regressor_content:
            result['status'] = 'missing_source_content'
            self.logger.error(f"      ✗ Missing source content from Step 10")
            return result
        
        self.logger.info(f"      ✓ Source code loaded ({len(fixing_content)} bytes)")
        
        # For C++ tests, check if it's actually compilable
        if result['test_type'] == 'cpp':
            if not self._can_run_cpp_test(test_content, fixing_content):
                result['status'] = 'not_executable'
                self.logger.warning(f"      ⚠ Test is not executable (no TEST() or main())")
                return result
        
        # TEST WITH REGRESSOR VERSION (EXPECT FAIL)
        self.logger.info(f"        Running test against REGRESSOR commit ({regressor_hash[:8]})...")
        temp_dir, test_file, source_file = self._create_test_env(
            test_content, filepath, regressor_content, test_path_fixing
        )
        
        if not temp_dir:
            result['status'] = 'env_error'
            self.logger.error(f"      ✗ Failed to create test environment")
            return result
        
        try:
            regressor_passed, regressor_output = self._run_test(
                test_file, result['test_type'], source_file, temp_dir
            )
            
            # Check if test can't be compiled
            if regressor_output == "CANNOT_COMPILE_TEST":
                result['status'] = 'not_executable'
                self.logger.warning(f"      ⚠ Test cannot be compiled (requires full build system)")
                return result
            
            # Check if this is a dependency issue
            if 'Missing dependencies' in regressor_output or 'Missing headers' in regressor_output:
                result['status'] = 'missing_dependencies'
                self.logger.warning(f"      ⚠ Skipped: Missing external dependencies")
                return result
            
            # Check for C++ compilation errors
            if 'C++ Compilation error' in regressor_output:
                result['status'] = 'cpp_compilation_error'
                self.logger.warning(f"      ⚠ Skipped: C++ compilation failed")
                self.logger.debug(f"        Error details: {regressor_output[:200]}")
                return result
            
            if regressor_passed:
                self.logger.info(f"        Test PASSED on regressor (unexpected - should fail)")
            else:
                self.logger.info(f"      ✓ Test FAILED on regressor (expected)")
            
            # Log output preview if verbose
            if not regressor_passed and self.verbose:
                self.logger.info(f"      Output preview (first 500 chars):")
                for line in regressor_output[:500].split('\n')[:10]:
                    self.logger.info(f"        {line}")
            
            result['regressor_result'] = {
                'passed': regressor_passed,
                'output_lines': len(regressor_output.split('\n')),
                'output_preview': regressor_output[:500],
                'full_output': regressor_output
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        # TEST WITH FIXING VERSION (EXPECT PASS)
        self.logger.info(f"        Running test against FIXING commit ({fixing_hash[:8]})...")
        temp_dir, test_file, source_file = self._create_test_env(
            test_content, filepath, fixing_content, test_path_fixing
        )
        
        if not temp_dir:
            result['status'] = 'env_error'
            self.logger.error(f"      ✗ Failed to create test environment")
            return result
        
        try:
            fixing_passed, fixing_output = self._run_test(
                test_file, result['test_type'], source_file, temp_dir
            )
            
            # Check if test can't be compiled
            if fixing_output == "CANNOT_COMPILE_TEST":
                result['status'] = 'not_executable'
                self.logger.warning(f"      ⚠ Test cannot be compiled (requires full build system)")
                return result
            
            # Check if this is a dependency issue
            if 'Missing dependencies' in fixing_output or 'Missing headers' in fixing_output:
                result['status'] = 'missing_dependencies'
                self.logger.warning(f"      ⚠ Skipped: Missing external dependencies")
                return result
            
            # Check for C++ compilation errors
            if 'C++ Compilation error' in fixing_output:
                result['status'] = 'cpp_compilation_error'
                self.logger.warning(f"      ⚠ Skipped: C++ compilation failed")
                self.logger.debug(f"        Error details: {fixing_output[:200]}")
                return result
            
            if fixing_passed:
                self.logger.info(f"      ✓ Test PASSED on fixing (expected)")
            else:
                self.logger.info(f"        Test FAILED on fixing (unexpected - should pass)")
            
            # Log output preview if verbose or if test failed
            if not fixing_passed:
                self.logger.warning(f"        Test output (first 800 chars):")
                output_lines = fixing_output[:800].split('\n')
                for line in output_lines[:15]:
                    self.logger.warning(f"        {line}")
                if len(output_lines) > 15:
                    self.logger.warning(f"        ... ({len(output_lines) - 15} more lines)")
            
            result['fixing_result'] = {
                'passed': fixing_passed,
                'output_lines': len(fixing_output.split('\n')),
                'output_preview': fixing_output[:500],
                'full_output': fixing_output
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        # DETERMINE STATUS
        if regressor_passed and fixing_passed:
            result['status'] = 'both_pass'
            self.logger.warning(f"        RESULT: both_pass (test doesn't catch regression)")
        elif not regressor_passed and fixing_passed:
            result['status'] = 'confirms_regression'
            result['confirms_regression'] = True
            self.logger.info(f"      ✓ RESULT: confirms_regression (PERFECT!)")
        elif regressor_passed and not fixing_passed:
            result['status'] = 'unexpected_behavior'
            self.logger.warning(f"        RESULT: unexpected_behavior")
        else:
            result['status'] = 'both_fail'
            self.logger.warning(f"        RESULT: both_fail (test broken)")
        
        return result
    
    def validate_all(self) -> Dict:
        """Main validation process"""
        self.logger.info("\n" + "=" * 70)
        self.logger.info("STEP 12: TEST VALIDATION AGAINST REGRESSION/FIX COMMITS")
        self.logger.info("=" * 70)
        
        if not self.step11_summary:
            return {}
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'bugs': 0,
                'source_files': 0,
                'tests_run': 0,
                'confirms_regression': 0,
                'both_pass': 0,
                'both_fail': 0,
                'unexpected': 0,
                'missing_dependencies': 0,
                'cpp_compilation_error': 0,
                'not_executable': 0,
                'test_error': 0,
                'confirmation_rate': 0.0
            },
            'bugs': {}
        }
        
        self.logger.info("\n Validating tests...\n")
        
        for bug_id, bug_info in self.step11_summary.get('bugs', {}).items():
            self.logger.info(f"Bug {bug_id}:")
            bug_results = {'files': []}
            
            for filepath, file_info in bug_info['files'].items():
                self.logger.info(f"   {os.path.basename(filepath)}")
                
                file_results = {'source_file': filepath, 'validations': []}
                
                for match in file_info['matches']:
                    match_idx = match['match_idx']
                    
                    fixing_tests = match['fixing_commit']['tests']
                    regressor_tests = match['regressor_commit']['tests']
                    fixing_hash = match['fixing_commit']['hash']
                    regressor_hash = match['regressor_commit']['hash']
                    
                    if not fixing_tests and not regressor_tests:
                        continue
                    
                    test_path_fixing = fixing_tests[0] if fixing_tests else regressor_tests[0]
                    test_path_regressor = regressor_tests[0] if regressor_tests else test_path_fixing
                    
                    validation = self.validate_test_pair(
                        bug_id, filepath, match_idx,
                        test_path_fixing, test_path_regressor,
                        fixing_hash, regressor_hash
                    )
                    
                    file_results['validations'].append(validation)
                    results['summary']['tests_run'] += 1
                    
                    if validation['status'] == 'confirms_regression':
                        results['summary']['confirms_regression'] += 1
                        status_icon = "✓"
                    elif validation['status'] == 'both_pass':
                        results['summary']['both_pass'] += 1
                        status_icon = "~"
                    elif validation['status'] == 'both_fail':
                        results['summary']['both_fail'] += 1
                        status_icon = "✗"
                    elif validation['status'] == 'missing_dependencies':
                        results['summary']['missing_dependencies'] += 1
                        status_icon = "⚠"
                    elif validation['status'] == 'cpp_compilation_error':
                        results['summary']['cpp_compilation_error'] += 1
                        status_icon = "⚠"
                    elif validation['status'] == 'not_executable':
                        results['summary']['not_executable'] += 1
                        status_icon = "⊘"
                    else:
                        results['summary']['unexpected'] += 1
                        status_icon = "!"
                    
                    self.logger.info(
                        f"    {status_icon} Match {match_idx}: {validation['status']}"
                    )
                
                if file_results['validations']:
                    bug_results['files'].append(file_results)
                    results['summary']['source_files'] += 1
            
            if bug_results['files']:
                results['bugs'][bug_id] = bug_results
                results['summary']['bugs'] += 1
        
        # Calculate confirmation rate (excluding tests with missing dependencies or compilation errors)
        tests_with_results = (results['summary']['confirms_regression'] + 
                             results['summary']['both_pass'] + 
                             results['summary']['both_fail'] + 
                             results['summary']['unexpected'])
        
        if tests_with_results > 0:
            results['summary']['confirmation_rate'] = (
                results['summary']['confirms_regression'] / tests_with_results * 100
            )
        
        self._save_results(results)
        self._print_summary(results)
        
        return results
    
    def _save_results(self, results: Dict):
        """Save results to files"""
        json_path = Path(self.output_dir) / 'validation_results.json'
        with open(json_path, 'w') as f:
            json.dump(results, f, indent=2)
        
        text_path = Path(self.output_dir) / 'validation_report.txt'
        self._write_text_report(text_path, results)
    
    def _write_text_report(self, output_path: Path, results: Dict):
        """Write comprehensive text report"""
        with open(output_path, 'w') as f:
            f.write("TEST VALIDATION REPORT\n")
            f.write("=" * 70 + "\n\n")
            
            stats = results['summary']
            f.write("SUMMARY METRICS:\n")
            f.write("-" * 70 + "\n")
            f.write(f"Bugs analyzed: {stats['bugs']}\n")
            f.write(f"Source files tested: {stats['source_files']}\n")
            f.write(f"Total tests run: {stats['tests_run']}\n\n")
            
            f.write("TEST RESULTS:\n")
            f.write("-" * 70 + "\n")
            f.write(f"✓ Confirms regression: {stats['confirms_regression']}\n")
            f.write(f"~ Both pass: {stats['both_pass']}\n")
            f.write(f"✗ Both fail: {stats['both_fail']}\n")
            f.write(f"! Unexpected: {stats['unexpected']}\n")
            f.write(f"⚠ Missing dependencies: {stats['missing_dependencies']}\n")
            f.write(f"! Test error: {stats['test_error']}\n\n")
            f.write(f"CONFIRMATION RATE: {stats['confirmation_rate']:.1f}%\n")
            f.write(f"   (Calculated from {stats['confirms_regression'] + stats['both_pass'] + stats['both_fail'] + stats['unexpected']} tests with results)\n\n")
            
            f.write("DETAILED RESULTS:\n")
            f.write("=" * 70 + "\n\n")
            
            for bug_id, bug_data in results.get('bugs', {}).items():
                f.write(f"Bug {bug_id}\n")
                f.write("-" * 70 + "\n")
                
                for file_data in bug_data.get('files', []):
                    f.write(f"  {file_data['source_file']}\n")
                    for val in file_data.get('validations', []):
                        f.write(f"    Test: {os.path.basename(val['test_file'])}\n")
                        f.write(f"      Type: {val['test_type']}\n")
                        f.write(f"      Status: {val['status']}\n")
                        if val['regressor_result']:
                            f.write(f"      Regressor: {'FAIL' if not val['regressor_result']['passed'] else 'PASS'}\n")
                        if val['fixing_result']:
                            f.write(f"      Fixing:   {'PASS' if val['fixing_result']['passed'] else 'FAIL'}\n")
                        f.write(f"      Confirms regression: {'Yes' if val['confirms_regression'] else 'No'}\n")
                        f.write("\n")
    
    def _print_summary(self, results: Dict):
        """Print summary to console"""
        stats = results['summary']
        
        self.logger.info("\n" + "=" * 70)
        self.logger.info("VALIDATION COMPLETE")
        self.logger.info("=" * 70)
        
        self.logger.info(f"\n METRICS:")
        self.logger.info(f"  • Bugs: {stats['bugs']}")
        self.logger.info(f"  • Source files: {stats['source_files']}")
        self.logger.info(f"  • Tests run: {stats['tests_run']}")
        
        self.logger.info(f"\n✓ Results:")
        self.logger.info(f"  • Confirms regression: {stats['confirms_regression']}")
        self.logger.info(f"  • Both pass: {stats['both_pass']}")
        self.logger.info(f"  • Both fail: {stats['both_fail']}")
        self.logger.info(f"  • Unexpected: {stats['unexpected']}")
        
        self.logger.info(f"\n⚠ Skipped/Unable to Run:")
        self.logger.info(f"  • Not executable: {stats['not_executable']}")
        self.logger.info(f"  • Missing dependencies: {stats['missing_dependencies']}")
        self.logger.info(f"  • C++ compilation errors: {stats['cpp_compilation_error']}")
        
        self.logger.info(f"\n CONFIRMATION RATE: {stats['confirmation_rate']:.1f}%")
        tests_with_results = (stats['confirms_regression'] + stats['both_pass'] + 
                            stats['both_fail'] + stats['unexpected'])
        self.logger.info(f"   (Confirmation rate calculated from {tests_with_results} tests with results)")
        self.logger.info(f"\n Output: {self.output_dir}/")


def main():
    """Main execution"""
    import argparse
    
    # Define local repos
    local_repos = {
        'mozilla-central': './mozilla-central',
        'mozilla-release': './mozilla-release',
        'mozilla-autoland': './mozilla-autoland',
        'mozilla-esr115': './mozilla-esr115'
    }
    
    parser = argparse.ArgumentParser(
        description='Validate tests using Step 11 & Step 10 results (REVISED for Step 11 new format)'
    )
    parser.add_argument(
        '--step11-dir', '-s11',
        default='test_extraction',
        help='Step 11 results directory (with fixing/ and regressor/ subdirs)'
    )
    parser.add_argument(
        '--step10-dir', '-s10',
        default='step10_matched_methodDiffs',
        help='Step 10 results directory'
    )
    parser.add_argument(
        '--output', '-o',
        default='step12_test_validation',
        help='Output directory'
    )
    parser.add_argument(
        '--repo-root', '-r',
        default='./mozilla-central',
        help='Mozilla repository root (for mach commands)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )
    parser.add_argument(
        '--timeout', '-t',
        type=int,
        default=60,
        help='Test execution timeout in seconds'
    )
    
    args = parser.parse_args()
    
    validator = TestValidator(
        step11_results_dir=args.step11_dir,
        step10_results_dir=args.step10_dir,
        output_dir=args.output,
        repo_root=args.repo_root,
        verbose=args.verbose,
        timeout=args.timeout,
        local_repos=local_repos
    )
    
    results = validator.validate_all()
    
    print("\n" + "=" * 70)
    print("STEP 12 COMPLETED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    main()