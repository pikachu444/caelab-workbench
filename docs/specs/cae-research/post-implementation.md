# D native 결과·공식 converter·OpenCourantRP 구현 명세 — 재구성본

복구 개정 R4-recovered, 2026-11-12. 이 문서는 실행환경 소실 뒤 공개 작업 문맥에서 재구성한 **새 문서**다. 과거 author part의 SHA256 2d3ae2e8bfdd0451cbceca9a9d97c832e51693de351f7bddcc61a22a3127b1ea와 byte-identical하다고 주장하지 않는다. 해당 원문과 작은 native CSV/VTK/T01/로그의 실제 bytes는 이 재구성에 포함되지 않는다. 아래 과거 실행 관측과 현재 복구 가능한 설계·생성 recipe를 구분하며 E/F가 실제 새 blob을 재검토해야 한다.

이 명세는 구현할 제품 계약과 reference acceptance 설계다. 새 Workbench operation/TH reader, MaterialStudy GUI, RP fork, 실제 provider loop나 Windows 제품 흐름을 구현했다는 보고가 아니다. 기존 JobManager/RunStore/ArtifactRefV1/ResultRefV1와 복구 공통 core의 C1 DTO/EvidenceEnvelope, C2 revision, C3 PredictionBundle/history, C4 assessment, C5 constraint, C6 units, C8 저장 규칙을 소비한다. 별도 queue/DB/공통 FE 모델을 만들지 않는다. 기존 원 덱 실행을 보정 지원 profile로 차단하지 않는다.

## D0. 실제 source/실행으로 반박한 옛 가정

1. LAW1 shell NIP5는 cb760c Starter에서 warning1084로 N=0으로 바뀐다. 기존 LAW1 모든 layer/top/bottom oracle를 폐기한다. 실제 LAW1 fixture는 effective N0이고, 별도 고항복 LAW2 fixture에서 N5와 layer1..5 transfer를 확인했다. 다른 법칙 물리 검증으로 확대하지 않는다.
2. /IMPVEL Scale_y=0은 이 profile에서 default1이다. zero amplitude로 왼쪽을 고정하면 강체 이동/zero stress가 나올 수 있다. 왼쪽 x는 /BCS, 오른쪽만 IMPVEL과 같은 초기 velocity로 정의한다.
3. actual TIME은 파일 번호, nominal ANIM/TFILE 간격이나 engine 종료 Tf와 다르다. 실제 Tf=.01에서 field 최종 TIME=.0100191, TH 최종=.007508895였다. ordinal을 물리 시간으로 사용하지 않는다.
4. T01→CSV를 영구 후속/unsupported로 남기지 않는다. OC-TH4-1은 선정 LAW33 foam의 stress6/rho/VOL/8 node U·V/actual time/global diagnostics를 위한 필수 구현 profile이다.
5. RP units.py는 label이다. unknown variable→U, middle→membrane, solver VM fallback은 수치 evidence adapter에 재사용하지 않는다. mixed-cell tensor의 다른 family zero padding도 유효값이 아니다.
6. thsol.F comment만으로 Lagrangian VOL이 초기 부피라고 결론내리지 않는다. 현재 source는 JLAG=1,rho>0일 때 WWA(11)=GBUF%VOL*PM(89,...)/GBUF%RHO로 current volume을 계산한다.
7. D-INTEG-01 정정: optional fork exact CLI는 **--post-only --handoff <json> --receipt <json>**다. 옛 계획의 --project <dir> 또는 다른 adapter 중 나중에 선택한다는 문장은 이 계약으로 대체한다. upstream에 이 CLI가 이미 있다고 말하지 않는다.
8. D-INTEG-02/E-06 정정: 기존 project.py SCHEMA_VERSION=3은 현 source 사실이다. post-only operation sidecar는 v3 compatible, 신규 material_study role 최초 저장은 v4다. 모든 reader/SaveAs는 v1/2/3/4를 읽고 선택 결과·관계를 보존한다. 무조건 “첫 단계 v3 유지”를 전역 gate로 쓰지 않는다.

## D1. 고정 source와 법적 경계

| ID | 고정 identity | 실제 접점과 한계 |
|---|---|---|
| RP-S | CAMLAB-UChile/OpenCourantRP cec23cbdd4058237e04fdcc7ecb06a3834578cae, pyproject 1.0.0 | 독립 PySide6/PyVistaQt 앱. AGPL-3.0-or-later. gui script opencourantrp→app.main. 현재 result 자동 CLI 없음 |
| OC-S | OpenCourant/OpenCourant cb760c206e346eff12161a2268b134ef9d48a4ef, latest-20261011 | native solver source AGPL. runtime/input reader/H3D/CFG의 별도 조건을 본체 조건으로 덮어쓰지 않음 |
| OC-C | OpenCourant/Tools actual local checkout 1b6bb9200ee7dca899e61c18bd982c3fb1c72fd1 | output_converters/anim_to_vtk/src/anim_to_vtk.cpp, th_to_csv/src/th_to_csv.c의 개별 permission notice는 MIT |
| OC-C-last | converter 두 파일 last change 3fc8c859f30582a1f71e156244987e1a14474d4c | 두 파일 bytes는 1b6 checkout과 동일함을 당시 해시 대조했다. actual extractor identity는 1b6으로 고정 |
| OC-TH | OC-S starter/source/output/th/hm_read_thgrou.F; engine/source/output/th/thsol.F, thnod.F | TH 코드/순서/응력축/값 정의. thsol global WA2:7와 local WA35:40 구별 |
| OC-FMT | OC-S hm_cfg_files의 PROP/prop_p1_shell.cfg, LOADS/impvel.cfg, OUTPUTBLOCK/th_bric.cfg/th_node.cfg, CARDS/eng_dtix.cfg | format 참고만. CFG 전체를 제품에 복사하거나 본체 라이선스로 일반화하지 않음 |
| WB-S | Workbench 104cdd6888a5554eecc8cf965c60ff1bd980b6da | JobManager, NativeRun/RunStore, input_snapshot, read_native_frame, atomic_json/FileLock 재사용 |

Converter source SHA256:
- anim_to_vtk.cpp: fb2c58a90ac90197aa9cf00c9a8954c638fc6ae267d46de20b94e4ef9cde9549
- th_to_csv.c: e680547aec4317db158313e5869f644046e8bbe73b8b0b68be18105c46687304

