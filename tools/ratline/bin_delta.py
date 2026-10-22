#!/usr/bin/env python3
"""Copy/insert binary delta for small files (Assembly-CSharp.dll). Output: <name>.bdelta (lzma JSON ops) + <name>.lit (lzma literals)."""
import argparse, hashlib, json, lzma
from pathlib import Path
BLOCK = 32


def make(src, dst):
    index = {}
    for i in range(0, len(src) - BLOCK + 1):
        index.setdefault(src[i:i + BLOCK], i)
    ops, lit = [], bytearray()
    i = 0
    pending = bytearray()
    def flush():
        if pending:
            ops.append(['l', len(lit), len(pending)]); lit.extend(pending); pending.clear()
    while i < len(dst):
        j = index.get(dst[i:i + BLOCK]) if i + BLOCK <= len(dst) else None
        if j is None:
            pending.append(dst[i]); i += 1; continue
        n = BLOCK
        while i + n < len(dst) and j + n < len(src) and dst[i + n] == src[j + n]:
            n += 1
        flush(); ops.append(['c', j, n]); i += n
    flush()
    return ops, bytes(lit)


def apply(src, ops, lit):
    out = bytearray()
    for op in ops:
        out += (src if op[0] == 'c' else lit)[op[1]:op[1] + op[2]]
    return bytes(out)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original', type=Path, required=True); p.add_argument('--patched', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    src, dst = a.original.read_bytes(), a.patched.read_bytes()
    ops, lit = make(src, dst)
    assert apply(src, ops, lit) == dst
    lit_name = a.out.name + '.lit'
    a.out.write_bytes(lzma.compress(json.dumps({'literals': lit_name, 'ops': ops}).encode()))
    (a.out.parent / lit_name).write_bytes(lzma.compress(lit))
    print(json.dumps({'ops': len(ops), 'literal_bytes': len(lit), 'delta_bytes': a.out.stat().st_size + (a.out.parent / lit_name).stat().st_size,
                      'patched_sha256': hashlib.sha256(dst).hexdigest()}))
