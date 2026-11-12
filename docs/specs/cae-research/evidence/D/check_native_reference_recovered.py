"""New reconstructed reference validator for recovered D fixtures.
No raw result bytes or prior success receipt are recreated here.
Requires numpy and pyvista. Use --fixture-root with regenerated affine/layered data.
This code was not executed after environment loss; independent review is required.
"""
from pathlib import Path
import argparse,csv,json,math
import numpy as np
import pyvista as pv

NODES=set(range(11,19))|set(range(21,25))
RIGHT={12,13,16,17,22,23}
YX={13,14,17,18,23,24}
ZX={15,16,17,18}
def required_names(nip):
    return {"2DELEM_Stress_(upper)","2DELEM_Stress_(lower)"} | {
        f"2DELEM_Stress_(layer__{k})_" for k in range(1,(5 if nip else 1)+1)
    }

def validate_field(mesh,nip):
    assert "TIME" in mesh.field_data, "MISSING_TIME"
    assert np.asarray(mesh.field_data["TIME"]).shape==(1,), "INVALID_TIME_SHAPE"
    t=float(mesh.field_data["TIME"][0])
    assert math.isfinite(t), "INVALID_TIME"
    node=np.asarray(mesh.point_data["NODE_ID"])
    elem=np.asarray(mesh.cell_data["ELEMENT_ID"])
    part=np.asarray(mesh.cell_data["PART_ID"])
    assert node.shape==(12,) and len(set(node.tolist()))==12 and set(node.tolist())==NODES, "MISSING_ID"
    assert elem.shape==(2,) and len(set(elem.tolist()))==2 and set(elem.tolist())=={101,202}, "MISSING_ID"
    assert part.shape==(2,), "INVALID_PART_SHAPE"
    solid=elem.tolist().index(101)
    shell=elem.tolist().index(202)
    assert part[solid]==10 and part[shell]==20, "OBSERVABLE_MISMATCH_PART"
    assert int(mesh.celltypes[solid])==12 and int(mesh.celltypes[shell])==9, "OBSERVABLE_MISMATCH_FAMILY"
    required=required_names(nip)
    assert required.issubset(set(mesh.cell_data)), "MISSING_REQUIRED_SHELL_ARRAY"
    assert "3DELEM_Stress" in mesh.cell_data, "MISSING_REQUIRED_SOLID_ARRAY"
    expected_solid=np.diag([120*t,40*t,40*t]).reshape(9)
    expected_shell=np.diag([1000/(1-.25**2)*.1*t,1000*.25/(1-.25**2)*.1*t,0]).reshape(9)
    arrays={}
    for key,index,expected in [("3DELEM_Stress",solid,expected_solid)] + [
        (key,shell,expected_shell) for key in sorted(required)
    ]:
        raw=np.asarray(mesh.cell_data[key])
        assert raw.shape==(2,9), "INVALID_COMPONENT_COUNT"
        actual=raw[index]
        assert np.isfinite(actual).all(), "NONFINITE_REQUIRED_VALUE"
        assert np.all(np.abs(actual-expected)<=1e-5+.01*np.abs(expected)), "STRESS_ORACLE:"+key
        arrays[key]=actual.tolist()
    u=np.asarray(mesh.point_data["Displacement"])
    v=np.asarray(mesh.point_data["Velocity"])
    assert u.shape==(12,3) and v.shape==(12,3), "INVALID_COMPONENT_COUNT"
    for row,nid in enumerate(node):
        right=nid in RIGHT
        target=np.array([t if right else 0.,0.,0.])
        assert np.all(np.abs(u[row]-target)<=1e-8+.001*np.abs(target)), "NODE_U_ORACLE"
        assert np.allclose(v[row],[1 if right else 0,0,0],rtol=.001,atol=1e-8), "NODE_V_ORACLE"
        original=[10 if right else 0,10 if nid in YX else 0,20 if nid>=21 else 10 if nid in ZX else 0]
        assert np.allclose(np.asarray(mesh.points[row],float)-u[row],original,rtol=0,atol=1e-4), "NODE_ID_GEOMETRY_MISMATCH"
    return {"actual_TIME":t,"values":arrays}

def validate_history(path):
    with path.open(newline="") as stream:
        rows=list(csv.reader(stream))
    assert len(rows)>=2, "COVERAGE_INCOMPLETE"
    normalized=[" ".join(h.split()) for h in rows[0]]
    assert normalized[0]=="time" and len(normalized)==len(set(normalized)), "INVALID_HEADER"
    assert all(len(r)==len(normalized) for r in rows[1:]), "COVERAGE_INCOMPLETE"
    values=np.asarray(rows[1:],float)
    assert np.isfinite(values).all(), "NONFINITE_REQUIRED_VALUE"
    time=values[:,0]
    assert np.all(np.diff(time)>0), "TIME_NOT_STRICTLY_INCREASING"
    def col(key):
        assert key in normalized, "MISSING_REQUIRED_COLUMN:"+key
        return values[:,normalized.index(key)]
    sx=col("BRIC 101 hex SX")
    rho=col("BRIC 101 hex DENS")
    vol=col("BRIC 101 hex VOL")
    assert np.all(np.abs(sx-120*time)<=1e-5+.01*np.abs(120*time)), "TH_STRESS_ORACLE"
    assert np.all(rho>0), "INVALID_DENSITY"
    assert np.allclose(7.8e-9/rho,vol/1000,rtol=3e-6,atol=1e-8), "TH_RHO_VOL_MISMATCH"
    assert np.allclose(7.8e-9/rho,1+.1*time,rtol=3e-6,atol=1e-8), "TH_RHO_ORACLE"
    for nid in range(11,19):
        expected=time if nid in RIGHT else np.zeros_like(time)
        assert np.allclose(col(f"NODE {nid} n{nid} DX"),expected,rtol=1e-5,atol=1e-8), "TH_NODE_U_ORACLE"
    return {"actual_TIME":time.tolist(),"column_count":len(normalized),"row_count":len(values)}

