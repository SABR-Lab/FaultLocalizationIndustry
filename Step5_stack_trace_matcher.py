#!/usr/bin/env python3
"""
Enhanced Crash Function Matcher with Detailed File Output - REFACTORED VERSION
Compares crash stack trace functions with vulnerable function calls AND changed file names
FIXES: Eliminates duplicate analysis by implementing proper caching

Enhanced Workflow:
1. Get crashes and their stack traces from updated Crash_filter.py (with file/module info)
2. Get vulnerable functions AND changed file names from complete_rootcause.py (CACHED)
3. Compare changed files with crash stack trace file names (FILE-LEVEL CORRELATION)
4. Compare vulnerable function NAMES directly with stack functions (DIRECT FUNCTION CORRELATION)
5. Get function calls for vulnerable functions and compare with stack functions (INDIRECT FUNCTION CORRELATION)
6. Save detailed analysis to individual files per crash
7. Print clean summary statistics in terminal

Required files in same directory: Crash_filter.py, complete_rootcause.py, Automated_function_calls.py
"""

import re
import time
import json
from typing import List, Dict, Any, Set, Optional, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
import traceback

# Import required components with detailed error handling
try:
    from Step1_crash_extractor import Step1SingleSignatureTest, CrashInfo, FunctionInfo
    CRASH_EXTRACTION_AVAILABLE = True
    print("✓ Crash extraction available")
except ImportError as e:
    print(f"✗ Error: Could not import Crash_filter.py: {e}")
    CRASH_EXTRACTION_AVAILABLE = False

try:
    from Step3_crash_analyzer import AutomatedMozillaCrashAnalyzer
    ROOTCAUSE_ANALYSIS_AVAILABLE = True
    print("✓ Root cause analysis available")
except ImportError as e:
    print(f"✗ Error: Could not import crash_analyzer.py: {e}")
    ROOTCAUSE_ANALYSIS_AVAILABLE = False
try:
    from Step4_Automated_function_calls import UnifiedCrashFunctionAnalyzer
    FUNCTION_CALL_ANALYSIS_AVAILABLE = True
    print("✓ Function call analysis available")
except ImportError as e:
    print(f"✗ Error: Could not import Automated_function_calls.py: {e}")
    FUNCTION_CALL_ANALYSIS_AVAILABLE = False


@dataclass
class FileMatch:
    changed_file: str
    stack_file: str
    stack_function: str
    stack_module: str
    stack_frame_number: int
    match_type: str
    confidence: float


@dataclass
class DirectFunctionMatch:
    vulnerable_function: str
    stack_function: str
    stack_file: str
    stack_module: str
    stack_frame_number: int
    match_type: str
    confidence: float


@dataclass
class IndirectFunctionMatch:
    vulnerable_function: str
    called_function: str
    stack_function: str
    stack_file: str
    stack_module: str
    stack_frame_number: int
    match_type: str
    confidence: float


@dataclass
class CrashAnalysisResult:
    crash_id: str
    crash_date: str
    crash_channel: str
    crash_signature: str
    stack_functions: List[Dict[str, str]]
    changed_files: List[str]
    vulnerable_functions: List[str]
    vulnerable_function_calls: Dict[str, List[str]]
    file_matches: List[FileMatch]
    direct_function_matches: List[DirectFunctionMatch]
    indirect_function_matches: List[IndirectFunctionMatch]
    analysis_status: str
    error_message: Optional[str] = None


@dataclass
class CachedAnalysisData:
    """Cached analysis data to prevent duplicate processing"""
    analysis_result: Dict[str, Any]
    enhanced_analysis: Dict[str, Any]
    function_call_results: Dict[str, Any]
    changed_files: List[str]
    vulnerable_functions: List[str]
    vulnerable_function_calls: Dict[str, List[str]]
    timestamp: float


