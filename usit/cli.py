from __future__ import annotations
import argparse, json, sys, tempfile
from pathlib import Path
from .scanner import scan_project
from .safety import load_authorization_file, authorization_record, canonical_project, canonical_output
from .graph import validate_graph_file
from .benchmark import run_fixture_benchmark
from .association_benchmark import run_association_invalidation_benchmark
from .android_adapter import AndroidProjectAdapter
from .command_planner import CommandPlanner
from .business_semantics import analyze_business
from .business_test_planner import plan_business_tests
from .business_graph import project_business_graph
from .security_test_planner import plan_security_tests
from .test_result_importer import ingest_junit_results
from .coverage_result_importer import ingest_coverage_results
from .business_evidence_verifier import verify_business_function_evidence
from .verification_gap_analyzer import build_verification_gap_backlog
from .project_quality_assessment import assess_project_quality
from .project_quality_report import render_project_quality_markdown
from .business_traceability import build_business_traceability
from .traceability_template import create_feature_mapping_template
from .business_traceability_report import render_business_traceability_markdown
from .runtime_evidence import validate_runtime_evidence_files
from .android_event_adapter import adapt_android_jsonl_file
from .ai_gateway import Agent2APIClient, DEFAULT_BASE_URL, DEFAULT_API_KEY_ENV
from .ai_orchestrator import load_model_registry, run_ensemble, registry_template
from .ai_claim_review import review_claim_ledger_file
from .ai_evidence_binding import verify_evidence_bindings_file
from .scan_attestation import load_trusted_public_key, public_key_id, verify_scan_attestation
from .controlled_pytest import run_controlled_pytest

def selftest() -> int:
    from .scanner import discover_files, analyze_file
    checks=[]
    with tempfile.TemporaryDirectory(prefix='usit-selftest-') as td:
        # Resolve the base once so the synthetic paths share the canonical, long
        # (non-8.3) form that ``discover_files`` returns; otherwise a short-named
        # temp directory makes ``relative_to`` fail for reasons unrelated to the
        # scanner behaviour under test.
        base=Path(td).resolve(); proj=base/'project'; out=base/'out'; proj.mkdir()
        (proj/'app.py').write_text('import os\nclass Service:\n    def run(self):\n        return helper()\ndef helper():\n    return 1\n',encoding='utf-8')
        (proj/'package.json').write_text('{"name":"fixture"}',encoding='utf-8')
        (proj/'.env').write_text('API_TOKEN=must_not_read',encoding='utf-8')
        (proj/'id_rsa').write_text('PRIVATE KEY',encoding='utf-8')
        (proj/'node_modules').mkdir(); (proj/'node_modules'/'x.js').write_text('bad',encoding='utf-8')
        fs=discover_files(proj); names={p.relative_to(proj).as_posix() for p in fs}
        checks.append(('敏感文件/目录排除', '.env' not in names and 'id_rsa' not in names and not any(x.startswith('node_modules/') for x in names)))
        a=analyze_file(proj,proj/'app.py')
        checks.append(('Python符号提取', {s['name'] for s in a['symbols']} >= {'Service','run','helper'}))
        checks.append(('Python导入提取', any(x['name']=='os' for x in a['imports'])))
        auth=authorization_record(proj.resolve(),out.resolve())
        s1=scan_project(str(proj),str(out),force=True,authorization=auth); s2=scan_project(str(proj),str(out),force=True,authorization=auth)
        checks.append(('扫描输出生成', all((out/n).is_file() for n in ('report.md','report.json','scan_manifest.json','knowledge_graph.json','test_plan.json'))))
        checks.append(('增量未变识别', len(s2['incremental']['unchanged']) >= 2 and not s2['incremental']['modified']))
        try: scan_project(str(proj),str(proj/'inside-output'),authorization=auth); guard=False
        except ValueError: guard=True
        checks.append(('输出目录安全守卫',guard))
    for name,ok in checks: print(f"{'PASS' if ok else 'FAIL'} | {name}")
    print(f"RESULT: {'PASS' if all(ok for _,ok in checks) else 'FAIL'} ({sum(ok for _,ok in checks)}/{len(checks)})")
    return 0 if all(ok for _,ok in checks) else 1

