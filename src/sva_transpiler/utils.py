# utils.py
#

import re
from .constants import OP_MAP, UNARY_MAP

def clean_symbol(s):
    if not s: return ""
    s = str(s)
    s = re.sub(r'^\d+\s+', '', s)
    return s.strip()

def get_name_from_call_or_invocation(node):
    kind = node.get('kind', '')
    name = ""
    if kind == 'Invocation':
        target = node.get('target', {})
        if isinstance(target, dict):
            if target.get('kind') == 'NamedValue':
                name = clean_symbol(target.get('symbol', ''))
            elif 'name' in target:
                name = target['name']
    elif kind == 'Call':
        sub = node.get('subroutine')
        if isinstance(sub, dict):
             name = sub.get('name', '')
        else:
             name = str(sub)
    return name

def extract_type_width(type_str):
    """Estrae la width N. Se fallisce ritorna 0 (scalare/ignoto), non è un errore critico bloccante."""
    if not type_str or not isinstance(type_str, str): return 0
    match = re.search(r'\[(\d+)(?::(\d+))?\]', type_str)
    if match:
        left = int(match.group(1))
        right = int(match.group(2)) if match.group(2) else left
        return abs(left - right) + 1
    return 0 

def extract_past_depth(arg_node):
    """
    Estrae il valore intero (depth) dal secondo argomento di $past.
    STRICT MODE: Solleva ValueError se non riesce a determinare un valore intero valido.
    """
    if not arg_node: 
        return 1
    
    if arg_node.get('kind') in ('Conversion', 'Simple', 'Expression'):
        return extract_past_depth(arg_node.get('operand') or arg_node.get('expr'))
    
    val = arg_node.get('value')
    if val is not None:
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            clean_val = val
            if "'" in val:
                parts = re.split(r"[dbh']", val)
                clean_val = parts[-1] 
            try:
                return int(clean_val)
            except ValueError:
                raise ValueError(f"Invalid integer value for $past depth: '{val}' in node {arg_node}")

    const_val = arg_node.get('constant')
    if const_val:
        match = re.search(r'\d+$', str(const_val))
        if match: return int(match.group(0))

    raise ValueError(f"Unable to extract constant integer depth from $past argument: {arg_node}")

def format_verilog_literal(node):
    """Formatta literal (es. 4'b0111). Fail-safe: ritorna stringa originale se parsing fallisce."""
    val = node.get('value')
    if val is None: return "0"
    
    val_str = str(val)
    type_str = node.get('type', '')
    width = extract_type_width(type_str)
    
    if width > 0 and "'" in val_str:
        parts = val_str.split("'")
        if len(parts) == 2:
            base_char = parts[1][0].lower()
            raw_digits = parts[1][1:].replace("_", "")
            try:
                if base_char == 'b':
                    int_val = int(raw_digits, 2)
                    return f"{width}'b{int_val:0{width}b}"
                elif base_char == 'h':
                    int_val = int(raw_digits, 16)
                    hex_digits = (width + 3) // 4
                    return f"{width}'h{int_val:0{hex_digits}x}"
                elif base_char == 'd':
                    int_val = int(raw_digits)
                    return f"{width}'d{int_val}"
            except:
                pass 
    return val_str

def collect_sampling_needs(node, requests=None):
    if requests is None: requests = {}
    if not isinstance(node, dict): return requests

    kind = node.get('kind', '')

    if kind in ('Invocation', 'Call'):
        name = get_name_from_call_or_invocation(node)
        args = node.get('arguments', [])
        
        if any(x in name for x in ['$past', '$rose', '$fell', '$stable', '$changed']):
            if args:
                arg_expr = args[0].get('expr', args[0])
                expr_key = parse_expression(arg_expr) 
                
                depth = 1
                if '$past' in name:
                    if len(args) >= 2:
                        depth = extract_past_depth(args[1])
                
                type_str = arg_expr.get('type', '')
                width = ""
                if isinstance(type_str, str):
                    match = re.search(r'\[.*?\]', type_str)
                    width = match.group(0) if match else ""
                
                if expr_key not in requests:
                    requests[expr_key] = {'width': width, 'max_depth': depth}
                else:
                    requests[expr_key]['max_depth'] = max(requests[expr_key]['max_depth'], depth)
                    if not requests[expr_key]['width'] and width:
                        requests[expr_key]['width'] = width
                return requests

    for key, val in node.items():
        if isinstance(val, dict):
            collect_sampling_needs(val, requests)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    collect_sampling_needs(item, requests)
    return requests

def get_max_past_depth(node):
    if not isinstance(node, dict): return 0
    kind = node.get('kind', '')
    max_d = 0

    if kind in ('Invocation', 'Call'):
        name = get_name_from_call_or_invocation(node)
        args = node.get('arguments', [])
        if '$past' in name:
            if len(args) >= 2:
                return extract_past_depth(args[1])
            return 1
        elif any(x in name for x in ['$rose', '$fell', '$stable', '$changed']):
            return 1
        for arg in args:
            arg_expr = arg.get('expr', arg)
            max_d = max(max_d, get_max_past_depth(arg_expr))
        return max_d

    if kind in ('BinaryOp', 'Binary'):
        max_d = max(max_d, get_max_past_depth(node.get('left')))
        max_d = max(max_d, get_max_past_depth(node.get('right')))
    elif kind in ('UnaryOp', 'Unary'):
        max_d = max(max_d, get_max_past_depth(node.get('operand')))

    for key, val in node.items():
        if isinstance(val, dict):
            max_d = max(max_d, get_max_past_depth(val))
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    max_d = max(max_d, get_max_past_depth(item))
    return max_d

