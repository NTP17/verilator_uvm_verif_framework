#!/usr/bin/env python3
"""Merge multiple SVA ENGINE REPORT files into a single accumulated report.

Sums assertion counts and coverage bin hits across all input files,
then recalculates percentages and outputs a unified report.

Usage: merger.py report1.txt report2.txt ... [-o merged.txt]
"""
import re
import sys
from collections import OrderedDict


def parse_report(path):
    """Parse a single SVA report file into structured data."""
    with open(path) as f:
        text = f.read()

    cycles = 0
    m = re.search(r'Simulation cycles:\s*(\d+)', text)
    if m:
        cycles = int(m.group(1))

    # Parse assertions
    assertions = OrderedDict()
    for m in re.finditer(
        r'\[(\w+)\]\s+(\S+)(\s+\([^)]+\))?\n'
        r'\s+pass:\s*(\d+)\s+fail:\s*(\d+)\s+vacuous:\s*(\d+)\s+attempts:\s*(\d+)',
        text
    ):
        kind, name = m.group(1), m.group(2)
        loc = m.group(3).strip() if m.group(3) else ''
        key = (kind, name, loc)
        counts = (int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)))
        assertions[key] = counts

    # Parse covergroups
    covergroups = OrderedDict()
    cg_pattern = re.compile(
        r'covergroup:\s+(\S+)\s+\([^,]+,\s*(\d+)\s+samples\)')
    cp_pattern = re.compile(
        r'coverpoint:\s+(\S+)\s+\(\S+\s+-\s+\d+/(\d+)\s+bins hit\)')
    bin_pattern = re.compile(
        r'^\s+[% ]\s+(\S+)\s+(.*?)\s+hits:\s*(\d+)\s*$')
    cross_header = re.compile(
        r'cross:\s+(\S+)\s+\(\S+\s+-\s+\d+/(\d+)\s+bins hit\)')
    cross_bin = re.compile(
        r'^\s+<(.+?)>\s+hits:\s*(\d+)\s*$')

    current_cg = None
    current_cp = None
    current_cross = None
    in_cross = False

    for line in text.splitlines():
        m = cg_pattern.search(line)
        if m:
            cg_name = m.group(1)
            samples = int(m.group(2))
            if cg_name not in covergroups:
                covergroups[cg_name] = {
                    'samples': 0, 'coverpoints': OrderedDict(),
                    'crosses': OrderedDict()}
            covergroups[cg_name]['samples'] += samples
            current_cg = covergroups[cg_name]
            current_cp = None
            current_cross = None
            in_cross = False
            continue

        if current_cg is not None:
            m = cross_header.search(line)
            if m:
                cross_name = m.group(1)
                total = int(m.group(2))
                if cross_name not in current_cg['crosses']:
                    current_cg['crosses'][cross_name] = {
                        'total': total, 'bins': OrderedDict()}
                current_cross = current_cg['crosses'][cross_name]
                current_cp = None
                in_cross = True
                continue

            m = cp_pattern.search(line)
            if m:
                cp_name = m.group(1)
                total = int(m.group(2))
                if cp_name not in current_cg['coverpoints']:
                    current_cg['coverpoints'][cp_name] = {
                        'total': total, 'bins': OrderedDict()}
                current_cp = current_cg['coverpoints'][cp_name]
                current_cross = None
                in_cross = False
                continue

            if in_cross and current_cross is not None:
                m = cross_bin.match(line)
                if m:
                    key = m.group(1)
                    hits = int(m.group(2))
                    current_cross['bins'][key] = \
                        current_cross['bins'].get(key, 0) + hits
                    continue

            if current_cp is not None and not in_cross:
                m = bin_pattern.match(line)
                if m:
                    bname = m.group(1)
                    meta = m.group(2).strip()
                    hits = int(m.group(3))
                    if bname not in current_cp['bins']:
                        current_cp['bins'][bname] = {'meta': meta, 'hits': 0}
                    current_cp['bins'][bname]['hits'] += hits
                    continue

    return cycles, assertions, covergroups


