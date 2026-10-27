# 실제 CalculiX 덱으로 시작하는 MDO 가이드

이 안내는 100×20 mm 인장판의 두께를 바꾸는 작은 연결 실습입니다. 입력·단위·원자료를 보존하고 실제 관측과 대리모델 예측을 구분합니다. 최종 후보의 별도 공개 입력 검증은 [CURRENT_STATE](../../CURRENT_STATE.md)에 기록합니다. 서비스 실행과 offscreen 렌더는 Windows 마우스·IME·DPI·3D 화면 검증을 대신하지 않습니다.

## 1. 입력과 실행 환경

[README](../../README.md)의 의존성을 설치하고 실제 설치 경로를 지정합니다.

```powershell
$env:CAELAB_CCX = 'C:\실제설치경로\ccx.exe'
$env:CAELAB_PARAVIEW = 'C:\실제설치경로\paraview.exe'
Test-Path -LiteralPath $env:CAELAB_CCX
Test-Path -LiteralPath $env:CAELAB_PARAVIEW
$caseDir = Join-Path $env:USERPROFILE 'caelab-tension-tutorial'
New-Item -ItemType Directory -Force $caseDir
Copy-Item docs/tutorials/assets/tension_plate_template.inp $caseDir
python -m apps.workbench_qt.mdo_studio
```

예시 설치 경로를 그대로 사용하지 마세요. 재실행할 때는 기존 적용 파일을 덮지 않는 새 실습 폴더를 사용합니다. 부모 프로젝트에서는 저장한 CalculiX 노드를 선택하고 **MDO Studio 열기**를 사용할 수 있습니다.

[완전한 입력 파일](assets/tension_plate_template.inp)은 4개 S4 셸 요소와 10개 입력 절점으로 구성됩니다. 탄성계수 210000 N/mm², 포아송비 0.3, 밀도 `7.85e-9 tonne/mm³`, 합계 인장 하중 1000 N이며 일관 단위는 mm–N–s입니다. `*NODE FILE U`, `*EL FILE S`, `*NODE PRINT RF`, `*EL PRINT EMAS/EVOL` 출력 요청을 보존합니다. `<THICKNESS>`는 MDO 토큰으로 ccx 자체 문법이 아닙니다.

현재 서비스의 완료 판정으로 검증한 범위는 기본/단위 시간의 기계 정적 단계입니다. 종료 코드 0만으로 성공으로 표시하지 않고 STA의 마지막 단계·시간과 FRD 필드의 완전성을 함께 확인합니다. 이 범위를 벗어난 절차의 원자료는 보존하지만 검증된 최적화 관측으로 자동 승격하지 않습니다.

## 2. 스칼라 변수와 응답 설정

1. **File → Open Native Input…**에서 복사한 덱을 엽니다. 토큰 목록이 표시됩니다.
2. 토큰이 없는 일반 숫자 덱은 **Study → Create Scalar Token…**에서 원본 파일·행·숫자의 출현 위치를 선택하고 미리보기를 확인합니다. 허용된 재료·단면 등의 스칼라 필드만 바인딩하며 원본은 아직 바꾸지 않습니다. 여러 토큰은 순서대로 추가할 수 있습니다.
3. **Configure Study**에서 `THICKNESS`의 Domain=`Continuous`, Lower=`1`, Upper=`3`, Initial=`2`, Unit=`mm`를 입력합니다.
4. 응답·목적·제약을 아래처럼 선언하고 **Apply**를 누릅니다. **Revert**는 미적용 편집을 되돌립니다. 설정 적용은 새 연구 revision이며 이전 계산의 정의를 바꾸지 않습니다.

| Response | Unit | Objective | Constraint / Limit | Source / Field / Component / Set / Reduction |
|---|---|---|---|---|
| mass_eall | N*s**2/mm | MIN | 없음 | DAT / EMAS / value / EALL / sum |
| disp | mm | MIN | UPPER / 0.02 | FRD / DISP / magnitude / ALL / max |
| stress | N/mm**2 | 없음 | UPPER / 40 | FRD / STRESS / von_mises / ALL / max |

Frame은 `last`입니다. 응력은 절점에 외삽·평균된 텐서에서 얻은 von Mises 값으로, 적분점 최대 응력과 같다고 가정하지 않습니다. 제약은 실습용이며 재료 인증 허용값이 아닙니다. 질량 단위 `N*s**2/mm`는 tonne와 동등합니다. 단위 칸을 kg로 바꾸는 것만으로 값이 환산되지는 않습니다.