원 source URL은 https://github.com/OpenCourant/Tools/blob/1b6bb9200ee7dca899e61c18bd982c3fb1c72fd1/output_converters/ 아래 해당 경로다. solver source URL은 https://github.com/OpenCourant/OpenCourant/blob/cb760c206e346eff12161a2268b134ef9d48a4ef/ 아래 해당 경로다. OC-S tools/의 README는 별도 Tools repo를 가리킨다.

Release: https://github.com/OpenCourant/OpenCourant/releases/tag/latest-20261011
- 실제 다운로드한 Linux zip SHA256: 6cb419677ca36d73acb96218cc0cf5e4f7d50494d9c7812ba27935e3c0f8ec94
- 공식 페이지 Windows zip 선언 SHA256: 6d0ea8b43b68aa12f0a2787b339384a554978cbb65a5d3dbaf1e40598f02e7e6
- 실제 Linux starter SHA256: 0bbea37273656b270bea53cf756b115bcd5d9a8875a3c41283922a3b927719d1
- engine: 54cc9fc2a7cea6f1215afb6da9109d368650ff1fa7985625cb111f47d7e92be4
- anim_to_vtk: 9c97eee2991f6949d16477831cc684165f53b790f498cbef51b8e8fd67bf28ae
- th_to_csv: 704d647331ae4befd329d18b991d644f2af930abd117c5d204c23925f75fc8db

Release CI checkout의 Tools ref가 pin되지 않았으므로 release tag만으로 converter source SHA를 추정하지 않는다. 당시 pinned source를 gcc/g++로 빌드해 실제 A005/T01의 output이 official binary와 byte-identical임을 확인했지만 같은 compiler/build의 인증은 아니다. Windows EXE 실행/새 설치/실화면은 미실시였다. 복구 환경에서는 이 native 원 출력과 로그 bytes를 다시 얻지 못했으므로 새로 실행했다거나 원 output을 복원했다고 말하지 않는다.

RP source를 WB로 복사하지 않는다. numerical reader/operator는 독립 수식과 기존 NumPy/VTK를 쓰고 RP는 external consumer다. Optional fork는 독립 AGPL repo/설치이며 소스·수정·저작권·의존 notice를 보존한다. 본 작업에 solver/RP vendor 배포는 포함되지 않는다. archive/binary/venv/source checkout/build 폴더를 최종 Git/ZIP에 넣지 않고 URL+SHA+재현 명령으로 연결한다.

## D2. 책임·신규 API·공용 참조

| 소유 경로 | 구현할 책임 |
|---|---|
| caelab/artifact_refs.py | root 단일 소유 DTO/strict JSON. Qt/VTK/GUI/solver import 없음 |
| caelab/native/operations.py | plan_conversion, submit_conversion, operation_snapshot, cancel_operation, resolve_result_ref; 기존 JobManager/RunStore 소비 |
| caelab/native/converters.py | plan_oc_animation, plan_oc_history, validate_oc_outputs; fixed argv/copy/input/output limits |
| caelab/native/result_adapters.py | inspect_dataset/read_result/profile registry. 등록된 installed code만 실행 |
| caelab/result_readers/vtk_fields.py | lazy vtkUnstructuredGridReader/vtkXMLUnstructuredGridReader; exact ID/time/array/family/coverage |
| caelab/result_readers/opencourant_history.py | stdlib csv의 read_titles, bind_history_columns, read_th_csv, extract_history |
| caelab/native/rp_consumer.py | prepare_rp_handoff, poll_rp_receipt, 명시 vanilla/manual fallback |
| 기존 Workbench façade | 아래 공용 함수와 project/context membership. GUI가 직접 process를 소유하지 않음 |

공용 façade 시그니처:
- list_result_capabilities(project, run_id) → AdapterCapabilityV1[]
- plan_result_conversion(project, run_id, converter_id, frame_ids, options) → ConversionPlanV1
- submit_result_conversion(project, accepted_plan_hash) → OperationRefV1
- operation_snapshot(project, operation_id) → OperationReceiptV1
- cancel_operation(project, operation_id) → bool
- select_result_operation(project,node_id,source_run_id,operation_id,dataset_id) → None
- list_result_datasets(project,source_run_id) → DatasetManifestV1[]
- query_result(project,ref) → NumericResultV1
- open_result(project,ref,consumer_id) → ConsumerLaunchReceiptV1
- capture_result_evidence(project,ref,asset_root) → existing evidence envelope

project는 등록된 NativeProject/ProjectContext에서만 RunStore를 얻는다. caller root/path override는 금지한다. NativeRun/job.json을 파생작업 manifest로 바꾸지 않는다. 기존 core private _read_grid나 CalculiX-only _finish를 범용 reader인 척 import하지 않는다. 현재 native/radioss.py의 ASCII fixture ordinal time과 native/results.py _collect_dat·CalculiX parse_dat의 final-frame reader를 physical history로 재사용하지 않는다.

ArtifactRefV1 = {id,role,path,sha256,size,format_id,producer:{id,version}} 정본 그대로다. Domain operation/dataset manifest의 owner mapping은 artifact_owners[id]={scope:run|operation|consumer,id,source_run_id?}다. 이것을 EvidenceEnvelopeV1의 top-level field라고 해석하지 않는다. EvidenceEnvelopeV1 wire의 owner 위치는 공통 core C1 정본대로 **provenance.artifact_owners**다. path는 owner root의 POSIX 상대 경로이며 drive/UNC/..·NUL·symlink/reparse 탈출을 거부한다. operation scope는 source_run_id 필수, selection/post/material/evidence는 등록 consumer namespace를 쓴다. 새 scope/Result DTO를 만들지 않는다.

ResultRefV1의 top-level 정본은 schema_version,source_run_id,source_node_id,source_receipt_sha256,source_receipt_ref,operation_id,operation_manifest_sha256,dataset_id,dataset_manifest_sha256,source_artifacts,frame_id,field_id,component,selection,location,basis,measure,shear_convention,units,geometry_state,operator,coverage,eligibility다. Selection의 ID 목록은 frozen artifact로 보존한다. source receipt는 준비 때 exact bytes copy이며 현재 job.json의 mutable/stale/UI hash로 대체하지 않는다. 모든 근거 소비자는 동일 source/ref hash를 검증한다.

