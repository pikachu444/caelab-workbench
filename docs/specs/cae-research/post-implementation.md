# 원래 해석 결과, 공식 변환기와 OpenCourantRP 구현 명세 D — 재구성본

복구 개정 `R4-recovered`, 2026-11-12. 이 문서는 실행 환경이 소실된 뒤 공개 작업 기록을 바탕으로 다시 작성한 **새 문서**다. 과거 담당자 원고의 SHA256 `2d3ae2e8bfdd0451cbceca9a9d97c832e51693de351f7bddcc61a22a3127b1ea`와 바이트까지 동일하다는 뜻이 아니다. 해당 원문과 작은 원래 해석 결과인 CSV, VTK, T01 및 로그의 실제 바이트는 이 재구성에 포함되지 않는다. 아래의 과거 실행 관측과 현재 복구할 수 있는 설계 및 생성 절차를 구분해서 읽어야 한다. 검토 담당 E/F는 실제 새 Git 파일 객체(blob)를 재검토해야 한다.

이 명세는 앞으로 구현할 제품 계약과 참조 시험의 완료 판정 기준을 정한다. 새 Workbench 변환 작업과 시간 이력 판독기, `MaterialStudy` 화면, RP 별도 수정판, 실제 답변 공급자 반복 호출 또는 Windows 제품 흐름을 구현했다는 보고서가 아니다. 여기서 ‘원래 해석 결과’는 솔버가 생성한 원형식 결과(`native`)를, ‘공식 변환기’는 그 결과를 다른 파일 형식으로 바꾸는 공식 프로그램(`converter`)을 뜻한다.

기존 `JobManager/RunStore/ArtifactRefV1/ResultRefV1`를 사용하고, 복구한 공통 핵심 계약의 C1 자료 전달 객체(DTO) 및 근거 교환 묶음 `EvidenceEnvelope`, C2 버전, C3 `PredictionBundle/history`, C4 평가, C5 제약, C6 단위, C8 저장 규칙을 따른다. 동시 저장 시 예상 버전이 맞을 때만 쓰는 비교 후 교환(CAS) 규칙을 지킨다. 별도 작업 대기열, 데이터베이스(DB) 또는 공통 유한요소(FE) 모델을 만들지 않는다. 원래 입력 덱 실행은 재료 보정 지원 프로필에 없다는 이유로 차단하지 않는다.

## D0. 실제 소스와 실행으로 바로잡은 가정

1. `LAW1 shell NIP5`는 `cb760c` Starter에서 경고 `1084`와 함께 `N=0`으로 바뀐다. 따라서 LAW1의 모든 층과 위·아래 위치를 검증한다던 기존 기준값을 폐기한다. 실제 LAW1 시험 자료는 적용된 값 `N0`을 사용했다. 별도의 고항복 LAW2 시험 자료로 `N5`와 `layer1..5` 전달을 확인했으며, 이를 다른 구성법칙의 물리 검증으로 확대하지 않는다.
2. `/IMPVEL Scale_y=0`은 이 프로필에서 기본값 1로 해석된다. 진폭을 0으로 주어 왼쪽을 고정하려 하면 강체 이동과 응력 0이 나올 수 있다. 왼쪽 x 방향은 `/BCS`로 고정하고 오른쪽에만 `IMPVEL`과 같은 초기 속도를 지정한다.
3. 실제 시간 `TIME`은 파일 번호, 명목상 `ANIM/TFILE` 간격 또는 Engine 종료 시간 `Tf`와 다르다. 실제 `Tf=.01`에서 공간장 결과의 마지막 `TIME=.0100191`, 시간 이력의 마지막 시간은 `.007508895`였다. 순번을 물리 시간으로 쓰지 않는다.
4. `T01→CSV`를 계속 후속 과제나 미지원 기능으로 남겨 두지 않는다. `OC-TH4-1`은 선정된 LAW33 발포체의 응력 6성분, `rho`, `VOL`, 절점 8개의 변위 U와 속도 V, 실제 시간 및 전역 진단값을 얻기 위한 필수 구현 프로필이다.
5. RP의 `units.py`는 단위 라벨을 다룬다. 알 수 없는 변수를 `U`로 바꾸거나, 중간층 `middle`을 막응력 `membrane`으로 바꾸거나, 솔버 등가응력 VM으로 대체하는 동작을 수치 근거 판독기에 재사용하지 않는다. 혼합 셀 텐서에서 다른 요소 계열에 채운 0도 유효한 값이 아니다.
6. `thsol.F`의 주석만으로 Lagrangian `VOL`이 초기 부피라고 결론 내리지 않는다. 현재 소스는 `JLAG=1,rho>0`일 때 `WWA(11)=GBUF%VOL*PM(89,...)/GBUF%RHO`로 현재 부피를 계산한다.
7. `D-INTEG-01` 정정: 선택적으로 개발할 RP 별도 수정판의 정확한 CLI는 **`--post-only --handoff <json> --receipt <json>`**다. 과거 계획의 `--project <dir>` 또는 다른 연결 방식 중 나중에 선택한다는 문장을 이 계약으로 대체한다. 원래 RP에 이 CLI가 이미 있다고 표현하지 않는다.
8. `D-INTEG-02/E-06` 정정: 현재 `project.py`의 `SCHEMA_VERSION=3`은 소스에서 확인한 사실이다. 후처리 전용 작업 보조 파일은 버전 3과 호환된다. 새 `material_study` 역할을 처음 저장할 때는 버전 4를 사용한다. 모든 판독기와 다른 이름으로 저장(`SaveAs`) 기능은 버전 1/2/3/4를 읽고 선택 결과와 관계를 보존한다. ‘첫 단계는 무조건 버전 3 유지’를 전체 완료 조건으로 삼지 않는다.

## D1. 고정한 소스와 라이선스 범위

| ID | 고정한 소스 식별값 | 실제 연결 지점과 한계 |
|---|---|---|
| `RP-S` | `CAMLAB-UChile/OpenCourantRP cec23cbdd4058237e04fdcc7ecb06a3834578cae`, `pyproject 1.0.0` | 독립 PySide6/PyVistaQt 앱이다. 라이선스는 `AGPL-3.0-or-later`다. GUI 진입점은 `opencourantrp→app.main`이며 현재 결과를 자동으로 여는 CLI는 없다. |
| `OC-S` | `OpenCourant/OpenCourant cb760c206e346eff12161a2268b134ef9d48a4ef`, `latest-20261011` | 솔버 소스는 AGPL이다. 실행 환경, 입력 판독기, H3D와 CFG에 적용되는 별도 조건을 본체 조건으로 덮어쓰지 않는다. |
| `OC-C` | 실제 로컬 체크아웃 `OpenCourant/Tools 1b6bb9200ee7dca899e61c18bd982c3fb1c72fd1` | `output_converters/anim_to_vtk/src/anim_to_vtk.cpp`, `th_to_csv/src/th_to_csv.c`의 개별 이용 허가 고지는 MIT다. |
| `OC-C-last` | 두 변환기 파일의 마지막 변경 `3fc8c859f30582a1f71e156244987e1a14474d4c` | 당시 해시를 대조해 두 파일이 `1b6` 체크아웃과 같은 바이트임을 확인했다. 실제 추출기 식별값은 `1b6`으로 고정한다. |
| `OC-TH` | `OC-S starter/source/output/th/hm_read_thgrou.F`; `engine/source/output/th/thsol.F, thnod.F` | 시간 이력(TH)의 코드, 순서, 응력축과 값 정의를 확인한다. `thsol`의 전역 `WA2:7`과 국부 `WA35:40`을 구분한다. |
| `OC-FMT` | `OC-S hm_cfg_files`의 `PROP/prop_p1_shell.cfg`, `LOADS/impvel.cfg`, `OUTPUTBLOCK/th_bric.cfg/th_node.cfg`, `CARDS/eng_dtix.cfg` | 형식을 참고하는 용도다. CFG 전체를 제품에 복사하거나 본체 라이선스로 일반화하지 않는다. |
| `WB-S` | `Workbench 104cdd6888a5554eecc8cf965c60ff1bd980b6da` | `JobManager`, `NativeRun/RunStore`, `input_snapshot`, `read_native_frame`, `atomic_json/FileLock`을 재사용한다. |

변환기 소스 SHA256은 다음과 같다.

- `anim_to_vtk.cpp`: `fb2c58a90ac90197aa9cf00c9a8954c638fc6ae267d46de20b94e4ef9cde9549`
- `th_to_csv.c`: `e680547aec4317db158313e5869f644046e8bbe73b8b0b68be18105c46687304`

변환기 원문은 `https://github.com/OpenCourant/Tools/blob/1b6bb9200ee7dca899e61c18bd982c3fb1c72fd1/output_converters/` 아래의 해당 경로다. 솔버 원문은 `https://github.com/OpenCourant/OpenCourant/blob/cb760c206e346eff12161a2268b134ef9d48a4ef/` 아래의 해당 경로다. `OC-S tools/`의 README는 별도 Tools 저장소를 가리킨다.

공식 배포 주소는 `https://github.com/OpenCourant/OpenCourant/releases/tag/latest-20261011`이다.

| 대상 | SHA256과 확인 범위 |
|---|---|
| 실제 다운로드한 Linux ZIP | `6cb419677ca36d73acb96218cc0cf5e4f7d50494d9c7812ba27935e3c0f8ec94` |
| 공식 페이지가 선언한 Windows ZIP | `6d0ea8b43b68aa12f0a2787b339384a554978cbb65a5d3dbaf1e40598f02e7e6` |
| 실제 Linux `starter` | `0bbea37273656b270bea53cf756b115bcd5d9a8875a3c41283922a3b927719d1` |
| 실제 Linux `engine` | `54cc9fc2a7cea6f1215afb6da9109d368650ff1fa7985625cb111f47d7e92be4` |
| 실제 Linux `anim_to_vtk` | `9c97eee2991f6949d16477831cc684165f53b790f498cbef51b8e8fd67bf28ae` |
| 실제 Linux `th_to_csv` | `704d647331ae4befd329d18b991d644f2af930abd117c5d204c23925f75fc8db` |

배포본의 CI 체크아웃은 Tools의 참조 버전을 고정하지 않았으므로 배포 태그만으로 변환기 소스 SHA를 추정하지 않는다. 당시 고정한 소스를 `gcc/g++`로 빌드해 실제 `A005/T01` 출력이 공식 실행 파일 출력과 바이트까지 같음을 확인했다. 그러나 같은 컴파일러나 빌드라는 인증은 아니다. Windows EXE 실행, 새 설치와 실제 화면 확인은 미실시였다. 복구 환경에서는 이 원래 출력과 로그 바이트를 다시 확보하지 못했다. 새로 실행했거나 원출력을 복원했다고 표현하지 않는다.