## 3. DoE와 실제 계산

**Study → Generate DOE…**에서 `lhs`, 표본 수 `16`, seed `42`를 선택합니다. 생성된 16점은 미계산 상태입니다. **Run → Run All Pending**으로 실제 CalculiX를 실행합니다. 선택한 일부 행만 계산하려면 **Run Selected / Pending**을 사용합니다.

실행별 입력 사본·INCLUDE·`model.frd`, `model.dat`, `model.sta`, `job.log`, 영수증을 보존합니다. 독립 연구는 원본 옆 `.caelab_mdo/<study-id>/runs`, 프로젝트 연결 연구는 부모 RunStore에 기록됩니다. GUI의 **Inspect Selected Attempts / Log**에서 재시도를 포함한 실행 근거를 확인합니다.

**Stop Scheduling**은 대기 중인 점의 새 실행을 중지하고 실행 중인 계산은 마치게 합니다. **Cancel Active Campaign**은 소유한 활성 계산도 취소합니다. 실패·취소는 제약 위반과 다르며 응답을 0으로 채우지 않습니다. 원본 적용과 설정 변경은 활성 계산이 끝난 뒤 진행합니다.

이 입력의 독립 확인식은 두께 t(mm)에 대해 질량 `1.57e-5*t` tonne, 축응력 `50/t` N/mm², 끝단 ux `0.0238095238/t` mm, FIX의 합력 Fx=−1000 N입니다. `disp`는 벡터 크기이므로 ux와 조금 다릅니다.

| 두께 mm | 실제 DAT 질량 tonne | 실제 최대 변위 mm | 실제 절점 von Mises N/mm² |
|---:|---:|---:|---:|
| 1 | 0.0000157 | 0.02385235 | 49.998 |
| 2 | 0.0000314 | 0.01192626 | 24.999 |
| 3 | 0.0000471 | 0.00795086 | 16.666 |

이는 이전 CalculiX 2.22 입력 검증의 [원자료 식별·해시 기록](assets/tension_plate_reference.json)입니다. 새 DoE가 이 세 값을 반드시 포함하지는 않으며 GUI를 이 표로 채우지 않습니다. 메시 수렴이나 일반 구조 안전성의 증거로 확대하지 않습니다.

## 4. 대리모델 검증·후보 탐색·다섯 뷰

**Study → Train / Validate Surrogate…**에서 실제 완료 자료로 모델을 학습합니다. 학습/검증 분할, 응답별 단위와 오차를 확인합니다. 모델이 학습됐다는 사실과 검증 기준을 만족했다는 사실은 다릅니다. **Constrained Surrogate Search…**의 NSGA-II는 저장한 대리모델의 목적과 제약을 평가하며 실제 솔버 호출로 표시하지 않습니다.

| 뷰 | 사용자 조작과 해석 |
|---|---|
| Pareto | 2개/3개 목적 축을 선택하고 점을 클릭합니다. 표의 같은 ID와 상세 정보가 선택됩니다. 실제/예측 표식을 구별하며 2D 투영이 전체 목적의 지배 관계를 다시 정의하지 않습니다. |
| Parallel | 물리 변수·응답 축을 재정렬하고 범위를 브러시해 표와 선택을 연결합니다. |
| Surface | 두 연속 변수와 응답을 선택하고 나머지 변수를 고정합니다. 저장된 대리모델의 슬라이스이며 슬라이더는 솔버를 실행하지 않습니다. 이 두께 1변수 입력에는 2변수 표면이 없으므로 필요 변수 수를 안내합니다. |
| Sensitivity | 응답·방법·표본 출처를 확인합니다. Spearman의 부호 있는 상관은 분산 기여율이나 인과 효과가 아닙니다. 대리모델 기반 실험은 실제 솔버 민감도와 구분합니다. |
| Convergence | 실제 시도 수/시간에 따른 유효 후보·실패·위반과 지정한 기준점의 HV를 확인합니다. 다목적 문제를 임의의 단일 최솟값으로 표시하지 않습니다. |

