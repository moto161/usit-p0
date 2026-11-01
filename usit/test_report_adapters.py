"""Conservative adapters for common test and code-coverage reports.
All parsers consume completed reports only. Coverage linkage requires an explicit
reviewed source-file + line-range mapping; no path/name guessing.
"""
from __future__ import annotations
import json
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Any

def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_file must be a non-empty relative path")
    path = value.replace("\\", "/")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("source_file must be a normalized relative path")
    return pure.as_posix()

def parse_pytest_json(data: bytes) -> list[dict[str, Any]]:
    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid pytest JSON report: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("tests"), list):
        raise ValueError("pytest JSON must contain a tests list")
    cases = []
    for index, item in enumerate(doc["tests"]):
        if not isinstance(item, dict):
            raise ValueError(f"pytest test entry {index} must be an object")
        nodeid, outcome = item.get("nodeid"), item.get("outcome")
        if not isinstance(nodeid, str) or not nodeid.strip():
            raise ValueError(f"pytest test entry {index} has no nodeid")
        if outcome not in {"passed", "failed", "skipped"}:
            raise ValueError(f"pytest test {nodeid} has unsupported outcome")
        cases.append({"test_case_id": nodeid.strip(), "classname": None, "name": nodeid.strip(),
            "status": {"passed":"PASSED","failed":"FAILED","skipped":"SKIPPED"}[outcome],
            "failure_count": int(outcome == "failed"), "error_count": 0,
            "skipped_count": int(outcome == "skipped"), "source_format":"PYTEST_JSON"})
    if not cases:
        raise ValueError("pytest JSON report contains no tests")
    ids = [x["test_case_id"] for x in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate pytest nodeid values are ambiguous")
    return sorted(cases, key=lambda x:x["test_case_id"])

def parse_coverage_py_json(data: bytes) -> dict[str, dict[str, Any]]:
    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid coverage.py JSON: {exc}") from exc
    files = doc.get("files") if isinstance(doc, dict) else None
    if not isinstance(files, dict):
        raise ValueError("coverage.py JSON must contain a files object")
    result = {}
    for raw_path, item in files.items():
        path = _safe_relative_path(raw_path)
        if not isinstance(item, dict):
            raise ValueError(f"coverage entry for {path} must be an object")
        executed, missing = item.get("executed_lines"), item.get("missing_lines")
        if not isinstance(executed, list) or not isinstance(missing, list):
            raise ValueError(f"coverage entry for {path} lacks executed_lines/missing_lines")
        lines = executed + missing
        if any(not isinstance(n,int) or isinstance(n,bool) or n < 1 for n in lines):
            raise ValueError(f"coverage entry for {path} contains invalid line number")
        if path in result:
            raise ValueError(f"duplicate normalized coverage path: {path}")
        result[path] = {"executable_lines":sorted(set(lines)), "executed_lines":sorted(set(executed)),
            "missing_lines":sorted(set(missing)), "source_format":"COVERAGE_PY_JSON"}
    return result

def parse_jacoco_xml(data: bytes) -> dict[str, dict[str, Any]]:
    if b"<!DOCTYPE" in data[:1000000].upper() or b"<!ENTITY" in data[:1000000].upper():
        raise ValueError("JaCoCo XML with DTD/entity declarations is not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"invalid JaCoCo XML: {exc}") from exc
    if root.tag != "report":
        raise ValueError("JaCoCo XML root must be report")
    result = {}
    for package in root.findall(".//package"):
        pkg = package.attrib.get("name","").replace(".","/").strip("/")
        for source in package.findall("sourcefile"):
            name = source.attrib.get("name")
            path = _safe_relative_path(f"{pkg}/{name}" if pkg else name)
            executed, missing = [], []
            for line in source.findall("line"):
                try:
                    nr, ci, mi = int(line.attrib["nr"]), int(line.attrib["ci"]), int(line.attrib["mi"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"JaCoCo line data is invalid for {path}") from exc
                if nr < 1 or ci < 0 or mi < 0:
                    raise ValueError(f"JaCoCo line data is out of range for {path}")
                if ci + mi:
                    (executed if ci > 0 else missing).append(nr)
            if path in result:
                raise ValueError(f"duplicate JaCoCo source path: {path}")
            result[path] = {"executable_lines":sorted(set(executed+missing)),
                "executed_lines":sorted(set(executed)), "missing_lines":sorted(set(missing)),
                "source_format":"JACOCO_XML"}
    if not result:
        raise ValueError("JaCoCo report contains no sourcefile line counters")
    return result

def map_line_coverage_to_functions(coverage: dict[str, dict[str, Any]],
        mapping_doc: dict[str, Any], business_graph: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(mapping_doc,dict) or mapping_doc.get("schema_version") != "usit-coverage-mapping-v1":
        raise ValueError("coverage mapping schema_version must be usit-coverage-mapping-v1")
    mappings = mapping_doc.get("mappings")
    if not isinstance(mappings,list):
        raise ValueError("coverage mapping.mappings must be a list")
    understanding = business_graph.get("business_understanding") if isinstance(business_graph,dict) else None
    views = understanding.get("business_function_views") if isinstance(understanding,dict) else None
    if not isinstance(views,list):
        raise ValueError("business graph is missing business_function_views")
    known = {v.get("business_function_id") for v in views if isinstance(v,dict)}
    if not known:
        raise ValueError("business graph contains no business function views")
    linked, seen = {}, set()
    for i,item in enumerate(mappings):
        if not isinstance(item,dict):
            raise ValueError(f"coverage mapping entry {i} must be an object")
        fid, path = item.get("business_function_id"), _safe_relative_path(item.get("source_file"))
        start,end = item.get("line_start"),item.get("line_end")
        if fid not in known:
            raise ValueError(f"coverage mapping entry {i} references unknown business_function_id")
        if not isinstance(start,int) or isinstance(start,bool) or not isinstance(end,int) or isinstance(end,bool) or start<1 or end<start:
            raise ValueError(f"coverage mapping entry {i} has invalid line range")
        key=(fid,path,start,end)
        if key in seen:
            raise ValueError("duplicate coverage mapping range")
        seen.add(key); linked.setdefault(fid,[]).append({"source_file":path,"line_start":start,"line_end":end})
    results=[]
    for fid in sorted(known):
        ranges=linked.get(fid,[]); executable=set(); executed=set(); missing=set(); absent=[]
        for entry in ranges:
            record=coverage.get(entry["source_file"])
            if record is None:
                absent.append(entry["source_file"]); continue
            selected=set(range(entry["line_start"],entry["line_end"]+1))
            executable.update(selected & set(record["executable_lines"]))
            executed.update(selected & set(record["executed_lines"]))
            missing.update(selected & set(record["missing_lines"]))
        if not ranges or absent or not executable:
            status,percent="UNKNOWN",None
        else:
            status="COVERED" if executable <= executed else ("PARTIAL" if executed else "NOT_COVERED")
            percent=round(100*len(executed)/len(executable),2)
        results.append({"business_function_id":fid,"code_coverage_status":status,
            "line_coverage_percent":percent,"executable_line_count":len(executable),
            "covered_line_count":len(executed),"uncovered_line_count":len(missing),
            "mapping_ranges":ranges,"coverage_source_files_missing":sorted(set(absent)),
            "coverage_basis":"EXPLICIT_SOURCE_FILE_LINE_RANGE_MAPPING",
            "verification_status":"RUNTIME_COVERAGE_IMPORTED" if percent is not None else "NOT_MEASURED"})
    return results
