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

### 2026-11-05 실제 컴파일 관측

[PR89](https://github.com/pikachu444/caelab-workbench/pull/89)의 installer 코드
`a1ccf8bf52a1f12ec746d5fe8609051a9de6b656`는 병합 `549b2adb`와 같은 코드 트리입니다.
작성자는 별도 승인된 Inno Setup 7.1.1 x64를 사용자 범위에 설치한 뒤, 기존 r2 앱
source `b8266199b5edaf7fef04a1765e3a56b768990052`의 같은 build manifest를 사용해
실제 ISCC compile을 1회 수행했고 exit 0을 확인했습니다. 입력 manifest SHA256은
`998cf5f78304c8d40627c1058f3783b67b7df253e779067d97bb011f6a85a200`입니다.
앱은 다시 빌드하지 않았으며 이전 prepare-only 산출물과 원 r2 ZIP은 보존했습니다.
준비·컴파일 과정의 임시 ZIP은 인도 r2 ZIP과 별도 컨테이너로 기록합니다.

결과 `caelab-workbench-setup.exe`는 **257,711,146 bytes**이고 SHA256은
`4f0adf2e9aef04456ddea30ab1c7818f3c311f70e94e4c7f86d86c09b2429538`입니다.
manifest 상태는 `compiled_not_installed`, `compile_exit_code=0`이며,
설치/제거/업데이트 검증은 `not_performed`입니다. 같은 원 QA가 EXE·manifest·
sidecar·생성 스크립트·로그·source를 읽기 대조해 실제 컴파일 산출물 인수 범위를
통과시켰습니다. 이 대조에서 재컴파일·프로그램 실행·행동 검사는 반복하지 않았습니다.

설치 registry와 compiler console은 7.1.1을 기록하지만 ISCC PE resource의
FileVersion/ProductVersion은 `0.0.0.0`이며, manifest는 이 실제 값을 보존합니다.
생성 EXE의 서명 상태는 `NotSigned`입니다. `x64` 식별자 사용 중단 예정 및
`x64os` 대체 경고와 `Non-commercial use only` 배너도 로그에 남겼습니다.
콘솔 배너만으로 기존 사용자 승인 라이선스 범위를 변경하거나 추가 구매·새 약관
수락·상업/공개 배포 승인을 자동 결정하지 않았습니다.

이 컴파일 관측에는 Workbench 설치 EXE 실행, 앱 설치/제거/업데이트·WMI 실행·
Qt/VTK 실화면·솔버·rollback·clean Windows/E2E 검수가 포함되지 않습니다.
현재 PC의 사용자 범위 설치·실행·별도 시험 프로젝트 저장·정상 닫기/재열기는
추가 승인되어 기존 WB 담당이 확인 중이며 실제 관측 결과는 별도로 연결합니다.
원 사용자 자료·설정을 보존하고 자식 프로세스 설정과 시험 자료를 격리합니다.
제거·업그레이드·clean Windows·솔버 전체 검수는 이 승인에 포함되지 않습니다.
설치 중 rollback의 완전성은 실제 검수 전까지 미확인입니다. 이전 payload 보존과
빌드 산출물의 원자적 게시를 같은 보장으로 합치지 않습니다.

공식 근거: [lowest](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm),
[WorkingDir](https://jrsoftware.org/ishelp/topic_iconssection.htm),
[CloseApplications](https://jrsoftware.org/ishelp/topic_setup_closeapplications.htm),
[설치 순서](https://jrsoftware.org/ishelp/topic_installorder.htm),
[WMI Count](https://learn.microsoft.com/en-us/windows/win32/wmisdk/swbemobjectset-count).
