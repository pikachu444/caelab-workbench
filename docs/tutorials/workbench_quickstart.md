# 워크벤치 시작 가이드

[README](../../README.md)에서 시작하는 설치·사용 안내입니다. 기본 경로는 프로젝트 구성 → 네이티브 입력 준비 → 해석 → 결과 확인 → 전문가 검토입니다. MDO는 필요할 때 별도로 사용하는 연구 도구입니다.

아래는 현재 구현된 조작과 파일 역할을 설명합니다. PrePoMax Windows 끝단 실행 등 아직 확인하지 못한 범위는 [현재 상태](../../CURRENT_STATE.md)를 따릅니다. 이 안내 자체가 전체 연결의 실환경 검증 완료를 뜻하지는 않습니다.

## 설치

Python 3.11 이상과 Git을 설치한 Windows의 PowerShell에서 실행합니다. `python --version`으로 사용할 Python을 먼저 확인하세요.

```powershell
git clone https://github.com/pikachu444/caelab-workbench.git
cd caelab-workbench
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui,rag]"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m apps.workbench_qt
```

이후 명령도 저장소 루트에서 가상환경의 Python을 사용합니다. `gui`는 PySide6·VTK·SciPy·Matplotlib을, `rag`는 PDF 텍스트 읽기용 pypdf를 추가합니다. MDO 수치 연구에는 `.[mdo]`, 개발 검사에는 `.[dev]`가 별도로 필요합니다. 설치 버전은 `pyproject.toml`의 범위와 Python에 따라 달라지며 Python 3.11에서는 PySide6 6.12 미만으로 제한합니다.

일반 Python 패키지는 격리된 `.venv`에 설치됩니다. 외부 GUI·솔버나 LLM 공급자 연결은 따로 준비합니다. 가상환경을 활성화하지 않아도 위 명령으로 실행할 수 있으며 PowerShell 실행 정책을 바꿀 필요가 없습니다.

## 1. 외부 프로그램 준비

사용할 FreeCAD·PrePoMax·CalculiX·ParaView를 별도로 설치합니다. Python 패키지 설치는 이 프로그램을 설치하지 않습니다. 아래 예시를 실제 실행 파일 경로로 바꿔 같은 PowerShell 세션에서 실행하세요.

```powershell
$env:CAELAB_FREECAD = 'C:\실제설치경로\FreeCAD.exe'
$env:CAELAB_PREPOMAX = 'C:\실제설치경로\PrePoMax.exe'
$env:CAELAB_CCX = 'C:\실제설치경로\ccx.exe'
$env:CAELAB_PARAVIEW = 'C:\실제설치경로\paraview.exe'
Test-Path -LiteralPath $env:CAELAB_CCX
.\.venv\Scripts\python.exe -m apps.workbench_qt
```

필요한 프로그램만 설정하면 됩니다. 각 경로가 실제 파일인지 `Test-Path`로 확인하세요. ParaView는 화면이 있는 `paraview` 실행 파일을 지정하며 `pvpython`·`pvbatch`를 대신 지정하지 않습니다. 프로그램 탐색과 실행 지원은 별개이며, Windows 자동 설치 도우미나 WSL 연결이 모두 구현됐다고 가정하지 마세요.

### CAELab Prep와 공용 입력 위젯

