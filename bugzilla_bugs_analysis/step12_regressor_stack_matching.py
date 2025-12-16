#!/usr/bin/env python3
"""
================================================================================
STEP 12: MATCH REGRESSOR COMMITS WITH STACK TRACES
================================================================================

PURPOSE:
--------
For each bug, compare the regressor commit's files and methods against the
stack trace's files and functions to validate if regression analysis correctly
identifies crash locations.

INPUT:
------
- Step 8 output: outputs/step8_method_extraction/bugs/bug_*.json
  (Contains regressor commit files and extracted methods)
- Step 1 output: outputs/step1_bugzilla_bugs_extraction/older_stack_only/bugs/bug_*.json
  (Contains stack traces from Bugzilla)
- Step 2 output: outputs/step2_socorro_extraction/full_stack_socorro/bugs/bug_*.json
  (Contains full stack traces from Socorro)

OUTPUT:
-------
outputs/step12_regressor_stack_matching/
├── bugs/
│   └── bug_<ID>.json
├── matching_summary.json
└── matching_report.txt
"""

import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path
from collections import defaultdict


class StackTraceMatcher:
    """Match regressor commit files/methods with stack trace files/functions"""
    
    def __init__(self):
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # Input directories
        self.step8_dir = self.outputs_base / "step8_method_extraction" / "bugs"
        self.step1_dir = self.outputs_base / "step1_bugzilla_bugs_extraction" / "older_stack_only" / "bugs"
        self.step2_dir = self.outputs_base / "step2_socorro_extraction" / "full_stack_socorro" / "bugs"
        
        # Output directory
        self.output_dir = self.outputs_base / "step12_stack_regressor_matching"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Also check Step 2 bugzilla fallback directory
        self.step2_bugzilla_dir = self.outputs_base / "step2_socorro_extraction" / "bugzilla_stack_only" / "bugs"
        
        print("=" * 80)
        print("STEP 12: STACK TRACE AND REGRESSOR MATCHING")
        print("=" * 80)
        print(f"\nInput directories:")
        print(f"  Step 8 (methods): {self.step8_dir}")
        print(f"  Step 1 (older stacks): {self.step1_dir}")
        print(f"  Step 2 Socorro: {self.step2_dir}")
        print(f"  Step 2 Bugzilla fallback: {self.step2_bugzilla_dir}")
        print(f"\nOutput directory: {self.output_dir}\n")
    
    def load_step8_bugs(self) -> Dict[str, Dict]:
        """Load bugs from Step 8 output (method extraction)"""
        bugs = {}
        
        if not self.step8_dir.exists():
            print(f"WARNING: Step 8 directory not found: {self.step8_dir}")
            return bugs
        
        for filepath in self.step8_dir.glob("bug_*.json"):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    bug = json.load(f)
                    bug_id = bug.get('bug_id', filepath.stem.replace('bug_', ''))
                    bugs[bug_id] = bug
            except Exception as e:
                print(f"  Warning: Failed to load {filepath}: {e}")
        
        print(f"Loaded {len(bugs)} bugs from Step 8")
        return bugs
    
    def load_stack_trace_bug(self, bug_id: str) -> Optional[Tuple[Dict, str]]:
        """
        Load stack trace for a bug from Step 1 OR Step 2.
        Checks BOTH directories since bug will be in one of them for sure.
        Returns (bug_data, source) where source is 'step1_bugzilla' or 'step2_socorro'
        """
        # Check Step 2 (Socorro - full stack traces from recent bugs)
        step2_file = self.step2_dir / f"bug_{bug_id}.json"
        if step2_file.exists():
            try:
                with open(step2_file, 'r', encoding='utf-8') as f:
                    return json.load(f), 'step2_socorro'
            except Exception as e:
                print(f"    Warning: Failed to load Step 2 bug {bug_id}: {e}")
        
        # Check Step 1 (Bugzilla - older bugs with stack traces)
        step1_file = self.step1_dir / f"bug_{bug_id}.json"
        if step1_file.exists():
            try:
                with open(step1_file, 'r', encoding='utf-8') as f:
                    return json.load(f), 'step1_bugzilla'
            except Exception as e:
                print(f"    Warning: Failed to load Step 1 bug {bug_id}: {e}")
        
        # Also check Step 2 bugzilla_stack_only (fallback from Socorro)
        step2_bugzilla_dir = self.outputs_base / "step2_socorro_extraction" / "bugzilla_stack_only" / "bugs"
        step2_bz_file = step2_bugzilla_dir / f"bug_{bug_id}.json"
        if step2_bz_file.exists():
            try:
                with open(step2_bz_file, 'r', encoding='utf-8') as f:
                    return json.load(f), 'step2_bugzilla_fallback'
            except Exception as e:
                print(f"    Warning: Failed to load Step 2 bugzilla fallback bug {bug_id}: {e}")
        
        # Not found in any location
        return None, None
    
    def extract_regressor_info(self, step8_bug: Dict) -> List[Dict]:
        """Extract regressor commit files and methods from Step 8 bug data"""
        regressor_info = []
        
        for file_data in step8_bug.get('files', []):
            filepath = file_data.get('filepath', '')
            
            for commit in file_data.get('regressor_commits', []):
                methods = commit.get('methods', [])
                method_names = [m.get('name', '') for m in methods if m.get('name')]
                
                regressor_info.append({
                    'filepath': filepath,
                    'filename': Path(filepath).name if filepath else '',
                    'commit_hash': commit.get('commit_hash', ''),
                    'full_hash': commit.get('full_hash', ''),
                    'regressor_bug_id': commit.get('regressor_bug_id', ''),
                    'methods': methods,
                    'method_names': method_names,
                    'method_count': len(method_names)
                })
        
        return regressor_info
    
    def extract_stack_trace_info(self, stack_bug: Dict, source: str) -> Dict:
        """Extract stack trace files and functions from Step 1 or Step 2 bug data"""
        frames = []
        filenames = set()
        functions = set()
        
        if source == 'step2_socorro':
            # Step 2 Socorro format: stack_trace.frames[]
            stack_trace = stack_bug.get('stack_trace', {})
            raw_frames = stack_trace.get('frames', [])
            
            for frame in raw_frames:
                file_path = frame.get('file', '') or frame.get('source', '')
                function = frame.get('function', '')
                
                # Extract just the filename from the path
                filename = Path(file_path).name if file_path else ''
                
                frames.append({
                    'frame_index': frame.get('frame_index'),
                    'file': file_path,
                    'filename': filename,
                    'function': function,
                    'module': frame.get('module', ''),
                    'line': frame.get('line')
                })
                
                if filename:
                    filenames.add(filename)
                if function:
                    functions.add(function)
        
        elif source == 'step2_bugzilla_fallback':
            # Step 2 Bugzilla fallback format: same as Socorro but from bugzilla
            stack_trace = stack_bug.get('stack_trace', {})
            raw_frames = stack_trace.get('frames', [])
            
            for frame in raw_frames:
                file_path = frame.get('file', '') or frame.get('source', '')
                function = frame.get('function', '')
                
                filename = Path(file_path).name if file_path else ''
                
                frames.append({
                    'frame_index': frame.get('frame_index'),
                    'file': file_path,
                    'filename': filename,
                    'function': function,
                    'module': frame.get('module', ''),
                    'line': frame.get('line')
                })
                
                if filename:
                    filenames.add(filename)
                if function:
                    functions.add(function)
        
        elif source == 'step1_bugzilla':
            # Step 1 format: stack_traces[].parsed_frames[]
            for stack_trace in stack_bug.get('stack_traces', []):
                for frame in stack_trace.get('parsed_frames', []):
                    file_path = frame.get('file', '')
                    function = frame.get('function', '')
                    
                    filename = Path(file_path).name if file_path else ''
                    
                    frames.append({
                        'frame_index': frame.get('frame_index'),
                        'file': file_path,
                        'filename': filename,
                        'function': function,
                        'module': frame.get('module', ''),
                        'line': frame.get('line')
                    })
                    
                    if filename:
                        filenames.add(filename)
                    if function:
                        functions.add(function)
        
        return {
            'source': source,
            'frames': frames,
            'frame_count': len(frames),
            'filenames': list(filenames),
            'functions': list(functions),
            'has_filenames': len(filenames) > 0,
            'has_functions': len(functions) > 0
        }
    
    def normalize_function_name(self, name: str) -> str:
        """Normalize function name for comparison"""
        if not name:
            return ''
        
        # Remove template parameters
        name = re.sub(r'<[^>]*>', '', name)
        
        # Remove parameters
        name = re.sub(r'\([^)]*\)', '', name)
        
        # Get just the function name (after last ::)
        if '::' in name:
            name = name.split('::')[-1]
        
        # Remove common prefixes
        name = re.sub(r'^(ns|NS_|mozilla::)', '', name)
        
        return name.strip().lower()
    
    def normalize_filename(self, name: str) -> str:
        """Normalize filename for comparison"""
        if not name:
            return ''
        return Path(name).name.lower()
    
    def match_files_and_methods(self, regressor_info: List[Dict], 
                                 stack_info: Dict) -> Dict:
        """Compare regressor files/methods with stack trace files/functions"""
        
        # Normalize stack trace data
        stack_filenames_normalized = {
            self.normalize_filename(f): f for f in stack_info['filenames']
        }
        stack_functions_normalized = {
            self.normalize_function_name(f): f for f in stack_info['functions']
        }
        
        file_matches = []
        method_matches = []
        
        for reg in regressor_info:
            reg_filename = self.normalize_filename(reg['filename'])
            
            # Check file match
            file_match = {
                'regressor_file': reg['filepath'],
                'regressor_filename': reg['filename'],
                'matched': False,
                'matched_stack_file': None
            }
            
            if reg_filename and reg_filename in stack_filenames_normalized:
                file_match['matched'] = True
                file_match['matched_stack_file'] = stack_filenames_normalized[reg_filename]
            
            file_matches.append(file_match)
            
            # Check method matches
            for method_name in reg['method_names']:
                method_normalized = self.normalize_function_name(method_name)
                
                method_match = {
                    'regressor_file': reg['filepath'],
                    'regressor_method': method_name,
                    'regressor_method_normalized': method_normalized,
                    'matched': False,
                    'matched_stack_function': None,
                    'match_type': None
                }
                
                # Exact match (normalized)
                if method_normalized and method_normalized in stack_functions_normalized:
                    method_match['matched'] = True
                    method_match['matched_stack_function'] = stack_functions_normalized[method_normalized]
                    method_match['match_type'] = 'exact'
                else:
                    # Partial match - check if method name is contained in any stack function
                    for stack_func_norm, stack_func_orig in stack_functions_normalized.items():
                        if method_normalized and method_normalized in stack_func_norm:
                            method_match['matched'] = True
                            method_match['matched_stack_function'] = stack_func_orig
                            method_match['match_type'] = 'partial'
                            break
                        elif stack_func_norm and stack_func_norm in method_normalized:
                            method_match['matched'] = True
                            method_match['matched_stack_function'] = stack_func_orig
                            method_match['match_type'] = 'partial_reverse'
                            break
                
                method_matches.append(method_match)
        
        # Calculate statistics
        total_files = len(file_matches)
        matched_files = sum(1 for m in file_matches if m['matched'])
        total_methods = len(method_matches)
        matched_methods = sum(1 for m in method_matches if m['matched'])
        
        return {
            'file_matches': file_matches,
            'method_matches': method_matches,
            'statistics': {
                'total_regressor_files': total_files,
                'matched_files': matched_files,
                'file_match_rate': matched_files / total_files if total_files > 0 else 0,
                'total_regressor_methods': total_methods,
                'matched_methods': matched_methods,
                'method_match_rate': matched_methods / total_methods if total_methods > 0 else 0,
                'stack_has_filenames': stack_info['has_filenames'],
                'stack_has_functions': stack_info['has_functions'],
                'stack_filename_count': len(stack_info['filenames']),
                'stack_function_count': len(stack_info['functions'])
            }
        }
    
    def process_bug(self, bug_id: str, step8_bug: Dict) -> Optional[Dict]:
        """Process a single bug - match regressor with stack trace"""
        
        # Load stack trace from Step 1 or Step 2
        stack_bug, source = self.load_stack_trace_bug(bug_id)
        
        if stack_bug is None:
            return {
                'bug_id': bug_id,
                'status': 'no_stack_trace_found',
                'error': 'Bug not found in Step 1 or Step 2 outputs'
            }
        
        # Extract regressor info from Step 8
        regressor_info = self.extract_regressor_info(step8_bug)
        
        if not regressor_info:
            return {
                'bug_id': bug_id,
                'status': 'no_regressor_commits',
                'stack_source': source,
                'error': 'No regressor commits found in Step 8 data'
            }
        
        # Extract stack trace info
        stack_info = self.extract_stack_trace_info(stack_bug, source)
        
        # Check if stack trace has data
        if not stack_info['has_filenames'] and not stack_info['has_functions']:
            return {
                'bug_id': bug_id,
                'status': 'empty_stack_trace',
                'stack_source': source,
                'regressor_info': regressor_info,
                'stack_info': {
                    'source': source,
                    'frame_count': stack_info['frame_count'],
                    'filenames': [],
                    'functions': [],
                    'note': 'Stack trace has no file names or function names'
                },
                'matching_results': None
            }
        
        # Perform matching
        matching_results = self.match_files_and_methods(regressor_info, stack_info)
        
        return {
            'bug_id': bug_id,
            'status': 'matched',
            'stack_source': source,
            'bugzilla_url': stack_bug.get('bugzilla_url', ''),
            'summary': stack_bug.get('summary', ''),
            'regressor_info': regressor_info,
            'stack_info': {
                'source': source,
                'frame_count': stack_info['frame_count'],
                'filenames': stack_info['filenames'],
                'functions': stack_info['functions'][:50]  # Limit for readability
            },
            'matching_results': matching_results
        }
    
    def process_all_bugs(self) -> Dict:
        """Process all bugs from Step 8"""
        step8_bugs = self.load_step8_bugs()
        
        if not step8_bugs:
            return {'error': 'No bugs loaded from Step 8'}
        
        results = {
            'processing_timestamp': datetime.now().isoformat(),
            'bugs_processed': 0,
            'bugs_matched': 0,
            'bugs_no_stack': 0,
            'bugs_no_regressor': 0,
            'bugs_empty_stack': 0,
            'bug_results': {},
            'aggregate_stats': {
                'total_file_comparisons': 0,
                'total_file_matches': 0,
                'total_method_comparisons': 0,
                'total_method_matches': 0
            }
        }
        
        print(f"\nProcessing {len(step8_bugs)} bugs...\n")
        
        for i, (bug_id, step8_bug) in enumerate(step8_bugs.items(), 1):
            print(f"[{i}/{len(step8_bugs)}] Bug {bug_id}...", end=" ")
            
            bug_result = self.process_bug(bug_id, step8_bug)
            results['bugs_processed'] += 1
            
            # Save individual bug result
            bug_output_file = self.bugs_output_dir / f"bug_{bug_id}.json"
            with open(bug_output_file, 'w', encoding='utf-8') as f:
                json.dump(bug_result, f, indent=2)
            
            # Update statistics
            status = bug_result.get('status', 'unknown')
            
            if status == 'matched':
                results['bugs_matched'] += 1
                stats = bug_result['matching_results']['statistics']
                results['aggregate_stats']['total_file_comparisons'] += stats['total_regressor_files']
                results['aggregate_stats']['total_file_matches'] += stats['matched_files']
                results['aggregate_stats']['total_method_comparisons'] += stats['total_regressor_methods']
                results['aggregate_stats']['total_method_matches'] += stats['matched_methods']
                
                print(f"✓ Files: {stats['matched_files']}/{stats['total_regressor_files']}, "
                      f"Methods: {stats['matched_methods']}/{stats['total_regressor_methods']}")
            
            elif status == 'no_stack_trace_found':
                results['bugs_no_stack'] += 1
                print("✗ No stack trace found")
            
            elif status == 'no_regressor_commits':
                results['bugs_no_regressor'] += 1
                print("✗ No regressor commits")
            
            elif status == 'empty_stack_trace':
                results['bugs_empty_stack'] += 1
                print("○ Stack trace has no file/function names")
            
            results['bug_results'][bug_id] = {
                'status': status,
                'stack_source': bug_result.get('stack_source'),
                'file_matches': bug_result.get('matching_results', {}).get('statistics', {}).get('matched_files', 0),
                'method_matches': bug_result.get('matching_results', {}).get('statistics', {}).get('matched_methods', 0)
            }
        
        # Calculate aggregate rates
        agg = results['aggregate_stats']
        if agg['total_file_comparisons'] > 0:
            agg['overall_file_match_rate'] = agg['total_file_matches'] / agg['total_file_comparisons']
        else:
            agg['overall_file_match_rate'] = 0
        
        if agg['total_method_comparisons'] > 0:
            agg['overall_method_match_rate'] = agg['total_method_matches'] / agg['total_method_comparisons']
        else:
            agg['overall_method_match_rate'] = 0
        
        return results
    
    def save_results(self, results: Dict):
        """Save matching results"""
        print(f"\n{'=' * 80}")
        print("SAVING RESULTS")
        print(f"{'=' * 80}\n")
        
        # Save summary JSON
        summary_file = self.output_dir / 'matching_summary.json'
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        print(f"✓ Saved summary to {summary_file}")
        
        # Save human-readable report
        report_file = self.output_dir / 'matching_report.txt'
        self._save_report(results, report_file)
        print(f"✓ Saved report to {report_file}")
    
    def _save_report(self, results: Dict, output_path: Path):
        """Save human-readable report"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("STEP 9: STACK TRACE MATCHING REPORT\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Processing Time: {results['processing_timestamp']}\n\n")
            
            f.write("SUMMARY\n")
            f.write("-" * 40 + "\n")
            f.write(f"Total bugs processed: {results['bugs_processed']}\n")
            f.write(f"  - Matched: {results['bugs_matched']}\n")
            f.write(f"  - No stack trace found: {results['bugs_no_stack']}\n")
            f.write(f"  - No regressor commits: {results['bugs_no_regressor']}\n")
            f.write(f"  - Empty stack trace: {results['bugs_empty_stack']}\n\n")
            
            agg = results['aggregate_stats']
            f.write("AGGREGATE MATCHING STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(f"File comparisons: {agg['total_file_matches']}/{agg['total_file_comparisons']} "
                   f"({agg['overall_file_match_rate']:.1%})\n")
            f.write(f"Method comparisons: {agg['total_method_matches']}/{agg['total_method_comparisons']} "
                   f"({agg['overall_method_match_rate']:.1%})\n\n")
            
            # Per-bug results
            f.write("PER-BUG RESULTS\n")
            f.write("-" * 40 + "\n")
            for bug_id, bug_result in results['bug_results'].items():
                status = bug_result['status']
                source = bug_result.get('stack_source', 'N/A')
                
                if status == 'matched':
                    f.write(f"Bug {bug_id} [{source}]: "
                           f"Files={bug_result['file_matches']}, "
                           f"Methods={bug_result['method_matches']}\n")
                else:
                    f.write(f"Bug {bug_id}: {status}\n")
    
    def print_summary(self, results: Dict):
        """Print summary to console"""
        print(f"\n{'=' * 80}")
        print("MATCHING SUMMARY")
        print(f"{'=' * 80}")
        
        print(f"\nBugs processed: {results['bugs_processed']}")
        print(f"  Matched: {results['bugs_matched']}")
        print(f"  No stack trace: {results['bugs_no_stack']}")
        print(f"  No regressor: {results['bugs_no_regressor']}")
        print(f"  Empty stack: {results['bugs_empty_stack']}")
        
        agg = results['aggregate_stats']
        print(f"\nAggregate Results:")
        print(f"  File matches: {agg['total_file_matches']}/{agg['total_file_comparisons']} "
              f"({agg['overall_file_match_rate']:.1%})")
        print(f"  Method matches: {agg['total_method_matches']}/{agg['total_method_comparisons']} "
              f"({agg['overall_method_match_rate']:.1%})")


def main():
    """Main execution"""
    matcher = StackTraceMatcher()
    results = matcher.process_all_bugs()
    
    if 'error' not in results:
        matcher.save_results(results)
        matcher.print_summary(results)
        
        print(f"\n{'=' * 80}")
        print("✓ STEP 9 COMPLETE")
        print(f"{'=' * 80}")
        print(f"\nOutput: {matcher.output_dir}")
    else:
        print(f"\n✗ Error: {results['error']}")


if __name__ == "__main__":
    main()