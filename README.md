# caelab-workbench

AI 어시스턴트와 함께 설계하고 구현하는 CAE 연구 환경입니다. 기존 autonomous-cae-lab의 감사와 재설계 결과를 바탕으로 새로 구축합니다.

## 목적

- 각 프로그램을 연결하고, 그 결과를 근거로 토론하는 Windows용 Qt 워크벤치를 만듭니다. 부모 화면은 독립 프로그램을 상자로 선택·연결하는 런처형 프로젝트 화면입니다.
- 전처리 본선은 솔버별 네이티브 프로그램(FreeCAD FEM·Salome·Gmsh), 후처리는 ParaView입니다. 자체 변환기는 보조이며, 공통 형식은 프로젝트 관계·실행 상태·결과와 토론의 근거를 담습니다.
- 흐름: FreeCAD 모델 → 솔버 계산(CalculiX부터) → ParaView 결과 → 실제 설정과 로그를 근거로 한 검토와 재실행.
- 전문가가 연결되지 않아도 프로젝트·계산·결과 확인은 동작해야 합니다. 전문가는 읽고 제안하며, 적용과 실행은 사용자가 각각 선택합니다.
- 설계 판단은 저장소 `DESIGN.md`의 제품 정의와 5가지 기준 결정을 따릅니다. 구현 여부는 코드·실행 결과와 `CURRENT_STATE.md`를 대조합니다.

## 작업 방식

개발·별도 세션 운영·검토·병합 규칙은 [AGENTS.md](AGENTS.md)에 둡니다. 모듈별 AGENTS는 해당 폴더의 책임과 검사만 추가합니다.

작업 순서와 완료 조건은 [기존 개선 계획](docs/review/2026-10-23-codex-improvement-plan.md)을 따릅니다. 새 세션에서는 두 문서를 읽고 현재 원격 코드에서 미완료 항목을 확인해 시작합니다.

## 상태

- 골격과 개별 기능이 있으나 사용자 파일부터 결과·토론·재개까지의 1단계 연결은 진행 중입니다. 확인 범위와 남은 결함은 [CURRENT_STATE.md](CURRENT_STATE.md)에 기록합니다.
- 현재 원격 기준 코드에는 작업 창이 import하는 `expert_panel.py`가 누락돼 있어 해당 창 생성이 실패합니다. 기존 파일 복구가 필요하며, 아래 설치 명령만으로 GUI 전체가 실행 가능하다고 보장하지 않습니다.

## 실행

```bash
pip install -e ".[gui,dev]"
python -m pytest            # 자동 검사; 설치된 솔버의 개별 검사는 전체 GUI 끝단 검증과 다릅니다
python -m apps.workbench_qt # GUI 진입점; 위의 작업 창 누락 결함 참조
```

CalculiX 실행 파일은 PATH에서 찾고, `CAELAB_CCX` 환경변수로 직접 지정할 수도 있습니다.

설치 도우미의 실행 구현은 Linux 대상입니다. Windows 항목은 배포·실행 전제 안내이며 설치 요청은 `not-implemented`와 실패 종료 코드를 반환합니다. Windows의 Code_Aster·FEniCSx 및 WSL 연결 범위는 [ARCHITECTURE.md](ARCHITECTURE.md)를 참조합니다.
