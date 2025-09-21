#!/usr/bin/env python3
"""
Output Manager for Clean Terminal Display

Centralizes all print statements and provides clean, non-repetitive output
"""

import time
from typing import List, Dict, Any, Optional


class OutputManager:
    """Manages clean terminal output without duplication"""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._last_messages = set()  # Track to avoid duplicates
        self._current_section = None
    
    def _should_print(self, message: str, allow_duplicates: bool = False) -> bool:
        """Check if message should be printed (avoid duplicates)"""
        if allow_duplicates:
            return True
        
        message_key = message.strip().lower()
        if message_key in self._last_messages:
            return False
        
        self._last_messages.add(message_key)
        return True
    
    def section_header(self, title: str, char: str = "="):
        """Print a clean section header"""
        if self._current_section == title:
            return  # Don't repeat the same section
        
        self._current_section = title
        print(f"\n{char * 60}")
        print(f"{title.upper()}")
        print(f"{char * 60}")
    
    def subsection(self, title: str):
        """Print a subsection header"""
        print(f"\n{title}")
        print("-" * len(title))
    
    def info(self, message: str, indent: int = 0):
        """Print info message with optional indentation"""
        prefix = "  " * indent
        if self._should_print(message):
            print(f"{prefix}{message}")
    
    def success(self, message: str, indent: int = 0):
        """Print success message"""
        prefix = "  " * indent
        if self._should_print(message):
            print(f"{prefix}✓ {message}")
    
    def warning(self, message: str, indent: int = 0):
        """Print warning message"""
        prefix = "  " * indent
        if self._should_print(message):
            print(f"{prefix}⚠ {message}")
    
    def error(self, message: str, indent: int = 0):
        """Print error message"""
        prefix = "  " * indent
        print(f"{prefix}✗ {message}")
    
    def progress(self, current: int, total: int, item_name: str = "item"):
        """Print progress information"""
        print(f"[{current}/{total}] Processing {item_name}...")
    
    def crash_summary(self, crash_id: str, build_id: str, channel: str, revision: str):
        """Print clean crash summary"""
        print(f"\nCrash Analysis Summary:")
        print(f"  ID: {crash_id}")
        print(f"  Build: {build_id}")
        print(f"  Channel: {channel}")
        print(f"  Revision: {revision[:12]}")
    
    def file_analysis_summary(self, total_files: int, code_files: int, analyzed_files: int):
        """Print file analysis summary"""
        print(f"\nFile Analysis:")
        print(f"  Total files changed: {total_files}")
        print(f"  Code files: {code_files}")
        print(f"  Files analyzed: {analyzed_files}")
    
    def commit_analysis_result(self, filename: str, has_exact: bool, has_potential: bool, 
                              exact_revision: str = None, potential_revision: str = None):
        """Print commit analysis result"""
        if has_exact:
            print(f"  {filename}: EXACT match ({exact_revision[:12]})")
        elif has_potential:
            print(f"  {filename}: Potential match ({potential_revision[:12]})")
        else:
            print(f"  {filename}: No introducing commit found")
    
    def analysis_stats(self, total_analyzed: int, successful: int, with_matches: int):
        """Print final analysis statistics"""
        success_rate = (successful / total_analyzed * 100) if total_analyzed > 0 else 0
        match_rate = (with_matches / successful * 100) if successful > 0 else 0
        
        print(f"\nAnalysis Results:")
        print(f"  Files analyzed: {total_analyzed}")
        print(f"  Successful: {successful} ({success_rate:.1f}%)")
        print(f"  With matches: {with_matches} ({match_rate:.1f}%)")
    
    def function_summary(self, filename: str, total_functions: int, affected_functions: int):
        """Print function analysis summary"""
        if self.verbose and total_functions > 0:
            print(f"  {filename}: {affected_functions}/{total_functions} functions affected")
    
    def pipeline_phase(self, phase_number: int, phase_name: str):
        """Print pipeline phase header"""
        print(f"\nPHASE {phase_number}: {phase_name.upper()}")
        print("-" * 50)
    
    def final_summary(self, signature: str, crashes_analyzed: int, successful: int, 
                     total_functions: int, files_with_matches: int):
        """Print final comprehensive summary"""
        self.section_header("ANALYSIS COMPLETE")
        
        success_rate = (successful / crashes_analyzed * 100) if crashes_analyzed > 0 else 0
        
        print(f"Signature: {signature}")
        print(f"Crashes analyzed: {crashes_analyzed}")
        print(f"Successful analyses: {successful} ({success_rate:.1f}%)")
        print(f"Total functions affected: {total_functions}")
        print(f"Files with matches: {files_with_matches}")
    
    def debug(self, message: str, indent: int = 0):
        """Print debug message only in verbose mode"""
        if self.verbose:
            prefix = "  " * indent
            print(f"{prefix}DEBUG: {message}")
    
    def clear_cache(self):
        """Clear the message cache"""
        self._last_messages.clear()
        self._current_section = None