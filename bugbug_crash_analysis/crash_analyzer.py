# Step3_crash_analyzer.py - Crash statistics analysis with direct links

import urllib.parse
import json
import os
from datetime import datetime, timedelta
import re
from Step1_config import (MIN_CRASH_VOLUME, CRASH_ANALYSIS_DAYS, 
                   CRASH_CACHE_FILE, CACHE_DIR)

class CrashAnalyzer:
    def __init__(self):
        self.crash_cache = {}
        self._load_cache()
        self._ensure_cache_dir()
    
    def _ensure_cache_dir(self):
        """Create cache directory if it doesn't exist"""
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
    
    def clean_signature(self, signature):
        """Clean crash signature - USE ONLY FIRST LINE"""
        # Remove BugBug formatting: "[@ signature]" -> "signature"
        cleaned = re.sub(r'\[@\s*([^\]]+)\]', r'\1', signature)
        
        # Split by newlines and take ONLY the first signature
        lines = cleaned.split('\r\n')
        if lines:
            cleaned = lines[0].split('\n')[0]
        
        cleaned = cleaned.strip()
        return cleaned
    
    def generate_crash_stats_url(self, signature, days=None):
        """Generate direct URL to Mozilla Crash Stats for this signature"""
        if days is None:
            days = CRASH_ANALYSIS_DAYS
        
        clean_sig = self.clean_signature(signature)
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Format dates as ISO with timezone (as Mozilla expects)
        start_iso = start_date.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        end_iso = end_date.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # URL encode the signature
        encoded_sig = urllib.parse.quote(clean_sig)
        
        # Build the crash-stats URL using the /signature/ endpoint
        base_url = "https://crash-stats.mozilla.org/signature/"
        url = (f"{base_url}?signature={encoded_sig}"
               f"&date=>={urllib.parse.quote(start_iso)}"
               f"&date=<{urllib.parse.quote(end_iso)}"
               f"&_columns=date&_columns=product&_columns=version"
               f"&_columns=build_id&_columns=platform"
               f"&_sort=-date&page=1")
        
        return url
    
    def get_crash_volume(self, signature, days=None):
        """Get crash statistics - returns links since API is blocked"""
        if days is None:
            days = CRASH_ANALYSIS_DAYS
        
        clean_sig = self.clean_signature(signature)
        
        # Check cache first
        cache_key = f"{clean_sig}_{days}"
        if cache_key in self.crash_cache:
            return self.crash_cache[cache_key]
        
        # Generate crash stats URL
        crash_url = self.generate_crash_stats_url(signature, days)
        
        # Since API is blocked, we'll create a placeholder result
        # Users can click the URL to see actual crash volumes
        crash_stats = {
            'total': 0,  # Unknown - API blocked
            'signature': clean_sig,
            'original_signature': signature,
            'days_analyzed': days,
            'crash_stats_url': crash_url,
            'note': 'API unavailable - click URL to view crashes',
            'last_updated': datetime.now().isoformat()
        }
        
        # Cache the result
        self.crash_cache[cache_key] = crash_stats
        self._save_cache()
        
        return crash_stats
    
    def enrich_bugs_with_crash_data(self, crash_bugs):
        """Add crash URLs to bug information"""
        print(f"Generating crash stats links for {len(crash_bugs)} bugs...")
        print("Note: Mozilla Crash Stats API requires authentication.")
        print("Links will be provided for manual verification.\n")
        
        enriched_bugs = []
        
        for i, bug in enumerate(crash_bugs):
            if (i + 1) % 100 == 0:
                print(f"Processing bug {i+1}/{len(crash_bugs)}...")
            
            signature = bug['signature']
            crash_stats = self.get_crash_volume(signature)
            
            # Include ALL bugs with crash stats links
            # User can click links to see actual volumes
            bug_with_crashes = bug.copy()
            bug_with_crashes['crash_stats'] = crash_stats
            bug_with_crashes['crash_stats_url'] = crash_stats['crash_stats_url']
            
            # Set a nominal volume for sorting purposes
            # Higher priority bugs get higher nominal scores
            bug_with_crashes['crash_volume'] = self._estimate_priority(bug)
            
            enriched_bugs.append(bug_with_crashes)
        
        # Sort by estimated priority
        enriched_bugs.sort(key=lambda x: x['crash_volume'], reverse=True)
        
        print(f"Generated crash stats links for all {len(enriched_bugs)} bugs")
        print("Click URLs in reports to view actual crash volumes on Mozilla Crash Stats")
        
        return enriched_bugs
    
    def _estimate_priority(self, bug):
        """Estimate bug priority based on metadata (since API is unavailable)"""
        score = 100  # Base score
        
        # Check keywords for priority indicators
        keywords = bug.get('keywords', [])
        if isinstance(keywords, str):
            keywords = keywords.lower()
        else:
            keywords = ' '.join(keywords).lower() if keywords else ''
        
        summary = bug.get('summary', '').lower()
        
        # High priority indicators
        if 'crash' in keywords or 'crash' in summary:
            score += 50
        if 'topcrash' in keywords:
            score += 100
        if 'regression' in keywords:
            score += 30
        if 'startup' in summary:
            score += 40
        
        # Check resolution date (more recent = higher priority)
        last_change = bug.get('last_change', '')
        if last_change:
            try:
                change_date = datetime.fromisoformat(last_change.replace('Z', '+00:00'))
                days_since = (datetime.now(change_date.tzinfo) - change_date).days
                # Recent bugs get bonus points
                if days_since < 30:
                    score += 50
                elif days_since < 90:
                    score += 30
                elif days_since < 180:
                    score += 10
            except:
                pass
        
        return score
    
    def analyze_crash_trends(self, signature, days=None):
        """Display crash information with URL"""
        if days is None:
            days = CRASH_ANALYSIS_DAYS
            
        crash_stats = self.get_crash_volume(signature, days)
        
        print(f"\n--- CRASH INFORMATION ---")
        print(f"Signature: {crash_stats['signature'][:80]}")
        print(f"Analysis Period: {days} days")
        print(f"View crashes at: {crash_stats['crash_stats_url']}")
        print(f"Note: Click URL to see actual crash volumes and details")
        
        return crash_stats
    
    def _load_cache(self):
        """Load crash cache from file"""
        if os.path.exists(CRASH_CACHE_FILE):
            try:
                with open(CRASH_CACHE_FILE, 'r') as f:
                    self.crash_cache = json.load(f)
            except:
                self.crash_cache = {}
    
    def _save_cache(self):
        """Save crash cache to file"""
        try:
            with open(CRASH_CACHE_FILE, 'w') as f:
                json.dump(self.crash_cache, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save crash cache: {e}")