# 현재 상태

- 제품 정의 (사용자 확정): 워크벤치는 각 프로그램을 연결하는 프로그램이고, 그 결과들을 근거로 토론하는 프로그램이다.
- 진행 단계: 골격(0단계) 완료, 연결(1단계) 진행 중.
- 본선: 솔버별 네이티브 전처리기를 그대로 쓴다 (CalculiX=FreeCAD FEM, Code_Aster=Salome, OpenRadioss=Gmsh). 자체 공통 형식은 입력 모델 전체가 아니라 프로젝트 상태와 토론용 결과 요약으로 축소한다. 자체 변환기(`caelab/solvers`)는 전처리기가 없는 경로용 보조다.
- 된 것: 프로그램 연결 층(`caelab/connections` — FreeCAD·Salome·Gmsh·CalculiX·ParaView 찾기/상태/실행 명령), 토론 근거용 결과 요약(`caelab/result_summary`), 모델 스키마·저장/재개, 입력 검사, CalculiX 보조 변환기(덱 생성·실행·결과 판독), JobManager·Workbench 단일 경로, 프로젝트 화면에서 상자별 프로그램 연결 상태 표시와 설치된 프로그램 실행 열기.
- 확인한 것: 자동 테스트 통과 (리눅스). 끝단 테스트는 실제 CalculiX 2.23으로 외팔보를 계산해 반력 평형(적용 하중과 0.5% 이내 일치)을 확인했고, 결과 요약이 같은 수치를 담는지도 대조했습니다. GUI는 가상 화면(Xvfb) 캡처로 확인 (Windows 실화면 아님).
- 확인 안 된 것: Windows 실화면 GUI 수용, FreeCAD 파일(.FCStd) 읽기, ParaView로 결과 파일 열기, LLM 전문가 토론 흐름.
- 다음: FreeCAD 파일 읽기 연결 → ParaView 결과 열기 → 두 번째 솔버(Code_Aster+Salome) 연결 → 전문가 토론 패널.
