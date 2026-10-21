# 검토 보고 — MeshLane (simvia-tech/meshlane) 채택 여부

검토일 2026-10-21, 검토 채팅 003_PDE 라인. 채택 결정이 아닌 검토까지가 범위.
실측 환경: 임시 venv에 meshlane 5.5.0 + meshio 5.3.5를 나란히 설치해 같은 자산으로 대조.

## 1. 기본 사실

- 라이선스 MIT (meshio와 동일). meshio의 포크이며 전체 이력·저작권을 승계.
- 유지 상태: 저장소 생성 2026-06-18. 릴리스 5.4.1(6/22) → 5.5.0(2026-08-24)까지
  두 달에 5회. 마지막 push 2026-10-15, 마지막 커밋 2026-10-08 (ansysInp 수정).
  별 39, 포크 4, 열린 이슈 20. **활발하지만 생긴 지 4개월 된 단일 회사(Simvia) 프로젝트.**
- 설치: `pip install meshlane[all]` 가능 (PyPI 5.5.0, wheel 0.21MB).
  `[all]`이 h5py·netCDF4를 끌어오며 MED·XDMF·CGNS·H5M에 필요. 기본 의존은 numpy뿐.
- 파이썬: requires_python >=3.10. 우리 환경(3.12 venv, 3.14 fenicsx-env)과 충돌 없음.

## 2. 지원 형식 — 우리 형식 기준

| 형식 | meshlane | 비고 |
|---|---|---|
| MED 읽기·쓰기 | ○ (강화) | 아래 3절 실측 참조 |
| VTK / VTU / XDMF | ○ | meshio와 동일 계열 |
| Gmsh .msh (2.2/4.0/4.1) | △ | 쓰기는 되나 자기 판독기가 재읽기 실패 (실측) |
| CalculiX .inp | △ | Abaqus .inp로 읽기·쓰기 자체는 됨. 단 집합이 ELSET으로 나가지 않음 (실측) |
| Radioss (.rad/블록) | ✕ | 형식 자체가 없음. TWISBEAM_0001.rad 읽기 시도 → "형식을 알 수 없음" 오류 |

meshio 대비 늘어난 것 (README + 실측):
- **MED 그룹 복원**: meshio는 beam.med의 그룹을 읽는 단계에서 잃음. meshlane은 복원함.
- MED 다중 메시 파일, 다중 시간 단계 결과 필드(NDT/NOR/PDT 보존), 메타데이터·단위·
  성분 이름 왕복, MED 4.1 비트마스크 메타데이터, 다각형 셀.
- 신규 판독기: OpenFOAM polyMesh (읽기 전용), Ansys/APDL .inp·.cdb 읽기·쓰기.
  (주의: .inp 확장자를 Abaqus와 Ansys가 공유 — 형식 지정이 필요한 지점.)
- meshio 본체는 유지보수 중단 상태라고 meshlane README가 명시.

## 3. 실측 — beam.med (절점 190, TETRA 433, 그룹 FIXED·LOADF·SOLID)

meshlane으로 읽으면: 절점 190, 셀 tetra 433 + 표면 triangle 28,
**셀 집합 FIXED 14 · LOADF 14 · SOLID 433** 으로 그룹이 살아 있음.
meshio로 읽으면: 절점·셀 수는 같으나 **셀 집합이 비어 있음** (그룹 소실, cell_tags만 남음).

| 변환 (meshlane) | 절점·셀 수 | 그룹(집합) |
|---|---|---|
| MED → MED 왕복 | 190 / 433+28 유지 | **유지** (FIXED 14, LOADF 14, SOLID 433) |
| MED → VTU/VTK/XDMF | 유지 | 집합으로는 소실. 대신 합쳐진 태그 셀 데이터(`FIXED-LOADF-SOLID`)로만 남음 |
| MED → .inp (Abaqus) | 유지 | **완전 소실** — 파일에 ELSET/NSET이 0개 |
| MED → .msh (Gmsh) | 파일은 생성됨 | **자기 판독기로 재읽기 실패** (`gmsh:physical` 블록 불일치 ValueError), meshio 판독기도 실패. 단 Gmsh 본체(4.14.1)는 그 파일을 읽어 MED로 변환했고 SOLID 433은 살아 있었음 → 쓰기 결과는 Gmsh 기준 유효, meshlane 판독기 버그 |

기타 자산:
- 결과 VTU (감사 저장소의 frame 결과, 절점 4·tetra 1, 필드 U·q): meshlane 읽기 정상,
  MED로 변환·재읽기까지 필드 보존 확인.
- OpenCourant 결과 VTK(172점·123셀)는 파일로 남아 있지 않아(당시 pvpython 표준출력으로
  확인) 직접 시험은 못 했고, VTK 계열 경로는 위 beam 변환으로 갈음함.

## 4. 판정

**조건부로 쓸 만합니다 — MED 읽기와 MED→보기용 변환(VTU/XDMF)에 한해서.**

- 근거: 우리에게 필요한 핵심은 Code_Aster/Salome의 MED에서 **그룹을 잃지 않고**
  읽는 것인데, meshio는 읽는 순간 그룹이 사라져 대안이 못 됩니다. meshlane은
  그룹을 셀 집합으로 복원하고 MED 왕복에서도 유지한 유일한 쪽입니다.
  현재 워크벤치에는 파이썬 MED 판독기가 없어 이 경로는 비어 있습니다.
- 쓰지 말아야 할 경로: ① CalculiX .inp 쓰기 (집합 소실 — FreeCAD 작성기가 본선 유지),
  ② Gmsh .msh 쓰기 (자기 판독기 버그 — Gmsh 본체 사용 유지), ③ Radioss (지원 없음 —
  OpenCourant 자체 변환 유지).
- 위험: 프로젝트가 젊고(4개월) 단일 회사 의존이며 버전 변동이 빠릅니다.
  도입 시 버전을 고정(==5.5.0)하고 얇은 감싸개 모듈 뒤에 격리해, 솔버 입력
  덱 생성 같은 본선에는 얹지 않는 조건이 필요합니다.

## 5. 도입 시 병합 요청 (통합 채팅용 제안)

1. `pyproject.toml` 선택 의존성에 `meshlane[all]==5.5.0` 추가 (필수 의존 아님).
2. 신규 감싸개 모듈 1곳에서만 사용: MED 읽기(그룹→셀 집합 확인)와
   MED → VTU/XDMF 변환(ParaView 보기·결과 요약 근거용) 전용.
   소유·위치는 통합에서 정해 주세요 (PDE 소관 아님).
3. 집합이 필요한 변환(.inp, .msh 쓰기)은 기존 프로그램별 경로 유지 명시.