EvidenceEnvelopeV1은 공통 core C1(blob b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac)을 실제 읽고 따른다. Wire는 {schema_version:1,id,kind,content_sha256,provenance,availability,payload}이며 kind enum은 native_run|manual|calibration|document_chunk다. 별도 kind=result를 만들지 않는다. Native 유래 ResultRef는 kind=native_run,payload={legacy_evidence_ref:ArtifactRefV1,source_run_id,source_node_id,derived_result_ref:ResultRefV1|null}에 둔다. Payload와 derived ref의 run/node membership을 검증한다. legacy_evidence_ref는 기존 capture descriptor의 frozen JSON bytes이며 metadata/text/files와 stored_path를 자동 rewrite하지 않는다.

provenance={producer:{id,version},sources:[{source_kind,source_id,source_revision,url}],artifact_owners:{artifact_id:{scope,id,source_run_id?}}}. source_kind는 native_run|manual|calibration|official_source|paper|solver_manual만 사용한다. 당시 문서 예시에서 owner를 단순 envelope 필드로 줄여 쓴 표현은 EvidenceEnvelope의 wire 이름이 아니다. availability={state:available|partial|source_unavailable|corrupt|unsupported,reason_codes:[],missing_artifact_ids:[]}다. 모든 union record는 additionalProperties=false,필수키/허용null/finite JSON 검증이다. Envelope content_sha256은 자기 필드만 제외한 전체 provenance/owners/availability/payload의 canonical JSON SHA256이며 원 legacy descriptor hash와 다르다. 원 자료 미회수이면 source_unavailable로 표현하고 known hash/URL/identity를 보존하되 모델에게 실제 파일 bytes가 제공됐다고 표시하지 않는다.

내부 HistoryColumnBindingV1은 공용 결과를 복제하지 않는 metadata다:
{schema_version:1,column_index,raw_header,normalized_header,group_title,output_type,entity_id,entity_title,variable_code,source_titles_pair_index,quantity,component,measure,basis,unit,operator}.
column_index는0-based, titles pair index는 global/part/subset을 제외한 TH group 순번이다. Dataset field provenance에 넣어 content hash에 포함하며 다른 CSV에 재사용하지 않는다.

Root C3: DatasetManifestV1.dataset_kind=field|history. Field는 frames[]/series=[], history는 frames=[]/series[{series_id,coordinate_ref:ArtifactRefV1,row_count,source_row_index_ref:ArtifactRefV1,field_ids}]다. History ResultRef.frame_id는 등록 series_id(예 history:all)를 참조하고 물리시간'all' frame을 만들지 않는다. NumericResultV1.sample_coordinates는 실제 source time[]이고 values/mask/counts를 함께 검증한다. geometry_state=unknown, 최초 history operator는 curve_extract만. row snapshots/series reduction은 미지원이며 RP field consumer는 history를 거부해 기존 curve 표/시각화 consumer로 전달한다.

Expert 첫 query가 point-only이면 D의 shell/IP/layer synthetic ref는 evidence.describe만 허용하고 query는 unsupported로 반환한다. 같은 operator 요청은 frozen base ref를 보존하고 dataclasses.replace 상당 경로에서 operator만 변경한 request ref를 만든다. values→identity, min/max/mean/norm은 등록 operator/version/shape/unit gate를 통과해야 한다. trace에는 base hash와 request ref hash를 둘 다 남긴다.

## D3. OC-ANIM-1 변환 계획과 판독

입력은 OC-S A### 또는 A###.gz이며 output은 legacy ASCII VTK3.0 UNSTRUCTURED_GRID다. stdout byte mode 보존과 output format ASCII는 다른 개념이다.

1. RunStore inventory에서 선택한 정확 run/A artifact/hash를 고정한다. 현재 folder glob로 다른 run을 주워오지 않는다. inputs/inA001 같은 짧은 ASCII basename에 bytes copy하고 원 path·name·SHA 계보를 보존한다.
2. gzip이면 raw gz와 해제 bytes 각각 ref/hash를 둔다. streaming chunk마다 cancel, 기본 해제 cap5GiB(변경은 accepted plan에 고정), 부분 해제는 converter input이 아니다.
3. argv=[absolute_converter_exe,"inA001"],cwd=operation input staging,stdin=DEVNULL,shell=False. stdout은 outputs/<source_name>.vtk.part로 byte stream,stderr는 별도 stderr.log다. stdout와stderr를 섞지 않는다.
4. return0/nonempty만으로 성공하지 않는다. header·dataset type·point/cell>0·TIME finite scalar·IDs/arrays/component count·topology·family masks·원 snapshot geometry/U 관계를 검증하고 source SHA를 재확인한 뒤 atomic rename/dataset commit한다. validating 중 취소면 completed가 아니다.
5. source FIELD TIME는 native a_time이다. CYCLE는 converter에서 hardcoded0이므로 실제 cycle oracle가 아니다. NODE_ID는 point, ELEMENT_ID/PART_ID는 cell이다. 실제 fixture cell order는 shell202→solid101이므로 index순서가 아니라 ID로 결합한다.
6. 2DELEM arrays에는 다른 family용 zero tensor가, 3DELEM에도 반대 family zero가 들어간다. 해당 native family mask만 valid다. wrong-family selection은 OBSERVABLE_MISMATCH이며 zero로 채우지 않는다. native ID/family namespace와 uniqueness를 검증한다.

| 원 배열 | association/성분 | 지원 의미 |
|---|---|---|
| Displacement/Velocity | point/global xyz exactly3 | explicit component와 Euclidean norm. 동일 단위/known orthonormal basis만 |
| 3DELEM_Stress | solid row-major tensor9 [xx,xy,xz,yx,yy,yz,zx,zy,zz] | 검증한 Isolid1/global Cauchy stress/VM |
| 2DELEM_Stress_(upper)/(lower) | shell/current element-local tensor9 | 정확 upper/lower. NIP와 별도 location |
| 2DELEM_Stress_(layer__n)_ | 실제 출력 n | LAW1 N0의 실제 layer1, LAW2 N5의1..5. 없는 layer3/middle을 membrane으로 대체하지 않음 |
| solid/shell Strain | 실제 배열/component | 측도·shear source가 미확정이면 diagnostic query만, fit=false/MEASURE_UNKNOWN |
| 발견한 기타 배열 | 원 association/descriptor | 이 두 oracle 밖 quantity는 declared/unverified; 이름만으로 fit승격 없음 |

