# E 독립 검토 — 새 재구성 구현 명세

작성일 2026-11-12. 검토자 E. 이 문서는 환경 단절 뒤 실제 Git blob을 읽고 새로 작성한 검토다. 회수하지 못한 원 R4 파일 또는 원 토의 seq18이 아니다.

## 판정과 정확한 대상

**선정 범위의 정적 구현 명세를 인수한다. E 고유 미해결 blocker는 0개다.** 구현자가 다시 정해야 했던 공용 계약·OS 지원·저장 소유·답변 검토 wire의 주요 결정을 본문과 부속 명세가 구체적으로 고정했다. 이는 제품 서비스/GUI/native/LLM/Windows가 구현 또는 실행 완료됐다는 승인이 아니다. 새 참조 코드의 Python/native CI 결과는 그 실행 receipt가 별도로 소유한다.

직접 읽은 WB 통합 후보 commit: `98be90bfe1d8d02ae099bc0aecf84d1d11110218`. 이 commit의 실제 파일을 fetch_file로 다시 읽어 아래 6개 명세와 README/CURRENT_STATE/기존 계획/최신 C 평가 묶음의 Git blob이 검토본과 일치함을 확인했다. fetch_commit의 변경 65개 경로는 문서·참조 자산·검증 workflow이며 제품 app/caelab 코드 변경은 0개였다. 후보 이후 검토 보고서·토의·receipt 추가는 root의 최종 묶음 검사 대상이며 여기서 존재했다고 소급하지 않는다.

| 대상 | 검토한 Git blob |
|---|---|
| 통합 본문 | 0a84d2e009dbd891d3115d1fcb036e644628ba9a |
| 공통 core | b3ec806dd6d5e52c5062c75fc98b1e829b2c08ac |
| A 재료 | 1f8fb280e3a0f8413721d9c4eed9f7596a163f4d |
| B 재료 | f5e122220fb6f6c5706dcb4906832c0d80fded2b (후속 exact blob) |
| C 전문가 | 035b6e431f34e94bac33e29f2f0cc1e796995438 |
| D 후처리 | 279e81037d279f0852a40577d32ec4188bfcc599 |
| WB 인계 README | c24a5a9b8e14c1ae6b830a3ddc44c3bdbc1d7195 |
| WB CURRENT_STATE / 기존 계획 | e742249fba190aa0b42b17a93aa50991b80b134d / f2d46b2861dff4a13db762c6b6472ae5662655d9 |
| Prep 인계 / CURRENT_STATE / 재사용 계획 | 962c15e9e4e36b61b356fd210bab99481fc2ba60 / 1b863bfc20d39c3310e16205fafe8415c9fc5c14 / 12af234eb0df343e612cbf67d30c61bb6729c1e6 |

그 뒤 B 후속 blob f5e122...의 변경을 실제 읽었다. 변경은 현재 core 절 참조/PredictionBundle C3와 SVD 계산실패의 not_computed 구분이며 계약과 일치한다. B 새 실물 source bundle b95d68052eb52097fcc0a2f23527edd836c0b108의 원문11/notice8 UTF-8 길이·SHA256을 현재 JS로 전부 재계산해 일치했다(별도 E receipt45d842c60eb50b94a68090fc6ee98c7cb8b7f038). Prep commit 5cb3cccef66d58b5ec7cba6eb015b3de734d50ef의 실제 변경3개와 각 blob도 읽어 대조했다. handoff의 WB 링크만 98be90...로 고정됐으며 의미 변경은 없다. 이 후속 B 변경과 검토 기록은 root의 최종 WB commit에 추가해야 한다.

WB 기준 source는 104cdd6888a5554eecc8cf965c60ff1bd980b6da, Prep은 555af5b3ab2c6e7dc276111609302ef565bb4fc8이다. 환경 단절 전 이 source와 README/DESIGN/AGENTS 및 사용자 U1–U9 전체를 독립 읽어 세운 기준을 유지했다. 이후에는 새 bytes를 읽어 재판정했으며 분량·저자의 수용 메시지·예전 통과 기록만으로 합격시키지 않았다.

## 원 지적의 실제 종결

