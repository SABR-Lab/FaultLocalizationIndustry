#!/usr/bin/env python3
"""
Repository Analysis Module for Mozilla Crash Analysis Tool

This module handles all repository operations including commit analysis, 
diff parsing, and function extraction.
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

from data_models import CommitInfo, FileChange, FunctionAnalysis

# Tree-sitter integration
try:
    from c_parser import CParser
    TREE_SITTER_AVAILABLE = True
    print("✓ Tree-sitter C parser imported successfully")
except ImportError as e:
    print(f"⚠ Warning: Tree-sitter C parser not available: {e}")
    TREE_SITTER_AVAILABLE = False


class RepositoryAnalyzer:
    """
    Handles all repository operations and analysis
    """
    
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
            print(" API-only mode: Skipping repository validation")
        
        self.session = session or requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla Crash Analysis Tool 3.0'
        })
    
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
                print(f"✓ Found Build ID: {build_id}")
                return build_id
            else:
                print("⚠ No build ID found in crash data")
                return None
                
        except requests.RequestException as e:
            print(f"✗ Error fetching crash data: {e}")
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
                print(f"⚠ No revision found for build ID: {build_id}")
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
                print(f"✗ Could not find revision in any expected location")
                return None
            
            print(f"✓ Found revision: {revision}")
            print(f"✓ Found channel: {channel_info}")
            
            return revision, channel_info
            
        except requests.RequestException as e:
            print(f"✗ Error fetching revision from buildhub: {e}")
            return None
        except Exception as e:
            print(f"✗ Unexpected error parsing buildhub response: {e}")
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
            
            print(f"  File filtering summary for {revision[:12]}:")
            print(f"   Total files changed: {total_files}")
            print(f"   Code files: {code_files}")
            print(f"   Filtered out: {len(file_changes['filtered_out'])}")
            
            # Show some examples of filtered files
            if len(file_changes['filtered_out']) <= 5:
                for filtered_file in file_changes['filtered_out']:
                    print(f"      {filtered_file}")
            else:
                for filtered_file in file_changes['filtered_out'][:3]:
                    print(f"      {filtered_file}")
                print(f"       and {len(file_changes['filtered_out']) - 3} more non-code files")
        
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
                return None
            
            return CommitInfo(
                revision=revision,
                author=author,
                date=date,
                description=description,
                files_changed=[],
                bug_numbers=bug_numbers,
                channel=channel
            )
            
        except Exception as e:
            print(f"✗ Error processing commit {revision[:12]}: {e}")
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
            print(f"✗ Error getting annotations for {filename}: {output}")
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
            print(f"✓ Successfully extracted {filename} at revision {revision} with {len(lines)} lines")
            return numbered_content
        else:
            print(f"✗ Error getting file content for {filename} at revision {revision}: {content}")
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
            
            print(f"✓ File content with line numbers saved to: {output_path}")
            return True
        except Exception as e:
            print(f"✗ Error saving file: {e}")
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
            print(f"✗ Error getting file content for {filename} at revision {revision}: {content}")
            return None

    def parse_functions_from_content(self, content: str, filename: str) -> List[Dict[str, Any]]:
        """
        Parse functions from file content using tree-sitter
        """
        if not TREE_SITTER_AVAILABLE:
            print("⚠ Tree-sitter parser not available. Skipping function parsing.")
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
            print(f"✗ Error parsing functions from {filename}: {e}")
            return []

    def update_repositories(self):
        """
        Update all local repositories to get latest changes
        """
        print("🔄 Updating local repositories...")
        for channel, repo_path in self.repo_paths.items():
            print(f"   Updating {channel}...")
            success, output = self._run_hg_command(repo_path, ['pull', '-u'])
            if success:
                print(f"     ✓ {channel} updated successfully")
            else:
                print(f"     ✗ Failed to update {channel}: {output}")