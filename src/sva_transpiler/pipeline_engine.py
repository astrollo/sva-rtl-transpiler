import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict

@dataclass
class GenStep:
    expr: str
    delay: int
    tap_range: Optional[tuple]
    sig_name: str
    pipe_regs: List[str] = field(default_factory=list)
    history_regs: List[str] = field(default_factory=list)
    pass_signal: str = ""
    fail_signal: Optional[str] = None
    update_lines: List[str] = field(default_factory=list)
    data_regs: Dict[str, str] = field(default_factory=dict)

def _substitute_local_vars(expr_str, current_stage_data_regs, *, local_vars_def=None, strict=False, err_ctx=""):
    """
    Sostituisce i riferimenti alle variabili locali con i registri/wire del datapath corrente.

    Se strict=True (e local_vars_def è non-vuoto), segnala un errore se l'espressione
    legge una local var che non è presente in current_stage_data_regs (read-before-write).
    """
    if strict and local_vars_def:
        # local_vars_def può essere un dict (nome->tipo) o un iterabile di nomi
        if isinstance(local_vars_def, dict):
            local_names = list(local_vars_def.keys())
        else:
            local_names = list(local_vars_def)

        missing = []
        for v in local_names:
            if re.search(r'\b' + re.escape(v) + r'\b', expr_str) and v not in current_stage_data_regs:
                missing.append(v)

        if missing:
            ctx = f" {err_ctx}" if err_ctx else ""
            raise ValueError(
                f"Semantic Error: read-before-write of local var(s) {', '.join(missing)}{ctx}. Expr: {expr_str}"
            )

    new_expr = expr_str
    for var, reg in current_stage_data_regs.items():
        pattern = r'\b' + re.escape(var) + r'\b'
        new_expr = re.sub(pattern, reg, new_expr)
    return new_expr


