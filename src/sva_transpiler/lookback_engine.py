from dataclasses import dataclass
from typing import List, Dict, Set, Optional
import re
from .utils import parse_expression

@dataclass
class LookbackStageInfo:
    step_idx: int
    expr_sig: str
    original_expr: object 
    min_delay: int
    max_delay: int
    cumulative_max: int
    vec_name: str
    fail_sig: str
    cond_sig: Optional[str] = None
    cond_vec_name: Optional[str] = None
    data_vec_names: Dict[str, str] = None 
    has_local_vars: bool = False
    # Segnali di design da storicizzare per questo step
    signals_to_snapshot: Set[str] = None 
    min_rep: int = 1
    max_rep: int = 1

class LookbackEngine:
    def __init__(self, assertion_id: str, steps: List, start_signal: str, 
                 clk: str, rst: str, local_vars: Dict[str, str] = None,
                 start_vars: Dict[str, str] = None,
                 generate_asserts: bool = True, is_negated: bool = False,
                 design_signals_map: Dict[str, str] = None,
                 ifdef_mode: str = 'disable'):
        self.assertion_id = assertion_id
        self.steps = steps
        self.start_signal = start_signal
        self.clk = clk.strip() 
        self.rst = rst
        self.local_vars_def = local_vars if local_vars else {}
        self.start_vars = start_vars if start_vars else {}
        self.design_signals_map = design_signals_map if design_signals_map else {}
        self.generate_asserts = generate_asserts
        self.is_negated = is_negated 
        
        self.stages: List[LookbackStageInfo] = []
        self.total_max_depth = 0
        self.decl_lines = []
        self.logic_lines = []
        
        self.signal_history_map = {} 
        self.ifdef_mode = ifdef_mode

        self._analyze_timing()
        self._validate_rhs_dynamic_timing_before_local_var_producers()        
        self._validate_rhs_dynamic_timing_before_local_var_reads()


    def _analyze_timing(self):
        # Helper interno per il calcolo del vero delay massimo ---
        # Include sia il gap (##N) sia l'espansione causata dalla ripetizione [*N:M]
        def _get_true_max(step):
            b_max = step.tap_range[1] if step.tap_range else step.delay
            if getattr(step, 'consec_range', None):
                m_rep = step.consec_range[1]
                return b_max + (m_rep - 1 if m_rep > 0 else 0)
            return b_max

        # Ricalcolo globale della profondità totale
        self.total_max_depth = sum(_get_true_max(s) for s in self.steps)
        current_cum_max = 0
        
        start_vec_name = f"_a{self.assertion_id}_rhs_vec_start"

        # --- Buffer Storico Isolato per le Variabili dell'Antecedente ---
        self.start_vars_vecs = {}
        if self.start_vars:
            for var_name, var_sig in self.start_vars.items():
                vec_name = f"_a{self.assertion_id}_rhs_vec_start_var_{var_name}"
                var_type = self.local_vars_def.get(var_name, "logic")
                self._generate_shift_register(var_sig, vec_name, self.total_max_depth, custom_type=var_type)
                self.start_vars_vecs[var_name] = vec_name

        for i, step in enumerate(self.steps):
            # 1. Calcolo del ritardo di innesco (gap ##)
            base_d_min = step.tap_range[0] if step.tap_range else step.delay
            base_d_max = step.tap_range[1] if step.tap_range else step.delay
            
            my_rep_duration = 0
            # 2. Integrazione della durata della ripetizione [*N:M]
            if getattr(step, 'consec_range', None):
                min_rep, max_rep = step.consec_range
                my_rep_duration = (max_rep - 1 if max_rep > 0 else 0)
                
                # Il ritardo massimo si "allunga" per ogni ciclo di persistenza oltre il primo
                d_max = base_d_max + my_rep_duration
                
                # Il ritardo minimo gestisce il bypass: se [*0], applica il collasso temporale (-1)
                if min_rep == 0:
                    d_min = max(0, base_d_min - 1)
                else:
                    d_min = base_d_min + (min_rep - 1)
            else:
                d_min = base_d_min
                d_max = base_d_max
                
            current_cum_max += d_max
            
            step_uses_vars = self._expr_uses_local_vars(step.expr)
            
            # Estraiamo i segnali grezzi SOLO se l'espressione usa variabili locali
            signals_to_snap = self._extract_design_signals(step.expr) if step_uses_vars else set()

            if signals_to_snap:
                for sig in signals_to_snap:
                    if sig not in self.signal_history_map:
                        hist_name = f"_a{self.assertion_id}_rhs_sig_hist_{sig}"
                        self._generate_signal_history(sig, hist_name, self.total_max_depth)
                        self.signal_history_map[sig] = hist_name

            expr_sig_name = f"_a{self.assertion_id}_rhs_expr_{i}"
            self.decl_lines.append(f"    logic {expr_sig_name};")
            
            if step_uses_vars:
                self.logic_lines.append(f"    assign {expr_sig_name} = 1'b1; // Deferred check")
            else:
                parsed_expr = self._process_expression(step.expr)
                self.logic_lines.append(f"    assign {expr_sig_name} = {parsed_expr};")
            
            # --- 1. RISOLUZIONE CONDIZIONE THROUGHOUT ---
            cond_sig_name: Optional[str] = None
            cond_vec_name: Optional[str] = None
            
            if step.throughout_cond:
                if self._expr_uses_local_vars(step.throughout_cond):
                    raise ValueError(
                        "Unsupported SVA feature: Use of local variables inside a 'throughout' "
                        "condition in the consequent is not supported."
                    )
                cond_sig_name = f"_a{self.assertion_id}_rhs_cond_{i}"
                parsed_cond: str = self._process_expression(step.throughout_cond)
                self.decl_lines.append(f"    logic {cond_sig_name};")
                self.logic_lines.append(f"    assign {cond_sig_name} = {parsed_cond};")

            # --- 2. ALLOCAZIONE REGISTRI STORICI (MASKED PIPELINING) ---
            vec_name: str = f"_a{self.assertion_id}_rhs_vec_{i}"
            
            future_delays = sum(_get_true_max(s) for s in self.steps[i+1:])
            
            if i == 0:
                # Lo stadio 0 (l'ancora finale) non subisce slittamenti causati dai gap a monte
                required_depth: int = future_delays + my_rep_duration
            else:
                # W: la finestra di incertezza (ampiezza del gap dinamico dello stadio attuale)
                W = d_max - d_min
                
                if my_rep_duration > 0:
                    # Aggiungiamo 'W' per compensare lo slittamento all'indietro 
                    # nel tempo causato dal percorso più veloce del gap dinamico
                    required_depth: int = future_delays + W + my_rep_duration
                else:
                    required_depth: int = future_delays


            # Iniezione della maschera (cond_sig_name) nel data-path principale
            self._generate_shift_register(
                input_sig=expr_sig_name, 
                vec_name=vec_name, 
                depth=required_depth, 
                width=1,
                mask_cond=cond_sig_name
            )            

            step_data_vecs: Dict[str, str] = {}
            if self.local_vars_def:
                for var_name, var_type in self.local_vars_def.items():
                    data_in_sig: str = f"_a{self.assertion_id}_rhs_data_{i}_{var_name}"
                    self.decl_lines.append(f"    {var_type} {data_in_sig};")
                    
                    assign_rhs: str = "'0"
                    if step.assignments and var_name in step.assignments:
                        raw_expr = step.assignments[var_name]
                        if self._expr_uses_local_vars(raw_expr):
                            raise ValueError(
                                f"Unsupported SVA feature: Re-assignment of local variable '{var_name}' "
                                f"using another local variable in the consequent is not supported."
                            )
                        assign_rhs = self._process_expression(raw_expr)

                    self.logic_lines.append(f"    assign {data_in_sig} = {assign_rhs};") 

                    data_vec_name: str = f"_a{self.assertion_id}_rhs_vec_{i}_{var_name}"
                    
                    # Usa lo stesso required_depth corretto per i dati locali in transito!
                    self._generate_shift_register(
                        input_sig=data_in_sig, 
                        vec_name=data_vec_name, 
                        depth=required_depth, 
                        custom_type=var_type,
                        mask_cond=cond_sig_name
                    )
                    step_data_vecs[var_name] = data_vec_name

            fail_sig_name = f"_a{self.assertion_id}_rhs_fail_{i}"
            self.decl_lines.append(f"    logic {fail_sig_name};")
            
            self.stages.append(LookbackStageInfo(
                step_idx=i,
                expr_sig=expr_sig_name,
                original_expr=step.expr,
                min_delay=d_min,
                max_delay=d_max,
                cumulative_max=current_cum_max,
                vec_name=vec_name,
                fail_sig=fail_sig_name,
                cond_sig=cond_sig_name,
                cond_vec_name=cond_vec_name,
                data_vec_names=step_data_vecs,
                has_local_vars=step_uses_vars,
                signals_to_snapshot=signals_to_snap,
                min_rep=min_rep if getattr(step, 'consec_range', None) else 1,
                max_rep=max_rep if getattr(step, 'consec_range', None) else 1
            ))

        # --- Pass Signal for Cover ---
        self.pass_sig_name = f"_a{self.assertion_id}_rhs_pass"
        self.decl_lines.append(f"    logic {self.pass_sig_name};")

    def _validate_rhs_dynamic_timing_before_local_var_producers(self):
        """
        Guardrail RHS dynamic timing before local-variable producer.

        Caso NON supportato:
            e ##[1:3] (d, l_data = data_in) ##2 (data_out == l_data)

        Motivo:
            la local var viene prodotta a un tempo variabile. Il valore corretto
            da leggere nel consumer dipende dal ramo temporale scelto dal delay
            dinamico a monte. L'architettura attuale non propaga ancora questo
            slack fino al producer della local var.
        """
        if not self.local_vars_def:
            return

        def is_dynamic_timing_step(step):
            tap_range = getattr(step, 'tap_range', None)
            if tap_range and tap_range[0] != tap_range[1]:
                return True

            consec_range = getattr(step, 'consec_range', None)
            if consec_range and consec_range[0] != consec_range[1]:
                return True

            return False

        def expr_reads_var(expr, var_name):
            return re.search(r'\b' + re.escape(var_name) + r'\b', str(expr)) is not None

        def var_is_read_after(var_name, producer_idx):
            for later_step in self.steps[producer_idx + 1:]:
                if expr_reads_var(later_step.expr, var_name):
                    return True
            return False

        dynamic_timing_seen = False

        for step_idx, step in enumerate(self.steps):
            current_is_dynamic = is_dynamic_timing_step(step)
            assignments = getattr(step, 'assignments', {}) or {}

            if assignments:
                live_assigned_vars = [
                    var_name
                    for var_name in assignments.keys()
                    if var_name in self.local_vars_def and var_is_read_after(var_name, step_idx)
                ]

                if live_assigned_vars and (dynamic_timing_seen or current_is_dynamic):
                    raise ValueError(
                        "Unsupported SVA feature: RHS local-variable assignment after or during "
                        "a dynamic timing operator is not supported when the variable is read later. "
                        f"Variable(s): {', '.join(live_assigned_vars)}. "
                        "Example not supported: 'e ##[1:3] (d, l = data_in) ##2 "
                        "(data_out == l)'. Rewrite the sequence so that the local-variable "
                        "producer occurs at a fixed time."
                    )

            if current_is_dynamic:
                dynamic_timing_seen = True

    def _validate_rhs_dynamic_timing_before_local_var_reads(self):
        """
        Guardrail RHS local vars + dynamic timing.

        Caso supportato:
            e ##2 f ##[1:3] (data_out == l_data)

        Casi NON supportati:
            e ##[1:2] f ##[1:3] (data_out == l_data)
            e ##[1:2] f ##2     (data_out == l_data)

        Motivo:
            se uno slack temporale dinamico appare a monte dello stage che legge
            una local var, il valore corretto dei segnali storicizzati dipende
            dal path temporale scelto. L'architettura attuale supporta solo il
            caso in cui il delay dinamico sia direttamente sullo stage consumer.
        """
        if not self.local_vars_def:
            return

        def is_dynamic_timing_step(step):
            tap_range = getattr(step, 'tap_range', None)
            if tap_range and tap_range[0] != tap_range[1]:
                return True

            consec_range = getattr(step, 'consec_range', None)
            if consec_range and consec_range[0] != consec_range[1]:
                return True

            return False

        dynamic_timing_seen = []

        for step_idx, step in enumerate(self.steps):
            reads_local_var = self._expr_uses_local_vars(step.expr)
            current_is_dynamic = is_dynamic_timing_step(step)

            if reads_local_var and dynamic_timing_seen:
                raise ValueError(
                    "Unsupported SVA feature: RHS local-variable reads after an upstream "
                    "dynamic timing operator are not supported. The current implementation "
                    "supports local-variable reads only when any dynamic delay/repetition is "
                    "directly attached to the consumer step, for example: "
                    "'e ##2 f ##[1:3] (data_out == l_data)'. Unsupported examples include: "
                    "'e ##[1:2] f ##[1:3] (data_out == l_data)' and "
                    "'e ##[1:2] f ##2 (data_out == l_data)'."
                )

            if current_is_dynamic:
                dynamic_timing_seen.append(step_idx)


    def _expr_uses_local_vars(self, expr_node):
        if not self.local_vars_def: return False
        expr_str = str(expr_node) 
        for var in self.local_vars_def:
            if re.search(r'\b' + re.escape(var) + r'\b', expr_str):
                return True
        return False

    def _extract_design_signals(self, expr_node) -> Set[str]:
        expr_str = str(expr_node)
        tokens = set(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', expr_str))
        signals = set()
        for t in tokens:
            if t in self.design_signals_map and t not in self.local_vars_def:
                signals.add(t)
        return signals

    def _process_expression(self, expr_node):
        return parse_expression(expr_node)

    def _substitute_vars(self, expr_str, subs):
        new_expr = expr_str
        for var in sorted(subs.keys(), key=len, reverse=True):
            replacement = subs[var]
            pattern = r'\b' + re.escape(var) + r'\b'
            new_expr = re.sub(pattern, replacement, new_expr)
        return new_expr

    def _generate_signal_history(self, signal: str, hist_name: str, depth: int) -> None:
        base_type = self.design_signals_map.get(signal, "logic")
        
        # Iniezione dimensione packed per i segnali di design
        type_0 = re.sub(r'\blogic\b', f'logic [{depth}:0]', base_type)
        
        if depth == 0:
            self.decl_lines.append(f"    {type_0} {hist_name};")
            self.logic_lines.append(f"    assign {hist_name} = {signal};")
            return
            
        type_1 = re.sub(r'\blogic\b', f'logic [{depth}:1]', base_type)
        hist_ff_name = f"{hist_name}_ff"
        
        self.decl_lines.append(f"    {type_0} {hist_name};")
        self.decl_lines.append(f"    {type_1} {hist_ff_name} = '0;") # Inizializzazione inline Formal-Safe
        
        rst_expr = self.rst if self.rst else "1'b0"
        
        # Shift vettoriale protetto per l'edge case depth == 1
        shift_expr = f"{signal}" if depth == 1 else f"{{{hist_ff_name}[{depth-1}:1], {signal}}}"
        
        self.logic_lines.append(f"""
    always_ff @({self.clk}) begin
        if ({rst_expr}) begin
            {hist_ff_name} <= '0;
        end else begin
            {hist_ff_name} <= {shift_expr};
        end
    end""")
        
        # Ricostruzione wire array packed
        self.logic_lines.append(f"    assign {hist_name} = {{{hist_ff_name}, {signal}}};")


    #def _generate_shift_register(self, input_sig: str, vec_name: str, depth: int, width: int = 1, custom_type: Optional[str] = None) -> None:

    def _generate_shift_register(self, input_sig: str, vec_name: str, depth: int, width: int = 1, custom_type: Optional[str] = None, mask_cond: Optional[str] = None) -> None:
        """
        Genera un registro a scorrimento (shift register) sintetizzabile per tracciare la storia di un segnale.
        
        Args:
            input_sig: Il segnale RTL in ingresso al registro.
            vec_name: Il nome della variabile array packed da generare.
            depth: La profondità temporale (quanti cicli di clock storicizzare).
            width: La larghezza in bit del segnale (default 1).
            custom_type: Permette l'override del tipo base (es. per propagare i tipi delle variabili SVA).
            mask_cond: (Opzionale) Condizione di kill-switch (Masked Pipelining). Se cade, svuota la storia.
        """
        # Recupera il tipo base. Il main.py garantisce che contenga la parola 'logic'
        base_type = f"logic [{width-1}:0]" if not custom_type else custom_type
        
        # Inietta la dimensione packed [N:0] o [N:1] immediatamente dopo la keyword 'logic'
        # Questo trasforma 'logic [7:0]' in 'logic [depth:0] [7:0]' (array packed multidimensionale)
        type_0 = re.sub(r'\blogic\b', f'logic [{depth}:0]', base_type)
        
        # Caso base: profondità zero (valutazione istantanea)
        if depth == 0:
            self.decl_lines.append(f"    {type_0} {vec_name};")
            self.logic_lines.append(f"    assign {vec_name} = {input_sig};")
            return

        type_1 = re.sub(r'\blogic\b', f'logic [{depth}:1]', base_type)
        hist_name = f"{vec_name}_hist"
        
        # Dichiarazione con inizializzazione inline '0 (Formal-Safe, nessun blocco initial/multi-driver)
        self.decl_lines.append(f"    {type_1} {hist_name} = '0;")
        self.decl_lines.append(f"    {type_0} {vec_name};")
        
        # Assegnazione combinatoria vettoriale (elimina il blocco always_comb con loop for)
        self.logic_lines.append(f"    assign {vec_name} = {{{hist_name}, {input_sig}}};")
        
        rst_expr = self.rst if self.rst else "1'b0"
        
        # Shift vettoriale (elimina il loop sequenziale). 
        # La concatenazione fallisce se depth=1 (slice [0:1] non valido in SV), quindi serve un if protettivo
        shift_expr = f"{input_sig}" if depth == 1 else f"{{{hist_name}[{depth-1}:1], {input_sig}}}"
        
        active_rst_expr: str = f"({rst_expr}) || !({mask_cond})" if mask_cond else rst_expr
        
        self.logic_lines.append(f"""
    always_ff @({self.clk}) begin
        if ({active_rst_expr}) begin
            {hist_name} <= '0;
        end else begin
            {hist_name} <= {shift_expr};
        end
    end""")

    def _generate_nested_loops_logic(self, target_stage_idx: int) -> List[str]:
        N = target_stage_idx
        lines = []
        lines.append(f"    // --- Z-Node Lookback Logic for Stage {N} ---")

        rst_expr = self.rst if self.rst else "1'b0"
        ns = f"_a{self.assertion_id}_lb"

        def get_D(k):
            return sum(self.stages[r].max_delay for r in range(k+1, N+1))

        def get_X_k(k, age_offset: int = 0):
            stg = self.stages[k]
            d_k = get_D(k)
            base_check = f"{stg.vec_name}[{d_k}]"
            
            if not stg.has_local_vars and not stg.signals_to_snapshot:
                return base_check
                
            subs = {}
            used_local_vars = []
            if stg.has_local_vars and self.local_vars_def:
                expr_str = str(stg.original_expr)
                for var in self.local_vars_def:
                    if re.search(r'\b' + re.escape(var) + r'\b', expr_str):
                        used_local_vars.append(var)

            for var in used_local_vars:
                producer_vec = None
                producer_idx = -1
                for search_i in range(stg.step_idx - 1, -1, -1):
                    search_step = self.stages[search_i]
                    original_s = self.steps[search_i]
                    if original_s.assignments and var in original_s.assignments:
                        if var in search_step.data_vec_names:
                            producer_vec = search_step.data_vec_names[var]
                            producer_idx = search_i
                            break
                if not producer_vec and var in self.start_vars:
                    producer_vec = self.start_vars_vecs[var]
                    producer_idx = -1

                if producer_vec:
                    if producer_idx == -1:
                        delay_start = sum(self.stages[r].max_delay for r in range(0, N+1))
                        subs[var] = f"{producer_vec}[{delay_start}]"
                    else:
                        d_p = sum(self.stages[r].max_delay for r in range(producer_idx+1, N+1))
                        subs[var] = f"{producer_vec}[{d_p}]"
                else:
                    raise ValueError(f"SVA Semantic Error: Local variable '{var}' read before write.")

            if stg.signals_to_snapshot:
                for sig in stg.signals_to_snapshot:
                    if sig in self.signal_history_map:
                        hist_reg = self.signal_history_map[sig]
                        subs[sig] = f"{hist_reg}[{d_k + age_offset}]"

            base_expr_str = self._process_expression(stg.original_expr)
            dynamic_expr = self._substitute_vars(base_expr_str, subs)
            return f"({base_check} && ({dynamic_expr}))"

        def add_downstream(expr, downstream_agg):
            if downstream_agg == "1'b1":
                return expr
            return f"({expr} && {downstream_agg})"

        lines.append(f"    // 1. Z-Nodes tree (Bottom-Up)")
        
        expr_N = get_X_k(N)
        z_name = f"{ns}_z_{N}_s{N}"
        lines.append(f"    logic {z_name};")
        lines.append(f"    assign {z_name} = {expr_N};")

        ff_lines = []

        for k in range(N-1, -1, -1):
            stg_next = self.stages[k+1]
            step_next = self.steps[k+1]
            W_next = stg_next.max_delay - stg_next.min_delay
            sr_name = f"{ns}_sr_z_{N}_s{k+1}"
            prev_z = f"{ns}_z_{N}_s{k+1}"
            
            if k + 1 == N:
                downstream_agg = "1'b1"
            else:
                downstream_agg = f"{ns}_agg_{N}_s{k+2}"
            
            has_consec = getattr(step_next, 'consec_range', None) is not None
            if has_consec:
                min_rep, max_rep = step_next.consec_range
            else:
                min_rep, max_rep = 1, 1

            next_needs_path_sensitive_expr = (
                stg_next.has_local_vars or bool(stg_next.signals_to_snapshot)
            )

            if W_next > 0 and not next_needs_path_sensitive_expr:
                lines.append(f"    logic [{W_next}:1] {sr_name}_q = '0;")
                ff_lines.append(f"        if ({rst_expr}) {sr_name}_q <= '0;")
                if W_next == 1:
                    ff_lines.append(f"        else {sr_name}_q <= {prev_z};")
                else:
                    ff_lines.append(f"        else {sr_name}_q <= {{{sr_name}_q[{W_next}-1:1], {prev_z}}};")

            # --- FIX BUG 2: Solo istanziato se la finestra di incertezza è reale (W_next > 0) ---
            if has_consec and min_rep == 0 and W_next > 0:
                bypass_sr_name = f"{ns}_sr_bypass_{N}_s{k+1}"
                bypass_depth = W_next 
                lines.append(f"    logic [{bypass_depth}:1] {bypass_sr_name}_q = '0;")
                ff_lines.append(f"        if ({rst_expr}) {bypass_sr_name}_q <= '0;")
                if bypass_depth == 1:
                    ff_lines.append(f"        else {bypass_sr_name}_q <= {downstream_agg};")
                else:
                    ff_lines.append(f"        else {bypass_sr_name}_q <= {{{bypass_sr_name}_q[{bypass_depth}-1:1], {downstream_agg}}};")

            # ---  GAP E RIPETIZIONI ---
            agg_terms = []
            D_base = sum(self.stages[r].max_delay for r in range(k+2, N+1))
            base_d_min = step_next.tap_range[0] if step_next.tap_range else step_next.delay
            base_d_max = step_next.tap_range[1] if step_next.tap_range else step_next.delay
            

            for j in range(0, W_next + 1):
                if next_needs_path_sensitive_expr:
                    tap = add_downstream(get_X_k(k + 1, age_offset=j), downstream_agg)
                else:
                    tap = prev_z if j == 0 else f"{sr_name}_q[{j}]"

                valid_chains_for_tap = []
                
                if has_consec:
                    for rep in range(min_rep, max_rep + 1):
                        if rep == 0:
                            continue # Gestito dal bypass puro
                        
                        gap = base_d_max + (max_rep - rep) - j
                        
                        if base_d_min <= gap <= base_d_max:
                            endpoint = D_base + j
                            chain = []
                            for i in range(endpoint + rep - 1, endpoint, -1):
                                chain.append(f"{stg_next.vec_name}[{i}]")
                            
                            if chain:
                                and_chain = " && ".join(chain)
                                valid_chains_for_tap.append(f"(({and_chain}) && {tap})")
                            else:
                                valid_chains_for_tap.append(tap)
                                
                    W_gap = base_d_max - base_d_min
                    if min_rep == 0 and (W_next - W_gap) <= j <= W_next:
                        bypass_tap = downstream_agg if j == 0 else f"{bypass_sr_name}_q[{j}]"
                        valid_chains_for_tap.append(bypass_tap)
                else:
                    valid_chains_for_tap.append(tap)

                if valid_chains_for_tap:
                    unique_chains = list(dict.fromkeys(valid_chains_for_tap))
                    if len(unique_chains) > 1:
                        agg_terms.append("(" + " || ".join(unique_chains) + ")")
                    else:
                        agg_terms.append(unique_chains[0])

            agg_expr = " || ".join(agg_terms) if agg_terms else "1'b0"

            agg_name = f"{ns}_agg_{N}_s{k+1}"
            lines.append(f"    logic {agg_name};")
            lines.append(f"    assign {agg_name} = {agg_expr};")

            expr_k = get_X_k(k)
            curr_z = f"{ns}_z_{N}_s{k}"
            lines.append(f"    logic {curr_z};")
            lines.append(f"    assign {curr_z} = {expr_k} && {agg_name};")

        lines.append(f"    // 2. Final Aggregation for Stage {N}")
        stg_0 = self.stages[0]
        step_0 = self.steps[0]
        W_0 = stg_0.max_delay - stg_0.min_delay
        z_0 = f"{ns}_z_{N}_s0"
        
        downstream_agg_0 = f"{ns}_agg_{N}_s1" if N > 0 else "1'b1"
        
        has_consec_0 = getattr(step_0, 'consec_range', None) is not None
        if has_consec_0:
            min_rep_0, max_rep_0 = step_0.consec_range
        else:
            min_rep_0, max_rep_0 = 1, 1

        stage0_needs_path_sensitive_expr = (
            stg_0.has_local_vars or bool(stg_0.signals_to_snapshot)
        )

        if W_0 > 0 and not stage0_needs_path_sensitive_expr:
            sr_name_0 = f"{ns}_sr_z_{N}_s0"
            lines.append(f"    logic [{W_0}:1] {sr_name_0}_q = '0;")
            ff_lines.append(f"        if ({rst_expr}) {sr_name_0}_q <= '0;")
            
            if W_0 == 1:
                ff_lines.append(f"        else {sr_name_0}_q <= {z_0};")
            else:
                ff_lines.append(f"        else {sr_name_0}_q <= {{{sr_name_0}_q[{W_0}-1:1], {z_0}}};")

        # --- FIX BUG 1: Esteso di +1 (W_0 + 1) per assorbire l'access indexing j+1 senza Out-of-Bounds ---
        if has_consec_0 and min_rep_0 == 0:
            bypass_sr_name_0 = f"{ns}_sr_bypass_{N}_s0"
            bypass_depth_0 = W_0 + 1 
            lines.append(f"    logic [{bypass_depth_0}:1] {bypass_sr_name_0}_q = '0;")
            ff_lines.append(f"        if ({rst_expr}) {bypass_sr_name_0}_q <= '0;")
            if bypass_depth_0 == 1:
                ff_lines.append(f"        else {bypass_sr_name_0}_q <= {downstream_agg_0};")
            else:
                ff_lines.append(f"        else {bypass_sr_name_0}_q <= {{{bypass_sr_name_0}_q[{bypass_depth_0}-1:1], {downstream_agg_0}}};")

        agg_terms_0 = []
        D_base_0 = sum(self.stages[r].max_delay for r in range(1, N+1))
        base_d_min_0 = step_0.tap_range[0] if step_0.tap_range else step_0.delay
        base_d_max_0 = step_0.tap_range[1] if step_0.tap_range else step_0.delay

        for j in range(0, W_0 + 1):
            if stage0_needs_path_sensitive_expr:
                tap = add_downstream(get_X_k(0, age_offset=j), downstream_agg_0)
            else:
                tap = z_0 if j == 0 else f"{sr_name_0}_q[{j}]"

            valid_chains_for_tap = []
            
            if has_consec_0:
                for rep in range(min_rep_0, max_rep_0 + 1):
                    if rep == 0:
                        continue 
                    
                    gap = base_d_max_0 + (max_rep_0 - rep) - j
                    
                    if base_d_min_0 <= gap <= base_d_max_0:
                        endpoint = D_base_0 + j
                        chain = []
                        for i in range(endpoint + rep - 1, endpoint, -1):
                            chain.append(f"{stg_0.vec_name}[{i}]")
                        
                        if chain:
                            and_chain = " && ".join(chain)
                            valid_chains_for_tap.append(f"(({and_chain}) && {tap})")
                        else:
                            valid_chains_for_tap.append(tap)
                            
                W_gap_0 = base_d_max_0 - base_d_min_0
                if min_rep_0 == 0 and (W_0 - W_gap_0) <= j <= W_0:
                    bypass_tap = f"{bypass_sr_name_0}_q[{j+1}]"
                    valid_chains_for_tap.append(bypass_tap)
            else:
                valid_chains_for_tap.append(tap)

            if valid_chains_for_tap:
                unique_chains = list(dict.fromkeys(valid_chains_for_tap))
                if len(unique_chains) > 1:
                    agg_terms_0.append("(" + " || ".join(unique_chains) + ")")
                else:
                    agg_terms_0.append(unique_chains[0])

        agg_expr_final = " || ".join(agg_terms_0) if agg_terms_0 else "1'b0"

        agg_final_name = f"{ns}_agg_final_{N}"
        lines.append(f"    logic {agg_final_name};")
        
        cond_sig_N = self.stages[N].cond_sig
        if cond_sig_N:
            lines.append(f"    assign {agg_final_name} = ({agg_expr_final}) && ({cond_sig_N});")
        else:
            lines.append(f"    assign {agg_final_name} = {agg_expr_final};")

        lines.append(f"    // 3. Match and Fail pipeline")
        prev_match = f"_a{self.assertion_id}_match_{N-1}" if N > 0 else self.start_signal
        max_N = self.stages[N].max_delay

        if max_N > 0:
            match_d_name: str = f"_a{self.assertion_id}_match_{N-1}_d" if N > 0 else f"_a{self.assertion_id}_start_d"
            lines.append(f"    logic [{max_N}:1] {match_d_name} = '0;")
            
            cond_sig: Optional[str] = self.stages[N].cond_sig
            active_rst_expr: str = f"({rst_expr}) || !({cond_sig})" if cond_sig else rst_expr
            
            ff_lines.append(f"        if ({active_rst_expr}) {match_d_name} <= '0;")
            
            if max_N == 1:
                ff_lines.append(f"        else {match_d_name} <= {prev_match};")
            else:
                slice_width: int = max_N - 1
                ff_lines.append(f"        else {match_d_name} <= {{{match_d_name}[{slice_width}:1], {prev_match}}};")

            delayed_match = f"{match_d_name}[{max_N}]"
        else:
            delayed_match = prev_match

        curr_match = f"_a{self.assertion_id}_match_{N}"
        lines.append(f"    logic {curr_match};")
        lines.append(f"    assign {curr_match} = {delayed_match} && {agg_final_name};")

        fail_sig = self.stages[N].fail_sig
        cond_sig = self.stages[N].cond_sig
        fail_expr = f"{delayed_match} && !{agg_final_name}"
        
        if max_N > 0 and cond_sig:
            match_d_name = f"_a{self.assertion_id}_match_{N-1}_d" if N > 0 else f"_a{self.assertion_id}_start_d"
            throughout_fail_expr = f"(| {match_d_name}) && !({cond_sig})"
            fail_expr = f"({fail_expr}) || ({throughout_fail_expr})"

        lines.append(f"    assign {fail_sig} = {fail_expr};")
        if N == len(self.stages) - 1:
            lines.append(f"    assign _a{self.assertion_id}_rhs_pass = {curr_match};")

        if ff_lines:
            lines.append(f"    always_ff @({self.clk}) begin")
            for ff_line in ff_lines:
                lines.append(ff_line)
            lines.append(f"    end")

        return lines



    def _generate_sequential_asserts(self) -> List[str]:
        if not self.generate_asserts: return []
        lines = []
        lines.append(f"    // --- Sequential Assertions Check ---")
        if self.ifdef_mode == 'disable':
            lines.append(f"    `ifndef DISABLE_{self.assertion_id}")
        elif self.ifdef_mode == 'enable':
            lines.append(f"    `ifdef ENABLE_{self.assertion_id}")
        lines.append(f"    always_ff @({self.clk}) begin")
        indent = "        "
        if self.rst:
            lines.append(f"{indent}if (!({self.rst})) begin")
            indent += "    "
        for stage in self.stages:
            lines.append(f"{indent}if ({stage.fail_sig}) begin")
            msg = f"Assertion Failed at Stage {stage.step_idx} (Deadline {stage.cumulative_max}). Time: %0t"
            lines.append(f"{indent}    assert(0) else $display(\"{msg}\", $time);")
            lines.append(f"{indent}end")
        if self.rst:
            indent = indent[:-4]
            lines.append(f"{indent}end")
        lines.append(f"    end")
        if self.ifdef_mode == 'disable':
            lines.append(f"    `endif // DISABLE_{self.assertion_id}")
        elif self.ifdef_mode == 'enable':
            lines.append(f"    `endif // ENABLE_{self.assertion_id}")

        return lines

    def generate_verilog(self) -> str:
        output = []
        output.extend(self.decl_lines)
        output.append("")
        output.extend(self.logic_lines)
        output.append("")
        for i in range(len(self.stages)):
            output.extend(self._generate_nested_loops_logic(i))
            output.append("")
        output.extend(self._generate_sequential_asserts())
        return "\n".join(output)

def generate_lookback_checker(assertion_id, steps, start_signal, clk, rst, local_vars=None, start_vars=None, generate_asserts=True, is_negated=False, design_signals_map=None, ifdef_mode='disable'):
    engine = LookbackEngine(assertion_id, steps, start_signal, clk, rst, local_vars, start_vars, generate_asserts, is_negated, design_signals_map, ifdef_mode)
    return engine.generate_verilog()