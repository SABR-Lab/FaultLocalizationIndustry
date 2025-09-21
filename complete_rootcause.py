#!/usr/bin/env python3
"""
Automated Mozilla Crash Analysis Tool with Tree-sitter Integration

This script combines:
1. Crash extraction from the first script (paste.txt)
2. Automated analysis of all extracted crashes using the second script functionality

It automatically extracts crashes using the first script's methods, then runs
comprehensive analysis on each crash including:
- Getting BuildID from crash data
- Finding revision ID and channel for that build
- Getting commit info from local Mercurial repositories
- Extracting commit history for specific files and line changes
- Finding commits that likely introduced the code being fixed
- Exact diff comparison to identify the precise introducing commit
- Extracting full file content from introducing commits with line numbers
- Parsing extracted files with tree-sitter to identify vulnerable functions
- Performing analysis on functions
- Verifying analysis accuracy by comparing with actual diffs

Requires local clones of Mozilla repositories and tree-sitter-c.
"""

import requests
import json
import re
import subprocess
import os
import time
import tempfile
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path
from dataclasses import dataclass

# Import the crash extraction functionality from the first script
try:
    from Step1_crash_extractor import Step1SingleSignatureTest, CrashInfo
    CRASH_EXTRACTION_AVAILABLE = True
    print(" Crash extraction functionality imported successfully")
except ImportError as e:
    print(f" Warning: Could not import crash extraction functionality: {e}")
    print("Make sure paste.py is in the same directory")
    CRASH_EXTRACTION_AVAILABLE = False

# Tree-sitter integration
try:
    from c_parser import CParser
    TREE_SITTER_AVAILABLE = True
    print(" Tree-sitter C parser imported successfully")
except ImportError as e:
    print(f"  Warning: Tree-sitter C parser not available: {e}")
    TREE_SITTER_AVAILABLE = False


@dataclass
class CommitInfo:
    revision: str
    author: str
    date: str
    description: str
    files_changed: List[str]
    bug_numbers: List[str]
    channel: str


@dataclass
class FileChange:
    filename: str
    added_lines: List[int]
    removed_lines: List[int]
    functions_affected: List[str]
    diff_content: str


@dataclass
class FunctionAnalysis:
    name: str
    start_line: int
    end_line: int
    size: int
    return_type: str
    parameters: List[str]
    lines_added_in_commit: List[int]
    lines_removed_in_fix: List[int]
    is_newly_introduced: bool
    code_content: str


