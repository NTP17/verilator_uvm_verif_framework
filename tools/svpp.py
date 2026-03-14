#!/usr/bin/env python3
"""
svpp.py — SystemVerilog Preprocessor for Verilator

Transforms native SystemVerilog covergroup and SVA concurrent assertion
syntax into DPI-C calls that the sva_engine C++ backend can evaluate.

Usage:
    python3 svpp.py <input.sv> [-o <output.sv>]
    python3 svpp.py <dir>  [-o <outdir>]    # batch mode

The preprocessor:
  1. Parses covergroup ... endgroup blocks
  2. Parses assert/cover/assume property (...) blocks
  3. Emits replacement SV code with DPI-C calls
  4. Preserves everything else verbatim
"""

import argparse
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# =====================================================================
#  Data Structures
# =====================================================================

@dataclass
class BinDef:
    name: str
    kind: str = "value"  # value, transition, wildcard, illegal, ignore, default
    ranges: List[Tuple[int, int]] = field(default_factory=list)
    values: List[int] = field(default_factory=list)
    transition_seq: List[List[int]] = field(default_factory=list)
    wc_mask: int = 0
    wc_pattern: int = 0

@dataclass
class CoverpointDef:
    name: str
    expr: str  # the coverpoint expression (e.g., "txn.addr")
    bins: List[BinDef] = field(default_factory=list)
    auto_bin_max: int = 64

@dataclass
class CrossDef:
    name: str
    coverpoints: List[str] = field(default_factory=list)  # coverpoint names

@dataclass
class CovergroupDef:
    name: str
    clock_event: str = ""  # e.g., "posedge clk"
    coverpoints: List[CoverpointDef] = field(default_factory=list)
    crosses: List[CrossDef] = field(default_factory=list)
    options: dict = field(default_factory=dict)
    start_line: int = 0
    end_line: int = 0

