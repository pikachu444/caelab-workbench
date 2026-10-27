# MDO Studio GUI 개발 및 세션 운영 규칙

[루트 AGENTS.md](../../../AGENTS.md) 및 [apps/workbench_qt/AGENTS.md](../AGENTS.md)를 적용한다. 이 폴더는 MDO Studio 전용 GUI 컴포넌트 담당 개발자가 소유한다.

## 1. 패키지 책임과 구성
- **책임 범위**:
  - MDO Studio 메인 윈도우 워크스페이스 (`window.py`)
  - 스터디 설정 도크 위젯 (`setup_dock.py`)
  - 설계점 평가 매트릭스 테이블 모델 (`design_points_model.py`)
  - 3-Tab 엔지니어링 분석 대시보드 뷰 (`views/pareto_view.py`, `views/response_surface_view.py`, `views/sensitivity_view.py`)
  - 독립 실행 엔트리포인트 (`__main__.py`)
- **실행 모드**:
  - Workbench 통합 모드: 메인 윈도우 메뉴 및 툴바에서 호출.
  - 독립 실행 모드: `python -m apps.workbench_qt.mdo_studio` 명령으로 단독 실행 가능해야 한다.

## 2. GUI 설계 및 테마 규칙
- **다크/라이트 테마 대비 일관성**:
  - `apps/workbench_qt/theme.py`의 `current_theme_mode()` 및 테마 토글 신호를 준수한다.
  - 위젯 배경이나 텍스트 색상을 하드코딩(예: 순백색 `#ffffff` 고정)하여 다크 모드에서 배경 번짐이나 글자 가독성 저하를 유발하지 않는다.
  - `QScrollArea` 뷰포트는 `background: transparent;` 스타일을 적용해 부모 테마 배경을 투과시킨다.
- **모달 대화상자 안전장치**:
  - 사용자 피드백(`QMessageBox.information`, `QMessageBox.warning` 등)은 GUI 사용자에게 명확한 상태를 전달하되, 자동화 테스트 및 배치 환경에서 스크립트 실행을 영구 블로킹하지 않도록 모킹 가능한 구조를 유지한다.
- **원본 덱 보호 원칙**:
  - "Set as Current Design Point" 등 활성 덱을 수정하는 동작은 반드시 원본 파일의 백업 사본(`*.inp.bak`)을 디스크에 선제 생성한 후 파라미터를 갱신한다.

## 3. 테스트 및 검증 규칙
- 모든 Qt GUI 테스트는 헤드리스 CI 환경 지원을 위해 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`를 명시한다.
- 변경 후 `python -m pytest tests/test_mdo_studio_gui.py tests/test_mdo_deck_and_responses.py`를 실행하여 통과를 검증한다.
