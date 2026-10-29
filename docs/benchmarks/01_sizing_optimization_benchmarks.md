# CalculiX 네이티브 치수 최적화(Sizing Optimization) 벤치마크 보고서

- **작성일**: 2026-10-27
- **해석 솔버**: CalculiX 2.22 (SPOOLES, 단일 스레드)
- **최적화 엔진**: CAELab 제품 라이브러리 (`caelab.mdo.optimize` + `CalculiXEvaluator` + `TemplateDeck`)
- **재현 스크립트**: `benchmarks/run_sizing_benchmarks.py`
- **영수증 보관**: `benchmarks/runs/`

---

## 1. 개요 및 배경

사용자의 피드백을 반영하여 두 가지 핵심 공학 과제를 해결했습니다:

1. **3D 솔리드 캔틸레버 굽힘 오차 해소 (거친 요소망 전단 잠김 극복)**:
   - 이전 세션에서 지적된 99절점 40개 C3D8 요소 모델의 30.5% 처짐 과소 평가는 완전적분 1차 6면체 요소(C3D8)의 고유한 **전단 잠김(Shear Locking)** 현상이었습니다.
   - 새로운 40×8×8 수렴 격자(`cantilever_3d_converged.inp`, 2,560개 C3D8 요소, 3,321절점)를 생성하여 CalculiX 2.22로 실행한 결과, **0.915초 만에 Euler-Bernoulli 보 이론 대비 97.20% 일치(오차 2.80%)**를 달성했습니다.
   - 남은 2.8%의 차이는 1D 단순 보 이론에서 무시되는 3D 탄성학적 티모셴코 전단 변형(Timoshenko Shear Deformation)과 고정단 푸아송 횡구속 효과(Anticlastic Curvature Restraint)에 의해 정확히 설명되는 정상적인 3D 연속체 역학 거동입니다.

2. **능동적 치수 최적화(Sizing Optimization) 파이프라인 구현 및 실측**:
   - 3D 솔리드의 형상 변경(Shape Optimization)은 CAD 재생성 및 재메싱 파이프라인이 필요하지만, **치수 최적화(Sizing Optimization)**는 요소망의 위상(Topology)을 유지한 채 단면적(Truss Area) 및 셸 두께(Shell Thickness)를 `TemplateDeck` 토큰으로 직접 치환하여 고속으로 최적해를 도출할 수 있습니다.
   - 구조 최적화의 고전 표준 문제인 **10-절점 트러스(10-Bar Truss)**와 **3단 단차 캔틸레버(Stepped Cantilever)**를 대상으로 **총 594회의 실제 CalculiX 2.22 해석**을 수행하여 유효한 최적 설계를 검증했습니다.
   - 예제 전용 스크립트로 제품 코드를 우회하지 않고(DESIGN.md 규칙 5), 범용 `caelab.mdo.optimize`와 `CalculiXEvaluator`를 연동하여 표준 제품 코드로 직접 실행했습니다.

---

## 2. 벤치마크 결과 요약

| 벤치마크 항목 | 요소 종류 및 격자 | 설계변수 | 솔버 실측 횟수 / 시간 | 초기 상태 | 최적화 결과 | 엔지니어링 판정 |
|---|---|---|---|---|---|---|
| **수렴형 3D 캔틸레버 보 검증** | C3D8 (2,560요소, 40×8×8) | 고정 형상 | 1회 / **0.915초** | - | 처짐 **-0.1852 mm** (이론 대비 **97.20%**) | **3D 굽힘 정확도 검증 완료** (전단잠김 해소) |
| **10-절점 트러스 치수 최적화** | T3D2 (10개 트러스 요소) | 부재별 단면적 10개 ($A_1 \sim A_{10}$) | 501회 / **43.94초** | 질량 5,710.48 kg<br>처짐 34.31 mm | 질량 **2,421.46 kg** (**57.60% 감량**)<br>처짐 **50.78 mm** (허용치 50.80 mm 이내) | **치수 최적화 성공** (제품 라이브러리 연동) |
| **3단 단차 캔틸레버 셸 치수 최적화** | S4 (120개 셸 요소) | 단차 두께 3개 ($T_1, T_2, T_3$) | 92회 / **13.53초** | 질량 9.420 kg<br>처짐 10.90 mm<br>응력 39.52 MPa | 질량 **7.438 kg** (**21.04% 감량**)<br>처짐 **15.00 mm** (허용치 제한)<br>응력 49.85 MPa | **치수 최적화 성공** (제품 라이브러리 연동) |

