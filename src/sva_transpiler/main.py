import sys
import json
import argparse
import re
import os
from .verilog_builder import generate_verilog_code
from .utils import parse_expression, clean_symbol

class SvaTranspiler:
    def __init__(self, json_path, output_path, args):
        self.json_path = json_path
        self.output_path = output_path
        self.args = args
        
        # Internal State
        self.assertion_counter = 0
        self.module_insertions = {} 
        self.port_insertions = {}
        self.lines_to_comment = [] 
        self.sv_path = None
        self.sv_lines = [] 
        
        # Stats
        self.stats = {
            'assert': 0,
            'cover': 0,
            'assume': 0,
            'errors': 0
        }
        

    def run(self):
        try:
            with open(self.json_path, 'r') as f:
                ast_root = json.load(f)
        except Exception as e:
            print(f"Fatal Error: Cannot read JSON file: {e}")
            sys.exit(1)

        self.sv_path = self._discover_sv_filename(ast_root)
        if not self.sv_path:
            print("Warning: Cannot determine source .sv file from JSON.")
            sys.exit(1)
        else:
            print(f"Info: Source file identified: {self.sv_path}")
            self.load_source_lines()


        if 'design' in ast_root:
            self.traverse_members(ast_root['design'].get('members', []))
        elif 'members' in ast_root: 
            self.traverse_members(ast_root['members'])
        else:
            self.find_assertions_recursive(ast_root)

        self.write_patched_output()
        self.print_summary()

    def _discover_sv_filename(self, node):
        if 'definitions' in node:
            for definition in node['definitions']:
                if 'source_file' in definition:
                    return definition['source_file']
        if 'design' in node and 'members' in node['design']:
            for member in node['design']['members']:
                if 'source_file' in member:
                    return member['source_file']
        return None

    def load_source_lines(self):
        if not os.path.exists(self.sv_path):
            print(f"Error: Source file '{self.sv_path}' not found.")
            sys.exit(1)
        try:
            with open(self.sv_path, 'r') as f:
                self.sv_lines = f.read().splitlines()
        except Exception as e:
            print(f"Error reading source: {e}")
            sys.exit(1)

    # --- Ricerca Bounding Box Porte ---
    def _find_port_insertion_point(self, ast_root: dict) -> tuple:
        """
        Identifica il punto esatto di iniezione per le nuove porte di uscita.
        
        L'algoritmo individua l'ultima porta dichiarata nell'AST, cerca il primo 
        punto e virgola ';' successivo (che chiude l'header del modulo) e risale 
        all'indietro per trovare la parentesi ')' che chiude la lista delle porte.
        Supporta ritorni a capo tra ')' e ';'.

        Args:
            ast_root (dict): Il nodo radice dell'AST JSON.

        Returns:
            tuple: (indice_riga, indice_carattere, modulo_ha_porte)
        """
        max_line = 0
        has_ports = False
        
        # 1. Attraversamento dell'AST per trovare la riga dell'ultima porta nota
        def _walk(n):
            nonlocal max_line, has_ports
            if isinstance(n, dict):
                if n.get('kind') == 'Port':
                    has_ports = True
                    line = n.get('source_line', 0)
                    if line > max_line: max_line = line
                for v in n.values(): _walk(v)
            elif isinstance(n, list):
                for item in n: _walk(item)
        _walk(ast_root)
        
        # 2. Ricerca del delimitatore ';' dell'header del modulo
        # Partiamo dall'indice corrispondente alla riga dell'ultima porta
        start_search_idx = max(0, max_line - 1)
        for i in range(start_search_idx, len(self.sv_lines)):
            # Rimuoviamo i commenti per evitare falsi positivi
            line_content = self.sv_lines[i].split('//')[0]
            
            if ';' in line_content:
                # 3. Backtracking per trovare la parentesi ')' della lista porte
                # Risaliamo dalla riga del ';' fino alla riga dell'ultima porta
                for j in range(i, start_search_idx - 1, -1):
                    search_line = self.sv_lines[j].split('//')[0]
                    
                    # Se siamo sulla stessa riga del ';', cerchiamo solo prima di esso
                    end_offset = search_line.find(';') if j == i else len(search_line)
                    r_paren_idx = search_line.rfind(')', 0, end_offset)
                    
                    if r_paren_idx != -1:
                        # Ritorna la posizione esatta della parentesi chiusa
                        return j, r_paren_idx, has_ports
                # Se troviamo il ';' ma non la ')', interrompiamo per evitare 
                # di cercare nella logica interna del modulo
                break
        
        return -1, -1, False    
    # --- ---

    def _find_start_line_by_keyword(self, end_line, keyword):
        if not end_line: return None
        current_idx = end_line - 1
        while current_idx >= 0:
            line = self.sv_lines[current_idx].strip()
            if keyword in line and not line.startswith("//"):
                return current_idx + 1
            current_idx -= 1
        return end_line

    def find_closing_line(self, start_line, keyword):
        current_idx = start_line - 1
        while current_idx < len(self.sv_lines):
            line = self.sv_lines[current_idx]
            clean_line = line.split('//')[0].strip() 
            if keyword in clean_line:
                return current_idx + 1
            current_idx += 1
        return start_line

    def _extract_design_signals_from_ast(self, node):
        """
        Scansiona l'AST per identificare segnali NamedValue (Opzione A).
        Popola una mappa nome_segnale -> tipo_sv.
        """
        signals_map = {}
        def _walk(n):
            if not isinstance(n, dict): return
            
            # Identificazione precisa tramite AST
            if n.get('kind') == 'NamedValue':
                name = clean_symbol(n.get('symbol', ''))
                v_type = n.get('type')
                if name and v_type:
                    signals_map[name] = v_type
            
            # Esplorazione ricorsiva
            for key, val in n.items():
                if isinstance(val, dict):
                    _walk(val)
                elif isinstance(val, list):
                    for item in val:
                        _walk(item)        

        _walk(node)
        return signals_map


    def _make_default_context(self, parent_context=None):
        """
        Crea una copia del contesto lessicale dei default SVA.
        I default clocking / disable iff sono scope-local: un modulo non deve
        ereditare accidentalmente i default di un altro modulo visitato prima.
        """
        if parent_context is None:
            return {
                'default_clock': None,
                'default_reset': None,
            }

        return {
            'default_clock': parent_context.get('default_clock'),
            'default_reset': parent_context.get('default_reset'),
        }

    def _format_default_clocking(self, node):
        """Estrae il clock da un ClockingBlock default prodotto da slang."""
        event_node = node.get('event')
        if not isinstance(event_node, dict):
            return None

        edge = event_node.get('edge', '')
        expr = event_node.get('expr', {})
        clk_signal = parse_expression(expr)

        return f"{edge.lower().replace('edge', 'edge ')}{clk_signal}" if edge else clk_signal

    def _format_default_disable(self, node):
        """Estrae la condizione da un defaultDisable prodotto da slang."""
        expr_node = node.get('expr', {})
        expr_str = parse_expression(expr_node)

        if isinstance(expr_node, dict) and expr_node.get('kind') not in ('NamedValue', 'Identifier'):
            return f"({expr_str})"

        return expr_str

    def traverse_members(self, members, parent_label=None, current_port_point=None, default_context=None):
        if not isinstance(members, list):
            return

        # Copia locale: i default dichiarati in questo scope non devono mutare
        # il contesto del chiamante o di altri moduli/scope fratelli.
        scope_context = self._make_default_context(default_context)

        # Pre-pass sui membri immediati dello scope. Serve perché slang può
        # emettere defaultDisable dopo le assertion anche se nel sorgente è prima.
        for node in members:
            if not isinstance(node, dict):
                continue

            kind = node.get('kind', '')

            if kind == 'defaultDisable':
                scope_context['default_reset'] = self._format_default_disable(node)

            elif kind == 'ClockingBlock' and node.get('isDefault'):
                default_clock = self._format_default_clocking(node)
                if default_clock:
                    scope_context['default_clock'] = default_clock

        for node in members:
            self.process_node(node, parent_label, current_port_point, scope_context)



    def process_node(self, node, parent_label=None, current_port_point=None, default_context=None):
        """
        Visita un nodo dell'AST. Se individua un modulo, calcola il punto di
        iniezione porte e propaga il contesto lessicale dei default SVA.
        """
        if not isinstance(node, dict):
            return

        if default_context is None:
            default_context = self._make_default_context()

        kind = node.get('kind', '')

        # --- SCOPE DEL MODULO: Identificazione locale del punto di inserimento ---
        new_port_point = current_port_point

        # Se entriamo in un'istanza o nel corpo di un modulo, calcoliamo il punto
        # di iniezione specifico per le porte checker.
        if kind in ('Instance', 'InstanceBody'):
            if self.args.checker:
                new_port_point = self._find_port_insertion_point(node)

        current_label = parent_label

        if kind == 'StatementBlock' and 'name' in node:
            current_label = node['name']

        if 'block' in node:
            val = node['block']
            if isinstance(val, str):
                parts = val.split()
                if len(parts) > 0 and not parts[-1].isdigit():
                    current_label = parts[-1]

        if kind == 'defaultDisable':
            expr_node = node.get('expr', {})
            e = node.get('source_line_end') or expr_node.get('source_line_end')
            s = self._find_start_line_by_keyword(e, "default")
            if s and e:
                self.lines_to_comment.append((s, e))
            return

        if kind == 'ClockingBlock':
            s = node.get('source_line_start') or node.get('source_line')
            e = node.get('source_line_end')
            if s and not e:
                e = self.find_closing_line(s, "endclocking")
            if s and e:
                self.lines_to_comment.append((s, e))
            return

        if kind == 'Sequence':
            s = node.get('source_line_start') or node.get('source_line')
            e = node.get('source_line_end')
            if s and not e:
                e = self.find_closing_line(s, "endsequence")
            if s and e:
                self.lines_to_comment.append((s, e))
            return

        if kind == 'Property':
            s = node.get('source_line_start') or node.get('source_line')
            e = node.get('source_line_end')
            if s and not e:
                e = self.find_closing_line(s, "endproperty")
            if s and e:
                self.lines_to_comment.append((s, e))
            return

        if kind == 'ConcurrentAssertion':
            self.handle_assertion(
                node,
                current_label,
                current_port_point=new_port_point,
                default_context=default_context,
            )
            return

        # Propaghiamo il contesto del modulo ai nodi figli.
        # Quando incontriamo una lista members, usiamo traverse_members per fare
        # il pre-pass scoped dei default clocking / default disable iff.
        if 'members' in node:
            self.traverse_members(
                node['members'],
                current_label,
                new_port_point,
                default_context,
            )

        if 'body' in node:
            body = node['body']

            if isinstance(body, list):
                for b in body:
                    self.process_node(
                        b,
                        current_label,
                        new_port_point,
                        default_context,
                    )

            elif isinstance(body, dict):
                self.process_node(
                    body,
                    current_label,
                    new_port_point,
                    default_context,
                )


    def _strip_assertion_wrappers(self, node):
        clk, rst, core_node = None, None, node
        while isinstance(core_node, dict):
            kind = core_node.get('kind', '')
            if kind == 'ConcurrentAssertion':
                core_node = core_node.get('propertySpec', {})
                continue
            if kind == 'Clocking' or 'clocking' in core_node:
                if not clk: clk = self._find_clock_recursive(core_node)
                core_node = core_node.get('expr') or core_node.get('body')
                continue
            if kind == 'DisableIff' or 'disableIff' in core_node:
                if not rst: rst = self._find_reset_recursive(core_node)
                core_node = core_node.get('expr') or core_node.get('body')
                continue
            if kind == 'AssertionInstance':
                core_node = core_node.get('body')
                continue
            if kind in ('Simple', 'Parenthesized'):
                core_node = core_node.get('expr') or core_node.get('operand')
                continue
            break
        return clk, rst, core_node

    def _find_clock_recursive(self, node):
        if not isinstance(node, dict): return None
        if 'clocking' in node:
            clk_node = node['clocking']
            edge = clk_node.get('edge', '')
            expr = clk_node.get('expr', {})
            clk_sig = parse_expression(expr)
            return f"{edge.lower().replace('edge', 'edge ')}{clk_sig}" if edge else clk_sig
        if 'expr' in node and isinstance(node['expr'], dict):
            res = self._find_clock_recursive(node['expr'])
            if res: return res
        return None

    def _find_reset_recursive(self, node):
        if not isinstance(node, dict): return None
        kind = node.get('kind', '')
        def wrap_if_complex(expr_node):
            if not isinstance(expr_node, dict): return parse_expression(expr_node)
            expr_str = parse_expression(expr_node)
            return f"({expr_str})" if expr_node.get('kind') not in ('NamedValue', 'Identifier') else expr_str

        if kind == 'DisableIff' and 'condition' in node:
            return wrap_if_complex(node['condition'])
        if 'disableIff' in node and 'condition' in node['disableIff']:
            return wrap_if_complex(node['disableIff']['condition'])
        if 'expr' in node and isinstance(node['expr'], dict):
            res = self._find_reset_recursive(node['expr'])
            if res: return res
        return None

    def extract_local_vars_from_ast(self, assertion_node):
        local_vars_def = {}
        try:
            def _walk_for_vars(n):
                if not isinstance(n, dict): return
                if 'localVars' in n:
                    for var in n['localVars']:
                        name, v_type = var.get('name'), var.get('type')
                        if name and v_type: local_vars_def[name] = v_type
                
                # Esplorazione ricorsiva universale senza chiavi hardcoded
                for key, val in n.items():
                    if isinstance(val, dict):
                        _walk_for_vars(val)
                    elif isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict):
                                _walk_for_vars(item)
                                
            _walk_for_vars(assertion_node)
        except Exception: return {}
        return local_vars_def


    def handle_assertion(self, node, label, current_port_point=None, default_context=None):
        self.assertion_counter += 1
        if 'label' in node:
            l = node['label']
            if isinstance(l, dict) and 'name' in l: label = l['name']
            elif isinstance(l, str): label = l
        final_label = label if label else f"assert_{self.assertion_counter}"

        scope_context = self._make_default_context(default_context)
        default_clock = scope_context.get('default_clock')
        default_reset = scope_context.get('default_reset')

        clk_local, rst_local, core_node = self._strip_assertion_wrappers(node)
        clk = clk_local or default_clock
        rst = rst_local or default_reset

        if not clk:
            print(f"ERROR: Assertion '{final_label}' skipped. No clock found.")
            self.stats['errors'] += 1
            return 


        # --- SIGNAL DISCOVERY & VALIDATION ---
        design_signals_map = self._extract_design_signals_from_ast(core_node)
        for name, v_type in design_signals_map.items():
            if 'logic' not in v_type.lower():
                 print(f"ERROR: Assertion '{final_label}' uses unsupported type '{v_type}' for signal '{name}'. Only 'logic' and logic arrays are supported.")
                 self.stats['errors'] += 1
                 return

        # --- LOCAL VARS DISCOVERY & VALIDATION ---
        # Estraiamo le variabili locali qui per validarne i tipi prima di generare l'hardware.
        local_vars_def = self.extract_local_vars_from_ast(node)
        for name, v_type in local_vars_def.items():
            if 'logic' not in v_type.lower():
                 print(f"ERROR: Assertion '{final_label}' uses unsupported local variable type '{v_type}' for '{name}'. Only 'logic' and logic arrays are supported.")
                 self.stats['errors'] += 1
                 return


        # --- TEMPORAL OPERATORS VALIDATION ---
        # Gli operatori temporali SVA (come $past) vengono campionati globalmente ad ogni ciclo di clock.
        # Le variabili locali, invece, esistono solo nel contesto dinamico e frammentato dei registri 
        # di pipeline temporali. Pertanto, blocchiamo l'uso di $past() su variabili locali.
        if local_vars_def:
            def check_temporal_args(n):
                if not isinstance(n, dict): return
                kind = n.get('kind', '')
                
                if kind in ('Invocation', 'Call'):
                    name_str = ""
                    if 'target' in n and isinstance(n['target'], dict):
                        name_str = str(n['target'].get('name', n['target'].get('symbol', '')))
                    elif 'subroutine' in n:
                        sub = n['subroutine']
                        name_str = str(sub.get('name', sub)) if isinstance(sub, dict) else str(sub)
                    
                    if any(x in name_str for x in ['$past', '$rose', '$fell', '$stable', '$changed']):
                        args = n.get('arguments', [])
                        if args:
                            arg_expr = args[0].get('expr', args[0])
                            try:
                                arg_str = str(parse_expression(arg_expr)) 
                                for l_var in local_vars_def:
                                    if re.search(r'\b' + re.escape(l_var) + r'\b', arg_str):
                                        raise ValueError(f"Applying temporal operators ({name_str.strip()}) to SVA local variable '{l_var}' is not supported.")
                            except ValueError as e:
                                if "Applying temporal operators" in str(e): raise e
                
                for v in n.values():
                    if isinstance(v, dict): check_temporal_args(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict): check_temporal_args(item)
                            
            try:
                check_temporal_args(core_node)
            except ValueError as e:
                print(f"ERROR: Assertion '{final_label}' - {e}")
                self.stats['errors'] += 1
                return


        s = node.get('source_line_start') or node.get('source_line')
        e = node.get('source_line_end') or node.get('propertySpec', {}).get('source_line_end')
        if s and not e: e = self.find_closing_line(s, ";")
        if s and e: self.lines_to_comment.append((s, e))

        ast_kind = node.get('assertionKind', 'Assert')
        keyword = ast_kind.lower().replace('property', '').replace('assertion', '').strip()

        # ---  Filtraggio Cover ---
        # I cover points sono utili in simulazione/formal per tracciare il raggiungimento di stati,
        # ma in un hardware checker sintetizzabile pilotano logica inutile che verrebbe ottimizzata via.
        if getattr(self.args, 'checker', False) and keyword == 'cover':
            print(f"WARNING: L'asserzione '{final_label}' è di tipo 'cover'. "
                  f"In modalità --checker i cover vengono ignorati per evitare sintesi di logica inutile.")
            # Aggiorniamo le statistiche (opzionale ma pulito)
            self.stats['cover_skipped'] = self.stats.get('cover_skipped', 0) + 1
            return # Interrompiamo l'elaborazione di questo nodo
        # ---  ---

        # --- INIZIO BLOCCO VERBOSE ---
        if not getattr(self.args, 'quiet', False):
            label_str = label if label else "NOT FOUND"
            
             # Risoluzione provenienza Clock
            if clk_local:
                clk_str = f"{clk_local} (local)"
            elif default_clock:
                clk_str = f"{default_clock} (default clocking)"
            else:
                clk_str = "NOT FOUND"
            
            # Risoluzione provenienza Reset
            if rst_local:
                rst_str = f"{rst_local} (disable iff)"
            elif default_reset:
                rst_str = f"{default_reset} (default disable)"
            else:
                rst_str = "NOT FOUND"

            # Formattazione Variabili Locali
            if local_vars_def:
                vars_str = ", ".join([f"{v_type} {v_name}" for v_name, v_type in local_vars_def.items()])
            else:
                vars_str = "NONE"
                
            line_str = str(s) if s else "UNKNOWN"

            print(f"Assertion {self.assertion_counter}:")
            print(f"  - Label: {label_str}")
            print(f"  - Type: {keyword}")
            print(f"  - Clock: {clk_str}")
            print(f"  - Reset: {rst_str}")
            print(f"  - Local Vars: {vars_str}")
            print(f"  - Source Line: {line_str}\n")
        # --- FINE BLOCCO VERBOSE ---

        try:
            result = generate_verilog_code(
                assertion_id=self.assertion_counter, 
                clk=clk, rst=rst, node=core_node, label=final_label,
                ifdef_mode=self.args.ifdef_mode,
                keyword=keyword, 
                assert_action=self.args.assert_action,
                cover_action=self.args.cover_action,
                local_vars_def=local_vars_def,
                rhs_goto_watchdog=self.args.rhs_goto_watchdog,
                design_signals_map=design_signals_map,
                is_checker_mode=getattr(self.args, 'checker', False),
                generate_pass=getattr(self.args, 'pass_opt', False)                
            )

            # --- INIZIO INSERIMENTO FASE 3: Unpacking e Dispatch ---
            # Polimorfismo difensivo: garantisce retrocompatibilità con uscite premature
            if isinstance(result, tuple):
                code, new_ports = result
            else:
                code, new_ports = result, []

            if code.strip().startswith("// ERROR"): 
                self.stats['errors'] += 1
            else: 
                self.stats[keyword] = self.stats.get(keyword, 0) + 1
                
                # Iniezione Logica 
                endmodule_line = self.find_closing_line(s, "endmodule")
                insert_idx = endmodule_line - 1
                
                if insert_idx not in self.module_insertions:
                    self.module_insertions[insert_idx] = []
                self.module_insertions[insert_idx].append(code)
                
                # Iniezione Porte locale al modulo
                if new_ports and current_port_point and current_port_point[0] != -1:
                    if current_port_point not in self.port_insertions:
                        self.port_insertions[current_port_point] = []
                    self.port_insertions[current_port_point].extend(new_ports)

        except Exception as e:
            print(f"ERROR: Assertion '{final_label}' failed during generation: {e}")
            self.stats['errors'] += 1


    def find_assertions_recursive(self, node, label=None):
        if isinstance(node, dict):
            self.process_node(node, label, None)
            for k, v in node.items():
                if isinstance(v, (dict, list)): self.find_assertions_recursive(v, label)
        elif isinstance(node, list):
            for item in node: self.find_assertions_recursive(item, label)

    def write_patched_output(self) -> None:
        """
        Esegue il patching del file sorgente in tre fasi distinte per mantenere 
        la coerenza degli indici riga calcolati dallo AST.
        """
        output_lines = list(self.sv_lines)

        # FASE 1: COMMENTO (PRIMA OPERAZIONE)
        # Poiché il commento non aggiunge nuove righe, non invalida gli indici successivi.
        lines_idx_to_comment = set()
        for start, end in self.lines_to_comment:
            for i in range(start - 1, end):
                if 0 <= i < len(output_lines): 
                    lines_idx_to_comment.add(i)
        
        for i in sorted(lines_idx_to_comment):
            if not output_lines[i].strip().startswith("//"):
                output_lines[i] = "// [SVA-DISABLED] " + output_lines[i]

        # 2. UNIFICAZIONE TASK DI INIEZIONE
        # Creiamo una lista di tuple (indice_riga, priorità, dati)
        # Priorità 0 per la Logica (Checkers), 1 per le Porte. 
        # Sulla stessa riga, processiamo prima i port (sub-line) poi la logica.
        all_injections = []

        # Aggiungiamo i blocchi di logica
        for line_idx, blocks in self.module_insertions.items():
            chunk = ["", "    // === TRANSPILED SVA CHECKERS ==="]
            for block in blocks:
                chunk.extend([block, ""])
            all_injections.append((line_idx, 0, chunk))

        # Aggiungiamo le porte
        for (l_idx, c_idx, has_ports), ports in self.port_insertions.items():
            all_injections.append((l_idx, 1, (c_idx, has_ports, ports)))

        # 3. APPLICAZIONE BOTTOM-UP RIGOROSA
        # Ordiniamo per riga decrescente.
        for line_idx, p_type, data in sorted(all_injections, key=lambda x: x[0], reverse=True):
            if p_type == 0:  # Iniezione Logica (Intere righe)
                output_lines = output_lines[:line_idx] + data + output_lines[line_idx:]
            
            else:  # Iniezione Porte (Sub-line patching)
                c_idx, has_ports, ports = data
                port_chunk = "," if has_ports else ""
                port_chunk += "\n"
                for i, p in enumerate(ports):
                    suffix = "" if i == len(ports) - 1 else ","
                    port_chunk += f"    {p}{suffix}\n"
                
                # Patching chirurgico della riga alla posizione del carattere ')'
                original_line = output_lines[line_idx]
                output_lines[line_idx] = original_line[:c_idx] + port_chunk + original_line[c_idx:]

        # SCRITTURA FINALE
        try:
            with open(self.output_path, 'w') as f:
                f.write("\n".join(output_lines))
        except IOError as e:
            print(f"Error writing output file: {e}")



    def print_summary(self):
        print("\n" + "="*40 + "\n          TRANSPILER SUMMARY          \n" + "="*40)
        print(f" Assertions translated : {self.stats.get('assert', 0)}")
        print(f" Covers translated     : {self.stats.get('cover', 0)}")
        if getattr(self.args, 'checker', False):
            print(f" Covers skipped (chk)  : {self.stats.get('cover_skipped', 0)}") 
        print(f" Assumptions translated: {self.stats.get('assume', 0)}")
        print("-" * 40 + f"\n ERRORS (Skipped)      : {self.stats['errors']}\n" + "-" * 40)
        total_gen = sum(len(blocks) for blocks in self.module_insertions.values())
        print(f" TOTAL GENERATED       : {total_gen}\n" + "="*40 + "\n")        


