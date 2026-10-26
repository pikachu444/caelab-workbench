# 상용 MDO 벤치마크 검증 계획: Abaqus / OptiStruct 표준 문제 대비 CalculiX 비교 검증

본 문서는 상용 최적화 소프트웨어(Altair OptiStruct, Dassault Systèmes Tosca/Isight, Abaqus)의 공인 벤치마크 매뉴얼에 수록된 표준 문제를 CalculiX 기반으로 모델링하고, caelab-workbench의 클린슬레이트 MDO 엔진과 1:1 수치 성능 및 최적해를 정밀 비교하는 계획과 기술 근거를 정의합니다.

---

## 1. 벤치마크 표준 문제 선정

구조 치수 최적화(Sizing Optimization) 분야에서 전 세계 학술 및 산업계의 표준 검증 문제 2종을 선정합니다.

### 1.1 표준 문제 1: 10-Bar Truss Benchmark (10부재 트러스 경량화)
- **출처 및 공인 레퍼런스**:
  - Haftka, R. T., & Gürdal, Z. (1992), *Elements of Structural Optimization*.
  - Altair OptiStruct Verification Problem: `OS-V: 0010` (10-Bar Plane Truss Sizing Optimization).
  - Abaqus Example Problems Manual: Sizing optimization of a planar truss.
- **기하 및 하중 조건**:
  - 6개 절점, 10개 트러스 부재로 구성된 평면 캔틸레버 트러스 구조 (스팬 $L = 360\text{ in}$, 높이 $H = 360\text{ in}$).
  - 노드 2와 노드 4에 $Y$축 하향 집중 하중 $P = 100,000\text{ lbf}$ ($444.8\text{ kN}$) 재하.
  - 재질: 알루미늄 ($E = 1.0 \times 10^7\text{ psi}$, $\rho = 0.1\text{ lb/in}^3$).
- **설계 변수**:
  - 10개 부재의 단면적 $A_1 \sim A_{10} \in [0.1, 35.0]\text{ in}^2$ (연속 변수 10개).
- **목적 및 제약 조건**:
  - 목적: 총 트러스 중량 최소화 $\min(\text{Weight})$
  - 응력 제약: 전 부재 $|\sigma_i| \le 25,000\text{ psi}$ ($172.4\text{ MPa}$)
  - 변위 제약: 모든 절점 수직/수평 변위 $|\delta_x|, |\delta_y| \le 2.0\text{ in}$ ($50.8\text{ mm}$)
- **공식 레퍼런스 최적해 (Published Benchmark Optimum)**:
  - **최적 총 중량**: **$5060.85\text{ lbs}$ ($2295.5\text{ kg}$)**
  - 활성 제약(Active Constraints): 부재 1, 3, 4, 7, 8, 9의 응력 한계 및 절점 2, 3의 수직 변위 한계.

---

### 1.2 표준 문제 2: Sizing Optimization of Stepped Cantilever Beam (5단 단차 보)
- **출처**:
  - Altair HyperStudy / OptiStruct Structural Sizing Benchmark Manual.
- **기하 및 하중 조건**:
  - 5개 단차(Step)를 가진 외팔보, 선단 수직 하중 $P$.
  - 설계 변수: 각 단차의 폭 $b_1 \sim b_5$ 및 높이 $h_1 \sim h_5$ (총 10개 변수).
  - 목적: 체적 최소화 $\min(\text{Volume})$.
  - 제약: 각 단차의 von Mises 응력 $\le \sigma_{\text{allow}}$, 선단 최대 처짐 $\le \delta_{\text{allow}}$.

---

## 2. CalculiX 모델링 및 덱 연동 구조

CalculiX는 Abaqus 키워드 문법과 1:1에 가까운 호환성을 제공하므로, 상용 벤치마크 덱을 무결성 있게 그대로 활용할 수 있습니다.

### 2.1 10-Bar Truss CalculiX 인풋 덱 구성 (`10bar_truss.inp`)
```inp
*HEADING
10-BAR TRUSS BENCHMARK (HAFTKA & GURDAL / OPTISTRUCT OS-V0010)
*NODE
1, 720.0, 360.0, 0.0
2, 720.0,   0.0, 0.0
3, 360.0, 360.0, 0.0
4, 360.0,   0.0, 0.0
5,   0.0, 360.0, 0.0
6,   0.0,   0.0, 0.0
*ELEMENT, TYPE=T3D2, ELSET=BAR1
1, 3, 1
*ELEMENT, TYPE=T3D2, ELSET=BAR2
2, 5, 3
*ELEMENT, TYPE=T3D2, ELSET=BAR3
3, 4, 2
...
*SOLID SECTION, ELSET=BAR1, MATERIAL=ALUMINUM
@@A1@@
*SOLID SECTION, ELSET=BAR2, MATERIAL=ALUMINUM
@@A2@@
...
*BOUNDARY
5, 1, 3, 0.0
6, 1, 3, 0.0
*CLOAD
2, 2, -100000.0
4, 2, -100000.0
*STEP
*STATIC
*NODE PRINT, NSET=NALL
U
*EL PRINT, ELSET=EALL
S
*END STEP
```

- **토큰 연동**: 각 부재의 단면적을 `@@A1@@` ~ `@@A10@@`으로 치환.
- **응답 추출 (`caelab.mdo.responses`)**:
  - CalculiX 계산 후 생성되는 `.dat` 파일에서 정규식으로 절점 변위(`U2`)와 요소 축응력(`S11`)을 파싱.
  - 단면적 합산으로 총 중량($W = \rho \sum L_i A_i$) 계산.

---

## 3. 정량적 성능 비교 및 판정 기준

동일한 인풋 덱에 대해 상용 툴(OptiStruct / Abaqus) 공인 데이터와 우리 MDO 엔진의 해석 결과를 대조합니다:

| 검증 평가 지표 | 정의 및 수식 | 상용 동등 합격 기준 |
|---|---|:---:|
| **1. 최적해 갭 (Optimality Gap)** | $\frac{\|W_{\text{engine}} - W_{\text{ref}}\|}{W_{\text{ref}}} \times 100\%$ | **$\le 0.5\%$ 이내** |
| **2. 제약조건 위반량 (Constraint Violation)** | $\max_j (\max(0, \frac{g_j(x)}{Limit_j})) \times 100\%$ | **$0.0\%$ (수치 허용오차 $10^{-5}$)** |
| **3. 솔버 호출 횟수 (FEA Calls)** | 최적점 도달 시까지의 총 CalculiX 실행 횟수 | **OptiStruct DUAL/SLP 수준과 동등** |
| **4. Kriging 대리최적화 가속비** | $\frac{N_{\text{direct}} - N_{\text{surrogate}}}{N_{\text{direct}}} \times 100\%$ | **해석 횟수 $70\%$ 이상 절감** |

---

## 4. 검증 진행 절차 및 산출물

1. **CalculiX 검증 덱 준비**: `tests/benchmarks/decks/10bar_truss.inp` 배치.
2. **알고리즘별 최적화 실행**:
   - SLSQP (구배 기반 경사하강법)
   - NSGA-II (다목적 유전알고리즘)
   - Kriging 대리모델 기반 최적화 (Optimal LHS 50샘플 + 순차 샘플링)
3. **수치 수렴 이력 차트 오버레이**:
   - OptiStruct 수렴 곡선과 우리 엔진의 수렴 이력을 단일 그래프에 비교 렌더링.
4. **결과 보고서 작성**: `docs/benchmarks/results/10bar_truss_comparison_report.md`에 최종 수렴치, 오차율, 계산 소요 시간 수록.
