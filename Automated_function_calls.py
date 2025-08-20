#!/usr/bin/env python3
"""
Automated Crash Function Call Analyzer
Combines crash extraction with function call analysis

This script:
1. Extracts crashes for a given signature using the crash extraction functionality
2. For each extracted crash, runs the complete analysis pipeline
3. Analyzes function calls in the affected code
4. Generates comprehensive reports with function call details

Usage:
    python automated_crash_analyzer.py
"""

import requests
import json
import re
import subprocess
import os
import time
import tempfile
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path
from dataclasses import dataclass

# Import the crash extraction functionality
try:
    from Crash_filter import Step1SingleSignatureTest, CrashInfo
    CRASH_EXTRACTION_AVAILABLE = True
    print(" Crash extraction functionality imported successfully")
except ImportError as e:
    print(f" Warning: Could not import crash extraction functionality: {e}")
    print("Make sure the crash extraction script is in the same directory")
    CRASH_EXTRACTION_AVAILABLE = False

# Import the automated analysis functionality
try:
    from complete_rootcause import AutomatedMozillaCrashAnalyzer
    AUTOMATED_ANALYSIS_AVAILABLE = True
    print(" Automated analysis functionality imported successfully")
except ImportError as e:
    print(f" Warning: Could not import automated analysis functionality: {e}")
    print("Make sure the automated analysis script is in the same directory")
    AUTOMATED_ANALYSIS_AVAILABLE = False

# Tree-sitter integration
try:
    from c_parser import CParser
    TREE_SITTER_AVAILABLE = True
    print(" Tree-sitter C parser imported successfully")
except ImportError as e:
    print(f"  Warning: Tree-sitter C parser not available: {e}")
    TREE_SITTER_AVAILABLE = False