Prep는 별도 저장소·프로그램입니다. 두 저장소를 나란히 받았다면 Workbench 환경에서 다음처럼 공용 위젯을 설치합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e "../caelab-prep[widgets]"
```

MDO Studio의 Continuous 변수 Lower·Upper·Initial에 Prep `NumericEdit`을 사용합니다. 단위·변수 도메인·Apply·revision·저장은 MDO 서비스가 맡습니다. 불완전한 숫자는 Apply 시 오류와 함께 보존되며 Escape는 마지막으로 적용한 값으로 복원합니다. Prep가 없으면 기존 Qt 표 입력을 사용합니다.

여기서는 독립 Prep GUI용 가상환경을 별도로 구성합니다. 아래 명령은 **Workbench 저장소 루트**에서 실행하고, 나란히 받은 Prep 저장소에 `.venv`가 아직 없는 경우의 예입니다. 기존 Prep 환경이 있으면 새로 만들지 말고 그 환경의 launcher를 지정하세요.

```powershell
python -m venv ..\caelab-prep\.venv
..\caelab-prep\.venv\Scripts\python.exe -m pip install -e "../caelab-prep[gui]"
$env:CAELAB_PREP = (Resolve-Path '..\caelab-prep\.venv\Scripts\caelab-prep.exe').Path
```

Workbench의 연결 설정으로 같은 실행 파일을 지정해도 됩니다. GUI 환경과 공용 위젯 환경은 별개입니다. Prep의 플랫폼별 준비 조건은 [Prep 설치 안내](https://github.com/pikachu444/caelab-prep#readme)를 확인하세요.

1. 전처리 프로그램으로 **CAELab Prep**을 추가하고 원본 `.rad`를 입력으로 등록합니다.
2. Prep에서 편집한 RAD 작업 문서 `.caeprep`를 저장해 전처리 노드의 작업 문서로 등록합니다. 기본 열기는 작업 문서, 명시적인 입력 열기는 원본을 사용합니다.
3. Prep에서 내보낸 `.rad`를 출력·솔버 입력으로 등록합니다. 작업 문서를 바꾸면 하류 연결은 stale 상태가 되므로 새 내보내기·등록이 필요합니다.

중립 재료·곡선 `.caeprep`는 RAD 작업 문서와 다릅니다. Workbench는 문서 종류를 확인하고 모델 해석·버전 검증은 Prep에 맡깁니다. 열기는 `caelab-prep gui <파일>`을 실행하며 계산을 시작하지 않습니다.

### OpenRadioss / OpenCourant

Windows 배포판을 별도 폴더에 풀고 배포판의 라이선스·런타임을 함께 보존합니다. 연결 설정의 OpenCourant 경로나 `CAELAB_OPENCOURANT`에 설치 홈(하위 `exec/`에 Starter·Engine이 있는 폴더) 또는 `starter_*` 실행 파일을 지정합니다.

```powershell
$env:CAELAB_OPENCOURANT = 'C:\실제설치경로\OpenCourant'
Test-Path -LiteralPath (Join-Path $env:CAELAB_OPENCOURANT 'exec')
.\.venv\Scripts\python.exe -m apps.workbench_qt
```

현재 두 단계 어댑터는 Windows 설치 홈의 `extlib/intelOneAPI_runtime/win64`, `extlib/hm_reader/win64`, `extlib/h3d/lib/win64`를 솔버 자식 프로세스 PATH에 추가하고 H3D·KMP 스택 기본값을 전달합니다. 기존 PATH와 명시한 H3D·스택 설정은 보존합니다. 전역 PATH는 변경하지 않습니다. 이 경로 지정으로 누락 DLL이 설치되지는 않습니다. 로컬 빌드는 해당 빌드에 맞는 런타임 경로를 실행 환경에 준비해야 하며 임의 버전 DLL을 섞지 마세요.

Prep에서 내보낸 `이름_0000.rad`를 솔버 입력으로 등록합니다. 같은 폴더에 `이름_0001.rad`와 상대 INCLUDE 파일을 둡니다. 지원 프로파일은 Engine `/RUN/이름/1` 한 개, 직렬 실행입니다. 절대·상위 폴더·별칭 INCLUDE나 재시작 연속 실행은 추정하지 않고 실행 전에 진단합니다.

Run은 원본 이름·bytes를 보존한 사본을 만들고 Starter의 종료·restart 파일을 검증한 뒤 Engine을 실행합니다. 단계 상태·로그·결과 원본은 기존 실행 목록에서 확인합니다. 프로세스 정상 종료, 결과 판독 상태, 모델의 물리 타당성은 각각 확인해야 합니다. 현재 native binary animation은 물리 필드 전체를 판독하지 못하므로 `partial`·빈 프레임을 완전한 후처리 성공으로 읽지 마세요. 검증한 패키지와 플랫폼 범위는 [W3 기록](../../CURRENT_STATE.md#rad-w3)을 따릅니다.

## 2. 프로젝트와 네이티브 파일 연결

1. **프로그램 추가**에서 FreeCAD CAD, 전처리기, CalculiX, 후처리기, 전문가 상자를 필요한 만큼 추가하고 **저장**으로 프로젝트 JSON의 위치를 정합니다.
2. 상자를 선택해 **선택 설정**에서 파일을 지정합니다. 전처리 프로그램은 PrePoMax로 선택합니다. 전처리기·솔버 선택을 바꾸더라도 해당 프로그램의 미구현 연결이 자동으로 지원되지는 않습니다.
3. FreeCAD 상자에 `.FCStd` 원본을 등록합니다. FreeCAD에서 STEP를 내보내고 워크벤치의 CAD 출력으로 등록합니다.
4. **연결…**로 CAD 출력을 전처리 입력에 연결한 뒤 전처리 상자를 엽니다. 편집 문서가 아직 없으면 연결된 입력을 엽니다. 기존 PMX를 등록한 상태에서 새 STEP를 가져오려면 **형상·입력 가져오기**를 사용합니다. PrePoMax에서 메시·요소·재료·접촉·하중·단계·출력을 설정합니다.
5. PrePoMax에서 `.pmx`를 저장하고 **편집 문서 → 등록…**으로 등록합니다. 다음 기본 열기는 PMX를 사용합니다. STEP는 형상 교환 파일이므로 해석 조건이나 CAD 설계 이력을 전달하지 않습니다.
6. PrePoMax에서 내보낸 `.inp`를 전처리 출력으로 등록하고 CalculiX 입력에 연결합니다. 파일의 **단위…**에서 실제 덱의 일관 단위를 선언합니다. 단위 표시는 원본 숫자를 자동 환산하지 않습니다.

이미 작성한 CalculiX `.inp`가 있으면 솔버 입력에 직접 지정할 수 있습니다. 기존 FreeCAD FEM 입력 경로도 유지합니다. CAD나 PMX를 수정했다면 전처리기에서 조건을 확인하고 덱을 다시 내보내 등록하세요. 연결만으로 형상 변경 뒤 재메시·조건 재연결이 자동 수행되지는 않습니다.

## 3. 계산과 결과 확인

CalculiX 상자에서 해석 창을 열어 현재 입력과 단위를 확인한 뒤 **현재 입력 계산**을 누릅니다. 계산 중에는 상태·로그를 확인하고 필요하면 **진행 중 계산 취소**를 사용합니다. 프로젝트나 입력 파일을 여는 것만으로 계산하지 않습니다.

계산 후에는 실행 목록에서 확인할 계산을 선택하고 종료 상태와 결과 판독 상태를 함께 확인하세요. 현재 입력과 과거 실행의 입력은 다를 수 있습니다. 입력이나 편집 문서를 바꾸면 연결된 결과가 오래된 상태가 되므로 변경 전 계산을 새 모델의 결과로 읽지 않도록 확인합니다.

후처리 상자를 솔버에 연결해 선택한 실행의 결과를 엽니다. ParaView에는 변환된 PVD/VTU를, 후처리기로 PrePoMax를 선택하면 같은 실행의 원 FRD를 전달합니다. 파일이 없거나 판독되지 않은 결과를 다른 실행의 결과로 자동 대체하지 않습니다.

## 4. 같은 계산을 근거로 전문가와 토론

전문가 상자를 솔버와 연결하고 독립 전문가 창을 엽니다. **연결 설정**에서 사용할 공급자를 설정합니다. 독립 실행도 가능합니다.

```powershell
.\.venv\Scripts\python.exe -m apps.workbench_qt.expert_window
```

프로젝트에서 연 전문가 창에서는 **계산 결과 가져오기…**로 대상 실행과 프레임을 확인할 수 있습니다. 프로젝트 없이 독립 실행한 창에서는 이 버튼이 표시되지 않으므로 **파일 첨부…**로 자료를 추가합니다. 자료를 미리보기로 읽는 것과 질문 근거로 선택하는 것은 다릅니다. **이번 질문에 첨부**로 선택하고 질문을 작성해 **보내기**를 누릅니다.

답변의 출처와 입력·단위·결과 프레임을 원자료와 대조하세요. **제안 검토**에서는 원본 변경 여부를 확인하거나 제안을 거절할 수 있습니다. 실제 수정은 전용 프로그램에서 수행하고 새 덱을 등록해 다시 계산합니다. 전문가 답변이 원본 수정이나 재해석을 자동 실행하지 않습니다.

## 5. 저장과 이어서 작업하기

부모 프로젝트의 **저장**으로 프로그램·파일·연결을 보존합니다. 전문가 대화는 해당 창에서 저장합니다. 기본 프로젝트 `project.json`과 함께 만들어지는 `project.runs` 실행 기록 폴더도 보관하세요. 프로젝트 JSON만 복사하면 계산·대화·보존 자료가 누락될 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m apps.workbench_qt 'C:\작업\project.json'
.\.venv\Scripts\python.exe -m apps.workbench_qt.expert_window --discussion 'C:\작업\discussion.json'
```