def _inspect_android(a) -> int:
    """Read-only Android inspection: static profile + command candidates.

    Never executes Gradle/ADB or any test/build command.  Writes two JSON files
    (android_profile.json, android_test_plan.json) and prints a summary.
    """
    auth = load_authorization_file(a.authorization_file, a.project, a.out)
    # Reuse the existing fail-closed canonicalisation so scope/overlap checks
    # are identical to `scan`.
    project = canonical_project(a.project)
    out_parent = canonical_output(a.out).parent
    out_parent.mkdir(parents=True, exist_ok=True)
    out = canonical_output(a.out)
    if not a.force and out.exists() and any(out.iterdir()):
        raise ValueError("inspect-android output already exists; pass --force to overwrite, or choose a new path.")
    out.mkdir(parents=True, exist_ok=True)
    profile = AndroidProjectAdapter(project).analyze().to_dict()
    plan = CommandPlanner(profile).to_dict()
    (out / "android_profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "android_test_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "project_root": str(project),
        "detection_status": profile["detection_status"],
        "module_count": len(profile["modules"]),
        "test_source_count": len(profile["test_sources"]),
        "candidate_count": plan["candidate_count"],
        "outputs": [str(out / "android_profile.json"), str(out / "android_test_plan.json")],
        "note": "Static inspection + candidate generation only. No Gradle/ADB/test command was executed.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0



def _plan_business_tests(a) -> int:
    """Build evidence-linked business test candidates; never execute them."""
    auth = load_authorization_file(a.authorization_file, a.project, a.out)
    project = canonical_project(a.project)
    out = canonical_output(a.out)
    auth.validate(project, out)
    if out.exists():
        raise ValueError("business test-plan output already exists; choose a new path to preserve prior evidence")
    semantics = analyze_business(project)
    business_graph = None
    plan = None
    security_plan = None
    graph_path = None
    if a.graph:
        supplied_graph = Path(a.graph).expanduser()
        if supplied_graph.is_symlink():
            raise ValueError("graph input must not be a symbolic link")
        graph_path = supplied_graph.resolve(strict=True)
        if not graph_path.is_file():
            raise ValueError("graph input must be a regular file")
        if graph_path.is_relative_to(project):
            raise ValueError("graph input must be outside the target project")
        if graph_path.is_relative_to(out):
            raise ValueError("graph input must be outside the output directory")
        source_graph = json.loads(graph_path.read_text(encoding="utf-8"))
        business_graph = project_business_graph(source_graph, semantics)
        security_plan = plan_security_tests(business_graph)
    plan = plan_business_tests(semantics, business_graph)
    out.mkdir(parents=True, exist_ok=False)
    (out / "business_semantics.json").write_text(
        json.dumps(semantics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "business_test_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if business_graph is not None:
        import hashlib
        business_graph_bytes = (
            json.dumps(business_graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if security_plan is not None:
            security_plan["source_business_graph_sha256"] = hashlib.sha256(business_graph_bytes).hexdigest()
        (out / "business_knowledge_graph.json").write_bytes(business_graph_bytes)
    if security_plan is not None:
        (out / "security_test_candidates.json").write_text(
            json.dumps(security_plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    summary = {
        "project_root": str(project),
        "entity_count": semantics["entity_count"],
        "feature_count": semantics["feature_count"],
        "endpoint_count": semantics["endpoint_count"],
        "discrepancy_count": semantics["discrepancy_count"],
        "test_candidate_count": plan["candidate_count"],
        "security_test_candidate_count": (
            security_plan["candidate_count"] if security_plan is not None else None
        ),
        "business_graph_linked_records": (
            business_graph.get("business_understanding", {}).get("linked_records", 0)
            if business_graph is not None else None
        ),
        "business_graph_unlinked_records": (
            len(business_graph.get("business_understanding", {}).get("unlinked_records", []))
            if business_graph is not None else None
        ),
        "graph_input": str(graph_path) if graph_path is not None else None,
        "execution_status": "NOT_EXECUTED",
        "outputs": [
            str(out / "business_semantics.json"),
            str(out / "business_test_plan.json"),
            *([str(out / "business_knowledge_graph.json")] if business_graph is not None else []),
            *([str(out / "security_test_candidates.json")] if security_plan is not None else []),
        ],
        "note": "Static business understanding and security test-candidate design only; candidates require requirements review and separate authorization. No build, test, database or network operation was executed.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0

def main(argv=None):
    parser=argparse.ArgumentParser(prog='usit',description='USIT P0 read-only software scanner')
    sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('scan',help='Scan a project without executing its code'); p.add_argument('project'); p.add_argument('--out',required=True,help='Output directory outside project root'); p.add_argument('--authorization-file',required=True,help='External JSON authorization record bound to project, action and output')
    p.add_argument('--force',action='store_true',help='若输出目录已有扫描结果则强制覆盖（默认冲突时新建带时间戳子目录，避免覆盖原始结果）')
    sub.add_parser('selftest',help='Run built-in synthetic tests')
    b=sub.add_parser('benchmark-fixtures',help='Run static parser ground-truth fixtures without executing fixture code')
    b.add_argument('--out',required=True,help='New JSON result path; existing files are never overwritten')
    t=sub.add_parser('benchmark-associations',help='Check static test-to-symbol association invalidation against synthetic changes')
    t.add_argument('--out',required=True,help='New JSON result path; existing files are never overwritten')
    v=sub.add_parser('validate-graph',help='Validate a USIT knowledge graph without modifying it')
    v.add_argument('graph',help='Path to knowledge_graph.json')
    v.add_argument('--project-root',help='Optional project root for file/line evidence checks')
    v.add_argument('--out-json',help='Machine-readable validation report (must not already exist)')
    v.add_argument('--out-md',help='Human-readable validation report (must not already exist)')
    # P0.7.13-F1: read-only Android structural inspection + command-candidate
    # generation.  The name "inspect" is deliberate — this command NEVER executes
    # Gradle, ADB, or any test/build command; it only reads project files and
    # emits structured candidates whose execution_status is NOT_EXECUTED.
    ins=sub.add_parser('inspect-android',help='Inspect an Android/Gradle project statically and emit test-command candidates (read-only; no execution)')
    ins.add_argument('project',help='Authorized absolute project root to inspect')
    ins.add_argument('--out',required=True,help='Output directory for android_profile.json and android_test_plan.json (must not overlap the project)')
    ins.add_argument('--authorization-file',required=True,help='External JSON authorization record bound to project, action and output')
    ins.add_argument('--force',action='store_true',help='Overwrite an existing, non-empty output directory')
    bt=sub.add_parser('plan-business-tests',help='Extract business semantics and generate evidence-linked test candidates (static only)')
    bt.add_argument('project',help='Authorized absolute project root')
    bt.add_argument('--out',required=True,help='New output directory outside the project root')
    bt.add_argument('--authorization-file',required=True,help='External authorization record for static read-only analysis')
    bt.add_argument('--graph',help='Optional existing knowledge_graph.json from a completed USIT scan; emits business_knowledge_graph.json')
    rp=sub.add_parser('run-pytest',help='Run pytest only against a clean staged Git HEAD snapshot; requires explicit execution authorization')
    rp.add_argument('project',help='Absolute Git project root; dirty or untracked worktrees are refused')
    rp.add_argument('--evidence-parent',required=True,help='Existing absolute directory outside the project for immutable run evidence')
    rp.add_argument('--authorization-file',required=True,help='External usit-test-execution-authorization-v1 record acknowledging project-code execution')
    rp.add_argument('--timeout',type=float,default=1800,help='Maximum execution seconds (default: 1800)')
    rp.add_argument('--pytest-arg',action='append',default=[],help='Allow-listed pytest selector/option; repeat as needed')
    ir=sub.add_parser('ingest-test-results',help='Import completed JUnit XML or pytest JSON results through an explicit mapping; never runs tests')
    ir.add_argument('--junit',required=True,help='JUnit XML report from an already completed test run')
    ir.add_argument('--mapping',required=True,help='Reviewed JSON mapping of exact testcase IDs to business_function_id')
    ir.add_argument('--graph',required=True,help='business_knowledge_graph.json generated by USIT')
    ir.add_argument('--out',required=True,help='New JSON result path; existing files are never overwritten')
    ir.add_argument('--format',choices=['junit-xml','pytest-json'],default='junit-xml',help='Completed test report format')
    ir.add_argument('--project-root',help='Absolute source-tree root for read-only verification of mapped test source files')
    ir.add_argument('--provenance-record',help='Optional controlled-runner execution provenance JSON')
    ir.add_argument('--source-manifest',help='Source snapshot manifest paired with --provenance-record')
    cr=sub.add_parser('ingest-coverage',help='Import coverage.py JSON or JaCoCo XML through explicit source-line mappings; never runs tests')
    cr.add_argument('--coverage',required=True,help='Completed coverage report file')
    cr.add_argument('--mapping',required=True,help='Reviewed source_file + line range to business_function_id mapping JSON')
    cr.add_argument('--graph',required=True,help='business_knowledge_graph.json generated by USIT')
    cr.add_argument('--project-root',required=True,help='Absolute root of the exact source tree covered by the report; source files are read-only')
    cr.add_argument('--format',choices=['coverage-py-json','jacoco-xml'],required=True,help='Coverage report format')
    cr.add_argument('--out',required=True,help='New JSON result path; existing files are never overwritten')
    ev=sub.add_parser('verify-evidence',help='Cross-check imported test and coverage evidence per business function; never executes tests')
    ev.add_argument('--graph',required=True,help='Exact business_knowledge_graph.json used by importers')
    ev.add_argument('--test-results',help='Optional usit-test-execution-results-v1 JSON')
    ev.add_argument('--coverage-results',help='Optional usit-code-coverage-results-v1 JSON')
    ev.add_argument('--out',required=True,help='New JSON evidence matrix path; existing files are never overwritten')
    ga=sub.add_parser('analyze-gaps',help='Build a requirements and business-function verification gap backlog; review candidates only')
    ga.add_argument('--semantics',required=True,help='business_semantics.json')
    ga.add_argument('--graph',required=True,help='business_knowledge_graph.json')
    ga.add_argument('--test-plan',help='Optional business_test_plan.json')
    ga.add_argument('--evidence-matrix',help='Optional verify-evidence output JSON')
    ga.add_argument('--traceability',help='Optional trace-business output JSON; must be bound to the exact graph bytes')
    ga.add_argument('--out',required=True,help='New JSON gap backlog path; existing files are never overwritten')
    qa=sub.add_parser('assess-quality',help='Summarize multidimensional project quality from evidence artifacts; no aggregate score')
    qa.add_argument('--semantics',required=True,help='business_semantics.json')
    qa.add_argument('--graph',required=True,help='business_knowledge_graph.json')
    qa.add_argument('--test-plan',help='Optional business_test_plan.json')
    qa.add_argument('--evidence-matrix',help='Optional verify-evidence output JSON')
    qa.add_argument('--gap-backlog',help='Optional analyze-gaps output JSON')
    qa.add_argument('--out',required=True,help='New JSON assessment path; existing files are never overwritten')
    qa.add_argument('--out-md',help='Optional new Markdown report path; existing files are never overwritten')
    bt=sub.add_parser('trace-business',help='Build an explicit review-gated requirements-to-runtime-evidence traceability matrix')
    bt.add_argument('--semantics',required=True,help='business_semantics.json')
    bt.add_argument('--graph',required=True,help='business_knowledge_graph.json')
    bt.add_argument('--mapping',required=True,help='Reviewed feature-to-business-function mapping manifest JSON')
    bt.add_argument('--test-plan',help='Optional business_test_plan.json')
    bt.add_argument('--security-test-plan',help='Optional security_test_candidates.json')
    bt.add_argument('--evidence-matrix',help='Optional verify-evidence output JSON')
    bt.add_argument('--runtime-evidence',help='Optional validate-runtime-evidence output JSON')
    bt.add_argument('--out',required=True,help='New traceability matrix JSON path; existing files are never overwritten')
    bt.add_argument('--out-md',help='Optional new Markdown traceability report path; existing files are never overwritten')
    tt=sub.add_parser('init-traceability-template',help='Create an unapproved feature-to-function review template bound to the exact graph hash')
    tt.add_argument('--semantics',required=True,help='business_semantics.json')
    tt.add_argument('--graph',required=True,help='business_knowledge_graph.json')
    tt.add_argument('--out',required=True,help='New mapping template JSON path; existing files are never overwritten')
    re=sub.add_parser('validate-runtime-evidence',help='Validate already captured privacy-minimised runtime events; does not connect to or execute the target app')
    re.add_argument('--events',required=True,help='Existing usit-runtime-evidence-v1 JSON event file')
    re.add_argument('--graph',required=True,help='Exact business_knowledge_graph.json used by the event capture session')
    re.add_argument('--out',required=True,help='New validation report path; existing files are never overwritten')
    aa=sub.add_parser('adapt-android-events',help='Convert a privacy-minimised Android instrumentation JSONL export into USIT runtime evidence; offline only')
    aa.add_argument('--input',required=True,help='Structured Android event export containing USIT_ANDROID_EVENT records')
    aa.add_argument('--graph',required=True,help='Business graph used by the instrumentation session')
    aa.add_argument('--session-id',required=True,help='Opaque session identifier')
    aa.add_argument('--captured-at',required=True,help='Session capture timestamp with timezone')
    aa.add_argument('--out',required=True,help='New usit-runtime-evidence-v1 JSON path; existing files are never overwritten')
    am=sub.add_parser('ai-models',help='List models exposed by the local Agent2API gateway; never prints credentials')
    am.add_argument('--base-url',default=DEFAULT_BASE_URL,help='Loopback gateway URL (default: http://127.0.0.1:3065/v1)')
    am.add_argument('--api-key-env',default=DEFAULT_API_KEY_ENV,help='Environment variable containing the optional gateway key')
    am.add_argument('--timeout',type=float,default=20.0,help='Request timeout in seconds')
    ac=sub.add_parser('ai-chat',help='Send an explicitly supplied prompt to one local Agent2API model')
    ac.add_argument('--model',required=True,help='Exact model ID returned by ai-models')
    ac.add_argument('--prompt',required=True,help='Explicit prompt text; project files are never read automatically')
    ac.add_argument('--system',help='Optional system instruction')
    ac.add_argument('--base-url',default=DEFAULT_BASE_URL,help='Loopback gateway URL')
    ac.add_argument('--api-key-env',default=DEFAULT_API_KEY_ENV,help='Environment variable containing the optional gateway key')
    ac.add_argument('--timeout',type=float,default=60.0,help='Request timeout in seconds')
    ac.add_argument('--max-tokens',type=int,default=1024,help='Maximum response tokens (1..8192)')
    ai_init=sub.add_parser('ai-init-registry',help='Create a local model-role registry template; never overwrites existing files')
    ai_init.add_argument('--out',required=True,help='New local JSON registry path')
    ae=sub.add_parser('ai-ensemble',help='Run one explicit task across configured local models in parallel')
    ae.add_argument('--config',required=True,help='Local model registry JSON path')
    ae.add_argument('--task',required=True,choices=['architecture','defect','test_design','review','general'])
    ae.add_argument('--prompt',required=True,help='Explicit prompt text; no project files are read automatically')
    ae.add_argument('--max-models',type=int,default=3,help='Maximum enabled models to call (1..8)')
    ae.add_argument('--base-url',default=DEFAULT_BASE_URL,help='Loopback Agent2API URL')
    ae.add_argument('--api-key-env',default=DEFAULT_API_KEY_ENV,help='Environment variable containing the gateway key')
    ae.add_argument('--timeout',type=float,default=60.0)
    ae.add_argument('--max-tokens',type=int,default=2048)
    ae.add_argument('--out',help='Optional new JSON result path; existing files are never overwritten')
    acr=sub.add_parser('review-ai-claims',help='Compare explicit model claims against a reference catalog; never promotes claims to facts')
    acr.add_argument('--input',required=True,help='Structured usit-ai-claim-ledger-v1 JSON; free-text model output is not parsed automatically')
    acr.add_argument('--out',required=True,help='New review JSON path; existing files are never overwritten')
    avs=sub.add_parser('verify-scan-attestation',help='Verify detached scan manifest signature against independently provisioned Ed25519 public keys')
    avs.add_argument('--manifest',required=True,help='Exact scan manifest file')
    avs.add_argument('--attestation',required=True,help='Detached scan_attestation.json sidecar')
    avs.add_argument('--trusted-public-key',action='append',required=True,help='External raw or PEM Ed25519 public key; repeat for multiple trusted keys')
    avs.add_argument('--expected-snapshot-id',help='Optional caller-supplied anti-substitution policy; not a freshness timestamp')
    avs.add_argument('--out',required=True,help='New JSON verification report path; existing files are never overwritten')
    aev=sub.add_parser('verify-evidence-bindings',help='Verify AI evidence catalog references against exact local artifact bytes; never promotes claims to facts')
    aev.add_argument('--input',required=True,help='Structured usit-ai-claim-ledger-v1 JSON')
    aev.add_argument('--evidence-root',required=True,help='Directory containing the referenced local evidence artifacts')
    aev.add_argument('--trusted-public-key',action='append',default=[],help='External raw or PEM Ed25519 public key for snapshot attestations; repeat for multiple trusted keys')
    aev.add_argument('--out',required=True,help='New JSON binding report path; existing files are never overwritten')
    a=parser.parse_args(argv)
    try:
        if a.cmd=='verify-scan-attestation':
            manifest_path=Path(a.manifest).expanduser()
            attestation_path=Path(a.attestation).expanduser()
            if manifest_path.is_symlink() or attestation_path.is_symlink():
                raise ValueError('manifest and attestation paths must not be symbolic links')
            manifest=manifest_path.resolve(strict=True)
            attestation=attestation_path.resolve(strict=True)
            if not manifest.is_file() or not attestation.is_file():
                raise ValueError('manifest and attestation must be regular files')
            if manifest.stat().st_size > 50*1024*1024 or attestation.stat().st_size > 64*1024:
                raise ValueError('manifest or attestation exceeds the supported size limit')
            trust={}
            for key_path in a.trusted_public_key:
                raw_key=load_trusted_public_key(key_path)
                trust[public_key_id(raw_key)]=raw_key
            result=verify_scan_attestation(manifest.read_bytes(),attestation.read_bytes(),trust,
                expected_snapshot_id=a.expected_snapshot_id)
            output=Path(a.out).expanduser()
            if output.is_symlink() or output.exists():
                raise ValueError('attestation report already exists or is a symbolic link; refusing to overwrite')
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'result_path':str(output.resolve()),'status':result['status'],
                'key_id':result['key_id'],'snapshot_id':result['snapshot_id'],
                'signature_valid':result['signature_valid'],'trusted_key':result['trusted_key'],
                'freshness':result['freshness']},ensure_ascii=False,indent=2))
            return 0 if result['status']=='ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY' else 2
        if a.cmd=='verify-evidence-bindings':
            trust={}
            for key_path in a.trusted_public_key:
                raw_key=load_trusted_public_key(key_path)
                trust[public_key_id(raw_key)]=raw_key
            result=verify_evidence_bindings_file(a.input,a.evidence_root,trust)
            output=Path(a.out).expanduser()
            if output.is_symlink() or output.exists():
                raise ValueError('evidence binding output already exists or is a symbolic link; refusing to overwrite')
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'result_path':str(output.resolve()),'schema':result['schema'],
                'evidence_count':result['evidence_count'],'binding_status_counts':result['binding_status_counts'],
                'claim_count':result['claim_count'],'claim_binding_status_counts':result['claim_binding_status_counts'],
                'snapshot_binding_status_counts':result['snapshot_binding_status_counts'],
                'attestation_status_counts':result['attestation_status_counts'],
                'overall_truth_status':result['overall_truth_status']},ensure_ascii=False,indent=2))
            binding_ok=(
                all(x['binding_status']=='ARTIFACT_HASH_MATCH_AND_LOCATOR_VALID'
                    for x in result['evidence_bindings'])
                and all(x['binding_status']=='ALL_BINDINGS_RESOLVED_NOT_AUTHENTICATED'
                        for x in result['claims'])
            )
            snapshot_ok=all(x.get('snapshot_binding_status') in (
                'NOT_APPLICABLE','NOT_PROVIDED','MATCHED_SCAN_MANIFEST_ENTRY'
            ) for x in result['evidence_bindings'])
            attestation_ok=all(x.get('attestation_status') in (
                'NOT_APPLICABLE','ATTESTATION_NOT_PROVIDED',
                'ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY'
            ) for x in result['evidence_bindings'])
            return 0 if binding_ok and snapshot_ok and attestation_ok else 2
        if a.cmd=='review-ai-claims':
            result=review_claim_ledger_file(a.input)
            output=Path(a.out).expanduser()
            if output.is_symlink() or output.exists():
                raise ValueError('claim review output already exists or is a symbolic link; refusing to overwrite')
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'result_path':str(output.resolve()),'schema':result['schema'],
                'claim_count':result['claim_count'],'comparison_group_count':result['comparison_group_count'],
                'review_required_count':result['review_required_count'],
                'unknown_evidence_reference_count':result['unknown_evidence_reference_count'],
                'overall_truth_status':result['overall_truth_status']},ensure_ascii=False,indent=2))
            return 0 if result['review_required_count']==0 and result['unknown_evidence_reference_count']==0 else 2
        if a.cmd=='ai-init-registry':
            output=Path(a.out).expanduser()
            if output.is_symlink() or output.exists():
                raise ValueError('registry output already exists or is a symbolic link; refusing to overwrite')
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(registry_template(),ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'registry_path':str(output.resolve()),'status':'TEMPLATE_CREATED',
                'models_enabled_by_default':False},ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='ai-ensemble':
            registry=load_model_registry(a.config)
            result=run_ensemble(registry,a.task,a.prompt,max_models=a.max_models,
                timeout=a.timeout,max_tokens=a.max_tokens,base_url=a.base_url,
                api_key_env=a.api_key_env)
            if a.out:
                output=Path(a.out).expanduser()
                if output.is_symlink() or output.exists():
                    raise ValueError('ensemble output already exists or is a symbolic link; refusing to overwrite')
                output.parent.mkdir(parents=True,exist_ok=True)
                output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                result['result_path']=str(output.resolve())
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result['status']=='SUCCEEDED' else 2 if result['status']=='PARTIAL' else 1
        if a.cmd=='ai-models':
            client=Agent2APIClient(a.base_url,api_key_env=a.api_key_env,timeout=a.timeout)
            models=client.list_models()
            print(json.dumps({"gateway":"Agent2API","base_url":client.base_url,
                "model_count":len(models),"models":models,
                "credential_source":a.api_key_env if client.api_key else "NOT_CONFIGURED",
                "network_scope":"LOOPBACK_ONLY"},ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='ai-chat':
            client=Agent2APIClient(a.base_url,api_key_env=a.api_key_env,timeout=a.timeout)
            result=client.chat(a.model,a.prompt,a.system,a.max_tokens)
            # Prompt, system text and credentials are deliberately omitted from output.
            print(json.dumps({"gateway":"Agent2API","model":result["model"],
                "content":result["content"],"finish_reason":result["finish_reason"],
                "usage":result["usage"],"request_id":result["request_id"],
                "prompt_content_logged":False},ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='selftest': return selftest()
        if a.cmd=='benchmark-fixtures':
            output=Path(a.out).expanduser().resolve()
            if output.exists(): raise ValueError('benchmark output already exists; choose a new path to avoid overwriting')
            output.parent.mkdir(parents=True,exist_ok=True)
            result=run_fixture_benchmark()
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'result_path':str(output),'languages':result['languages']},ensure_ascii=False,indent=2))
            return 0 if all(x['false_resolved_nonresolved_count']==0 for x in result['languages']) else 1
        if a.cmd=='benchmark-associations':
            output=Path(a.out).expanduser().resolve()
            if output.exists(): raise ValueError('benchmark output already exists; choose a new path to avoid overwriting')
            output.parent.mkdir(parents=True,exist_ok=True)
            result=run_association_invalidation_benchmark()
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'result_path':str(output),'status':result['status'],'passed':result['passed'],'total':result['total']},ensure_ascii=False,indent=2))
            return 0 if result['status']=='PASS' and result['false_current_links_after_evidence_change']==0 else 1
        if a.cmd=='validate-graph':
            graph_path=Path(a.graph).expanduser().resolve(strict=True)
            json_out=Path(a.out_json).expanduser().resolve() if a.out_json else graph_path.parent/'graph_validation.json'
            md_out=Path(a.out_md).expanduser().resolve() if a.out_md else graph_path.parent/'graph_validation.md'
            project_root=Path(a.project_root).expanduser().resolve() if a.project_root else None
            if json_out==md_out or json_out==graph_path or md_out==graph_path:
                raise ValueError('graph input and JSON/Markdown report paths must be distinct')
            if project_root and (json_out.is_relative_to(project_root) or md_out.is_relative_to(project_root)):
                raise ValueError('validation reports must be outside --project-root to preserve target-project read-only safety')
            if json_out.exists() or md_out.exists():
                if a.out_json or a.out_md:
                    raise ValueError('validation report output already exists; choose new paths to avoid overwriting')
                base=1
                while True:
                    json_out=graph_path.parent/f'graph_validation-{base}.json'
                    md_out=graph_path.parent/f'graph_validation-{base}.md'
                    if not json_out.exists() and not md_out.exists(): break
                    base+=1
            # P0.7.10-V: --project-root no longer triggers source reads.  Line
            # checks now run against the graph's own source_line_counts snapshot
            # (validate_graph_file supplies it), so this command never re-opens
            # the target project.  project_root is retained only for the
            # path-escape and existence guards, which read no content.
            result=validate_graph_file(graph_path,project_root,json_out,md_out)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result['is_valid'] else 1
        if a.cmd=='inspect-android':
            return _inspect_android(a)
        if a.cmd=='plan-business-tests':
            return _plan_business_tests(a)
        if a.cmd=='init-traceability-template':
            import hashlib
            def read_template_input(path, limit):
                source=Path(path).expanduser()
                if source.is_symlink(): raise ValueError("input must not be a symbolic link")
                resolved=source.resolve(strict=True)
                if not resolved.is_file(): raise ValueError("input must be a regular file")
                raw=resolved.read_bytes()
                if len(raw)>limit: raise ValueError("traceability template input exceeds size limit")
                return raw,json.loads(raw)
            sem_bytes,sem_doc=read_template_input(a.semantics,20*1024*1024)
            graph_bytes,graph_doc=read_template_input(a.graph,50*1024*1024)
            if graph_doc.get("schema_version")!="usit-graph-v1" or not isinstance(graph_doc.get("business_understanding"),dict):
                raise ValueError("graph must be a business-projected usit-graph-v1 artifact")
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("mapping template output already exists; choose a new path")
            graph_hash=hashlib.sha256(graph_bytes).hexdigest()
            result=create_feature_mapping_template(sem_doc,graph_hash)
            result["input_hashes"]={"business_semantics_sha256":hashlib.sha256(sem_bytes).hexdigest(),
                                    "business_graph_sha256":graph_hash}
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({"template_status":result["template_status"],
                "mapping_count":result["mapping_count"],"source_graph_sha256":graph_hash,
                "result_path":str(output)},ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='trace-business':
            import hashlib
            def read_trace_json(path, limit):
                source=Path(path).expanduser()
                if source.is_symlink(): raise ValueError("input must not be a symbolic link")
                resolved=source.resolve(strict=True)
                if not resolved.is_file(): raise ValueError("input must be a regular file")
                raw=resolved.read_bytes()
                if len(raw)>limit: raise ValueError("business traceability input exceeds size limit")
                return raw,json.loads(raw)
            sem_bytes,sem_doc=read_trace_json(a.semantics,20*1024*1024)
            graph_bytes,graph_doc=read_trace_json(a.graph,50*1024*1024)
            mapping_bytes,mapping_doc=read_trace_json(a.mapping,10*1024*1024)
            plan_pair=read_trace_json(a.test_plan,20*1024*1024) if a.test_plan else (None,None)
            matrix_pair=read_trace_json(a.evidence_matrix,50*1024*1024) if a.evidence_matrix else (None,None)
            runtime_pair=read_trace_json(a.runtime_evidence,50*1024*1024) if a.runtime_evidence else (None,None)
            security_pair=read_trace_json(a.security_test_plan,20*1024*1024) if a.security_test_plan else (None,None)
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("traceability output already exists; choose a new path to preserve prior evidence")
            markdown_output=None
            if a.out_md:
                markdown_output=Path(a.out_md).expanduser()
                if markdown_output.is_symlink(): raise ValueError("Markdown output must not be a symbolic link")
                markdown_output=markdown_output.resolve()
                if markdown_output==output: raise ValueError("JSON and Markdown output paths must be distinct")
                if markdown_output.exists(): raise ValueError("Markdown traceability output already exists; choose a new path")
            hashes={"business_semantics_sha256":hashlib.sha256(sem_bytes).hexdigest(),
                    "business_graph_sha256":hashlib.sha256(graph_bytes).hexdigest(),
                    "mapping_manifest_sha256":hashlib.sha256(mapping_bytes).hexdigest()}
            if plan_pair[0] is not None: hashes["test_plan_sha256"]=hashlib.sha256(plan_pair[0]).hexdigest()
            if matrix_pair[0] is not None: hashes["evidence_matrix_sha256"]=hashlib.sha256(matrix_pair[0]).hexdigest()
            if runtime_pair[0] is not None: hashes["runtime_evidence_validation_sha256"]=hashlib.sha256(runtime_pair[0]).hexdigest()
            if security_pair[0] is not None: hashes["security_test_plan_sha256"]=hashlib.sha256(security_pair[0]).hexdigest()
            result=build_business_traceability(sem_doc,graph_doc,mapping_doc,
                plan_pair[1],matrix_pair[1],hashes,runtime_pair[1],security_pair[1])
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            if markdown_output is not None:
                markdown_output.parent.mkdir(parents=True,exist_ok=True)
                markdown_output.write_text(render_business_traceability_markdown(result),encoding='utf-8')
            summary={"schema_version":result["schema_version"],
                     "feature_count":result["feature_count"],
                     "business_function_view_count":result["business_function_view_count"],
                     "reviewed_mapping_count":result["reviewed_mapping_count"],
                     "unmapped_feature_count":result["unmapped_feature_count"],
                     "traceability_status_counts":result["traceability_status_counts"],
                     "result_path":str(output)}
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='assess-quality':
            import hashlib
            def read_quality_json(path, limit):
                source=Path(path).expanduser()
                if source.is_symlink(): raise ValueError("input must not be a symbolic link")
                resolved=source.resolve(strict=True)
                if not resolved.is_file(): raise ValueError("input must be a regular file")
                raw=resolved.read_bytes()
                if len(raw)>limit: raise ValueError("quality assessment input exceeds size limit")
                return raw,json.loads(raw)
            sem_bytes,sem_doc=read_quality_json(a.semantics,20*1024*1024)
            graph_bytes,graph_doc=read_quality_json(a.graph,50*1024*1024)
            plan_pair=read_quality_json(a.test_plan,20*1024*1024) if a.test_plan else (None,None)
            matrix_pair=read_quality_json(a.evidence_matrix,50*1024*1024) if a.evidence_matrix else (None,None)
            gaps_pair=read_quality_json(a.gap_backlog,50*1024*1024) if a.gap_backlog else (None,None)
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("quality assessment output already exists; choose a new path to preserve prior evidence")
            markdown_output=None
            if a.out_md:
                markdown_output=Path(a.out_md).expanduser()
                if markdown_output.is_symlink(): raise ValueError("Markdown output must not be a symbolic link")
                markdown_output=markdown_output.resolve()
                if markdown_output==output: raise ValueError("JSON and Markdown output paths must be distinct")
                if markdown_output.exists(): raise ValueError("Markdown output already exists; choose a new path to preserve prior evidence")
            hashes={"business_semantics_sha256":hashlib.sha256(sem_bytes).hexdigest(),
                    "business_graph_sha256":hashlib.sha256(graph_bytes).hexdigest()}
            if plan_pair[0] is not None: hashes["test_plan_sha256"]=hashlib.sha256(plan_pair[0]).hexdigest()
            if matrix_pair[0] is not None: hashes["evidence_matrix_sha256"]=hashlib.sha256(matrix_pair[0]).hexdigest()
            if gaps_pair[0] is not None: hashes["gap_backlog_sha256"]=hashlib.sha256(gaps_pair[0]).hexdigest()
            result=assess_project_quality(sem_doc,graph_doc,plan_pair[1],matrix_pair[1],gaps_pair[1],hashes)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            if markdown_output is not None:
                markdown_output.parent.mkdir(parents=True,exist_ok=True)
                markdown_output.write_text(render_project_quality_markdown(result),encoding='utf-8')
            summary={"schema_version":result["schema_version"],"assessment_mode":result["assessment_mode"],
                     "inventory":result["inventory"],"dimensions":result["dimensions"],
                     "evidence_integrity":result["evidence_integrity"],"result_path":str(output),
                     "markdown_result_path":str(markdown_output) if markdown_output else None}
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='analyze-gaps':
            import hashlib
            def read_gap_json(path, limit):
                source=Path(path).expanduser()
                if source.is_symlink(): raise ValueError("input must not be a symbolic link")
                resolved=source.resolve(strict=True)
                if not resolved.is_file(): raise ValueError("input must be a regular file")
                raw=resolved.read_bytes()
                if len(raw)>limit: raise ValueError("gap analysis input exceeds size limit")
                return raw,json.loads(raw)
            sem_bytes,sem_doc=read_gap_json(a.semantics,20*1024*1024)
            graph_bytes,graph_doc=read_gap_json(a.graph,50*1024*1024)
            plan_pair=read_gap_json(a.test_plan,20*1024*1024) if a.test_plan else (None,None)
            matrix_pair=read_gap_json(a.evidence_matrix,50*1024*1024) if a.evidence_matrix else (None,None)
            trace_pair=read_gap_json(a.traceability,50*1024*1024) if a.traceability else (None,None)
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("gap backlog output already exists; choose a new path to preserve prior evidence")
            hashes={"business_semantics_sha256":hashlib.sha256(sem_bytes).hexdigest(),
                    "business_graph_sha256":hashlib.sha256(graph_bytes).hexdigest()}
            if plan_pair[0] is not None: hashes["test_plan_sha256"]=hashlib.sha256(plan_pair[0]).hexdigest()
            if matrix_pair[0] is not None: hashes["evidence_matrix_sha256"]=hashlib.sha256(matrix_pair[0]).hexdigest()
            if trace_pair[0] is not None: hashes["traceability_matrix_sha256"]=hashlib.sha256(trace_pair[0]).hexdigest()
            result=build_verification_gap_backlog(
                sem_doc,graph_doc,plan_pair[1],matrix_pair[1],hashes,trace_pair[1]
            )
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            summary={key:result[key] for key in ("schema_version","business_function_count","gap_count","gap_kind_counts","priority_counts")}
            summary["result_path"]=str(output)
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='adapt-android-events':
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("Android adapter output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("Android adapter output already exists; choose a new path to preserve prior evidence")
            result=adapt_android_jsonl_file(a.input,a.graph,a.session_id,a.captured_at)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({"schema_version":result["schema_version"],"event_count":len(result["events"]),
                "session_id":result["session"]["session_id"],"platform":"ANDROID","result_path":str(output)},ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='validate-runtime-evidence':
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("runtime validation output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("runtime validation output already exists; choose a new path to preserve prior evidence")
            result=validate_runtime_evidence_files(a.events,a.graph)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            summary={key:result[key] for key in ("schema_version","assessment_status","event_count","correlation_count","business_function_observation_count","evidence_conflict_count")}
            summary["result_path"]=str(output)
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0 if result["evidence_conflict_count"]==0 else 1
        if a.cmd=='verify-evidence':
            import hashlib
            def read_evidence_json(path, limit):
                source=Path(path).expanduser()
                if source.is_symlink(): raise ValueError("input must not be a symbolic link")
                resolved=source.resolve(strict=True)
                if not resolved.is_file(): raise ValueError("input must be a regular file")
                raw=resolved.read_bytes()
                if len(raw)>limit: raise ValueError("evidence input exceeds size limit")
                return raw, json.loads(raw)
            graph_bytes, graph_doc=read_evidence_json(a.graph,50*1024*1024)
            test_doc=read_evidence_json(a.test_results,20*1024*1024)[1] if a.test_results else None
            coverage_doc=read_evidence_json(a.coverage_results,50*1024*1024)[1] if a.coverage_results else None
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("evidence output already exists; choose a new path to preserve prior evidence")
            result=verify_business_function_evidence(graph_doc,test_doc,coverage_doc,hashlib.sha256(graph_bytes).hexdigest())
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            summary={key:result[key] for key in ("schema_version","business_function_count","verification_conclusion_counts","evidence_conflict_count")}
            summary["result_path"]=str(output)
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0 if result["evidence_conflict_count"]==0 else 1
        if a.cmd=='ingest-coverage':
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError("output must not be a symbolic link")
            output=output.resolve()
            if output.exists(): raise ValueError("coverage output already exists; choose a new path to preserve prior evidence")
            result=ingest_coverage_results(a.coverage,a.mapping,a.graph,a.format,a.project_root)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            summary={key:result[key] for key in ("schema_version","source_format","business_function_count","coverage_status_counts")}
            summary["result_path"]=str(output)
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0
        if a.cmd=='run-pytest':
            if a.timeout <= 0 or a.timeout > 24*60*60:
                raise ValueError('timeout must be greater than zero and no more than 24 hours')
            result=run_controlled_pytest(
                a.project,evidence_parent=a.evidence_parent,
                authorization_file=a.authorization_file,pytest_args=a.pytest_arg,
                timeout=a.timeout,
            )
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result.get('status')=='PASS' else 1
        if a.cmd=='ingest-test-results':
            output=Path(a.out).expanduser()
            if output.is_symlink(): raise ValueError('output must not be a symbolic link')
            output=output.resolve()
            if output.exists(): raise ValueError('test-result output already exists; choose a new path to preserve prior evidence')
            result=ingest_junit_results(
                a.junit,a.mapping,a.graph,a.format,a.project_root,
                provenance_record_path=a.provenance_record,
                source_manifest_path=a.source_manifest,
            )
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            summary={key:result[key] for key in ('schema_version','test_case_count','test_case_status_counts','unmapped_test_case_count','business_function_count','business_function_status_counts')}
            summary['result_path']=str(output)
            summary['code_coverage']='NOT_MEASURED'
            provenance=result.get('execution_provenance')
            summary['execution_provenance_status']=(
                provenance.get('record_validation_status','NOT_ESTABLISHED')
                if isinstance(provenance,dict) else 'NOT_PROVIDED'
            )
            summary['provenance_authenticity_status']=(
                provenance.get('provenance_authenticity_status','UNAUTHENTICATED')
                if isinstance(provenance,dict) else 'NOT_PROVIDED'
            )
            print(json.dumps(summary,ensure_ascii=False,indent=2))
            return 0
        auth=load_authorization_file(a.authorization_file,a.project,a.out)
        print(json.dumps(scan_project(a.project,a.out,force=getattr(a,'force',False),authorization=auth),ensure_ascii=False,indent=2)); return 0
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}',file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