| 지적 | 수정과 재검토 | 판정 |
|---|---|---|
| P-E1 독립 GUI가 한 문단 구상에 머묾 | core C9/Prep 인계: 별도 Qt entry, study/project/node/read-only, 실제 CSV preview·mapping, 파라미터/비교 표, worker와 callback token, 평가/holdout/residual/실패, SaveAs·export/Apply, 공유 PropertyHost와 draft revision. Windows 인수도 구체적 | 명세 닫음; 실제 GUI 미구현 |
| P-E2 저장/재열기·실패 경계 부족 | C7의 실제 제안 API/return DTO와 C8의 closure·source/destination lease·복구 행동으로 보완. E01/E06 및 아래 회수판 추가 지적까지 실제 재검토 | 닫음 |
| P-E3 comparison/assessment 정본 충돌 | id/required_for_fit/required_for_validation, 진단 비교, 고정 목적함수 집합·분모, split 판정, objective/assessment 단일 직렬화. validation/holdout 실패가 train objective를 지우지 않음 | 닫음 |
| E-01 문서 CAS와 계산 revision 혼동 | C2 document_revision=manifest bytes SHA와 immutable content_revision 분리. 요청/optimizer/cache는 content; index/progress 저장은 최신 document CAS; UI operation token/content 검사; 외부 변경 중단·결과 보존 | 닫음 |
| E-02 빈 claims/미정 final wire로 검토 우회 | C5 strict final과 reviewer wire, host 전체 비공백 segment, 부분 hint 비정본, claims0·coverage 누락·검토 불능은 insufficient, identity/context host binding. 현재 JS 반례와 수정 재실행까지 확인 | 닫음 |
| E-03 재심 tuple2 대 3필드 충돌 | frozen ReassessmentLinkV1(review_id,item_id,original_context_hash), wrong/missing link는 event0, 원 review/context 불변, 실제 reviewer actor 확인. 회수판 후속 답변 fixture 오류도 별도로 찾아 고침 | 닫음 |
| E-04 이식 후 잘못된 Workbench 경로 | 새 helper fe4e640...와 optimizer baf997.../cold bd69eab.../README b36a6a...를 실제 읽음. 필수 --workbench-root, import 전후 파일 hash/origin, 양쪽 원본 read-denial coldcopy. 기준 source7개를 직접 GitHub 재취득해 SHA256 일치 확인 | 정적 재현 인계 닫음; 새 Python cold 실행은 이 보고서에서 미실시 |
| E-05 Windows WSL 자식 정리 불명확 | GEOS native는 Linux-host serial only, Windows/WSL UNSUPPORTED_HOST_TRANSPORT/dispatch0. Windows DP는 정확 analytic peer 독립 지원. 미구현 supervisor/WSL 주장을 철회했고 실제 Linux lifecycle 수락을 남김 | 닫음 |
| E-06 child/parent 저장 충돌·v3/v4 | lifetime child writer, 모든 parent SaveAs lease timeout0·정렬·실패 시 모두해제/변경0, idle child도 busy. child commit 뒤 parent CAS 충돌은 persistent pending event; readonly source write0·hash 재검사; destination lease 획득/manifest commit 후 source 해제; commit후 cleanup오류 성공copy 보존. material role v4/readers1–4, post-only v3 | 닫음 |

기존 MDOStudyService의 INP/CalculiX private 실행을 범용 evaluator처럼 호출하지 않고 실제 VariableSpace/optimize/StudyManager/JobManager/RunStore를 조합한다. 새 generic FE DB/queue/전처리기 또는 모든 모델의 자체 적분기를 요구하지 않는다. Prep baseline의 실제 LiveClient.request와 src/caelab_prep/cae_core/document.py execute_many 시그니처도 cloud로 다시 확인했다. 열린 owner process IPC와 정확 typed 지원, multi-resource 후보 검증/한 Undo, acklost/cachemiss의 unknown 상태를 명시했다.

## 회수판에서 새로 발견한 오류와 직접 재검사

원 지적을 형식적으로 닫은 뒤 새 자산을 그대로 승인하지 않았다.

- **E-RC1:** C 참조 parser가 __proto__ extra key, inherited citation toString, RFC JSON이 아닌 NBSP, 45040 UTF-8 bytes의 한국어 action을 허용했다. 저자가 null-prototype object/own-property/RFC whitespace/UTF8 32768-byte gate로 수정했다. 새 b43ec55993945393838ce95872add96e10dfa4b7에 E가 같은 네 반례를 다시 실행해 모두 거부, 기존 negative review8/정상 final not_assessed/정상 review pass/major revise/재심 누락 event0도 확인했다. 기계 wire 검사이며 의미적 답변 정답은 보증하지 않는다.
- **E-RC2:** core 전체 status enum 문장과 constraint_status 대문자 enum이 충돌하고 dimensionless unit 문자열이 모호했다. 필드별 정확 enum과 unit=""를 실제 수정한 f78a... 이후 최종 core에서도 확인했다.
- **E-RC3:** 재구성 중 축약된 부모·RunStore 비차단 lease와 pending association 기록을 다시 고정했다. 최종 C8/D5에 immutable event, 재열기·reconcile CAS/충돌 유지, 모든 lease timeout0, idle writer 제한이 있다.
- **E-RC4:** EvidenceEnvelope 이름과 kind=result를 후속 구현자에게 남긴 문장을 거부했다. 최종 C1이 exact outer fields/kind/native_run·manual·calibration·document_chunk payload/provenance.artifact_owners/availability/legacy descriptor wrapper를 고정한다. master/C/D가 같은 정본을 소비한다.
- **E-RC5:** C followup fixture가 새 reply를 가리키면서 이전 답변의 claim span·coverage hash/count를 복사했다. 새 4c6bbb9c274c914ec7976cd5af9d0b59e480bc2b를 실제 읽고 다시 계산했다. 새 답변98 codepoints/비공백80, reply/request/pack hashes 및 canonical bytes, host segments, binder가 만든 전체 reviewer record가 exact 일치했고 원 event/link context는 보존됐다. request_context_hash와 evidence_pack_hash도 분리했다.