재열기는 자동 계산이나 LLM 호출을 시작하지 않습니다. 프로젝트 저장 형식 3은 이전 형식 1/2를 읽습니다.

프로젝트 밖의 독립 대화를 이동·복사할 때는 `discussion.json`과 같은 위치의 `assets/` 폴더를 함께 보관해야 첨부 근거도 유지됩니다.

**다른 이름으로 저장…**은 소유 실행 기록·대화·자산을 복사하지만 일반 CAD·PMX·INP 원본까지 모두 복제하지는 않습니다. 외부 파일 참조가 계속 유효한지 확인하세요. 연결 MDO 연구를 복사할 때 편집 중인 Studio가 있으면 먼저 닫아야 합니다. 다른 창이 먼저 저장해 충돌하면 기존 파일을 덮어쓰지 말고 최신 프로젝트와 미저장 변경을 확인합니다.

## 6. 설계안 비교가 필요할 때

다음 의존성을 설치한 뒤 저장한 CalculiX 노드를 선택하고 **도구 → MDO Studio 열기…**를 사용하거나 독립 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mdo]"
.\.venv\Scripts\python.exe -m apps.workbench_qt.mdo_studio
```

변수·응답·실험계획, 실제 계산과 예측의 구분, 현재 설계 적용·Undo·재해석, 저장·읽기 전용 열기는 [MDO 실습 가이드](01_end_to_end_calculix_mdo_guide.md)에 있습니다. 적용은 덱 토큰 치환이며 FreeCAD 형상 갱신·재메시를 수행하지 않습니다.

## 문제가 생겼을 때

| 증상 | 먼저 확인할 것 |
|---|---|
| `No module named PySide6`·`vtk` | 실행한 Python이 `.venv\Scripts\python.exe`인지, 같은 환경에 `.[gui,rag]`를 설치했는지 |
| 가상환경 활성화 스크립트가 차단됨 | 활성화 대신 위의 `.venv\Scripts\python.exe` 직접 실행 명령 사용 |
| 프로그램을 찾지 못하거나 열지 못함 | 연결 설정·환경변수의 실제 실행 파일, 설치 운영체제와 지원 범위 |
| Starter가 `3221225781`로 종료 | 입력 계산 전 DLL loader 실패 가능성. 해당 빌드의 Intel·reader·H3D 배포 파일과 자식 PATH 확인; 실행 파일 발견만으로 런타임 충족을 판단하지 않음 |
| 계산을 시작할 수 없음 | 프로젝트 저장 여부, 입력·INCLUDE·편집 문서의 존재와 변경 상태, 솔버 경로 |
| 결과가 없거나 오래된 것으로 표시됨 | 선택한 실행, 입력 개정, 원로그와 결과 판독 상태; 파일 변경 뒤 필요하면 재계산 |
| ParaView에 열 결과가 없음 | 선택 실행의 PVD/VTU 존재 여부와 해석 창의 판독 상태 |
| 전문가가 답하지 않거나 근거가 다름 | 공급자 연결 설정, 이번 질문에 첨부한 자료, 대상 실행과 프레임 |
| 재열기 후 기록 누락·저장 충돌 | 프로젝트의 실행 기록 폴더와 외부 원본 경로, 다른 창에서 저장한 변경 |

현재 미구현·미검증 항목은 [CURRENT_STATE.md](../../CURRENT_STATE.md), 질문·재현 가능한 문제 보고는 [Issues](https://github.com/pikachu444/caelab-workbench/issues)를 이용하세요.
