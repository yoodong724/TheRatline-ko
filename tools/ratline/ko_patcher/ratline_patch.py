"""The Ratline Korean patch installer / restorer.

Runs from <game>/KoreanPatch/bin with the bundled Python. The game folder is the parent of KoreanPatch.
Never touches saves (AppData/LocalLow) or the registry. Every change is hash-checked and journaled.
"""
import hashlib
import json
import lzma
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PATCH = Path(__file__).resolve().parents[1]
GAME = PATCH.parent
PAYLOAD = PATCH / 'payload'
BACKUP = PATCH / 'backup'
STATE = PATCH / 'state.json'
MANIFEST = json.loads((PAYLOAD / 'manifest.json').read_text(encoding='utf-8'))


def say(msg):
    print(msg, flush=True)


class Stop(Exception):
    pass


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def gp(rel):
    return GAME / Path(rel)


def load_state():
    return json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else None


def save_state(state):
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, STATE)


def game_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Ratlines.exe', '/NH'], capture_output=True, text=True).stdout
        return 'Ratlines.exe' in out
    except OSError:
        return False


def steam_build():
    acf = GAME.parent.parent / 'appmanifest_3756940.acf'
    if not acf.exists():
        return None
    import re
    m = re.search(r'"buildid"\s+"(\d+)"', acf.read_text(encoding='utf-8', errors='replace'))
    return m.group(1) if m else None


def check_game():
    if not (gp('Ratlines.exe').exists() and gp('Ratlines_Data').is_dir()):
        raise Stop('KoreanPatch 폴더를 게임 폴더(Ratlines.exe가 있는 폴더) 안에 넣고 다시 실행하세요.\n현재 상위 폴더: ' + str(GAME))
    if game_running():
        raise Stop('게임이 실행 중입니다. 게임을 종료한 뒤 다시 실행하세요.')


def classify():
    """Return (kind, details): original | patched | partial | mismatch."""
    orig_ok, patched_ok, bad = [], [], []
    for f in MANIFEST['files']:
        p = gp(f['path'])
        actual = sha(p) if p.exists() else None
        if actual == f['patched_sha256']:
            patched_ok.append(f['path'])
        elif actual == f['original_sha256']:
            orig_ok.append(f['path'])
        else:
            bad.append({'path': f['path'], 'actual': actual})
    n = len(MANIFEST['files'])
    if len(orig_ok) == n:
        return 'original', bad
    if len(patched_ok) == n:
        return 'patched', bad
    if bad:
        return 'mismatch', bad
    return 'partial', bad


def free_space_ok(need):
    usage = shutil.disk_usage(GAME)
    return usage.free > need


def copy_verified(src, dst, expected):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + '.ko-tmp')
    shutil.copyfile(src, tmp)
    if sha(tmp) != expected:
        tmp.unlink()
        raise Stop('복사 검증 실패: ' + str(dst))
    os.replace(tmp, dst)


def bin_delta_apply(original, delta_path, out, expected):
    ops = json.loads(lzma.decompress(delta_path.read_bytes()))
    src = original.read_bytes()
    lit = lzma.decompress((delta_path.parent / ops['literals']).read_bytes())
    buf = bytearray()
    for op in ops['ops']:
        if op[0] == 'c':
            buf += src[op[1]:op[1] + op[2]]
        else:
            buf += lit[op[1]:op[1] + op[2]]
    tmp = out.with_name(out.name + '.ko-tmp')
    tmp.write_bytes(bytes(buf))
    if sha(tmp) != expected:
        tmp.unlink()
        raise Stop('차분 적용 결과가 예상과 다릅니다: ' + str(out))
    os.replace(tmp, out)


def bundle_delta_apply(original, delta_dir, out, expected):
    sys.path.insert(0, str(PATCH / 'bin'))
    import bundle_apply
    tmp = out.with_name(out.name + '.ko-tmp')
    try:
        result = bundle_apply.apply(original, delta_dir, tmp)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    import gc
    gc.collect()
    if result != expected:
        tmp.unlink()
        raise Stop('번들 적용 결과가 예상과 다릅니다(' + result[:12] + ').')
    os.replace(tmp, out)


def rollback(state):
    say('오류가 발생해 원래 상태로 되돌립니다...')
    for f in reversed(MANIFEST['files']):
        p = gp(f['path'])
        if f['original_sha256'] is None:
            if p.exists() and f['path'] in state.get('applied', []):
                p.unlink()
        else:
            b = BACKUP / f['path']
            if b.exists() and sha(b) == f['original_sha256'] and (not p.exists() or sha(p) != f['original_sha256']):
                copy_verified(b, p, f['original_sha256'])
    remove_empty_dirs()
    kind, bad = classify()
    if kind == 'original':
        shutil.rmtree(BACKUP, ignore_errors=True)
        if STATE.exists():
            STATE.unlink()
        say('원래 상태로 되돌렸습니다(원본 확인 완료, 백업 삭제).')
    else:
        state['status'] = 'rollback_incomplete'
        save_state(state)
        say('자동 복구가 끝나지 않았습니다. restore.cmd를 실행하거나 Steam "게임 파일 무결성 확인"을 사용하세요. 백업은 남겨 두었습니다.')


def remove_empty_dirs():
    for d in sorted({str(Path(f['path']).parent) for f in MANIFEST['files'] if f['original_sha256'] is None}, key=len, reverse=True):
        p = gp(d)
        while p != GAME and p.exists() and p.is_dir() and not any(p.iterdir()):
            p.rmdir()
            p = p.parent


