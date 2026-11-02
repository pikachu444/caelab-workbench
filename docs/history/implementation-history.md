# 구현 이력

현재 지원과 남은 일은 [CURRENT_STATE](../../CURRENT_STATE.md), 제품 기준은 [DESIGN](../../DESIGN.md)에 있습니다. 이 문서는 과거 구현·실패·정정의 원문을 찾는 목차입니다. 아래 링크는 고정 Git 커밋을 가리킵니다. 당시의 ‘현재’, 승인, 담당, 개인 환경을 오늘의 상태나 실행 권한으로 이어받지 않습니다.

## 주요 변경

| 시점·변경 | 원문과 확인 범위 |
|---|---|
| 2026-10-23 P1–P7 구현·검토 | [초기 리뷰](../review/2026-10-23-implementation-review.md)와 [개선 계획](../review/2026-10-23-codex-improvement-plan.md). 계획의 종료 조건과 당시 실행 결과는 구분합니다. |
| 2026-10-26 저장·재개·독립 입력 | 아래 과거 현황과 복구 목차. 종료 표의 PASS 주장과 같은 문서의 잔여·독립성 판정이 상충하므로 전체 인도 승인으로 재사용하지 않습니다. |
| 2026-10-26–28 MDO 재설계·교정 | [설계 검토](../review/2026-10-26-commercial-mdo-benchmark-and-architecture.md), [수치·벤치마크 감사](../review/2026-10-27-codex-gpt-61-sol-audit-and-benchmark-expansion.md), [README·화면 정정](../review/2026-10-27-readme-benchmark-audit.md). 당시 코드와 예측·실측 구분을 보존합니다. |
| 2026-11-02 RAD 두 단계 연결 | [PR55](https://github.com/pikachu444/caelab-workbench/pull/55), [최종 CI](https://github.com/pikachu444/caelab-workbench/actions/runs/41368317991). 이전 Linux ERROR760 실패는 뒤의 성공으로 삭제하지 않습니다. |
| 2026-11-02 Windows 경로·시간 초과 검사 | [PR57](https://github.com/pikachu444/caelab-workbench/pull/57), [후보 CI](https://github.com/pikachu444/caelab-workbench/actions/runs/41390631184). merge fc1626a의 새 clone 확인은 해당 snapshot의 근거입니다. |
| 2026-11-02 화면·MDO·실행 파일 탐색 | [PR58](https://github.com/pikachu444/caelab-workbench/pull/58), [PR59](https://github.com/pikachu444/caelab-workbench/pull/59), [PR60](https://github.com/pikachu444/caelab-workbench/pull/60). 후속 반례는 현재 현황에서 별도로 추적합니다. |

<a id="checkpoint"></a>

## 과거 현황의 항목별 원문

기준은 문서 정리 전 main `32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba`입니다. 날짜별 관측·실패·입력 해시·당시 승인과 정정 전후 관계는 아래 원문에 남아 있습니다. 반복되는 실행 내역을 현재 상태 문서에 다시 복사하지 않습니다.

| 과거 항목 | 고정 원문 |
|---|---|
| <a id="calculixevaluator-동적-탐색-및-미사용-인자-정리-2026-11-02"></a>CalculiXEvaluator 동적 탐색 및 미사용 인자 정리 (2026-11-02) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#calculixevaluator-%EB%8F%99%EC%A0%81-%ED%83%90%EC%83%89-%EB%B0%8F-%EB%AF%B8%EC%82%AC%EC%9A%A9-%EC%9D%B8%EC%9E%90-%EC%A0%95%EB%A6%AC-2026-11-02) |
| <a id="스키매틱-자동-그리드-배치-및-openradioss-요약아웃라인-연동-2026-11-02"></a>스키매틱 자동 그리드 배치 및 OpenRadioss 요약·아웃라인 연동 (2026-11-02) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EC%8A%A4%ED%82%A4%EB%A7%A4%ED%8B%B1-%EC%9E%90%EB%8F%99-%EA%B7%B8%EB%A6%AC%EB%93%9C-%EB%B0%B0%EC%B9%98-%EB%B0%8F-openradioss-%EC%9A%94%EC%95%BD%EC%95%84%EC%9B%83%EB%9D%BC%EC%9D%B8-%EC%97%B0%EB%8F%99-2026-11-02) |
| <a id="rad-w3-구현과-확인-상태-2026-11-02"></a>RAD W3 구현과 확인 상태 (2026-11-02) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#rad-w3-%EA%B5%AC%ED%98%84%EA%B3%BC-%ED%99%95%EC%9D%B8-%EC%83%81%ED%83%9C-2026-11-02) |
| <a id="windows-로컬-후속-확인-2026-11-02"></a>Windows 로컬 후속 확인 (2026-11-02) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#windows-%EB%A1%9C%EC%BB%AC-%ED%9B%84%EC%86%8D-%ED%99%95%EC%9D%B8-2026-11-02) |
| <a id="prep-공용-위젯과-rad-파일-연결-2026-11-01"></a>Prep 공용 위젯과 RAD 파일 연결 (2026-11-01) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#prep-%EA%B3%B5%EC%9A%A9-%EC%9C%84%EC%A0%AF%EA%B3%BC-rad-%ED%8C%8C%EC%9D%BC-%EC%97%B0%EA%B2%B0-2026-11-01) |
| <a id="독립-전처리기-개발과-기존-연결-병합-2026-10-29"></a>독립 전처리기 개발과 기존 연결 병합 (2026-10-29) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EB%8F%85%EB%A6%BD-%EC%A0%84%EC%B2%98%EB%A6%AC%EA%B8%B0-%EA%B0%9C%EB%B0%9C%EA%B3%BC-%EA%B8%B0%EC%A1%B4-%EC%97%B0%EA%B2%B0-%EB%B3%91%ED%95%A9-2026-10-29) |
| <a id="cad와-솔버별-전처리-분리-2026-10-29"></a>CAD와 솔버별 전처리 분리 (2026-10-29) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#cad%EC%99%80-%EC%86%94%EB%B2%84%EB%B3%84-%EC%A0%84%EC%B2%98%EB%A6%AC-%EB%B6%84%EB%A6%AC-2026-10-29) |
| <a id="현재-상태"></a>현재 상태 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%ED%98%84%EC%9E%AC-%EC%83%81%ED%83%9C) |
| <a id="mdo-doe-기법별-설정생성-예정-점-수연속-변수-수준값-및-민감도-중복-재분석-방지-38-39-40-2026-11-02"></a>MDO DoE 기법별 설정·생성 예정 점 수·연속 변수 수준값 및 민감도 중복 재분석 방지 (#38, #39, #40, 2026-11-02) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#mdo-doe-%EA%B8%B0%EB%B2%95%EB%B3%84-%EC%84%A4%EC%A0%95%EC%83%9D%EC%84%B1-%EC%98%88%EC%A0%95-%EC%A0%90-%EC%88%98%EC%97%B0%EC%86%8D-%EB%B3%80%EC%88%98-%EC%88%98%EC%A4%80%EA%B0%92-%EB%B0%8F-%EB%AF%BC%EA%B0%90%EB%8F%84-%EC%A4%91%EB%B3%B5-%EC%9E%AC%EB%B6%84%EC%84%9D-%EB%B0%A9%EC%A7%80-38-39-40-2026-11-02) |
| <a id="mdo-phase-3-구현수치-검증-windows-최종-인도-대기-2026-10-28"></a>MDO Phase 3 구현·수치 검증, Windows 최종 인도 대기 (2026-10-28) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#mdo-phase-3-%EA%B5%AC%ED%98%84%EC%88%98%EC%B9%98-%EA%B2%80%EC%A6%9D-windows-%EC%B5%9C%EC%A2%85-%EC%9D%B8%EB%8F%84-%EB%8C%80%EA%B8%B0-2026-10-28) |
| <a id="calculix-네이티브-치수-최적화sizing-optimization-벤치마크-완료-2026-10-27"></a>CalculiX 네이티브 치수 최적화(Sizing Optimization) 벤치마크 완료 (2026-10-27) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#calculix-%EB%84%A4%EC%9D%B4%ED%8B%B0%EB%B8%8C-%EC%B9%98%EC%88%98-%EC%B5%9C%EC%A0%81%ED%99%94sizing-optimization-%EB%B2%A4%EC%B9%98%EB%A7%88%ED%81%AC-%EC%99%84%EB%A3%8C-2026-10-27) |
| <a id="mdo-정직화-작업-2026-10-27-task-codex-honest-remediation-001"></a>MDO 정직화 작업 (2026-10-27, TASK-CODEX-HONEST-REMEDIATION-001) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#mdo-%EC%A0%95%EC%A7%81%ED%99%94-%EC%9E%91%EC%97%85-2026-10-27-task-codex-honest-remediation-001) |
| <a id="mdo-수치-api-로컬-구현-2026-10-27"></a>MDO 수치 API 로컬 구현 (2026-10-27) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#mdo-%EC%88%98%EC%B9%98-api-%EB%A1%9C%EC%BB%AC-%EA%B5%AC%ED%98%84-2026-10-27) |
| <a id="새-mdo-설계-2026-10-26-후속-요청"></a>새 MDO 설계 (2026-10-26 후속 요청) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EC%83%88-mdo-%EC%84%A4%EA%B3%84-2026-10-26-%ED%9B%84%EC%86%8D-%EC%9A%94%EC%B2%AD) |
| <a id="최신-확인과-남은-작업-2026-10-26"></a>최신 확인과 남은 작업 (2026-10-26) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EC%B5%9C%EC%8B%A0-%ED%99%95%EC%9D%B8%EA%B3%BC-%EB%82%A8%EC%9D%80-%EC%9E%91%EC%97%85-2026-10-26) |
| <a id="새-세션에서-이어갈-작업"></a>새 세션에서 이어갈 작업 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EC%83%88-%EC%84%B8%EC%85%98%EC%97%90%EC%84%9C-%EC%9D%B4%EC%96%B4%EA%B0%88-%EC%9E%91%EC%97%85) |
| <a id="1단계-종료-조건별-판정"></a>1단계 종료 조건별 판정 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#1%EB%8B%A8%EA%B3%84-%EC%A2%85%EB%A3%8C-%EC%A1%B0%EA%B1%B4%EB%B3%84-%ED%8C%90%EC%A0%95) |
| <a id="독립-입력-적격성"></a>독립 입력 적격성 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EB%8F%85%EB%A6%BD-%EC%9E%85%EB%A0%A5-%EC%A0%81%EA%B2%A9%EC%84%B1) |
| <a id="다음-실행에-지정한-미사용-원-덱-후보"></a>다음 실행에 지정한 미사용 원 덱 후보 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EB%8B%A4%EC%9D%8C-%EC%8B%A4%ED%96%89%EC%97%90-%EC%A7%80%EC%A0%95%ED%95%9C-%EB%AF%B8%EC%82%AC%EC%9A%A9-%EC%9B%90-%EB%8D%B1-%ED%9B%84%EB%B3%B4) |
| <a id="개선-계획-p1p7의-복구-현황"></a>개선 계획 P1~P7의 복구 현황 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EA%B0%9C%EC%84%A0-%EA%B3%84%ED%9A%8D-p1p7%EC%9D%98-%EB%B3%B5%EA%B5%AC-%ED%98%84%ED%99%A9) |
| <a id="코드에서-확인한-범위와-남은-결함"></a>코드에서 확인한 범위와 남은 결함 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EC%BD%94%EB%93%9C%EC%97%90%EC%84%9C-%ED%99%95%EC%9D%B8%ED%95%9C-%EB%B2%94%EC%9C%84%EC%99%80-%EB%82%A8%EC%9D%80-%EA%B2%B0%ED%95%A8) |
| <a id="stage-4-1-최적화-대시보드--로컬-구현"></a>Stage 4-1 최적화 대시보드 — 로컬 구현 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#stage-4-1-%EC%B5%9C%EC%A0%81%ED%99%94-%EB%8C%80%EC%8B%9C%EB%B3%B4%EB%93%9C--%EB%A1%9C%EC%BB%AC-%EA%B5%AC%ED%98%84) |
| <a id="p1에서-바뀐-동작"></a>P1에서 바뀐 동작 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p1%EC%97%90%EC%84%9C-%EB%B0%94%EB%80%90-%EB%8F%99%EC%9E%91) |
| <a id="p2-의존성시작-진단-수정"></a>P2 의존성·시작 진단 수정 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p2-%EC%9D%98%EC%A1%B4%EC%84%B1%EC%8B%9C%EC%9E%91-%EC%A7%84%EB%8B%A8-%EC%88%98%EC%A0%95) |
| <a id="p2-프로그램-경계-정정"></a>P2 프로그램 경계 정정 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p2-%ED%94%84%EB%A1%9C%EA%B7%B8%EB%9E%A8-%EA%B2%BD%EA%B3%84-%EC%A0%95%EC%A0%95) |
| <a id="p3-사용자-파일저장프로그램-열기"></a>P3 사용자 파일·저장·프로그램 열기 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p3-%EC%82%AC%EC%9A%A9%EC%9E%90-%ED%8C%8C%EC%9D%BC%EC%A0%80%EC%9E%A5%ED%94%84%EB%A1%9C%EA%B7%B8%EB%9E%A8-%EC%97%B4%EA%B8%B0) |
| <a id="p4-네이티브-실행결과-이력"></a>P4 네이티브 실행·결과 이력 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p4-%EB%84%A4%EC%9D%B4%ED%8B%B0%EB%B8%8C-%EC%8B%A4%ED%96%89%EA%B2%B0%EA%B3%BC-%EC%9D%B4%EB%A0%A5) |
| <a id="p5-결과-전달독립-전문가와-검색-자료"></a>P5 결과 전달·독립 전문가와 검색 자료 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p5-%EA%B2%B0%EA%B3%BC-%EC%A0%84%EB%8B%AC%EB%8F%85%EB%A6%BD-%EC%A0%84%EB%AC%B8%EA%B0%80%EC%99%80-%EA%B2%80%EC%83%89-%EC%9E%90%EB%A3%8C) |
| <a id="p6-제안-검토같은-연구-재개"></a>P6 제안 검토·같은 연구 재개 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p6-%EC%A0%9C%EC%95%88-%EA%B2%80%ED%86%A0%EA%B0%99%EC%9D%80-%EC%97%B0%EA%B5%AC-%EC%9E%AC%EA%B0%9C) |
| <a id="p7-보충-입력-확인과-ci"></a>P7 보충 입력 확인과 CI | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#p7-%EB%B3%B4%EC%B6%A9-%EC%9E%85%EB%A0%A5-%ED%99%95%EC%9D%B8%EA%B3%BC-ci) |
| <a id="전문가-대화-화면-개선-중"></a>전문가 대화 화면 개선 중 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EC%A0%84%EB%AC%B8%EA%B0%80-%EB%8C%80%ED%99%94-%ED%99%94%EB%A9%B4-%EA%B0%9C%EC%84%A0-%EC%A4%91) |
| <a id="부모-창-정보-표시-개선-중"></a>부모 창 정보 표시 개선 중 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EB%B6%80%EB%AA%A8-%EC%B0%BD-%EC%A0%95%EB%B3%B4-%ED%91%9C%EC%8B%9C-%EA%B0%9C%EC%84%A0-%EC%A4%91) |
| <a id="앞선-정합성-변경에서-확인한-것"></a>앞선 정합성 변경에서 확인한 것 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EC%95%9E%EC%84%A0-%EC%A0%95%ED%95%A9%EC%84%B1-%EB%B3%80%EA%B2%BD%EC%97%90%EC%84%9C-%ED%99%95%EC%9D%B8%ED%95%9C-%EA%B2%83) |
| <a id="개발자-환경의-과거-실행-기록-이번-변경에서-재실행하지-않음"></a>개발자 환경의 과거 실행 기록 (이번 변경에서 재실행하지 않음) | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EA%B0%9C%EB%B0%9C%EC%9E%90-%ED%99%98%EA%B2%BD%EC%9D%98-%EA%B3%BC%EA%B1%B0-%EC%8B%A4%ED%96%89-%EA%B8%B0%EB%A1%9D-%EC%9D%B4%EB%B2%88-%EB%B3%80%EA%B2%BD%EC%97%90%EC%84%9C-%EC%9E%AC%EC%8B%A4%ED%96%89%ED%95%98%EC%A7%80-%EC%95%8A%EC%9D%8C) |
| <a id="다음-순서"></a>다음 순서 | [당시 기록](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/CURRENT_STATE.md#%EB%8B%A4%EC%9D%8C-%EC%88%9C%EC%84%9C) |

<a id="recovery"></a>

## 과거 인계·복구 자료

| 자료 | 보존한 원문·역할 |
|---|---|
| 이전 NEXT_SESSION | [고정 인계문](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/NEXT_SESSION.md). 외부 자산 위치·프로젝트/실행 대조값·팀 기록·복구 묶음의 당시 위치를 찾을 때만 사용합니다. |
| 전문가 기능 인계 | [assist/MERGE_REQUEST](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/caelab/assist/MERGE_REQUEST.md). 당시 요청과 실제 원격 누락을 지적한 초기 리뷰를 함께 읽습니다. |
| PDE 기능 인계 | [pde/MERGE_REQUEST](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/caelab/pde/MERGE_REQUEST.md). 현행 구현 계약은 [구조](../../ARCHITECTURE.md)를 확인합니다. |
| 솔버 기능 인계 | [solvers/MERGE_REQUEST](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/caelab/solvers/MERGE_REQUEST.md). 당시 설치·구현 요청이며 현재 지원표가 아닙니다. |
| 퇴역 numerical 운영 지침 | [당시 AGENTS 원문](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/caelab/numerical/AGENTS.md). 새 수치 코드 추가를 허용하던 지침과 clean-slate 폐기 결정을 대조하는 과거 자료입니다. |

## 이전 화면 자료

아래 8개 PNG는 현재 코드·테스트·사용 가이드가 소비하지 않는 개발검증자료입니다. 활성 트리에서 제거하고, 캡션 오류를 설명한 [기존 감사](../review/2026-10-27-readme-benchmark-audit.md)와 고정 원문을 연결했습니다. 실제 Windows 전체 사용 흐름의 완료 근거로 사용하지 않습니다.

- [screen_cae_main_window.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_cae_main_window.png)
- [screen_expert_window.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_expert_window.png)
- [screen_mdo_studio_dark.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_mdo_studio_dark.png)
- [screen_mdo_studio_dark_pareto.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_mdo_studio_dark_pareto.png)
- [screen_mdo_studio_dark_surface.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_mdo_studio_dark_surface.png)
- [screen_mdo_studio_dark_tornado.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_mdo_studio_dark_tornado.png)
- [screen_mdo_studio_light.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_mdo_studio_light.png)
- [screen_project_launcher.png](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/docs/images/screen_project_launcher.png)

## 이전 GUI 데모의 생성 연구 상태

`scripts/generate_gui_demonstration.py`가 출력한 아래 연구 JSON은 현재 코드·테스트·사용 가이드의 입력으로 참조되지 않습니다. 활성 트리에서 제거하고 고정 원문으로 연결합니다. 입력 덱 3개와 실측 영수증 3개는 [sizing 보고](../benchmarks/01_sizing_optimization_benchmarks.md)의 재현 입력·측정 출처로 유지합니다. 데모 생성기를 현재 API에서 재실행해 재생성할 수 있는지는 이번 문서 정리에서 확인하지 않았습니다.

- [stepped_cantilever_gui_study.json](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/benchmarks/runs/stepped_cantilever_gui_study.json)
- [truss_10bar_gui_study.json](https://github.com/pikachu444/caelab-workbench/blob/32e9d7c9fecdec24800dbbb5a4dfb08e37e008ba/benchmarks/runs/truss_10bar_gui_study.json)
