# 병합 요청 — 001 솔버 설치·연계 (2026-10-21)

통합 채팅이 main에 반영할 내용만 정리했습니다. 공유 파일(connections.py,
CURRENT_STATE.md, ARCHITECTURE.md)은 이 채팅에서 직접 고치지 않았습니다.

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

## 미완 — Code_Aster .sif (사용자 확인 대기)

- 파일: salome_meca-lgpl-2025.1.0-1-20251026-scibian-12.sif (5.34GB),
  버킷 `open-sim-center` (페이지 데이터에 s3:// 경로 확인), 직접 주소는 403.
- 다운로드 버튼은 Next.js 서버 액션 handleDownload(플랫폼 ID 107, 약관
  동의 bool)가 서명 주소를 반환하는 구조임을 JS 청크 분석으로 확인.
  단, 이 환경의 HTTP 호출에는 서버가 액션을 실행하지 않고 페이지 응답만
  반환함을 대조 실험(잘못된 액션 ID에도 동일 응답)으로 확인.
- 브라우저 자동화는 클릭까지 성공했으나 주소 캡처·다운로드 상태 확인이
  도구 제약으로 불가. 사용자에게 chrome://downloads에서 링크 주소 복사를
  요청한 상태입니다. 주소가 오면 병렬 이어받기로 받고, Apptainer 래퍼
  (`run_aster_sif.sh`)를 만들어 연기 테스트 반력 Z 합 +1,000 N(5% 이내)
  통과까지 마칩니다.