E 실행기록은 recovery_check_receipt.json(blob f0be6fa69051a2cee38c6187972343b615071ddc)과 recovery_followup_check.json(blob f195445e9ed537d557f309d4023d7f0fcc94ee6c)다. 새 실제 JS 실행을 기록했으며 원 Python 로그를 복원한 것이 아니다.

## 전체 과업·업무 완주 인계 확인

| 사용자 목표 | 명세가 고정한 경로와 실제 한계 |
|---|---|
| 여러 재료의 계산·동정·최적화 | A6/B5 정확 profile이 식·state·parameter bounds/units·실험제어·관측·backend API/native card·잔차/제약·optimizer·독립 oracle·holdout·export/Apply로 이어진다. DP/Xia/J2 이름표나 선형 예제만으로 범위를 대체하지 않는다 |
| 모형 구분과 재사용 | native LAW33, Linux GEOS와 Windows analytic DP, 두 Ogden, 실제 pyvisco API, optional compiled NEML, JC prescribed-state, derived XBP의 서로 다른 능력과 미실시를 분리한다. 원 Xia2002·다른 LAW·원본 없는 UMAT를 지원한 척하지 않는다 |
| 연구창·공용 GUI | 새 독립 MaterialStudyWindow에 실제 파일/명령/결과/취소/저장 흐름이 있고 Prep의 NumericEdit/CurveTable/CurvePlot/PropertyEditor를 host로 소비한다. native Prep 문서와 WB draft/history를 이중 소유하지 않는다 |
| 일반 이론·자료·전문가 | run 없는 theory와 실제 첨부가 있는 case/mixed, 초안보존, 실제 원문 corpus, 한영 projection, read-only tools, provider별 native/structured/prompt-only, 별도 reviewer·이견·후속 turn·재심·저장 경로가 정해져 있다. 모델명이나 lexical score 하나로 질문을 차단하지 않는다 |
| 실물 자료와 검토 | C source bundle38c12...의 원문8/notice10 길이·SHA 및 chunk16의 raw char/line span/텍스트·부모hash를 E가 현재 JS에서 모두 재계산했다. 새 labels93881...를 실제 읽고 F의 독립 semantic receipt16ff7b27...도 읽었다. 명시 solver/판본 유실과 기계적인 개발 문항 오류의 실제 수정은 확인했으나 검색 정확도/실 LLM 품질 측정으로 확대하지 않는다 |
| native 결과·RP·Windows | D의 공식 converter argv/short basename/TITLES, actual TIME/ID/family/층/축/측도/단위/coverage, derived operation 소유·cache·cancel·같은 ResultRef, manual RP와 optional fork exact CLI/first-render receipt가 정해져 있다. RP 표시·exit0·ordinal을 수치 진실로 쓰지 않는다 |
| 저장·실패·재현 | mutable CAS와 immutable content, raw/변환·배열·receipt closure, fresh experiment state, corruption 대 historical stale, SaveAs·crash·pending association, typed 오류/취소·latecallback, portable source/native installation 인수가 구체적이다 |
| 다음 구현자 착수 | core C11의 단일 파일 소유·선행·수락표, 기존 코드 접점과 새 API 구분, WB/Prep 상태·계획과 인계 링크가 있다. metadata 등록만으로 미래 모델 지원을 주장하지 않으며 별도 정확 law/API/observable/oracle/platform gate를 요구한다 |

A 최종의 XBP e=S*s+ep, global total INTERNAL/KINETIC/HOURGLASS ENERGY 대 BRIC IE energy-density 구분, host fit_loss/identifiability enum 정정도 실제 diff로 확인했다. B constraints/finite objective와 필수 scalar 계산 실패, D v4와 envelope owner 위치, C objective 객체 유지가 같은 공통 계약과 일치한다.

