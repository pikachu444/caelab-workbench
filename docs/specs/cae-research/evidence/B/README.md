# 재구성한 B 참조 계산 묶음

이 묶음은 **제품 기능의 완료 증거가 아니라, 정의한 재료식과 API를 검증하기 위한 참조 코드·자료**다. 아래 과거 실행·미실시 설명은 복구 문서를 작성한 당시의 기록이다. 이후 재실행한 범위와 결과는 [최신 실행 안내](../../README.md#실제로-실행해-확인한-내용)를 따른다. 복구 당시에는 일부 계산을 과거 환경에서 실제 실행한 상태였고, 재구성 스크립트는 새 환경의 재실행과 독립 검토가 필요했다. Windows 제품 화면·네이티브 보정·실물 재료 적합성을 검증한 묶음은 아니다.

B-RECOVERY-1은 실행 환경을 사용할 수 없게 된 뒤 새로 재구성했다. 이전 B 묶음과 바이트가 같은 복원본이 아니다. 과거 관측 JSON에는 이미 확인할 수 있던 수치 결과를 옮겨 적었다. 원래 실행 기록(receipt), CSV 바이트, 소스와 바이너리의 대응 근거, 새 성공 실행을 만들어 넣지 않았다. 소스 자산의 해시는 새로 검토하는 이 바이트를 식별한다.

## 실행 환경과 명령

Python 3.12의 별도 선택 가상환경을 명시해 사용한다. 과거 검사에서 사용한 수치 패키지는 NumPy 2.3.5, SciPy 1.17.0, pyvisco 2.1.3, filelock 4.2.10이다. pyvisco가 선언한 의존성은 pip로 설치한다. 이 디렉터리에는 패키지나 실행 환경을 동봉하지 않는다.

~~~powershell
py -3.12 -m venv C:\CAE\b-reference-env
C:\CAE\b-reference-env\Scripts\python.exe -m pip install numpy==2.3.5 scipy==1.17.0 pyvisco==2.1.3 filelock==4.2.10
$Py = 'C:\CAE\b-reference-env\Scripts\python.exe'
$B = 'C:\CAE\workbench\docs\specs\cae-research\evidence\B'
$WB = 'C:\CAE\workbench'
& $Py "$B\reference_probe.py"
& $Py "$B\equilibrium_probe.py"
& $Py "$B\run_pyvisco.py"
& $Py "$B\optimizer_probe.py" --workbench-root $WB
& $Py "$B\constraint_probe.py" --workbench-root $WB
& $Py "$B\check_assets.py"
& $Py "$B\run_neml.py"
~~~

Linux에서는 해당 가상환경의 bin/python과 일반 POSIX 경로를 사용한다. 위 경로는 모두 사용자가 고르는 예시다. 스크립트는 현재 작업 디렉터리나 추정한 부모/workbench 위치가 아니라 __file__에서 자료 묶음 위치를 찾는다.

명시한 core 루트는 workbench_core_sources.json의 주요 소스 SHA256 일곱 개와 일치해야 한다. 기준 개정은 104cdd6888a5554eecc8cf965c60ff1bd980b6da이며, 문서만 바뀐 HEAD는 달라도 된다. 보조 코드는 import 전에 바이트를 검사하고 import 뒤 실제 로드 경로·해시를 다시 확인한다. 일곱 core 파일 검사로 모든 선택 의존성을 인증할 수는 없다. 실제 로드한 pyvisco 계산 파일은 source_manifest.json과 별도로 대조한다.

## 각 검사에서 확인하는 것

reference_probe는 새 합성 관측값과 실행 기록을 만든다. 갱신한 COMP 검증 자료는 전용 equilibrium 검사와 같은 유한 영역 유일근 알고리즘을 사용한다. 측정한 실험실 자료를 재현하는 것이 아니다. expected.json은 고정된 독립 수치 기준값이며 현재 평가기로 다시 생성하지 않는다.

check_assets는 새로 생성한 probe_receipt.json을 요구한다. 소스 해시, 수치값 열아홉 개, 선택한 CSV 행의 자료 범위를 검사한다. 실패를 숨기려고 해시 manifest를 다시 만드는 것은 인수 검증이 아니다.

선택 NEML 소스 빌드는 과거 BLAS_LIBRARIES 누락으로 막혔다. run_neml의 exit20은 BACKEND_UNAVAILABLE이며 합격이나 skip이 아니다. 실제 라이브러리가 설치돼 있으면 텐서/스칼라 수치 비교를 실행할 수 있다. 수치가 통과해도 반환값은 21/source_correspondence=unverified다. 환경변수로 고정 소스의 빌드를 입증할 수는 없다. 구현 명세가 요구하는 실제 빌드 기록과 로드 확장 모듈의 해시 검증을 별도로 수행해야 한다. 여기서는 Windows NEML 설치, 제품 GUI, 네이티브 보정, 실물 재료 적합성을 입증하지 않는다.

## 원본 위치에 의존하지 않는 재현 검사

~~~powershell
& $Py "$B\cold_reproduce.py" --workbench-root $WB --destination C:\CAE\B-cold-check
~~~

대상 디렉터리는 새로 만들거나 비워 두어야 하며 원본 디렉터리들 밖에 있어야 한다. 이 명령은 묶음과 core를 복사하고, Python 감사 훅으로 자식 프로세스가 두 원본을 읽지 못하게 한다. 복사 위치에서 참조 계산을 실행하고 실제 로드한 core 경로가 복사본만 가리키는지 확인한다. NEML의 exit20/21은 백엔드 또는 소스 확인 조건의 미충족으로 기록한다.

환경 소실 전에는 이 절차의 이전 버전으로 참조/API/무결성 명령 여섯 개를 실제 통과했으며 NEML은20이었다. 이 복구 기록 자체를 이번 재구성 버전의 재실행 증거로 사용하지 않는다. 이후 실제 재실행 여부는 위 최신 실행 안내에서 확인한다.

blob 복구 중에는 브랜치·PR·main·제품 실행 코드를 변경하지 않았다. 최종 새 blob 개정은 E/F가 직접 읽어야 한다. 원래 원시 산출물 바이트가 없다는 점과 새 실행 환경이 필요하다는 점은 여전히 한계로 남는다.
