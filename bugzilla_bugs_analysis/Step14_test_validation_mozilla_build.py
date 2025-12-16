#!/usr/bin/env python3
"""
Step 14: Test Validation Using Mozilla Build System (./mach)
Uses Step 12 (test files) and Step 11 (full source content).
Runs tests via Mozilla's ./mach system.

INPUT:  method2_outputs/step12_test_extraction/
        method2_outputs/step11_matched_method_diffs/
OUTPUT: method2_outputs/step14_mozilla_build_validation/
"""

import json
import os
import subprocess
from datetime import datetime
from typing import Dict, Optional, Tuple
from pathlib import Path
import logging
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
method2_outputs = script_dir / "method2_outputs"

# Input/Output paths
STEP12_DIR = method2_outputs / "step12_test_extraction"
STEP11_DIR = method2_outputs / "step11_matched_method_diffs"
OUTPUT_DIR = method2_outputs / "step14_mozilla_build_validation"

# Mozilla repository root
MOZILLA_REPO = parent_dir / "mozilla-central"


class TestValidatorMozillaBuild:
    """Validate test behavior using Mozilla build system"""
    
    def __init__(self, step12_dir: str = None, step11_dir: str = None,
                 output_dir: str = None, mozilla_repo: str = None,
                 verbose: bool = False, timeout: int = 120):
        self.step12_dir = Path(step12_dir) if step12_dir else STEP12_DIR
        self.step11_dir = Path(step11_dir) if step11_dir else STEP11_DIR
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.mozilla_repo = Path(mozilla_repo) if mozilla_repo else MOZILLA_REPO
        self.verbose = verbose
        self.timeout = timeout
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=level, format='%(message)s')
        self.logger = logging.getLogger(__name__)
        
        # Verify Mozilla build
        if not self._verify_mozilla_build():
            self.logger.warning("⚠ Mozilla build not found or incomplete")
        
        # Load Step 12 summary
        self.step12_summary = self._load_step12_summary()
    
    def _verify_mozilla_build(self) -> bool:
        """Verify Mozilla build system is available"""
        self.logger.info(f"Checking Mozilla build at: {self.mozilla_repo}")
        
        mach_path = self.mozilla_repo / 'mach'
        if not mach_path.exists():
            self.logger.error(f"✗ mach script not found")
            return False
        
        if not any(self.mozilla_repo.glob('obj-*')):
            self.logger.warning("⚠ No build directories found. Run './mach build' first")
            return False
        
        self.logger.info("✓ Mozilla build system verified")
        return True
    
    def _load_step12_summary(self) -> Optional[Dict]:
        """Load Step 12 summary results"""
        summary_file = self.step12_dir / 'step12_test_extraction_summary.json'
        
        if not summary_file.exists():
            self.logger.error(f"Step 12 summary not found: {summary_file}")
            return None
        
        self.logger.info(f"Loading Step 12 results from: {summary_file}")
        with open(summary_file, 'r') as f:
            return json.load(f)
    
    def _detect_test_type(self, test_path: str) -> str:
        """Detect test type from path"""
        test_lower = test_path.lower()
        
        if 'mochitest' in test_lower:
            return 'mochitest'
        elif 'xpcshell' in test_lower:
            return 'xpcshell'
        elif test_lower.endswith('.cpp') or test_lower.endswith('.h'):
            return 'gtest'
        elif test_lower.endswith('.js'):
            return 'javascript'
        elif test_lower.endswith('.py'):
            return 'python'
        return 'unknown'
    
    def _run_gtest(self, test_pattern: str) -> Tuple[bool, str]:
        """Run C++ gtest via ./mach gtest"""
        try:
            result = subprocess.run(
                ['./mach', 'gtest', test_pattern],
                cwd=self.mozilla_repo,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, f"gtest timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"gtest error: {str(e)}"
    
    def _run_mochitest(self, test_file: str) -> Tuple[bool, str]:
        """Run mochitest via ./mach mochitest"""
        try:
            result = subprocess.run(
                ['./mach', 'mochitest', test_file],
                cwd=self.mozilla_repo,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, f"mochitest timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"mochitest error: {str(e)}"
    
    def _run_xpcshell_test(self, test_file: str) -> Tuple[bool, str]:
        """Run xpcshell test via ./mach xpcshell-test"""
        try:
            result = subprocess.run(
                ['./mach', 'xpcshell-test', test_file],
                cwd=self.mozilla_repo,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, f"xpcshell timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"xpcshell error: {str(e)}"
    
    def _run_test(self, test_file: str, test_type: str) -> Tuple[bool, str]:
        """Run test based on type using Mozilla build system"""
        if test_type == 'gtest':
            test_pattern = os.path.basename(test_file).replace('.cpp', '').replace('.h', '')
            return self._run_gtest(test_pattern)
        elif test_type == 'mochitest':
            return self._run_mochitest(test_file)
        elif test_type == 'xpcshell':
            return self._run_xpcshell_test(test_file)
        else:
            return False, f"Unsupported test type: {test_type}"
    
    def validate_test(self, bug_id: str, filepath: str, match_idx: int,
                     test_path: str, commit_hash: str, commit_type: str) -> Dict:
        """Validate a single test"""
        result = {
            'test_file': test_path,
            'test_type': self._detect_test_type(test_path),
            'commit_hash': commit_hash[:8],
            'commit_type': commit_type,
            'passed': False,
            'output_preview': ''
        }
        
        passed, output = self._run_test(test_path, result['test_type'])
        result['passed'] = passed
        result['output_preview'] = output[:500]
        
        return result
    
    def validate_all(self) -> Dict:
        """Main validation process using Mozilla build system"""
        self.logger.info("\n" + "="*80)
        self.logger.info("STEP 14: TEST VALIDATION (MOZILLA BUILD SYSTEM)")
        self.logger.info("="*80)
        
        if not self.step12_summary:
            return {}
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'mozilla_repo': str(self.mozilla_repo),
            'summary': {
                'bugs': 0,
                'tests_run': 0,
                'confirms_regression': 0,
                'both_pass': 0,
                'both_fail': 0,
                'test_error': 0
            },
            'bugs': {}
        }
        
        self.logger.info("\nValidating tests via Mozilla build system...\n")
        
        for bug_id, bug_info in self.step12_summary.get('bugs', {}).items():
            self.logger.info(f"Bug {bug_id}:")
            bug_results = {'files': []}
            
            for file_data in bug_info.get('files', []):
                filepath = file_data['filepath']
                self.logger.info(f"  {os.path.basename(filepath)}")
                
                file_results = {'source_file': filepath, 'validations': []}
                
                for match in file_data.get('matches', []):
                    match_idx = match['match_idx']
                    
                    fixing_tests = match['fixing_commit'].get('tests', [])
                    regressor_tests = match['regressor_commit'].get('tests', [])
                    
                    if not fixing_tests and not regressor_tests:
                        continue
                    
                    # Run test for fixing commit
                    fixing_result = None
                    if fixing_tests:
                        test_path = fixing_tests[0]['path']
                        fixing_result = self.validate_test(
                            bug_id, filepath, match_idx, test_path,
                            match['fixing_commit']['hash'], 'fixing'
                        )
                    
                    # Run test for regressor commit
                    regressor_result = None
                    if regressor_tests:
                        test_path = regressor_tests[0]['path']
                        regressor_result = self.validate_test(
                            bug_id, filepath, match_idx, test_path,
                            match['regressor_commit']['hash'], 'regressor'
                        )
                    
                    validation = {
                        'match_idx': match_idx,
                        'fixing_result': fixing_result,
                        'regressor_result': regressor_result,
                        'status': 'unknown',
                        'confirms_regression': False
                    }
                    
                    # Determine status
                    fixing_passed = fixing_result['passed'] if fixing_result else None
                    regressor_passed = regressor_result['passed'] if regressor_result else None
                    
                    if regressor_passed is not None and fixing_passed is not None:
                        if not regressor_passed and fixing_passed:
                            validation['status'] = 'confirms_regression'
                            validation['confirms_regression'] = True
                            results['summary']['confirms_regression'] += 1
                            status_icon = "✓"
                        elif regressor_passed and fixing_passed:
                            validation['status'] = 'both_pass'
                            results['summary']['both_pass'] += 1
                            status_icon = "~"
                        elif not regressor_passed and not fixing_passed:
                            validation['status'] = 'both_fail'
                            results['summary']['both_fail'] += 1
                            status_icon = "✗"
                        else:
                            validation['status'] = 'unexpected'
                            results['summary']['test_error'] += 1
                            status_icon = "!"
                    else:
                        validation['status'] = 'incomplete'
                        results['summary']['test_error'] += 1
                        status_icon = "?"
                    
                    self.logger.info(f"    {status_icon} Match {match_idx}: {validation['status']}")
                    
                    file_results['validations'].append(validation)
                    results['summary']['tests_run'] += 1
                
                if file_results['validations']:
                    bug_results['files'].append(file_results)
            
            if bug_results['files']:
                results['bugs'][bug_id] = bug_results
                results['summary']['bugs'] += 1
        
        # Calculate confirmation rate
        tests_with_results = (results['summary']['confirms_regression'] + 
                             results['summary']['both_pass'] + 
                             results['summary']['both_fail'])
        
        if tests_with_results > 0:
            results['summary']['confirmation_rate'] = round(
                results['summary']['confirms_regression'] / tests_with_results * 100, 1)
        else:
            results['summary']['confirmation_rate'] = 0.0
        
        # Save results
        output_file = self.output_dir / 'step14_mozilla_validation_results.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        
        self.logger.info(f"\n{'='*80}")
        self.logger.info("VALIDATION COMPLETE")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"Tests run: {results['summary']['tests_run']}")
        self.logger.info(f"Confirms regression: {results['summary']['confirms_regression']}")
        self.logger.info(f"Both pass: {results['summary']['both_pass']}")
        self.logger.info(f"Both fail: {results['summary']['both_fail']}")
        self.logger.info(f"Confirmation rate: {results['summary']['confirmation_rate']}%")
        self.logger.info(f"\nResults saved to: {output_file}")
        
        return results


def main():
    """Main execution"""
    if not STEP12_DIR.exists():
        print(f"ERROR: Step 12 directory not found: {STEP12_DIR}")
        print("Please run Step 12 first.")
        sys.exit(1)
    
    if not MOZILLA_REPO.exists():
        print(f"ERROR: Mozilla repository not found: {MOZILLA_REPO}")
        sys.exit(1)
    
    validator = TestValidatorMozillaBuild(verbose=False, timeout=120)
    validator.validate_all()
    
    print("\n" + "="*80)
    print("✓ STEP 14 COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()