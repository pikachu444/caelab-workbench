# 현재 상태

문서 기준은 main `3c18ea100c6ab301e90f836d209cffcfda1af338`와 아래 CAD GUI 통합 후보 `f5652c7984c39143e1c9a735ff2743e23e07d4a3`입니다. CAD 후보의 문서 합성 시점에는 새 최종 QA·CI·병합이 미완료입니다. 이 문서는 구현 상태와 확인 범위를 소유합니다. 제품 기준은 [DESIGN](DESIGN.md), 계약은 [ARCHITECTURE](ARCHITECTURE.md), 사용 절차는 [시작 가이드](docs/tutorials/workbench_quickstart.md)를 따릅니다. 날짜별 실행·실패·정정·복구 위치는 [구현 이력](docs/history/implementation-history.md)에서 고정 원문으로 찾습니다.

## 구현 범위

### 상용 CAE 대조 통합 GUI 상세 명세서 (8차 개정 정본)

상용 CAE 도구(Altair HyperMesh, Dassault Abaqus/CAE, PrePoMax)의 공식 매뉴얼(PrePoMax 2.0.0 p.14/18/49, HyperMesh 2025.1 등) 및 Prep DESIGN R2 필수 8제품 벤치마크에 대조한 통합 GUI 상세 명세서 [docs/gui/commercial-cae-gui-spec.md](docs/gui/commercial-cae-gui-spec.md) 8차 개정을 완료했습니다.
- 전처리기 소유권 및 호출 계약: OpenRadioss는 Prep 소유(`.caeprep` $\rightarrow$ `_0000.rad`), CalculiX는 PrePoMax/FreeCAD FEM 소유(`.pmx` $\rightarrow$ `.inp`). 호출은 `Workbench.open_program(project, node_id, use_input=False)`([workbench.py L509+](caelab/workbench.py))을 사용하며, 등록 ID(`caelab_prep`, `prepomax`), `node.files["document"]` 우선 바인딩, `_file_status` stat 캐시 및 변경 감지 시점, 명시적 새로고침(`project.refresh()`), 파일 mtime/해시 감지 기반 하류 결과 `STALE` 전파, 저장 취소 시 0 run 보존, 무실행 재열기 라이프사이클 및 저장 실패(기존 보존) vs 노드/해석 실패(`failed` 전이) 복구 계약 규정.
- 취소 정리 계약: `activity is not None and not activity.active and activity.cleanup_error == ""`를 검증하여 비정상 종료 시 보존 상태 및 오류 안내 규정.
- 8제품 자료 목록 및 일부 대응: Prep 8개 presentation adapter 및 native 명령 연결 코드 실체([reference_components.py L393](https://github.com/pikachu444/caelab-prep/blob/f18739cb5ff8e9b52c5a81e6ccd901d6aff82b3f/src/caelab_prep/ui/reference_components.py#L393), [reference_workspace.py L34](https://github.com/pikachu444/caelab-prep/blob/f18739cb5ff8e9b52c5a81e6ccd901d6aff82b3f/src/caelab_prep/ui/reference_workspace.py#L34))와 공개 원본 관측 한계/locator 미확인 및 BeidPrep 원제품 관측(우리 구현 PR40과 별개)을 분리 대조하고, 원제품 동일 과업 비교 미완료(과업 비교 예정) 상태를 명시.
- 품질 지표 및 3D 솔리드: TRI3 skew는 대변 중점 방향 벡터와 인접 변 중점 연결선 사잇각($|90^\circ - \theta_{\min}|$, 정삼각형 $0^\circ$), QUAD4 skew는 대변 중점 연결선 사잇각($|90^\circ - \phi| \le 60^\circ$), Warpage는 CAELab 제안 정책(두 분할 중 최대 이면각 $\le 15^\circ$), Jacobian은 가우스 적분점(TRI3 1점, QUAD4 4점) 행렬식 비율($\ge 0.6$) 규정. 직선 Tet10은 8-ULP 및 실제 10절점 양의 야코비안 인증(`certify_straight_tet10`)을 명시하고, 곡면 Tet10 및 고급 스윕을 신규 제안 범위로 분리.
- 3D 가시화 및 솔리드 자유도 엄밀화: C3D10 솔리드 병진 1~3자유도 엄밀 한정(회전 자유도 배제), Von Mises 응력의 적분점/절점 외삽 메타데이터 분리, 미확인 시 `[위치 미확인]` 경고, 선언된 단위계 기반 유도 단위(MPa/Pa) 표기, 결측치 `NaN` 회색 마스킹, 실변형 1배($S=1.0$)와 자동 맞춤 배율($S=S_{\text{auto}}$) 수식 분리, 과거 run의 메시/단위/프레임 보존 원칙 명시.
- Windows 환경 및 IME: Qt 6 공식 High-DPI 가이드, 시스템 기본 폰트(Segoe UI/맑은 고딕), `collections.py` 계승 `QInputMethodEvent` 조합 텍스트 유실 방지 규격 및 1440x900/1024x768/High-DPI 통과 기준 명시 (검증 계획 제안, 담당/시점 미정, P7 단계 연계). Prep 기존 1920x1080/100%, 3840x2160/200%, 960x640 기준 및 Prep CURRENT_STATE 잔여(N03–N10/N12) 연계 명시.
- WBS 및 정본 계획(P1~P7) 연계: 정본 계획 `docs/review/2026-10-23-codex-improvement-plan.md` §4 및 Muse(Kavi)의 제안 매핑을 수용하여 WBS-1(P2 문서 정합성), WBS-2(P1+P4 Phase 4-B Step 2 구현 후보, 선행 조건으로 원 지적자 재검토 통과 명시), WBS-3(P4 기존 FRD/VTU 경로 재사용+P5 가시화 표시), WBS-4(Issue #45 P5 독립 프로그램 기여 한정), WBS-5(Phase 3.7 제안)로 매핑하고 확정 근거 없는 회신·검수 담당/시점 미정 명시. DESIGN G1 대표 과업을 AC-5로 정식 편입.

### Gmsh MSH 4.1 straight Tet10 → CalculiX C3D10 변환 (Phase 4-B 1·2단계)

독립 어댑터 `caelab/adapters/msh_c3d10.py`와 검증 테스트 `tests/test_msh_c3d10.py`, `tests/test_calculix_c3d10_deck.py`를 구현 및 독립 검토 피드백(S1/S2/R1~R5)과 R3 생산자 기하 계약 일치, CalculiX 마스터 덱 조립·솔버 연동을 완료했습니다.
- 입력: Gmsh MSH 4.1 ASCII 파일 및 `caelab.standalone-msh` v1 sidecar JSON (`length_unit=mm`, `element_profile=straight_tet10`).
- 헤더 및 무결성(R5, R2): `$MeshFormat`의 `4.1 0 8`(ASCII, data-size 8) 엄격 검증. 단일 메모리 버퍼(`read_bytes()`)에서 SHA256 대조와 파싱을 함께 수행해 TOCTOU 간극 배제.
- 절점 및 요소 검증(R4): 블록 내·외 절점 중복 태그 감지, 유효 양의 정수 비연속 ID 보존, Tri6 경계 요소의 양수 ID/중복/미정의 절점 참조 및 Tet10 ↔ Tri6 요소 태그 전역 충돌 검증.
- 직선 프로파일 형상 검증(R3): 생산자(`caelab-prep`)의 엄격한 기하 계약과 일치하도록 임의 상대 편차/체적 하한을 배제하고, 정확한 8-ULP 중간점 잔차 예산(`math.ulp` 기반) 및 코너 사면체 섭동 행렬 놈(matrix-norm perturbation bound) 기반 전역 야코비안 양성 인증(`certify_straight_tet10`)을 직접 수행하여 비직선·음수/미인증 요소를 수학적으로 엄격 거부.
- 노드 순서 변환: raw Gmsh type 11 `(12, 23, 31, 14, 34, 24)` midside 노드를 CalculiX C3D10 `(12, 23, 31, 14, 24, 34)`로 슬롯 9와 10을 단 1회 스왑.
- 무손실 좌표 보존 및 원본 보호(S2, R1): MSH 원본 좌표 문자열을 무손실(lossless) 보존 출력하여 IEEE-754 재파싱 오차 0 보장. 출력 경로가 원본 MSH 또는 메타데이터 JSON과 동일하거나 별칭(alias)인 경우 쓰기 전 즉시 거부(`MshC3D10Error`)하여 원본 불변성 보장.
- Tri6 경계 절점 추출(`extract_boundary_nodes`): `mesh.tri6_boundary`에 등록된 2차 삼각형 경계 어노테이션에서 고유 절점 추출 및 기하 조건자(`predicate(x, y, z)`) 필터링 지원.
- 마스터 덱 조립(`assemble_c3d10_master_deck`): C3D10 include 덱(`*INCLUDE, INPUT=<path>`)을 참조하는 완성형 CalculiX `.inp` 마스터 덱 문자열 생성. `*HEADING`, `*NSET`(한 줄당 최대 16개 ID 줄바꿈 규격 준수), `*MATERIAL`, `*ELASTIC`, `*DENSITY`, `*SOLID SECTION`(ELSET=EL_C3D10), `*STEP`, `*STATIC`, `*BOUNDARY`(구속 절점 DOFs 1~3), `*CLOAD`(하중 절점 총 하중 분배), `*NODE FILE`(U, RF), `*EL FILE`(S), `*NODE PRINT`(선택적 NSET RF 반력합 출력) 카드를 표준 Abaqus/CalculiX 문법으로 완결.
- 구속·하중 절점 배타성 검증: 구속 절점 집합과 하중 절점 집합의 교집합(충돌) 발생 시 하중-반력 왜곡 방지를 위해 즉시 거부(`MshC3D10Error`)하는 fail-closed 보호.
- StructuralModel 변환(`build_c3d10_structural_model`): `ConvertedMesh`를 Workbench 표준 스키마 문서(`caelab.model.schema.StructuralModel`)로 직접 변환하여 `validate_model` 및 `CalculiXConverter().validate` 적합성 검증 통과.
- 단위계 보존: `unit_system`에 `mm` 및 `mm-t-s` 지정 시 `CalculiXConverter` 및 `ResultSet.units`에 `{"length": "mm", "force": "N"}` 보존 및 `summarize_result`에서 `max_displacement_mm` / `max_displacement_m` 자동 산출.
- 실물 검증 및 환경 격리: 외부 검증 자산은 `CAELAB_FIXED_L_ASSET_DIR` 환경변수 또는 표준 로컬 캐시를 참조하며 자산 부재 시 CI에서는 skip으로 격리. Prep 생성 고정 L 실물 자산(`045acf69...` / `52fc715c...`, 87개 절점, 36개 Tet10 요소, 28개 Tri6 경계) 로컬 검증에서 include 덱 생성, 바닥면(z=0) 구속 21절점, 상단면(z=1.0) 하중 21절점을 분리 추출하여 실제 CalculiX 2.21 솔버 계산(DAT 반력합 $F_z=100.0\text{ N}$ 및 FRD $99.999918\text{ N}$ 대조), 결과 요약(`summarize_result`), 어댑터 검사 17건 및 덱/실행 검사 9건(총 26건) 전건 통과 확인. 솔버 부재 환경에서는 예외 없이 안전한 진단 메시지와 함께 실패(`success=False`)로 격리.
- 계약 강화 및 검토 의견 반영(P2 6건 R76-1~R76-6):
  1. R76-1 (단위계 오표기 방지): `length_unit='mm'`인 MSH에 대해 좌표 환산 없이 `unit_system='si'`로 라벨링하는 요청을 엄격 차단(`MshC3D10Error`).
  2. R76-2 (하중·물성·노드 무결성): `load_vector` 성분 개수(정확히 3개: fx, fy, fz) 엄격 검증. 비영 하중 벡터 지정 시 `loaded_nodes`가 비어있으면 하중 유실 방지를 위해 즉시 거부. 유한 수치($E > 0$, $-1 < \nu < 0.5$, $\rho > 0$) 및 메시에 존재하지 않는 노드 참조 거부.
  3. R76-3 (모델-덱 물리조건 전체 대응 및 엄격한 부동소수점 일치): 지원하는 단일 정적 조립 범위에서 솔버 실행 전 덱의 `FIXED_NODES` 및 `LOAD_NODES` 절점 집합 일치뿐 아니라, 모델 내 모든 BC/하중 성분의 유한성 사전 검증 및 상충 중복 정의 엄격 차단(동일 유한값만 수용), 그리고 `(절점, DOF) -> 구속값` 및 `(절점, DOF) -> 하중값`의 전단사 대응을 임의의 공학 허용오차 없이 엄격한 부동소수점 일치(float equality)로 대조 검증. 절점별 하중 재분배, 대상 절점 오류, 추가 하중 성분/방향, 미세한 지정변위 변경($5\times 10^{-12}$, $10^{-8}$ 포함), 미세한 하중 변경($-50.000000000005$ 포함), 중복 BC 상충 정의($1.0$ vs $1.0000000005$), 미정의 추가 구속, 모델/덱 내 비유한 수치(NaN/Inf) 및 집계 오버플로를 fail-closed 거부(`MshC3D10Error`).
  4. R76-4 (작업 디렉토리 격리): `CalculiXConverter.run` 및 `run_c3d10_simulation`에서 `deck` 파일이 `workdir` 직하에 위치하도록 강제하여 cwd/basename 충돌 및 임의 파일 덮어쓰기 방지.
  5. R76-5 (실행 이력 단위 일관성): `JobManager.submit_model`에서 `mm` 및 `mm-t-s` 모델 제출 시 `NativeRun.units={"length": "mm", "force": "N"}` 및 `unit_source="StructuralModel.unit_system"` 기록.
  6. R76-6 (검사 격리 및 자원 정리 검증): `tests/test_calculix_c3d10_deck.py`의 솔버 실행 검사에서 `monkeypatch.setenv`로 `CAELAB_CCX` 환경변수 누출을 차단하고, 순수 메타데이터 검사는 워커 실행을 격리한 후 `JobManager.close() is True`로 스레드 종료 및 lease 해제를 확인. 전용 회귀 검사 7건 추가 (어댑터 17건 + 덱/솔버/단위 17건 = 총 34건 통과).
- 범위 한정: Phase 4-B는 단일 컴포넌트 C3D10 선형 정해석 조립·계산·요약에 한정하며 다중 컴포넌트 어셈블리/접촉 해석은 미포함.

### Workbench 3D 응력 컨투어 뷰어 및 변형 배율 제어 (Phase 2 WBS-3, AC-3, AC-4)

통합 GUI 상세 명세서 [docs/gui/commercial-cae-gui-spec.md](docs/gui/commercial-cae-gui-spec.md) §8, AC-3, AC-4에 따라 `apps/workbench_qt/viewport.py`, `apps/workbench_qt/main_window.py`, `caelab/solvers/base.py`, `caelab/solvers/calculix.py`, `caelab/model_execution.py`에 3D 응력 컨투어 가시화 및 변형 배율 제어 기능을 구현하고 검증 테스트 `tests/test_viewport_contour.py` (10건)를 추가했습니다.
- Von Mises 등가 응력 계산 (`compute_von_mises`): 6성분 응력($S_{xx}, S_{yy}, S_{zz}, S_{xy}, S_{yz}, S_{zx}$)으로부터 불변량 등가 응력 수식을 엄밀 계산. 일축 인장, 순수 전단, 정수압 응력 검증 통과.
- 결측치 및 비유한치 마스킹 (AC-3): 계산 대상 노드의 응력 데이터 누락 또는 `NaN`/`Inf` 포함 시 `np.nan`으로 마스킹하고, VTK `lut.SetNanColor(0.55, 0.55, 0.55)`를 적용하여 회색 중립색으로 렌더링.
- 단위계 기반 유도 단위 계약 (`deduce_stress_unit`, AC-3): 결과 단위계가 `length: mm`, `force: N`인 경우 `MPa`, `length: m`, `force: N`인 경우 `Pa`로 유도 표기. 단위 미선언 또는 부분 선언 시 응력 표시를 보류하고 `[단위 미선언: 파생 응력 보류]` 경고를 범례에 표시.
- 외삽 출처 및 신뢰도 고지 (AC-3): CalculiX FRD `STRESS` 판독 결과를 절점 외삽값으로 명시하고 범례/스칼라바에 `[절점 외삽]` 라벨 표시 (CalculiX 2.21 공식 매뉴얼 §6.13, §7.51 부합).
- 실변형 1배 및 자동 맞춤 배율 분리 (AC-4): $\mathbf{x}_{\text{display}} = \mathbf{x}_{\text{orig}} + S \cdot \mathbf{u}$ 수식에서 `real` 모드($S=1.0 \times \text{factor}$)와 `auto` 모드($S=S_{\text{auto}} \times \text{factor}$, 모델 바운딩 박스 최장축의 25% 기준 맞춤)를 분리하고 뷰포트 툴바 콤보박스 연동.
- 필드 전환 콤보박스: `변위 (크기)`, `변위 X`, `변위 Y`, `변위 Z`, `Von Mises 응력` 간 실시간 전환 및 스칼라바/범례 텍스트 동기화.
- 피어 리뷰(Codex) 지적 반영 및 결함 보완 (PR80-WB1~WB4):
  1. PR80-WB1 (저장 결과 응력 키 정수 복원): `caelab/model_execution.py` 역직렬화 시 `stresses` 딕셔너리의 절점 ID를 정수(`int`)로 복원하도록 보완하여, 과거 실행 재열기 및 저장 후 표시 시 문자열 키로 인한 `NaN` 결측 방지 (`test_model_execution_stresses_restoration_roundtrip`).
  2. PR80-WB2 (변형 벡터 명시 선택): `viewport.py`에서 `grid.GetPointData().SetActiveVectors("U")` 및 `warp.SetInputArrayToProcess(...)`를 명시하여 `vtkWarpVector`가 변위 벡터 `U`를 입력으로 정확히 참조함을 보장하고, 실제 warp 출력 좌표 $\mathbf{x}_{\text{orig}} + S \cdot \mathbf{u}$ 일치 검증 (`test_warp_vector_active_vectors_and_actual_coordinates`).
  3. PR80-WB3 (SI/mm 길이 단위 일관성, 스칼라-범례 정합 및 미확인 단위 경계 방어): `deduce_length_unit`을 도입하여 모델 메시 좌표계(`model.unit_system`: exact `si`, `mm`, `mm-t-s`)와 결과 변위 길이 단위(`result.units["length"]`: exact `m`, `mm`)를 엄밀 대조(임의 대소문자/별칭 승격 차단). SI($\text{m}$)와 $\text{mm}$ 간 warp 좌표계 일관성뿐 아니라 VTK 스칼라 배열값, 스칼라바 제목, MainWindow Qt 범례 라벨 간 1000배 불일치를 완전 해소. 미선언/임의 단위 입력 시 명시 scale 전달 여부와 무관하게 실제 배율 변형을 억제($S=0.0$)하고, Von Mises 응력 필드 선택 시에도 `[절점 외삽 · 변형 단위 미선언: 실제 변형 억제]` 상태를 두 범례에 보존 (`test_deduce_length_unit_contract`, `test_unit_consistency_scalars_and_legends_si`, `test_unit_consistency_scalars_and_legends_cross_mm_and_m`, `test_undeclared_units_suppresses_real_scale_and_indicates_status`).
  4. PR80-WB4 (본선 연결 범위 구분): 보조 `MainWindow`의 3D 컨투어/배율 제어 구현과 기본 `ProjectWindow` $\rightarrow$ `NativeAnalysisWindow`의 native 선택 run(표/로그 및 ParaView VTU 연동) 경로를 명확히 분리 기록.
  5. PR80-P2 (TypeError 은폐 제거 및 런타임 호환): `MainWindow._load_run_result`에서 `show_results` 내부 TypeError를 기본 표시로 fallback 은폐하던 경로를 제거하여 실제 예외를 드러내도록 교정하고, 테스트 픽스처 인자 계약 일치. 아울러 리눅스/CI 환경에서 `vtkOpenGLRenderer`의 `AddActor2D` 부재 결함을 `AddActor` 표준 호출로 교정하고, 부분 변위 시 `_last_result=None` 초기화를 보완하여 전체 회귀 검사 113 passed, 1 skipped 통과.

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
| 선택 geometry의 CAD·편집 문서 연결 | 모듈과 실제 `ProjectWindow` 소비자를 결합한 소스 후보를 인수했습니다. FreeCAD/SolidCraft/CADCraft 선택, `document` 등록·문서 우선 열기·누락 문서 재지정과 기존 FreeCAD input-only 경로를 연결합니다. CADCraft의 교환 출력은 DXF/SVG로 구분합니다. [사용·소유 경계](docs/tutorials/cad-file-handoff.md#cad-연결-편집-모듈과-소비자-경계)와 [후보의 검사 범위](#cad-gui-integration-candidate)를 따르며, 최종 원 QA·필수 CI·병합 및 실제 CAD/Windows 검수를 완료로 표시하지 않습니다. |
| 전문가 오류 안내 분류 | [PR65](https://github.com/pikachu444/caelab-workbench/pull/65)에서 `user_error()`의 개정 충돌·문서 잠금·무결성·파일 누락을 예외 타입 및 정밀 어휘로 우선 분류하여 '없' 오분류를 교정했습니다 (#48). main에 병합 완료되었습니다. |
| 설치 매니페스트 예외 처리 | [PR64](https://github.com/pikachu444/caelab-workbench/pull/64)에서 매니페스트 누락·파손·구조 결함 시 전체 상태 조회가 중단되지 않고 안전하게 연결 불가로 진단하도록 교정했습니다 (#50). main에 병합 완료되었습니다. |
| 전문가 대화 시간 예산·단계 표시 | [PR73](https://github.com/pikachu444/caelab-workbench/pull/73)은 main `009d96211c496ced3269763da82975cce642a1f9`에 병합됐습니다 (#46). 준비 20초(정책 기본값)·공급자 응답 60초·전체 관리 대기 85초를 분리하며, 원 독립 QA의 기존 8반례에서 늦은 응답·취소·전환 경계를 확인했습니다. 후속 GUI 후보는 현재 요청의 준비/응답 단계를 표시하고 취소·완료·이전 요청의 늦은 알림을 무시합니다. 관련 Qt offscreen 32검사가 통과했습니다. 후속 PR의 재검토·CI 결과는 해당 PR을 따르며, 실제 공급자·OS 실화면 검증과 전체 제품 완료로 확대하지 않습니다. |
| 전문가 대화 첨부 무결성 백그라운드 검증 (P2 반례 보완) | [PR82](https://github.com/pikachu444/caelab-workbench/pull/82)에서 `ExpertWindow._load` 및 `attach` 동기 해시 검증을 백그라운드 워커와 큐로 분리했습니다 (#45). [PR87](https://github.com/pikachu444/caelab-workbench/pull/87)에서 검증 대상 자산(`target_assets`) 동등성 대조 및 자산 변경 시 재검증 로직을 도입하여 원 지적자(Codex)의 P2 반례 고착을 완전히 해결했습니다. 비자산 편집(초안 저장 등) 중에도 유효한 자산 검증 결과가 안전하게 적용되어 `_asset_verifying`이 정상 해제되고 Ask 버튼이 활성화되며, 검증 중 자산이 변조된 경우 stale 결과를 폐기하고 신규 검증을 재개합니다. 관련 Qt offscreen 34검사가 통과했습니다. 원 지적자의 재검토 판정을 확인하며 실제 공급자·OS 실화면 검증과 전체 P5 완료로 확대하지 않습니다. |
| RAD 요약의 에너지 출처·시간·손상 행 | [PR61](https://github.com/pikachu444/caelab-workbench/pull/61)에서 에너지 번들 분리·동적 헤더·손상 행 처리와 footer 부재 시간 표기를 교정했습니다. 실제 `NativeAnalysisWindow.show_result()`의 footer 부재·사이클 불일치·일치 3사례가 통과했고 원 비판자가 수용했습니다. main 반영 여부는 PR61의 병합 상태로 확인합니다. |
| MDO 민감도 캐시 무결성 | [PR62](https://github.com/pikachu444/caelab-workbench/pull/62)에서 원본 INP/INCLUDE 변경·삭제와 대형 산출물 동일 크기/mtime 변조 시 캐시를 무효화하고 원본·증거를 재검증하도록 교정했습니다. 실제 캐시 경로의 원본 4사례 통과와 기대 메시지 정정 후 대형 출력 1사례 통과를 구분하며 원 비판자가 수용했습니다. main 반영 여부는 PR62의 병합 상태로 확인합니다. |
| Starter 경고 종료 | [PR83](https://github.com/pikachu444/caelab-workbench/pull/83)에서 OpenCourant/OpenRadioss Starter 실행 시 오류 0·경고 양수 종료 및 유효 restart 생성을 정상 수용하고, 기존 출력 충돌 방지 및 Engine 진입 전 진단 보존을 구현했습니다. 가이드 문서(`docs/tutorials/radioss-warning-diagnostics.md`) 동기화 및 11개 회귀 검사 통과를 확인하였으며 main에 병합 완료되었습니다. |
| Windows 전체 사용 흐름 | 저장·전체 재개, 부모 전체 조작·복구, 실제 IME·창 크기·DPI의 각 증거를 DESIGN 종료 조건과 대조해야 함. 일부 화면·Qt offscreen 통과로 전체 승인하지 않음 |
| 독립 입력과 검색 평가 | 과거 현황의 PASS 표와 잔여·독립성 판정이 상충함. [종료 표](docs/history/implementation-history.md#1단계-종료-조건별-판정)와 [입력 적격성](docs/history/implementation-history.md#독립-입력-적격성)의 원자료·시점·서명 범위를 대조하기 전 전체 완료를 확정하지 않음 |
| Windows 포터블 배포 후보 (1단계 진행 중) | [PR84](https://github.com/pikachu444/caelab-workbench/pull/84)에서 Prep PR66 호환 포터블 아카이브 CLI(`scripts/archive_windows_bundle.py`)를 구현·통합했습니다. 실물 로컬 패키징을 통해 13개 핵심 의존성 정적 검사 통과(`build_windows_bundle.py`, `built_static_only`), 6,945개 멤버 파일 CRC 및 SHA-256 검증, `CAELabWorkbench-portable.zip` (381,565,354 bytes, SHA-256 `4f0acfff2163ce7a0065356a1826f49c0d785b0b7254e7139440fb3ac6ff6c83`) 및 `.sha256` 체크섬 발행, 독립 바이너리 스모크 테스트(`CAELabWorkbench.exe --help`, `--caelab-solver-gate`, `--caelab-module`) 정상 통과를 확인했습니다. 단, 이는 정적 번들 후보 생성 단계이며 실제 clean Windows 환경에서의 Qt/VTK GUI 실화면 렌더링, CalculiX/OpenRadioss 실물 솔버 구동 및 E2E 전체 과업 검증은 아직 미완료 잔여입니다. 1단계 포터블 배포는 2026-11-05 18:00 KST 목표로 추진하며 2단계 Windows Installer(`caelab-workbench-setup.exe`)는 조건부 목표(2026-11-08 18:00 KST)로 유지합니다. |
| 보조 StructuralModel 창 | [PR67](https://github.com/pikachu444/caelab-workbench/pull/67)의 #44 단위 표시 교정은 main에 통합됐습니다. 정확한 `si`만 SI 환산·기호를 적용하고 다른 선언·미선언은 원값과 단위 미확인 안내를 유지합니다. 계산 비동기화·소유 취소와 영구 실행 이력은 [PR71](https://github.com/pikachu444/caelab-workbench/pull/71)의 범위이며 위 설명과 해당 PR의 상태를 따릅니다. Windows 실화면·본선 실행의 완료로 확대하지 않습니다. |
| Windows 설치형 준비·컴파일·현재 PC 부분 관측 | [PR89](https://github.com/pikachu444/caelab-workbench/pull/89)의 준비 도구·Inno 템플릿은 main `549b2adb`에 통합됐습니다. 기존 prepare-only와 2026-11-05 실제 compile 1회 exit 0은 별도 기록입니다. 앱 source `b8266199`·installer 코드 `a1ccf8b`로 생성한 EXE는 257,711,146 bytes이며 컴파일 당시 manifest 상태 `compiled_not_installed`, 정확한 SHA256·입력 연결과 같은 원 QA의 산출물 읽기 인수 범위는 [컴파일 관측](docs/tutorials/windows-installer.md#2026-11-05-실제-컴파일-관측)에 기록합니다. 설치 registry/console 7.1.1과 PE resource 0.0.0.0, unsigned EXE·로그 경고를 보존합니다. 추가 승인된 현재 개발 PC 시험에서 설치 로그·HKCU 등록·시작 메뉴 2링크를 확인했고 직접 설치 exit code는 미회수(null)입니다. 새 설치 payload 6,946파일/1,069,940,374 bytes의 목록·크기·SHA256은 compiled inventory와 전부 일치했습니다. 시작 메뉴 링크의 target·작업 폴더를 대조했고 실제 시험은 같은 경로의 직접 실행입니다. 설치 앱 live/responding·초기 Qt 접근성 트리까지 관측했으나 black capture와 첫 Save 입력의 활성화 실패로 저장·정상 닫기·재열기·실화면은 미확인입니다. 같은 QA는 1차 설치·직접 실행 증거만 수용했습니다. 별도 새 프로세스의 공식 Raise 1회도 같은 활성화 오류로 실패했으며 후속 QA 해석은 미회수입니다. 각 빈 시험 프로세스의 강제 정리 4294967295와 모니터 종료를 정상 닫기/crash로 세지 않습니다. 설치 앱·원 사용자 자료/설정은 보존했고 비지원 입력 주입·보안 우회·합성 저장은 하지 않았습니다. [현재 PC 관측](docs/tutorials/windows-installer.md#2026-11-05-현재-pc-부분-관측)은 환경 입력/관측 차단과 제품 결함 미확정을 구분합니다. 제거·업그레이드는 미승인·미실시이며 clean Windows·WMI 차단 경계·Qt/VTK 품질·솔버·rollback·전체 E2E 검수는 남습니다. 전체 설치형 납품은 미완료이고 2026-11-08 18:00 KST는 조건부 목표입니다. |
| 미구현 실행 환경 | WSL 파일/작업 연결, 일부 솔버별 전처리·결과 판독은 [플랫폼 계약](ARCHITECTURE.md#플랫폼별-실행-전제)에 명시 |

<a id="cad-gui-integration-candidate"></a>

## CAD GUI 통합 후보 — 2026-11-05

통합 후보 `f5652c7984c39143e1c9a735ff2743e23e07d4a3`는 기존 G 소비자
`6cdead6`와 모듈 `6bf1fbb`의 결합 `465073c896c69fc2ed62488866cf61c316bc4a3f`를
계승합니다. 선택 geometry의 편집기와 기본 문서 등록 경로 모두 프로젝트·선택
노드·프로그램 문맥을 확인하며, 파일 선택 중 문맥이 바뀌거나 취소되면 등록·실행하지
않습니다. backend가 변경 후 실패하면 실제 모델을 다시 표시하고 남은/불확실한
변경과 기존 dirty를 보존합니다. 부모 상자에는 소유 문서·변경/누락 상태를 표시하되
실행/실패 상태를 파일 경고로 덮지 않습니다. 공통 core/schema·설정·의존성 변경은 없습니다.
runtime와 CAD 가이드는 `570dd26` 및 이전 결합 `2f6b805`와 동일하며,
이번 복구 변경은 `tests/test_native_project_gui.py`의 창 정리 절차에 한정합니다.
등록 후 지속 읽기 실패로 새 참조가 남으면 해당 경로의 이전 캐시를 무효화하고,
실제 문서 경로·확인 불가·dirty와 원 오류 안내를 유지하도록 보완했습니다. 실패한
읽기는 캐시하지 않으며 같은 metadata로 접근이 복구돼도 다시 확인합니다.

작성자는 runtime 수정 `570dd26`에서 Python 3.13.5·Qt offscreen으로 CAD·지속 읽기 실패·
정상 캐시 관련 **31건**의 실패·오류·skip 0, exit 0과 외부 audit 이벤트 0을 보고했습니다.
이전 `54d6481`의 CAD **27건**과 input-only·누락 파일 기본 버튼·연결 저장/Save As
호환성 **8건**은 각각의 당시 실행이며 31건에 합산하거나 현재 코드 전체 검사로 바꾸지 않습니다.
실제 Workbench·프로젝트 저장 및 다른 `ProjectWindow` 인스턴스 재열기를 사용하되
jobs 실행 경계는 기록 대역으로 두고, 호스트 프로그램 availability는 검사 fixture로
고정했습니다. 이는 실제 CAD 실행·export·OS 화면 검수가 아닙니다. 수정 전 중간
22건을 최종 결과에 합산하지 않습니다.

같은 원 QA는 이전 문서 후보 `65ebe7d`를 지속 읽기 실패의 실제 오류 안내 차단과
Ubuntu에서 Windows argv를 검사하는 fixture 전제 두 P2로 인도 보류했습니다.
후자는 소스 반례이며 실제 Ubuntu 실행 실패와 구분합니다. 그 외 지적의 명시 종결과
별도 공존 그래프·새 창 probe 1건 통과도 두 P2의 종결은 아닙니다. 이번 수정은 Windows
host 모델을 해당 flow에 명시하고, document-only 첫 열기 뒤 서로 다른 input/document/output·
연결·단위·stale의 저장/새 창 복원과 문서 우선 열기를 같은 영구 검사에 보완했습니다.
지속 읽기 실패는 신규 경로·동일 경로 ready 캐시·누락 문서 기본 등록 경로에서 확인하고,
metadata 불변 복구를 포함했습니다. 같은 원 비판자는 결합 `2f6b805`에서 두 P2를
각각 종결하고 새 P1/P2가 없다고 판정했습니다. 독립 delta 8건/skip 0과 지속 읽기
probe의 경고 1·Qt 미처리 예외 0·실제 경로/dirty 보존을 확인했으며 다른 실행과 합산하지 않습니다.

이후 `2f6b805`의 [CI41944510227](https://github.com/pikachu444/caelab-workbench/actions/runs/41944510227)에서
Product Python 3.11은 1488 passed/46 skipped, 실제 CalculiX·OpenCourant 작업은
성공했습니다. Product Python 3.12는 전체 회귀 79% 이후 job 15분 제한으로 취소됐고
최종 pytest 요약은 없습니다. 이를 CI 통과나 특정 제품 assertion 실패로 표시하지 않습니다.
두 환경의 Qt·SciPy 버전도 달라 timeout의 전체 원인은 미확정입니다.

기존 WB·원 QA의 격리 4검사 baseline/비교에서는 닫힌 소유 Qt 창의 잔존을 관측했고,
대상별 삭제 완료 시 7개에서 0개가 됐습니다. 단일 Windows 관측 pair를 일반 개선율이나
Linux CI 복구로 확대하지 않습니다. 이번 테스트 수정은 정상 닫기 수락 뒤 소유 대상에만
DeferredDelete를 완료하며, 이미 삭제된 Qt wrapper를 다시 읽지 않도록 Workbench 참조를
먼저 보관합니다. global 삭제·GC·제품 탐색·workflow·timeout·skip은 바꾸지 않았습니다.
작성자는 수정된 native GUI 모듈을 Python 3.13.5·Qt 6.9.1 offscreen·격리 INI로 한 번
실행해 **136 passed, 0 failed/error/skipped**, exit 0·외부 audit 이벤트 0을 보고했습니다.
실제 프로그램 탐색과 기존 검증·params를 유지한 이 로컬 결과를 이전 실행에 합산하거나
새 Linux 전체 CI 결과로 바꾸지 않습니다. 이 문서가 포함된 새 최종 head의 원 QA 확인과
필수 CI는 아직 미완료입니다.

[Draft PR93](https://github.com/pikachu444/caelab-workbench/pull/93)의 원 QA 모듈
17건과 [CI 4작업 성공](https://github.com/pikachu444/caelab-workbench/actions/runs/41882182590)은
모듈 `6bf1fbb`의 기존 증거입니다. 소비자·이 현황 문서를 포함한 최종 결합 head의
원 QA 재검토나 CI로 바꿔 표시하지 않습니다. 문서 합성 시점에 그 최종 검토·필수 CI와
전체 제품 회귀는 미완료이며, 같은 원 QA의 수용 비판 종결과 정확한 후보 검증 뒤
지정 통합 담당의 정상 병합·새 clone 확인을 거칩니다. 기존 frozen r2에는 이 CAD GUI
코드가 없고, 실제 CAD·Windows 실화면·전체 CAD→해석 과업의 인수는 남아 있습니다.

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