Tensor symmetry tolerance는 abs1e-8+rel1e-6이며 실패를 조용히 symmetrize하지 않는다. VM = sqrt(.5*((xx-yy)^2+(yy-zz)^2+(zz-xx)^2)+3*(xy^2+yz^2+xz^2)). Full tensor와 scalar VM 지원은 별개다. shell max/min은 실제 비교 위치 IDs 목록만의 범위이며 모든 두께 최대라고 쓰지 않는다. 원 association을 point smoothing/IP extrapolation으로 자동 변경하지 않는다.

Geometry는 deformed points와 reference→current U를 함께 가진다. reference=points-U, display(a)=reference+a*U. 두 번 warp하거나 원 배열을 수정하지 않는다. Triangle current local axes: x=unit(P2-P1),n=unit((P2-P1)×(P3-P1)),y=n×x. Quad n=unit((P3-P1)×(P4-P2)),x를 unit(P3-P1)+unit(P2-P4)에서 plane에 투영해 정규화,y=n×x. max edge에 대한 방향norm 비<1e-12는 UNSUPPORTED_BASIS다. R rows가 local axes이면 global=Rᵀ local R. Element축과 orthotropic material축은 같지 않다. orientation payload/hash가 없으면 material basis unsupported. Aligned native와90도 synthetic은 다른 증거이며 synthetic을 native rotated-shell physics로 승격하지 않는다.

mm–Mg–s 선언의 SI scale: length.001,mass1000,time1,stress1e6,force1,energy.001,density1e12,velocity.001이다. source 단위 미선언은 unknown/scale=null이며 RP label을 수치 환산의 증거로 쓰지 않는다. UI표시단위 변경은 원 bytes/units를 바꾸지 않는다.

## D4. OC-TH4-1 T01→CSV와 재료 관측

필수 선정 범위: OC-S /TFILE/4+/TH/TITLE, Lagrangian one-point HEX8(Isolid1), BRIC stress6/DENS/VOL/IE/BULK, NODE DX/DY/DZ/VX/VY/VZ, global energy/mass/time. LAW33 Ka0/Ka1 물리는 materialA의 법칙/oracle가 판정하고 transfer profile은 독립 LAW1/Law2와 실제LAW33 rawdeck로 확인한다. TFILE1–3/restart stitching/arbitrary integrated-point averaging/native reaction force 검증은 이 profile에 포함되지 않는다.

Output cards:
- /TH/BRIC/<groupid>,다음줄 group title BRIC,다음줄 %-10s code칸들 SX SY SZ SXY SYZ SXZ DENS VOL IE BULK,다음줄 %10d +10spaces+%-80s entity title.
- affine ID101/entityhex,foam ID1/entityfoam처럼 frozen request의 title/ID를 결합한다.
- /TH/NODE/<groupid>,titleNODE,%-10s code칸 DX DY DZ VX VY VZ,각 row %10d%10d%-80s nativeID/skew0/n<id>.
- Engine /TFILE/4 다음줄 explicit dt, /TH/TITLE.

공식 th_to_csv는 argv=[absolute_exe,"inT01","history"],cwd=input staging,stdinDEVNULL,shellFalse다. stdout/stderr는 log이고 output파일 history.csv를 작성한다. inT01_TITLES도 exact frozen bytes로 같은 basename에 복사한다. C char[100] filename buffer 때문에 긴 절대경로/원 basename을 argv에 넣지 않는다. optional 출력stem에 .csv를 붙여 넘기면 .csv.csv가 된다. 짧은 ASCII 이름과 원 한글/공백 프로젝트 경로의 관계를 source mapping으로 보존한다.

T01/TITLES snapshot이 없으면 dispatch전 UNSUPPORTED_OBSERVABLE이다. 출력카드를 원run에 넣거나 solver를 다시 실행하지 않는다. TITLES 없는 var n/미확정 impulse flag 경로는 보정에 쓰지 않는다. 추가 output은 Prep 명시 수정→새 native run이다.

read_titles는 ASCII paired lines(type int width10,variable code width10)의 순서와 raw bytes/hash를 보존한다. read_th_csv는 stdlib csv.reader(newline="")로 quoted raw_header를 읽는다. header/global 첫열 exact time,각data row 같은column수,finite값,time strictly increasing 필수다. 정규화는 trim+중간 공백1개이며 raw_header/column_index를 함께 보존한다. 알려지지 않은 NO NAME을 추정 quantity로 바꾸지 않는다.

실제 normalized header는 BRIC 101 hex SX,BRIC 101 hex DENS,NODE 12 n12 DX였다. TITLES type1(Solid)/type0(Node),pair순번,frozenoutputrequest,entityID/title을 모두 대조한다. 요청 code 순서를 CSV순서라고 가정하면 안 된다. 실제 canonical order는 SX SY SZ SXY SYZ SXZ IE DENS BULK VOL이다. suffix SX만 매칭하거나 title숫자를 임의nativeID로 해석하지 않는다. duplicate normalized header는 INVALID_DATASET이며 합치지 않는다. arbitrary title 입력은 explicit binding이 검증돼야 한다.

TH stress [SX,SY,SZ,SXY,SYZ,SXZ]→tensor6[xx,yy,zz,xy,yz,xz],global Cauchy/tensor shear다. thsol WA2:7은 회전 formulation에서 global 변환 후 값,LS*는 local 별도다. Isolid1 한점만 IPpoint로 읽으며 fullintegrated 평균을 IP1로 붙이지 않는다. Shell TH층은 이 profile 미지원이다.

LAW33필수: stress6(global,Cauchy),DENS,모든8node U/V,actualtime. J=rho0/rho, current VOL/V0와 nodal λxλyλz의 전달일관성을 검사한다. c=1-J compression-positive,eps_v=J-1,λx=(L0+mean(Uright)-mean(Uleft))/L0. IE는 specific internal energy per initial volume이며 globalINTERNAL ENERGY와 섞지 않는다. kinetic/internal/hourglass/addedmass threshold는 A의 explicit assessment policy가 소유한다.

