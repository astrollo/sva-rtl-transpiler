# linearizer.py

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import copy
from .utils import parse_expression

@dataclass
class SequenceStep:
    expr: str = "1'b1"
    delay: int = 0
    tap_range: Optional[Tuple[int, int]] = None
    # --- Estensioni per GOTO [->N:M] ---
    is_goto: bool = False
    contributes_to_pass: bool = False
    flush_goto: bool = False
    # --- Estensioni per NON-CONSECUTIVE [=N:M] ---
    is_non_consecutive_tail: bool = False
    # --- Metadati per GOTO ottimizzato ---
    goto_index: int = -1
    goto_total: int = -1
    goto_min: int = -1
    # --- Estensioni per VARIABILI LOCALI ---
    assignments: Dict[str, str] = field(default_factory=dict)
    # --- Estensione THROUGHOUT ---
    throughout_cond: Optional[str] = None
    is_first_match: bool = False
    consec_range: Optional[Tuple[int, int]] = None

BOOLEAN_KINDS = {
    'BinaryOp', 'UnaryOp', 'NamedValue', 'IntegerLiteral',
    'UnbasedUnsizedIntegerLiteral', 'RealLiteral', 'ElementSelect',
    'RangeSelect', 'Invocation', 'Call', 
    'ConditionalOp', 'Concatenation', 
    'Expression', 'Conversion', 'MemberAccess'
}

def linearize_sequence(node, context: str = 'lhs', past_substitutions=None) -> List[SequenceStep]:
    if past_substitutions is None: past_substitutions = {}
    if not node: return []
    try:
        raw_steps = _linearize_recursive(node, context, past_substitutions)
    except ValueError as e:
        raise e
    
    final_steps = []
    for s in raw_steps:
        if s.tap_range:
            min_t, max_t = s.tap_range
            if min_t < 0 or min_t > max_t:
                raise ValueError(f"Invalid tap_range {s.tap_range}")
            if s.delay != max_t: s.delay = max_t

        # --- FIX: SPLIT DELAY FROM GOTO ---
        if (s.is_goto or s.is_non_consecutive_tail) and (s.delay > 0 or s.tap_range):
            filler = SequenceStep(
                expr="1'b1", 
                delay=s.delay, 
                tap_range=s.tap_range,
                throughout_cond=s.throughout_cond # Qui propaghiamo perché è interno alla logica GOTO
            )
            final_steps.append(filler)
            
            s.delay = 0
            s.tap_range = None
            final_steps.append(s)
        else:
            final_steps.append(s)
           
    # --- GUARDRAIL INTELLIGENTE: Ordine Variabili Locali vs Goto ---
    has_active_local_vars = False

    for step in final_steps:
        # 1. Se lo step assegna una variabile, alziamo il flag
        if getattr(step, 'assignments', {}):
            has_active_local_vars = True
        
        # 2. Se incontriamo un Goto/NC, controlliamo se ci sono variabili "in volo"
        if getattr(step, 'is_goto', False) or getattr(step, 'is_non_consecutive_tail', False):
            if has_active_local_vars:
                raise ValueError(
                    "Unsupported SVA feature: A local variable cannot be assigned BEFORE or DURING "
                    "a Goto [->N:M] or Non-Consecutive [=N:M] repetition. "
                    "Assigning variables AFTER the Goto is permitted."
                )


    # --- Prevenzione Time-Collapse su ##0 ---
    # Esaminiamo coppie di step adiacenti. Se uno step può collassare a zero (empty match)
    # e il successivo ha un ritardo di innesco nullo (##0), il tempo logico andrebbe 
    # a ritroso (overlap negativo). Questo blocco impone la Strict SVA Semantics.
    for i in range(len(final_steps) - 1):
        curr_step = final_steps[i]
        next_step = final_steps[i+1]
        
        # 1. Valuta se il token corrente ha il permesso matematico di non esistere
        curr_can_be_empty = (curr_step.consec_range is not None and curr_step.consec_range[0] == 0)
        
        # 2. Estrae il vero ritardo di innesco del token successivo
        next_min_delay = next_step.tap_range[0] if next_step.tap_range else next_step.delay
        
        # 3. Intercetta la condizione degenere
        if curr_can_be_empty and next_min_delay == 0:
            raise ValueError(
                "SVA Semantic Error: Illegal sequence overlap. "
                "A repetition that can resolve to zero matches (e.g., [*0:M]) "
                "cannot be followed by a zero-delay operator (##0). "
                "This generates a negative time paradox. Please restructure the property or use ##1."
            )

    return final_steps 

