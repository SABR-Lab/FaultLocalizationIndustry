#!/usr/bin/env python3
"""
STEP 1: CRASH SIGNATURE TO BUG MAPPER (PARALLELIZED)
With concurrent processing to speed up API calls
"""

import requests
import json
import re
import subprocess
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import defaultdict
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import shared utilities
from bugbug_utils import BugBugUtils
    

class SignatureToBugMapper:
    """Maps crash signatures to bug numbers (PARALLELIZED)"""
    
    def __init__(self, local_repos: Dict[str, str] = None, max_workers: int = None):
        """
        Initialize the mapper
        
        Args:
            local_repos: Dictionary mapping repo names to local paths
            max_workers: Number of parallel threads (default: 10)
                        Increase for faster network, decrease if hitting rate limits
        """
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla Crash Bug Mapper 1.0'
        })
        self.local_repos = local_repos or {}
        if max_workers is None:
            max_workers = min(32, (subprocess.cpu_count() or 2) * 2)
        self.max_workers = max_workers
        print(f"  Parallelization: {max_workers} worker threads")
    
    def get_crashes_for_signature(self, signature: str, months_back: int = 6, 
                                  max_fetch: int = 500) -> List[Dict]:
        """
        Get crash IDs for a signature from the past N months
        Limited to max_fetch crashes to avoid excessive API calls
        """
        print(f" Searching for crashes with signature: {signature}")
        print(f" Time period: Last {months_back} months")
        print(f"  Will fetch maximum {max_fetch} crashes")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=months_back * 30)
        
        crashes = []
        page = 1
        
        while len(crashes) < max_fetch:
            print(f"  Fetching page {page}... ({len(crashes)} crashes so far)")
            
            params = {
                'signature': f'={signature}',
                'date': [
                    f'>={start_date.strftime("%Y-%m-%d")}',
                    f'<{end_date.strftime("%Y-%m-%d")}'
                ],
                '_results_number': 100,
                '_results_offset': (page - 1) * 100,
                '_facets': 'signature',
                '_columns': ['uuid', 'date', 'build_id', 'version', 'product']
            }
            
            try:
                response = self.session.get(
                    'https://crash-stats.mozilla.org/api/SuperSearch/',
                    params=params,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()
                
                hits = data.get('hits', [])
                if not hits:
                    break
                
                for hit in hits:
                    if len(crashes) >= max_fetch:
                        break
                    crashes.append({
                        'crash_id': hit.get('uuid'),
                        'date': hit.get('date'),
                        'build_id': hit.get('build_id'),
                        'version': hit.get('version'),
                        'product': hit.get('product')
                    })
                
                total = data.get('total', 0)
                print(f"    Found {len(hits)} crashes (total available: {total})")
                
                if len(crashes) >= max_fetch or len(crashes) >= total:
                    break
                
                page += 1
                time.sleep(0.5)
                
            except Exception as e:
                print(f"   Error fetching crashes: {e}")
                break
        
        print(f" Fetched {len(crashes)} crashes")
        return crashes
    
    def get_build_id(self, crash_id: str) -> Optional[str]:
        """Get build ID from crash data"""
        url = f"https://crash-stats.mozilla.org/api/ProcessedCrash/?crash_id={crash_id}"
        
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            return data.get('build', {}).get('buildId')
        except Exception as e:
            print(f"    Error getting build ID for {crash_id}: {e}")
            return None
    
    def get_revision_from_build_id(self, build_id: str) -> Optional[str]:
        """Get revision from build ID using buildhub"""
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
            response = self.session.post(url, json=query, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                return None
            
            source = hits[0].get("_source", {})
            
            # Try multiple locations for revision
            revision = None
            for path in [
                ("build", "revision"),
                ("source", "revision"),
                ("target", "revision"),
                ("revision",)
            ]:
                try:
                    temp = source
                    for key in path:
                        temp = temp[key]
                    revision = temp
                    break
                except (KeyError, TypeError):
                    continue
            
            return revision
            
        except Exception as e:
            print(f"    Error getting revision from buildhub: {e}")
            return None
    
    def get_bug_numbers_from_revision(self, revision: str) -> List[str]:
        """Get bug numbers from a revision's commit message"""
        
        # Try local repositories first
        for repo_name, repo_path in self.local_repos.items():
            try:
                result = subprocess.run(
                    ['hg', 'log', '-r', revision, '--template', '{desc}'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    description = result.stdout
                    
                    if UTILS_AVAILABLE:
                        bug_numbers = BugBugUtils.extract_bug_ids_from_desc(description)
                    else:
                        bug_numbers = re.findall(r'[Bb]ug\s+(\d+)', description)
                    
                    if bug_numbers:
                        return bug_numbers
            except Exception:
                continue
        
        # Fall back to remote repositories
        repos = [
            'mozilla-central',
            'integration/autoland',
            'releases/mozilla-release',
            'releases/mozilla-beta',
            'releases/mozilla-esr128',
            'releases/mozilla-esr115',
            'releases/mozilla-esr102',
            'releases/mozilla-esr91'
        ]
        
        for repo in repos:
            url = f"https://hg.mozilla.org/{repo}/json-rev/{revision}"
            
            try:
                response = self.session.get(url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    description = data.get('desc', '')
                    
                    if UTILS_AVAILABLE:
                        bug_numbers = BugBugUtils.extract_bug_ids_from_desc(description)
                    else:
                        bug_numbers = re.findall(r'[Bb]ug\s+(\d+)', description)
                    
                    if bug_numbers:
                        return bug_numbers
                        
            except Exception:
                continue
        
        return []
    
    def process_single_crash(self, crash: Dict, index: int, total: int) -> Dict:
        """
        Process a single crash (can be called in parallel)
        
        Args:
            crash: Crash dictionary
            index: Current index
            total: Total crashes
            
        Returns:
            Processed result dictionary
        """
        crash_id = crash['crash_id']
        print(f"  [{index}/{total}] Processing crash: {crash_id}")
        
        result = {
            'crash_id': crash_id,
            'date': crash['date'],
            'build_id': crash.get('build_id'),
            'version': crash.get('version'),
            'product': crash.get('product'),
            'revision': None,
            'bug_numbers': []
        }
        
        # If build_id not in crash data, fetch it
        if not result['build_id']:
            result['build_id'] = self.get_build_id(crash_id)
        
        if not result['build_id']:
            return result
        
        # Get revision from build ID
        revision = self.get_revision_from_build_id(result['build_id'])
        
        if not revision:
            return result
        
        result['revision'] = revision
        
        # Get bug numbers from revision
        bug_numbers = self.get_bug_numbers_from_revision(revision)
        
        if bug_numbers:
            result['bug_numbers'] = bug_numbers
        
        return result
    
    def _build_bugs_index(self, results: List[Dict], signature: str) -> Dict:
        """Build an index organized by bug number"""
        bugs_index = defaultdict(lambda: {
            'signature': signature,
            'crash_ids': [],
            'crash_details': []
        })
        
        for result in results:
            for bug_number in result['bug_numbers']:
                bugs_index[bug_number]['crash_ids'].append(result['crash_id'])
                bugs_index[bug_number]['crash_details'].append({
                    'crash_id': result['crash_id'],
                    'date': result['date'],
                    'build_id': result['build_id'],
                    'version': result['version'],
                    'revision': result['revision']
                })
        
        final_index = {}
        for bug_number, data in bugs_index.items():
            final_index[bug_number] = {
                'bug_number': bug_number,
                'signature': data['signature'],
                'crash_count': len(data['crash_ids']),
                'crash_ids': data['crash_ids'],
                'crash_details': data['crash_details'],
                'bugzilla_url': f"https://bugzilla.mozilla.org/show_bug.cgi?id={bug_number}"
            }
        
        return final_index
    
    def map_signature_to_bugs(self, signature: str, months_back: int = 6, 
                             max_crashes: int = 100, max_fetch: int = 500) -> Dict:
        """
        Main function: Map a signature to bug numbers (PARALLELIZED)
        """
        print("\n" + "="*80)
        print(f" STEP 1: MAPPING SIGNATURE TO BUG NUMBERS (PARALLELIZED)")
        print("="*80)
        
        # Step 1: Get crashes for signature
        crashes = self.get_crashes_for_signature(signature, months_back, max_fetch)
        
        if not crashes:
            return {
                'signature': signature,
                'error': 'No crashes found',
                'total_crashes': 0,
                'results': [],
                'bugs_index': {}
            }
        
        # Limit crashes to process
        crashes_to_process = crashes[:max_crashes]
        if len(crashes) > max_crashes:
            print(f"  Limiting to first {max_crashes} crashes")
        
        print(f"\n Processing {len(crashes_to_process)} crashes in parallel ({self.max_workers} workers)...\n")
        
        results = []
        completed = 0
        
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_index = {
                executor.submit(
                    self.process_single_crash, 
                    crash, 
                    i, 
                    len(crashes_to_process)
                ): i 
                for i, crash in enumerate(crashes_to_process, 1)
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_index):
                result = future.result()
                results.append(result)
                completed += 1
                
                # Print progress every 10 crashes
                if completed % 10 == 0 or completed == len(crashes_to_process):
                    print(f"  Progress: {completed}/{len(crashes_to_process)} crashes processed")
        
        # Filter results to only include those with bug numbers
        results_with_bugs = [r for r in results if r['bug_numbers']]
        
        bugs_found = set()
        for result in results_with_bugs:
            bugs_found.update(result['bug_numbers'])
        
        # Build the bugs index
        print(f"\n Building bugs index...")
        bugs_index = self._build_bugs_index(results_with_bugs, signature)
        
        # Summary
        print("\n" + "="*80)
        print(" STEP 1 SUMMARY")
        print("="*80)
        print(f"Signature: {signature}")
        print(f"Total crashes found: {len(crashes)}")
        print(f"Crashes processed: {len(crashes_to_process)}")
        print(f"Crashes with bug numbers: {len(results_with_bugs)}")
        print(f"Unique bugs found: {len(bugs_found)}")
        
        if bugs_found:
            print(f"\n Bug Numbers Found (with crash counts):")
            for bug in sorted(bugs_found):
                crash_count = bugs_index[bug]['crash_count']
                print(f"  • Bug {bug}: {crash_count} crash(es) - {bugs_index[bug]['bugzilla_url']}")
        
        return {
            'signature': signature,
            'total_crashes': len(crashes),
            'processed_crashes': len(crashes_to_process),
            'crashes_with_bugs': len(results_with_bugs),
            'unique_bugs': list(bugs_found),
            'results': results_with_bugs,
            'bugs_index': bugs_index
        }
    
    def save_results(self, mapping_results: Dict, filename: str = None):
        """Save results to JSON file"""
        if not filename:
            safe_sig = mapping_results['signature'].replace(':', '_').replace('/', '_')[:50]
            filename = f"step1_sig_to_bugs_{safe_sig}.json"
        
        try:
            with open(filename, 'w') as f:
                json.dump(mapping_results, f, indent=2)
            print(f"\n Results saved to: {filename}")
            return filename
        except Exception as e:
            print(f"\n Failed to save results: {e}")
            return None
    
    def print_bugs_report(self, mapping_results: Dict):
        """Print a detailed report organized by bug number"""
        bugs_index = mapping_results.get('bugs_index', {})
        
        if not bugs_index:
            print("\n No bugs found to report")
            return
        
        print("\n" + "="*80)
        print(" BUGS REPORT - ORGANIZED BY BUG NUMBER")
        print("="*80)
        
        for bug_number in sorted(bugs_index.keys()):
            bug_data = bugs_index[bug_number]
            
            print(f"\n{'='*80}")
            print(f" BUG {bug_number}")
            print(f"{'='*80}")
            print(f"Signature: {bug_data['signature']}")
            print(f"Crash Count: {bug_data['crash_count']}")
            print(f"Bugzilla URL: {bug_data['bugzilla_url']}")
            print(f"\nAssociated Crash IDs:")
            
            for i, crash_detail in enumerate(bug_data['crash_details'][:10], 1):
                date = crash_detail['date'][:10] if crash_detail['date'] else 'N/A'
                print(f"  {i}. {crash_detail['crash_id']}")
                print(f"     Date: {date} | Version: {crash_detail['version']} | Build: {crash_detail['build_id']}")
            
            if bug_data['crash_count'] > 10:
                print(f"  ... and {bug_data['crash_count'] - 10} more crashes")


def main():
    """Main execution function"""
    
    local_repos = {
        'autoland': './mozilla-autoland',
        'central': './mozilla-central',
        'release': './mozilla-release',
        'esr115': './mozilla-esr115'
    }
    
    # Initialize with custom max_workers (increase for faster network)
    mapper = SignatureToBugMapper(local_repos=local_repos)
    
    signature = "OOM | small"
    
    # Run mapping (now parallelized!)
    results = mapper.map_signature_to_bugs(
        signature=signature,
        months_back=6,
        max_crashes=6000,
        max_fetch=6000
    )
    
    # Print detailed bugs report
    mapper.print_bugs_report(results)
    
    # Print simple crash table
    print("\n" + "="*80)
    print(" CRASHES TABLE")
    print("="*80)
    print(f"{'Crash ID':<40} {'Date':<20} {'Bug Numbers':<20}")
    print("-"*80)
    
    for result in results['results'][:20]:
        bugs = ', '.join(result['bug_numbers']) if result['bug_numbers'] else 'None'
        date = result['date'][:10] if result['date'] else 'N/A'
        print(f"{result['crash_id']:<40} {date:<20} {bugs:<20}")
    
    if len(results['results']) > 20:
        print(f"... and {len(results['results']) - 20} more crashes")
    
    # Save results
    filename = mapper.save_results(results)
    
    print("\n Step 1 Complete!")
    if filename:
        print(f" Results saved to: {filename}")


if __name__ == "__main__":
    main()
