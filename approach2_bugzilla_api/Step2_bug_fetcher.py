#!/usr/bin/env python3
"""
================================================================================
STEP 2: BUGBUG VALIDATOR
================================================================================

PROCESS:
--------
* Load bug numbers from Step 1 JSON file
* For each bug number:
  - Check if bug exists in BugBug database (exact ID lookup)
  - If found: Extract metadata (summary, status, resolution, product, component, etc.)
  - If not found: Add to rejected_bugs list
* Check crash signature matching:
  - Compare Step 1 signature with BugBug's crash_signature field
  - Flag mismatches (different signatures)
  - Allow bugs without crash_signature in BugBug
* Build validated_bugs with full metadata + crash mappings

OUTPUT: step2_bugbug_analysis_*.json
------------------------------------
* validated_bugs: {bug_id: {metadata, crash_mapping: {signature, crash_ids}}}
* rejected_bugs: Bug IDs not found in BugBug
* signature_mismatches: Bugs where signatures don't match

KEY POINTS:
-----------
* Validation = exact bug ID match (no fuzzy matching)
* Not all bugs have crash_signature in BugBug (this is OK)
* No duplicate data - each bug appears only once
"""


from datetime import datetime
import json
import sys
from typing import Dict, Optional, Set
from pathlib import Path

try:
    from bugbug_utils import get_bugbug_cache, BugBugUtils
    UTILS_AVAILABLE = True
except ImportError:
    UTILS_AVAILABLE = False
    print("ERROR: bugbug_utils.py not found!")