RP 소스를 WB로 복사하지 않는다. 수치 판독기와 연산자는 독립 수식 및 기존 NumPy/VTK를 사용하고 RP는 외부 결과 표시 프로그램으로 연결한다. 선택적 수정판(`fork`)은 독립 AGPL 저장소와 설치본으로 관리하며 소스, 수정, 저작권과 의존성 고지를 보존한다. 이 작업에는 솔버 또는 RP 배포본을 함께 제공하는 일이 포함되지 않는다. 압축 배포본, 실행 파일, 가상 환경(`venv`), 소스 체크아웃과 빌드 폴더는 최종 Git/ZIP에 넣지 않고 URL, SHA와 재현 명령으로 연결한다.

## D2. 담당 범위, 신규 API와 공용 참조

| 소유 경로 | 구현할 책임 |
|---|---|
| `caelab/artifact_refs.py` | 통합 책임자 한 명이 공용 DTO와 엄격한 JSON 검증을 관리한다. Qt, VTK, GUI 또는 솔버를 가져오지 않는다. |
| `caelab/native/operations.py` | `plan_conversion, submit_conversion, operation_snapshot, cancel_operation, resolve_result_ref`를 구현하고 기존 `JobManager/RunStore`를 사용한다. |
| `caelab/native/converters.py` | `plan_oc_animation, plan_oc_history, validate_oc_outputs`를 구현한다. 실행 인자, 복사와 입출력 한도를 고정한다. |
| `caelab/native/result_adapters.py` | `inspect_dataset/read_result`와 지원 프로필 등록부를 관리한다. 설치되어 등록된 코드만 실행한다. |
| `caelab/result_readers/vtk_fields.py` | `vtkUnstructuredGridReader/vtkXMLUnstructuredGridReader`를 필요할 때 가져온다. 정확한 ID, 시간, 배열, 요소 계열과 필수 자료 충족 여부를 검사한다. |
| `caelab/result_readers/opencourant_history.py` | 표준 라이브러리 `csv`로 `read_titles, bind_history_columns, read_th_csv, extract_history`를 구현한다. |
| `caelab/native/rp_consumer.py` | `prepare_rp_handoff, poll_rp_receipt`를 구현하고 원본 RP의 수동 열기 방식을 명시적으로 안내한다. |
| 기존 Workbench 공용 진입 계층 | 아래 공용 함수와 프로젝트 및 맥락 소속 검사를 담당한다. GUI가 프로세스를 직접 소유하지 않는다. |

공용 진입 함수의 시그니처는 다음과 같다.

~~~text
list_result_capabilities(project, run_id) → AdapterCapabilityV1[]
plan_result_conversion(project, run_id, converter_id, frame_ids, options) → ConversionPlanV1
submit_result_conversion(project, accepted_plan_hash) → OperationRefV1
operation_snapshot(project, operation_id) → OperationReceiptV1
cancel_operation(project, operation_id) → bool
select_result_operation(project,node_id,source_run_id,operation_id,dataset_id) → None
list_result_datasets(project,source_run_id) → DatasetManifestV1[]
query_result(project,ref) → NumericResultV1
open_result(project,ref,consumer_id) → ConsumerLaunchReceiptV1
capture_result_evidence(project,ref,asset_root) → existing evidence envelope
~~~

`project`는 등록된 `NativeProject/ProjectContext`에서만 `RunStore`를 얻는다. 호출자가 루트나 경로를 덮어쓰지 못하게 한다. `NativeRun/job.json`을 파생 작업의 명세 목록으로 바꾸지 않는다. 기존 핵심 계층의 비공개 `_read_grid`나 CalculiX 전용 `_finish`를 범용 판독기처럼 가져오지 않는다. 현재 `native/radioss.py`의 ASCII 시험 자료 순번 시간과 `native/results.py _collect_dat`, CalculiX `parse_dat`의 마지막 프레임 판독기를 물리 시간 이력으로 재사용하지 않는다.

`ArtifactRefV1 = {id,role,path,sha256,size,format_id,producer:{id,version}}` 정본을 그대로 쓴다. 작업과 데이터셋 명세 목록의 소유 관계는 `artifact_owners[id]={scope:run|operation|consumer,id,source_run_id?}`다. 이것을 `EvidenceEnvelopeV1` 최상위 필드로 해석하지 않는다. 근거 교환 묶음 교환 형식의 소유 관계 위치는 공통 핵심 계약 C1의 **`provenance.artifact_owners`**다.

`path`는 소유 루트에 대한 POSIX 상대 경로다. 드라이브, UNC, `..`, NUL, 심볼릭 링크나 Windows 재분석 지점(`reparse`)을 이용한 루트 이탈을 거부한다. `operation` 범위에는 `source_run_id`가 필수다. 선택, 후처리, 재료와 근거는 등록된 `consumer` 이름공간을 사용한다. 새 범위나 새 결과 DTO를 만들지 않는다.

`ResultRefV1`의 최상위 정본 필드는 `schema_version,source_run_id,source_node_id,source_receipt_sha256,source_receipt_ref,operation_id,operation_manifest_sha256,dataset_id,dataset_manifest_sha256,source_artifacts,frame_id,field_id,component,selection,location,basis,measure,shear_convention,units,geometry_state,operator,coverage,eligibility`다. 선택 영역의 ID 목록은 고정한 파일로 보존한다. 원실행 기록 사본인 `source receipt`는 준비할 때 정확한 바이트로 복사한다. 현재 `job.json`의 변경 가능한 값, 오래된 상태(`stale`) 또는 화면 해시로 대체하지 않는다. 모든 근거 소비자는 같은 원본과 참조 해시를 검증한다.

`EvidenceEnvelopeV1`은 공통 핵심 계약 C1(blob `b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac`)을 실제로 읽고 따른다. 교환 형식은 `{schema_version:1,id,kind,content_sha256,provenance,availability,payload}`이고 `kind` 열거값은 `native_run|manual|calibration|document_chunk`다. 별도 `kind=result`를 만들지 않는다. 원래 해석 결과에서 나온 `ResultRef`는 `kind=native_run,payload={legacy_evidence_ref:ArtifactRefV1,source_run_id,source_node_id,derived_result_ref:ResultRefV1|null}`에 둔다. `payload`와 파생 참조의 실행 및 노드 소속을 검증한다. `legacy_evidence_ref`는 기존 캡처 설명자의 고정된 JSON 바이트다. `metadata/text/files`와 `stored_path`를 자동으로 다시 쓰지 않는다.

출처 형식은 `provenance={producer:{id,version},sources:[{source_kind,source_id,source_revision,url}],artifact_owners:{artifact_id:{scope,id,source_run_id?}}}`다. `source_kind`는 `native_run|manual|calibration|official_source|paper|solver_manual`만 사용한다. 과거 문서 예시에서 소유 관계를 근거 교환 묶음의 단순 필드처럼 줄여 쓴 표현은 실제 교환 형식의 필드명이 아니다.

가용성 형식은 `availability={state:available|partial|source_unavailable|corrupt|unsupported,reason_codes:[],missing_artifact_ids:[]}`다. 자료 종류별로 정해진 한 가지 형식만 허용하는 모든 레코드는 `additionalProperties=false`를 적용하고 필수 키, 허용된 `null`과 유한한 JSON 값을 검증한다. `content_sha256`은 자기 필드만 제외한 전체 `provenance/owners/availability/payload`의 정규화 JSON SHA256이며 구형 원본 설명자의 해시와 다르다. 원자료를 다시 확보하지 못하면 `source_unavailable`로 표시하고 알려진 해시, URL과 식별값을 보존한다. 모델에 실제 파일 바이트를 제공한 것처럼 표시하지 않는다.

내부 `HistoryColumnBindingV1`은 공용 결과를 복제하지 않는 메타데이터다.

~~~text
{schema_version:1,column_index,raw_header,normalized_header,group_title,output_type,entity_id,entity_title,variable_code,source_titles_pair_index,quantity,component,measure,basis,unit,operator}
~~~

`column_index`는 0부터 시작한다. 제목 쌍 인덱스는 전역, 부품과 부분집합을 제외한 TH 그룹 순번이다. 데이터셋 필드의 출처 정보에 넣어 내용 해시에 포함하며 다른 CSV에 재사용하지 않는다.

핵심 계약 C3에 따라 `DatasetManifestV1.dataset_kind=field|history`다. 공간장 `field`는 `frames[]/series=[]`, 시간 이력 `history`는 `frames=[]/series[{series_id,coordinate_ref:ArtifactRefV1,row_count,source_row_index_ref:ArtifactRefV1,field_ids}]`를 사용한다. 이력의 `ResultRef.frame_id`는 등록된 `series_id`(예: `history:all`)를 참조한다. 물리시간이 `'all'`인 프레임을 만들지 않는다. `NumericResultV1.sample_coordinates`는 실제 원본 시간 배열이며 `values/mask/counts`를 함께 검증한다. `geometry_state=unknown`이고 최초 이력 연산자는 `curve_extract`만 지원한다. 행별 스냅숏이나 계열 축약 연산은 미지원이다. RP 공간장 표시기는 이력을 거부하고 기존 곡선 표와 시각화 표시기로 전달한다.

전문가의 첫 조회가 점 조회만 지원한다면 D의 셸, 적분점(IP), 층 합성 참조에는 `evidence.describe`만 허용하고 조회는 `unsupported`로 반환한다. 연산자를 요청할 때 고정한 기본 참조를 보존하고 `dataclasses.replace`에 해당하는 방식으로 연산자만 바꾼 요청 참조를 만든다. `values→identity`, `min/max/mean/norm`은 등록 연산자, 버전, 배열 모양과 단위 조건을 통과해야 한다. 추적 기록에는 기본 해시와 요청 참조 해시를 모두 남긴다.

## D3. OC-ANIM-1 변환 계획과 판독

입력은 `OC-S A###` 또는 `A###.gz`이며 출력은 기존 ASCII `VTK3.0 UNSTRUCTURED_GRID`다. 표준 출력을 바이트 모드로 보존하는 일과 출력 파일 형식이 ASCII인 일은 별개다.