class FunctionNameProcessor:
    """Handles function name cleaning and comparison operations"""
    
    @staticmethod
    def clean_function_name(func_name: str) -> str:
        """Clean function name for matching"""
        if not func_name:
            return ""
        
        try:
            clean = re.sub(r'<[^>]*>', '', func_name)
            if '::' in clean:
                clean = clean.split('::')[-1]
            clean = clean.split('(')[0]
            return clean.strip()
        except Exception:
            return func_name.strip()
    
    @staticmethod
    def functions_are_related(func_name: str, vuln_func: str) -> bool:
        """Check if functions are related"""
        if not func_name or not vuln_func:
            return False
        
        if func_name == vuln_func:
            return True
        
        clean_func = FunctionNameProcessor.clean_function_name(func_name)
        clean_vuln = FunctionNameProcessor.clean_function_name(vuln_func)
        
        return (
            func_name.endswith(vuln_func) or
            vuln_func in func_name or
            clean_func == clean_vuln or
            (len(clean_func) > 3 and len(clean_vuln) > 3 and 
             (clean_func in clean_vuln or clean_vuln in clean_func))
        )
    
    @staticmethod
    def functions_match_improved(func1: str, func2: str) -> bool:
        """Improved function matching"""
        if not func1 or not func2 or func1 == 'Unknown' or func2 == 'Unknown':
            return False
        
        func1_lower = func1.lower().strip()
        func2_lower = func2.lower().strip()
        
        if func1_lower == func2_lower:
            return True
        
        if len(func1_lower) < 3 or len(func2_lower) < 3:
            return False
        
        if len(func1_lower) >= 6 and len(func2_lower) >= 6:
            if (func1_lower in func2_lower and len(func1_lower) / len(func2_lower) > 0.6) or \
               (func2_lower in func1_lower and len(func2_lower) / len(func1_lower) > 0.6):
                return True
        
        clean1 = FunctionNameProcessor.clean_function_name(func1)
        clean2 = FunctionNameProcessor.clean_function_name(func2)
        
        if clean1 and clean2 and len(clean1) > 2 and len(clean2) > 2:
            if clean1.lower() == clean2.lower():
                return True
        
        return False
    
    @staticmethod
    def get_match_type_and_confidence(func1: str, func2: str) -> Tuple[str, float]:
        """Determine match type and confidence score"""
        if not func1 or not func2:
            return "no_match", 0.0
        
        func1_lower = func1.lower().strip()
        func2_lower = func2.lower().strip()
        
        if func1.strip() == func2.strip():
            return "exact", 1.0
        
        if func1_lower == func2_lower:
            return "case_insensitive", 0.9
        
        clean1 = FunctionNameProcessor.clean_function_name(func1)
        clean2 = FunctionNameProcessor.clean_function_name(func2)
        if clean1 and clean2 and clean1.lower() == clean2.lower():
            return "cleaned_exact", 0.8
        
        if len(func1_lower) >= 6 and len(func2_lower) >= 6:
            if func1_lower in func2_lower:
                confidence = len(func1_lower) / len(func2_lower)
                return "substring", min(confidence, 0.7)
            elif func2_lower in func1_lower:
                confidence = len(func2_lower) / len(func1_lower)
                return "substring", min(confidence, 0.7)
        
        return "no_match", 0.0


class FileNameMatcher:
    """Handles file name comparison operations"""
    
    @staticmethod
    def compare_file_names(changed_file: str, stack_file: str) -> Tuple[str, float]:
        """Compare file names with various matching strategies"""
        if not changed_file or not stack_file:
            return "no_match", 0.0
        
        changed_filename = changed_file.split('/')[-1] if '/' in changed_file else changed_file
        stack_filename = stack_file.split('/')[-1] if '/' in stack_file else stack_file
        
        if changed_filename.lower() == stack_filename.lower():
            return "exact_filename", 1.0
        
        if changed_file.lower() == stack_file.lower():
            return "exact_path", 1.0
        
        if changed_filename.lower() in stack_file.lower() or stack_filename.lower() in changed_file.lower():
            return "path_contains", 0.8
        
        changed_base = changed_filename.split('.')[0].lower()
        stack_base = stack_filename.split('.')[0].lower()
        
        if changed_base == stack_base and len(changed_base) > 3:
            return "basename_match", 0.7
        
        if len(changed_base) > 4 and len(stack_base) > 4:
            if changed_base in stack_base or stack_base in changed_base:
                overlap = min(len(changed_base), len(stack_base)) / max(len(changed_base), len(stack_base))
                if overlap > 0.6:
                    return "partial_match", 0.5
        
        return "no_match", 0.0