실제 time 순서를 유지한다. strain축 global sorting/nominaltime삽입/t0발명/endpointhold/extrapolation은 금지한다. 실험 comparison time range를 actual sample이 bracket하지 못하면 COVERAGE_INCOMPLETE다. 마지막 comparison 뒤까지 run duration을 명시해 coverage를 얻어야 하며 /TFILE/4가 exactnominalgrid를 보장한다고 가정하지 않는다. Native V는 explicit half-step일 수 있어 ramp에서 같은 currentTIME command V와 자동비교하지 않는다. command적분 변위와 actualU의 비교를 보존한다.

반력/접촉/section force는 별도 확장이다. Official csvFileWrite는 TITLES/type으로 impulse를 분류하고 endpointsone-sided/interiorsecant:
F0=(I1-I0)/(t1-t0),
Fj=(I[j+1]-I[j-1])/(t[j+1]-t[j-1]),
Flast=(Ilast-Iprevious)/(tlast-tprevious).
Irregular-grid에서 exact instantaneous derivative가 아니다. REACX를 native force원값으로 표시하지 않는다. operator official_impulse_secant/version=converterhash,params(endpoint/interior/time_source)를 보존한다. 비교실험도 같은 measurementoperator를 명시해야 한다. ≥3samples,strictTIME,완전titles가필수. Native반력oracle미실시이므로fit=false REACTION_FORCE_NOT_VERIFIED. Synthetic t[0,1,3],I[0,1,9]→F[1,3,4]와1row/duplicateTIME거부는 알고리즘 수락이며 실제 반력 검증은 아니다.

## D5. operation 저장·CAS·취소·캐시

RunStore.root 아래:
- source_run_id/job.json: 기존 solver 기록
- .derived/source_run_id/operation_id/{operation.json,dataset.json,inputs/,outputs/,operation.log,stderr.log}
- .post/launch_id/{handoff.json,data/,receipt.json}
- selected-derived/sha256(node_id).json
- selections/selection_id/payload.json

OperationReceiptV1은 schema1,id,operation_kind convert|extract,source_run_id,source_receipt_ref/sha,source_input_revision,source_artifacts,adapteridentity/codehash,executable(path_audit/version/hash/OS),planhash/cachekey,created/started/finished,state,parse_state,stage,progress,returncode,error,warnings,outputs,dataset_ref,reused_from_operation_id를 갖는다. Artefacts는 공용 ArtifactRef만 쓴다. exactargv/cwdrelative/stdinrole/stdouttarget/stderrtarget/env_names/toolversion을 audit하되 secret값은 저장하지 않는다.

source receipt는 준비시 exact bytes copy다. Reopen은 frozenbytes/artifactSHA와 현재run의 immutableidentity(id,node,solver,inputrevision,snapshot원/실행inputSHA/nativeoutputSHA)를 대조한다. 현재stale/livepath/UI를 fingerprint에 넣지 않는다. frozenreceipt 안 옛절대경로는 audit문자열이며 resolver가 열기경로로 쓰지 않는다. Historicalread는 stale와corrupt를구별한다.

Plan은 실행0이며 exe/hash/version/host,inputSHA,frames/fields,options,unitprofile,outputstorageestimate를 고정한다. Submit재검증에서 달라지면 STALE_PLAN. 등록된 installedadapter 외 JSON shell/code loading0. terminalsource run만입력으로 쓰며 callerrootoverride0.

queued→preparing→running→validating→completed. 모든copy/readchunk/stage에cancelcheckpoint. return0뒤 validating취소도cancelled이고dataset등록0. managerlock에서첫terminal만수용. timeout기본120s(준비/검증포함),storage/decompressionlimits는acceptedplan고정. Windows JobObject KILL_ON_JOB_CLOSE에 suspendedchild할당후resume,Unixnewsessionprocessgroup TERM5s→KILL5swait. cleanup실패 PROCESS_CLEANUP_FAILED를 clean cancel로 속이지 않는다. recordedPID는diagnostic이며재열기adopt/kill0. crash로unlockedactive→interrupted,.part는격리,자동재실행0.

Cache identity는 immutable sourcefingerprint/inputrolesSHA+converterexe/hash/version+reader/profile/operators+frames/fields/options+unit/basis/measure. mutable documentCAS/absoluteauditpath/stale/camera/theme는제외한다. hit도파일과eligibility재검증. cancelled/failed/interrupted/requiredpartial는성공hit아님. 여러operation보존,latest/differentrun자동대체0.

Post-onlyv3/newmaterialrolev4/read1–4규칙은D0에고정했다. 공통 core C8 writer/SaveAs 규칙을 소비한다:childopen→close lifetime study.lock,child단일writer,childsave는자기lease재획득없이parentCAS비차단갱신. normalparentSave는projectlock/CAS이며closure복사아님. childcommit뒤parentCAS충돌은연구롤백없이ASSOCIATION_UPDATE_PENDING이다. C8의 child data pending-association/<event_id>.json에 parent identity/relative association,expected parent document revision,committed child document/content revisions,role payload,created_at의 불변 record를 atomic 저장한다. Reconcile은 같은 parent/expectedrevision에서만 비차단 CAS를 재시도하고 충돌을 자동병합하지 않는다. 성공은 별도 resolution event로 닫고 원 record를 보존한다. ParentSaveAs는childroots snapshot→정렬childleases→projectlock→RunStorelease를timeout0으로획득하고projectrevision재검사. 하나라도실패면모두해제·무변경. idlewritablechild도STUDY_OPEN_FOR_EDIT이며닫은뒤SaveAs. activeoperation/viewerstagingwriter도beforecopybusy.

Standalone material SaveAs는 C8의 별도 트랜잭션이며 destination parent journal/staging을 쓴다. Read-only source에는 lock/journal을 생성하지 않고 manifest+closure/runreceipt hashes의 before/after 낙관 copy로 SOURCE_CHANGED_DURING_COPY를 검출한다. Destination data rename 뒤 destination study.lock 획득·closure재검증·manifest commit을 끝낸 뒤에만 source lease를 해제하고 writable binding을 전환한다. commit후 cleanup실패는 유효 destination을 지우지 않고 committed_cleanup_pending이다. Parent 완료closure를bytescopy하고새root에서refs/hash/sourceidentity재검증후projectatomiccommit. source root삭제후archive만으로frozenhistory/field/review재열기필수. unknownsidecarschema는read-onlyunsupported,.derived없음은empty,자동삭제/수정/재실행0. 새study의document_revision은mutablemanifestCAS,content_revision은연구내용불변hash다. post는constraint/assessment DTO를별도로만들지않는다. requiredtrain실행/measure/coverageintegrity오류는objective없음,정상고오차는finitefitloss,finiteINFEASIBLE와requiredconstraint미계산을구별한다.