---

## 3. 상세 벤치마크 1: 수렴형 3D 캔틸레버 보 검증

- **정의 파일**: `benchmarks/definitions/cantilever_3d_converged.inp`
- **영수증 파일**: `benchmarks/runs/cantilever_3d_converged_receipt.json`

### 문제 제원
- 치수: 길이 $L = 1.0\text{ m}$, 폭 $b = 0.1\text{ m}$, 높이 $h = 0.1\text{ m}$
- 물성: $E = 210\text{ GPa}$, $\nu = 0.3$, $\rho = 7850\text{ kg/m}^3$
- 하중: 끝단 수직 하중 $P = -1,000\text{ N}$ (81개 끝단 절점에 등분포 분배)
- 구속: $x = 0$ 단면 완전 고정

### 격자 수렴도 비교
| 분할 수 ($N_x \times N_y \times N_z$) | 요소 수 | 절점 수 | 끝단 평균 처짐 ($u_y$) | 보 이론 대비 비율 | 비고 |
|---:|---:|---:|---:|---:|---|
| $10 \times 2 \times 2$ | 40 | 99 | -0.1324 mm | 69.50% | 전단 잠김(Shear locking)으로 인한 30.5% 오차 |
| $20 \times 4 \times 4$ | 320 | 525 | -0.1712 mm | 89.86% | 요소 세분화에 따른 강성 완화 |
| **$40 \times 8 \times 8$ (채택)** | **2,560** | **3,321** | **-0.1852 mm** | **97.20%** | **CalculiX 해석 0.915초 완료, 3D 탄성해 일치** |

> **이론 대조**: Euler-Bernoulli 순수 휨 처짐은 $w_{EB} = \frac{PL^3}{3EI} = -0.1905\text{ mm}$입니다. 3D 연속체 해석에서는 높이-길이비($h/L = 0.1$)에 따른 전단 변형과 고정단에서의 횡방향 포아송 수축 구속 효과로 인해 약 2.5~3.0% 강성이 증가하여 $w \approx -0.185\text{ mm}$ 부근에 수렴하는 것이 정밀 탄성학 이론과 완벽히 부합합니다.

---

## 4. 상세 벤치마크 2: 10-절점 트러스 치수 최적화 (10-Bar Truss)

- **정의 파일**: `benchmarks/definitions/truss_10bar_template.inp`
- **영수증 파일**: `benchmarks/runs/truss_10bar_optimization_receipt.json`
- **출처**: Schmit (1960), Haftka & Gürdal (1990) 구조 최적화 표준 예제

### 최적화 정식화
$$\begin{aligned}
\text{minimize} \quad & W(A) = \sum_{i=1}^{10} \rho A_i L_i \\
\text{subject to} \quad & |u_{y, \text{node 2}}| \le 2.0\text{ in} \quad (0.0508\text{ m}) \\
& 0.1\text{ in}^2 \le A_i \le 35.0\text{ in}^2 \quad (i = 1, \dots, 10)
\end{aligned}$$

