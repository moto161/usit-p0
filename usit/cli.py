from __future__ import annotations
import argparse, json, os, secrets, sys, tempfile
from pathlib import Path
from .scanner import scan_project
from .safety import load_authorization_file, authorization_record, canonical_project, canonical_output
from .graph import validate_graph_file
from .benchmark import run_fixture_benchmark
from .association_benchmark import run_association_invalidation_benchmark
from .android_adapter import AndroidProjectAdapter
from .command_planner import CommandPlanner

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

def _write_exclusive_bytes(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while creating Android output")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(fd)


def _write_json_output(path: Path, value, *, overwrite: bool) -> None:
    """Write one JSON artifact without following an existing output symlink."""
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if not overwrite:
        _write_exclusive_bytes(path, payload)
        return

    # Atomic replacement replaces a symlink itself rather than opening its target.
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    _write_exclusive_bytes(temporary, payload)
    try:
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _inspect_android(a) -> int:
    """Read-only Android inspection: static profile + command candidates.

    Never executes Gradle/ADB or any test/build command.  Writes two JSON files
    (android_profile.json, android_test_plan.json) and prints a summary.
    """
    auth = load_authorization_file(a.authorization_file, a.project, a.out)
    if a.force and "overwrite_existing_output" not in auth.allowed_actions:
        raise ValueError("--force requires explicit overwrite_existing_output authorization.")
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
    _write_json_output(out / "android_profile.json", profile, overwrite=a.force)
    _write_json_output(out / "android_test_plan.json", plan, overwrite=a.force)
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
    v.add_argument('--allow-untrusted-embedded-line-counts', action='store_true', help='Explicitly allow line-range checks to rely on editable source_line_counts embedded in the graph (not a trust anchor)')
    # P0.7.13-F1: read-only Android structural inspection + command-candidate
    # generation.  The name "inspect" is deliberate — this command NEVER executes
    # Gradle, ADB, or any test/build command; it only reads project files and
    # emits structured candidates whose execution_status is NOT_EXECUTED.
    ins=sub.add_parser('inspect-android',help='Inspect an Android/Gradle project statically and emit test-command candidates (read-only; no execution)')
    ins.add_argument('project',help='Authorized absolute project root to inspect')
    ins.add_argument('--out',required=True,help='Output directory for android_profile.json and android_test_plan.json (must not overlap the project)')
    ins.add_argument('--authorization-file',required=True,help='External JSON authorization record bound to project, action and output')
    ins.add_argument('--force',action='store_true',help='Overwrite an existing, non-empty output directory')
    a=parser.parse_args(argv)
    try:
        if a.cmd=='selftest': return selftest()
        if a.cmd=='benchmark-fixtures':
            output=Path(a.out).expanduser().resolve()
            if output.exists(): raise ValueError('benchmark output already exists; choose a new path to avoid overwriting')
            output.parent.mkdir(parents=True,exist_ok=True)
            result=run_fixture_benchmark()
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'result_path':str(output),'languages':result['languages']},ensure_ascii=False,indent=2))
            return 0 if all(x['false_resolved_nonresolved_count']==0 for x in result['languages']) else 1
        if a.cmd=='benchmark-associations':
            output=Path(a.out).expanduser().resolve()
            if output.exists(): raise ValueError('benchmark output already exists; choose a new path to avoid overwriting')
            output.parent.mkdir(parents=True,exist_ok=True)
            result=run_association_invalidation_benchmark()
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
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
            result=validate_graph_file(graph_path,project_root,json_out,md_out,require_trusted=not a.allow_untrusted_embedded_line_counts)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result['is_valid'] else 1
        if a.cmd=='inspect-android':
            return _inspect_android(a)
        auth=load_authorization_file(a.authorization_file,a.project,a.out)
        print(json.dumps(scan_project(a.project,a.out,force=getattr(a,'force',False),authorization=auth),ensure_ascii=False,indent=2)); return 0
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}',file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