def negative_checks(original):
    reports=[]
    def rejected(name,mutate):
        mesh=original.copy(deep=True)
        mutate(mesh)
        try:
            validate_field(mesh,5)
        except (AssertionError,KeyError,ValueError) as exc:
            reports.append({"case":name,"status":"rejected","reason":str(exc)})
        else:
            raise AssertionError("MUTATION_ACCEPTED:"+name)
    rejected("delete_layer3",lambda m:m.cell_data.remove("2DELEM_Stress_(layer__3)_"))
    rejected("delete_all_shell_stress",lambda m:[
        m.cell_data.remove(k) for k in list(m.cell_data) if k.startswith("2DELEM_Stress_")
    ])
    def rename(m):
        m.cell_data["2DELEM_Stress_(layer__30)_"]=m.cell_data["2DELEM_Stress_(layer__3)_"].copy()
        m.cell_data.remove("2DELEM_Stress_(layer__3)_")
    rejected("rename_layer3_to30",rename)
    def swap_element_id(m):
        ids=np.asarray(m.cell_data["ELEMENT_ID"]).copy()
        m.cell_data["ELEMENT_ID"]=ids[::-1]
    rejected("swap_element_ID_only",swap_element_id)
    def swap_node_id(m):
        ids=np.asarray(m.point_data["NODE_ID"]).copy()
        left=int(np.flatnonzero(ids==11)[0])
        right=int(np.flatnonzero(ids==12)[0])
        ids[left],ids[right]=ids[right],ids[left]
        m.point_data["NODE_ID"]=ids
    rejected("swap_node_ID_only",swap_node_id)
    rejected("invalid_layer_tensor_shape",lambda m:m.cell_data.__setitem__(
        "2DELEM_Stress_(layer__3)_",np.zeros((m.n_cells,6))
    ))
    rejected("missing_TIME",lambda m:m.field_data.remove("TIME"))
    # Correct point permutation keeps all arrays, points and connectivity consistent.
    mesh=original.copy(deep=True)
    order=np.array([1,0,*range(2,12)])
    points=mesh.points.copy()
    data={k:np.asarray(v).copy() for k,v in mesh.point_data.items()}
    mesh.points=points[order]
    for key,values in data.items():
        mesh.point_data[key]=values[order]
    inverse=np.argsort(order)
    cells=mesh.cells.copy()
    position=0
    while position<len(cells):
        count=int(cells[position])
        cells[position+1:position+1+count]=inverse[cells[position+1:position+1+count]]
        position+=count+1
    mesh.cells=cells
    validate_field(mesh,5)
    reports.append({"case":"consistent_point_permutation","status":"accepted"})
    # Independent distinct layer values make a wrong-layer replacement observable.
    # This is an array/selection oracle, not native bending physics or a product adapter.
    layer_values={
        "2DELEM_Stress_(upper)":np.diag([10.,0.,0.]).reshape(9),
        "2DELEM_Stress_(layer__3)_":np.diag([5.,0.,0.]).reshape(9),
        "2DELEM_Stress_(lower)":np.diag([-4.,0.,0.]).reshape(9)
    }
    def exact_middle(values):
        assert "2DELEM_Stress_(layer__3)_" in values, "MISSING_REQUIRED_LAYER"
        assert values["2DELEM_Stress_(layer__3)_"][0]==5., "WRONG_LAYER_ORACLE"
    exact_middle(layer_values)
    layer_values["2DELEM_Stress_(layer__3)_"]=layer_values["2DELEM_Stress_(upper)"].copy()
    try:
        exact_middle(layer_values)
    except AssertionError as exc:
        reports.append({"case":"upper_substituted_for_distinct_middle","status":"rejected","reason":str(exc)})
    else:
        raise AssertionError("MUTATION_ACCEPTED:WRONG_LAYER")
    return reports

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture-root",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    report={"scope":"new reconstructed checker executed on supplied data, not recovered historical raw files","profiles":{}}
    for name,nip in [("affine",0),("layered",5)]:
        folder=args.fixture_root/name
        paths=sorted(folder.glob(f"{name}A*.vtk"))
        assert len(paths)==5, "COVERAGE_INCOMPLETE_FRAMES"
        frames=[validate_field(pv.read(p),nip) for p in paths]
        times=[r["actual_TIME"] for r in frames]
        assert all(a<b for a,b in zip(times,times[1:])) and times[-1]>=.005, "TIME_COVERAGE"
        report["profiles"][name]={"fields":frames,"history":validate_history(folder/f"{name}T01.csv")}
        if nip==5:
            report["negative_checks"]=negative_checks(pv.read(paths[-1]))
    # Preserve source data; report never targets an input artifact.
    if args.report.resolve().is_relative_to(args.fixture_root.resolve()):
        raise ValueError("Write report outside the fixture source root.")
    with args.report.open("x",encoding="utf-8") as stream:
        json.dump(report,stream,indent=2,allow_nan=False)
        stream.write("\n")

if __name__=="__main__":
    main()
