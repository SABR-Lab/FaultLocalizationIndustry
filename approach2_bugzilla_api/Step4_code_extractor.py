#!/usr/bin/env python3
"""
Step 4: Code Extractor for Regression Analysis (Modified)
Extracts actual code diffs for fixing and regression commits
- Saves each diff as a separate .diff text file
- Organizes files in folders by bug
"""

import json
import requests
from datetime import datetime
from typing import Dict, List, Optional
import time
import os


class CodeExtractor:
    """Extracts code diffs from commits and saves as .diff files"""
    
    def __init__(self, base_url: str = "https://hg.mozilla.org", output_dir: str = "extracted_diffs"):
        """
        Initialize the extractor
        
        Args:
            base_url: Base URL for Mozilla's Mercurial repository
            output_dir: Directory to save extracted diffs
        """
        self.base_url = base_url
        self.session = requests.Session()
        self.rate_limit_delay = 0.5  # Delay between requests in seconds
        self.output_dir = output_dir
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
    
    def get_commit_diff(self, repository: str, commit_hash: str) -> Optional[str]:
        """
        Fetch the diff for a specific commit
        
        Args:
            repository: Repository name (e.g., 'mozilla-central', 'mozilla-autoland')
            commit_hash: Full commit hash
            
        Returns:
            Diff text or None if failed
        """
        url = f"{self.base_url}/{repository}/raw-rev/{commit_hash}"
        
        try:
            time.sleep(self.rate_limit_delay)  # Rate limiting
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.text
            
        except requests.exceptions.RequestException as e:
            print(f"    Error fetching commit {commit_hash[:12]}: {e}")
            return None
    
    def save_diff_file(self, bug_dir: str, filename: str, diff_content: str) -> bool:
        """
        Save diff content to a .diff file
        
        Args:
            bug_dir: Bug directory path
            filename: Name of the diff file
            diff_content: The diff text content
            
        Returns:
            True if successful, False otherwise
        """
        try:
            filepath = os.path.join(bug_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(diff_content)
            return True
        except Exception as e:
            print(f"      Error saving file {filename}: {e}")
            return False
    
    def extract_fixing_commits(self, bug_id: str, bug_dir: str, analysis: Dict) -> int:
        """
        Extract diffs for ALL fixing commits and combine into one file
        
        Args:
            bug_id: Bug ID
            bug_dir: Directory for this bug
            analysis: Bug analysis from Step 3
            
        Returns:
            Number of successfully extracted commits
        """
        print(f"\n  Extracting ALL fixing commits for Bug {bug_id}...")
        
        fixing_commits = analysis.get('fixing_commits', [])
        if not fixing_commits:
            print(f"    No fixing commits found")
            return 0
        
        print(f"    Found {len(fixing_commits)} fixing commit(s)")
        
        # Combine all fixing commits into one file
        combined_diff = ""
        extracted_count = 0
        
        for i, commit in enumerate(fixing_commits, 1):
            commit_hash = commit['commit_hash']
            short_hash = commit['short_hash']
            print(f"    [{i}/{len(fixing_commits)}] Fetching {short_hash}...")
            
            # Try autoland first, then central
            diff_content = self.get_commit_diff('mozilla-autoland', commit_hash)
            if not diff_content:
                diff_content = self.get_commit_diff('mozilla-central', commit_hash)
            
            if diff_content:
                # Add separator and commit info
                if extracted_count > 0:
                    combined_diff += "\n\n" + "="*80 + "\n"
                
                combined_diff += f"# FIXING COMMIT {i}/{len(fixing_commits)}\n"
                combined_diff += f"# Commit: {short_hash}\n"
                combined_diff += f"# Author: {commit.get('author', 'Unknown')}\n"
                combined_diff += "="*80 + "\n\n"
                combined_diff += diff_content
                
                extracted_count += 1
                print(f"      ✓ Added to fix.diff ({len(diff_content)} bytes)")
            else:
                print(f"      ✗ Failed to fetch")
        
        # Save combined file
        if extracted_count > 0:
            if self.save_diff_file(bug_dir, "fix.diff", combined_diff):
                print(f"    ✓ Saved all {extracted_count} fixing commits to fix.diff")
            else:
                print(f"    ✗ Failed to save fix.diff")
        
        return extracted_count
    
    def extract_matching_regression_commits(self, bug_id: str, bug_dir: str, 
                                           regressor_detail: Dict) -> int:
        """
        Extract diffs ONLY for regression commits that modified the same files as the fix
        
        Args:
            bug_id: Bug ID
            bug_dir: Directory for this bug
            regressor_detail: Regressor detail from regression_chain
            
        Returns:
            Number of successfully extracted commits
        """
        regressor_bug_id = regressor_detail['bug_id']
        matching_commits = regressor_detail.get('matching_commits', [])
        
        if not matching_commits:
            print(f"    No matching commits for regressor Bug {regressor_bug_id}")
            return 0
        
        print(f"    Found {len(matching_commits)} matching commit(s) for regressor Bug {regressor_bug_id}")
        
        # Combine all regression commits into one file
        combined_diff = ""
        extracted_count = 0
        
        for i, commit in enumerate(matching_commits, 1):
            commit_hash = commit['commit_hash']
            short_hash = commit['short_hash']
            overlap_count = commit['file_overlap_count']
            
            print(f"      [{i}/{len(matching_commits)}] Fetching {short_hash} (overlap: {overlap_count} files)...")
            
            # Try autoland first, then central
            diff_content = self.get_commit_diff('mozilla-autoland', commit_hash)
            if not diff_content:
                diff_content = self.get_commit_diff('mozilla-central', commit_hash)
            
            if diff_content:
                # Add separator and commit info
                if extracted_count > 0:
                    combined_diff += "\n\n" + "="*80 + "\n"
                
                combined_diff += f"# REGRESSION COMMIT {i}/{len(matching_commits)}\n"
                combined_diff += f"# Regressor Bug: {regressor_bug_id}\n"
                combined_diff += f"# Commit: {short_hash}\n"
                combined_diff += f"# Author: {commit.get('author', 'Unknown')}\n"
                combined_diff += f"# File Overlap: {overlap_count} files\n"
                combined_diff += f"# Overlapping Files: {', '.join(commit.get('overlapping_files', [])[:3])}\n"
                combined_diff += "="*80 + "\n\n"
                combined_diff += diff_content
                
                extracted_count += 1
                print(f"        ✓ Added to regression.diff ({len(diff_content)} bytes)")
            else:
                print(f"        ✗ Failed to fetch")
        
        return extracted_count
    
    def extract_bug_code(self, bug_id: str, analysis: Dict) -> Dict:
        """
        Extract code for a single bug and save as .diff files
        
        Args:
            bug_id: Bug ID
            analysis: Bug analysis from Step 3
            
        Returns:
            Summary of extraction
        """
        print(f"\n{'='*60}")
        print(f"Extracting code for Bug {bug_id}")
        print(f"{'='*60}")
        print(f"Summary: {analysis['summary']}")
        
        # Create bug directory
        bug_dir = os.path.join(self.output_dir, f"bug_{bug_id}")
        os.makedirs(bug_dir, exist_ok=True)
        
        # Extract ALL fixing commits
        fixing_count = self.extract_fixing_commits(bug_id, bug_dir, analysis)
        
        if fixing_count == 0:
            print(f"    No fixing commits could be extracted for Bug {bug_id}")
            # Remove empty directory
            try:
                os.rmdir(bug_dir)
            except:
                pass
            return {
                'bug_id': bug_id,
                'success': False,
                'fixing_commits': 0,
                'regression_commits': 0
            }
        
        result = {
            'bug_id': bug_id,
            'success': True,
            'fixing_commits': fixing_count,
            'regression_commits': 0
        }
        
        # Extract matching regression commits if this bug has regressions
        regression_chain = analysis.get('regression_chain', {})
        if regression_chain.get('has_regression'):
            print(f"\n  Processing {len(regression_chain['regression_details'])} regressor(s)...")
            
            # Combine all regression commits from all regressors into one file
            all_regression_diff = ""
            total_regression_count = 0
            
            for reg_detail in regression_chain['regression_details']:
                regressor_bug_id = reg_detail['bug_id']
                matching_commits = reg_detail.get('matching_commits', [])
                
                if not matching_commits:
                    print(f"    No matching commits for regressor Bug {regressor_bug_id}")
                    continue
                
                print(f"    Found {len(matching_commits)} matching commit(s) for regressor Bug {regressor_bug_id}")
                
                for i, commit in enumerate(matching_commits, 1):
                    commit_hash = commit['commit_hash']
                    short_hash = commit['short_hash']
                    overlap_count = commit['file_overlap_count']
                    
                    print(f"      [{i}/{len(matching_commits)}] Fetching {short_hash} (overlap: {overlap_count} files)...")
                    
                    # Try autoland first, then central
                    diff_content = self.get_commit_diff('mozilla-autoland', commit_hash)
                    if not diff_content:
                        diff_content = self.get_commit_diff('mozilla-central', commit_hash)
                    
                    if diff_content:
                        # Add separator and commit info
                        if total_regression_count > 0:
                            all_regression_diff += "\n\n" + "="*80 + "\n"
                        
                        all_regression_diff += f"# REGRESSION COMMIT (from Bug {regressor_bug_id})\n"
                        all_regression_diff += f"# Commit: {short_hash}\n"
                        all_regression_diff += f"# Author: {commit.get('author', 'Unknown')}\n"
                        all_regression_diff += f"# File Overlap: {overlap_count} files\n"
                        overlapping = commit.get('overlapping_files', [])
                        if overlapping:
                            all_regression_diff += f"# Overlapping Files: {', '.join(overlapping[:3])}"
                            if len(overlapping) > 3:
                                all_regression_diff += f" ... and {len(overlapping) - 3} more"
                            all_regression_diff += "\n"
                        all_regression_diff += "="*80 + "\n\n"
                        all_regression_diff += diff_content
                        
                        total_regression_count += 1
                        print(f"        ✓ Added to regression.diff ({len(diff_content)} bytes)")
                    else:
                        print(f"        ✗ Failed to fetch")
            
            # Save combined regression file
            if total_regression_count > 0:
                if self.save_diff_file(bug_dir, "regression.diff", all_regression_diff):
                    print(f"    ✓ Saved all {total_regression_count} regression commits to regression.diff")
                    result['regression_commits'] = total_regression_count
                else:
                    print(f"     Failed to save regression.diff")
            else:
                print(f"      No matching regression commits could be extracted")
        
        return result
    
    def extract_from_analysis(self, regression_analysis_file: str) -> Dict:
        """
        Extract code from regression analysis results
        
        Args:
            regression_analysis_file: Path to Step 3 JSON output
            
        Returns:
            Complete extraction summary
        """
        print("\n" + "="*80)
        print("STEP 4: CODE EXTRACTION")
        print("="*80)
        
        # Load regression analysis
        print(f"\nLoading regression analysis from: {regression_analysis_file}")
        with open(regression_analysis_file, 'r') as f:
            regression_data = json.load(f)
        
        signature = regression_data.get('signature', 'Unknown')
        regression_analyses = regression_data.get('regression_analyses', {})
        
        print(f"Signature: {signature}")
        print(f"Total bugs to process: {len(regression_analyses)}")
        print(f"Output directory: {self.output_dir}/")
        
        # Extract code for each bug
        results = []
        successful_bugs = 0
        total_fixing = 0
        total_regression = 0
        
        for bug_id, analysis in regression_analyses.items():
            result = self.extract_bug_code(bug_id, analysis)
            results.append(result)
            
            if result['success']:
                successful_bugs += 1
                total_fixing += result['fixing_commits']
                total_regression += result['regression_commits']
        
        print(f"\n{'='*80}")
        print("EXTRACTION COMPLETE")
        print(f"{'='*80}")
        print(f"Total bugs processed: {len(regression_analyses)}")
        print(f"Successful extractions: {successful_bugs}")
        print(f"Failed extractions: {len(regression_analyses) - successful_bugs}")
        print(f"Total fixing commits: {total_fixing}")
        print(f"Total regression commits: {total_regression}")
        print(f"\nAll diffs saved to: {self.output_dir}/")
        
        return {
            'signature': signature,
            'extraction_timestamp': datetime.now().isoformat(),
            'output_directory': self.output_dir,
            'summary': {
                'total_bugs': len(regression_analyses),
                'successful_bugs': successful_bugs,
                'total_fixing_commits': total_fixing,
                'total_regression_commits': total_regression
            },
            'bug_results': results
        }
    
    def save_summary(self, results: Dict) -> str:
        """
        Save extraction summary to a text file
        
        Args:
            results: Results dictionary
            
        Returns:
            Summary filename
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = os.path.join(self.output_dir, f"extraction_summary_{timestamp}.txt")
        
        try:
            with open(filename, 'w') as f:
                f.write("="*80 + "\n")
                f.write("CODE EXTRACTION SUMMARY\n")
                f.write("="*80 + "\n\n")
                f.write(f"Signature: {results['signature']}\n")
                f.write(f"Timestamp: {results['extraction_timestamp']}\n")
                f.write(f"Output Directory: {results['output_directory']}/\n\n")
                
                f.write(f"Total Bugs: {results['summary']['total_bugs']}\n")
                f.write(f"Successful: {results['summary']['successful_bugs']}\n")
                f.write(f"Total Fixing Commits: {results['summary']['total_fixing_commits']}\n")
                f.write(f"Total Regression Commits: {results['summary']['total_regression_commits']}\n\n")
                
                f.write("="*80 + "\n")
                f.write("PER-BUG RESULTS\n")
                f.write("="*80 + "\n\n")
                
                for bug_result in results['bug_results']:
                    f.write(f"Bug {bug_result['bug_id']}:\n")
                    if bug_result['success']:
                        f.write(f"  ✓ Success\n")
                        f.write(f"  Fixing commits: {bug_result['fixing_commits']}\n")
                        f.write(f"  Regression commits: {bug_result['regression_commits']}\n")
                    else:
                        f.write(f"  ✗ Failed\n")
                    f.write("\n")
            
            print(f"\nSummary saved to: {filename}")
            return filename
        except Exception as e:
            print(f"\nFailed to save summary: {e}")
            return None


def main():
    """Main execution function"""
    
    # Initialize extractor with output directory
    extractor = CodeExtractor(output_dir="extracted_diffs")
    
    # Path to Step 3 output
    regression_analysis_file = "step3_regression_analysis_OOM | small.json"
    
    # Extract code and save as .diff files
    results = extractor.extract_from_analysis(regression_analysis_file)
    
    # Save summary
    extractor.save_summary(results)
    
    # Print detailed summary
    print("\n" + "="*80)
    print("FILES CREATED")
    print("="*80)
    
    for bug_result in results['bug_results']:
        if bug_result['success']:
            bug_id = bug_result['bug_id']
            print(f"\nBug {bug_id}:")
            print(f"  Directory: extracted_diffs/bug_{bug_id}/")
            print(f"  Fixing commits: {bug_result['fixing_commits']} file(s)")
            print(f"  Regression commits: {bug_result['regression_commits']} file(s)")
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)
    print(f"\nAll diff files saved in: {extractor.output_dir}/")


if __name__ == "__main__":
    main()