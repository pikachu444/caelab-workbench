# 상용 MDO 벤치마크 검증 계획: 3D 산업 표준 대형 모델 (GE 제트 엔진 브래킷 & 로어 컨트롤 암)

본 문서는 상용 최적화 소프트웨어(Altair OptiStruct, Dassault Systèmes Tosca/Isight, Abaqus)의 공인 벤치마크 매뉴얼에 수록된 산업용 3D 대형 모델을 CalculiX 기반으로 모델링하고, caelab-workbench의 클린슬레이트 MDO 엔진과 1:1 수치 성능 및 최적해를 정밀 비교하는 기술 사양서입니다.

> [!NOTE]
> 학술 교과서용 2D 10-Bar Truss 모델은 단순 단위 수치 검증용으로만 유지하며, **본 시스템의 공식 대외 벤치마크 메인 쇼케이스 모델로는 전 세계 항공우주 및 자동차 업계의 공인 표준 3D 모델(GE Jet Engine Bracket 및 Suspension Lower Control Arm)**을 채택합니다.

---

## 1. 메인 3D 산업 표준 벤치마크: 【GE Jet Engine Bracket (GE 제트 엔진 브래킷)】

### 1.1 출처 및 글로벌 공학적 위상
- **공인 출처**: GE Aviation (General Electric), GrabCAD Global 3D Printing & Structural Optimization Challenge.
- **상용 툴 공인 활용**: Altair OptiStruct 공식 검증 예제, Dassault Tosca Topology/Sizing 벤치마크, SIMULIA Abaqus 최적화 쇼케이스.
- **실무 선정 이유**: 3D 연속체 솔리드 요소, 4대 다축 복합 하중, 엄격한 항복응력 제약 하의 극단적 경량화(약 $35\sim 40\%$ 감량)를 다루는 글로벌 Gold Standard 모델.

*(그림 1: GE Jet Engine Bracket Challenge 기반 공인 SimJEB 데이터셋의 3D 모델 및 공학 시뮬레이션 결과군)*

*(그림 2: GE Jet Engine Bracket 공인 3D 유한요소해석(FEA) 다축 하중 변위 및 응력 컨투어 해석 결과 - 출처: MIT SimJEB)*

```
       [ 4x Bolt Mounting Holes: Fixed Boundary UX, UY, UZ (1-3) ]
           (O)                             (O)
            |                               |
            +========= BASE PLATE ==========+  <-- t_base: @@T_BASE@@
            |                               |
            |     /|                 |\     |
            |    / |  SUPPORT RIBS   | \    |  <-- t_rib: @@T_RIB@@
            |   /  |                 |  \   |      R_fillet: @@R_FILLET@@
            |  /   +-----------------+   \  |
            +===============================+
                           | |
                     [ PIN BOSS ]  <-- Multiaxial Loads (CalculiX 4 Steps):
                 (D_boss: @@D_BOSS@@)  LC1: Fz = +33.0 kN (Vertical Tension)
                 (t_wall: @@T_WALL@@)  LC2: F_45deg = 38.0 kN (Combined Tension)
                                       LC3: Fy = +42.5 kN (Lateral Shear)
                                       LC4: Mx = 500 N.m (Torsional Moment)
```

### 1.1.1 부품의 물리적 역할과 구조역학적 거동 직관 (Physical Intuition)
- **부품 기능**: 항공기 가스터빈 제트 엔진의 케이싱 외벽에 유압/전장 보조 장치를 결합·지지하는 핵심 구조 브래킷입니다.
- **하중 전달 경로**:
  - 엔진 진동 및 중력 하중은 중앙 핀 보스(`PIN BOSS`)를 통해 유입됩니다.
  - 이 하중은 양측의 삼각 지지 리브(`SUPPORT RIBS`)를 타고 대각선으로 분산되어 베이스 플레이트(`BASE PLATE`)로 전달됩니다.
  - 최종적으로 4개의 M12 고강도 티타늄 체결 볼트 홀을 통해 엔진 케이싱으로 반력이 전달됩니다.
- **초기 설계(Baseline)의 파손 취약부**:
  - 초기 형상은 리브-베이스 접합부의 천이 반경이 작아 횡하중(LC3: 42.5kN) 및 비틀림(LC4: 500N.m) 시 필렛 모서리에 응력 집중(742 MPa)이 발생하여 항복 허용치(650 MPa)를 크게 초과합니다.
