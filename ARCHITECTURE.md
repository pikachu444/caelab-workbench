# 구조

기준은 재설계 설계안 v1의 개정 기준(2026-10-21)입니다: 제품 정의는 "각 프로그램을 연결하고, 그 결과들을 근거로 토론하는 프로그램"이며, 기존 저장소의 문서는 근거 자료일 뿐 기준으로 삼지 않습니다.

## 원칙

- **연결이 본선입니다** (`caelab/connections`). 전처리는 솔버별 네이티브 프로그램을 그대로 씁니다: CalculiX는 FreeCAD FEM, Code_Aster는 Salome, OpenRadioss는 Gmsh. 후처리는 ParaView입니다. 워크벤치는 이 프로그램들을 찾고, 상태를 보여주고, 파일을 넘겨 실행합니다.
- **자체 변환기는 보조입니다** (`caelab/solvers`). 네이티브 전처리기가 없는 솔버 경로에서만 쓰며, 지원표는 변환기 안에 한 곳에서 선언합니다.
- **자체 공통 형식은 축소됐습니다.** 입력 모델 전체를 담지 않고, 프로젝트 상태와 토론 근거로 쓸 결과 요약(`caelab/result_summary`)만 공통으로 둡니다. 모델 스키마(`caelab/model`)는 보조 변환기와 검사·실행 경로가 쓰는 내부 표현입니다.
- 입력 검사만 하고 허용 목록은 없습니다 (`caelab/validate`). 검증 사례가 없다는 이유로 실행을 막지 않고, 결과에 검증 상태를 표시합니다.
- 실행 소유는 JobManager 하나, 명령 경로는 Workbench 하나입니다 (`caelab/jobs.py`, `caelab/workbench.py`). GUI·CLI·MCP는 전부 이 경로로 들어옵니다.
- 프로젝트를 여는 것만으로는 계산이나 LLM 호출이 일어나지 않습니다.

## 현재 범위

- 연결 층: 프로그램 8종 등록(FreeCAD·Salome·Gmsh·CalculiX·Code_Aster·OpenCourant·FEniCSx·ParaView), 환경 변수 우선·PATH 탐색, 실행 명령 생성(실행 자체는 호출자 결정).
- 결과 요약: 솔버·성공 여부·최대 변위·반력 합·검증 상태·로그 꼬리를 솔버 중립 형식으로 정리하고 한국어 한 단락으로 서식화.
- 보조 변환기: CalculiX 1종. 덱 생성(.inp), ccx 실행, 결과(.frd) 판독.
- 수치 엔진: `caelab/numerical` — DOE·최소제곱 적합·차분진화 최적화·UQ·Sobol 민감도·RBF 대리모델. 평가자는 값→응답 딕셔너리 계약이며, `model_binding`이 공통 모델의 임의 숫자 위치를 변수로, 실행 결과의 임의 스칼라를 응답으로 연결한다.
- PDE 라인: `caelab/pde` — FEniCSx(dolfinx) 실행 환경. 공통 모델 스키마에 넣지 않고 자체 형식을 유지한다.
- GUI: PySide6 (`apps/workbench_qt`). 부모는 ANSYS Workbench식 프로젝트 화면(도구상자·상자 연결 도식·상태 표시·전체 실행), 상자는 자기 프로그램의 연결 상태를 표시하고 설치된 프로그램을 연다. 자체 작업 창은 확인·검사·실행·요약 수준으로 제한한다.

## 아직 없는 것

- Code_Aster(Salome) 설치와 계산 검증, Windows 실화면 수용 검증이 남았습니다. 전문가 토론 패널은 구현 마무리 중이고, FreeCAD 파일 읽기와 ParaView 결과 열기는 확인된 상태입니다. OpenRadioss는 배포가 끝나 후계 OpenCourant로 등록·검증됐습니다.
