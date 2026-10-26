# 튜토리얼: 박스 거더 캔틸레버 단면 치수 최적화 (Sizing Optimization)

이 튜토리얼은 CalculiX FEA 덱(`.inp`)을 활용하여 3차원 박스 거더 캔틸레버의 단면 치수를 다목적(중량 최소화 및 최대 응력 최소화)으로 최적화하고, 최적 설계점을 원본 해석 모델에 확정 반영하는 전 과정을 다룹니다.

---

## 1. 해석 모델 및 최적화 문제 정의

### 대상 구조물 개요
- **구조 형식**: 길이 $L = 1000\text{ mm}$의 박스 단면 외팔보 (박스 거더)
- **하중 및 경계 조건**: 루트단($X=0$) 완전 고정, 선단부($X=1000$)에 $Y$축 하향 집중 하중 $F_y = -10,000\text{ N}$
- **재질**: 일반 구조용 강재 (탄성계수 $E = 210\text{ GPa}$, 포아송비 $\nu = 0.3$, 밀도 $\rho = 7.85 \times 10^{-6}\text{ kg/mm}^3$, 항복강도 $\sigma_y = 250\text{ MPa}$)

```
   고정단 (X=0)                             자유단 (X=1000)
   +=======+======================================+
   |       |   Box Section: Height(H), Width(B)   |  | Fy = -10 kN
   |       |   Sheet Thickness: (t)               |  v
   +=======+======================================+
```

### 설계 변수 (Design Variables)
1. **웹 높이 ($H$, `HEIGHT`)**: $60.0 \sim 150.0\text{ mm}$ (연속형, 기준값 $100.0\text{ mm}$)
2. **플랜지 폭 ($B$, `WIDTH`)**: $40.0 \sim 100.0\text{ mm}$ (연속형, 기준값 $60.0\text{ mm}$)
3. **판재 두께 ($t$, `THICKNESS`)**: $[1.6, 2.0, 2.5, 3.2, 4.5, 6.0]\text{ mm}$ (이산 규격 판재, 기준값 $3.2\text{ mm}$)

### 설계 목적 (Objectives)
- **목적 1**: 총 중량 최소화 $\min(\text{Mass})$ [$\text{kg}$]
- **목적 2**: 최대 von Mises 응력 최소화 $\min(\sigma_{\max})$ [$\text{MPa}$]

### 제약 조건 (Constraints)
- **선단 최대 처짐**: $\delta_{\max} \le 2.0\text{ mm}$
- **최대 von Mises 응력**: $\sigma_{\max} \le 160.0\text{ MPa}$ (안전율 $1.56$ 확보)

---

## 2. CalculiX 인풋 덱 준비 및 파라미터 토큰 치환

기존 CalculiX 인풋 파일(`cantilever.inp`)에서 변경 대상인 단면 물성 및 절점 좌표 정의부에 치환 토큰(`@@TOKEN@@`)을 삽입합니다.

### 원본 단면 및 형상 정의부 수정 예시
```inp
** =========================================================
** SECTION PROPERTIES: BOX BEAM SHELL
** =========================================================
*SHELL SECTION, ELSET=Eall, MATERIAL=STEEL
@@THICKNESS@@, 5
**
** =========================================================
** KEY NODES FOR SECTION CORNERS
** =========================================================
*NODE
1, 0.0, 0.0, 0.0
2, 0.0, @@HEIGHT@@, 0.0
3, @@WIDTH@@, @@HEIGHT@@, 0.0
4, @@WIDTH@@, 0.0, 0.0
...
```

- 수정 후 파일을 `cantilever_template.inp`로 저장합니다.
- 토큰 규칙은 `@@변수명@@` 또는 `{{변수명}}` 형식을 지원합니다.

---

## 3. MDO Studio 실행 및 연구 설정

### 프로그램 기동
터미널에서 아래 명령을 실행하여 MDO Studio를 엽니다:
```bash
python -m apps.workbench_qt.mdo_studio
```

### 템플릿 덱 로드 및 토큰 자동 감지
1. 상단 툴바에서 **[Open Native Deck]**을 클릭하고 준비한 `cantilever_template.inp`를 선택합니다.
2. 시스템이 덱을 구문 분석하여 발견된 토큰(`HEIGHT`, `WIDTH`, `THICKNESS`)을 좌측 **Setup Dock** 변수 테이블에 자동으로 등록합니다.
3. 각 변수의 속성을 다음과 같이 설정합니다:
   - `HEIGHT`: Type = Continuous, Min = 60.0, Max = 150.0, Baseline = 100.0
   - `WIDTH`: Type = Continuous, Min = 40.0, Max = 100.0, Baseline = 60.0
   - `THICKNESS`: Type = Discrete, Choices = `1.6, 2.0, 2.5, 3.2, 4.5, 6.0`, Baseline = 3.2

