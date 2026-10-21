# 현재 상태

- 제품 정의 (사용자 확정): 워크벤치는 각 프로그램을 연결하는 프로그램이고, 그 결과들을 근거로 토론하는 프로그램이다.
- 진행 단계: 골격(0단계) 완료, 연결(1단계) 진행 중.
- 본선: 솔버별 네이티브 전처리기를 그대로 쓴다 (CalculiX=FreeCAD FEM, Code_Aster=Salome, OpenRadioss=Gmsh). 자체 공통 형식은 입력 모델 전체가 아니라 프로젝트 상태와 토론용 결과 요약으로 축소한다. 자체 변환기(`caelab/solvers`)는 전처리기가 없는 경로용 보조다.
- 된 것: 프로그램 연결 층(`caelab/connections` — FreeCAD·Salome·Gmsh·CalculiX·Code_Aster·OpenCourant·FEniCSx·ParaView 찾기/상태/실행 명령), 토론 근거용 결과 요약(`caelab/result_summary`), 모델 스키마·저장/재개, 입력 검사, CalculiX 보조 변환기(덱 생성·실행·결과 판독), JobManager·Workbench 단일 경로, 프로젝트 화면에서 상자별 프로그램 연결 상태 표시와 설치된 프로그램 실행 열기, 솔버별 연기 테스트(`tests/test_solver_smoke.py`, 설치된 환경에서만 실행).
- 설치·확인한 것 (이 컴퓨터, 2026-10-21): FreeCAD 1.1.4 (공식 AppImage) — FEM 파일을 코드로 만들어 자체 작성기로 인풋 파일을 쓰고 ccx 실행까지 완료, 반력 평형은 공유 절점 하중 18.65 N까지 규명해 정확히 대조. Gmsh 4.14.1 단독 설치. OpenCourant (OpenRadioss 후계, 공식 배포 종료로 대체) — 공식 QA 덱 TWISBEAM으로 스타터+엔진 정상 종료 (16,796 사이클, 에너지 오차 −0.0%). ParaView 6.2.0 (conda) — OpenCourant 결과를 VTK로 변환해 읽음 (점 172, 셀 123).
- FEniCSx PDE 라인: ~/workspace/tools/fenicsx-env (dolfinx 0.11.0)에 설치, 포아송 제조해 대조 통과 (L2 오차 1.35e-3 @32×32, 차수 1.99). 공통 스키마가 아닌 자체 형식 유지.
- 수치 엔진(`caelab/numerical`): 원본의 DOE·적합·최적화·UQ·민감도를 이식하고 공통 모델 변수·응답에 연결. 테스트 19개 통과 (Sobol S1 0.900/0.100, UQ 평균 9.999 등 해석해 대조).
- 확인 안 된 것: Windows 실화면 GUI 수용, Code_Aster 실행 (배포처 이전으로 설치 파일 주소 확인 중), Salome 실행 (9.16 설치 파일 받는 중), FreeCAD가 만든 결과 파일의 반력을 .dat에서 읽는 판독기 확장, LLM 전문가 토론 흐름.
- 다음: Code_Aster·Salome 설치와 계산 테스트 → 결과 판독기 .dat 반력 확장 → 전문가 토론 패널.
