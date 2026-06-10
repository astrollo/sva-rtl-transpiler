import sys
import copy
import re
from typing import List, Optional, Dict
from .linearizer import linearize_sequence
from .utils import parse_expression, collect_sampling_needs
from .pipeline_engine import generate_pipeline_stage
from .lookback_engine import generate_lookback_checker

_TEMPORAL_FUNCS = ('$past', '$rose', '$fell', '$stable', '$changed')


def _iter_step_expression_texts(step):
    """
    Restituisce tutte le espressioni dello step che possono contenere
    riferimenti agli shadow register delle funzioni temporali SVA.
    """
    expr = getattr(step, 'expr', None)
    if expr:
        yield str(expr)

    throughout_cond = getattr(step, 'throughout_cond', None)
    if throughout_cond:
        yield str(throughout_cond)

    assignments = getattr(step, 'assignments', {}) or {}
    for assign_expr in assignments.values():
        if assign_expr:
            yield str(assign_expr)


def _max_sampled_history_depth(assertion_id, step):
    """
    Cerca riferimenti del tipo _a<ID>_past_<N>[<DEPTH>] prodotti da
    parse_expression(..., past_substitutions=...).
    """
    pattern = re.compile(rf"\b_a{re.escape(str(assertion_id))}_past_\d+\[(\d+)\]")
    max_depth = 0

    for text in _iter_step_expression_texts(step):
        for match in pattern.finditer(text):
            max_depth = max(max_depth, int(match.group(1)))

    return max_depth


def _check_no_raw_temporal_calls(steps):
    """
    Guardrail: dopo la linearizzazione non devono restare chiamate raw a
    $past/$rose/$fell/$stable/$changed. Se restano, la sostituzione non è
    avvenuta e il warmup non sarebbe calcolabile in modo affidabile.
    """
    for step in steps:
        for text in _iter_step_expression_texts(step):
            if any(func in text for func in _TEMPORAL_FUNCS):
                raise ValueError(
                    "Internal error: unresolved SVA temporal function after linearization. "
                    f"Expression: {text}"
                )


def _step_min_delay(step):
    tap_range = getattr(step, 'tap_range', None)
    if tap_range:
        return int(tap_range[0])
    return int(getattr(step, 'delay', 0) or 0)


def _sequence_min_completion_offset(steps, base_offset=0):
    """
    Calcola il primo ciclo in cui la sequenza può completare, usando solo
    i minimi temporali. Serve per posizionare il RHS nel caso |-> / |=>.
    """
    current_offset = int(base_offset)
    idx = 0

    while idx < len(steps):
        step = steps[idx]
        min_delay = _step_min_delay(step)
        eval_offset = current_offset + min_delay

        if getattr(step, 'is_goto', False):
            group_min_hits = int(getattr(step, 'goto_min', 1) or 1)

            while idx < len(steps) and getattr(steps[idx], 'is_goto', False):
                group_min_hits = int(getattr(steps[idx], 'goto_min', group_min_hits) or group_min_hits)
                idx += 1

            if idx < len(steps) and getattr(steps[idx], 'is_non_consecutive_tail', False):
                group_min_hits = int(getattr(steps[idx], 'goto_min', group_min_hits) or group_min_hits)
                idx += 1

            current_offset = eval_offset + max(0, group_min_hits - 1)
            continue

        if getattr(step, 'is_non_consecutive_tail', False):
            current_offset = eval_offset
            idx += 1
            continue

        consec_range = getattr(step, 'consec_range', None)
        if consec_range:
            min_rep = int(consec_range[0])
            if min_rep == 0:
                current_offset = current_offset + max(0, min_delay - 1)
            else:
                current_offset = eval_offset + (min_rep - 1)
        else:
            current_offset = eval_offset

        idx += 1

    return current_offset


def _sequence_temporal_warmup_deficit(assertion_id, steps, base_offset=0):
    """
    Calcola il warmup minimo richiesto dagli operatori temporali contenuti
    in una sequenza già linearizzata:

        deficit = history_depth - earliest_eval_offset

    Il risultato è clampato implicitamente a zero dal max iniziale.
    """
    _check_no_raw_temporal_calls(steps)

    max_deficit = 0
    current_offset = int(base_offset)
    idx = 0

    def account_step(step, eval_offset):
        nonlocal max_deficit
        history_depth = _max_sampled_history_depth(assertion_id, step)
        if history_depth > 0:
            max_deficit = max(max_deficit, history_depth - eval_offset)

    while idx < len(steps):
        step = steps[idx]
        min_delay = _step_min_delay(step)
        eval_offset = current_offset + min_delay

        if getattr(step, 'is_goto', False):
            group_min_hits = int(getattr(step, 'goto_min', 1) or 1)

            while idx < len(steps) and getattr(steps[idx], 'is_goto', False):
                account_step(steps[idx], eval_offset)
                group_min_hits = int(getattr(steps[idx], 'goto_min', group_min_hits) or group_min_hits)
                idx += 1

            if idx < len(steps) and getattr(steps[idx], 'is_non_consecutive_tail', False):
                account_step(steps[idx], eval_offset)
                group_min_hits = int(getattr(steps[idx], 'goto_min', group_min_hits) or group_min_hits)
                idx += 1

            current_offset = eval_offset + max(0, group_min_hits - 1)
            continue

        if getattr(step, 'is_non_consecutive_tail', False):
            account_step(step, eval_offset)
            current_offset = eval_offset
            idx += 1
            continue

        account_step(step, eval_offset)

        consec_range = getattr(step, 'consec_range', None)
        if consec_range:
            min_rep = int(consec_range[0])
            if min_rep == 0:
                current_offset = current_offset + max(0, min_delay - 1)
            else:
                current_offset = eval_offset + (min_rep - 1)
        else:
            current_offset = eval_offset

        idx += 1

    return max(0, max_deficit)


