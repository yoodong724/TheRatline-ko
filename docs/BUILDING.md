# 공개 제작 도구 안내

## 공개 범위

이 저장소는 설치기와 배포용 차분·패키지를 만드는 최소 도구 소스입니다. 게임에 표시되는 문안이나 이미지·영상, 번역 데이터, 문안이 들어 있는 제작 코드, 작업 기록은 포함하지 않습니다.

**소스와 원본 게임만으로 완성된 한글패치를 재현할 수 없습니다.** 최종 번역/조판 결과, 검증된 게임 번들과 보정 DLL, 런타임 manifest·payload 경로표, 폰트와 영상 등 비공개 빌드 입력이 추가로 필요합니다. 아래 명령은 그 입력을 별도로 갖춘 제작 환경을 위한 사용법입니다. 플레이어는 Releases의 설치용 ZIP을 사용하세요.

| 파일 | 역할 |
|---|---|
| `tools/ratline/ko_patcher/ratline_patch.py` | 설치·백업·적용 결과 확인·실패 복구·제거 |
| `tools/ratline/ko_patcher/bundle_apply.py` | 설치기에서 Unity 오브젝트 차분 적용 |
| `tools/ratline/bundle_delta.py` | 검증된 원본/수정 번들 간 오브젝트 차분 생성·적용·비교 |
| `tools/ratline/bin_delta.py` | 관리 DLL의 복사/삽입 바이너리 차분 생성 |
| `tools/ratline/build_release.py` | 별도로 준비한 입력으로 `KoreanPatch/`와 설치용 ZIP 조립 |
| `tools/ratline/baseline.py` | 조립 도구가 쓰는 경로·시각·SHA-256·새 JSON 파일 저장 함수만 발췌 |

`.gitignore`는 공개 대상 파일을 명시한 허용 목록입니다. 번역·이미지·payload·원본 게임을 이 저장소에 추가하지 마세요. GitHub의 자동 `Source code` 압축에도 같은 소스 파일만 들어 있습니다.

## 환경

- 제작 도구: Python **3.13**, `requirements.txt`의 고정 의존성. UnityPy **1.25.4**로 생성·검증했습니다.
- 설치용 ZIP: Windows용 Python **3.13.13**과 최소 의존성을 포함하므로 사용자가 Python을 따로 설치하지 않습니다.
- 제3자 라이브러리·폰트의 고지는 설치용 ZIP에 포함합니다. 원본 게임·폰트 바이너리는 이 소스 저장소에 넣지 않습니다.

```bash
python3.13 -m venv .venv-ratline
.venv-ratline/bin/python -m pip install -r requirements.txt
```

## 추가로 필요한 비공개 입력

1. 지원 빌드의 정품 원본 파일과 파일 경로·크기·SHA-256을 기록한 `data/source/23882898/manifest.json`.
2. 번역·이미지·영상·글꼴·실행 보정을 적용하고 검수한 번들/DLL/추가 파일.
3. `runtime/<build>/<revision>/manifest.json`: `build_id`, `revision`, `canonical_revision`, `payload` 등을 가진 런타임 manifest. `payload`에는 각 게임 상대 경로, 크기, 해시, 교체/추가 방식이 필요합니다.
4. `--payload-sources` JSON: `runtime_manifest`와 각 payload의 게임 경로(`path`)·제작 파일 경로(`source`)·해시(`sha256`)를 연결하는 `sources` 배열.
5. `README.txt`, `CHANGELOG.txt`, `LICENSES/`가 있는 배포 문서 폴더. Windows Python embeddable 환경과 패키지 휠을 내려받는 캐시도 사용합니다. 조립 도구는 순수 Python 의존성 `tpk_ar`를 `.venv-ratline/lib/python3.13/site-packages`에서 복사합니다.

이 입력들은 공개 소스에서 누락된 오류가 아니라 의도적으로 배포하지 않는 게임 콘텐츠입니다.

## 차분 생성·조립

원본 설치본은 읽기 전용 기준으로 두고, 검증된 수정 파일과 새 출력 경로를 사용합니다.

```bash
# Unity 번들 오브젝트 차분
.venv-ratline/bin/python tools/ratline/bundle_delta.py make \
  --original /path/to/original/data.unity3d \
  --patched /path/to/validated/data.unity3d --out build/new-bundle-delta

# 원본에서 수정 번들이 정확히 재구성되는지 확인
.venv-ratline/bin/python tools/ratline/bundle_delta.py apply \
  --original /path/to/original/data.unity3d \
  --delta build/new-bundle-delta --out build/reconstructed.unity3d

# DLL 차분: .bdelta와 .bdelta.lit가 함께 생성됨
.venv-ratline/bin/python tools/ratline/bin_delta.py \
  --original /path/to/original/Assembly-CSharp.dll \
  --patched /path/to/validated/Assembly-CSharp.dll \
  --out build/Assembly-CSharp.dll.bdelta

# 별도로 준비한 manifest와 제작물로 설치용 ZIP 조립
.venv-ratline/bin/python tools/ratline/build_release.py \
  --release ratline-ko-23882898-rc3 --version ratline-ko-23882898-rc3 \
  --payload-sources /path/to/private-payload-sources.json \
  --bundle-delta build/new-bundle-delta \
  --dll-delta build/Assembly-CSharp.dll.bdelta \
  --docs /path/to/release-docs --cache build/release-cache
```

`build_release.py`는 `dist/<release>/`가 이미 있으면 덮어쓰지 않습니다. 생성한 파일과 manifest의 해시를 비교하고, 별도 테스트 사본에서 설치·제거·실패 복구와 변경 범위의 게임 표시를 확인해야 합니다. **도구 실행 성공은 번역·이미지·플레이 검수 통과를 뜻하지 않습니다.**