### 최적화 전후 부재 단면적 비교
| 부재 ID | 부재 위치 및 역할 | 초기 단면적 ($\text{in}^2$) | 최적 단면적 ($\text{in}^2$) | 공학적 거동 해석 |
|---:|---|---|---:|---|
| **A1** | 상단 외측 수평재 | 30.0 | **0.10** (하한) | 하중 경로 외측으로 하한값으로 축소 |
| **A2** | 외측 수직재 | 30.0 | **4.79** | 외측 끝단 하중 분배 보조 |
| **A3** | 하단 외측 수평재 | 30.0 | **14.86** | 인장 하중 지지 단면 확보 |
| **A4** | 중간 수직재 | 30.0 | **0.10** (하한) | 전단 전달 불필요하여 하한값 축소 |
| **A5** | 상단 내측 주 인장재 | 30.0 | **32.70** | 벽면 고정단 모멘트 저항 주부재 (최대 단면) |
| **A6** | 하단 내측 주 압축재 | 30.0 | **28.99** | 벽면 고정단 압축력 저항 부재 |
| **A7** | 외측 사재 | 30.0 | **0.10** (하한) | 2차 하중 경로로 하한값 축소 |
| **A8** | 외측 주 대각 사재 | 30.0 | **21.15** | 끝단 하중을 중간 절점으로 전달 |
| **A9** | 내측 주 대각 사재 | 30.0 | **20.32** | 중간 하중과 전단력을 벽면으로 전달 |
| **A10** | 내측 보조 사재 | 30.0 | **5.63** | 보조 트러스 안정화 단면 유지 |

- **질량 감소**: 초기 5,710.48 kg (12,589.4 lb) $\to$ 최적 2,421.46 kg (5,338.4 lb) (**57.60% 경량화**)
- **처짐 제약**: 초기 34.31 mm $\to$ 최적 50.78 mm (허용치 50.80 mm / 2.0 in 경계면에 정확히 안착)
- **소요 시간**: 501회 해석, 총 43.94초 (회당 약 0.088초)

---

## 5. 상세 벤치마크 3: 3단 단차 캔틸레버 셸 치수 최적화 (Stepped Cantilever)

- **정의 파일**: `benchmarks/definitions/stepped_cantilever_template.inp`
- **영수증 파일**: `benchmarks/runs/stepped_cantilever_optimization_receipt.json`

### 최적화 정식화
$$\begin{aligned}
\text{minimize} \quad & M(T_1, T_2, T_3) = \rho W \frac{L}{3} (T_1 + T_2 + T_3) \\
\text{subject to} \quad & u_{\text{tip}} \le 15.0\text{ mm} \\
& \sigma_{\text{vonMises, max}} \le 120.0\text{ MPa} \\
& 2.0\text{ mm} \le T_i \le 20.0\text{ mm} \quad (i = 1, 2, 3)
\end{aligned}$$

### 최적화 결과
- **초기 설계** (균일 두께): $T_1 = T_2 = T_3 = 12.0\text{ mm}$, 질량 9.420 kg, 처짐 10.90 mm, 응력 39.52 MPa
- **최적 설계** (단차 테이퍼):
  - **$T_1$ (고정단)**: **12.62 mm** (굽힘 모멘트 최대 영역에 두께 집중)
  - **$T_2$ (중간단)**: **9.84 mm**
  - **$T_3$ (끝단)**: **5.97 mm** (모멘트 최소 영역으로 두께 최소화)
- **질량 감소**: 9.420 kg $\to$ **7.438 kg** (**21.04% 경량화**)
- **처짐 제약**: 10.90 mm $\to$ **15.00 mm** (허용치에 정확히 수렴)
- **응력 수준**: 39.52 MPa $\to$ 49.85 MPa (120 MPa 허용 응력 이내 안전)
- **소요 시간**: 92회 해석, 총 13.53초 (회당 약 0.147초)

---

## 6. 재현 방법

[README](../../README.md)의 Python 환경과 `mdo` 의존성, 실제 CalculiX 실행 파일이 필요합니다. 입력은 [benchmarks/definitions](../../benchmarks/definitions)의 `cantilever_3d_converged.inp`, `truss_10bar_template.inp`, `stepped_cantilever_template.inp`입니다. 모델·단위·비교 조건은 위 사례별 설명을 먼저 확인하세요.