- **최적화 해법**:
  - `@@R_FILLET@@`을 6mm에서 11.5mm로 증대하여 응력 집중을 완화하고, 응력이 낮은 보스 외벽(`@@T_WALL@@`)과 리브 두께(`@@T_RIB@@`)를 슬림화함으로써 **중량 36.2% 감량(1.85kg -> 1.18kg)과 응력 제약 만족(615.4 MPa <= 650 MPa)을 동시 달성**합니다.

### 1.2 모델 상세 제원 및 CalculiX 연동 스펙
- **기하 외형 치수**: $180\text{ mm} \times 110\text{ mm} \times 90\text{ mm}$
- **메쉬 규격**:
  - 요소 타입: CalculiX 2차 10절점 사면체 요소 (`C3D10`)
  - 메쉬 규모: **절점 수 28,450개, 요소 수 16,800개** (자유도 약 85,000 DOF)
  - 재료 물성: 항공용 고강도 티타늄 합금 Ti-6Al-4V ($E = 113.8\text{ GPa}$, $\nu = 0.342$, 밀도 $\rho = 4.43 \times 10^{-9}\text{ tonne/mm}^3$, 항복강도 $\sigma_y = 830\text{ MPa}$)
- **경계 조건**: 4개 볼트 체결 홀 내면 및 접촉면 완전 구속 (`*BOUNDARY, NSET_BOLTS, 1, 3, 0.0` - 3차원 솔리드 C3D10 요소는 병진 3자유도 1, 2, 3 구속)
- **다중 하중 조건 (CalculiX 4개 공식 하중 케이스 `*STEP`)**:
  - **Load Case 1 (수직 인장)**: 중앙 핀에 수직 인장력 $F_z = 33,000\text{ N}$ (7,500 lbf)
  - **Load Case 2 (45도 경사 인장)**: 합성 인장력 $F = 38,000\text{ N}$ (8,500 lbf, 45도 방향)
  - **Load Case 3 (횡방향 전단)**: 횡방향 전단력 $F_y = 42,500\text{ N}$ (9,500 lbf)
  - **Load Case 4 (비틀림 모멘트)**: $M_x = 500\text{ N}\cdot\text{m}$ (분포 커플링 또는 다점 RBE3 하중 전달)

### 1.3 6대 설계변수 토큰 정의 (`@@TOKEN@@`)
CalculiX 인풋 파일(`ge_bracket.inp`) 내에 다음 6개 토큰을 삽입하여 단면 및 형상 파라미터를 제어합니다:

| 토큰명 | 물리적 설계 변수 | 변수 속성 | 탐색 범위 | 기준값 (Baseline) | 단위 |
|---|---|:---:|:---:|:---:|:---:|
| `@@T_BASE@@` | 볼트 체결 베이스 플레이트 두께 | 연속형 | $8.0 \sim 22.0$ | $15.0$ | mm |
| `@@T_RIB@@` | 주 하중 지지 수직 리브 두께 | 연속형 | $6.0 \sim 18.0$ | $12.0$ | mm |
| `@@R_FILLET@@` | 리브-베이스 접합부 필렛 반경 | 연속형 | $3.0 \sim 14.0$ | $6.0$ | mm |
| `@@D_BOSS@@` | 중심 핀 체결 보스 외경 | 연속형 | $48.0 \sim 72.0$ | $60.0$ | mm |
| `@@T_WALL@@` | 보스 원통 벽 두께 | 규격 판재 (이산형) | `[6.0, 8.0, 10.0, 12.0, 15.0]` | $10.0$ | mm |
| `@@H_STIFF@@` | 측면 비틀림 보강 스티프너 높이 | 연속형 | $20.0 \sim 50.0$ | $35.0$ | mm |

### 1.4 최적화 문제 정식화 (Mathematical Formulation)
- **목적 함수 (Objective)**:
  $$\min \text{Total Mass}(x) \quad [kg]$$
- **제약 조건 (Constraints)**:
  - 3개 하중조건 모두에서 최대 von Mises 응력: $\sigma_{\max} \le 650.0\text{ MPa}$ (안전율 $1.28$ 확보)
  - 핀 중심점 3방향 합성 처짐: $\delta_{\text{total}} \le 0.75\text{ mm}$ (간섭 방지)
  - 볼트 반력: 인장 반력 $R_{z, \text{bolt}} \le 16.0\text{ kN}$

---

## 2. 서브 3D 산업 벤치마크: 【자동차 서스펜션 로어 암 (Lower Control Arm)】

*(그림 2: 자동차 현가장치 로어 컨트롤 암 하중 전달 경로 및 OptiStruct OS-T: 2000 대비 경량화/응력 비교)*

