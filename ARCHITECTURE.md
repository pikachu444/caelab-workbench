# 구조

기준은 이 저장소 DESIGN.md의 제품 정의와 5가지 결정입니다. 본문 정합성 개정일은 2026-10-23입니다. 목표 구조와 현재 구현을 구분하며, 기존 저장소의 문서는 이식 근거 자료로만 사용합니다.

## 설계 원칙 (목표)

- **연결이 본선입니다** (`caelab/connections`). 전처리는 솔버별 네이티브 프로그램을 그대로 씁니다: CalculiX는 FreeCAD FEM, Code_Aster는 Salome, OpenRadioss는 Gmsh. 후처리는 ParaView입니다. 워크벤치는 이 프로그램들을 찾고, 상태를 보여주고, 파일을 넘겨 실행합니다.
- **자체 변환기는 보조입니다** (`caelab/solvers`). 네이티브 전처리기가 없는 솔버 경로에서만 쓰며, 지원표는 변환기 안에 한 곳에서 선언합니다.
- **자체 공통 형식은 작게 유지합니다.** 프로젝트·파일·작업 관계와 토론 근거로 쓸 결과 요약(`caelab/result_summary`)을 공통으로 둡니다. 기존 모델 스키마(`caelab/model`)는 보조 변환기와 검사·실행 경로의 내부 표현으로 제한할 대상이며, 현재 제품 경로에서의 분리는 아직 끝나지 않았습니다.
- 입력 검사만 하고 허용 목록은 없습니다 (`caelab/validate`). 검증 사례가 없다는 이유로 실행을 막지 않고, 결과에 검증 상태를 표시합니다.
- 실행 소유는 JobManager 하나, 명령 경로는 Workbench 하나로 모읍니다 (`caelab/jobs.py`, `caelab/workbench.py`). 현재 동기 실행과 GUI 직접 프로그램 열기가 남아 있으며 CLI·MCP까지 통합 완료된 상태는 아닙니다.
- 프로젝트를 여는 것만으로는 계산이나 LLM 호출이 일어나지 않습니다.

## 현재 범위

- 연결 층: 프로그램 8종 등록(FreeCAD·Salome·Gmsh·CalculiX·Code_Aster·OpenCourant·FEniCSx·ParaView), 환경 변수 우선·PATH 탐색, 실행 명령 생성(실행 자체는 호출자 결정).
- 결과 요약: 결과의 solver_id와 선언 단위를 보존한다. 누락 수치는 null/미확인으로 남기며 단위를 추정하지 않는다. 미터·밀리미터 환산은 선언된 길이 단위에만 적용한다.
- 보조 변환기: CalculiX 1종. 입력·지원 검사 뒤 덱을 생성하고 ccx를 실행한다. 미방출 조건·옵션은 객체 위치와 함께 거부한다. FRD/DAT의 최종 프레임·요청 필드를 검사하며 실행·판독 오류는 failed와 디스크 로그로 남긴다. 네이티브 본선의 비동기·취소·저장 구현은 P4에 남아 있다.
- 수치 엔진: `caelab/numerical` — DOE·최소제곱 적합·차분진화 최적화·UQ·Sobol 민감도·RBF 대리모델. 평가자는 값→응답 딕셔너리 계약이며, `model_binding`이 기존 내부 모델의 숫자 위치와 결과 스칼라를 연결한다. 실제 열린 네이티브 프로젝트와의 결합 완료를 뜻하지 않는다.
- PDE 라인: `caelab/pde` — FEniCSx(dolfinx) 실행 환경. 공통 모델 스키마에 넣지 않고 자체 형식을 유지한다.
- GUI: PySide6 (`apps/workbench_qt`). 부모는 ANSYS Workbench식 프로젝트 화면이며 프로그램 발견 상태와 열기 기능이 있다. 현재 고정 예제·파일과 실제 실행에 연결되지 않은 상자 완료 표시가 남아 있다. 자체 작업 창은 확인·검사·실행·요약으로 제한하는 것이 기준이고, 기존 최적화 설정 패널의 허용 범위는 사용자 결정 대기다.

## 아직 없는 것

- 사용자 네이티브 입력부터 결과 열기·토론·저장/재개까지의 본선 연결은 진행 중입니다. 개별 프로그램 설치·연기 테스트를 전체 연결 완료로 세지 않습니다.
- 현재 원격 코드에는 MainWindow가 import하는 expert_panel.py가 누락되어 있습니다. 기존 패널 복구와 실제 결과 전달이 필요합니다. OpenAI 호환 공급자의 요청 코드는 있지만 OpenScience.discuss는 실행 미구현 상태입니다.
- Windows 실화면 검증 여부는 별도로 기록합니다. 아래 플랫폼 정보와 Linux 개발 환경 기록만으로 Windows 사용 가능 또는 전체 GUI 완료를 선언하지 않습니다.

## 플랫폼별 실행 전제

| 경로 | 현재 코드·매니페스트 범위 | 남은 확인·구현 |
|---|---|---|
| Linux 설치 도우미 | AppImage·압축 파일·micromamba·수동 파일 등록 경로. Code_Aster는 .sif와 Apptainer 래퍼 | 개발자 설치 기록과 새 환경의 실제 실행 확인을 구분 |
| Windows 설치 도우미 | 배포 검증 상태와 installer_support=not-implemented를 구분. 설치 요청은 not-implemented·종료 코드 1, 레지스트리·셸 스크립트를 만들지 않음 | Windows 네이티브 설치·실행·검증 구현. Linux 대상으로 강제 지정해도 Windows 호스트에서 실행하지 않음 |
| Windows의 Code_Aster | 검증하지 못한 MSI 파일명을 제거하고 배포 안내 페이지만 유지. execution_environment=unresolved. .sif 래퍼는 Linux 경로에만 있음 | 네이티브 배포 또는 WSL2 경로 선택·확인. 현재 WSL 호출·경로 변환 구현 없음 |
| Windows의 FEniCSx | 매니페스트에 WSL2에서 Linux 항목을 사용한다는 안내 | Windows 앱에서 WSL 실행 환경·파일·작업을 연결하는 구현 |
| 연결 층 | 현재 환경변수·PATH 탐색과 실행 파일/파일 인자 생성 | 설치 레지스트리, cwd/env, 플랫폼별 명령, 결과 회수·취소 결합 |

매니페스트에는 설치 구현 여부(`installer_support`), 실행 환경(`execution_environment`), 전제(`requires`)와 배포 검증 상태를 구분해 기록했습니다. 연결 계약에는 호스트 OS, 실행 파일·인자·cwd·환경, Windows/WSL 경로 교환·취소·결과 회수를 추가해야 합니다. 메타데이터와 실패 상태 처리는 플랫폼 실행 지원 자체를 뜻하지 않습니다.
