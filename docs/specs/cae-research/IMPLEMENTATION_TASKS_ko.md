# 재료 연구·전문가 자문·후처리 개발 작업표

이 표는 앞으로 구현할 작업을 설명합니다. 기존 기능과 계산 예제를 연결하는 방법은 정리했지만, 아래 작업 전체가 제품에서 구현된 상태는 아닙니다.

작업 번호는 개발자와 AI 작업자가 같은 항목을 찾는 이름입니다. 자동 처리용 [JSON 목록](implementation-map.json)과 같은 12개 작업을 설명합니다. JSON의 `reviewed`는 명세를 검토했다는 뜻이고, `planned`는 제품 기능의 구현이 예정돼 있다는 뜻입니다.

## 순서와 결과

| 작업 | 만들 기능 | 먼저 필요한 작업 | 결과 |
|---|---|---|---|
| `CORE01` | 공통 데이터와 단위 처리 | 없음 | 자료 형식·단위·성분·좌표계·출처를 검사하는 공통 코드입니다. |
| `CORE02` | 시험 비교와 재료 계수 동정 | `CORE01` | 시험자료 읽기, 계산 호출, 시험별 오차와 제약조건·결과를 저장하는 공통 기능입니다. |
| `STORE01` | 저장·다른 이름으로 저장·중단 복구 | `CORE02` | 저장, 읽기 전용 열기, 다른 이름으로 저장, 파일 재연결과 중단 복구 기능입니다. |
| `MAT-A` | DP·발포체·직교이방 재료 계산 연결 | `CORE02`, `POST01-04` | 모델별 계수와 상태를 받아 계산하고 필요한 곡선을 반환하는 코드입니다. |
| `MAT-B` | 초탄성·점탄성·반복 소성 등의 계산 연결 | `CORE02` | 모델별 계산과 라이브러리 호출, 결과 읽기, 최적화 제약조건 처리입니다. |
| `UI01` | 재료 연구 화면 | `CORE02`, `STORE01` | 자료·모델·계수·비교·실행·결과를 확인하는 Windows용 Qt 화면입니다. |
| `PREP01` | 동정한 계수를 열린 Prep 모델에 반영 | `CORE01`, `CORE02` | 변경 미리보기와 적용, 여러 재료 항목을 한 번에 바꾸고 되돌리는 기능입니다. |
| `LINK01` | 프로젝트와 독립 프로그램 연결 | `STORE01`, `UI01`, `PREP01` | 프로그램 등록, 프로젝트의 재료 연구 항목과 파일 관계, Prep 공용 위젯의 연결입니다. |
| `E0-2` | 질문·첨부자료·문헌 검색 | `CORE01` | 질문과 첨부자료 저장, 문헌 준비와 검색, 판본·출처 확인 기능입니다. |
| `E3-5` | 실제 AI 자문·읽기 도구·별도 검토 | `E0-2`, `POST01-04` | 실제 AI 연결, 읽기 도구 호출과 반환, 답변 검토·수정·재검토 기록입니다. |
| `POST01-04` | 솔버 결과 변환과 수치 읽기 | `CORE01` | 결과 변환 실행, 응력·변위의 공간 분포와 시간 이력 읽기, 원래 해석과의 연결 기록입니다. |
| `POST05-06` | RP에서 선택한 결과 열기 | `POST01-04` | 기존 RP의 수동 열기 안내와, 선택한 결과를 자동으로 여는 별도 수정판 연결입니다. |

서로 선행 관계가 없는 작업은 나눠 진행할 수 있습니다. 같은 파일은 편집자를 한 명으로 정하고 변경을 조정합니다. 선행 조건은 전체 작업을 완료하기 위한 조건입니다. 예를 들어 MAT-A의 독립 재료 계산부터 시작할 수 있어도, 실제 솔버 결과를 읽는 부분은 POST01-04와 연결해 확인해야 합니다.

## 작업별로 개발자에게 맡길 내용

### CORE01: 공통 데이터와 단위 처리