오류정본: PROGRAM_UNAVAILABLE,UNSUPPORTED_CAPABILITY,STALE_PLAN,SOURCE_CHANGED,HASH_MISMATCH,PATH_ESCAPE,CONVERTER_EXIT,EMPTY_OUTPUT,INVALID_DATASET,MISSING_TIME,MISSING_ID,UNSUPPORTED_FIELD,UNSUPPORTED_BASIS,MEASURE_UNKNOWN,UNIT_UNKNOWN,PARTIAL_REQUIRED_FIELD,TIMEOUT,CANCELLED,PROCESS_CLEANUP_FAILED,STARTUP_FAILED,HANDOFF_MISMATCH. Materialdispatch지원없음은UNSUPPORTED_OBSERVABLE,실행후required누락COVERAGE_INCOMPLETE,wrongassociation/type/id는OBSERVABLE_MISMATCH. Details에field/selection/row/artifact/reason/availablelist. 오류→0/다른변수/다른run/lastframefallback0.

## D6. 사용자흐름·RP수동과optionalexactfork

부모Qt에서run→operation/dataset→actualtimeframe→field/component→selection/layer/basis를명시선택. Historicalrun은표시하되verifiedhash이면조회가능,corrupt는stale로가장하지않는다. 열기만으로solver/converter/LLM0. 변환계획은실행0,변환은기존JobManager,RP보기는준비dataset보기다. 내부nonce/hash를기본화면에나열하지않고사용자에게대상/진행/실패/복구를보여준다.

Vanilla1.0.0은manual only:installedRP일반실행→Selectprojectfolder→독립poststaging선택. 정확한starter/enginepair또는combineddeck와NAMEA숫자.vtk사본이필요하다. find_deck_name/list_vtk실제규칙을따르며folderlastframe/userfield선택은수동한계다. QSettings는OpenCourantRP/OpenCourantRP전역project/units를기억한다. vanilla에지원없는argv를보내거나빈앱실행을exactresultopen성공으로표시하지않는다.

RP run_deck.bat는rp_run삭제/StarterEngine재실행/H3D제거·ANIM추가가있으므로보기·기존결과변환에절대호출하지않는다. gzanim.convert도자체blockingprocess/중도cancel없음이어서WBworker에import하지않는다. A파일없는H3D-only는명시불가,Prep출력수정과새run이별도다.

Optionalfork는verifiedconversion/수치query의선행조건이아니다. 별도AGPLrepository에cli.py/handoff.py신규,app.pyentry/MainWindow/guards,project.pyexactfilelist,buildlauncher/pyprojectentry를변경한다. viewer/variables/PyVista는재사용하되exactmodefallback을금지한다.

- --capabilities-file PATH:Qtimport없이profile wb-rp-post1/schema/version JSON을atomicwrite후exit0.
- exactargv: --post-only --handoff ABS_JSON --receipt ABS_JSON.
- unknown/missing/mismatchedprofile은manualfallback표시,vanilla에flags전송0.
- handoff:launchid/nonce/fullResultRef+hash,datasetmanifesthash,exactrelativefileallowlist+SHA,requestedframe/actualTIME/field/component/layer/basis/units/geometry/selection,receiptpath.
- staging .post/launchid/data에bytescopy하며RP는source root나arbitraryURI를추가로읽지않는다. oneframe도allowlist를사용해globlast를대체한다.
- MainWindow(post_only=True,startup_handoff=...)는launchisolatedQSettings,기존project/lastfile/units/variable복원0. declaredunits만라벨,unknown빈값.
- projectchooser/Run/Stop/GZIP는UI와_run/_stop/_set_gz_pending/_maybe_ask_gzip/_ask_gzip직접메서드모두guard.
- exactunknownfield는exit4,Ufallback0. missingmiddle→membranefallback0. smoothoff/originalassociation/deformationscale1로초기화. 후속사용자표시변경은initialselectionreceipt와분리.
- snapshot/video는사용자명시별도경로,source/stagingoverwrite0.

Receipt순서:schema/nonce/hash/containment검증→actualVTKread→exactframe/field/layer/basis→first-renderEndEvent→atomicready. Ready는launch/nonce/refhash/actualTIME/counts/selectedarray/component/layer/basis/availablefields/coverage/monotonicseq를보존한다. normalclose는closedexit0+ready_seen/ready_at유지. load전close는ready아님. Popen만으로opened성공아님. invalidschema/CLIexit2,missing/hash3,unsupportedfield/basis/layer4,QtVTKstartup/load5. 실패receipt+noninteractiveexit이며MessageBox영구대기0.

WBreadydeadline30s설정가능/UIblocking0. lock안firstterminal,timeout후늦은ready무효. ready전cancel은해당launchprocessgroup만중단하고실패staging보존. solver/conversion상태불변. ready후사용자close는closed이며solver취소아님. 재열기는새launch/nonce,닫힌창자동재실행0. Ready는수치oracle/Windows실화면의대체아님.

## D7. Windows·빌드·환경

RPsource최소Python3.10+,PySide6>=6.5,PyVista>=.43,PyVistaQt>=.11및pyproject기타의존성을정확lock/notice로기록한다. 설치·startup·resultload는별도상태다. upstreamtools/build_exe.py는PyInstalleronedir/windowed와solver자동fetch를한다. 이를WBbuild필수로실행하지않는다. view-onlyforkbuild는solver미동봉profile/전용viewselftest를두고upstream--selftest의solver/video/mathtext검사와구별한다.

NativeWindowsconverter는exec/anim_to_vtk_win64.exe,th_to_csv_win64.exe. configuredOpenCourantroot하나의exe/runtime를사용한다. PATH선택runtimehm_reader/win64+intelOneAPI_runtime/win64,RAD_CFG_PATH=선택root/hm_cfg_files,KMP_STACKSIZE400m. Externalenvironment는WBbundleQt/PythonPATH·PYTHONPATH·QT_PLUGIN_PATH·QML2_IMPORT_PATH오염을제거/명시하고secretenv값을저장하지않는다. DLLloader오류/프로그램발견/정상종료/판독성공을독립표시한다.