def merge(files):
    total_cycles = 0
    merged_asserts = OrderedDict()
    merged_cgs = OrderedDict()

    for path in files:
        cycles, asserts, cgs = parse_report(path)
        total_cycles += cycles

        for key, (p, f, v, a) in asserts.items():
            if key in merged_asserts:
                op, of, ov, oa = merged_asserts[key]
                merged_asserts[key] = (op + p, of + f, ov + v, oa + a)
            else:
                merged_asserts[key] = (p, f, v, a)

        for cg_name, cg in cgs.items():
            if cg_name not in merged_cgs:
                merged_cgs[cg_name] = {
                    'samples': 0,
                    'coverpoints': OrderedDict(),
                    'crosses': OrderedDict()}
            mc = merged_cgs[cg_name]
            mc['samples'] += cg['samples']

            for cp_name, cp in cg['coverpoints'].items():
                if cp_name not in mc['coverpoints']:
                    mc['coverpoints'][cp_name] = {
                        'total': cp['total'], 'bins': OrderedDict()}
                mcp = mc['coverpoints'][cp_name]
                for bname, bdata in cp['bins'].items():
                    if bname not in mcp['bins']:
                        mcp['bins'][bname] = {
                            'meta': bdata['meta'], 'hits': 0}
                    mcp['bins'][bname]['hits'] += bdata['hits']

            for x_name, x in cg['crosses'].items():
                if x_name not in mc['crosses']:
                    mc['crosses'][x_name] = {
                        'total': x['total'], 'bins': OrderedDict()}
                mx = mc['crosses'][x_name]
                for key, hits in x['bins'].items():
                    mx['bins'][key] = mx['bins'].get(key, 0) + hits

    return total_cycles, merged_asserts, merged_cgs


def _is_excluded(meta):
    """ILLEGAL and IGNORE bins are excluded from hit/total counts."""
    return '[ILLEGAL]' in meta or '[IGNORE]' in meta


def _cp_stats(cp):
    """Return (bins_hit, total_bins) excluding ILLEGAL/IGNORE."""
    hit = sum(1 for b in cp['bins'].values()
              if b['hits'] > 0 and not _is_excluded(b['meta']))
    total = sum(1 for b in cp['bins'].values()
                if not _is_excluded(b['meta']))
    return hit, total


def format_report(cycles, asserts, cgs):
    lines = []
    lines.append('=' * 64)
    lines.append('  SVA ENGINE REPORT (merged)')
    lines.append('=' * 64)
    lines.append(f'  Simulation cycles: {cycles}')
    lines.append('-' * 64)

    if asserts:
        lines.append('')
        lines.append('  ASSERTIONS:')
        for (kind, name, loc), (p, f, v, a) in asserts.items():
            loc_str = f' {loc}' if loc else ''
            lines.append(f'    [{kind}] {name}{loc_str}')
            lines.append(
                f'      pass: {p}  fail: {f}  vacuous: {v}  attempts: {a}')

    if cgs:
        lines.append('')
        lines.append('  COVERGROUPS:')
        for cg_name, cg in cgs.items():
            # Covergroup % = average of per-coverpoint and per-cross %
            # (matches the C++ engine's Covergroup::coverage_pct())
            pct_sum = 0.0
            n_items = 0
            for cp in cg['coverpoints'].values():
                hit, total = _cp_stats(cp)
                pct_sum += (100.0 * hit / total) if total else 100.0
                n_items += 1
            for x in cg['crosses'].values():
                x_hit = sum(1 for h in x['bins'].values() if h > 0)
                x_pct = (100.0 * x_hit / x['total']) if x['total'] else 100.0
                pct_sum += x_pct
                n_items += 1
            cg_pct = (pct_sum / n_items) if n_items else 100.0
            lines.append('')
            lines.append(
                f'  covergroup: {cg_name}  '
                f'({cg_pct:.1f}% coverage, {cg["samples"]} samples)')

            for cp_name, cp in cg['coverpoints'].items():
                hit, total = _cp_stats(cp)
                cp_pct = (100.0 * hit / total) if total else 100.0
                lines.append(
                    f'    coverpoint: {cp_name}  '
                    f'({cp_pct:.1f}% - {hit}/{total} bins hit)')
                for bname, bdata in cp['bins'].items():
                    mark = ' ' if bdata['hits'] > 0 else '%'
                    meta = bdata['meta']
                    meta_str = f'  {meta}' if meta else ''
                    lines.append(
                        f'      {mark} {bname:<20s}{meta_str}  '
                        f'hits: {bdata["hits"]}')

            for x_name, x in cg['crosses'].items():
                hit = sum(1 for h in x['bins'].values() if h > 0)
                total = x['total']
                x_pct = (100.0 * hit / total) if total else 100.0
                lines.append(
                    f'    cross: {x_name}  '
                    f'({x_pct:.1f}% - {hit}/{total} bins hit)')
                for key, hits in x['bins'].items():
                    lines.append(f'      <{key}>  hits: {hits}')

    lines.append('')
    lines.append('=' * 64)
    lines.append('')
    return '\n'.join(lines)


def main():
    args = sys.argv[1:]
    out_path = None
    if '-o' in args:
        idx = args.index('-o')
        out_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if not args:
        print(f'Usage: {sys.argv[0]} report1.txt [report2.txt ...] [-o out.txt]',
              file=sys.stderr)
        sys.exit(1)

    cycles, asserts, cgs = merge(args)
    report = format_report(cycles, asserts, cgs)

    if out_path:
        with open(out_path, 'w') as f:
            f.write(report)
    else:
        print(report)


if __name__ == '__main__':
    main()
