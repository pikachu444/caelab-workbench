# 상용 MDO 7대 다분야 종합 벤치마크 사양서 (Comprehensive Multi-Domain Industrial Benchmarks)

본 문서는 상용 5대 최적화 소프트웨어(Altair OptiStruct, Dassault Systèmes Tosca/Isight, SIMULIA Abaqus, Esteco modeFRONTIER, Siemens HEEDS)의 공식 검증 매뉴얼에 수록된 전 산업 7대 대표 벤치마크 문제를 CalculiX 기반으로 모델링하고, caelab-workbench의 클린슬레이트 MDO 엔진과 1:1 수치 성능 및 최적해를 정밀 비교하기 위한 공인 기술 사양서입니다.

> [!IMPORTANT]
> **실무 엄격성 원칙**: 본 문서는 단순 1D 교과서 예제(10-bar truss)를 넘어 실제 산업 현장에서 엔지니어들이 설계하는 수만 절점 3D 솔리드·쉘 모델, 다축 복합 하중, 비선형 소성 및 피로/좌굴 문제를 총망라합니다.

---

## 1. 7대 전 산업 도메인 벤치마크 총괄 요약

| No | 산업 도메인 | 벤치마크 모델명 | FEA 요소 및 규모 | 하중 및 물리 특성 | 상용 툴 대조 기준 |
|:---:|---|---|---|---|---|
| **1** | **항공우주 브래킷** | **GE Jet Engine Bracket** | 3D Solid C3D10 / 28,450 절점 | 공식 4대 복합 하중 (수직/45도/횡하중/비틀림) | OptiStruct / Tosca ($36.5\%$ 감량) |
| **2** | **자동차 샤시 현가** | **Suspension Lower Control Arm** | 3D Solid/Shell / 45,200 절점 | 3대 동하중 (급제동 / 한계선회 / 험로요철) | OptiStruct OS-T: 2000 / OS-E: 0105 ($28.3\%$ 감량) |
| **3** | **내연기관 파워트레인** | **Engine Connecting Rod** | 3D Solid C3D8R / 25,600 절점 | 7,200 RPM 교번 관성 인장 vs 폭발 압축 피로 | Abaqus / modeFRONTIER ($SF \ge 1.50$) |
| **4** | **항공기 기체 주익** | **Aircraft Wing Rib with Cutouts** | 박판 쉘 S4R / 32,400 절점 | 순항 양력 전단류 + CalculiX `*BUCKLE` 좌굴 | NASA-SP-8007 / OptiStruct ($\lambda \ge 1.35$) |
| **5** | **자동차 충돌 안전** | **Thin-Walled Front Crash Box** | 대변형 쉘 S4R / 18,500 절점 | 56 km/h 고속 충돌 동적 비선형 소성 압궤 | OpenRadioss / LS-DYNA ($EA \ge 18\text{ kJ}$) |
| **6** | **석유화학 압력용기** | **High-Pressure Flange Nozzle** | 축대칭/솔리드 / 22,400 절점 | 15 MPa 내압 + 볼트 인장 + 열응력 연성 | ASME Sec VIII Div 2 SCL 응력 선형화 |
| **7** | **토목 대형 교량보** | **Stepped Cantilever Box Girder** | 5단 분할 쉘 S4R / 35,800 절점 | 60m 전장 자중 + 320 kN 이동 차량 활하중 | AASHTO / Eurocode 3 ($\delta \le L/800$) |

*(그림 1: 글로벌 상용 MDO 벤치마크 기준 모델군: MIT SimJEB / GrabCAD 챌린지 공인 3D 모델 및 공학 시뮬레이션 결과군)*

---

## 2. 7대 벤치마크 상세 엔지니어링 규격

### 【Benchmark 1】 항공우주 3D 브래킷: GE Jet Engine Bracket (OptiStruct / Tosca 공인)

*(그림 2: GE Jet Engine Bracket 3D FEA 다축 하중 변위 및 응력 컨투어 해석 결과 - MIT SimJEB 공인 데이터셋)*
1. **도메인 및 출처**: 항공우주 제트 엔진 부품 / GE Aviation & GrabCAD Global Challenge / Altair OptiStruct 공식 검증 예제
2. **해석 메쉬 및 재료 스펙**:
   - 요소 타입: CalculiX 2차 10절점 사면체 요소 (`C3D10`)
   - 규모: **절점 28,450개, 요소 16,800개** (자유도 약 85,350 DOF)
   - 재료 물성: 항공용 티타늄 Ti-6Al-4V ($E = 113.8\text{ GPa}$, $\nu = 0.342$, $\rho = 4.43 \times 10^{-9}\text{ t/mm}^3$, $\sigma_y = 830\text{ MPa}$)
   - 단위계: `mm-tonne-N-MPa`
