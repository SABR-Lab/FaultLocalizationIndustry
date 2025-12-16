#!/usr/bin/env python3
"""
================================================================================
STEP 4: REGRESSED-BY FILTER (with Bugzilla API Lookup)
================================================================================

PURPOSE:
--------
For each bug from Step 1 and Step 2, query Bugzilla API to find if it has
a regressed_by field, and extract the regressor bug IDs.

INPUT:
------
- Step 1: outputs/step1_bugzilla_bugs_extraction/older_stack_only/bugs/*.json
- Step 2: outputs/step2_socorro_extraction/full_stack_socorro/bugs/*.json

OUTPUT:
-------
outputs/step3_regressed_by_filter/
├── bugs_with_regression/
│   └── bugs/bug_<ID>.json
├── filter_summary.json
└── statistics_report.txt
"""

import json
import sys
import os
import time
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from collections import defaultdict

# Set up paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))
print(f"Added to Python path: {parent_dir}")

os.chdir(parent_dir)
print(f"Changed working directory to: {parent_dir}")


class BugzillaClient:
    """Client for querying Bugzilla REST API"""
    
    BASE_URL = "https://bugzilla.mozilla.org/rest"
    
    def __init__(self, rate_limit_delay: float = 0.5):
        """
        Initialize Bugzilla client.
        
        Args:
            rate_limit_delay: Seconds to wait between API calls
        """
        self.rate_limit_delay = rate_limit_delay
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'Mozilla-Crash-Analysis-Research/1.0'
        })
    
    def get_bug_regressed_by(self, bug_id: str) -> Tuple[Optional[List[str]], Optional[Dict]]:
        """
        Query Bugzilla API to get regressed_by field for a bug.
        
        Args:
            bug_id: Bug ID to query
            
        Returns:
            Tuple of (regressed_by list, full bug data) or (None, None) on error
        """
        url = f"{self.BASE_URL}/bug/{bug_id}"
        params = {
            'include_fields': 'id,regressed_by,summary,status,resolution,product,component'
        }
        
        try:
            time.sleep(self.rate_limit_delay)  # Rate limiting
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if 'bugs' in data and len(data['bugs']) > 0:
                bug_data = data['bugs'][0]
                regressed_by = bug_data.get('regressed_by', [])
                
                # Normalize to list of strings
                if isinstance(regressed_by, list):
                    regressed_by = [str(r) for r in regressed_by if r]
                elif regressed_by:
                    regressed_by = [str(regressed_by)]
                else:
                    regressed_by = []
                
                return regressed_by, bug_data
            
            return [], None
            
        except requests.exceptions.RequestException as e:
            print(f"    API Error for bug {bug_id}: {e}")
            return None, None
        except (json.JSONDecodeError, KeyError) as e:
            print(f"    Parse Error for bug {bug_id}: {e}")
            return None, None
    
    def get_bugs_regressed_by_batch(self, bug_ids: List[str], batch_size: int = 50) -> Dict[str, List[str]]:
        """
        Query Bugzilla API for multiple bugs at once (more efficient).
        
        Args:
            bug_ids: List of bug IDs to query
            batch_size: Number of bugs per API call
            
        Returns:
            Dict mapping bug_id to regressed_by list
        """
        results = {}
        
        for i in range(0, len(bug_ids), batch_size):
            batch = bug_ids[i:i + batch_size]
            
            url = f"{self.BASE_URL}/bug"
            params = {
                'id': ','.join(batch),
                'include_fields': 'id,regressed_by,summary,status,resolution,product,component'
            }
            
            try:
                time.sleep(self.rate_limit_delay)
                response = self.session.get(url, params=params, timeout=60)
                response.raise_for_status()
                
                data = response.json()
                
                if 'bugs' in data:
                    for bug in data['bugs']:
                        bug_id = str(bug.get('id', ''))
                        regressed_by = bug.get('regressed_by', [])
                        
                        # Normalize
                        if isinstance(regressed_by, list):
                            regressed_by = [str(r) for r in regressed_by if r]
                        elif regressed_by:
                            regressed_by = [str(regressed_by)]
                        else:
                            regressed_by = []
                        
                        results[bug_id] = {
                            'regressed_by': regressed_by,
                            'bugzilla_data': bug
                        }
                
            except requests.exceptions.RequestException as e:
                print(f"    Batch API Error: {e}")
                # Fall back to individual queries for this batch
                for bug_id in batch:
                    if bug_id not in results:
                        regressed_by, bug_data = self.get_bug_regressed_by(bug_id)
                        if regressed_by is not None:
                            results[bug_id] = {
                                'regressed_by': regressed_by,
                                'bugzilla_data': bug_data
                            }
        
        return results


