# 현재 상태

- 제품 정의 (사용자 확정): 워크벤치는 각 프로그램을 연결하는 프로그램이고, 그 결과들을 근거로 토론하는 프로그램이다.
- 진행 단계: 골격(0단계) 완료, 연결(1단계) 진행 중.
- 본선: 솔버별 네이티브 전처리기를 그대로 쓴다 (CalculiX=FreeCAD FEM, Code_Aster=Salome, OpenRadioss=Gmsh). 자체 공통 형식은 입력 모델 전체가 아니라 프로젝트 상태와 토론용 결과 요약으로 축소한다. 자체 변환기(`caelab/solvers`)는 전처리기가 없는 경로용 보조다.
- 된 것: 프로그램 연결 층(`caelab/connections` — FreeCAD·Salome·Gmsh·CalculiX·Code_Aster·OpenCourant·FEniCSx·ParaView 찾기/상태/실행 명령), 토론 근거용 결과 요약(`caelab/result_summary`), 모델 스키마·저장/재개, 입력 검사, CalculiX 보조 변환기(덱 생성·실행·결과 판독), JobManager·Workbench 단일 경로, 프로젝트 화면에서 상자별 프로그램 연결 상태 표시와 설치된 프로그램 실행 열기, 솔버별 연기 테스트(`tests/test_solver_smoke.py`, 설치된 환경에서만 실행).
- 설치·확인한 것 (이 컴퓨터, 2026-10-21): FreeCAD 1.1.4 (공식 AppImage) — FEM 파일을 코드로 만들어 자체 작성기로 인풋 파일을 쓰고 ccx 실행까지 완료, 반력 평형은 공유 절점 하중 18.65 N까지 규명해 정확히 대조. Gmsh 4.14.1 단독 설치. OpenCourant (OpenRadioss 후계, 공식 배포 종료로 대체) — 공식 QA 덱 TWISBEAM으로 스타터+엔진 정상 종료 (16,796 사이클, 에너지 오차 −0.0%). ParaView 6.2.0 (conda) — OpenCourant 결과를 VTK로 변환해 읽음 (점 172, 셀 123). Salome 9.16.0 단독본 — 헤드리스로 상자 메시 생성(NETGEN, 절점 259·사면체 584·그룹 FIXED/LOADF/SOLID)→MED 내보내기→MEDCoupling 재읽기 일치까지 확인. Apptainer 1.5.4 설치 (.sif 실행 준비).
- FEniCSx PDE 라인: ~/workspace/tools/fenicsx-env (dolfinx 0.11.0)에 설치, 포아송 제조해 대조 통과 (L2 오차 1.35e-3 @32×32, 차수 1.99). 공통 스키마가 아닌 자체 형식 유지.
- 수치 엔진(`caelab/numerical`): 원본의 DOE·적합·최적화·UQ·민감도를 이식하고 공통 모델 변수·응답에 연결. 테스트 19개 통과 (Sobol S1 0.900/0.100, UQ 평균 9.999 등 해석해 대조).
- 최적화 화면: HyperStudy식 5단계 패널(설정→DOE→근사모델→최적화→재실행 검증), 자체 수치 엔진 사용, 단일 목적까지. 리눅스 오프스크린 테스트 4개 통과 (근사 RMSE 0.245, 최적점 (0.507, −1.012), 재실행 오차 0.00775), 메인 창 도킹 결합 완료. 모델 연결(변수 경로 선택)은 후속.
- 결과 판독기: CalculiX .dat 반력 읽기 확장 완료 — FreeCAD 덱 반력 Fz +981.35 N (차이 18.65 N은 공유 절점 하중과 일치).
- 전문가 토론: 실제 ccx 결과 요약을 근거로 로컬 LLM(llama.cpp + Qwen2.5-1.5B, 유료 API 없음)으로 토론 2회전 동작 확인 (제안 파싱·맥락 유지). 답변 품질은 소형 모델 수준.
- 설치 도우미(`caelab/solvers/installer`): 프로그램 8종의 매니페스트(버전·공식 주소·크기·배치·환경변수·전제)와 설치 엔진·명령줄. 단위 테스트 6개 통과. 빈 폴더 끝단 설치 검증은 진행 중. Code_Aster .sif는 수동 항목으로 등록해 두면 래퍼를 만들어 등록한다.
- 메인 창 속성 칸은 표시 전용으로 전환됨 (설계: 자체 화면은 확인·검사·실행·요약 — 값 변경은 전문가 제안 적용 경로로만).
- 확인 안 된 것: Windows 실화면 GUI 수용 (사용자 지시로 홀드 중), Code_Aster 실행 (.sif 5.74 GB 병렬 다운로드 진행 중 — 끝나면 Apptainer로 계산 테스트), 전문가 토론의 Windows OpenScience 구독 실행.
- 다음: Code_Aster 계산 테스트 (반력 Z 합 +1,000 N 대조) → 최적화 패널 모델 연결 → 1단계 종료 조건(압력·자중·접촉·비선형 모델 끝단).
