# Windows 후보를 포터블 ZIP으로 인도하기

이 도구는 기존 Windows 빌드 후보의 파일을 검증하여 ZIP과 SHA-256 체크섬을
새 디렉터리에 만듭니다. 프로그램을 빌드·설치·실행하거나 외부 솔버를 찾아 추가하지
않습니다. 후보 자체의 동봉 범위는 빌드 검토 대상입니다. ZIP 생성은 Windows 실화면
검사나 전체 의존성의 재배포 승인이 아닙니다.

## 입력과 실행

`scripts/build_windows_bundle.py`가 만든 후보가 필요합니다. 빌드 루트에는
`build-manifest.json`, 그 아래 `dist/CAELabWorkbench/`에는 실제 실행 파일과
런타임 파일이 있어야 합니다. 후보를 옮겼다면 이 상대 구조를 함께 유지합니다.
manifest에 남은 원래 PC의 절대 `bundle_root`는 실행 위치로 사용하지 않습니다.

```powershell
python scripts/archive_windows_bundle.py --manifest C:/candidates/wb/build-manifest.json --output C:/handoff/wb-portable
```

출력의 부모 디렉터리는 미리 존재해야 하며, 출력 디렉터리 자체는 없어야 합니다.
기존 출력이나 후보 안·소스 저장소 안을 지정하면 거부합니다. 정상 종료는 0,
검증·게시 실패는 1입니다. 실패 원인은 표준 오류로 출력합니다.

출력은 다음 두 파일입니다.

- `CAELabWorkbench-portable.zip`: 모든 payload는 `CAELabWorkbench/` 아래에 있습니다.
- `CAELabWorkbench-portable.zip.sha256`: ZIP의 SHA-256과 파일 이름입니다.

## 검증과 실패 시 보존

도구는 `built_static_only` 상태·빌드 종료 0·필수 정적 검사와 실제 파일 존재를
다시 확인합니다. inventory의 상대경로·개수·크기·SHA-256을 실제 후보와 대조하고
누락·추가·변조, 대소문자 중복, 경로 탈출, symlink/junction을 거부합니다.
선언된 optional 패키지의 metadata·고지 파일도 현재 bytes와 대조합니다.
metadata가 없는 기존 후보를 단순 재압축하여 통과시키지 않습니다.

임시 디렉터리에서 압축한 뒤 CRC와 모든 ZIP payload의 SHA-256을 확인하고,
원 manifest와 실제 후보 전체가 바뀌지 않았는지 다시 검사합니다. ZIP과 체크섬이
모두 준비되면 출력 디렉터리를 원자적으로 게시합니다. 기존 출력은 교체하지
않습니다. 게시 실패는 임시 출력을 정리하며 원본 후보·manifest는 수정하지 않습니다.
원자적 비교체 게시가 제공되지 않는 운영체제에서는 실패로 남깁니다.

공개 `CAELabWorkbench/portable-manifest.json`은 원 manifest의 SHA-256,
`source_revision`, baseline인 `base_revision`, 상대 source 파일 해시, 프로젝트와
의존 버전, 실제 payload inventory·optional 고지 범위만 기록합니다. `base_revision`을
packaging commit으로 바꾸지 않습니다. 아카이브 도구 자체의 해시는 별도 기록합니다.
원 manifest·빌드 로그·명령·PC의 절대 경로는 ZIP에 복사하지 않습니다. 고지 파일
내용은 원문 그대로 보존합니다.

현재 optional 고지 검사는 기존 빌더와 같은 Torch/torchvision 범위입니다.
이를 전체 동봉 라이선스 검토 완료로 표시하지 않습니다. 공개 기록의
`windows_runtime_validation`과 `redistribution_approval`도 이 아카이브 도구가
수행하거나 부여한 것으로 승격하지 않습니다. 실제 Windows 사용자 과업·설정
재열기·VTK·정상 종료와 전체 고지 검토는 고정된 동일 후보에서 별도로 확인합니다.