3. **경계 및 공식 4대 복합 하중 조건 (CalculiX 4 Multi-Step)**:
   - 경계: 4개 베이스 볼트 체결 홀 완전 구속 (`*BOUNDARY, NSET_BOLTS, 1, 3, 0.0` - C3D10 솔리드 병진 3자유도 구속)
   - Step 1 (수직 하중 LC1): 핀 중심에 $F_z = +33.0\text{ kN}$ (약 7,418 lbf)
   - Step 2 (45도 경사 하중 LC2): 핀 중심에 $F_{45^\circ} = 38.0\text{ kN}$ ($F_y = 26.87\text{ kN}, F_z = 26.87\text{ kN}$)
   - Step 3 (횡방향 전단 LC3): 핀 중심에 $F_y = +42.5\text{ kN}$ (약 9,554 lbf)
   - Step 4 (비틀림 모멘트 LC4): 핀 중심에 $M_x = 500.0\text{ N}\cdot\text{m}$ ($500,000\text{ N}\cdot\text{mm}$, 분포 커플링/RBE3 하중 전달)
4. **설계변수 토큰 (6 Tokens)**:
   - `@@T_BASE@@`: 베이스 플레이트 두께 ($8.0 \sim 22.0\text{ mm}$, Base: $15.0$)
   - `@@T_RIB@@`: 주 리브 두께 ($6.0 \sim 18.0\text{ mm}$, Base: $12.0$)
   - `@@R_FIL@@`: 리브-베이스 전이 필렛 반경 ($3.0 \sim 14.0\text{ mm}$, Base: $6.0$)
   - `@@D_BOSS@@`: 중앙 핀 보스 외경 ($48.0 \sim 72.0\text{ mm}$, Base: $60.0$)
   - `@@T_WALL@@`: 보스 벽 두께 규격판재 이산형 (`[6.0, 8.0, 10.0, 12.0, 15.0]\text{ mm}`)
   - `@@H_STIFF@@`: 측면 보강 스티프너 높이 ($20.0 \sim 50.0\text{ mm}$, Base: $35.0$)
5. **MDO 정식화**:
   - 목적: $\min \text{Mass}(x)$ [kg]
   - 제약: 전 하중 스텝 최대 von Mises 응력 $\sigma_{\text{vM},\max} \le 650.0\text{ MPa}$ (안전율 1.28 확보), $\delta_{\text{pin}} \le 0.75\text{ mm}$, 볼트 인장 반력 $R_{z,\text{bolt}} \le 16.0\text{ kN}$
6. **상용 툴 목표 레퍼런스**:
   - OptiStruct: 초기 $1.85\text{ kg} \to$ 최적 $1.175\text{ kg}$ (36.5% 감량, Max Stress $628.0\text{ MPa}$)
   - caelab MDO 목표치: $1.180\text{ kg}$ 수준 (편차 0.5% 이내 목표)

---

### 【Benchmark 2】 자동차 샤시: Suspension Lower Control Arm (OptiStruct OS-T: 2000 / OS-E: 0105)

> [!NOTE]
> **자동차 샤시 현가장치 로어 컨트롤 암(Suspension Lower Control Arm)**은 Altair OptiStruct 공식 튜토리얼 매뉴얼(Tutorial OS-T: 2000 및 OS-E: 0105) 및 SAE 2008-01-0880 표준을 1:1 준용합니다. 3D 솔리드(C3D10)와 스탬핑 쉘(S4R) 복합 메쉬 기반의 급제동/한계선회/요철 3대 다중 하중 스텝으로 구성됩니다.
1. **도메인 및 출처**: 자동차 섀시 현가장치 / Altair OptiStruct `OS-T: 2000` / `OS-E: 0105` (Control Arm Sizing & Shape Optimization) / SAE 2008-01-0880
2. **해석 메쉬 및 재료 스펙**:
   - 요소 타입: 3D 솔리드 (`C3D10`) 및 스탬핑 판금 셸 (`S4R`) 복합 메쉬
   - 규모: **절점 45,200개, 요소 28,500개** (자유도 약 135,000 DOF)
   - 재료 물성: 고장력강 SPFC 590 ($E = 210\text{ GPa}$, $\nu = 0.30$, $\rho = 7.85 \times 10^{-9}\text{ t/mm}^3$, $\sigma_y = 380\text{ MPa}$, $\sigma_u = 590\text{ MPa}$)
   - 단위계: `mm-tonne-N-MPa`
