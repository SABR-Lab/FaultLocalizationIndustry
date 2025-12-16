#!/usr/bin/env python3
"""
Shared utilities for BugBug analysis
Provides singleton cache and common utility functions to avoid code duplication
"""

from bugbug import bugzilla
from typing import Dict, Optional, List, Iterator
import re


class BugBugCache:
    """
    Singleton cache for BugBug database
    Ensures the database is loaded only once across all scripts
    """
    
    _instance = None
    _bugs = None
    _loaded = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BugBugCache, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not BugBugCache._loaded:
            print("Loading BugBug database (one time across all scripts)...")
            self._load_bugs()
            BugBugCache._loaded = True
    
    def _load_bugs(self):
        """Load BugBug bug database into memory"""
        try:
            bugs = bugzilla.get_bugs()
            
            BugBugCache._bugs = {}
            crash_bugs_count = 0
            
            for bug in bugs:
                bug_id = str(bug['id'])
                BugBugCache._bugs[bug_id] = bug
                
                if bug.get('cf_crash_signature'):
                    crash_bugs_count += 1
            
            print(f"Loaded {len(BugBugCache._bugs)} bugs from BugBug")
            print(f"Found {crash_bugs_count} bugs with crash signatures")
            
        except Exception as e:
            print(f"Error loading BugBug data: {e}")
            BugBugCache._bugs = {}
    
    def get_bug(self, bug_id: str) -> Optional[Dict]:
        """
        Get a single bug by ID
        
        Args:
            bug_id: Bug ID to retrieve
            
        Returns:
            Bug dictionary or None if not found
        """
        if BugBugCache._bugs is None:
            return None
        return BugBugCache._bugs.get(str(bug_id))
    
    def get_bugs_batch(self, bug_ids: List[str]) -> List[Dict]:
        """
        Get multiple bugs efficiently
        
        Args:
            bug_ids: List of bug IDs to retrieve
            
        Returns:
            List of bug dictionaries
        """
        if BugBugCache._bugs is None:
            return []
        
        return [
            BugBugCache._bugs[str(bid)] 
            for bid in bug_ids 
            if str(bid) in BugBugCache._bugs
        ]
    
    def get_bugs_batch_iterator(self, bug_ids: List[str]) -> Iterator[Dict]:
        """
        Get multiple bugs as an iterator (memory efficient)
        
        Args:
            bug_ids: List of bug IDs to retrieve
            
        Yields:
            Bug dictionaries
        """
        if BugBugCache._bugs is None:
            return
        
        bug_id_set = set(str(bid) for bid in bug_ids)
        for bug_id, bug in BugBugCache._bugs.items():
            if bug_id in bug_id_set:
                yield bug
    
    def all_bugs(self) -> Dict[str, Dict]:
        """
        Get all bugs
        
        Returns:
            Dictionary of all bugs {bug_id: bug_data}
        """
        return BugBugCache._bugs or {}
    
    def count(self) -> int:
        """Get total number of bugs"""
        return len(BugBugCache._bugs) if BugBugCache._bugs else 0


class BugBugUtils:
    """Shared utility functions for BugBug analysis"""
    
    @staticmethod
    def extract_uplift_information(bug: Dict) -> List[Dict]:
        """
        Extract channel deployment information from bug comments
        
        Args:
            bug: Bug dictionary with comments
            
        Returns:
            List of uplift information dictionaries
        """
        uplift_data = []
        repo_pattern = r'https://hg\.mozilla\.org/([^/]+/[^/]+)/rev/([a-f0-9]+)'
        
        for comment in bug.get('comments', []):
            text = comment.get('text', '')
            author = comment.get('author', 'Unknown')
            
            matches = re.findall(repo_pattern, text)
            for repo_path, commit_hash in matches:
                # Determine channel from repository path
                if 'mozilla-central' in repo_path:
                    channel = 'mozilla-central'
                elif 'releases/mozilla-release' in repo_path:
                    channel = 'release'
                elif 'releases/mozilla-esr' in repo_path:
                    if 'esr128' in repo_path:
                        channel = 'esr128'
                    elif 'esr115' in repo_path:
                        channel = 'esr115'
                    else:
                        channel = 'esr'
                elif 'autoland' in repo_path:
                    channel = 'autoland'
                else:
                    channel = repo_path
                
                uplift_data.append({
                    'channel': channel,
                    'repository': repo_path,
                    'commit_hash': commit_hash,
                    'full_url': f"https://hg.mozilla.org/{repo_path}/rev/{commit_hash}",
                    'comment_author': author
                })
        
        return uplift_data
    
    @staticmethod
    def extract_bug_ids_from_desc(description: str) -> List[str]:
        """
        Extract bug IDs from commit description
        
        Args:
            description: Commit description text
            
        Returns:
            List of bug IDs found in description
        """
        patterns = [
            r'[Bb]ug\s+(\d+)',
            r'b=(\d+)',  # Shorthand notation
        ]
        
        bug_ids = []
        for pattern in patterns:
            matches = re.findall(pattern, description)
            bug_ids.extend(matches)
        
        return list(set(bug_ids))  # Remove duplicates
    
    @staticmethod
    def format_bug_summary(bug: Dict) -> Dict:
        """
        Format bug data consistently across all scripts
        
        Args:
            bug: Bug dictionary from BugBug
            
        Returns:
            Formatted bug summary dictionary
        """
        return {
            'bug_id': str(bug['id']),
            'summary': bug.get('summary', ''),
            'status': bug.get('status', ''),
            'resolution': bug.get('resolution', ''),
            'product': bug.get('product', ''),
            'component': bug.get('component', ''),
            'severity': bug.get('bug_severity', ''),
            'priority': bug.get('priority', ''),
            'creation_time': bug.get('creation_time', ''),
            'last_change_time': bug.get('last_change_time', ''),
            'assigned_to': bug.get('assigned_to', ''),
            'crash_signature': bug.get('cf_crash_signature', ''),
            'keywords': bug.get('keywords', []),
            'whiteboard': bug.get('whiteboard', ''),
            'target_milestone': bug.get('target_milestone', ''),
            'version': bug.get('version', ''),
            'regressed_by': bug.get('regressed_by', [])
        }
    
    @staticmethod
    def get_channel_priority() -> Dict[str, int]:
        """
        Get channel priority mapping for determining canonical commits
        
        Returns:
            Dictionary mapping channel names to priority (lower = earlier)
        """
        return {
            'autoland': 1,
            'mozilla-central': 2,
            'releases/mozilla-beta': 3,
            'release': 4,
            'esr128': 5,
            'esr115': 6,
            'esr': 7
        }
    
    @staticmethod
    def get_earliest_uplift(uplifts: List[Dict]) -> Optional[Dict]:
        """
        Get the earliest (most canonical) uplift from a list
        
        Args:
            uplifts: List of uplift dictionaries
            
        Returns:
            The earliest uplift based on channel priority
        """
        if not uplifts:
            return None
        
        CHANNEL_PRIORITY = BugBugUtils.get_channel_priority()
        
        def get_priority(uplift):
            channel = uplift['channel']
            for key in CHANNEL_PRIORITY:
                if key in channel:
                    return CHANNEL_PRIORITY[key]
            return 99  # Unknown channel
        
        return min(uplifts, key=get_priority)


# Convenience function to get the singleton instance
def get_bugbug_cache() -> BugBugCache:
    """
    Get the BugBug cache singleton instance
    
    Returns:
        BugBugCache instance (creates if doesn't exist)
    """
    return BugBugCache()