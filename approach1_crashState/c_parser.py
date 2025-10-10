#!/usr/bin/env python3
"""
C Programming Language Parser using Tree-sitter
A boilerplate for parsing C code files and extracting syntax information.
"""
import json
import tree_sitter_c as tsc
from tree_sitter import Language, Parser, Node
import typer
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional


class CParser:
    """A parser for C programming language using tree-sitter."""
    
    def __init__(self):
        """Initialize the C parser."""
        self.language = Language(tsc.language())
        self.parser = Parser(self.language)
    
    def parse_file(self, file_path: str) -> Node:
        """
        Parse a C source file and return the syntax tree.
        
        Args:
            file_path (str): Path to the C source file
            
        Returns:
            Node: Root node of the parsed syntax tree
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                source_code = file.read()
            
            # Parse the source code
            tree = self.parser.parse(bytes(source_code, 'utf8'))
            return tree.root_node
        
        except FileNotFoundError:
            print(f"Error: File '{file_path}' not found.")
            sys.exit(1)
        except Exception as e:
            print(f"Error parsing file: {e}")
            sys.exit(1)
    
    def extract_functions(self, root_node: Node) -> List[Dict[str, Any]]:
        """
        Extract function definitions from the syntax tree.
        
        Args:
            root_node (Node): Root node of the syntax tree
            
        Returns:
            List[Dict]: List of function information dictionaries
        """
        functions = []
        
        def traverse(node: Node):
            if node.type == 'function_definition':
                func_info = self.extract_function_info(node)
                functions.append(func_info)
            
            for child in node.children:
                traverse(child)
        
        traverse(root_node)
        return functions
    
    def extract_function_info(self, func_node: Node) -> Dict[str, Any]:
        """
        Extract detailed information from a function definition node.
        
        Args:
            func_node (Node): Function definition node
            
        Returns:
            Dict: Function information
        """
        func_info = {
            'name': None,
            'return_type': None,
            'parameters': [],
            'start_line': func_node.start_point[0] + 1,
            'end_line': func_node.end_point[0] + 1,
            'start_byte': func_node.start_byte,
            'end_byte': func_node.end_byte,
            

            # just added this part 
            # Add aliases for compatibility with the hg script
            'start': func_node.start_point[0] + 1,
            'end': func_node.end_point[0] + 1



        }
        
        # Extract function declarator
        declarator = None
        for child in func_node.children:
            if child.type == 'function_declarator':
                declarator = child
                break
        
        if declarator:
            # Extract function name
            for child in declarator.children:
                if child.type == 'identifier':
                    func_info['name'] = child.text.decode('utf8')
                    break
            
            # Extract parameters
            param_list = None
            for child in declarator.children:
                if child.type == 'parameter_list':
                    param_list = child
                    break
            
            if param_list:
                func_info['parameters'] = self.extract_parameters(param_list)
        
        # Extract return type (simplified - gets text before function name)
        if func_node.children:
            first_child = func_node.children[0]
            if first_child.type in ['primitive_type', 'type_identifier']:
                func_info['return_type'] = first_child.text.decode('utf8')
        
        return func_info
    
    def extract_parameters(self, param_list_node: Node) -> List[Dict[str, str]]:
        """
        Extract parameter information from parameter list node.
        
        Args:
            param_list_node (Node): Parameter list node
            
        Returns:
            List[Dict]: List of parameter information
        """
        parameters = []
        
        for child in param_list_node.children:
            if child.type == 'parameter_declaration':
                param_info = {'type': None, 'name': None}
                
                for param_child in child.children:
                    if param_child.type in ['primitive_type', 'type_identifier']:
                        param_info['type'] = param_child.text.decode('utf8')
                    elif param_child.type == 'identifier':
                        param_info['name'] = param_child.text.decode('utf8')
                
                parameters.append(param_info)
        
        return parameters
    
    def extract_variables(self, root_node: Node) -> List[Dict[str, Any]]:
        """
        Extract variable declarations from the syntax tree.
        
        Args:
            root_node (Node): Root node of the syntax tree
            
        Returns:
            List[Dict]: List of variable information dictionaries
        """
        variables = []
        
        def traverse(node: Node):
            if node.type == 'declaration':
                var_info = self.extract_variable_info(node)
                if var_info:
                    variables.extend(var_info)
            
            for child in node.children:
                traverse(child)
        
        traverse(root_node)
        return variables
    
    def extract_variable_info(self, decl_node: Node) -> List[Dict[str, Any]]:
        """
        Extract variable information from declaration node.
        
        Args:
            decl_node (Node): Declaration node
            
        Returns:
            List[Dict]: List of variable information
        """
        variables = []
        var_type = None
        
        # Extract type
        for child in decl_node.children:
            if child.type in ['primitive_type', 'type_identifier']:
                var_type = child.text.decode('utf8')
                break
        
        # Extract variable names
        for child in decl_node.children:
            if child.type == 'init_declarator':
                for init_child in child.children:
                    if init_child.type == 'identifier':
                        variables.append({
                            'name': init_child.text.decode('utf8'),
                            'type': var_type,
                            'line': init_child.start_point[0] + 1
                        })
            elif child.type == 'identifier':
                variables.append({
                    'name': child.text.decode('utf8'),
                    'type': var_type,
                    'line': child.start_point[0] + 1
                })
        
        return variables
    
    def print_tree(self, node: Node, depth: int = 0, max_depth: int = 5):
        """
        Print the syntax tree structure.
        
        Args:
            node (Node): Current node
            depth (int): Current depth
            max_depth (int): Maximum depth to print
        """
        if depth > max_depth:
            return
        
        indent = "  " * depth
        node_text = node.text.decode('utf8') if node.text else ""
        
        # Truncate long text for readability
        if len(node_text) > 50:
            node_text = node_text[:47] + "..."
        
        print(f"{indent}{node.type}: {repr(node_text)}")
        
        for child in node.children:
            self.print_tree(child, depth + 1, max_depth)
    
    def get_statistics(self, root_node: Node) -> Dict[str, int]:
        """
        Get statistics about the parsed code.
        
        Args:
            root_node (Node): Root node of the syntax tree
            
        Returns:
            Dict: Statistics dictionary
        """
        stats = {
            'total_nodes': 0,
            'functions': 0,
            'variables': 0,
            'statements': 0,
            'expressions': 0
        }
        
        def count_nodes(node: Node):
            stats['total_nodes'] += 1
            
            if node.type == 'function_definition':
                stats['functions'] += 1
            elif node.type == 'declaration':
                stats['variables'] += 1
            elif 'statement' in node.type:
                stats['statements'] += 1
            elif 'expression' in node.type:
                stats['expressions'] += 1
            
            for child in node.children:
                count_nodes(child)
        
        count_nodes(root_node)
        return stats





app = typer.Typer(
    help="Parse C source files using tree-sitter",
    epilog="""
