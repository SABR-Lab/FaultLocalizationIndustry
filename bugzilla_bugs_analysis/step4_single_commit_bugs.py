#!/usr/bin/env python3
"""
================================================================================
STEP 4: SINGLE FIXING COMMIT + REGRESSOR FILE MATCHING
================================================================================

Uses same patterns as bugbug_utils.py but with additional fallbacks:
1. BugBug repository cache
2. Local hg repos (mozilla-central, etc.)
3. Bugzilla API (extracts commit hashes from bug comments)
"""

import json
import sys
import os
import subprocess
import re
import requests
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
os.chdir(parent_dir)

try:
    from bugbug import repository
    from bugbug_utils import get_bugbug_cache, BugBugUtils
    BUGBUG_AVAILABLE = True
except ImportError as e:
    BUGBUG_AVAILABLE = False
    print(f"WARNING: BugBug not available: {e}")


class SingleCommitRegressorMatcher:
    """Filter bugs to single fixing commit and match regressor commits."""
    
    BUGZILLA_API = "https://bugzilla.mozilla.org/rest"
    
    # Same patterns as bugbug_utils.py + additional patterns
    BUG_ID_PATTERNS = [
        r'[Bb]ug\s+(\d+)',      # Bug 12345, bug 12345 (same as bugbug_utils)
        r'b=(\d+)',              # b=12345 (same as bugbug_utils)
        r'[Bb]ug[:\-]?\s*(\d+)', # Bug:12345, Bug-12345, Bug12345
        r'\[Bug\s*(\d+)\]',      # [Bug 12345]
    ]
    
    def __init__(self, max_workers: int = 4, debug: bool = False):
        self.commits_by_bug = defaultdict(list)
        self.max_workers = max_workers
        self.debug = debug
        self.print_lock = threading.Lock()
        self.result_lock = threading.Lock()
        
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        self.input_dir = self.outputs_base / "step3_regressed_by_filter" / "bugs_with_regression" / "bugs"
        self.output_base = self.outputs_base / "step4_single_commit_regressor_match"
        self.output_base.mkdir(parents=True, exist_ok=True)
        
        self.local_repos = {
            'mozilla-central': './mozilla-central',
            'mozilla-autoland': './mozilla-autoland', 
            'mozilla-release': './mozilla-release',
            'mozilla-esr115': './mozilla-esr115'
        }
        
        # Check which repos exist
        self.available_repos = {}
        print(f"Input: {self.input_dir}")
        print(f"Output: {self.output_base}")
        print(f"\nLocal repositories:")
        for name, path in self.local_repos.items():
            if os.path.exists(path):
                self.available_repos[name] = path
                print(f"  ✓ {name}: {path}")
            else:
                print(f"  ✗ {name}: {path}")
        
        print(f"\nDebug: {self.debug}, Workers: {self.max_workers}\n")
        
        if BUGBUG_AVAILABLE:
            print("Initializing BugBug...")
            self.bug_cache = get_bugbug_cache()
            print(f"  BugBug cache: {self.bug_cache.count()} bugs\n")
            print("Building commit index from BugBug repository...")
            self._build_commit_index()
        else:
            self.bug_cache = None
            print("BugBug not available - using local repos + Bugzilla API\n")
    
    def _safe_print(self, msg: str):
        with self.print_lock:
            print(msg)
    
    def _debug_print(self, msg: str):
        if self.debug:
            self._safe_print(f"    [DEBUG] {msg}")
    
    def _extract_bug_ids(self, text: str) -> List[str]:
        """Extract bug IDs using same patterns as bugbug_utils + extras"""
        bug_ids = set()
        for pattern in self.BUG_ID_PATTERNS:
            for match in re.finditer(pattern, text):
                bug_ids.add(match.group(1))
        return list(bug_ids)
    
    def _build_commit_index(self):
        """Build index of commits by bug ID from BugBug repository"""
        commit_count = 0
        try:
            for commit in repository.get_commits():
                commit_count += 1
                if commit_count % 10000 == 0:
                    print(f"  {commit_count} commits...")
                
                desc = commit.get('desc', '')
                # Use same extraction as bugbug_utils
                bug_ids = BugBugUtils.extract_bug_ids_from_desc(desc)
                
                for bug_id in bug_ids:
                    self.commits_by_bug[bug_id].append({
                        'node': commit.get('node', ''),
                        'short_node': commit.get('node', '')[:12],
                        'desc': desc,
                        'author': commit.get('author', ''),
                        'pushdate': commit.get('pushdate', ''),
                        'files': commit.get('files', []),
                        'components': commit.get('components', []),
                        'source': 'bugbug'
                    })
            
            print(f"✓ Indexed {commit_count} commits for {len(self.commits_by_bug)} bugs\n")
        except Exception as e:
            print(f"Error: {e}")
    
    def _search_local_repos(self, bug_id: str) -> List[Dict]:
        """Search local hg repos for commits"""
        commits = []
        
        if not self.available_repos:
            return []
        
        for repo_name, repo_path in self.available_repos.items():
            try:
                # Search for bug ID
                result = subprocess.run(
                    ['hg', 'log', '-k', f'Bug {bug_id}', '--template',
                     '{node}\\n{author}\\n{date|isodate}\\n{desc|firstline}\\n{files}\\n---END---\\n'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    for entry in result.stdout.strip().split('---END---\n'):
                        if not entry.strip():
                            continue
                        lines = entry.strip().split('\n')
                        if len(lines) >= 4:
                            node = lines[0].strip()
                            desc = lines[3].strip()
                            
                            # Verify bug ID is in description
                            if re.search(rf'\b{bug_id}\b', desc):
                                commits.append({
                                    'node': node,
                                    'short_node': node[:12],
                                    'desc': desc,
                                    'author': lines[1].strip(),
                                    'pushdate': lines[2].strip(),
                                    'files': lines[4].strip().split() if len(lines) > 4 else [],
                                    'components': [],
                                    'source': f'local:{repo_name}'
                                })
                                self._debug_print(f"Local repo found: {node[:12]} in {repo_name}")
            except Exception as e:
                self._debug_print(f"Local search error ({repo_name}): {e}")
        
        # Deduplicate
        seen = set()
        return [c for c in commits if not (c['node'] in seen or seen.add(c['node']))]
    
    def _search_bugzilla_comments(self, bug_id: str) -> List[Dict]:
        """Extract commit hashes from Bugzilla bug comments"""
        commits = []
        
        try:
            url = f"{self.BUGZILLA_API}/bug/{bug_id}/comment"
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            comments = data.get('bugs', {}).get(str(bug_id), {}).get('comments', [])
            
            # Pattern to find commit hashes in hg.mozilla.org links
            hg_pattern = r'https://hg\.mozilla\.org/[^/]+(?:/[^/]+)?/rev/([a-f0-9]{12,40})'
            
            found_hashes = set()
            for comment in comments:
                text = comment.get('text', '')
                for match in re.finditer(hg_pattern, text):
                    found_hashes.add(match.group(1))
            
            # Try to get commit info from local repos
            for commit_hash in found_hashes:
                commit_info = self._get_commit_by_hash(commit_hash)
                if commit_info:
                    commit_info['source'] = 'bugzilla'
                    commits.append(commit_info)
                    self._debug_print(f"Bugzilla found: {commit_hash[:12]}")
        
        except Exception as e:
            self._debug_print(f"Bugzilla API error: {e}")
        
        return commits
    
    def _get_commit_by_hash(self, commit_hash: str) -> Optional[Dict]:
        """Get commit details by hash from local repos"""
        for repo_name, repo_path in self.available_repos.items():
            try:
                result = subprocess.run(
                    ['hg', 'log', '-r', commit_hash, '--template',
                     '{node}\\n{author}\\n{date|isodate}\\n{desc|firstline}\\n{files}'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    lines = result.stdout.strip().split('\n')
                    if len(lines) >= 4:
                        return {
                            'node': lines[0].strip(),
                            'short_node': lines[0].strip()[:12],
                            'desc': lines[3].strip(),
                            'author': lines[1].strip(),
                            'pushdate': lines[2].strip(),
                            'files': lines[4].strip().split() if len(lines) > 4 else [],
                            'components': [],
                            'source': f'local:{repo_name}'
                        }
            except:
                continue
        return None
    
    def get_commits_for_bug(self, bug_id: str) -> Tuple[List[Dict], str]:
        """Get commits for a bug from all sources"""
        bug_id = str(bug_id).strip()
        all_commits = []
        sources = []
        
        # 1. BugBug cache
        if BUGBUG_AVAILABLE and bug_id in self.commits_by_bug:
            bugbug_commits = self.commits_by_bug[bug_id]
            all_commits.extend(bugbug_commits)
            if bugbug_commits:
                sources.append('bugbug')
                self._debug_print(f"BugBug: {len(bugbug_commits)} commits for {bug_id}")
        
        # 2. Local repos (always check - BugBug might be outdated)
        local_commits = self._search_local_repos(bug_id)
        existing_nodes = {c['node'] for c in all_commits}
        new_local = [c for c in local_commits if c['node'] not in existing_nodes]
        if new_local:
            all_commits.extend(new_local)
            sources.append('local')
            self._debug_print(f"Local: {len(new_local)} new commits for {bug_id}")
        
        # 3. Bugzilla API (if still nothing found)
        if not all_commits:
            bz_commits = self._search_bugzilla_comments(bug_id)
            if bz_commits:
                all_commits.extend(bz_commits)
                sources.append('bugzilla')
                self._debug_print(f"Bugzilla: {len(bz_commits)} commits for {bug_id}")
        
        # Deduplicate
        seen = set()
        unique = [c for c in all_commits if not (c['node'] in seen or seen.add(c['node']))]
        
        source_str = '+'.join(sources) if sources else 'none'
        return unique, source_str
    
    def get_matching_regressor_commits(self, regressor_commits: List[Dict], 
                                        fixing_files: List[str]) -> List[Dict]:
        """Get regressor commits that modified same files as the fix"""
        if not regressor_commits or not fixing_files:
            return []
        
        fixing_set = set(fixing_files)
        matching = []
        
        for commit in regressor_commits:
            overlap = list(fixing_set & set(commit.get('files', [])))
            if overlap:
                matching.append({
                    'commit_hash': commit['node'],
                    'short_hash': commit['node'][:12],
                    'description': commit.get('desc', ''),
                    'author': commit.get('author', ''),
                    'pushdate': commit.get('pushdate', ''),
                    'files_modified': commit.get('files', []),
                    'file_overlap_count': len(overlap),
                    'overlapping_files': overlap,
                    'components': commit.get('components', [])
                })
        
        matching.sort(key=lambda x: x['file_overlap_count'], reverse=True)
        return matching
    
    def load_bug_files(self, directory: Path) -> Dict[str, Dict]:
        """Load bug JSON files"""
        bugs = {}
        if not directory.exists():
            print(f"  ERROR: {directory} not found")
            return bugs
        
        for f in directory.glob("bug_*.json"):
            try:
                with open(f, 'r') as fp:
                    bug = json.load(fp)
                    if bug.get('bug_id'):
                        bugs[str(bug['bug_id'])] = bug
            except Exception as e:
                print(f"  Warning: {f}: {e}")
        return bugs
    
    def process_single_bug(self, bug_id: str, bug_data: Dict, idx: int, total: int) -> Dict:
        """Process a single bug"""
        fixing_commits, source = self.get_commits_for_bug(bug_id)
        regressed_by = bug_data.get('regressed_by', [])
        
        result = {
            'bug_id': bug_id,
            'bug_data': bug_data,
            'fixing_commit_count': len(fixing_commits),
            'category': 'unknown',
            'commit_source': source,
            'regressed_by': regressed_by
        }
        
        if len(fixing_commits) == 0:
            result['category'] = 'no_commits'
            self._safe_print(f"[{idx}/{total}] Bug {bug_id}: ✗ No commits [{source}]")
            return result
        
        if len(fixing_commits) > 1:
            result['category'] = 'multi_commit'
            self._safe_print(f"[{idx}/{total}] Bug {bug_id}: → Multi ({len(fixing_commits)}) [{source}]")
            return result
        
        # Single commit
        result['category'] = 'single_commit'
        fix = fixing_commits[0]
        fixing_files = fix.get('files', [])
        
        result['fixing_commit'] = {
            'commit_hash': fix['node'],
            'short_hash': fix['short_node'],
            'description': fix['desc'],
            'author': fix['author'],
            'pushdate': fix['pushdate'],
            'files': fixing_files,
            'file_count': len(fixing_files),
            'components': fix.get('components', [])
        }
        
        # Find matching regressor commits
        result['regressor_analysis'] = []
        total_matching = 0
        
        for reg_id in regressed_by:
            reg_commits, reg_src = self.get_commits_for_bug(str(reg_id))
            matching = self.get_matching_regressor_commits(reg_commits, fixing_files)
            total_matching += len(matching)
            
            result['regressor_analysis'].append({
                'regressor_bug_id': str(reg_id),
                'total_commits': len(reg_commits),
                'matching_commits_count': len(matching),
                'matching_commits': matching,
                'commit_source': reg_src
            })
        
        result['total_matching_regressor_commits'] = total_matching
        self._safe_print(
            f"[{idx}/{total}] Bug {bug_id}: ✓ Single ({fix['short_node']}) "
            f"- {len(fixing_files)} files → {total_matching} matches [{source}]"
        )
        return result
    
    def filter_and_match(self) -> Dict:
        """Main processing"""
        print("=" * 70)
        print("STEP 4: SINGLE COMMIT + REGRESSOR MATCHING")
        print("=" * 70 + "\n")
        
        all_bugs = self.load_bug_files(self.input_dir)
        if not all_bugs:
            return {'error': 'No bugs found', 'summary': {}}
        
        print(f"Loaded {len(all_bugs)} bugs\n")
        print("=" * 70)
        print("PROCESSING")
        print("=" * 70 + "\n")
        
        single_matched, single_no_match, multi, no_commits = {}, {}, {}, {}
        commit_dist = defaultdict(int)
        source_stats = defaultdict(int)
        
        total = len(all_bugs)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.process_single_bug, bid, bdata, i+1, total): bid
                for i, (bid, bdata) in enumerate(all_bugs.items())
            }
            
            for future in as_completed(futures):
                bug_id = futures[future]
                try:
                    result = future.result()
                    with self.result_lock:
                        commit_dist[result['fixing_commit_count']] += 1
                        source_stats[result.get('commit_source', 'none')] += 1
                        
                        merged = {**result['bug_data'], **result}
                        del merged['bug_data']
                        
                        if result['category'] == 'single_commit':
                            if result.get('total_matching_regressor_commits', 0) > 0:
                                single_matched[bug_id] = merged
                            else:
                                single_no_match[bug_id] = merged
                        elif result['category'] == 'multi_commit':
                            multi[bug_id] = merged
                        else:
                            no_commits[bug_id] = merged
                except Exception as e:
                    self._safe_print(f"Error {bug_id}: {e}")
                    no_commits[bug_id] = {**all_bugs[bug_id], 'error': str(e)}
        
        results = {
            'filter_timestamp': datetime.now().isoformat(),
            'summary': {
                'total_input_bugs': total,
                'single_commit_with_matching_regressors': len(single_matched),
                'single_commit_no_file_overlap': len(single_no_match),
                'multi_commit_bugs': len(multi),
                'no_commit_bugs': len(no_commits),
                'commit_count_distribution': dict(sorted(commit_dist.items())),
                'commit_sources': dict(source_stats),
            },
            'single_commit_matched': single_matched,
            'single_commit_no_match': single_no_match,
            'multi_commit_bugs': multi,
            'no_commit_bugs': no_commits
        }
        
        self._print_summary(results)
        return results
    
    def _print_summary(self, results: Dict):
        s = results['summary']
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total: {s['total_input_bugs']}")
        print(f"✓ Single + matched: {s['single_commit_with_matching_regressors']}")
        print(f"○ Single, no overlap: {s['single_commit_no_file_overlap']}")
        print(f"→ Multi commit: {s['multi_commit_bugs']}")
        print(f"✗ No commits: {s['no_commit_bugs']}")
        print(f"\nDistribution: {s['commit_count_distribution']}")
        print(f"Sources: {s['commit_sources']}")
        
        if results['no_commit_bugs']:
            print(f"\nNo-commit bugs (check manually):")
            for bid in list(results['no_commit_bugs'].keys())[:5]:
                print(f"  https://bugzilla.mozilla.org/show_bug.cgi?id={bid}")
    
    def save_results(self, results: Dict):
        print("\n" + "=" * 70)
        print("SAVING")
        print("=" * 70 + "\n")
        
        matched_dir = self.output_base / "bugs_with_single_commit_regressor_commit" / "bugs"
        matched_dir.mkdir(parents=True, exist_ok=True)
        
        for f in matched_dir.glob("bug_*.json"):
            f.unlink()
        
        for bug_id, data in results['single_commit_matched'].items():
            with open(matched_dir / f"bug_{bug_id}.json", 'w') as f:
                json.dump(data, f, indent=2)
        
        print(f"✓ Saved {len(results['single_commit_matched'])} bugs to {matched_dir}")
        
        with open(self.output_base / "filter_summary.json", 'w') as f:
            json.dump({
                'timestamp': results['filter_timestamp'],
                'summary': results['summary'],
                'matched_bug_ids': sorted(results['single_commit_matched'].keys()),
                'multi_bug_ids': sorted(results['multi_commit_bugs'].keys()),
                'no_commit_bug_ids': sorted(results['no_commit_bugs'].keys())
            }, f, indent=2)
        print("✓ Saved filter_summary.json")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    
    matcher = SingleCommitRegressorMatcher(max_workers=args.workers, debug=args.debug)
    results = matcher.filter_and_match()
    
    if 'error' not in results:
        matcher.save_results(results)
        print("\n✓ STEP 4 COMPLETE")
        s = results['summary']
        print(f"  Single + matched: {s['single_commit_with_matching_regressors']}")
        print(f"  Multi: {s['multi_commit_bugs']}")
        print(f"  No commits: {s['no_commit_bugs']}")


if __name__ == "__main__":
    main()