시험자료와 해석 결과를 여러 기능이 같은 뜻으로 읽도록 데이터 형식과 단위 변환을 정합니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `caelab/artifact_refs.py`, `caelab/materials/contracts.py`, `caelab/materials/units.py`
- **기대하는 결과:** 자료 형식·단위·성분·좌표계·출처를 검사하는 공통 코드입니다.
- **완료 확인:** 잘못된 형식, 모르는 단위, 누락된 값, 다른 파일을 가리키는 참조를 거부해야 합니다. 온도와 질량 단위도 명세대로 변환해야 합니다.
- **상세 기준:** [core-implementation.md](core-implementation.md)의 C1/C3/C6.

### CORE02: 시험 비교와 재료 계수 동정

계산곡선을 시험곡선과 비교하고 오차를 줄이는 계수를 기존 최적화 도구로 찾습니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `caelab/materials/registry.py`, `caelab/materials/experiments.py`, `caelab/materials/residuals.py`, `caelab/materials/evaluator.py`, `caelab/materials/service.py`
- **기대하는 결과:** 시험자료 읽기, 계산 호출, 시험별 오차와 제약조건·결과를 저장하는 공통 기능입니다.
- **완료 확인:** 계수 동정에 필수인 시험에서 계산이나 자료 변환이 실패하거나 필요한 결과가 누락되면 목적값을 만들지 않습니다. 정상 계산의 큰 오차와 별도 검증시험의 불합격은 계산 불능과 구분하고, 유효하게 계산한 동정 오차를 보존합니다. 실패한 시험을 빼고 평균을 낮추거나, 계수 동정에 쓰지 않을 시험자료를 동정에 사용해서는 안 됩니다.
- **상세 기준:** [core-implementation.md](core-implementation.md)의 C2/C4/C5/C7.

### STORE01: 저장·다른 이름으로 저장·중단 복구

자료와 계산 결과의 연결을 보존하고 파일 이동이나 작업 중단 뒤에도 복구합니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `caelab/materials/service.py`, `caelab/project.py`, `caelab/native/run_store.py`
- **기대하는 결과:** 저장, 읽기 전용 열기, 다른 이름으로 저장, 파일 재연결과 중단 복구 기능입니다.
- **완료 확인:** 읽기 전용 원본을 바꾸지 않고 복사해야 합니다. 다른 작업의 변경을 덮어쓰지 않아야 하며, 복사 중 실패해도 유효한 원본이나 완료된 복사본을 지워서는 안 됩니다.
- **상세 기준:** [core-implementation.md](core-implementation.md)의 C8.

### MAT-A: DP·발포체·직교이방 재료 계산 연결

선정한 DP, LAW33, 직교이방 탄성과 파생 탄소성 모델을 공통 동정 기능에 연결합니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `caelab/materials/adapters/dp_axisym.py`, `caelab/materials/adapters/geos_dp.py`, `caelab/materials/adapters/foam33.py`, `caelab/materials/adapters/ortho_plane_stress.py`, `caelab/materials/adapters/xbp_power.py`
- **기대하는 결과:** 모델별 계수와 상태를 받아 계산하고 필요한 곡선을 반환하는 코드입니다.
- **완료 확인:** 각 모델의 식·부호·경화·소성 흐름·하중 이력·필요 출력이 명세와 같아야 합니다. 독립 계산이나 실제 솔버 결과와 대조하고, 지원하지 않는 조건을 알려야 합니다.
- **상세 기준:** [material-pressure-orthotropic.md](material-pressure-orthotropic.md)의 각 모델의 계산식·실제 실행·시험자료 비교와 완료 조건.

### MAT-B: 초탄성·점탄성·반복 소성 등의 계산 연결

Ogden, Prony, Chaboche, Johnson–Cook을 각 계산 범위에 맞게 연결합니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `caelab/materials/adapters/ogden.py`, `caelab/materials/adapters/pyvisco.py`, `caelab/materials/adapters/neml_chaboche.py`, `caelab/materials/adapters/jc_state.py`
- **기대하는 결과:** 모델별 계산과 라이브러리 호출, 결과 읽기, 최적화 제약조건 처리입니다.
- **완료 확인:** 비압축성·압축성 Ogden을 구분하고 온도·시간·전단 성분·재료 이력을 정확히 처리해야 합니다. 필요한 라이브러리를 실행하지 못한 항목을 성공으로 표시해서는 안 됩니다.
- **상세 기준:** [material-finite-history.md](material-finite-history.md)의 다섯 계산 범위의 라이브러리 호출·검증 계산·제약조건.

