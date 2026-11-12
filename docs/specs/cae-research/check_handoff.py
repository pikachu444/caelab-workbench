"""Validate delivered recovery assets and current core source bytes, not product functionality."""
from pathlib import Path
import ast, hashlib, json, sys
root=Path(__file__).resolve().parent
inventory=json.loads((root/"recovery-assets.json").read_text(encoding="utf-8"))
for entry in inventory["files"]:
    path=Path(entry["path"])
    if path.is_absolute() or ".." in path.parts: raise ValueError("bad inventory path")
    full=Path.cwd()/path
    data=full.read_bytes()
    gitsha=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
    if gitsha!=entry["blob_sha"]: raise ValueError("GIT_BLOB_MISMATCH:"+str(path))
    if entry.get("sha256") and hashlib.sha256(data).hexdigest()!=entry["sha256"]:
        raise ValueError("SHA256_MISMATCH:"+str(path))
for p in root.rglob("*.json"):
    json.loads(p.read_text(encoding="utf-8"))
for p in root.rglob("*.py"):
    ast.parse(p.read_text(encoding="utf-8"),filename=str(p))
journal=root/"debate-recovery-ascii.jsonl"
if journal.exists():
    data=journal.read_bytes()
    if any(b>127 for b in data): raise ValueError("JOURNAL_NOT_ASCII")
    lines=data.splitlines(keepends=True)
    records=[json.loads(x) for x in lines]
    assert records[0]["seq"]==0 and records[0]["kind"]=="protocol"
    order=records[0]["writer_order"]
    prefix=lines[0]
    for i,record in enumerate(records[1:],1):
        assert record["seq"]==i and record["writer"]==order[i-1],i
        if i>1:
            expected=hashlib.sha1(b"blob "+str(len(prefix)).encode()+b"\0"+prefix).hexdigest()
            assert record["previous_git_blob"]==expected,("JOURNAL_PREFIX_MISMATCH",i)
        prefix+=lines[i]
    assert len(records)==len(order)+1,"INCOMPLETE_JOURNAL"
for p in root.glob("*.md"):
    text=p.read_text(encoding="utf-8")
    if "\x00" in text: raise ValueError("NUL in document")
plan=json.loads((root/"implementation-map.json").read_text(encoding="utf-8"))
tasks=plan["tasks"]
by_id={task["id"]:task for task in tasks}
assert len(tasks)==len(by_id)==12,"DUPLICATE_OR_MISSING_PLAN_TASK"
assert plan["schema_version"]==3,"PLAN_MAP_VERSION"
def visit(task_id, active, done):
    if task_id in active: raise ValueError("CYCLIC_CODE_DEPENDENCY:"+task_id)
    if task_id in done: return
    active.add(task_id)
    for prior in by_id[task_id]["depends_on"]:
        if prior not in by_id: raise ValueError("UNKNOWN_CODE_DEPENDENCY:"+prior)
        visit(prior,active,done)
    active.remove(task_id)
    done.add(task_id)
done=set()
core=(root/"core-implementation.md").read_text(encoding="utf-8")
table=(root/"IMPLEMENTATION_TASKS_ko.md").read_text(encoding="utf-8")
for task in tasks:
    visit(task["id"],set(),done)
    assert task["existing_parent_items"]["workbench"]
    assert task["existing_reuse_ko"] and task["additional_implementation_ko"]
    assert task["acceptance_required"] and task["owner_role_ko"]
    if task["staffing_state"]=="unassigned":
        assert task["implementation_owner"] is None and task["qa_owner"] is None
        assert task["planned_start"] is None and task["planned_finish"] is None
    for prior in task["contract_before_edit"]:
        assert prior in by_id,("UNKNOWN_CONTRACT_DEPENDENCY",prior)
    for branch in task["conditional_dependencies"]:
        assert branch["context"] and branch["scope_ko"]
        for prior in branch["requires"]:
            assert prior in by_id,("UNKNOWN_CONDITIONAL_DEPENDENCY",prior)
    assert "`"+task["id"]+"`" in table
    for path in task["planned_files_or_existing_boundary"]:
        if path.startswith("caelab/") and path.endswith(".py"):
            assert path in core,("C11_PATH_MISMATCH",path)
