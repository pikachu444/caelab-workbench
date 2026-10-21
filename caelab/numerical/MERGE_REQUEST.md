# 병합 요청 — 수치 엔진·최적화 (통합 채팅 담당자께)

이 채팅(002 수치 엔진·최적화)의 소관 파일만 추가했습니다:
`caelab/numerical/`(신규 패키지: `core·variables·fit·analysis·uq·
sensitivity·events·model_binding`), 테스트 3개
(`tests/test_numerical_engine.py`, `tests/test_numerical_model_binding.py`,
`tests/test_numerical_sensitivity.py`). 공유 파일은 건드리지 않았습니다.

## 0. 검증 완료 (2026-10-21 실측, caelab-venv: numpy 2.3.5, scipy 1.17.0)

- 신규 테스트 19개 전부 통과. 전체 테스트 52개 통과, 7개 건너뜀
  (건너뜀은 기존 솔버 설치 의존 항목들).
- DOE: SciPy LHS 16개, 시드 재현·구간·층화 확인. 실패 평가는 이유와 함께
  행으로 남음.
- 최적화: 볼 함수 (x−0.5)²+(y+1)² 최소값 0에 대해 최적 목적값 1.4e-4
  이하(세대 10~15), 제약 x≤0에서는 해석해 0.25에 ±0.02로 도달.
- 적합: 알려진 계수 k=3을 최소제곱으로 복원 (오차 1e-6 이내),
  holdout RMSE 약 7e-14 — holdout은 조정에 쓰지 않음.
- UQ: U(0,10) 두 개 합, 256개 표본 — 평균 9.999 (해석해 10),
  10 초과 비율 0.504 (해석해 0.5).
- 민감도(Sobol, SALib): y=3a+b, 표본 1,024개 — S1이 0.900 / 0.100
  (해석해 0.9 / 0.1), ST도 0.900 / 0.100.
- 대리모델(RBF): 선형 합 응답에 대해 holdout RMSE 5.5e-15.
- 공통 모델 연결: 재료 강성(materials[0].params.youngs_modulus,
  이름으로도 지정 가능)·하중(loads[TIP_FORCE].values.fy)을 변수로,
  ResultSet의 max_displacement·reaction_totals.fy를 응답으로 연결해
  DOE·최적화 실행 확인. 원본 모델은 바뀌지 않음(복사본에서만 계산).
  최적화 결과는 해석적 모서리 E=3.0e11, fy=−500과 5% 이내로 일치.

## 1. `pyproject.toml` — 의존성 추가 (요청)

현재 `dependencies = []`라서 깨끗한 환경에서는 `caelab.numerical`을
읽을 수 없습니다. 아래 추가를 요청합니다 (이 채팅에서는 고치지 않음).

```toml
dependencies = ["numpy>=1.26", "scipy>=1.11"]

[project.optional-dependencies]
sensitivity = ["SALib>=1.4"]
```

SALib가 없으면 Sobol 함수만 `RuntimeError`로 이유를 밝히고,
테스트 파일은 건너뜀 사유를 표시하고 넘어갑니다.

## 2. `CURRENT_STATE.md` — 수치 엔진 줄 추가 (제안 문구)

- "수치 엔진(`caelab/numerical`): 원본의 DOE·적합·최적화·UQ·민감도를
  이식하고 공통 모델 변수·응답에 연결. 테스트 19개 통과
  (Sobol S1 0.900/0.100, UQ 평균 9.999 등 해석해 대조)."

## 3. `ARCHITECTURE.md` — 현재 범위에 한 줄 추가 (제안 문구)

- "수치 엔진: `caelab/numerical` — DOE·최소제곱 적합·차분진화 최적화·
  UQ·Sobol 민감도·RBF 대리모델. 평가자는 값→응답 딕셔너리 계약이며,
  `model_binding`이 공통 모델의 임의 숫자 위치를 변수로, 실행 결과의
  임의 스칼라를 응답으로 연결한다."

## 4. connections.py·모델 스키마 — 변경 없음

- 외부 프로그램 실행이 없어 `connections.py` 등록은 필요 없습니다.
- 모델 스키마 변경도 필요 없습니다. 연결은 경로 문자열로만 하며,
  모델을 바꾸지 않고 복사본에 값을 넣습니다.

## 5. 이식하지 않은 원본 부분 (숨기지 않고 보고)

원본의 아래 부분은 의존하는 하위 체계가 새 저장소에 없어서 넣지
않았고, 해당 입력은 조용히 바꾸지 않고 명확한 오류로 거절합니다.

- 병렬 실행(process/batch) — 직렬만 이식.
- `slsqp_adjoint` 최적화 — adjoint 하위 체계 없음.
- 합집합(joint) 분포 UQ, 이산·범주·종속 변수 — 차원식 하위 체계 없음.
- 파일 배치 내보내기/가져오기, 연구 기록(record) 저장 체계.