class AnalysisDataExtractor:
    """Handles extraction and processing of analysis data"""
    
    def __init__(self, rootcause_analyzer, function_call_analyzer):
        self.rootcause_analyzer = rootcause_analyzer
        self.function_call_analyzer = function_call_analyzer
    
    def extract_cached_analysis_data(self, crash_id: str) -> CachedAnalysisData:
        """Get all analysis data for a crash with caching to prevent duplicates"""
        
        print(f"  Running fresh analysis for {crash_id}")
        
        # Initialize empty results
        analysis_result = {}
        enhanced_analysis = {}
        function_call_results = {}
        changed_files = []
        vulnerable_functions = []
        vulnerable_function_calls = {}
        
        try:
            # Step 1: Run root cause analysis ONCE
            if self.rootcause_analyzer:
                print(f"\n Root cause analysis")
                analysis_result = self.rootcause_analyzer.full_analysis(crash_id)
                
                if 'error' not in analysis_result:
                    # Extract changed files
                    changed_files = self._extract_changed_files(analysis_result)
                    
                    print(f"\n Enhanced analysis.")
                    enhanced_analysis = self.rootcause_analyzer.enhanced_extract_and_analyze_introducing_commits(analysis_result)
                    
                    # Extract vulnerable functions from enhanced analysis
                    vulnerable_functions = self._extract_vulnerable_functions(enhanced_analysis)
            
            # Step 2: Get function calls using the SAME enhanced analysis data
            if self.function_call_analyzer and enhanced_analysis and vulnerable_functions:
                print(f"    Analyzing function calls")
                
                function_call_results = self.function_call_analyzer.analyze_function_calls_for_enhanced_analysis(
                    enhanced_analysis, crash_id
                )
                
                if function_call_results:
                    vulnerable_function_calls = self._extract_function_calls(
                        function_call_results, vulnerable_functions
                    )
            
        except Exception as e:
            print(f"    Error in analysis: {e}")
        
        return CachedAnalysisData(
            analysis_result=analysis_result,
            enhanced_analysis=enhanced_analysis,
            function_call_results=function_call_results,
            changed_files=changed_files,
            vulnerable_functions=vulnerable_functions,
            vulnerable_function_calls=vulnerable_function_calls,
            timestamp=time.time()
        )
    
    def _extract_changed_files(self, analysis_result: Dict[str, Any]) -> List[str]:
        """Extract changed files from analysis result"""
        changed_files = []
        if 'file_changes_by_type' in analysis_result:
            file_changes_dict = analysis_result['file_changes_by_type']
            for change_type in ['modified', 'added']:
                if change_type in file_changes_dict:
                    changed_files.extend(file_changes_dict[change_type])
        return changed_files
    
    def _extract_vulnerable_functions(self, enhanced_analysis: Dict[str, Any]) -> List[str]:
        #Extract vulnerable functions from enhanced analysis
        vulnerable_functions = []
        
        if enhanced_analysis:
            for filename, file_analysis in enhanced_analysis.items():
                function_comparison = file_analysis.get('function_comparison', {})
                vuln_funcs = function_comparison.get('vulnerable_functions', {})
                if vuln_funcs:
                    vulnerable_functions.extend(vuln_funcs.keys())
                
                introducing_functions = file_analysis.get('introducing_functions', {})
                func_details = introducing_functions.get('function_details', {})
                if func_details:
                    vulnerable_functions.extend(func_details.keys())
        
        return [f for f in set(vulnerable_functions) if f and len(f) > 0]
    
    def _extract_function_calls(self, function_call_results: Dict[str, Any], 
                               vulnerable_functions: List[str]) -> Dict[str, List[str]]:
        """Extract function calls for vulnerable functions"""
        vulnerable_function_calls = {}
        
        for filename, file_analysis in function_call_results.items():
            if not isinstance(file_analysis, dict):
                continue
            
            function_calls_mapping = file_analysis.get('function_calls_map', {})
            if not function_calls_mapping:
                continue
            
            for func_name, calls in function_calls_mapping.items():
                if not func_name or not calls or not isinstance(calls, list):
                    continue
                
                for vuln_func in vulnerable_functions:
                    if FunctionNameProcessor.functions_are_related(func_name, vuln_func):
                        valid_calls = [call for call in calls if self._is_valid_function_call(call)]
                        if valid_calls:
                            vulnerable_function_calls[vuln_func] = valid_calls
                        break
        
        return vulnerable_function_calls
    
    def _is_valid_function_call(self, call: str) -> bool:
        """Validate function call"""
        if not call or len(call) < 2:
            return False
        
        invalid_patterns = [
            r'^\d+$',
            r'^[{}()\[\]<>.,;:]+$',
            r'^(if|for|while|else|return|break|continue|true|false|null|nullptr)$'
        ]
        
        for pattern in invalid_patterns:
            if re.match(pattern, call, re.IGNORECASE):
                return False
        
        return True