3. **경계 및 다중 하중 조건 (3 Multi-Step)**:
   - 경계: 차체 체결 프런트/리어 부싱 중심 핀 지지
   - Step 1 (급제동 Braking): 볼 조인트에 전후방 하중 $F_x = -15.0\text{ kN}$
   - Step 2 (한계 선회 Cornering): 볼 조인트에 횡하중 $F_y = +12.5\text{ kN}$
   - Step 3 (험로 요철 Pothole Bump): 볼 조인트에 수직 충격 하중 $F_z = +20.0\text{ kN}$
4. **설계변수 토큰 (6 Tokens)**:
   - `@@T_WEB@@`: 메인 스탬핑 웹 두께 ($2.0 \sim 4.5\text{ mm}$, Base: $3.2$)
   - `@@T_FLANGE@@`: 외곽 플랜지 두께 ($2.5 \sim 5.0\text{ mm}$, Base: $3.5$)
   - `@@D_BUSH_F@@`: 프런트 부싱 하우징 외경 ($35.0 \sim 50.0\text{ mm}$, Base: $42.0$)
   - `@@D_BUSH_R@@`: 리어 부싱 하우징 외경 ($45.0 \sim 65.0\text{ mm}$, Base: $55.0$)
   - `@@D_BALL@@`: 볼 조인트 체결부 보스 직경 ($30.0 \sim 45.0\text{ mm}$, Base: $38.0$)
   - `@@R_RELIEF@@`: 벤딩 릴리프 필렛 반경 ($5.0 \sim 15.0\text{ mm}$, Base: $8.0$)
5. **MDO 정식화**:
   - 목적: $\min \text{Mass}(x)$ [kg]
   - 제약: 전 스텝 $\sigma_{\text{vM},\max} \le 280.0\text{ MPa}$ (안전율 1.35), 부싱 비틀림 강성 $K_\theta \ge 1.8 \times 10^5\text{ N}\cdot\text{mm/deg}$, 1차 고유진동수 $f_1 \ge 45.0\text{ Hz}$
6. **상용 툴 목표 레퍼런스**:
   - OptiStruct/Tosca: 초기 $3.42\text{ kg} \to$ 최적 $2.45\text{ kg}$ (28.3% 감량)

---

### 【Benchmark 3】 내연기관 파워트레인: Engine Connecting Rod (Abaqus / modeFRONTIER)
1. **도메인 및 출처**: 자동차/선박 엔진 구동계 / SIMULIA Abaqus Example 1.1.3 / SAE 2003-01-1310 / modeFRONTIER
2. **해석 메쉬 및 재료 스펙**:
   - 요소 타입: 정밀 구조 1차 육면체 감차적분 (`C3D8R`) 및 필렛 사면체 (`C3D10`)
   - 규모: **절점 25,600개, 요소 21,200개** (자유도 약 76,800 DOF)
   - 재료 물성: 고강도 단조강 42CrMo4 ($E = 206\text{ GPa}$, $\nu = 0.29$, $\rho = 7.83 \times 10^{-9}\text{ t/mm}^3$, $\sigma_y = 750\text{ MPa}$, 피로한도 $\sigma_e = 420\text{ MPa}$)
3. **경계 및 교번 피로 하중 조건 (2 Step Cyclic Fatigue)**:
   - Step 1 (팽창 행정 폭발 압축): 피스톤 핀 접촉 코사인 분포 압축력 $F_c = -28.5\text{ kN}$
   - Step 2 (배기/흡입 행정 관성 인장): $7,200\text{ RPM}$ 최대 회전 관성 인장력 $F_t = +18.2\text{ kN}$
