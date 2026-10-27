# caelab.mdo 수치 최적화 엔진 운영 규칙

[루트 AGENTS.md](../../AGENTS.md) 및 [caelab/AGENTS.md](../AGENTS.md)를 적용한다. 이 폴더는 수치 알고리즘 및 MDO 코어 담당 개발자가 소유하며, GUI 코드를 포함하지 않는다.

## 1. 패키지 책임과 경계
- **책임 범위**:
  - 설계 공간 및 변수 모델링 (`variables.py`)
  - 목적함수 및 3상태 제약조건 평가 (`objectives.py`, `constraints.py`)
  - 실험계획법(DoE) 표본 생성 (`doe.py`)
  - 전산 대리모델(Kriging/GPR, RBF, PRS) 적합 및 불확실성 예측 (`surrogate.py`)
  - 글로벌 민감도 분석(Sobol, Morris) (`sensitivity.py`)
  - 국소/전역/다목적 최적화 알고리즘 (`optimize.py`)
  - 파레토 프론티어, 초체적, Knee Point 의사결정 (`pareto.py`)
  - 스터디 이력 관리 및 오케스트레이션 (`study_manager.py`)
  - 네이티브 덱 파라메트릭 토큰 치환 (`inp_binder.py`)
  - 솔버 출력 로그/결과 스칼라 응답 축소 (`responses.py`)
- **의존성 경계**:
  - `caelab.mdo` 내의 모든 모듈은 **PySide6, Qt 및 GUI 모듈을 절대 import하지 않는다**.
  - 순수 Python 및 수치 라이브러리(NumPy, SciPy, scikit-learn, SALib, pymoo)만 사용하며 헤드리스(Headless) 환경에서 완전 독립 동작해야 한다.
  - 외부 선택적 라이브러리(pymoo, SALib)는 런타임에 안전하게 임포트하고, 부재 시 명확한 ImportError 또는 fallback 안내를 제공한다.

## 2. 수치 무결성 및 실패 처리 원칙
- **해석 실패의 수치화 금지**:
  - 솔버 충돌, 발산, 파일 누락, NaN/Inf 반환 등 해석 실패를 임의의 유효 수치나 모의 성공으로 조작하지 않는다.
  - 실패한 설계점은 명시적으로 `EvaluationStatus.FAILED` 및 `Feasibility.INFEASIBLE`로 기록하고 패널티 목적함수를 적용한다.
- **재현성과 난수 제어**:
  - DoE(LHS, Sobol) 및 확률적 최적화(DE, NSGA-II)는 반드시 `seed` 파라미터를 통해 100% 수치 재현성을 보장한다.
- **배열 불변성**:
  - 외부로 반환되는 TDP 매트릭스 및 파레토 최적해 데이터는 읽기 전용(`copy()` 또는 불변 튜플)으로 제공하여 외부 조작으로 인한 내부 상태 오염을 방지한다.

## 3. 검증 및 테스트 요건
- 새 수치 기능이나 계약 변경은 반드시 `tests/test_mdo_*.py`에 단위/회귀 검사를 추가한다.
- 변경 후 `python -m pytest -q (Get-ChildItem tests\test_mdo_*.py)`를 실행하여 전수 통과를 확인한다.
