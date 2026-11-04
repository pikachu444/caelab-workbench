# Windows 설치형 준비와 검수 — WB-INSTALLER-1

이 설치형은 기존 Windows 앱 번들을 입력으로 사용합니다. 앱을 다시 빌드하거나
외부 솔버를 설치하지 않습니다. 제품 기준은 [DESIGN](../../DESIGN.md), 순서와
인도 조건은 [기존 P1~P7 계획](../review/2026-10-23-codex-improvement-plan.md)을
따릅니다. [N13 설치형 일정](https://github.com/pikachu444/caelab-workbench/issues/79#issuecomment-6624092778)의
11/8 18:00 KST는 실제 검수 전까지 조건부 목표입니다.

## 입력과 명령

입력은 기존 `build_windows_bundle.py`의 `build-manifest.json` 하나입니다.
source/base revision은 caller-supplied 기록으로 보존합니다. 기존 아카이브 도구가
후보의 파일 목록·해시·정적 의존·선언 고지를 검사해 임시 ZIP을 만들고, 설치형
준비 도구가 그 ZIP의 정확한 멤버·상대경로·바이트 해시를 확인해 고정 snapshot을
만듭니다. 원 후보나 사용자 프로젝트·설정에는 쓰지 않습니다.

```powershell
python scripts/build_windows_installer.py --manifest <build-output>/build-manifest.json --output <새-준비-폴더> --prepare-only
python scripts/build_windows_installer.py --manifest <build-output>/build-manifest.json --output <새-컴파일-폴더> --compiler <이미-사용-가능한-ISCC.exe>
```

출력 경로는 소스/후보 밖의 새 디렉터리여야 합니다. 준비만 수행하면 고정
`payload/`, `setup.iss`, `installer-manifest.json`을 만들고 `prepared_not_compiled`로
기록합니다. 컴파일러가 없으면 기본 컴파일 명령은 실패하며 성공으로 표시하지
않습니다. 도구를 자동 설치하거나 라이선스 동의를 대신하지 않습니다.

컴파일 성공 시 설치 EXE와 SHA256 sidecar를 만들고 `compiled_not_installed`로
기록합니다. 입력 manifest/ZIP·템플릿·준비 스크립트·컴파일러 버전/해시 및 결과
해시가 연결됩니다. 컴파일러 실패·중단·입력 변경·출력 경합 시 기존 후보와 기존
출력은 보존하고 이번 staging만 정리합니다. 컴파일 성공은 설치 검수나 재배포
승인을 뜻하지 않습니다.

## 설치·업데이트·제거 계약

- 고정 AppId와 사용자별 설치 위치를 사용하며 `PrivilegesRequired=lowest`입니다.
- 앱 파일은 원 build manifest SHA별 `payloads/` 하위에 설치합니다. 새 버전이
  이전 바이너리에 섞이지 않으며, 업그레이드 전 이전 폴더를 재귀 삭제하지 않습니다.
- 시작 메뉴 바로가기는 새 payload를 가리키고 작업 폴더는 버전과 무관한
  사용자 `CAELAB/Workbench/Workspace`입니다. 이 폴더는 제거 시 보존합니다.
- UI·Expert·MDO의 사용자 QSettings 및 명시 `CAELAB_*_SETTINGS_PATH` INI를
  생성·삭제·이주하지 않습니다. portable의 상대 INI 자동 이주는 제공하지 않습니다.
- 설치·제거 직전에 동명 `CAELabWorkbench.exe` 프로세스를 WMI로 조회합니다.
  portable·독립 창·gate도 포함합니다. 프로세스가 있거나 연결/쿼리/Count 접근이
  실패하면 중단하고, 자동 종료·재시작·권한 상승은 하지 않습니다. 조회 이후의
  새 실행까지 막거나 조회의 즉시 반환을 보장한다고 표시하지 않습니다.
- 제거는 Inno가 기록한 설치 파일·바로가기를 대상으로 합니다. 프로젝트·INI·
  사용자 설정 레지스트리나 `{app}` 전체를 지우는 항목은 없습니다.

## 검수 계획과 현재 한계

소스/합성 fixture 검사는 실제 설치·이전 버전 복구를 대신하지 않습니다.
같은 고정 설치 EXE의 해시와 다음 과업을 실제 수행자·환경·슬롯에 연결해야 합니다.

| 과업 | 기대 결과·보존 확인 |
|---|---|
| 비관리자 설치·시작 메뉴 | UAC 상승 요청 없이 사용자 위치, 정상 Qt/VTK 창 및 작업 폴더 |
| 실행 중 설치/제거 | 동명 앱 존재 또는 조회 실패에서 중단, 앱/솔버 강제 종료 없음 |
| 업데이트 | 같은 AppId, 이전 payload/프로젝트/설정 보존, 바로가기로 새 버전 실행 |
| 실패·취소 | 설치 중 디스크/복사 실패·취소 후 이전 앱 실행과 사용자 데이터/설정 유지 |
| 제거·다시 설치 | 기록된 앱/링크만 제거, 프로젝트·Workspace·QSettings/명시 INI 보존 |
| 대표 과업 | 저장한 project/run·대화·설정 재개, native 제출/취소/실패·결과/프레임 연결 |

현재 Inno 컴파일러 설치·실제 사용자 PC 설치/제거/UAC·clean Windows GUI/Qt·VTK·
실물 솔버 과업은 수행하지 않았습니다. 설치 중 rollback의 완전성은 실제 검수
전까지 미확인입니다. 이전 payload 보존과 빌드 산출물의 원자적 게시를 같은
보장으로 합치지 않습니다.

공식 근거: [lowest](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm),
[WorkingDir](https://jrsoftware.org/ishelp/topic_iconssection.htm),
[CloseApplications](https://jrsoftware.org/ishelp/topic_setup_closeapplications.htm),
[설치 순서](https://jrsoftware.org/ishelp/topic_installorder.htm),
[WMI Count](https://learn.microsoft.com/en-us/windows/win32/wmisdk/swbemobjectset-count).