---

## 4. DoE 실험 계획법 샘플링 및 해석 매트릭스 실행

1. **DoE 알고리즘 선택**: 좌측 도크의 DoE 탭에서 **Optimal Latin Hypercube (Maximin)**를 선택합니다.
2. **샘플 수 설정**: 탐색점 수를 `30`으로 지정하고 **[Generate DoE Matrix]**를 클릭합니다.
3. 중앙 하단의 **Evaluation Table**에 30개 설계 조합 매트릭스가 생성됩니다.
4. **[Run DoE Optimization]**을 클릭하면 백그라운드에서 각 설계점마다 덱 치환 ➔ CalculiX 프로세스 실행 ➔ 결과(`.dat`/`.frd`) 자동 추출 파이프라인이 구동됩니다.
5. 해석이 완료된 행은 상태가 `SUCCEEDED`로 갱신되며, 계산된 질량, 최대 처짐, 최대 응력 값이 테이블에 실시간으로 채워집니다. 제약을 벗어난 행은 시각적으로 붉은색 위반 플래그가 표시됩니다.

---

## 5. Kriging 대리모델 생성 및 3D 응답곡면 분석

1. 우측 하단 분석 영역에서 **[Response Surface]** 탭을 클릭합니다.
2. **[Fit Surrogate]**를 실행하여 30개 샘플 해석 데이터를 바탕으로 Kriging(Gaussian Process Regression) 모델을 적합합니다.
3. 교차검증 점수($R^2 \ge 0.98$, $\text{RMSE} \le 2.0\text{ MPa}$)를 확인하여 대리모델의 예측 신뢰도를 검증합니다.
4. **3D 응답곡면 조작**:
   - $X$축: `HEIGHT`, $Y$축: `WIDTH`, $Z$축: `Max_Stress`로 지정합니다.
   - 마우스 드래그로 3D 곡면을 회전하여 웹 높이($H$)와 플랜지 폭($B$)이 응력에 미치는 비선형 영향을 관찰합니다.
   - 하단의 `THICKNESS` 슬라이더를 $1.6\text{ mm} \to 3.2\text{ mm} \to 6.0\text{ mm}$로 이동시키면, 판재 두께가 두꺼워짐에 따라 응력 곡면 전체가 하향 평행이동하는 물리적 거동을 직관적으로 확인할 수 있습니다.

---

## 6. NSGA-II 다목적 최적화 및 파레토 Knee Point 선정

1. 좌측 도크에서 알고리즘을 **NSGA-II** (세대수 40, 개체수 30)로 설정하고 **[Run Multi-Objective Optimization]**을 실행합니다.
2. 우측 **[Pareto Frontier]** 탭을 확인합니다:
   - 가로축: 질량(Mass) [$\text{kg}$]
   - 세로축: 최대 응력(Max Stress) [$\text{MPa}$]
   - 응력 제약($\le 160\text{ MPa}$)을 만족하는 청색 유효해 군집과 제약 위반 적색 해가 명확히 분리되어 표시됩니다.
3. 시스템이 자동으로 파레토 프론트 곡선의 최대 굴곡점인 **Knee Point**를 강조 표시합니다:
   - **Knee Point 결과 예시**:
     - 질량: $12.4\text{ kg}$ (기준 모델 $18.2\text{ kg}$ 대비 **$31.8\%$ 경량화 달성**)
     - 최대 응력: $148.5\text{ MPa}$ (허용 기준 $160\text{ MPa}$ 만족)
     - 최대 처짐: $1.82\text{ mm}$ (허용 기준 $2.0\text{ mm}$ 만족)
     - 추천 치수: $H = 124.0\text{ mm}$, $B = 52.0\text{ mm}$, $t = 2.5\text{ mm}$

---

## 7. "Set as Current" 덱 치환 및 최종 검증

1. Evaluation Table 또는 Pareto 차트에서 확정할 최적점 행을 클릭합니다.
2. 좌측 도크 하단의 **[Set as Current Design Point]** 버튼을 클릭합니다.
3. 시스템 알림과 함께 다음 작업이 자동으로 수행됩니다:
   - 원본 인풋 덱의 백업 파일(`cantilever_template.inp.bak`) 생성.
   - 활성 덱의 `@@HEIGHT@@`, `@@WIDTH@@`, `@@THICKNESS@@` 토큰이 최적 수치(`124.0`, `52.0`, `2.5`)로 정밀 치환됨.
4. 메인 프로젝트 화면으로 복귀하여 최종 덱으로 1회 검증 해석을 실행하고, **ParaView**를 통해 3차원 응력 분포가 대리모델 예측치와 정확히 일치함을 확인하고 설계를 완료합니다.