def _compute_temporal_warmup_target(assertion_id, lhs_pipelines_steps, rhs_pipelines_steps, is_overlapped):
    """
    Warmup globale della property. Gli shadow register restano dimensionati
    alla profondità massima richiesta; questo valore decide solo quanti cicli
    bloccare l'avvio della property.
    """
    warmup_target = 0
    lhs_completion_offsets = []

    for lhs_steps in lhs_pipelines_steps:
        warmup_target = max(
            warmup_target,
            _sequence_temporal_warmup_deficit(assertion_id, lhs_steps, base_offset=0)
        )
        lhs_completion_offsets.append(_sequence_min_completion_offset(lhs_steps, base_offset=0))

    lhs_min_completion = min(lhs_completion_offsets) if lhs_completion_offsets else 0
    implication_delay = 0 if is_overlapped else 1
    rhs_base_offset = lhs_min_completion + implication_delay

    for rhs_steps in rhs_pipelines_steps:
        warmup_target = max(
            warmup_target,
            _sequence_temporal_warmup_deficit(assertion_id, rhs_steps, base_offset=rhs_base_offset)
        )

    return max(0, warmup_target)

def _generate_goto_head(assertion_id, start_signal, goto_steps, clk, rst, decl_lines, logic_lines, watchdog_cfg):
    """
    Genera un blocco hardware Forward per gestire un Goto [->n] in testa al conseguente.
    Supporta pienamente l'operatore 'throughout'.
    """
    if not goto_steps:
        return start_signal, None

    goto_depth = len(goto_steps)
    target_expr = goto_steps[0].expr
    sig_base = f"_a{assertion_id}_rhs_goto"
    
    # 1. ESTRAZIONE CONDIZIONE THROUGHOUT
    t_cond = getattr(goto_steps[0], 'throughout_cond', None)

    # =========================================================================
    # RAMO A: NESSUN WATCHDOG (Logica a Contatore Unario / Shift)
    # =========================================================================
    if str(watchdog_cfg).lower() == 'none':
        done_wire = f"{sig_base}_done"
        target_wire = f"{sig_base}_target"
        fail_wire = f"{sig_base}_fail"

        decl_lines.append(f"    // --- RHS Goto Head Logic ([->{goto_depth}] - NO WATCHDOG) ---")
        decl_lines.append(f"    logic {target_wire};")
        decl_lines.append(f"    logic {done_wire};")
        decl_lines.append(f"    logic {fail_wire};")
        
        q_regs = []
        for i in range(goto_depth):
            q_name = f"{sig_base}_s{i}_q"
            d_name = f"{sig_base}_s{i}_d"
            decl_lines.append(f"    logic {q_name} = 1'b0;")
            decl_lines.append(f"    logic {d_name};")
            q_regs.append(q_name)

        logic_lines.append(f"    assign {target_wire} = {target_expr};")
        
        for i in range(goto_depth):
            q_name = f"{sig_base}_s{i}_q"
            d_name = f"{sig_base}_s{i}_d"
            
            if i == 0:
                wait_state = f"{q_name} || {start_signal}"
                d_expr = f"({target_wire}) ? 1'b0 : ({wait_state})"
            else:
                prev_q = f"{sig_base}_s{i-1}_q"
                prev_wait = f"{prev_q} || {start_signal}" if i == 1 else f"{prev_q}"
                d_expr = f"({target_wire}) ? ({prev_wait}) : {q_name}"
            
            # KILL SWITCH: Se cade il throughout, azzera gli ingressi
            if t_cond:
                d_expr = f"({t_cond}) ? ({d_expr}) : 1'b0"
                
            logic_lines.append(f"    assign {d_name} = {d_expr};")

        rst_check = f"if ({rst}) begin" if rst else "if (1'b0) begin"
        logic_lines.append(f"    always_ff @({clk}) begin")
        logic_lines.append(f"        {rst_check}")
        for q_name in q_regs: logic_lines.append(f"            {q_name} <= 1'b0;")
        logic_lines.append(f"        end else begin")
        for i in range(goto_depth): logic_lines.append(f"            {sig_base}_s{i}_q <= {sig_base}_s{i}_d;")
        logic_lines.append(f"        end")
        logic_lines.append(f"    end")
        
        # LOGICA PASS
        triggers = []
        if goto_depth == 1: triggers.append(start_signal)
        if goto_depth > 0: triggers.append(f"{sig_base}_s{goto_depth-1}_q")

        merged_q = " || ".join(triggers) if triggers else "1'b0"
        pass_expr = f"({merged_q}) && ({target_wire})"
        
        # MASK SUL PASS
        if t_cond:
            logic_lines.append(f"    assign {done_wire} = ({t_cond}) ? ({pass_expr}) : 1'b0;")
            # LOGICA FAIL: Il throughout cade mentre c'è un thread in volo
            any_q = " || ".join(q_regs) if q_regs else "1'b0"
            fail_expr = f"!({t_cond}) && (({any_q}) || ({start_signal}))"
            logic_lines.append(f"    assign {fail_wire} = {fail_expr};")
            return done_wire, fail_wire
        else:
            logic_lines.append(f"    assign {done_wire} = {pass_expr};")
            logic_lines.append(f"    assign {fail_wire} = 1'b0;")
            return done_wire, None


    # =========================================================================
    # RAMO B: CON WATCHDOG (Delay-Line Match Filter)
    # =========================================================================
    try:
        timeout_cycles = int(watchdog_cfg)
    except (ValueError, TypeError):
        raise ValueError(
            f"Configurazione watchdog non valida: '{watchdog_cfg}'. "
            f"Per disabilitarlo usa 'none', altrimenti fornisci un numero intero."
        )
    
    if timeout_cycles < 2:
        raise ValueError(
            f"Lunghezza watchdog non supportata: {timeout_cycles}. "
            f"L'architettura richiede un watchdog di almeno 2 cicli."
        )

    v_width = timeout_cycles - 1
    
    decl_lines.append(f"    // --- RHS Goto Delay-Line Filter (Depth: {goto_depth}, Watchdog: {timeout_cycles}) ---")
    if t_cond:
        decl_lines.append(f"    // Throughout Condition applied: {t_cond}")
    
    all_fails = []
    for i in range(goto_depth):
        decl_lines.append(f"    logic [{v_width-1}:0] {sig_base}_s{i}_q = '0;")
        decl_lines.append(f"    logic [{v_width-1}:0] {sig_base}_s{i}_d;")
        decl_lines.append(f"    logic {sig_base}_s{i}_pass;")
        decl_lines.append(f"    logic {sig_base}_s{i}_fail;")
        if i < goto_depth - 1:
             decl_lines.append(f"    logic {sig_base}_s{i}_pass_reg = 1'b0;")
        all_fails.append(f"{sig_base}_s{i}_fail")

    for i in range(goto_depth):
        q_vec = f"{sig_base}_s{i}_q"
        d_vec = f"{sig_base}_s{i}_d"
        p_sig = f"{sig_base}_s{i}_pass"
        f_sig = f"{sig_base}_s{i}_fail"
        
        in_token = start_signal if i == 0 else f"{sig_base}_s{i-1}_pass_reg"
        
        # INGRESSI E SHIFT (con Kill Switch)
        d_0_expr = f"({in_token}) && !({target_expr})"
        if t_cond:
            logic_lines.append(f"    assign {d_vec}[0] = ({t_cond}) ? ({d_0_expr}) : 1'b0;")
        else:
            logic_lines.append(f"    assign {d_vec}[0] = {d_0_expr};")
            
        if v_width > 1:
            d_shift_expr = f"{q_vec}[{v_width-2}:0] & {{({v_width-1}){{!({target_expr})}}}}"
            if t_cond:
                logic_lines.append(f"    assign {d_vec}[{v_width-1}:1] = ({t_cond}) ? ({d_shift_expr}) : '0;")
            else:
                logic_lines.append(f"    assign {d_vec}[{v_width-1}:1] = {d_shift_expr};")
        
        # LOGICA PASS (con maschera)
        pass_expr = f"(|({q_vec} & {{({v_width}){{({target_expr})}}}} )) || (({in_token}) && ({target_expr}))"
        if t_cond:
            logic_lines.append(f"    assign {p_sig} = ({t_cond}) ? ({pass_expr}) : 1'b0;")
        else:
            logic_lines.append(f"    assign {p_sig} = {pass_expr};")
            
        # LOGICA FAIL (Overflow Watchdog + Throughout Drop)
        wd_fail_expr = f"{q_vec}[{v_width-1}] && !({target_expr})"
        if t_cond:
            token_attivi = f"(|{q_vec}) || ({in_token})"
            logic_lines.append(f"    assign {f_sig} = ({wd_fail_expr}) || (!({t_cond}) && ({token_attivi}));")
        else:
            logic_lines.append(f"    assign {f_sig} = {wd_fail_expr};")

    rst_check = f"if ({rst}) begin" if rst else "if (1'b0) begin"
    logic_lines.append(f"    always_ff @({clk}) begin")
    logic_lines.append(f"        {rst_check}")
    for i in range(goto_depth):
        logic_lines.append(f"            {sig_base}_s{i}_q <= '0;")
        if i < goto_depth - 1: logic_lines.append(f"            {sig_base}_s{i}_pass_reg <= 1'b0;")
    logic_lines.append(f"        end else begin")
    for i in range(goto_depth):
        logic_lines.append(f"            {sig_base}_s{i}_q <= {sig_base}_s{i}_d;")
        if i < goto_depth - 1: logic_lines.append(f"            {sig_base}_s{i}_pass_reg <= {sig_base}_s{i}_pass;")
    logic_lines.append(f"        end")
    logic_lines.append(f"    end")

    done_wire = f"{sig_base}_s{goto_depth-1}_pass"
    merged_fail = f"{sig_base}_merged_fail"
    logic_lines.append(f"    logic {merged_fail};")
    logic_lines.append(f"    assign {merged_fail} = {' || '.join(all_fails)};")
    
    return done_wire, merged_fail




