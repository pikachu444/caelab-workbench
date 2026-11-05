# CAD 도구와 PrePoMax 파일 연결

FreeCAD는 기본 CAD 연결입니다. SolidCraft와 CADCraft는 사용자가 별도로
설치한 독립 프로그램을 선택하는 추가 연결이며 Workbench에 동봉하지 않습니다.
설정된 실행 파일은 전역 PATH를 바꾸지 않고 해당 프로그램 열기에 사용합니다.

| 프로그램 | 실행 파일 환경변수 | 소유 작업 문서 | 명시적 교환 파일 |
|---|---|---|---|
| FreeCAD | CAELAB_FREECAD | FCStd | STEP |
| SolidCraft | CAELAB_SOLIDCRAFT | sc, solidcraft | STEP |
| CADCraft | CAELAB_CADCRAFT | DXF, DWG, DWT | DXF, SVG 등 2D 출력 |
| PrePoMax | CAELAB_PREPOMAX | PMX | STEP 입력, INP 출력 |

기존 설치 레지스트리의 프로그램별 경로도 재사용합니다. 명시적으로 지정한
실행 파일이 없으면 다른 설치본으로 자동 대체하지 않습니다. 프로그램 발견,
파일 열기, export, 계산 완료는 각각 다른 상태입니다.

## API의 3D CAD 파일 연결 계약

아래 절차는 Workbench API로 노드와 파일을 등록하는 연결 계약입니다. 기존 frozen
r2의 프로젝트 GUI에는 geometry 프로그램을 SolidCraft/CADCraft로 선택하거나
geometry의 document를 등록하는 연결이 구현되지 않았습니다. 새 편집 모듈의
소스 준비와 실제 소비자 연결·배포 검증은 다음 절의 조건으로 구분합니다.

### CAD 연결 편집 모듈과 소비자 경계

`cad_connection_editor.py`는 선택한 geometry의 CAD와 편집 문서 경로를 표시하고
선택 의도를 알리는 Qt 모듈입니다. FreeCAD 기본값과 기존 프로그램 등록표를
사용합니다. 실행 파일 미설정과 문서 등록 상태를 구분하며, CAD가 미설치여도
오프라인 문서 등록 의도는 전달할 수 있습니다. 프로그램 선택이나 문서 등록이
실제 열기·export·계산 완료를 뜻하지 않습니다.

소비자 통합 후보의 `ProjectWindow`는 geometry를 하나 선택하면 이 편집기를
표시합니다. CAD 선택과 편집 문서 등록은 기존 Workbench 서비스에 전달됩니다.
문서만 등록해도 기본 열기를 요청할 수 있고, 누락 문서는 같은 역할로 다시
지정합니다. 새 SolidCraft/CADCraft의 기본 버튼은 편집 문서를 등록하며 기존
FreeCAD input-only 지정·열기 경로를 유지합니다. CADCraft의 교환 출력은
DXF/SVG로 안내하며 STEP 솔리드 출력으로 표시하지 않습니다.

소비자가 프로젝트·선택 노드·프로그램의 실제 변경마다 증가하는 GUI 문맥 epoch를
전달하고, 같은 문맥의 상태 갱신에는 값을 유지합니다. `clear_context()`와 상태
주입은 변경 요청을 만들지 않습니다. 파일 선택 중 문맥이 바뀌면 결과를 버립니다.
두 문자열 signal은 같은 GUI 스레드에서 즉시 소비하며, 소비자가 현재 프로젝트
객체·노드·변경 전 프로그램을 적용 직전 다시 확인합니다. 이 값을 프로젝트
파일에 저장하거나 일반 개정 체계로 사용하지 않습니다.
누락 문서의 기본 버튼으로 여는 파일 선택도 프로젝트·노드 객체·프로그램과
epoch를 전후 대조하고 같은 문서 등록 처리로 합류합니다. 문맥이 바뀌었거나
선택을 취소하면 파일 참조를 바꾸거나 프로그램을 실행하지 않습니다.

