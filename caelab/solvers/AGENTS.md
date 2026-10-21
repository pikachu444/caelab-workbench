# caelab/solvers 규칙

- 소유: 001_솔버 설치·연계 채팅.
- 솔버별 보조 변환기, 실행, 결과 판독을 둔다. 전처리는 솔버별 네이티브 프로그램(FreeCAD, Salome, Gmsh)이 본선이고, 여기는 네이티브 전처리기가 없는 경로용 보조만 만든다.
- 새 솔버를 붙이면 tests/test_solver_smoke.py에 설치된 환경에서만 도는 연기 테스트를 추가한다.
- 반력 같은 검증 수치는 적용 하중과 대조한 값으로 보고한다.
- FreeCAD가 만든 CalculiX 덱의 .frd에는 반력(FORC) 블록이 없다. 반력은 .dat의 NODE PRINT에서 읽는다.