def generate_verilog_code(assertion_id, clk, rst, node, label,
                          ifdef_mode='disable', 
                          keyword='assert', 
                          assert_action='none', 
                          cover_action='none',
                          local_vars_def=None,
                          rhs_goto_watchdog="128",
                          design_signals_map=None,
                          is_checker_mode=False,
                          generate_pass=False):
    
    active_rst = rst

    def mask_with_active_disable(expr: str) -> str:
        """
        Applica la semantica di disable iff ai segnali finali esposti in
        modalità hardware checker.

        active_rst rappresenta direttamente la condizione di disable.
        Quindi il segnale checker deve essere visibile solo quando tale
        condizione è falsa.
        """
        if active_rst:
            return f"!({active_rst}) && ({expr})"
        return expr

    if local_vars_def is None:
        local_vars_def = {}

    expr_node = node
    
    if expr_node is None: return f"// ERROR generating {label}: Core node is null"
    
    lhs_node = expr_node 
    rhs_node = None
    is_overlapped = False
    rhs_is_negated = False
    
    kind = expr_node.get('kind', '') if isinstance(expr_node, dict) else ''
    op = expr_node.get('op', '') if isinstance(expr_node, dict) else ''
    
    if kind == 'Binary' and 'Implication' in op:
        lhs_node = expr_node.get('left')
        rhs_node = expr_node.get('right')
        if op == 'OverlappedImplication': is_overlapped = True
        elif op == 'NonOverlappedImplication': is_overlapped = False
        else: return f"// ERROR generating {label}: Unknown implication operator '{op}'"
    elif keyword in ('assert', 'assume', 'cover'):
        rhs_node = lhs_node
        lhs_node = {'kind': 'IntegerLiteral', 'value': 1, 'type': {'width': 1}}
        is_overlapped = True

    if rhs_node:
        if isinstance(rhs_node, dict):
            r_kind = rhs_node.get('kind', '')
            r_op = rhs_node.get('op', '')
            if r_kind in ('Unary', 'UnaryOp') and r_op in ('LogicalNot', 'Not', '!'):
                rhs_is_negated = True
                rhs_node = rhs_node.get('operand') or rhs_node.get('expr')

    lhs_nodes_to_process = []
    def collect_lhs_branches(n):
        if not isinstance(n, dict):
            lhs_nodes_to_process.append(n)
            return
        k = n.get('kind', '')
        op = n.get('op', '') 
        if k in ('SequenceOr', 'OrSequence') or (k == 'Binary' and op == 'Or'):
            if 'left' in n and 'right' in n:
                collect_lhs_branches(n['left'])
                collect_lhs_branches(n['right'])
            elif 'elements' in n:
                for el in n['elements']: collect_lhs_branches(el)
            else: lhs_nodes_to_process.append(n)
            return
        if k in ('ConsecutiveRepetition', 'SequenceRepetition') or ('repetition' in n):
            rep = n.get('repetition', {}) if 'repetition' in n else n
            if rep.get('kind') in ('Goto', 'GoTo', 'NonConsecutive', 'Nonconsecutive'):
                lhs_nodes_to_process.append(n)
                return
            try:
                r_min = int(rep.get('min', 1))
                r_max = int(rep.get('max', r_min))
            except:
                lhs_nodes_to_process.append(n)
                return

            if r_min != r_max:
                # --- NUOVA LOGICA DI INSTRADAMENTO ---
                # Verifichiamo se la ripetizione è applicata a un booleano puro.
                # Se sì, NON ennuplichiamo qui: lasciamo che il linearizer crei un singolo 
                # step con 'consec_range', che verrà gestito efficientemente dal pipeline_engine.
                
                def is_pure_boolean(node):
                    if not isinstance(node, dict): return False
                    k = node.get('kind', '')
                    if k in ('Simple', 'Parenthesized'):
                        return is_pure_boolean(node.get('expr') or node.get('operand'))
                    # Usiamo i tipi base booleani (NamedValue, BinaryOp, ecc.)
                    return k in ('BinaryOp', 'UnaryOp', 'NamedValue', 'IntegerLiteral', 
                                'UnbasedUnsizedIntegerLiteral', 'Expression', 'Invocation', 'Call')

                operand = n.get('operand') or n.get('expr')
                if is_pure_boolean(operand):
                    lhs_nodes_to_process.append(n)
                    return

                # --- VECCHIA LOGICA (FALLBACK) ---
                # Se è una macro-sequenza complessa, manteniamo l'ennuplicazione AST
                # per garantire l'isolamento dei thread e il supporto alle variabili locali.
                for i in range(r_min, r_max + 1):
                    new_node = copy.deepcopy(n)
                    if 'repetition' in new_node:
                        new_node['repetition']['min'] = i
                        new_node['repetition']['max'] = i
                        new_node['repetition']['kind'] = 'Consecutive'
                    else:
                        new_node['min'] = i
                        new_node['max'] = i
                    lhs_nodes_to_process.append(new_node)
                return

        lhs_nodes_to_process.append(n)

    collect_lhs_branches(lhs_node)

    rhs_nodes_to_process = []
    def collect_rhs_branches(n):
        if not isinstance(n, dict):
            rhs_nodes_to_process.append(n)
            return
        k = n.get('kind', '')
        op = n.get('op', '')
        if k in ('SequenceAnd', 'AndSequence') or (k == 'Binary' and op == 'And'):
            if 'left' in n and 'right' in n:
                collect_rhs_branches(n['left'])
                collect_rhs_branches(n['right'])
            elif 'elements' in n:
                for el in n['elements']: collect_rhs_branches(el)
            else: rhs_nodes_to_process.append(n)
            return
        rhs_nodes_to_process.append(n)

    if rhs_node:
        collect_rhs_branches(rhs_node)
        
        # --- COVER PROPERTIES ---
        if keyword == 'cover':
            if len(rhs_nodes_to_process) > 1:
                err_msg = f"// ERROR generating {label}: SVA 'and' operator in the consequent is not supported for 'cover property'."
                print(f"[GENERATOR ERROR] {err_msg}", file=sys.stderr)
                return err_msg
            
            if rhs_is_negated:
                err_msg = f"// ERROR generating {label}: Global negation 'not()' in the consequent is not supported for 'cover property'."
                print(f"[GENERATOR ERROR] {err_msg}", file=sys.stderr)
                return err_msg

        if rhs_is_negated and len(rhs_nodes_to_process) > 1:
            err_msg = f"// ERROR generating {label}: Global negation 'not()' of the and of sequences (not (A and B)) is not supported."
            print(f"[GENERATOR ERROR] {err_msg}", file=sys.stderr)
            return err_msg

    warmup_target = 0
    lhs_pipelines_steps = []
    rhs_pipelines_steps = []
    past_substitutions: Dict[str, str] = {}
    sampled_signals_decl = []
    sampled_signals_update = []
    sampled_signals_reset = []

    try:
        sampling_reqs = collect_sampling_needs(expr_node)
        for i, (expr_str, info) in enumerate(sampling_reqs.items()):
            width = info.get('width', '') 
            depth = info.get('max_depth', 1)
            reg_base = f"_a{assertion_id}_past_{i}"
            
            # 1. Dichiarazione array packed con inizializzazione inline
            # 'width' contiene già l'eventuale [N:0], es: "logic [max_depth:1] [7:0] reg_name = '0;"
            packed_type = f"logic [{depth}:1] {width}".strip()
            sampled_signals_decl.append(f"    {packed_type} {reg_base} = '0;")
            
            # 2. Reset vettoriale 
            sampled_signals_reset.append(f"{reg_base} <= '0;")
            
            # 3. Shift vettoriale combinato (protetto per l'edge case depth == 1)
            shift_expr = f"{expr_str}" if depth == 1 else f"{{{reg_base}[{depth}-1:1], {expr_str}}}"
            sampled_signals_update.append(f"{reg_base} <= {shift_expr};")
            
            past_substitutions[expr_str] = reg_base

        
        for branch_node in lhs_nodes_to_process:
            steps = linearize_sequence(branch_node, context='lhs', past_substitutions=past_substitutions)
            lhs_pipelines_steps.append(steps)

        # --- GUARDRAIL: LHS compact dynamic delay + local variables ---
        #
        # Caso non supportato:
        #   (b, l_data = data_in) ##[N:M] c
        #
        if local_vars_def:
            for lhs_branch_idx, lhs_steps in enumerate(lhs_pipelines_steps):
                active_lhs_local_vars = set()

                for step_idx, step in enumerate(lhs_steps):
                    dynamic_delay = (
                        getattr(step, 'tap_range', None) is not None and
                        step.tap_range[0] != step.tap_range[1]
                    )

                    if dynamic_delay and active_lhs_local_vars:
                        raise ValueError(
                            "Unsupported SVA feature: local variables assigned before a compact "
                            "dynamic delay ##[N:M] in the antecedent are not supported. "
                            "This can create multiple concurrent local-variable contexts that "
                            "the compact LHS pipeline cannot preserve. "
                            "Rewrite the antecedent as an explicit OR of fixed-delay branches, "
                            "for example: ((b, l = x) ##1 c) or ((b, l = x) ##2 c) or "
                            "((b, l = x) ##3 c)."
                        )

                    if getattr(step, 'assignments', {}):
                        active_lhs_local_vars.update(step.assignments.keys())


        final_rhs_branches = []
        rhs_branch_negations = []

        for branch_node in rhs_nodes_to_process:
            local_negated = False
            actual_node = branch_node
            
            if isinstance(actual_node, dict):
                k = actual_node.get('kind', '')
                op = actual_node.get('op', '')
                if k in ('Unary', 'UnaryOp') and op in ('LogicalNot', 'Not', '!'):
                    local_negated = True
                    actual_node = actual_node.get('operand') or actual_node.get('expr')
            
            final_rhs_branches.append(actual_node)
            rhs_branch_negations.append(local_negated)

        for branch_node in final_rhs_branches:
            steps = linearize_sequence(branch_node, context='rhs', past_substitutions=past_substitutions)
            
            # GUARDRAIL: Prevenzione first_match nel Conseguente (Lookback Engine)
            for step in steps:
                if getattr(step, 'is_first_match', False):
                    raise ValueError(
                        "Unsupported SVA feature: 'first_match()' in the consequent (RHS) "
                        "is not currently supported. "
                        "Please rewrite the property or restrict first_match to the antecedent."
                    )
            
            rhs_pipelines_steps.append(steps)

        warmup_target = _compute_temporal_warmup_target(
            assertion_id=assertion_id,
            lhs_pipelines_steps=lhs_pipelines_steps,
            rhs_pipelines_steps=rhs_pipelines_steps,
            is_overlapped=is_overlapped
        )



    except ValueError as e:
        print(f"[GENERATOR ERROR] Assertion '{label}': {str(e)}", file=sys.stderr)
        return f"// ERROR generating {label}: {str(e)}"

    decl_lines = []
    assign_lines = []
    update_lines = []
    rhs_code_blocks = []

    if sampled_signals_decl:
        decl_lines.append(f"    // --- Shadow Registers for $past ---")
        decl_lines.extend(sampled_signals_decl)
    if sampled_signals_update:
        update_lines.extend(sampled_signals_update)

    start_trigger_signal = "1'b1"
    if warmup_target > 0:
        cnt_bits = warmup_target.bit_length()
        cnt_reg = f"_a{assertion_id}_warmup_cnt"
        warmup_done_sig = f"_a{assertion_id}_warmup_done"
        decl_lines.append(f"    // --- Warm-up Counter ---")
        decl_lines.append(f"    logic [{cnt_bits-1}:0] {cnt_reg} = 0;")
        decl_lines.append(f"    logic {warmup_done_sig};")
        assign_lines.append(f"    assign {warmup_done_sig} = ({cnt_reg} == {warmup_target});")
        sampled_signals_reset.append(f"{cnt_reg} <= '0;")
        update_lines.append(f"if ({cnt_reg} >= {warmup_target}) begin")
        update_lines.append(f"    {cnt_reg} <= {warmup_target};")
        update_lines.append(f"end else begin")
        update_lines.append(f"    {cnt_reg} <= {cnt_reg} + 1;")
        update_lines.append(f"end")
        start_trigger_signal = warmup_done_sig

    decl_lines.append(f"    // --- Assertion {assertion_id} ({label}) ---")
    decl_lines.append(f"    // Type: {keyword.upper()}")
    if rhs_is_negated: decl_lines.append(f"    // Modifier: NOT (Negated Consequent)")
    
    needs_duplication = local_vars_def and len(lhs_pipelines_steps) > 1
    if needs_duplication:
        decl_lines.append(f"    // Mode: Duplicated RHS (Local Vars + OR Antecedent)")
    
    # ─────────────────────────────────────────────────────────────────────
    # CASO A: Duplicazione RHS
    # ─────────────────────────────────────────────────────────────────────
    if needs_duplication:
        all_rhs_fails = []
        all_rhs_passes = []
        implication_delay = 0 if is_overlapped else 1
        
        for lhs_idx, lhs_steps in enumerate(lhs_pipelines_steps):
            lhs_prefix = f"lhs_b{lhs_idx}"
            
            pipe_res = generate_pipeline_stage(
                assertion_id, lhs_steps, start_trigger_signal, lhs_prefix, 
                start_vars_regs={}, local_vars_def=local_vars_def, generate_fail=False,
                decl_lines=decl_lines, assign_lines=assign_lines, 
                update_lines=update_lines, sampled_signals_reset=sampled_signals_reset
            )
            lhs_trigger, lhs_end_vars = pipe_res[0], pipe_res[1]
            
            rhs_trigger_for_branch = lhs_trigger

            # Passa al RHS solo le local vars effettivamente assegnate nell'antecedente di QUESTO branch.
            rhs_vars_for_branch = lhs_end_vars
            if local_vars_def:
                lhs_assigned_vars = set()
                for _s in lhs_steps:
                    if _s.assignments:
                        lhs_assigned_vars |= set(_s.assignments.keys())
                rhs_vars_for_branch = {v: lhs_end_vars[v] for v in lhs_assigned_vars if v in lhs_end_vars}
                         
            if rhs_node and implication_delay > 0:
                bridge_reg = f"_a{assertion_id}_{lhs_prefix}_imp_d"
                decl_lines.append(f"    logic {bridge_reg} = 1'b0;")
                update_lines.append(f"{bridge_reg} <= {lhs_trigger};")
                sampled_signals_reset.append(f"{bridge_reg} <= 1'b0;")
                rhs_trigger_for_branch = bridge_reg
                
                bridge_vars = {}
                for v_name, reg_name in rhs_vars_for_branch.items():  # usa il mapping già filtrato
                    b_var_reg = f"_a{assertion_id}_{lhs_prefix}_imp_d_{v_name}"
                    decl_lines.append(f"    {local_vars_def[v_name]} {b_var_reg} = '0;")
                    update_lines.append(f"{b_var_reg} <= {reg_name};")
                    sampled_signals_reset.append(f"{b_var_reg} <= '0;")
                    bridge_vars[v_name] = b_var_reg
                rhs_vars_for_branch = bridge_vars         

            if rhs_node:
                for rhs_idx, rhs_steps in enumerate(rhs_pipelines_steps):
                    # GUARDRAIL: Prevenzione Deadlock FSM in Caso A
                    for step in rhs_steps:
                        if getattr(step, 'is_goto', False) or getattr(step, 'is_non_consecutive_tail', False):
                            raise ValueError(
                                f"Unsupported SVA feature: Goto [->] or Non-Consecutive [=] repetition "
                                f"in the consequent is not supported when combined with LHS 'or' branches "
                                f"and local variables."
                            )

                    if len(rhs_pipelines_steps) > 1: rhs_prefix = f"{lhs_prefix}_rhs_b{rhs_idx}"
                    else: rhs_prefix = f"{lhs_prefix}_rhs"
                    
                    engine_id = f"{assertion_id}_{rhs_prefix}"
                    local_neg = rhs_branch_negations[rhs_idx]
                    engine_is_negated = (rhs_is_negated != local_neg)

                    

                    # Usa il LookbackEngine duplicato e isolato per supportare i delay dinamici ##[N:M]
                    checker_block = generate_lookback_checker(
                        assertion_id=engine_id,
                        steps=rhs_steps,
                        start_signal=rhs_trigger_for_branch,
                        clk=clk,
                        rst=active_rst,
                        local_vars=local_vars_def,
                        start_vars=rhs_vars_for_branch,
                        generate_asserts=False,
                        is_negated=engine_is_negated,
                        design_signals_map=design_signals_map,
                        ifdef_mode=ifdef_mode
                    )
                    rhs_code_blocks.append(checker_block)
                    
                    # Raccoglie i segnali di fail generati dal lookback
                    for k in range(len(rhs_steps)):
                        all_rhs_fails.append(f"_a{engine_id}_rhs_fail_{k}")
                    
                    # --- Raccoglie i segnali di pass per il cover ---
                    if not engine_is_negated:
                        all_rhs_passes.append(f"_a{engine_id}_rhs_pass")


        rhs_fails = all_rhs_fails
        if all_rhs_passes:
            rhs_final_pass_sig = f"_a{assertion_id}_rhs_any_pass"
            decl_lines.append(f"    logic {rhs_final_pass_sig};")
            rhs_code_blocks.append(f"    assign {rhs_final_pass_sig} = {' || '.join(all_rhs_passes)};")
            rhs_final_pass = rhs_final_pass_sig
        else:
            rhs_final_pass = "1'b0"
        lhs_final_pass = "1'b0" 
    
    # ─────────────────────────────────────────────────────────────────────
    # CASO B: Logica Originale LHS + GOTO HEAD + LOOKBACK TAIL
    # ─────────────────────────────────────────────────────────────────────
    else:
        lhs_branch_triggers = []
        final_lhs_vars = {} 
        for idx, steps in enumerate(lhs_pipelines_steps):
            prefix = f"lhs_b{idx}" if len(lhs_pipelines_steps) > 1 else "lhs"
            pipe_res = generate_pipeline_stage(
                assertion_id, steps, start_trigger_signal, prefix, 
                start_vars_regs={}, local_vars_def=local_vars_def, generate_fail=False,
                decl_lines=decl_lines, assign_lines=assign_lines, 
                update_lines=update_lines, sampled_signals_reset=sampled_signals_reset
            )
            trigger, end_vars = pipe_res[0], pipe_res[1]
            lhs_branch_triggers.append(trigger)
            if idx == 0: final_lhs_vars = end_vars 
        
        lhs_final_pass_sig = f"_a{assertion_id}_lhs_final"
        decl_lines.append(f"    logic {lhs_final_pass_sig};")
        if not lhs_branch_triggers: assign_lines.append(f"    assign {lhs_final_pass_sig} = 1'b0;")
        else: assign_lines.append(f"    assign {lhs_final_pass_sig} = {' || '.join(lhs_branch_triggers)};")
        lhs_final_pass = lhs_final_pass_sig

        rhs_start_trigger = lhs_final_pass

        # Passa al RHS solo le local vars effettivamente assegnate nell'antecedente (unico branch in questo caso).
        rhs_start_vars = final_lhs_vars
        if local_vars_def:
            lhs_assigned_vars = set()
            for _s in lhs_pipelines_steps[0]:
                if _s.assignments:
                    lhs_assigned_vars |= set(_s.assignments.keys())
            rhs_start_vars = {v: final_lhs_vars[v] for v in lhs_assigned_vars if v in final_lhs_vars}

        if rhs_node:
            implication_delay = 0 if is_overlapped else 1
            if implication_delay > 0:
                bridge_reg = f"_a{assertion_id}_imp_d"
                decl_lines.append(f"    logic {bridge_reg} = 1'b0;")
                update_lines.append(f"{bridge_reg} <= {lhs_final_pass};")
                sampled_signals_reset.append(f"{bridge_reg} <= 1'b0;")
                rhs_start_trigger = bridge_reg
                
                bridge_vars = {}
                for v_name, reg_name in rhs_start_vars.items():  # usa il mapping già filtrato
                    b_var_reg = f"_a{assertion_id}_imp_d_{v_name}"
                    decl_lines.append(f"    {local_vars_def[v_name]} {b_var_reg} = '0;")
                    update_lines.append(f"{b_var_reg} <= {reg_name};")
                    sampled_signals_reset.append(f"{b_var_reg} <= '0;")
                    bridge_vars[v_name] = b_var_reg
                rhs_start_vars = bridge_vars

        rhs_fails = []
        rhs_final_pass = "1'b0"
        rhs_passes = []

        if rhs_node:
            for idx, steps in enumerate(rhs_pipelines_steps):
                branch_suffix = f"_b{idx}" if len(rhs_pipelines_steps) > 1 else ""
                engine_id = f"{assertion_id}{branch_suffix}"
                local_neg = rhs_branch_negations[idx]
                engine_is_negated = (rhs_is_negated != local_neg)


                goto_head_steps = []
                tail_steps = []
                in_goto_head = True

                for step in steps:
                    if in_goto_head and getattr(step, 'is_goto', False):
                        goto_head_steps.append(step)
                    else:
                        in_goto_head = False
                        
                        # Il LookbackEngine non supporta l'esplorazione dinamica di Goto.
                        if getattr(step, 'is_non_consecutive_tail', False):
                            raise ValueError(
                                f"Unsupported SVA feature: Non-Consecutive repetition [=N] is not supported "
                                f"in the consequent. Please use exact Goto repetition [->N] only."
                            )
                        if getattr(step, 'is_goto', False):
                            raise ValueError(
                                f"Unsupported SVA feature: Goto [->] repetition is only supported at the very "
                                f"beginning of the consequent (e.g., |-> a[->1] ##1 b). Found inside the tail."
                            )
                            
                        tail_steps.append(step)

                current_branch_start = rhs_start_trigger
                

                if goto_head_steps:
                    # --  No range [->N:M] nel conseguente --
                    valid_goto_hits = sum(1 for s in goto_head_steps if getattr(s, 'contributes_to_pass', False))
                    if valid_goto_hits > 1:
                        raise ValueError(
                            f"Unsupported SVA feature: Goto repetition with a range (e.g., [->N:M]) "
                            f"in the consequent is not supported. "
                            f"Use exact counts (e.g., [->N]) instead."
                        )
                    # Il blocco Goto Head è un puro control-path e non possiede FIFO per 
                    # trattenere i dati delle variabili locali durante i cicli di attesa.
                    if local_vars_def:
                        raise ValueError(
                            f"Unsupported SVA feature: Using Goto [->] or Non-Consecutive [=] repetition "
                            f"in the consequent is not supported when SVA local variables are defined, "
                        )

                    goto_decl = []
                    goto_logic = []
                    goto_done, goto_fail = _generate_goto_head(
                        engine_id, current_branch_start, goto_head_steps, 
                        clk, active_rst, goto_decl, goto_logic, rhs_goto_watchdog 
                    )




                    rhs_code_blocks.append("\n".join(goto_decl))
                    rhs_code_blocks.append("\n".join(goto_logic))
                    
                    current_branch_start = goto_done
                    if goto_fail: rhs_fails.append(goto_fail)

                    # --- Segnale di pass dal Goto se non ci sono tail steps ---
                    if not tail_steps and not engine_is_negated:
                        rhs_passes.append(goto_done)

                if tail_steps:
                    checker_block = generate_lookback_checker(
                        assertion_id=engine_id,
                        steps=tail_steps,
                        start_signal=current_branch_start,
                        clk=clk,
                        rst=active_rst,
                        local_vars=local_vars_def,
                        start_vars=rhs_start_vars,
                        generate_asserts=False,
                        is_negated=engine_is_negated,
                        design_signals_map=design_signals_map,
                        ifdef_mode=ifdef_mode
                    )

                    rhs_code_blocks.append(checker_block)
                    
                    for k in range(len(tail_steps)):
                        rhs_fails.append(f"_a{engine_id}_rhs_fail_{k}")
                        
                    # --- Raccoglie il segnale di pass dal lookback tail ---
                    if not engine_is_negated:
                        rhs_passes.append(f"_a{engine_id}_rhs_pass")
            
            # --- Generazione del pass_sig finale per il Caso B ---
            if rhs_passes:
                rhs_final_pass_sig = f"_a{assertion_id}_rhs_final_pass"
                decl_lines.append(f"    logic {rhs_final_pass_sig};")
                rhs_code_blocks.append(f"    assign {rhs_final_pass_sig} = {' || '.join(rhs_passes)};")
                rhs_final_pass = rhs_final_pass_sig
                

    # ═════════════════════════════════════════════════════════════════════
    # 5. OUTPUT FINALE
    # ═════════════════════════════════════════════════════════════════════
    final_lines = []
    checker_ports = []
    
    final_lines.append("    // Antecedent logic")
    final_lines.extend(decl_lines)
    final_lines.extend(assign_lines)
    
    has_updates = len(update_lines) > 0 or len(sampled_signals_reset) > 0
    
    if has_updates:
        final_lines.append(f"    always_ff @({clk}) begin")
        if active_rst:
            final_lines.append(f"        if ({active_rst}) begin")
            for line in sampled_signals_reset: final_lines.append(f"            {line}")
            final_lines.append("        end else begin")
        else: final_lines.append("        begin")
        
        for line in update_lines: final_lines.append(f"            {line}")
        
        final_lines.append("        end")
        final_lines.append("    end")

    if rhs_code_blocks:
        final_lines.append("")
        final_lines.append("    // Consequent logic")
        final_lines.extend(rhs_code_blocks)

    # --- Generazione Hardware Checker ---
    if is_checker_mode and keyword in ('assert', 'assume'):
        final_lines.append("")
        final_lines.append(f"    // --- Hardware Checker Signals for {label} ---")
        
        # 1. Porta di FAIL.
        #
        # In modalità checker il fail è una porta combinatoria. Deve quindi
        # essere mascherato esplicitamente con la condizione di disable iff,
        # altrimenti le assertion a latenza zero possono alzare fail durante
        # il reset/disable.
        port_fail = f"{label}_{keyword}_fail"
        if not rhs_fails:
            return f"// ERROR generating {label}: Internal Checker Error - No fail condition found.", []
        
        checker_ports.append(f"output logic {port_fail}")

        raw_fail_cond = " || ".join(rhs_fails)
        masked_fail_cond = mask_with_active_disable(raw_fail_cond)

        final_lines.append(f"    assign {port_fail} = {masked_fail_cond};")

        # 2. Porta di PASS.
        #
        # Anche il pass deve essere soppresso durante disable iff. Questo evita
        # che un checker hardware segnali pass validi mentre la property SVA
        # sarebbe disabilitata.
        if generate_pass:
            port_pass = f"{label}_{keyword}_pass"
            checker_ports.append(f"output logic {port_pass}")
            
            raw_pass_cond = rhs_final_pass
            masked_pass_cond = mask_with_active_disable(raw_pass_cond)

            final_lines.append(f"    assign {port_pass} = {masked_pass_cond};")

    # La logica testbench classica viene generata SOLO se non siamo in modalità checker
    has_legacy_asserts = (keyword != 'none' and not is_checker_mode)
    # --- ---
    
    if has_legacy_asserts:
        final_lines.append("")
        final_lines.append("    // --- Final Assertion Checks ---")
        final_lines.append(f"    always_ff @({clk}) begin")
        if active_rst:
            final_lines.append(f"        if (!{active_rst}) begin")
        else:
            final_lines.append(f"        begin")

        if ifdef_mode == 'disable':
            final_lines.append(f"            `ifndef DISABLE_{label}")
        elif ifdef_mode == 'enable':
            final_lines.append(f"            `ifdef ENABLE_{label}")


        if keyword == 'cover':
            target_signal = "1'b0" 
            if rhs_node:
                if rhs_is_negated: 
                    if rhs_fails: target_signal = " || ".join(rhs_fails)
                else: target_signal = rhs_final_pass
            else: target_signal = lhs_final_pass
            
            if cover_action == 'display':
                final_lines.append(f"            if ({target_signal}) begin")
                final_lines.append(f"                {label}: cover(1);")
                final_lines.append(f'                $display("COVER {label} reached at time %0t", $time);')
                final_lines.append(f"            end")
            else: 
                final_lines.append(f"            if ({target_signal}) {label}: cover(1);")
        
        elif keyword == 'assume':
            if rhs_fails:
                fail_cond = " || ".join(rhs_fails)
                final_lines.append(f"            if ({fail_cond}) {label}: assume(0);")
        
        elif keyword == 'assert':
            if rhs_fails:
                fail_cond = " || ".join(rhs_fails)
                
                suffix = ";"
                if assert_action == 'display': 
                    suffix = f' else $display("ASSERT FAILURE: {label} at time %0t", $time);'
                elif assert_action == 'error': 
                    suffix = f' else $error("ASSERT FAILURE: {label} at time %0t", $time);'
                
                final_lines.append(f"            if ({fail_cond}) {label}: assert(0){suffix}")

        if ifdef_mode in ('disable', 'enable'):
            final_lines.append(f"            `endif")

        if active_rst:
            final_lines.append("        end")
        else:
            final_lines.append("        end")
        final_lines.append("    end")

    return "\n".join(final_lines), checker_ports