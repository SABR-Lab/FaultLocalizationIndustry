#!/usr/bin/env python3
"""
================================================================================
STEP 8: PARSE CODE FILES WITH TREE-SITTER TO EXTRACT METHODS
================================================================================

PURPOSE:
--------
Parse the extracted code files from Step 7 using tree-sitter to identify
all methods/functions in each file.

INPUT:
------
- Step 7 output: outputs/step7_full_file_contents/bug_*/extraction_metadata.json

OUTPUT:
-------
outputs/step8_method_extraction/
├── bugs/
│   ├── bug_<ID>.json
│   └── ...
├── extraction_summary.json
└── extraction_report.txt
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import sys

# Setup paths
script_dir = Path(__file__).resolve().parent
parent_dir = script_dir.parent
sys.path.insert(0, str(parent_dir))

os.chdir(parent_dir)
print(f"Changed working directory to: {parent_dir}")

# Try importing tree-sitter
try:
    from tree_sitter import Language, Parser, Query, QueryCursor
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    print("WARNING: tree-sitter not installed!")

# Try importing language modules
LANGUAGE_MODULES = {}
try:
    import tree_sitter_c
    LANGUAGE_MODULES['c'] = tree_sitter_c
except ImportError:
    pass

try:
    import tree_sitter_cpp
    LANGUAGE_MODULES['cpp'] = tree_sitter_cpp
except ImportError:
    pass

try:
    import tree_sitter_python
    LANGUAGE_MODULES['python'] = tree_sitter_python
except ImportError:
    pass

try:
    import tree_sitter_javascript
    LANGUAGE_MODULES['javascript'] = tree_sitter_javascript
except ImportError:
    pass


class MethodExtractor:
    """Extract methods/functions from code files using tree-sitter"""
    
    EXTENSION_TO_LANGUAGE = {
        '.c': 'c', '.h': 'c',
        '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp',
        '.hpp': 'cpp', '.hh': 'cpp', '.hxx': 'cpp',
        '.py': 'python',
        '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
    }
    
    FUNCTION_QUERIES = {
        'c': """
            (function_definition
                declarator: (function_declarator
                    declarator: (identifier) @function.name))
        """,
        'cpp': """
            (function_definition
                declarator: (function_declarator
                    declarator: (identifier) @function.name))
            (function_definition
                declarator: (function_declarator
                    declarator: (qualified_identifier
                        name: (identifier) @method.name)))
            (function_definition
                declarator: (function_declarator
                    declarator: (field_identifier) @method.name))
        """,
        'python': """
            (function_definition
                name: (identifier) @function.name)
        """,
        'javascript': """
            (function_declaration
                name: (identifier) @function.name)
            (method_definition
                name: (property_identifier) @method.name)
        """
    }
    
    def __init__(self):
        if not TREE_SITTER_AVAILABLE:
            raise ImportError("tree-sitter is required but not installed!")
        
        if not LANGUAGE_MODULES:
            raise ImportError("No tree-sitter language modules installed!")
        
        # Paths
        self.script_dir = Path(__file__).resolve().parent
        self.outputs_base = self.script_dir / "outputs"
        
        # INPUT: Step 7 output (individual bug folders)
        self.input_dir = self.outputs_base / "step7_full_file_contents"
        
        # OUTPUT: Step 8 output
        self.output_dir = self.outputs_base / "step8_method_extraction"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create bugs output directory
        self.bugs_output_dir = self.output_dir / "bugs"
        self.bugs_output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Input directory (Step 7 output):")
        print(f"  {self.input_dir}")
        print(f"Output directory:")
        print(f"  {self.output_dir}")
        
        self.parsers = {}
        self._initialize_parsers()

    def _initialize_parsers(self):
        """Initialize tree-sitter parsers"""
        print("\nInitializing tree-sitter parsers...")
        
        for lang_name, lang_module in LANGUAGE_MODULES.items():
            try:
                language = Language(lang_module.language())
                parser = Parser(language)
                self.parsers[lang_name] = {'parser': parser, 'language': language}
                print(f"   Initialized {lang_name}")
            except Exception as e:
                print(f"   Failed to initialize {lang_name}: {e}")
        
        print(f"\nInitialized {len(self.parsers)} language parsers\n")

    def get_language_for_file(self, filepath: str) -> Optional[str]:
        """Get language based on file extension"""
        ext = Path(filepath).suffix.lower()
        return self.EXTENSION_TO_LANGUAGE.get(ext)
    
    def extract_methods_from_content(self, content: str, language: str, 
                                    filepath: str, line_offset: int = 0) -> List[Dict]:
        """Extract methods from content"""
        if language not in self.parsers:
            return []
        
        parser_info = self.parsers[language]
        parser = parser_info['parser']
        lang = parser_info['language']
        
        try:
            tree = parser.parse(bytes(content, 'utf8'))
            root_node = tree.root_node
            
            query_string = self.FUNCTION_QUERIES.get(language, '')
            if not query_string:
                return []
            
            query = Query(lang, query_string)
            cursor = QueryCursor(query)
            
            methods = []
            content_lines = content.split('\n')
            
            for pattern_index, captures_dict in cursor.matches(root_node):
                for capture_name, nodes in captures_dict.items():
                    node_list = nodes if isinstance(nodes, list) else [nodes]
                    
                    for node in node_list:
                        method_name = content[node.start_byte:node.end_byte]
                        
                        parent = node.parent
                        attempts = 0
                        while parent and attempts < 10:
                            if parent.type in [
                                'function_definition', 'function_declaration', 
                                'method_definition', 'function_item', 
                                'method_declaration', 'constructor_declaration'
                            ]:
                                break
                            parent = parent.parent
                            attempts += 1
                        
                        if parent:
                            start_line = parent.start_point[0] + 1 + line_offset
                            end_line = parent.end_point[0] + 1 + line_offset
                            
                            signature_lines = []
                            for i in range(parent.start_point[0], min(parent.start_point[0] + 3, len(content_lines))):
                                if i < len(content_lines):
                                    signature_lines.append(content_lines[i].strip())
                            signature = ' '.join(signature_lines)[:200]
                            
                            methods.append({
                                'name': method_name,
                                'type': capture_name.replace('.name', ''),
                                'start_line': start_line,
                                'end_line': end_line,
                                'line_count': end_line - start_line + 1,
                                'signature': signature
                            })
            
            # Remove duplicates
            seen = set()
            unique_methods = []
            for method in sorted(methods, key=lambda x: x['start_line']):
                key = (method['name'], method['start_line'])
                if key not in seen:
                    seen.add(key)
                    unique_methods.append(method)
            
            return unique_methods
            
        except Exception as e:
            print(f"      ✗ Error parsing {filepath}: {e}")
            return []

    def parse_extracted_file(self, file_path: str, original_filepath: str) -> Dict:
        """Parse extracted file and extract methods"""
        language = self.get_language_for_file(original_filepath)
        
        if not language:
            return {'success': False, 'error': 'Unsupported language', 'methods': []}
        
        if language not in self.parsers:
            return {'success': False, 'error': f'Parser not available for {language}', 'methods': []}
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                code_content = f.read()
            
            methods = self.extract_methods_from_content(code_content, language, original_filepath)
            
            return {
                'success': True,
                'language': language,
                'methods': methods,
                'method_count': len(methods),
                'file_size': len(code_content),
                'line_count': len(code_content.split('\n'))
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e), 'methods': []}

    def load_bug_metadata(self, bug_dir: Path) -> Optional[Dict]:
        """Load extraction_metadata.json from a bug directory"""
        metadata_file = bug_dir / "extraction_metadata.json"
        if not metadata_file.exists():
            return None
        
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  Warning: Failed to load {metadata_file}: {e}")
            return None

    def process_bug(self, bug_id: str, bug_data: Dict) -> Dict:
        """Process a single bug"""
        print(f"\n  Processing {len(bug_data.get('extracted_files', []))} files...")
        
        results = {
            'bug_id': bug_id,
            'extraction_timestamp': datetime.now().isoformat(),
            'files': []
        }
        
        total_methods = 0
        
        for file_data in bug_data.get('extracted_files', []):
            filepath = file_data['filepath']
            
            file_result = {
                'filepath': filepath,
                'fixing_commits': [],
                'regressor_commits': []
            }
            
            # Parse fixing commits
            for commit_data in file_data.get('fixing_commits', []):
                output_file = commit_data.get('output_file', '')
                if not output_file or not os.path.exists(output_file):
                    continue
                
                parse_result = self.parse_extracted_file(output_file, filepath)
                
                commit_result = {
                    'commit_hash': commit_data.get('commit_hash'),
                    'full_hash': commit_data.get('full_hash'),
                    'parse_success': parse_result['success'],
                    'language': parse_result.get('language', 'unknown'),
                    'methods': parse_result['methods'],
                    'method_count': len(parse_result['methods'])
                }
                
                total_methods += len(parse_result['methods'])
                file_result['fixing_commits'].append(commit_result)
            
            # Parse regressor commits
            for commit_data in file_data.get('regressor_commits', []):
                output_file = commit_data.get('output_file', '')
                if not output_file or not os.path.exists(output_file):
                    continue
                
                parse_result = self.parse_extracted_file(output_file, filepath)
                
                commit_result = {
                    'commit_hash': commit_data.get('commit_hash'),
                    'full_hash': commit_data.get('full_hash'),
                    'regressor_bug_id': commit_data.get('regressor_bug_id'),
                    'parse_success': parse_result['success'],
                    'language': parse_result.get('language', 'unknown'),
                    'methods': parse_result['methods'],
                    'method_count': len(parse_result['methods'])
                }
                
                total_methods += len(parse_result['methods'])
                file_result['regressor_commits'].append(commit_result)
            
            if file_result['fixing_commits'] or file_result['regressor_commits']:
                results['files'].append(file_result)
        
        results['summary'] = {
            'total_files': len(results['files']),
            'total_methods': total_methods
        }
        
        return results
    
    def process_all_bugs(self) -> Dict:
        """Process all bugs from Step 7 output"""
        print("=" * 80)
        print("STEP 8: METHOD EXTRACTION WITH TREE-SITTER")
        print("=" * 80 + "\n")
        
        if not self.input_dir.exists():
            print(f"ERROR: Input directory not found: {self.input_dir}")
            print("Please run Step 7 first.")
            return {'error': 'Input directory not found'}
        
        # Find all bug directories
        bug_dirs = sorted([
            d for d in self.input_dir.iterdir() 
            if d.is_dir() and d.name.startswith('bug_')
        ])
        
        if not bug_dirs:
            print(f"ERROR: No bug directories found in {self.input_dir}")
            return {'error': 'No bug directories found'}
        
        print(f"Found {len(bug_dirs)} bug directories to process")
        print(f"Languages supported: {', '.join(self.parsers.keys())}\n")
        
        total_bugs_processed = 0
        total_methods_extracted = 0
        successful_bug_ids = []
        failed_bug_ids = []
        
        for i, bug_dir in enumerate(bug_dirs, 1):
            bug_id = bug_dir.name.replace('bug_', '')
            print(f"[{i}/{len(bug_dirs)}] Bug {bug_id}...")
            
            # Load bug metadata from Step 7
            bug_data = self.load_bug_metadata(bug_dir)
            if not bug_data:
                print(f"   No extraction_metadata.json found")
                failed_bug_ids.append(bug_id)
                continue
            
            # Process the bug
            bug_result = self.process_bug(bug_id, bug_data)
            
            # Save individual bug result
            bug_output_file = self.bugs_output_dir / f"bug_{bug_id}.json"
            with open(bug_output_file, 'w', encoding='utf-8') as f:
                json.dump(bug_result, f, indent=2)
            
            total_bugs_processed += 1
            bug_methods = bug_result['summary']['total_methods']
            total_methods_extracted += bug_methods
            
            if bug_methods > 0:
                successful_bug_ids.append(bug_id)
                print(f"    ✓ Extracted {bug_methods} methods → saved")
            else:
                failed_bug_ids.append(bug_id)
                print(f"    ✗ No methods extracted")
        
        # Build summary
        summary = {
            'extraction_timestamp': datetime.now().isoformat(),
            'input_directory': str(self.input_dir),
            'output_directory': str(self.output_dir),
            'languages_supported': list(self.parsers.keys()),
            'summary': {
                'total_bugs_processed': total_bugs_processed,
                'bugs_with_methods': len(successful_bug_ids),
                'bugs_without_methods': len(failed_bug_ids),
                'total_methods_extracted': total_methods_extracted
            },
            'successful_bug_ids': successful_bug_ids,
            'failed_bug_ids': failed_bug_ids
        }
        
        self._print_summary(summary)
        
        return summary
    
    def _print_summary(self, summary: Dict):
        """Print extraction summary"""
        print(f"\n{'=' * 80}")
        print("EXTRACTION SUMMARY")
        print(f"{'=' * 80}")
        
        s = summary['summary']
        print(f"\nBugs processed: {s['total_bugs_processed']}")
        print(f"   With methods: {s['bugs_with_methods']}")
        print(f"   Without methods: {s['bugs_without_methods']}")
        print(f"\nTotal methods extracted: {s['total_methods_extracted']}")
    
    def save_results(self, results: Dict):
        """Save extraction summary"""
        print(f"\n{'=' * 80}")
        print("SAVING RESULTS")
        print(f"{'=' * 80}\n")
        
        # Save summary JSON
        summary_file = self.output_dir / 'extraction_summary.json'
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        print(f"✓ Saved extraction summary to {summary_file}")
        
        # Save report
        report_file = self.output_dir / 'extraction_report.txt'
        self._save_report(results, report_file)
        print(f" Saved extraction report to {report_file}")
        
        # Individual bug files already saved during processing
        print(f" Individual bug files saved to {self.bugs_output_dir}")
    
    def _save_report(self, results: Dict, output_path: Path):
        """Save human-readable report"""
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("STEP 8: METHOD EXTRACTION REPORT\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"Extraction Time: {results['extraction_timestamp']}\n")
            f.write(f"Input: {results['input_directory']}\n")
            f.write(f"Output: {results['output_directory']}\n")
            f.write(f"Languages: {', '.join(results['languages_supported'])}\n\n")
            
            s = results['summary']
            f.write("SUMMARY\n")
            f.write("-" * 40 + "\n")
            f.write(f"Bugs processed: {s['total_bugs_processed']}\n")
            f.write(f"Bugs with methods: {s['bugs_with_methods']}\n")
            f.write(f"Bugs without methods: {s['bugs_without_methods']}\n")
            f.write(f"Total methods extracted: {s['total_methods_extracted']}\n\n")
            
            if results['successful_bug_ids']:
                f.write("SUCCESSFUL BUGS\n")
                f.write("-" * 40 + "\n")
                for bug_id in results['successful_bug_ids']:
                    f.write(f"  Bug {bug_id}\n")
                f.write("\n")
            
            if results['failed_bug_ids']:
                f.write("FAILED/SKIPPED BUGS\n")
                f.write("-" * 40 + "\n")
                for bug_id in results['failed_bug_ids']:
                    f.write(f"  Bug {bug_id}\n")


def main():
    """Main execution function"""
    print("=" * 80)
    print("STEP 8: METHOD EXTRACTION WITH TREE-SITTER")
    print("=" * 80 + "\n")
    
    if not TREE_SITTER_AVAILABLE:
        print("ERROR: tree-sitter is not installed!")
        print("Install with: pip install tree-sitter==0.21.3")
        sys.exit(1)
    
    if not LANGUAGE_MODULES:
        print("ERROR: No tree-sitter language modules installed!")
        print("Install with: pip install tree-sitter-c tree-sitter-cpp tree-sitter-python tree-sitter-javascript")
        sys.exit(1)
    
    print(f"Available language parsers: {', '.join(LANGUAGE_MODULES.keys())}\n")
    
    extractor = MethodExtractor()
    results = extractor.process_all_bugs()
    
    if 'error' not in results:
        extractor.save_results(results)
        
        print("\n" + "=" * 80)
        print("✓ STEP 8 COMPLETE")
        print("=" * 80)
        print(f"\nOutput: {extractor.output_dir}")
        print(f"\nEach bug file contains:")
        print(f"  - files[].fixing_commits[].methods[]")
        print(f"  - files[].regressor_commits[].methods[]")
    else:
        print("\n" + "=" * 80)
        print(" STEP 8 FAILED")
        print("=" * 80)
        print(f"\nError: {results.get('error')}")


if __name__ == "__main__":
    main()