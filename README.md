# caelab-workbench

AI 어시스턴트와 함께 설계하고 구현하는 CAE 연구 환경입니다. 기존 autonomous-cae-lab의 감사와 재설계 결과를 바탕으로 새로 구축합니다.

## 목적

- 공통 구조 모델을 중심으로 솔버 변환기와 전후처리 어댑터를 갖춘 워크벤치를 만듭니다.
- 흐름: FreeCAD 모델 → 솔버 계산(CalculiX부터) → ParaView 결과 → 실제 설정과 로그를 근거로 한 검토와 재실행.
- AI 없이도 전 기능이 동작하는 것이 전제입니다.

## 작업 방식

- 설계, 코드 작성, 자동 테스트는 Linux 환경에서 진행합니다.
- Windows 실화면 GUI 검증처럼 로컬에서만 가능한 검사는 Windows 로컬에서 수행합니다.
- 확인 안 된 것을 확인됐다고 쓰지 않습니다. 단계마다 확인한 범위를 커밋과 문서에 남깁니다.

## 상태

- 2026-10-21 저장소 개설. 재설계 골격 구현에 착수했습니다 (자세한 상태는 CURRENT_STATE.md).

## 실행

```bash
pip install -e ".[gui,dev]"
python -m pytest            # 자동 테스트 (CalculiX가 있으면 끝단 실행 테스트까지)
python -m apps.workbench_qt # GUI 셸 (프로젝트 폴더를 인자로 줄 수 있습니다)
```

CalculiX 실행 파일은 PATH에서 찾고, `CAELAB_CCX` 환경변수로 직접 지정할 수도 있습니다.
