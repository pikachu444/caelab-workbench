# MDO Studio 비주얼 퀵스타트 가이드 (그림으로 보는 사용자 매뉴얼)

이 문서는 사용자가 복잡한 텍스트 대신 **실제 프로그램 화면 캡처(다크/라이트 모드, 차트, 설정창)**를 보며 3분 만에 MDO Studio의 핵심 기능을 한눈에 이해하고 조작할 수 있도록 구성된 시각화 가이드입니다.

---

## 1. MDO Studio 작업 영역 구성도

MDO Studio는 상용 CAE 다분야 최적화 도구(modeFRONTIER, HEEDS, OptiStruct)의 직관적인 워크스페이스 구조를 채택하여, **3대 핵심 분할 영역(설계 탐색기 / 평가 이력 매트릭스 / 3-Tab 엔지니어링 분석 대시보드)**으로 구성되어 있습니다.

![MDO Studio 다크 모드 전체 화면](../images/screen_mdo_studio_dark.png)
*(그림 1: MDO Studio 다크 모드 워크스페이스 구성도)*

```
+-----------------------------------------------------------------------------------------------+
| [ 상단 툴바 ] New Study | Load Deck | Load Demo Study | Run All Points | ☀ Toggle Dark/Light  |
+--------------------------+--------------------------------------------------------------------+
| 1. 스터디 설정 독        | 2. 평가 이력 매트릭스 테이블 (Evaluation History Matrix)           |
|    (Study Setup Dock)    | - 설계점(DP-001...), 설계변수값, 목적함수/제약값, 실행시간 표시    |
| - 설계 변수 테이블       | - 상태 뱃지 (SUCCEEDED / FAILED, FEASIBLE / INFEASIBLE 실시간 뱃지)|
| - 목적함수 및 제약조건   | - 최적 설계점 선택 및 우클릭 "Set as Current Design Point"         |
| - 해석 솔버 파라미터     +--------------------------------------------------------------------+
|                          | 3. 3-Tab 엔지니어링 분석 대시보드 (Engineering Analytics)          |
|                          |    [ 파레토 프론트 (Pareto) ]                                      |
|                          |    [ 반응 곡면 (Response Surface, Kriging ±2σ) ]                   |
|                          |    [ 민감도 토네이도 (Sensitivity Tornado) ]                       |
+--------------------------+--------------------------------------------------------------------+
```

### 각 영역의 역할
1. **좌측 스터디 설정 독 (Setup Dock)**: 설계변수의 상하한 및 규격치수(이산형) 설정, 목적함수(최소화/최대화) 및 제약조건(상한/하한/허용치) 정의.
2. **우측 상단 평가 이력 테이블 (Evaluation History Table)**: DoE 샘플 및 세대별 해석 실행 결과 매트릭스 (`SUCCEEDED`, `FEASIBLE`, `INFEASIBLE` 실시간 색상 뱃지 및 계산 소요 시간 표시). 특정 설계점을 선택하면 우클릭으로 현재 활성 덱에 즉시 적용 가능.
3. **우측 하단 분석 대시보드 (Analytics Dashboard)**:
   - **파레토 프론트 (Pareto)**: 상충하는 목적함수 간의 최적 트레이드오프 곡선 및 절충 최적점(Knee Point ★) 자동 표시.
   - **반응 곡면 (Response Surface)**: Kriging(GPR) 대리모델을 기반으로 한 파라미터별 예측 곡선과 $\pm 2\sigma$ 신뢰 구간(Uncertainty Band) 및 실측 샘플 점 시각화.
   - **민감도 토네이도 (Sensitivity Tornado)**: Sobol 글로벌 민감도 분석 지표를 내림차순 막대그래프로 정렬 표시.

---

## 2. 라이트 모드 & 다크 모드 테마 비교

장시간 해석 모니터링을 위한 고대비 다크 모드와 보고서 캡처용 라이트 모드를 원클릭으로 전환할 수 있습니다.

| 다크 테마 (Dark Theme) | 라이트 테마 (Light Theme) |
|---|---|
| ![다크 테마](../images/screen_mdo_studio_dark.png) | ![라이트 테마](../images/screen_mdo_studio_light.png) |
| *야간 및 장시간 작업에 최적화된 저피로 테마* | *주간 작업 및 기술 문서 보고서 캡처에 적합한 고대비 테마* |

---

## 3. 핵심 분석 차트 3종 시각화

### 3.1 파레토 프론티어 및 Knee Point (무릎점) 자동 검출
다목적 최적화(예: 질량 최소화 vs 최대 응력 최소화) 수행 시, 두 목적이 상충하는 최적 경계선(Pareto Frontier)을 2차원 산점도로 시각화합니다.

