#!/usr/bin/env python3
"""
================================================================================
STEP 2: SOCORRO STACK TRACE EXTRACTION
================================================================================

PURPOSE:
--------
Extract full stack traces from Socorro for recent bugs. Use Socorro's full 
stack when available, fallback to Bugzilla when Socorro is inaccessible.

INPUT:
------
step1_bugzilla_bugs_extraction/recent_with_socorro/bugs/*.json

OUTPUT:
-------
step2_socorro_extraction/
├── full_stack_socorro/
│   └── bugs/bug_<ID>.json
├── bugzilla_stack_only/
│   └── bugs/bug_<ID>.json
└── extraction_summary.json
"""

import requests
import json
import time
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from pathlib import Path
from bs4 import BeautifulSoup


class SocorroStackExtractor:
    """Extract full stack traces from Socorro crash reports"""
    
    VALID_MODULE_PATTERN = re.compile(
        r'^(xul\.dll|xul\.so|libxul\.so|nss3\.dll|libnss3\.so|'
        r'mozglue\.dll|libmozglue\.so|kernel32\.dll|ntdll\.dll|'
        r'ucrtbase\.dll|libpthread\.so|libc\.so|libm\.so|'
        r'[\w]+\.dll|[\w]+\.so|[\w]+\.dylib)$',
        re.IGNORECASE
    )
    
    def __init__(self, rate_limit_delay: float = 1.0):
        self.rate_limit_delay = rate_limit_delay
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla-Crash-Research/3.0 (Academic Research)',
            'Accept': 'text/html,application/xhtml+xml',
        })
        
        self.script_dir = Path(__file__).resolve().parent
        self.step1_output = self.script_dir / "outputs" / "step1_bugzilla_bugs_extraction"
        self.output_base = self.script_dir / "outputs" / "step2_socorro_extraction"
        self.output_base.mkdir(parents=True, exist_ok=True)
        
        print(f"Step 1 input: {self.step1_output}")
        print(f"Step 2 output: {self.output_base}")
        print()
    
    def load_step1_recent_bugs(self) -> Dict[str, Dict]:
        """Load recent bugs from Step 1 output"""
        recent_dir = self.step1_output / "recent_with_socorro" / "bugs"
        
        if not recent_dir.exists():
            print(f"ERROR: Step 1 output not found: {recent_dir}")
            return {}
        
        bugs = {}
        for filepath in recent_dir.glob("bug_*.json"):
            try:
                with open(filepath, 'r') as f:
                    bug = json.load(f)
                    bugs[bug['bug_id']] = bug
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  Warning: Failed to load {filepath}: {e}")
        
        print(f"Loaded {len(bugs)} bugs from Step 1")
        return bugs
    
    def fetch_socorro_report(self, crash_id: str) -> Optional[str]:
        """Fetch Socorro crash report HTML page"""
        url = f"https://crash-stats.mozilla.org/report/index/{crash_id}"
        try:
            response = self.session.get(url, timeout=30)
            if response.status_code == 200:
                return response.text
            print(f"      Socorro returned status {response.status_code}")
        except requests.RequestException as e:
            print(f"      Request error: {e}")
        return None
    
    def parse_socorro_stack_trace(self, html: str) -> Optional[Dict]:
        """Parse stack trace from Socorro crash report HTML"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            result = {
                'signature': '',
                'crash_reason': '',
                'moz_crash_reason': '',
                'crashing_thread': {},
                'frames': []
            }
            
            # Extract metadata
            for label in ['Signature', 'Crash Reason', 'MOZ_CRASH Reason']:
                row = soup.find('td', string=re.compile(f'^{label}$', re.I))
                if row:
                    value = row.find_next_sibling('td')
                    if value:
                        key = label.lower().replace(' ', '_').replace('moz_crash_reason', 'moz_crash_reason')
                        result[key] = value.get_text(strip=True)
            
            # Find crashing thread
            thread_match = re.search(r'Crashing Thread\s*\((\d+)\)(?:,\s*Name:\s*([^\n<]+))?', html)
            if thread_match:
                result['crashing_thread'] = {
                    'index': int(thread_match.group(1)),
                    'name': (thread_match.group(2) or '').strip()
                }
            
            # Find stack trace table
            frames = []
            for table in soup.find_all('table'):
                first_row = table.find('tr')
                if first_row:
                    headers = [h.get_text(strip=True).lower() for h in first_row.find_all(['th', 'td'])]
                    if 'frame' in headers and 'signature' in headers:
                        for row in table.find_all('tr')[1:]:
                            cells = row.find_all('td')
                            if len(cells) >= 3:
                                try:
                                    frame_idx = int(cells[0].get_text(strip=True))
                                except ValueError:
                                    continue
                                
                                source = cells[3].get_text(strip=True) if len(cells) > 3 else ''
                                file_path, line_num = '', None
                                if ':' in source and source.split(':')[-1].isdigit():
                                    parts = source.rsplit(':', 1)
                                    file_path, line_num = parts[0], int(parts[1])
                                
                                frames.append({
                                    'frame_index': frame_idx,
                                    'module': cells[1].get_text(strip=True),
                                    'function': cells[2].get_text(strip=True),
                                    'source': source,
                                    'file': file_path,
                                    'line': line_num,
                                    'trust': cells[4].get_text(strip=True) if len(cells) > 4 else ''
                                })
                        break
            
            if not frames:
                frames = self._parse_stack_from_text(html)
            
            result['frames'] = frames
            result['frame_count'] = len(frames)
            return result if frames else None
            
        except Exception as e:
            print(f"      Parse error: {e}")
            return None
    
    def _parse_stack_from_text(self, html: str) -> List[Dict]:
        """Fallback: Parse stack trace from raw text"""
        frames = []
        text = re.sub(r'<[^>]+>', '\n', html)
        
        for line in text.split('\n'):
            parts = re.split(r'\t+|\s{2,}', line.strip())
            if len(parts) >= 3:
                try:
                    frame_idx = int(parts[0])
                except ValueError:
                    continue
                
                source = parts[3] if len(parts) > 3 else ''
                file_path, line_num = '', None
                if ':' in source and source.split(':')[-1].isdigit():
                    p = source.rsplit(':', 1)
                    file_path, line_num = p[0], int(p[1])
                
                frames.append({
                    'frame_index': frame_idx,
                    'module': parts[1],
                    'function': parts[2],
                    'source': source,
                    'file': file_path,
                    'line': line_num,
                    'trust': parts[4] if len(parts) > 4 else ''
                })
        return frames
    
    def _extract_bugzilla_frames(self, bug: Dict) -> List[Dict]:
        """Extract and filter valid frames from Bugzilla stack traces"""
        for st in bug.get('stack_traces', []):
            if st.get('parsed_frames'):
                frames = []
                for f in st['parsed_frames']:
                    module = f.get('module', '')
                    func = f.get('function', '')
                    if self.VALID_MODULE_PATTERN.match(module):
                        frames.append(f)
                    elif '::' in func or func.startswith('ns') or func.startswith('NS_'):
                        frames.append(f)
                
                frames.sort(key=lambda x: x.get('frame_index', 999))
                seen = set()
                unique = []
                for f in frames:
                    idx = f.get('frame_index')
                    if idx not in seen:
                        seen.add(idx)
                        unique.append(f)
                return unique
        return []
    
    def process_bug(self, bug: Dict) -> Tuple[str, Dict]:
        """Process a single bug - extract Socorro or fallback to Bugzilla"""
        bug_id = bug['bug_id']
        crash_ids = bug.get('socorro_crash_ids', [])
        bz_frames = self._extract_bugzilla_frames(bug)
        
        processed = {
            'bug_id': bug_id,
            'summary': bug.get('summary', ''),
            'product': bug.get('product', ''),
            'component': bug.get('component', ''),
            'creation_time': bug.get('creation_time', ''),
            'crash_signatures': bug.get('crash_signatures', []),
            'bugzilla_url': bug.get('bugzilla_url', ''),
            'socorro_crash_ids': crash_ids,
        }
        
        if not crash_ids:
            processed['stack_source'] = 'bugzilla'
            processed['fallback_reason'] = 'no_socorro_crash_id'
            processed['stack_trace'] = {
                'source': 'bugzilla',
                'frames': bz_frames,
                'frame_count': len(bz_frames)
            }
            return 'bugzilla_stack_only', processed
        
        # Try to fetch Socorro stack trace
        socorro_data = None
        successful_crash_id = None
        
        for crash_id in crash_ids:
            print(f"    Fetching Socorro: {crash_id[:24]}...")
            
            html = self.fetch_socorro_report(crash_id)
            if not html:
                print(f"      Failed to fetch crash report")
                continue
            
            parsed = self.parse_socorro_stack_trace(html)
            if not parsed or not parsed.get('frames'):
                print(f"      No frames parsed from crash report")
                continue
            
            print(f"      ✓ Extracted {len(parsed['frames'])} frames from Socorro")
            socorro_data = parsed
            successful_crash_id = crash_id
            break  # Successfully got Socorro data, stop trying
        
        # Use Socorro if we got it, otherwise fallback to Bugzilla
        if socorro_data and successful_crash_id:
            processed['stack_source'] = 'socorro'
            processed['crash_id'] = successful_crash_id
            processed['socorro_metadata'] = {
                'signature': socorro_data.get('signature', ''),
                'crash_reason': socorro_data.get('crash_reason', ''),
                'moz_crash_reason': socorro_data.get('moz_crash_reason', ''),
                'crashing_thread': socorro_data.get('crashing_thread', {})
            }
            processed['stack_trace'] = {
                'source': 'socorro',
                'crash_id': successful_crash_id,
                'frames': socorro_data['frames'],
                'frame_count': len(socorro_data['frames'])
            }
            return 'full_stack_socorro', processed
        else:
            processed['stack_source'] = 'bugzilla'
            processed['fallback_reason'] = 'socorro_fetch_failed'
            processed['stack_trace'] = {
                'source': 'bugzilla',
                'frames': bz_frames,
                'frame_count': len(bz_frames)
            }
            return 'bugzilla_stack_only', processed
    
    def extract_all_bugs(self, max_bugs: int = None) -> Tuple[Dict, Dict, Dict]:
        """Extract stack traces for all bugs from Step 1"""
        print("=" * 80)
        print("STEP 2: SOCORRO STACK TRACE EXTRACTION")
        print("=" * 80)
        
        bugs = self.load_step1_recent_bugs()
        if not bugs:
            return {}, {}, {}
        
        if max_bugs:
            bugs = dict(list(bugs.items())[:max_bugs])
        
        full_socorro = {}
        bugzilla_only = {}
        stats = {
            'total': 0,
            'socorro': 0,
            'bugzilla': 0,
            'fallback_reasons': defaultdict(int)
        }
        
        for i, (bug_id, bug) in enumerate(bugs.items(), 1):
            print(f"\n[{i}/{len(bugs)}] Bug {bug_id}")
            
            category, processed = self.process_bug(bug)
            stats['total'] += 1
            
            if category == 'full_stack_socorro':
                full_socorro[bug_id] = processed
                stats['socorro'] += 1
                print(f"  ✓ SOCORRO ({processed['stack_trace']['frame_count']} frames)")
            else:
                bugzilla_only[bug_id] = processed
                stats['bugzilla'] += 1
                stats['fallback_reasons'][processed.get('fallback_reason', 'unknown')] += 1
                print(f"  → BUGZILLA ({processed['stack_trace']['frame_count']} frames)")
            
            time.sleep(self.rate_limit_delay)
        
        print(f"\n{'='*80}")
        print(f"COMPLETE: {stats['socorro']} Socorro, {stats['bugzilla']} Bugzilla")
        print(f"Fallback reasons: {dict(stats['fallback_reasons'])}")
        
        return full_socorro, bugzilla_only, stats
    
    def save_results(self, full_socorro: Dict, bugzilla_only: Dict, stats: Dict):
        """Save extraction results"""
        for bugs, name in [(full_socorro, 'full_stack_socorro'), (bugzilla_only, 'bugzilla_stack_only')]:
            out_dir = self.output_base / name / "bugs"
            out_dir.mkdir(parents=True, exist_ok=True)
            for bug_id, bug in bugs.items():
                with open(out_dir / f"bug_{bug_id}.json", 'w') as f:
                    json.dump(bug, f, indent=2)
            print(f"Saved {len(bugs)} bugs to {name}/")
        
        summary = {
            'timestamp': datetime.now().isoformat(),
            'stats': dict(stats),
            'full_stack_socorro': len(full_socorro),
            'bugzilla_stack_only': len(bugzilla_only)
        }
        with open(self.output_base / "extraction_summary.json", 'w') as f:
            json.dump(summary, f, indent=2, default=str)


def main():
    extractor = SocorroStackExtractor(rate_limit_delay=1.0)
    
    full_socorro, bugzilla_only, stats = extractor.extract_all_bugs(max_bugs=100)
    extractor.save_results(full_socorro, bugzilla_only, stats)
    
    print(f"\n✓ STEP 2 COMPLETE")
    print(f"  Socorro: {len(full_socorro)} | Bugzilla: {len(bugzilla_only)}")


if __name__ == "__main__":
    main()