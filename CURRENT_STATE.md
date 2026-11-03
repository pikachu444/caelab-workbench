# 현재 상태

문서 기준은 main `32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba`입니다. 이 문서는 구현 상태와 확인 범위를 소유합니다. 제품 기준은 [DESIGN](DESIGN.md), 계약은 [ARCHITECTURE](ARCHITECTURE.md), 사용 절차는 [시작 가이드](docs/tutorials/workbench_quickstart.md)를 따릅니다. 날짜별 실행·실패·정정·복구 위치는 [구현 이력](docs/history/implementation-history.md)에서 고정 원문으로 찾습니다.

## 구현 범위

### Gmsh MSH 4.1 straight Tet10 → CalculiX C3D10 변환 (Phase 4-B 1단계)

독립 어댑터 `caelab/adapters/msh_c3d10.py`와 검증 테스트 `tests/test_msh_c3d10.py`를 구현했습니다.
- 입력: Gmsh MSH 4.1 ASCII 파일 및 `caelab.standalone-msh` v1 sidecar JSON (`length_unit=mm`, `element_profile=straight_tet10`).
- 노드 순서 변환: raw Gmsh type 11 `(12, 23, 31, 14, 34, 24)` midside 노드를 CalculiX C3D10 `(12, 23, 31, 14, 24, 34)`로 슬롯 9와 10을 단 1회 스왑.
- 검증 및 거부(Fail-Closed): sidecar 누락, hash 불일치, mm 외 단위, 음수 절점, 댕글링 참조 등 거부. 유효 양의 정수 비연속 ID는 온전히 보존.
- 실물 검증: Prep에서 생성한 고정 L 실물 자산(`045acf69...` / `52fc715c...`) 87개 절점, 36개 Tet10 요소, 28개 Tri6 경계 어노테이션 분리 판독 및 INP include 덱 재파싱 100% 일치 확인.
- 범위 한정: 1단계는 mesh-only include 덱 변환에 한정하며 실제 CalculiX 솔버 실행 및 해석 UI 진입은 미포함.


