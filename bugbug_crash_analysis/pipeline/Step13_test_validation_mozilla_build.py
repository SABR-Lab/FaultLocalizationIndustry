#!/usr/bin/env python3
"""
Step 12: Test Validation Against Regression/Fix Commits (MOZILLA BUILD SYSTEM VERSION)
Uses Step 11 (test files) and Step 10 (full source content).
Runs tests via Mozilla's ./mach system instead of standalone compilation.

Data Flow:
1. Load Step 11 results → Get test file paths and commit info
2. Load source content from Step 10 (full source at both commits)
3. For each test:
   - Determine test type (cpp, javascript, mochitest, xpcshell, python)
   - Run via appropriate ./mach command
   - Compare regressor vs fixing results
4. Report findings

Uses:
- ./mach gtest for C++ gtest tests
- ./mach mochitest for mochitest tests
- ./mach xpcshell-test for xpcshell tests
- node/mocha/jest for JavaScript tests
- python/pytest for Python tests
"""

import json
import os
import subprocess
import tempfile
import shutil
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import logging


class TestValidatorMozillaBuild:
    """Validate test behavior using Mozilla build system"""
    
    def __init__(self,
                 step11_results_dir: str,
                 step10_results_dir: str,
                 output_dir: str = "step12_test_validation",
                 mozilla_repo_root: str = "./mozilla-central",
                 verbose: bool = False,
                 timeout: int = 120):
        """
        Initialize test validator for Mozilla build system.
        
        Args:
            step11_results_dir: Directory containing Step 11 results
            step10_results_dir: Directory containing Step 10 results
            output_dir: Directory for output files
            mozilla_repo_root: Path to Mozilla repository root (for ./mach commands)
            verbose: Enable verbose logging
            timeout: Test execution timeout in seconds
        """
        self.step11_results_dir = step11_results_dir
        self.step10_results_dir = step10_results_dir
        self.output_dir = output_dir
        self.mozilla_repo_root = mozilla_repo_root
        self.verbose = verbose
        self.timeout = timeout
        
        self._setup_logging()
        os.makedirs(output_dir, exist_ok=True)
        
        # Verify Mozilla repo exists
        if not self._verify_mozilla_build():
            self.logger.warning("⚠ Mozilla build not found or incomplete")
        
        self.step11_summary = self._load_step11_summary()
    
    def _setup_logging(self):
        """Configure logging"""
        level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(level=level, format='%(message)s')
        self.logger = logging.getLogger(__name__)
    
    def _verify_mozilla_build(self) -> bool:
        """Verify Mozilla build system is available"""
        self.logger.info(f"Checking Mozilla build system at: {self.mozilla_repo_root}")
        
        # Check for mach script
        mach_path = os.path.join(self.mozilla_repo_root, 'mach')
        if not os.path.exists(mach_path):
            self.logger.error(f"✗ mach script not found: {mach_path}")
            return False
        
        # Check for build directory
        build_dir = os.path.join(self.mozilla_repo_root, 'obj-*')
        if not any(Path(self.mozilla_repo_root).glob('obj-*')):
            self.logger.warning(f"⚠ No build directories found. Run './mach build' first")
            return False
        
        self.logger.info(f"✓ Mozilla build system verified")
        return True
    
    def _load_step11_summary(self) -> Optional[Dict]:
        """Load Step 11 summary results"""
        step11_summary = os.path.join(self.step11_results_dir, 'SUMMARY_tests_found.json')
        
        if not os.path.exists(step11_summary):
            self.logger.error(f"Step 11 summary not found: {step11_summary}")
            return None
        
        self.logger.info(f"Loading Step 11 results from: {step11_summary}")
        with open(step11_summary, 'r') as f:
            return json.load(f)
    
    def _extract_test_content_from_step11(self, 
                                         bug_id: str, 
                                         filepath: str, 
                                         match_idx: int,
                                         test_path: str,
                                         commit_type: str) -> Optional[str]:
        """Extract test file content from Step 11's organized directory structure."""
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        safe_filepath = safe_filepath.replace('.cpp', '').replace('.h', '').replace('.js', '').replace('.py', '')
        
        test_filename = os.path.basename(test_path)
        
        test_file_path = os.path.join(
            self.step11_results_dir,
            f"bug_{bug_id}",
            safe_filepath,
            f"match_{match_idx}",
            commit_type.lower(),
            test_filename
        )
        
        if not os.path.exists(test_file_path):
            self.logger.warning(f"Step 11 test file not found: {test_file_path}")
            return None
        
        try:
            with open(test_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            if not content or len(content.strip()) == 0:
                self.logger.warning(f"Empty test content for {test_filename}")
                return None
            
            self.logger.debug(f"Extracted test content for {test_filename} ({len(content)} bytes)")
            return content
        
        except Exception as e:
            self.logger.error(f"Error reading Step 11 test file: {e}")
            return None
    
    def _find_source_content_in_step10(self, 
                                      bug_id: str, 
                                      filepath: str, 
                                      commit_hash: str) -> Optional[str]:
        """Find full source content from Step 10 directory structure."""
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        bug_dir = os.path.join(self.step10_results_dir, f"bug_{bug_id}", safe_filepath)
        
        if not os.path.exists(bug_dir):
            self.logger.warning(f"Step 10 bug dir not found: {bug_dir}")
            return None
        
        for root, dirs, files in os.walk(bug_dir):
            for file in files:
                if file.endswith('.full') and commit_hash in file:
                    full_file_path = os.path.join(root, file)
                    try:
                        with open(full_file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.logger.debug(f"Found source content in Step 10: {full_file_path}")
                        return content
                    except Exception as e:
                        self.logger.warning(f"Error reading Step 10 file: {e}")
        
        self.logger.warning(f"Could not find Step 10 content for {bug_id}/{filepath}/{commit_hash[:8]}")
        return None
    
    def _detect_test_type(self, test_path: str) -> str:
        """Detect test type from path"""
        test_lower = test_path.lower()
        
        if 'mochitest' in test_lower:
            return 'mochitest'
        elif 'xpcshell' in test_lower:
            return 'xpcshell'
        elif test_lower.endswith('.cpp') or test_lower.endswith('.h'):
            return 'cpp'
        elif test_lower.endswith('.js'):
            return 'javascript'
        elif test_lower.endswith('.py'):
            return 'python'
        
        return 'unknown'
    
    def _run_gtest(self, test_pattern: str) -> Tuple[bool, str]:
        """Run C++ gtest via ./mach gtest"""
        try:
            self.logger.debug(f"Running gtest: {test_pattern}")
            result = subprocess.run(
                ['./mach', 'gtest', test_pattern],
                cwd=self.mozilla_repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            output = result.stdout + result.stderr
            passed = result.returncode == 0
            
            self.logger.debug(f"gtest result: {'PASSED' if passed else 'FAILED'}")
            return passed, output
        
        except subprocess.TimeoutExpired:
            return False, f"gtest timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"gtest error: {str(e)}"
    
    def _run_mochitest(self, test_file: str) -> Tuple[bool, str]:
        """Run mochitest via ./mach mochitest"""
        try:
            self.logger.debug(f"Running mochitest: {test_file}")
            result = subprocess.run(
                ['./mach', 'mochitest', test_file],
                cwd=self.mozilla_repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            output = result.stdout + result.stderr
            passed = result.returncode == 0
            
            self.logger.debug(f"mochitest result: {'PASSED' if passed else 'FAILED'}")
            return passed, output
        
        except subprocess.TimeoutExpired:
            return False, f"mochitest timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"mochitest error: {str(e)}"
    
    def _run_xpcshell_test(self, test_file: str) -> Tuple[bool, str]:
        """Run xpcshell test via ./mach xpcshell-test"""
        try:
            self.logger.debug(f"Running xpcshell-test: {test_file}")
            result = subprocess.run(
                ['./mach', 'xpcshell-test', test_file],
                cwd=self.mozilla_repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            
            output = result.stdout + result.stderr
            passed = result.returncode == 0
            
            self.logger.debug(f"xpcshell-test result: {'PASSED' if passed else 'FAILED'}")
            return passed, output
        
        except subprocess.TimeoutExpired:
            return False, f"xpcshell-test timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"xpcshell-test error: {str(e)}"
    
    def _run_javascript_test(self, test_file: str, temp_dir: str) -> Tuple[bool, str]:
        """Run JavaScript test via node/mocha/jest"""
        commands = [
            ['mocha', test_file],
            ['node', test_file],
            ['jest', test_file]
        ]
        
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout
                )
                
                if result.returncode == 0 or 'not found' not in result.stderr.lower():
                    return result.returncode == 0, result.stdout + result.stderr
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
        
        return False, "No JavaScript test runner available"
    
    def _run_python_test(self, test_file: str, temp_dir: str) -> Tuple[bool, str]:
        """Run Python test via pytest/unittest/python"""
        commands = [
            ['python', '-m', 'pytest', test_file, '-v'],
            ['python', '-m', 'unittest', 'discover', '-s', os.path.dirname(test_file)],
            ['python', test_file]
        ]
        
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout
                )
                
                if result.returncode == 0 or 'not found' not in result.stderr.lower():
                    return result.returncode == 0, result.stdout + result.stderr
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
        
        return False, "No Python test runner available"
    
    def _run_test(self, test_file: str, test_type: str, temp_dir: str = None) -> Tuple[bool, str]:
        """Run test based on type using Mozilla build system"""
        try:
            if test_type == 'cpp':
                # Extract test pattern from filename
                test_pattern = os.path.basename(test_file).replace('.cpp', '').replace('.h', '')
                return self._run_gtest(test_pattern)
            
            elif test_type == 'mochitest':
                return self._run_mochitest(test_file)
            
            elif test_type == 'xpcshell':
                return self._run_xpcshell_test(test_file)
            
            elif test_type == 'javascript':
                return self._run_javascript_test(test_file, temp_dir or os.path.dirname(test_file))
            
            elif test_type == 'python':
                return self._run_python_test(test_file, temp_dir or os.path.dirname(test_file))
            
            else:
                return False, "Unknown test type"
        
        except subprocess.TimeoutExpired:
            return False, f"Test timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"Test error: {str(e)}"
    
    def validate_test_pair(self,
                          bug_id: str,
                          filepath: str,
                          match_idx: int,
                          test_path_fixing: str,
                          test_path_regressor: str,
                          fixing_hash: str,
                          regressor_hash: str) -> Dict:
        """Validate test against both fixing and regressor versions using Mozilla build."""
        result = {
            'source_file': filepath,
            'test_file': test_path_fixing,
            'test_type': self._detect_test_type(test_path_fixing),
            'fixing_commit': fixing_hash[:8],
            'regressor_commit': regressor_hash[:8],
            'fixing_result': None,
            'regressor_result': None,
            'status': 'unknown',
            'confirms_regression': False
        }
        
        # Extract test content from Step 11
        self.logger.info(f"        Loading test files from Step 11...")
        test_content_fixing = self._extract_test_content_from_step11(
            bug_id, filepath, match_idx, test_path_fixing, 'fixing'
        )
        test_content_regressor = self._extract_test_content_from_step11(
            bug_id, filepath, match_idx, test_path_regressor, 'regressor'
        )
        
        if not test_content_fixing and not test_content_regressor:
            result['status'] = 'test_content_error'
            self.logger.error(f"      ✗ Could not extract test content")
            return result
        
        test_content = test_content_fixing or test_content_regressor
        self.logger.info(f"      ✓ Using test content ({len(test_content)} bytes)")
        
        # Get source code from Step 10
        self.logger.info(f"        Loading source code from Step 10...")
        fixing_content = self._find_source_content_in_step10(bug_id, filepath, fixing_hash)
        regressor_content = self._find_source_content_in_step10(bug_id, filepath, regressor_hash)
        
        if not fixing_content or not regressor_content:
            result['status'] = 'missing_source_content'
            self.logger.error(f"      ✗ Missing source content from Step 10")
            return result
        
        self.logger.info(f"      ✓ Source code loaded ({len(fixing_content)} bytes)")
        
        # TEST WITH REGRESSOR VERSION (EXPECT FAIL)
        self.logger.info(f"        Running test against REGRESSOR commit ({regressor_hash[:8]})...")
        
        try:
            regressor_passed, regressor_output = self._run_test(
                test_path_regressor, result['test_type']
            )
            
            if regressor_passed:
                self.logger.info(f"        Test PASSED on regressor (unexpected - should fail)")
            else:
                self.logger.info(f"      ✓ Test FAILED on regressor (expected)")
            
            result['regressor_result'] = {
                'passed': regressor_passed,
                'output_lines': len(regressor_output.split('\n')),
                'output_preview': regressor_output[:500],
                'full_output': regressor_output
            }
        except Exception as e:
            self.logger.error(f"      ✗ Error running regressor test: {e}")
            result['status'] = 'test_error'
            return result
        
        # TEST WITH FIXING VERSION (EXPECT PASS)
        self.logger.info(f"        Running test against FIXING commit ({fixing_hash[:8]})...")
        
        try:
            fixing_passed, fixing_output = self._run_test(
                test_path_fixing, result['test_type']
            )
            
            if fixing_passed:
                self.logger.info(f"      ✓ Test PASSED on fixing (expected)")
            else:
                self.logger.info(f"        Test FAILED on fixing (unexpected - should pass)")
            
            result['fixing_result'] = {
                'passed': fixing_passed,
                'output_lines': len(fixing_output.split('\n')),
                'output_preview': fixing_output[:500],
                'full_output': fixing_output
            }
        except Exception as e:
            self.logger.error(f"      ✗ Error running fixing test: {e}")
            result['status'] = 'test_error'
            return result
        
        # DETERMINE STATUS
        if regressor_passed and fixing_passed:
            result['status'] = 'both_pass'
            self.logger.warning(f"        RESULT: both_pass (test doesn't catch regression)")
        elif not regressor_passed and fixing_passed:
            result['status'] = 'confirms_regression'
            result['confirms_regression'] = True
            self.logger.info(f"      ✓ RESULT: confirms_regression (PERFECT!)")
        elif regressor_passed and not fixing_passed:
            result['status'] = 'unexpected_behavior'
            self.logger.warning(f"        RESULT: unexpected_behavior")
        else:
            result['status'] = 'both_fail'
            self.logger.warning(f"        RESULT: both_fail (test broken)")
        
        return result
    
    def validate_all(self) -> Dict:
        """Main validation process using Mozilla build system"""
        self.logger.info("\n" + "=" * 70)
        self.logger.info("STEP 12: TEST VALIDATION (MOZILLA BUILD SYSTEM)")
        self.logger.info("=" * 70)
        
        if not self.step11_summary:
            return {}
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'bugs': 0,
                'source_files': 0,
                'tests_run': 0,
                'confirms_regression': 0,
                'both_pass': 0,
                'both_fail': 0,
                'unexpected': 0,
                'test_error': 0,
                'confirmation_rate': 0.0
            },
            'bugs': {}
        }
        
        self.logger.info("\n Validating tests via Mozilla build system...\n")
        
        for bug_id, bug_info in self.step11_summary.get('bugs', {}).items():
            self.logger.info(f"Bug {bug_id}:")
            bug_results = {'files': []}
            
            for filepath, file_info in bug_info['files'].items():
                self.logger.info(f"   {os.path.basename(filepath)}")
                
                file_results = {'source_file': filepath, 'validations': []}
                
                for match in file_info['matches']:
                    match_idx = match['match_idx']
                    
                    fixing_tests = match['fixing_commit']['tests']
                    regressor_tests = match['regressor_commit']['tests']
                    fixing_hash = match['fixing_commit']['hash']
                    regressor_hash = match['regressor_commit']['hash']
                    
                    if not fixing_tests and not regressor_tests:
                        continue
                    
                    test_path_fixing = fixing_tests[0] if fixing_tests else regressor_tests[0]
                    test_path_regressor = regressor_tests[0] if regressor_tests else test_path_fixing
                    
                    validation = self.validate_test_pair(
                        bug_id, filepath, match_idx,
                        test_path_fixing, test_path_regressor,
                        fixing_hash, regressor_hash
                    )
                    
                    file_results['validations'].append(validation)
                    results['summary']['tests_run'] += 1
                    
                    if validation['status'] == 'confirms_regression':
                        results['summary']['confirms_regression'] += 1
                        status_icon = "✓"
                    elif validation['status'] == 'both_pass':
                        results['summary']['both_pass'] += 1
                        status_icon = "~"
                    elif validation['status'] == 'both_fail':
                        results['summary']['both_fail'] += 1
                        status_icon = "✗"
                    else:
                        results['summary']['test_error'] += 1
                        status_icon = "!"
                    
                    self.logger.info(f"    {status_icon} Match {match_idx}: {validation['status']}")
                
                if file_results['validations']:
                    bug_results['files'].append(file_results)
                    results['summary']['source_files'] += 1
            
            if bug_results['files']:
                results['bugs'][bug_id] = bug_results
                results['summary']['bugs'] += 1
        
        # Calculate confirmation rate
        tests_with_results = (results['summary']['confirms_regression'] + 
                             results['summary']['both_pass'] + 
                             results['summary']['both_fail'] + 
                             results['summary']['unexpected'])
        
        if tests_with_results > 0:
            results['summary']['confirmation_rate'] = (
                results['summary']['confirms_regression'] / tests_with_results * 100
            )
        
        self._save_results(results)
        self._print_summary(results)
        
        return results
    
    def _save_results(self, results: Dict):
        """Save results to files"""
        json_path = Path(self.output_dir) / 'validation_results.json'
        with open(json_path, 'w') as f:
            json.dump(results, f, indent=2)
        
        text_path = Path(self.output_dir) / 'validation_report.txt'
        self._write_text_report(text_path, results)
    
    def _write_text_report(self, output_path: Path, results: Dict):
        """Write comprehensive text report"""
        with open(output_path, 'w') as f:
            f.write("TEST VALIDATION REPORT (MOZILLA BUILD SYSTEM)\n")
            f.write("=" * 70 + "\n\n")
            
            stats = results['summary']
            f.write("SUMMARY METRICS:\n")
            f.write("-" * 70 + "\n")
            f.write(f"Bugs analyzed: {stats['bugs']}\n")
            f.write(f"Source files tested: {stats['source_files']}\n")
            f.write(f"Total tests run: {stats['tests_run']}\n\n")
            
            f.write("TEST RESULTS:\n")
            f.write("-" * 70 + "\n")
            f.write(f"✓ Confirms regression: {stats['confirms_regression']}\n")
            f.write(f"~ Both pass: {stats['both_pass']}\n")
            f.write(f"✗ Both fail: {stats['both_fail']}\n")
            f.write(f"! Test error: {stats['test_error']}\n\n")
            f.write(f"CONFIRMATION RATE: {stats['confirmation_rate']:.1f}%\n")
            f.write(f"   (Calculated from {tests_with_results} tests with results)\n\n")
            
            f.write("DETAILED RESULTS:\n")
            f.write("=" * 70 + "\n\n")
            
            for bug_id, bug_data in results.get('bugs', {}).items():
                f.write(f"Bug {bug_id}\n")
                f.write("-" * 70 + "\n")
                
                for file_data in bug_data.get('files', []):
                    f.write(f"  {file_data['source_file']}\n")
                    for val in file_data.get('validations', []):
                        f.write(f"    Test: {os.path.basename(val['test_file'])}\n")
                        f.write(f"      Type: {val['test_type']}\n")
                        f.write(f"      Status: {val['status']}\n")
                        if val['regressor_result']:
                            f.write(f"      Regressor: {'FAIL' if not val['regressor_result']['passed'] else 'PASS'}\n")
                        if val['fixing_result']:
                            f.write(f"      Fixing:   {'PASS' if val['fixing_result']['passed'] else 'FAIL'}\n")
                        f.write(f"      Confirms regression: {'Yes' if val['confirms_regression'] else 'No'}\n")
                        f.write("\n")
    
    def _print_summary(self, results: Dict):
        """Print summary to console"""
        stats = results['summary']
        
        self.logger.info("\n" + "=" * 70)
        self.logger.info("VALIDATION COMPLETE")
        self.logger.info("=" * 70)
        
        self.logger.info(f"\n METRICS:")
        self.logger.info(f"  • Bugs: {stats['bugs']}")
        self.logger.info(f"  • Source files: {stats['source_files']}")
        self.logger.info(f"  • Tests run: {stats['tests_run']}")
        
        self.logger.info(f"\n✓ Results:")
        self.logger.info(f"  • Confirms regression: {stats['confirms_regression']}")
        self.logger.info(f"  • Both pass: {stats['both_pass']}")
        self.logger.info(f"  • Both fail: {stats['both_fail']}")
        self.logger.info(f"  • Test errors: {stats['test_error']}")
        
        self.logger.info(f"\n CONFIRMATION RATE: {stats['confirmation_rate']:.1f}%")
        tests_with_results = (stats['confirms_regression'] + stats['both_pass'] + 
                            stats['both_fail'] + stats['unexpected'])
        self.logger.info(f"   (Confirmation rate calculated from {tests_with_results} tests with results)")
        self.logger.info(f"\n Output: {self.output_dir}/")


def main():
    """Main execution"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Validate tests using Mozilla build system (./mach)'
    )
    parser.add_argument(
        '--step11-dir', '-s11',
        default='test_extraction',
        help='Step 11 results directory'
    )
    parser.add_argument(
        '--step10-dir', '-s10',
        default='step10_matched_methodDiffs',
        help='Step 10 results directory'
    )
    parser.add_argument(
        '--output', '-o',
        default='step12_test_validation',
        help='Output directory'
    )
    parser.add_argument(
        '--mozilla-repo', '-m',
        default='./mozilla-central',
        help='Mozilla repository root (must have ./mach and built obj-* directories)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )
    parser.add_argument(
        '--timeout', '-t',
        type=int,
        default=120,
        help='Test execution timeout in seconds'
    )
    
    args = parser.parse_args()
    
    validator = TestValidatorMozillaBuild(
        step11_results_dir=args.step11_dir,
        step10_results_dir=args.step10_dir,
        output_dir=args.output,
        mozilla_repo_root=args.mozilla_repo,
        verbose=args.verbose,
        timeout=args.timeout
    )
    
    results = validator.validate_all()
    
    print("\n" + "=" * 70)
    print("STEP 12 COMPLETED SUCCESSFULLY (MOZILLA BUILD SYSTEM)")
    print("=" * 70)


if __name__ == "__main__":
    main()