class RegressedByFilter:
    """Filter bugs by querying Bugzilla for regressed_by information"""
    
    def __init__(self, use_batch_api: bool = True, rate_limit: float = 0.3):
        """
        Initialize the filter.
        
        Args:
            use_batch_api: Whether to use batch API calls (faster)
            rate_limit: Seconds between API calls
        """
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # Input directories
        self.step1_older_dir = self.outputs_base / "step1_bugzilla_bugs_extraction" / "older_stack_only" / "bugs"
        self.step2_socorro_dir = self.outputs_base / "step2_socorro_extraction" / "full_stack_socorro" / "bugs"
        
        # Output directory
        self.output_base = self.outputs_base / "step3_regressed_by_filter"
        self.output_base.mkdir(parents=True, exist_ok=True)
        
        # Bugzilla client
        self.bugzilla = BugzillaClient(rate_limit_delay=rate_limit)
        self.use_batch_api = use_batch_api
        
        print(f"Input directories:")
        print(f"  Step 1 (older bugs): {self.step1_older_dir}")
        print(f"  Step 2 (Socorro bugs): {self.step2_socorro_dir}")
        print(f"Output directory: {self.output_base}")
        print(f"Using batch API: {use_batch_api}\n")
    
    def load_bug_files(self, directory: Path) -> Dict[str, Dict]:
        """Load all bug JSON files from a directory"""
        bugs = {}
        
        if not directory.exists():
            print(f"  WARNING: Directory not found: {directory}")
            return bugs
        
        for filepath in directory.glob("bug_*.json"):
            try:
                with open(filepath, 'r') as f:
                    bug_data = json.load(f)
                    bug_id = str(bug_data.get('bug_id', ''))
                    if bug_id:
                        bugs[bug_id] = bug_data
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  Warning: Failed to load {filepath}: {e}")
        
        return bugs
    
    def load_inputs(self) -> Tuple[Dict, Dict]:
        """Load bugs from Step 1 and Step 2"""
        print("=" * 80)
        print("LOADING INPUT DATA")
        print("=" * 80 + "\n")
        
        # Load Step 1
        print("Loading Step 1 (older bugs with Bugzilla stack traces)...")
        step1_bugs = self.load_bug_files(self.step1_older_dir)
        print(f"  Loaded {len(step1_bugs)} bugs from Step 1\n")
        
        # Load Step 2
        print("Loading Step 2 (recent bugs with Socorro stack traces)...")
        step2_bugs = self.load_bug_files(self.step2_socorro_dir)
        print(f"  Loaded {len(step2_bugs)} bugs from Step 2\n")
        
        # Track sources
        step1_only = set(step1_bugs.keys()) - set(step2_bugs.keys())
        step2_only = set(step2_bugs.keys()) - set(step1_bugs.keys())
        overlap = set(step1_bugs.keys()) & set(step2_bugs.keys())
        
        # Merge (Step 2 takes precedence)
        all_bugs = {**step1_bugs, **step2_bugs}
        
        # Mark sources
        bug_sources = {}
        for bug_id in all_bugs.keys():
            if bug_id in step1_only:
                bug_sources[bug_id] = 'step1'
            elif bug_id in step2_only:
                bug_sources[bug_id] = 'step2'
            else:
                bug_sources[bug_id] = 'both'
        
        source_stats = {
            'step1_total': len(step1_bugs),
            'step2_total': len(step2_bugs),
            'step1_only': len(step1_only),
            'step2_only': len(step2_only),
            'overlap': len(overlap),
            'total_unique': len(all_bugs)
        }
        
        print(f"Total unique bugs: {len(all_bugs)}")
        print(f"  From Step 1 only: {len(step1_only)}")
        print(f"  From Step 2 only: {len(step2_only)}")
        print(f"  In both (using Step 2 data): {len(overlap)}\n")
        
        self.bug_sources = bug_sources
        
        return all_bugs, source_stats
    
    def query_bugzilla_for_regressors(self, bug_ids: List[str]) -> Dict[str, Dict]:
        """
        Query Bugzilla API for regressed_by info for all bugs.
        
        Args:
            bug_ids: List of bug IDs to query
            
        Returns:
            Dict mapping bug_id to regressor info
        """
        print("=" * 80)
        print("QUERYING BUGZILLA API FOR REGRESSED_BY INFORMATION")
        print("=" * 80 + "\n")
        
        total = len(bug_ids)
        print(f"Querying {total} bugs from Bugzilla...\n")
        
        if self.use_batch_api:
            # Use batch API for efficiency
            print("Using batch API (50 bugs per request)...\n")
            
            results = {}
            batch_size = 50
            
            for i in range(0, total, batch_size):
                batch = bug_ids[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (total + batch_size - 1) // batch_size
                
                print(f"  Batch {batch_num}/{total_batches}: bugs {i+1}-{min(i+batch_size, total)}...")
                
                batch_results = self.bugzilla.get_bugs_regressed_by_batch(batch, batch_size=batch_size)
                results.update(batch_results)
                
                # Progress
                with_regression = sum(1 for r in batch_results.values() if r.get('regressed_by'))
                print(f"    → Found {with_regression}/{len(batch_results)} with regressed_by")
            
            return results
        else:
            # Query individually (slower but more reliable)
            print("Querying bugs individually...\n")
            
            results = {}
            
            for i, bug_id in enumerate(bug_ids, 1):
                print(f"  [{i}/{total}] Bug {bug_id}...", end=" ")
                
                regressed_by, bug_data = self.bugzilla.get_bug_regressed_by(bug_id)
                
                if regressed_by is not None:
                    results[bug_id] = {
                        'regressed_by': regressed_by,
                        'bugzilla_data': bug_data
                    }
                    
                    if regressed_by:
                        print(f"✓ regressed_by: {regressed_by}")
                    else:
                        print("✗ no regressed_by")
                else:
                    print("⚠ API error")
            
            return results
    
    def filter_bugs(self) -> Dict:
        """
        Main filtering logic: load bugs, query Bugzilla, filter by regressed_by.
        
        Returns:
            Filter results dictionary
        """
        print("=" * 80)
        print("STEP 4: REGRESSED_BY FILTER (with Bugzilla API Lookup)")
        print("=" * 80 + "\n")
        
        # Load input bugs
        all_bugs, source_stats = self.load_inputs()
        
        # Query Bugzilla for regressed_by info
        bug_ids = list(all_bugs.keys())
        bugzilla_results = self.query_bugzilla_for_regressors(bug_ids)
        
        # Process results
        print("\n" + "=" * 80)
        print("PROCESSING RESULTS")
        print("=" * 80 + "\n")
        
        bugs_with_regression = {}
        bugs_without_regression = {}
        api_errors = []
        regressor_count_distribution = defaultdict(int)
        all_regressor_bugs = set()
        
        # Track by source
        source_breakdown = {
            'step1': {'with': 0, 'without': 0, 'error': 0},
            'step2': {'with': 0, 'without': 0, 'error': 0},
            'both': {'with': 0, 'without': 0, 'error': 0}
        }
        
        for bug_id, original_data in all_bugs.items():
            bug_source = self.bug_sources.get(bug_id, 'unknown')
            
            if bug_id not in bugzilla_results:
                # API error
                api_errors.append(bug_id)
                if bug_source in source_breakdown:
                    source_breakdown[bug_source]['error'] += 1
                print(f"  Bug {bug_id} [{bug_source}]: ⚠ API error - skipped")
                continue
            
            bz_result = bugzilla_results[bug_id]
            regressed_by = bz_result.get('regressed_by', [])
            bz_data = bz_result.get('bugzilla_data', {})
            
            if regressed_by:
                # Has regressors
                regressor_count = len(regressed_by)
                regressor_count_distribution[regressor_count] += 1
                all_regressor_bugs.update(regressed_by)
                
                if bug_source in source_breakdown:
                    source_breakdown[bug_source]['with'] += 1
                
                # Merge original data with Bugzilla data
                enriched_bug = {
                    **original_data,
                    'regressed_by': regressed_by,
                    'regressor_count': regressor_count,
                    'source': bug_source,
                    'bugzilla_status': bz_data.get('status'),
                    'bugzilla_resolution': bz_data.get('resolution'),
                    'bugzilla_lookup_timestamp': datetime.now().isoformat()
                }
                bugs_with_regression[bug_id] = enriched_bug
                
                print(f"  Bug {bug_id} [{bug_source}]: ✓ regressed_by {regressed_by}")
            else:
                # No regressors
                if bug_source in source_breakdown:
                    source_breakdown[bug_source]['without'] += 1
                
                bugs_without_regression[bug_id] = original_data
                print(f"  Bug {bug_id} [{bug_source}]: ✗ no regressed_by")
        
        # Build results
        total_processed = len(bugs_with_regression) + len(bugs_without_regression)
        
        results = {
            'filter_timestamp': datetime.now().isoformat(),
            'input_sources': {
                'step1_older_bugs': str(self.step1_older_dir),
                'step2_socorro_bugs': str(self.step2_socorro_dir)
            },
            'summary': {
                'total_input_bugs': len(all_bugs),
                'total_processed': total_processed,
                'api_errors': len(api_errors),
                'bugs_with_regression': len(bugs_with_regression),
                'bugs_without_regression': len(bugs_without_regression),
                'regression_rate_percent': round(len(bugs_with_regression) / total_processed * 100, 1) if total_processed > 0 else 0,
                'unique_regressor_bugs': len(all_regressor_bugs),
                'regressor_count_distribution': dict(sorted(regressor_count_distribution.items())),
                'source_breakdown': {
                    'step1': source_breakdown['step1'],
                    'step2': source_breakdown['step2'],
                    'both_sources': source_breakdown['both']
                },
                'source_stats': source_stats
            },
            'bugs_with_regression': bugs_with_regression,
            'bugs_without_regression_ids': list(bugs_without_regression.keys()),
            'api_error_bug_ids': api_errors,
            'all_regressor_bug_ids': sorted(list(all_regressor_bugs))
        }
        
        self._print_summary(results)
        
        return results
    
    def _print_summary(self, results: Dict):
        """Print filter summary"""
        summary = results['summary']
        
        print("\n" + "=" * 80)
        print("FILTER SUMMARY")
        print("=" * 80)
        
        print(f"\nTotal input bugs: {summary['total_input_bugs']}")
        print(f"Successfully queried: {summary['total_processed']}")
        print(f"API errors: {summary['api_errors']}")
        
        print(f"\nBugs WITH regressed_by: {summary['bugs_with_regression']} ({summary['regression_rate_percent']}%)")
        print(f"Bugs WITHOUT regressed_by: {summary['bugs_without_regression']}")
        print(f"Unique regressor bugs referenced: {summary['unique_regressor_bugs']}")
        
        print("\n" + "-" * 40)
        print("SOURCE BREAKDOWN")
        print("-" * 40)
        
        sb = summary['source_breakdown']
        for source_name, counts in sb.items():
            total = counts['with'] + counts['without'] + counts['error']
            if total > 0:
                pct = round(counts['with'] / (counts['with'] + counts['without']) * 100, 1) if (counts['with'] + counts['without']) > 0 else 0
                print(f"\n{source_name}:")
                print(f"  WITH regressed_by: {counts['with']} ({pct}%)")
                print(f"  WITHOUT regressed_by: {counts['without']}")
                print(f"  API errors: {counts['error']}")
        
        print("\n" + "-" * 40)
        print("REGRESSOR COUNT DISTRIBUTION")
        print("-" * 40 + "\n")
        
        for count, num_bugs in sorted(summary['regressor_count_distribution'].items()):
            bar = '█' * min(num_bugs, 50)
            print(f"  {count} regressor(s): {num_bugs:4d} bugs {bar}")
        
        # Sample bugs
        bugs_with_reg = results['bugs_with_regression']
        if bugs_with_reg:
            print("\n" + "-" * 40)
            print("SAMPLE BUGS WITH REGRESSED_BY")
            print("-" * 40 + "\n")
            
            for bug_id in list(bugs_with_reg.keys())[:10]:
                bug = bugs_with_reg[bug_id]
                regressor_list = bug.get('regressed_by', [])
                bug_source = bug.get('source', 'unknown')
                summary_text = bug.get('summary', 'N/A')[:60]
                print(f"  Bug {bug_id} [{bug_source}]: regressed_by {regressor_list}")
                print(f"    {summary_text}...")
    
    def save_results(self, results: Dict):
        """Save results to output directory"""
        print("\n" + "=" * 80)
        print("SAVING RESULTS")
        print("=" * 80 + "\n")
        
        # Save individual bug files
        bugs_dir = self.output_base / "bugs_with_regression" / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)
        
        for bug_id, bug_data in results['bugs_with_regression'].items():
            bug_path = bugs_dir / f"bug_{bug_id}.json"
            with open(bug_path, 'w') as f:
                json.dump(bug_data, f, indent=2)
        
        print(f"✓ Saved {len(results['bugs_with_regression'])} bugs with regressed_by")
        print(f"  Location: {bugs_dir}")
        
        # Save filter summary
        summary_path = self.output_base / "filter_summary.json"
        
        summary_data = {
            'filter_timestamp': results['filter_timestamp'],
            'input_sources': results['input_sources'],
            'summary': results['summary'],
            'bugs_with_regression': {
                'count': len(results['bugs_with_regression']),
                'bug_ids': sorted(list(results['bugs_with_regression'].keys()))
            },
            'bugs_without_regression': {
                'count': len(results['bugs_without_regression_ids']),
                'bug_ids': sorted(results['bugs_without_regression_ids'])
            },
            'api_errors': {
                'count': len(results['api_error_bug_ids']),
                'bug_ids': results['api_error_bug_ids']
            },
            'all_regressor_bug_ids': results['all_regressor_bug_ids']
        }
        
        with open(summary_path, 'w') as f:
            json.dump(summary_data, f, indent=2)
        print(f"\n✓ Saved filter summary to {summary_path}")
        
        # Save statistics report
        stats_path = self.output_base / "statistics_report.txt"
        self._save_statistics_report(results, stats_path)
        print(f"✓ Saved statistics report to {stats_path}")
    
    def _save_statistics_report(self, results: Dict, output_path: Path):
        """Save human-readable statistics report"""
        with open(output_path, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("REGRESSED_BY FILTER STATISTICS REPORT\n")
            f.write("(with Bugzilla API Lookup)\n")
            f.write("=" * 80 + "\n\n")
            
            summary = results['summary']
            
            f.write(f"Generated: {results['filter_timestamp']}\n\n")
            
            f.write("INPUT:\n")
            f.write(f"  Total bugs from Step 1 & 2: {summary['total_input_bugs']}\n")
            f.write(f"  Successfully queried via API: {summary['total_processed']}\n")
            f.write(f"  API errors: {summary['api_errors']}\n\n")
            
            f.write("RESULTS:\n")
            f.write(f"  ✓ Bugs WITH regressed_by: {summary['bugs_with_regression']} ({summary['regression_rate_percent']}%)\n")
            f.write(f"  ✗ Bugs WITHOUT regressed_by: {summary['bugs_without_regression']}\n")
            f.write(f"  Unique regressor bugs: {summary['unique_regressor_bugs']}\n\n")
            
            f.write("=" * 80 + "\n")
            f.write("SOURCE BREAKDOWN\n")
            f.write("=" * 80 + "\n\n")
            
            sb = summary['source_breakdown']
            for source_name, counts in sb.items():
                total = counts['with'] + counts['without']
                if total > 0:
                    pct = round(counts['with'] / total * 100, 1)
                    f.write(f"{source_name}:\n")
                    f.write(f"  WITH regressed_by: {counts['with']} ({pct}%)\n")
                    f.write(f"  WITHOUT regressed_by: {counts['without']}\n")
                    f.write(f"  API errors: {counts['error']}\n\n")
            
            f.write("=" * 80 + "\n")
            f.write("REGRESSOR COUNT DISTRIBUTION\n")
            f.write("=" * 80 + "\n\n")
            
            for count in sorted(summary['regressor_count_distribution'].keys()):
                num_bugs = summary['regressor_count_distribution'][count]
                f.write(f"  {count} regressor(s): {num_bugs} bugs\n")
            
            f.write("\n" + "=" * 80 + "\n")
            f.write("BUGS WITH REGRESSED_BY (first 30)\n")
            f.write("=" * 80 + "\n\n")
            
            for bug_id in list(results['bugs_with_regression'].keys())[:30]:
                bug = results['bugs_with_regression'][bug_id]
                f.write(f"Bug {bug_id} [{bug.get('source', '?')}]:\n")
                f.write(f"  Summary: {bug.get('summary', 'N/A')[:70]}\n")
                f.write(f"  Regressed by: {bug.get('regressed_by', [])}\n")
                f.write(f"  Status: {bug.get('bugzilla_status', 'N/A')}\n\n")
            
            if len(results['bugs_with_regression']) > 30:
                f.write(f"... and {len(results['bugs_with_regression']) - 30} more\n")
            
            f.write("\n" + "=" * 80 + "\n")
            f.write("ALL REGRESSOR BUG IDs\n")
            f.write("=" * 80 + "\n\n")
            
            regressor_ids = results['all_regressor_bug_ids']
            f.write(f"Total: {len(regressor_ids)}\n\n")
            
            for i in range(0, len(regressor_ids), 10):
                chunk = regressor_ids[i:i+10]
                f.write(f"  {', '.join(chunk)}\n")


def main():
    """Main execution"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Filter bugs by regressed_by via Bugzilla API')
    parser.add_argument('--no-batch', action='store_true', 
                        help='Query bugs individually instead of in batches')
    parser.add_argument('--rate-limit', type=float, default=0.3,
                        help='Seconds between API calls (default: 0.3)')
    args = parser.parse_args()
    
    filterer = RegressedByFilter(
        use_batch_api=not args.no_batch,
        rate_limit=args.rate_limit
    )
    
    results = filterer.filter_bugs()
    
    filterer.save_results(results)
    
    print("\n" + "=" * 80)
    print("✓ STEP 4 COMPLETE")
    print("=" * 80)
    print(f"\nResults:")
    print(f"  Bugs WITH regressed_by: {results['summary']['bugs_with_regression']}")
    print(f"  Bugs WITHOUT regressed_by: {results['summary']['bugs_without_regression']}")
    print(f"  API errors: {results['summary']['api_errors']}")
    print(f"  Unique regressor bugs: {results['summary']['unique_regressor_bugs']}")
    print(f"\nOutput: {filterer.output_base}")
    

if __name__ == "__main__":
    main()