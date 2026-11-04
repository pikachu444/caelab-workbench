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

## 3D CAD에서 계산 입력까지

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
CAD export 내용, PrePoMax의 Windows import, GUI 파일 선택 경로와 설치된 binary의
동작은 별도 검증 대상입니다. 이 연결만으로 전체 CAD→해석 흐름을 인도 완료로
처리하지 않습니다. 프로그램이나 라이선스 자산을 자동 다운로드하지 않습니다.

조사 소스는 [SolidCraft 72df682](https://github.com/tangency-labs/solidcraft/tree/72df682af7b6867323d6976ac59b4d7799bb0e01)과
[CADCraft 2099288](https://github.com/storytold/cadcraft/tree/2099288f5449b1602d2e2eedca92fe99fb19b973)입니다.
SolidCraft의 STEP export와 CADCraft의 2D writer를 구분하며, 조사 HEAD의 기능을
설치한 stable binary의 검증으로 대체하지 않습니다. 두 소스의 MIT OR Apache-2.0
선택 라이선스와 각 NOTICE의 외부 의존·자산 조건은 binary 재배포와 별도로 검토합니다.
