"""Build a conservative, evidence-backed verification gap backlog.

Gaps are review/test-planning signals, never automatic defect verdicts.
Only explicit business_function_id links are consumed; documented feature names
are not fuzzy-matched to endpoints or functions.
"""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from typing import Any


def _stable_gap_id(kind: str, target: str, evidence: list[dict[str, Any]]) -> str:
    raw=json.dumps({"kind":kind,"target":target,"evidence":evidence},
                   sort_keys=True,ensure_ascii=False,separators=(",",":"))
    return "gap-"+hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_verification_gap_backlog(
    semantics: dict[str, Any],
    business_graph: dict[str, Any],
    test_plan: dict[str, Any] | None = None,
    evidence_matrix: dict[str, Any] | None = None,
    input_hashes: dict[str, str] | None = None,
    traceability_matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(semantics,dict) or not isinstance(semantics.get("features",[]),list):
        raise ValueError("business semantics must contain a features list")
    if not isinstance(semantics.get("endpoint_contract_alignment",[]),list):
        raise ValueError("business semantics endpoint_contract_alignment must be a list")
    if not isinstance(business_graph,dict) or business_graph.get("schema_version")!="usit-graph-v1":
        raise ValueError("business graph must use usit-graph-v1")
    understanding=business_graph.get("business_understanding")
    if not isinstance(understanding,dict):
        raise ValueError("business graph is missing business_understanding")
    views=understanding.get("business_function_views")
    if not isinstance(views,list):
        raise ValueError("business graph is missing business_function_views")
    if test_plan is not None and (not isinstance(test_plan,dict) or test_plan.get("schema_version")!="usit-business-test-plan-v1"):
        raise ValueError("test plan must use usit-business-test-plan-v1")
    if evidence_matrix is not None and (not isinstance(evidence_matrix,dict) or evidence_matrix.get("schema_version")!="usit-business-evidence-verification-v1"):
        raise ValueError("evidence matrix must use usit-business-evidence-verification-v1")

    view_by_id={}
    for i,view in enumerate(views):
        if not isinstance(view,dict) or not isinstance(view.get("business_function_id"),str):
            raise ValueError(f"business_function_views[{i}] has invalid business_function_id")
        fid=view["business_function_id"]
        if fid in view_by_id:
            raise ValueError(f"duplicate business_function_id: {fid}")
        view_by_id[fid]=view

    candidates=test_plan.get("candidates",[]) if test_plan else []
    if not isinstance(candidates,list):
        raise ValueError("test_plan.candidates must be a list")
    design_by_id: dict[str,list[dict[str,Any]]]={}
    for item in candidates:
        if isinstance(item,dict) and isinstance(item.get("business_function_id"),str):
            design_by_id.setdefault(item["business_function_id"],[]).append(item)

    matrix_rows=evidence_matrix.get("business_function_evidence",[]) if evidence_matrix else []
    if not isinstance(matrix_rows,list):
        raise ValueError("evidence_matrix.business_function_evidence must be a list")
    runtime_by_id={}
    for row in matrix_rows:
        if not isinstance(row,dict) or not isinstance(row.get("business_function_id"),str):
            raise ValueError("evidence matrix contains invalid business_function_id")
        fid=row["business_function_id"]
        if fid in runtime_by_id:
            raise ValueError(f"duplicate evidence row for business_function_id: {fid}")
        runtime_by_id[fid]=row

    if traceability_matrix is not None:
        if not isinstance(traceability_matrix, dict) or traceability_matrix.get("schema_version") != "usit-business-traceability-matrix-v1":
            raise ValueError("traceability matrix must use usit-business-traceability-matrix-v1")
        if traceability_matrix.get("source_graph_sha256") != (input_hashes or {}).get("business_graph_sha256"):
            raise ValueError("traceability matrix source_graph_sha256 does not match the supplied business graph bytes")
        trace_rows = traceability_matrix.get("rows")
        if not isinstance(trace_rows, list):
            raise ValueError("traceability matrix rows must be a list")
    else:
        trace_rows = []

    gaps=[]
    def add(kind:str,scope:str,target:str,severity:str,reason:str,action:str,evidence:list[dict[str,Any]],function_id:str|None=None):
        refs=[x for x in evidence if isinstance(x,dict)]
        gaps.append({
            "gap_id":_stable_gap_id(kind,target,refs),"gap_kind":kind,"scope":scope,
            "target":target,"business_function_id":function_id,
            "priority":severity,"status":"REVIEW_REQUIRED",
            "reason":reason,"recommended_next_action":action,
            "evidence_refs":refs,"defect_confirmed":False,
        })

    # A reviewed traceability matrix may close this gap only for an exact
    # feature identity and a target function validated in the same graph bytes.
    reviewed_feature_links={}
    for index, row in enumerate(trace_rows):
        if not isinstance(row, dict):
            raise ValueError(f"traceability matrix rows[{index}] must be an object")
        if row.get("mapping_decision") != "APPROVED_LINK":
            continue
        name=row.get("feature_name")
        source=row.get("feature_source") if isinstance(row.get("feature_source"), str) else ""
        line=row.get("feature_line")
        if not isinstance(name, str) or not isinstance(row.get("business_function_id"), str) or not row["business_function_id"]:
            continue
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise ValueError(f"traceability matrix rows[{index}] has invalid feature_line")
        target_id=row["business_function_id"]
        if target_id not in view_by_id:
            raise ValueError(f"traceability matrix row references unknown business_function_id: {target_id}")
        key=(name.strip(), source, line)
        if key in reviewed_feature_links:
            raise ValueError(f"duplicate approved traceability row for feature identity: {key}")
        reviewed_feature_links[key]=row

    # Legacy graph-level reviewed links remain supported for backward compatibility.
    documented_links=understanding.get("documented_feature_linkage",[])
    link_by_identity={}
    if isinstance(documented_links,list):
        for row in documented_links:
            if not isinstance(row,dict) or not isinstance(row.get("feature_name"),str):
                continue
            source=row.get("source") if isinstance(row.get("source"),str) else ""
            line=row.get("line")
            if not isinstance(line,int) or isinstance(line,bool) or line < 1:
                line=None
            key=(row["feature_name"].strip(),source,line)
            link_by_identity.setdefault(key,[]).append(row)
    for feature in semantics.get("features",[]):
        if not isinstance(feature,dict) or not isinstance(feature.get("name"),str) or not feature["name"].strip():
            continue
        name=feature["name"].strip()
        source=feature.get("source") if isinstance(feature.get("source"),str) else ""
        line=feature.get("line")
        if not isinstance(line,int) or isinstance(line,bool) or line < 1:
            line=None
        links=link_by_identity.get((name,source,line),[])
        trace_row=reviewed_feature_links.get((name,source,line))
        linked=(
            trace_row is not None
            or any(x.get("linkage_status")=="EXPLICIT_REVIEWED_LINK" for x in links)
        )
        if not linked:
            evidence=[]
            if feature.get("source"):
                evidence.append({"path":feature.get("source"),"line":feature.get("line"),"kind":"documented_feature"})
            add("REQUIREMENT_TRACEABILITY_GAP","REQUIREMENT",name,
                feature.get("priority") if feature.get("priority") in {"P0","P1","P2"} else "P2",
                "文档功能尚无经审阅的 feature → business_function 显式关联；不能按名称猜测覆盖关系。",
                "由需求负责人确认功能边界、验收标准，并建立带文档行号和目标 business_function_id 的显式映射。",
                evidence)

    alignments=semantics.get("endpoint_contract_alignment",[])
    for row in alignments:
        if not isinstance(row,dict): continue
        status=row.get("status")
        method=str(row.get("method") or "").upper()
        path=str(row.get("path") or "")
        if not method or not path: continue
        if status=="DOCUMENTED_NOT_FOUND_IN_CODE":
            kind="DOCUMENTED_ENDPOINT_UNMATCHED"
            reason="文档接口未在当前静态扫描范围找到精确 method/path 匹配；不是接口缺失结论。"
            action="确认文档版本、服务范围、路由挂载、网关重写和动态路由，再决定补证或修订契约。"
        elif status=="CODE_ENDPOINT_NOT_DOCUMENTED":
            kind="UNDOCUMENTED_CODE_ENDPOINT"
            reason="代码路由未在当前已识别 API 文档中找到对应契约；不代表路由无效。"
            action="由接口负责人确认是否内部接口、文档遗漏、遗留入口或有意公开，并记录处置依据。"
        elif status=="MULTIPLE_CODE_MATCHES":
            kind="AMBIGUOUS_ENDPOINT_CONTRACT"
            reason="同一文档 method/path 对应多个静态代码路由，当前不能唯一关联执行路径。"
            action="核实服务边界、路由注册顺序、版本前缀和中间件，再确认唯一契约归属。"
        else:
            continue
        doc=row.get("document_evidence") if isinstance(row.get("document_evidence"),dict) else {}
        evidence=[]
        if doc.get("source"): evidence.append({"path":doc.get("source"),"line":doc.get("line"),"kind":"api_document"})
        for item in row.get("code_evidence",[]) if isinstance(row.get("code_evidence"),list) else []:
            if isinstance(item,dict) and item.get("source"):
                evidence.append({"path":item.get("source"),"line":item.get("line"),"kind":"code_route"})
        add(kind,"API_CONTRACT",f"{method} {path}","P1",reason,action,evidence)

    for fid,view in sorted(view_by_id.items()):
        runtime=runtime_by_id.get(fid,{})
        designs=design_by_id.get(fid,[])
        source=view.get("source") if isinstance(view.get("source"),dict) else {}
        refs=[{"path":source.get("path"),"line":source.get("line"),"kind":"business_function_source"}] if source.get("path") else []
        if not designs:
            add("NO_LINKED_TEST_DESIGN","BUSINESS_FUNCTION",fid,"P1",
                "该业务功能没有通过显式 business_function_id 关联的测试设计候选。",
                "补充需求验收标准和测试设计；确认真实页面/接口/数据路径后再建立精确映射。",
                refs,fid)
        if not runtime:
            add("NO_RUNTIME_TEST_EVIDENCE","BUSINESS_FUNCTION",fid,"P1",
                "没有导入该业务功能的运行时测试结果，不能视为已验证。",
                "在获批隔离环境执行适用测试，导入 JUnit/pytest 报告并使用审阅过的 testcase 映射。",
                refs,fid)
        else:
            conclusion=runtime.get("verification_conclusion")
            if runtime.get("evidence_conflict") or conclusion=="EVIDENCE_CONFLICT":
                add("EVIDENCE_CONFLICT","BUSINESS_FUNCTION",fid,"P0",
                    "该功能关联的证据存在图版本或关联冲突，当前结果不可用于验证结论。",
                    "重新生成同一业务图版本的测试/覆盖报告，核对 SHA-256、功能 ID 和映射后重导入。",
                    refs,fid)
            elif runtime.get("test_execution_status")=="FAILED":
                add("TEST_FAILURE_OBSERVED","BUSINESS_FUNCTION",fid,"P0",
                    "导入的运行时测试报告记录至少一个失败用例；失败原因尚需复核。",
                    "检查失败日志、复现步骤、测试数据和预期标准；确认是否为产品缺陷或测试环境问题。",
                    refs,fid)
            elif runtime.get("test_execution_status") != "PASSED":
                add("INCONCLUSIVE_TEST_EVIDENCE","BUSINESS_FUNCTION",fid,"P1",
                    "测试执行状态缺失、无效、未执行、被跳过或结果未知，不能形成通过结论。",
                    "核对测试结果工件及状态映射；补齐测试前置条件后在获批环境执行，并保留原始报告。",
                    refs,fid)
            # B1: the two new conflict kinds each get their own review
            # signal. The generic P0 EVIDENCE_CONFLICT gap above already
            # fires for the same row; these P1 entries name the cause.
            if runtime.get("runtime_consistency_status")=="MISMATCH":
                add("RUNTIME_STATIC_MISMATCH","BUSINESS_FUNCTION",fid,"P1",
                    "运行时事件的显式字段（HTTP method/path、数据库操作/表）与该功能的静态视图不一致；组合结论已降级为证据冲突。",
                    "核对采集端事件字段与静态图谱的 method/endpoint、数据库访问记录，确认哪一边过期或错误后重新采集或重新生成图谱。",
                    refs,fid)
            if runtime.get("source_version_binding_status")=="DIVERGENT":
                add("TEST_COVERAGE_SOURCE_VERSION_DIVERGENCE","BUSINESS_FUNCTION",fid,"P1",
                    "测试快照与覆盖率报告的源码哈希在重叠文件上不一致；两者可能来自同一执行的两次不同源码版本，组合结论已降级为证据冲突。",
                    "用同一源码版本重新执行测试并重新采集覆盖率，或核对快照与覆盖率的来源记录。",
                    refs,fid)
        if runtime.get("code_coverage_status") in {None,"NOT_MEASURED","UNKNOWN"}:
            add("CODE_COVERAGE_NOT_MEASURED","BUSINESS_FUNCTION",fid,"P2",
                "没有可用的业务功能源码行覆盖率证据。",
                "如项目支持，生成 coverage.py/JaCoCo 报告，并审阅精确源码路径与行范围映射；不要用 0% 代替未知。",
                refs,fid)
        elif runtime.get("code_coverage_status") in {"PARTIAL","NOT_COVERED"}:
            add("CODE_COVERAGE_GAP","BUSINESS_FUNCTION",fid,"P2",
                "显式映射的可执行代码行存在未覆盖部分；这不是业务功能缺陷结论。",
                "评估缺失行是否属于业务关键路径，并设计有效断言和分支场景后重新执行覆盖采集。",
                refs,fid)

    gaps.sort(key=lambda x:({"P0":0,"P1":1,"P2":2}.get(x["priority"],9),x["gap_kind"],x["target"],x["gap_id"]))
    counts=Counter(x["gap_kind"] for x in gaps)
    return {
        "schema_version":"usit-verification-gap-backlog-v1",
        "model":"VerificationGapAnalyzer",
        "input_hashes":dict(sorted((input_hashes or {}).items())),
        "business_function_count":len(view_by_id),
        "documented_feature_count":len([x for x in semantics.get("features",[]) if isinstance(x,dict)]),
        "gap_count":len(gaps),
        "gap_kind_counts":dict(sorted(counts.items())),
        "priority_counts":dict(sorted(Counter(x["priority"] for x in gaps).items())),
        "gaps":gaps,
        "limitations":[
            "所有条目均为 REVIEW_REQUIRED，不自动认定为软件缺陷。",
            "PRD 功能与业务功能视图没有显式审阅映射时，不按名称、模块或相似度推断关联。",
            "测试 PASS 不证明需求完整或业务正确；代码行覆盖率不证明分支、断言或端到端覆盖。",
            "未提供证据标记为缺口/NOT_MEASURED，不推断为失败或 0% 覆盖。",
            "当前业务功能视图以静态 endpoint slice 为基础，不能代表项目全部用户功能。",
        ],
    }
