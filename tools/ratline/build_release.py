#!/usr/bin/env python3
"""Assemble dist/<release>/KoreanPatch and the release zip from a verified runtime (stage 12).

Layout: zip root KoreanPatch/ {install.cmd, restore.cmd, README.txt, CHANGELOG.txt, LICENSES/, bin/, payload/}.
The patch never contains original game files: data.unity3d ships as an object delta, Assembly-CSharp.dll as a
binary delta, and only project-made files (Korean images, movies, helper DLL, word banks, licenses) as copies.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from baseline import REPO, now, sha, write_new  # noqa: E402

PY_VERSION = '3.13.13'
WHEELS = ['UnityPy==1.25.4', 'attrs==26.1.0', 'lz4==4.4.5', 'fsspec==2026.9.0', 'brotli==1.2.0', 'pillow==12.3.0']
PURE = ['tpk_ar', 'tpk_ar-0.2.4.dist-info']  # pure-Python, sdist only on PyPI: copied from the pinned build venv
PATCHER = REPO / 'tools/ratline/ko_patcher'
REPLACE_BY_DELTA = {'Ratlines_Data/data.unity3d': 'bundle-delta', 'Ratlines_Data/Managed/Assembly-CSharp.dll': 'bin-delta'}

CMD = '''@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0bin\\python\\python.exe" -I "%~dp0bin\\ratline_patch.py" {action}
set RC=%ERRORLEVEL%
echo.
pause
exit /b %RC%
'''


def fetch(url, dest):
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as r, open(dest, 'wb') as f:
            shutil.copyfileobj(r, f)
    return dest


def build_python(cache, bin_dir):
    embed = fetch(f'https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip', cache / f'python-{PY_VERSION}-embed-amd64.zip')
    py = bin_dir / 'python'
    with zipfile.ZipFile(embed) as z:
        z.extractall(py)
    wheels = cache / 'wheels'
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary', ':all:', '--platform', 'win_amd64',
                    '--python-version', '3.13', '--implementation', 'cp', '-d', str(wheels), *WHEELS], check=True, capture_output=True)
    site = py / 'Lib/site-packages'
    site.mkdir(parents=True)
    used = []
    for w in sorted(wheels.glob('*.whl')):
        if not any(w.name.lower().startswith(n.split('==')[0].lower().replace('-', '_')) for n in WHEELS):
            continue
        with zipfile.ZipFile(w) as z:
            z.extractall(site)
        used.append({'wheel': w.name, 'sha256': sha(w)})
    venv_site = REPO / '.venv-ratline/lib/python3.13/site-packages'
    for name in PURE:
        shutil.copytree(venv_site / name, site / name, ignore=shutil.ignore_patterns('__pycache__'))
        used.append({'pure_copy': name})
    pth = next(py.glob('python*._pth'))
    pth.write_text(pth.read_text().replace('.\n', '.\nLib\\site-packages\n', 1))
    return {'python_embed': {'file': embed.name, 'sha256': sha(embed)}, 'wheels': used}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release', required=True)
    p.add_argument('--version', required=True)
    p.add_argument('--payload-sources', type=Path, required=True)
    p.add_argument('--bundle-delta', type=Path, required=True)
    p.add_argument('--dll-delta', type=Path, required=True, help='.bdelta file (its .lit sibling is copied too)')
    p.add_argument('--docs', type=Path, required=True, help='directory with README.txt, CHANGELOG.txt, LICENSES/')
    p.add_argument('--cache', type=Path, required=True)
    a = p.parse_args()
    dist = REPO / 'dist' / a.release
    root = dist / 'KoreanPatch'
    if dist.exists():
        p.error('New release directory required')
    sources = json.loads(a.payload_sources.read_text())
    runtime = json.loads((REPO / sources['runtime_manifest']).read_text())
    original = {f['path']: f for f in json.loads((REPO / 'data/source/23882898/manifest.json').read_text())['files']}
    src = {s['path']: s for s in sources['sources']}
    root.mkdir(parents=True)
    (root / 'bin').mkdir()
    payload = root / 'payload'
    files = []
    space = 0
    for row in runtime['payload']:
        path = row['path']
        orig = original.get(path)
        entry = {'path': path, 'original_sha256': orig['sha256'] if orig else None, 'patched_sha256': row['sha256'], 'size': row['size']}
        if path in REPLACE_BY_DELTA:
            entry['method'] = REPLACE_BY_DELTA[path]
            if entry['method'] == 'bundle-delta':
                shutil.copytree(a.bundle_delta, payload / 'delta/data.unity3d')
                entry['payload'] = 'delta/data.unity3d'
                assert json.loads((a.bundle_delta / 'index.json').read_text())['target_sha256'] == row['sha256']
            else:
                (payload / 'delta').mkdir(parents=True, exist_ok=True)
                shutil.copyfile(a.dll_delta, payload / 'delta' / a.dll_delta.name)
                lit = a.dll_delta.parent / (a.dll_delta.name + '.lit')
                shutil.copyfile(lit, payload / 'delta' / lit.name)
                entry['payload'] = 'delta/' + a.dll_delta.name
        else:
            assert orig is None or path.startswith('Ratlines_Data/StreamingAssets/Video/Chinese/'), 'Only project-made files are copied: ' + path
            s = REPO / src[path]['source']
            assert sha(s) == row['sha256']
            dest = payload / 'files' / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, dest)
            entry['method'] = 'copy'
            entry['payload'] = 'files/' + path
        if orig:
            space += orig['size']
        space += row['size']
        files.append(entry)
    manifest = {'schema_version': 'ratline-ko-release/1', 'version': a.version, 'release': a.release, 'steam_app_id': '3756940',
                'steam_build': runtime['build_id'], 'runtime_revision': runtime['revision'], 'canonical_revision': runtime['canonical_revision'],
                'files': files, 'space_needed_bytes': space + (1 << 30)}
    (payload / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    for name in ['ratline_patch.py', 'bundle_apply.py']:
        shutil.copyfile(PATCHER / name, root / 'bin' / name)
    toolchain = build_python(a.cache, root / 'bin')
    for action in ['install', 'restore']:
        (root / f'{action}.cmd').write_bytes(CMD.format(action=action).replace('\n', '\r\n').encode('utf-8'))
    for item in a.docs.iterdir():
        target = root / item.name
        (shutil.copytree if item.is_dir() else shutil.copyfile)(item, target)
    sums = []
    for f in sorted(root.rglob('*')):
        if f.is_file():
            sums.append(f'{sha(f)}  {f.relative_to(dist).as_posix()}')
    (dist / 'SHA256SUMS-KoreanPatch.txt').write_text('\n'.join(sums) + '\n', encoding='utf-8')
    zpath = dist / f'{a.release}.zip'
    with zipfile.ZipFile(zpath, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(root.rglob('*')):
            if f.is_file():
                info = zipfile.ZipInfo(f.relative_to(dist).as_posix(), date_time=(2026, 10, 20, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED if f.suffix.lower() in ('.mp4', '.png', '.xz', '.whl', '.zip') else zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with open(f, 'rb') as fh:
                    z.writestr(info, fh.read())
    release_manifest = {'schema_version': 'ratline-ko-release-build/1', 'at': now(), 'release': a.release, 'version': a.version,
                        'zip': {'path': str(zpath.relative_to(REPO)), 'sha256': sha(zpath), 'size': zpath.stat().st_size},
                        'patch_manifest_sha256': sha(payload / 'manifest.json'), 'runtime_revision': runtime['revision'],
                        'toolchain': toolchain, 'files_in_zip': len(sums),
                        'excluded': ['original game files', 'source extracts', 'personal saves', 'work logs', 'QA artifacts']}
    write_new(dist / 'release-build.json', release_manifest)
    print(json.dumps({'zip': str(zpath.relative_to(REPO)), 'size_mb': round(zpath.stat().st_size / 2**20, 1), 'sha256': release_manifest['zip']['sha256']}))


if __name__ == '__main__':
    main()
