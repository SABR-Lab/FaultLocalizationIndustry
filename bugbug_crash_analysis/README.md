# BugBug Crash Analysis - Fault Localization for Mozilla Firefox

## Overview

This project performs **fault localization for Mozilla Firefox crashes** by correlating crash data with bug information and identifying the exact commits that introduced and fixed bugs. The analysis pipeline traces crashes back to their root causes using the BugBug database, mercurial commit history, and advanced method-level matching techniques.

## Project Goal

For a given Firefox crash signature, this project:
1. Finds related crashes from the Mozilla Crash Stats API
2. Extracts associated bug numbers
3. Retrieves comprehensive bug information from the BugBug database
4. Identifies regressor commits (commits that introduced the bug)
5. Identifies fixing commits (commits that fixed the bug)
6. Performs method-level analysis to confirm which commits affected the same code
7. Extracts relevant test cases and diffs for further analysis

## Workflow Overview

Crash Signature
    ↓
Extract Bugs from Crash Stats API
    ↓
Validate Bugs in BugBug Database
    ↓
Extract Bug Details & Regression Information
    ↓
Retrieve Commit Diffs & Full Content
    ↓
Find Overlapping Files (Fixed & Regressor)
    ↓
Parse Methods & Line Ranges (TreeSitter)
    ↓
Match Methods Between Commits
    ↓
Confirm Regressor & Fixing Commits
    ↓
Extract Diffs & Test Cases



## Pipeline Steps

### **Data Preparation**

** utils/bugbug_dataset_extractor.py **
- Downloads BugBug datasets (bugs, commits, revisions) to local machine
- Initializes the data source for all subsequent analysis

** utils/bugbug_utils.py **
- Provides singleton cache for BugBug data access
- Extracts deployment information across Firefox release channels (mozilla-central, release, esr115, autoland)
- Standardizes and normalizes bug metadata

### **Pipeline Steps**

** Step1_crash_bug_mapper.py **
- Maps bugs extracted from crash data against the local BugBug database
- Keeps common bugs and filters out unmatched entries
- Output: Validated bug IDs present in both crash data and BugBug

** Step2_bug_fetcher.py **
- Validates bug numbers against the BugBug database
- Checks if bugs exist and compares crash signatures
- Outputs full bug metadata for matched bugs
- Identifies and reports any mismatches or missing data

** Step3_bug_details_extractor.py **
- For each validated bug, determines if it has a regressor bug
- Extracts regression relationships
- Identifies fixing commits associated with each bug
- Matches files modified across fixing and regressor commits

** Step4_diff_extractor.py **
- Retrieves complete diff content for fixing commits
- Retrieves complete diff content for regressor commits
- Stores diffs for subsequent analysis

** Step5_overlapping_files.py **
- Identifies files changed in both fixing commits AND regressor commits
- Filters commits to focus on files with mutual changes
- Output: Set of common files modified across fix/regressor pairs

** Step6_overlappingFiles_fullContent.py **
- For common files, retrieves the full file content from parent commits
- Captures parent state of fixing commits
- Captures parent state of regressor commits
- Prepares content for method parsing

** Step7_parser.py **
- Uses TreeSitter to parse full file content
- Extracts all method/function definitions
- Identifies method names, start lines, and end lines
- Creates method line-range mappings for both fixing and regressor commits

** Step8_diff_methods_matcher.py **
- Compares extracted diffs with method line ranges
- Identifies which methods were changed in each diff
- Maps diff line changes to specific method names
- Output: List of methods modified in fixing commits and regressor commits

** Step9_fixing_regressor_matcher.py **
- Compares method names changed in fixing commits vs regressor commits
- Matches methods that appear in both
- **Confirmation**: Matching methods confirm the regressor commit introduced the bug and the fixing commit resolved it

** Step10_matched_method_diff.py **
- For files with matching methods, extracts the specific diffs
- Captures diff of the regressor commit (the buggy change)
- Captures diff of the fixing commit (the fix)
- Prepares data for test case analysis and validation

## Data Sources

### Mozilla Repositories

This project uses local clones of Mozilla's mercurial repositories to retrieve commit history, diffs, and file content:

- **Mozilla Autoland**: https://hg.mozilla.org/integration/autoland
- **Mozilla Central**: https://hg.mozilla.org/mozilla-central
- **Mozilla Release**: https://hg.mozilla.org/releases/mozilla-release
- **Mozilla ESR 115**: https://hg.mozilla.org/releases/mozilla-esr115

### Local Setup

Clone these repositories locally:

bash
hg clone https://hg.mozilla.org/integration/autoland ~/mozilla-repos/autoland
hg clone https://hg.mozilla.org/mozilla-central ~/mozilla-repos/mozilla-central
hg clone https://hg.mozilla.org/releases/mozilla-release ~/mozilla-repos/mozilla-release
hg clone https://hg.mozilla.org/releases/mozilla-esr115 ~/mozilla-repos/mozilla-esr115


### External APIs

- **Mozilla Crash Stats API**: Provides crash data and signatures
- **BugBug Database**: Contains Firefox bug information, relationships, and metadata
- **TreeSitter**: Parses source code to extract method definitions and syntax information

## Dependencies

-  bugbug - BugBug database interface
-  tree-sitter - Source code parsing
-  mercurial (hg) - Version control operations
-  Local mercurial repository clones (see above)

##Execution Flow:

Step 1 → saves results locally
Step 2 → reads Step 1's results → saves its own results locally
Step 3 → reads Step 2's results → saves its own results locally
Step 4 → reads Step 3's results → saves its own results locally
(and so on through Step 10)

Each step performs one specific analysis task and outputs a local file. The following step cannot begin until the previous step completes and saves its output.

## Key Concepts

### Regressor Commit
A commit that introduced a bug. Identified by comparing method-level changes across fixing and potential regressor commits.

### Fixing Commit
A commit that resolved a bug. Identified through BugBug's regression relationships and validated by method matching.

### Method-Level Matching
The core validation technique: if the same methods are modified in both a fixing commit and a potential regressor commit, it confirms the relationship between them.

### Overlapping Files
Files modified in both fixing and regressor commits. These files are the focus of detailed analysis since they contain the bug and its fix.

## Output

The final analysis produces:
- Confirmed regressor/fixing commit pairs
- Method names changed in each commit
- Diffs showing exact changes
- Associated test cases (for validation)
- Fault localization results at method granularity