def parse_expression(expr_node, depth=0, past_substitutions=None):
    if past_substitutions is None: past_substitutions = {}
    if depth > 50: raise ValueError("Recursion limit exceeded")
    if expr_node is None: return "1'b1"
    if not isinstance(expr_node, dict): return str(expr_node)
    
    kind = expr_node.get('kind', '')

    if kind == 'AssertionInstance':
        return parse_expression(expr_node.get('body'), depth + 1, past_substitutions)

    if kind in ('Invocation', 'Call'):
        name = get_name_from_call_or_invocation(expr_node)
        args_node = expr_node.get('arguments', [])
        args = []
        for arg in args_node:
            arg_expr = arg.get('expr', arg)
            args.append(parse_expression(arg_expr, depth + 1, past_substitutions))
        
        arg0 = args[0] if args else "1'b0"

        if past_substitutions:
            reg_base = past_substitutions.get(arg0)
            if reg_base:
                if '$past' in name:
                    past_n = 1
                    if len(args_node) >= 2:
                        past_n = extract_past_depth(args_node[1])
                    return f"{reg_base}[{past_n}]"
                
                reg_d1 = f"{reg_base}[1]"
                if '$rose' in name: return f"({arg0} && !{reg_d1})"
                if '$fell' in name: return f"(!{arg0} && {reg_d1})"
                if '$stable' in name: return f"({arg0} == {reg_d1})"
                if '$changed' in name: return f"({arg0} != {reg_d1})"

        return f"{name}({', '.join(args)})"

    if kind in ('IntegerLiteral', 'UnbasedUnsizedIntegerLiteral'):
        return format_verilog_literal(expr_node)
    
    if kind == 'RealLiteral':
        return str(expr_node.get('value', '0.0'))
    
    if kind == 'NamedValue':
        return clean_symbol(expr_node.get('symbol', ''))

    if kind in ('Simple', 'Conversion', 'Expression', 'SimpleAssertionExpr'):
        inner = expr_node.get('expr') or expr_node.get('operand')
        return parse_expression(inner, depth + 1, past_substitutions)
    
    if kind == 'Parenthesized':
        inner = expr_node.get('expr')
        return f"({parse_expression(inner, depth + 1, past_substitutions)})"
    
    if kind in ('BinaryOp', 'Binary'):
        op_name = expr_node.get('op', '')
        # FIX: Check esplicito operatori mancanti
        if op_name in ('NonOverlappedImplication', 'OverlappedImplication'):
            return parse_expression(expr_node.get('left'), depth + 1, past_substitutions)
        
        if op_name not in OP_MAP:
             raise ValueError(f"Unsupported binary operator: '{op_name}'")

        left = parse_expression(expr_node.get('left'), depth + 1, past_substitutions)
        right = parse_expression(expr_node.get('right'), depth + 1, past_substitutions)
        op_sym = OP_MAP[op_name]
        return f"({left} {op_sym} {right})"
    
    if kind in ('UnaryOp', 'Unary'):
        op_name = expr_node.get('op', '')
        # FIX: Check esplicito operatori mancanti
        if op_name not in UNARY_MAP:
             raise ValueError(f"Unsupported unary operator: '{op_name}'")

        op_sym = UNARY_MAP[op_name]
        operand = parse_expression(expr_node.get('operand'), depth + 1, past_substitutions)
        return f"{op_sym}{operand}"
    
    if kind == 'ElementSelect':
        base = parse_expression(expr_node.get('value'), depth + 1, past_substitutions)
        selector = parse_expression(expr_node.get('selector'), depth + 1, past_substitutions)
        return f"{base}[{selector}]"
    
    if kind == 'RangeSelect':
        base = parse_expression(expr_node.get('value'), depth + 1, past_substitutions)
        left = parse_expression(expr_node.get('left'), depth + 1, past_substitutions)
        right = parse_expression(expr_node.get('right'), depth + 1, past_substitutions)
        return f"{base}[{left}:{right}]"
    
    if kind == 'ConditionalOp':
        conditions = expr_node.get('conditions', [])
        cond = "1'b1" 
        
        if conditions and isinstance(conditions, list) and len(conditions) > 0:
            first_cond = conditions[0]
            pred_node = first_cond.get('expr') if isinstance(first_cond, dict) else first_cond
            cond = parse_expression(pred_node, depth + 1, past_substitutions)
        else:
             cond = parse_expression(expr_node.get('pred'), depth + 1, past_substitutions)
        
        true_val = parse_expression(expr_node.get('left'), depth + 1, past_substitutions)
        false_val = parse_expression(expr_node.get('right'), depth + 1, past_substitutions)
        return f"({cond} ? {true_val} : {false_val})"
    
    if kind == 'Concatenation':
        elements = expr_node.get('operands', [])
        parts = [parse_expression(e, depth + 1, past_substitutions) for e in elements]
        return "{" + ", ".join(parts) + "}"
    
    for field in ('expr', 'operand', 'value', 'body'):
        if field in expr_node and isinstance(expr_node[field], dict):
            try: return parse_expression(expr_node[field], depth + 1, past_substitutions)
            except ValueError: pass 
    
    raise ValueError(f"Unknown AST node kind: '{kind}' in expression: {expr_node}")