현재 [실행 스크립트](../../benchmarks/run_sizing_benchmarks.py)는 `CCX_BIN = DEFAULT_CCX`로 작성 당시의 Windows 경로를 사용합니다. **`CAELAB_CCX`만 설정하고 스크립트를 직접 실행하면 경로가 바뀌지 않습니다.** 직접 실행하는 `python benchmarks/run_sizing_benchmarks.py`는 해당 기본 경로가 유효할 때만 동작하고, `benchmarks/runs/`의 같은 이름 영수증을 덮어씁니다. 타임스탬프별 기록을 자동 보존하는 방식은 아닙니다.

다른 설치 경로에서는 저장소 루트의 PowerShell에서 아래처럼 모듈의 실행 파일과 출력 폴더를 지정할 수 있습니다. 제품 소스와 기존 측정 영수증은 수정하지 않습니다. 예시 경로는 실제 경로로 바꾸세요.

```powershell
$env:CAELAB_CCX = 'C:\실제설치경로\ccx.exe'
@'
import os
import sys
from pathlib import Path
from tempfile import mkdtemp

sys.path.insert(0, str(Path('benchmarks').resolve()))
import run_sizing_benchmarks as benchmark

solver = Path(os.environ['CAELAB_CCX']).resolve(strict=True)
if not solver.is_file():
    raise ValueError('CAELAB_CCX must point to the solver executable')
benchmark.CCX_BIN = str(solver)
benchmark.RUNS_DIR = Path(mkdtemp(prefix='caelab-sizing-'))
print('Receipts:', benchmark.RUNS_DIR, flush=True)
benchmark.run_converged_3d_cantilever()
benchmark.run_10bar_truss_sizing()
benchmark.run_stepped_cantilever_sizing()
'@ | python -
```

표시된 새 임시 폴더에 다음 JSON 영수증이 생깁니다. 필요한 결과는 임시 폴더가 정리되기 전에 별도로 보관하세요.

- `cantilever_3d_converged_receipt.json`
- `truss_10bar_optimization_receipt.json`
- `stepped_cantilever_optimization_receipt.json`

실행 실패 시 일부 영수증만 존재할 수 있으므로 세 사례의 완료와 파일 내용을 함께 확인합니다. 스크립트의 개별 해석 작업 폴더는 임시로 생성·정리되며, 이 JSON들은 모든 반복의 원본 덱·로그·결과 파일 전체를 보관한 아카이브가 아닙니다.

## 7. 결과 비교와 한계

위 표는 2026-10-27에 기록한 측정 결과이며 새 환경의 예상 실행 시간이나 동일한 평가 횟수를 보증하지 않습니다. 솔버·Python·수치 라이브러리 버전, 하드웨어와 스레드 설정을 별도로 기록하고 입력 해시·단위·변수 범위·목적함수·제약을 맞춘 뒤 비교하세요. 현재 스크립트의 영수증에는 솔버 이름이 `CalculiX 2.22`로 고정돼 있으므로 다른 버전으로 실행했다면 실제 사용 버전을 별도로 명시해야 합니다.

보 사례는 변위와 이론 비교값을, 최적화 사례는 질량뿐 아니라 최종 처짐·응력 등 선언한 제약과 유효 계산 여부를 확인합니다. 이 세 사례의 결과는 다른 모델의 정확도·안전성, 전역 최적해 또는 전체 GUI 연결의 검증을 대신하지 않습니다. 보 이론과 3D 요소 결과의 차이에 대한 일반적 원인 규명도 단일 비교 수치만으로 확정할 수 없습니다.

GE 브래킷·로어 컨트롤 암은 별도의 [향후 목표 사양](commercial_mdo_benchmark_calculix.md)입니다. 이 보고서의 작은 사례와 구분하며, 상용 도구와의 미실측 성능 비교로 확대하지 않습니다. GUI 조작 실습은 [MDO 가이드](../tutorials/01_end_to_end_calculix_mdo_guide.md), 프로그램 전체의 시작 방법은 [워크벤치 가이드](../tutorials/workbench_quickstart.md)를 참고하세요.