4. **설계변수 토큰 (6 Tokens)**:
   - `@@W_SHANK@@`: I-Beam 섕크 플랜지 폭 ($12.0 \sim 22.0\text{ mm}$, Base: $18.0$)
   - `@@T_WEB@@`: I-Beam 중앙 웹 두께 ($3.5 \sim 8.0\text{ mm}$, Base: $5.0$)
   - `@@T_FLANGE@@`: I-Beam 플랜지 두께 ($4.0 \sim 10.0\text{ mm}$, Base: $6.5$)
   - `@@H_COLUMN@@`: 섕크 단면 전체 높이 ($18.0 \sim 30.0\text{ mm}$, Base: $24.0$)
   - `@@R_SMALL@@`: 소단부 핀 보스 외경 ($14.0 \sim 20.0\text{ mm}$, Base: $17.0$)
   - `@@R_BIG@@`: 대단부 크랭크 보스 외경 ($28.0 \sim 36.0\text{ mm}$, Base: $32.0$)
5. **MDO 정식화**:
   - 목적: $\min \text{Mass}(x)$ [g]
   - 제약: Goodman 선도 기준 피로 안전계수 $SF \ge 1.50$ ($\sigma_a/\sigma_e + \sigma_m/\sigma_u \le 0.667$), 소/대단부 베어링 진원도 변형량 $\Delta D \le 0.015\text{ mm}$
6. **상용 툴 목표 레퍼런스**:
   - modeFRONTIER/Abaqus: 초기 $525\text{ g} \to$ 최적 $412\text{ g}$ (21.5% 감량, $SF = 1.52$)

---

### 【Benchmark 4】 항공기 기체 구조: Aircraft Wing Rib with Stiffened Cutouts (NASA-SP / OptiStruct)
1. **도메인 및 출처**: 항공기 주익 구조 / NASA-SP-8007 / AIAA MDO Aircraft Benchmark / OptiStruct Wing Design
2. **해석 메쉬 및 재료 스펙**:
   - 요소 타입: 박판 쉘 1차 감차적분 요소 (`S4R`)
   - 규모: **절점 32,400개, 요소 31,800개** (자유도 약 194,400 DOF)
   - 재료 물성: 항공 알루미늄 Al 7075-T6 ($E = 71.7\text{ GPa}$, $\nu = 0.33$, $\rho = 2.81 \times 10^{-9}\text{ t/mm}^3$, $\sigma_y = 503\text{ MPa}$)
3. **경계 및 복합 하중 조건 (CalculiX `*BUCKLE` 연동)**:
   - 경계: 전후방 스파 및 스킨 결합 플랜지 라인 단순 지지
   - 하중: 순항 양력 전단류 $V = 45.0\text{ kN}$ 및 상하면 스킨 인장/압축 굽힘류 $N_x = \pm 120\text{ kN/m}$
4. **설계변수 토큰 (6 Tokens)**:
   - `@@T_RIB@@`: 메인 립 웹 두께 규격판재 이산형 (`[1.2, 1.6, 2.0, 2.5, 3.2]\text{ mm}`)
   - `@@R_HOLE1@@`: 전방 경량화 컷아웃 홀 반경 ($40.0 \sim 100.0\text{ mm}$, Base: $70.0$)
   - `@@R_HOLE2@@`: 후방 경량화 컷아웃 홀 반경 ($50.0 \sim 120.0\text{ mm}$, Base: $85.0$)
   - `@@W_FLANGE@@`: 컷아웃 외곽 비드 성형 플랜지 폭 ($10.0 \sim 25.0\text{ mm}$, Base: $15.0$)
   - `@@H_BEAD@@`: 비드 성형 엠보싱 깊이 ($4.0 \sim 12.0\text{ mm}$, Base: $6.0$)
   - `@@T_STIFF@@`: 수직 보강 스티프너 두께 ($1.6 \sim 4.0\text{ mm}$, Base: $2.5$)
5. **MDO 정식화**:
   - 목적: $\min \text{Weight}(x)$ [kg]
   - 제약: CalculiX `*BUCKLE` 1차 선형 좌굴 하중계수 $\lambda_{\text{cr}} \ge 1.35$, 전단 응력 $\tau_{\max} \le 220\text{ MPa}$, 최대 응력 $\sigma_{\text{vM}} \le 350\text{ MPa}$
6. **상용 툴 목표 레퍼런스**:
   - OptiStruct: 초기 $2.65\text{ kg} \to$ 최적 $1.82\text{ kg}$ (31.3% 감량, $\lambda_{\text{cr}} = 1.38$)

