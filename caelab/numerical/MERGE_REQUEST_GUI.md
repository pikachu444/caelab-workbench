# 병합 요청 — 최적화 패널 메인 창 결합 (통합 채팅 담당자께)

이 채팅(002 수치 엔진·최적화, 화면 소유 지정받음)의 소관으로 새 파일만
추가했습니다: `apps/workbench_qt/optimization_panel.py`(신규),
`tests/test_optimization_panel.py`(신규). 메인 창 파일은 직접 고치지
않았으니, 아래 결합을 통합 채팅에서 반영해 주세요.

## 0. 검증 완료 (2026-10-21 실측, 리눅스 오프스크린)

- 패널 테스트 4개 통과, 수치 엔진 19개 포함 23개 통과,
  전체 65개 통과·7개 건너뜀.
- 5단계 전 흐름을 해석해가 있는 볼 함수 (최소값 0, 위치 (0.5, −1))로
  실행: DOE 실제 평가 24개(학습 16 + 테스트 8) → 근사모델 정확도
  RMSE 0.245·MAE 0.174·최대오차 0.452 (테스트 DOE 8개 기준) →
  최적화 최적점 (0.507, −1.012), 근사 예측값 −0.00756 → 재실행
  실제값 0.000192, 절대 오차 0.00775 (DOE 범위 대비 0.1% 미만).
  근사모델이 최소값 아래로 예측한 것을 재실행 검증이 잡아낸 사례입니다.
- GUI 완료 판정은 Windows 실화면 기준입니다. 위는 리눅스
  오프스크린 확인이며, Windows 실화면 확인 전에는 완료로 쓰지
  마세요 (005 채팅의 Windows 확인 대상에 넣어 주세요).

## 1. `apps/workbench_qt/main_window.py` — 도킹 추가 (제안)

전문가 패널과 같은 방식입니다. 전문가 도크를 만든 자리 뒤에:

```python
from .optimization_panel import OptimizationPanel

self.optimization_panel = OptimizationPanel()
self.optimization_dock = QDockWidget("최적화")
self.optimization_dock.setWidget(self.optimization_panel)
self.addDockWidget(Qt.RightDockWidgetArea, self.optimization_dock)
self.tabifyDockWidget(props_dock, self.optimization_dock)
self.optimization_dock.hide()
```

메뉴·도구모음에 "최적화 패널 열기" 동작 추가 (전문가 메뉴 옆
"해석" 메뉴가 적당합니다):

```python
def open_optimization_panel(self) -> None:
    self.optimization_dock.show()
    self.optimization_dock.raise_()
```

## 2. 모델 연결 (후속 결정 필요)

패널은 평가 함수를 주입받는 구조이고, 공통 모델 연결 지점은
`OptimizationPanel.configure_with_model(model, variable_specs,
run_fn, response_specs)`로 준비해 뒀습니다 (`caelab.numerical.
ModelEvaluator` 사용). 메인 창에서 붙이려면 사용자가 변수로 쓸
모델 경로를 고르는 방법이 필요한데, 이는 패널 1단계 표에
"모델 경로" 열을 추가하는 후속 작업으로 제안합니다. 1차 결합은
도킹 + 빈 패널 열기까지로 해 주세요.

## 3. `CURRENT_STATE.md` — 최적화 화면 줄 추가 (제안 문구)

- "최적화 화면: HyperStudy식 5단계 패널(설정→DOE→근사모델→최적화→
  재실행 검증), 자체 수치 엔진 사용, 단일 목적까지. 리눅스
  오프스크린 테스트 4개 통과, Windows 실화면 확인은 아직."
