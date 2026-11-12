# 사용자 관점 진행상태와 전체 구현 인계 — 2026-11-12

이번에 완료한 것은 저장소 현황 조사, 정확한 source/수식/API 연구, 실제 참조 실행, 작성자4명·독립 검토자2명의 반론·수정·재확인, 전체 개발 명세·참조 자산·개발 순서/현황 갱신입니다. 새 제품 기능 전체를 구현했다는 뜻은 아닙니다.

## 지금 사용할 수 있는 범위와 앞으로 연결할 기능

| 사용자 작업 | 현재 소스에서 확인한 범위 | 이번에 정한 실제 개발 |
|---|---|---|
| 모델 준비 | Prep에 native RAD 문서/카드·조건·곡선·메시 편집,저장/Undo/export와열린GUI IPC/MCP경로가있음. 모든LAW/키워드·모든GUI/플랫폼을상용수준완료로확대하지않음 | existingtypedprovider/codec/공용Qt를재사용하고 exact재료card·curve binding과단일atomicApply를추가 |
| 해석 실행 | Workbench기존JobManager/RunStore와CalculiX/OpenCourant·OpenRadioss계열native입력실행경로가있음 | 원덱/INCLUDE/바이너리/출력을불변snapshot으로보존하고선택재료실험의candidate별실제실행/곡선수집연결 |
| 시험자료로재료동정 | 기존MDO의변수/optimizer/제약과INP·스칼라응답경로를쓸수있음. 새범용MaterialStudyGUI/다시험재료동정제품flow는미구현 | 실제CSV·측도/단위·하중이력·고정/자유/연계계수·다채널잔차/weights·holdout·제약/식별성·저장재열기·nativeexport/Apply |
| 전문가자문 | 기존대화/근거/프로바이더코드와PrepMCP를새전문가RAG/toolloop완료로볼수없음 | 결과없는이론질문,원문검색·출처해시/판본/실근거조회,model요청tool왕복,독립review/재심·이견보존·근거부족/오류/취소표시 |
| OpenCourantRP | 별도실행앱으로수동후처리사용가능. 현재upstream은선택run정확자동열기/post-onlyCLI가없음 | WB수치converter/reader와별도RP표시를연결;vanilla수동보기와optionalpost-onlyfork를분리인수 |

새solver실행queue/FE모델DB/웹목업/유료API필수조건을추가하지않습니다. 전문가가읽고제안하는동작과사용자의명시Apply/run은분리합니다. 전문가심의는서로다른불변요청/답변으로검증하며역할명이같거나다르다는이유만으로독립성을주장하지않습니다.

## DP/Xia 이외의 재료 모델도 다루는 방식

[재료A](material-pressure-orthotropic.md)와[재료B](material-finite-history.md)는법칙·상태·계수·단위/측도·실험·backendAPI/카드·출력·제약·독립수치oracle·export/Apply·플랫폼조건을정의합니다.

| 모델군 | 선정한 정확한 첫 구현 | 한계/구분 |
|---|---|---|
| 압력의존소성 | 선형경화·비관련흐름DP의명시축대칭branch,analyticpeer와GEOS native driver경로 | apex/tension/cap/softening자동지원없음;GEOS선택backendLinuxonly,Windowsanalyticpeer |
| 발포체 | 실제OpenCourantLAW33Ka0·Ka1 | crush와rate/hold의상태·airpressure/currentdensity·실제TH전달을구분 |
| 직교이방/종이 | plane-stresselastic과GMN2012-derived power XBP | 원Xia2002/LAW112와동일시하지않음;derivedgeneralreturnmap/nativeexport검증은각각별도 |
| 초탄성 | incompressibleOgdenN2와compressibleOgdenN1 | 유한K횡방향traction-free해/모든root선별·안정성제약,무한K곡선과bulkfit혼합금지 |
| 점탄성 | Prony/generalizedMaxwell+WLF/Arrhenius의시간/주파수응답 | 실제pyviscoAPI·감소시간·온도/주파수단위·장기/복소응답을명시 |
| 반복소성 | NEMLChaboche2backstress+Voce | Mandel순서/이력/응력제어와energybookkeeping분리,NEML실제backendgate미충족 |
| 속도·온도·파손개시 | prescribed-stateJohnsonCookflow와uncoupledfractureinitiation | FE응력적분·damageevolution·elementdeletion·meshobjective파괴지원으로확대하지않음 |

J2/선형탄성은기초회귀/확장성시험일뿐최종범위가아닙니다. 새재료도같은등록계약으로추가하지만이름만적어서최적화가능으로표시하지않습니다. 실제계산backend/출력과검증을충족해야합니다.

## 실제 토의·검증에서 바꾼 내용

- 원답변전체를검토하지않고선택한claims만검토해통과하는문제를막았습니다. 새strictparser의prototype/상속citation/비RFC공백/UTF8budget우회도독립검토자가발견·재실행·수정확인했습니다.
- 고오차finiteobjective,유효한물리제약위반(INFEASIBLE),계산/필수출력실패(nullobjective)를분리했습니다. 학습실패/holdout/진단실패가목적함수분모를바꾸지않습니다.
- 문서bytesrevision과계산contentrevision,읽기전용SaveAs/목적지lease/외부변경CAS/부모연결충돌복구를구체화했습니다.
- 실제native실행으로LAW1NIP5강제변경,실제TIME,THconverter파일출력,현재VOL·density,layer/ID/축·측도추정문제를확인했습니다.
- 최종재구성자료의잘못된질문판본/solverfacet과서로다른답변의reviewcoverage를독립검토자가발견해실물과검증코드를고쳤습니다.

[공유ASCII토의](debate-recovery-ascii.jsonl)는M→E→F→M→A→B→C→D→E→F→M순서로각담당자가앞파일을읽고자기항목만추가했습니다. [E검토](review-E.md)/[F검토](review-F.md)에정확한대상blob/지적/수정/재확인범위가있습니다. 요청모델설정은A–Dsol/high,E–FAstra/xhigh이며숨은추론토큰/내부추론횟수는측정하지못했습니다.

## 현재 재실행 결과와 인계 자료

[현재CI](https://github.com/pikachu444/caelab-workbench/actions/runs/43140439077)는공식LinuxOpenCourant를해시검사한뒤새LAW33Ka1cappedpair/새Euleroracle/refinement,참조수치·유한Kroot·실pyvisco/기존optimizer·제약·coldcopy를실제실행해통과했습니다. Pronyfit146observations에서loss1.4321823225436936e-13,untouchedholdout3.291180473817548e-14였습니다. 합성referencefit이며실재료타당성보증이아닙니다. NEMLexit20은별도backend미충족입니다. D는새덱생성까지만현재재실행했습니다.

[전체인계색인](README.md)에서통합본문+5부속정본,원문/notice/실제질문·strictschema/golden·생성/실행/검사코드·현재CI/과거관측·독립검토·토의파일을읽을수있습니다. [작업단위표coreC11](core-implementation.md)은각파일/담당/선행/인수를고정합니다. Prep에는별도저장소의docs/material-research-implementation-handoff.md로기존ownerIPC·typednativebinding·공용Qt·Undo를담당할작업을연결합니다.

환경단절전원raw전체가회수됐다고주장하지않습니다. 회수원bytes/새문서/새현재실행은manifest와receipt에서구별합니다. 남은것은정의된계약대로제품코드를구현하고실provider/WindowsGUI/새PrepApply/RPfork와실재료시험으로인수하는작업입니다.