N13 Windows 패키징 [PR69](https://github.com/pikachu444/caelab-workbench/pull/69)는 별도 진입점, 외부 프로세스 DLL/Qt 환경 격리, 독립 창의 상대 INI 경로 보존과 소스·의존 버전 기록을 구현합니다. 실행 진입점·manifest·설정 경계 53검사와 worker 후속 영향 24검사가 각각 통과했으며 실제 솔버 사례는 재실행하지 않았습니다. source gate의 변경 cwd 실패와 외부 프로그램 시작 실패의 `opened` 오표시를 고쳤습니다. 생성 확인은 최대 10초의 동기 대기이고 UI 무차단 보증은 아닙니다.

고정 source `2887d4417e3f68cf9caae05583e2618fda70fc4d`의 후보 E는 1회 로컬 빌드에서 종료 코드 0·필수 정적 검사 12항목을 충족했습니다(6,831파일·1,073,840,754 bytes). 소스 104파일·패키징 해시·Python/의존 버전을 빌드 전후 대조했습니다. 새 EXE의 gate를 소스 밖 작업 폴더·PYTHONPATH 없는 자식 환경으로 실행해 Python 콘솔 probe의 생성 성공/실패 전달, 번들 PATH 제거와 caller/sibling 보존, Qt 원값 복원을 확인했습니다. 이 검사는 GUI나 vendor solver 실행이 아닙니다. 이후 [운영 보고](https://github.com/pikachu444/caelab-workbench/issues/56#issuecomment-6588867386)는 같은 E의 Windows MDO·Expert 실화면, 정상 종료(exit 0), 새 PID의 별도 INI 복원을 확인했습니다. Expert는 합성 로컬 대화만 사용했습니다. 원본 B/D와 고정 E를 구분하며 이 보고를 최신 소스 전체의 EXE·VTK 렌더링 검증으로 확대하지 않습니다. 공개 바이너리 릴리스는 하지 않았습니다.

E에는 SciPy의 선택적 array API 경로와 PyInstaller hook을 통해 Torch·torchvision도 수집됐습니다. 이들은 기존 13개 고정 의존 버전 기록에 없으며 별도 LICENSE/NOTICE·배포 메타데이터 수집도 확인되지 않았습니다. 외부 배포 전에 실제 수집 의존성의 버전·고지 경계를 정리해야 합니다. 선택 경로라는 이유만으로 제거가 안전하다고 판정하거나 임의 exclude·재빌드를 하지 않았습니다.

후속 패키징 소스는 Torch·torchvision의 등록된 설치 파일과 Analysis 전체 source를 대조해 평탄화된 목적지의 파일도 추적하고, metadata 버전·선언된 License-File을 확인해 dist-info를 비재귀 수집합니다. 다른 배포판의 내부 Torch 호환 코드를 이름만 보고 Torch로 분류하지 않습니다. 최종 번들에서 metadata·고지 bytes의 SHA를 대조하고, 누락·다른 설치본·Windows 목적지 충돌·빌드 중 의존 버전 변경은 성공으로 처리하지 않습니다. 기존 13개 빌드 도구 버전과 선택적 설치 입력 버전, 실제 동봉 증거는 별도로 기록합니다. 전체 동봉 의존성·native 라이선스 조건 검토 완료를 뜻하지 않습니다. 기존 E의 Analysis를 읽은 음성 검사에서는 Torch 4,649·torchvision 364개의 Analysis 참조가 해당 설치본에 귀속되고 각각 고지 107·1개가 선언됐으나 E의 metadata 누락으로 거절됐습니다. E를 수정하지 않았으며 새 source의 바이너리는 아직 빌드하지 않았습니다.

E 이후 source 후속은 생성 실패 ACK의 원 예외 접두부를 보존해 기존 파일 누락·권한 안내 분류로 전달합니다. 중간 RuntimeError 접두부로 분류가 사라지는 반례를 재현하고 해당 생성 실패 검사를 교정했습니다. 이 후속은 E에 포함되지 않으며 E를 최신 소스 전체의 EXE 검증으로 세지 않습니다.

보조 창 #42/#43의 선행 판독 분리는 PR70으로 main에 통합됐습니다. 기존 CalculiX 판독을 실행 없는 `read_result`로 분리해 동기 실행과 공유하며 출력 요청·집합·프레임·반력 보류·단위의 기존 의미를 유지합니다. 합성 결과·모의 실행 61검사가 통과했고 실제 solver 3사례는 재실행하지 않았습니다.

보조 창 #42/#43의 PR71은 최초 저장/실행 전에 영구 폴더를 선택하고 `model.json`과 기존 `runs/` 저장소·lease·원자 쓰기를 재사용합니다. 저장 계층의 bool `run_store_expected`는 내부 StructuralModel 스키마에 추가하지 않습니다. marker가 true인데 저장소가 없으면 열기/저장 모두 오류로 남기며 재생성하지 않습니다. legacy 열기는 파일을 만들지 않고 명시 저장 때 이행합니다. 활성 lease는 다른 저장을 거절합니다. 창이 읽은 모델의 semantic revision을 저장 lease 안에서 재검사해 다른 창이 저장한 모델을 덮지 않습니다. 실패 때 이전 모델·이력을 보존합니다. 자유도 키는 같은 값의 int/str 별칭만 합치며 충돌 값은 위치를 포함한 오류로 거절합니다.

같은 구현은 기존 JobManager의 worker·소유 프로세스 취소·시간 제한을 재사용합니다. 실행별 모델 사본·단위 선언·결과 JSON을 보존하고, 재열기 때 기록된 artifact 전체와 최초 입력 사본의 해시를 검증하며 실패·취소 기록도 유지합니다. 실행 도중 INP가 바뀌면 원 모델의 정상 결과로 게시하지 않습니다. 과거 결과는 그 실행의 메시와 단위로 표시하고 현재 모델로 돌아오면 표시 상태를 함께 초기화합니다. 늦은 완료는 사용자가 선택한 과거 실행을 덮지 않습니다. 창 닫기는 그 창의 실행만 취소하고 QTimer로 worker·lease 정리를 기다리며 다른 실행은 유지합니다. 저장·owner·모델 전송·Qt·합성 출력 연결의 관련 검사를 수행합니다. Python probe와 합성 출력 검사는 실제 vendor solver 실행이나 OS 실화면·VTK 렌더링 검증이 아니며 PR의 검토·CI·병합 상태와 구분합니다.

| 사용자 작업 | main의 구현 | 남은 조건 |
|---|---|---|
| 프로그램과 파일 연결 | 런처형 프로젝트, 역할별 프로그램 추가·연결, 입력/편집 문서/출력 구분, stale, 제거·복원, 저장·다른 이름 저장 | 프로그램 등록을 실제 교환 형식·계산 지원으로 확대하지 않음. PrePoMax Windows GUI 끝단은 미검증 |
| CalculiX 계산·후처리 | 원 덱/INCLUDE snapshot, JobManager 실행·취소·로그·이력, FRD/DAT 판독·VTU/PVD·ParaView 전달 | 선언 단위·프레임·partial을 유지. 모든 카드·물리 모델의 정확성 보증이 아님 |
| CAELab Prep 연결 | 독립 Prep에 RAD input/document 전달, 내보낸 덱 output, 선택적 NumericEdit 재사용 | Prep 모델·Undo·문서 버전은 별도 저장소 소유. 위젯 사용이 독립 앱 배포 검증은 아님 |
| 전문가 대화 | 독립 창, 선택 자료의 사본·출처·대화 저장과 재개, 공급자 연결·취소 | 실제 공급자/검색 품질과 실패 처리는 해당 실행 증거로 판정. 열기만으로 요청하지 않음 |
| MDO Studio | 독립 연구 창, 스칼라 INP 토큰 변수, DoE·실제 관측·대리모델·최적화·민감도·적용/Undo | CAD 재생성·재메시 없음. 예측과 실제 해석, 제약 충족과 공학 승인은 구별 |

<a id="rad-w3"></a>

## OpenRadioss·OpenCourant

Starter→Engine을 하나의 실행 이력·timeout·취소 범위에서 순서대로 실행합니다. 원본·실행 입력, 상대 INCLUDE, 단계별 로그·종료 상태, restart와 실제 결과 이름/해시를 보존하고 저장본을 다시 읽습니다. 현재 native binary animation의 필드 판독은 `partial`이며 빈 프레임을 완전한 후처리로 취급하지 않습니다.

공식 OpenCourant `latest-20261011`(source `cb760c206e346eff12161a2268b134ef9d48a4ef`) Windows ZIP의 공식 SHA256을 확인한 환경에서 저장소 tetra·shell/INCLUDE 실제 서비스 2건이 통과했습니다. Starter/Engine 0/0, 해시·저장·재열기 확인이며 GUI 전체·물리 oracle 검증은 아닙니다. 코드와 배포본을 함께 바꾼 결과이므로 PATH 수정만의 효과로 단정하지 않습니다. [환경·실패 원문](docs/history/implementation-history.md#rad-w3-구현과-확인-상태-2026-11-02)

<a id="ui-schematic-radioss-kpi"></a>
<a id="mdo-calculix-evaluator-41"></a>
<a id="mdo-sensitivity-cache-integrity-followup"></a>
<a id="mdo-studio-민감도-분석-캐시-키-계약-및-전체-sha-256-무결성-보완-2026-11-02"></a>

## 미해결 결함과 인도 조건

| 항목 | 확인된 상태·다음 판정 |
|---|---|
| 전문가 오류 안내 분류 | [PR65](https://github.com/pikachu444/caelab-workbench/pull/65)에서 `user_error()`의 개정 충돌·문서 잠금·무결성·파일 누락을 예외 타입 및 정밀 어휘로 우선 분류하여 '없' 오분류를 교정했습니다 (#48). main에 병합 완료되었습니다. |
| 설치 매니페스트 예외 처리 | [PR64](https://github.com/pikachu444/caelab-workbench/pull/64)에서 매니페스트 누락·파손·구조 결함 시 전체 상태 조회가 중단되지 않고 안전하게 연결 불가로 진단하도록 교정했습니다 (#50). main에 병합 완료되었습니다. |
| 전문가 대화 시간 예산·단계 표시 | [PR73](https://github.com/pikachu444/caelab-workbench/pull/73)은 main `009d96211c496ced3269763da82975cce642a1f9`에 병합됐습니다 (#46). 준비 20초(정책 기본값)·공급자 응답 60초·전체 관리 대기 85초를 분리하며, 원 독립 QA의 기존 8반례에서 늦은 응답·취소·전환 경계를 확인했습니다. 후속 GUI 후보는 현재 요청의 준비/응답 단계를 표시하고 취소·완료·이전 요청의 늦은 알림을 무시합니다. 관련 Qt offscreen 32검사가 통과했습니다. 후속 PR의 재검토·CI 결과는 해당 PR을 따르며, 실제 공급자·OS 실화면 검증과 전체 제품 완료로 확대하지 않습니다. |
| RAD 요약의 에너지 출처·시간·손상 행 | [PR61](https://github.com/pikachu444/caelab-workbench/pull/61)에서 에너지 번들 분리·동적 헤더·손상 행 처리와 footer 부재 시간 표기를 교정했습니다. 실제 `NativeAnalysisWindow.show_result()`의 footer 부재·사이클 불일치·일치 3사례가 통과했고 원 비판자가 수용했습니다. main 반영 여부는 PR61의 병합 상태로 확인합니다. |
| MDO 민감도 캐시 무결성 | [PR62](https://github.com/pikachu444/caelab-workbench/pull/62)에서 원본 INP/INCLUDE 변경·삭제와 대형 산출물 동일 크기/mtime 변조 시 캐시를 무효화하고 원본·증거를 재검증하도록 교정했습니다. 실제 캐시 경로의 원본 4사례 통과와 기대 메시지 정정 후 대형 출력 1사례 통과를 구분하며 원 비판자가 수용했습니다. main 반영 여부는 PR62의 병합 상태로 확인합니다. |
| Starter 경고 종료 | 오류 0·경고 존재·유효 restart를 보존하며 이어 실행하는 교정은 별도 미게시 후보. 현재 main의 완료 기능으로 세지 않음 |
| Windows 전체 사용 흐름 | 저장·전체 재개, 부모 전체 조작·복구, 실제 IME·창 크기·DPI의 각 증거를 DESIGN 종료 조건과 대조해야 함. 일부 화면·Qt offscreen 통과로 전체 승인하지 않음 |
| 독립 입력과 검색 평가 | 과거 현황의 PASS 표와 잔여·독립성 판정이 상충함. [종료 표](docs/history/implementation-history.md#1단계-종료-조건별-판정)와 [입력 적격성](docs/history/implementation-history.md#독립-입력-적격성)의 원자료·시점·서명 범위를 대조하기 전 전체 완료를 확정하지 않음 |
| Windows 배포 | 소스/venv 설치와 standalone EXE/DLL 배포 검증은 별개. 패키징 후보의 실제 사용자 진입점·리소스·독립 프로세스·설정 보존 검증이 남음 |
| 보조 StructuralModel 창 | [PR67](https://github.com/pikachu444/caelab-workbench/pull/67)의 #44 단위 표시 교정은 main에 통합됐습니다. 정확한 `si`만 SI 환산·기호를 적용하고 다른 선언·미선언은 원값과 단위 미확인 안내를 유지합니다. 계산 비동기화·소유 취소와 영구 실행 이력은 [PR71](https://github.com/pikachu444/caelab-workbench/pull/71)의 범위이며 위 설명과 해당 PR의 상태를 따릅니다. Windows 실화면·본선 실행의 완료로 확대하지 않습니다. |
| 미구현 실행 환경 | Windows installer, WSL 파일/작업 연결, 일부 솔버별 전처리·결과 판독은 [플랫폼 계약](ARCHITECTURE.md#플랫폼별-실행-전제)에 명시 |

## 검사 결과의 범위

- PR57 후보 `fc10c84`의 [필수 CI 4종](https://github.com/pikachu444/caelab-workbench/actions/runs/41390631184)이 성공했고, 병합 `fc1626a`의 새 clone tree 일치와 관련 18검사를 확인했습니다. 이후 PR58–60의 전체 Windows 검증으로 합산하지 않습니다.
- 오래 실행한 Windows 전체 검사 결과는 **1160 passed, 20 skipped, 10 failed**입니다. 시작 snapshot과 실행 중 일부 subprocess 소스가 달라 최종 main의 고정 검증이 아닙니다. 최종 JUnit/log에는 실제 시작 명령·인터프리터 경로·고정 소스 SHA를 연결한 메타데이터가 없어 그 파일만으로 실행 환경을 독립 확정할 수 없습니다. 실패는 installer 3건, pythonw stream timeout 2건, UTF-8 프로젝트를 기본 cp949로 읽은 검사 1건, 기존 May 솔버 환경의 실제 RAD 4건입니다. 환경 부족·테스트 전제·제품 결함을 구별해 후속 처리합니다.
- 같은 pythonw 2건의 별도 실행은 통과했지만 장기 실행의 timeout 원인은 확정되지 않았습니다. 작은 재실행 통과만으로 전체 실패를 삭제하지 않습니다.
- 자동·합성 프로세스·Qt offscreen·실제 솔버·OS 화면 조작은 서로 다른 근거입니다. 종료 코드 0과 읽을 수 있는 결과도 물리 타당성·최종 제품 인도와 구별합니다.

## 과거 주소

기존 현황 항목의 링크는 아래 위치에서 유지합니다. 날짜별 수치·개인 환경·옛 재개 지시는 [항목별 고정 원문](docs/history/implementation-history.md#checkpoint)을 읽고 당시 기록으로 해석합니다.

<a id="calculixevaluator-동적-탐색-및-미사용-인자-정리-2026-11-02"></a>
<a id="스키매틱-자동-그리드-배치-및-openradioss-요약아웃라인-연동-2026-11-02"></a>
<a id="rad-w3-구현과-확인-상태-2026-11-02"></a>
<a id="windows-로컬-후속-확인-2026-11-02"></a>
<a id="prep-공용-위젯과-rad-파일-연결-2026-11-01"></a>
<a id="독립-전처리기-개발과-기존-연결-병합-2026-10-29"></a>
<a id="cad와-솔버별-전처리-분리-2026-10-29"></a>
<a id="mdo-doe-기법별-설정생성-예정-점-수연속-변수-수준값-및-민감도-중복-재분석-방지-38-39-40-2026-11-02"></a>
<a id="mdo-phase-3-구현수치-검증-windows-최종-인도-대기-2026-10-28"></a>
<a id="calculix-네이티브-치수-최적화sizing-optimization-벤치마크-완료-2026-10-27"></a>
<a id="mdo-정직화-작업-2026-10-27-task-codex-honest-remediation-001"></a>
<a id="mdo-수치-api-로컬-구현-2026-10-27"></a>
<a id="새-mdo-설계-2026-10-26-후속-요청"></a>
<a id="최신-확인과-남은-작업-2026-10-26"></a>
<a id="새-세션에서-이어갈-작업"></a>
<a id="1단계-종료-조건별-판정"></a>
<a id="독립-입력-적격성"></a>
<a id="다음-실행에-지정한-미사용-원-덱-후보"></a>
<a id="개선-계획-p1p7의-복구-현황"></a>
<a id="코드에서-확인한-범위와-남은-결함"></a>
<a id="stage-4-1-최적화-대시보드--로컬-구현"></a>
<a id="p1에서-바뀐-동작"></a>
<a id="p2-의존성시작-진단-수정"></a>
<a id="p2-프로그램-경계-정정"></a>
<a id="p3-사용자-파일저장프로그램-열기"></a>
<a id="p4-네이티브-실행결과-이력"></a>
<a id="p5-결과-전달독립-전문가와-검색-자료"></a>
<a id="p6-제안-검토같은-연구-재개"></a>
<a id="p7-보충-입력-확인과-ci"></a>
<a id="전문가-대화-화면-개선-중"></a>
<a id="부모-창-정보-표시-개선-중"></a>
<a id="앞선-정합성-변경에서-확인한-것"></a>
<a id="개발자-환경의-과거-실행-기록-이번-변경에서-재실행하지-않음"></a>
<a id="다음-순서"></a>