def _linearize_recursive(node, context: str, past_substitutions) -> List[SequenceStep]:
    if not node: return []
    if not isinstance(node, dict): return [SequenceStep(expr=str(node))]
    
    kind = node.get('kind', '')

    # 0. WRAPPER
    if kind in ('Simple', 'Parenthesized', 'SimpleAssertionExpr'):
        if 'repetition' not in node:
            inner = node.get('expr') or node.get('operand')
            return _linearize_recursive(inner, context, past_substitutions)

    # 1. WRAPPER LOGICI
    if kind in ('Clocking', 'DisableIff'):
        child = node.get('expr') or node.get('body')
        if child: return _linearize_recursive(child, context, past_substitutions)
        return []
    if kind == 'AssertionInstance':
        body = node.get('body')
        if body: return _linearize_recursive(body, context, past_substitutions)
        return []

    # 1.b FIRST_MATCH (Slang)
    if kind == 'FirstMatch':
        seq_node = node.get('seq')
        child_steps = _linearize_recursive(seq_node, context, past_substitutions)
        if not child_steps:
            return []

        # Guardrail 0: vieta goto/nonconsecutive dentro first_match
        for s in child_steps:
            if getattr(s, 'is_goto', False) or getattr(s, 'is_non_consecutive_tail', False):
                raise ValueError(
                    "Unsupported SVA feature: first_match() cannot be applied to Goto [->N] "
                    "or Non-Consecutive [=N] repetitions."
                )

        # Guardrail 1: first_match supportato SOLO su delay dinamico ##[N:M]
        range_idxs = [
            i for i, s in enumerate(child_steps)
            if s.tap_range and (s.tap_range[0] != s.tap_range[1])
        ]
        if not range_idxs:
            raise ValueError(
                "Unsupported SVA feature: first_match() is supported only on standard dynamic delays ##[N:M]."
            )
        if len(range_idxs) != 1:
            raise ValueError(
                "Unsupported SVA feature: first_match() with multiple dynamic delays is not supported."
            )

        idx = range_idxs[0]

        # Guardrail 2: il ##[N:M] deve essere l’ULTIMO step della sequenza dentro first_match
        if idx != (len(child_steps) - 1):
            raise ValueError(
                "Unsupported SVA feature: in this transpiler, first_match() only supports "
                "##[N:M] at the very end of the sequence."
            )

        child_steps[idx].is_first_match = True
        return child_steps


    # 2. SEQUENCE WITH MATCH
    if kind == 'SequenceWithMatch':
        inner_seq = node.get('expr')
        rep = node.get('repetition', {})
        match_items = node.get('matchItems', [])

        extracted_assignments = {}
        if match_items:
            for item in match_items:
                if item.get('kind') == 'Assignment':
                    left_sym = item.get('left', {}).get('symbol', '')
                    var_name = left_sym.split()[-1] if left_sym else ''
                    right_expr = parse_expression(item.get('right'), past_substitutions=past_substitutions)
                    if var_name: extracted_assignments[var_name] = right_expr

        steps = []
        if rep.get('kind') == 'Consecutive':
            rep_min = int(rep.get('min', 1))
            rep_max = int(rep.get('max', rep_min))
            if rep_min != rep_max: raise ValueError("Nested Range Repetition is not supported.")
            if rep_min == 0: return []
            base_steps = _linearize_recursive(inner_seq, context, past_substitutions)
            if not base_steps: return []
            for i in range(rep_min):
                current = copy.deepcopy(base_steps)
                if i > 0: current[0].delay += 1
                steps.extend(current)
        
        elif rep.get('kind') in ('Goto', 'GoTo', 'NonConsecutive', 'Nonconsecutive'):
             if match_items: raise ValueError("Local Variable assignments inside Goto/NonConsecutive repetition are NOT supported.")
             fake_node = copy.deepcopy(node)
             fake_node['kind'] = 'GotoRepetition'
             if rep.get('kind') in ('NonConsecutive', 'Nonconsecutive'): 
                 fake_node['kind'] = 'NonConsecutiveRepetition'
             fake_node['repetition'] = rep
             fake_node['operand'] = inner_seq
             return _linearize_recursive(fake_node, context, past_substitutions)
        else:
            steps = _linearize_recursive(inner_seq, context, past_substitutions)

        if extracted_assignments and steps:
            steps[-1].assignments.update(extracted_assignments)
        return steps

    # 3. RIPETIZIONI
    if kind in ('ConsecutiveRepetition', 'SequenceRepetition', 'GotoRepetition', 'NonConsecutiveRepetition') or ('repetition' in node):
        if 'repetition' in node:
            rep_node = node['repetition']
            child_node = {k: v for k, v in node.items() if k != 'repetition'}
        else:
            rep_node = node
            child_node = node.get('operand') or node.get('expr')
        rep_kind = rep_node.get('kind', 'Consecutive')
        
        if rep_kind in ('Goto', 'GoTo', 'NonConsecutive', 'Nonconsecutive'):
            def is_boolean_safe(n):
                if not isinstance(n, dict): return False 
                k = n.get('kind','')
                if k in ('Simple', 'Parenthesized'): return is_boolean_safe(n.get('expr') or n.get('operand'))
                return k in BOOLEAN_KINDS
            
            is_nc = rep_kind in ('NonConsecutive', 'Nonconsecutive')
            if not is_boolean_safe(child_node):
                raise ValueError(f"Goto/NonConsecutive Repetition is supported ONLY for simple Boolean expressions.")

            rep_min = int(rep_node.get('min', 1))
            rep_max = int(rep_node.get('max', rep_min))
            base_expr = parse_expression(child_node, past_substitutions=past_substitutions)
            unrolled_steps = []
            
            for i in range(1, rep_max + 1):
                contrib = (i >= rep_min)
                flush = (i == rep_max) and not is_nc
                step = SequenceStep(expr=base_expr, delay=0, is_goto=True, contributes_to_pass=contrib, flush_goto=flush)
                step.goto_index = i - 1
                step.goto_total = rep_max
                step.goto_min = rep_min  # <--- NUOVO METADATO
                unrolled_steps.append(step)
            
            if is_nc:
                tail_step = SequenceStep(expr=base_expr, delay=0, is_non_consecutive_tail=True)
                # <--- ANCORAGGIO DEL TAIL ALLA MACRO-STRUTTURA
                tail_step.goto_index = rep_max 
                tail_step.goto_total = rep_max
                tail_step.goto_min = rep_min
                unrolled_steps.append(tail_step)
            return unrolled_steps


        if rep_kind == 'Consecutive':
            rep_min = int(rep_node.get('min', 1))
            rep_max = int(rep_node.get('max', rep_min))
            
            if rep_min == 0 and rep_max == 0: 
                return []

            def is_boolean_safe_range(n):
                if not isinstance(n, dict): return False 
                k = n.get('kind','')
                if k in ('Simple', 'Parenthesized'): return is_boolean_safe_range(n.get('expr') or n.get('operand'))
                return k in BOOLEAN_KINDS

            # Se è un booleano, niente srotolamento, sia per l'antecedente che per il conseguente.
            if is_boolean_safe_range(child_node):
                base_expr = parse_expression(child_node, past_substitutions=past_substitutions)
                return [SequenceStep(expr=base_expr, delay=0, consec_range=(rep_min, rep_max))]
                
            # --- SEQUENZE COMPLESSE ---
            else:
                if rep_min != rep_max:
                    raise ValueError(f"Unsupported SVA feature: Consecutive range [*N:M] on complex sequences is not supported.")
                
                # Srotolamento mantenuto SOLO per sequenze complesse (es. con variabili locali)
                base_steps = _linearize_recursive(child_node, context, past_substitutions)
                if not base_steps: return []
                
                unrolled = []
                for i in range(rep_min):
                    current = copy.deepcopy(base_steps)
                    if i > 0: current[0].delay += 1
                    unrolled.extend(current)
                return unrolled


    # 4. WRAPPER PROPRIETA'
    if kind == 'ConcurrentAssertion':
        prop = node.get('propertySpec', {})
        if 'expr' in prop: return _linearize_recursive(prop['expr'], context, past_substitutions)
        elif prop: return _linearize_recursive(prop, context, past_substitutions)
        return []

    # 5. CONCATENAZIONE
    if kind == 'SequenceConcat' and 'elements' in node:
        steps = []
        for elem in node['elements']:
            min_d = int(elem.get('min', 0))
            max_d = int(elem.get('max', min_d))
            child = elem.get('sequence')
            child_steps = _linearize_recursive(child, context, past_substitutions)
            
            if child_steps:
                # --- FIX: THROUGHOUT ISOLATION ---
                # Se lo step successivo ha una condizione throughout e dobbiamo applicare
                # un delay esterno, NON fondiamo il delay nello step (perché erediterebbe
                # impropriamente la condizione). Creiamo uno step di delay puro.
                if max_d > 0 and child_steps[0].throughout_cond is not None:
                    filler = SequenceStep(expr="1'b1", delay=max_d)
                    if min_d != max_d:
                        filler.tap_range = (min_d, max_d)
                        filler.delay = max_d
                    steps.append(filler)
                    steps.extend(child_steps)
                else:
                    first = child_steps[0]
                    if max_d > 0: _apply_delay(first, min_d, max_d)
                    steps.extend(child_steps)
        return steps
    
    # 6. DELAY SEQUENCE
    if kind in ('TimedSequence', 'DelaySequence') or 'delay' in node:
        max_d = int(node.get('max', node.get('delay', 0)))
        min_d = int(node.get('min', max_d))
        child = node.get('operand') or node.get('child') or node.get('expr')
        child_steps = _linearize_recursive(child, context, past_substitutions)
        
        if child_steps and max_d > 0:
            # --- FIX: THROUGHOUT ISOLATION ---
            if child_steps[0].throughout_cond is not None:
                filler = SequenceStep(expr="1'b1", delay=max_d)
                if min_d != max_d:
                    filler.tap_range = (min_d, max_d)
                    filler.delay = max_d
                # Prepend the filler
                child_steps.insert(0, filler)
            else:
                _apply_delay(child_steps[0], min_d, max_d)
                
        return child_steps

    # 7. OPERATORI SPECIALI
    if kind in ('GotoRepetition', 'GoToRepetition'):
        fake_node = {'kind': 'SequenceRepetition', 'repetition': {'kind': 'Goto', 'min': node.get('min',1), 'max': node.get('max',1)}, 'operand': node.get('operand')}
        if 'repetition' in node: fake_node['repetition'] = node['repetition']
        fake_node['repetition']['kind'] = 'Goto'
        return _linearize_recursive(fake_node, context, past_substitutions)

    if kind in ('NonConsecutiveRepetition', 'NonconsecutiveRepetition'):
        fake_node = {'kind': 'SequenceRepetition', 'repetition': {'kind': 'NonConsecutive', 'min': node.get('min',1), 'max': node.get('max',1)}, 'operand': node.get('operand')}
        if 'repetition' in node: fake_node['repetition'] = node['repetition']
        fake_node['repetition']['kind'] = 'NonConsecutive'
        return _linearize_recursive(fake_node, context, past_substitutions)

    if kind == 'Binary':
        op = node.get('op', '')
        if op == 'Throughout':
            cond_node = node.get('left')
            cond_expr = parse_expression(cond_node, past_substitutions=past_substitutions)
            seq_node = node.get('right')
            
            seq_steps = _linearize_recursive(seq_node, context, past_substitutions)
            if not seq_steps: return []
            
            # --- FIX THROUGHOUT SEMANTICS ---
            # Applichiamo la condizione throughout ricorsivamente agli step
            for step in seq_steps:
                if step.throughout_cond:
                    step.throughout_cond = f"({step.throughout_cond}) && ({cond_expr})"
                else:
                    step.throughout_cond = f"({cond_expr})"
            return seq_steps
            
        raise ValueError(f"Unsupported sequence binary operator: '{op}'")

    if kind in BOOLEAN_KINDS:
        return [SequenceStep(expr=parse_expression(node, past_substitutions=past_substitutions))]
    else:
        raise ValueError(f"Unknown or unsupported AST node kind: '{kind}'. Context: {context}")

def _apply_delay(step, min_d, max_d):
    if step.tap_range:
        om, ox = step.tap_range
        step.tap_range = (om + min_d, ox + max_d)
        step.delay = ox + max_d
    elif min_d != max_d:
        step.tap_range = (step.delay + min_d, step.delay + max_d)
        step.delay += max_d
    else:
        step.delay += max_d