class CrashMatchingEngine:
    """Handles the core matching logic between crashes and analysis data"""
    
    def __init__(self):
        self.function_processor = FunctionNameProcessor()
        self.file_matcher = FileNameMatcher()
    
    def match_files_with_stack_traces(self, changed_files: List[str], 
                                    stack_functions: List[FunctionInfo]) -> List[FileMatch]:
        """Compare changed files with crash stack trace file names"""
        if not changed_files or not stack_functions:
            return []
        
        file_matches = []
        
        for changed_file in changed_files:
            for frame_idx, func_info in enumerate(stack_functions[:10]):
                if func_info.file_name == 'Unknown File':
                    continue
                
                try:
                    match_type, confidence = self.file_matcher.compare_file_names(
                        changed_file, func_info.file_name
                    )
                    
                    if confidence >= 0.3:
                        match = FileMatch(
                            changed_file=changed_file,
                            stack_file=func_info.file_name,
                            stack_function=func_info.function_name,
                            stack_module=func_info.module_name,
                            stack_frame_number=frame_idx,
                            match_type=match_type,
                            confidence=confidence
                        )
                        file_matches.append(match)
                        
                except Exception:
                    continue
        
        return file_matches
    
    def match_vulnerable_functions_directly(self, vulnerable_functions: List[str], 
                                          stack_functions: List[FunctionInfo]) -> List[DirectFunctionMatch]:
        """Compare vulnerable function names directly with stack function names"""
        if not vulnerable_functions or not stack_functions:
            return []
        
        direct_matches = []
        
        for vuln_func in vulnerable_functions:
            for frame_idx, func_info in enumerate(stack_functions[:10]):
                if not func_info.function_name or func_info.function_name == 'Unknown Function':
                    continue
                
                try:
                    if self.function_processor.functions_match_improved(vuln_func, func_info.function_name):
                        match_type, confidence = self.function_processor.get_match_type_and_confidence(
                            vuln_func, func_info.function_name
                        )
                        
                        if confidence >= 0.3:
                            match = DirectFunctionMatch(
                                vulnerable_function=vuln_func,
                                stack_function=func_info.function_name,
                                stack_file=func_info.file_name,
                                stack_module=func_info.module_name,
                                stack_frame_number=frame_idx,
                                match_type=match_type,
                                confidence=confidence
                            )
                            direct_matches.append(match)
                            
                except Exception:
                    continue
        
        return direct_matches
    
    def match_function_calls_with_stack_traces(self, vulnerable_function_calls: Dict[str, List[str]], 
                                             stack_functions: List[FunctionInfo]) -> List[IndirectFunctionMatch]:
        """Compare vulnerable function calls with crash stack function names"""
        if not vulnerable_function_calls or not stack_functions:
            return []
        
        # Create lookup for stack functions
        crash_stack_info, crash_stack_functions = self._build_stack_lookup(stack_functions)
        
        # Match function calls with stack functions
        indirect_matches = []
        
        for vuln_func, called_functions in vulnerable_function_calls.items():
            valid_called_functions = [f for f in called_functions if f and len(f.strip()) > 0]
            
            for called_func in valid_called_functions:
                matches = self._find_matching_stack_functions(
                    called_func, crash_stack_functions, crash_stack_info, vuln_func
                )
                indirect_matches.extend(matches)
        
        return indirect_matches
    
    def _build_stack_lookup(self, stack_functions: List[FunctionInfo]) -> Tuple[List[Dict], Set[str]]:
        """Build lookup structures for stack functions"""
        crash_stack_info = []
        crash_stack_functions = set()
        
        for frame_idx, func_info in enumerate(stack_functions[:10]):
            if not func_info.function_name or func_info.function_name == 'Unknown Function':
                continue
            
            try:
                clean_function = re.sub(r'<[^>]*>', '', func_info.function_name)
                clean_function = clean_function.split('::')[-1] if '::' in clean_function else clean_function
                clean_function = clean_function.split('(')[0].strip()
                
                if clean_function:
                    crash_stack_functions.add(clean_function)
                    crash_stack_functions.add(func_info.function_name)
                    
                    crash_frame_info = {
                        'frame_number': frame_idx,
                        'function': func_info.function_name,
                        'file': func_info.file_name,
                        'module': func_info.module_name
                    }
                    crash_stack_info.append(crash_frame_info)
            except Exception:
                continue
        
        return crash_stack_info, crash_stack_functions
    
    def _find_matching_stack_functions(self, called_func: str, crash_stack_functions: Set[str],
                                     crash_stack_info: List[Dict], vuln_func: str) -> List[IndirectFunctionMatch]:
        """Find matching stack functions for a called function"""
        matches = []
        
        for crash_func in crash_stack_functions:
            try:
                if self.function_processor.functions_match_improved(called_func, crash_func):
                    match_type, confidence = self.function_processor.get_match_type_and_confidence(
                        called_func, crash_func
                    )
                    
                    if confidence >= 0.3:
                        frame_info = self._find_frame_info(crash_func, called_func, crash_stack_info)
                        
                        match = IndirectFunctionMatch(
                            vulnerable_function=vuln_func,
                            called_function=called_func,
                            stack_function=crash_func,
                            stack_file=frame_info['file'],
                            stack_module=frame_info['module'],
                            stack_frame_number=frame_info['frame_number'],
                            match_type=match_type,
                            confidence=confidence
                        )
                        matches.append(match)
            except Exception:
                continue
        
        return matches
    
    def _find_frame_info(self, crash_func: str, called_func: str, crash_stack_info: List[Dict]) -> Dict[str, Any]:
        """Find frame information for a matching function"""
        for frame in crash_stack_info:
            if crash_func == frame['function'] or called_func.lower() in frame['function'].lower():
                return {
                    'frame_number': frame['frame_number'],
                    'file': frame['file'],
                    'module': frame['module']
                }
        
        return {
            'frame_number': 0,
            'file': 'Unknown File',
            'module': 'Unknown Module'
        }


class CrashDataValidator:
    """Handles crash data validation"""
    
    @staticmethod
    def validate_crash_data(crash: CrashInfo) -> bool:
        """Validate crash data structure"""
        try:
            required_attrs = ['crash_id', 'signature', 'date']
            for attr in required_attrs:
                if not hasattr(crash, attr) or not getattr(crash, attr):
                    return False
            
            if not hasattr(crash, 'all_functions'):
                crash.all_functions = []
            elif crash.all_functions:
                for func_info in crash.all_functions:
                    if not hasattr(func_info, 'function_name') or not hasattr(func_info, 'file_name') or not hasattr(func_info, 'module_name'):
                        return False
            
            return True
        except Exception:
            return False


