"""Result summaries — the evidence base for discussion.

The discussion program (the workbench's second half) reasons over
what actually ran: solver, success, headline numbers, verification
state, and the tail of the solver log. This module turns a ResultSet
into that compact, solver-neutral summary. It never invents values:
numbers come from the parsed solver output only.
"""

from __future__ import annotations

import json
import math

from .solvers.base import ResultSet


def summarize_result(result: ResultSet, solver: str | None = None) -> dict:
    """Preserve result provenance; the legacy solver argument cannot override it."""
    units = dict(getattr(result, "units", {}))
    displacement = result.max_displacement
    length = units.get("length")
    metres = (displacement if length == "m" else displacement / 1000.0
              if length == "mm" else None) if displacement is not None else None
    return {
        "solver": result.solver_id,
        "solver_id": result.solver_id,
        "success": result.success,
        "max_displacement": displacement,
        "units": units,
        "max_displacement_m": metres,
        "max_displacement_mm": metres * 1000.0 if metres is not None else None,
        "reaction_totals": dict(result.reaction_totals),
        "verification": result.verification,
        "log_tail": result.log_tail[-800:],
    }


def format_summary_ko(summary: dict) -> str:
    """One short paragraph a person (or an expert prompt) can read."""
    if not summary["success"]:
        return (
            f"{summary['solver']} 계산이 실패했습니다. "
            f"검증 상태: {summary['verification']}."
        )
    units = summary.get("units", {})
    displacement = summary["max_displacement"]
    length = units.get("length", "단위 미확인 — 사용자 선언 필요")
    displacement_text = (f"{displacement:.6g} {length}"
                         if displacement is not None else "미확인")
    fy = summary["reaction_totals"].get("fy")
    force = units.get("force", "단위 미확인 — 사용자 선언 필요")
    fy_text = f", 반력 합 Fy {fy:,.1f} {force}" if fy is not None else ""
    return (
        f"{summary['solver']} 계산 완료. "
        f"최대 변위 {displacement_text}{fy_text}. "
        f"검증 상태: {summary['verification']}."
    )


def finite_summary(value):
    """JSON summaries use null for nonfinite numbers; raw source bytes stay intact."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): finite_summary(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_summary(item) for item in value]
    return value


def format_native_evidence(evidence: dict, result: dict) -> str:
    """Compact numerical provenance, separated from native input and solver logs."""
    encode = lambda value: json.dumps(finite_summary(value), ensure_ascii=False, allow_nan=False)
    lines = [f"근거 {evidence['id']} / solver={evidence['solver_id']}",
             ("사용자 선택 자료, 동일 실행 대응 미확인" if evidence['source_kind'] == 'manual' else
              f"선택 실행 run={evidence['run_id']} / node={evidence['node_id']}"),
             f"입력 SHA256={evidence['input_revision'] or '미확인'}",
             f"process_state={evidence['process_state']} / parse_state={evidence['parse_state']}",
             f"단위 선언={encode(evidence['units']) or '{}'} / 단위 출처={evidence['unit_source'] or '미확인'}",
             "단위를 추정하지 않습니다. 솔버 종료·판독·유한성·공학적 적합성은 별도이며 공학적 검증은 미실시입니다."]
    frame = next((frame for frame in result.get('frames', []) if frame['index'] == evidence['frame_index']), None)
    if frame is not None:
        lines += ["FRD 선택 프레임=" + encode({key: frame.get(key) for key in
                  ('index', 'step', 'increment', 'value', 'analysis_type', 'kode', 'pvd_timestep')}),
                  "FRD value는 원래 표기이며 시간(s)으로 추정하지 않습니다. 선택 요약과 ParaView 전체 시계열 열기는 다릅니다.",
                  "유한값 범위는 해당 프레임의 실제 저장 성분·커버 절점만 사용합니다. 누락을 0으로 채우지 않으며 응력의 적분점/외삽/평균 여부를 추정하지 않습니다."]
        for field in frame.get('fields', []):
            coverage = field.get('coverage', {})
            compact = {key: coverage.get(key) for key in ('declared_count', 'record_count', 'mesh_node_count')}
            compact.update({key + '_count': len(coverage.get(key, [])) for key in
                            ('missing_node_ids', 'unknown_node_ids', 'nonfinite_node_ids')})
            lines.append(f"필드 {field['name']} / 성분 메타=" + encode(field.get('components', []))
                         + " / 데이터 메타=" + encode({key: field.get(key) for key in ('dataset_counter', 'result_type')})
                         + " / coverage=" + encode(compact) + " / 유한값 범위=" + encode(field.get('summary', {})))
        lines.append("FRD 프레임 요약=" + encode(frame.get('summary', {})))
    else:
        lines.append("FRD 선택 프레임이 없습니다. 없는 값은 미확인입니다.")
    dat = result.get('dat', {})
    if dat and dat.get('status') != 'missing':
        lines += ["DAT는 FRD와 합치지 않은 별도 파일의 마지막 판독 프레임입니다. 시간값 일치로 같은 실행 상태를 단정하지 않습니다.",
                  "DAT 프레임=" + encode(dat.get('frame')) + " / 상태=" + str(dat.get('status'))]
        lines.append("DAT 변위 요약=" + encode({
            'max_displacement': dat.get('max_displacement'),
            'displacement_node_count': dat.get('displacement_node_count'),
            'mesh_node_count': dat.get('mesh_node_count'),
            'units': evidence['units'], 'unit_source': evidence['unit_source']}))
        lines.append("DAT 수치는 판독된 집합·절점만 포함합니다. 전체 메시 절점 수·누락 범위는 미확인이며, 누락을 0으로 채우지 않습니다.")
        for name, values in dat.get('sets', {}).items():
            totals = {key: values[key] for key in ('total_force', 'total_displacement') if key in values}
            counts = {key: len(values[key]) for key in ('displacements', 'nodal_forces') if key in values}
            ranges = {}
            for kind in ('displacements', 'nodal_forces'):
                if kind not in values:
                    continue
                ranges[kind] = {}
                for index, component in enumerate(('x', 'y', 'z')):
                    finite = [row[index] for row in values[kind].values() if len(row) > index
                              and isinstance(row[index], (int, float)) and math.isfinite(row[index])]
                    ranges[kind][component] = {'minimum': min(finite, default=None),
                                              'maximum': max(finite, default=None), 'finite_count': len(finite)}
            lines.append(f"DAT 집합 {name}: 개수={encode(counts)}, 유한 성분 범위={encode(ranges)}, 원 합계={encode(totals)}")
    lines.append("FORC·DAT 힘과 힘합은 원 출력이며 반력으로 바꾸지 않습니다.")
    for warning in dict.fromkeys([*evidence.get('warnings', []), *result.get('warnings', []), *dat.get('warnings', [])]):
        lines.append("경고: " + str(warning))
    return '\n'.join(lines)