class BugBugAnalyzer:
    """Analyzes bugs from BugBug database and validates against crash data"""
    
    def __init__(self):
        if not UTILS_AVAILABLE:
            raise ImportError("bugbug_utils.py is required but not found!")
        
        self.bug_cache = get_bugbug_cache()
        print(f"Loaded BugBug cache: {self.bug_cache.count()} bugs\n")
    
    def load_step1_results(self, step1_file: str) -> Dict:
        """Load Step 1 results from JSON file"""
        print(f"Loading Step 1 results: {step1_file}")
        
        with open(step1_file, 'r') as f:
            step1_results = json.load(f)
        
        signature = step1_results.get('signature', 'Unknown')
        total_bugs = len(step1_results.get('unique_bugs', []))
        print(f"  Signature: {signature}")
        print(f"  Unique bugs: {total_bugs}\n")
        
        return step1_results
    
    def get_bug_details(self, bug_id: str) -> Optional[Dict]:
        """Get bug information from BugBug cache"""
        bug = self.bug_cache.get_bug(bug_id)
        if not bug:
            return None
        
        return BugBugUtils.format_bug_summary(bug)
    
    def validate_bugs(self, step1_file: str) -> Dict:
        """Validate bugs from Step 1 against BugBug database"""
        
        # Load Step 1 results
        crash_results = self.load_step1_results(step1_file)
        signature = crash_results.get('signature', 'Unknown')
        all_bug_numbers = crash_results.get('unique_bugs', [])
        
        print("="*80)
        print("VALIDATING BUGS IN BUGBUG")
        print("="*80)
        
        bugs_in_bugbug: Set[str] = set()
        bugs_not_in_bugbug: Set[str] = set()
        enriched_bugs = {}
        signature_mismatches = []
        
        for bug_id in all_bug_numbers:
            bug_details = self.get_bug_details(bug_id)
            
            if bug_details:
                bugs_in_bugbug.add(bug_id)
                enriched_bugs[bug_id] = bug_details
                
                # Check crash signature match
                bugbug_signature = bug_details.get('crash_signature', '')
                if bugbug_signature:
                    # Check if Step 1 signature is in BugBug signature
                    # BugBug signatures can have multiple like "[@ sig1] [@ sig2]"
                    if signature not in bugbug_signature:
                        signature_mismatches.append({
                            'bug_id': bug_id,
                            'step1_signature': signature,
                            'bugbug_signature': bugbug_signature
                        })
                        print(f" Bug {bug_id}: MISMATCH - {bug_details['summary'][:50]}")
                    else:
                        print(f" Bug {bug_id}: {bug_details['summary'][:70]}")
                else:
                    print(f" Bug {bug_id}: {bug_details['summary'][:70]}")
            else:
                bugs_not_in_bugbug.add(bug_id)
                print(f"✗ Bug {bug_id}: NOT in BugBug")
        
        # Filter crashes to only include validated bugs
        filtered_crashes = []
        for crash_result in crash_results.get('results', []):
            crash_bugs = crash_result.get('bug_numbers', [])
            validated_bugs = [b for b in crash_bugs if b in bugs_in_bugbug]
            
            if validated_bugs:
                crash_result['validated_bugs'] = validated_bugs
                crash_result['bugbug_details'] = {
                    bug_id: enriched_bugs[bug_id] 
                    for bug_id in validated_bugs
                }
                filtered_crashes.append(crash_result)
        
        # Summary
        print("\n" + "="*80)
        print("VALIDATION SUMMARY")
        print("="*80)
        print(f"Total bugs: {len(all_bug_numbers)}")
        print(f"Validated: {len(bugs_in_bugbug)}")
        print(f"Not found: {len(bugs_not_in_bugbug)}")
        print(f"Signature mismatches: {len(signature_mismatches)}")
        print(f"Validation rate: {round(len(bugs_in_bugbug) / len(all_bug_numbers) * 100, 1) if all_bug_numbers else 0}%")
        print(f"Crashes with validated bugs: {len(filtered_crashes)}/{len(crash_results.get('results', []))}")
        
        if bugs_not_in_bugbug:
            print(f"\nBugs not in BugBug: {', '.join(sorted(bugs_not_in_bugbug))}")
        
        return {
            'signature': signature,
            'step1_file': step1_file,
            'analysis_timestamp': datetime.now().isoformat(),
            'summary': {
                'total_crashes_found': crash_results.get('total_crashes', 0),
                'crashes_processed': crash_results.get('processed_crashes', 0),
                'crashes_with_validated_bugs': len(filtered_crashes),
                'total_unique_bugs': len(all_bug_numbers),
                'bugs_validated': len(bugs_in_bugbug),
                'bugs_not_in_bugbug': len(bugs_not_in_bugbug),
                'signature_mismatches': len(signature_mismatches),
                'validation_rate_percent': round(len(bugs_in_bugbug) / len(all_bug_numbers) * 100, 1) if all_bug_numbers else 0
            },
            'validated_bugs': {
                bug_id: {
                    **enriched_bugs[bug_id],
                    'crash_mapping': {
                        'signature': signature,
                        'crash_count': crash_results.get('bugs_index', {}).get(bug_id, {}).get('crash_count', 0),
                        'crash_ids': crash_results.get('bugs_index', {}).get(bug_id, {}).get('crash_ids', [])
                    }
                }
                for bug_id in bugs_in_bugbug
            },
            'rejected_bugs': list(bugs_not_in_bugbug),
            'signature_mismatches': signature_mismatches
        }
    
    def save_results(self, results: Dict, filename: str = None) -> str:
        """Save analysis results to JSON file"""
        if not filename:
            safe_sig = results['signature'].replace(':', '_').replace('/', '_')[:50]
            #timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = f"step2_bugbug_analysis_{safe_sig}.json" #_{timestamp}.json"
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved: {filename}")
        return filename
    
    def print_bug_details(self, results: Dict):
        """Print detailed bug information with crash mappings"""
        validated_bugs = results.get('validated_bugs', {})
        signature = results.get('signature', 'Unknown')
        signature_mismatches = results.get('signature_mismatches', [])
        
        if not validated_bugs:
            return
        
        print("\n" + "="*80)
        print("VALIDATED BUGS - DETAILS AND CRASH MAPPINGS")
        print("="*80)
        
        for bug_id in sorted(validated_bugs.keys()):
            bug_info = validated_bugs[bug_id]
            print(f"\nBug {bug_id}:")
            print(f"  Summary: {bug_info['summary']}")
            print(f"  Status: {bug_info['status']} ({bug_info['resolution']})")
            print(f"  Product: {bug_info['product']}")
            print(f"  Component: {bug_info['component']}")
            print(f"  Severity: {bug_info['severity']}")
            print(f"  Priority: {bug_info['priority']}")
            
            # Check for signature mismatch
            mismatch = next((m for m in signature_mismatches if m['bug_id'] == bug_id), None)
            if mismatch:
                print(f"\n  CRASH SIGNATURE MISMATCH:")
                print(f"    Step 1 Signature: {mismatch['step1_signature']}")
                print(f"    BugBug Signature: {mismatch['bugbug_signature']}")
            elif bug_info.get('crash_signature'):
                print(f"\n  ✓ Crash Signature Match:")
                print(f"    Signature: {bug_info['crash_signature']}")
            else:
                print(f"\n   No crash signature in BugBug")
            
            # Show crash mappings
            crash_mapping = bug_info.get('crash_mapping', {})
            if crash_mapping:
                crash_count = crash_mapping.get('crash_count', 0)
                crash_ids = crash_mapping.get('crash_ids', [])
                
                print(f"\n  Crash Mapping:")
                print(f"    Step 1 Signature: {signature}")
                print(f"    Total Crashes: {crash_count}")
                print(f"    Crash IDs:")
                for i, crash_id in enumerate(crash_ids, 1):
                    print(f"      {i}. {crash_id}")
        
        # Print detailed mismatch report if any
        if signature_mismatches:
            print("\n" + "="*80)
            print("SIGNATURE MISMATCH DETAILS")
            print("="*80)
            
            for mismatch in signature_mismatches:
                bug_id = mismatch['bug_id']
                bug_data = validated_bugs.get(bug_id, {})
                crash_mapping = bug_data.get('crash_mapping', {})
                crash_ids = crash_mapping.get('crash_ids', [])
                
                print(f"\nBug {bug_id}:")
                print(f"  Step 1 Signature: {mismatch['step1_signature']}")
                print(f"  BugBug Signature: {mismatch['bugbug_signature']}")
                print(f"  Affected Crash IDs:")
                for i, crash_id in enumerate(crash_ids, 1):
                    print(f"    {i}. {crash_id}")


def main():
    """Main execution"""
    
    if not UTILS_AVAILABLE:
        print("ERROR: bugbug_utils.py not found!")
        return
    
    # Get Step 1 file
    if len(sys.argv) > 1:
        step1_file = sys.argv[1]
    else:
        step1_file = "step1_sig_to_bugs_OOM | small.json"
        print(f"Using default file: {step1_file}")
        print(f"Usage: python {sys.argv[0]} <step1_results.json>\n")
    
    # Check file exists
    if not Path(step1_file).exists():
        print(f"ERROR: File '{step1_file}' not found!")
        return
    
    # Run analysis
    try:
        analyzer = BugBugAnalyzer()
        results = analyzer.validate_bugs(step1_file)
        filename = analyzer.save_results(results)
        analyzer.print_bug_details(results)
        
        print("\n" + "="*80)
        print(" STEP 2 COMPLETE")
        print("="*80)
        print(f"Validated bugs: {results['summary']['bugs_validated']}")
        print(f"Output file: {filename}")
        
    except Exception as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()