import argparse

def main() -> None:
    # Utilizziamo RawTextHelpFormatter per rispettare i ritorni a capo (\n) nelle descrizioni
    parser = argparse.ArgumentParser(
        description="JSON AST to Verilog Transpiler",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument(
        "json_file", 
        help="Path to the input Slang AST JSON file.\n"
             "slang should be invoked as:\n"
             "slang -ast-json-source-info -ast-json  JSON_FILE  VERILOG_FILE"
    )
    
    parser.add_argument(
        "-o", "--output", 
        required=True, 
        help="Path to the output Verilog file where the patched RTL will be saved."
    )
    
    parser.add_argument(
        "--assert-action", 
        choices=['none', 'display', 'error'], 
        default='none',
        help="Action to perform upon assertion failure:\n"
             "  none    : Only generate the assert statement (default)\n"
             "  display : Append a $display statement with failure time\n"
             "  error   : Append an $error statement with failure time"
    )
    
    parser.add_argument(
        "--cover-action", 
        choices=['none', 'display'], 
        default='none',
        help="Action to perform upon cover property match:\n"
             "  none    : Only generate the cover statement (default)\n"
             "  display : Append a $display statement logging the match time"
    )
    
    parser.add_argument(
        "--ifdef-mode", 
        choices=['none', 'disable', 'enable'], 
        default='none', 
        help="Control how generated assertions are wrapped with preprocessor macros:\n"
             "  disable : Wrap with `ifndef DISABLE_<label> (default)\n"
             "  enable  : Wrap with `ifdef ENABLE_<label>\n"
             "  none    : Do not generate any macro wrappers"
    )
    
    parser.add_argument(
        "--rhs-goto-watchdog", 
        default="16",
        help="Timeout in clock cycles to prevent infinite stalls in Consequent Goto [->N] sequences.\n"
             "Accepts an integer value or 'none' to completely disable the watchdog timer.\n"
             "(default: 16)"
    )
    

    parser.add_argument(
        "-q", "--quiet", 
        action="store_true", 
        help="Suppresses detailed per-assertion output.\n"
             "Only critical errors and the final summary will be printed."
    )
    parser.add_argument(
        "--checker", 
        action="store_true", 
        help="Enable Hardware Checker mode.\n"
             "Generates synthesizable output ports (<label>_assert_fail, <label>_assume_fail)\n"
             "instead of simulation constructs. Incompatible with --assert-action,\n"
             "--cover-action, and --ifdef-mode."
    )

    parser.add_argument(
        "--pass", 
        dest="pass_opt", # Usiamo pass_opt perché 'pass' è parola riservata in Python
        action="store_true", 
        help="In --checker mode, generates a <label>_pass output signal that pulses \n"
             "when the assertion is verified non-vacuously."
    )
    args = parser.parse_args()

    # --- Validazione Mutua Esclusione ---
    # In modalità hardware checker, i costrutti per la simulazione testuale non hanno senso logico.
    # Blocchiamo l'esecuzione se l'utente tenta di forzare configurazioni contrastanti.
    if args.checker:
        if args.assert_action != 'none' or args.cover_action != 'none' or args.ifdef_mode != 'none':
            parser.error(
                "Le opzioni --assert-action, --cover-action e --ifdef-mode "
                "sono incompatibili con --checker.\n"
                "In modalità checker, il tool genera esclusivamente hardware sintetizzabile."
            )
        # Verifica che --pass sia usato solo con --checker
        if args.pass_opt and not args.checker:
            parser.error("L'opzione --pass può essere attivata solo se è specificato --checker.")
    # ---  ---


    transpiler = SvaTranspiler(args.json_file, args.output, args)
    transpiler.run()


if __name__ == "__main__":
    main()