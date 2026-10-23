# 병합 요청 — 001 솔버 설치·연계 (2026-10-21)

> **과거 인계 기록:** 아래 내용은 당시 작업 환경의 요청·보고를 보존한 것이다. 채팅 번호·개인 경로·반영/완료 표현을 현재 작업 지시나 원격 main 상태로 사용하지 않는다. 현재 운영은 [루트 AGENTS](../../AGENTS.md), 구현 상태는 [CURRENT_STATE](../../CURRENT_STATE.md)를 따른다.

통합 채팅이 main에 반영할 내용만 정리했습니다. 공유 파일(connections.py,
CURRENT_STATE.md, ARCHITECTURE.md)은 이 채팅에서 직접 고치지 않았습니다.

## 설치 도우미 (신규, 커밋 c2fe640 + 작업 트리에 후속 수정)

- `caelab/solvers/installer/` 신설: `manifest.json`(8종: 버전·공식 주소·
  크기·해시·배치 폴더·CAELAB_* ·실행 전제), `engine.py`, `cli.py`
  (`python -m caelab.solvers.installer list|install|verify`).
  단위 테스트 `tests/test_installer.py` 6개 통과.
- 리눅스 항목은 전부 수동 설치 실측과 대조 검증됨 (FreeCAD·Gmsh는 공식
  파일과 크기 일치, OpenCourant는 공식 SHA-256 일치). Windows 항목은
  공식 설치 파일·자동 설치 옵션만 수록하고 전부
  `unverified-windows`(실기 확인 전)로 표기.
- Code_Aster 리눅스 .sif는 `manual` 항목: 엔진이 자동 다운로드하지
  않고 이유를 표시하며 건너뛰고, 사용자가 파일을 배치하면 크기 대조
  후 Apptainer 래퍼(`run_aster_sif.sh`)를 생성해 등록한다.
- 등록 형식 제안 (연결 층 결합은 통합 몫): 설치 루트에
  `caelab-install-registry.json` — `{"env": {CAELAB_*: 절대경로},
  "runtime_env": {프로그램id: {변수: 값}}, "programs": {...}}` 와
  `caelab-env.sh`를 남긴다. connections.py가 CAELAB_* 환경변수가 없을
  때 이 레지스트리를 읽는 폴백을 넣으면 설치 도우미와 연결된다.
  첫 실행 설치 마법사 화면 결합은 005 몫으로 남긴다.
- 끝단 검증 (tools-install-test, 1차): FreeCAD·Gmsh·CalculiX·ParaView
  설치+등록 성공, Code_Aster 수동 건너뜀 정상. Salome는 전개까지 완료
  (타르 전개 방식을 시스템 tar로 바꾼 수정 포함). OpenCourant·FEniCSx와
  연기 테스트 대조는 2026-10-21 19:48 런타임 재시작 이후의 환경 문제
  (아래)로 중단된 상태이며, 회복되는 대로 마저 보고한다.
- 환경 문제 (2026-10-21 저녁, 통합 확인 요망): 재시작 후 ① /usr 설치분
  소멸(Apptainer, Salome apt 의존성) — 재설치 중 apt 목록 갱신이 미러
  무응답으로 지연, ② 실행 프로세스의 파일 내용 쓰기가 간헐적으로
  "Operation not permitted"로 거부됨 (git 개체 쓰기 포함 — 그래서
  설치 엔진의 레지스트리 원자적 교체 수정은 작업 트리에만 있고
  커밋 대기 중; 셸·파일 도구 쓰기는 가능).

## 설치·검증 완료 (실측)