1. `RunStore` 파일 목록에서 선택한 정확한 실행, A 파일과 해시를 고정한다. 현재 폴더의 패턴 검색으로 다른 실행 파일을 가져오지 않는다. `inputs/inA001` 같은 짧은 ASCII 파일명에 바이트를 복사하고 원래 경로, 이름과 SHA 계보를 보존한다.
2. gzip이면 원래 gz와 해제한 바이트에 각각 참조와 해시를 둔다. 스트리밍 조각마다 취소를 확인한다. 기본 해제 한도는 5GiB이며 변경한 한도는 사용자가 수락한 계획에 고정한다. 일부만 해제한 파일을 변환기 입력으로 쓰지 않는다.
3. `argv=[absolute_converter_exe,"inA001"]`, `cwd=operation input staging`, `stdin=DEVNULL`, `shell=False`로 실행한다. 표준 출력은 `outputs/<source_name>.vtk.part`에 바이트 스트림으로 쓰고 표준 오류는 별도 `stderr.log`에 쓴다. 두 출력을 섞지 않는다.
4. 종료 코드 0과 비어 있지 않은 파일만으로 성공 처리하지 않는다. 헤더, 데이터셋 종류, 0보다 큰 점과 셀 수, 유한한 스칼라 `TIME`, ID, 배열과 성분 수, 위상, 요소 계열 마스크, 원본 스냅숏의 형상과 U의 관계를 검사한다. 원본 SHA를 재확인한 뒤 원자적 이름 변경과 데이터셋 등록을 한다. 검증 중 취소되면 완료가 아니다.
5. 원본의 `FIELD TIME`은 `native a_time`이다. `CYCLE`은 변환기에서 0으로 고정되어 있으므로 실제 사이클 검증 기준값이 아니다. `NODE_ID`는 점, `ELEMENT_ID/PART_ID`는 셀에 대응한다. 실제 시험 자료의 셀 순서는 `shell202→solid101`이므로 배열 순번이 아닌 ID로 결합한다.
6. `2DELEM` 배열에는 다른 요소 계열용 0 텐서가, `3DELEM` 배열에도 반대 계열용 0이 들어 있다. 해당 원래 요소 계열 마스크만 유효하다. 잘못된 계열을 선택하면 `OBSERVABLE_MISMATCH`이며 0으로 채우지 않는다. 원래 ID, 요소 계열 이름공간과 ID의 유일성을 검증한다.

| 원래 배열 | 값의 소속과 성분 | 지원하는 의미 |
|---|---|---|
| `Displacement/Velocity` | 점, 전역 xyz 정확히 3성분 | 명시한 성분과 유클리드 노름을 지원한다. 동일 단위와 알려진 정규 직교 기저가 필요하다. |
| `3DELEM_Stress` | 솔리드, 행 우선 텐서 9성분 `[xx,xy,xz,yx,yy,yz,zx,zy,zz]` | 검증한 `Isolid1`, 전역 Cauchy 응력과 VM을 지원한다. |
| `2DELEM_Stress_(upper)/(lower)` | 셸, 현재 요소 국부 텐서 9성분 | 정확한 위쪽과 아래쪽 위치를 지원한다. 두께 적분점 수 NIP와 위치는 구분한다. |
| `2DELEM_Stress_(layer__n)_` | 실제 출력된 `n` | LAW1 N0의 실제 `layer1`, LAW2 N5의 `1..5`를 지원한다. 없는 `layer3/middle`을 `membrane`으로 대체하지 않는다. |
| 솔리드 및 셸 `Strain` | 실제 배열과 성분 | 변형률 정의와 전단 성분 규약의 출처가 미확정이면 진단 조회만 허용하고 `fit=false/MEASURE_UNKNOWN`으로 처리한다. |
| 그 밖에 발견한 배열 | 원래 소속과 설명자 | 이 두 기준 시험 밖의 물리량은 선언되었지만 미검증 상태다. 이름만으로 적합 평가용으로 승격하지 않는다. |

텐서 대칭 허용오차는 절대 `1e-8`과 상대 `1e-6`의 합이다(`abs1e-8+rel1e-6`). 실패한 텐서를 조용히 대칭화하지 않는다. 등가응력은 `VM = sqrt(.5*((xx-yy)^2+(yy-zz)^2+(zz-xx)^2)+3*(xy^2+yz^2+xz^2))`로 계산한다. 전체 텐서 지원과 스칼라 VM 지원은 별개다. 셸 최댓값과 최솟값은 실제 비교한 위치 ID 목록에서만 구한 값이며 전체 두께의 극값이라고 표현하지 않는다. 값의 원래 소속을 절점 평활화나 적분점 외삽으로 자동 변경하지 않는다.

형상은 변형된 점 좌표와 기준 형상에서 현재 형상으로의 변위 U를 함께 가진다. `reference=points-U`, `display(a)=reference+a*U`다. 변형을 두 번 적용하거나 원배열을 수정하지 않는다. 삼각형의 현재 국부 축은 `x=unit(P2-P1),n=unit((P2-P1)×(P3-P1)),y=n×x`다. 사각형은 `n=unit((P3-P1)×(P4-P2))`를 사용한다. `unit(P3-P1)+unit(P2-P4)`를 평면에 투영하고 정규화해 x축을 만들며 `y=n×x`로 둔다. 최대 모서리 길이에 대한 방향 벡터 노름의 비가 `1e-12`보다 작으면 `UNSUPPORTED_BASIS`다. R의 행이 국부 축이면 `global=Rᵀ local R`이다.

요소 축과 직교이방성 재료 축은 같지 않다. 방향 정보와 그 해시가 없으면 재료 기저를 지원하지 않는다. 축이 정렬된 원래 해석 시험과 90도 회전한 합성 시험은 서로 다른 증거다. 합성 시험을 실제 회전 셸의 물리 검증으로 승격하지 않는다.

`mm–Mg–s`로 단위를 선언했을 때 SI 변환 배율은 길이 `.001`, 질량 `1000`, 시간 `1`, 응력 `1e6`, 힘 `1`, 에너지 `.001`, 밀도 `1e12`, 속도 `.001`이다. 원본 단위가 선언되지 않았으면 `unknown/scale=null`로 둔다. RP 라벨을 수치 환산의 증거로 쓰지 않는다. 화면 표시 단위를 바꾸어도 원래 바이트와 단위는 바뀌지 않는다.

## D4. OC-TH4-1의 T01→CSV 변환과 재료 관측값

필수 지원 범위는 `OC-S /TFILE/4+/TH/TITLE`, Lagrangian 1점 적분 HEX8(`Isolid1`), BRIC의 응력 6성분과 `DENS/VOL/IE/BULK`, NODE의 `DX/DY/DZ/VX/VY/VZ`, 전역 에너지, 질량과 시간이다. LAW33 Ka0/Ka1의 물리적 옳고 그름은 담당 `materialA`의 구성법칙과 독립 기준값으로 판정한다. 결과 전달 프로필은 독립 LAW1/LAW2 및 실제 LAW33 원덱으로 확인한다. `TFILE1–3`, 재시작 이력 이어 붙이기, 임의 적분점 평균과 원래 반력 검증은 이 프로필에 포함되지 않는다.

출력 카드는 다음 형식을 따른다.

- `/TH/BRIC/<groupid>` 다음 줄에는 그룹 제목 `BRIC`, 그다음 줄에는 `%-10s` 코드 칸으로 `SX SY SZ SXY SYZ SXZ DENS VOL IE BULK`를 쓴다. 다음 줄은 `%10d +10spaces+%-80s entity title` 형식이다.
- 아핀 변형 시험의 `ID101/entityhex`, 발포체 시험의 `ID1/entityfoam`처럼 고정 요청의 제목과 ID를 함께 대응시킨다.
- `/TH/NODE/<groupid>`에는 제목 `NODE`, `%-10s` 코드 칸 `DX DY DZ VX VY VZ`를 쓴다. 각 행은 `%10d%10d%-80s` 형식의 원래 ID, `skew0`, `n<id>`다.
- Engine에는 `/TFILE/4` 다음 줄에 명시적 `dt`를 쓰고 `/TH/TITLE`을 사용한다.

공식 `th_to_csv` 실행은 `argv=[absolute_exe,"inT01","history"]`, `cwd=input staging`, `stdin=DEVNULL`, `shell=False`다. 표준 출력과 오류는 로그이며 결과는 `history.csv` 파일에 쓴다. `inT01_TITLES`도 같은 기본 파일명에 정확한 고정 바이트로 복사한다. C의 `char[100]` 파일명 버퍼 때문에 긴 절대 경로나 원래 기본 파일명을 `argv`에 넣지 않는다. 선택적 출력 이름에 `.csv`를 붙여 넘기면 `.csv.csv`가 된다. 짧은 ASCII 이름과 한글 또는 공백이 있는 원래 프로젝트 경로의 관계는 출처 대응표에 보존한다.

T01/TITLES 스냅숏이 없으면 실행 요청을 넘기기 전에 `UNSUPPORTED_OBSERVABLE`로 거부한다. 원래 실행에 출력 카드를 넣거나 솔버를 다시 실행하지 않는다. TITLES가 없는 `var n` 또는 충격량 플래그가 미확정인 경로는 보정에 쓰지 않는다. 출력을 추가하려면 사용자가 Prep에서 명시적으로 수정하고 새 원형식 해석을 실행해야 한다.

`read_titles`는 ASCII 쌍 행의 순서와 원래 바이트 및 해시를 보존한다. 각 쌍은 정수 자료형 코드(`type int`)를 적는 10칸과 변수 코드(`variable code`)를 적는 10칸으로 구성한다. 원래 표기는 `type int width10,variable code width10`이다. `read_th_csv`는 표준 라이브러리의 `csv.reader(newline="")`로 따옴표가 있는 원헤더 `raw_header`를 읽는다. 헤더의 전역 첫 열은 정확히 `time`이어야 한다. 모든 데이터 행은 같은 열 수를 가져야 하고 값은 유한해야 하며 시간은 엄격히 증가해야 한다. 헤더 정규화는 양 끝 공백을 제거하고 내부 공백을 한 칸으로 바꾸는 것뿐이다. `raw_header/column_index`를 함께 보존한다. 알려지지 않은 `NO NAME`을 추측한 물리량으로 바꾸지 않는다.

실제 정규화 헤더는 `BRIC 101 hex SX`, `BRIC 101 hex DENS`, `NODE 12 n12 DX`였다. TITLES의 `type1(Solid)/type0(Node)`, 제목 쌍 순번, 고정 출력 요청, 개체 ID와 제목을 모두 대조한다. 요청 코드 순서가 CSV 순서라고 가정하지 않는다. 실제 표준 순서는 `SX SY SZ SXY SYZ SXZ IE DENS BULK VOL`이다. 접미사 `SX`만으로 대응시키거나 제목의 숫자를 임의로 원래 ID로 해석하지 않는다. 정규화 헤더가 중복되면 `INVALID_DATASET`이며 합치지 않는다. 사용자가 임의 제목을 입력하면 명시적인 열 연결 검증을 통과해야 한다.

TH 응력은 `[SX,SY,SZ,SXY,SYZ,SXZ]→tensor6[xx,yy,zz,xy,yz,xz]`로 대응하며 전역 Cauchy 응력과 텐서 전단 성분이다. `thsol WA2:7`은 회전 정식화에서 전역 변환 후 값이고 `LS*`는 별도 국부 값이다. `Isolid1`의 한 점만 적분점 값 `IPpoint`로 읽는다. 완전 적분 평균을 `IP1`이라고 붙이지 않는다. 셸 TH 층은 이 프로필에서 지원하지 않는다.