class ReportGenerator:
    """Handles generation of analysis reports"""
    
    def save_crash_analysis_to_file(self, result: CrashAnalysisResult) -> str:
        """Save detailed crash analysis to individual file"""
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = f"crash_analysis_{result.crash_id}_{timestamp}.txt"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                self._write_crash_header(f, result)
                self._write_stack_frames(f, result)
                self._write_changed_files(f, result)
                self._write_vulnerable_functions(f, result)
                self._write_function_calls(f, result)
                self._write_matches(f, result)
                f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            return filename
            
        except Exception as e:
            print(f"Error saving analysis file: {e}")
            return ""
    
    def _write_crash_header(self, f, result: CrashAnalysisResult):
        """Write crash header information"""
        f.write(f"CRASH ANALYSIS REPORT\n")
        f.write(f"=" * 80 + "\n\n")
        
        f.write(f"Crash Information:\n")
        f.write(f"  ID: {result.crash_id}\n")
        f.write(f"  Date: {result.crash_date}\n")
        f.write(f"  Channel: {result.crash_channel}\n")
        f.write(f"  Signature: {result.crash_signature}\n")
        f.write(f"  Status: {result.analysis_status}\n")
        if result.error_message:
            f.write(f"  Error: {result.error_message}\n")
        f.write(f"\n")
    
    def _write_stack_frames(self, f, result: CrashAnalysisResult):
        """Write stack frames information"""
        f.write(f"Stack Frames:\n")
        for func in result.stack_functions:
            f.write(f"  Frame {func['frame_number']}: {func['function']}\n")
            f.write(f"    File: {func['file']}\n")
            f.write(f"    Module: {func['module']}\n")
        f.write(f"\n")
    
    def _write_changed_files(self, f, result: CrashAnalysisResult):
        """Write changed files information"""
        f.write(f"Changed Files ({len(result.changed_files)}):\n")
        for i, file in enumerate(result.changed_files, 1):
            f.write(f"  {i}. {file}\n")
        f.write(f"\n")
    
    def _write_vulnerable_functions(self, f, result: CrashAnalysisResult):
        """Write vulnerable functions information"""
        f.write(f"Vulnerable Functions ({len(result.vulnerable_functions)}):\n")
        for i, func in enumerate(result.vulnerable_functions, 1):
            f.write(f"  {i}. {func}\n")
        f.write(f"\n")
    
    def _write_function_calls(self, f, result: CrashAnalysisResult):
        """Write vulnerable function calls information"""
        f.write(f"Vulnerable Function Calls:\n")
        for func, calls in result.vulnerable_function_calls.items():
            f.write(f"  {func}:\n")
            for call in calls:
                f.write(f"    - {call}\n")
        f.write(f"\n")
    
    def _write_matches(self, f, result: CrashAnalysisResult):
        """Write all match types"""
        self._write_file_matches(f, result)
        self._write_direct_function_matches(f, result)
        self._write_indirect_function_matches(f, result)
    
    def _write_file_matches(self, f, result: CrashAnalysisResult):
        """Write file matches"""
        f.write(f"FILE-LEVEL MATCHES ({len(result.file_matches)}):\n")
        for match in result.file_matches:
            f.write(f"  {match.changed_file} ↔ {match.stack_file}\n")
            f.write(f"    Frame: {match.stack_frame_number}, Function: {match.stack_function}\n")
            f.write(f"    Module: {match.stack_module}\n")
            f.write(f"    Match: {match.match_type} (confidence: {match.confidence:.2f})\n")
        f.write(f"\n")
    
    def _write_direct_function_matches(self, f, result: CrashAnalysisResult):
        """Write direct function matches"""
        f.write(f"DIRECT FUNCTION MATCHES ({len(result.direct_function_matches)}):\n")
        for match in result.direct_function_matches:
            f.write(f"  {match.vulnerable_function} ↔ {match.stack_function}\n")
            f.write(f"    Frame: {match.stack_frame_number}, File: {match.stack_file}\n")
            f.write(f"    Module: {match.stack_module}\n")
            f.write(f"    Match: {match.match_type} (confidence: {match.confidence:.2f})\n")
        f.write(f"\n")
    
    def _write_indirect_function_matches(self, f, result: CrashAnalysisResult):
        """Write indirect function matches"""
        f.write(f"INDIRECT FUNCTION MATCHES ({len(result.indirect_function_matches)}):\n")
        for match in result.indirect_function_matches:
            f.write(f"  {match.vulnerable_function} → {match.called_function} ↔ {match.stack_function}\n")
            f.write(f"    Frame: {match.stack_frame_number}, File: {match.stack_file}\n")
            f.write(f"    Module: {match.stack_module}\n")
            f.write(f"    Match: {match.match_type} (confidence: {match.confidence:.2f})\n")
        f.write(f"\n")


