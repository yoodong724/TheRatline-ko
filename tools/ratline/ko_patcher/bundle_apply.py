"""Installer-side application of the data.unity3d object delta (same algorithm as tools/ratline/bundle_delta.py)."""
import hashlib
import json
import lzma
import os
from pathlib import Path
import lz4.block
import UnityPy
from attrs import evolve
from UnityPy.helpers import CompressionHelper


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def apply(original, delta, out):
    assert UnityPy.__version__ == '1.25.4'
    delta = Path(delta)
    index = json.loads((delta / 'index.json').read_text())
    # Load from memory so no file handle on the original/backup stays open (Windows cannot delete open files).
    env = UnityPy.load(Path(original).read_bytes())
    files = {n: f for n, f in env.file.files.items() if hasattr(f, 'objects')}
    for row in index['objects']:
        raw = lzma.decompress((delta / row['blob']).read_bytes())
        if _sha(raw) != row['sha256']:
            raise ValueError('delta blob corrupt: ' + row['blob'])
        f = files[row['file']]
        if row['kind'] == 'changed':
            obj = f.objects[row['path_id']]
            assert obj.class_id == row['class_id']
            obj.set_raw_data(raw)
        else:
            template = next(o for o in f.objects.values() if o.class_id == row['class_id']
                            and o.assets_file.types.index(o.serialized_type) == row['type_index'])
            f.objects[row['path_id']] = evolve(template, path_id=row['path_id'], data=raw)
    saved = CompressionHelper.COMPRESSION_MAP[2]
    try:
        CompressionHelper.COMPRESSION_MAP[2] = lambda d: lz4.block.compress(d, mode='fast', acceleration=1, store_size=False)
        data = env.file.save(packer='lz4')
    finally:
        CompressionHelper.COMPRESSION_MAP[2] = saved
    with open(out, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    del env, data
    h = hashlib.sha256()
    with open(out, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()