LAW33 필수 관측값은 전역 Cauchy 응력 6성분, `DENS`, 절점 8개 모두의 U/V와 실제 시간이다. `J=rho0/rho`, 현재 `VOL/V0`와 절점 기반 `λxλyλz`가 일관되게 전달되는지 검사한다. `c=1-J`는 압축 양수이고, `eps_v=J-1`, `λx=(L0+mean(Uright)-mean(Uleft))/L0`다. `IE`는 초기 부피로 나눈 내부에너지, 즉 에너지밀도다. 전역 `INTERNAL ENERGY`와 섞지 않는다. 운동에너지, 내부에너지, 아워글라스와 추가 질량의 임계값은 A가 명시한 평가 정책에서 정한다.

실제 시간 순서를 유지한다. 변형률 축으로 전체를 정렬하거나, 명목 시간을 끼워 넣거나, 없는 `t0`를 만들거나, 끝값을 유지해 범위를 연장하거나, 외삽하지 않는다. 실제 표본이 실험 비교 시간 범위의 양 끝을 둘러싸지 못하면 `COVERAGE_INCOMPLETE`다. 마지막 비교 시점 뒤까지 실행하도록 시간을 명시해 필요한 범위를 확보해야 한다. `/TFILE/4`가 정확한 명목 시간 격자를 보장한다고 가정하지 않는다. 원래 속도 V는 명시적 시간 적분의 반 시간 간격 값일 수 있으므로 램프에서 같은 현재 `TIME`의 명령 속도 V와 자동 비교하지 않는다. 명령을 적분한 변위와 실제 U의 비교는 유지한다.

반력, 접촉력과 단면력은 별도 확장 범위다. 공식 `csvFileWrite`는 TITLES와 type으로 충격량을 분류하고, 양 끝에서는 한쪽 차분, 내부에서는 할선으로 다음 값을 계산한다.

~~~text
F0=(I1-I0)/(t1-t0),
Fj=(I[j+1]-I[j-1])/(t[j+1]-t[j-1]),
Flast=(Ilast-Iprevious)/(tlast-tprevious).
~~~

이는 불규칙 시간 격자에서 정확한 순간 미분값이 아니다. `REACX`를 솔버가 출력한 원래 힘으로 표시하지 않는다. 연산자 `official_impulse_secant/version=converterhash`와 `params(endpoint/interior/time_source)`를 보존한다. 비교 실험에도 같은 측정 연산자를 명시해야 한다. 표본 3개 이상, 엄격한 시간 증가와 완전한 TITLES가 필수다. 원래 반력 기준 시험은 미실시이므로 `fit=false REACTION_FORCE_NOT_VERIFIED`다. 합성 자료 `t[0,1,3],I[0,1,9]→F[1,3,4]`와 1행 및 중복 TIME 거부는 알고리즘 인수 시험이며 실제 반력 검증이 아니다.

## D5. 파생 작업 저장, CAS, 취소와 캐시

`RunStore.root` 아래에 다음 경로를 둔다.

~~~text
source_run_id/job.json: 기존 solver 기록
.derived/source_run_id/operation_id/{operation.json,dataset.json,inputs/,outputs/,operation.log,stderr.log}
.post/launch_id/{handoff.json,data/,receipt.json}
selected-derived/sha256(node_id).json
selections/selection_id/payload.json
~~~

`OperationReceiptV1`은 작업이 무엇을 실행했고 어떻게 끝났는지 기록한다. 스키마 버전은 `schema_version=1`이다. 기록할 정보는 `id,operation_kind convert|extract,source_run_id,source_receipt_ref/sha,source_input_revision,source_artifacts,adapteridentity/codehash,executable(path_audit/version/hash/OS),planhash/cachekey,created/started/finished,state,parse_state,stage,progress,returncode,error,warnings,outputs,dataset_ref,reused_from_operation_id`다. 이 목록에서 슬래시 등으로 묶은 표현은 기록할 정보를 줄여 쓴 것이며 새 JSON 키를 정의하지 않는다. 정확한 키와 구조는 공용 `OperationReceiptV1` 계약을 따른다. 파일 참조는 공용 `ArtifactRef`만 쓴다. 정확한 `argv`, 상대 작업 디렉터리(`cwd_relative`), 표준 입력 역할(`stdin_role`), 표준 출력과 오류 대상(`stdout_target/stderr_target`), 환경변수 이름(`env_names`), 도구 버전(`tool_version`)을 감사 기록에 남기되 비밀 값은 저장하지 않는다.

원실행 기록은 준비할 때 정확한 바이트로 복사한다. 다시 열 때 고정 바이트 및 자료 SHA를 현재 실행의 불변 식별값과 대조한다. 이 식별값은 `id,node,solver,inputrevision`, 스냅숏 원본과 실행 입력의 SHA, 원래 출력 SHA다. 현재의 오래된 상태, 현재 실제 경로 또는 화면 정보를 지문에 넣지 않는다. 고정 기록 안의 과거 절대 경로는 감사용 문자열이며 참조 해석기가 파일을 여는 경로로 쓰지 않는다. 이력 조회에서는 현재 모델과 달라 오래된 `stale`와 바이트가 손상된 `corrupt`를 구분한다.

계획을 만들 때는 실행하지 않는다. 실행 파일, 해시, 버전, 호스트, 입력 SHA, 프레임과 필드, 옵션, 단위 프로필 및 예상 저장 용량을 고정한다. 제출 시 재검증해서 달라졌으면 `STALE_PLAN`이다. 설치 후 등록된 연결 코드 외에는 JSON으로 셸이나 코드를 불러오지 않는다. 종료 상태가 된 원래 실행만 입력으로 사용하고 호출자가 루트 위치를 덮어쓰지 못하게 한다.

작업은 `queued→preparing→running→validating→completed` 순서로 진행한다. 모든 복사, 읽기 조각과 단계에서 취소를 확인한다. 종료 코드 0 이후라도 검증 중 취소하면 `cancelled`이며 데이터셋을 등록하지 않는다. 관리자 잠금 안에서 처음 도착한 종료 상태만 수용한다. 기본 제한 시간은 준비와 검증을 포함한 120초이며 저장 용량 및 압축 해제 한도는 수락한 계획에 고정한다.

Windows에서는 일시 정지 상태로 만든 자식 프로세스를 `KILL_ON_JOB_CLOSE`가 설정된 `JobObject`에 배정한 뒤 실행을 재개한다. Unix에서는 새 세션의 프로세스 그룹을 만들고 `TERM` 후 5초, 이어서 `KILL` 후 5초를 기다린다. 자원 정리가 실패하면 `PROCESS_CLEANUP_FAILED`이며 정상 취소처럼 표시하지 않는다. 기록한 PID는 진단용이다. 다시 열 때 해당 PID의 프로세스를 인계받거나 종료하지 않는다. 비정상 종료 뒤 잠금이 없는 활성 작업은 `interrupted`로 바꾸고 `.part` 파일은 격리한다. 자동 재실행하지 않는다.

캐시 식별값에는 불변 원본 지문과 입력 역할별 SHA, 변환기 실행 파일과 해시 및 버전, 판독기와 프로필 및 연산자, 프레임과 필드 및 옵션, 단위와 기저 및 물리량 정의를 넣는다. 변경 가능한 문서 CAS, 감사용 절대 경로, 오래된 상태, 카메라와 테마는 제외한다. 캐시가 맞아도 파일과 사용 적격성을 재검증한다. `cancelled/failed/interrupted` 또는 필수 자료가 일부만 있는 결과는 성공 캐시로 취급하지 않는다. 여러 작업을 보존하고 최신 작업이나 다른 실행 결과로 자동 대체하지 않는다.

후처리 전용 버전 3, 새 재료 역할 버전 4, 읽기 버전 1–4 규칙은 D0을 따른다. 공통 핵심 계약 C8의 쓰기와 `SaveAs` 규칙을 사용한다. 자식 연구를 열 때부터 닫을 때까지 `study.lock`을 유지하며 작성자는 한 명이다. 자식 저장은 자신의 점유권을 다시 얻지 않고 부모 CAS를 대기 없이 갱신한다. 부모의 일반 저장은 프로젝트 잠금과 CAS를 쓰며 참조 자료 전체를 복사하는 동작이 아니다.

자식을 저장한 뒤 부모 CAS가 충돌하면 연구 저장을 되돌리지 않고 `ASSOCIATION_UPDATE_PENDING`으로 기록한다. C8의 자식 데이터 경로 `pending-association/<event_id>.json`에 부모 식별값과 상대 연결, 예상 부모 문서 버전, 저장을 완료한 자식 문서 및 내용 버전, 역할 데이터와 `created_at`을 담은 불변 기록을 원자적으로 쓴다. 연결 복구(`Reconcile`)는 같은 부모와 예상 버전에서만 CAS를 대기 없이 재시도하며 충돌을 자동 병합하지 않는다. 성공하면 별도 해결 이벤트로 닫고 원기록을 보존한다.

부모 `SaveAs`는 자식 루트 목록의 스냅숏을 만든 뒤 정렬한 자식 점유권, 프로젝트 잠금, `RunStore` 점유권을 순서대로 제한 시간 0으로 얻고 프로젝트 버전을 다시 검사한다. 하나라도 실패하면 모두 해제하고 아무것도 바꾸지 않는다. 편집 가능한 자식이 유휴 상태로 열려 있어도 `STUDY_OPEN_FOR_EDIT`이며 닫은 뒤 `SaveAs`를 해야 한다. 활성 작업 또는 뷰어 준비 파일 작성자가 있어도 복사를 시작하기 전에 사용 중으로 거부한다.

독립 재료 연구의 `SaveAs`는 C8의 별도 트랜잭션이며 저장 대상 부모 디렉터리의 기록 파일과 준비 영역을 사용한다. 읽기 전용 원본에는 잠금이나 기록 파일을 만들지 않는다. 명세 목록, 전체 참조 묶음과 실행 기록 해시를 복사 전후로 대조하는 방식으로 `SOURCE_CHANGED_DURING_COPY`를 검출한다. 대상 데이터 이름 변경 후 대상 `study.lock`을 얻고 전체 참조를 재검증하며 명세 목록 저장을 마친 뒤에만 원본 점유권을 해제하고 쓰기 대상을 새 위치로 바꾼다. 저장 완료 후 정리가 실패하면 유효한 대상을 지우지 않고 `committed_cleanup_pending`으로 남긴다.