Linuxsourceconverter선택빌드:gcc -DLINUX th_to_csv.c,g++ -DLINUX anim_to_vtk.cpp. WindowsVS2022x64NativeTools원build는cl /Fe... th_to_csv.c,cl -DWIN32 /Fe... anim_to_vtk.cpp Ws2_32.lib. Solver전체oneAPI/readerbuild는별도task다. WSL은별도등록transport/취소/전송검증전에는nativeWindows지원으로표시하지않는다.

Windows인수:한글/공백/긴원경로와짧은inputbasename,stdoutstderr분리,childcancel/timeout/earlyclose,기존QSettingsproject오염,unknownfield,GPU/VTKfirst-render,DPI100/200,SaveAs후source삭제재열기. Processfake는lifecycle,synthetic은format/operator,realnative는선정profile,OS실화면은별도축이다.

## D8. 실제 reference 관측과 고정 fixture recipe

환경 소실 전에 아래 actual reference를 수행했고 E/F가 별도로 검산했다. **이 문서는 원 CSV/VTK/T01/로그 bytes를 재구성하지 않는다.** 새 구현자는 공식 package와 생성 recipe로 다시 실행해 raw artifact/SHA/OS/argv를 저장해야 한다. 다음 숫자는 당시 공개 보고에 남은 관측과 사전 numerical policy이며 새 실행 결과가 아니다.

### Affine LAW1 N0

단위 mm–Mg–s, E=1000MPa, nu=.25, rho=7.8e-9Mg/mm3, Tf=.01s이다.

- HEX101/part10은10mm cube다. nodes11..18은 차례로 (0,0,0),(10,0,0),(10,10,0),(0,10,0),(0,0,10),(10,0,10),(10,10,10),(0,10,10)이다.
- 별도 QUAD202/part20은 nodes21..24=(0,0,20),(10,0,20),(10,10,20),(0,10,20), thickness1mm다.
- Solid property는 Isolid1/Ismstr4/Iale0, qa1.1/qb.05/h.1, lambda0/mu0다.
- Shell은 Ishell24/Ismstr4/Ish3n2/Idrill2/Ipinch0,Hm/Hf/Hr/Dm0,Dn.015,N0,THICK1,Ashear5/6,Ithick2,Iplas0,Ipos0다.
- Shell N row의 fixed widths는 N10+blank10+THICK20+ASHEAR20+blank10+Ithick10+Iplas10+Ipos10이다.
- 모든 node의 y/z translation과 shell rotation은0이다. left x는 BCS0, right vx는 IMPVEL1mm/s와 같은 initial velocity다. Left IDs는11,14,15,18,21,24, right는12,13,16,17,22,23이다.
- Constant velocity FUNCT=(0,1),(.01,1), IMPVEL은 global X/skew0/sensor0/frame0/Icoor0/Scale_x1/Scale_y1/Tstart0/Tstop.01이다. 외력·접촉·중력·mass scaling·전역 damping은 없다.

작은변형 독립 oracle는 epsxx=.1t, solid stress diag(120t,40t,40t)MPa, shell stress diag((1000/(1-.25^2))*.1t,그 값의 nu배,0)다. Shear는0, Uright=tmm/left0,Vright1mm/s/left0이다. Stress 자체를 입력하는 시험이 아니다.

### 별도 layered LAW2 N5

같은 geometry/E/nu를 쓰는 독립 LAW2 fixture다. a=1000MPa,b=1MPa,n=1,c=0,epsdot0=1/s,eps_p_max=1e30,sigma_max=1e30,ICC0/Fsmooth0/Fcut0/Chard0,m1/Tmelt1e30/rhoCp1/Tr0이고 shell N5다. 고항복 탄성 영역의5layer transfer fixture이며 LAW2 소성 적분 검증이 아니다.

필수 native arrays는 upper/lower와 layer__1..5다. LAW1 N0은 upper/lower+layer__1만 요구한다. 실제 발견한 배열만 순회해 completeness를 판정하면 안 된다.

두 fixture 출력카드는 /ANIM/DT 0 .0025,/ANIM/VECT/DISP,/ANIM/VECT/VEL,/ANIM/BRICK/TENS/STRESS/000,/ANIM/BRICK/TENS/STRAIN/000,/ANIM/SHELL/TENS/STRESS/ALL,/ANIM/SHELL/TENS/STRAIN/ALL,/ANIM/SHELL/TENS/STRESS/UPPER,/LOWER,/ANIM/SHELL/TENS/STRAIN/UPPER,/LOWER,/TFILE/4 .0025,/TH/TITLE이다. 약칭 /LOWER는 앞선 동일 family의 완전한 card path를 뜻하며 실제 raw deck에서는 전체 path를 쓴다.

당시 두 Starter는0error/0warning,Engine은 NORMAL460cycles였다. 실제 fieldTIME=[0,.00251024,.00502048,.0075089,.0100191],TH=[0,.002510241,.005020482,.007508895]이고106columns×4rows가 모두 완전했다. Solid final xx≈1.20136MPa, LAW2 shell layer3 xx≈1.06817MPa였다.

사전 numerical policy:
- stress abs(error)<=1e-5MPa+.01abs(expected)
- U abs(error)<=1e-8mm+.001abs(expected)
- V abs(error)<=1e-8mm/s+.001abs(expected)
- rho0/rho, VOL/V0, 1+.1t의 일치는 rtol3e-6+atol1e-8.

각 actual t에서 독립 Hooke 식을 평가한다. Nominal time을 대입하지 않는다. 이 허용오차는 fixture numerical policy이며 실제 재료 adequacy가 아니다.

### LAW33 관측과 짧은 Ka1 refinement

당시 A 소유 Ka0/Ka1 raw deck을 별도 사본에서 official Linux로 실행했다. 둘 다 Starter0error/0warning,Engine NORMAL18720/18790cycles,TH100rows×82columns 완전이었다. Ka0 최종 actualt=.04950070,SX=-.1MPa,Ka1은t=.04950116,SX=-.07701539MPa,rho=1.019368e-10,VOL=.981이었다. 이 raw deck 전체와 원 출력은 이 복구 blob에서 추측 생성하지 않는다.