class StatisticsCalculator:
    """Handles statistics calculation and reporting"""
    
    def __init__(self):
        self.reset_statistics()
    
    def reset_statistics(self):
        """Reset all statistics counters"""
        self.crashes_with_file_matches = 0
        self.crashes_with_direct_function_matches = 0
        self.crashes_with_indirect_function_matches = 0
        self.crashes_with_any_match = 0
        self.total_file_matches = 0
        self.total_direct_function_matches = 0
        self.total_indirect_function_matches = 0
        self.successful_analyses = 0
        self.partial_analyses = 0
        self.failed_analyses = 0
    
    def update_statistics(self, result: CrashAnalysisResult):
        """Update statistics based on analysis result"""
        if result.analysis_status == "success":
            self.successful_analyses += 1
            
            has_matches = False
            if result.file_matches:
                self.crashes_with_file_matches += 1
                self.total_file_matches += len(result.file_matches)
                has_matches = True
            
            if result.direct_function_matches:
                self.crashes_with_direct_function_matches += 1
                self.total_direct_function_matches += len(result.direct_function_matches)
                has_matches = True
            
            if result.indirect_function_matches:
                self.crashes_with_indirect_function_matches += 1
                self.total_indirect_function_matches += len(result.indirect_function_matches)
                has_matches = True
            
            if has_matches:
                self.crashes_with_any_match += 1
                
        elif result.analysis_status == "partial":
            self.partial_analyses += 1
        else:
            self.failed_analyses += 1
    
    def get_correlation_rates(self) -> Dict[str, float]:
        """Calculate correlation rates"""
        if self.successful_analyses == 0:
            return {
                'overall': 0.0,
                'file': 0.0,
                'direct_function': 0.0,
                'indirect_function': 0.0
            }
        
        return {
            'overall': (self.crashes_with_any_match / self.successful_analyses) * 100,
            'file': (self.crashes_with_file_matches / self.successful_analyses) * 100,
            'direct_function': (self.crashes_with_direct_function_matches / self.successful_analyses) * 100,
            'indirect_function': (self.crashes_with_indirect_function_matches / self.successful_analyses) * 100
        }
    
    def print_terminal_summary(self, signature: str, total_crashes: int, saved_files: List[str]):
        """Print clean terminal summary"""
        rates = self.get_correlation_rates()
        
        print(f"\n" + "=" * 60)
        print(f"ANALYSIS COMPLETE")
        print(f"=" * 60)
        
        print(f"Analysis Summary:")
        print(f"  Total crashes analyzed: {total_crashes}")
        print(f"  Successful analyses: {self.successful_analyses}")
        print(f"  Partial analyses: {self.partial_analyses}")
        print(f"  Failed analyses: {self.failed_analyses}")
        print(f"  Success rate: {(self.successful_analyses/total_crashes*100):.1f}%")
        
        print(f"\nCorrelation Results:")
        print(f"  Crashes with ANY matches: {self.crashes_with_any_match}")
        print(f"  Overall correlation rate: {rates['overall']:.1f}%")
        
        print(f"\nMatch Breakdown:")
        print(f"  File-level matches:")
        print(f"    Crashes: {self.crashes_with_file_matches}")
        print(f"    Total matches: {self.total_file_matches}")
        print(f"    Rate: {rates['file']:.1f}%")
        
        print(f"  Direct function matches:")
        print(f"    Crashes: {self.crashes_with_direct_function_matches}")
        print(f"    Total matches: {self.total_direct_function_matches}")
        print(f"    Rate: {rates['direct_function']:.1f}%")
        
        print(f"  Indirect function matches:")
        print(f"    Crashes: {self.crashes_with_indirect_function_matches}")
        print(f"    Total matches: {self.total_indirect_function_matches}")
        print(f"    Rate: {rates['indirect_function']:.1f}%")
        
        if saved_files:
            print(f"\nDetailed Analysis Files:")
            for filename in saved_files:
                print(f"  - {filename}")
        
        print(f"\nInterpretation:")
        self._print_interpretation(rates['overall'])
    
    def _print_interpretation(self, overall_rate: float):
        """Print interpretation of results"""
        if overall_rate >= 70:
            print(f"  HIGH correlation: Strong evidence linking crashes to vulnerable code")
        elif overall_rate >= 30:
            print(f"  MODERATE correlation: Some evidence of relationship")
        else:
            print(f"  LOW correlation: Limited evidence of direct relationship")


