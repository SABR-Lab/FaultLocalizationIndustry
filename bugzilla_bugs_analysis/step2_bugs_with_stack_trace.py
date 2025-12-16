#!/usr/bin/env python3
"""
================================================================================
NEW STEP 2: STACK TRACE FILTER (PARALLELIZED)
================================================================================

PURPOSE:
--------
Filter crash bugs to keep only those with stack traces in their description
or comments. This ensures we have actionable crash data for fault localization.

PROCESS:
--------
1. Load crash bugs from New Step 1
2. For each bug (IN PARALLEL):
   - Fetch bug comments from Bugzilla REST API
   - Search description and comments for stack traces using regex
   - Extract Socorro links if present
   - Calculate confidence score for stack trace presence
3. Filter: Keep only bugs with stack traces (confidence >= threshold)
4. Save filtered results

"""

import requests
import json
import re
import time
import sys
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


class StackTraceFilter:
    """Filter bugs to keep only those with stack traces (PARALLELIZED)"""
    
    BUGZILLA_API = "https://bugzilla.mozilla.org/rest"
    
    # Regex patterns for stack trace detection
    STACK_TRACE_PATTERNS = [
        # GDB-style stack frames
        r'#\d+\s+0x[0-9a-fA-F]+\s+in\s+\S+',     # Matches: #0 0x7fff12345 in functionName
        r'#\d+\s+\S+\s*\([^)]*\)\s+at\s+\S+:\d+',    # Matches: #0 functionName() at file.cpp:123 
        
        # Frame indicators (Mozilla's Socorro crash report format)
        r'\[\s*frame\s*\d+\s*\]',    # Matches: [frame 0], [frame 1], [ frame 2 ]         
        r'^\s*\d+\s+\S+\s+0x[0-9a-fA-F]+',   # Matches: 0 libxul.so 0x7fff1234
        
        # Mozilla-specific patterns
        r'mozilla::\S+::\S+\s*\(',   # Matches: mozilla::namespace::Function(
        r'\[@\s*[^\]]+\s*\]',    # Matches: [@ functionName] - Mozilla crash signature format
        
        # File:line patterns
        r'\bat\s+\S+\.(cpp|c|h|cc|js|jsm):\d+', # Matches: at filename.cpp:123
        
        # Stack trace section headers
        r'(?i)(crash|stack)\s*(trace|dump|report)',  # Matches: "stack trace", "Crash Report", "Stack Dump" (case-insensitive)
        r'(?i)thread\s+\d+\s*:',        # Matches: Thread 0:, thread 1:, THREAD 2:
        r'(?i)crashing\s+thread',       # Matches: Crashing Thread
    ]
    
    # Socorro link patterns which gives the crash ID
    SOCORRO_PATTERNS = [
        r'https?://crash-stats\.mozilla\.org/report/index/([a-f0-9-]+)',
        r'https?://crash-stats\.mozilla\.org/report/([a-f0-9-]+)',
        r'bp-([a-f0-9-]{36})',
    ]
    
    def __init__(self, rate_limit_delay: float = 0.3, max_workers: int = None):
        """
        Initialize the filter.
        
        Args:
            rate_limit_delay: Seconds between API requests
            max_workers: Number of parallel threads (default: 10)
        """
        self.rate_limit_delay = rate_limit_delay
        if max_workers is None:
            max_workers = min(10, (os.cpu_count() or 2) * 2)
        self.max_workers = max_workers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla-Crash-Research/2.0',
            'Accept': 'application/json'
        })
        
        # Set up output directory
        self.script_dir = Path(__file__).resolve().parent
        self.output_dir = self.script_dir / "method2_outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"  Parallelization: {max_workers} worker threads")
        print(f"  Output directory: {self.output_dir}")
    
    def load_step1_results(self, step1_file: str) -> Dict:
        """Load New Step 1 results"""
        print(f"Loading Step 1 results: {step1_file}")
        
        with open(step1_file, 'r') as f:
            data = json.load(f)
        
        bugs = data.get('bugs', {})
        print(f"  Loaded {len(bugs)} crash bugs\n")
        
        return data
    
    def fetch_bug_comments(self, bug_id: str) -> List[Dict]:
        """
        Fetch comments for a bug from Bugzilla REST API.
        
        Args:
            bug_id: Bug ID
            
        Returns:
            List of comment dictionaries
        """
        url = f"{self.BUGZILLA_API}/bug/{bug_id}/comment"
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if 'bugs' in data and bug_id in data['bugs']:
                return data['bugs'][bug_id].get('comments', [])
            return []
        except requests.RequestException as e:
            print(f"    Error fetching comments for bug {bug_id}: {e}")
            return []
    
    def detect_stack_trace(self, text: str) -> Tuple[bool, int, List[str]]:
        """
        Detect if text contains a stack trace.
        
        Args:
            text: Text to analyze
            
        Returns:
            Tuple of (has_stack_trace, confidence_score, matched_patterns)
        """
        matched_patterns = []
        confidence = 0
        
        # Check each pattern
        for pattern in self.STACK_TRACE_PATTERNS:
            if re.search(pattern, text, re.MULTILINE | re.IGNORECASE):
                matched_patterns.append(pattern[:50])
                confidence += 15
        
        # Check for memory addresses (strong indicator)
        address_pattern = r'0x[0-9a-fA-F]{8,16}'
        addresses = re.findall(address_pattern, text)
        if len(addresses) >= 3:
            confidence += min(len(addresses) * 3, 25)
            matched_patterns.append(f"memory_addresses:{len(addresses)}")
        
        # Check for function call patterns
        func_pattern = r'\w+::\w+(?:::\w+)*\s*\([^)]*\)'
        func_calls = re.findall(func_pattern, text)
        if len(func_calls) >= 2:
            confidence += min(len(func_calls) * 3, 20)
            matched_patterns.append(f"function_calls:{len(func_calls)}")
        
        # Check for numbered stack frames
        frame_pattern = r'^\s*#?\d+\s+'
        frames = re.findall(frame_pattern, text, re.MULTILINE)
        if len(frames) >= 3:
            confidence += 15
            matched_patterns.append(f"numbered_frames:{len(frames)}")
        
        # Cap confidence at 100
        confidence = min(confidence, 100)
        has_stack_trace = confidence >= 40
        
        return has_stack_trace, confidence, matched_patterns
    
    def extract_socorro_links(self, text: str) -> List[str]:
        """
        Extract Socorro crash report links from text.
        
        Args:
            text: Text to search
            
        Returns:
            List of crash IDs
        """
        crash_ids = []
        
        for pattern in self.SOCORRO_PATTERNS:
            matches = re.findall(pattern, text)
            crash_ids.extend(matches)
        
        # Deduplicate
        return list(set(crash_ids))
    
    def extract_stack_trace_text(self, text: str) -> Optional[str]:
        """
        Extract the actual stack trace portion from text.
        
        Args:
            text: Full text containing stack trace
            
        Returns:
            Extracted stack trace or None
        """
        lines = text.split('\n')
        stack_lines = []
        in_stack = False
        
        for line in lines:
            # Detect start of stack trace
            if re.search(r'(?i)(stack|trace|backtrace|crash|frame)', line):
                in_stack = True
            
            # Check if line looks like a stack frame
            is_stack_line = (
                re.search(r'#\d+\s+', line) or
                re.search(r'^\s*\d+\s+0x[0-9a-fA-F]+', line) or
                re.search(r'0x[0-9a-fA-F]{8,}', line) or
                (re.search(r'\w+::\w+', line) and re.search(r'\(', line))
            )
            
            if is_stack_line:
                in_stack = True
                stack_lines.append(line)
            elif in_stack and line.strip():
                if re.search(r'(at |in |from |\(|\)|0x)', line):
                    stack_lines.append(line)
                elif len(stack_lines) >= 3 and not line.strip():
                    break
        
        if len(stack_lines) >= 3:
            return '\n'.join(stack_lines[:50])  # Limit size
        return None
    
    def analyze_bug(self, bug_id: str, bug_data: Dict) -> Dict:
        """
        Analyze a single bug for stack trace presence.
        
        Args:
            bug_id: Bug ID
            bug_data: Bug data from Step 1
            
        Returns:
            Analysis result
        """
        result = {
            'bug_id': bug_id,
            'has_stack_trace': False,
            'stack_trace_confidence': 0,
            'stack_trace_sources': [],
            'socorro_links': [],
            'extracted_stack_trace': None,
            'analysis_error': None
        }
        
        # Fetch comments
        comments = self.fetch_bug_comments(bug_id)
        time.sleep(self.rate_limit_delay)
        
        if not comments:
            result['analysis_error'] = 'Could not fetch comments'
            return result
        
        # Analyze each comment
        all_text = []
        max_confidence = 0
        all_socorro_links = []
        best_stack_trace = None
        
        for i, comment in enumerate(comments):
            comment_text = comment.get('text', '')
            if not comment_text:
                continue
            
            all_text.append(comment_text)
            
            # Detect stack trace
            has_stack, confidence, patterns = self.detect_stack_trace(comment_text)
            
            if confidence > max_confidence:
                max_confidence = confidence
                if has_stack:
                    result['stack_trace_sources'].append({
                        'source': f'comment_{i}',
                        'confidence': confidence,
                        'patterns': patterns
                    })
                    
                    # Try to extract actual stack trace
                    extracted = self.extract_stack_trace_text(comment_text)
                    if extracted and (not best_stack_trace or len(extracted) > len(best_stack_trace)):
                        best_stack_trace = extracted
            
            # Extract Socorro links
            socorro_links = self.extract_socorro_links(comment_text)
            all_socorro_links.extend(socorro_links)
        
        # Update result
        result['has_stack_trace'] = max_confidence >= 40
        result['stack_trace_confidence'] = max_confidence
        result['socorro_links'] = list(set(all_socorro_links))
        result['extracted_stack_trace'] = best_stack_trace
        
        return result
    
    def filter_bugs(self, step1_file: str, confidence_threshold: int = 40) -> Dict:
        """
        Filter bugs to keep only those with stack traces (PARALLELIZED).
        
        Args:
            step1_file: Path to Step 1 JSON file
            confidence_threshold: Minimum confidence to consider as having stack trace
            
        Returns:
            Filtered results
        """
        print("=" * 80)
        print("NEW STEP 2: FILTERING BUGS WITH STACK TRACES (PARALLELIZED)")
        print("=" * 80)
        print(f"\nConfidence threshold: {confidence_threshold}")
        print(f"Worker threads: {self.max_workers}\n")
        
        # Load Step 1 results
        step1_data = self.load_step1_results(step1_file)
        bugs = step1_data.get('bugs', {})
        
        bugs_with_stack_traces = {}
        bugs_without_stack_traces = {}
        bugs_with_socorro = {}
        
        total = len(bugs)
        completed = 0
        
        # Convert to list for indexing
        bug_items = list(bugs.items())
        
        print(f"Processing {total} bugs in parallel...\n")
        
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_bug = {
                executor.submit(self.analyze_bug, bug_id, bug_data): (bug_id, bug_data)
                for bug_id, bug_data in bug_items
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_bug):
                bug_id, bug_data = future_to_bug[future]
                completed += 1
                
                try:
                    analysis = future.result()
                    
                    # Merge bug data with analysis
                    merged = {**bug_data, **analysis}
                    
                    if analysis['has_stack_trace']:
                        bugs_with_stack_traces[bug_id] = merged
                        status = f"✓ Stack trace (confidence: {analysis['stack_trace_confidence']})"
                    else:
                        bugs_without_stack_traces[bug_id] = merged
                        status = f"✗ No stack trace (confidence: {analysis['stack_trace_confidence']})"
                    
                    if analysis['socorro_links']:
                        bugs_with_socorro[bug_id] = analysis['socorro_links']
                    
                    # Print progress
                    print(f"[{completed}/{total}] Bug {bug_id}: {status}")
                    
                except Exception as e:
                    print(f"[{completed}/{total}] Bug {bug_id}: Error - {e}")
                    bugs_without_stack_traces[bug_id] = {
                        **bug_data,
                        'analysis_error': str(e)
                    }
        
        # Build results
        results = {
            'filter_timestamp': datetime.now().isoformat(),
            'step1_file': step1_file,
            'parameters': {
                'confidence_threshold': confidence_threshold,
                'max_workers': self.max_workers
            },
            'summary': {
                'total_input_bugs': total,
                'bugs_with_stack_traces': len(bugs_with_stack_traces),
                'bugs_without_stack_traces': len(bugs_without_stack_traces),
                'bugs_with_socorro_links': len(bugs_with_socorro),
                'filter_rate_percent': round(len(bugs_with_stack_traces) / total * 100, 1) if total > 0 else 0
            },
            'bugs_with_stack_traces': bugs_with_stack_traces,
            'bugs_without_stack_traces': bugs_without_stack_traces,
            'socorro_links_by_bug': bugs_with_socorro
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
        print(f"Bugs WITH stack traces: {summary['bugs_with_stack_traces']}")
        print(f"Bugs WITHOUT stack traces: {summary['bugs_without_stack_traces']}")
        print(f"Bugs with Socorro links: {summary['bugs_with_socorro_links']}")
        print(f"Filter rate: {summary['filter_rate_percent']}%")
        
        # Show sample bugs with stack traces
        bugs_with_st = results['bugs_with_stack_traces']
        if bugs_with_st:
            print("\nSample bugs with stack traces:")
            for bug_id in list(bugs_with_st.keys())[:5]:
                bug = bugs_with_st[bug_id]
                confidence = bug.get('stack_trace_confidence', 0)
                socorro_count = len(bug.get('socorro_links', []))
                print(f"  Bug {bug_id}: confidence={confidence}, socorro_links={socorro_count}")
                print(f"    {bug['summary'][:70]}...")
    
    def save_results(self, results: Dict, filename: str = None) -> str:
        """Save results to JSON file in method2_outputs folder"""
        if not filename:
            filename = "new_step2_bugs_with_stack_traces.json"
        
        # Ensure filename is just the basename, then join with output_dir
        filename = Path(filename).name
        output_path = self.output_dir / filename
        
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved: {output_path}")
        return str(output_path)


def main():
    """Main execution"""
    # Hardcoded input file path
    script_dir = Path(__file__).resolve().parent
    step1_file = script_dir / "method2_outputs" / "new_step1_crash_bugs.json"
    
    # Validate input file
    if not step1_file.exists():
        print(f"ERROR: Input file not found: {step1_file}")
        sys.exit(1)

    # Initialize with parallelization
    filterer = StackTraceFilter()

    # Run filtering
    results = filterer.filter_bugs(
        step1_file=str(step1_file),
        confidence_threshold=40
    )

    # Save results
    filename = filterer.save_results(results)

    print("\n" + "=" * 80)
    print("NEW STEP 2 COMPLETE")
    print("=" * 80)
    print(f"Bugs with stack traces: {results['summary']['bugs_with_stack_traces']}")
    print(f"Output file: {filename}")


if __name__ == "__main__":
    main()