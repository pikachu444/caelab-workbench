# Radioss Starter 경고와 실행 기록

Starter가 오류 없이 경고를 보고한 경우에도 Engine 계산을 이어갈 수 있습니다.
Workbench는 다음 조건을 모두 확인한 Starter 경고 종료만 수용합니다.

- 프로세스 종료 코드가 0이고 종료 문구가 `TERMINATION WITH WARNING`입니다.
- listing의 마지막 집계에 `0 ERROR(S)`와 양수 `WARNING(S)`가 있습니다.
- 이번 실행 폴더에 비어 있지 않은 `<이름>_0000_0001.rst`가 생성됐습니다.
- fatal 또는 양수 오류를 나타내는 진단이 없습니다.

이는 경고를 지우거나 모델 검토를 대신하는 동작이 아닙니다. 경고 내용은
Starter listing에서 확인하고 필요한 모델 수정은 원본 전처리기에서 수행합니다.
restart 파일의 존재만으로 그 내용이나 모델의 물리적 타당성을 승인하지 않습니다.

## 실행·실패·재열기

각 run의 단계 기록에는 종료 종류, 오류·경고 수, listing 이름과 모델 검토
메시지가 남습니다. 이 기록은 Engine 시작 전에 저장됩니다. 따라서 이후
Engine 실행이나 결과 판독이 실패해도 Starter 경고를 확인할 수 있습니다.
프로젝트를 다시 열어도 같은 실행의 단계 진단과 결과 경고가 유지됩니다.

Engine 경고 종료, fatal 또는 양수 오류, 누락된 restart는 정상 종료로
처리하지 않습니다. Starter 검증 실패 시 Engine을 시작하지 않습니다.
취소·시간 초과는 기존 실행 관리 경로를 따르며 경고 종료로 바꾸지 않습니다.

실행 전 예상 listing/restart 이름이 이미 있으면 충돌로 거부합니다.
기존 파일을 삭제하거나 이전 실행의 파일을 이번 성공 근거로 사용하지 않습니다.

## 결과 판독과 입력 보존

단계 경고는 기존 결과 판독 경고에 합쳐집니다. 부분 판독(`partial`)은 그대로
남으며, Starter가 수용됐다는 이유로 전체 결과를 읽었다고 표시하지 않습니다.
이번 교정은 원덱의 물리 키워드를 생성하거나 바꾸는 기능이 아닙니다.

경고 종료 문구와 집계의 근거는 고정 OpenCourant의
[종료 처리](https://github.com/OpenCourant/OpenCourant/blob/cb760c206e346eff12161a2268b134ef9d48a4ef/starter/source/output/analyse/analyse_arret.F#L121)입니다.