## 인수 범위의 한계

1. 이 판단은 위 exact 문서·자산의 **정적 구현 준비도**다. 누구든 구현하면 반드시 성공한다는 보장이 아니며 제품 코드 변경/실 사용자 업무 완주를 대신하지 않는다.
2. 원 로컬 commit fba70a.../Prep79da5... 및 316개 원 자산 전체, 원17항목 journal을 회수했다고 주장하지 않는다. 과거 원SHA 확인자료·새 재획득 source·새 재구성·현재 실행을 구분한다. 이 새 검토는 원 R4 중단을 과거 합격으로 바꾸지 않는다.
3. 일부 native CSV/T01/VTK/로그 원bytes와 원 cold receipt가 없다. 명세/recipe와 남은 실제 과거 관측은 구현 인계를 돕지만 원 실행을 여기서 다시 증명하지 않는다.
4. GEOS native, 일반 XBP kernel, NEML 실제 tensor build, 장기 Ka1 시험·실재료 실험 적합성, 새 제품 service/Qt/provider/Prep multiApply/RP fork/Windows 전체 흐름은 각각 미구현·미실시 gate다. 계획에서 제외하거나 통과로 바꾸지 않았다.
5. 검증 workflow aa69ab.../integritychecker df2e6.../nativefetch ee7b...는 읽기 검토했다. 현재 Python/native 실행 결과를 이 보고서에서 발명하지 않는다. 실제 Actions 결과가 나면 exact commit/생성 raw/receipt를 추가해 실행 범위를 따로 판정해야 한다.
6. C corpus는 source8/span16의 제한된 공학 자료다. 평가 covered24는 한영12 intent이며 24개 독립 공학영역이 아니다. F가 labels96의 의미·facet span을 검토한 사실과 E의 hash/span 검사는 실제 retrieval/live답변 성능과 다르다.

따라서 E01–06/P 지적과 E-RC1–5는 이 exact 정본에서 닫고 구현 착수용 명세로 인수한다. 최종 배포 묶음의 검토 보고서·토의 파일·링크·asset manifest와 새 CI 실행 기록은 root가 실제 최종 commit에서 완결해야 한다.

## 후속 실제 원격 실행 확인

보고서 작성 중 새 [Actions 실행43140439077](https://github.com/pikachu444/caelab-workbench/actions/runs/43140439077)이 완료됐다. E는 GitHub job130925841559의 실제 로그를 직접 읽었다. root가 보관한 정리 로그 blob39c9d20285cba77b07eee5ec4f9389e99fc31577도 읽었지만 원 로그와 byte-identical하지 않으므로 동일 해시라고 주장하지 않는다. 위 통과/수치/exit 관측은 직접 가져온 job 로그에서도 확인했다. 실행 receipt52c4d5ff7c583d5c03def04b6a868cef2946c01c도 읽었다. PR head는98be90bfe1d8d02ae099bc0aecf84d1d11110218이고 실제 checkout은 이를 baseline104cdd...에 합친 GitHub merge ref f975d44였다. 위의 '새 Python/native 미실시'는 이 후속 실행 전 검토 범위다.

현재 로그에서 A 산술/deck recipe, pinned official Linux release, 새 LAW33 Ka1 dt2e-6/dt1e-6 native pair와 새 fixed-step Euler 수치검사, B numerical/finite-K-root/19expected/static17asset, 실제 pyvisco+기존 optimizer, constraints와 양쪽 원본 read-denial coldcopy가 실행·통과한 것을 확인했다. 기존 optimizer는146 observations, CONVERGED, fit_loss1.4321823225436936e-13, untouched holdout_loss3.291180473817548e-14다. Coldcopy6개 reference command는exit0, NEML은exit20으로 BACKEND_UNAVAILABLE이 남는다. 따라서 E04의 이식 후 재현은 새 원격 실행에서도 좁은 실제 근거를 확보했다.

D는 새 deck 생성만 실행했다. D native solver/converter/checker, Ka0의 새 native 실행, 과거 F DOP853, GEOS, 일반 XBP, NEML 실제 backend, 제품 GUI/서비스/실 LLM/Prep multiApply/RP fork/Windows·실재료 타당성으로 범위를 확대하지 않는다. E가 ZIP 안의 새 native raw CSV를 별도 수치 재검산한 것은 아니며 그 작업은 native 검토자/원 실행 산출물의 별도 증거다. 이 후속 기록도 원래 잃어버린 raw 자료를 복원했다는 주장이 아니다.
