# AGENTS.md — caelab-workbench 공통 규칙

- 제품 정의: 워크벤치는 각 프로그램을 연결하는 프로그램이고, 그 결과들을 근거로 토론하는 프로그램이다.
- 설계 기준은 ~/workspace/cae_lab_redesign/DESIGN.md 하나뿐이다. 이 파일들과 충돌하면 설계 문서를 따른다. 기존 저장소(autonomous-cae-lab)의 이슈를 기준으로 삼지 않는다.
- 병렬 작업 규칙: 작업 채팅은 자기 모듈 폴더만 수정하고 로컬 커밋까지만 한다. 원격 푸시와 공유 파일(caelab/connections.py, CURRENT_STATE.md, ARCHITECTURE.md, AGENTS.md 파일들) 변경은 통합 채팅(000)만 한다. 공유 파일 변경이 필요하면 병합 요청으로 올린다.
- 보고 규칙: 완료는 실행으로 확인한 수치로만 말한다. 추정이나 미확인을 확인됐다고 쓰지 않는다. GUI는 Windows 실화면 확인 전에는 완료라고 하지 않는다.
- 외부 테스트 자산(OpenCourant QA 덱 등 CC BY-NC)은 저장소에 넣지 않는다. ~/workspace/tools 아래에 두고 환경변수로 참조한다.
- 원격 푸시 수단(push_files)은 바이너리를 올릴 수 없다. 커밋은 코드와 텍스트만.
