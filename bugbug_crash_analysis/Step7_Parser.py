#!/usr/bin/env python3
"""
Step 7: Parse Code Files with Tree-sitter to Extract Methods
Compatible with tree-sitter 0.20.x
Extracts methods with accurate line numbers (excluding header)
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

try:
    from tree_sitter import Language, Parser, Query, QueryCursor
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    print("ERROR: tree-sitter not installed!")

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
        '.c': 'c',
        '.h': 'c',
        '.cpp': 'cpp',
        '.cc': 'cpp',
        '.cxx': 'cpp',
        '.hpp': 'cpp',
        '.hh': 'cpp',
        '.hxx': 'cpp',
        '.py': 'python',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.mjs': 'javascript',
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
        """,
        'java': """
            (method_declaration
                name: (identifier) @method.name)
            (constructor_declaration
                name: (identifier) @constructor.name)
        """
    }
    
    def __init__(self, step6_results_file: str, output_dir: str = "method_extraction"):
        if not TREE_SITTER_AVAILABLE:
            raise ImportError("tree-sitter is required but not installed!")
        
        if not LANGUAGE_MODULES:
            raise ImportError("No tree-sitter language modules installed!")
        
        self.step6_results_file = step6_results_file
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"Loading Step 6 results from: {step6_results_file}")
        with open(step6_results_file, 'r') as f:
            self.step6_data = json.load(f)
        
        print(f"Found {len(self.step6_data['bugs'])} bugs\n")
        
        self.parsers = {}
        self._initialize_parsers()

    def _initialize_parsers(self):
        """Initialize tree-sitter parsers"""
        print("Initializing tree-sitter parsers...")
        
        for lang_name, lang_module in LANGUAGE_MODULES.items():
            try:
                language = Language(lang_module.language())
                parser = Parser(language)
                
                self.parsers[lang_name] = {
                    'parser': parser,
                    'language': language
                }
                print(f"  ✓ Initialized {lang_name}")
                        
            except Exception as e:
                print(f"  ✗ Failed to initialize {lang_name}: {str(e)}")
                import traceback
                traceback.print_exc()
        
        print(f"\nInitialized {len(self.parsers)} language parsers\n")

    def get_language_for_file(self, filepath: str) -> Optional[str]:
        """Get language based on file extension"""
        ext = Path(filepath).suffix.lower()
        return self.EXTENSION_TO_LANGUAGE.get(ext)
    
    def extract_methods_from_content(self, content: str, language: str, 
                                    filepath: str, line_offset: int = 0) -> List[Dict]:
        """
        Extract methods from content.
        line_offset: number of lines from header to add back to get actual file line numbers
        """
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
                                'function_definition', 'function_declaration', 'method_definition',
                                'function_item', 'method_declaration', 'constructor_declaration'
                            ]:
                                break
                            parent = parent.parent
                            attempts += 1
                        
                        if parent:
                            # Add line_offset to convert from code-only line numbers to actual file line numbers
                            start_line = parent.start_point[0] + 1 + line_offset
                            end_line = parent.end_point[0] + 1 + line_offset
                            start_col = parent.start_point[1]
                            end_col = parent.end_point[1]
                            
                            signature_lines = []
                            for line_idx in range(parent.start_point[0], min(parent.start_point[0] + 3, len(content_lines))):
                                if line_idx < len(content_lines):
                                    signature_lines.append(content_lines[line_idx].strip())
                            signature = ' '.join(signature_lines)
                            
                            if len(signature) > 200:
                                signature = signature[:200] + '...'
                            
                            methods.append({
                                'name': method_name,
                                'type': capture_name.replace('.name', ''),
                                'start_line': start_line,
                                'end_line': end_line,
                                'start_column': start_col,
                                'end_column': end_col,
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
            import traceback
            traceback.print_exc()
            return []

    def parse_extracted_file(self, file_path: str, original_filepath: str) -> Dict:
        """Parse extracted file and extract methods (no header offset needed)"""
        language = self.get_language_for_file(original_filepath)
        
        if not language:
            return {
                'success': False,
                'error': 'Unsupported language',
                'methods': []
            }
        
        if language not in self.parsers:
            return {
                'success': False,
                'error': f'Parser not available for {language}',
                'methods': []
            }
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                code_content = f.read()
            
            # No header to skip, file contains only raw code
            methods = self.extract_methods_from_content(
                code_content, language, original_filepath, 
                line_offset=0  # No offset needed
            )
            
            return {
                'success': True,
                'language': language,
                'methods': methods,
                'method_count': len(methods),
                'file_size': len(code_content),
                'line_count': len(code_content.split('\n'))
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'methods': []
            }

    def process_bug(self, bug_id: str, bug_data: Dict) -> Dict:
        """Process a single bug"""
        print(f"\n{'='*80}")
        print(f"Processing Bug {bug_id}")
        print(f"{'='*80}")
        
        results = {
            'bug_id': bug_id,
            'files': []
        }
        
        for file_data in bug_data['extracted_files']:
            filepath = file_data['filepath']
            print(f"\n  File: {filepath}")
            
            file_result = {
                'filepath': filepath,
                'fixing_commits': [],
                'regressor_commits': []
            }
            
            print(f"    Parsing fixing commits...")
            for commit_data in file_data['fixing_commits']:
                print(f"      Commit {commit_data['commit_hash']}...")
                
                parse_result = self.parse_extracted_file(
                    commit_data['output_file'],
                    filepath
                )
                
                commit_result = {
                    'commit_hash': commit_data['commit_hash'],
                    'full_hash': commit_data['full_hash'],
                    'parent_hash': commit_data.get('parent_hash'),
                    'parse_success': parse_result['success'],
                    'language': parse_result.get('language', 'unknown'),
                    'methods': parse_result['methods'],
                    'method_count': len(parse_result['methods']),
                    'file_info': {
                        'size': parse_result.get('file_size', 0),
                        'lines': parse_result.get('line_count', 0)
                    },
                    'header_lines': parse_result.get('header_lines', 0)
                }
                
                if parse_result['success']:
                    print(f"        ✓ Found {len(parse_result['methods'])} methods")
                    for method in parse_result['methods'][:3]:
                        print(f"          - {method['name']} (lines {method['start_line']}-{method['end_line']})")
                    if len(parse_result['methods']) > 3:
                        print(f"          ... and {len(parse_result['methods']) - 3} more")
                else:
                    print(f"        ✗ Parse failed: {parse_result.get('error', 'Unknown error')}")
                
                file_result['fixing_commits'].append(commit_result)
            
            print(f"    Parsing regressor commits...")
            for commit_data in file_data['regressor_commits']:
                print(f"      Commit {commit_data['commit_hash']} (Bug {commit_data['regressor_bug_id']})...")
                
                parse_result = self.parse_extracted_file(
                    commit_data['output_file'],
                    filepath
                )
                
                commit_result = {
                    'commit_hash': commit_data['commit_hash'],
                    'full_hash': commit_data['full_hash'],
                    'parent_hash': commit_data.get('parent_hash'),
                    'regressor_bug_id': commit_data['regressor_bug_id'],
                    'parse_success': parse_result['success'],
                    'language': parse_result.get('language', 'unknown'),
                    'methods': parse_result['methods'],
                    'method_count': len(parse_result['methods']),
                    'file_info': {
                        'size': parse_result.get('file_size', 0),
                        'lines': parse_result.get('line_count', 0)
                    },
                    'header_lines': parse_result.get('header_lines', 0)
                }
                
                if parse_result['success']:
                    print(f"        ✓ Found {len(parse_result['methods'])} methods")
                    for method in parse_result['methods'][:3]:
                        print(f"          - {method['name']} (lines {method['start_line']}-{method['end_line']})")
                    if len(parse_result['methods']) > 3:
                        print(f"          ... and {len(parse_result['methods']) - 3} more")
                else:
                    print(f"        ✗ Parse failed: {parse_result.get('error', 'Unknown error')}")
                
                file_result['regressor_commits'].append(commit_result)
            
            results['files'].append(file_result)
        
        return results
    
    def process_all_bugs(self) -> Dict:
        """Process all bugs"""
        print("\n" + "="*80)
        print("METHOD EXTRACTION WITH TREE-SITTER")
        print("="*80)
        
        all_results = {
            'extraction_timestamp': datetime.now().isoformat(),
            'step6_results_file': self.step6_results_file,
            'languages_supported': list(self.parsers.keys()),
            'bugs': {}
        }
        
        total_files = 0
        total_methods = 0
        
        for bug_id, bug_data in self.step6_data['bugs'].items():
            bug_result = self.process_bug(bug_id, bug_data)
            all_results['bugs'][bug_id] = bug_result
            
            for file_data in bug_result['files']:
                total_files += 1
                for commit in file_data['fixing_commits'] + file_data['regressor_commits']:
                    total_methods += commit['method_count']
        
        all_results['summary'] = {
            'total_bugs': len(all_results['bugs']),
            'total_files': total_files,
            'total_methods_extracted': total_methods
        }
        
        print(f"\n{'='*80}")
        print("EXTRACTION SUMMARY")
        print(f"{'='*80}")
        print(f"Total bugs processed: {all_results['summary']['total_bugs']}")
        print(f"Total files parsed: {total_files}")
        print(f"Total methods extracted: {total_methods}")
        
        return all_results
    
    def save_results(self, results: Dict) -> str:
        """Save results to JSON"""
        output_file = os.path.join(self.output_dir, f'Step7_method_extraction.json')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2)
        
        print(f"\nResults saved to: {output_file}")
        return output_file
    
    def create_summary_report(self, results: Dict) -> str:
        """Create summary report"""
        output_file = os.path.join(self.output_dir, f'step7_method_summary.txt')
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("METHOD EXTRACTION SUMMARY\n")
            f.write("="*80 + "\n\n")
            f.write(f"Extraction Time: {results['extraction_timestamp']}\n")
            f.write(f"Source: {results['step6_results_file']}\n\n")
            
            f.write(f"Languages Supported: {', '.join(results['languages_supported'])}\n\n")
            
            f.write(f"Total Bugs: {results['summary']['total_bugs']}\n")
            f.write(f"Total Files: {results['summary']['total_files']}\n")
            f.write(f"Total Methods: {results['summary']['total_methods_extracted']}\n\n")
            
            f.write("="*80 + "\n")
            f.write("PER-BUG DETAILS\n")
            f.write("="*80 + "\n\n")
            
            for bug_id, bug_data in results['bugs'].items():
                f.write(f"Bug {bug_id}:\n")
                
                for file_data in bug_data['files']:
                    f.write(f"\n  File: {file_data['filepath']}\n")
                    
                    f.write(f"    Fixing Commits:\n")
                    for commit in file_data['fixing_commits']:
                        f.write(f"      {commit['commit_hash']} ({commit['language']}): {commit['method_count']} methods\n")
                        f.write(f"        Parent: {commit['parent_hash']}\n")
                        for method in commit['methods'][:5]:
                            f.write(f"        - {method['name']} (lines {method['start_line']}-{method['end_line']})\n")
                        if len(commit['methods']) > 5:
                            f.write(f"        ... and {len(commit['methods']) - 5} more\n")
                    
                    f.write(f"    Regressor Commits:\n")
                    for commit in file_data['regressor_commits']:
                        f.write(f"      {commit['commit_hash']} ({commit['language']}): {commit['method_count']} methods\n")
                        f.write(f"        Parent: {commit['parent_hash']}\n")
                        for method in commit['methods'][:5]:
                            f.write(f"        - {method['name']} (lines {method['start_line']}-{method['end_line']})\n")
                        if len(commit['methods']) > 5:
                            f.write(f"        ... and {len(commit['methods']) - 5} more\n")
                
                f.write("\n")
        
        print(f"Summary report saved to: {output_file}")
        return output_file


def main():
    """Main execution function"""
    if not TREE_SITTER_AVAILABLE:
        print("\nERROR: tree-sitter is not installed!")
        print("\nInstall with: pip install tree-sitter==0.21.3")
        return
    
    if not LANGUAGE_MODULES:
        print("\nERROR: No tree-sitter language modules installed!")
        print("\nInstall language modules with:")
        print("  pip install tree-sitter-c")
        print("  pip install tree-sitter-cpp")
        print("  pip install tree-sitter-python")
        print("  pip install tree-sitter-javascript")
        return
    
    print(f"\nAvailable language parsers: {', '.join(LANGUAGE_MODULES.keys())}\n")
    
    step6_results = "step6_full_file_contents/Step6_extraction_results.json"
    
    if not os.path.exists(step6_results):
        print(f"ERROR: Step 6 results file not found: {step6_results}")
        return
    
    extractor = MethodExtractor(
        step6_results_file=step6_results,
        output_dir="step7_method_extraction"
    )
    
    print("\nStarting method extraction...")
    results = extractor.process_all_bugs()
    
    json_file = extractor.save_results(results)
    summary_file = extractor.create_summary_report(results)
    
    print("\n" + "="*80)
    print("METHOD EXTRACTION COMPLETE")
    print("="*80)
    print(f"\nJSON results: {json_file}")
    print(f"Summary report: {summary_file}")
    
    print("\n" + "="*80)
    print("DONE!")
    print("="*80)


if __name__ == "__main__":
    main()