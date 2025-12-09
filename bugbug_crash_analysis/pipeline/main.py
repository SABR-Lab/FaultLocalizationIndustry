#!/usr/bin/env python3
"""
MOZILLA CRASH ANALYSIS PIPELINE
===============================
Analyzes crash signatures → bugs → regressions → fixes → test validation

Usage:
  python main.py --run-all                  # Run full pipeline
  python main.py --step 1                   # Run single step
  python main.py --step 5                   # Auto-detects input from step 4
  python main.py --list-steps               # Show all steps

Author: Sidiqa (DePaul University / Mozilla Research)
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, Optional




DEFAULT_SIGNATURE = "OOM | small"

DEFAULT_CONFIG = {
    'local_repos': {
        'mozilla-central': './mozilla-central',
        'mozilla-autoland': './mozilla-autoland',
        'mozilla-release': './mozilla-release',
        'mozilla-esr115': './mozilla-esr115'
    },
    'max_workers': None,
    'months_back': 6,
    'max_crashes': None,
    'max_fetch_per_day': 10000,
    'timeout': 60,
    'verbose': False
}

STEPS = {
    1:  'Crash Signature → Bug Mapper',
    2:  'BugBug Validator',
    3:  'Regression Analyzer',
    4:  'Code Diff Extractor',
    5:  'Overlapping Files Detector',
    6:  'Full File Content Extractor',
    7:  'Method Extractor (Tree-sitter)',
    8:  'Method-Diff Matcher',
    9:  'Fixing-Regressor Matcher',
    10: 'Matched Method Diff Extractor',
    11: 'Test File Extractor',
    12: 'Test Validator',
}




def get_step1_output(signature: str) -> str:
    safe_sig = signature.replace(':', '_').replace('/', '_')[:50]
    return f"step1_sig_to_bugs_{safe_sig}.json"

def get_step2_output(signature: str) -> str:
    safe_sig = signature.replace(':', '_').replace('/', '_')[:50]
    return f"step2_bugbug_analysis_{safe_sig}.json"

def get_step3_output(signature: str) -> str:
    safe_sig = signature.replace(':', '_').replace('/', '_')[:50]
    return f"step3_regression_analysis_{safe_sig}.json"

STEP4_OUTPUT = "step4_extracted_diffs"
STEP5_OUTPUT = "step5_overlapping_files_output/overlapping_files.json"
STEP6_OUTPUT = "step6_full_file_contents/Step6_extraction_results.json"
STEP7_OUTPUT = "step7_method_extraction/Step7_method_extraction.json"
STEP8_OUTPUT = "step8_method_diff_matching/Step8_method_diff_matching.json"
STEP9_OUTPUT = "step9_fixing_regressor_method_matching/Step9_fixing_regressor_matches.json"
STEP10_OUTPUT = "step10_matched_methodDiffs"
STEP11_OUTPUT = "test_extraction"
STEP12_OUTPUT = "step12_test_validation"


# ==============================================================================
# INPUT DETECTION
# ==============================================================================

def get_input_for_step(step: int, signature: str) -> Optional[str]:
    if step == 1:
        return None
    elif step == 2:
        return get_step1_output(signature)
    elif step == 3:
        return get_step2_output(signature)
    elif step == 4:
        return get_step3_output(signature)
    elif step == 5:
        return STEP4_OUTPUT
    elif step == 6:
        return STEP5_OUTPUT
    elif step == 7:
        return STEP6_OUTPUT
    elif step == 8:
        return STEP7_OUTPUT
    elif step == 9:
        return STEP8_OUTPUT
    elif step == 10:
        return STEP9_OUTPUT
    elif step == 11:
        return STEP9_OUTPUT
    elif step == 12:
        return STEP11_OUTPUT
    return None


def verify_input_exists(step: int, input_path: str) -> bool:
    if step in [5, 12]:
        return os.path.isdir(input_path)
    else:
        return os.path.exists(input_path)



def list_steps():
    print("\n" + "=" * 70)
    print(" PIPELINE STEPS")
    print("=" * 70)
    for num, name in STEPS.items():
        print(f"  {num:2}. {name}")
    print("=" * 70 + "\n")


def load_config(config_file: str = None) -> Dict:
    config = DEFAULT_CONFIG.copy()
    if config_file and os.path.exists(config_file):
        with open(config_file, 'r') as f:
            config.update(json.load(f))
    return config


def print_step_header(step: int):
    print(f"\n{'=' * 70}")
    print(f" STEP {step}: {STEPS[step].upper()}")
    print(f"{'=' * 70}\n")



def run_step1(signature: str, config: Dict) -> str:
    from step1_parallalized import SignatureToBugMapper
    
    mapper = SignatureToBugMapper(
        local_repos=config['local_repos'],
        max_workers=config.get('max_workers')
    )
    
    results = mapper.map_signature_to_bugs(
        signature=signature,
        months_back=config.get('months_back', 6),
        max_crashes=config.get('max_crashes'),
        max_fetch_per_day=config.get('max_fetch_per_day', 10000)
    )
    
    output_file = mapper.save_results(results)
    mapper.print_bugs_report(results)
    
    print(f"  Output: {output_file}")
    return output_file


def run_step2(input_file: str, config: Dict) -> str:
    from Step2_bug_fetcher import BugBugAnalyzer
    
    print(f"  Input: {input_file}")
    
    analyzer = BugBugAnalyzer()
    results = analyzer.validate_bugs(input_file)
    output_file = analyzer.save_results(results)
    analyzer.print_bug_details(results)
    
    print(f"  Output: {output_file}")
    return output_file


def run_step3(input_file: str, config: Dict) -> str:
    from Step3_bug_details_extractor import RegressionAnalyzer
    
    print(f"  Input: {input_file}")
    
    analyzer = RegressionAnalyzer()
    results = analyzer.analyze_from_step2_file(input_file)
    output_file = analyzer.save_results(results)
    
    print(f"  Output: {output_file}")
    return output_file


def run_step4(input_file: str, config: Dict) -> str:
    from Step4_diff_extractor import CodeExtractor
    
    print(f"  Input: {input_file}")
    
    extractor = CodeExtractor(
        output_dir="step4_extracted_diffs",
        local_repos=config['local_repos']
    )
    
    results = extractor.extract_from_analysis(input_file)
    extractor.save_summary(results)
    
    output_dir = STEP4_OUTPUT
    print(f"  Output: {output_dir}/")
    return output_dir


def run_step5(input_dir: str, config: Dict) -> str:
    from Step5_overlapping_files import OverlappingFilesExtractor
    
    print(f"  Input: {input_dir}/")
    
    extractor = OverlappingFilesExtractor(
        extracted_diffs_dir=input_dir,
        output_dir="step5_overlapping_files_output"
    )
    
    results = extractor.extract_all_overlapping_files()
    extractor.save_results(results)
    extractor.save_debug_report(results)
    
    output_file = STEP5_OUTPUT
    print(f"  Output: {output_file}")
    return output_file


def run_step6(input_file: str, config: Dict) -> str:
    from Step6_overlappingFiles_fullCotent import FullFileExtractor
    
    print(f"  Input: {input_file}")
    
    extractor = FullFileExtractor(
        step5_file=input_file,
        output_dir="step6_full_file_contents",
        local_repos=config['local_repos']
    )
    
    results = extractor.extract_all_files()
    extractor.save_results(results)
    extractor.create_summary_report(results)
    
    output_file = STEP6_OUTPUT
    print(f"  Output: {output_file}")
    return output_file


def run_step7(input_file: str, config: Dict) -> str:
    from Step7_Parser import MethodExtractor
    
    print(f"  Input: {input_file}")
    
    extractor = MethodExtractor(
        step6_results_file=input_file,
        output_dir="step7_method_extraction"
    )
    
    results = extractor.process_all_bugs()
    extractor.save_results(results)
    extractor.create_summary_report(results)
    
    output_file = STEP7_OUTPUT
    print(f"  Output: {output_file}")
    return output_file


def run_step8(input_file: str, config: Dict) -> str:
    from Step8_diff_methods_matcher import MethodDiffMatcher
    
    step4_dir = STEP4_OUTPUT
    step7_file = input_file
    
    print(f"  Input 1: {step7_file}")
    print(f"  Input 2: {step4_dir}/")
    
    matcher = MethodDiffMatcher(
        step4_diffs_dir=step4_dir,
        step7_file=step7_file,
        output_dir="step8_method_diff_matching",
        debug=config.get('verbose', False)
    )
    
    results = matcher.process_all_bugs()
    matcher.save_results(results)
    matcher.create_summary_report(results)
    
    output_file = STEP8_OUTPUT
    print(f"  Output: {output_file}")
    return output_file


def run_step9(input_file: str, config: Dict) -> str:
    from Step9_fixing_regressor_matcher import FixingRegressorMatcher
    
    print(f"  Input: {input_file}")
    
    matcher = FixingRegressorMatcher(
        step8_json_file=input_file,
        output_dir="step9_fixing_regressor_method_matching"
    )
    
    results = matcher.analyze_all_bugs()
    matcher.save_results(results)
    matcher.create_summary_report(results)
    
    output_file = STEP9_OUTPUT
    print(f"  Output: {output_file}")
    return output_file


def run_step10(input_file: str, config: Dict) -> str:
    from Step10_matched_method_Diff import LocalRepoExtractor
    
    print(f"  Input: {input_file}")
    
    extractor = LocalRepoExtractor(
        local_repos=config['local_repos'],
        output_dir="step10_matched_methodDiffs",
        debug=config.get('verbose', False)
    )
    
    extractor.extract_all(input_file)
    
    output_dir = STEP10_OUTPUT
    print(f"  Output: {output_dir}/")
    return output_dir


def run_step11(input_file: str, config: Dict) -> str:
    from Step11_tests import TestFileExtractor
    
    print(f"  Input: {input_file}")
    
    extractor = TestFileExtractor(
        local_repos=config['local_repos'],
        output_dir="test_extraction",
        debug=config.get('verbose', False),
        step9_json=input_file
    )
    
    extractor.extract_all()
    
    output_dir = STEP11_OUTPUT
    print(f"  Output: {output_dir}/")
    return output_dir


def run_step12(input_dir: str, config: Dict) -> str:
    from Step12_test_validation import TestValidator
    
    step11_dir = input_dir
    step10_dir = STEP10_OUTPUT
    
    print(f"  Input 1: {step11_dir}/")
    print(f"  Input 2: {step10_dir}/")
    
    validator = TestValidator(
        step11_results_dir=step11_dir,
        step10_results_dir=step10_dir,
        output_dir="step12_test_validation",
        repo_root=config['local_repos'].get('mozilla-central', './mozilla-central'),
        verbose=config.get('verbose', False),
        timeout=config.get('timeout', 60),
        local_repos=config['local_repos']
    )
    
    validator.validate_all()
    
    output_dir = STEP12_OUTPUT
    print(f"  Output: {output_dir}/")
    return output_dir


# ==============================================================================
# PIPELINE EXECUTION
# ==============================================================================

def run_step(step: int, signature: str, config: Dict, manual_input: str = None) -> str:
    print_step_header(step)
    
    if step == 1:
        return run_step1(signature, config)
    
    input_path = manual_input if manual_input else get_input_for_step(step, signature)
    
    if not input_path:
        raise ValueError(f"No input path for step {step}")
    
    if not verify_input_exists(step, input_path):
        raise FileNotFoundError(
            f"Input not found: {input_path}\n"
            f"Run step {step - 1} first, or provide --input manually."
        )
    
    runners = {
        2: run_step2,
        3: run_step3,
        4: run_step4,
        5: run_step5,
        6: run_step6,
        7: run_step7,
        8: run_step8,
        9: run_step9,
        10: run_step10,
        11: run_step11,
        12: run_step12,
    }
    
    return runners[step](input_path, config)


def run_pipeline(signature: str, config: Dict, start: int = 1, end: int = 12) -> Dict[str, str]:
    print("\n" + "=" * 70)
    print(" MOZILLA CRASH ANALYSIS PIPELINE")
    print("=" * 70)
    print(f" Signature: {signature}")
    print(f" Steps: {start} → {end}")
    print(f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    outputs = {}
    
    for step in range(start, end + 1):
        try:
            output = run_step(step, signature, config)
            outputs[f'step{step}'] = output
            print(f"\n✓ Step {step} complete → {output}")
            
        except Exception as e:
            print(f"\n✗ Step {step} failed: {e}")
            raise
    
    print("\n" + "=" * 70)
    print(" PIPELINE COMPLETE")
    print("=" * 70)
    print(f" Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n Outputs:")
    for step_name, path in outputs.items():
        print(f"   {step_name}: {path}")
    print("=" * 70 + "\n")
    
    return outputs


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Mozilla Crash Analysis Pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python main.py --run-all                    Run full pipeline (steps 1-12)
  python main.py --step 1                     Run only step 1
  python main.py --step 5                     Run step 5 (auto-detects input)
  python main.py --step 3 --input file.json   Run step 3 with custom input
  python main.py --signature "OOM" --run-all  Run with custom signature
  python main.py --list-steps                 Show all pipeline steps

Default signature: "{DEFAULT_SIGNATURE}"
        """
    )
    
    parser.add_argument('--signature', '-s', default=DEFAULT_SIGNATURE,
                        help=f'Crash signature (default: "{DEFAULT_SIGNATURE}")')
    parser.add_argument('--run-all', action='store_true', 
                        help='Run full pipeline')
    parser.add_argument('--step', type=int, choices=range(1, 13), metavar='N',
                        help='Run single step (1-12)')
    parser.add_argument('--start-step', type=int, default=1, 
                        help='Start from step N (with --run-all)')
    parser.add_argument('--end-step', type=int, default=12, 
                        help='End at step N (with --run-all)')
    parser.add_argument('--input', '-i', 
                        help='Manual input file/directory (overrides auto-detect)')
    parser.add_argument('--config', '-c', 
                        help='Config file (JSON)')
    parser.add_argument('--list-steps', action='store_true', 
                        help='List all pipeline steps')
    parser.add_argument('--verbose', '-v', action='store_true', 
                        help='Verbose output')
    parser.add_argument('--months-back', type=int, default=6, 
                        help='Months to search (Step 1)')
    parser.add_argument('--max-crashes', type=int, 
                        help='Max crashes to process (Step 1)')
    
    args = parser.parse_args()
    
    if args.list_steps:
        list_steps()
        return 0
    
    config = load_config(args.config)
    config['verbose'] = args.verbose
    config['months_back'] = args.months_back
    if args.max_crashes:
        config['max_crashes'] = args.max_crashes
    
    try:
        if args.run_all:
            print(f"Using signature: {args.signature}")
            run_pipeline(args.signature, config, args.start_step, args.end_step)
        
        elif args.step:
            print(f"Using signature: {args.signature}")
            output = run_step(args.step, args.signature, config, args.input)
            print(f"\n✓ Final output: {output}")
        
        else:
            parser.print_help()
            return 1
        
        return 0
    
    except KeyboardInterrupt:
        print("\n\nPipeline interrupted by user")
        return 130
    
    except Exception as e:
        print(f"\n✗ Pipeline failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())