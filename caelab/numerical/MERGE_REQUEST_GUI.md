# 병합 요청 — 최적화 패널 메인 창 결합 (통합 채팅 담당자께)

> **과거 인계 기록:** 아래 내용은 당시 작업 환경의 요청·보고를 보존한 것이다. 채팅 번호·개인 경로·반영/완료 표현을 현재 작업 지시나 원격 main 상태로 사용하지 않는다. 현재 운영은 [루트 AGENTS](../../AGENTS.md), 구현 상태는 [CURRENT_STATE](../../CURRENT_STATE.md)를 따른다.

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

## 2. 모델 연결 (완료 — 2026-10-21 후속 작업)

패널 1단계 표에 "모델 경로" 열과 "응답 모델 경로" 입력을
추가했습니다. 사용자가 표에서 고른 경로 그대로
`ModelEvaluator`를 다시 만들어 연결하며, 경로는 적용 시점에
실재·숫자 여부를 검사합니다(하나만 경로를 쓰는 혼합 입력은
한 줄 오류로 거절). 메인 창에서 남는 일은 모델과 run_fn을
넘기는 것뿐입니다:

```python
# 열린 모델과 CalculiX 변환기 기준 예시
def _opt_run_fn(model):
    import tempfile
    from caelab.solvers.calculix import CalculiXConverter

    converter = CalculiXConverter()
    with tempfile.TemporaryDirectory() as td:
        return converter.run(model, converter.write_input(model, td), td)

self.optimization_panel.configure_with_model(
    model, variable_specs, _opt_run_fn, response_specs)
```

variable_specs/response_specs의 기본값(어떤 경로를 처음부터
채워 둘지)은 통합에서 정해 주세요. 패널 쪽은 사용자가 표에서
바꿀 수 있습니다.

검증 (실측): cantilever 예제로 실제 ccx 끝단 실행 — DOE 실제
평가 12회 실패 0, 근사 RMSE 1.80e-5 m, 최적점 E=2.98e11 Pa·
fy=−516 N, 재실행 실제값 4.818e-5 m이 독립 직접 솔버 실행값과
일치 (기준 모델 1.328e-4 m의 36%). 오프스크린 테스트는 패널
파일에 8개 (모델 연결 4개 포함).

## 3. `CURRENT_STATE.md` — 최적화 화면 줄 추가 (제안 문구)

- "최적화 화면: HyperStudy식 5단계 패널(설정→DOE→근사모델→최적화→
  재실행 검증), 자체 수치 엔진 사용, 단일 목적까지. 리눅스
  오프스크린 테스트 4개 통과, Windows 실화면 확인은 아직."
