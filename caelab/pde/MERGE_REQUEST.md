# 병합 요청 — PDE 라인 (통합 채팅 담당자께)

이 채팅(PDE 라인)의 소관 파일만 추가했습니다: `caelab/pde/`(신규 패키지),
`tests/test_pde_line.py`(신규). 공유 파일은 건드리지 않았으니, 아래는
통합 채팅에서 반영해 주실 내용입니다.

## 0. 검증 완료 (2026-10-21 실측)

- 설치: `~/workspace/tools/fenicsx-env` (micromamba, conda-forge),
  dolfinx 0.11.0, PETSc 3.25.6, Python 3.14.8, 용량 1.8GB.
- 포아송 제조해 대조 (u=sin(πx)sin(πy), P1, 메시 8/16/32):
  L2 오차 2.113e-2 → 5.377e-3 → 1.350e-3, 수렴 차수 1.974·1.993
  (이론값 2), H1 세미노름 차수 0.989·0.997 (이론값 1),
  상대 잔차 3.87e-14 (가장 촘촘한 메시). 판정 3개 모두 통과.
- 테스트: `tests/test_pde_line.py` 9개 통과 (실제 계산 1개 포함,
  `CAELAB_FENICSX_PYTHON` 설정 시).

## 1. `caelab/connections.py` — FEniCSx 등록 (제안)

`PROGRAMS`에 아래 항목 추가를 제안합니다. FEniCSx는 단독 실행 파일이
아니라 dolfinx가 설치된 파이썬 실행 환경이라, 찾기 방식은 원본
(autonomous-cae-lab)의 관례를 따라 환경변수 `CAELAB_FENICSX_PYTHON`이
본선입니다. `caelab/pde/runner.py`도 같은 환경변수에서만 실행 경로를
받습니다(패키지 규칙: 실행 경로를 코드에 박지 않음).

```python
ProgramSpec(
    "fenicsx", "FEniCSx", "solver", "CAELAB_FENICSX_PYTHON",
    (),
    "PDE 라인 실행 환경. 돌린x가 든 파이썬을 환경변수로 지정한다. "
    "PDE는 공통 모델 스키마에 넣지 않고 자체 형식을 유지한다 (DESIGN §3.3).",
),
```

주의: `tests/test_connections_summary.py`의
`test_registry_covers_the_connected_programs`가 솔버 집합을
`{"calculix", "codeaster", "opencourant"}`와 정확히 같다고 단언합니다.
등록 시 이 단언에 `"fenicsx"`를 더해 주셔야 합니다.

## 2. `CURRENT_STATE.md` — PDE 줄 추가 (제안 문구)

- "FEniCSx PDE 라인: ~/workspace/tools/fenicsx-env (dolfinx 0.11.0)에
  설치, 포아송 제조해 대조 통과 (L2 오차 1.35e-3 @32×32, 차수 1.99).
  공통 스키마가 아닌 자체 형식 유지."

## 3. 환경변수 확정 (제안)

- `CAELAB_FENICSX_PYTHON=~/workspace/tools/fenicsx-env/bin/python`
  (이 변수가 없으면 PDE 실행은 "찾지 못함"으로 실패하고, 테스트의
  실제 계산 항목은 건너뜁니다.)