class AutomatedMozillaCrashAnalyzer:
    def __init__(self, repo_paths: Dict[str, str], session: Optional[requests.Session] = None, api_only: bool = False):
        """
        Initialize with paths to local Mozilla repositories
        
        Args:
            repo_paths: Dict mapping channel names to local repository paths
                       e.g., {'mozilla-central': '/path/to/mozilla-central',
                             'mozilla-release': '/path/to/mozilla-release',
                             'mozilla-esr115': '/path/to/mozilla-esr115'}
            api_only: If True, skip repository validation (for API-only operations)
        """
        self.repo_paths = {}
        
        if not api_only:
            # Validate repository paths
            for channel, path in repo_paths.items():
                repo_path = Path(path)
                if repo_path.exists() and (repo_path / '.hg').exists():
                    self.repo_paths[channel] = str(repo_path)
                    print(f" Found repository: {channel} at {path}")
                else:
                    print(f" Warning: Repository not found or not a Mercurial repo: {path}")
            
            if not self.repo_paths:
                raise ValueError("No valid repositories found!")
        else:
            print("API-only mode: Skipping repository validation")
        
        self.session = session or requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla Crash Analysis Tool 3.0'
        })
        
        # Initialize crash extractor if available
        if CRASH_EXTRACTION_AVAILABLE:
            self.crash_extractor = Step1SingleSignatureTest()
            print(" Crash extractor initialized")
        else:
            self.crash_extractor = None
            print(" Crash extractor not available")
    
    def extract_crashes_for_signature(self, signature: str, years_back: int = 1, 
                                    sample_strategy: str = "monthly", 
                                    dedup_strategy: str = "stack_trace") -> List[CrashInfo]:
        """
        Extract crashes for a specific signature using the first script's functionality
        """
        if not CRASH_EXTRACTION_AVAILABLE or not self.crash_extractor:
            print(" Crash extraction not available")
            return []
        
        print(f" Extracting crashes for signature: {signature}")
        print(f" Time period: {years_back} years back")
        print(f" Strategy: {sample_strategy} sampling, {dedup_strategy} deduplication")
        
        try:
            crashes = self.crash_extractor.test_specific_signature_longterm(
                signature=signature,
                years_back=years_back,
                sample_strategy=sample_strategy,
                dedup_strategy=dedup_strategy
            )
            
            print(f" Successfully extracted {len(crashes)} crashes")
            return crashes
            
        except Exception as e:
            print(f" Error extracting crashes: {e}")
            return []
    
    def _run_hg_command(self, repo_path: str, command: List[str]) -> Tuple[bool, str]:
        """
        Run a Mercurial command in the specified repository
        """
        try:
            full_command = ['hg'] + command
            result = subprocess.run(
                full_command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode == 0, result.stdout if result.returncode == 0 else result.stderr
        except subprocess.TimeoutExpired:
            return False, "Command timed out"
        except Exception as e:
            return False, str(e)
    
    def get_build_id(self, crash_id: str) -> Optional[str]:
        """
        Step 1: Get BuildID from crash data
        """
        url = f"https://crash-stats.mozilla.org/api/ProcessedCrash/?crash_id={crash_id}"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract build ID from the crash data
            build_id = data.get('build')
            if build_id:
                print(f" Found Build ID: {build_id}")
                return build_id
            else:
                print(" No build ID found in crash data")
                return None
                
        except requests.RequestException as e:
            print(f" Error fetching crash data: {e}")
            return None
    
    def get_revision_and_channel_from_build_id(self, build_id: str) -> Optional[tuple]:
        """
        Step 2: Get revision ID and channel for the Build ID using buildhub
        Returns tuple of (revision, channel) if found
        """
        url = "https://buildhub.moz.tools/api/search"
        query = {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"build.id": build_id}}
                    ]
                }
            }
        }
        
        try:
            response = self.session.post(url, json=query)
            response.raise_for_status()
            data = response.json()
            
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                print(f" No revision found for build ID: {build_id}")
                return None
            
            # Try to extract revision and channel
            revision = None
            channel_info = None
            
            source = hits[0].get("_source", {})
            
            # Try to get revision from various possible locations
            try:
                revision = source["build"]["revision"]
            except KeyError:
                try:
                    revision = source["source"]["revision"]
                except KeyError:
                    try:
                        revision = source["target"]["revision"]
                    except KeyError:
                        try:
                            revision = source["revision"]
                        except KeyError:
                            pass
            
            # Try to get channel information
            try:
                channel_info = source["target"]["channel"]
            except KeyError:
                try:
                    channel_info = source["build"]["channel"]
                except KeyError:
                    try:
                        channel_info = source["channel"]
                    except KeyError:
                        try:
                            # Sometimes channel is in target.version
                            version = source["target"]["version"]
                            if "nightly" in version.lower():
                                channel_info = "nightly"
                            elif "beta" in version.lower():
                                channel_info = "beta"
                            elif "esr" in version.lower():
                                channel_info = "esr"
                            else:
                                channel_info = "release"
                        except KeyError:
                            pass
            
            if not revision:
                print(f" Could not find revision in any expected location")
                return None
            
            print(f" Found revision: {revision}")
            print(f" Found channel: {channel_info}")
            
            return revision, channel_info
            
        except requests.RequestException as e:
            print(f" Error fetching revision from buildhub: {e}")
            return None
        except Exception as e:
            print(f" Unexpected error parsing buildhub response: {e}")
            return None

    def map_channel_to_repo(self, channel: str) -> str:
        """
        Map buildhub channel to local repository name
        """
        if not channel:
            return self._get_default_repo()
        
        channel_lower = channel.lower()
        
        # Channel mapping
        channel_mapping = {
            'nightly': 'mozilla-central',
            'central': 'mozilla-central',
            'mozilla-central': 'mozilla-central',
            'release': 'mozilla-release',
            'mozilla-release': 'mozilla-release',
            'beta': 'mozilla-release',
            'esr': 'mozilla-esr115',
            'esr115': 'mozilla-esr115',
            'mozilla-esr115': 'mozilla-esr115'
        }
        
        # Try exact match first
        if channel_lower in channel_mapping:
            preferred_repo = channel_mapping[channel_lower]
            if preferred_repo in self.repo_paths:
                return preferred_repo
        
        # Try partial matches for ESR versions
        if 'esr' in channel_lower:
            if 'mozilla-esr115' in self.repo_paths:
                return 'mozilla-esr115'
        
        return self._get_default_repo()
    
    def _get_default_repo(self) -> str:
        """
        Get the first available repository as default
        """
        preferred_order = ['mozilla-central', 'mozilla-release', 'mozilla-esr115']
        
        for repo in preferred_order:
            if repo in self.repo_paths:
                return repo
        
        return list(self.repo_paths.keys())[0] if self.repo_paths else None

    def find_revision_in_repos(self, revision: str, preferred_channel: str = None) -> Optional[Tuple[str, str]]:
        """
        Find which local repository contains the given revision
        Returns tuple of (channel, repo_path) if found
        """
        # If we have channel info, try that repository first
        if preferred_channel:
            preferred_repo = self.map_channel_to_repo(preferred_channel)
            if preferred_repo and preferred_repo in self.repo_paths:
                repo_path = self.repo_paths[preferred_repo]
                success, output = self._run_hg_command(repo_path, ['log', '-r', revision, '--template', '{node}'])
                if success and revision in output:
                    return preferred_repo, repo_path
        
        # If preferred repo doesn't have it, search all repos
        for channel, repo_path in self.repo_paths.items():
            if preferred_channel and channel == self.map_channel_to_repo(preferred_channel):
                continue
                
            success, output = self._run_hg_command(repo_path, ['log', '-r', revision, '--template', '{node}'])
            if success and revision in output:
                return channel, repo_path
        
        return None
    
    def get_changed_files(self, revision: str, preferred_channel: str = None, silent: bool = False) -> Dict[str, List[str]]:
        """
        Get files changed at a specific revision using hg status
        Filters out non-code files but keeps them in 'filtered_out' category for reference
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return {}
        
        channel, repo_path = repo_info
        
        # Get the parent revision to compare against
        success, parent_output = self._run_hg_command(repo_path, ['log', '-r', revision, '--template', '{p1node}'])
        if not success:
            return {}
        
        parent_revision = parent_output.strip()
        
        # Use hg status to get detailed file changes
        success, status_output = self._run_hg_command(repo_path, ['status', '--rev', parent_revision, '--rev', revision])
        
        if not success:
            return {}
        
        file_changes = {
            'modified': [],
            'added': [],
            'removed': [],
            'copied': [],
            'renamed': [],
            'unknown': [],
            'filtered_out': []  # New category for non-code files
        }
        
        for line in status_output.strip().split('\n'):
            if not line.strip():
                continue
                
            status_code = line[0]
            filename = line[2:].strip()
            
            # Check if this is a code file
            if self._is_code_file(filename):
                # Add to appropriate category
                if status_code == 'M':
                    file_changes['modified'].append(filename)
                elif status_code == 'A':
                    file_changes['added'].append(filename)
                elif status_code == 'R':
                    file_changes['removed'].append(filename)
                elif status_code == 'C':
                    file_changes['copied'].append(filename)
                else:
                    file_changes['unknown'].append(f"{status_code} {filename}")
            else:
                # Add to filtered out category with status code for reference
                file_changes['filtered_out'].append(f"{status_code} {filename}")
        
        # Print filtering summary ONLY if not silent and there are filtered files
        if not silent and file_changes['filtered_out']:
            total_files = sum(len(files) for files in file_changes.values())
            code_files = total_files - len(file_changes['filtered_out'])
            
            print(f"🔍 File filtering summary for {revision[:12]}:")
            print(f"   Total files changed: {total_files}")
            print(f"   Code files: {code_files}")
            print(f"   Filtered out: {len(file_changes['filtered_out'])}")
            
            # Show some examples of filtered files
            if len(file_changes['filtered_out']) <= 5:
                for filtered_file in file_changes['filtered_out']:
                    print(f"     🚫 {filtered_file}")
            else:
                for filtered_file in file_changes['filtered_out'][:3]:
                    print(f"     🚫 {filtered_file}")
                print(f"     🚫 ... and {len(file_changes['filtered_out']) - 3} more non-code files")
        
        return file_changes

        
    def _is_code_file(self, filename: str) -> bool:
        """
        Determine if a file is a code file that should be analyzed
        Returns True for code files, False for non-code files
        """
        if not filename:
            return False
        
        filename_lower = filename.lower()
        
        # Code file extensions we want to analyze
        code_extensions = {
            # C/C++ files
            '.c', '.cc', '.cpp', '.cxx', '.c++', '.h', '.hh', '.hpp', '.hxx', '.h++',
            # JavaScript/TypeScript
            '.js', '.jsx', '.ts', '.tsx', '.mjs',
            # Rust
            '.rs',
            # Python (for build scripts, tests)
            '.py',
            # IDL files (Mozilla specific)
            '.idl', '.webidl',
            # Build files
            '.mk', '.in',
            # Shell scripts
            '.sh', '.bash',
            # Other Mozilla-specific
            '.jsm', '.sys.mjs'
        }
        
        # Check extension
        for ext in code_extensions:
            if filename_lower.endswith(ext):
                return True
        
        # Special cases for files without extensions that are typically code
        code_file_patterns = [
            'makefile',
            'moz.build',
            'configure'
        ]
        
        filename_base = filename_lower.split('/')[-1]  # Get just the filename
        for pattern in code_file_patterns:
            if pattern in filename_base:
                return True
        
        # File patterns to explicitly exclude (non-code files)
        exclude_patterns = [
            # Documentation
            '.md', '.txt', '.rst', '.html', '.xml', '.xhtml',
            # Images and media
            '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp',
            # Data files
            '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.list',
            # Localization
            '.properties', '.dtd', '.ftl',
            # Test data
            '.expected', '.ref', '.test',
            # Build outputs
            '.o', '.obj', '.so', '.dll', '.dylib', '.a', '.lib',
            # Archives
            '.zip', '.tar', '.gz', '.xz', '.bz2',
            # Certificates and keys
            '.pem', '.crt', '.key',
            # Fonts
            '.ttf', '.otf', '.woff', '.woff2',
            # Other
            '.pdf', '.log', '.tmp'
        ]
        
        for ext in exclude_patterns:
            if filename_lower.endswith(ext):
                return False
        
        # Directory patterns to exclude
        exclude_directories = [
            'third_party/',
            'testing/web-platform/',
            'intl/icu/',
            'media/libvpx/',
            'gfx/skia/',
            'js/src/octane/',
            'browser/locales/',
            'mobile/locales/',
            'toolkit/locales/'
        ]
        
        for exclude_dir in exclude_directories:
            if exclude_dir in filename_lower:
                return False
        
        # If we can't determine, be conservative and include it
        # This ensures we don't accidentally filter out important files
        return True
    def is_merge_commit(self, revision: str, preferred_channel: str = None) -> bool:
        """
        Check if a commit is a merge commit by examining its description
        Returns True if it's a merge commit that should be filtered out
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return False
        
        channel, repo_path = repo_info
        
        # Get just the description
        success, description = self._run_hg_command(repo_path, ['log', '-r', revision, '--template', '{desc}'])
        
        if not success:
            return False
        
        description_lower = description.lower()
        
        # Check for merge commit patterns
        is_merge_commit = any(phrase in description_lower for phrase in [
            'merge autoland to mozilla-central',
            'merge mozilla-central to',
            'merge central to',
            'merge beta to',
            'merge release to',
            'merge esr',
            'a=merge'
        ])
        
        # Check for additional merge patterns
        merge_patterns = [
            r'merge.*a=merge',
            r'merge.*to.*central',
            r'merge.*to.*release',
            r'merge.*to.*beta',
            r'merge.*to.*esr',
            r'automated merge'
        ]
        
        for pattern in merge_patterns:
            if re.search(pattern, description_lower):
                is_merge_commit = True
                break
        
        if is_merge_commit:
            print(f"Filtering out merge commit {revision[:12]}: {description[:100]}...")
        
        return is_merge_commit


    def get_commit_info(self, revision: str, preferred_channel: str = None) -> Optional[CommitInfo]:
        """
        Get commit info from local Mercurial repository
        Filters out commits without bug numbers and "No bug" descriptions
        Note: Merge commits are now filtered earlier in the pipeline by full_analysis()
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return None
        
        channel, repo_path = repo_info
        
        template = '{author}|{date|isodate}|{desc}'
        success, output = self._run_hg_command(repo_path, ['log', '-r', revision, '--template', template])
        
        if not success:
            return None
        
        try:
            parts = output.strip().split('|', 2)
            author = parts[0]
            date = parts[1]
            description = parts[2] if len(parts) > 2 else ""
            
            # Extract bug numbers from description
            bug_numbers = re.findall(r'[Bb]ug (\d+)', description)
            
            # Filter out commits with no bug numbers or "No bug" in description
            description_lower = description.lower()
            has_no_bug_text = any(phrase in description_lower for phrase in [
                'no bug',
                'nobug',
                'no-bug'
            ])
            
            if not bug_numbers or has_no_bug_text:
                """print(f" Filtering out commit {revision[:12]}: ", end="")
                if not bug_numbers and has_no_bug_text:
                    print("No bug numbers found AND contains 'No bug' text")
                elif not bug_numbers:
                    print("No bug numbers found in description")
                elif has_no_bug_text:
                    print("Contains 'No bug' text in description")
                print(f"   Description: {description[:100]}...")"""
                return None
            
            # Get file changes for valid commits
            """file_changes_dict = self.get_changed_files(revision, preferred_channel)
            
            all_files = []
            for change_type, files in file_changes_dict.items():
                if change_type != 'unknown' and change_type != 'filtered_out':
                    all_files.extend(files)
            
            print(f" Valid commit {revision[:12]} with {len(bug_numbers)} bug number(s): {bug_numbers}")"""
            
            return CommitInfo(
                revision=revision,
                author=author,
                date=date,
                description=description,
                files_changed= [],#all_files,
                bug_numbers=bug_numbers,
                channel=channel
            )
            
        except Exception as e:
            print(f" Error processing commit {revision[:12]}: {e}")
            return None

    

    def get_clean_file_diff(self, revision: str, filename: str, preferred_channel: str = None) -> Optional[str]:
        """
        Get clean diff using: hg diff -c <revision> <filename>
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return None
        
        channel, repo_path = repo_info
        
        command = ['diff', '-c', revision, filename]
        success, diff_output = self._run_hg_command(repo_path, command)
        
        if success:
            lines = diff_output.split('\n')
            clean_lines = []
            
            for line in lines:
                if line.startswith('diff -r ') and ' -r ' in line:
                    clean_lines.append(f"diff --git a/{filename} b/{filename}")
                    continue
                clean_lines.append(line)
            
            return '\n'.join(clean_lines)
        else:
            return None

    def get_file_history(self, revision: str, filename: str, max_commits: int = 50, preferred_channel: str = None) -> List[Dict]:
        """
        Step 4: Get commit history for a specific file
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return []
        
        channel, repo_path = repo_info
        
        template = '{node|short}|{author}|{date|isodate}|{desc|firstline}'
        command = [
            'log',
            '-r', f'reverse(ancestors({revision}))',
            '--limit', str(max_commits),
            '--template', template + '\n',
            filename
        ]
        
        success, output = self._run_hg_command(repo_path, command)
        
        if not success:
            return []
        
        commits = []
        for line in output.strip().split('\n'):
            if line.strip():
                try:
                    parts = line.split('|', 3)
                    commits.append({
                        'node': parts[0],
                        'author': parts[1],
                        'date': parts[2],
                        'desc': parts[3] if len(parts) > 3 else ''
                    })
                except:
                    continue
        
        return commits

    def get_commits_affecting_lines(self, revision: str, filename: str, line_numbers: List[int], preferred_channel: str = None) -> List[Dict]:
        """
        Find commits that specifically changed the given line numbers in a file
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return []
        
        channel, repo_path = repo_info
        
        # Use hg annotate to find which revisions last modified each line
        line_ranges = []
        for line_num in sorted(line_numbers):
            line_ranges.append(f"{line_num}")
        
        if not line_ranges:
            return []
        
        # Get annotation for the file at the revision
        success, output = self._run_hg_command(repo_path, ['annotate', '-r', revision, '-n', '-u', filename])
        
        if not success:
            print(f" Error getting annotations for {filename}: {output}")
            return []
        
        affecting_revisions = set()
        lines = output.split('\n')
        
        for line_num in line_numbers:
            if line_num <= len(lines) and line_num > 0:
                line = lines[line_num - 1]
                # Extract revision from annotation (format: "user rev: content")
                match = re.match(r'\s*\w+\s+(\d+):', line)
                if match:
                    affecting_revisions.add(match.group(1))
        
        # Get commit info for affecting revisions
        commits = []
        template = '{node|short}|{author}|{date|isodate}|{desc|firstline}'
        
        for rev in affecting_revisions:
            success, output = self._run_hg_command(repo_path, ['log', '-r', rev, '--template', template])
            if success:
                parts = output.split('|', 3)
                commits.append({
                    'node': parts[0],
                    'author': parts[1],
                    'date': parts[2],
                    'desc': parts[3] if len(parts) > 3 else '',
                    'local_rev': rev
                })
        
        return commits

    def analyze_line_changes(self, diff_content: str) -> Dict[str, List[int]]:
        """
        Analyze diff to find specific line numbers that were changed
        """
        changes = {
            'added_lines': [],
            'removed_lines': [],
            'functions_affected': []
        }
        
        if not diff_content:
            return changes
        
        current_new_line = 0
        current_old_line = 0
        in_hunk = False
        
        ignore_patterns = {
            'if', 'else', 'for', 'while', 'switch', 'case', 'break', 'continue',
            'return', 'const', 'static', 'inline', 'namespace', 'using',
            'auto', 'void', 'int', 'bool', 'char', 'long', 'short', 'float', 'double',
            'HANDLE', 'DWORD', 'BOOL', 'LPHANDLE', 'FALSE', 'TRUE'
        }
        
        macro_patterns = [
            r'^[A-Z_][A-Z0-9_]*$',
            r'^MOZ_',
            r'^NS_',
            r'^CHROMIUM_',
        ]
        
        for line in diff_content.split('\n'):
            if line.startswith('@@'):
                match = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
                if match:
                    current_old_line = int(match.group(1))
                    current_new_line = int(match.group(2))
                    in_hunk = True
                continue
            
            if not in_hunk:
                continue
            
            if line.startswith('+') and not line.startswith('+++'):
                changes['added_lines'].append(current_new_line)
                current_new_line += 1
            elif line.startswith('-') and not line.startswith('---'):
                changes['removed_lines'].append(current_old_line)
                current_old_line += 1
            elif line.startswith(' '):
                current_new_line += 1
                current_old_line += 1
            
            # Look for function definitions and calls in added/removed lines
            if line.startswith(('+', '-')) and not line.startswith(('+++', '---')):
                clean_line = line[1:].strip()
                
                patterns = [
                    r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*\{',
                    r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(',
                    r'\b([a-zA-Z_][a-zA-Z0-9_]*::[a-zA-Z_][a-zA-Z0-9_]*)\s*\(',
                    r'\b(?:class|struct)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                ]
                
                for pattern in patterns:
                    matches = re.finditer(pattern, clean_line)
                    for func_match in matches:
                        func_name = func_match.group(1)
                        
                        if func_name.lower() in ignore_patterns:
                            continue
                        
                        is_macro = False
                        for macro_pattern in macro_patterns:
                            if re.match(macro_pattern, func_name):
                                is_macro = True
                                break
                        if is_macro:
                            continue
                        
                        if len(func_name) < 3:
                            continue
                        
                        if func_name.islower() and '_' not in func_name and '::' not in func_name:
                            continue
                        
                        if func_name not in changes['functions_affected']:
                            changes['functions_affected'].append(func_name)
        
        return changes

    def find_introducing_commits(self, revision: str, filename: str, preferred_channel: str = None) -> Dict[str, Any]:
        """
        Find commits that likely introduced the code that was fixed in the root revision
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return {}
        
        channel, repo_path = repo_info
        
        root_commit = self.get_commit_info(revision, preferred_channel)
        if not root_commit:
            return {}
        
        diff_content = self.get_clean_file_diff(revision, filename, preferred_channel)
        if not diff_content:
            return {}
        
        line_changes = self.analyze_line_changes(diff_content)
        changed_lines = line_changes['added_lines'] + line_changes['removed_lines']
        
        history = self.get_file_history(revision, filename, max_commits=50, preferred_channel=preferred_channel)
        
        introducing_commits = []
        related_commits = []
        
        for commit in history:
            commit_node = commit['node']
            commit_desc = commit['desc']
            
            if commit_node.startswith(revision[:12]):
                continue
            
            try:
                analysis = self._analyze_commit_relevance(
                    commit_node, commit_desc, filename, root_commit, 
                    changed_lines, repo_path
                )
                
                if analysis is None:
                    continue
                
                relevance_score = analysis.get('relevance_score', 0.0)
                
                if relevance_score >= 0.7:
                    introducing_commits.append({
                        **commit,
                        'analysis': analysis
                    })
                elif relevance_score >= 0.3:
                    related_commits.append({
                        **commit,
                        'analysis': analysis
                    })
                    
            except Exception as e:
                continue
        
        return {
            'root_commit': root_commit.__dict__,
            'introducing_commits': introducing_commits,
            'related_commits': related_commits,
            'analysis_summary': {
                'total_history_commits': len(history),
                'high_relevance_commits': len(introducing_commits),
                'medium_relevance_commits': len(related_commits),
                'lines_analyzed': len(changed_lines)
            }
        }
    
    def _analyze_commit_relevance(self, commit_node: str, commit_desc: str, filename: str, 
                             root_commit: CommitInfo, changed_lines: List[int], repo_path: str) -> Dict[str, Any]:
        """
        Analyze how relevant a historical commit is to the root fix
        Modified to be silent and focus on universal relevance indicators
        """
        try:
            relevance_score = 0.0
            reasons = []
            
            root_desc_lower = root_commit.description.lower()
            commit_desc_lower = commit_desc.lower()
            
            # Check for diff overlap (most important indicator)
            diff_found = False
            success, diff_output = self._run_hg_command(repo_path, ['diff', '-c', commit_node, filename])
            if success and diff_output:
                diff_found = True
            else:
                for other_channel, other_repo_path in self.repo_paths.items():
                    if other_repo_path != repo_path:
                        success, diff_output = self._run_hg_command(other_repo_path, ['diff', '-c', commit_node, filename])
                        if success and diff_output:
                            diff_found = True
                            break
            
            if diff_found:
                try:
                    hist_line_changes = self.analyze_line_changes(diff_output)
                    hist_changed_lines = hist_line_changes['added_lines'] + hist_line_changes['removed_lines']
                    
                    # Line proximity analysis - most reliable indicator
                    line_overlap = 0
                    for root_line in changed_lines:
                        for hist_line in hist_changed_lines:
                            if abs(root_line - hist_line) <= 10:
                                line_overlap += 1
                    
                    if line_overlap > 0:
                        overlap_score = min(line_overlap / len(changed_lines), 0.5)
                        relevance_score += overlap_score
                        reasons.append(f"Line proximity: {line_overlap} lines near root changes")
                    
                    # Function overlap analysis
                    root_functions = set(self.analyze_line_changes(self.get_clean_file_diff(
                        root_commit.revision, filename, root_commit.channel))['functions_affected'])
                    hist_functions = set(hist_line_changes['functions_affected'])
                    
                    common_functions = root_functions.intersection(hist_functions)
                    if common_functions:
                        relevance_score += 0.3
                        reasons.append(f"Common functions: {', '.join(list(common_functions)[:3])}")
                        
                except Exception as e:
                    pass
            
            # Bug number correlation - strong indicator for related commits
            root_bugs = set(root_commit.bug_numbers)
            commit_bug_match = re.findall(r'[Bb]ug (\d+)', commit_desc)
            commit_bugs = set(commit_bug_match)
            
            if root_bugs.intersection(commit_bugs):
                relevance_score += 0.4
                reasons.append(f"Related bug numbers: {', '.join(root_bugs.intersection(commit_bugs))}")
            
            # File mention in description - moderate indicator
            if filename.lower() in commit_desc_lower:
                relevance_score += 0.2
                reasons.append("Same filename mentioned in description")
            
            return {
                'relevance_score': min(relevance_score, 1.0),
                'reasons': reasons,
                'commit_node': commit_node,
                'commit_desc': commit_desc
            }
            
        except Exception as e:
            return {
                'relevance_score': 0.0,
                'reasons': [f"Analysis failed: {str(e)}"],
                'commit_node': commit_node,
                'commit_desc': commit_desc
            }

    def find_exact_introducing_commit(self, revision: str, filename: str, introducing_commits: List[Dict], preferred_channel: str = None) -> Dict[str, Any]:
        """
        Find the exact commit that introduced the vulnerable code by comparing diffs
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return {}
        
        channel, repo_path = repo_info
        
        root_diff = self.get_clean_file_diff(revision, filename, preferred_channel)
        if not root_diff:
            return {}
        
        root_changes = self.analyze_line_changes(root_diff)
        root_removed_lines = root_changes['removed_lines']
        
        removed_code_patterns = self._extract_removed_code_patterns(root_diff)
        
        exact_matches = []
        
        for commit in introducing_commits:
            commit_node = commit['node']
            
            hist_diff = None
            for channel_name, channel_repo_path in self.repo_paths.items():
                success, diff_output = self._run_hg_command(channel_repo_path, ['diff', '-c', commit_node, filename])
                if success and diff_output:
                    hist_diff = diff_output
                    break
            
            if not hist_diff:
                continue
            
            hist_changes = self.analyze_line_changes(hist_diff)
            hist_added_lines = hist_changes['added_lines']
            
            added_code_patterns = self._extract_added_code_patterns(hist_diff)
            
            match_score = self._compare_code_patterns(removed_code_patterns, added_code_patterns)
            
            if match_score >= 0.7:
                exact_matches.append({
                    **commit,
                    'pattern_match_score': match_score,
                    'added_lines_count': len(hist_added_lines),
                    'matching_patterns': min(len(removed_code_patterns), len(added_code_patterns)),
                    'historical_diff': hist_diff[:1000]
                })
        
        exact_matches.sort(key=lambda x: x['pattern_match_score'], reverse=True)
        
        return {
            'root_revision': revision,
            'root_removed_lines': len(root_removed_lines),
            'root_patterns_analyzed': len(removed_code_patterns),
            'exact_matches': exact_matches,
            'analysis_summary': {
                'commits_analyzed': len(introducing_commits),
                'exact_matches_found': len(exact_matches),
                'best_match_score': exact_matches[0]['pattern_match_score'] if exact_matches else 0.0
            }
        }
    
    def _extract_removed_code_patterns(self, diff_content: str) -> List[str]:
        """
        Extract code patterns from removed lines in the diff
        """
        patterns = []
        in_hunk = False
        
        for line in diff_content.split('\n'):
            if line.startswith('@@'):
                in_hunk = True
                continue
            
            if not in_hunk:
                continue
            
            if line.startswith('-') and not line.startswith('---'):
                code_line = line[1:].strip()
                
                if not code_line:
                    continue
                
                normalized = self._normalize_code_line(code_line)
                if normalized and len(normalized) > 10:
                    patterns.append(normalized)
        
        return patterns
    
    def _extract_added_code_patterns(self, diff_content: str) -> List[str]:
        """
        Extract code patterns from added lines in the diff
        """
        patterns = []
        in_hunk = False
        
        for line in diff_content.split('\n'):
            if line.startswith('@@'):
                in_hunk = True
                continue
            
            if not in_hunk:
                continue
            
            if line.startswith('+') and not line.startswith('+++'):
                code_line = line[1:].strip()
                
                if not code_line:
                    continue
                
                normalized = self._normalize_code_line(code_line)
                if normalized and len(normalized) > 10:
                    patterns.append(normalized)
        
        return patterns
    
    def _normalize_code_line(self, code_line: str) -> str:
        """
        Normalize code line for comparison
        """
        if '//' in code_line:
            code_line = code_line[:code_line.index('//')]
        
        code_line = ' '.join(code_line.split())
        normalized = code_line.lower()
        
        return normalized
    
    def _compare_code_patterns(self, removed_patterns: List[str], added_patterns: List[str]) -> float:
        """
        Compare removed code patterns with added code patterns to find matches
        """
        if not removed_patterns or not added_patterns:
            return 0.0
        
        total_matches = 0
        exact_matches = 0
        
        for removed_pattern in removed_patterns:
            best_match_score = 0.0
            
            for added_pattern in added_patterns:
                if removed_pattern == added_pattern:
                    exact_matches += 1
                    best_match_score = 1.0
                    break
                
                similarity = self._calculate_similarity(removed_pattern, added_pattern)
                if similarity > best_match_score:
                    best_match_score = similarity
            
            total_matches += best_match_score
        
        base_score = total_matches / len(removed_patterns)
        exact_bonus = (exact_matches / len(removed_patterns)) * 0.3
        
        return min(base_score + exact_bonus, 1.0)
    
    def _calculate_similarity(self, pattern1: str, pattern2: str) -> float:
        """
        Calculate similarity between two code patterns
        """
        words1 = set(pattern1.split())
        words2 = set(pattern2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0.0
    
    def get_file_content_with_line_numbers(self, revision: str, filename: str, preferred_channel: str = None) -> Optional[str]:
        """
        Get the full content of a file at a specific revision with line numbers
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return None
        
        channel, repo_path = repo_info
        
        success, content = self._run_hg_command(repo_path, ['cat', '-r', revision, filename])
        
        if success:
            lines = content.split('\n')
            numbered_lines = []
            for i, line in enumerate(lines, 1):
                numbered_lines.append(f"{i:6d}\t{line}")
            
            numbered_content = '\n'.join(numbered_lines)
            print(f" Successfully extracted {filename} at revision {revision} with {len(lines)} lines")
            return numbered_content
        else:
            print(f" Error getting file content for {filename} at revision {revision}: {content}")
            return None
    
    def save_file_content_with_line_numbers(self, revision: str, filename: str, output_path: str = None, preferred_channel: str = None) -> bool:
        """
        Save the file content with line numbers to a local file
        """
        content = self.get_file_content_with_line_numbers(revision, filename, preferred_channel)
        if not content:
            return False
        
        if not output_path:
            safe_filename = filename.replace('/', '_').replace('\\', '_').replace(':', '_')
            output_path = f"{safe_filename}_at_{revision[:12]}_with_lines.txt"
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(f"File: {filename}\n")
                f.write(f"Revision: {revision}\n")
                f.write(f"Extracted at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 80 + "\n\n")
                f.write(content)
            
            print(f" File content with line numbers saved to: {output_path}")
            return True
        except Exception as e:
            print(f" Error saving file: {e}")
            return False

    def get_file_content_at_revision(self, revision: str, filename: str, preferred_channel: str = None) -> Optional[str]:
        """
        Get the raw file content at a specific revision without line numbers
        """
        repo_info = self.find_revision_in_repos(revision, preferred_channel)
        if not repo_info:
            return None
        
        channel, repo_path = repo_info
        
        success, content = self._run_hg_command(repo_path, ['cat', '-r', revision, filename])
        
        if success:
            return content
        else:
            print(f" Error getting file content for {filename} at revision {revision}: {content}")
            return None

    def parse_functions_from_content(self, content: str, filename: str) -> List[Dict[str, Any]]:
        """
        Parse functions from file content using tree-sitter
        """
        if not TREE_SITTER_AVAILABLE:
            print(" Tree-sitter parser not available. Skipping function parsing.")
            return []
        
        try:
            temp_fd, temp_path = tempfile.mkstemp(suffix='.c', text=True)
            
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as temp_file:
                temp_file.write(content)
            
            c_parser = CParser()
            root_node = c_parser.parse_file(temp_path)
            functions = c_parser.extract_functions(root_node)
            
            try:
                os.remove(temp_path)
            except:
                pass
            
            return functions
            
        except Exception as e:
            print(f" Error parsing functions from {filename}: {e}")
            return []

    def analyze_functions_in_diff(self, revision: str, filename: str, preferred_channel: str = None) -> Dict[str, Any]:
        """
        Analyze which functions were affected by changes in a specific commit
        """
        print(f"🔍 Analyzing functions affected by commit {revision[:12]} in {filename}")
        
        diff_content = self.get_clean_file_diff(revision, filename, preferred_channel)
        if not diff_content:
            print(f" Could not get diff for {filename}")
            return {}
        
        line_changes = self.analyze_line_changes(diff_content)
        added_lines = set(line_changes['added_lines'])
        removed_lines = set(line_changes['removed_lines'])
        all_changed_lines = added_lines.union(removed_lines)
        
        file_content = self.get_file_content_at_revision(revision, filename, preferred_channel)
        if not file_content:
            print(f" Could not get file content for {filename} at revision {revision}")
            return {}
        
        functions = self.parse_functions_from_content(file_content, filename)
        
        # IMMEDIATE PRINT: Total functions found
        print(f" Found {len(functions)} functions in {filename}")
        
        if not functions:
            return {
                'total_functions': 0,
                'affected_functions': [],
                'line_changes': line_changes,
                'diff_content': diff_content
            }
        
        affected_functions = []
        
        for func in functions:
            func_name = func.get('name', 'unknown')
            func_start = func.get('start_line', 0)
            func_end = func.get('end_line', 0)
            
            if not func_name or func_name == 'unknown' or func_start == 0 or func_end == 0:
                continue
            
            func_added_lines = []
            func_removed_lines = []
            
            for line_num in added_lines:
                if func_start <= line_num <= func_end:
                    func_added_lines.append(line_num)
            
            for line_num in removed_lines:
                if func_start <= line_num <= func_end:
                    func_removed_lines.append(line_num)
            
            if func_added_lines or func_removed_lines:
                func_size = func_end - func_start + 1
                total_changes = len(func_added_lines) + len(func_removed_lines)
                change_percentage = (total_changes / func_size * 100) if func_size > 0 else 0
                
                func_lines = file_content.split('\n')[func_start-1:func_end]
                func_code = '\n'.join(func_lines)
                
                function_analysis = FunctionAnalysis(
                    name=func_name,
                    start_line=func_start,
                    end_line=func_end,
                    size=func_size,
                    return_type=func.get('return_type', ''),
                    parameters=func.get('parameters', []),
                    lines_added_in_commit=sorted(func_added_lines),
                    lines_removed_in_fix=sorted(func_removed_lines),
                    is_newly_introduced=len(func_added_lines) > len(func_removed_lines),
                    code_content=func_code
                )
                
                affected_functions.append(function_analysis)
                
                # IMMEDIATE PRINT: Individual function details
                print(f"    {func_name}: lines {func_start}-{func_end} ({func_size} lines)")
                print(f"      Added: {len(func_added_lines)} lines, Removed: {len(func_removed_lines)} lines")
                print(f"      Change: {change_percentage:.1f}%")
        
        # IMMEDIATE PRINT: Summary of affected functions
        print(f" SUMMARY: {len(affected_functions)}/{len(functions)} functions affected by commit {revision[:12]}")
        
        affected_functions.sort(key=lambda f: len(f.lines_added_in_commit) + len(f.lines_removed_in_fix), reverse=True)
        
        return {
            'total_functions': len(functions),
            'affected_functions': affected_functions,
            'line_changes': line_changes,
            'diff_content': diff_content,
            'analysis_summary': {
                'functions_affected': len(affected_functions),
                'newly_introduced_functions': len([f for f in affected_functions if f.is_newly_introduced]),
                'total_lines_changed': len(all_changed_lines)
            }
        }
    def verify_analysis_accuracy(self, revision: str, filename: str, 
                                claimed_analysis: Dict[str, Any], 
                                preferred_channel: str = None) -> Dict[str, Any]:
        """
        Verify the accuracy of the analysis by comparing with actual diff and functions
        """
        print(f"\n VERIFYING ANALYSIS ACCURACY FOR {filename}")
        print("=" * 80)
        
        # Get the actual diff from the introducing commit
        print(" Getting actual diff from introducing commit...")
        actual_diff = self.get_clean_file_diff(revision, filename, preferred_channel)
        if not actual_diff:
            return {'error': 'Could not get diff content'}
        
        # Extract exact changed lines from the diff
        actual_diff_lines = self._extract_exact_diff_lines(actual_diff)
        
        # Get the file content and parse functions
        print(" Getting file content and parsing functions...")
        file_content = self.get_file_content_at_revision(revision, filename, preferred_channel)
        if not file_content:
            return {'error': 'Could not get file content'}
        
        actual_functions = self.parse_functions_from_content(file_content, filename)
        
        # Match diff lines to actual functions
        print(" Matching diff lines to actual functions...")
        actual_affected_functions = self._match_diff_lines_to_functions(actual_diff_lines, actual_functions)
        
        # Compare with claimed analysis
        print(" Comparing with claimed analysis...")
        verification_results = self._compare_claimed_vs_actual(claimed_analysis, actual_affected_functions)
        
        return {
            'filename': filename,
            'revision': revision,
            'actual_diff_lines': actual_diff_lines,
            'actual_affected_functions': actual_affected_functions,
            'verification_results': verification_results,
            'accuracy_summary': verification_results.get('summary', {})
        }

    def _extract_exact_diff_lines(self, diff_content: str) -> Dict[str, List[int]]:
        """
        Extract the EXACT line numbers that were changed from the diff
        """
        changes = {
            'added_lines': [],
            'removed_lines': []
        }
        
        if not diff_content:
            return changes
        
        print("🔍 Parsing diff content for exact line numbers...")
        
        current_new_line = 0
        current_old_line = 0
        in_hunk = False
        
        for line in diff_content.split('\n'):
            if line.startswith('@@'):
                match = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
                if match:
                    current_old_line = int(match.group(1))
                    current_new_line = int(match.group(2))
                    in_hunk = True
                    print(f"  📍 Hunk: old line {current_old_line}, new line {current_new_line}")
                continue
            
            if not in_hunk:
                continue
            
            if line.startswith('+') and not line.startswith('+++'):
                changes['added_lines'].append(current_new_line)
                print(f"    + Line {current_new_line}: {line[1:60]}...")
                current_new_line += 1
            elif line.startswith('-') and not line.startswith('---'):
                changes['removed_lines'].append(current_old_line)
                print(f"    - Line {current_old_line}: {line[1:60]}...")
                current_old_line += 1
            elif line.startswith(' '):
                current_new_line += 1
                current_old_line += 1
        
        print(f" Diff summary:")
        print(f"  Added lines: {sorted(changes['added_lines'])}")
        print(f"  Removed lines: {sorted(changes['removed_lines'])}")
        print(f"  Total changes: {len(changes['added_lines']) + len(changes['removed_lines'])} lines")
        
        return changes

    def _match_diff_lines_to_functions(self, diff_lines: Dict[str, List[int]], 
                                      functions: List[Dict]) -> Dict[str, Dict]:
        """
        Match the exact diff lines to the functions that contain them
        """
        print(" Matching diff lines to functions...")
        
        affected_functions = {}
        
        for func in functions:
            func_name = func.get('name', 'unknown')
            func_start = func.get('start_line', 0)
            func_end = func.get('end_line', 0)
            
            if not func_name or func_name == 'unknown' or func_start == 0:
                continue
            
            # Find which diff lines fall within this function
            func_added_lines = []
            func_removed_lines = []
            
            for line_num in diff_lines['added_lines']:
                if func_start <= line_num <= func_end:
                    func_added_lines.append(line_num)
            
            for line_num in diff_lines['removed_lines']:
                if func_start <= line_num <= func_end:
                    func_removed_lines.append(line_num)
            
            func_changed_lines = func_added_lines + func_removed_lines
            
            # Include functions that have ANY changes (even just 1 line)
            if func_changed_lines:
                func_size = func_end - func_start + 1
                change_percentage = (len(func_changed_lines) / func_size) * 100
                
                affected_functions[func_name] = {
                    'function_boundaries': {
                        'start_line': func_start,
                        'end_line': func_end,
                        'size': func_size
                    },
                    'exact_changes': {
                        'added_lines': sorted(func_added_lines),
                        'removed_lines': sorted(func_removed_lines),
                        'total_changed_lines': sorted(func_changed_lines),
                        'change_count': len(func_changed_lines),
                        'change_percentage': round(change_percentage, 1)
                    },
                    'function_metadata': {
                        'return_type': func.get('return_type', 'unknown'),
                        'parameters': func.get('parameters', [])
                    }
                }
                
                print(f"   {func_name}:")
                print(f"     Lines {func_start}-{func_end} ({func_size} lines)")
                print(f"     Added: {func_added_lines}")
                print(f"     Removed: {func_removed_lines}")
                print(f"     Changes: {len(func_changed_lines)} lines ({change_percentage:.1f}%)")
        
        return affected_functions

    def _compare_claimed_vs_actual(self, claimed_analysis: Dict, actual_functions: Dict) -> Dict:
        """
        Compare the claimed analysis with the actual function changes
        """
        print("🔍 Comparing claimed vs actual analysis...")
        
        verification_results = {
            'accurate_functions': {},
            'inaccurate_functions': {},
            'missing_functions': {},
            'false_positive_functions': {},
            'summary': {}
        }
        
        # Extract claimed function data
        claimed_functions = set()
        claimed_function_data = {}
        
        if 'function_details' in claimed_analysis:
            claimed_function_data = claimed_analysis['function_details']
            claimed_functions = set(claimed_function_data.keys())
        elif 'affected_functions' in claimed_analysis:
            for func in claimed_analysis['affected_functions']:
                if hasattr(func, 'name'):
                    func_name = func.name
                    claimed_functions.add(func_name)
                    claimed_function_data[func_name] = {
                        'changed_lines': getattr(func, 'lines_added_in_commit', []) + getattr(func, 'lines_removed_in_fix', [])
                    }
        
        actual_function_names = set(actual_functions.keys())
        
        print(f" Comparison overview:")
        print(f"  Claimed functions: {sorted(claimed_functions)}")
        print(f"  Actual functions: {sorted(actual_function_names)}")
        
        # Check each claimed function
        for func_name in claimed_functions:
            if func_name in actual_functions:
                claimed_lines = set(claimed_function_data[func_name].get('changed_lines', []))
                actual_lines = set(actual_functions[func_name]['exact_changes']['total_changed_lines'])
                
                if claimed_lines == actual_lines:
                    verification_results['accurate_functions'][func_name] = {
                        'status': 'ACCURATE',
                        'claimed_lines': sorted(claimed_lines),
                        'actual_lines': sorted(actual_lines)
                    }
                    print(f"   {func_name}: ACCURATE")
                else:
                    verification_results['inaccurate_functions'][func_name] = {
                        'status': 'INACCURATE',
                        'claimed_lines': sorted(claimed_lines),
                        'actual_lines': sorted(actual_lines),
                        'extra_claimed': sorted(claimed_lines - actual_lines),
                        'missing_claimed': sorted(actual_lines - claimed_lines)
                    }
                    print(f"   {func_name}: INACCURATE")
                    print(f"     Claimed: {sorted(claimed_lines)}")
                    print(f"     Actual:  {sorted(actual_lines)}")
                    if claimed_lines - actual_lines:
                        print(f"     Extra: {sorted(claimed_lines - actual_lines)}")
                    if actual_lines - claimed_lines:
                        print(f"     Missing: {sorted(actual_lines - claimed_lines)}")
            else:
                verification_results['false_positive_functions'][func_name] = {
                    'status': 'FALSE_POSITIVE',
                    'claimed_lines': claimed_function_data[func_name].get('changed_lines', [])
                }
                print(f"   {func_name}: FALSE POSITIVE")
        
        # Check for missing functions
        for func_name in actual_function_names:
            if func_name not in claimed_functions:
                verification_results['missing_functions'][func_name] = {
                    'status': 'MISSING',
                    'actual_lines': actual_functions[func_name]['exact_changes']['total_changed_lines']
                }
                print(f"    {func_name}: MISSING from claimed analysis")
        
        # Calculate summary
        total_actual = len(actual_function_names)
        accurate_count = len(verification_results['accurate_functions'])
        
        verification_results['summary'] = {
            'total_functions_actual': total_actual,
            'total_functions_claimed': len(claimed_functions),
            'accurate_functions': accurate_count,
            'inaccurate_functions': len(verification_results['inaccurate_functions']),
            'false_positives': len(verification_results['false_positive_functions']),
            'missing_functions': len(verification_results['missing_functions']),
            'accuracy_percentage': round((accurate_count / max(total_actual, 1)) * 100, 1) if total_actual > 0 else 0.0
        }
        
        print(f"\n ACCURACY SUMMARY:")
        print(f"   Actually changed: {total_actual} functions")
        print(f"   Claimed changed: {len(claimed_functions)} functions")
        print(f"   Accurate: {accurate_count}")
        print(f"   Inaccurate: {len(verification_results['inaccurate_functions'])}")
        print(f"   False positives: {len(verification_results['false_positive_functions'])}")
        print(f"    Missing: {len(verification_results['missing_functions'])}")
        print(f"   Accuracy: {verification_results['summary']['accuracy_percentage']}%")
        
        return verification_results

    def enhanced_extract_and_analyze_introducing_commits(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enhanced version that automatically extracts and analyzes functions from introducing commits
        Now handles both exact matches and best potential matches
        """
        if 'file_analyses' not in results:
            print(" No file analyses found in results")
            return {}
        
        print("\n" + "="*80)
        print("ENHANCED INTRODUCING COMMIT ANALYSIS WITH FUNCTION PARSING")
        print("="*80)
        
        enhanced_file_analyses = {}
        
        for filename, analysis in results['file_analyses'].items():
            print(f"\n Processing file: {filename}")
            
            # First try exact introducing commit
            exact_analysis = analysis.get('exact_introducing_commit', {})
            exact_matches = exact_analysis.get('exact_matches', [])
            
            introducing_commit_data = None
            analysis_type = None
            
            if exact_matches:
                # Use exact match
                best_match = exact_matches[0]
                introducing_commit_data = {
                    'revision': best_match['node'],
                    'pattern_match_score': best_match['pattern_match_score'],
                    'author': best_match['author'],
                    'date': best_match['date'],
                    'description': best_match['desc']
                }
                analysis_type = "EXACT"
                print(f"   Found EXACT introducing commit: {introducing_commit_data['revision']}")
                
            elif 'best_potential_introducing_commit' in analysis:
                # Use best potential match
                potential_data = analysis['best_potential_introducing_commit']
                introducing_commit_data = {
                    'revision': potential_data['revision'],
                    'pattern_match_score': potential_data['relevance_score'],  # Use relevance score
                    'author': potential_data['author'],
                    'date': potential_data['date'],
                    'description': potential_data['description']
                }
                analysis_type = "POTENTIAL"
                print(f"   Found POTENTIAL introducing commit: {introducing_commit_data['revision']}")
                print(f"   Relevance score: {potential_data['relevance_score']:.2f}")
                print(f"   Reasons: {', '.join(potential_data.get('reasons', []))}")
                
            else:
                print(f"   No introducing commit found for {filename}")
                continue
            
            introducing_revision = introducing_commit_data['revision']
            pattern_score = introducing_commit_data['pattern_match_score']
            
            print(f"   {analysis_type} commit: {introducing_revision}")
            print(f"   Score: {pattern_score:.2f}")
            print(f"   Author: {introducing_commit_data['author']}")
            print(f"   Date: {introducing_commit_data['date']}")
            print(f"   Description: {introducing_commit_data['description']}")
            
            root_channel = results.get('commit_info', {}).get('channel')
            
            print(f"   Analyzing functions in introducing commit...")
            
            introducing_function_analysis = self.analyze_functions_in_diff(
                introducing_revision, filename, root_channel
            )
            
            print(f"   Analyzing functions in root fix commit...")
            
            root_revision = results.get('revision')
            root_function_analysis = self.analyze_functions_in_diff(
                root_revision, filename, root_channel
            )
            
            print(f"   Extracting file content...")
            
            repo_info = self.find_revision_in_repos(introducing_revision, root_channel)
            if repo_info:
                channel, repo_path = repo_info
                success, full_hash = self._run_hg_command(repo_path, ['log', '-r', introducing_revision, '--template', '{node}'])
                if success:
                    full_introducing_revision = full_hash.strip()
                else:
                    full_introducing_revision = introducing_revision
            else:
                full_introducing_revision = introducing_revision
            
            safe_filename = filename.replace('/', '_').replace('\\', '_').replace(':', '_')
            output_filename = f"{safe_filename}_{analysis_type.lower()}_introducing_commit_{introducing_revision}_with_functions.txt"
            
            if self.save_file_content_with_line_numbers(full_introducing_revision, filename, output_filename, root_channel):
                print(f"   Saved file content to: {output_filename}")
            else:
                print(f"   Failed to save file content")
            
            # Clean function analysis
            clean_introducing_functions = self._clean_function_analysis(introducing_function_analysis)
            clean_root_functions = self._clean_function_analysis(root_function_analysis)
            # Generate function comparison
            

            
            enhanced_file_analyses[filename] = {
                'introducing_commit_info': {
                    'revision': full_introducing_revision,
                    'short_revision': introducing_revision,
                    'pattern_match_score': pattern_score,
                    'author': introducing_commit_data['author'],
                    'date': introducing_commit_data['date'],
                    'description': introducing_commit_data['description'],
                    'output_file': output_filename,
                    'analysis_type': analysis_type  # Track whether this was exact or potential
                },
                'introducing_functions': clean_introducing_functions,
                'fixed_functions': clean_root_functions,
                'function_comparison': self._compare_clean_function_analyses(
                    clean_introducing_functions, clean_root_functions
                )
            }
            
            # Print clean summary
            

            if clean_introducing_functions.get('function_details'):
                print(f"   Functions in {analysis_type.lower()} introducing commit: {len(clean_introducing_functions['function_details'])}")
                for func_name in list(clean_introducing_functions['function_details'].keys())[:3]:
                    func_info = clean_introducing_functions['function_details'][func_name]
                    print(f"      {func_name}: lines {func_info['start']}-{func_info['end']} ({len(func_info['changed_lines'])} lines changed)")
            
            if clean_root_functions.get('function_details'):
                print(f"   Functions in root fix commit: {len(clean_root_functions['function_details'])}")
                for func_name in list(clean_root_functions['function_details'].keys())[:3]:
                    func_info = clean_root_functions['function_details'][func_name]
                    print(f"      {func_name}: lines {func_info['start']}-{func_info['end']} ({len(func_info['changed_lines'])} lines changed)")
        
        return enhanced_file_analyses
    def _clean_function_analysis(self, function_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean function analysis to remove duplicates and provide essential info only
        Removed vulnerability scoring components
        """
        if not function_analysis or not function_analysis.get('affected_functions'):
            return {
                'total_functions': 0,
                'function_details': {},
                'summary': {
                    'functions_affected': 0,
                    'total_lines_changed': 0
                }
            }
        
        function_details = {}
        total_lines_changed = 0
        
        for func in function_analysis['affected_functions']:
            if hasattr(func, 'name') and func.name:
                # Combine added and removed lines for total changed lines
                changed_lines = list(set(func.lines_added_in_commit + func.lines_removed_in_fix))
                total_lines_changed += len(changed_lines)
                
                function_details[func.name] = {
                    'start': func.start_line,
                    'end': func.end_line,
                    'size': func.size,
                    'changed_lines': sorted(changed_lines),
                    'return_type': func.return_type if func.return_type else 'unknown'
                }
        
        return {
            'total_functions': function_analysis.get('total_functions', 0),
            'function_details': function_details,
            'summary': {
                'functions_affected': len(function_details),
                'total_lines_changed': total_lines_changed
            }
        }

    def _compare_clean_function_analyses(self, introducing_functions: Dict[str, Any], 
                                       root_functions: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compare clean function analyses between introducing and root commits
        Removed vulnerability scoring components
        """
        intro_func_names = set(introducing_functions.get('function_details', {}).keys())
        root_func_names = set(root_functions.get('function_details', {}).keys())
        
        # Find functions that appear in both commits (the vulnerable ones)
        common_functions = intro_func_names.intersection(root_func_names)
        
        vulnerable_functions = {}
        
        for func_name in common_functions:
            intro_func = introducing_functions['function_details'][func_name]
            root_func = root_functions['function_details'][func_name]
            
            vulnerable_functions[func_name] = {
                'introduced_details': {
                    'start_line': intro_func['start'],
                    'end_line': intro_func['end'],
                    'size': intro_func['size'],
                    'changed_lines': intro_func['changed_lines']
                },
                'fixed_details': {
                    'start_line': root_func['start'],
                    'end_line': root_func['end'],
                    'size': root_func['size'],
                    'changed_lines': root_func['changed_lines']
                },
                'analysis': {
                    'lines_that_introduced_vulnerability': intro_func['changed_lines'],
                    'lines_that_fixed_vulnerability': root_func['changed_lines'],
                    'total_affected_lines': len(set(intro_func['changed_lines'] + root_func['changed_lines']))
                }
            }
        
        return {
            'vulnerable_functions': vulnerable_functions,
            'summary': {
                'functions_in_introducing': len(intro_func_names),
                'functions_in_root': len(root_func_names),
                'common_vulnerable_functions': len(common_functions),
                'functions_only_in_introducing': len(intro_func_names - root_func_names),
                'functions_only_in_root': len(root_func_names - intro_func_names)
            }
        }

    def generate_clean_function_report(self, enhanced_analyses: Dict[str, Any], 
                                     crash_id: str) -> Dict[str, Any]:
        """
        Generate a clean function report with essential function-level details only
        Removed vulnerability scoring and risk assessment
        """
        print("\n" + "="*80)
        print("GENERATING CLEAN FUNCTION REPORT")
        print("="*80)
        
        report = {
            'crash_id': crash_id,
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'summary': {},
            'affected_functions_by_file': {},
            'common_functions': []
        }
        
        total_affected_functions = 0
        all_affected_functions = []
        
        for filename, file_analysis in enhanced_analyses.items():
            print(f"\n Analyzing: {filename}")
            
            func_comparison = file_analysis.get('function_comparison', {})
            vulnerable_functions = func_comparison.get('vulnerable_functions', {})
            
            if not vulnerable_functions:
                print(f"   No common functions identified")
                continue
            
            print(f"   Found {len(vulnerable_functions)} common functions")
            
            file_affected_functions = {}
            
            for func_name, func_data in vulnerable_functions.items():
                introduced_details = func_data['introduced_details']
                fixed_details = func_data['fixed_details']
                analysis = func_data['analysis']
                
                function_data = {
                    'location': {
                        'start_line': introduced_details['start_line'],
                        'end_line': introduced_details['end_line'],
                        'size': introduced_details['size']
                    },
                    'changes': {
                        'lines_introduced': analysis['lines_that_introduced_vulnerability'],
                        'lines_fixed': analysis['lines_that_fixed_vulnerability'],
                        'total_affected_lines': analysis['total_affected_lines']
                    },
                    'commit_info': file_analysis['introducing_commit_info']
                }
                
                file_affected_functions[func_name] = function_data
                
                # Add to global list
                all_affected_functions.append({
                    'name': func_name,
                    'filename': filename,
                    'lines_changed_in_introduction': len(analysis['lines_that_introduced_vulnerability']),
                    'lines_changed_in_fix': len(analysis['lines_that_fixed_vulnerability']),
                    **function_data
                })
                
                print(f"     {func_name}:")
                print(f"       Lines {introduced_details['start_line']}-{introduced_details['end_line']}")
                print(f"       Introduced: {len(analysis['lines_that_introduced_vulnerability'])} lines")
                print(f"       Fixed: {len(analysis['lines_that_fixed_vulnerability'])} lines")
            
            report['affected_functions_by_file'][filename] = file_affected_functions
            total_affected_functions += len(vulnerable_functions)
        
        # Sort functions by total lines changed (introduced + fixed)
        all_affected_functions.sort(
            key=lambda x: x['lines_changed_in_introduction'] + x['lines_changed_in_fix'], 
            reverse=True
        )
        
        report['summary'] = {
            'total_affected_functions': total_affected_functions,
            'files_with_affected_functions': len([f for f in enhanced_analyses.keys() 
                                                if enhanced_analyses[f].get('function_comparison', {}).get('vulnerable_functions')]),
            'average_lines_per_function': sum(f['lines_changed_in_introduction'] + f['lines_changed_in_fix'] 
                                            for f in all_affected_functions) / len(all_affected_functions) if all_affected_functions else 0.0
        }
        
        report['common_functions'] = all_affected_functions[:20]  # Top 20
        
        # Print summary
        print(f"\n CLEAN FUNCTION REPORT SUMMARY:")
        print(f"    Total Affected Functions: {total_affected_functions}")
        print(f"    Files with Affected Functions: {report['summary']['files_with_affected_functions']}")
        print(f"    Average Lines Changed: {report['summary']['average_lines_per_function']:.1f}")
        
        if all_affected_functions:
            print(f"\n TOP AFFECTED FUNCTIONS:")
            for i, func in enumerate(all_affected_functions[:5], 1):
                total_changes = func['lines_changed_in_introduction'] + func['lines_changed_in_fix']
                print(f"   {i}. {func['name']} ({total_changes} lines changed)")
                print(f"       File: {func['filename']}")
                print(f"       Lines: {func['location']['start_line']}-{func['location']['end_line']}")
                print(f"       Intro: {func['lines_changed_in_introduction']}, Fix: {func['lines_changed_in_fix']}")
        
        return report

    def update_repositories(self):
        """
        Update all local repositories to get latest changes
        """
        print("🔄 Updating local repositories...")
        for channel, repo_path in self.repo_paths.items():
            print(f"   Updating {channel}...")
            success, output = self._run_hg_command(repo_path, ['pull', '-u'])
            if success:
                print(f"     {channel} updated successfully")
            else:
                print(f"     Failed to update {channel}: {output}")
    
    def full_analysis(self, crash_id: str, update_repos: bool = False) -> Dict[str, Any]:
        """
        Complete analysis pipeline using local repositories
        Fixed to eliminate duplicate output
        """
        print(f" Starting analysis for crash ID: {crash_id}")
        
        if update_repos:
            self.update_repositories()
        
        # Step 1: Get Build ID
        build_id = self.get_build_id(crash_id)
        if not build_id:
            return {'error': 'Could not retrieve build ID'}
        
        # Step 2: Get Revision and Channel
        revision_info = self.get_revision_and_channel_from_build_id(build_id)
        if not revision_info:
            return {'error': 'Could not retrieve revision'}
        
        revision, channel = revision_info
        print(f" Detected channel: {channel}")
        
        # Step 3: EARLY MERGE COMMIT FILTER
        if self.is_merge_commit(revision, channel):
            return {'error': 'Filtered out: Merge commit detected', 'merge_commit': True}
        
        # Step 4: Get Commit Info (simplified, without file changes)
        commit_info = self.get_commit_info(revision, channel)
        if not commit_info:
            return {'error': 'Could not retrieve commit info'}
        
        # Step 5: Get file changes ONCE with output
        print(f" Getting file changes for revision {revision[:12]}...")
        file_changes_dict = self.get_changed_files(revision, channel, silent=True)  # Silent first

        # Update commit_info with actual file changes
        all_files = []
        for change_type, files in file_changes_dict.items():
            if change_type not in ['unknown', 'filtered_out']:
                all_files.extend(files)
        commit_info.files_changed = all_files

        print(f" Root Commit Info:")
        print(f"    Channel: {commit_info.channel}")
        print(f"    Author: {commit_info.author}")
        print(f"    Date: {commit_info.date}")
        print(f"    Description: {commit_info.description}")
        print(f"    Bug numbers: {commit_info.bug_numbers}")

        # Calculate totals
        code_files = all_files
        filtered_files = file_changes_dict.get('filtered_out', [])
        total_all_files = len(code_files) + len(filtered_files)

        print(f" File Analysis Summary:")
        print(f"    Total files changed: {total_all_files}")

        # Print all file names (both code and filtered)
        all_changed_files = []
        for change_type, files in file_changes_dict.items():
            if change_type == 'filtered_out':
                for f in files:
                    all_changed_files.append(f)  # These already have status prefix like "A filename" or "R filename"
            elif files:
                for f in files:
                    # Add status prefix for code files
                    if change_type == 'modified':
                        all_changed_files.append(f"M {f}")
                    elif change_type == 'added':
                        all_changed_files.append(f"A {f}")
                    elif change_type == 'removed':
                        all_changed_files.append(f"R {f}")

        for f in all_changed_files:
            print(f"      • {f}")

        print(f"    Code files (will be analyzed): {len(code_files)}")
        for f in code_files:
            print(f"      • {f}")

        print(f"    Non-code files (filtered out): {len(filtered_files)}")
        for filtered_file in filtered_files:
            print(f"       {filtered_file}")

        # Show which files will be analyzed
        files_to_analyze = file_changes_dict.get('modified', []) + file_changes_dict.get('added', [])
        if files_to_analyze:
            print(f" Files selected for introducing commit analysis:")
            for i, f in enumerate(files_to_analyze, 1):
                print(f"    {i}. {f}")
        else:
            print(f" No code files selected for analysis")
        file_analyses = {}
        for i, filename in enumerate(files_to_analyze, 1):
            print(f"\n [{i}/{len(files_to_analyze)}] Analyzing: {filename}")
            
            analysis = {}
            
            # Get diff for this file
            diff = self.get_clean_file_diff(revision, filename, channel)
            if diff:
                line_changes = self.analyze_line_changes(diff)
                analysis['line_changes'] = line_changes
                analysis['diff_content'] = diff
                
                changed_lines = line_changes['added_lines'] + line_changes['removed_lines']
                print(f"      Lines changed: {len(changed_lines)} ({len(line_changes['added_lines'])} added, {len(line_changes['removed_lines'])} removed)")
                
                if changed_lines:
                    line_affecting_commits = self.get_commits_affecting_lines(revision, filename, changed_lines, channel)
                    analysis['line_affecting_commits'] = line_affecting_commits
            else:
                print(f"       Could not get diff for {filename}")
                continue
            if filename not in file_changes_dict.get('removed', []):
                # Get recent history
                history = self.get_file_history(revision, filename, max_commits=20, preferred_channel=channel)
                analysis['recent_commits'] = history
                
                print(f"      🔎 Searching for introducing commits in {len(history)} historical commits...")
                introducing_analysis = self.find_introducing_commits(revision, filename, channel)
                analysis['introducing_commits_analysis'] = introducing_analysis
                
                high_score_commits = introducing_analysis.get('introducing_commits', [])
                if high_score_commits:
                    print(f"       Found {len(high_score_commits)} potential introducing commit(s)")
                    print(f"       Finding EXACT introducing commit by comparing diffs...")
                    
                    exact_analysis = self.find_exact_introducing_commit(revision, filename, high_score_commits, channel)
                    analysis['exact_introducing_commit'] = exact_analysis
                    
                    # Show detailed info about the EXACT introducing commit
                    exact_matches = exact_analysis.get('exact_matches', [])
                    if exact_matches:
                        # EXACT match found
                        best_match = exact_matches[0]
                        introducing_revision = best_match['node']
                        
                        print(f"         EXACT Introducing Commit Found:")
                        print(f"         Revision: {introducing_revision}")
                        print(f"         Pattern Score: {best_match['pattern_match_score']:.2f}")
                        print(f"         Author: {best_match['author']}")
                        print(f"         Date: {best_match['date']}")
                        print(f"         Description: {best_match['desc']}")
                        
                        # Get bug numbers for introducing commit
                        try:
                            repo_info = self.find_revision_in_repos(introducing_revision, channel)
                            if repo_info:
                                repo_channel, repo_path = repo_info
                                success, bug_output = self._run_hg_command(repo_path, ['log', '-r', introducing_revision, '--template', '{desc}'])
                                if success:
                                    bug_numbers = re.findall(r'[Bb]ug (\d+)', bug_output)
                                    print(f"         Bug Numbers: {bug_numbers if bug_numbers else 'None found'}")
                        except Exception as e:
                            print(f"          Could not get bug numbers: {e}")
                            
                    else:
                        # No EXACT match, but we have potential commits - show the best potential one
                        print(f"       No exact introducing commit found with sufficient confidence (>= 0.7)")
                        print(f"       Showing BEST POTENTIAL introducing commit:")
                        
                        # Get the best potential commit from high_score_commits
                        best_potential = max(high_score_commits, key=lambda x: x.get('analysis', {}).get('relevance_score', 0.0))
                        potential_revision = best_potential['node']
                        relevance_score = best_potential.get('analysis', {}).get('relevance_score', 0.0)
                        relevance_reasons = best_potential.get('analysis', {}).get('reasons', [])
                        
                        print(f"         Revision: {potential_revision}")
                        print(f"         Relevance Score: {relevance_score:.2f}")
                        print(f"         Author: {best_potential['author']}")
                        print(f"         Date: {best_potential['date']}")
                        print(f"         Description: {best_potential['desc']}")
                        print(f"         Relevance Reasons: {', '.join(relevance_reasons) if relevance_reasons else 'General code proximity'}")
                        
                        # Get bug numbers for potential introducing commit
                        try:
                            repo_info = self.find_revision_in_repos(potential_revision, channel)
                            if repo_info:
                                repo_channel, repo_path = repo_info
                                success, bug_output = self._run_hg_command(repo_path, ['log', '-r', potential_revision, '--template', '{desc}'])
                                if success:
                                    bug_numbers = re.findall(r'[Bb]ug (\d+)', bug_output)
                                    print(f"         Bug Numbers: {bug_numbers if bug_numbers else 'None found'}")
                        except Exception as e:
                            print(f"          Could not get bug numbers: {e}")
                        
                        # Store the best potential commit in the analysis results for later processing
                        analysis['best_potential_introducing_commit'] = {
                            'revision': potential_revision,
                            'relevance_score': relevance_score,
                            'author': best_potential['author'],
                            'date': best_potential['date'],
                            'description': best_potential['desc'],
                            'reasons': relevance_reasons
                        }
                else:
                    print(f"       No potential introducing commits found")

            
            file_analyses[filename] = analysis
        
        print(f"\n Analysis complete for crash {crash_id}")

        # IMPROVED SUMMARY LOGIC - Count both exact and potential introducing commits
        total_files_with_introducing = 0
        exact_matches = 0
        potential_matches = 0

        for filename, file_analysis in file_analyses.items():
            has_exact = bool(file_analysis.get('exact_introducing_commit', {}).get('exact_matches'))
            has_potential = bool(file_analysis.get('best_potential_introducing_commit'))
            
            if has_exact:
                total_files_with_introducing += 1
                exact_matches += 1
            elif has_potential:
                total_files_with_introducing += 1
                potential_matches += 1

        print(f" Summary: {total_files_with_introducing}/{len(files_to_analyze)} files have identified introducing commits")
        if exact_matches > 0 and potential_matches > 0:
            print(f"    └─ {exact_matches} exact match(es), {potential_matches} potential match(es)")
        elif exact_matches > 0:
            print(f"    └─ {exact_matches} exact match(es)")
        elif potential_matches > 0:
            print(f"    └─ {potential_matches} potential match(es)")

        return {
            'crash_id': crash_id,
            'build_id': build_id,
            'revision': revision,
            'commit_info': commit_info.__dict__,
            'file_changes_by_type': file_changes_dict,
            'file_analyses': file_analyses
        }
    def automated_crash_analysis_for_signature(self, signature: str, 
                                              years_back: int = 1,
                                              sample_strategy: str = "monthly",
                                              dedup_strategy: str = "stack_trace",
                                              max_crashes_to_analyze: int = 10,
                                              update_repos: bool = False) -> Dict[str, Any]:
        """
        Automated analysis pipeline that:
        1. Extracts crashes for a signature using the first script
        2. Analyzes each crash using the second script functionality
        3. Combines results into comprehensive report
        """
        print(" AUTOMATED CRASH ANALYSIS PIPELINE")
        print("=" * 80)
        print(f" Signature: {signature}")
        print(f" Period: {years_back} years back")
        print(f" Strategy: {sample_strategy} sampling, {dedup_strategy} deduplication")
        print(f" Max crashes to analyze: {max_crashes_to_analyze}")
        print("=" * 80)
        
        # Step 1: Extract crashes using first script functionality
        print("\n PHASE 1: EXTRACTING CRASHES")
        crashes = self.extract_crashes_for_signature(
            signature=signature,
            years_back=years_back,
            sample_strategy=sample_strategy,
            dedup_strategy=dedup_strategy
        )
        
        if not crashes:
            return {
                'error': f'No crashes found for signature: {signature}',
                'signature': signature,
                'total_crashes_found': 0
            }
        
        print(f" Successfully extracted {len(crashes)} crashes")
        
        # Limit the number of crashes to analyze to avoid overwhelming processing
        crashes_to_analyze = crashes[:max_crashes_to_analyze]
        if len(crashes) > max_crashes_to_analyze:
            print(f" Limiting analysis to first {max_crashes_to_analyze} crashes")
        
        # Step 2: Analyze each crash
        print(f"\n PHASE 2: ANALYZING {len(crashes_to_analyze)} CRASHES")
        crash_analyses = {}
        successful_analyses = 0
        failed_analyses = 0
        
        for i, crash in enumerate(crashes_to_analyze, 1):
            crash_id = crash.crash_id
            print(f"\n Analyzing crash {i}/{len(crashes_to_analyze)}: {crash_id}")
            print(f"    Date: {crash.date}")
            print(f"   Channel: {crash.product_channel}")
            
            try:
                # Run full analysis on this crash
                analysis_result = self.full_analysis(crash_id, update_repos=update_repos)
                
                if 'error' in analysis_result:
                    print(f"    Analysis failed: {analysis_result['error']}")
                    failed_analyses += 1
                    crash_analyses[crash_id] = {
                        'error': analysis_result['error'],
                        'crash_info': crash.__dict__
                    }
                else:
                    print(f"    Analysis successful")
                    successful_analyses += 1
                    
                    # Add original crash info to the analysis
                    analysis_result['original_crash_info'] = crash.__dict__
                    crash_analyses[crash_id] = analysis_result
                    
            except Exception as e:
                print(f"    Analysis failed with exception: {e}")
                failed_analyses += 1
                crash_analyses[crash_id] = {
                    'error': str(e),
                    'crash_info': crash.__dict__
                }
        
        print(f"\n PHASE 2 SUMMARY:")
        print(f"    Successful analyses: {successful_analyses}")
        print(f"    Failed analyses: {failed_analyses}")
        print(f"    Success rate: {(successful_analyses/len(crashes_to_analyze)*100):.1f}%")
        
        # Step 3: Enhanced function analysis for successful cases
        print(f"\n PHASE 3: ENHANCED FUNCTION ANALYSIS")
        enhanced_analyses = {}
        
        for crash_id, analysis in crash_analyses.items():
            if 'error' not in analysis:
                print(f"\n Enhanced analysis for crash: {crash_id}")
                try:
                    enhanced_analysis = self.enhanced_extract_and_analyze_introducing_commits(analysis)
                    if enhanced_analysis:
                        enhanced_analyses[crash_id] = enhanced_analysis
                        print(f"    Enhanced analysis completed")
                    else:
                        print(f"     No enhanced analysis possible")
                except Exception as e:
                    print(f"    Enhanced analysis failed: {e}")
        
        # Step 4: Generate comprehensive report
        print(f"\n PHASE 4: GENERATING COMPREHENSIVE REPORT")
        
        # Aggregate all function analyses
        all_affected_functions = []
        all_files_analyzed = set()
        all_introducing_commits = set()
        
        for crash_id, enhanced_analysis in enhanced_analyses.items():
            for filename, file_analysis in enhanced_analysis.items():
                all_files_analyzed.add(filename)
                
                if 'introducing_commit_info' in file_analysis:
                    introducing_commit = file_analysis['introducing_commit_info']['short_revision']
                    all_introducing_commits.add(introducing_commit)
                
                func_comparison = file_analysis.get('function_comparison', {})
                vulnerable_functions = func_comparison.get('vulnerable_functions', {})
                
                for func_name, func_data in vulnerable_functions.items():
                    all_affected_functions.append({
                        'crash_id': crash_id,
                        'filename': filename,
                        'function_name': func_name,
                        'introducing_commit': file_analysis.get('introducing_commit_info', {}).get('short_revision'),
                        'lines_introduced': len(func_data['analysis']['lines_that_introduced_vulnerability']),
                        'lines_fixed': len(func_data['analysis']['lines_that_fixed_vulnerability']),
                        'total_lines_affected': func_data['analysis']['total_affected_lines']
                    })
        
        # Create comprehensive report
        comprehensive_report = {
            'signature': signature,
            'analysis_parameters': {
                'years_back': years_back,
                'sample_strategy': sample_strategy,
                'dedup_strategy': dedup_strategy,
                'max_crashes_analyzed': max_crashes_to_analyze
            },
            'extraction_summary': {
                'total_crashes_extracted': len(crashes),
                'crashes_analyzed': len(crashes_to_analyze),
                'successful_analyses': successful_analyses,
                'failed_analyses': failed_analyses,
                'success_rate_percentage': round((successful_analyses/len(crashes_to_analyze)*100), 1)
            },
            'function_analysis_summary': {
                'total_affected_functions': len(all_affected_functions),
                'unique_files_analyzed': len(all_files_analyzed),
                'unique_introducing_commits': len(all_introducing_commits),
                'crashes_with_function_analysis': len(enhanced_analyses)
            },
            'detailed_crash_analyses': crash_analyses,
            'enhanced_function_analyses': enhanced_analyses,
            'aggregated_function_data': all_affected_functions,
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Save comprehensive report
        safe_signature = signature.replace(':', '_').replace('/', '_').replace('\\', '_')
        report_filename = f"automated_analysis_{safe_signature}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        
        try:
            with open(report_filename, 'w') as f:
                json.dump(comprehensive_report, f, indent=2, default=str)
            print(f" Comprehensive report saved to: {report_filename}")
        except Exception as e:
            print(f" Failed to save report: {e}")
        
        # Print final summary
        print(f"\n AUTOMATED ANALYSIS COMPLETE!")
        print(f" FINAL SUMMARY:")
        print(f"    Signature: {signature}")
        print(f"    Crashes extracted: {len(crashes)}")
        print(f"    Crashes analyzed: {len(crashes_to_analyze)}")
        print(f"    Successful analyses: {successful_analyses}")
        print(f"   Enhanced function analyses: {len(enhanced_analyses)}")
        print(f"    Total affected functions: {len(all_affected_functions)}")
        print(f"   Unique files: {len(all_files_analyzed)}")
        print(f"    Unique introducing commits: {len(all_introducing_commits)}")
        print(f"    Report saved: {report_filename}")
        
        if all_affected_functions:
            print(f"\n TOP AFFECTED FUNCTIONS:")
            # Sort by total lines affected
            sorted_functions = sorted(all_affected_functions, 
                                    key=lambda x: x['total_lines_affected'], 
                                    reverse=True)
            
            for i, func in enumerate(sorted_functions[:5], 1):
                print(f"   {i}. {func['function_name']} ({func['total_lines_affected']} lines)")
                print(f"       {func['filename']}")
                print(f"       Commit: {func['introducing_commit']}")
                print(f"       Crash: {func['crash_id']}")
        
        return comprehensive_report


def automated_main():
    """
    Main function for automated crash analysis using extracted crashes
    """
    # Configure paths to your local repositories
    # UPDATE THESE PATHS to match your local repository locations
    repo_paths = {
        'mozilla-central': 'mozilla-central',
        'mozilla-release': 'mozilla-release', 
        'mozilla-esr115': 'mozilla-esr115'
    }
    
    # Example signatures from the first script - choose one or add your own
    EXAMPLE_SIGNATURES = [
        "mozilla::dom::ClientHandle::Control",
        "mozilla::dom::quota::QuotaManager::Shutdown::<T>::operator()",
        "mozilla::dom::ChildProcessChannelListener::OnChannelReady",
        "mozilla::dom::ServiceWorkerRegistrar::GetShutdownPhase",
        "mozilla::dom::workerinternals::RuntimeService::CrashIfHanging",
        "mozilla::dom::RemoteObjectProxyBase::GetOrCreateProxyObject",
        "mozilla::dom::ContentProcess::InfallibleInit"
    ]
    
    # Select signature to analyze
    signature_to_analyze = "OOM | small"  # Change this as needed
    
    print(f" AUTOMATED MOZILLA CRASH ANALYSIS")
    print(f" Analyzing signature: {signature_to_analyze}")
    print("="*80)
    
    try:
        if not CRASH_EXTRACTION_AVAILABLE:
            print(" Crash extraction not available. Make sure paste.py is in the same directory.")
            return
        
        # Initialize the automated analyzer
        analyzer = AutomatedMozillaCrashAnalyzer(repo_paths)
        
        # Run automated analysis
        results = analyzer.automated_crash_analysis_for_signature(
            signature=signature_to_analyze,
            years_back=1,                    # Analyze crashes from last 1 year
            sample_strategy="monthly",       # Sample monthly
            dedup_strategy="stack_trace",    # Deduplicate by stack trace
            max_crashes_to_analyze=10,        # Limit to 5 crashes for demonstration
            update_repos=False               # Set to True to update repos first
        )
        
        if 'error' in results:
            print(f" Automated analysis failed: {results['error']}")
            return
        
        print(f"\n AUTOMATED ANALYSIS COMPLETED SUCCESSFULLY!")
        print(f" Results summary:")
        print(f"    Total crashes extracted: {results['extraction_summary']['total_crashes_extracted']}")
        print(f"    Crashes analyzed: {results['extraction_summary']['crashes_analyzed']}")
        print(f"    Success rate: {results['extraction_summary']['success_rate_percentage']}%")
        print(f"    Function analyses: {results['function_analysis_summary']['crashes_with_function_analysis']}")
        print(f"    Total functions affected: {results['function_analysis_summary']['total_affected_functions']}")
        
        # Show some example results
        if results['aggregated_function_data']:
            print(f"\n EXAMPLE AFFECTED FUNCTIONS:")
            for i, func in enumerate(results['aggregated_function_data'][:3], 1):
                print(f"   {i}. {func['function_name']}")
                print(f"       File: {func['filename']}")
                print(f"       Crash: {func['crash_id']}")
                print(f"       Lines affected: {func['total_lines_affected']}")
        
        print(f"\n Next steps:")
        print(f"    Check the generated JSON report for detailed results")
        print(f"    Look for generated source files: *_introducing_commit_*_with_functions.txt")
        print(f"    Modify signature_to_analyze to analyze different crash patterns")
        print(f"    Adjust analysis parameters (years_back, max_crashes_to_analyze) as needed")
        
    except Exception as e:
        print(f" Automated analysis failed with error: {e}")
        import traceback
        traceback.print_exc()


def test_crash_api(crash_id: str):
    """
    Test function to verify the crash ID and check what data is available
    """
    print(f" Testing crash ID: {crash_id}")
    print("-" * 40)
    
    analyzer = AutomatedMozillaCrashAnalyzer({}, api_only=True)
    
    print(" Step 1: Getting Build ID...")
    build_id = analyzer.get_build_id(crash_id)
    if not build_id:
        print(" Failed to get build ID")
        return False
    print(f" Build ID: {build_id}")
    
    print("\n Step 2: Getting Revision and Channel...")
    revision_info = analyzer.get_revision_and_channel_from_build_id(build_id)
    if not revision_info:
        print(" Failed to get revision")
        return False
    
    revision, channel = revision_info
    print(f" Revision: {revision}")
    print(f" Channel: {channel}")
    
    print(f"\n API tests passed! Ready to run automated analysis.")
    print(f"Make sure you have revision {revision} in one of your local repositories.")
    
    if channel:
        channel_mapping = {
            'nightly': 'mozilla-central',
            'central': 'mozilla-central',
            'release': 'mozilla-release',
            'beta': 'mozilla-release',
            'esr': 'mozilla-esr115',
            'esr115': 'mozilla-esr115'
        }
        
        recommended_repo = None
        for key, repo in channel_mapping.items():
            if key in channel.lower():
                recommended_repo = repo
                break
        
        if recommended_repo:
            print(f" Recommended repository: {recommended_repo}")
            print(f"   Make sure you have '{recommended_repo}' in your repo_paths")
    
    return True


if __name__ == "__main__":
    """
    Entry point for the automated crash analysis tool
    """
    print(" AUTOMATED MOZILLA CRASH ANALYSIS TOOL")
    print("=" * 50)
    
    if not CRASH_EXTRACTION_AVAILABLE:
        print(" ERROR: Crash extraction functionality not available!")
        print("Make sure paste.py (the first script) is in the same directory.")
        print("The file should be renamed to 'paste.py' so it can be imported.")
        exit(1)
    
    print(" All dependencies available. Starting automated analysis...")
    
    # Run the automated analysis directly
    automated_main()