### UI01: 재료 연구 화면

사용자가 시험자료를 읽고 계수를 정한 뒤 계산·동정·비교·저장을 수행하는 독립 창을 만듭니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `apps/workbench_qt/material_study.py`
- **기대하는 결과:** 자료·모델·계수·비교·실행·결과를 확인하는 Windows용 Qt 화면입니다.
- **완료 확인:** 실제 CSV를 읽어 계산하고 중지·취소, 시험별 곡선과 잔차, 검증 결과, 저장 후 다시 열기까지 확인해야 합니다. 실제 Windows 화면의 한글 입력과 화면 배율도 검사해야 합니다.
- **상세 기준:** [core-implementation.md](core-implementation.md)의 C9.

### PREP01: 동정한 계수를 열린 Prep 모델에 반영

사용자가 확인한 계수와 곡선을 현재 열려 있는 Prep 문서에 반영합니다.

- **작업할 저장소:** pikachu444/caelab-prep
- **수정할 파일 또는 기존 코드:** `src/caelab_prep/application/material_binding.py`, 기존 Prep의 실행 중인 문서 명령 처리와 입력파일 형식 처리 코드
- **기대하는 결과:** 변경 미리보기와 적용, 여러 재료 항목을 한 번에 바꾸고 되돌리는 기능입니다.
- **완료 확인:** 다른 문서나 오래된 문서 상태에는 적용하지 않아야 합니다. 여러 카드와 곡선을 한 번의 명령으로 바꾸고 한 번의 Undo로 되돌려야 합니다. 적용 응답을 잃었을 때 무작정 다시 적용해서는 안 됩니다.
- **상세 기준:** [core-implementation.md](core-implementation.md)의 C10과 [Prep 구현 인계 문서](https://github.com/pikachu444/caelab-prep/blob/main/docs/material-research-implementation-handoff.md).

### LINK01: 프로젝트와 독립 프로그램 연결

Workbench 프로젝트에서 재료 연구 창을 열고 파일·결과·Prep 문서를 연결합니다.

- **작업할 저장소:** pikachu444/caelab-workbench + pikachu444/caelab-prep
- **수정할 파일 또는 기존 코드:** 기존 Workbench program_catalog 및 프로젝트 실행 코드, 기존 Prep 공용 위젯의 공개 연결 코드
- **기대하는 결과:** 프로그램 등록, 프로젝트의 재료 연구 항목과 파일 관계, Prep 공용 위젯의 연결입니다.
- **완료 확인:** 프로젝트를 저장하거나 다른 위치로 복사해도 같은 자료와 선택 결과를 열어야 합니다. 재료 연구 문서를 해석 입력파일로 오인해서는 안 됩니다.
- **상세 기준:** [core-implementation.md](core-implementation.md)의 C8/C9/C11.

### E0-2: 질문·첨부자료·문헌 검색

해석 결과 없이도 이론 질문을 시작하고, 질문에 맞는 원문과 첨부 결과를 찾습니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** 기존 assist의 질문·자료·대화 관리 코드와 ExpertWindow, 전문가 명세의 새 문헌 준비·색인·검색 판단 코드
- **기대하는 결과:** 질문과 첨부자료 저장, 문헌 준비와 검색, 판본·출처 확인 기능입니다.
- **완료 확인:** 관련 없는 문헌을 충분한 근거처럼 제공해서는 안 됩니다. 질문에 명시한 솔버와 버전을 유지하고, 자료가 없거나 오래된 경우를 알려야 합니다. 사용자가 쓰던 질문도 보존해야 합니다.
- **상세 기준:** [expert-implementation.md](expert-implementation.md)의 질문 문맥, 문헌 원문·판본·언어·관련성, 이전 자료 읽기.

### E3-5: 실제 AI 자문·읽기 도구·별도 검토

AI가 필요한 자료를 요청해 읽고 답변하며, 별도 검토에서 근거와 가정을 확인하게 합니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** 기존 assist의 AI 연결 및 대화 실행 관리 코드, 전문가 명세의 읽기 도구 및 답변 검토 코드
- **기대하는 결과:** 실제 AI 연결, 읽기 도구 호출과 반환, 답변 검토·수정·재검토 기록입니다.
- **완료 확인:** 실제 AI가 요청한 도구 호출과 결과 반환을 확인해야 합니다. 답변 전체를 검토하고 근거 부족·오류·취소를 남겨야 합니다. AI에게 모델 변경이나 솔버 실행 권한을 주지 않습니다.
- **상세 기준:** [expert-implementation.md](expert-implementation.md)의 실제 AI 호출과 반환, 답변 전체의 근거 검토, 재검토와 Windows 확인.

### POST01-04: 솔버 결과 변환과 수치 읽기

결과파일을 변환해 원래 시간·요소·층·좌표계·단위를 보존한 수치로 읽습니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `caelab/native/operations.py`, `caelab/native/converters.py`, `caelab/native/result_adapters.py`, `caelab/result_readers/vtk_fields.py`, `caelab/result_readers/opencourant_history.py`
- **기대하는 결과:** 결과 변환 실행, 응력·변위의 공간 분포와 시간 이력 읽기, 원래 해석과의 연결 기록입니다.
- **완료 확인:** 변환 프로그램이 종료됐다는 사실만으로 성공 처리하지 않아야 합니다. 실제 원 ID·시간·필수 출력의 개수와 값, 단위·층·축을 검사하고 다른 계산 기준과 대조해야 합니다.
- **상세 기준:** [post-implementation.md](post-implementation.md)의 원 결과의 보존, 공간 분포·시간 이력 변환과 수치 읽기.

### POST05-06: RP에서 선택한 결과 열기

Workbench에서 선택한 결과를 별도 OpenCourantRP에서 확인하도록 연결합니다.

- **작업할 저장소:** pikachu444/caelab-workbench
- **수정할 파일 또는 기존 코드:** `caelab/native/rp_consumer.py`, 선택적으로 사용할 별도 OpenCourantRP 수정판
- **기대하는 결과:** 기존 RP의 수동 열기 안내와, 선택한 결과를 자동으로 여는 별도 수정판 연결입니다.
- **완료 확인:** 원래 해석 결과를 덮어쓰거나 솔버를 다시 실행하지 않아야 합니다. 자동 연결은 선택한 자료가 실제 표시됐는지 응답으로 확인해야 합니다. 기존 RP를 수동으로 쓰는 경로도 제공합니다.
- **상세 기준:** [post-implementation.md](post-implementation.md)의 별도 후처리의 수동 열기와 자동 자료 전달·표시 확인.

## 전체 흐름의 마지막 확인

작은 계산 시험을 통과한 뒤에는 실제 사용 과정으로 연결해 확인해야 합니다. 사용자가 CSV 시험자료를 읽고, 모델과 조정할 계수를 정하고, 계산과 동정을 실행하고, 별도 시험곡선과 비교하고, 저장 후 다시 열어 같은 결과를 확인해야 합니다.

해석 입력파일에 계수를 내보내거나 열린 Prep 모델에 적용하는 기능은 지원하는 재료와 입력 항목에 대해서만 제공합니다. 전문가 자문에는 같은 자료와 계산 결과를 전달하고 RP에는 선택한 결과를 넘깁니다. 적용과 해석 실행은 사용자가 명시적으로 수행합니다.

실제 AI 연결, Windows 화면, 오류·중단·취소, 저장과 다시 열기를 확인하기 전에는 해당 작업을 완료로 표시하지 않습니다. 가상 자료나 작은 계산 예제의 성공을 실재료 검증이나 전체 제품 완성으로 바꾸지 않습니다.