부모 저장은 완료된 전체 참조 묶음을 바이트로 복사하고 새 루트에서 참조, 해시와 원본 식별값을 재검증한 뒤 프로젝트를 원자적으로 저장한다. 원본 루트를 삭제해도 보관본만으로 고정된 시간 이력, 공간장과 검토를 다시 열 수 있어야 한다. 알 수 없는 보조 파일 스키마는 읽기 전용 미지원으로 처리한다. `.derived`가 없으면 빈 상태이며 자동 삭제, 수정 또는 재실행하지 않는다.

새 연구의 `document_revision`은 변경 가능한 명세 목록의 CAS 버전이고 `content_revision`은 연구 내용의 불변 해시다. 후처리는 제약 또는 평가 DTO를 별도로 만들지 않는다. 필수 학습 실행, 물리량 정의 또는 필수 자료의 충족성과 무결성에 오류가 있으면 목적함수를 만들지 않는다. 정상 계산에서 오차가 크면 유한한 적합 손실로 남긴다. 유한한 제약 위반 `INFEASIBLE`와 필수 제약값 미계산을 구분한다.

오류 코드의 정본은 다음과 같다.

`PROGRAM_UNAVAILABLE,UNSUPPORTED_CAPABILITY,STALE_PLAN,SOURCE_CHANGED,HASH_MISMATCH,PATH_ESCAPE,CONVERTER_EXIT,EMPTY_OUTPUT,INVALID_DATASET,MISSING_TIME,MISSING_ID,UNSUPPORTED_FIELD,UNSUPPORTED_BASIS,MEASURE_UNKNOWN,UNIT_UNKNOWN,PARTIAL_REQUIRED_FIELD,TIMEOUT,CANCELLED,PROCESS_CLEANUP_FAILED,STARTUP_FAILED,HANDOFF_MISMATCH`.

재료 관측값을 요청하기 전 지원이 없으면 `UNSUPPORTED_OBSERVABLE`, 실행 후 필수값이 누락되면 `COVERAGE_INCOMPLETE`, 값의 소속, 종류 또는 ID가 틀리면 `OBSERVABLE_MISMATCH`다. 상세 정보에는 필드, 선택 범위, 행, 파일, 사유와 사용 가능한 목록(`field/selection/row/artifact/reason/availablelist`)을 넣는다. 오류를 0, 다른 변수, 다른 실행 또는 마지막 프레임으로 대체하지 않는다.

## D6. 사용자 흐름, 원본 RP 수동 열기와 선택적 자동 연결 수정판

사용자는 부모 Qt 화면에서 실행, 변환 작업과 데이터셋, 실제 시간의 프레임, 필드와 성분, 선택 범위와 층 및 기저를 차례로 명시한다. 과거 실행도 표시하고 해시가 검증되면 조회할 수 있게 한다. 손상된 자료를 단순히 오래된 상태로 표시하지 않는다. 여는 동작만으로 솔버, 변환기 또는 LLM을 실행하지 않는다. 변환 계획은 실행이 없고, 실제 변환은 기존 `JobManager`가 담당하며, RP 보기는 준비된 데이터셋을 보여준다. 일회용 실행 식별값(`nonce`)과 해시를 기본 화면에 나열하지 않고 대상, 진행 상황, 실패와 복구 방법을 보여준다.

원본 RP 1.0.0은 수동 열기만 지원한다. 설치된 RP를 일반 실행한 뒤 `Select project folder`에서 독립 후처리 준비 폴더를 선택한다. 정확한 Starter/Engine 입력 쌍 또는 합쳐진 입력 덱과 `NAMEA숫자.vtk` 사본이 필요하다. 실제 `find_deck_name/list_vtk` 규칙을 따른다. 폴더의 마지막 프레임 선택과 사용자의 필드 선택은 수동 방식의 한계다. `QSettings`는 `OpenCourantRP/OpenCourantRP`의 전역 프로젝트와 단위를 기억한다. 원본 RP에 지원하지 않는 인자를 보내거나 빈 앱 실행을 정확한 결과 열기의 성공으로 표시하지 않는다.

RP의 `run_deck.bat`는 `rp_run` 삭제, Starter/Engine 재실행, H3D 제거와 ANIM 추가를 수행하므로 결과 보기나 기존 결과 변환에 절대로 호출하지 않는다. `gzanim.convert`도 자체 차단 프로세스를 실행하고 중도 취소를 지원하지 않으므로 WB 작업 스레드에 가져오지 않는다. A 파일 없이 H3D만 있으면 해당 A 파일 변환을 명시할 수 없다. Prep에서 출력 설정을 수정하고 새로 실행하는 일은 별도 작업이다.

선택적 RP 수정판은 검증된 변환이나 수치 조회의 선행 조건이 아니다. 별도 AGPL 저장소에 `cli.py/handoff.py`를 새로 만들고, `app.py` 진입점, `MainWindow`와 실행 차단 검사, `project.py`의 정확한 파일 목록, 빌드 실행기와 `pyproject` 진입점을 변경한다. 기존 `viewer/variables/PyVista`는 재사용하되 정확한 선택을 요구하는 모드에서는 임의 대체를 금지한다.

- `--capabilities-file PATH`는 Qt를 가져오지 않고 프로필 `wb-rp-post1`, 스키마와 버전 JSON을 원자적으로 쓴 뒤 종료 코드 0으로 끝낸다.
- 정확한 실행 인자는 `--post-only --handoff ABS_JSON --receipt ABS_JSON`이다.
- 프로필이 알 수 없거나 없거나 불일치하면 수동 열기 안내를 표시한다. 원본 RP에는 이 플래그를 보내지 않는다.
- 전달 문서 `handoff`에는 실행 ID와 nonce, 전체 `ResultRef`와 해시, 데이터셋 명세 목록 해시, 정확한 상대 파일 허용 목록과 SHA, 요청 프레임과 실제 TIME, 필드, 성분, 층, 기저, 단위, 형상, 선택 범위 및 결과 기록 경로를 넣는다. 원문 항목명은 `launchid/nonce/fullResultRef+hash,datasetmanifesthash,exactrelativefileallowlist+SHA,requestedframe/actualTIME/field/component/layer/basis/units/geometry/selection,receiptpath`다.
- `.post/launchid/data` 준비 영역에 바이트를 복사한다. RP는 원본 루트나 임의 URI에서 추가 자료를 읽지 않는다. 한 프레임도 허용 목록으로 지정해 폴더 검색의 마지막 파일 선택을 대체한다.
- `MainWindow(post_only=True,startup_handoff=...)`는 실행별로 격리한 `QSettings`를 쓴다. 이전 `project/lastfile/units/variable`을 복원하지 않는다. 선언된 단위만 라벨로 표시하고 알 수 없는 값은 비워 둔다.
- 프로젝트 선택, `Run/Stop/GZIP`는 화면과 직접 메서드 `_run/_stop/_set_gz_pending/_maybe_ask_gzip/_ask_gzip` 양쪽에서 실행을 차단한다.
- 정확한 모드에서 필드를 알 수 없으면 종료 코드 4다. `U`로 대체하거나 없는 `middle`을 `membrane`으로 대체하지 않는다. 평활화 끔, 원래 값의 소속 유지, 변형 배율 1로 초기화한다. 이후 사용자가 표시를 바꾼 내용은 최초 선택 결과 기록과 분리한다.
- 화면 캡처와 동영상은 사용자가 별도로 지정한 경로에만 저장하며 원본이나 준비 영역을 덮어쓰지 않는다.

결과 기록(`receipt`)은 스키마, nonce, 해시와 허용 경로 내부 여부 검증, 실제 VTK 읽기, 정확한 프레임과 필드 및 층과 기저 선택, 첫 화면 그리기의 `EndEvent` 관측, `ready`의 원자적 저장 순서로 만든다. `ready`에는 `launch/nonce/refhash/actualTIME/counts/selectedarray/component/layer/basis/availablefields/coverage/monotonicseq`를 보존한다. 정상 종료는 `closed`, 종료 코드 0이며 `ready_seen/ready_at`을 유지한다. 읽기 전에 창을 닫으면 준비 완료가 아니다. `Popen` 성공만으로 `opened` 성공 처리하지 않는다.

스키마나 CLI 오류는 종료 코드 2, 파일 누락이나 해시 오류는 3, 미지원 필드·기저·층은 4, Qt/VTK 시작 또는 읽기 실패는 5다. 실패 기록을 쓰고 사용자 응답을 기다리지 않고 종료하며 `MessageBox`에서 영구 대기하지 않는다.

WB의 준비 완료 대기 한도는 기본 30초이며 설정할 수 있다. 화면 처리는 막지 않는다. 잠금 안에서 첫 종료 상태만 인정하고 제한 시간이 지난 뒤 온 `ready`는 무효다. `ready` 전에 취소하면 해당 실행의 프로세스 그룹만 중단하고 실패한 준비 자료는 보존한다. 솔버와 변환 상태는 바꾸지 않는다. `ready` 후 사용자가 창을 닫으면 `closed`이며 솔버 취소가 아니다. 다시 열 때는 새 실행 ID와 nonce를 만들고 닫힌 창을 자동 재실행하지 않는다. `ready`는 수치 기준값 검사나 Windows 실제 화면 검증을 대신하지 않는다.

## D7. Windows, 빌드와 실행 환경

RP 소스의 최소 요구사항은 `Python3.10+`, `PySide6>=6.5`, `PyVista>=.43`, `PyVistaQt>=.11`이다. `pyproject`의 다른 의존성까지 정확한 버전 고정 목록과 고지에 기록한다. 설치, 시작과 결과 읽기는 별도 상태로 표시한다. 원래 `tools/build_exe.py`는 PyInstaller의 폴더 배포(`onedir`), 창 모드(`windowed`)와 솔버 자동 입수를 수행한다. 이를 WB 빌드의 필수 단계로 실행하지 않는다. 보기 전용 수정판 빌드는 솔버를 동봉하지 않는 프로필과 전용 보기 자체 검사를 둔다. 원래 `--selftest`의 솔버, 동영상과 수식 텍스트(`mathtext`) 검사와 구분한다.

Windows 원형식 변환기는 `exec/anim_to_vtk_win64.exe`, `th_to_csv_win64.exe`다. 설정한 OpenCourant 루트 하나에 속하는 실행 파일과 실행 환경을 사용한다. `PATH`에는 선택한 루트의 `hm_reader/win64`와 `intelOneAPI_runtime/win64`를 사용한다. `RAD_CFG_PATH=선택root/hm_cfg_files`, `KMP_STACKSIZE=400m`로 설정한다. 외부 프로세스 환경에서 WB 묶음의 Qt 및 Python 경로 영향과 `PYTHONPATH/QT_PLUGIN_PATH/QML2_IMPORT_PATH` 오염을 제거하거나 명시적으로 정한다. 비밀 환경변수 값은 저장하지 않는다. DLL 적재 오류, 프로그램 발견, 정상 종료와 판독 성공을 각각 표시한다.