class EnhancedCrashStackFunctionMatcher:
    """Enhanced matcher with detailed file output and clean terminal statistics - NO DUPLICATES"""
    
    def __init__(self, repo_paths: Dict[str, str], max_retries: int = 3):
        self.repo_paths = repo_paths
        self.max_retries = max_retries
        
        # Cache to prevent duplicate analysis
        self._analysis_cache: Dict[str, CachedAnalysisData] = {}
        
        # Initialize components
        self.crash_extractor = self._initialize_crash_extractor()
        self.rootcause_analyzer = self._initialize_rootcause_analyzer()
        self.function_call_analyzer = self._initialize_function_call_analyzer()
        
        # Initialize helper classes
        self.data_extractor = AnalysisDataExtractor(
            self.rootcause_analyzer, self.function_call_analyzer
        )
        self.matching_engine = CrashMatchingEngine()
        self.validator = CrashDataValidator()
        self.report_generator = ReportGenerator()
        self.statistics = StatisticsCalculator()
    
    def _initialize_crash_extractor(self):
        """Initialize crash extractor with error handling"""
        if CRASH_EXTRACTION_AVAILABLE:
            try:
                return Step1SingleSignatureTest()
            except Exception as e:
                print(f"✗ Failed to initialize crash extractor: {e}")
        return None
    
    def _initialize_rootcause_analyzer(self):
        """Initialize root cause analyzer with error handling"""
        if ROOTCAUSE_ANALYSIS_AVAILABLE:
            try:
                return AutomatedMozillaCrashAnalyzer(self.repo_paths)
            except Exception as e:
                print(f"✗ Failed to initialize root cause analyzer: {e}")
        return None
    
    def _initialize_function_call_analyzer(self):
        """Initialize function call analyzer with error handling"""
        if FUNCTION_CALL_ANALYSIS_AVAILABLE:
            try:
                return UnifiedCrashFunctionAnalyzer(self.repo_paths)
            except Exception as e:
                print(f"✗ Failed to initialize function call analyzer: {e}")
        return None
    
    def get_crashes_and_stack_traces(self, signature: str, years_back: int = 1, max_crashes: int = 10) -> List[CrashInfo]:
        """Get crashes with enhanced file and module information"""
        if not self.crash_extractor:
            return []
        
        crashes = self.crash_extractor.test_specific_signature_longterm(
            signature=signature,
            years_back=years_back,
            sample_strategy="monthly",
            dedup_strategy="stack_trace"
        )
        
        if len(crashes) > max_crashes:
            crashes = crashes[:max_crashes]
        
        # Validate crash data
        valid_crashes = []
        for crash in crashes:
            if self.validator.validate_crash_data(crash):
                valid_crashes.append(crash)
        
        return valid_crashes
    
    def get_cached_analysis_data(self, crash_id: str) -> CachedAnalysisData:
        """Get all analysis data for a crash with caching to prevent duplicates"""
        
        # Check cache first
        if crash_id in self._analysis_cache:
            cached_data = self._analysis_cache[crash_id]
            # Cache is valid for 1 hour
            if time.time() - cached_data.timestamp < 3600:
                print(f"  Using cached analysis for {crash_id}")
                return cached_data
        
        # Extract fresh data
        cached_data = self.data_extractor.extract_cached_analysis_data(crash_id)
        
        # Cache the results
        self._analysis_cache[crash_id] = cached_data
        return cached_data
    
    def analyze_single_crash(self, crash: CrashInfo) -> CrashAnalysisResult:
        """Analyze single crash with all correlation types - NO DUPLICATE ANALYSIS"""
        crash_id = crash.crash_id
        
        # Initialize result
        result = CrashAnalysisResult(
            crash_id=crash_id,
            crash_date=crash.date,
            crash_channel=crash.product_channel,
            crash_signature=crash.signature,
            stack_functions=[],
            changed_files=[],
            vulnerable_functions=[],
            vulnerable_function_calls={},
            file_matches=[],
            direct_function_matches=[],
            indirect_function_matches=[],
            analysis_status="failed"
        )
        
        try:
            # Extract stack functions
            result.stack_functions = self._extract_stack_functions(crash)
            
            if not result.stack_functions:
                result.error_message = "No stack functions available"
                return result
            
            # Get all analysis data in ONE call (prevents duplicates)
            cached_data = self.get_cached_analysis_data(crash_id)
            
            result.changed_files = cached_data.changed_files
            result.vulnerable_functions = cached_data.vulnerable_functions
            result.vulnerable_function_calls = cached_data.vulnerable_function_calls
            
            if not result.changed_files and not result.vulnerable_functions:
                result.error_message = "No changed files or vulnerable functions found"
                return result
            
            # Perform all matching operations
            result = self._perform_matching_operations(result, crash)
            
            # Determine final status
            result = self._determine_analysis_status(result)
            
        except Exception as e:
            result.error_message = str(e)
        
        return result
    
    def _extract_stack_functions(self, crash: CrashInfo) -> List[Dict[str, str]]:
        """Extract stack functions from crash"""
        stack_functions = []
        if hasattr(crash, 'all_functions') and crash.all_functions:
            for i, func_info in enumerate(crash.all_functions):
                stack_functions.append({
                    'frame_number': i,
                    'function': func_info.function_name,
                    'file': func_info.file_name,
                    'module': func_info.module_name
                })
        return stack_functions
    
    def _perform_matching_operations(self, result: CrashAnalysisResult, crash: CrashInfo) -> CrashAnalysisResult:
        """Perform all matching operations"""
        # File-level matching
        if result.changed_files:
            result.file_matches = self.matching_engine.match_files_with_stack_traces(
                result.changed_files, crash.all_functions
            )
        
        # Direct function matching
        if result.vulnerable_functions:
            result.direct_function_matches = self.matching_engine.match_vulnerable_functions_directly(
                result.vulnerable_functions, crash.all_functions
            )
        
        # Indirect function matching
        if result.vulnerable_function_calls:
            result.indirect_function_matches = self.matching_engine.match_function_calls_with_stack_traces(
                result.vulnerable_function_calls, crash.all_functions
            )
        
        return result
    
    def _determine_analysis_status(self, result: CrashAnalysisResult) -> CrashAnalysisResult:
        """Determine final analysis status"""
        total_matches = (len(result.file_matches) + 
                        len(result.direct_function_matches) + 
                        len(result.indirect_function_matches))
        
        if total_matches > 0:
            result.analysis_status = "success"
        elif result.changed_files or result.vulnerable_functions:
            result.analysis_status = "partial"
            result.error_message = "Found data but no correlations"
        
        return result
    
    def analyze_crashes_for_signature(self, signature: str, years_back: int = 1, max_crashes: int = 10) -> Dict[str, Any]:
        """Main analysis function with clean terminal output"""
        print(f"CRASH CORRELATION ANALYSIS")
        print(f"Signature: {signature}")
        print(f"Parameters: {max_crashes} crashes, {years_back} year(s)")
        print("=" * 60)
        
        # Validate components
        validation_result = self._validate_components()
        if 'error' in validation_result:
            return validation_result
        
        # Extract crashes
        crashes = self._extract_and_validate_crashes(signature, years_back, max_crashes)
        if not crashes:
            return self._create_error_result(f'No crashes found for signature: {signature}', signature, 0)
        
        print(f"Found {len(crashes)} crashes to analyze")
        
        # Analyze crashes and generate results
        results, saved_files = self._analyze_crashes_and_save_reports(crashes)
        
        # Calculate and display statistics
        return self._generate_final_results(signature, len(crashes), results, saved_files)
    
    def _validate_components(self) -> Dict[str, Any]:
        """Validate that all required components are available"""
        missing_components = []
        if not CRASH_EXTRACTION_AVAILABLE or not self.crash_extractor:
            missing_components.append("Crash_filter.py")
        if not ROOTCAUSE_ANALYSIS_AVAILABLE or not self.rootcause_analyzer:
            missing_components.append("complete_rootcause.py")
        if not FUNCTION_CALL_ANALYSIS_AVAILABLE or not self.function_call_analyzer:
            missing_components.append("Automated_function_calls.py")
        
        if missing_components:
            return {
                'error': f'Missing required components: {", ".join(missing_components)}',
                'missing_components': missing_components
            }
        
        return {}
    
    def _extract_and_validate_crashes(self, signature: str, years_back: int, max_crashes: int) -> List[CrashInfo]:
        """Extract and validate crashes"""
        print(f"Extracting crashes...")
        return self.get_crashes_and_stack_traces(signature, years_back, max_crashes)
    
    def _create_error_result(self, error_msg: str, signature: str, total_crashes: int) -> Dict[str, Any]:
        """Create error result dictionary"""
        return {
            'error': error_msg,
            'signature': signature,
            'total_crashes': total_crashes
        }
    
    def _analyze_crashes_and_save_reports(self, crashes: List[CrashInfo]) -> Tuple[List[CrashAnalysisResult], List[str]]:
        """Analyze crashes and save detailed reports"""
        results = []
        saved_files = []
        
        self.statistics.reset_statistics()
        
        for i, crash in enumerate(crashes, 1):
            print(f"\nAnalyzing crash {i}/{len(crashes)}: {crash.crash_id}")
            
            try:
                result = self.analyze_single_crash(crash)
                results.append(result)
                self.statistics.update_statistics(result)
                    
            except Exception as e:
                self.statistics.failed_analyses += 1
                print(f"  Error: {str(e)}")
        
        # Save analysis files AFTER all processing is complete
        saved_files = self._save_analysis_files(results)
        
        return results, saved_files
    
    def _save_analysis_files(self, results: List[CrashAnalysisResult]) -> List[str]:
        """Save analysis files for successful and partial results"""
        print(f"\nSaving detailed analysis files...")
        saved_files = []
        
        for result in results:
            if result.analysis_status in ["success", "partial"]:
                print(f"  Saving analysis for {result.crash_id}...")
                filename = self.report_generator.save_crash_analysis_to_file(result)
                if filename:
                    saved_files.append(filename)
        
        return saved_files
    
    def _generate_final_results(self, signature: str, total_crashes: int, 
                               results: List[CrashAnalysisResult], saved_files: List[str]) -> Dict[str, Any]:
        """Generate final results and print summary"""
        # Print clean terminal summary
        self.statistics.print_terminal_summary(signature, total_crashes, saved_files)
        
        # Calculate rates
        rates = self.statistics.get_correlation_rates()
        
        # Return comprehensive statistics
        return {
            'signature': signature,
            'total_crashes_analyzed': total_crashes,
            'successful_analyses': self.statistics.successful_analyses,
            'partial_analyses': self.statistics.partial_analyses,
            'failed_analyses': self.statistics.failed_analyses,
            'crashes_with_any_match': self.statistics.crashes_with_any_match,
            'crashes_with_file_matches': self.statistics.crashes_with_file_matches,
            'crashes_with_direct_function_matches': self.statistics.crashes_with_direct_function_matches,
            'crashes_with_indirect_function_matches': self.statistics.crashes_with_indirect_function_matches,
            'total_file_matches': self.statistics.total_file_matches,
            'total_direct_function_matches': self.statistics.total_direct_function_matches,
            'total_indirect_function_matches': self.statistics.total_indirect_function_matches,
            'overall_correlation_rate': rates['overall'],
            'file_correlation_rate': rates['file'],
            'direct_function_correlation_rate': rates['direct_function'],
            'indirect_function_correlation_rate': rates['indirect_function'],
            'saved_analysis_files': saved_files,
            'detailed_results': results,
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def clear_cache(self):
        """Clear the analysis cache"""
        self._analysis_cache.clear()
        print("Analysis cache cleared")


def main():
    """Main function with clean output"""
    # Configure repository paths
    repo_paths = {
        'mozilla-central': 'mozilla-central',
        'mozilla-release': 'mozilla-release', 
        'mozilla-esr115': 'mozilla-esr115'
    }
    
    # Test signatures
    test_signatures = [
        "mozilla::ErrorLoadingSheet",
        "mozilla::dom::TypedArray_base<T>::ProcessFixedData",
        "mozilla::dom::quota::QuotaManager::Shutdown::<T>::operator()",
        "OOM | small",
        "mozilla::dom::ClientHandle::Control",
        "mozilla::dom::quota::QuotaManager::Shutdown::<T>::operator()",
        "mozilla::dom::ChildProcessChannelListener::OnChannelReady",
        "memmove"
    ]
    
    # Configuration
    signature_to_analyze = test_signatures[3]
    max_crashes = 5
    years_back = 1
    max_retries = 3
    
    # Check components
    if not all([CRASH_EXTRACTION_AVAILABLE, ROOTCAUSE_ANALYSIS_AVAILABLE, FUNCTION_CALL_ANALYSIS_AVAILABLE]):
        print("Missing required components - cannot proceed")
        return
    
    try:
        # Initialize matcher
        matcher = EnhancedCrashStackFunctionMatcher(repo_paths, max_retries=max_retries)
        
        # Run analysis
        results = matcher.analyze_crashes_for_signature(
            signature=signature_to_analyze,
            years_back=years_back,
            max_crashes=max_crashes
        )
        
        if 'error' in results:
            print(f"Analysis failed: {results['error']}")
            return
        
        print(f"\nANALYSIS SUMMARY:")
        print(f"Overall correlation rate: {results['overall_correlation_rate']:.1f}%")
        print(f"Crashes with matches: {results['crashes_with_any_match']}/{results['successful_analyses']}")
        print(f"Analysis files saved: {len(results['saved_analysis_files'])}")
        
        # Optional: Clear cache to free memory
        matcher.clear_cache()
        
    except Exception as e:
        print(f"Critical error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()