---

### 【Benchmark 5】 자동차 충돌 안전: Thin-Walled Front Crash Box (OpenRadioss / LS-DYNA)
1. **도메인 및 출처**: 자동차 충돌 안전 차체 부재 / USCAR Crash Consortium / OpenRadioss & LS-DYNA 벤치마크
2. **해석 메쉬 및 재료 스펙**:
   - 요소 타입: 대변형·소성 붕괴 박판 쉘 요소 (`S4R` / OpenRadioss `SHELL4`)
   - 규모: **절점 18,500개, 요소 18,200개** (자유도 약 111,000 DOF)
   - 재료 물성: 고강도 듀얼페이즈강 DP600 ($E = 210\text{ GPa}$, $\nu = 0.30$, $\rho = 7.85 \times 10^{-6}\text{ kg/mm}^3$, $\sigma_y = 350\text{ MPa}$, Swift 가공경화 $\sigma = 1020(0.005 + \bar{\varepsilon}^p)^{0.18}\text{ MPa}$)
   - **단위계**: `mm-kg-ms-kN-GPa` (충돌 동역학 일관 단위계)
3. **경계 및 충돌 조건**:
   - 후단: 차체 프레임 완전 고정
   - 선단: 강체 충돌벽에 질량 $M = 500\text{ kg}$이 초기 속도 $v_0 = 15.6\text{ m/s}$ ($56\text{ km/h}$)로 충돌, 압축 스트로크 $150\text{ mm}$
4. **설계변수 토큰 (5 Tokens)**:
   - `@@T_SHELL@@`: 크러시 박스 쉘 두께 ($1.2 \sim 2.5\text{ mm}$, Base: $1.8$)
   - `@@W_OCT@@`: 8각 단면 외폭 ($60.0 \sim 110.0\text{ mm}$, Base: $80.0$)
   - `@@D_TRIGGER@@`: 좌굴 개시 유도 노치 깊이 ($2.0 \sim 8.0\text{ mm}$, Base: $4.0$)
   - `@@P_TRIGGER@@`: 선단으로부터 트리거 위치 ($20.0 \sim 50.0\text{ mm}$, Base: $30.0$)
   - `@@TAPER_ANG@@`: 단면 테이퍼 각도 ($0.0 \sim 5.0^\circ$, Base: $0.0$)
5. **MDO 정식화**:
   - 목적: 충돌 에너지 흡수량 최대화 $\max EA(x) = \int_0^{\delta_{\max}} F(s)\,ds$ [kJ]
   - 제약: 탑승자 감속도 보호를 위한 초기 피크 충돌력 상한 $F_{\text{peak}} \le 120.0\text{ kN}$, 압축 하중 효율 $CFE = F_{\text{mean}}/F_{\text{peak}} \ge 0.70$
6. **상용 툴 목표 레퍼런스**:
   - LS-DYNA/Isight: $EA = 18.2\text{ kJ}$, $F_{\text{peak}} = 114.0\text{ kN}$, $CFE = 0.72$

---

### 【Benchmark 6】 플랜트/압력용기: High-Pressure Flange Nozzle (ASME Sec VIII Div 2)
1. **도메인 및 출처**: 석유화학/원자력 플랜트 용기 / ASME BPVC Section VIII Div 2 Part 5 (Design by Analysis) / WRC 429
2. **해석 메쉬 및 재료 스펙**:
   - 요소 타입: 축대칭 4절점 감차적분 (`CAX4R`) 또는 3D 솔리드 (`C3D8R`)
   - 규모: **절점 22,400개, 요소 20,800개** (자유도 약 67,200 DOF)
   - 재료 물성: 압력용기용 SA-516 Gr. 70 탄소강 ($E = 200\text{ GPa}$, $\nu = 0.30$, $\alpha = 1.2 \times 10^{-5}\text{ /K}$, 설계응력강도 $S_m = 138.0\text{ MPa}$)
3. **경계 및 복합 하중 조건 (내압 + 볼트 프리텐션 + 열경사)**:
   - Step 1: 설계 내압 $P = 15.0\text{ MPa}$ 인가 및 볼트 조임 프리텐션 $F_{\text{bolt}} = 85.0\text{ kN/bolt}$
   - Step 2: 정상상태 열응력 연성 해석 (내면 $320^\circ\text{C}$, 외면 대류 방열 $40^\circ\text{C}$)