선택적인 Linux 소스 변환기 빌드 명령은 `gcc -DLINUX th_to_csv.c`, `g++ -DLINUX anim_to_vtk.cpp`다. Windows VS2022 x64 Native Tools의 원래 빌드는 `cl /Fe... th_to_csv.c`, `cl -DWIN32 /Fe... anim_to_vtk.cpp Ws2_32.lib`다. 전체 솔버의 oneAPI 및 판독기 빌드는 별도 작업이다. WSL은 별도 등록 통신 방식, 취소와 전송을 검증하기 전까지 Windows 직접 실행 지원으로 표시하지 않는다.

Windows 완료 검사에는 한글, 공백, 긴 원래 경로와 짧은 입력 기본 파일명, 표준 출력과 오류 분리, 자식 취소, 시간 초과, 조기 종료, 기존 `QSettings`의 프로젝트 설정 영향, 알 수 없는 필드, GPU/VTK 첫 화면 그리기, DPI 100/200, `SaveAs` 뒤 원본 삭제 및 재열기를 포함한다. 프로세스 모사는 작업 수명주기, 합성 자료는 형식과 연산자, 실제 원래 해석 결과는 선정 프로필을 검증한다. 운영체제 실제 화면 확인은 이들과 별도의 검증 항목이다.

## D8. 과거 참조 시험 관측과 시험 자료 생성 절차

환경 소실 전에 다음 참조 시험을 실제로 수행했고 E/F가 별도로 검산했다. **이 문서는 원래 CSV/VTK/T01/로그 바이트를 재구성하지 않는다.** 새 구현자는 공식 배포본과 생성 절차로 다시 실행하고 원시 파일, SHA, 운영체제와 `argv`를 저장해야 한다. 다음 숫자는 당시 공개 보고에 남은 관측값과 사전에 정한 수치 허용 정책이다. 새 실행 결과가 아니다.

### 아핀 변형 LAW1 N0 시험

단위는 `mm–Mg–s`, `E=1000MPa`, `nu=.25`, `rho=7.8e-9Mg/mm3`, `Tf=.01s`다.

- `HEX101/part10`은 한 변 10mm인 정육면체다. `nodes11..18`은 차례로 `(0,0,0),(10,0,0),(10,10,0),(0,10,0),(0,0,10),(10,0,10),(10,10,10),(0,10,10)`이다.
- 별도 `QUAD202/part20`의 `nodes21..24=(0,0,20),(10,0,20),(10,10,20),(0,10,20)`이며 두께는 1mm다.
- 솔리드 속성은 `Isolid = 1`, `Ismstr = 4`, `Iale = 0`, `qa = 1.1`, `qb = .05`, `h = .1`, `lambda = 0`, `mu = 0`이다.
- 셸 속성은 `Ishell = 24`, `Ismstr = 4`, `Ish3n = 2`, `Idrill = 2`, `Ipinch = 0`이다. `Hm/Hf/Hr/Dm`은 모두 0, `Dn = .015`, `N = 0`, `THICK = 1`, `Ashear = 5/6`, `Ithick = 2`, `Iplas = 0`, `Ipos = 0`이다.
- 셸 N 행의 고정 칸 너비는 순서대로 N 10칸, 공백 10칸, THICK 20칸, ASHEAR 20칸, 공백 10칸, Ithick 10칸, Iplas 10칸, Ipos 10칸이다. 원래 형식 표기는 `N10+blank10+THICK20+ASHEAR20+blank10+Ithick10+Iplas10+Ipos10`이다.
- 모든 절점의 y/z 병진과 셸 회전은 0이다. 왼쪽 x 방향은 BCS로 변위 0을 지정해 고정한다. 오른쪽 x 방향 초기 속도는 IMPVEL의 1mm/s와 같게 지정한다. 왼쪽 ID는 `11,14,15,18,21,24`, 오른쪽은 `12,13,16,17,22,23`이다.
- 일정 속도 함수는 `FUNCT=(0,1),(.01,1)`이다. `IMPVEL`은 전역 X 방향이며 `skew = 0`, `sensor = 0`, `frame = 0`, `Icoor = 0`, `Scale_x = 1`, `Scale_y = 1`, `Tstart = 0`, `Tstop = .01`이다. 외력, 접촉, 중력, 질량 스케일링과 전역 감쇠는 없다.

소변형 독립 기준식은 `epsxx=.1t`, 솔리드 응력 `diag(120t,40t,40t)MPa`, 셸 응력 `diag((1000/(1-.25^2))*.1t,그 값의 nu배,0)`다. 전단은 0이다. 변위는 오른쪽 `Uright=tmm`, 왼쪽 0이고 속도는 오른쪽 `Vright = 1mm/s`, 왼쪽 0이다. 응력 자체를 입력하는 시험이 아니다.

### 별도 층 전달 LAW2 N5 시험

같은 형상과 E 및 nu를 사용하는 독립 LAW2 시험이다. 매개변수는 `a=1000MPa,b=1MPa,n=1,c=0,epsdot0=1/s,eps_p_max=1e30,sigma_max=1e30`이다. `ICC = 0`, `Fsmooth = 0`, `Fcut = 0`, `Chard = 0`, `m = 1`, `Tmelt = 1e30`, `rhoCp = 1`, `Tr = 0`이며 셸의 `N = 5`다. 고항복 탄성 영역에서 5개 층의 값 전달을 검사하는 시험이며 LAW2 소성 적분 검증이 아니다.

필수 원배열은 `upper/lower`와 `layer__1..5`다. LAW1 N0은 `upper/lower+layer__1`만 요구한다. 실제로 발견한 배열만 순회해 모든 필수 배열이 있다고 판단하지 않는다.

두 시험의 출력 카드는 `/ANIM/DT 0 .0025,/ANIM/VECT/DISP,/ANIM/VECT/VEL,/ANIM/BRICK/TENS/STRESS/000,/ANIM/BRICK/TENS/STRAIN/000,/ANIM/SHELL/TENS/STRESS/ALL,/ANIM/SHELL/TENS/STRAIN/ALL,/ANIM/SHELL/TENS/STRESS/UPPER,/LOWER,/ANIM/SHELL/TENS/STRAIN/UPPER,/LOWER,/TFILE/4 .0025,/TH/TITLE`이다. 약칭 `/LOWER`는 앞선 같은 요소 계열의 완전한 카드 경로다. 실제 원덱에는 전체 경로를 쓴다.

당시 두 Starter는 오류 0개와 경고 0개로 끝났고 Engine은 정상 종료(`NORMAL`)했으며 460사이클을 계산했다. 실제 공간장 시간은 `fieldTIME=[0,.00251024,.00502048,.0075089,.0100191]`, 시간 이력은 `TH=[0,.002510241,.005020482,.007508895]`였다. 106열과 4행의 값이 모두 완전했다. 솔리드의 마지막 `xx≈1.20136MPa`, LAW2 셸 3층의 `xx≈1.06817MPa`였다.

사전에 정한 수치 허용 정책은 다음과 같다.

~~~text
stress abs(error)<=1e-5MPa+.01abs(expected)
U abs(error)<=1e-8mm+.001abs(expected)
V abs(error)<=1e-8mm/s+.001abs(expected)
rho0/rho, VOL/V0, 1+.1t의 일치는 rtol3e-6+atol1e-8.
~~~

각 실제 시간 t에서 독립 Hooke 식을 평가하고 명목 시간을 대입하지 않는다. 이 허용오차는 시험 자료의 수치 정책이며 실제 재료의 적합성을 입증하지 않는다.

### LAW33 관측과 짧은 Ka1 시간 간격 세분화 시험

당시 A 담당자의 Ka0/Ka1 원덱을 별도 사본에서 공식 Linux 프로그램으로 실행했다. 둘 다 Starter 오류 0개와 경고 0개였고 Engine은 모두 정상 종료(`NORMAL`)했으며 각각 18720사이클과 18790사이클을 계산했다. TH는 100행과 82열이 모두 완전했다. Ka0의 최종 실제 시간은 `.04950070`, `SX=-.1MPa`였다. Ka1은 `t=.04950116,SX=-.07701539MPa,rho=1.019368e-10,VOL=.981`이었다. 이 원덱 전체와 원출력을 복구 문서에서 추측해 생성하지 않는다.

A가 소스 구성법칙의 상미분방정식(ODE)과 독립 대조한 최대 응력 오차는 `Ka0=2.7082e-7MPa`, `Ka1=3.1231e-6MPa`였다. J의 최대 오차는 `5.10e-7`, 명령 변위의 오차는 `1e-8mm`였다. 추가 질량(`addedmass`)과 아워글라스(`HG`) 값은 모두 0이었다. F의 별도 `DOP853` 검산은 `Ka0≈2.7093e-7MPa`, `Ka1≈3.3790e-6MPa`였다. 이는 짧은 아핀 변형에서 소스 구성법칙을 확인한 수치 근거다. 일반 실험에 대한 적합성이나 제품 최적화 기능 완료를 뜻하지 않는다.

Ka1 DTIX 소스는 `OC-S engine/source/input/freform.F:577–595`다. `READ DTIN,DTMX`로 값을 읽는다. 최댓값이 음수(`max<0`)이면 오류이고 0이면 `1e20`으로 바꾸며, 입력값이 최댓값보다 커도(`in>max`) 오류다. 별도 기준 Engine에는 `/DTIX` 다음 줄에 `2e-6 2e-6`, 절반 시간 간격 시험에는 `1e-6 1e-6`을 사용하며 질량 스케일링은 없다. 0초부터 0.045초까지 0.0005초 간격의 고정 격자 91개에서 실제 TIME의 선형 보간을 사용하고 외삽하지 않았다. 표본별 허용오차는 `1e-5MPa+.005abs(oracle)`, 시간 간격 세분화 비교의 허용오차는 `1e-5MPa+.0025abs(half)`다. A의 실행과 F의 독립 검산에서 최대 세분화 차이는 `maxrefinement=7.56674e-6MPa`로 통과했다.

제한을 두지 않았던 원래 탐색 시험은 별도 이력이다. 이를 새 카드로 실행한 결과로 바꾸지 않는다. 장기 연속 시험의 시간 간격 세분화, 장시간 유지, 속도와 전단 시험, 물리 실험은 미실시다.

### 합성 수치 시험과 동일한 ResultRef

90도 회전해 국부 x가 전역 y로 향하는 삼각형(`shell101`)과 사면체(`solid202`), 절점 ID 11..17(`NODE`), 부품 ID 10/20(`PART`), 실제 시간 `TIME = .125/.250`을 사용한다. 셸 위층, 중간층과 아래층의 국부 `xx=10/5/-4MPa`, 솔리드 `xx=12`다. RP의 기존 `evaluate`에서 중간층의 국부 xx와 전역 yy가 모두 5MPa, 사용 가능한 위치의 최대 VM이 10MPa, 솔리드 VM이 12MPa임을 확인했다. 무변형 점은 `undeformedpoints=stored-U`임을 확인했다. 인위적으로 만든 이진 VTK이며 솔버의 물리 계산 결과가 아니다.