@dataclass
class SvaAssertionDef:
    name: str
    kind: str  # "assert", "cover", "assume"
    clock_event: str = ""
    property_expr: str = ""
    signals: List[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    source_file: str = ""
    fail_message: str = ""  # custom message from 'else' clause

# =====================================================================
#  Parser Helpers
# =====================================================================

def strip_comments(text):
    """Remove // and /* */ comments, preserving line structure."""
    result = []
    in_block = False
    i = 0
    while i < len(text):
        if in_block:
            if text[i:i+2] == '*/':
                in_block = False
                i += 2
                continue
            if text[i] == '\n':
                result.append('\n')
            i += 1
        else:
            if text[i:i+2] == '//':
                # Skip to end of line
                while i < len(text) and text[i] != '\n':
                    i += 1
            elif text[i:i+2] == '/*':
                in_block = True
                i += 2
            else:
                result.append(text[i])
                i += 1
    return ''.join(result)


def find_matching_paren(text, start):
    """Find the index of the closing paren/brace matching the one at start."""
    openers = {'(': ')', '[': ']', '{': '}'}
    closer = openers.get(text[start])
    if not closer:
        return start
    depth = 1
    i = start + 1
    while i < len(text) and depth > 0:
        if text[i] == text[start]:
            depth += 1
        elif text[i] == closer:
            depth -= 1
        i += 1
    return i - 1


def extract_signals_from_expr(expr):
    """Extract signal names from a boolean/SV expression."""
    # Remove string literals
    expr = re.sub(r'"[^"]*"', '', expr)
    # Remove SV keywords that look like identifiers
    keywords = {
        'disable', 'iff', 'or', 'and', 'not', 'intersect', 'within',
        'throughout', 'first_match', 'if', 'else', 'int', 'logic',
        'bit', 'reg', 'wire', 'input', 'output', 'inout', 'begin',
        'end', 'module', 'endmodule', 'function', 'endfunction',
        'task', 'endtask', 'return', 'void', 'null',
    }
    # Find all identifiers (including hierarchical like txn.addr)
    idents = re.findall(r'\b([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*(?:\[\d+(?::\d+)?\])?)\b', expr)
    signals = []
    for ident in idents:
        base = ident.split('.')[0].split('[')[0]
        if base not in keywords and not base.startswith('$'):
            if ident not in signals:
                signals.append(ident)
    return signals


def parse_sv_literal(text):
    """Parse a SystemVerilog numeric literal and return its integer value."""
    text = text.strip()
    # SV-style: <width>'<base><digits>  e.g., 4'b0001, 8'hFF, 32'd10
    m = re.match(r"(\d+)'([bBhHoOdD])([0-9a-fA-F_?xXzZ]+)", text)
    if m:
        base_ch = m.group(2).lower()
        digits = m.group(3).replace('_', '')
        if base_ch == 'b':
            return int(digits.replace('?', '0').replace('x', '0').replace('z', '0'), 2)
        elif base_ch == 'h':
            return int(digits.replace('?', '0').replace('x', '0').replace('z', '0'), 16)
        elif base_ch == 'o':
            return int(digits, 8)
        elif base_ch == 'd':
            return int(digits)
    # C-style hex: 0xFF
    m = re.match(r'0[xX]([0-9a-fA-F]+)', text)
    if m:
        return int(m.group(1), 16)
    # Plain decimal
    m = re.match(r'-?\d+', text)
    if m:
        return int(m.group(0))
    return None


def parse_value_list(text):
    """Parse a bin value list like {0, [1:3], 5, [10:20], 4'b0001}."""
    ranges = []
    values = []
    text = text.strip().strip('{}').strip()
    if not text:
        return ranges, values

    parts = split_top_level(text, ',')
    for part in parts:
        part = part.strip()
        # Range: [lo:hi]
        m = re.match(r'\[\s*(.+?)\s*:\s*(.+?)\s*\]', part)
        if m:
            lo = parse_sv_literal(m.group(1))
            hi = parse_sv_literal(m.group(2))
            if lo is not None and hi is not None:
                ranges.append((lo, hi))
                continue
        # Try as a single SV literal
        val = parse_sv_literal(part)
        if val is not None:
            ranges.append((val, val))
            values.append(val)
            continue

    return ranges, values


def parse_transition_list(text):
    """Parse transition bin: (0 => 1 => 2), (3 => 4)."""
    text = text.strip().strip('{}').strip()
    transitions = []
    # Split by ),  (
    seqs = re.findall(r'\(([^)]+)\)', text)
    for seq in seqs:
        vals = [int(v.strip()) for v in seq.split('=>')]
        transitions.append(vals)
    return transitions


def split_top_level(text, sep=','):
    """Split text by separator, respecting parentheses and brackets."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current).strip())
    return parts


# =====================================================================
#  Covergroup Parser
# =====================================================================

def parse_covergroup(lines, start_idx):
    """Parse a covergroup block starting at line start_idx."""
    # First line: covergroup <name> @(<event>) ;
    first_line = lines[start_idx].strip()
    m = re.match(
        r'covergroup\s+(\w+)\s*(?:@\s*\(([^)]*)\))?\s*;',
        first_line
    )
    if not m:
        return None, start_idx

    cg = CovergroupDef(name=m.group(1), start_line=start_idx)
    if m.group(2):
        cg.clock_event = m.group(2).strip()

    # Collect body until endgroup
    i = start_idx + 1
    body_lines = []
    while i < len(lines):
        stripped = lines[i].strip()
        if re.match(r'endgroup\b', stripped):
            cg.end_line = i
            break
        body_lines.append(lines[i])
        i += 1
    else:
        cg.end_line = len(lines) - 1

    # Join body and parse
    body = '\n'.join(body_lines)
    body_clean = strip_comments(body)

    # Parse options
    for m in re.finditer(r'option\.(\w+)\s*=\s*(\S+)\s*;', body_clean):
        cg.options[m.group(1)] = m.group(2)

    # Parse coverpoints — find them by scanning for 'coverpoint' keyword
    # and matching balanced braces
    cp_positions = []
    for m in re.finditer(r'(?:(\w+)\s*:\s*)?coverpoint\s+', body_clean):
        cp_positions.append(m)

    for m in cp_positions:
        cp_name = m.group(1) or f"cp_{len(cg.coverpoints)}"
        rest = body_clean[m.end():]

        # Extract expression: everything up to { or ;
        expr_end = len(rest)
        for j, ch in enumerate(rest):
            if ch in '{;':
                expr_end = j
                break

        cp_expr = rest[:expr_end].strip()
        cp = CoverpointDef(name=cp_name, expr=cp_expr)

        if expr_end < len(rest) and rest[expr_end] == '{':
            # Find matching }
            brace_start = expr_end
            brace_depth = 1
            k = brace_start + 1
            while k < len(rest) and brace_depth > 0:
                if rest[k] == '{': brace_depth += 1
                elif rest[k] == '}': brace_depth -= 1
                k += 1
            bin_body = rest[brace_start + 1:k - 1]
            parse_bins(cp, bin_body)
        else:
            cp.auto_bin_max = int(cg.options.get('auto_bin_max', '64'))

        cg.coverpoints.append(cp)

    # Parse crosses
    # Pattern: [label:] cross <cp1>, <cp2> [, <cp3>] ;
    cross_pattern = re.compile(
        r'(?:(\w+)\s*:\s*)?cross\s+(\w+(?:\s*,\s*\w+)+)\s*;'
    )
    for m in cross_pattern.finditer(body_clean):
        x_name = m.group(1) or f"cross_{len(cg.crosses)}"
        cp_names = [n.strip() for n in m.group(2).split(',')]
        cg.crosses.append(CrossDef(name=x_name, coverpoints=cp_names))

    return cg, cg.end_line


def parse_bins(cp, bin_body):
    """Parse bin definitions inside a coverpoint."""
    # Split by semicolons
    stmts = [s.strip() for s in bin_body.split(';') if s.strip()]

    for stmt in stmts:
        # option.auto_bin_max
        m = re.match(r'option\.auto_bin_max\s*=\s*(\d+)', stmt)
        if m:
            cp.auto_bin_max = int(m.group(1))
            continue

        # illegal_bins <name> = ...
        m = re.match(r'illegal_bins\s+(\w+)\s*=\s*(.+)', stmt, re.DOTALL)
        if m:
            bdef = BinDef(name=m.group(1), kind="illegal")
            bdef.ranges, bdef.values = parse_value_list(m.group(2))
            cp.bins.append(bdef)
            continue

        # ignore_bins <name> = ...
        m = re.match(r'ignore_bins\s+(\w+)\s*=\s*(.+)', stmt, re.DOTALL)
        if m:
            bdef = BinDef(name=m.group(1), kind="ignore")
            bdef.ranges, bdef.values = parse_value_list(m.group(2))
            cp.bins.append(bdef)
            continue

        # wildcard bins <name> = ...
        m = re.match(r'wildcard\s+bins\s+(\w+)\s*=\s*(.+)', stmt, re.DOTALL)
        if m:
            bdef = BinDef(name=m.group(1), kind="wildcard")
            # Parse wildcard value: e.g., 4'b1??0
            val_str = m.group(2).strip().strip('{}').strip()
            wm = re.match(r"(\d+)'b([01?]+)", val_str)
            if wm:
                mask = 0
                pattern = 0
                for ch in wm.group(2):
                    mask <<= 1
                    pattern <<= 1
                    if ch == '1':
                        mask |= 1
                        pattern |= 1
                    elif ch == '0':
                        mask |= 1
                    # ? => mask bit 0 (don't care)
                bdef.wc_mask = mask
                bdef.wc_pattern = pattern
            cp.bins.append(bdef)
            continue

        # bins <name>[] = ... (array of individual bins)
        m = re.match(r'bins\s+(\w+)\s*\[\]\s*=\s*(.+)', stmt, re.DOTALL)
        if m:
            bdef = BinDef(name=m.group(1), kind="value")
            val_text = m.group(2).strip()
            # Check for transitions: (a => b)
            if '=>' in val_text:
                bdef.kind = "transition"
                bdef.transition_seq = parse_transition_list(val_text)
            else:
                bdef.ranges, bdef.values = parse_value_list(val_text)
                # For array bins, create individual bins per value
                if bdef.values and len(bdef.values) > 1:
                    for v in bdef.values:
                        sub = BinDef(
                            name=f"{m.group(1)}[{v}]",
                            kind="value",
                            ranges=[(v, v)]
                        )
                        cp.bins.append(sub)
                    continue
            cp.bins.append(bdef)
            continue

        # bins <name> = default;
        m = re.match(r'bins\s+(\w+)\s*=\s*default', stmt)
        if m:
            cp.bins.append(BinDef(name=m.group(1), kind="default"))
            continue

        # bins <name> = { ... } with transitions
        m = re.match(r'bins\s+(\w+)\s*=\s*(.+)', stmt, re.DOTALL)
        if m:
            bdef = BinDef(name=m.group(1))
            val_text = m.group(2).strip()
            if '=>' in val_text:
                bdef.kind = "transition"
                bdef.transition_seq = parse_transition_list(val_text)
            else:
                bdef.kind = "value"
                bdef.ranges, bdef.values = parse_value_list(val_text)
            cp.bins.append(bdef)
            continue


# =====================================================================
#  SVA Parser
# =====================================================================

def parse_sva_assertion(lines, start_idx, filename=""):
    """Parse an SVA assert/cover/assume property block."""
    line = lines[start_idx].strip()

    # Pattern: [label:] assert|cover|assume property (@(<event>) [disable iff (<expr>)] <property>);
    # Can span multiple lines
    # First, determine the kind and optional label
    m = re.match(
        r'(?:(\w+)\s*:\s*)?(assert|cover|assume)\s+property\s*\(',
        line
    )
    if not m:
        return None, start_idx

    label = m.group(1)
    kind = m.group(2)

    # Collect the entire property expression (may span multiple lines)
    # Find the matching closing paren + semicolon
    full_text = lines[start_idx]
    i = start_idx
    paren_depth = 0
    started = False
    end_found = False

    # Count parens from the opening '(' after 'property'
    j = full_text.find('property') + len('property')
    while j < len(full_text):
        if full_text[j] == '(':
            paren_depth += 1
            started = True
        elif full_text[j] == ')':
            paren_depth -= 1
            if started and paren_depth == 0:
                # Check for trailing semicolon
                rest = full_text[j+1:].strip()
                if rest.startswith(';') or not rest:
                    end_found = True
                break
        j += 1

    if not end_found:
        # Multi-line — keep collecting
        i += 1
        while i < len(lines):
            full_text += ' ' + lines[i].strip()
            # Re-scan for balanced parens
            j2 = 0
            paren_depth = 0
            started = False
            prop_start = full_text.find('property') + len('property')
            for k in range(prop_start, len(full_text)):
                if full_text[k] == '(':
                    paren_depth += 1
                    started = True
                elif full_text[k] == ')':
                    paren_depth -= 1
                    if started and paren_depth == 0:
                        end_found = True
                        break
            if end_found:
                break
            i += 1

    if not end_found:
        return None, start_idx

    # Extract the property expression between the outer parens
    prop_start = full_text.find('property') + len('property')
    # Find first ( after 'property'
    first_paren = full_text.index('(', prop_start)
    # Find matching )
    last_paren = find_matching_paren(full_text, first_paren)
    prop_expr = full_text[first_paren+1:last_paren].strip()

    # Extract 'else' clause message if present (after closing paren)
    fail_message = ""
    after_prop = full_text[last_paren+1:].strip()
    if after_prop.startswith('else'):
        else_text = after_prop[4:].strip().rstrip(';').strip()
        # Extract message string from common patterns:
        # `uvm_error("msg"), `uvm_error("TAG", "msg"), $error("msg"), etc.
        msg_match = re.search(r'"([^"]*)"(?:\s*\)\s*)?$', else_text)
        if msg_match:
            fail_message = msg_match.group(1)
        elif else_text:
            fail_message = else_text

    # Parse clock event if present
    clock_event = ""
    remaining_expr = prop_expr
    clk_m = re.match(r'@\s*\(([^)]+)\)\s*(.*)', prop_expr, re.DOTALL)
    if clk_m:
        clock_event = clk_m.group(1).strip()
        remaining_expr = clk_m.group(2).strip()

    if not label:
        label = f"_sva_{kind}_{start_idx}"

    sva = SvaAssertionDef(
        name=label,
        kind=kind,
        clock_event=clock_event,
        property_expr=remaining_expr,
        start_line=start_idx,
        end_line=i,
        source_file=filename,
        fail_message=fail_message,
    )
    sva.signals = extract_signals_from_expr(remaining_expr)

    return sva, i


# =====================================================================
#  Code Generator
# =====================================================================

def generate_covergroup_code(cg, indent="    ", in_class=False):
    """Generate replacement SV code for a covergroup."""
    lines = []
    ind = indent

    # Comment: original covergroup preserved
    lines.append(f"{ind}// === svpp: covergroup '{cg.name}' => DPI-C backend ===")

    # Variable declarations
    lines.append(f"{ind}int __cg_{cg.name}_id;")
    for cp in cg.coverpoints:
        lines.append(f"{ind}int __cp_{cg.name}_{cp.name}_id;")
    for x in cg.crosses:
        lines.append(f"{ind}int __cx_{cg.name}_{x.name}_id;")

    # Initialization: function for class context, initial block for module
    if in_class:
        lines.append(f"{ind}function void __svpp_cg_{cg.name}_init();")
    else:
        lines.append(f"{ind}initial begin")
    lines.append(f"{ind}    __cg_{cg.name}_id = sva_cg_create(\"{cg.name}\");")

    for cp in cg.coverpoints:
        var = f"__cp_{cg.name}_{cp.name}_id"
        lines.append(f"{ind}    {var} = sva_cg_add_coverpoint("
                     f"__cg_{cg.name}_id, \"{cp.name}\");")

        if not cp.bins:
            # Auto bins
            lines.append(f"{ind}    sva_cg_add_auto_bins({var}, {cp.auto_bin_max});")
        else:
            for b in cp.bins:
                if b.kind == "value":
                    for lo, hi in b.ranges:
                        lines.append(
                            f"{ind}    sva_cg_add_bin({var}, "
                            f"\"{b.name}\", {lo}, {hi});")
                elif b.kind == "transition":
                    for seq in b.transition_seq:
                        # Pass as individual DPI calls
                        vals = ', '.join(str(v) for v in seq)
                        lines.append(
                            f"{ind}    // transition bin: {b.name} = "
                            f"({' => '.join(str(v) for v in seq)})")
                        # Use a begin/end block with array
                        lines.append(f"{ind}    begin")
                        lines.append(f"{ind}        longint __trans_{b.name}[] = "
                                     f"'{{{vals}}};")
                        lines.append(
                            f"{ind}        sva_cg_add_transition_bin({var}, "
                            f"\"{b.name}\", __trans_{b.name}, {len(seq)});")
                        lines.append(f"{ind}    end")
                elif b.kind == "wildcard":
                    lines.append(
                        f"{ind}    sva_cg_add_wildcard_bin({var}, "
                        f"\"{b.name}\", {b.wc_mask}, {b.wc_pattern});")
                elif b.kind == "illegal":
                    for lo, hi in b.ranges:
                        lines.append(
                            f"{ind}    sva_cg_add_illegal_bin({var}, "
                            f"\"{b.name}\", {lo}, {hi});")
                elif b.kind == "ignore":
                    for lo, hi in b.ranges:
                        lines.append(
                            f"{ind}    sva_cg_add_ignore_bin({var}, "
                            f"\"{b.name}\", {lo}, {hi});")
                elif b.kind == "default":
                    lines.append(
                        f"{ind}    sva_cg_add_default_bin({var}, "
                        f"\"{b.name}\");")

    # Cross coverage
    for x in cg.crosses:
        cp_refs = []
        for cp_name in x.coverpoints:
            cp_refs.append(f"__cp_{cg.name}_{cp_name}_id")

        if len(cp_refs) == 2:
            lines.append(
                f"{ind}    __cx_{cg.name}_{x.name}_id = sva_cg_add_cross2("
                f"__cg_{cg.name}_id, \"{x.name}\", "
                f"{cp_refs[0]}, {cp_refs[1]});")
        elif len(cp_refs) == 3:
            lines.append(
                f"{ind}    __cx_{cg.name}_{x.name}_id = sva_cg_add_cross3("
                f"__cg_{cg.name}_id, \"{x.name}\", "
                f"{cp_refs[0]}, {cp_refs[1]}, {cp_refs[2]});")

    if in_class:
        lines.append(f"{ind}endfunction")
    else:
        lines.append(f"{ind}end")

    # Sampling: function for class context, always block for module
    if in_class:
        lines.append(f"{ind}function void __svpp_cg_{cg.name}_sample();")
    elif cg.clock_event:
        lines.append(f"{ind}always @({cg.clock_event}) begin")
    else:
        lines.append(f"{ind}// No clocking event — call sva_cg_sample manually")
        lines.append(f"{ind}// or use: always @(posedge clk) begin")
        lines.append(f"{ind}always @(*) begin  // NOTE: add proper clock event")

    for cp in cg.coverpoints:
        var = f"__cp_{cg.name}_{cp.name}_id"
        # Cast expression to longint for DPI
        expr = cp.expr
        lines.append(f"{ind}    sva_cg_sample_point({var}, longint'({expr}));")

    lines.append(f"{ind}    sva_cg_sample(__cg_{cg.name}_id);")
    if in_class:
        lines.append(f"{ind}endfunction")
    else:
        lines.append(f"{ind}end")

    # Final report (module context only)
    if not in_class:
        lines.append(f"{ind}final begin")
        lines.append(f"{ind}    sva_report();")
        lines.append(f"{ind}end")
    lines.append(f"{ind}// === svpp: end covergroup '{cg.name}' ===")

    return lines


def generate_sva_code(sva, indent="    ", is_last_for_clock=False):
    """Generate replacement SV code for an SVA assertion."""
    lines = []
    ind = indent

    kind_num = {"assert": 0, "cover": 1, "assume": 2}[sva.kind]

    # Escape the property expression for C string
    prop_escaped = sva.property_expr.replace('\\', '\\\\').replace('"', '\\"')

    lines.append(f"{ind}// === svpp: {sva.kind} property '{sva.name}' => DPI-C ===")
    lines.append(f"{ind}int __sva_{sva.name}_id;")

    # Initialization
    lines.append(f"{ind}initial begin")
    lines.append(
        f'{ind}    __sva_{sva.name}_id = sva_assert_create_ex('
        f'"{sva.name}", '
        f'"{prop_escaped}", '
        f'{kind_num}, '
        f'"{sva.source_file}", '
        f'{sva.start_line + 1});')
    if sva.fail_message:
        msg_escaped = sva.fail_message.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(
            f'{ind}    sva_assert_set_message(__sva_{sva.name}_id, '
            f'"{msg_escaped}");')
    lines.append(f"{ind}end")

    lines.append(f"{ind}// === svpp: end {sva.kind} property '{sva.name}' ===")

    return lines


def generate_sva_tick_block(sva_list, clock_event, indent="    "):
    """Generate a consolidated always block that sets all signals and calls sva_tick once."""
    lines = []
    ind = indent

    # Collect all unique signals across all assertions with this clock
    all_signals = []
    for sva in sva_list:
        for sig in sva.signals:
            if sig not in all_signals:
                all_signals.append(sig)

    lines.append(f"{ind}// === svpp: consolidated signal sampling + tick for @({clock_event}) ===")
    lines.append(f"{ind}always @({clock_event}) begin")
    for sig in all_signals:
        lines.append(f'{ind}    sva_set("{sig}", longint\'({sig}));')
    lines.append(f"{ind}    sva_tick(longint'($time));")
    lines.append(f"{ind}end")

    return lines


# =====================================================================
#  Main Preprocessor
# =====================================================================

def preprocess_file(input_path, output_path=None):
    """Preprocess a single SystemVerilog file."""
    with open(input_path, 'r') as f:
        lines = f.readlines()

    # Strip trailing newlines but preserve them for output
    raw_lines = [l.rstrip('\n') for l in lines]

    output_lines = []
    covergroups = []
    assertions = []
    skip_until = -1
    need_import = False
    has_sva_tick = False

    # First pass: identify all covergroups and SVA blocks
    i = 0
    while i < len(raw_lines):
        stripped = raw_lines[i].strip()

        # Detect covergroup
        if re.match(r'covergroup\s+\w+', stripped):
            cg, end_line = parse_covergroup(raw_lines, i)
            if cg:
                covergroups.append(cg)
                i = end_line + 1
                continue

        # Detect SVA: assert/cover/assume property
        if re.match(r'(?:\w+\s*:\s*)?(assert|cover|assume)\s+property\s*\(', stripped):
            sva, end_line = parse_sva_assertion(raw_lines, i, os.path.basename(input_path))
            if sva:
                assertions.append(sva)
                i = end_line + 1
                continue

        i += 1

    if covergroups or assertions:
        need_import = True

    # Second pass: generate output
    # Build a set of line ranges to skip
    skip_ranges = set()
    for cg in covergroups:
        for l in range(cg.start_line, cg.end_line + 1):
            skip_ranges.add(l)
    for sva in assertions:
        for l in range(sva.start_line, sva.end_line + 1):
            skip_ranges.add(l)

    # Find the end of module port list (line with ");") to insert import after it
    module_line = -1
    port_end_line = -1
    for i, line in enumerate(raw_lines):
        if re.match(r'\s*module\s+', line):
            module_line = i
        if module_line >= 0 and port_end_line < 0:
            if re.search(r'\)\s*;', line):
                port_end_line = i
                break

    # If no port list found, insert after module line
    if port_end_line < 0:
        port_end_line = module_line

    # Also check if sva_dpi_pkg is already imported
    already_imported = any('sva_dpi_pkg' in l for l in raw_lines)

    # Collect all unique clock events for grouped sva_tick calls
    sva_clocks = {}
    for sva in assertions:
        clk = sva.clock_event or "posedge clk"
        if clk not in sva_clocks:
            sva_clocks[clk] = []
        sva_clocks[clk].append(sva)

    # Track whether we've emitted the consolidated tick block for each clock
    tick_emitted_for_clock = set()

    for i, line in enumerate(raw_lines):
        if i in skip_ranges:
            # Check if this is the start of a construct we're replacing
            is_cg_start = any(cg.start_line == i for cg in covergroups)
            is_sva_start = any(sva.start_line == i for sva in assertions)

            if is_cg_start:
                cg = next(c for c in covergroups if c.start_line == i)
                # Detect class context: check if any line before covergroup has 'class'
                in_class = any(re.match(r'\s*class\b', raw_lines[j])
                               for j in range(0, cg.start_line))
                # Comment out original
                output_lines.append(f"    // [svpp] Original covergroup '{cg.name}' "
                                    f"(lines {cg.start_line+1}-{cg.end_line+1}) "
                                    f"transformed to DPI-C calls")
                # Emit replacement code
                output_lines.extend(generate_covergroup_code(cg, in_class=in_class))

            elif is_sva_start:
                sva = next(s for s in assertions if s.start_line == i)
                clk = sva.clock_event or "posedge clk"
                # Comment out original
                output_lines.append(f"    // [svpp] Original {sva.kind} property "
                                    f"'{sva.name}' (lines {sva.start_line+1}-"
                                    f"{sva.end_line+1}) transformed to DPI-C calls")
                # Emit initialization code (no always block per-assertion)
                output_lines.extend(generate_sva_code(sva))
                # If this is the last SVA for this clock event, emit the
                # consolidated tick block
                svas_for_clk = sva_clocks.get(clk, [])
                if sva is svas_for_clk[-1] and clk not in tick_emitted_for_clock:
                    output_lines.extend(
                        generate_sva_tick_block(svas_for_clk, clk))
                    tick_emitted_for_clock.add(clk)

            # Skip all other lines in the range (already handled)
            continue

        # Insert import after module port list close
        if need_import and not already_imported and i == port_end_line:
            output_lines.append(line)
            output_lines.append("    import sva_dpi_pkg::*;")
            continue

        # In class context, replace covergroup new()/sample() calls
        if covergroups:
            for cg in covergroups:
                # Replace: cg_name = new(); -> __svpp_cg_<name>_init();
                if re.search(rf'\b{cg.name}\s*=\s*new\b', line):
                    line = re.sub(
                        rf'\b{cg.name}\s*=\s*new\s*\(\s*\)\s*;',
                        f'__svpp_cg_{cg.name}_init();', line)
                # Replace: cg_name.sample(); -> __svpp_cg_<name>_sample();
                if re.search(rf'\b{cg.name}\.sample\b', line):
                    line = re.sub(
                        rf'\b{cg.name}\.sample\s*\(\s*\)\s*;',
                        f'__svpp_cg_{cg.name}_sample();', line)

        output_lines.append(line)

    # Write output
    if output_path is None:
        output_path = input_path  # overwrite in place (typically to .pp.sv)

    with open(output_path, 'w') as f:
        for line in output_lines:
            f.write(line + '\n')

    return covergroups, assertions


def preprocess_directory(input_dir, output_dir):
    """Preprocess all .sv files in a directory."""
    os.makedirs(output_dir, exist_ok=True)

    for fname in sorted(os.listdir(input_dir)):
        if fname.endswith('.sv'):
            in_path = os.path.join(input_dir, fname)
            out_path = os.path.join(output_dir, fname)
            cgs, svas = preprocess_file(in_path, out_path)
            if cgs or svas:
                print(f"  {fname}: {len(cgs)} covergroup(s), "
                      f"{len(svas)} assertion(s) transformed")
            else:
                # Just copy unchanged
                print(f"  {fname}: no changes")


# =====================================================================
#  CLI Entry Point
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description='SystemVerilog Preprocessor for Verilator — '
                    'transforms covergroups and SVA into DPI-C calls'
    )
    parser.add_argument('input', help='Input .sv file or directory')
    parser.add_argument('-o', '--output', help='Output .sv file or directory')
    parser.add_argument('--in-place', action='store_true',
                        help='Modify input file(s) in place')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Print detailed transformation info')

    args = parser.parse_args()

    if os.path.isdir(args.input):
        out_dir = args.output or (args.input if args.in_place
                                  else args.input + '_pp')
        print(f"svpp: preprocessing directory {args.input} -> {out_dir}")
        preprocess_directory(args.input, out_dir)
    else:
        if args.in_place:
            out_file = args.input
        elif args.output:
            out_file = args.output
        else:
            base, ext = os.path.splitext(args.input)
            out_file = base + '.pp' + ext

        print(f"svpp: {args.input} -> {out_file}")
        cgs, svas = preprocess_file(args.input, out_file)
        print(f"  {len(cgs)} covergroup(s), {len(svas)} assertion(s) transformed")


if __name__ == '__main__':
    main()
