# AGENTS.md — caelab-workbench 공통 규칙

- 제품 정의: 워크벤치는 각 프로그램을 연결하는 프로그램이고, 그 결과들을 근거로 토론하는 프로그램이다.
- 설계 기준은 ~/workspace/cae_lab_redesign/DESIGN.md 하나뿐이다. 이 파일들과 충돌하면 설계 문서를 따른다. 기존 저장소(autonomous-cae-lab)의 이슈를 기준으로 삼지 않는다.
- 병렬 작업 규칙: 작업 채팅은 자기 모듈 폴더만 수정하고 로컬 커밋까지만 한다. 원격 푸시와 공유 파일(caelab/connections.py, CURRENT_STATE.md, ARCHITECTURE.md, AGENTS.md 파일들) 변경은 통합 채팅(000)만 한다. 공유 파일 변경이 필요하면 병합 요청으로 올린다.
- 작업 위치 (git worktree): 000 통합은 본 폴더 ~/workspace/caelab-workbench (브랜치 main). 작업 채팅은 자기 worktree에서만 작업하고 본 폴더를 직접 고치지 않는다. 001은 ~/workspace/caelab-workbench-001-solvers (work/001-solvers), 002는 ~/workspace/caelab-workbench-002-numerical (work/002-numerical), 003은 ~/workspace/caelab-workbench-003-pde (work/003-pde), 004는 ~/workspace/caelab-workbench-004-assist (work/004-assist), 005는 ~/workspace/caelab-workbench-005-gui (work/005-gui).
- 작업을 시작할 때 현재 폴더와 브랜치가 자기 것인지 먼저 확인한다.
- 병합 절차: 통합이 작업 브랜치를 로컬 main에 합치고 전체 테스트를 돌린 뒤, 바뀐 파일을 원격 병합 브랜치(merge/<주제>)에 push_files로 올려 PR을 만들고, GitHub에서 PR을 병합해 원격 main에 반영한다. 병합 후 통합이 로컬 main을 작업 브랜치들에 동기화한다. worktree로도 의미 충돌은 못 막으므로 통합 단계 전체 테스트는 필수다.
- 보고 규칙: 완료는 실행으로 확인한 수치로만 말한다. 추정이나 미확인을 확인됐다고 쓰지 않는다. GUI는 Windows 실화면 확인 전에는 완료라고 하지 않는다.
- 외부 테스트 자산(OpenCourant QA 덱 등 CC BY-NC)은 저장소에 넣지 않는다. ~/workspace/tools 아래에 두고 환경변수로 참조한다.
- 원격 푸시 수단(push_files)은 바이너리를 올릴 수 없다. 커밋은 코드와 텍스트만.