def install():
    check_game()
    state = load_state()
    kind, bad = classify()
    if kind == 'patched':
        if state and state.get('version') == MANIFEST['version']:
            raise Stop('이미 이 버전(' + MANIFEST['version'] + ')이 설치되어 있습니다.')
        raise Stop('한글 패치가 이미 적용된 상태입니다. restore.cmd로 복원한 뒤 다시 설치하세요.')
    if state and state.get('status') in ('installed', 'installing') and kind != 'original':
        raise Stop('다른 버전의 한글 패치(' + str(state.get('version')) + ')가 설치되어 있거나 설치가 끝나지 않았습니다. restore.cmd를 먼저 실행하세요.')
    if kind == 'partial':
        raise Stop('이전 설치나 복원이 끝나지 않은 상태입니다. restore.cmd를 먼저 실행하세요.')
    if kind != 'original':
        build = steam_build()
        lines = '\n'.join('  ' + b['path'] for b in bad)
        hint = ('Steam 빌드 ' + build + '은(는) 이 패치의 지원 빌드 ' + MANIFEST['steam_build'] + '와 다릅니다.') if build and build != MANIFEST['steam_build'] else \
               '게임 파일이 원본과 다릅니다. Steam에서 "게임 파일 무결성 확인"을 한 뒤 다시 시도하세요.'
        raise Stop('설치를 중단했습니다. ' + hint + '\n원본과 다른 파일:\n' + lines)
    build = steam_build()
    if build and build != MANIFEST['steam_build']:
        raise Stop('Steam 빌드 ' + build + '은(는) 지원 빌드 ' + MANIFEST['steam_build'] + '와 다릅니다. 설치하지 않습니다.')
    if not free_space_ok(MANIFEST['space_needed_bytes']):
        raise Stop('디스크 공간이 부족합니다. 약 ' + str(MANIFEST['space_needed_bytes'] // (1 << 20)) + 'MB가 필요합니다.')
    if BACKUP.exists():
        shutil.rmtree(BACKUP)
    state = {'version': MANIFEST['version'], 'status': 'installing', 'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'backed_up': [], 'applied': []}
    save_state(state)
    try:
        say('[1/3] 원본 백업')
        for f in MANIFEST['files']:
            if f['original_sha256'] is None:
                continue
            copy_verified(gp(f['path']), BACKUP / f['path'], f['original_sha256'])
            state['backed_up'].append(f['path']); save_state(state)
        say('[2/3] 한글 패치 적용')
        for f in MANIFEST['files']:
            say('  ' + f['path'])
            p = gp(f['path'])
            if f['method'] == 'copy':
                copy_verified(PAYLOAD / f['payload'], p, f['patched_sha256'])
            elif f['method'] == 'bin-delta':
                bin_delta_apply(BACKUP / f['path'], PAYLOAD / f['payload'], p, f['patched_sha256'])
            elif f['method'] == 'bundle-delta':
                bundle_delta_apply(BACKUP / f['path'], PAYLOAD / f['payload'], p, f['patched_sha256'])
            else:
                raise Stop('알 수 없는 적용 방식: ' + f['method'])
            state['applied'].append(f['path']); save_state(state)
        say('[3/3] 설치 결과 확인')
        kind, bad = classify()
        if kind != 'patched':
            raise Stop('설치 후 확인 실패: ' + ', '.join(b['path'] for b in bad))
    except BaseException as error:
        rollback(state)
        if isinstance(error, Stop):
            raise
        raise Stop('설치 중 오류: ' + repr(error))
    state['status'] = 'installed'; state['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
    save_state(state)
    say('설치 완료. 게임 설정에서 언어를 "한국어"로 선택하세요.')


def restore():
    check_game()
    state = load_state()
    kind, bad = classify()
    if kind == 'original' and not BACKUP.exists():
        if STATE.exists():
            STATE.unlink()
        raise Stop('한글 패치가 설치되어 있지 않습니다. 게임 파일은 이미 원본 상태입니다.')
    if not BACKUP.exists():
        raise Stop('백업이 없어 복원할 수 없습니다. Steam에서 "게임 파일 무결성 확인"으로 원본을 복구하세요.')
    for f in MANIFEST['files']:
        if f['original_sha256'] is not None:
            p, b = gp(f['path']), BACKUP / f['path']
            if p.exists() and sha(p) == f['original_sha256']:
                continue
            if not b.exists() or sha(b) != f['original_sha256']:
                raise Stop('백업 파일이 손상되었습니다: ' + f['path'] + '\nSteam에서 "게임 파일 무결성 확인"으로 원본을 복구하세요. 백업은 지우지 않았습니다.')
    state = state or {'version': MANIFEST['version']}
    state['status'] = 'restoring'; save_state(state)
    say('[1/2] 원본 복원')
    for f in MANIFEST['files']:
        p = gp(f['path'])
        if f['original_sha256'] is None:
            if p.exists():
                p.unlink()
        elif not p.exists() or sha(p) != f['original_sha256']:
            copy_verified(BACKUP / f['path'], p, f['original_sha256'])
    remove_empty_dirs()
    say('[2/2] 복원 결과 확인')
    kind, bad = classify()
    if kind != 'original':
        raise Stop('복원 확인 실패: ' + ', '.join(b['path'] for b in bad) + '\n백업은 지우지 않았습니다.')
    shutil.rmtree(BACKUP)
    STATE.unlink()
    say('복원 완료. 백업을 삭제했습니다. KoreanPatch 폴더는 다시 설치할 때 쓸 수 있습니다.')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    say('The Ratline 한글 패치 ' + MANIFEST['version'] + ' (Steam 빌드 ' + MANIFEST['steam_build'] + ')')
    try:
        {'install': install, 'restore': restore}[cmd]()
        sys.exit(0)
    except Stop as stop:
        say('\n' + str(stop))
        sys.exit(1)
    except KeyError:
        say('사용법: install.cmd 또는 restore.cmd를 실행하세요.')
        sys.exit(2)