VTK를 사용할 수 없으면 명시적인 **2D slice fallback**이 표시됩니다. 이는 3D 뷰 통과의 증거가 아닙니다. Tree와 Setup은 **View** 메뉴에서 열고 닫을 수 있으며 **Save Layout / Restore Default Layout**으로 배치를 관리합니다.

## 5. 실제 확인 → 적용 → 재해석 → ParaView

1. 예측 후보를 선택하고 **Run → Fresh Native Confirmation**을 실행합니다. 새 ACTUAL 행이 생기며 원 예측 행을 실제 관측으로 바꾸지 않습니다.
2. 실제 계산이 성공한 행의 응답과 제약을 확인합니다. Pareto에서 선택한 점과 표의 ID가 같은지 확인합니다.
3. **Set as Current Design Point**의 파일 변경·백업·단위·제약 위반 미리보기를 검토하고 적용합니다. 루트와 INCLUDE 원본의 개정이 바뀌었으면 거부됩니다.
4. 원본 옆 `.bak`와 후속 백업이 보존되고 적용 전 바이트와 일치하는지 확인합니다. **Undo Current Design Apply**로 이전 덱을 복원할 수 있습니다. 일반 숫자 덱에서 시작했다면 백업도 원래 숫자 덱입니다.
5. **Run Current Design**으로 적용된 덱을 새로 계산한 뒤 **Open Current Rerun in ParaView**를 실행합니다. 해당 실행의 VTU/PVD에서 변위·응력 전체 필드를 선택합니다. 적용 자체는 계산이나 ParaView 열기를 실행하지 않습니다.

외부 CAD 원본을 수정하거나 다시 메시하는 기능이 아닙니다. 여러 파일 적용은 저널과 복구를 갖춘 절차이며, 잠금을 따르지 않는 외부 편집기까지 전역 파일 시스템 원자성을 보장하지 않습니다. 중단된 적용이 남으면 복구를 마친 뒤 후속 작업을 진행합니다.

## 6. 저장·재열기·오류 확인

**File → Save**는 연구를 저장하고 **Save As…**는 소유 실행·모델을 포함한 독립 복사본을 만듭니다. 부모 프로젝트에 연결돼 있던 연구를 Save As하면 원 부모의 관계는 유지되고 복사본은 독립 연구가 됩니다. 외부 INP는 두 연구가 같은 파일을 참조하므로 한쪽의 적용 후 다른 쪽은 원본 개정 충돌을 확인해야 합니다.

다른 위치로 옮길 때는 `study.json`과 함께 `study.mdo-data` 폴더를 보존합니다. 외부 INP 원본은 자동 이동하지 않습니다. 부모 프로젝트의 **다른 이름 저장**은 평평한 RunStore와 연결 연구를 복사하며, 편집 중인 Studio가 있으면 먼저 해당 편집 창을 닫아야 합니다.

```powershell
python -m apps.workbench_qt.mdo_studio --study "C:\연구\study.json"
python -m apps.workbench_qt.mdo_studio --study "C:\연구\study.json" --read-only
```

재열기에서 변수·기준·행·시도·선택·현재 설계·모델과 원자료 무결성을 대조합니다. 파일을 열기만 해서는 계산하지 않습니다. 읽기 전용은 동시에 검토할 수 있고 복구나 저장 쓰기를 하지 않습니다.

| 증상 | 조치 |
|---|---|
| 솔버/ParaView 없음 | 실제 실행 파일과 환경변수·연결 설정 확인 |
| 필수 응답 또는 최종 단계 미확인 | 실행 로그·STA·FRD/DAT와 출력 요청 확인; 실패 행은 적용 불가 |
| 원본·결과 해시 불일치 | 외부 편집 원인 확인, 보존 자료 복구 또는 새로운 계산 |
| 적용 중 쓰기 실패/중단 | 백업·저널을 유지하고 복구 명령 사용; 외부 편집 충돌은 덮어쓰지 않음 |
| 다른 창이 프로젝트를 먼저 저장함 | 해당 창의 미저장 편집을 보존하고 저장된 프로젝트를 다시 열어 조정 |

출력 의미는 [CalculiX 2.22 공식 매뉴얼](https://www.dhondt.de/ccx_2.22.pdf)을 따릅니다. 실제 인도 가능 여부는 [CURRENT_STATE](../../CURRENT_STATE.md)의 관측 결과와 남은 조건을 확인하세요.