4. **설계변수 토큰 (5 Tokens)**:
   - `@@T_VESSEL@@`: 주 용기 벽 두께 ($18.0 \sim 35.0\text{ mm}$, Base: $28.0$)
   - `@@T_NOZZLE@@`: 분기 노즐 벽 두께 ($12.0 \sim 28.0\text{ mm}$, Base: $20.0$)
   - `@@R_FILLET@@`: 노즐-쉘 접합 전이 필렛 반경 ($10.0 \sim 30.0\text{ mm}$, Base: $15.0$)
   - `@@T_FLANGE@@`: 체결 플랜지 림 두께 ($35.0 \sim 70.0\text{ mm}$, Base: $50.0$)
   - `@@W_HUB@@`: 테이퍼드 허브 보강 폭 ($25.0 \sim 50.0\text{ mm}$, Base: $35.0$)
5. **MDO 정식화**:
   - 목적: $\min \text{Total Volume}(x)$ [L]
   - 제약 (ASME Sec VIII Div 2 응력 선형화 SCL 기준):
     - 1차 일반 막응력: $P_m \le S_m = 138.0\text{ MPa}$
     - 1차 국부 막+굽힘응력: $P_L + P_b \le 1.5 S_m = 207.0\text{ MPa}$
     - 1차+2차 복합 응력범위: $P + Q \le 3.0 S_m = 414.0\text{ MPa}$
     - 가스켓 밀폐면 접촉압력: $p_{\text{gasket}} \ge 2.5 P = 37.5\text{ MPa}$
6. **상용 툴 목표 레퍼런스**:
   - ANSYS/Abaqus: 초기 $63.3\text{ L} \to$ 최적 $48.5\text{ L}$ (23.4% 절감, ASME 전 항목 합격)

---

### 【Benchmark 7】 토목/대형 교량보: Stepped Cantilever Box Girder (AASHTO / Eurocode 3)
1. **도메인 및 출처**: 대형 강교량 상부구조 / AASHTO LRFD / Eurocode 3 Part 1-5 / Vanderplaats Structural MDO
2. **해석 메쉬 및 재료 스펙**:
   - 요소 타입: 후판/박판 쉘 요소 (`S4R`, `S8R`) 및 다이어프램 빔 요소
   - 규모: **절점 35,800개, 요소 34,200개** (자유도 약 214,800 DOF, 전장 $L = 60.0\text{ m}$)
   - 재료 물성: 교량 구조용 강재 SM490 / S355 ($E = 210\text{ GPa}$, $\nu = 0.30$, $\rho = 7.85 \times 10^{-9}\text{ t/mm}^3$, $\sigma_y = 355\text{ MPa}$)
3. **경계 및 이동 활하중 조건 (CalculiX `*DLOAD` & 다단 Step)**:
   - 지지: 교각부 롤러 및 힌지 탄성 베어링 경계조건
   - 하중: Step 1 자중 하중 (`*DLOAD, GRAV`), Step 2 표준 트럭 활하중 320 kN 이동 집중하중군 및 횡풍하중
4. **설계변수 토큰 (11 Tokens - 5개 세그먼트 분할)**:
   - `@@T_FLG_1@@` ~ `@@T_FLG_5@@`: 5개 길이 구간별 상하 플랜지 두께 ($12.0 \sim 40.0\text{ mm}$)
   - `@@T_WEB_1@@` ~ `@@T_WEB_5@@`: 5개 구간별 좌우 웹 플레이트 두께 ($10.0 \sim 25.0\text{ mm}$)
   - `@@L_DIA@@`: 횡격벽(Diaphragm) 설치 간격 ($3,000 \sim 6,000\text{ mm}$, Base: $4,500$)
5. **MDO 정식화**:
   - 목적: $\min \text{Total Steel Mass}(x)$ [ton]
   - 제약: 도로교 설계기준 최대 활하중 처짐 $\delta_{\max} \le L/800 = 75.0\text{ mm}$, 판폭두께비 $b/t \le 45.0$, $\sigma_{\text{vM},\max} \le 235.0\text{ MPa}$
6. **상용 툴 목표 레퍼런스**:
   - Vanderplaats / OptiStruct: 초기 $76.7\text{ ton} \to$ 최적 $62.4\text{ ton}$ (18.6% 감량, $\delta = 73.2\text{ mm}$)
