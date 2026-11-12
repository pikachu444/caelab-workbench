# CAELab 전체 구현 인계

이 묶음은 사용자 요청에 따라 범용 재료 모델링·최적화, 전문가 자문/독립 검토, 외부 프로그램/native 후처리의 구현을 구체화한 개발 명세와 참조 자산입니다. 새 제품 기능 자체를 구현한 보고서는 아닙니다. DP/Xia는 예시이며 여러 모델군과 backend를 다룹니다.

## 구현자가 읽을 순서

| 순서 | 파일 | 고정한 구현 내용 |
|---|---|---|
| 1 | [통합 계약](../spec-cae-research-implementation.md) | 기존 코드 재사용·공통 DTO·영역 경계 |
| 2 | [core](core-implementation.md) | 서비스/목적함수·제약·revision/CAS·SaveAs/복구·Windows Qt·열린 Prep IPC/atomic Apply |
| 3 | [A 재료](material-pressure-orthotropic.md) | DP 두 backend·LAW33Ka0/Ka1·직교이방·명확히 이름 붙인 derived XBP |
| 4 | [B 재료](material-finite-history.md) | incompressible/compressible Ogden·Prony/TRS·NEML 반복소성·JC prescribed-state |
| 5 | [전문가](expert-implementation.md) | 일반CAE질문·원문 corpus·검색·read tools·provider loop·全답변/주장 검증·별도 reviewer/재심 |
| 6 | [후처리](post-implementation.md) | 실제converter·native ID/TIME/층/축/TH·수치 검증·RP별도앱과optional fork |
| 7 | [자산 목록](recovery-assets.json)·[검토 E](review-E.md)·[검토 F](review-F.md) | 실제 파일 identity·실행/정적검토 범위·해결한 지적 |
| 8 | [공유 ASCII 토의](debate-recovery-ascii.jsonl) | 지정순서·이전blob참조·각 작성자의 반론/답변/최종 판정 |

공통 contracts/서비스/CAS/저장/GUI/IPC는 core가 이전 통합본문보다 우선합니다. 영역별 정확한 law/provider/후처리는 A/B/C/D 정본이 소유합니다. 모델명 분기로 generic service를 늘리지 않습니다. 새모델은 exactlaw/source/license·계수/상태/단위/측도·실제backend API/카드·관측/coverage·독립oracle·실험식별·export/Apply·플랫폼검증을 갖춘 adapter로 추가합니다.

[작업 배정 JSON](implementation-map.json)과 core C11의 파일/선행/인수 표대로 CORE01→CORE02/STORE01을 구현하고 MAT-A/MAT-B·E0–E2·POST01–04를 병렬 진행한 뒤 UI01/PREP01/LINK01과 실제provider/RP/Windows 인수를 닫습니다. API/DTO/오류/취소/잠금/원자저장/수치한계가 해당 정본에 있습니다. 지원binding없는native export/Apply를 만들어내지 않습니다.

## 실제 토의와 회수 경계

작성4명(A/B/C/D)과 독립 검토2명(E/F)이 과업 전체를 읽고 서로 다른 관점으로 비판·수정·재확인을 수행했습니다. 요청한 모델설정은 A–D gpt-6.1-sol/high,E–F gpt-6-astra/xhigh였습니다. 실제 숨은 backend모델·추론토큰 수·내부추론 횟수는 관측할 수 없습니다.

환경단절 전 로컬ASCII journal은17항목까지 실제 작성됐으며 마지막SHA256은 6cfd2995c2d1cbf4fb04ab0a40d732b84f4b7bee9b44c53196dd0ba2da71c56f입니다. 원 journal전체bytes와 로컬커밋 전체는 현재 회수하지 못했습니다. 복구journal은 **새 실제 파일기반 토의**입니다. 각 지정작성자가 이전실제blob을 읽고 자기entry만추가하며 root가다른사람의발언을대필하지 않습니다. 원seq18/19를복원했다고주장하지않습니다.

회수manifest는 (a) 원SHA와일치한bytes,(b) pinned공개source를다시실제확보한bytes,(c) 새로재구성한명세/코드/fixture,(d) 과거실행관측만남고raw출력미회수를구분합니다. 잘못된 초기후보blob은최종목록에넣지않습니다. 재구성코드가현재다시실행됐는지는현재검증결과와분리합니다.

## 참조 자산과 실제 결과 범위

- evidence/A: exactsource/license,LAW33원덱일부/시간steprefinement입력,원SHA확인된historicalassessment2개,portable생성/실행/검사recipe. 과거 LinuxLAW33Ka0/Ka1과caprefinement의shortaffine검증은실시했지만rawCSV/T01/log전체는이번묶음에없습니다.
- evidence/B: pinnedsourceidentity와library/API계약,실험/expected/reference/기존optimizer제약probe/finiteKroot/optionalNEML/coldcopyrecipe. 과거 실제pyvisco+기존optimizer fit과독립확인은있으며NEML빌드성공을주장하지않습니다.
- evidence/C: pinnedraw8·notice10·source span16의실물bundle,36dev/60eval문항,strictschema/golden/referenceJS와tool/providerwire/reviewevent실물. 현재JS실행과독립공격검사통과는strictwire/hostcoverage범위이며semantic정답률/실LLM인수는아닙니다.
- evidence/D: LAW1N0/LAW2N5nativefixture생성recipe와source/executable/oracle정책. 당시실제nativeconverter/reader검증과새재구성recipe의미실행을구분합니다.

모든referencefit은합성/수치검증범위를명시합니다. 실재료물리검증·새제품service/GUI·liveLLMprovider·새Prep multiApply·RPfork·Windows전체흐름은구현인수작업입니다. 미실시를pass로바꾸지않습니다.

## 현재 실제 재실행 결과

[Actions43140439077](https://github.com/pikachu444/caelab-workbench/actions/runs/43140439077)의새실행이통과했습니다. exactrecoveryasset검사·A참조계산·공식Linuxrelease해시검사·실제LAW33Ka1의2e-6/1e-6cappedpair와새Euleroracle/refinement검사·D덱생성·B수치/유한Kroot·실pyvisco/기존optimizer·제약·coldcopy재생산을수행했습니다. Pronyfit은146observations,loss1.4321823225436936e-13,untouchedholdout3.291180473817548e-14입니다.

새nativeCSV/로그/수치결과는[현재실행artifact](https://github.com/pikachu444/caelab-workbench/actions/runs/43140439077/artifacts/13909436920)와[실행receipt](evidence/M/current-ci-receipt.json)에연결합니다. 이는미회수원raw를복원한것이아니라새실제실행결과입니다. D는새덱생성만실행했고새Dnative/checker/과거DOP853/Ka0native를다시검증한것은아닙니다. NEML은exit20으로backendgate미충족을별도기록했습니다. greenjob을NEMLpass로읽지않습니다. 문서에재구성직후미실행이라고적은referencecode의이후실행상태는이receipt가소유합니다.

제품service/GUI/실LLMprovider/Prep새multiApply/RPfork/Windows와실재료검증은여전히구현인수입니다. 과거receipt를수정하여새실행처럼보이지않게합니다.
