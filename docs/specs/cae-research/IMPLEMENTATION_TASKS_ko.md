# CAELab 전체 개발 작업표

**전체 개발 순서와 첫 사용자 납품은 [통합 개발 계획](../../review/2026-10-23-codex-improvement-plan.md#execution-order)을 먼저 읽으세요.** 이 문서는 그 순서를 실제 코드 작업으로 나눈 표입니다. 새 재료 분야만이 아니라 기존 두 제품의 미완료 작업도 포함합니다. 문서 작성·심의로 구현 상태를 올리지 않습니다.

처음 착수할 코드는 **D01 전문가 프레임·상태 수정, D02 공통 자료·단위, D03 Prep 표시 갱신**입니다. D37 환경 준비를 병행합니다. 첫 새 사용자 납품은 **D02/D04/D05/D06/D07: 실제 CSV→Prony 2항 동정→곡선·잔차·별도 시험→저장·새 프로세스 재열기**입니다. 그 뒤 아래 모든 후속을 계속합니다.

번호는 작업을 찾기 위한 이름입니다. 번호가 작은 모든 일이 끝나야 큰 번호를 시작한다는 뜻은 아닙니다. **직접 선행**은 실제 코드 연결 조건이며, 서로 독립인 분야는 계약 합의 뒤 병렬 작성합니다. **최종 확인**에 적은 공동 확인은 구현 착수의 전역 대기 조건이 아닙니다. 한 사람이 맡으면 계획의 순서 0→1→2를 따라 우선 구현하고 각 분야의 준비된 첫 행을 이어갑니다.

<a id="whole-backlog"></a>

## 전체 실행 목록

| 작업 분야 | 해당 실행행과 기본 우선순위 |
|---|---|
| 기존 전문가 오류→이론·검색→실제 도구→별도 심의 | D01 → D08/D09 → D20 → D21 |
| 공통 계산→첫 Prony→저장·연구창→전체 모델 | D02 → D04/D05 → D06 → D07/D18 → D12 → D13 → D14 → D15 → D16 → D17 |
| 결과 변환·판독→RP | D02 계약 → D10 → D11 → D22. 원 RP 수동 표시와 선택 자동 연결은 따로 종료합니다. |
| Prep 성능·기존 연속 작업·일반 편집 | D03 → D23/D38 → D24/D25. 8방식 비교 D31은 접근 가능한 제품부터 병행합니다. |
| Prep 메셔·형상 후속 | D26 → D27 → D28 → D29 → D30을 기본 우선순위로 하고 실제 선행이 없는 연구는 병행할 수 있습니다. D39는 조건부 후속입니다. |
| 재료 적용·기존 프로그램·실제 환경·전체 인수 | D19/D32 준비 → D33/D34 실제 Windows·설치 → D36. D37 환경 준비는 처음부터, D35 추가 실행 환경은 채택한 분기만 진행합니다. |

정확한 수식·수치 허용오차·자료 구조·오류는 기존 [공통 C](core-implementation.md), [A](material-pressure-orthotropic.md), [B](material-finite-history.md), [전문가 C](expert-implementation.md), [후처리 D](post-implementation.md)와 각 기존 N/단계 기준을 함께 따릅니다. 이 표에서 조건을 줄였다고 상세 명세의 필수 검사를 없애지 않습니다. 표시한 경로 중 현재 없는 것은 새로 만들 대상으로 명시하며, 개발자는 기존 파일을 먼저 확인합니다.

<a id="d01"></a>

### D01. 전문가 창의 프레임·검증 상태 수정

- **종류·담당 분야:** 기존 수정 / 전문가. 연결 요구: WB:#45, WB:P5.
- **첫 파일:** WB: `apps/workbench_qt/expert_window.py` (기존), WB: `apps/workbench_qt/native_analysis.py` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 요청 프레임을 첨부 선택까지 전달하고 검증 중·실패·완료를 표시한다. 기존 토큰·워커를 재사용한다.
- **최종 확인:** 프레임 0, 서로 다른 프레임, 사라진 프레임, 초안·첨부 변경과 닫힌 창에 도착한 늦은 응답을 확인한다. 창을 여는 동작만으로 캡처·AI 호출·해석을 시작하지 않는다.
- **그다음:** 같은 파일 담당자가 D08로 이어간다. #45의 Windows 확인은 D33에 남긴다.

<a id="d02"></a>

### D02. 시험·결과·단위의 공통 자료 구현

- **종류·담당 분야:** 신규 / 재료 공통. 연결 요구: CORE01.
- **첫 파일:** WB: `caelab/artifact_refs.py` (신규), WB: `caelab/materials/contracts.py` (신규), WB: `caelab/materials/units.py` (신규). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 자료·결과·평가의 불변 참조와 명시한 단위·측도 및 변환을 구현한다.
- **최종 확인:** C1/C3/C6의 엄격한 직렬화, 상대 경로·원자료 해시, 누락·단위 불일치 거절을 확인한다.
- **그다음:** D04/D05/D08/D10의 코드 연결에 사용한다. 계약 합의만으로 첫 사용자 납품을 완료 처리하지 않는다.

<a id="d03"></a>

### D03. 숨김·표시 갱신을 한 번에 처리

- **종류·담당 분야:** 기존 개선 / Prep. 연결 요구: PREP:N17.
- **첫 파일:** Prep: `src/caelab_prep/ui/prep_controller.py` (기존), Prep: `src/caelab_prep/ui/viewport.py` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 최종 숨김 집합(hidden)을 한 번에 적용하여 중간에 전체 모델이 표시되는 현상과 중복 렌더링을 제거한다.
- **최종 확인:** 선택·표시·문서·Undo가 유지되는지 확인하고, VTK를 실제로 초기화한 뒤 Render 호출·최종 프레임 확인을 UI 입력 접수와 구별한다. 기존 100만 요소·100 part·20 source에서 고정한 100회 동작으로 숨김·표시(hide/show)의 p95 200 ms와 렌더링 횟수를 측정한다. 목표 미달은 그대로 기록하며 N17 전체 완료와 구분한다.
- **그다음:** D24의 전체 성능 예산과 D23의 기존 편집 작업을 끝까지 확인하는 단계로 이어간다.

<a id="d37"></a>

### D37. 실제 검증 환경·프로그램·원문 접근 준비

- **종류·담당 분야:** 개발과 병행하는 준비 / 환경 준비. 연결 요구: WB:P7, PREP:N03, PREP:N12, PREP:N13, PREP:N14.
- **첫 파일:** WB: `CURRENT_STATE.md` (기존), WB: `docs/tutorials/workbench_quickstart.md` (기존), Prep: `docs/ui-reference-variants.md` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 사용 가능한 Windows 장비, WB/Prep 고정 버전, 외부 솔버·RP·CAD·원제품, pyvisco/NEML/GEOS, 실제 공급자와 허용 원문·시험자료의 위치·버전·권한·연결 방법을 확인한다.
- **최종 확인:** 각 환경의 확인 결과·준비 작업·막히는 정확한 실행행과 가능한 독립 개발을 기록한다. 유료 구매·계정 가입이나 사용자 자료 보유를 임의로 가정하지 않는다. Windows 본검증은 기존 일괄 후속 방침을 유지한다.
- **그다음:** 접근이 갖춰진 분기만 실제로 인수한다. 준비 중에도 D01/D02/D03 등 독립 개발을 진행한다.

<a id="d04"></a>

### D04. 여러 시험의 실제 계산·동정 서비스

- **종류·담당 분야:** 신규 / 재료 공통. 연결 요구: CORE02.
- **첫 파일:** WB: `caelab/materials/experiments.py` (신규), WB: `caelab/materials/residuals.py` (신규), WB: `caelab/materials/registry.py` (신규), WB: `caelab/materials/evaluator.py` (신규), WB: `caelab/materials/service.py` (신규). **직접 선행:** D02.
- **만들 결과:** 엄격한 CSV 검사 → 시험 정의 → 등록 어댑터 → 실제 예측 → 잔차·제약 → 기존 optimize/BatchEvaluator의 흐름을 연결한다.
- **최종 확인:** ACTUAL과 예측의 구분, 후보·시험별 독립 초기화, 고정한 자료 분할·식별성, Stop/Cancel, 캐시 reuse/refresh와 최종 선택 후 보류 시험의 노출 이력을 검사한다.
- **그다음:** D05의 실제 Prony 계산과 합동 확인한 뒤 D06/D07로 연결한다. 모든 후속 모델도 같은 경로에 등록한다.

<a id="d05"></a>

### D05. Prony 점탄성의 첫 실제 동정

- **종류·담당 분야:** 신규 / 재료 계산. 연결 요구: MAT-B.
- **첫 파일:** WB: `caelab/materials/adapters/prony_trs.py` (신규). **직접 선행:** D02.
- **만들 결과:** B-VIS-TRS-1의 2항 일반화 Maxwell 모델(generalized Maxwell)을 구현한다. E0/a1/a2/tau1/tau2 다섯 계수는 조정하고, 온도 이동 계수는 근거 있는 고정값으로 두어 DMA·완화 자료를 계산·동정한다.
- **최종 확인:** 실제 pyvisco 2.1.3 API·소스 SHA, Hz/rad/s·온도·위상·관측 구간, 독립 기준과 미사용 시험을 확인한다. 합성 WLF 값을 실측 자료의 기본값으로 쓰지 않는다. D04와 실제 합동 동정을 수행한다. 첫 시험 묶음은 B3의 온도별 DMA+완화 유한 묶음이며 제품 SLSQP의 ftol=1e-10/FD=1e-6을 따른다. 과거 146회 probe의 자료 분할·평균값·ftol/FD를 제품 인수에 그대로 복사하지 않는다.
- **그다음:** D07의 첫 사용자 납품으로 연결한다. D12에서 동일 프로필의 전체 TRS 범위를 마친다.

<a id="d06"></a>

### D06. 연구 저장·이동·복구 구현

- **종류·담당 분야:** 신규 / 저장·프로젝트. 연결 요구: STORE01.
- **첫 파일:** WB: `caelab/materials/service.py` (신규), WB: `caelab/project.py` (기존), WB: `caelab/native/run_store.py` (기존). **직접 선행:** D04.
- **만들 결과:** 재료 연구 v1·프로젝트 v4에 원자료·선택 평가·라이브러리 식별정보와 소유 결과를 저장한다.
- **최종 확인:** 구형 v1–3 프로젝트 읽기, 잠금·원자 저장·읽기 전용·중단 복구·이동/SaveAs, 실행 중 SaveAs와 누락·변조를 확인한다. 기존 MDO v2는 자동 변환하지 않는다.
- **그다음:** D07 연구창과 D18 독립 프로젝트 연결에 사용한다.

<a id="d08"></a>

### D08. 결과 없이 이론·문헌 질문하기

- **종류·담당 분야:** 신규 / 전문가. 연결 요구: E0-2.
- **첫 파일:** WB: `caelab/assist/contracts.py` (신규), WB: `caelab/assist/discussions.py` (기존), WB: `apps/workbench_qt/expert_window.py` (기존). **직접 선행:** D01, D02.
- **만들 결과:** v2 이론 질문·theory 역할, 첨부 없는 요청, 구형 대화 읽기, 최초 저장 시 백업과 초안 보존을 연결한다.
- **최종 확인:** 첨부·실행 결과가 없어도 사용자가 명시적으로 질문하고 저장·재열기를 할 수 있어야 한다. 질문 scope와 실제 실행·직접 파일·재료 평가의 근거를 섞지 않는다.
- **그다음:** D09 검색, D20 실제 도구 연결과 D21 심의로 이어간다. POST·재료·메셔의 전체 완료를 기다리지 않는다.

<a id="d09"></a>

### D09. 실제 원문 확보·한영 검색·관련성 판정

- **종류·담당 분야:** 신규 / 전문가 검색. 연결 요구: E0-2.
- **첫 파일:** WB: `caelab/assist/corpus.py` (신규), WB: `caelab/assist/query_language.py` (신규), WB: `scripts/build_knowledge_index.py` (신규). **직접 선행:** D02.
- **만들 결과:** 허용된 원문·판본·출처·고정 구간·원자료 해시, CorpusManifest와 한영 QueryPlan·BM25/혼합 검색을 구현한다. 오프라인 원문·색인 준비와 실제 AI의 관련성 판정을 분리한다.
- **최종 확인:** 원문 미확보/source_unavailable, 추출·chunk·인용 구간의 대응을 확인한다. 독립 질문의 정답으로 검색 재현율·관련성을 확인한다. 실제 의미 판정(semantic)은 D20의 공급자·예산·추적 기록(trace)이 준비된 뒤 인수한다.
- **그다음:** D20과 연결해 실제 문헌 자문을 인수한다. 색인 파일 생성만으로 완료 처리하지 않는다.

<a id="d10"></a>

### D10. 기존 실행기에 변환 작업 소유 기능 추가

- **종류·담당 분야:** 신규 / 후처리. 연결 요구: POST01-04.
- **첫 파일:** WB: `caelab/jobs.py` (기존), WB: `caelab/native/operations.py` (신규), WB: `caelab/native/converters.py` (신규). **직접 선행:** D02.
- **만들 결과:** 기존 JobManager의 소유 프로세스·취소·시간 제한을 재사용해 submit_result_conversion/operation_snapshot/cancel_operation을 구현한다.
- **최종 확인:** operation.json을 솔버의 job.json과 구별한다. stdout/stderr·종료 상태·부분 결과·낡은 결과·취소·자식 프로세스 정리·SaveAs를 보존한다. 새 작업 대기열(queue)이나 가짜 솔버 실행을 만들지 않는다.
- **그다음:** D11의 외부 변환기와 D22 선택 RP 자동 연결에 사용한다.

<a id="d11"></a>

### D11. OpenCourant 공간장·시간 이력 정확히 읽기

- **종류·담당 분야:** 신규 / 후처리. 연결 요구: POST01-04.
- **첫 파일:** WB: `caelab/native/result_adapters.py` (신규), WB: `caelab/result_readers/vtk_fields.py` (신규), WB: `caelab/result_readers/opencourant_history.py` (신규). **직접 선행:** D02.
- **만들 결과:** 지원 변환기와 원래 형식의 실제 시간·ID·층·축·단위를 보존해 공간장(field)·시간 이력(history)을 읽고 수치 조회를 제공한다.
- **최종 확인:** 실제 변환기·원결과·독립 수치 기준으로 개수·값·TIME을 대조한다. frame_index를 실제 시간으로 가장하지 않는다. 빈 결과나 부분 결과를 성공으로 게시하지 않는다. 기존 VTU의 수치 조회는 D02 뒤 독립 구현·확인한다. 외부 변환기를 실제 실행하는 경로에만 D10 작업 소유 코드가 필요하다.
- **그다음:** 해당 TH 판독부터 D13 LAW33에 연결한다. 공간장(field) 판독이 되면 D22의 기본 RP 연결도 병행한다.

<a id="d23"></a>

### D23. 이미 구현한 6종 메시 편집과 연속 작업 확인

- **종류·담당 분야:** 기존 검증·발견결함 수정 / Prep 기존 편집. 연결 요구: PREP:N18, PREP:N15.
- **첫 파일:** Prep: `src/caelab_prep/ui/mesh_workflow.py` (기존), Prep: `src/caelab_prep/application/mesh_relations.py` (기존), Prep: `src/caelab_prep/ui/element_edits.py` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 절점 이동(node), 명시 병합(merge), 요소 삭제, 셸 법선 변경(shell), 재배정과 크기 변경에 따른 재메시(size)를 재구현하지 않는다. 두 사용자 입력으로 연속 GUI 작업·네이티브 파일·솔버까지 확인한다.
- **최종 확인:** 후보 → 영향 검토 → Apply/취소 → Undo → 저장·새 프로세스 재열기 → Starter/Engine에서 ID·참조·질량/조건을 비교한다. 오류는 같은 행에서 수정한다. Windows 확인은 D33과 공유한다. N11의 별도 CAD·메시 형상·규모 조건은 D38에서 확인한다.
- **그다음:** D25의 실제 미구현 편집 확대와 D26–D30 메셔 연구를 분리한다.

<a id="d07"></a>

### D07. CSV부터 동정·저장·재열기까지 연결

- **종류·담당 분야:** 신규 / 연구 화면. 연결 요구: UI01.
- **첫 파일:** WB: `apps/workbench_qt/material_study.py` (신규). **직접 선행:** D04, D05, D06.
- **만들 결과:** 실제 CSV 열기, 열·단위 지정, 계수의 고정·자유·연계 및 범위 설정, 실행, 곡선·잔차 확인, 결과 선택·저장과 새 프로세스 재열기를 한 연구창에 연결한다.
- **최종 확인:** 실제 Prony 모델로 캐시를 쓰지 않는 새 확인 평가(refresh), 선택 고정 후 보류 시험 확인, Stop/Cancel·실패 처리와 이전 결과 선택 보존을 수행한다. Windows 사용자 인수는 D33에서 따로 기록한다.
- **그다음:** 첫 사용자 납품 이후에도 계속한다. D12–D17의 모든 프로필, D19 적용, D21 수치 자문과 D36 전체 종료까지 확인한다.

<a id="d18"></a>

### D18. 독립 연구창·프로젝트·공용 위젯 연결

- **종류·담당 분야:** 신규 / 프로젝트 연결. 연결 요구: LINK01, PREP:N19, PREP:N20, PREP:N21.
- **첫 파일:** WB: `caelab/connections.py` (기존), WB: `caelab/project.py` (기존), WB: `caelab/workbench.py` (기존), WB: `apps/workbench_qt/project_window.py` (기존). **직접 선행:** D06, D07.
- **만들 결과:** 연구 파일 역할·독립 실행·프로젝트 상대 참조·SaveAs와 Prep의 공용 숫자·곡선·속성 위젯을 연결한다.
- **최종 확인:** 기존 v1–3 파일과 MDO·RunStore의 회귀, 독립 창·선택 평가 보존·누락 자료·프로젝트 이동을 확인한다. Prep 문서나 Undo의 소유를 복제하지 않는다.
- **그다음:** D19 적용은 PREP01과 합동 확인하고 D36의 같은 프로젝트 전체 흐름으로 이어간다.

<a id="d20"></a>

### D20. 실제 AI 검색·읽기 도구 왕복

- **종류·담당 분야:** 신규 / 전문가. 연결 요구: E3-5.
- **첫 파일:** WB: `caelab/assist/query_tools.py` (신규), WB: `caelab/assist/provider_steps.py` (신규), WB: `caelab/assist/experts.py` (기존). **직접 선행:** D08, D09.
- **만들 결과:** 구독·로컬 공급자별 실제 도구 요청 → 호스트 읽기 → 반환 → 답변을 연결하고 역할별 자료·도구 권한과 명시한 예산을 검사한다. 실제 관련성 판정 프로필을 GUI에서 선택 → 불변 ref로 저장 → ask API에 전달 → 별도 판정 요청·추적 기록(trace)으로 호출하는 경로를 구현한다. 이 호출도 기존 4회 반복·공급자 60초·전체 85초의 공유 예산과 실제 요청 수에 포함한다.
- **최종 확인:** 실제 문헌 질문과 manual 직접 파일 질문을 각각 수행한다. 설정·모의 응답·목록 등록만으로 실제 호출을 완료했다고 하지 않으며, 없는 실행 ID를 만들지 않는다. 사용자 프로필 미선택·호출 실패·원문 부족을 서로 다른 상태로 표시한다. 문헌 답변의 독립 심의는 D21의 문헌 인용·근거 묶음 구현 뒤 완료 처리한다.
- **그다음:** D21 독립 심의로 연결한다. native 수치 질문에만 D11, calibration 질문에만 D04와 고정 평가가 필요하다.

<a id="d31"></a>

### D31. 8개 제품별 같은 과업 비교·차이 수정

- **종류·담당 분야:** 기존 검증·UI 보완 / Prep 8가지 GUI. 연결 요구: PREP:N03, PREP:N04, PREP:N05, PREP:N06, PREP:N07, PREP:N08, PREP:N09, PREP:N10.
- **첫 파일:** Prep: `src/caelab_prep/ui` (기존), Prep: `docs/ui-reference-variants.md` (기존), Prep: `docs/benchmarks/2026-10-30/README.md` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** HyperMesh → PrePoMax → Naksha → Abaqus/CAE → COMSOL → midasNFX → MeshFree → BeidPrep 순서로 같은 T1–T6 과업의 원제품 버전·대응 작업을 확보해 차이를 수정한다.
- **최종 확인:** 각 제품의 전체 창·모달 창·오류/취소·클릭·키·값·파일을 같은 Prep 구현과 대조한다. 접근하지 못한 항목은 보류하며 제품명·색 변경이나 스크린샷 관찰만으로 완료 처리하지 않는다.
- **그다음:** D23/D33과 같은 증거를 공유하되 N03–N10은 제품별로 따로 종료한다.

<a id="d38"></a>

### D38. 대표 조립의 CAD·메시부터 네이티브 입력까지 연결

- **종류·담당 분야:** 기존 검증·발견결함 수정 / Prep 연속 과업. 연결 요구: PREP:N11, PREP:N01, PREP:N02.
- **첫 파일:** Prep: `src/caelab_prep/ui/mesh_workflow.py` (기존), Prep: `src/caelab_prep/application/mesh_relations.py` (기존), Prep: `src/caelab_prep/application/session.py` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** N11의 서로 다른 두 사용자 입력에서 CAD/외부 메시 → 후보 → 채택·재연결 → 재료/단면/파트 → 곡선/집합/조건 → 저장/새 프로세스 재열기 → Starter/Engine을 이어서 확인한다.
- **최종 확인:** C1의 열린 표면(open surface) 20k–100k·면 국부(face-local) 경로와 C2의 서로 맞닿은 솔리드(touching solids)에 대한 conformal/local-volume/independent 경로를 원문서·ID/참조/조건·실제 솔버와 대조한다. N01의 case와 N02의 tri3/tet4 import는 기존 구현을 재사용한다. D23의 6개 연산 통과만으로 N11 전체를 완료 처리하지 않는다.
- **그다음:** 기능 결함은 해당 N행에서 수정한다. 원제품 비교는 D31, 실제 Windows는 D33, 두 앱은 D36에서 같은 자료로 확인한다.

<a id="d12"></a>

### D12. Prony의 전체 온도·항 수·이력 범위

- **종류·담당 분야:** 신규 / 재료 계산. 연결 요구: MAT-B.
- **첫 파일:** WB: `caelab/materials/adapters/prony_trs.py` (신규). **직접 선행:** D04, D05.
- **만들 결과:** B3의 WLF 7개 자유계수, n=1/2/3 선택, 온도 변화 구간(램프)·시간 이력까지 같은 프로필을 확장한다.
- **최종 확인:** 선택에는 train/validation만 사용하고 holdout은 선택을 고정한 뒤 새 확인 평가에서만 노출한다. B3의 모든 수치·위상·경로 순서·초기화와 실패 반례를 확인한다.
- **그다음:** D07 화면에서 전체 프로필의 사용을 인수하고, 다음 D14–D17 모델을 동일 계약으로 연결한다.

<a id="d13"></a>

### D13. LAW33 발포체 두 프로필의 실제 솔버 동정

- **종류·담당 분야:** 신규 / 재료·솔버. 연결 요구: MAT-A.
- **첫 파일:** WB: `caelab/materials/adapters/opencourant_foam33.py` (신규). **직접 선행:** D04, D11.
- **만들 결과:** oc_foam33_ka0_v1/ka1을 계수 → 입력 사본 → 실제 Starter/Engine → TH → 여러 시험의 잔차 → 새 최종 평가로 연결한다.
- **최종 확인:** Ka0/Ka1 각각의 독립 수치·시간 세분화·에너지/힘 수지·쿠폰 관측·holdout과 입력·출력 원자료의 해시를 확인한다. 기존 참조 실행의 성공을 제품 동정의 성공으로 재사용하지 않는다.
- **그다음:** D19의 네이티브 내보내기·열린 Prep 문서 적용과 D21 수치 심의에 연결한다.

<a id="d14"></a>

### D14. 비압축·압축 Ogden 초탄성 두 프로필

- **종류·담당 분야:** 신규 / 재료 계산. 연결 요구: MAT-B.
- **첫 파일:** WB: `caelab/materials/adapters/ogden.py` (신규). **직접 선행:** D04.
- **만들 결과:** 정확한 INC/COMP 두 정식화, 횡방향 평형근, 제약과 독립 시험 동정을 구현한다.
- **최종 확인:** B2의 평형근(root) 선택·안정성·다중 시험·초기화·실패·holdout을 두 프로필 각각에서 확인하고 D07/D06으로 사용한다.
- **그다음:** D15 압력 의존, D16 방향성, D17 반복·속도·온도 모델로 확대한다.

<a id="d15"></a>

### D15. DP 해석식과 GEOS 드라이버 두 경로

- **종류·담당 분야:** 신규 / 재료 계산. 연결 요구: MAT-A.
- **첫 파일:** WB: `caelab/materials/adapters/dp_axisym_closed.py` (신규), WB: `caelab/materials/adapters/geos_dp.py` (신규), WB: `caelab/native/geos_driver.py` (신규), WB: `caelab/native/adapter.py` (기존), WB: `caelab/connections.py` (기존). **직접 선행:** D04.
- **만들 결과:** dp_axisym_closed_v1과 geos_dp_axisym_linear_v1을 별도로 등록하고 GEOS 입력, 프로그램 발견·실행과 텍스트 결과를 연결한다.
- **최종 확인:** A4의 혼합 제어·탄성/소성 상태·모든 행·드라이버 원자료 해시를 검증한다. GEOS는 material_library이며 FE 필드로 가장하지 않는다. 실제 Linux 확인과 Windows 미지원을 구분한다.
- **그다음:** D16/D17과 함께 전체 11개 프로필의 지원표를 완성한다. GEOS 환경 대기는 해석식·다른 모델의 개발을 막지 않는다.

<a id="d16"></a>

### D16. 직교이방 탄성과 비선형 XBP 파생 모델

- **종류·담당 분야:** 신규 / 재료 계산. 연결 요구: MAT-A.
- **첫 파일:** WB: `caelab/materials/adapters/orthotropic_plane.py` (신규), WB: `caelab/materials/adapters/gmn12_xbp.py` (신규). **직접 선행:** D04.
- **만들 결과:** ortho_plane_stress_elastic_v1과 gmn12_xbp_power_plane_stress_v1의 방향·평면응력·이력·여러 시험 동정을 구현한다.
- **최종 확인:** A6/A7의 식·축·파워 법칙·상태/수렴·독립 기준을 각각 검증한다. 파생 XBP를 Xia2002 원모델이나 LAW112 지원으로 표시하지 않는다.
- **그다음:** D19의 모델별 적용 판정과 D21 근거 검토에 연결한다.

<a id="d17"></a>

### D17. Johnson–Cook 상태 모델과 Chaboche 반복 소성

- **종류·담당 분야:** 신규 / 재료 계산. 연결 요구: MAT-B.
- **첫 파일:** WB: `caelab/materials/adapters/neml_chaboche.py` (신규), WB: `caelab/materials/adapters/jc_state.py` (신규). **직접 선행:** D04.
- **만들 결과:** B-CYC-AF2-1의 실제 NEML 반복 응력 이력과 B-JC-STATE-1의 측정 소성 상태·속도/온도 유동응력을 별도로 구현한다.
- **최종 확인:** B4/B5의 전체 독립 기준·소스 대응·상태 초기화·세분화·시험정보·holdout·플랫폼을 각각 확인한다. NEML 미설치나 exit20·21은 미충족이며, JC를 요소 삭제·파괴에너지 모델로 확대하지 않는다.
- **그다음:** D12–D17의 총 11개 프로필을 계산·동정·FE·출력·Apply·플랫폼별로 각각 판정한 뒤 D36에서 재료 범위를 확인한다.

<a id="d24"></a>

### D24. 100만 요소 전체 성능 목표 달성

- **종류·담당 분야:** 기존 개선 / Prep 성능. 연결 요구: PREP:N17.
- **첫 파일:** Prep: `src/caelab_prep/ui` (기존), Prep: `src/caelab_prep/cae_core` (기존), Prep: `tools/benchmark_model.py` (신규). **직접 선행:** D03.
- **만들 결과:** N17의 지연 UI·배열 공유·부분 갱신·저장 경로에서 실제 병목을 고정 측정으로 찾아 순서대로 수정한다.
- **최종 확인:** 같은 고정 입력인 100만 요소·100 part·20 source에서 열기/재열기(open/reopen) 45초, 저장(save) 60초, 최대 RSS(peakRSS) 12 GiB/동시 16 GiB를 execution-stages 기준으로 확인한다. 고정한 100회 동작의 p95는 선택(selection) 150 ms/스칼라 편집(scalar edit) 100 ms/Undo 100 ms/표시(visibility) 200 ms다. 취소(cancel) 접수 1초/worker 종료 5초도 확인한다. 최종 후보는 최대 3회 확인하고 원자료·환경·목표 미달과 Windows 실화면 확인을 구분한다.
- **그다음:** D33의 실제 Windows/VTK 확인과 연결하고 N17의 일부 개선·전체 합격을 나눠 기록한다.

<a id="d25"></a>

### D25. 일반 메시 편집의 후속 연산 구현

- **종류·담당 분야:** 기존 계획 미구현 / Prep 메시 편집. 연결 요구: PREP:N18.
- **첫 파일:** Prep: `src/caelab_prep/application/mesh_edit_candidates.py` (기존), Prep: `src/caelab_prep/application/native_mesh_edit.py` (기존), Prep: `src/caelab_prep/ui/element_edits.py` (기존), Prep: `src/caelab_prep/radioss/modeling.py` (기존). **직접 선행:** D23.
- **만들 결과:** 모서리 분할(edge split) → 모서리 교환(edge swap) → quad/tri 변환 → 고차 승격 순서로 연산별 지원 요소·경계·집합/조건 영향과 네이티브 codec을 확정해 구현한다. 첫 edge split은 명시적인 선형 삼각 표면부터 선정하고 새 node/cell·네이티브 ID 분배·양측 경계·집합/조건 영향을 계획한다. quad/tri는 family/card·단면 정식화, 고차는 공유 중간 절점(midnode)·곡면 경계·고차 Jacobian을 별도로 구현한다.
- **최종 확인:** 각 연산의 보존량·품질·공유 경계·참조 영향·Preview/취소/한 번의 Undo·재열기/네이티브 재읽기와 실제 솔버를 확인한다. 미지원 배치(unsupported layout)나 뒤틀린 사각형(warpedquad) 등은 명시적으로 거절한다.
- **그다음:** 일반 CAD의 자동 조건 복구를 완료 처리하지 않는다. 각 지원 확대는 N18에 따로 근거를 연결한다.

<a id="d26"></a>

### D26. 선·beam/truss와 준비된 셸면의 제품 연결

- **종류·담당 분야:** 기존 연구 후속 / Prep 메셔. 연결 요구: PREP:N22.
- **첫 파일:** Prep: `src/caelab_prep/application/structured_mesh.py` (기존), Prep: `src/caelab_prep/application/mesh_candidates.py` (기존), Prep: `src/caelab_prep/application/session.py` (기존), Prep: `src/caelab_prep/radioss/modeling.py` (기존), Prep: `src/caelab_prep/ui/mesh_workflow.py` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 검증된 제한 SDK를 CAD 곡선/좌표틀(curve/frame), 선 단면·방향, 준비된 셸(shell)의 두께·offset과 공통 채택 경로에 연결한다.
- **최종 확인:** F0–F2의 정확한 요소군(family)·절점 순서(nodeorder)·두께/축 계약을 정한 뒤 GUI·Undo·저장·네이티브 파일·실제 솔버의 F3–F4를 확인한다.
- **그다음:** D27 중립면과 D28/D29 quad/hex로 확대하되 독립 재료 계산·이론 질문을 막지 않는다.

<a id="d27"></a>

### D27. 제한 중립면의 형상군 실증·제품 연결

- **종류·담당 분야:** 기존 연구 후속 / Prep 메셔. 연결 요구: PREP:N23.
- **첫 파일:** Prep: `scripts/evaluation/midsurface_recipe/paired_offset.py` (기존), Prep: `docs/mesher-feasibility-roadmap.md` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 평판·절곡·단순 접합의 쌍 구성/오프셋(pair/offset)·공차·수동 보정과 원 솔리드(solid) 참조를 실증한 뒤 채택 UI로 연결한다.
- **최종 확인:** 얇고 긴 형상·작은 피처·접합부·미사용 형상의 위치/두께·연결 품질·실패를 F0–F4로 검사한다. 임의 형상의 자동 중립면 생성으로 확대하지 않는다.
- **그다음:** 실증 실패 시 정확한 no-go 판정과 외부·수동 대안을 평가하고 기능은 미완료로 남긴다. D26과 지원 범위 안에서 연결한다.

<a id="d28"></a>

### D28. quad 지원 형상과 실무 품질 확대

- **종류·담당 분야:** 기존 부분 구현 확대 / Prep 메셔. 연결 요구: PREP:N24.
- **첫 파일:** Prep: `src/caelab_prep/application/structured_mesh.py` (기존), Prep: `src/caelab_prep/application/structured_geometry.py` (기존), Prep: `src/caelab_prep/ui/mesh_workflow.py` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 첫 코드는 기존 두 패치(patch)의 생성 설정(recipe)에서 공유 꼭짓점(vertex)을 한 번 편집해 양쪽 패치를 일관되게 갱신하는 기능이다. structured_mesh가 현재 거절하는 patches 편집을 명시한 지원 범위에서 확장한다. 품질값이 양수인지, 원래 recipe가 바뀌지 않는지, 새 후보 생성·Undo·재열기·네이티브 출력을 확인한 뒤 기존 평면 사각형(planar quad)의 재생성·채택을 재사용해 곡면·다중 파트(curved/multipart)의 전체 H-Q 형상군으로 확대한다.
- **최종 확인:** 고정 형상·미사용 형상·얇고 긴 형상·복잡한 피처 형상의 요소 목록(inventory)·연결·품질·네이티브 파일·GUI·솔버를 F0–F4로 확인한다. 몇 가지 평면(planar) 형상의 성공을 전체 합격으로 쓰지 않는다.
- **그다음:** D29 sweep hex로 이어간다. 지원 불가 항목은 외부 메셔·수동 분할 대안을 평가한다.

<a id="d29"></a>

### D29. sweep hex 지원 형상과 실무 품질 확대

- **종류·담당 분야:** 기존 부분 구현 확대 / Prep 메셔. 연결 요구: PREP:N25.
- **첫 파일:** Prep: `src/caelab_prep/application/structured_mesh.py` (기존), Prep: `src/caelab_prep/application/structured_geometry.py` (기존), Prep: `src/caelab_prep/ui/mesh_workflow.py` (기존). **직접 선행:** D28.
- **만들 결과:** D28의 복수 패치(multipatch) 편집 경로를 재사용해 스윕 벡터(sweep vector)·층(layer)·공유면(shared face)을 확인하는 것부터 시작한다. 기존의 제한된 Hex8 단면·생성 설정(recipe)을 재사용하고 비틀림·곡면·원통·환형(annulus)·CAD 분할의 H-S/F4를 이어 실증한다.
- **최종 확인:** 절점 순서·Jacobians·품질·전이 경계·얇고 긴 형상·미사용 형상·실제 네이티브 파일/솔버를 확인한다. 지원 불가 형상은 원본을 보호하고 명시적으로 거절한다.
- **그다음:** D30 혼합 전이와 대체 엔진 후보 검증으로 이어가되 범용 all-hex 완료로 확대하지 않는다.

<a id="d30"></a>

### D30. hex/tet/prism/pyramid 전이의 실제 지원 확보

- **종류·담당 분야:** 기존 연구 후속 / Prep 메셔. 연결 요구: PREP:N26.
- **첫 파일:** Prep: `src/caelab_prep/radioss/modeling.py` (기존), Prep: `docs/mesher-feasibility-roadmap.md` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 공식 입력 생성기(writer)·솔버의 전이 요소 지원을 먼저 확인하고, 경계가 일치하는(conformal) 메셔·라이선스·배포·실제 API 대안을 평가한다.
- **최종 확인:** 원 요소 목록(inventory)·면 연결(face)·비정합 절점(hangingnode)·품질·질량중심·해석 응답을 F0–F4로 확인한다. 요소를 버리거나 반복 절점의 BRICK으로 치환해 손실 없는 지원을 가장하지 않는다.
- **그다음:** no-go는 조사 판정이며 제품 기능 완료가 아니다. 검증된 대체 메셔·혼합 지원 또는 명시적인 미지원 상태를 전체 종료 판정에 남긴다.

<a id="d39"></a>

### D39. Auto-Tet·형상 정리 등 미진한 연구 후속

- **종류·담당 분야:** 조건부 연구·지원확대 / 조건부 CAD·메시. 연결 요구: PREP:Phase3.6, PREP:Phase3.7, WB:WBS5.
- **첫 파일:** Prep: `docs/mesher-feasibility-roadmap.md` (기존), Prep: `docs/execution-stages.md` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** Phase3.6 Auto-Tet의 과거 품질 미달·no-go와 준비된 Tet10(prepared Tet10)의 제한된 성공을 구별한다. Phase3.7의 와셔(washer)·필렛 억제(fillet)·일괄 처리/정리(batch/cleanup)에 대한 범위·후보 API·라이선스·대표 형상을 먼저 고정한다.
- **최종 확인:** 기존 시제품(prototype)·입력·품질 원자료를 재사용해 F0 → F2 실증 → 지원 범위/영향 계약 → 제품 채택 → 실제 솔버 F3/F4 순서로 판정한다. 얇고 긴 형상·복잡한 피처·공식 형상·미사용 형상을 포함한다. 품질 미달이나 미병합 시제품을 구현 완료로 세지 않는다.
- **그다음:** 채택한 기능만 기존 N/단계의 후속으로 구현하고, no-go이면 검증된 외부·수동 대안을 평가한다. 미결정 조건을 전체 실행 대기열에서 삭제하지 않는다.

<a id="d19"></a>

### D19. 카드·곡선·축의 지원 확대와 원자 적용

- **종류·담당 분야:** 신규 / Prep 재료 적용. 연결 요구: PREP01, PREP:N15, PREP:N16, PREP:N19, PREP:N20.
- **첫 파일:** Prep: `src/caelab_prep/application/material_binding.py` (신규), Prep: `src/caelab_prep/radioss` (기존), Prep: `src/caelab_prep/application` (기존). **직접 선행:** D02, D04.
- **만들 결과:** 기존 document.edit의 LAW1 E 연결을 재사용해 신규 prepare/commit 응답부터 구현한다. 이어 정확한 지원 모델의 전체 계수·곡선·축에 대한 codec·미리보기·적용을 연결한다. material_binding.py는 신규 파일이다.
- **최종 확인:** 단일 E 연결과 전체 동정값 적용을 별도로 판정한다. 최종 확인은 D06/D07/D13/D18과 함께 수행하며 개정 충돌·응답 유실·한 번의 Undo/Redo·저장/재열기·네이티브 의미·실제 FE 재평가를 확인한다. Ka0 curve_id=0 동정만으로 다중 곡선 적용을 완료 처리하지 않는다. 지원 재료+FUNCT101의 별도 transaction과 공유 곡선의 사용처를 확인한다. 축 적용은 방향을 쓰는 지원 모델의 codec으로만 인수하며, 미지원 방향 Apply는 미지원으로 유지한다.
- **그다음:** 지원하는 내보내기 구현(exporter)이 있을 때만 네이티브 덱을 제공하고, 미지원이면 보고서만 제공한다. 계산·내보내기·Apply를 각각 표시한 뒤 D36에서 사용자 변경·재해석을 확인한다.

<a id="d21"></a>

### D21. 실제 수치 인용·독립 답변 검토·반론 처리

- **종류·담당 분야:** 신규 / 전문가 심의. 연결 요구: E3-5.
- **첫 파일:** WB: `caelab/assist/answer_review.py` (신규), WB: `caelab/assist/discussions.py` (기존), WB: `caelab/assist/query_tools.py` (신규). **직접 선행:** D20.
- **만들 결과:** 먼저 문헌 인용(citation), 실제 표시 내용에 고정한 exact-shown EvidencePack, 별도 검토자(reviewer) 요청과 불변 이벤트(event) 저장을 구현해 theory 답변 전체를 검토한다. 이어 원자료 해시·위치(locator)·단위·실행/평가에 묶인 수치 인용과 반론 → 수정 → 재심을 연결한다.
- **최종 확인:** 실제 이론·수치·재료·manual 질문을 분리해 모든 주장의 근거를 검토한다. native 질문에는 D11, 재료 질문에는 고정 평가와 보류 시험 노출 기록이 필요하다. 원 지적자가 수정 turn·필수 세 필드·이벤트를 재검토한다.
- **그다음:** D36에서 같은 수치·대화·심의·사용자 적용·별도 재해석·재열기를 합동 확인한다.

<a id="d22"></a>

### D22. 원 RP 수동 표시와 선택 자동 연결

- **종류·담당 분야:** 신규 / RP 후처리. 연결 요구: POST05-06.
- **첫 파일:** WB: `caelab/native/rp_consumer.py` (신규), WB: `별도 OpenCourantRP 선택 수정판` (외부). **직접 선행:** D11.
- **만들 결과:** 먼저 독립적으로 복사한 결과를 원 RP에서 수동으로 열어 표시한다. 자동화는 별도 수정판의 --post-only --handoff/--receipt 경로로 구현한다.
- **최종 확인:** 수동·자동 각각의 실제 선택 자료·TIME·필드·층·첫 화면을 확인한다. 원본을 바꾸지 않으며 솔버 재실행은 0건이어야 한다. 자동 연결은 D10의 소유·취소/timeout, nonce/hash, 지연 응답, Run/Stop/GZIP 차단과 Windows를 별도로 인수한다.
- **그다음:** 원 RP 사용은 선택 수정판 전체를 기다리지 않는다. 자동 지원은 선택한 경우에만 완료 선언하고 D36에 결과를 연결한다.

<a id="d32"></a>

### D32. CAD·PrePoMax·MDO·보조 해석의 남은 전체 흐름 확인

- **종류·담당 분야:** 기존 검증·발견결함 수정 / 기존 프로그램 연결. 연결 요구: WB:P3, WB:P4, WB:P5, WB:P6, WB:WBS2, WB:WBS3.
- **첫 파일:** WB: `caelab/connections.py` (기존), WB: `caelab/workbench.py` (기존), WB: `caelab/mdo/service.py` (기존), WB: `apps/workbench_qt/project_window.py` (기존), WB: `docs/tutorials/cad-file-handoff.md` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 이미 병합된 CAD·프레임 뷰어·전문가 연결(브리지)·MDO·보조 해석을 재구현하지 않는다. 실제 CAD 문서/STEP → PrePoMax PMX/INP → CalculiX → FRD/ParaView의 전체 흐름을 확인한다. 기존 Workbench.open_program/submit_native/open_native_results와 MDOStudyService.confirm/apply/undo/rerun_current/save_as를 실제 사용자 인수의 진입점으로 사용한다.
- **최종 확인:** 원문서·출력·갱신 필요 상태(stale), 선택 실행, 정밀 결과, 명시적 변경·재해석, 저장·재개와 MDO·보조 창의 영구 이력을 확인한다. 독립 외부 입력과 과거 PASS 기록이 충돌하면 원자료로 해결한다. 기존 MDO 명세의 DisciplineGraph·비순환 결합 목표와 현재 단일 CalculiX 구현을 대조해 실제 지원·미지원·후속 범위를 판정한다. 기존 INP 기능 인수로 다분야 결합 전체를 완료 처리하지 않는다.
- **그다음:** 환경별 GUI는 D33, 배포는 D34, 전체 독립 입력·검색 평가는 D36에서 종료한다.

<a id="d35"></a>

### D35. Code_Aster·FEniCSx의 미구현 연결 처리

- **종류·담당 분야:** 조건부 실행 환경 개발 / 추가 실행 환경. 연결 요구: WB:P4, WB:P7.
- **첫 파일:** WB: `caelab/connections.py` (기존), WB: `caelab/pde` (기존), WB: `caelab/solvers` (기존), WB: `caelab/native/code_aster.py` (기존), WB: `caelab/pde/scipy_solver.py` (기존), WB: `caelab/solvers/pde_solver.py` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 프로그램별 미지원 경로를 남기고 Code_Aster/FEniCSx·WSL 채택 여부와 Windows 네이티브 우선 원칙을 먼저 확인한다. 채택한 분기만 실행 환경·경로·파일·작업 연결을 구현한다. Code_Aster 현재 판독은 JSON fixture에 한정되므로 채택 시 실제 MED/HDF5 판독·변환과 물리 필드·단위 검증을 구현한다. SciPy2D는 기존 Python API를 재사용하며, 제품 연결을 채택하면 프로그램 등록→작업 소유→VTU 생성→NativeResultView를 구현한다. 3D·시간적분은 별도 범위로 유지한다.
- **최종 확인:** Code_Aster의 미결정 환경과 FEniCSx의 WSL 미연결은 현재 미지원으로 유지한다. 실제 실행·결과/단위·시간·파일 교환을 프로그램별로 확인하며 PDE 값을 구조 변위·응력으로 표시하지 않는다.
- **그다음:** 환경 결정이 필요한 분기만 보류한다. 기존 지원 프로그램의 제품 인수와 구별하고 전체 미완료표에 남긴다.

<a id="d33"></a>

### D33. 두 앱과 실제 프로그램의 Windows 사용 확인

- **종류·담당 분야:** 기존·신규 최종 검증 / Windows 통합. 연결 요구: PREP:N12, PREP:N14, WB:P7, WB:WBS4.
- **첫 파일:** WB: `apps/workbench_qt/WINDOWS_CHECK.md` (기존), Prep: `src/caelab_prep/ui` (기존), WB: `apps/workbench_qt` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 기존 5개 실패(설치 3개·Qt worker 2개)를 고정 환경에서 재현하고 원인을 수정하거나 정확한 환경 차단을 판정한다. 두 앱·각 연결·새 기능을 실제로 조작한다.
- **최종 확인:** Qt/VTK 실화면, IME, 한글·공백 경로, FHD/4K, DPI·좁은 창, 취소·닫기, 저장·전체 재개와 실제 공급자·솔버·후처리를 구별해 기록한다. 과거 별도 스냅숏의 cp949 읽기·pythonw timeout·RAD 환경 실패도 원자료를 보존하고, 현재 고정 환경에서 해당 증상이 재현되는지와 원인·영향을 판정한다. 다른 스냅숏의 실패 수를 한 실행 결과로 합치지 않는다.
- **그다음:** 필요한 기능이 준비될 때마다 확인한다. Windows 대기는 독립적인 Linux 개발을 막지 않는다. D34 새 설치와 D36 최종 묶음에 연결한다.

<a id="d34"></a>

### D34. 새 Windows 설치·portable 배포·업데이트·복구

- **종류·담당 분야:** 기존 배포 잔여 / 배포·설치. 연결 요구: PREP:N13, WB:P7.
- **첫 파일:** WB: `scripts/build_windows_bundle.py` (기존), WB: `scripts/archive_windows_bundle.py` (기존), WB: `scripts/build_windows_installer.py` (기존), WB: `apps/workbench_qt/desktop_entry.py` (기존), WB: `apps/workbench_qt/bundle_diagnostics.py` (기존), WB: `docs/tutorials/windows-installer.md` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** 현재 portable·설치형 준비와 컴파일 증거를 재사용하고, 새 환경의 실행·저장·재열기·솔버·Qt/VTK·설치 실패 복구·업데이트를 검증·수정한다.
- **최종 확인:** EXE 생성이나 현재 개발 PC에서 초기 창을 관측한 사실을 새 환경 설치(clean) 완료로 세지 않는다. 서명·버전·등록, 산출물 해시(hash), 외부 프로그램 발견과 두 앱의 조합을 정확히 기록한다.
- **그다음:** D33의 실제 기능 확인과 같은 배포 후보를 D36 전체 인수에 사용한다. 과거 11/5·11/8 목표일을 새 납기로 쓰지 않는다.

<a id="d36"></a>

### D36. 같은 프로젝트로 전체 요구의 완료 판정

- **종류·담당 분야:** 합동 인수 / 전체 인수. 연결 요구: WB:P1, WB:P2, WB:P3, WB:P4, WB:P5, WB:P6, WB:P7, PREP:G0, PREP:G1, PREP:G2, PREP:G3, PREP:G4.
- **첫 파일:** WB: `CURRENT_STATE.md` (기존), WB: `docs/review/2026-10-23-codex-improvement-plan.md` (기존), WB: `docs/specs/cae-research/implementation-map.json` (기존). **직접 선행:** 없음; 기존 기반 또는 합의한 계약으로 독립 준비.
- **만들 결과:** CAD·Prep·해석·재료 동정·자문/독립 심의·명시적 적용/별도 재해석·RP/ParaView·저장/재열기를 같은 프로젝트·자료로 확인한다.
- **최종 확인:** D01–D34 중 D22는 원 RP 수동 표시를 필수로, 자동판은 채택한 경우에만 필수로 하여 각 지원 범위의 근거와 미해결 조건을 대조한다. 독립 외부 입력의 비선형·마찰 접촉·혼합 요소, 전체 11개 프로필·8방식·Windows·검색 질문·실물 자료 적합성을 따로 확인한다. D38 대표 연속 과업은 필수이며 D35 미결정 환경·D39 조건부 연구는 채택한 범위에서만 인수한다. 미충족 필수 요구가 있으면 전체 완료가 아니다.
- **그다음:** 모든 필수 요구가 충족돼야 전체 완료다. 남은 항목이 있으면 부분 납품과 다음 작업을 같은 정본에 남기고 계속한다.

## 기존 진도표에서 빠지지 않았는지 확인

| 기존 항목 | 실행행 또는 재사용 위치 |
|---|---|
| N01 케이스, N02 외부 메시 | 기존 구현을 D38에서 재사용·회귀 확인합니다. 새 개발로 재집계하지 않습니다. |
| N03–N10 8제품 방식 | D31에서 제품별 T1–T6 비교·차이 수정을 진행합니다. |
| N11 대표 연속 과업 | D38의 정확한 C1/C2·규모·CAD/메시 조건으로 확인합니다. |
| N12 Windows, N13 새 설치, N14 양앱 조합 | D33/D34/D36. 기존 보류와 정확한 실환경 판정을 유지합니다. |
| N15 곡선, N16 원자 명령 | 기존 구현 재사용. D04/D07/D19/D23에서 새로운 소비와 회귀를 확인합니다. |
| N17 대용량·성능 | D03 첫 개선과 D24 전체 예산을 구분합니다. |
| N18 메시 편집 | D23 기존 6종 종단 확인, D25 실제 후속 연산 구현입니다. |
| N19 owner IPC, N20 실제 MCP client, N21/W3 실행 | 기존 구현 재사용. D18/D19/D11의 새 소비를 따로 인수합니다. |
| N22/N23/N24/N25/N26 | D26/D27/D28/D29/D30. 성공한 제한 family와 남은 F0–F4를 구별합니다. |
| Phase3.6 Auto-Tet, Phase3.7 형상 정리 | D39. 미달·미병합·조건부 채택은 완료가 아닙니다. |
| WB P1–P7·GUI WBS | D01/02/06/18/20/21/32–36의 기존 요구와 신규 확장을 함께 대조합니다. 기존 완료 이력은 CURRENT_STATE에 유지합니다. |
| 새 연구 12개 묶음 | 아래 기술 계약표와 JSON의 tasks는 분야 묶음입니다. 위 실행행을 합쳐 별도 제품 수·진행률로 만들지 않습니다. |

실제 현황은 두 저장소 CURRENT_STATE의 해당 원항목에서 갱신합니다. `implementation-map.json` v3의 `execution_queue`는 위 실행 순서이고 `tasks`는 원래 12개 연구 분야 계약입니다. 각각 다른 제품 진도표가 아닙니다. 원자료/플랫폼/모델별 미확인은 실제 구현 담당자가 해당 근거로 닫아야 합니다.

---

## 재료 연구 분야의 12개 세부 계약

이 12개 항목은 [기존 전체 계획](../../review/2026-10-23-codex-improvement-plan.md#plan-integration) 아래의 세부 작업입니다. 새 전체 제품 12개가 아닙니다. 현재 모두 제품 구현은 `planned`(예정)이며, 명세 검토와 참조 계산의 통과를 구현 완료로 세지 않습니다.

정확한 목록은 [JSON v3](implementation-map.json)에 있습니다. v3에는 전체 실행 순서와 원래 12개 분야 계약을 구분해 담았습니다. 실제 구현 상태는 [Workbench CURRENT_STATE](../../../CURRENT_STATE.md)와 [Prep N01–N26](https://github.com/pikachu444/caelab-prep/blob/main/CURRENT_STATE.md#remaining-work)에서 관리합니다. 서로 다른 목록의 행을 합해 진행률을 계산하지 않습니다.

## 기존 계획과 무엇이 이어지는가

WB P1–P7과 Prep P0–P9/G0–G4는 다른 번호입니다. Prep 제품 요구 R1–R4와 재사용 순서 R0–R5도 구별합니다. 아래 부모 항목은 재사용·확장의 위치이며 그 항목 전체를 완료 처리한다는 뜻이 아닙니다.

| 작업 | 기존 WB 계획 | Prep 접점 | 새로 만들 결과 |
|---|---|---|---|
| `CORE01` | P1·P3·P4; AC-3/4의 단위·축 계약 | Prep P1; G1–G3의 단위·참조 | 공통 자료·결과·근거 참조, 재료 측도·온도·질량 변환 |
| `CORE02` | P1·P4; 기존 MDO 확장 | Prep G2/T3·N15의 곡선 소비 | 여러 시험·실제 평가·잔차·보류 검증·취소·캐시·최종 재계산 |
| `STORE01` | P3·P6; AC-1 저장·재열기 | Prep 문서 단일 소유; G4 파일 관계 | 재료 연구 저장·프로젝트 v4 역할·전체 참조 복사·읽기 전용·복구 |
| `MAT-A` | P3·P4; 기존 MDO/솔버 확장 | Prep G1/T2·G2/T3·G3/T6 | 정확한 A 6개 프로필의 계산·동정·독립 검증과 모델별 지원 판정 |
| `MAT-B` | P4; 기존 MDO 확장 | Prep G1/T2·G2/T3·G3/T6 | B 5개 프로필의 계산·제약·이력·독립 검증 |
| `UI01` | P3·P6·P7; GUI 환경 기준 | Prep P2/P4·N15; 공용 위젯 연결 | 실제 CSV·모델·계수·계산/동정·잔차·보류 시험·저장/재열기 화면 |
| `PREP01` | P6; AC-1 변경·stale 접점 | Prep P6a/b·N16; P8c·N19/20; G1/T2·G2/T3·G3/T6 | 지원 모델의 카드·곡선·축 미리보기/원자 적용·응답 유실 처리 |
| `LINK01` | P3·P6; AC-1 일부 | Prep G4·W1/W2; 재사용 R2/R3/R5 | 독립 연구 역할·상대 참조·프로젝트 v4·이동/SaveAs |
| `E0-2` | P5; WBS-4·이슈 #45의 공유 창 | Prep P8c/N20 읽기 경계 | 결과 없는 이론 질문·원문/판본·문헌 준비/색인·관련성·초안 보존 |
| `E3-5` | P5; WBS-4·이슈 #45 요청 수명 | Prep 지원 문서 읽기 전용 조회 | 실제 AI 도구 왕복·모든 주장 검토·수정/재심·요청 구별 |
| `POST01-04` | P4·P5; WBS-3·AC-3/4 수치 접점 | Prep G3 출력; N21/W3 불변 실행 | OpenCourant 변환·시간 이력·ID/TIME/층/축/단위·수치 조회 |
| `POST05-06` | P5·P7; 외부 후처리의 RP 확장 | Prep 원문서/덱/INCLUDE 소유; 재사용 R5 | 수동 열기 안내와 선택 파생판의 정확한 자료 전달·표시 응답 |

## 재사용할 구현과 추가 개발

| 작업 | 현재 구현에서 사용할 부분 | 추가 구현 |
|---|---|---|
| `CORE01` | 기존 단위 도구, 실행/파일 해시와 원자료 보존 | 공통 자료·결과·근거 참조, 재료 측도·온도·질량 변환 |
| `CORE02` | mdo/optimize.py의 optimize·BatchEvaluator, 변수·제약·StudyManager와 기존 JobManager/RunStore | 여러 시험·실제 평가·잔차·보류 검증·취소·캐시·최종 재계산 |
| `STORE01` | atomic_json·FileLock·프로젝트 v1–3·RunStore | 재료 연구 저장·프로젝트 v4 역할·전체 참조 복사·읽기 전용·복구 |
| `MAT-A` | 공개 구성식과 원덱 실행·기존 수치 최적화 | 정확한 A 6개 프로필의 계산·동정·독립 검증과 모델별 지원 판정 |
| `MAT-B` | 기존 수치 최적화·pyvisco/NEML 등 실제 계산 라이브러리 | B 5개 프로필의 계산·제약·이력·독립 검증 |
| `UI01` | Qt 동작 패턴·NumericEdit·CurveTable·CurvePlot·PropertyEditor | 실제 CSV·모델·계수·계산/동정·잔차·보류 시험·저장/재열기 화면 |
| `PREP01` | 기존 LiveClient·형식 지정 명령·execute_many·Undo·코덱 | 지원 모델의 카드·곡선·축 미리보기/원자 적용·응답 유실 처리 |
| `LINK01` | program_catalog·Workbench 명령·프로젝트 노드·공용 위젯 호스트 | 독립 연구 역할·상대 참조·프로젝트 v4·이동/SaveAs |
| `E0-2` | 독립 ExpertWindow·대화/CAS·첨부·기존 검색 | 결과 없는 이론 질문·원문/판본·문헌 준비/색인·관련성·초안 보존 |
| `E3-5` | 기존 공급자·취소·명시 질문·대화 저장 | 실제 AI 도구 왕복·모든 주장 검토·수정/재심·요청 구별 |
| `POST01-04` | JobManager/RunStore·FRD/VTU/PVD·프레임·원해시 | OpenCourant 변환·시간 이력·ID/TIME/층/축/단위·수치 조회 |
| `POST05-06` | 원 RP 수동 열기·기존 프로그램 실행 경계 | 수동 열기 안내와 선택 파생판의 정확한 자료 전달·표시 응답 |

기존 `MDOStudyService.optimize`는 대리모델·INP 전용 흐름입니다. 재료의 실제 계산에는 `mdo/optimize.py`의 `optimize`와 `BatchEvaluator`를 새 `CalibrationStudyService`에서 조합하고 `ACTUAL` 관측과 대리모델 예측을 구분합니다. 현재 MDO 저장본을 새 재료 연구로 자동 변환하지 않습니다.

## 개발 순서와 끝났다고 판단할 조건

편집 전 합의는 공통 형식과 API를 맞추는 일입니다. 코드 연결 선행은 실제 호출할 코드가 필요하다는 뜻입니다. 최종 확인은 사용자의 작업을 끝까지 수행하는 검사입니다. 세 조건을 하나의 완료 대기로 합치지 않습니다.

| 작업 | 편집 전 합의 | 실제 코드 연결 선행 | 최종 확인 |
|---|---|---|---|
| `CORE01` | 공통 책임·형식 합의 | 기존 기반 | C1/C3/C6의 엄격한 직렬화·단위·소유자·누락 거절 |
| `CORE02` | `CORE01` | `CORE01` | 실제 등록 어댑터 최소 1개와 독립 기준으로 공통 기능 확인; MDOStudyService의 INP·SURROGATE 전용 경로를 직접 평가기로 사용하지 않음 |
| `STORE01` | `CORE01`, `CORE02` | `CORE02` | v1–4 읽기·이동·SaveAs·잠금·실패 복구·선택 결과 보존 |
| `MAT-A` | `CORE01`, `CORE02` | `CORE02` | A 6개 프로필 각각의 계산·최적화·플랫폼 조건; 실제 솔버 유한요소 계산 분기는 실제 원결과 판독과 별도 FE 재평가 |
| `MAT-B` | `CORE01`, `CORE02` | `CORE02` | B 5개 프로필 각각의 실제 계산·동정·독립 기준; NEML·플랫폼 미확인을 성공으로 바꾸지 않음 |
| `UI01` | `CORE01`, `CORE02`, `STORE01` | `CORE02`, `STORE01` | 실제 계산 어댑터 최소 1개로 C9의 전체 사용자 작업; 표시하는 각 프로필의 실제 지원/미지원 상태; Windows C9; 공통 창 틀의 동작 확인과 모든 모델 지원을 구별 |
| `PREP01` | `CORE01`, `CORE02`, `STORE01` | `CORE01`, `CORE02` | LAW1 E 첫 연결과 여러 시험의 동정값 적용을 별도 판정; 지원 모델의 전체 계수·곡선·축·단위→prepare/commit→Undo/Redo→저장/재열기/native 의미 보존; STORE01·UI01·LINK01과 합동 확인; Apply 미지원과 내보내기 구현 미지원을 구별 |
| `LINK01` | `CORE01`, `STORE01`, `PREP01` | `STORE01`, `UI01` | 독립 진입점과 v1–4 재열기·선택 평가 보존; Apply 연결은 PREP01과 함께 확인; 전체 연결은 자문·심의·결과·RP의 같은 자료로 합동 확인 |
| `E0-2` | `CORE01` | `CORE01` | 이론 질문·원문/판본·관련성 평가; 이슈 #45의 검증 중 표시·첨부/초안 변경·닫힌 창 지연 응답 잔여를 동일 산출물로 승계 |
| `E3-5` | `CORE01`, `E0-2` | `E0-2` | 실제 공급자의 도구 요청/반환과 독립 요청의 답변 심의; #45 실제 공급자 취소·지연 응답과 Windows 흐름; 모의 형식 검사·실제 도구 왕복·공학 답변 품질·Windows를 각각 확인 |
| `POST01-04` | `CORE01` | `CORE01` | 실제 변환기와 솔버 원결과·독립 수치 기준; 취소·부분/낡음·이동/SaveAs; 기존 N21 실행 완료와 구별 |
| `POST05-06` | `CORE01`, `POST01-04` | `POST01-04` | 원 RP 수동 실제 표시와 선택 수정판 자동 전달/실제 표시를 별도 인수; 원본 불변·중복 솔버 재실행 차단·Windows; 자동 연결을 지원한다고 표시할 때만 선택 수정판 인수 필수 |

조건부 선행도 확인해야 합니다. MAT-A의 실제 솔버 유한요소 계산 분기만 해당 POST 판독기를 사용하며 해석식·라이브러리는 이를 기다리지 않습니다. UI01은 검증한 A/B 실제 프로필 1개 이상으로 연결을 확인하지만 A/B 전체 11개 프로필의 지원을 대신하지 않습니다. PREP01은 STORE01의 저장된 평가의 식별정보와 저장 규칙을 먼저 합의하고 단일값 IPC 개발부터 병행합니다. 최종 적용은 저장·화면·프로젝트와 함께 확인합니다. E3-5 이론/문헌은 POST 불필요, native_run 질문은 해당 판독기, calibration 질문은 CORE02 고정 평가가 필요합니다. LINK01의 Prep 적용 부분에만 PREP01 코드가 필요합니다.

E3-5는 사용자가 직접 고른 파일의 질문도 확인합니다. 문헌·실행 결과·재료 평가와 구별한 직접 파일 근거로 읽고 검토하며, 없는 실행 ID를 만들어 붙이지 않습니다.

## 누가 무엇을 편집하는가

| 작업 | 필요한 역할 | 실제 구현자 | 독립 QA |
|---|---|---|---|
| `CORE01` | 공통 계약·서비스 담당 | 미배정 | 미배정 |
| `CORE02` | 공통 계약·서비스 담당 | 미배정 | 미배정 |
| `STORE01` | 공통 계약·서비스 담당 | 미배정 | 미배정 |
| `MAT-A` | 재료 어댑터 담당 | 미배정 | 미배정 |
| `MAT-B` | 재료 어댑터 담당 | 미배정 | 미배정 |
| `UI01` | 연구창·프로젝트 연결 담당 | 미배정 | 미배정 |
| `PREP01` | Prep 카드·문서 담당 | 미배정 | 미배정 |
| `LINK01` | 연구창·프로젝트 연결 담당 | 미배정 | 미배정 |
| `E0-2` | 전문가·검색 담당 | 미배정 | 미배정 |
| `E3-5` | 전문가·검색 담당 | 미배정 | 미배정 |
| `POST01-04` | 후처리·RP 담당 | 미배정 | 미배정 |
| `POST05-06` | 후처리·RP 담당 | 미배정 | 미배정 |

실제 구현 담당·검토자는 아직 미배정입니다. 과거 WBS의 사람 이름이나 이번 문서 작성자를 미래 구현 담당으로 간주하지 않습니다. 착수 담당은 기존 미게시 작업을 확인한 뒤 기준 SHA·branch/worktree·허용 파일·검사와 다음 통합 순서를 수락해야 합니다. 공유 파일은 한 명이 편집합니다. 특히 CORE02/STORE01의 `materials/service.py`, E0-2/E3-5/#45의 `ExpertWindow`·assist, Prep의 live 분배기·카드 코덱은 중복 편집하지 않습니다.

## 일정과 진행 보고

공통 계약 합의 → 공통 기능·분야별 어댑터/문헌/후처리 병렬 개발 → 실제 모델과 화면/Prep 연결 → 프로젝트·자문·심의·RP 합동 확인 → 지원 범위별 실제 환경 인수 순서입니다. 정확한 분기와 병렬 조건은 기존 전체 계획에 있습니다.

달력 날짜·기간은 실제 인력과 작업량을 수락한 뒤 정합니다. 현재 `planned_start`/`planned_finish`는 `null`입니다. 별도 간트 원본은 확보하지 못했으므로 기존 달력·G/M/Q/S 번호를 이행했다고 표시하지 않습니다. Windows 후속 때문에 독립 가능한 개발을 멈추지 않되 Windows 합격으로 대신 보고하지 않습니다.

다음 작업은 새 표만 보고 고르지 않습니다. 기존 CURRENT_STATE·진행 PR·파일 점유를 먼저 확인하고 같은 경로의 잘못된 성공/자료 손실/회귀를 우선 수정합니다. 그 뒤 실제 코드 선행이 준비된 기존 작업과 새 작업을 함께 배정합니다. 무관한 메셔·원제품·Windows 대기는 독립 개발의 일괄 차단 사유가 아닙니다.

## 구현자가 작업을 인수할 때

1. AGENTS·DESIGN·CURRENT_STATE와 기존 계획의 부모 항목을 읽고 현재 main/dirty·실제 사용 가능한 코드를 대조합니다.
2. 이 행의 기존 재사용분·추가분·계약 합의·코드 선행·최종 확인을 그대로 인수합니다. 공통 계약 변경은 소비자와 먼저 토의합니다.
3. 상세 수식·공개 함수·입출력·저장·실패/취소·수치 기준은 연결된 공통 및 분야 명세를 구현합니다. 모델명·공급자명·프로그램 등록만으로 지원을 표시하지 않습니다.
4. 같은 결과물을 원 지적자와 독립 QA가 확인한 뒤 코드·실제 사용자 동작·모델별 지원·Windows 상태와 부모 항목에 미친 영향을 갱신합니다.

첫 모델의 사용 과정만 끝내고 전체 완료로 보고하지 않습니다. A의 6개·B의 5개 정확 프로필은 각각 판정하며 계산·동정·FE·native 내보내기·Apply의 미지원도 표시합니다. 새 12개 전체 연결과 기존 두 제품의 원래 잔여/납품은 별도입니다. 합동 인수는 기존 계획의 끝까지 확인할 범위를 따릅니다.

GEOS 구성식 드라이버의 정확한 등록·XML 고정 사본·실행·텍스트 수집은 재료 A 명세의 `geos_driver` 프로세스 연결 계약을 따릅니다. 현재 등록돼 있다는 뜻이 아닙니다. 탐색 중 보류 시험의 실행/노출 금지와 최종 선택 고정은 공통 C4 보완, 역할별 실제 읽기 권한·수치 인용·질문 관련성의 추가 인수는 전문가 명세를 따릅니다.
