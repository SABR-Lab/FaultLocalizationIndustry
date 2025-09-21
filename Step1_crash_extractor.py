#!/usr/bin/env python3
"""
Step 1: Test crash filtering with a specific signature
Much simpler and more reliable for initial testing
Enhanced with file and module information extraction
"""

import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class FunctionInfo:
    function_name: str
    file_name: str
    module_name: str

@dataclass
class CrashInfo:
    crash_id: str
    signature: str
    date: str
    product_channel: str
    bug_report_url: Optional[str]
    stack_trace: List[str]
    all_functions: List[FunctionInfo]  # Changed to FunctionInfo objects
    crash_report_url: str
    api_url: str

class Step1SingleSignatureTest:
    def __init__(self):
        self.crash_stats_api = "https://crash-stats.mozilla.org/api/"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla Crash Analysis Test 1.0'
        })
    
    def test_specific_signature_longterm(self, signature: str, years_back: int = 5, sample_strategy: str = "monthly", dedup_strategy: str = "stack_trace") -> List[CrashInfo]:
        """
        Extract crashes over multiple years using intelligent sampling and deduplication
        
        Args:
            signature: Exact signature to search for
            years_back: How many years back to search (default: 5)
            sample_strategy: 'monthly', 'quarterly', or 'random'
            dedup_strategy: 'stack_trace', 'top_functions', 'comprehensive', etc.
        
        Returns:
            List of unique CrashInfo objects sampled across the time period
        """
        print(f"  LONG-TERM CRASH EXTRACTION WITH DEDUPLICATION")
        print(f"Signature: {signature}")
        print(f"Time period: {years_back} years back")
        print(f"Sampling strategy: {sample_strategy}")
        print(f"Deduplication strategy: {dedup_strategy}")
        print("=" * 70)
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years_back * 365)
        
        # Generate sampling periods based on strategy
        sampling_periods = self._generate_sampling_periods(start_date, end_date, sample_strategy)
        
        print(f" Generated {len(sampling_periods)} sampling periods")
        print(f" Estimated total crashes to collect: {len(sampling_periods) * 4 * 50} (periods × channels × avg_per_period)")
        
        all_crashes = []
        channels = ['nightly', 'release', 'esr', 'beta']
        
        for i, (period_start, period_end) in enumerate(sampling_periods):
            period_name = period_start.strftime("%Y-%m")
            print(f"\n Period {i+1}/{len(sampling_periods)}: {period_name}")
            print(f"    {period_start.strftime('%Y-%m-%d')} to {period_end.strftime('%Y-%m-%d')}")
            
            period_crashes = []
            for channel in channels:
                crashes = self._get_crashes_for_period(signature, channel, period_start, period_end)
                if crashes:
                    print(f"     {channel}: {len(crashes)} crashes")
                    period_crashes.extend(crashes)
                else:
                    print(f"     {channel}: 0 crashes")
            
            all_crashes.extend(period_crashes)
            print(f"    Period total: {len(period_crashes)} crashes")
            
            # Progress update
            total_collected = len(all_crashes)
            progress = ((i + 1) / len(sampling_periods)) * 100
            print(f"    Overall progress: {progress:.1f}% ({total_collected} crashes collected)")
        
        print(f"\n RAW RESULTS: {len(all_crashes)} crashes over {years_back} years")
        
        # Deduplicate crashes to remove redundant ones
        unique_crashes = self._deduplicate_crashes(all_crashes, dedup_strategy)
        
        # Analyze duplicate patterns
        self._analyze_duplicate_patterns(all_crashes, unique_crashes)
        
        # Show temporal distribution of unique crashes
        self._show_temporal_distribution(unique_crashes, years_back)
        
        print(f"\n FINAL UNIQUE DATASET: {len(unique_crashes)} crashes")
        
        return unique_crashes
        
    def _deduplicate_crashes(self, crashes: List[CrashInfo], dedup_strategy: str = "stack_trace") -> List[CrashInfo]:
        """
        Remove duplicate crashes based on different strategies
        
        Args:
            crashes: List of crashes to deduplicate
            dedup_strategy: How to identify duplicates
                - "crash_id": Remove exact same crash IDs (basic)
                - "stack_trace": Remove crashes with identical stack traces (recommended)
                - "top_functions": Remove crashes with same top 5 functions
                - "signature_only": Remove crashes with same signature (too aggressive)
                - "comprehensive": Combination of multiple factors
        
        Returns:
            Deduplicated list of crashes
        """
        if not crashes:
            return crashes
            
        print(f"\n DEDUPLICATING CRASHES using '{dedup_strategy}' strategy")
        print(f"   Input: {len(crashes)} crashes")
        
        seen_hashes = set()
        unique_crashes = []
        duplicates_found = 0
        
        for crash in crashes:
            # Generate hash based on strategy
            crash_hash = self._generate_crash_hash(crash, dedup_strategy)
            
            if crash_hash not in seen_hashes:
                seen_hashes.add(crash_hash)
                unique_crashes.append(crash)
            else:
                duplicates_found += 1
        
        print(f"   Output: {len(unique_crashes)} unique crashes")
        print(f"   Removed: {duplicates_found} duplicates ({(duplicates_found/len(crashes)*100):.1f}%)")
        
        return unique_crashes

    def _generate_crash_hash(self, crash: CrashInfo, strategy: str) -> str:
        """Generate a hash to identify duplicate crashes"""
        import hashlib
        
        if strategy == "crash_id":
            # Basic - just use crash ID (should always be unique anyway)
            return crash.crash_id
            
        elif strategy == "stack_trace":
            # Hash the complete stack trace
            stack_str = "|".join(crash.stack_trace) if crash.stack_trace else "empty"
            return hashlib.md5(stack_str.encode()).hexdigest()
            
        elif strategy == "top_functions":
            # Hash just the top 5 functions (less strict)
            top_5 = [func.function_name for func in crash.all_functions[:5]] if crash.all_functions else []
            top_str = "|".join(top_5)
            return hashlib.md5(top_str.encode()).hexdigest()
            
        elif strategy == "signature_only":
            # Hash just the signature (very aggressive deduplication)
            return hashlib.md5(crash.signature.encode()).hexdigest()
            
        elif strategy == "comprehensive":
            # Combine multiple factors for robust deduplication
            factors = [
                crash.signature,
                "|".join([func.function_name for func in crash.all_functions[:3]]) if crash.all_functions else "empty",
                crash.product_channel,
                # Note: Don't include date as crashes can repeat over time
            ]
            combined = "||".join(factors)
            return hashlib.md5(combined.encode()).hexdigest()
            
        else:
            # Default to stack trace
            return self._generate_crash_hash(crash, "stack_trace")

    def _analyze_duplicate_patterns(self, original_crashes: List[CrashInfo], unique_crashes: List[CrashInfo]):
        """Analyze what types of duplicates were found"""
        if len(original_crashes) == len(unique_crashes):
            print("   No duplicate patterns to analyze")
            return
            
        print(f"\n DUPLICATE ANALYSIS:")
        
        # Group original crashes by their characteristics
        from collections import defaultdict
        
        # Analyze by stack trace similarity
        stack_groups = defaultdict(list)
        for crash in original_crashes:
            if crash.all_functions:
                # Use top 3 functions as grouping key
                key = "|".join([func.function_name for func in crash.all_functions[:3]])
                stack_groups[key].append(crash)
        
        # Find groups with multiple crashes (duplicates)
        duplicate_groups = {k: v for k, v in stack_groups.items() if len(v) > 1}
        
        if duplicate_groups:
            print(f"   Found {len(duplicate_groups)} groups with duplicates:")
            for i, (pattern, crashes) in enumerate(list(duplicate_groups.items())[:5]):  # Show top 5
                print(f"     Group {i+1}: {len(crashes)} crashes with pattern:")
                print(f"       {pattern[:100]}...")  # Truncate long patterns
        
        # Analyze by channel distribution
        channel_dups = defaultdict(int)
        for crash in original_crashes:
            channel_dups[crash.product_channel] += 1
        
        unique_channel_dups = defaultdict(int) 
        for crash in unique_crashes:
            unique_channel_dups[crash.product_channel] += 1
        
        print(f"\n   Duplicates by channel:")
        for channel in channel_dups:
            removed = channel_dups[channel] - unique_channel_dups.get(channel, 0)
            if removed > 0:
                print(f"     {channel}: {removed} duplicates removed")

    def _generate_sampling_periods(self, start_date: datetime, end_date: datetime, strategy: str) -> List[tuple]:
        """Generate sampling periods based on strategy"""
        periods = []
        
        if strategy == "monthly":
            # Sample 3 consecutive days per month
            current = start_date.replace(day=1)
            while current < end_date:
                # Sample days 10-12 of each month (usually good representation)
                try:
                    sample_start = current.replace(day=10)
                    sample_end = current.replace(day=12)
                    if sample_end <= end_date:
                        periods.append((sample_start, sample_end))
                except ValueError:
                    # Handle months with < 12 days (shouldn't happen, but safety)
                    pass
                
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
        
        elif strategy == "quarterly":
            # Sample 1 week per quarter
            current = start_date.replace(month=1, day=1)
            while current < end_date:
                quarters = [
                    (1, 15),   # Mid-January
                    (4, 15),   # Mid-April  
                    (7, 15),   # Mid-July
                    (10, 15),  # Mid-October
                ]
                
                for month, day in quarters:
                    try:
                        sample_start = current.replace(month=month, day=day)
                        sample_end = sample_start + timedelta(days=7)
                        if sample_start >= start_date and sample_end <= end_date:
                            periods.append((sample_start, sample_end))
                    except ValueError:
                        pass
                
                current = current.replace(year=current.year + 1)
        
        elif strategy == "random":
            # Random 3-day periods throughout the time span
            import random
            total_days = (end_date - start_date).days
            num_samples = min(60, total_days // 30)  # ~2 samples per month, max 60
            
            for _ in range(num_samples):
                random_days = random.randint(0, total_days - 3)
                sample_start = start_date + timedelta(days=random_days)
                sample_end = sample_start + timedelta(days=3)
                periods.append((sample_start, sample_end))
            
            # Sort by date
            periods.sort(key=lambda x: x[0])
        
        return periods

    def _get_crashes_for_period(self, signature: str, channel: str, start_date: datetime, end_date: datetime) -> List[CrashInfo]:
        """Get crashes for a specific time period (optimized for sampling)"""
        crashes = []
        search_url = f"{self.crash_stats_api}SuperSearch/"
        params = {
            'signature': f'={signature}',
            'date': [f'>={start_date.strftime("%Y-%m-%d")}', f'<={end_date.strftime("%Y-%m-%d")}'],  # Array format
            'release_channel': channel,
            '_columns': ['uuid', 'date', 'signature', 'product', 'version', 'release_channel'],
            '_results_number': 50  # Reasonable sample per period
        }
        
        try:
            response = self.session.get(search_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            hits = data.get('hits', [])
            total_available = data.get('total', 0)
            
            # Process crashes efficiently (sample only some for full details)
            sample_size = min(10, len(hits))  # Max 10 detailed crashes per period/channel
            for i, hit in enumerate(hits[:sample_size]):
                crash_id = hit['uuid']
                
                crash_detail = self._get_crash_details(crash_id, channel)
                if crash_detail:
                    crashes.append(crash_detail)
                    
        except Exception as e:
            print(f"       Error searching {channel}: {e}")
            
        return crashes

    def _show_temporal_distribution(self, crashes: List[CrashInfo], years_back: int):
        """Show how crashes are distributed over time"""
        if not crashes:
            return
            
        print(f"\n TEMPORAL DISTRIBUTION:")
        
        # Group by year and channel
        from collections import defaultdict
        year_channel_counts = defaultdict(lambda: defaultdict(int))
        
        for crash in crashes:
            try:
                # Parse date (format: 2025-08-16T17:26:24+00:00)
                date_str = crash.date.split('T')[0]  # Get just the date part
                year = date_str.split('-')[0]
                channel = crash.product_channel
                year_channel_counts[year][channel] += 1
            except:
                continue
        
        # Display results
        for year in sorted(year_channel_counts.keys()):
            print(f"\n{year}:")
            channels = year_channel_counts[year]
            total_year = sum(channels.values())
            for channel in sorted(channels.keys()):
                count = channels[channel]
                percentage = (count / total_year) * 100 if total_year > 0 else 0
                print(f"  {channel}: {count} crashes ({percentage:.1f}%)")
            print(f"  Total: {total_year} crashes")
    
    def _get_crashes_for_signature(self, signature: str, channel: str, 
                                 start_date: datetime, end_date: datetime) -> List[CrashInfo]:
        """Get ALL crashes for the specific signature using pagination"""
        all_crashes = []
        search_url = f"{self.crash_stats_api}SuperSearch/"
        
        # Start with first page
        offset = 0
        page_size = 1000  # Maximum per request
        
        while True:
            params = {
                'signature': f'={signature}',
                'date': f'>={start_date.strftime("%Y-%m-%d")}',
                'release_channel': channel,
                '_columns': ['uuid', 'date', 'signature', 'product', 'version', 'release_channel'],
                '_results_number': page_size,
                '_results_offset': offset
            }
            
            try:
                response = self.session.get(search_url, params=params)
                response.raise_for_status()
                data = response.json()
                
                hits = data.get('hits', [])
                total = data.get('total', 0)
                
                if not hits:
                    break  # No more results
                
                print(f"     Page {offset//page_size + 1}: Found {len(hits)} crashes (total: {total})")
                
                # Process crashes in batches to avoid overwhelming output
                crashes = []
                for i, hit in enumerate(hits):
                    crash_id = hit['uuid']
                    
                    crash_detail = self._get_crash_details(crash_id, channel)
                    if crash_detail:
                        crashes.append(crash_detail)
                    
                all_crashes.extend(crashes)
                
                # Check if we've got all results
                if len(hits) < page_size or offset + len(hits) >= total:
                    break
                    
                offset += page_size
                
                # Safety limit to prevent infinite loops
                if len(all_crashes) >= 5000:  # Reasonable limit
                    print(f"       Reached safety limit of 5000 crashes for {channel}")
                    break
                    
            except Exception as e:
                print(f"      Error searching {channel} page {offset//page_size + 1}: {e}")
                break
                
        return all_crashes
    
    def get_crash_stack(self, uuid: str):
        """Get crash stack trace using the ProcessedCrash API"""
        url = f"https://crash-stats.mozilla.org/api/ProcessedCrash/?crash_id={uuid}"
        # Removed the print statement for cleaner output
        
        headers = {
            'User-Agent': 'Mozilla Crash Analysis Test 1.0'
        }
        response = requests.get(url, headers=headers)
        return response

    def _extract_file_and_module_info(self, frame: dict) -> FunctionInfo:
        """Extract function, file, and module information from a stack frame"""
        function_name = frame.get('function', 'Unknown Function')
        
        # Extract file name
        file_name = 'Unknown File'
        if 'file' in frame and frame['file']:
            file_path = frame['file']
            # Extract just the filename from the path
            file_name = file_path.split('/')[-1] if '/' in file_path else file_path.split('\\')[-1]
        elif 'filename' in frame and frame['filename']:
            file_path = frame['filename']
            file_name = file_path.split('/')[-1] if '/' in file_path else file_path.split('\\')[-1]
        
        # Extract module name
        module_name = 'Unknown Module'
        if 'module' in frame and frame['module']:
            module_path = frame['module']
            # Extract just the module name from the path
            module_name = module_path.split('/')[-1] if '/' in module_path else module_path.split('\\')[-1]
        elif 'module_name' in frame and frame['module_name']:
            module_name = frame['module_name']
        
        return FunctionInfo(
            function_name=function_name,
            file_name=file_name,
            module_name=module_name
        )


    def _get_crash_details(self, crash_id: str, channel: str) -> Optional[CrashInfo]:
        """Get detailed crash information including stack trace with file and module info"""
        try:
            # Use the ProcessedCrash API to get stack trace
            response = self.get_crash_stack(crash_id)
            response.raise_for_status()
            data = response.json()
            
            # Store the URLs for reference
            crash_report_url = f"https://crash-stats.mozilla.org/report/index/{crash_id}"
            api_url = f"https://crash-stats.mozilla.org/api/ProcessedCrash/?crash_id={crash_id}"
            
            # Extract stack trace and all functions with file and module info
            stack_trace = []
            all_functions = []  # Renamed from top_10_functions to include all functions
            
            if 'json_dump' in data and 'threads' in data['json_dump']:
                for thread in data['json_dump']['threads']:
                    if thread.get('frames'):
                        for frame in thread['frames']:  # Process all frames, no limit
                            if 'function' in frame and frame['function']:
                                function_name = frame['function']
                                stack_trace.append(function_name)
                                
                                # Extract detailed function info for all frames
                                existing_functions = [f.function_name for f in all_functions]
                                if function_name not in existing_functions:
                                    function_info = self._extract_file_and_module_info(frame)
                                    all_functions.append(function_info)
            
            # Look for bug report URL
            bug_report_url = None
            if 'bug_associations' in data and data['bug_associations']:
                for bug in data['bug_associations']:
                    bug_report_url = f"https://bugzilla.mozilla.org/show_bug.cgi?id={bug['bug_id']}"
                    break
            
            return CrashInfo(
                crash_id=crash_id,
                signature=data.get('signature', ''),
                date=data.get('date_processed', ''),
                product_channel=channel,
                bug_report_url=bug_report_url,
                stack_trace=stack_trace,
                all_functions=all_functions,  # Updated to include all functions
                crash_report_url=crash_report_url,
                api_url=api_url
            )
            
        except Exception as e:
            print(f"Error getting crash details for {crash_id}: {e}")
            return None

def test_with_specific_signature():
    """Test with a specific signature - you provide this!"""
    print("TESTING STEP 1: Single Signature Test")
    print("=" * 50)
    
    # SIGNATURES FROM YOUR MOZILLA CRASH STATS DATA:
    test_signature = "OOM | small"#"mozilla::dom::ClientHandle::Control"  # #2 crasher (6.29%) - NO EXTRA SPACE
    # test_signature = "mozilla::dom::quota::QuotaManager::Shutdown::<T>::operator()"  # #1 crasher (19.92%)
    # test_signature = "mozilla::dom::ChildProcessChannelListener::OnChannelReady"  # #3 crasher (5.40%) 
    # test_signature = "mozilla::dom::ServiceWorkerRegistrar::GetShutdownPhase"  # #6 crasher (2.68%)
    # test_signature = "mozilla::dom::workerinternals::RuntimeService::CrashIfHanging"  # #7 crasher (2.56%)
    # test_signature = "mozilla::dom::RemoteObjectProxyBase::GetOrCreateProxyObject"  # #14 crasher (1.81%)
    # test_signature = "mozilla::dom::ContentProcess::InfallibleInit"  # #17 crasher (1.47%)
    
    # Fallback check
    if test_signature == "YOUR_SIGNATURE_HERE":
        print(" Please uncomment one of the recommended signatures above!")
        return
    
    tester = Step1SingleSignatureTest()
    
    #  LONG-TERM ANALYSIS: 1 year with smart sampling AND deduplication (since this signature is recent)
    print(" Starting RECENT crash extraction (1 year) with deduplication...")
    crashes = tester.test_specific_signature_longterm(
        signature=test_signature,
        years_back=1,                   # Changed from 5 to 1 year
        sample_strategy="monthly",      # Options: "monthly", "quarterly", "random"
        dedup_strategy="stack_trace"    # Options: "stack_trace", "top_functions", "comprehensive"
    )
    
    # For older signatures that existed for 5 years, use:
    # crashes = tester.test_specific_signature_longterm(signature=test_signature, years_back=5, sample_strategy="quarterly", dedup_strategy="comprehensive")
    
    if crashes:
        print(f"\n SUCCESS: Found {len(crashes)} crashes across all channels")
        print("\nDetailed Results:")
        print("=" * 50)
        
        for i, crash in enumerate(crashes):
            print(f"\nCrash {i+1}:")
            print(f"  ID: {crash.crash_id}")
            print(f"  Date: {crash.date}")
            print(f"  Channel: {crash.product_channel}")
            print(f"  Signature: {crash.signature}")
            print(f"  Stack Trace Depth: {len(crash.stack_trace)}")
            print(f"  Crash Report URL: {crash.crash_report_url}")
            print(f"  API URL: {crash.api_url}")
            
            if crash.all_functions:
                print(f"  All Files and Functions:")
                for j, func_info in enumerate(crash.all_functions):  # Remove the [:10] limit to display all functions
                    print(f"    {j+1}. File: {func_info.file_name}")
                    print(f"       Function: {func_info.function_name}")
                    print(f"       Module: {func_info.module_name}")
                    print()  # Empty line for readability
            else:
                print(f"  Top Functions: None found")
                
            if crash.bug_report_url:
                print(f"  Bug Report: {crash.bug_report_url}")
            else:
                print(f"  Bug Report: None")
                
        print(f"\n Step 1 is working across all channels! You can now:")
        print("1. Analyze crashes from all Firefox versions")
        print("2. Compare patterns between nightly/release/esr")  
        print("3. Move on to Step 2 (bug analysis)")
        
        return crashes  # ← Return the crashes for further use
        
    else:
        print(f"\n No crashes found for signature: {test_signature}")
        print("\nTroubleshooting:")
        print("1. Verify the signature exists on crash-stats.mozilla.org")
        print("2. Try a different time period")
        print("3. Try a different signature")
        print("4. Check if signature is exactly correct (case-sensitive)")
        
        return []  # ← Return empty list when no crashes found

# Example signatures you could try (comment/uncomment as needed):
EXAMPLE_SIGNATURES = [
    # Common Firefox crash signatures - try these if you need examples
    "mozilla::dom::Document::GetShell",
    "js::jit::CodeGenerator::visitGuardReceiverPolymorphic", 
    "mozilla::layers::CompositorBridgeChild::RecvDidComposite",
    "nsTArray_Impl<T, Alloc>::ShiftData",
    "RefPtr<T>::operator->",
]

if __name__ == "__main__":
    test_with_specific_signature()
    
    # Uncomment this to see example signatures:
    # print(f"\nExample signatures you could try:")
    # for sig in EXAMPLE_SIGNATURES:
    #     print(f"  {sig}")