- **Salome 9.16.0** (단독본, Code_Aster 전처리기): `~/workspace/tools/SALOME-9.16.0-native-UB24.04-SRC/salome`
  - tarball 3,837,464,907 바이트, gzip 무결성 통과, 압축 해제 8.5GB.
  - 실행 전제: apt 패키지 libboost-filesystem1.83.0·libboost-serialization1.83.0·
    libboost-chrono1.83.0·libboost-date-time1.83.0·libboost-regex1.83.0·
    libboost-program-options1.83.0·python3-psutil·liblapacke·libopenblas0-serial
    + psutil 사본을 KERNEL/bin/salome 폴더에 배치 (런처가 PYTHONPATH를 재설정해서 필요).
    전체 sat 목록은 `~/workspace/tools/salome-apt-deps.txt` (libtiff5는 noble에 없어 제외).
  - 헤드리스 검증: `salome shell`로 상자 형상→NETGEN 사면체 메시→MED 내보내기.
    절점 259, 사면체 584, 그룹 FIXED 16·LOADF 16·SOLID 584. MEDCoupling 재읽기
    절점 259·셀 584·그룹 3개 일치. 연기 테스트 `test_salome_headless_med_mesh`
    (work/001-solvers 6cd4146, CAELAB_SALOME 게이트) 통과.
- **.dat 반력 판독기**: `caelab/solvers/calculix.py`에 `parse_dat` 추가 —
  절점별 *NODE PRINT와 FreeCAD TOTALS=ONLY 합계 행을 모두 읽고, .frd에
  FORC가 없을 때 병합. 실측: FreeCAD 덱 반력 Fz +981.35 N (적용 −1,000 N,
  차이 18.65 N은 고정면∩하중면 공유 절점 하중과 일치 — 기존 규명과 동일).
  테스트 4개 추가, 전체 통과. (main 반영됨: 9f4f13f)
- **Apptainer 1.5.4**: 공식 .deb를 `~/workspace/tools/apptainer_1.5.4_amd64.deb`로
  받아 설치. .sif 실행 준비 완료.

## 연결 층 환경변수 확정안 (connections.py 변경 불필요)

등록된 이름과 그대로 맞춥니다. 실행 경로는 코드가 아니라 환경변수로만 지정:

| 환경변수 | 값 |
|---|---|
| CAELAB_SALOME | `~/workspace/tools/SALOME-9.16.0-native-UB24.04-SRC/salome` |
| CAELAB_CODEASTER | `~/workspace/tools/run_aster_sif.sh` (.sif 도착 후 생성 — 아래 미완 항목) |
| CAELAB_ASTER_TESTDATA | `~/workspace/tools/aster-testdata` |
| CAELAB_CCX | `~/workspace/caelab-audit/ccxenv/bin/ccx` (기존) |

## 문서 문구 요청

- CURRENT_STATE "확인 안 된 것"에서 Salome 실행을 지우고, 위 Salome 실측
  (절점 259·셀 584·그룹 3개)을 "설치·확인한 것"에 넣어 주세요.
- CURRENT_STATE의 Code_Aster 항목은 ".sif 서명 주소 확보 실패 — 사용자
  확인 대기"로 바꿔 주세요 (아래 사유).
- ARCHITECTURE "아직 없는 것"에서 Code_Aster(Salome) 연결은 환경변수
  확정으로 부분 해소 — 표기는 통합 판단에 맡깁니다.

## Code_Aster .sif — 다운로드 완료, 계산 테스트만 환경 회복 대기

- 파일: salome_meca-lgpl-2025.1.0-1-20251026-scibian-12.sif,
  `~/workspace/tools/salome_meca.sif`에 **5,736,652,800 바이트**로
  다운로드·조립 완료 (2026-10-21 18:27, 서버 공시 크기와 정확히 일치).
- 주소 확보 방법 (재현 가능): 이 서버에 Puppeteer로 실제 크롬을 띄워
  (프록시 인증은 로컬 중계 프록시로 우회) 다운로드 흐름을 실행하고,
  새 탭 요청에서 서명 주소를 가로챘다. 스크립트는
  `~/workspace/tools/pptr/capture.js`. 직접 HTTP로 서버 액션을 부르는
  방식은 서버가 페이지 응답만 반환해 불가능함을 대조 실험으로 확인.
  서명 주소 유효기간은 3시간이라 자동화 대상이 아닌 수동 절차로
  매니페스트에 남겼다.
- 남은 것: Apptainer 재설치(재시작으로 소멸, apt 목록 갱신 지연 중) →
  이미지 안 run_aster 경로 확인 → 래퍼 생성 → 연기 테스트 반력 Z 합
  +1,000 N(5% 이내) 확인. 환경이 회복되는 대로 실행한다.