A의 독립 source-law ODE 대조는 maxstresserror Ka0=2.7082e-7MPa,Ka1=3.1231e-6MPa,Jmax5.10e-7,dxcommanderror1e-8mm,addedmass/HG0이었다. F의 별도 DOP853 검산은 Ka0≈2.7093e-7MPa,Ka1≈3.3790e-6MPa였다. 짧은 affine source-law numerical evidence이며 일반 실험 adequacy나 제품 optimizer 완료가 아니다.

Ka1 DTIX source는 OC-S engine/source/input/freform.F:577–595다. READ DTIN,DTMX이며 max<0은 오류,max0→1e20,in>max도 오류다. 별도 baseline Engine에 /DTIX 다음 줄2e-6 2e-6,half에는1e-6 1e-6을 쓰고 mass scaling은 없다. 고정91grid0..045s/.0005에서 actualTIME linear interpolation/no extrapolation을 사용했다. Samplewise tolerance는1e-5MPa+.005abs(oracle), refinement는1e-5MPa+.0025abs(half)다. A 실행/F 독립 검산의 maxrefinement=7.56674e-6MPa로 통과했다. 원 uncapped probe는 별도 이력이며 새 card 실행으로 바꾸지 않는다. 장기 campaign refinement·장기 hold/rate/shear·물리 실험은 미실시다.

### Synthetic numerical fixture와 같은 ResultRef

90도 triangle local x→global y(shell101)와 tet(solid202),NODE11..17,PART10/20,TIME.125/.250을 쓴다. Shell upper/middle/lower localxx=10/5/-4MPa,solidxx12다. RP 기존 evaluate에서 middlelocalxx5/globalyy5,maxavailableVM10,solidVM12,undeformedpoints=stored-U를 확인했다. Manufactured binaryVTK이며 native physics가 아니다.

Same-ResultRef fixture는 shell101/layer3/localxx/identity=5MPa,fit=false와 reason SYNTHETIC_FIXTURE_NOT_PHYSICAL_EVIDENCE,view/query/review 적격이다. Source receipt/operation record는 manufactured schema fixture이며 actual JobManager record로 쓰지 않는다. 원 golden bytes를 이 문서에서 복구했다고 주장하지 않는다. 새 fixture를 만들면 새 artifact/hash로 등록해야 한다.

## D9. E/F 비판·negative acceptance·종료 판정

E-06의 projectv3/v4와 child/root lease 충돌은 D0/D5에서 수정했다. 부모와 자식에 별도 writer를 추가하지 않는다. F-07의 Tools actualcheckout/lastchange/equalbytes,actualTIME/derivedforce/source-build 주장 경계를 유지한다.

F-11: 옛 verify_native.py는 requiredlayers 목록을 만들고도 실제 존재한 배열만 순회했다. 따라서 layer3 또는 모든 shellstress 누락을 통과시킬 수 있었다. 반박 근거가 없어 기존 checker를 기각했다. 개정 validate_field는 exactrequiredset,TIMEshape1,tensor2×9,U/V12×3,ID uniqueness,part/family,ID와 reference geometry 대응을 assert한다. 원 native bytes/generator는 이 수정으로 바꾸지 않았다.

당시 실제 negative 실행은 layer3삭제,모든shellstress삭제,layer3→30rename,elementID만swap,nodeID만좌우swap,TIME누락,서로 다른 값을 가진 synthetic의 upper값을 middle에 대입한7건을 모두 거부했다. 좌표·pointarrays·connectivity를 함께 바꾼 정상 permutation은 통과했다. F-owned 사본에서 옛 loop의 누락2반례와 개정 checker를 독립 재실행했다.

Native uniform membrane의 모든 layer값은 같으므로 physical through-thickness placement의 독립 증거는 아니다. Distinct synthetic은 selection 동작만 검증한다. 앞으로 실제 bending oracle를 추가할 때 numerical selection 검사를 물리 검증으로 소급 확대하지 않는다.

구현할 기존 tests의 수락 항목:
- Contract:unknown schema,path escape,hash/source owner/context membership.
- VTK:TIME 누락/비유한,중복ID,wrong-family zero,tensor component count,필수 layer누락,unknown material orientation/strain measure/unit.
- TH:TITLES 누락 dispatch거부,duplicate/decreasing time,rowwidth 불일치,unbracketed endpoint,duplicate header/suffix-only collision,force1row/duplicate time.
- Lifecycle fake:stdout/stderr 분리,return0 empty/invaliddataset,source변경,동일key 중복요청,validate중cancel,timeoutcleanup,late RPready,crashinterrupted,unknown future operation.
- SaveAs:idle child open+normalSave,idlechildSaveAsbusy,activefitbusy,close뒤closurecopy,source삭제뒤reopen,v1..4/selection보존,parentCAS pendingjournal.
- RP:vanilla manual만,unknownfield/hash/nonce/availablelist,first-renderready/closeexit0,Run/GZIP 직접 method guard,추가 solver spawn0,다른 launch 재사용0.
- Provider:동일 immutable ResultRef를 실제 deterministic read했을 때만 numeric evidence다. Tool 이름에 RP가 있다는 사실은 extraction 성공이 아니다.

Reference 재현은 독립 generator→nativeStarter/Engine→officialconverters→field/history oracle→synthetic/negative checks 순서다. 실행자는 exactsource/executableSHA/OS/cwd/argv/inputsoutputsSHA/stdoutstderr/requested·effectivecards/actual sampletime/허용오차를 receipt에 저장한다. 복구 후 raw artifact 미회수를 과거 실행 성공으로 숨기지 않고 재실행 뒤 새 receipt와 과거 이력을 구분한다.

완료 gate는 공통 DTO/owner와 operation lifecycle→OC-ANIM-1/OC-TH4-1→선정 material required observable과 잔차→expert same-ref query→manual RP→optional exact fork→Windows 실화면이다. 마지막 경로가 없어도 verified numerical conversion 구현은 가능하지만 exact RP 자동연결은 완료가 아니다. Native/view/fit/review 적격성은 공학 승인과 다르다. 미선정 모델은 exactlaw/source/license/state/unit/measure/card/API/observable/time/ID/basis/operator와 독립 numerical·physical fixtures,실패·저장·취소 gate를 한 묶음으로 추가한다. Registry metadata만으로 임의 law 계산 가능을 선언하지 않는다.