같은 결과 참조를 사용하는 시험은 `shell101/layer3/localxx/identity=5MPa`, `fit=false`, 사유 `SYNTHETIC_FIXTURE_NOT_PHYSICAL_EVIDENCE`로 설정한다. 보기, 조회와 검토에는 적격이다. 원실행 기록과 작업 기록은 인위적으로 만든 스키마 시험 자료이며 실제 `JobManager` 기록으로 쓰지 않는다. 이 문서에서 원래 기준 파일 바이트를 복구했다고 표현하지 않는다. 새 시험 자료를 만들면 새 파일과 해시로 등록해야 한다.

## D9. E/F 비판 처리, 실패 검증과 완료 판정

`E-06`의 프로젝트 버전 3/4 문제와 자식 및 부모 루트의 점유권 충돌은 D0/D5에서 수정했다. 부모와 자식에 별도 작성자를 추가하지 않는다. `F-07`에 따라 Tools의 실제 체크아웃, 마지막 변경과 동일 바이트, 실제 TIME, 파생 힘과 소스 빌드에 대해 어디까지 확인했는지 구분한다.

`F-11`: 과거 `verify_native.py`는 필수 층 목록 `requiredlayers`를 만들고도 실제 존재하는 배열만 순회했다. 따라서 3층 또는 모든 셸 응력이 빠져도 통과시킬 수 있었다. 반박 근거가 없어 기존 검사기를 기각했다. 개정 `validate_field`는 정확한 필수 집합(`exactrequiredset`), TIME 배열의 값 1개, 응력 텐서 배열의 2행×9성분, U와 V 각각의 12행×3성분, ID의 유일성, 부품과 요소 계열, ID와 기준 형상의 대응을 단언 검사한다. 이 검사기 수정으로 원래 해석 바이트나 생성기를 바꾸지 않았다.

당시 실제로 잘못된 자료를 넣은 7건을 모두 거부했다. 3층 삭제, 모든 셸 응력 삭제, `layer3→30` 이름 변경, 요소 ID만 맞바꾸기, 절점 ID만 좌우 맞바꾸기, TIME 누락, 서로 다른 값이 있는 합성 자료의 위층 값을 중간층에 대입하기다. 좌표, 점 배열과 연결 정보를 함께 바꾼 정상 순열은 통과했다. F가 소유한 사본에서 과거 반복문의 누락 반례 2개와 개정 검사기를 독립적으로 재실행했다.

균일한 막 변형의 원래 해석 결과는 모든 층 값이 같으므로 실제 두께 방향 위치를 독립적으로 입증하지 못한다. 층별 값이 다른 합성 자료는 선택 동작만 검증한다. 앞으로 실제 굽힘 기준 시험을 추가해도 이전의 수치 선택 검사를 물리 검증이었다고 소급해 확대하지 않는다.

기존 테스트에 구현할 완료 검사 항목은 다음과 같다.

| 검사 분야 | 완료 검사 항목 |
|---|---|
| 공용 계약 | 알 수 없는 스키마, 경로 이탈, 해시, 원본 소유자와 맥락 소속을 검사한다. |
| VTK | TIME 누락 또는 비유한 값, 중복 ID, 다른 요소 계열의 0, 텐서 성분 수, 필수 층 누락, 미상 재료 방향, 변형률 정의와 단위를 검사한다. |
| TH | TITLES가 없으면 실행 전달 전에 거부한다. 시간 중복 또는 감소, 행 너비 불일치, 실제 표본이 둘러싸지 못한 비교 끝점, 중복 헤더, 접미사만 맞는 충돌, 힘 자료 1행과 중복 시간을 검사한다. |
| 작업 수명주기 모사 | 표준 출력과 오류 분리, 종료 0이지만 비거나 잘못된 데이터셋, 원본 변경, 같은 키의 중복 요청, 검증 중 취소, 시간 초과 정리, 늦은 RP 준비 완료, 비정상 종료에 따른 중단, 미래의 알 수 없는 작업을 검사한다. |
| `SaveAs` | 유휴 자식을 연 상태의 부모 일반 저장, 유휴 자식이 열렸을 때 `SaveAs` 사용 중 처리, 활성 적합 작업의 사용 중 처리, 닫은 뒤 전체 참조 복사, 원본 삭제 뒤 재열기, 버전 `v1..4`와 선택 보존, 부모 CAS 충돌의 보류 기록을 검사한다. |
| RP | 원본은 수동 열기만 허용한다. 알 수 없는 필드, 해시, nonce, 사용 가능한 목록, 첫 화면 그리기 후 준비 완료와 닫기 종료 0, `Run/GZIP` 직접 메서드 차단, 추가 솔버 생성 없음, 다른 실행 재사용 없음을 검사한다. |
| 공급자 | 같은 불변 `ResultRef`를 같은 입력에서 같은 결과를 내는 실제 판독기로 읽었을 때만 수치 근거다. 도구 이름에 RP가 있다는 사실만으로 추출 성공으로 처리하지 않는다. |

참조 시험 재현은 독립 생성기, 원래 Starter/Engine, 공식 변환기, 공간장과 시간 이력 기준값, 합성 및 실패 검사 순서로 진행한다. 실행자는 정확한 소스, 실행 파일 SHA, 운영체제, 작업 디렉터리, `argv`, 입출력 SHA, 표준 출력과 오류, 요청한 카드와 실제 적용 카드, 실제 표본 시간과 허용오차를 실행 기록에 저장한다. 복구 후 원시 파일을 확보하지 못한 사실을 과거 실행 성공으로 감추지 않는다. 재실행한 뒤에는 새 실행 기록과 과거 이력을 구분한다.

완료 검사는 공용 DTO와 소유 관계 및 작업 수명주기에서 시작하고, 필요한 결과 형식의 `OC-ANIM-1/OC-TH4-1`을 검증한 뒤 각 소비자 흐름으로 나눈다. 재료 평가는 해당 필수 관측값과 잔차를, 전문가는 지원하는 같은 참조 조회를, RP 연결은 실제 화면 표시를 각각 확인한다. RP 기본 수동 연결과 선택적 정확 연결 수정판은 D10의 별도 기준을 따른다. 필요한 판독 프로필이 통과하면 해당 수치 기능을 구현할 수 있으며, 이론 자문이나 다른 계산기의 재료 평가를 RP 완료 뒤로 미루지 않는다. 수치 변환 완료만으로 정확한 RP 자동 연결까지 완료되었다고 표현할 수는 없다. 원래 결과, 보기, 적합 평가와 검토에 적격하다는 판정은 공학적 승인과 다르다.

아직 선정하지 않은 모델을 추가하려면 정확한 구성법칙, 소스, 라이선스, 상태, 단위, 물리량 정의, 카드, API, 관측값, 시간, ID, 기저와 연산자를 함께 정해야 한다. 독립 수치 시험과 물리 시험 자료, 실패, 저장과 취소의 완료 조건도 같은 묶음으로 추가한다. 등록부의 메타데이터만으로 임의 구성법칙을 계산할 수 있다고 선언하지 않는다.

## D10. 기존 실행·화면 계획과 후처리의 연결 및 전체 완료 기준

이 명세는 [기존 개선 계획](../../review/2026-10-23-codex-improvement-plan.md)의 P4 결과 판독, P5 같은 결과 전달, P6 저장과 재개를 확장한다. 기존 실행이나 내장 화면을 다시 완료 처리하는 작업이 아니다. 통합 작업 ID는 [구현 작업 연결표](implementation-map.json)를 따른다.

| 기존 항목과 새 작업 | 재사용할 부분 | 추가로 완료해야 할 사용자 동작 |
|---|---|---|
| P4, Prep N21/W3 → `POST01-04` | 기존 원덱 Starter→Engine 실행, 입력 사본, 로그, 종료·취소 기록과 `JobManager/RunStore` | 실행 종료와 부분 판독 상태를 유지하면서 실제 A/T01의 수치 판독을 추가한다. 선정한 정확한 ID, 시간, 층, 기저와 단위를 독립 기준값으로 검사한다. 기존 실제 솔버 실행을 새 판독 완료로 다시 세지 않는다. |
| WBS-3의 내장 컨투어 → `POST01-04`의 소비자 연결 | 기존 FRD/VTU와 내장 경량 화면의 표시 기능 | 새 데이터셋의 형식과 의미를 검증한 뒤 지원하는 화면에 전달한다. CalculiX 내장 컨투어의 기존 완료는 OpenCourant 원형식 판독이나 RP 실제 표시 완료가 아니다. |
| P5의 외부 후처리 결과 전달 → `POST05-06` | 기존 프로그램 탐색·설정·외부 실행과 선택 실행 전달 틀 | 원본 RP 수동 연결로 정확한 준비 자료를 실제 화면에서 확인한다. 선택적 수정판은 별도로 정확한 자동 전달과 첫 화면 응답을 검사한다. 빈 앱 실행과 종료 코드 0만으로 결과 열기를 성공 처리하지 않는다. |
| P5의 전문가, WBS-4/#45 → `E0-2/E3-5` | 기존 독립 전문가 창과 첨부 검증·대화 저장 | `POST01-04`는 지원하는 원결과 참조를 제공하고, 전문가는 그중 도구가 지원하는 범위만 읽는다. #45 잔여와 실제 AI 검토는 전문가 명세의 담당 작업에서 끝낸다. 같은 수정에 POST와 E의 완료를 중복 부여하지 않는다. |
| P6, `STORE01/LINK01` → 후처리 저장·재개 | 공용 파일 참조, C8 잠금·CAS·다른 이름 저장과 프로젝트 관계 | 선택한 작업·데이터셋·프레임·필드·성분·선택 범위와 원본 계보를 저장한다. 이동 후 같은 보관 자료를 열고 외부 누락이나 손상을 구분하며 자동 변환·솔버 실행을 하지 않는다. |

### D10.1 설계 합의, 필요한 코드와 최종 인수를 구분한다

`CORE01`의 공용 참조·소유·단위 계약은 `POST01-04` 구현의 공통 선행이다. 변환 작업 수명주기, 공간장 판독과 시간 이력 판독은 같은 작업표 안에서도 각각 검증한다. A 결과를 보는 데 T01 이력 전체를 요구하거나, T01 이력을 읽는 데 RP 자동 연결을 요구하지 않는다. 변환기 설치가 없으면 그 프로필의 실행 가능 상태만 제한하며 원래 덱 실행은 막지 않는다.

