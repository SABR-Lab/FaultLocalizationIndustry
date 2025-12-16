#!/usr/bin/env python3
"""
Step 13: Test Validation Against Regression/Fix Commits
Uses Step 12 (test files) and Step 11 (full source content).

INPUT:  method2_outputs/step12_test_extraction/
        method2_outputs/step11_matched_method_diffs/
OUTPUT: method2_outputs/step13_test_validation/
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
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
method2_outputs = script_dir / "method2_outputs"

# Input/Output paths
STEP12_DIR = method2_outputs / "step12_test_extraction"
STEP11_DIR = method2_outputs / "step11_matched_method_diffs"
OUTPUT_DIR = method2_outputs / "step13_test_validation"

# Local repositories
LOCAL_REPOS = {
    'mozilla-central': parent_dir / 'mozilla-central',
    'mozilla-release': parent_dir / 'mozilla-release',
    'mozilla-autoland': parent_dir / 'mozilla-autoland',
    'mozilla-esr115': parent_dir / 'mozilla-esr115'
}


class TestValidator:
    """Validate test behavior against regression and fix commits"""
    
    def __init__(self, step12_dir: str = None, step11_dir: str = None, 
                 output_dir: str = None, verbose: bool = False, timeout: int = 60):
        self.step12_dir = Path(step12_dir) if step12_dir else STEP12_DIR
        self.step11_dir = Path(step11_dir) if step11_dir else STEP11_DIR
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.verbose = verbose
        self.timeout = timeout
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=level, format='%(message)s')
        self.logger = logging.getLogger(__name__)
        
        # Setup local repos
        self.local_repos = {}
        for name, path in LOCAL_REPOS.items():
            path = Path(path)
            if path.exists():
                self.local_repos[name] = str(path)
        
        # Load Step 12 summary
        self.step12_summary = self._load_step12_summary()
    
    def _load_step12_summary(self) -> Optional[Dict]:
        """Load Step 12 summary results"""
        summary_file = self.step12_dir / 'step12_test_extraction_summary.json'
        
        if not summary_file.exists():
            self.logger.error(f"Step 12 summary not found: {summary_file}")
            return None
        
        self.logger.info(f"Loading Step 12 results from: {summary_file}")
        with open(summary_file, 'r') as f:
            return json.load(f)
    
    def _extract_test_content_from_step12(self, bug_id: str, filepath: str, 
                                          match_idx: int, test_path: str, 
                                          commit_type: str) -> Optional[str]:
        """Extract test file content from Step 12's directory structure"""
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        safe_filepath = safe_filepath.replace('.cpp', '').replace('.h', '').replace('.js', '').replace('.py', '')
        
        test_filename = os.path.basename(test_path)
        
        test_file_path = self.step12_dir / f"bug_{bug_id}" / safe_filepath / f"match_{match_idx}" / commit_type.lower() / test_filename
        
        if not test_file_path.exists():
            return None
        
        try:
            with open(test_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            self.logger.error(f"Error reading test file: {e}")
            return None
    
    def _find_source_content_in_step11(self, bug_id: str, filepath: str, 
                                       commit_hash: str) -> Optional[str]:
        """Find full source content from Step 11 directory"""
        safe_filepath = filepath.replace('/', '_').replace('\\', '_')
        bug_dir = self.step11_dir / f"bug_{bug_id}" / safe_filepath
        
        if not bug_dir.exists():
            return None
        
        for root, dirs, files in os.walk(bug_dir):
            for file in files:
                if file.endswith('.full') and commit_hash in file:
                    try:
                        with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                            return f.read()
                    except:
                        pass
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
    
    def _run_test(self, test_file: str, test_type: str, 
                 source_file: str = None, temp_dir: str = None) -> Tuple[bool, str]:
        """Run test based on type"""
        try:
            if test_type == 'javascript':
                commands = [['node', test_file], ['mocha', test_file]]
                for cmd in commands:
                    try:
                        result = subprocess.run(cmd, cwd=temp_dir, capture_output=True, 
                                              text=True, timeout=self.timeout)
                        if 'not found' not in result.stderr.lower():
                            return result.returncode == 0, result.stdout + result.stderr
                    except:
                        continue
                return False, "No JavaScript runner available"
            
            elif test_type == 'python':
                commands = [['python', '-m', 'pytest', test_file, '-v'], ['python', test_file]]
                for cmd in commands:
                    try:
                        result = subprocess.run(cmd, cwd=temp_dir, capture_output=True, 
                                              text=True, timeout=self.timeout)
                        if 'not found' not in result.stderr.lower():
                            return result.returncode == 0, result.stdout + result.stderr
                    except:
                        continue
                return False, "No Python runner available"
            
            elif test_type == 'cpp':
                # Try to compile
                out_file = os.path.join(temp_dir, 'test_exe')
                compile_cmd = ['clang++', '-std=c++17', '-o', out_file, test_file]
                if source_file:
                    compile_cmd.append(source_file)
                
                result = subprocess.run(compile_cmd, cwd=temp_dir, capture_output=True, 
                                       text=True, timeout=self.timeout)
                
                if result.returncode != 0:
                    return False, f"Compilation failed: {result.stderr[:500]}"
                
                # Run
                result = subprocess.run([out_file], cwd=temp_dir, capture_output=True, 
                                       text=True, timeout=self.timeout)
                return result.returncode == 0, result.stdout + result.stderr
            
            else:
                return False, f"Unknown test type: {test_type}"
        
        except subprocess.TimeoutExpired:
            return False, f"Test timeout ({self.timeout}s)"
        except Exception as e:
            return False, f"Test error: {str(e)}"
    
    def validate_test_pair(self, bug_id: str, filepath: str, match_idx: int,
                          test_path_fixing: str, test_path_regressor: str,
                          fixing_hash: str, regressor_hash: str) -> Dict:
        """Validate test against both commits"""
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
        
        # Load test content
        test_content_fixing = self._extract_test_content_from_step12(
            bug_id, filepath, match_idx, test_path_fixing, 'fixing')
        test_content_regressor = self._extract_test_content_from_step12(
            bug_id, filepath, match_idx, test_path_regressor, 'regressor')
        
        test_content = test_content_fixing or test_content_regressor
        if not test_content:
            result['status'] = 'test_content_error'
            return result
        
        # Load source content
        fixing_content = self._find_source_content_in_step11(bug_id, filepath, fixing_hash)
        regressor_content = self._find_source_content_in_step11(bug_id, filepath, regressor_hash)
        
        if not fixing_content or not regressor_content:
            result['status'] = 'missing_source_content'
            return result
        
        # Create temp environment and run tests
        temp_dir = tempfile.mkdtemp(prefix='test_validate_')
        
        try:
            # Write test file
            test_file = os.path.join(temp_dir, os.path.basename(test_path_fixing))
            with open(test_file, 'w', encoding='utf-8') as f:
                f.write(test_content)
            
            # Test with regressor version
            source_file = os.path.join(temp_dir, os.path.basename(filepath))
            with open(source_file, 'w', encoding='utf-8') as f:
                f.write(regressor_content)
            
            regressor_passed, regressor_output = self._run_test(
                test_file, result['test_type'], source_file, temp_dir)
            result['regressor_result'] = {'passed': regressor_passed, 'output_preview': regressor_output[:500]}
            
            # Test with fixing version
            with open(source_file, 'w', encoding='utf-8') as f:
                f.write(fixing_content)
            
            fixing_passed, fixing_output = self._run_test(
                test_file, result['test_type'], source_file, temp_dir)
            result['fixing_result'] = {'passed': fixing_passed, 'output_preview': fixing_output[:500]}
            
            # Determine status
            if not regressor_passed and fixing_passed:
                result['status'] = 'confirms_regression'
                result['confirms_regression'] = True
            elif regressor_passed and fixing_passed:
                result['status'] = 'both_pass'
            elif not regressor_passed and not fixing_passed:
                result['status'] = 'both_fail'
            else:
                result['status'] = 'unexpected_behavior'
        
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        return result
    
    def validate_all(self) -> Dict:
        """Main validation process"""
        self.logger.info("\n" + "="*80)
        self.logger.info("STEP 13: TEST VALIDATION")
        self.logger.info("="*80)
        
        if not self.step12_summary:
            return {}
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'bugs': 0, 'tests_run': 0,
                'confirms_regression': 0, 'both_pass': 0, 'both_fail': 0,
                'unexpected': 0, 'errors': 0
            },
            'bugs': {}
        }
        
        for bug_id, bug_info in self.step12_summary.get('bugs', {}).items():
            self.logger.info(f"\nBug {bug_id}:")
            bug_results = {'files': []}
            
            for file_data in bug_info.get('files', []):
                filepath = file_data['filepath']
                file_results = {'source_file': filepath, 'validations': []}
                
                for match in file_data.get('matches', []):
                    match_idx = match['match_idx']
                    
                    fixing_tests = match['fixing_commit'].get('tests', [])
                    regressor_tests = match['regressor_commit'].get('tests', [])
                    
                    if not fixing_tests and not regressor_tests:
                        continue
                    
                    test_path_fixing = fixing_tests[0]['path'] if fixing_tests else regressor_tests[0]['path']
                    test_path_regressor = regressor_tests[0]['path'] if regressor_tests else test_path_fixing
                    
                    validation = self.validate_test_pair(
                        bug_id, filepath, match_idx,
                        test_path_fixing, test_path_regressor,
                        match['fixing_commit']['hash'],
                        match['regressor_commit']['hash']
                    )
                    
                    file_results['validations'].append(validation)
                    results['summary']['tests_run'] += 1
                    
                    if validation['status'] == 'confirms_regression':
                        results['summary']['confirms_regression'] += 1
                        self.logger.info(f"  ✓ Match {match_idx}: confirms_regression")
                    elif validation['status'] == 'both_pass':
                        results['summary']['both_pass'] += 1
                        self.logger.info(f"  ~ Match {match_idx}: both_pass")
                    elif validation['status'] == 'both_fail':
                        results['summary']['both_fail'] += 1
                        self.logger.info(f"  ✗ Match {match_idx}: both_fail")
                    else:
                        results['summary']['errors'] += 1
                        self.logger.info(f"  ! Match {match_idx}: {validation['status']}")
                
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
        output_file = self.output_dir / 'step13_validation_results.json'
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
    
    if not STEP11_DIR.exists():
        print(f"ERROR: Step 11 directory not found: {STEP11_DIR}")
        print("Please run Step 11 first.")
        sys.exit(1)
    
    validator = TestValidator(verbose=False, timeout=60)
    validator.validate_all()
    
    print("\n" + "="*80)
    print("✓ STEP 13 COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()