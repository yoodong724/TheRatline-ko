#!/usr/bin/env python3
"""Object-level delta for Ratlines_Data/data.unity3d (stage 12 packaging).

make:  original bundle + patched bundle -> delta directory (index.json + one lzma blob per object).
apply: original bundle + delta -> new bundle; changed objects get their raw bytes, added objects are
       cloned from an existing object of the same serialized type and given the recorded raw bytes.
       Saved with the same lz4 packer as the build. Hashes are checked before and after.
Only UnityPy 1.25.4 is supported (same as the build).
"""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import lz4.block
import UnityPy
from attrs import evolve
from UnityPy.helpers import CompressionHelper

INDEX = 'index.json'


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def serialized(env):
    return {name: f for name, f in env.file.files.items() if hasattr(f, 'objects')}


def type_index(obj):
    return obj.assets_file.types.index(obj.serialized_type) if obj.serialized_type in obj.assets_file.types else None


def make(original, patched, out):
    assert UnityPy.__version__ == '1.25.4'
    out.mkdir(parents=True, exist_ok=False)
    src, dst = UnityPy.load(str(original)), UnityPy.load(str(patched))
    sf, df = serialized(src), serialized(dst)
    assert set(sf) == set(df), 'Bundle file set differs'
    for name in src.file.files:
        if name not in sf:
            assert bytes(src.file.files[name].bytes) == bytes(dst.file.files[name].bytes), name
    rows = []
    for name in sorted(df):
        a, b = sf[name], df[name]
        assert len(a.types) <= len(b.types) and all(x.class_id == y.class_id for x, y in zip(a.types, b.types)), name
        assert len(a.types) == len(b.types), f'New serialized types in {name} are not supported'
        assert not [k for k in a.objects if k not in b.objects], f'Removed objects in {name}'
        for pid, obj in b.objects.items():
            raw = obj.get_raw_data()
            if pid in a.objects and a.objects[pid].get_raw_data() == raw:
                continue
            kind = 'changed' if pid in a.objects else 'added'
            blob = f'{len(rows):05d}.xz'
            (out / blob).write_bytes(lzma.compress(raw, preset=6))
            rows.append(dict(file=name, path_id=pid, kind=kind, class_id=obj.class_id, type=obj.type.name,
                             type_index=type_index(obj), size=len(raw), sha256=sha_bytes(raw), blob=blob))
    index = dict(schema_version='ratline-bundle-delta/1', unitypy='1.25.4', packer='lz4 fast acceleration=1',
                 original_sha256=sha_file(original), original_size=original.stat().st_size,
                 target_sha256=sha_file(patched), target_size=patched.stat().st_size, objects=rows)
    (out / INDEX).write_text(json.dumps(index, ensure_ascii=False, indent=1))
    return index


def apply(original, delta, out):
    assert UnityPy.__version__ == '1.25.4'
    index = json.loads((delta / INDEX).read_text())
    if sha_file(original) != index['original_sha256']:
        raise SystemExit('ORIGINAL_MISMATCH')
    env = UnityPy.load(str(original))
    files = serialized(env)
    for row in index['objects']:
        raw = lzma.decompress((delta / row['blob']).read_bytes())
        if sha_bytes(raw) != row['sha256']:
            raise SystemExit('DELTA_CORRUPT ' + row['blob'])
        f = files[row['file']]
        if row['kind'] == 'changed':
            obj = f.objects[row['path_id']]
            assert obj.class_id == row['class_id']
            obj.set_raw_data(raw)
        else:
            template = next(o for o in f.objects.values()
                            if o.class_id == row['class_id'] and type_index(o) == row['type_index'])
            clone = evolve(template, path_id=row['path_id'], data=raw)
            f.objects[row['path_id']] = clone
    compressor = CompressionHelper.COMPRESSION_MAP[2]
    try:
        CompressionHelper.COMPRESSION_MAP[2] = lambda data: lz4.block.compress(data, mode='fast', acceleration=1, store_size=False)
        data = env.file.save(packer='lz4')
    finally:
        CompressionHelper.COMPRESSION_MAP[2] = compressor
    tmp = out.with_suffix(out.suffix + '.partial')
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    result = sha_file(tmp)
    tmp.replace(out)
    return dict(output_sha256=result, byte_identical=result == index['target_sha256'], target_sha256=index['target_sha256'])


def verify(candidate, delta):
    """Per-object equality of an applied bundle against the delta's recorded target objects."""
    index = json.loads((delta / INDEX).read_text())
    env = UnityPy.load(str(candidate))
    files = serialized(env)
    bad = []
    for row in index['objects']:
        o = files[row['file']].objects.get(row['path_id'])
        if o is None or sha_bytes(o.get_raw_data()) != row['sha256']:
            bad.append([row['file'], row['path_id']])
    return dict(objects_checked=len(index['objects']), mismatches=bad)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('make'); m.add_argument('--original', type=Path, required=True); m.add_argument('--patched', type=Path, required=True); m.add_argument('--out', type=Path, required=True)
    a = sub.add_parser('apply'); a.add_argument('--original', type=Path, required=True); a.add_argument('--delta', type=Path, required=True); a.add_argument('--out', type=Path, required=True)
    v = sub.add_parser('verify'); v.add_argument('--candidate', type=Path, required=True); v.add_argument('--delta', type=Path, required=True)
    args = p.parse_args()
    if args.cmd == 'make':
        idx = make(args.original, args.patched, args.out)
        print(json.dumps({'objects': len(idx['objects']), 'raw_bytes': sum(r['size'] for r in idx['objects'])}))
    elif args.cmd == 'apply':
        print(json.dumps(apply(args.original, args.delta, args.out)))
    else:
        print(json.dumps(verify(args.candidate, args.delta)))