모듈은 파일 내용·프로젝트를 쓰거나 해시·프로그램 탐색·실행·설정 저장을 수행하지
않습니다. 파일 상태는 기존 캐시의 주입값을 사용합니다. 실제 변경은 기존
`Workbench.set_node_program(project, ...)` 및 `set_node_file(project, ..., role="document")`
경로를 사용해 input 연결과 원본 bytes를 보존합니다. browse 취소는 요청 0이며,
backend 예외는 변경 후 발생할 수도 있습니다. 소비자는 실제 모델을 다시 표시하고
남은/불확실한 변경의 dirty와 기존 dirty를 유지하며 자동 rollback을 약속하지 않습니다.
오류 대화상자를 열기 전에 미저장 상태와 현재 모델 표시를 갱신하므로, 남은
변경을 저장하지 않은 채 닫아도 되는 상태로 표시하지 않습니다. 부모 상자에도
소유 문서명과 변경·누락 상태를 표시합니다.
등록 뒤 원본을 반복해서 읽지 못해도 새 참조와 미저장 상태를 유지하고 오류를
알립니다. 파일 상태는 준비됨으로 추정하지 않고 확인 불가로 표시합니다. 실패한
읽기는 캐시하지 않으므로 접근이 복구되면 파일 상태 새로 고침으로 다시 확인합니다.

모듈·실제 소비자·이 가이드의 동일 통합 head에서 저장/새 창 재열기·문서 우선 열기를
검토하기 전에는 이 GUI 기능을 인도 완료로 세지 않습니다. 모듈의 합성/offscreen
검사는 실제 CAD·Windows 화면 검수와 별개이며, 기존 frozen r2 설치본에는 이 새
모듈이 포함돼 있지 않습니다.

1. geometry 노드의 프로그램을 FreeCAD 또는 SolidCraft로 지정하고 원본 파일을
   document로 등록합니다. 기본 열기는 이 소유 문서를 우선합니다.
2. 외부 CAD에서 STEP를 명시적으로 내보낸 뒤 같은 노드의 output으로 등록하고
   길이 단위를 선언합니다. CAD 저장만으로 새 STEP를 만들었다고 간주하지 않습니다.
3. CAD output을 PrePoMax 노드 input으로 연결합니다. PrePoMax의 PMX 작업 문서는
   document로 별도 등록합니다. 입력 열기를 명시하면 STEP를 가져오고, 기본 열기는
   PMX를 엽니다. 기존 PrePoMax 호출은 -f 파일 형식입니다.
4. PrePoMax에서 계산용 INP를 명시적으로 내보내 output으로 등록합니다. 그 출력을
   CalculiX 노드 input으로 연결합니다. 프로그램 열기는 해석 제출이 아닙니다.
5. 프로젝트를 저장하고 다시 열어 원본·PMX·STEP·INP의 역할과 단위를 확인합니다.

CADCraft는 2D 원본과 DXF/SVG 교환 경로입니다. 직접 STEP 솔리드 출력으로
취급하지 않습니다. 2D에서 3D 스케치를 만드는 별도 도구의 import, solid 작성,
단위·누락 경고 확인은 추가 작업이며 자동 변환을 제공한다고 표시하지 않습니다.

## 현재 확인 범위와 사용 전제

연결 검사는 한글·공백 경로의 argv/cwd/env, 원본 우선 열기, 명시 input 열기,
프로젝트 저장·재열기의 파일 역할 보존을 모의 launcher로 확인합니다. 실제
CAD export 내용, PrePoMax의 Windows import와 설치된 binary의 동작은 별도 검증
대상입니다. 기존 r2의 geometry GUI 프로그램 선택·document 등록은 미구현이며,
새 모듈의 source/offscreen 검사와 실제 소비자 통합 판정은 구분합니다.
API·모듈 검사만으로 전체 CAD→해석 흐름을 인도 완료로
처리하지 않습니다. 프로그램이나 라이선스 자산을 자동 다운로드하지 않습니다.

조사 소스는 [SolidCraft 72df682](https://github.com/tangency-labs/solidcraft/tree/72df682af7b6867323d6976ac59b4d7799bb0e01)과
[CADCraft 2099288](https://github.com/storytold/cadcraft/tree/2099288f5449b1602d2e2eedca92fe99fb19b973)입니다.
SolidCraft의 STEP export와 CADCraft의 2D writer를 구분하며, 조사 HEAD의 기능을
설치한 stable binary의 검증으로 대체하지 않습니다. 두 소스의 MIT OR Apache-2.0
선택 라이선스와 각 NOTICE의 외부 의존·자산 조건은 binary 재배포와 별도로 검토합니다.