class UnifiedCrashFunctionAnalyzer:
    """
    Unified analyzer that combines crash extraction with function call analysis
    """
    
    def __init__(self, repo_paths: Dict[str, str], session: Optional[requests.Session] = None):
        """
        Initialize with paths to local Mozilla repositories
        """
        self.repo_paths = repo_paths
        self.session = session or requests.Session()
        self.session.headers.update({
            'User-Agent': 'Unified Crash Function Analyzer 1.0'
        })
        
        # Initialize crash extractor if available
        if CRASH_EXTRACTION_AVAILABLE:
            self.crash_extractor = Step1SingleSignatureTest()
            print(" Crash extractor initialized")
        else:
            self.crash_extractor = None
            print(" Crash extractor not available")
        
        # Initialize automated analyzer if available
        if AUTOMATED_ANALYSIS_AVAILABLE:
            self.automated_analyzer = AutomatedMozillaCrashAnalyzer(repo_paths, session)
            print(" Automated analyzer initialized")
        else:
            self.automated_analyzer = None
            print("Automated analyzer not available")
    
    def extract_function_calls(self, function_content: str) -> List[str]:
        """Extract function calls from function content with comprehensive patterns"""
        
        # Enhanced patterns to capture full method signatures and namespaces
        patterns = [
            # Full namespace and template method calls
            r'(\w+(?:::\w+)*(?:<[^>]*>)?(?:::\w+)*)\s*\(',
            
            # Complex template method calls with nested templates
            r'((?:\w+::)*\w+<[^<>]*(?:<[^<>]*>)*[^<>]*>::\w+)\s*\(',
            
            # Object method calls with full paths (obj.method or obj->method)
            r'(\w+(?:\.\w+)*(?:->\w+)*)\s*\(',
            
            # Simple function calls (fallback)
            r'(\w+)\s*\(',
            
            # Macro calls with parameters
            r'([A-Z_][A-Z0-9_]*)\s*\(',
            
            # Function pointer calls
            r'(\w+(?:_\w+)*)\s*\(',
            
            # Static class method calls
            r'(\w+::\w+)\s*\(',
        ]
        
        function_calls = set()
        
        # First pass: Extract all potential function calls
        for pattern in patterns:
            matches = re.findall(pattern, function_content, re.MULTILINE | re.DOTALL)
            function_calls.update(matches)
        
        # Enhanced pattern for complex template method calls
        complex_template_pattern = r'(\w+(?:::\w+)*<[^<>]*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>[^<>]*)*>::\w+)\s*\('
        complex_matches = re.findall(complex_template_pattern, function_content, re.MULTILINE | re.DOTALL)
        function_calls.update(complex_matches)
        
        # Pattern for very complex nested templates (like HashTable example)
        ultra_complex_pattern = r'(mozilla::(?:\w+::)*\w+<[^()]*>::\w+)\s*\('
        ultra_matches = re.findall(ultra_complex_pattern, function_content)
        function_calls.update(ultra_matches)
        
        # Mozilla crash stack trace format patterns
        mozilla_crash_patterns = [
            # Memory allocation functions like mozalloc_abort, moz_xmalloc
            r'(mozalloc_\w+)\s*\(',
            r'(moz_x\w+)\s*\(',
            
            # Mozilla detail namespace like mozilla::detail::HashTable<...>::changeTableSize
            r'(mozilla::detail::\w+<[^<>]*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>[^<>]*)*>::\w+)\s*\(',
            
            # Mozilla DOM functions like mozilla::dom::CallbackObject::FinishSlowJSInitIfMoreThanOneOwner
            r'(mozilla::dom::\w+::\w+)\s*\(',
            
            # Mozilla DOM binding functions like mozilla::dom::EventTarget_Binding::addEventListener
            r'(mozilla::dom::\w+_Binding::\w+)\s*\(',
            
            # Mozilla DOM binding detail like mozilla::dom::binding_detail::GenericMethod<...>
            r'(mozilla::dom::binding_detail::\w+<[^<>]*>)\s*\(',
            
            # JavaScript engine calls like js::Call, js::fun_apply, js::fun_call
            r'(js::\w+)\s*\(',
            
            # JavaScript BoundFunctionObject calls like js::BoundFunctionObject::call
            r'(js::\w+::\w+)\s*\(',
            
            # JavaScript handle types like JS::Handle<JSObject>, JS::MutableHandle<JS::Value>
            r'(JS::\w+<[^>]*>)\s*\(',
            
            # General Mozilla namespace patterns
            r'(mozilla::\w+::\w+(?:::\w+)*)\s*\(',
        ]
        
        # Extract Mozilla crash stack format calls
        for pattern in mozilla_crash_patterns:
            matches = re.findall(pattern, function_content, re.MULTILINE | re.DOTALL)
            function_calls.update(matches)
        
        # Filter out keywords but keep important system calls and full method signatures
        keywords_to_exclude = {
            'if', 'for', 'while', 'switch', 'return', 'break', 'continue',
            'sizeof', 'typeof', 'const_cast', 'static_cast', 'dynamic_cast',
            'reinterpret_cast', 'new', 'delete', 'this', 'class', 'struct',
            'true', 'false', 'nullptr', 'auto', 'decltype', 'typename',
            'template', 'namespace', 'using', 'typedef'
        }
        
        # Keep important calls but filter out obvious keywords
        filtered_calls = []
        for call in function_calls:
            # Clean up the call (remove extra spaces, etc.)
            call = call.strip()
            
            # Skip if it's a keyword
            if call in keywords_to_exclude:
                continue
                
            # Skip if it's just numbers or too short
            if len(call) <= 1 or call.isdigit():
                continue
                
            # Keep complex method signatures and namespaced calls
            if ('::' in call or 
                '<' in call or 
                '.' in call or 
                '->' in call or
                call.isupper() or  # Macros
                len(call) >= 3):   # Regular functions
                filtered_calls.append(call)
        
        return sorted(list(set(filtered_calls)))

    def analyze_function_calls_for_crashes(self, signature: str, 
                                         years_back: int = 1,
                                         sample_strategy: str = "monthly",
                                         dedup_strategy: str = "stack_trace",
                                         max_crashes_to_analyze: int = 5) -> Dict[str, Any]:
        """
        Main function that extracts crashes and analyzes function calls
        """
        print(f"\n UNIFIED CRASH FUNCTION CALL ANALYSIS")
        print("=" * 80)
        print(f" Signature: {signature}")
        print(f" Time period: {years_back} years back")
        print(f" Strategy: {sample_strategy} sampling, {dedup_strategy} deduplication")
        print(f" Max crashes to analyze: {max_crashes_to_analyze}")
        print("=" * 80)
        
        if not self.crash_extractor or not self.automated_analyzer:
            return {'error': 'Required components not available'}
        
        # Step 1: Extract crashes for the signature
        print(f"\n PHASE 1: EXTRACTING CRASHES FOR SIGNATURE")
        crashes = self.crash_extractor.test_specific_signature_longterm(
            signature=signature,
            years_back=years_back,
            sample_strategy=sample_strategy,
            dedup_strategy=dedup_strategy
        )
        
        if not crashes:
            return {
                'error': f'No crashes found for signature: {signature}',
                'signature': signature,
                'total_crashes_found': 0
            }
        
        print(f" Successfully extracted {len(crashes)} crashes")
        
        # Limit crashes to analyze
        crashes_to_analyze = crashes[:max_crashes_to_analyze]
        if len(crashes) > max_crashes_to_analyze:
            print(f" Limiting analysis to first {max_crashes_to_analyze} crashes")
        
        # Step 2: Run full analysis on each crash
        print(f"\n🔧 PHASE 2: RUNNING FULL ANALYSIS ON CRASHES")
        
        all_results = {}
        successful_analyses = 0
        failed_analyses = 0
        
        for i, crash in enumerate(crashes_to_analyze, 1):
            crash_id = crash.crash_id
            print(f"\n Analyzing crash {i}/{len(crashes_to_analyze)}: {crash_id}")
            print(f"    Date: {crash.date}")
            print(f"     Channel: {crash.product_channel}")
            
            try:
                # Run the full automated analysis
                analysis_result = self.automated_analyzer.full_analysis(crash_id, update_repos=False)
                
                if 'error' in analysis_result:
                    print(f"     Analysis failed: {analysis_result['error']}")
                    failed_analyses += 1
                    continue
                
                print(f"     Basic analysis successful")
                
                # Step 3: Enhanced analysis with function extraction
                enhanced_analysis = self.automated_analyzer.enhanced_extract_and_analyze_introducing_commits(analysis_result)
                
                if enhanced_analysis:
                    print(f"     Enhanced analysis successful")
                    
                    # Step 4: Function call analysis
                    function_call_results = self.analyze_function_calls_for_enhanced_analysis(
                        enhanced_analysis, crash_id
                    )
                    
                    all_results[crash_id] = {
                        'basic_analysis': analysis_result,
                        'enhanced_analysis': enhanced_analysis,
                        'function_call_analysis': function_call_results,
                        'original_crash_info': crash.__dict__
                    }
                    
                    successful_analyses += 1
                    print(f"     Complete analysis finished")
                else:
                    print(f"      Enhanced analysis failed")
                    failed_analyses += 1
                    
            except Exception as e:
                print(f"     Analysis failed with exception: {e}")
                failed_analyses += 1
                continue
        
        print(f"\n PHASE 2 SUMMARY:")
        print(f"     Successful analyses: {successful_analyses}")
        print(f"     Failed analyses: {failed_analyses}")
        print(f"     Success rate: {(successful_analyses/len(crashes_to_analyze)*100):.1f}%")
        
        # Step 5: Generate comprehensive function call report
        print(f"\n PHASE 3: GENERATING FUNCTION CALL REPORT")
        comprehensive_report = self.generate_comprehensive_function_call_report(
            all_results, signature, {
                'years_back': years_back,
                'sample_strategy': sample_strategy,
                'dedup_strategy': dedup_strategy,
                'max_crashes_analyzed': max_crashes_to_analyze,
                'total_crashes_extracted': len(crashes),
                'crashes_analyzed': len(crashes_to_analyze),
                'successful_analyses': successful_analyses,
                'failed_analyses': failed_analyses
            }
        )
        
        # Step 6: Save results
        self.save_comprehensive_results(comprehensive_report, signature)
        
        return comprehensive_report

    def analyze_function_calls_for_enhanced_analysis(self, enhanced_analysis: Dict[str, Any], 
                                                   crash_id: str) -> Dict[str, Any]:
        """
        Analyze function calls for each file in the enhanced analysis
        """
        print(f"     Analyzing function calls for crash {crash_id}")
        
        function_call_results = {}
        
        for filename, file_analysis in enhanced_analysis.items():
            print(f"       Processing file: {filename}")
            
            # Get introducing commit info
            introducing_commit_info = file_analysis.get('introducing_commit_info', {})
            if not introducing_commit_info:
                print(f"          No introducing commit info found")
                continue
            
            revision = introducing_commit_info.get('revision') or introducing_commit_info.get('short_revision')
            if not revision:
                print(f"          No revision found")
                continue
            
            # Get function details
            introducing_functions = file_analysis.get('introducing_functions', {})
            function_details = introducing_functions.get('function_details', {})
            
            if not function_details:
                print(f"          No function details found")
                continue
            
            print(f"         Found {len(function_details)} functions to analyze")
            
            # Analyze function calls for each function
            function_calls_map = {}
            
            for func_name, func_info in function_details.items():
                print(f"          🔧 Analyzing function: {func_name}")
                
                # Get function content from the repository
                func_content = self.get_function_content_from_enhanced_data(
                    func_name, func_info, revision, filename
                )
                
                if not func_content:
                    print(f"             Could not get content for {func_name}")
                    continue
                
                # Extract function calls
                function_calls = self.extract_function_calls(func_content)
                function_calls_map[func_name] = function_calls
                
                print(f"             Found {len(function_calls)} function calls")
                
                # Show some example calls
                if function_calls:
                    print(f"             Example calls: {', '.join(function_calls[:5])}")
                    if len(function_calls) > 5:
                        print(f"                ... and {len(function_calls) - 5} more")
            
            function_call_results[filename] = {
                'revision': revision,
                'function_calls_map': function_calls_map,
                'total_functions': len(function_details),
                'total_calls': sum(len(calls) for calls in function_calls_map.values()),
                'introducing_commit_info': introducing_commit_info
            }
            
            print(f"         File analysis complete: {len(function_details)} functions, {function_call_results[filename]['total_calls']} total calls")
        
        return function_call_results

    def get_function_content_from_enhanced_data(self, func_name: str, func_info: Dict[str, Any], 
                                              revision: str, filename: str) -> Optional[str]:
        """Get function content using line numbers from enhanced analysis"""
        try:
            # Find the repository containing this revision
            repo_path = None
            for repo_name, path in self.repo_paths.items():
                try:
                    result = subprocess.run(
                        ['hg', 'log', '-r', revision, '--template', '{node}'],
                        cwd=path,
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        repo_path = path
                        break
                except:
                    continue
            
            if not repo_path:
                print(f"             Could not find repository containing revision {revision}")
                return None
            
            # Get file content at revision
            result = subprocess.run(
                ['hg', 'cat', '-r', revision, filename],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                print(f"             Failed to get file content: {result.stderr}")
                return None
            
            file_content = result.stdout
            lines = file_content.split('\n')
            
            # Use the line numbers from enhanced analysis
            start_line = func_info.get('start', 0)
            end_line = func_info.get('end', 0)
            
            if start_line > 0 and end_line > 0 and start_line <= len(lines):
                # Extract function content using known boundaries
                func_lines = lines[start_line-1:end_line]  # Convert to 0-based indexing
                function_content = '\n'.join(func_lines)
                
                print(f"             Extracted {len(func_lines)} lines ({start_line}-{end_line})")
                
                return function_content
            else:
                print(f"             Invalid line numbers: start={start_line}, end={end_line}")
                return None
                
        except Exception as e:
            print(f"             Error getting function content: {e}")
            return None

    def generate_comprehensive_function_call_report(self, all_results: Dict[str, Any], 
                                                   signature: str, 
                                                   analysis_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a comprehensive report of function calls across all analyzed crashes
        """
        print(f"    📊 Generating comprehensive function call report")
        
        # Aggregate all function calls
        all_function_calls = {}
        all_affected_functions = []
        file_function_call_summary = {}
        crash_summaries = {}
        
        # Track Mozilla-specific patterns
        mozilla_patterns = {
            'mozilla_allocation': [],
            'mozilla_detail': [],
            'mozilla_dom': [],
            'js_engine': [],
            'mozilla_general': [],
            'critical_calls': []
        }
        
        critical_call_patterns = [
            'memmove', 'malloc', 'free', 'strcpy', 'sprintf', 'DuplicateHandle', 
            'ReadFile', 'WriteFile', 'MOZ_ASSERT', 'DCHECK', 'mozalloc_abort',
            'moz_xmalloc', 'moz_xrealloc', 'moz_xfree'
        ]
        
        for crash_id, crash_results in all_results.items():
            print(f"       Processing crash: {crash_id}")
            
            function_call_analysis = crash_results.get('function_call_analysis', {})
            
            crash_function_calls = {}
            crash_total_calls = 0
            crash_critical_calls = []
            
            for filename, file_analysis in function_call_analysis.items():
                function_calls_map = file_analysis.get('function_calls_map', {})
                
                if filename not in file_function_call_summary:
                    file_function_call_summary[filename] = {
                        'total_crashes': 0,
                        'all_calls': set(),
                        'function_occurrences': {},
                        'critical_calls': set()
                    }
                
                file_function_call_summary[filename]['total_crashes'] += 1
                
                for func_name, calls in function_calls_map.items():
                    crash_function_calls[f"{filename}::{func_name}"] = calls
                    crash_total_calls += len(calls)
                    
                    # Track function occurrences across crashes
                    if func_name not in file_function_call_summary[filename]['function_occurrences']:
                        file_function_call_summary[filename]['function_occurrences'][func_name] = 0
                    file_function_call_summary[filename]['function_occurrences'][func_name] += 1
                    
                    # Add all calls to the file summary
                    file_function_call_summary[filename]['all_calls'].update(calls)
                    
                    # Track Mozilla patterns
                    for call in calls:
                        if call.startswith('mozalloc_') or call.startswith('moz_x'):
                            mozilla_patterns['mozilla_allocation'].append({
                                'crash_id': crash_id,
                                'filename': filename,
                                'function': func_name,
                                'call': call
                            })
                        elif 'mozilla::detail::' in call:
                            mozilla_patterns['mozilla_detail'].append({
                                'crash_id': crash_id,
                                'filename': filename,
                                'function': func_name,
                                'call': call
                            })
                        elif 'mozilla::dom::' in call:
                            mozilla_patterns['mozilla_dom'].append({
                                'crash_id': crash_id,
                                'filename': filename,
                                'function': func_name,
                                'call': call
                            })
                        elif call.startswith('js::') or call.startswith('JS::'):
                            mozilla_patterns['js_engine'].append({
                                'crash_id': crash_id,
                                'filename': filename,
                                'function': func_name,
                                'call': call
                            })
                        elif call.startswith('mozilla::'):
                            mozilla_patterns['mozilla_general'].append({
                                'crash_id': crash_id,
                                'filename': filename,
                                'function': func_name,
                                'call': call
                            })
                        
                        # Track critical calls
                        if call in critical_call_patterns:
                            mozilla_patterns['critical_calls'].append({
                                'crash_id': crash_id,
                                'filename': filename,
                                'function': func_name,
                                'call': call
                            })
                            crash_critical_calls.append(call)
                            file_function_call_summary[filename]['critical_calls'].add(call)
                    
                    all_affected_functions.append({
                        'crash_id': crash_id,
                        'filename': filename,
                        'function_name': func_name,
                        'total_calls': len(calls),
                        'critical_calls': [call for call in calls if call in critical_call_patterns],
                        'mozilla_calls': [call for call in calls if call.startswith('mozilla::') or call.startswith('js::') or call.startswith('JS::')]
                    })
            
            crash_summaries[crash_id] = {
                'total_function_calls': crash_total_calls,
                'functions_analyzed': len(crash_function_calls),
                'critical_calls': list(set(crash_critical_calls)),
                'files_affected': len(function_call_analysis)
            }
            
            # Add to global function call tracking
            for func_identifier, calls in crash_function_calls.items():
                if func_identifier not in all_function_calls:
                    all_function_calls[func_identifier] = []
                all_function_calls[func_identifier].extend(calls)
        
        # Convert sets to lists for JSON serialization
        for filename in file_function_call_summary:
            file_function_call_summary[filename]['all_calls'] = sorted(list(file_function_call_summary[filename]['all_calls']))
            file_function_call_summary[filename]['critical_calls'] = sorted(list(file_function_call_summary[filename]['critical_calls']))
        
        # Generate final report
        comprehensive_report = {
            'signature': signature,
            'analysis_parameters': analysis_params,
            'function_call_analysis': {
                'total_crashes_analyzed': len(all_results),
                'total_function_calls_found': sum(len(calls) for calls in all_function_calls.values()),
                'total_unique_functions': len(all_function_calls),
                'mozilla_patterns_found': {
                    pattern: len(occurrences) for pattern, occurrences in mozilla_patterns.items()
                },
                'critical_calls_found': len(mozilla_patterns['critical_calls'])
            },
            'detailed_results': all_results,
            'function_call_summaries': {
                'by_crash': crash_summaries,
                'by_file': file_function_call_summary,
                'mozilla_patterns': mozilla_patterns,
                'all_affected_functions': all_affected_functions
            },
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Print summary
        print(f"     Function Call Analysis Summary:")
        print(f"       Total crashes analyzed: {len(all_results)}")
        print(f"       Total function calls found: {comprehensive_report['function_call_analysis']['total_function_calls_found']}")
        print(f"       Unique functions: {comprehensive_report['function_call_analysis']['total_unique_functions']}")
        print(f"        Critical calls found: {comprehensive_report['function_call_analysis']['critical_calls_found']}")
        
        print(f"        Mozilla patterns:")
        for pattern, count in comprehensive_report['function_call_analysis']['mozilla_patterns_found'].items():
            if count > 0:
                print(f"        • {pattern}: {count}")
        
        return comprehensive_report

    def save_comprehensive_results(self, comprehensive_report: Dict[str, Any], signature: str):
        """
        Save the comprehensive results to files
        """
        print(f" Saving comprehensive results...")
        
        # Generate safe filename
        safe_signature = signature.replace(':', '_').replace('/', '_').replace('\\', '_').replace('|', '_')
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        
        # Save main report
        main_report_filename = f"unified_crash_function_analysis_{safe_signature}_{timestamp}.json"
        try:
            with open(main_report_filename, 'w', encoding='utf-8') as f:
                json.dump(comprehensive_report, f, indent=2, default=str)
            print(f"   Main report saved: {main_report_filename}")
        except Exception as e:
            print(f"   Failed to save main report: {e}")
        
        # Save function call summary
        function_summary = {
            'signature': signature,
            'generated_at': comprehensive_report['generated_at'],
            'summary': comprehensive_report['function_call_analysis'],
            'mozilla_patterns': comprehensive_report['function_call_summaries']['mozilla_patterns'],
            'critical_findings': [
                pattern for pattern in comprehensive_report['function_call_summaries']['mozilla_patterns']['critical_calls']
            ],
            'file_summaries': comprehensive_report['function_call_summaries']['by_file']
        }
        
        summary_filename = f"function_call_summary_{safe_signature}_{timestamp}.json"
        try:
            with open(summary_filename, 'w', encoding='utf-8') as f:
                json.dump(function_summary, f, indent=2, default=str)
            print(f"   Function call summary saved: {summary_filename}")
        except Exception as e:
            print(f"   Failed to save function call summary: {e}")
        
        print(f"  📁 Results saved with timestamp: {timestamp}")


def main():
    """
    Main function for the unified crash function call analyzer
    """
    print(" UNIFIED CRASH FUNCTION CALL ANALYZER")
    print("=" * 80)
    
    # Check if all required components are available
    if not CRASH_EXTRACTION_AVAILABLE:
        print(" Crash extraction functionality not available!")
        print("Make sure the crash extraction script is in the same directory.")
        return
    
    if not AUTOMATED_ANALYSIS_AVAILABLE:
        print("Automated analysis functionality not available!")
        print("Make sure the automated analysis script is in the same directory.")
        return
    
    print(" All required components available")
    
    # Configure paths to your local repositories
    # UPDATE THESE PATHS to match your local repository locations
    repo_paths = {
        'mozilla-central': 'mozilla-central',
        'mozilla-release': 'mozilla-release', 
        'mozilla-esr115': 'mozilla-esr115'
    }
    
    # Example signatures - modify as needed
    EXAMPLE_SIGNATURES = [
        "OOM | small",
        "mozilla::dom::ClientHandle::Control",
        "mozilla::dom::quota::QuotaManager::Shutdown",
        "mozilla::dom::ChildProcessChannelListener::OnChannelReady",
        "memmove",
        "ReadFile"
    ]
    
    # Configuration
    signature_to_analyze = "OOM | small"  # Change this to analyze different signatures
    years_back = 1
    sample_strategy = "monthly"
    dedup_strategy = "stack_trace"
    max_crashes_to_analyze = 3  # Start small for testing
    
    print(f" Configuration:")
    print(f"   Signature: {signature_to_analyze}")
    print(f"   Years back: {years_back}")
    print(f"   Sample strategy: {sample_strategy}")
    print(f"   Dedup strategy: {dedup_strategy}")
    print(f"   Max crashes: {max_crashes_to_analyze}")
    print(f"   Repository paths: {list(repo_paths.keys())}")
    print("=" * 80)
    
    try:
        # Initialize the unified analyzer
        analyzer = UnifiedCrashFunctionAnalyzer(repo_paths)
        
        # Run the unified analysis
        results = analyzer.analyze_function_calls_for_crashes(
            signature=signature_to_analyze,
            years_back=years_back,
            sample_strategy=sample_strategy,
            dedup_strategy=dedup_strategy,
            max_crashes_to_analyze=max_crashes_to_analyze
        )
        
        if 'error' in results:
            print(f" Analysis failed: {results['error']}")
            return
        
        print(f"\n UNIFIED ANALYSIS COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        
        # Print detailed summary
        analysis_summary = results.get('function_call_analysis', {})
        print(f" ANALYSIS SUMMARY:")
        print(f"  Crashes analyzed: {analysis_summary.get('total_crashes_analyzed', 0)}")
        print(f"   Total function calls: {analysis_summary.get('total_function_calls_found', 0)}")
        print(f"   Unique functions: {analysis_summary.get('total_unique_functions', 0)}")
        print(f"    Critical calls: {analysis_summary.get('critical_calls_found', 0)}")
        
        # Show Mozilla patterns
        mozilla_patterns = analysis_summary.get('mozilla_patterns_found', {})
        if any(count > 0 for count in mozilla_patterns.values()):
            print(f"\n  MOZILLA PATTERNS FOUND:")
            for pattern, count in mozilla_patterns.items():
                if count > 0:
                    print(f"    • {pattern.replace('_', ' ').title()}: {count}")
        
        # Show top affected functions
        all_affected_functions = results.get('function_call_summaries', {}).get('all_affected_functions', [])
        if all_affected_functions:
            print(f"\n TOP AFFECTED FUNCTIONS:")
            # Sort by total calls
            sorted_functions = sorted(all_affected_functions, key=lambda x: x['total_calls'], reverse=True)
            for i, func in enumerate(sorted_functions[:5], 1):
                print(f"  {i}. {func['function_name']} ({func['total_calls']} calls)")
                print(f"      {func['filename']}")
                print(f"      Crash: {func['crash_id']}")
                if func['critical_calls']:
                    print(f"     ⚠️  Critical: {', '.join(func['critical_calls'])}")
                if func['mozilla_calls']:
                    print(f"     🏗️  Mozilla: {len(func['mozilla_calls'])} calls")
        
        # Show critical findings
        mozilla_patterns_detail = results.get('function_call_summaries', {}).get('mozilla_patterns', {})
        critical_calls = mozilla_patterns_detail.get('critical_calls', [])
        if critical_calls:
            print(f"\n  CRITICAL FUNCTION CALLS FOUND:")
            critical_by_call = {}
            for item in critical_calls:
                call = item['call']
                if call not in critical_by_call:
                    critical_by_call[call] = []
                critical_by_call[call].append(item)
            
            for call, occurrences in sorted(critical_by_call.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
                print(f"   {call}: {len(occurrences)} occurrence(s)")
                for occurrence in occurrences[:2]:  # Show first 2 occurrences
                    print(f"      {occurrence['filename']} -> {occurrence['function']}")
                    print(f"      Crash: {occurrence['crash_id']}")
                if len(occurrences) > 2:
                    print(f"     ... and {len(occurrences) - 2} more")
        
        # Show files with most function calls
        file_summaries = results.get('function_call_summaries', {}).get('by_file', {})
        if file_summaries:
            print(f"\n FILES WITH MOST FUNCTION CALLS:")
            sorted_files = sorted(file_summaries.items(), 
                                key=lambda x: len(x[1]['all_calls']), 
                                reverse=True)
            for i, (filename, summary) in enumerate(sorted_files[:3], 1):
                print(f"  {i}. {filename}")
                print(f"      Total calls: {len(summary['all_calls'])}")
                print(f"      Crashes: {summary['total_crashes']}")
                print(f"      Functions: {len(summary['function_occurrences'])}")
                if summary['critical_calls']:
                    print(f"       Critical: {', '.join(list(summary['critical_calls'])[:3])}")
        
        print(f"\n NEXT STEPS:")
        print(f"  1. Check the generated JSON reports for detailed analysis")
        print(f"  2. Look for patterns in critical function calls")
        print(f"  3. Investigate Mozilla-specific patterns that appear frequently")
        print(f"  4. Focus on functions that appear in multiple crashes")
        print(f"  5. Adjust signature or parameters to analyze different crash patterns")
        
        print(f"\n TIPS:")
        print(f"  • Critical calls like 'memmove', 'malloc', 'free' often indicate memory issues")
        print(f"  • Mozilla allocation functions (mozalloc_*) suggest memory allocation problems")
        print(f"  • High function call counts in a single function may indicate complexity issues")
        print(f"  • Functions appearing in multiple crashes are likely high-impact areas")
        
    except Exception as e:
        print(f" Unified analysis failed with error: {e}")
        import traceback
        traceback.print_exc()
        
        print(f"\n TROUBLESHOOTING:")
        print(f"  1. Ensure all repository paths are correct")
        print(f"  2. Check that crash extraction and automated analysis scripts are available")
        print(f"  3. Verify network connectivity for crash data retrieval")
        print(f"  4. Try reducing max_crashes_to_analyze for testing")


def test_specific_crash_analysis(crash_id: str, repo_paths: Dict[str, str]):
    """
    Test function to analyze a specific crash ID for function calls
    """
    print(f" TESTING SPECIFIC CRASH ANALYSIS")
    print(f" Crash ID: {crash_id}")
    print("=" * 50)
    
    try:
        if not AUTOMATED_ANALYSIS_AVAILABLE:
            print(" Automated analysis not available")
            return
        
        # Initialize analyzer
        analyzer = UnifiedCrashFunctionAnalyzer(repo_paths)
        
        if not analyzer.automated_analyzer:
            print(" Could not initialize automated analyzer")
            return
        
        # Run analysis on specific crash
        print(" Running full analysis...")
        analysis_result = analyzer.automated_analyzer.full_analysis(crash_id, update_repos=False)
        
        if 'error' in analysis_result:
            print(f" Analysis failed: {analysis_result['error']}")
            return
        
        print(" Basic analysis successful")
        
        # Enhanced analysis
        print(" Running enhanced analysis...")
        enhanced_analysis = analyzer.automated_analyzer.enhanced_extract_and_analyze_introducing_commits(analysis_result)
        
        if not enhanced_analysis:
            print(" Enhanced analysis failed")
            return
        
        print(" Enhanced analysis successful")
        
        # Function call analysis
        print(" Analyzing function calls...")
        function_call_results = analyzer.analyze_function_calls_for_enhanced_analysis(
            enhanced_analysis, crash_id
        )
        
        print("\n FUNCTION CALL RESULTS:")
        total_calls = 0
        total_functions = 0
        
        for filename, file_analysis in function_call_results.items():
            calls_map = file_analysis.get('function_calls_map', {})
            file_total_calls = sum(len(calls) for calls in calls_map.values())
            total_calls += file_total_calls
            total_functions += len(calls_map)
            
            print(f"   {filename}:")
            print(f"     Functions: {len(calls_map)}")
            print(f"     Total calls: {file_total_calls}")
            
            # Show example functions
            for i, (func_name, calls) in enumerate(list(calls_map.items())[:2]):
                print(f"    • {func_name}: {len(calls)} calls")
                if calls:
                    example_calls = calls[:3]
                    print(f"      Examples: {', '.join(example_calls)}")
                    if len(calls) > 3:
                        print(f"      ... and {len(calls) - 3} more")
        
        print(f"\n SUMMARY:")
        print(f"   Total function calls: {total_calls}")
        print(f"   Total functions: {total_functions}")
        print(f"   Files analyzed: {len(function_call_results)}")
        
        print(f"\n Test completed successfully!")
        
    except Exception as e:
        print(f" Test failed: {e}")
        import traceback
        traceback.print_exc()


def analyze_signature_with_custom_params():
    """
    Interactive function to analyze a signature with custom parameters
    """
    print(" CUSTOM SIGNATURE ANALYSIS")
    print("=" * 40)
    
    # Get user input
    signature = input("Enter signature to analyze (or press Enter for 'OOM | small'): ").strip()
    if not signature:
        signature = "OOM | small"
    
    try:
        years_back = int(input("Years back to search (default 1): ") or "1")
        max_crashes = int(input("Max crashes to analyze (default 3): ") or "3")
    except ValueError:
        years_back = 1
        max_crashes = 3
    
    sample_strategy = input("Sample strategy (monthly/weekly/daily, default monthly): ").strip() or "monthly"
    dedup_strategy = input("Dedup strategy (stack_trace/build_id, default stack_trace): ").strip() or "stack_trace"
    
    repo_paths = {
        'mozilla-central': 'mozilla-central',
        'mozilla-release': 'mozilla-release', 
        'mozilla-esr115': 'mozilla-esr115'
    }
    
    print(f"\n Starting analysis with:")
    print(f"   Signature: {signature}")
    print(f"   Years back: {years_back}")
    print(f"   Max crashes: {max_crashes}")
    print(f"   Sample strategy: {sample_strategy}")
    print(f"  Dedup strategy: {dedup_strategy}")
    
    try:
        analyzer = UnifiedCrashFunctionAnalyzer(repo_paths)
        results = analyzer.analyze_function_calls_for_crashes(
            signature=signature,
            years_back=years_back,
            sample_strategy=sample_strategy,
            dedup_strategy=dedup_strategy,
            max_crashes_to_analyze=max_crashes
        )
        
        if 'error' not in results:
            print(" Custom analysis completed successfully!")
        else:
            print(f" Analysis failed: {results['error']}")
            
    except Exception as e:
        print(f" Custom analysis failed: {e}")


if __name__ == "__main__":
    """
    Entry point for the unified crash function call analyzer
    """
    print(" UNIFIED CRASH FUNCTION CALL ANALYZER")
    print("Combines crash extraction with comprehensive function call analysis")
    print("=" * 80)
    
    # Check dependencies
    if not CRASH_EXTRACTION_AVAILABLE:
        print(" ERROR: Crash extraction functionality not available!")
        print("Make sure 'Crash_filter.py' is in the same directory.")
        exit(1)
    
    if not AUTOMATED_ANALYSIS_AVAILABLE:
        print(" ERROR: Automated analysis functionality not available!")
        print("Make sure 'automated_analysis.py' is in the same directory.")
        exit(1)
    
    print(" All dependencies available")
    
    # Show menu
    print("\n AVAILABLE OPTIONS:")
    print("1. Run automated analysis with default settings")
    print("2. Test with specific crash ID")
    print("3. Custom signature analysis")
    print("4. Exit")
    
    try:
        choice = input("\nSelect option (1-4): ").strip()
        
        if choice == "1":
            main()
        elif choice == "2":
            crash_id = input("Enter crash ID: ").strip()
            if crash_id:
                repo_paths = {
                    'mozilla-central': 'mozilla-central',
                    'mozilla-release': 'mozilla-release', 
                    'mozilla-esr115': 'mozilla-esr115'
                }
                test_specific_crash_analysis(crash_id, repo_paths)
            else:
                print(" No crash ID provided")
        elif choice == "3":
            analyze_signature_with_custom_params()
        elif choice == "4":
            print("Goodbye!")
        else:
            print(" Invalid choice. Running default analysis...")
            main()
            
    except KeyboardInterrupt:
        print("\n\n Analysis interrupted by user")
    except Exception as e:
        print(f"\n Unexpected error: {e}")
        import traceback
        traceback.print_exc()

    