![파레토 프론티어 및 Knee Point](../images/screen_mdo_studio_dark_pareto.png)
*(그림 2: 파레토 프론티어 곡선 및 시스템 자동 추천 Knee Point(★))*

- **청색 점**: 제약조건을 만족하는 유효 설계점 (Feasible).
- **적색 점**: 응력 또는 처짐 제약을 초과한 위반점 (Infeasible).
- **황금색 별(★) Knee Point**: 중량은 대폭 감량하면서 응력 여유도를 가장 균형 있게 확보한 최적 절충점 자동 산출.

---

### 3.2 Kriging 대리모델 기반 반응 곡선 및 예측 불확실성 대역 (±2σ Band)
수십 회의 DoE 해석 결과로부터 전산 대리모델(Kriging / Gaussian Process Regression)을 구축하여, 특정 설계변수 변화에 따른 목적함수의 비선형 응답 곡선과 예측 불확실성을 시각화합니다.

![반응 곡선 및 신뢰 대역](../images/screen_mdo_studio_dark_surface.png)
*(그림 3: Kriging 응답 곡선, 실측 DoE 샘플 점, 그리고 반투명 ±2σ 예측 불확실성 대역)*

- **청색 실선 (Mean Prediction)**: Kriging 대리모델이 예측한 응답의 평균 함수 곡선.
- **산점 (Evaluated Samples)**: 실제 CalculiX 솔버로 계산 완료된 DoE 데이터 포인트.
- **반투명 음영 대역 ($\pm 2\sigma$ Confidence Envelope)**: 95% 신뢰 구간을 의미하며, 샘플이 밀집된 영역은 띠가 좁아지고(높은 신뢰도), 샘플이 희소한 영역은 띠가 넓어져 추가 샘플링(Active Learning)이 필요한 영역을 직관적으로 식별.

---

### 3.3 설계변수 기여도 분석: 민감도 토네이도 차트
어떤 설계변수가 구조물의 안전성과 중량에 가장 지대한 영향을 미치는지 Sobol 글로벌 민감도 분석(First-order & Total-order Sobol Indices)을 통해 순위화합니다.

![민감도 토네이도 차트](../images/screen_mdo_studio_dark_tornado.png)
*(그림 4: 글로벌 민감도 지수 토네이도 차트)*

- 막대 길이가 길수록 해당 목적함수에 미치는 영향력이 큰 지배적 설계변수임을 의미.
- 불필요한 미소 영향 변수를 조기에 선별(Screening)하여 최적화 계산 효율 극대화.

---

## 4. 3분 퀵스타트 워크플로

```mermaid
flowchart LR
    A["1. 덱 불러오기<br/>(Load Deck: .inp)"] --> B["2. 토큰 자동 감지<br/>(Auto-detect @@TOKEN@@)"]
    B --> C["3. 스터디 실행<br/>(Run All Points)"]
    C --> D["4. 파레토 최적점 선택<br/>(Pareto Knee Point)"]
    D --> E["5. 현재 설계로 적용<br/>(Set as Current Design Point)"]
```

1. **상단 툴바 [Load Deck]**: 파라메트릭 치환 토큰(`@@변수명@@` 또는 `<변수명>`)이 삽입된 CalculiX `.inp` 파일을 엽니다 (또는 [Load Demo Study]를 눌러 25개 검증 데이터를 즉시 로드).
2. **좌측 스터디 설정 (Setup Dock)**: 덱 내의 파라메터 토큰이 좌측 변수 테이블에 자동 등록됩니다. 상한/하한/규격값을 지정하고 목적함수와 제약조건을 설정합니다.
3. **상단 툴바 [Run All Points]**: 설계 탐색 매트릭스를 백그라운드에서 순차/병렬 평가하고 결과를 실시간으로 갱신합니다.
4. **우측 분석 대시보드에서 최적점 탐색**:
   - [파레토 프론트] 탭에서 상충 관계를 확인하고 Knee Point(★)를 확인합니다.
   - [반응 곡면] 탭에서 Kriging 대리모델의 예측 경향과 불확실성($\pm 2\sigma$)을 검토합니다.
5. **평가 이력 테이블에서 최적점 우클릭 -> [Set as Current Design Point]**:
   - 원본 인풋 파일의 안전 백업 파일(`*.inp.bak`)이 자동 생성됩니다.
   - 선택한 최적 설계변수 수치로 활성 `.inp` 파일이 원클릭 갱신됩니다.