def generate_pipeline_stage(assertion_id, steps, start_trigger, prefix, 
                            start_vars_regs, local_vars_def, generate_fail,
                            decl_lines, assign_lines, update_lines, sampled_signals_reset):
    """
    Genera la logica di pipeline per l'Antecedente o per rami duplicati.
    Utilizza i tipi di dati verbatim dall'AST per le variabili locali.
    """
    current_trigger = start_trigger
    current_vars_regs = start_vars_regs if start_vars_regs else {} 
    
    stage_fail_signals = []
    
    goto_accumulators = []
    goto_data_accumulators = {v: [] for v in local_vars_def} if local_vars_def else {}
    
    if not steps: return current_trigger, {}, []

    for i, step in enumerate(steps):
        sig_base = f"_a{assertion_id}_{prefix}_s{i}"
        g_step = GenStep(expr=step.expr, delay=step.delay, tap_range=step.tap_range, sig_name=sig_base)
        
        step_data_delay_map = {v: {} for v in local_vars_def} if local_vars_def else {}

        # --- GESTIONE THROUGHOUT (ABORT CONDITION) ---
        t_cond = None
        if getattr(step, 'throughout_cond', None):
            t_cond = _substitute_local_vars(
                step.throughout_cond,
                current_vars_regs,
                local_vars_def=local_vars_def,
                strict=True,
                err_ctx=f"(a{assertion_id} {prefix} step {i} throughout)"
            )
        

        def add_update(line):
            if t_cond:
                parts = line.split('<=')
                if len(parts) == 2:
                    # Estrazione robusta tramite Regex dell'ultimo identificatore prima del <=
                    # Previene la corruzione di registri che contengono la sottostringa "if" (es. "shift_reg")
                    m = re.search(r'([a-zA-Z0-9_]+)\s*(?:\[[^\]]*\])?\s*$', parts[0])
                    reg_name = m.group(1) if m else parts[0].strip()
                    
                    update_lines.append(f"if (!({t_cond})) {reg_name} <= 1'b0;")
                    update_lines.append(f"else {line}")
                else:
                    update_lines.append(line)
            else:
                update_lines.append(line)


        # ═════════════════════════════════════════════════════════════════════
        # A. GOTO / TAIL (Logica FSM Ottimizzata a Shift Register)
        # ═════════════════════════════════════════════════════════════════════
        if getattr(step, 'is_goto', False) or getattr(step, 'is_non_consecutive_tail', False):
            is_goto = getattr(step, 'is_goto', False)
            is_tail = getattr(step, 'is_non_consecutive_tail', False)
            
            state_q = f"{sig_base}_q"
            state_d = f"{sig_base}_d"
            decl_lines.append(f"    logic {state_q} = 1'b0;")
            decl_lines.append(f"    logic {state_d};")
            sampled_signals_reset.append(f"{state_q} <= 1'b0;")
            
            expr = _substitute_local_vars(
                step.expr,
                current_vars_regs,
                local_vars_def=local_vars_def,
                strict=True,
                err_ctx=f"(a{assertion_id} {prefix} step {i} expr)"
            )
            g_step.expr = expr
            
            idx = getattr(step, 'goto_index', -1)
            total = getattr(step, 'goto_total', -1)
            min_hits = getattr(step, 'goto_min', -1)
            
            # --- Generazione Next-State (D) e Current-State (Q) ---
            if idx == 0:
                # Stadio 0: attende il primo match
                wait_state = f"{state_q} || {current_trigger}"
                d_expr = f"({expr}) ? 1'b0 : {wait_state}"
            else:
                # Stadi successivi: shiftano il wait state se c'è un match
                prev_sig_base = f"_a{assertion_id}_{prefix}_s{i-1}"
                if idx == 1:
                    prev_wait = f"{prev_sig_base}_q || {current_trigger}"
                else:
                    prev_wait = f"{prev_sig_base}_q"
                
                d_expr = f"({expr}) ? {prev_wait} : {state_q}"

            # Iniezione throughout combinatorio su _d (Azzera l'albero in caso di aborto)
            if t_cond: d_expr = f"({t_cond}) ? ({d_expr}) : 1'b0"
            
            assign_lines.append(f"    assign {state_d} = {d_expr};")
            update_lines.append(f"{state_q} <= {state_d};")
            
            # --- Accumulo per il Pass Signal ---
            if is_goto and (min_hits - 1 <= idx <= total - 1):
                goto_accumulators.append(state_q)
                
            flush_goto = getattr(step, 'flush_goto', False)
            if flush_goto or is_tail:
                g_step.pass_signal = f"{sig_base}_pass"
                decl_lines.append(f"    logic {g_step.pass_signal};")
                
                if is_tail:
                    # [=N:M] - Unione logica dei registri _d (Hold states)
                    nc_accumulators = []
                    for k in range(min_hits, total + 1):
                        step_offset = i - (total - k)
                        nc_accumulators.append(f"_a{assertion_id}_{prefix}_s{step_offset}_d")
                    
                    merged_expr = " || ".join(nc_accumulators) if nc_accumulators else "1'b0"
                    assign_lines.append(f"    assign {g_step.pass_signal} = {merged_expr};")

                else:
                    # [->N:M] - Logica match esatto. 
                    # Se min_hits == 1, includiamo current_trigger
                    # nell'OR combinatorio per catturare l'arrivo istantaneo del target (##0).
                    triggers = []
                    if min_hits == 1: triggers.append(current_trigger)
                    triggers.extend(goto_accumulators)
                    
                    merged_q = " || ".join(triggers) if triggers else "1'b0"
                    merged_expr = f"({merged_q}) && ({expr})"
                    
                    if t_cond: merged_expr = f"({merged_expr}) && ({t_cond})"
                    assign_lines.append(f"    assign {g_step.pass_signal} = {merged_expr};")

               
                current_trigger = g_step.pass_signal
                goto_accumulators = []
            
            continue


        # ═════════════════════════════════════════════════════════════════════
        # C. BOOLEAN RANGE REPETITION [*N:M] (Shift-AND Pipeline)
        # ═════════════════════════════════════════════════════════════════════
        consec_range = getattr(step, 'consec_range', None)
        if consec_range:
            min_r, max_r = consec_range
            
            # --- FIX: GESTIONE RITARDO DINAMICO (es. ##[1:2] a[*1:3]) ---
            min_d, max_d = step.tap_range if step.tap_range else (step.delay, step.delay)
            
            prev_trig = current_trigger
            if max_d > 0:
                for d in range(1, max_d + 1):
                    delay_reg = f"{sig_base}_trig_d{d}"
                    decl_lines.append(f"    logic {delay_reg} = 1'b0;")
                    sampled_signals_reset.append(f"{delay_reg} <= 1'b0;")
                    add_update(f"{delay_reg} <= {prev_trig};")
                    prev_trig = delay_reg

            # Trigger per i match reali (Unione dei tap validi nel range [min_d : max_d])
            active_taps = []
            if min_d == 0: active_taps.append(current_trigger)
            for d in range(max(1, min_d), max_d + 1):
                active_taps.append(f"{sig_base}_trig_d{d}")
                
            delayed_trigger = f"{sig_base}_active_trig"
            decl_lines.append(f"    logic {delayed_trigger};")
            assign_lines.append(f"    assign {delayed_trigger} = {' || '.join(active_taps)};")

            # Trigger vettoriale per il bypass della sequenza vuota [*0]
            bypass_taps = []
            for d in range(min_d, max_d + 1):
                b_d = max(0, d - 1)
                tap_name = current_trigger if b_d == 0 else f"{sig_base}_trig_d{b_d}"
                if tap_name not in bypass_taps:
                    bypass_taps.append(tap_name)
                    
            bypass_trigger = f"{sig_base}_bypass_trig"
            decl_lines.append(f"    logic {bypass_trigger};")
            assign_lines.append(f"    assign {bypass_trigger} = {' || '.join(bypass_taps)};")
            # --------------------------------------------------------------

            # Sostituzione eventuali macro (anche se è un booleano)
            expr = _substitute_local_vars(
                step.expr, current_vars_regs, local_vars_def=local_vars_def,
                strict=True, err_ctx=f"(a{assertion_id} {prefix} step {i} expr)"
            )
            g_step.expr = expr
            
            # Applicazione del throughout combinatorio
            expr_with_tcond = f"({expr}) && ({t_cond})" if t_cond else expr
            
            matches = []
            tap_data_sources = {}  
            tap_data_sources[1] = current_vars_regs 
            
            # Stadio 1: Combinatorio diretto dall'arrivo del token
            if max_r >= 1:
                match_sig_1 = f"{sig_base}_match_1"
                decl_lines.append(f"    logic {match_sig_1};")
                # Usa il delayed_trigger vettoriale!
                assign_lines.append(f"    assign {match_sig_1} = ({delayed_trigger}) && ({expr_with_tcond});")
                matches.append(match_sig_1)

            prev_match = matches[0] if matches else None
            
            # Stadi successivi: Shift logico (Token) + Shift dati (Shadow Pipeline)
            for k in range(2, max_r + 1):
                token_sig = f"{sig_base}_token_{k}"
                match_sig = f"{sig_base}_match_{k}"
                
                decl_lines.append(f"    logic {token_sig} = 1'b0;")
                sampled_signals_reset.append(f"{token_sig} <= 1'b0;")
                decl_lines.append(f"    logic {match_sig};")
                
                # Avanzamento del Token (add_update gestisce nativamente il kill-switch del throughout!)
                add_update(f"{token_sig} <= {prev_match};")
                
                # Avanzamento della Shadow Pipeline (Variabili locali in volo)
                current_stage_data = {}
                if local_vars_def:
                    for v_name, v_type in local_vars_def.items():
                        if v_name in current_vars_regs:
                            data_reg = f"{token_sig}_{v_name}"
                            decl_lines.append(f"    {v_type} {data_reg} = '0;")
                            sampled_signals_reset.append(f"{data_reg} <= '0;")
                            
                            prev_data = tap_data_sources[k-1][v_name]
                            
                            # Il dato viaggia solo se il token precedente era valido
                            if t_cond:
                                update_lines.append(f"if (!({t_cond})) {data_reg} <= '0;")
                                update_lines.append(f"else if ({prev_match}) {data_reg} <= {prev_data};")
                            else:
                                update_lines.append(f"if ({prev_match}) {data_reg} <= {prev_data};")
                                
                            current_stage_data[v_name] = data_reg
                
                tap_data_sources[k] = current_stage_data
                
                # Match dello stadio corrente
                assign_lines.append(f"    assign {match_sig} = ({token_sig}) && ({expr_with_tcond});")
                
                matches.append(match_sig)
                prev_match = match_sig
                
            # Selezione dei tap validi
            valid_taps = []
            if min_r == 0:
                # Usiamo il trigger vettoriale che ha già calcolato il collasso temporale
                valid_taps.append(bypass_trigger)
            

            start_idx = max(1, min_r)
            for idx in range(start_idx, max_r + 1):
                valid_taps.append(matches[idx - 1])
                
            # Generazione del segnale di uscita (OR reduction)
            g_step.pass_signal = f"{sig_base}_pass"
            decl_lines.append(f"    logic {g_step.pass_signal};")
            if valid_taps:
                assign_lines.append(f"    assign {g_step.pass_signal} = {' || '.join(valid_taps)};")
            else:
                assign_lines.append(f"    assign {g_step.pass_signal} = 1'b0;")
            
            # Priority-MUX per la risoluzione dei conflitti sulle variabili locali in uscita
            next_vars_regs = {}
            if local_vars_def:
                for v_name, v_type in local_vars_def.items():
                    if v_name in current_vars_regs:
                        out_name = f"{sig_base}_out_{v_name}"
                        decl_lines.append(f"    {v_type} {out_name};")
                        
                        mux_branches = []
                        if min_r == 0:
                            mux_branches.append(f"({bypass_trigger}) ? ({current_vars_regs[v_name]})")
                        
                        for idx in range(start_idx, max_r + 1):
                            match_cond = matches[idx - 1]
                            data_val = tap_data_sources[idx][v_name]
                            mux_branches.append(f"({match_cond}) ? ({data_val})")
                            
                        mux_branches.append(current_vars_regs[v_name]) # Fallback di sicurezza
                        
                        assign_lines.append(f"    assign {out_name} = {' : '.join(mux_branches)};")
                        next_vars_regs[v_name] = out_name
            
            # Chiusura blocco e transizione al prossimo step
            current_trigger = g_step.pass_signal
            current_vars_regs = next_vars_regs
            continue


        # ═════════════════════════════════════════════════════════════════════
        # B. STANDARD PIPELINE
        # ═════════════════════════════════════════════════════════════════════
        
        # Calcolo preventivo del delay del trigger
        prev_reg = current_trigger
        pipe_shifts = []  # list of (dest_reg, src_reg, stage_d)

        if step.delay > 0:
            for d in range(1, step.delay + 1):
                r_name = f"{sig_base}_d{d}"
                decl_lines.append(f"    logic {r_name} = 1'b0;")
                g_step.pipe_regs.append(r_name)
                sampled_signals_reset.append(f"{r_name} <= 1'b0;")
                
                # --- Crea un registro dati parallelo per ogni stadio ---
                if local_vars_def:
                    for v_name, v_type in local_vars_def.items():
                        v_reg = f"{r_name}_{v_name}"
                        decl_lines.append(f"    {v_type} {v_reg} = '0;")
                        sampled_signals_reset.append(f"{v_reg} <= '0;")

                pipe_shifts.append((r_name, prev_reg, d))
                prev_reg = r_name

        last_token = prev_reg

        # Calcolo espressione basato sullo stato precedente (current_vars_regs)
        g_step.expr = _substitute_local_vars(
            step.expr,
            current_vars_regs,
            local_vars_def=local_vars_def,
            strict=True,
            err_ctx=f"(a{assertion_id} {prefix} step {i} expr)"
        )

        # Per tap-range (##[N:M] con M>N) e assignment a local var,
        # la variabile deve essere catturata nel ciclo del match effettivo (pass_signal)
        # usando il mux di uscita _out_<var>, così il valore persiste oltre il ciclo del match.
        has_tap_range = bool(step.tap_range and step.tap_range[1] > step.tap_range[0])
        _range_assign_out_map = {}  # v_name -> (v_type, data_reg_name, out_wire)


        pass_taps = []
        tap_conditions = []
        min_d, max_d = step.tap_range if step.tap_range else (step.delay, step.delay)
        
        history_depth = (step.tap_range[1] - step.tap_range[0]) if step.tap_range else 0
        if history_depth > 0:
            prev_hist = g_step.expr
            for h in range(1, history_depth + 1):
                h_name = f"{sig_base}_hist_d{h}"
                decl_lines.append(f"    logic {h_name} = 1'b0;")
                g_step.history_regs.append(h_name)
                add_update(f"{h_name} <= {prev_hist};")
                sampled_signals_reset.append(f"{h_name} <= 1'b0;")
                prev_hist = h_name

        if step.delay == 0:
            effective_expr = g_step.expr
            if t_cond: effective_expr = f"({effective_expr}) && ({t_cond})"
            cond = f"({current_trigger} && {effective_expr})"
            pass_taps.append(cond)
            tap_conditions.append((cond, 0))
        else:
            for d in range(min_d, max_d + 1):
                if local_vars_def:
                    # Per ogni tap 'd' valuta step.expr nel contesto dati corretto:
                    # - d == 0  -> usa i registri "correnti" (current_vars_regs)
                    # - d > 0   -> usa i registri dati allineati allo stage d (sig_base_d{d}_<var>)
                    defined_vars = list(current_vars_regs.keys())
                    context_map_d = {
                        v: (current_vars_regs[v] if d == 0 else f"{sig_base}_d{d}_{v}")
                        for v in defined_vars
                    }
                    current_tap_expr = _substitute_local_vars(
                        step.expr,
                        context_map_d,
                        local_vars_def=local_vars_def,
                        strict=True,
                        err_ctx=f"(a{assertion_id} {prefix} step {i} tap d={d})"
                    )
                else:
                    current_tap_expr = g_step.expr


                if t_cond: current_tap_expr = f"({current_tap_expr}) && ({t_cond})"
                if d == 0:
                    cond = f"({current_trigger} && {current_tap_expr})"
                    pass_taps.append(cond)
                    tap_conditions.append((cond, 0))
                else:
                    reg_idx = d - 1
                    if 0 <= reg_idx < len(g_step.pipe_regs):
                        token_reg = g_step.pipe_regs[reg_idx]
                        cond = f"({token_reg} && {current_tap_expr})"
                        pass_taps.append(cond)
                        tap_conditions.append((cond, d))

        # --- FIRST_MATCH e SHIFT PARALLELO DATI ---
        if step.delay > 0:
            tap_cond_by_d = {d: cond for (cond, d) in tap_conditions}

            for dest_reg, src_reg, stage_d in pipe_shifts:
                rhs = src_reg

                # 1. Calcolo del Token Kill per il Control Path
                if getattr(step, 'is_first_match', False):
                    kill_d = stage_d - 1
                    if kill_d in tap_cond_by_d:
                        rhs = f"({rhs}) && !({tap_cond_by_d[kill_d]})"

                # 2. Aggiornamento Token Controllo (con gestione throughout se presente)
                # L'helper add_update gestisce già in automatico il gating se t_cond è presente!
                add_update(f"{dest_reg} <= {rhs};")                
                    
                # 3. Aggiornamento Datapath (Sincronizzato al token!)
                if local_vars_def:
                    for v_name in local_vars_def:
                        dest_v = f"{dest_reg}_{v_name}"
                        if stage_d == 1:
                            # Ingresso: cattura dall'uscita dello step precedente se c'è un innesco (current_trigger)
                            src_v = current_vars_regs.get(v_name, "'0")
                            enable_cond = f"({current_trigger})"
                        else:
                            # Transito: propaga dal registro precedente SOLO se il token sta avanzando (rhs)
                            src_v = f"{src_reg}_{v_name}"
                            enable_cond = f"({rhs})"
                            
                        if t_cond:
                            update_lines.append(f"if (!({t_cond})) {dest_v} <= '0;")
                            update_lines.append(f"else if ({enable_cond}) {dest_v} <= {src_v};")
                        else:
                            update_lines.append(f"if ({enable_cond}) {dest_v} <= {src_v};")

        g_step.pass_signal = f"{sig_base}_pass"
        decl_lines.append(f"    logic {g_step.pass_signal};")
        assign_lines.append(f"    assign {g_step.pass_signal} = {' || '.join(pass_taps)};")


        # --- Multiplexer di uscita combinatorio pulito ---
        next_vars_regs = {}
        if local_vars_def:
            for v_name, v_type in local_vars_def.items():
                var_defined_before = (v_name in current_vars_regs)
                var_assigned_here = bool(step.assignments and v_name in step.assignments)

                # Se la variabile non è ancora definita e non viene assegnata in questo step,
                # resta "undefined": non propagarla nel contesto del passo successivo.
                if not (var_defined_before or var_assigned_here):
                    continue

                out_name = f"{sig_base}_out_{v_name}"
                decl_lines.append(f"    {v_type} {out_name};")

                mux_branches = []
                for cond, d in tap_conditions:
                    # Contesto dati esatto per questo specifico tap 'd':
                    # include SOLO le local vars già definite (quelle presenti in current_vars_regs).
                    defined_vars = list(current_vars_regs.keys())
                    context_map_d = {
                        v: (current_vars_regs[v] if d == 0 else f"{sig_base}_d{d}_{v}")
                        for v in defined_vars
                    }

                    if var_assigned_here:
                        # La RHS dell'assegnamento è valutata nel contesto dati del tap (d)
                        data_val = _substitute_local_vars(
                            step.assignments[v_name],
                            context_map_d,
                            local_vars_def=local_vars_def,
                            strict=True,
                            err_ctx=f"(a{assertion_id} {prefix} step {i} rhs->{v_name} tap d={d})"
                        )
                    else:
                        # Pass-through del valore storico che viaggia col token
                        data_val = current_vars_regs[v_name] if d == 0 else f"{sig_base}_d{d}_{v_name}"

                    mux_branches.append(f"({cond}) ? ({data_val})")

                # Fallback quando nessun tap condizionale è attivo:
                # - se stiamo assegnando con tap-range, usa il registro di holding (deferred capture)
                # - altrimenti, propaga il valore precedente solo se già definito; se non lo è, usa '0 (don't-care)
                if has_tap_range and var_assigned_here:
                    data_reg_name = f"{sig_base}_{v_name}"
                    if v_name not in _range_assign_out_map:
                        decl_lines.append(f"    {v_type} {data_reg_name} = '0;")
                        sampled_signals_reset.append(f"{data_reg_name} <= '0;")
                        _range_assign_out_map[v_name] = (v_type, data_reg_name, out_name)
                    fallback = data_reg_name
                else:
                    fallback = current_vars_regs[v_name] if var_defined_before else "'0"

                mux_branches.append(fallback)

                assign_lines.append(f"    assign {out_name} = {' : '.join(mux_branches)};")
                next_vars_regs[v_name] = out_name

        # cattura deferred per assignment a local var in presenza di tap-range.
        # Aggiorna i registri {sig_base}_{v} nel ciclo del match effettivo (pass_signal),
        # prendendo il valore dal mux di uscita _out_{v}.
        if local_vars_def and _range_assign_out_map:
            for v_name, (_v_type, data_reg_name, out_wire) in _range_assign_out_map.items():
                if t_cond:
                    update_lines.append(f"if (!({t_cond})) {data_reg_name} <= '0;")
                    update_lines.append(f"else if ({g_step.pass_signal}) {data_reg_name} <= {out_wire};")
                else:
                    update_lines.append(f"if ({g_step.pass_signal}) {data_reg_name} <= {out_wire};")

        if generate_fail:
            fail_name = f"{sig_base}_fail"
            decl_lines.append(f"    logic {fail_name};")
            if step.delay == 0: assign_lines.append(f"    assign {fail_name} = {current_trigger} && !({g_step.expr});")
            else:
                conditions = [last_token, f"!({g_step.expr})"]
                for h_reg in g_step.history_regs: conditions.append(f"!({h_reg})")
                assign_lines.append(f"    assign {fail_name} = {' && '.join(conditions)};")
            g_step.fail_signal = fail_name
            stage_fail_signals.append(fail_name)

        update_lines.extend(g_step.update_lines)
        current_trigger = g_step.pass_signal
        current_vars_regs = next_vars_regs 
        
    return current_trigger, current_vars_regs, stage_fail_signals