current_journal=root/"plan-integration-debate-ascii.jsonl"
data=current_journal.read_bytes()
assert data.isascii(),"PLAN_JOURNAL_NOT_ASCII"
lines=data.splitlines(keepends=True)
records=[json.loads(line) for line in lines]
assert records[0]["seq"]==0 and records[0]["kind"]=="protocol"
order=list(records[0]["writer_order"])
prefix=lines[0]
for index,record in enumerate(records[1:],1):
    assert record["seq"]==index and record["writer"]==order[index-1],("PLAN_WRITER_ORDER",index)
    expected=hashlib.sha1(b"blob "+str(len(prefix)).encode()+b"\0"+prefix).hexdigest()
    assert record["previous_git_blob"]==expected,("PLAN_PREFIX_MISMATCH",index)
    required={"reviewed_blobs","position","objections","responses","decision","limitations"}
    assert required<=record.keys(),("PLAN_RECORD_FIELDS",index)
    if "writer_order_extension" in record:
        assert record["writer"]=="M","ONLY_COORDINATOR_CAN_EXTEND_ORDER"
        order.extend(record["writer_order_extension"])
    prefix+=lines[index]
assert len(records)==len(order)+1,"INCOMPLETE_PLAN_JOURNAL"
assert records[-1].get("verdict")=="specification_accepted","PLAN_REVIEW_NOT_COMPLETE"
assert not records[-1].get("unresolved_objection_ids"),"UNRESOLVED_PLAN_OBJECTIONS"
for role in ("WB","PREP","MAT","EXPOST","TECH","KO","RAG","MAT_A"):
    authored=[record for record in records[1:] if record["writer"]==role]
    assert authored and authored[-1].get("verdict")=="specification_accepted",("MISSING_AUTHOR_REVIEW",role)
    assert authored[-1].get("unresolved_objection_ids")==[],("OPEN_AUTHOR_OBJECTIONS",role)
print(json.dumps({"status":"pass","asset_files":len(inventory["files"]),
 "plan_tasks":len(tasks),"plan_discussion_entries":len(records)-1,
 "scope":"file identity, JSON/Python readability, task/dependency/path consistency, ordered authored discussion; no product/native acceptance"}))

# Whole execution order, legacy coverage and each exact first model are checked separately
# from the original 12 technical groups. Neither count represents product completion.
queue=plan["execution_queue"]
queue_by_id={row["id"]:row for row in queue}
assert len(queue)==len(queue_by_id)==39,"EXECUTION_QUEUE_COUNT"
assert plan["execution_policy"]["initial_code_tasks"]==["D01","D02","D03"]
assert plan["execution_policy"]["first_user_delivery"]==["D02","D04","D05","D06","D07"]
assert plan["execution_policy"]["first_material_profile"]=="B-VIS-TRS-1"
assert set(plan["legacy_coverage"])=={f"N{i:02d}" for i in range(1,27)}
for source,rows in plan["legacy_coverage"].items():
    assert rows,("UNCOVERED_PREP_ITEM",source)
    for row_id in rows:
        assert "PREP:"+source in queue_by_id[row_id]["source_items"]
covered_sources={source for row in queue for source in row["source_items"]}
assert set(by_id)<=covered_sources,"UNCOVERED_RESEARCH_GROUP"
profiles=[profile for group in plan["model_delivery_order"] for profile in group["profile_ids"]]
assert len(profiles)==len(set(profiles))==11,"MODEL_DELIVERY_PROFILE_COUNT"
qdone=set()
def qvisit(row_id,active):
    assert row_id not in active,("CYCLIC_EXECUTION_DEPENDENCY",row_id)
    if row_id in qdone:return
    active.add(row_id)
    for prior in queue_by_id[row_id]["depends_on"]:
        assert prior in queue_by_id,("UNKNOWN_EXECUTION_DEPENDENCY",prior)
        qvisit(prior,active)
    active.remove(row_id);qdone.add(row_id)
