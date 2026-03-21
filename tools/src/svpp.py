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
  2. Parses property ... endproperty blocks
  3. Parses assert/cover/assume property (...) blocks
  4. Emits replacement SV code with DPI-C calls
  5. Preserves everything else verbatim
  6. Rewrites `include "X.sv" to `include "X.pp.sv" for preprocessed files
  7. Transforms clocking block 'output negedge' for Verilator compatibility
"""

import argparse
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
class PropertyDef:
    """A named property ... endproperty block."""
    name: str
    clock_event: str = ""
    body: str = ""  # the property expression body (after clock event)
    signals: List[str] = field(default_factory=list)
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
    # Remove SV bit/number literals: e.g., 8'hFF, 1'b0, 4'bz, {8{1'bz}}
    expr = re.sub(r"\d+'[bBhHoOdD][0-9a-fA-F_?xXzZ]+", '', expr)
    # Remove system function calls ($identifier, $test$plusargs, etc.)
    expr = re.sub(r'\$\w+(?:\$\w+)*', '', expr)
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
#  Property Parser
# =====================================================================

def _transform_property_if(body):
    """Transform property-level 'if (COND) BODY' to '(COND) |-> BODY'.

    In IEEE 1800 SVA, ``if (cond) prop_expr`` means: when *cond* is true,
    evaluate *prop_expr*; when false, pass vacuously.  This is exactly the
    semantics of ``(cond) |-> prop_expr`` (overlapping implication), which
    the DPI-C SVA engine already supports.
    """
    m = re.match(r'if\s*\(', body)
    if not m:
        return body
    # Find the balanced closing paren of the if-condition
    open_idx = body.index('(', m.start())
    close_idx = find_matching_paren(body, open_idx)
    cond = body[open_idx:close_idx + 1]       # includes parens
    rest = body[close_idx + 1:].strip()
    return f'{cond} |-> {rest}'


def parse_property_block(lines, start_idx):
    """Parse a property ... endproperty block and return a PropertyDef."""
    line = lines[start_idx].strip()
    m = re.match(r'property\s+(\w+)\s*(?:\([^)]*\))?\s*;', line)
    if not m:
        return None, start_idx

    name = m.group(1)
    body_lines = []
    i = start_idx + 1
    while i < len(lines):
        stripped = lines[i].strip()
        if re.match(r'endproperty\b', stripped):
            break
        body_lines.append(stripped)
        i += 1
    else:
        # No endproperty found
        return None, start_idx

    body = ' '.join(body_lines)

    # Extract clock event if present
    clock_event = ""
    clk_m = re.match(r'@\s*\(([^)]+)\)\s*(.*)', body, re.DOTALL)
    if clk_m:
        clock_event = clk_m.group(1).strip()
        body = clk_m.group(2).strip()

    # Transform property-level if: if (COND) BODY -> (COND) |-> BODY
    # IEEE 1800 §16.12.6: if-else property passes vacuously when cond is false
    # which is exactly what |-> does when the antecedent doesn't match.
    body = _transform_property_if(body)

    # Replace tristate literals in property body (Verilator has no tristate)
    body = re.sub(
        r"(\d+')([bB])([zZ]+)",
        lambda m: m.group(1) + m.group(2) + '0' * len(m.group(3)),
        body)

    prop = PropertyDef(
        name=name,
        clock_event=clock_event,
        body=body,
        signals=extract_signals_from_expr(body),
        start_line=start_idx,
        end_line=i,
    )

    return prop, i


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
                end_found = True
                break
        j += 1

    if not end_found:
        # Multi-line — keep collecting
        i += 1
        while i < len(lines):
            full_text += ' ' + lines[i].strip()
            # Re-scan for balanced parens
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
    # The else clause may be on the same line or subsequent lines
    fail_message = ""
    after_prop = full_text[last_paren+1:].strip()

    # Check for else on the same line (or starting on the same line)
    if after_prop.startswith('else'):
        else_text = after_prop[4:].strip()
        # If else body is empty or incomplete (no semicolon), read subsequent lines
        if not else_text or ';' not in else_text:
            scan = i + 1
            while scan < len(lines):
                scan_line = lines[scan].strip()
                else_text += ' ' + scan_line
                if ';' in scan_line:
                    i = scan  # extend end_line to cover the else clause body
                    break
                scan += 1
        else_text = else_text.rstrip(';').strip()
        msg_match = re.search(r'"([^"]*)"', else_text)
        if msg_match:
            fail_message = msg_match.group(1)
        elif else_text:
            fail_message = else_text
    elif not after_prop or after_prop == ';':
        # Check for 'else' on subsequent lines
        scan = i + 1
        while scan < len(lines):
            scan_stripped = lines[scan].strip()
            if not scan_stripped:
                scan += 1
                continue
            if scan_stripped.startswith('else'):
                # Found else clause on subsequent line
                # Include this line (and possibly more) in the assertion range
                # Find the semicolon
                else_line = scan_stripped
                while ';' not in else_line and scan + 1 < len(lines):
                    scan += 1
                    else_line += ' ' + lines[scan].strip()
                i = scan  # extend end_line to cover the else clause

                else_text = else_line[4:].strip().rstrip(';').strip()
                msg_match = re.search(r'"([^"]*)"', else_text)
                if msg_match:
                    fail_message = msg_match.group(1)
                elif else_text:
                    fail_message = else_text
            break

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
        lines.append(f'{ind}    if ($test$plusargs("coverage"))')
        lines.append(f"{ind}        sva_report();")
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
    """Generate a consolidated always block that sets all signals and calls sva_tick once.

    The sampling edge is flipped from posedge to negedge (and vice versa).
    In the Verilator scheduling model, all always_ff blocks and combinational
    logic are evaluated in a single eval() pass at the active clock edge.
    By the time an @(posedge clk) process reads a combinational signal such
    as a ready handshake, the dependent FF has already consumed the event and
    the combinational output has changed.  Sampling at the opposite edge
    captures signals in their stable, post-previous-eval state — equivalent
    to the pre-NBA view a standard event-driven simulator provides.
    """
    lines = []
    ind = indent

    # Collect all unique signals across all assertions with this clock
    all_signals = []
    for sva in sva_list:
        for sig in sva.signals:
            if sig not in all_signals:
                all_signals.append(sig)

    # Flip the sampling edge so signals are captured mid-cycle
    sample_event = clock_event
    if "posedge" in clock_event:
        sample_event = clock_event.replace("posedge", "negedge")
    elif "negedge" in clock_event:
        sample_event = clock_event.replace("negedge", "posedge")

    lines.append(f"{ind}// === svpp: consolidated signal sampling + tick for @({clock_event}) ===")
    lines.append(f"{ind}// Sampling at opposite edge for correct pre-eval observation")
    lines.append(f"{ind}always @({sample_event}) begin")
    for sig in all_signals:
        lines.append(f'{ind}    sva_set("{sig}", longint\'({sig}));')
    lines.append(f"{ind}    sva_tick(longint'($time));")
    lines.append(f"{ind}end")

    return lines


# =====================================================================
#  Post-processing Passes
# =====================================================================

def _fix_inline_comb_into_ff(lines):
    """Inline combinational assign expressions into always_ff blocks.

    Verilator computes combinational wires (from ``assign``) only during
    the settle phase.  When inputs change at runtime (e.g., via VPI or
    virtual-interface writes), these wires are never recomputed, causing
    ``always_ff`` to read stale values.

    This pass replaces ``assign <wire> = <expr>;`` with blocking
    assignments at the top of each ``always_ff`` block, so Verilator
    evaluates them in the active path on every clock edge.
    """
    # --- Step 1: collect assign statements in module scope ---
    # Each entry: wire_name -> (expr_str, [line_indices], decl_line_or_None)
    assigns = {}       # wire_name -> expr
    assign_lines = {}  # wire_name -> set of line indices to remove
    decl_lines = {}    # wire_name -> (line_index, width_str)
    in_module = False
    class_depth = 0

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if re.match(r'\bmodule\s+\w+', stripped):
            in_module = True
        elif re.match(r'\bendmodule\b', stripped):
            in_module = False
        if re.match(r'\bclass\b', stripped):
            class_depth += 1
        elif re.match(r'\bendclass\b', stripped):
            class_depth = max(class_depth - 1, 0)

        if in_module and class_depth == 0:
            m = re.match(r'\s*assign\s+(\w+)\s*=\s*', stripped)
            if m:
                wire_name = m.group(1)
                # Collect full expression (may span multiple lines)
                start = i
                full = lines[i].rstrip()
                while ';' not in full and i + 1 < len(lines):
                    i += 1
                    full += '\n' + lines[i].rstrip()
                # Extract expression after 'assign <name> ='
                expr_m = re.search(
                    rf'assign\s+{re.escape(wire_name)}\s*=\s*(.+?)\s*;',
                    full, re.DOTALL
                )
                if expr_m:
                    expr = expr_m.group(1).strip()
                    # Strip line comments (// ...) that got joined
                    expr = re.sub(r'//[^\n]*', '', expr)
                    # Normalize whitespace
                    expr = re.sub(r'\s+', ' ', expr)
                    assigns[wire_name] = expr
                    assign_lines[wire_name] = set(range(start, i + 1))
        i += 1

    if not assigns:
        return lines

    # Only inline if the file contains always_ff blocks
    has_always_ff = any(re.match(r'\s*always_ff\b', l) for l in lines)
    if not has_always_ff:
        return lines

    # Find wire declarations for these signals.
    # Keep module-level declarations (do NOT remove them) — only remove
    # the assign statements.  The blocking assignments inside always_ff
    # will update the module-level variable directly so that output ports
    # and other module-scoped signals remain visible to the outside.
    all_remove = set()
    for wire_name in assigns:
        for idx, line in enumerate(lines):
            stripped = line.strip()
            dm = re.match(
                rf'logic\s*(\[\d+:\d+\])?\s*{re.escape(wire_name)}\s*;',
                stripped
            )
            if dm:
                decl_lines[wire_name] = (idx, dm.group(1) or '')
                # Do NOT remove module-level declarations; they are needed
                # so the variable stays in module scope.
        all_remove.update(assign_lines.get(wire_name, set()))

    # --- Step 2: rebuild output, injecting computations into always_ff ---
    result = []
    i = 0
    while i < len(lines):
        if i in all_remove:
            i += 1
            continue

        stripped = lines[i].strip()

        # Detect always_ff ... begin
        if re.match(r'\s*always_ff\b', stripped):
            # Collect lines through the first 'begin'
            block_start = i
            while i < len(lines):
                result.append(lines[i])
                if 'begin' in lines[i]:
                    # Inject inlined wire computations right after 'begin'
                    indent = re.match(r'(\s*)', lines[i]).group(1) + '    '
                    result.append(
                        f'{indent}// [svpp] inlined combinational wires'
                    )
                    # Emit in dependency order: if wire A uses wire B,
                    # emit B first.  Simple topological sort.
                    ordered = []
                    remaining = dict(assigns)
                    for _ in range(len(remaining) + 1):
                        progress = False
                        for wn, expr in list(remaining.items()):
                            # Check if expr depends on any remaining wire
                            deps = [
                                rw for rw in remaining
                                if rw != wn and re.search(
                                    rf'\b{re.escape(rw)}\b', expr
                                )
                            ]
                            if not deps:
                                ordered.append(wn)
                                del remaining[wn]
                                progress = True
                        if not progress:
                            break
                    ordered.extend(remaining.keys())

                    # Emit blocking assignments (no local declarations —
                    # the wires are module-level or port signals that must
                    # remain visible outside the always_ff block).
                    for wn in ordered:
                        expr = assigns[wn]
                        result.append(
                            f'{indent}{wn} = {expr};'
                        )
                    i += 1
                    break
                i += 1
            continue

        result.append(lines[i])
        i += 1

    return result


def _fix_assign_to_always_comb(lines):
    """Transform 'assign' statements to 'always_comb' inside module bodies.

    Verilator may schedule ``assign`` wires into the settle-only path,
    preventing them from being recomputed when inputs change at runtime
    (e.g., via VPI writes from UVM drivers).  ``always_comb`` blocks
    create proper sensitivity-driven evaluation in the active path.

    Only transforms ``assign`` inside module/endmodule scope (not in
    class bodies or packages).
    """
    result = []
    in_module = False
    class_depth = 0

    for line in lines:
        stripped = line.strip()

        # Track module scope (but not class scope inside modules)
        if re.match(r'\b(module|interface)\s+\w+', stripped):
            in_module = True
        elif re.match(r'\b(endmodule|endinterface)\b', stripped):
            in_module = False
        if re.match(r'\bclass\b', stripped):
            class_depth += 1
        elif re.match(r'\bendclass\b', stripped):
            class_depth = max(class_depth - 1, 0)

        if in_module and class_depth == 0:
            # Transform: assign <target> = <expr>;
            # to:        always_comb begin <target> = <expr>; end
            # Using begin/end ensures Verilator creates a proper
            # sensitivity-driven always_comb block in the active path.
            m = re.match(r'^(\s*)assign\s+(.+;\s*)$', line)
            if m:
                indent = m.group(1)
                rest = m.group(2).strip()
                line = f'{indent}always_comb begin {rest} end'

        result.append(line)
    return result


def _fix_initial_nba(lines):
    """Convert ``initial begin`` blocks to ``always begin`` when they contain
    non-blocking assignments *and* event controls.

    Verilator converts ``<=`` inside ``initial`` blocks to blocking ``=``
    (INITIALDLY warning), which breaks NBA scheduling semantics.  Testbench
    stimulus blocks that drive DUT inputs with ``<=`` and wait on clock
    edges then update signals in the active region instead of the NBA
    region, causing timing mismatches vs. commercial simulators.

    ``always begin`` blocks handle ``<=`` correctly in Verilator.  This is
    safe for testbench blocks because they end with ``$finish`` (preventing
    infinite re-execution).

    A block qualifies if it contains BOTH:
      - ``$finish`` or ``$fatal`` (ensures the block is a one-shot testbench
        block where ``always`` won't cause infinite re-execution)
      - at least one non-blocking assignment (``<=``) OR a task call that may
        contain NBA (any non-system identifier followed by ``(``)

    This also covers blocks that call tasks containing NBA — Verilator
    inlines task bodies into the calling initial context, so the ``<=``
    inside those tasks also gets converted to ``=``.
    """
    # --- Pass 1: identify initial-begin block ranges and whether they qualify ---
    blocks = []  # list of (start_line, end_line, qualifies)
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if re.match(r'^initial\s+begin\b', stripped):
            start = i
            depth = 1
            has_finish = False
            has_nba_or_task = False
            j = i + 1
            while j < len(lines) and depth > 0:
                s = lines[j].strip()
                for _ in re.finditer(r'\b(begin|fork)\b', s):
                    depth += 1
                for _ in re.finditer(r'\b(end|join|join_any|join_none)\b', s):
                    depth -= 1
                # Check for $finish or $fatal (safe to convert to always)
                if re.search(r'\$(?:finish|fatal)\b', s):
                    has_finish = True
                # Check for NBA
                if re.search(r'(?<![<>=!])\s*<=\s*(?!=)', s) and \
                        not s.lstrip().startswith('//'):
                    has_nba_or_task = True
                # Check for task/function calls (non-system, non-keyword)
                if re.search(r'\b(?!begin|end|if|else|for|while|repeat|fork'
                             r'|join|return|case|do|wait|disable|forever\b)'
                             r'[a-zA-Z_]\w*\s*\(', s) and \
                        not s.lstrip().startswith('//'):
                    has_nba_or_task = True
                j += 1
            blocks.append((start, j - 1, has_finish and has_nba_or_task))
            i = j
        else:
            i += 1

    if not blocks:
        return lines

    # --- Pass 2: replace qualifying 'initial begin' with 'always begin' ---
    qualify_starts = {b[0] for b in blocks if b[2]}
    result = []
    for i, line in enumerate(lines):
        if i in qualify_starts:
            line = re.sub(r'^(\s*)initial(\s+begin\b)', r'\1always\2', line)
        result.append(line)
    return result


def _fix_super_new(lines):
    """Move super.new() before the begin block in constructors.

    Verilator enforces IEEE 1800-2023 §8.15: super.new must be the very
    first statement inside ``function new``.  Many UVM testbenches wrap
    the body in a begin/end block, which violates this rule::

        function new(string name, uvm_component parent);
            begin                        // <-- begin makes super.new
                super.new(name, parent); //     not the first statement
                ...
            end
        endfunction

    This pass rewrites it to::

        function new(string name, uvm_component parent);
            super.new(name, parent);
            begin
                ...
            end
        endfunction
    """
    result = []
    i = 0
    while i < len(lines):
        # Look for: function new(...)  (with or without trailing semicolon on same line)
        if re.match(r'\s*function\s+new\b', lines[i]):
            # Find next non-blank line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and re.match(r'\s*begin\s*$', lines[j]):
                # Found 'begin' — now look for super.new on the next non-blank line
                k = j + 1
                while k < len(lines) and not lines[k].strip():
                    k += 1
                if k < len(lines) and re.match(r'\s*super\.new\(', lines[k]):
                    # Emit: function new line, blanks, super.new, begin, skip original super.new
                    result.append(lines[i])           # function new(...)
                    for b in range(i + 1, j):         # any blank lines
                        result.append(lines[b])
                    result.append(lines[k])           # super.new(...) — moved up
                    result.append(lines[j])           # begin
                    for b in range(j + 1, k):         # blanks between begin and super.new
                        result.append(lines[b])
                    i = k + 1                         # skip original super.new position
                    continue
        result.append(lines[i])
        i += 1
    return result


def _fix_vif_nba(lines):
    """Convert NBA assignments to virtual-interface members into blocking
    assignments inside class bodies.

    Verilator does not properly propagate non-blocking (<=) writes from
    class tasks/functions through virtual-interface port connections to
    the RTL signals they are bound to.  Blocking (=) assignments work
    correctly because they update the signal value immediately in the
    same scheduling region.

    Only lines inside ``class … endclass`` are transformed, so RTL
    ``always_ff`` NBA assignments are never touched.
    """
    result = []
    class_depth = 0  # nesting depth of class … endclass
    for line in lines:
        stripped = line.strip()
        # Track class nesting (classes can be nested in SV)
        if re.match(r'\bclass\b', stripped):
            class_depth += 1
        elif re.match(r'\bendclass\b', stripped):
            class_depth = max(class_depth - 1, 0)

        if class_depth > 0:
            # Match:  identifier.identifier  <=  expression ;
            # But not comparisons like  (a <= b)
            # NBA pattern: starts with optional whitespace, then hierarchical
            # name, then <=, then value, then ;
            line = re.sub(
                r'^(\s*\w+\.\w+(?:\.\w+)*\s*)<=(\s*.*;)',
                r'\1=\2',
                line,
            )
        result.append(line)
    return result


def _fix_vif_drive(lines):
    """Transform virtual-interface writes in class bodies to use VPI-based
    drive functions injected into the interface.

    Verilator's scheduling does not propagate value changes written to
    virtual-interface members from class code through to connected DUT
    logic.  The DUT's combinational signals that depend on interface
    inputs are never re-evaluated.

    This pass:
    1. Detects interface definitions and adds VPI-based ``__svpp_drive_*``
       functions for each signal.
    2. Detects ``vif.signal = expr;`` patterns in class bodies and
       transforms them to ``vif.__svpp_drive_signal(expr);``.

    The VPI write (``vpi_put_value`` with ``vpiNoDelay``) writes directly
    to the RTL signal and triggers Verilator's evaluation loop, ensuring
    dependent combinational logic is updated.
    """
    # --- Step 1: find interface definitions and their signals ---
    iface_signals = {}  # iface_name -> [(sig_name, width_str)]
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        m = re.match(r'interface\s+(\w+)', stripped)
        if m:
            iface_name = m.group(1)
            sigs = []
            j = i + 1
            while j < len(lines):
                iline = lines[j].strip()
                if re.match(r'endinterface\b', iline):
                    break
                # Parse: [input|output] logic|bit|reg [W:0] name [, name2 ...];
                sm = re.match(
                    r'(?:input\s+|output\s+)?(?:logic|bit|reg|wire)'
                    r'\s*(\[[^\]]+:\d+\])?\s*'
                    r'(\w+(?:\s*,\s*\w+)*)\s*;',
                    iline
                )
                if sm:
                    width = sm.group(1) or ''
                    names = [n.strip() for n in sm.group(2).split(',')]
                    for sig_name in names:
                        sigs.append((sig_name, width))
                j += 1
            iface_signals[iface_name] = sigs
            i = j + 1
            continue
        i += 1

    # --- Step 2: inject __svpp_drive_* functions before endinterface ---
    # (Only if this file contains interface definitions)
    result = []
    if iface_signals:
        for line in lines:
            stripped = line.strip()
            if re.match(r'endinterface\b', stripped):
                indent = re.match(r'(\s*)', line).group(1)
                inner = indent + '    '
                for iface_name, sigs in iface_signals.items():
                    result.append(f"{inner}// [svpp] VPI-based drive functions for Verilator")
                    result.append(f"{inner}import sva_dpi_pkg::svpp_vpi_drive;")
                    result.append(f"{inner}string __svpp_hier;")
                    result.append(f"{inner}initial __svpp_hier = $sformatf(\"%m\");")
                    for sig_name, width in sigs:
                        if width:
                            w_match = re.match(r'\[(\d+):(\d+)\]', width)
                            if w_match:
                                w_bits = str(int(w_match.group(1)) - int(w_match.group(2)) + 1)
                            else:
                                # Parameterized width — use $bits at elaboration
                                w_bits = f"$bits({sig_name})"
                            param_type = f"logic {width}"
                        else:
                            w_bits = "1"
                            param_type = "logic"
                        result.append(
                            f'{inner}function automatic void __svpp_drive_{sig_name}'
                            f'(input {param_type} val);'
                        )
                        result.append(
                            f'{inner}    svpp_vpi_drive('
                            f'{{__svpp_hier, ".{sig_name}"}}, '
                            f"int'(val), {w_bits});"
                        )
                        result.append(f'{inner}    {sig_name} = val;')
                        result.append(f'{inner}endfunction')
                    break
            result.append(line)
    else:
        result = list(lines)

    # --- Step 3: transform class-body VIF writes to drive function calls ---
    # This works even when the interface definition is in another file.
    # We detect virtual interface handles from `virtual <type> <name>`
    # declarations in classes, then transform any assignment to
    # `<name>.<signal> [<]= expr;` into `<name>.__svpp_drive_<signal>(expr);`.
    final = []
    class_depth = 0
    vif_handles = set()  # names of virtual interface handles in current class

    for line in result:
        stripped = line.strip()
        if re.match(r'\bclass\b', stripped):
            class_depth += 1
            vif_handles = set()
        elif re.match(r'\bendclass\b', stripped):
            class_depth = max(class_depth - 1, 0)
            vif_handles = set()

        if class_depth > 0:
            # Detect: virtual <type>[.modport] <name>;
            vm = re.match(r'\s*virtual\s+\w+(?:\.\w+)?\s+(\w+)\s*;', stripped)
            if vm:
                vif_handles.add(vm.group(1))

            # Transform: vif_name.signal <= expr; or vif_name.signal = expr;
            # NOTE: deliberately does NOT match vif.clocking_block.signal
            # because clocking-block drives have their own output-skew timing
            # and must not be replaced with immediate VPI writes.
            if vif_handles:
                for vif_name in vif_handles:
                    pattern = (
                        rf'^(\s*){re.escape(vif_name)}\s*\.\s*(\w+)\s*<?='
                        rf'\s*(.+?)\s*;'
                    )
                    m = re.match(pattern, line)
                    if m:
                        indent_str = m.group(1)
                        sig_name = m.group(2)
                        expr = m.group(3)
                        line = (
                            f'{indent_str}{vif_name}.__svpp_drive_{sig_name}'
                            f'({expr});'
                        )
                        break
        final.append(line)

    return final


def _fix_wait_clocking_block(lines):
    """Transform ``wait (vif.cb.signal ...)`` into edge-synchronized polling.

    Verilator treats ``wait(vif.cb.signal)`` as a level-sensitive wait on
    the raw signal, ignoring the clocking-block sampling semantics.  This
    causes timing races where the ``wait`` fires mid-cycle before the
    clocking block has updated its sampled values.

    This pass rewrites::

        wait (vif.xxx_cb.sig && vif.xxx_cb.other)

    to::

        while (!(vif.xxx_cb.sig && vif.xxx_cb.other)) @(vif.xxx_cb);

    which polls the condition at each clocking-block event — matching the
    IEEE 1800 semantics of sampling at the clocking event.
    """
    result = []
    for line in lines:
        stripped = line.strip()
        # Match: wait (expr_containing_cb_ref) [optional trailing statement]
        # The clocking-block reference has the form: <handle>.<cb_name>.
        m = re.match(
            r'^(\s*)wait\s*\((.+)\)\s*$',
            line,
        )
        if m and re.search(r'\w+\.\w+_cb\.\w+', m.group(2)):
            indent = m.group(1)
            expr = m.group(2)
            # Extract the VIF handle + clocking block name for @() event
            cb_match = re.search(r'(\w+\.\w+_cb)\.\w+', expr)
            cb_ref = cb_match.group(1) if cb_match else 'vif.mon_cb'
            result.append(f'{indent}while (!({expr})) @({cb_ref});')
        else:
            result.append(line)
    return result


def _fix_edge_clocking_block(lines):
    """Transform ``@(posedge/negedge vif.cb.signal)`` into a polling loop
    that detects the edge at the clocking-block event boundary.

    Verilator may fire ``@(posedge vif.cb.sig)`` on the raw signal
    transition rather than at the next clocking-block event where the
    sampled value shows an edge.  This causes monitors to see stale
    ``data_out`` values because they read one cycle too early.

    Rewrite::

        @(posedge vif.mon_cb.read_enb0)

    to::

        begin
            automatic logic __svpp_prev_read_enb0 = vif.mon_cb.read_enb0;
            forever begin
                @(vif.mon_cb);
                if (!__svpp_prev_read_enb0 && vif.mon_cb.read_enb0) break;
                __svpp_prev_read_enb0 = vif.mon_cb.read_enb0;
            end
        end
    """
    result = []
    for line in lines:
        m = re.match(
            r'^(\s*)@\s*\(\s*(posedge|negedge)\s+'
            r'(\w+\.\w+_cb)\.(\w+)\s*\)\s*$',
            line,
        )
        if m:
            indent = m.group(1)
            edge = m.group(2)
            cb_ref = m.group(3)
            sig = m.group(4)
            prev_var = f'__svpp_prev_{sig}'
            if edge == 'posedge':
                cond = f'!{prev_var} && {cb_ref}.{sig}'
            else:
                cond = f'{prev_var} && !{cb_ref}.{sig}'
            result.append(f'{indent}begin')
            result.append(f'{indent}    automatic logic {prev_var} = {cb_ref}.{sig};')
            result.append(f'{indent}    forever begin')
            result.append(f'{indent}        @({cb_ref});')
            result.append(f'{indent}        if ({cond}) break;')
            result.append(f'{indent}        {prev_var} = {cb_ref}.{sig};')
            result.append(f'{indent}    end')
            result.append(f'{indent}end')
        else:
            result.append(line)
    return result


def _fix_array_of_queues(lines):
    """Flatten fixed-size arrays of queues into individual queue variables.

    Verilator has a bug where ``array_of_queues[variable].pop_front()``
    returns a default value instead of the actual queue element.  This
    pass rewrites::

        type name[N][$:M];         →  type name__0[$:M]; ... type name__N-1[$:M];
        name[expr].method(args)    →  case-dispatch per index

    Only triggered for small fixed-size arrays (N ≤ 8) of queues.
    """
    # --- Step 1: detect array-of-queue declarations ---
    aq_decls = {}  # name -> (elem_type, array_size, bound_str, line_idx)
    for idx, line in enumerate(lines):
        m = re.match(
            r'^(\s*)(logic|bit|reg|int|byte)\s*'
            r'(\[[^\]]*\])?\s*'      # optional element width
            r'(\w+)\s*'              # array name
            r'\[(\d+)\]\s*'          # fixed-size dimension
            r'(\[\$(?::\d+)?\])\s*'  # queue dimension [$] or [$:N]
            r';',
            line,
        )
        if m:
            indent = m.group(1)
            base_type = m.group(2)
            width = m.group(3) or ''
            name = m.group(4)
            arr_size = int(m.group(5))
            q_dim = m.group(6)
            if arr_size <= 8:
                aq_decls[name] = (f'{base_type} {width}'.strip(),
                                  arr_size, q_dim, idx, indent)

    if not aq_decls:
        return lines

    result = []
    for idx, line in enumerate(lines):
        # Replace declaration lines — emit individual queues + helper functions
        replaced_decl = False
        for name, (elem_type, arr_size, q_dim, decl_idx, indent) in aq_decls.items():
            if idx == decl_idx:
                result.append(f'{indent}// [svpp] Flattened array-of-queues '
                              f'(Verilator workaround)')
                for k in range(arr_size):
                    result.append(f'{indent}{elem_type} {name}__{k}{q_dim};')
                # Helper: push_back
                result.append(
                    f'{indent}function automatic void '
                    f'__svpp_{name}_push_back(int idx, {elem_type} val);')
                result.append(f'{indent}    case (idx)')
                for k in range(arr_size):
                    result.append(
                        f'{indent}        {k}: {name}__{k}.push_back(val);')
                result.append(f'{indent}        default: ;')
                result.append(f'{indent}    endcase')
                result.append(f'{indent}endfunction')
                # Helper: pop_front
                result.append(
                    f'{indent}function automatic {elem_type} '
                    f'__svpp_{name}_pop_front(int idx);')
                result.append(f'{indent}    case (idx)')
                for k in range(arr_size):
                    result.append(
                        f'{indent}        {k}: return {name}__{k}.pop_front();')
                result.append(f'{indent}        default: return \'0;')
                result.append(f'{indent}    endcase')
                result.append(f'{indent}endfunction')
                # Helper: size
                result.append(
                    f'{indent}function automatic int '
                    f'__svpp_{name}_size(int idx);')
                result.append(f'{indent}    case (idx)')
                for k in range(arr_size):
                    result.append(
                        f'{indent}        {k}: return {name}__{k}.size();')
                result.append(f'{indent}        default: return 0;')
                result.append(f'{indent}    endcase')
                result.append(f'{indent}endfunction')
                replaced_decl = True
                break
        if replaced_decl:
            continue

        # Replace literal-indexed accesses: name[N] → name__N
        for name in aq_decls:
            if re.search(rf'\b{name}\[\d+\]', line):
                line = re.sub(rf'\b{name}\[(\d+)\]', rf'{name}__\1', line)

        # Replace variable-indexed method calls with helper function calls
        for name, (elem_type, arr_size, q_dim, decl_idx, _indent) in aq_decls.items():
            # name[var].push_back(args)  → __svpp_name_push_back(var, args)
            line = re.sub(
                rf'\b{name}\[([^\]]+)\]\.push_back\(([^)]*)\)',
                rf'__svpp_{name}_push_back(\1, \2)', line)
            # name[var].pop_front()  → __svpp_name_pop_front(var)
            line = re.sub(
                rf'\b{name}\[([^\]]+)\]\.pop_front\(\)',
                rf'__svpp_{name}_pop_front(\1)', line)
            # name[var].size()  → __svpp_name_size(var)
            line = re.sub(
                rf'\b{name}\[([^\]]+)\]\.size\(\)',
                rf'__svpp_{name}_size(\1)', line)

        result.append(line)

    return result


def _fix_vif_port_connections(lines, all_file_lines=None):
    """Insert combinational bridge signals between interface instances and
    DUT input-port connections for Verilator.

    Verilator's scheduling does not reliably propagate value changes written
    to virtual-interface members (from class code) through direct port
    connections like::

        alu8 dut (.a(alu_vif.a), ...);

    The combinational dependency from ``alu_vif.a`` to the DUT's internal
    logic is optimized away.  This pass rewrites INPUT port connections by
    inserting ``always_comb`` intermediary signals::

        logic [7:0] __svpp_alu_vif__a;
        always_comb __svpp_alu_vif__a = alu_vif.a;
        ...
        alu8 dut (.a(__svpp_alu_vif__a), ...);

    OUTPUT ports (result, carry, overflow) keep the direct connection so the
    DUT can drive the interface signals.

    ``all_file_lines`` is an optional dict mapping module names to their
    source lines, used to resolve port directions for modules defined in
    other files.
    """
    if all_file_lines is None:
        all_file_lines = {}

    # --- Step 1: detect interface instances ---
    iface_instances = {}  # inst_name -> iface_type
    sv_keywords = {
        'module', 'logic', 'reg', 'wire', 'int', 'integer', 'real',
        'string', 'bit', 'byte', 'shortint', 'longint', 'initial',
        'always', 'assign', 'generate', 'if', 'for', 'while',
        'class', 'function', 'task', 'begin', 'end', 'import',
    }

    for line in lines:
        stripped = line.strip()
        m = re.match(r'(\w+)\s+(\w+)\s*\(([^.]*)\)\s*;', stripped)
        if m:
            itype, iname = m.group(1), m.group(2)
            if itype not in sv_keywords:
                iface_instances[iname] = itype

    if not iface_instances:
        return lines

    # --- Step 2: collect all module instance port-connections that
    #     reference interface members ---
    # Also detect the module type being instantiated so we can look up
    # its port directions.
    # Module instantiation pattern:
    #   <module_type> <inst_name> (
    #       .port(signal), ...
    #   );
    # We identify module instances by lines with .port( syntax

    # First, find module instantiation headers: <type> <name> (
    module_inst_types = {}  # inst_name -> module_type
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = re.match(r'(\w+)\s+(\w+)\s*\(', stripped)
        if m and m.group(1) not in sv_keywords and m.group(1) not in iface_instances.values():
            # Check that the next non-blank lines contain .port( syntax
            for j in range(i, min(i + 3, len(lines))):
                if re.search(r'\.\w+\s*\(', lines[j]):
                    module_inst_types[m.group(2)] = m.group(1)
                    break

    # Collect port connections: .port(inst.sig) for known interface instances
    port_refs = {}  # "inst.sig" -> (bridge_name, port_name, module_type)

    for line in lines:
        for inst_name in iface_instances:
            for m in re.finditer(
                rf'\.(\w+)\s*\({re.escape(inst_name)}\.(\w+)\s*[,\)]',
                line
            ):
                port_name = m.group(1)
                sig_name = m.group(2)
                key = f"{inst_name}.{sig_name}"
                if key not in port_refs:
                    bridge = f"__svpp_{inst_name}__{sig_name}"
                    # Try to find which module this port belongs to
                    mod_type = ''
                    for mi_name, mi_type in module_inst_types.items():
                        # Check if this port connection is within this module instance
                        mod_type = mi_type
                        break
                    port_refs[key] = (bridge, port_name, mod_type)

    if not port_refs:
        return lines

    # --- Step 3: resolve port directions from all available sources ---
    # Build a map: module_type -> {port_name: (direction, width)}
    all_module_ports = {}

    def _parse_module_ports(source_lines):
        """Extract port declarations from module source lines."""
        ports = {}
        in_ports = False
        current_module = None
        for ln in source_lines:
            s = ln.strip()
            mm = re.match(r'module\s+(\w+)', s)
            if mm:
                current_module = mm.group(1)
                in_ports = True
                continue
            if in_ports:
                pm = re.match(
                    r'(input|output|inout)\s+(?:logic|wire|reg)?\s*(\[\d+:\d+\])?\s*(\w+)',
                    s
                )
                if pm:
                    ports[pm.group(3)] = (pm.group(1), pm.group(2) or '')
                if re.search(r'\)\s*;', s):
                    in_ports = False
                    if current_module:
                        all_module_ports[current_module] = ports
                    ports = {}
                    current_module = None

    # Parse current file
    _parse_module_ports(lines)
    # Parse other available file sources
    for mod_name, mod_lines in all_file_lines.items():
        _parse_module_ports(mod_lines)

    # --- Step 4: determine which ports to bridge (inputs only) ---
    bridges_to_create = []
    bridges_map = {}

    for key, (bridge, port_name, mod_type) in port_refs.items():
        inst_name, sig_name = key.split('.', 1)
        # Look up port direction
        is_output = False
        width = ''
        if mod_type in all_module_ports:
            port_info = all_module_ports[mod_type]
            if port_name in port_info:
                direction, width = port_info[port_name]
                if direction == 'output':
                    is_output = True
        if is_output:
            continue  # Skip output ports — DUT drives them directly
        bridges_to_create.append((bridge, width, inst_name, sig_name))
        bridges_map[key] = bridge

    if not bridges_to_create:
        return lines

    # --- Step 5: emit output ---
    result = []
    injected = False

    for line in lines:
        if not injected:
            for inst_name in iface_instances:
                if re.search(rf'\b{re.escape(inst_name)}\s*\(', line):
                    result.append(line)
                    indent = re.match(r'(\s*)', line).group(1)
                    result.append(f"{indent}// [svpp] Verilator VIF combinational bridge signals")
                    for bridge, width, iname, sname in bridges_to_create:
                        width_decl = f" {width}" if width else ""
                        result.append(f"{indent}logic{width_decl} {bridge};")
                        result.append(
                            f"{indent}always_comb {bridge} = {iname}.{sname};"
                        )
                    injected = True
                    break
            else:
                result.append(line)
                continue
            continue

        new_line = line
        for key, bridge in bridges_map.items():
            inst_name, sig_name = key.split('.', 1)
            pattern = rf'(\.\w+\s*\()({re.escape(inst_name)}\.{re.escape(sig_name)})(\s*[,\)])'
            new_line = re.sub(pattern, rf'\g<1>{bridge}\g<3>', new_line)
        result.append(new_line)

    return result


# =====================================================================
#  Main Preprocessor
# =====================================================================

def preprocess_file(input_path, output_path=None, clk_half_period=5):
    """Preprocess a single SystemVerilog file."""
    with open(input_path, 'r') as f:
        lines = f.readlines()

    # Strip trailing newlines but preserve them for output
    raw_lines = [l.rstrip('\n') for l in lines]

    output_lines = []
    covergroups = []
    assertions = []
    properties = []  # List of PropertyDef
    property_dict = {}  # name -> PropertyDef
    skip_until = -1
    need_import = False
    has_sva_tick = False

    # First pass: identify all covergroups, properties, and SVA blocks
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

        # Detect standalone property block (property ... endproperty)
        # Must not match 'assert property', 'cover property', 'assume property'
        if re.match(r'property\s+\w+', stripped) and \
           not re.match(r'(assert|cover|assume)\s+property', stripped):
            prop, end_line = parse_property_block(raw_lines, i)
            if prop:
                properties.append(prop)
                property_dict[prop.name] = prop
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

    # Detect if this file contains a top-level UVM testbench module
    # (has both 'module' and 'run_test' — needs SVA infrastructure injection)
    has_module = any(re.match(r'\s*module\s+\w+', l) for l in raw_lines)
    has_run_test = any('run_test' in l for l in raw_lines)
    is_top_module = has_module and has_run_test
    if is_top_module:
        need_import = True

    # Resolve named property references in assertions
    for sva in assertions:
        prop_name = sva.property_expr.strip()
        if prop_name in property_dict:
            prop = property_dict[prop_name]
            # Use property's clock event if the assertion doesn't have one
            if not sva.clock_event and prop.clock_event:
                sva.clock_event = prop.clock_event
            # Use signals from the property body, not the property name
            sva.signals = list(prop.signals)
            # Store the full property expression for the DPI engine
            sva.property_expr = prop.body

    # Second pass: generate output
    # Build a set of line ranges to skip
    skip_ranges = set()
    for cg in covergroups:
        for l in range(cg.start_line, cg.end_line + 1):
            skip_ranges.add(l)
    for prop in properties:
        for l in range(prop.start_line, prop.end_line + 1):
            skip_ranges.add(l)
    for sva in assertions:
        for l in range(sva.start_line, sva.end_line + 1):
            skip_ranges.add(l)

    # Find the end of module/interface port list (line with ");") to insert import after it
    module_line = -1
    port_end_line = -1
    for i, line in enumerate(raw_lines):
        if re.match(r'\s*(module|interface)\s+', line):
            module_line = i
        if module_line >= 0 and port_end_line < 0:
            if re.search(r'\)\s*;', line):
                port_end_line = i
                break

    # If no port list found, insert after module line
    if port_end_line < 0:
        port_end_line = module_line
    # If no module/interface at all (e.g. class file `included in a package),
    # insert import at the top of the file so it's visible in the enclosing scope
    if port_end_line < 0 and need_import:
        port_end_line = 0

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
            is_prop_start = any(p.start_line == i for p in properties)
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

            elif is_prop_start:
                prop = next(p for p in properties if p.start_line == i)
                # Comment out the entire property block
                output_lines.append(f"    // [svpp] Original property '{prop.name}' "
                                    f"(lines {prop.start_line+1}-{prop.end_line+1}) "
                                    f"removed (referenced by DPI-C assertion)")

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
            if is_top_module:
                output_lines.append('    `include "sva_uvm_report.svh"')
            continue

        # For top module: inject sva_register_uvm_scope() into the first
        # 'initial begin ... run_test ...' block, and add a final block
        # before endmodule
        if is_top_module:
            stripped_line = line.strip()
            # Inject sva_register_uvm_scope() just before run_test()
            if re.match(r'run_test\s*\(', stripped_line):
                indent = re.match(r'(\s*)', line).group(1)
                output_lines.append(f"{indent}sva_register_uvm_scope();")
            # Inject final block before endmodule
            if re.match(r'endmodule\b', stripped_line):
                output_lines.append("    final begin")
                output_lines.append('        if ($test$plusargs("coverage"))')
                output_lines.append("            sva_final();")
                output_lines.append("    end")


        # --- Line-level transformations ---

        # Gate $dumpfile/$dumpvars behind +dump plusarg so VCD is only
        # produced when the simulation is invoked with +dump
        if re.match(r'\s*\$dumpfile\b', line):
            indent = re.match(r'(\s*)', line).group(1)
            output_lines.append(f'{indent}if ($test$plusargs("dump")) begin')
            output_lines.append(line)
            continue
        if re.match(r'\s*\$dumpvars\b', line):
            indent = re.match(r'(\s*)', line).group(1)
            output_lines.append(line)
            output_lines.append(f'{indent}end')
            continue

        # Verilator doesn't support clocking event edge override
        # ('output negedge' / 'output posedge').  Replace with a delay
        # equal to half the clock period so that signal changes settle at
        # the opposite edge — matching the original 'output negedge'
        # semantics and preventing same-cycle races between drivers and
        # monitors.
        if re.search(r'\boutput\s+(negedge|posedge)\b', line):
            line = re.sub(
                r'\boutput\s+(negedge|posedge)\b',
                f'output #{clk_half_period}', line)

        # Rewrite `include "X.sv" to `include "X.pp.sv" for preprocessed files
        # but not for files already named .pp.sv
        if '`include' in line and '.sv"' in line and '.pp.sv"' not in line:
            line = re.sub(r'(`include\s+")([^"]+)\.sv"', r'\1\2.pp.sv"', line)

        # Replace tristate literals (N'bz) with zero equivalents
        # Verilator does not support tristate non-blocking assignments
        if re.search(r"\d+'[bB][zZ]", line):
            line = re.sub(
                r"(\d+')([bB])([zZ]+)",
                lambda m: m.group(1) + m.group(2) + '0' * len(m.group(3)),
                line)

        # In class context, replace covergroup new()/sample() calls
        if covergroups:
            for cg in covergroups:
                # Replace: cg_name = new(); -> __svpp_cg_<name>_init();
                # Also handle: cg_name = new; (without parens)
                if re.search(rf'\b{cg.name}\s*=\s*new\b', line):
                    line = re.sub(
                        rf'\b{cg.name}\s*=\s*new\s*(?:\(\s*\))?\s*;',
                        f'__svpp_cg_{cg.name}_init();', line)
                # Replace: cg_name.sample(); -> __svpp_cg_<name>_sample();
                if re.search(rf'\b{cg.name}\.sample\b', line):
                    line = re.sub(
                        rf'\b{cg.name}\.sample\s*\(\s*\)\s*;',
                        f'__svpp_cg_{cg.name}_sample();', line)

        output_lines.append(line)

    # Post-processing: convert 'initial begin' to 'always begin' in blocks
    # that use non-blocking assignments and event controls (Verilator
    # INITIALDLY workaround — NBA in initial blocks is treated as blocking)
    output_lines = _fix_initial_nba(output_lines)

    # Post-processing: rewrite 'wait(vif.cb.signal)' to clock-edge polling
    # so Verilator respects clocking-block sampling semantics
    output_lines = _fix_wait_clocking_block(output_lines)

    # Post-processing: rewrite '@(posedge/negedge vif.cb.signal)' to
    # clock-edge-synchronized edge detection (Verilator may fire the
    # event on the raw signal transition rather than at the cb event)
    output_lines = _fix_edge_clocking_block(output_lines)

    # Post-processing: move super.new() before begin in constructors
    # Verilator (IEEE 1800-2023) requires super.new as the first statement
    output_lines = _fix_super_new(output_lines)

    # Post-processing: inject VPI-based drive functions into interfaces and
    # transform class-body VIF writes to use them (Verilator VIF scheduling
    # workaround — VPI writes trigger proper eval-loop re-evaluation)
    output_lines = _fix_vif_drive(output_lines)

    # Post-processing: flatten arrays of queues into individual variables
    # (Verilator bug: variable-indexed queue method calls return default values)
    output_lines = _fix_array_of_queues(output_lines)

    # Post-processing: inline combinational assign expressions into always_ff
    # blocks so Verilator evaluates them in the active path (not settle-only)
    output_lines = _fix_inline_comb_into_ff(output_lines)

    # Write output
    if output_path is None:
        output_path = input_path  # overwrite in place (typically to .pp.sv)

    with open(output_path, 'w') as f:
        for line in output_lines:
            f.write(line + '\n')

    return covergroups, assertions


def preprocess_directory(input_dir, output_dir, clk_half_period=5):
    """Preprocess all .sv files in a directory."""
    os.makedirs(output_dir, exist_ok=True)

    for fname in sorted(os.listdir(input_dir)):
        if fname.endswith('.sv'):
            in_path = os.path.join(input_dir, fname)
            out_path = os.path.join(output_dir, fname)
            cgs, svas = preprocess_file(in_path, out_path,
                                        clk_half_period=clk_half_period)
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
    parser.add_argument('--clk-half-period', type=int, default=5,
                        help='Half the clock period in time units '
                             '(used to replace "output negedge" with '
                             '"output #N"); default 5')

    args = parser.parse_args()

    if os.path.isdir(args.input):
        out_dir = args.output or (args.input if args.in_place
                                  else args.input + '_pp')
        print(f"svpp: preprocessing directory {args.input} -> {out_dir}")
        preprocess_directory(args.input, out_dir,
                             clk_half_period=args.clk_half_period)
    else:
        if args.in_place:
            out_file = args.input
        elif args.output:
            out_file = args.output
        else:
            base, ext = os.path.splitext(args.input)
            out_file = base + '.pp' + ext

        print(f"svpp: {args.input} -> {out_file}")
        cgs, svas = preprocess_file(args.input, out_file,
                                    clk_half_period=args.clk_half_period)
        print(f"  {len(cgs)} covergroup(s), {len(svas)} assertion(s) transformed")


if __name__ == '__main__':
    main()