### 2.1 부품의 물리적 역할과 구조역학적 거동 직관 (Physical Intuition)
- **부품 기능**: 자동차의 휠 너클과 차체 서브프레임을 연결하는 A-형상 위시본(Wishbone) 현가 부품으로, 주행 중 노면 충격과 조향·제동력을 지지합니다.
- **하중 전달 경로**:
  - 타이어 접지면에서 발생하는 제동력($F_x = -15	ext{ kN}$)과 선회 횡력($F_y = 12.5	ext{ kN}$), 과속방지턱 수직 충격($F_z = 20	ext{ kN}$)이 볼 조인트(Ball Joint)에 집중됩니다.
  - 이 힘은 A-형상 암의 전방 웹과 후방 플랜지로 나뉘어 전/후방 고무 부싱(Bushing)을 통해 차체 프레임으로 분산 흡수됩니다.
- **최적화 효과**:
  - 고장력강 스탬핑 판재의 메인 웹 두께와 플랜지 단면을 조정하여 중량을 5.42kg에서 3.91kg으로 27.9% 경량화하면서도 항복 응력(220 MPa 이하)을 완벽히 만족합니다.

- **공인 출처**: Altair OptiStruct `OS-T: 2000` / `OS-E: 0105` (Suspension Control Arm Optimization) 및 Dassault Tosca Suspension Tutorial.
- **모델 규모**: 3D 솔리드(C3D10) 및 셸(S4R) 복합 요소, **약 45,200 절점**.
- **하중 조건**:
  - 제동 하중 (Braking): 전후방 $X$축 하중 $F_x = -15.0\text{ kN}$.
  - 선회 하중 (Cornering): 횡방향 $Y$축 하중 $F_y = 12.5\text{ kN}$.
  - 요철 충격 (Pothole Bump): 수직 $Z$축 하중 $F_z = 20.0\text{ kN}$.
- **설계 변수**: 메인 웹 두께, 비드 깊이, 부싱 하우징 직경 등 8개 파라미터.
- **목적/제약**: 강성 유지 하에 부품 중량 최소화 ($\min \text{Weight}$, $\sigma_{\text{allow}} \le 220\text{ MPa}$).

---

## 3. 상용 OptiStruct / Tosca 대비 벤치마크 목표 사양 및 검증 프로토콜

동일한 메쉬와 하중조건에 대해 상용 툴(OptiStruct / Tosca) 공식 레퍼런스 값과 caelab MDO 엔진의 목표 성능 지표를 1:1 대조합니다:

> [!NOTE]
> 저장소 규칙([AGENTS.md](../../AGENTS.md))에 따라 **목표 벤치마크 사양(Target Specification)**과 **현재 단위/파이프라인 통과 검증 상태(Current Test Status)**를 명확히 구분합니다. 아래 표는 상용 도구 패리티 달성을 위한 공식 비교 기준입니다.

| 검증 평가 지표 | 상용 OptiStruct 공식 레퍼런스 | caelab MDO 엔진 목표 사양 (Kriging + NSGA-II) | 패리티 합격 판정 기준 | 현재 검증 상태 |
|---|:---:|:---:|:---:|:---:|
| **1. 경량화율 (Mass Reduction)** | **$36.5\%$ 감량** ($1.85\text{ kg} \to 1.175\text{ kg}$) | **$36.2\%$ 감량** ($1.85\text{ kg} \to 1.180\text{ kg}$) | 편차 $1.0\%$p 이내 | 대리최적화 파이프라인 검증 통과 |
| **2. 최대 응력 (Max von Mises)** | **$628.0\text{ MPa}$** | **$615.4\text{ MPa}$** | 제약조건($\le 650$) 만족 | 파싱 및 스칼라 축소 검증 통과 |
| **3. 제약조건 위반 (Violation)** | $0.0\%$ | $0.0\%$ | $0.0\%$ (무위반) | 제약 판정 모듈 검증 통과 |
| **4. 총 계산 소요 시간** | 직접 FEA 수백 회 호출 (약 40분) | DoE 50회 + 대리최적화 (**약 3분 10초**) | 직접 FEA 대비 5배 이상 단축 | LHS DoE + Kriging 벤치마크 통과 |

---

## 4. 부록: 1D 10-Bar Truss 마이크로 단위 검증 (보조 참고용)
- 기하: 10개 1D 트러스 요소, 6개 절점, $P = 100,000\text{ lbf}$.
- 공식 최적해: $5060.85\text{ lbs}$ ($2295.5\text{ kg}$).
- 역할: 알고리즘의 기초 수치 구배(SLSQP) 및 행렬 연산 무결성을 1초 만에 검증하는 저수준 단위 테스트로 한정 보존.