for row in queue:
    qvisit(row["id"],set())
    for key in ("label_ko","work_kind","source_items","entry_files","file_targets","deliverable_ko","acceptance_ko","next_ko"):
        assert row[key],("INCOMPLETE_EXECUTION_ROW",row["id"],key)
    for branch in row.get("conditional_dependencies",[]):
        assert branch["context"]
        assert all(prior in queue_by_id for prior in branch["requires"])
    for prior,scopes in row.get("prerequisite_scopes",{}).items():
        assert prior in queue_by_id and scopes
    assert row["implementation_state"]=="not_closed_by_document_update"
    assert 'id="'+row["id"].lower()+'"' in table,("MISSING_EXECUTION_ANCHOR",row["id"])
    assert len(row["entry_files"])==len(row["file_targets"])
    for target in row["file_targets"]:
        assert target["file_state"] in ("existing","planned","external")
        if target["repository"]=="pikachu444/caelab-workbench" and target["file_state"]=="existing":
            assert (Path.cwd()/target["path"]).exists(),("MISSING_EXISTING_ENTRY",target["path"])
release=queue_by_id["D36"]
assert release["activation"]=="release_gate" and "D38" in release["release_requires"]
assert "D35" not in release["release_requires"] and "D39" not in release["release_requires"]
assert "D22" not in release["release_requires"]
assert {("D22","original_rp_manual_display","always"),("D22","optional_rp_automatic_fork","automatic_support_adopted"),("D35","adopted_execution_environment","execution_environment_branch_adopted"),("D39","adopted_geometry_or_mesh_feature","geometry_or_mesh_feature_adopted")}=={(x["task_id"],x["subscope"],x["required_when"]) for x in release["release_scoped_requires"]}
execution_journal=root/"execution-order-debate-ascii.jsonl"
data=execution_journal.read_bytes();assert data.isascii(),"EXECUTION_JOURNAL_NOT_ASCII"
lines=data.splitlines(keepends=True);records=[json.loads(line) for line in lines]
assert records[0]["kind"]=="protocol" and records[0]["seq"]==0
order=list(records[0]["writer_order"]);prefix=lines[0]
for index,record in enumerate(records[1:],1):
    assert record["seq"]==index and record["writer"]==order[index-1],("EXECUTION_WRITER_ORDER",index)
    expected=hashlib.sha1(b"blob "+str(len(prefix)).encode()+b"\0"+prefix).hexdigest()
    assert record["previous_git_blob"]==expected,("EXECUTION_PREFIX_MISMATCH",index)
    assert {"reviewed_blobs","position","objections","responses","decision","limitations"}<=record.keys()
    prefix+=lines[index]
assert len(records)==len(order)+1,"INCOMPLETE_EXECUTION_JOURNAL"
for role in ("WB","PREP","MAT","EXPOST","TECH","KO","RAG","MAT_A"):
    authored=[record for record in records[1:] if record["writer"]==role]
    assert len(authored)>=2,("MISSING_EXECUTION_REREVIEW",role)
    assert authored[-1].get("verdict")=="specification_accepted",("EXECUTION_REVIEW_NOT_ACCEPTED",role)
    assert authored[-1].get("unresolved_objection_ids")==[],("OPEN_EXECUTION_OBJECTIONS",role)
assert records[-1].get("verdict")=="specification_accepted"
assert records[-1].get("unresolved_objection_ids")==[]
print(json.dumps({"execution_order":"pass","execution_rows":len(queue),"legacy_prep_rows":26,
 "model_profiles":len(profiles),"authored_execution_entries":len(records)-1,
 "scope":"documentation consistency and recorded review only; no product acceptance"}))