계수 동정에서 LAW33 등의 솔버 시간 이력을 예측 곡선으로 사용하려면 해당 평가를 생성할 때 `OC-TH4-1`과 필수 관측값 검증이 필요하다. RP 표시나 애니메이션의 마지막 프레임, 평활화 값과 평균값으로 이 선행을 대신하지 않는다. 다른 계산기에서 이미 만든 고정 재료 평가를 전문가가 읽는 일에는 POST 전체 완료가 선행하지 않는다.

전문가의 이론·문헌 질문은 결과 변환과 독립적으로 구현하고 실제 공급자로 확인할 수 있다. 실제 결과 수치 질문은 그 선택 자료의 판독 프로필과 전문가 도구가 모두 지원할 때만 수치 근거를 반환한다. D2의 초기 점 조회 미지원 범위를 유지하며, POST에 셸·층·시간 이력 판독기가 생겼다는 이유만으로 전문가 초기 점 도구의 지원 범위를 확대하지 않는다. 이 조건은 [전문가 명세 8절](expert-implementation.md#8-기존-계획에서-이어받는-작업과-질문별-선행조건)과 같다.

### D10.2 RP 기본 연결과 선택적 수정판의 완료는 다르다

`POST05-06`은 기본 연결에서 제공할 기능과 선택적인 자동 연결을 구분해서 보고한다. 기본 연결은 설치된 원본 RP 1.0.0과 D6의 수동 절차를 사용한다. 사용자는 WB에서 선택한 실행과 실제 시간을 확인하고, 원본을 건드리지 않고 만든 독립 준비 폴더를 RP의 `Select project folder`로 연다. 원본 RP는 정확 프레임·필드 자동 CLI나 준비 완료 응답을 제공하지 않으므로, WB는 실제로 확인하지 않은 정확 열기를 자동 성공으로 표시하지 않는다.

기본 연결의 인수에는 RP 실제 창에서 준비한 VTK, 실제 TIME, 필드·성분·층·기저와 선언 단위를 확인한 기록이 필요하다. 원본 RP가 직접 표시하거나 확인할 수 없는 항목은 준비 명세 및 원파일과 대조하고 그 수동 한계를 남긴다. 필요한 선택을 할 수 없거나 원치 않는 변수 대체가 발생하면 그 선택의 실제 표시를 통과시키지 않는다. 수동 확인 기록은 관측한 사용자 동작과 화면 증거이며 수정판의 기계적 `ready` 응답을 대신 만들어 넣지 않는다. 원본 RP 실행 성공, 수동 표시 확인과 수치 정확성 검사는 각각 구분한다.

선택적 수정판은 독립 AGPL 저장소에서 D6의 `--capabilities-file`, 정확한 `--post-only --handoff ABS_JSON --receipt ABS_JSON`, 허용 파일 목록, 격리 설정과 모든 실행 차단을 구현한다. 실제 선택 결과를 읽고 첫 화면의 `EndEvent` 이후에만 `ready`를 기록해야 자동 정확 연결을 통과시킨다. 프로필 없음·불일치는 기본 수동 연결로 안내하며, 수정판 미설치가 기본 RP 사용이나 검증된 수치 추출의 사용을 막지 않는다. 수정판을 제공하지 않은 상태에서는 자동 정확 연결을 미구현으로 남긴다.

Windows에서는 설치 발견, 외부 실행, VTK 판독, 첫 화면 그리기, 사용자 선택과 종료를 각각 실제로 확인한다. Linux 실행, Qt offscreen 또는 모사 `receipt`는 해당 Windows 표시 인수의 대체 증거가 아니다. `run_deck.bat`, 솔버 재실행 또는 준비 자료 덮어쓰기로 보기를 복구하지 않는다.

### D10.3 같은 원본에서 수치·표시·근거·저장까지 이어지는 검사

다음은 최종 사용자 인수 절차다. 각 단계의 실행 여부와 실제 환경을 따로 남기며, 일부 통과를 전체 완료로 확대하지 않는다.

1. 저장소 밖 사용자 입력을 기존 P4 실행 경로로 실행하거나 이미 보관한 종료 실행을 선택한다. 정확한 실행과 입력 버전 및 원본 출력 해시를 확인한다. 실행하지 않은 입력을 실행 결과로 등록하지 않는다.
2. 사용자가 변환 계획의 원본, 프레임, 필드, 단위와 한도를 확인하고 명시적으로 제출한다. D3/D4의 실제 변환기를 실행하고, 검증 중 상태·취소·실패를 화면에 반영한다. 취소·실패·불완전 필수 출력은 성공 데이터셋이 아니다.
3. 같은 `ResultRef`에서 독립 기준값과 대조한 수치를 읽는다. 원래 ID와 실제 TIME, 층, 기저, 물리량 정의 및 단위가 일치하는지 확인한다. 원본 스냅숏과 변환 입력 및 출력 해시는 유지한다.
4. 공간장 결과는 D10.2의 기본 RP 수동 경로로 실제 표시한다. 선택적 수정판을 지원한다고 보고할 때는 같은 참조로 정확한 자동 전달과 첫 화면 응답도 별도로 확인한다. 시간 이력은 RP 공간장 표시기로 넘기지 않고 기존 곡선 표·시각화에서 실제 표본 시간을 확인한다.
5. 사용자가 전문가 근거로 선택하면 같은 고정 참조를 전달한다. 전문가는 지원하는 조회만 실행하고 수치, 판독 한계와 원본 계보를 보존한다. 실제 AI 답변과 독립 검토의 최종 인수는 E 작업이 맡으며 RP 창이 열렸다는 사실로 수치 근거 또는 검토 통과를 만들지 않는다.
6. 프로젝트를 저장하고 부모와 자식 창을 닫은 뒤 새 인스턴스로 다시 연다. 선택 작업과 데이터셋, 프레임 또는 시간 계열, 필드와 성분, 선택 범위·층·기저, 원실행 기록 및 대화의 관계가 같아야 한다. 뷰어 재실행은 사용자가 누르며 열기만으로 변환·솔버·AI를 호출하지 않는다.
7. C8의 잠금과 참조 복사 규칙으로 `SaveAs`를 수행한다. 실제로 보관한 스냅숏 자료는 원본 루트를 삭제해도 보관본만으로 다시 읽고 검사할 수 있어야 한다. 외부 문헌·외부 실행 파일 등 동봉하지 않은 관계까지 복구되었다고 주장하지 않는다. 해당 외부 경로, 식별값과 해시를 유지하고 누락·부분 자료·손상을 공통 가용성으로 표시한다. 없는 자료를 다른 실행이나 최신 프레임으로 바꾸지 않는다.

중간 단계 실패, 활성 작업 중 `SaveAs`, 자식 편집 창이 열린 상태, 부모 CAS 충돌, 원자료 변경, 판독 후 손상, 외부 RP 부재와 읽기 전용 보관본도 같은 사용자 경로에서 확인한다. 오류와 취소는 D5/C8 정본을 사용한다. 실패를 숨기기 위해 허용오차, 필수 배열, 원본 해시 또는 완료 조건을 낮추지 않는다.

이 문서 수정은 계획과 명세의 보완이다. 제품 코드, 실제 변환기, 실제 RP 화면이나 Windows 전체 인수를 이번 문서 작업에서 구현·실행했다고 보고하지 않는다.

### D10.4 독립 전문가가 직접 연 파일과 실행 계보의 경계

등록된 실제 실행이 있는 경우에만 `ResultRefV1`의 필수 `source_run_id/source_node_id/source_receipt_ref`를 채운다. 원실행 기록 없이 사용자가 직접 연 VTK나 텍스트 파일에 가짜 `NativeRun`, 노드 또는 실행 기록을 만들어 맞추지 않는다. 파일을 소비자 루트에 등록했다는 사실만으로 실행 계보가 생기는 것도 아니다.

그 파일은 공통 `kind=manual,payload={legacy_evidence_ref:ArtifactRefV1}`로 원바이트와 출처·선언 단위를 보존한다. 독립 전문가의 첨부·설명·문헌 질의는 허용한다. 검증된 판독 프로필이 있으면 실제 파일에서 읽은 허용 요약과 원문 위치가 있는 수동 수치 근거를 제공할 수 있다. 이는 전문가 내부 인용 형식이며 공통 `NumericResultV1/ResultRefV1`을 복제하지 않는다. 프로필이 없거나 값의 의미·단위·위치를 확인할 수 없으면 자료 한계와 판독 실패를 남기고 수치를 만들지 않는다.

초기 `evidence.query_field`는 실제 실행 계보가 있는 등록 `ResultRefV1`만 받으므로, 이 수동 파일에 대한 정밀 조회는 `unsupported`다. `evidence.describe`도 역할별 허용 요약만 반환하며 파일 전체를 우회 제공하지 않는다. [전문가 명세 9절](expert-implementation.md#9-역할별-읽기-권한-실제-수치-인용과-새-질문의-검색-판정)의 실제 원파일 해시·등록 위치·표시 텍스트 검증을 수동 요약에도 적용한다. 직접 파일 경로의 인수는 원실행 전달 검사와 별도로 수행하고 저장·재열기 뒤에도 수동 근거라는 지위를 유지한다.


## D11. 지금 구현할 순서와 기존 실행기의 확장 지점

D10은 외부 변환기 담당보다 먼저 기존 `caelab/jobs.py`의 단일 편집자에게 배정한다. 현재 `submit_native`는 NativeRun 전용이고 `launch`는 GUI 시작이므로 그대로 결과 변환 작업으로 취급하지 않는다. 기존 JobManager 안에 operation 실행·소유 프로세스그룹·stdout/stderr·취소/timeout·상태 snapshot의 공통 확장 훅을 구현한다. 공개 서비스는 D2의 `submit_result_conversion/operation_snapshot/cancel_operation`을 유지하고 구현이 그 훅을 호출한다. solver `job.json`과 파생 `operation.json`을 분리하며 새 manager/queue나 reader의 독립 Popen을 만들지 않는다. 기존 소유 프로세스 실행·정리 코드를 추출해 재사용한다.

실행행 D11 중 기존 VTU의 직접 수치 조회는 D02 뒤 독립적으로 구현·확인한다. 외부 변환기를 실행하는 경로만 D10의 작업 소유 코드 뒤에 연결해 원형식 변환과 field/history 판독을 등록한다. 실제 TH부터 LAW33 동정에 연결하고, field가 준비되면 D22 원 RP 수동 표시를 병행할 수 있다. 수동 경로도 실제 결과·TIME/층/필드의 표시 확인까지 수행한다. 선택 자동판은 동일 JobManager 소유 훅과 --post-only 전달·first-render receipt를 별도 인수한다. 수동 표시·실수치·문헌 자문·필수 합동 인수 D36을 선택 자동판 완료에 묶지 않는다.
