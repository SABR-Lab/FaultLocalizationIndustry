
"""
Data Models for Mozilla Crash Analysis Tool

This module contains all the data classes and models used throughout the crash analysis system.
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any


@dataclass
class CommitInfo:
    revision: str
    author: str
    date: str
    description: str
    files_changed: List[str]
    bug_numbers: List[str]
    channel: str


@dataclass
class FileChange:
    filename: str
    added_lines: List[int]
    removed_lines: List[int]
    functions_affected: List[str]
    diff_content: str


@dataclass
class FunctionAnalysis:
    name: str
    start_line: int
    end_line: int
    size: int
    return_type: str
    parameters: List[str]
    lines_added_in_commit: List[int]
    lines_removed_in_fix: List[int]
    is_newly_introduced: bool
    code_content: str