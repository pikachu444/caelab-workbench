# 현재 상태

문서 기준은 main `32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba`입니다. 이 문서는 구현 상태와 확인 범위를 소유합니다. 제품 기준은 [DESIGN](DESIGN.md), 계약은 [ARCHITECTURE](ARCHITECTURE.md), 사용 절차는 [시작 가이드](docs/tutorials/workbench_quickstart.md)를 따릅니다. 날짜별 실행·실패·정정·복구 위치는 [구현 이력](docs/history/implementation-history.md)에서 고정 원문으로 찾습니다.

## 구현 범위

N13 Windows 패키징 후속 후보는 별도 진입점, 외부 프로세스 DLL/Qt 환경 격리, 독립 창의 상대 INI 경로 보존과 소스·의존 버전 기록을 구현 중입니다. 원본 B/D 빌드와 현재 소스 후보는 구분합니다. 실행 진입점·manifest·설정 경계 53검사와 worker 후속 영향 24검사가 각각 통과했으며 실제 솔버 사례는 재실행하지 않았습니다. source gate의 변경 cwd 실패와 외부 프로그램 시작 실패의 `opened` 오표시를 고쳤습니다. 생성 확인은 최대 10초의 동기 대기이고 UI 무차단 보증은 아닙니다. 새 후보 빌드와 Windows 실화면·정상 닫기·INI 재열기·VTK 렌더링은 아직 미확인입니다.

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
| 전문가 오류 안내 분류 | [PR 후보/이슈 #48](https://github.com/pikachu444/caelab-workbench/issues/48)에서 `user_error()`의 개정 충돌·문서 잠금·무결성·파일 누락을 예외 타입 및 정밀 어휘로 우선 분류하여 '없' 오분류를 교정했습니다 (#48). main 반영 여부는 해당 PR의 병합 상태로 확인합니다. |
| 설치 매니페스트 예외 처리 | [PR64](https://github.com/pikachu444/caelab-workbench/pull/64)에서 매니페스트 누락·파손·구조 결함 시 전체 상태 조회가 중단되지 않고 안전하게 연결 불가로 진단하도록 교정했습니다 (#50). main 반영 여부는 PR64의 병합 상태로 확인합니다. |
| RAD 요약의 에너지 출처·시간·손상 행 | [PR61](https://github.com/pikachu444/caelab-workbench/pull/61)에서 에너지 번들 분리·동적 헤더·손상 행 처리와 footer 부재 시간 표기를 교정했습니다. 실제 `NativeAnalysisWindow.show_result()`의 footer 부재·사이클 불일치·일치 3사례가 통과했고 원 비판자가 수용했습니다. main 반영 여부는 PR61의 병합 상태로 확인합니다. |
| MDO 민감도 캐시 무결성 | [PR62](https://github.com/pikachu444/caelab-workbench/pull/62)에서 원본 INP/INCLUDE 변경·삭제와 대형 산출물 동일 크기/mtime 변조 시 캐시를 무효화하고 원본·증거를 재검증하도록 교정했습니다. 실제 캐시 경로의 원본 4사례 통과와 기대 메시지 정정 후 대형 출력 1사례 통과를 구분하며 원 비판자가 수용했습니다. main 반영 여부는 PR62의 병합 상태로 확인합니다. |
| Starter 경고 종료 | 오류 0·경고 존재·유효 restart를 보존하며 이어 실행하는 교정은 별도 미게시 후보. 현재 main의 완료 기능으로 세지 않음 |
| Windows 전체 사용 흐름 | 저장·전체 재개, 부모 전체 조작·복구, 실제 IME·창 크기·DPI의 각 증거를 DESIGN 종료 조건과 대조해야 함. 일부 화면·Qt offscreen 통과로 전체 승인하지 않음 |
| 독립 입력과 검색 평가 | 과거 현황의 PASS 표와 잔여·독립성 판정이 상충함. [종료 표](docs/history/implementation-history.md#1단계-종료-조건별-판정)와 [입력 적격성](docs/history/implementation-history.md#독립-입력-적격성)의 원자료·시점·서명 범위를 대조하기 전 전체 완료를 확정하지 않음 |
| Windows 배포 | 소스/venv 설치와 standalone EXE/DLL 배포 검증은 별개. 패키징 후보의 실제 사용자 진입점·리소스·독립 프로세스·설정 보존 검증이 남음 |
| 보조 StructuralModel 창 | 기준 main `96dd109`의 [#44](https://github.com/pikachu444/caelab-workbench/issues/44) 후속 후보에서 단위 표시를 교정했습니다. 정확한 `si`만 SI 환산·기호를 적용하고 다른 선언·미선언은 원값과 단위 미확인 안내를 유지합니다. main 반영은 #44의 연결 PR 병합 상태로 확인합니다. 계산 비동기화·소유 취소와 영구 실행 이력은 [#42](https://github.com/pikachu444/caelab-workbench/issues/42)·[#43](https://github.com/pikachu444/caelab-workbench/issues/43)에 남으며, Windows 실화면·본선 실행의 완료로 확대하지 않습니다. |
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
