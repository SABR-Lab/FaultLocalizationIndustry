#!/usr/bin/env python3
"""
================================================================================
STEP 1: BUGZILLA CRASH BUG EXTRACTOR - TIME-BASED STRATEGY (NO LIMITS)
================================================================================

PURPOSE:
--------
Extract ALL crash bugs from Bugzilla using a time-based strategy:
- RECENT (past 6 months): Bugs with BOTH stack traces AND Socorro links
- OLDER (before 6 months): Bugs with stack traces ONLY

NO LIMITS - extracts all matching bugs.
"""

import requests
import json
import time
import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from collections import defaultdict
from pathlib import Path


class BugzillaCrashExtractor:
    """Extract crash bugs from Bugzilla with time-based strategy"""
    
    BUGZILLA_API = "https://bugzilla.mozilla.org/rest"
    
    BUG_FIELDS = [
        'id', 'summary', 'status', 'resolution',
        'product', 'component', 'version',
        'cf_crash_signature', 'keywords', 'whiteboard',
        'severity', 'priority', 'target_milestone',
        'creation_time', 'last_change_time',
        'assigned_to', 'creator',
        'regressed_by', 'regressions', 'depends_on', 'blocks',
        'see_also', 'duplicates', 'dupe_of', 'comments'
    ]
    
    STACK_TRACE_PATTERNS = [
        r'#\d+\s+0x[0-9a-fA-F]+\s+in\s+\S+',
        r'#\d+\s+\S+\s*\([^)]*\)\s+at\s+\S+:\d+',
        r'\[\s*frame\s*\d+\s*\]',
        r'^\s*\d+\s+\S+\s+0x[0-9a-fA-F]+',
        r'mozilla::\S+::\S+\s*\(',
        r'ns[A-Z]\w+::\w+',
        r'\[@\s*[^\]]+\s*\]',
        r'\bat\s+\S+\.(cpp|c|h|cc|js|jsm|rs|cxx):\d+',
        r'(?i)(crash|stack)\s*(trace|dump|report)',
        r'(?i)thread\s+\d+\s*:',
        r'(?i)crashing\s+thread',
        r'frame\s+#\d+',
    ]
    
    ACTIVE_SOCORRO_PATTERNS = [
        r'https?://crash-stats\.mozilla\.org/report/index/([a-f0-9-]+)',
        r'https?://crash-stats\.mozilla\.org/report/([a-f0-9-]+)',
    ]
    
    VALID_MODULE_PATTERN = re.compile(
        r'^(xul\.dll|xul\.so|libxul\.so|nss3\.dll|libnss3\.so|'
        r'mozglue\.dll|libmozglue\.so|kernel32\.dll|ntdll\.dll|'
        r'ucrtbase\.dll|libpthread\.so|libc\.so|libm\.so|'
        r'[\w]+\.dll|[\w]+\.so|[\w]+\.dylib)$',
        re.IGNORECASE
    )
    
    def __init__(self, rate_limit_delay: float = 0.5, recent_months: int = 6):
        self.rate_limit_delay = rate_limit_delay
        self.recent_months = recent_months
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla-Crash-Research/3.0',
            'Accept': 'application/json'
        })
        
        self._compiled_stack_patterns = [
            re.compile(pattern, re.MULTILINE | re.IGNORECASE) 
            for pattern in self.STACK_TRACE_PATTERNS
        ]
        
        self.cutoff_date = datetime.now() - timedelta(days=recent_months * 30)
        self.cutoff_date_str = self.cutoff_date.strftime('%Y-%m-%d')
        
        self.script_dir = Path(__file__).resolve().parent
        self.output_base = self.script_dir / "outputs" / "step1_bugzilla_bugs_extraction"
        self.output_base.mkdir(parents=True, exist_ok=True)
        
        print(f"Output directory: {self.output_base}")
        print(f"Cutoff date (recent vs older): {self.cutoff_date_str}")
        print()
    
    def _is_recent_bug(self, creation_time: str) -> bool:
        try:
            bug_date = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
            bug_date = bug_date.replace(tzinfo=None)
            return bug_date >= self.cutoff_date
        except (ValueError, AttributeError):
            return False
    
    def search_crash_bugs_by_date(self, product: str, after_date: str = None, 
                                   before_date: str = None, limit: int = 500, 
                                   offset: int = 0) -> List[Dict]:
        url = f"{self.BUGZILLA_API}/bug"
        
        params = {
            'product': product,
            'f1': 'cf_crash_signature',
            'o1': 'isnotempty',
            'resolution': 'FIXED',
            'include_fields': ','.join(self.BUG_FIELDS),
            'limit': min(limit, 500),
            'offset': offset,
            'order': 'bug_id DESC'
        }
        
        field_num = 2
        if after_date:
            params[f'f{field_num}'] = 'creation_ts'
            params[f'o{field_num}'] = 'greaterthaneq'
            params[f'v{field_num}'] = after_date
            field_num += 1
        
        if before_date:
            params[f'f{field_num}'] = 'creation_ts'
            params[f'o{field_num}'] = 'lessthan'
            params[f'v{field_num}'] = before_date
        
        try:
            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json().get('bugs', [])
        except requests.RequestException as e:
            print(f"Error searching bugs: {e}")
            return []
    
    def _extract_socorro_links(self, bug: Dict) -> List[Dict]:
        socorro_data = []
        seen_crash_ids = set()
        
        def extract_from_text(text: str) -> List[Dict]:
            extracted = []
            if not text:
                return extracted
            
            markdown_pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
            for markdown_match in re.finditer(markdown_pattern, text):
                markdown_url = markdown_match.group(2)
                text = text.replace(markdown_match.group(0), markdown_url)
            
            for pattern in self.ACTIVE_SOCORRO_PATTERNS:
                try:
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        try:
                            full_url = match.group(0)
                            crash_id = match.group(1) if match.lastindex >= 1 else full_url
                            if not crash_id:
                                continue
                            crash_id = crash_id.strip().lower()
                            if not crash_id or crash_id in seen_crash_ids:
                                continue
                            if not re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', crash_id):
                                continue
                            seen_crash_ids.add(crash_id)
                            extracted.append({'url': full_url.strip(), 'crash_id': crash_id})
                        except (IndexError, AttributeError):
                            continue
                except re.error:
                    continue
            return extracted
        
        comments = bug.get('comments', [])
        if comments and len(comments) > 0:
            comment = comments[0]
            comment_text = comment.get('text', '')
            if comment_text:
                extracted_links = extract_from_text(comment_text)
                for link in extracted_links:
                    link['comment_index'] = 0
                    link['comment_text'] = comment_text
                    socorro_data.append(link)
        return socorro_data
    
    def _contains_stack_trace(self, text: str) -> Tuple[bool, List[str]]:
        if not text:
            return False, []
        matched_patterns = []
        for i, compiled_pattern in enumerate(self._compiled_stack_patterns):
            if compiled_pattern.search(text):
                matched_patterns.append(self.STACK_TRACE_PATTERNS[i])
        return len(matched_patterns) > 0, matched_patterns
    
    def _parse_stack_frames(self, text: str) -> List[Dict]:
        frames = []
        code_block_pattern = r'```(?:\w*\n)?(.*?)```'
        code_blocks = re.findall(code_block_pattern, text, re.DOTALL)
        text_to_parse = '\n'.join(code_blocks) if code_blocks else text
        
        for line in text_to_parse.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            frame = None
            
            match = re.match(
                r'#(\d+)\s+(?:0x[0-9a-fA-F]+\s+)?(?:in\s+)?'
                r'([^\s(]+)(?:\s*\([^)]*\))?\s*(?:at\s+([^\s:]+):(\d+))?',
                line
            )
            if match:
                frame = {
                    'frame_index': int(match.group(1)),
                    'function': match.group(2),
                    'file': match.group(3) or '',
                    'line': int(match.group(4)) if match.group(4) else None,
                    'module': '',
                    'raw': line
                }
            
            if not frame:
                match = re.match(r'^(\d+)\s{2,}([\w.]+)\s{2,}(.+?)(?:\s{2,}([\w/.:-]+))?$', line)
                if match:
                    module = match.group(2)
                    if self.VALID_MODULE_PATTERN.match(module):
                        source = match.group(4) or ''
                        file_path, line_num = '', None
                        if source and ':' in source:
                            parts = source.rsplit(':', 1)
                            file_path = parts[0]
                            try:
                                line_num = int(parts[1])
                            except ValueError:
                                pass
                        frame = {
                            'frame_index': int(match.group(1)),
                            'module': module,
                            'function': match.group(3).strip(),
                            'file': file_path,
                            'line': line_num,
                            'raw': line
                        }
            
            if not frame:
                match = re.match(r'([\w.]+)!([^\s+]+)(?:\+(0x[0-9a-fA-F]+))?', line)
                if match:
                    module = match.group(1)
                    if self.VALID_MODULE_PATTERN.match(module):
                        frame = {
                            'frame_index': len(frames),
                            'module': module,
                            'function': match.group(2),
                            'offset': match.group(3) or '',
                            'file': '',
                            'line': None,
                            'raw': line
                        }
            
            if frame:
                frames.append(frame)
        return frames
    
    def _extract_stack_traces(self, bug: Dict) -> List[Dict]:
        stack_traces = []
        for comment in bug.get('comments', []):
            text = comment.get('text', '')
            has_stack, matched_patterns = self._contains_stack_trace(text)
            if has_stack:
                parsed_frames = self._parse_stack_frames(text)
                if len(parsed_frames) > 0:
                    stack_traces.append({
                        'comment_id': comment.get('id'),
                        'comment_count': comment.get('count'),
                        'creator': comment.get('creator'),
                        'creation_time': comment.get('creation_time'),
                        'raw_text': text,
                        'matched_patterns': matched_patterns,
                        'parsed_frames': parsed_frames,
                        'frame_count': len(parsed_frames)
                    })
        return stack_traces
    
    def _parse_crash_signatures(self, signature_str: str) -> List[str]:
        if not signature_str:
            return []
        pattern = r'\[@\s*([^\]]+)\s*\]'
        matches = re.findall(pattern, signature_str)
        return [sig.strip() for sig in matches if sig.strip()]
    
    def _process_bug(self, bug: Dict) -> Dict:
        crash_sig = bug.get('cf_crash_signature', '')
        signatures = self._parse_crash_signatures(crash_sig)
        socorro_links = self._extract_socorro_links(bug)
        stack_traces = self._extract_stack_traces(bug)
        crash_ids = [link['crash_id'] for link in socorro_links if 'crash_id' in link]
        creation_time = bug.get('creation_time', '')
        is_recent = self._is_recent_bug(creation_time)
        
        return {
            'bug_id': str(bug['id']),
            'summary': bug.get('summary', ''),
            'status': bug.get('status', ''),
            'resolution': bug.get('resolution', ''),
            'product': bug.get('product', ''),
            'component': bug.get('component', ''),
            'version': bug.get('version', ''),
            'severity': bug.get('severity', ''),
            'priority': bug.get('priority', ''),
            'creation_time': creation_time,
            'last_change_time': bug.get('last_change_time', ''),
            'is_recent': is_recent,
            'crash_signature_raw': crash_sig,
            'crash_signatures': signatures,
            'keywords': bug.get('keywords', []),
            'regressed_by': bug.get('regressed_by', []),
            'regressions': bug.get('regressions', []),
            'bugzilla_url': f"https://bugzilla.mozilla.org/show_bug.cgi?id={bug['id']}",
            'socorro_links': socorro_links,
            'socorro_crash_ids': crash_ids,
            'has_socorro_link': len(socorro_links) > 0,
            'has_direct_crash_id': len(crash_ids) > 0,
            'stack_traces': stack_traces,
            'has_stack_traces': len(stack_traces) > 0,
            'stack_trace_count': len(stack_traces),
            'total_parsed_frames': sum(st['frame_count'] for st in stack_traces)
        }
    
    def extract_all_bugs(self, products: List[str] = None) -> Tuple[Dict, Dict, Dict]:
        """Extract ALL crash bugs - NO LIMITS"""
        if products is None:
            products = ['Firefox', 'Core']
        
        print("=" * 80)
        print("STEP 1: TIME-BASED CRASH BUG EXTRACTION (NO LIMITS)")
        print("=" * 80)
        print(f"\nStrategy:")
        print(f"  RECENT (past {self.recent_months} months, after {self.cutoff_date_str}):")
        print(f"     Require BOTH stack trace AND Socorro link")
        print(f"     Max bugs: ALL (no limit)")
        print(f"  OLDER (before {self.cutoff_date_str}):")
        print(f"     Require stack trace only (Socorro likely expired)")
        print(f"     Max bugs: ALL (no limit)")
        print(f"\n  Products: {products}")
        print(f"  Resolution: FIXED")
        print()
        
        recent_bugs = {}
        older_bugs = {}
        
        stats = {
            'total_examined': 0,
            'recent': {
                'examined': 0,
                'with_stack_and_socorro': 0,
                'with_stack_no_socorro': 0,
                'without_stack': 0,
                'by_product': defaultdict(int)
            },
            'older': {
                'examined': 0,
                'with_stack': 0,
                'without_stack': 0,
                'by_product': defaultdict(int)
            }
        }
        
        # ========== PHASE 1: RECENT BUGS ==========
        print(f"\n{'='*80}")
        print(f"PHASE 1: RECENT BUGS (after {self.cutoff_date_str})")
        print('='*80)
        
        for product in products:
            print(f"\n  Processing: {product}")
            offset = 0
            batch_size = 500
            
            while True:
                print(f"    Fetching bugs {offset} to {offset + batch_size}...")
                
                bugs = self.search_crash_bugs_by_date(
                    product=product,
                    after_date=self.cutoff_date_str,
                    limit=batch_size,
                    offset=offset
                )
                
                if not bugs:
                    print("    No more bugs found")
                    break
                
                print(f"    Retrieved {len(bugs)} bugs")
                
                for bug in bugs:
                    stats['total_examined'] += 1
                    stats['recent']['examined'] += 1
                    
                    processed = self._process_bug(bug)
                    bug_id = processed['bug_id']
                    
                    if not processed['has_stack_traces']:
                        stats['recent']['without_stack'] += 1
                        continue
                    
                    if not processed['has_socorro_link']:
                        stats['recent']['with_stack_no_socorro'] += 1
                        continue
                    
                    recent_bugs[bug_id] = processed
                    stats['recent']['with_stack_and_socorro'] += 1
                    stats['recent']['by_product'][product] += 1
                
                print(f"    Recent bugs collected so far: {len(recent_bugs)}")
                
                offset += batch_size
                time.sleep(self.rate_limit_delay)
                
                if len(bugs) < batch_size:
                    break
        
        print(f"\n  PHASE 1 COMPLETE: {len(recent_bugs)} recent bugs")
        
        # ========== PHASE 2: OLDER BUGS ==========
        print(f"\n{'='*80}")
        print(f"PHASE 2: OLDER BUGS (before {self.cutoff_date_str})")
        print('='*80)
        
        for product in products:
            print(f"\n  Processing: {product}")
            offset = 0
            batch_size = 500
            
            while True:
                print(f"    Fetching bugs {offset} to {offset + batch_size}...")
                
                bugs = self.search_crash_bugs_by_date(
                    product=product,
                    before_date=self.cutoff_date_str,
                    limit=batch_size,
                    offset=offset
                )
                
                if not bugs:
                    print("    No more bugs found")
                    break
                
                print(f"    Retrieved {len(bugs)} bugs")
                
                for bug in bugs:
                    stats['total_examined'] += 1
                    stats['older']['examined'] += 1
                    
                    processed = self._process_bug(bug)
                    bug_id = processed['bug_id']
                    
                    if not processed['has_stack_traces']:
                        stats['older']['without_stack'] += 1
                        continue
                    
                    older_bugs[bug_id] = processed
                    stats['older']['with_stack'] += 1
                    stats['older']['by_product'][product] += 1
                
                print(f"    Older bugs collected so far: {len(older_bugs)}")
                
                offset += batch_size
                time.sleep(self.rate_limit_delay)
                
                if len(bugs) < batch_size:
                    break
        
        print(f"\n  PHASE 2 COMPLETE: {len(older_bugs)} older bugs")
        
        # Summary
        print(f"\n{'='*80}")
        print("EXTRACTION COMPLETE")
        print('='*80)
        print(f"  Total examined: {stats['total_examined']}")
        print(f"  Recent with stack+Socorro: {stats['recent']['with_stack_and_socorro']}")
        print(f"  Older with stack: {stats['older']['with_stack']}")
        
        return recent_bugs, older_bugs, stats
    
    def save_results(self, recent_bugs: Dict, older_bugs: Dict, stats: Dict) -> Dict:
        """Save results to separate directories"""
        
        # Clear old files first
        recent_dir = self.output_base / "recent_with_socorro" / "bugs"
        older_dir = self.output_base / "older_stack_only" / "bugs"
        
        for d in [recent_dir, older_dir]:
            if d.exists():
                for f in d.glob("bug_*.json"):
                    f.unlink()
        
        # Save RECENT bugs
        print(f"\n{'='*80}")
        print("SAVING: Recent bugs")
        print('='*80)
        
        recent_dir.mkdir(parents=True, exist_ok=True)
        for bug_id, bug_data in recent_bugs.items():
            with open(recent_dir / f"bug_{bug_id}.json", 'w') as f:
                json.dump(bug_data, f, indent=2)
        print(f"  Saved {len(recent_bugs)} bug files")
        
        metadata = self._create_metadata(recent_bugs, 'recent_with_socorro')
        with open(self.output_base / "recent_with_socorro" / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        # Save OLDER bugs
        print(f"\n{'='*80}")
        print("SAVING: Older bugs")
        print('='*80)
        
        older_dir.mkdir(parents=True, exist_ok=True)
        for bug_id, bug_data in older_bugs.items():
            with open(older_dir / f"bug_{bug_id}.json", 'w') as f:
                json.dump(bug_data, f, indent=2)
        print(f"  Saved {len(older_bugs)} bug files")
        
        metadata = self._create_metadata(older_bugs, 'older_stack_only')
        with open(self.output_base / "older_stack_only" / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)
        
        # Save summary
        summary = {
            'extraction_timestamp': datetime.now().isoformat(),
            'cutoff_date': self.cutoff_date_str,
            'recent_months': self.recent_months,
            'statistics': {
                'total_examined': stats['total_examined'],
                'recent': dict(stats['recent']),
                'older': dict(stats['older'])
            },
            'recent_with_socorro': {
                'count': len(recent_bugs),
                'bug_ids': sorted(list(recent_bugs.keys()))
            },
            'older_stack_only': {
                'count': len(older_bugs),
                'bug_ids': sorted(list(older_bugs.keys()))
            }
        }
        
        with open(self.output_base / "extraction_summary.json", 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"\n✓ Summary saved")
        
        return summary
    
    def _create_metadata(self, bugs: Dict, category: str) -> Dict:
        by_product = defaultdict(int)
        by_component = defaultdict(int)
        total_frames = 0
        
        for bug_id, bug in bugs.items():
            by_product[bug['product']] += 1
            by_component[f"{bug['product']}::{bug['component']}"] += 1
            total_frames += bug['total_parsed_frames']
        
        return {
            'category': category,
            'extraction_timestamp': datetime.now().isoformat(),
            'summary': {
                'total_bugs': len(bugs),
                'total_parsed_frames': total_frames,
                'avg_frames_per_bug': total_frames / len(bugs) if bugs else 0
            },
            'by_product': dict(sorted(by_product.items(), key=lambda x: -x[1])),
            'by_component': dict(sorted(by_component.items(), key=lambda x: -x[1])[:20]),
            'bug_ids': sorted(list(bugs.keys()))
        }


def main():
    """Main execution - NO LIMITS"""
    extractor = BugzillaCrashExtractor(recent_months=6)
    
    # Extract ALL bugs - no max_recent or max_older limits
    recent_bugs, older_bugs, stats = extractor.extract_all_bugs(
        products=['Firefox', 'Core']
    )
    
    summary = extractor.save_results(recent_bugs, older_bugs, stats)
    
    print("\n" + "=" * 80)
    print("✓ STEP 1 COMPLETE")
    print("=" * 80)
    print(f"\nResults:")
    print(f"  Recent bugs (stack + Socorro): {len(recent_bugs)}")
    print(f"  Older bugs (stack only): {len(older_bugs)}")
    print(f"  Total: {len(recent_bugs) + len(older_bugs)}")
    print(f"\nOutput: {extractor.output_base}")


if __name__ == "__main__":
    main()