Examples:
  python c_parser.py input.c                    # Basic parsing
  python c_parser.py input.c --functions        # Extract functions
  python c_parser.py input.c --tree             # Show syntax tree
    """
)


@app.command()
def main(
    file: str = typer.Argument(help="C source file to parse"),
    functions: bool = typer.Option(False, "--functions", "-f", help="Extract and display function definitions"),
    tree: bool = typer.Option(False, "--tree", "-t", help="Display the syntax tree structure"),

    #adding the the file in Json format
    json_output: bool = typer.Option(False, "--json", "-j", help="Output results in JSON format"),
    max_depth: int = typer.Option(5, "--max-depth", "-d", help="Maximum depth of the syntax tree to display"),
):
    """Parse C source files using tree-sitter and extract syntax information."""
    
    # Check if file exists
    if not Path(file).exists():
        typer.echo(f"Error: File '{file}' does not exist.", err=True)
        raise typer.Exit(1)
    
    # Initialize parser
    c_parser = CParser()
    
    typer.echo(f"Parsing C file: {file}")
    typer.echo("=" * 50)
    
    # Parse the file
    root_node = c_parser.parse_file(file)


    # Added this one 
    # ADD THIS SECTION HERE - JSON handling (before regular output)
    """if json_output:
        if functions or not any([tree]):
            function_list = c_parser.extract_functions(root_node)
            print(json.dumps(function_list, indent=2))
        return  # Exit early for JSON output"""
    if json_output:
        function_list = c_parser.extract_functions(root_node)
        print(json.dumps(function_list, indent=2))
        return  # Exit early for JSON output

    
   
    # If no specific options, show basic info
    if not any([functions, tree]):
        functions = True
    
    
    # Extract and display functions
    if functions:
        typer.echo("\n FUNCTION DEFINITIONS")
        typer.echo("-" * 25)
        function_list = c_parser.extract_functions(root_node)
        
        if function_list:
            for func in function_list:
                typer.echo(f"Function: {func['name']}")
                typer.echo(f"  Return type: {func['return_type']}")
                typer.echo(f"  Lines: {func['start_line']}-{func['end_line']}")
                if func['parameters']:
                    typer.echo("  Parameters:")
                    for param in func['parameters']:
                        typer.echo(f"    {param['type']} {param['name']}")
                else:
                    typer.echo("  Parameters: None")
                typer.echo()
        else:
            typer.echo("No functions found.")
    
    # Display syntax tree
    if tree:
        typer.echo(f"\n SYNTAX TREE (max depth: {max_depth})")
        typer.echo("-" * 30)
        c_parser.print_tree(root_node, max_depth=max_depth)
    

if __name__ == "__main__":
    app()
