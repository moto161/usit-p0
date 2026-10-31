"""Run checked-in parser fixtures without executing fixture source code."""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .parser_contract import parser_capabilities
from .python_resolver import resolve_python_calls
from .js_ts_parser import analyze_js_ts_project
from .scanner import analyze_file, discover_files

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
PYTHON_STATES = {"RESOLVED", "UNRESOLVED", "AMBIGUOUS", "DYNAMIC", "EXTERNAL", "UNKNOWN"}


def _call_key(item: dict[str, Any]) -> tuple[str, int, str]:
    return item["path"], item["line"], item["callee"]


def _call_detail(path: str, call: dict[str, Any]) -> dict[str, Any]:
    return {"path": path, "line": call.get("line"), "column": call.get("column"),
            "callee": call.get("callee"), "status": call.get("resolution_status", "UNKNOWN"),
            "resolved": call.get("resolved", False), "target": call.get("target"),
            "import_evidence": call.get("import_evidence"),
            "candidate_targets": call.get("candidate_targets"),
            "evidence_chain": call.get("evidence_chain"), "diagnostic": call.get("diagnostic")}


def _run_fixture(name: str) -> dict[str, Any]:
    fixture = FIXTURE_ROOT / name
    expected = json.loads((fixture / "expected.json").read_text(encoding="utf-8"))
    source_root = fixture if name in {"python_cross_module", "javascript_typescript"} else fixture / "src"
    with tempfile.TemporaryDirectory(prefix="usit-fixture-") as td:
        # Resolve so the copied root shares the canonical (long) form that
        # ``discover_files`` returns; a short-named temp directory would make
        # ``relative_to`` fail for reasons unrelated to the parser under test.
        root = (Path(td) / "project").resolve()
        shutil.copytree(source_root, root)
        started = time.perf_counter()
        paths = discover_files(root)
        rows = [analyze_file(root, path) for path in paths
                if path.suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".java", ".kt", ".kts"}]
        if name == "javascript_typescript":
            parsed = analyze_js_ts_project(root, paths)
            for row in rows:
                data = parsed.get("files", {}).get(row["path"])
                if data:
                    row.update({key: data.get(key, row.get(key)) for key in ("symbols", "imports", "calls", "syntax_status")})
                    row["analysis_diagnostics"] = data.get("diagnostics", [])
            resolver = {"counts": {state: sum(c.get("resolution_status") == state for r in rows for c in r.get("calls", [])) for state in PYTHON_STATES}, "files_with_parse_errors": [r["path"] for r in rows if r.get("syntax_status") == "INVALID"]}
        else:
            resolver = resolve_python_calls(str(root), rows)
        elapsed_ms = (time.perf_counter() - started) * 1000
    language = "Python" if name == "python_cross_module" else (
        "JavaScript/TypeScript" if name == "javascript_typescript" else "Java/Kotlin")
    calls = [_call_detail(row["path"], call) for row in rows for call in row.get("calls", [])]
    predictions = {(c["path"], c["line"], c["callee"], c["target"]["file"], c["target"]["name"], c["target"]["line"])
                   for c in calls if c["status"] == "RESOLVED" and c.get("target")}
    unknown_candidates = [c for c in calls if c["status"] in {"UNKNOWN", "AMBIGUOUS", "DYNAMIC"}]

    if name == "python_cross_module":
        expected_relations = expected["expected_relations"]
        actual_by_key = {_call_key(c): c for c in calls}
        expected_keys = {_call_key(x) for x in expected_relations}
        comparison: list[dict[str, Any]] = []
        state_counts = {state: {"expected": 0, "matched": 0} for state in sorted(PYTHON_STATES)}
        false_resolved: list[dict[str, Any]] = []
        for truth in expected_relations:
            key = _call_key(truth)
            actual = actual_by_key.get(key)
            state = truth["status"]
            state_counts[state]["expected"] += 1
            checks: dict[str, bool] = {"present": actual is not None}
            if actual is not None:
                checks["status"] = actual["status"] == state
                checks["resolved_boolean"] = actual["resolved"] is (state == "RESOLVED")
                checks["column"] = actual["column"] == truth["column"]
                checks["import_evidence"] = actual.get("import_evidence") == truth.get("import_evidence")
                checks["candidate_targets"] = actual.get("candidate_targets") == truth.get("candidate_targets")
                actual_target = actual.get("target")
                if truth.get("target") is None:
                    checks["target"] = actual_target is None
                else:
                    checks["target"] = actual_target is not None and all(
                        actual_target.get(field) == truth["target"].get(field)
                        for field in ("file", "name", "line", "id"))
                matched = all(checks.values())
                if matched:
                    state_counts[state]["matched"] += 1
                if state != "RESOLVED" and actual["status"] == "RESOLVED":
                    false_resolved.append({"expected": truth, "actual": actual, "reason": "non-RESOLVED truth predicted as RESOLVED"})
            else:
                matched = False
            comparison.append({"expected": truth, "actual": actual, "checks": checks, "matched": matched})

        truth_resolved = {(x["path"], x["line"], x["callee"], x["target"]["file"],
                           x["target"]["name"], x["target"]["line"])
                          for x in expected_relations if x["status"] == "RESOLVED"}
        tp = len(predictions & truth_resolved)
        fp_items = sorted(predictions - truth_resolved)
        fn_items = sorted(truth_resolved - predictions)
        fp, fn = len(fp_items), len(fn_items)
        precision_denominator = tp + fp
        recall_denominator = tp + fn
        precision = tp / precision_denominator if precision_denominator else None
        recall = tp / recall_denominator if recall_denominator else None
        expected_keys_status = {(_call_key(x), x["status"]) for x in expected_relations}
        unexpected_calls = [c for c in calls if (_call_key(c), c["status"]) not in expected_keys_status]

        expected_parse_errors = expected["expected_parse_errors"]
        actual_parse_errors = [{"path": row["path"], **d} for row in rows
                               for d in row.get("analysis_diagnostics", []) if d.get("code") == "PYTHON_SYNTAX_ERROR"]
        parse_error_comparison = [{"expected": item, "matched": any(
            actual.get("path") == item["path"] and actual.get("code") == item["code"]
            and actual.get("line") == item["line"] for actual in actual_parse_errors)}
            for item in expected_parse_errors]
        indexed_file_errors = resolver.get("files_with_parse_errors", [])
        parse_error_comparison.append({"expected_files_with_parse_errors": expected["expected_files_with_parse_errors"],
                                       "actual_files_with_parse_errors": indexed_file_errors,
                                       "matched": indexed_file_errors == expected["expected_files_with_parse_errors"]})
        parse_error_comparison.extend([
            {"expected_modules_indexed": expected["expected_modules_indexed"],
             "actual_modules_indexed": resolver.get("modules_indexed"),
             "matched": resolver.get("modules_indexed") == expected["expected_modules_indexed"]},
            {"expected_symbols_indexed": expected["expected_symbols_indexed"],
             "actual_symbols_indexed": resolver.get("symbols_indexed"),
             "matched": resolver.get("symbols_indexed") == expected["expected_symbols_indexed"]},
        ])
        expected_nonresolved = [x for x in expected_relations if x["status"] != "RESOLVED"]
        expected_nonresolved_count = len(expected_nonresolved)
        state_correct = sum(row["matched"] for row in comparison if row["expected"]["status"] != "RESOLVED")
        classification_denominator = len(expected_relations)
        classification_hits = sum(row["matched"] for row in comparison)
        unmeasured = ["general Python ecosystem accuracy", "unmodeled syntax/runtime behavior"]
        all_checks_pass = (all(x["matched"] for x in comparison)
                           and all(x["matched"] for x in parse_error_comparison)
                           and not false_resolved and not fp_items and not fn_items and not unexpected_calls)
        result = {"status": "PASS" if all_checks_pass else "FAIL",
            "expected_relation_total": len(expected_relations), "expected_status_counts": state_counts,
            "relation_classification_hits": classification_hits,
            "relation_classification_total": classification_denominator,
            "relation_classification_accuracy": classification_hits / classification_denominator if classification_denominator else None,
            "nonresolved_state_hits": state_correct, "nonresolved_state_total": expected_nonresolved_count,
            "nonresolved_state_classification_accuracy": state_correct / expected_nonresolved_count if expected_nonresolved_count else None,
            "TP": tp, "FP": fp, "FN": fn, "precision_denominator": precision_denominator,
            "recall_denominator": recall_denominator, "precision": precision, "recall": recall,
            "false_resolved_nonresolved_count": len(false_resolved), "false_resolved_nonresolved": false_resolved,
            "resolved_false_positive_examples": fp_items, "resolved_false_negative_examples": fn_items,
            "unexpected_or_misclassified_calls": unexpected_calls, "relation_results": comparison,
            "parse_error_count": len(actual_parse_errors), "expected_parse_error_count": len(expected_parse_errors),
            "parse_errors": actual_parse_errors, "parse_error_checks": parse_error_comparison,
            "unknown_ambiguous_dynamic_candidate_count": len(unknown_candidates),
            "unmeasured_capabilities": unmeasured}
    elif name == "javascript_typescript":
        expected_relations = expected["expected_relations"]
        actual_by_key = {(c["path"], c["line"], c["column"], c["callee"]): c for c in calls}
        comparison = []
        false_resolved = []
        for truth in expected_relations:
            key = (truth["path"], truth["line"], truth["column"], truth["callee"])
            actual = actual_by_key.get(key)
            checks = {"present": actual is not None}
            if actual is not None:
                checks["status"] = actual["status"] == truth["status"]
                checks["resolved_boolean"] = actual["resolved"] is (truth["status"] == "RESOLVED")
                checks["diagnostic"] = not truth.get("diagnostic") or actual.get("diagnostic") == truth["diagnostic"]
                if "import_evidence" in truth:
                    checks["import_evidence"] = actual.get("import_evidence") == truth["import_evidence"]
                if truth.get("target"):
                    checks["target"] = actual.get("target") is not None and all(actual["target"].get(k) == v for k, v in truth["target"].items())
                if truth.get("candidate_targets"):
                    checks["candidate_targets"] = actual.get("candidate_targets") == truth["candidate_targets"]
                if truth.get("evidence_kinds"):
                    kinds = [x.get("kind") for x in (actual.get("evidence_chain") or [])]
                    checks["evidence"] = all(kind in kinds for kind in truth["evidence_kinds"])
                if truth["status"] != "RESOLVED" and actual["status"] == "RESOLVED":
                    false_resolved.append({"expected": truth, "actual": actual})
            comparison.append({"expected": truth, "actual": actual, "checks": checks, "matched": all(checks.values())})
        truth_resolved = {(x["path"],x["line"],x["column"],x["callee"],x["target"]["file"],x["target"]["name"],x["target"]["line"]) for x in expected_relations if x["status"] == "RESOLVED"}
        predictions = {(c["path"],c["line"],c["column"],c["callee"],c["target"]["file"],c["target"]["name"],c["target"]["line"]) for c in calls if c["status"] == "RESOLVED" and c.get("target")}
        tp = len(truth_resolved & predictions); fp_items = sorted(predictions-truth_resolved); fn_items = sorted(truth_resolved-predictions)
        fp=len(fp_items); fn=len(fn_items); precision_denominator=tp+fp; recall_denominator=tp+fn
        syntax_errors=[{"path":r["path"],**d} for r in rows for d in r.get("analysis_diagnostics",[]) if d.get("code","").startswith("TYPESCRIPT_SYNTAX_ERROR_")]
        expected_parse=expected["expected_parse_errors"]
        parse_checks=[{"expected":e,"matched":any(a["path"]==e["path"] and a["code"].startswith(e["code_prefix"]) and a.get("line")==e["line"] for a in syntax_errors)} for e in expected_parse]
        expected_keys = {(x["path"],x["line"],x["column"],x["callee"]) for x in expected_relations}
        unexpected_calls = [c for c in calls if (c["path"],c["line"],c["column"],c["callee"]) not in expected_keys]
        classification_hits = sum(x["matched"] for x in comparison)
        nonresolved_rows = [x for x in comparison if x["expected"]["status"] != "RESOLVED"]
        nonresolved_hits = sum(x["matched"] for x in nonresolved_rows)
        expected_state_counts = {state: sum(x["status"] == state for x in expected_relations) for state in sorted(PYTHON_STATES)}
        actual_state_counts = {state: sum(x["status"] == state for x in calls) for state in sorted(PYTHON_STATES)}
        all_pass=all(x["matched"] for x in comparison) and all(x["matched"] for x in parse_checks) and not fp_items and not fn_items and not false_resolved and not unexpected_calls
        result={"status":"PASS" if all_pass else "FAIL","known_truth_total":len(expected_relations),"prediction_call_total":len(calls),"TP":tp,"FP":fp,"FN":fn,"precision_denominator":precision_denominator,"recall_denominator":recall_denominator,"precision":tp/precision_denominator if precision_denominator else None,"recall":tp/recall_denominator if recall_denominator else None,"relation_classification_hits":classification_hits,"relation_classification_total":len(comparison),"relation_classification_accuracy":classification_hits/len(comparison) if comparison else None,"nonresolved_state_hits":nonresolved_hits,"nonresolved_state_total":len(nonresolved_rows),"nonresolved_state_classification_accuracy":nonresolved_hits/len(nonresolved_rows) if nonresolved_rows else None,"expected_status_counts":expected_state_counts,"actual_status_counts":actual_state_counts,"false_resolved_nonresolved_count":len(false_resolved),"false_resolved_nonresolved":false_resolved,"resolved_false_positive_examples":fp_items,"resolved_false_negative_examples":fn_items,"unexpected_calls":unexpected_calls,"relation_results":comparison,"syntax_error_count":len(syntax_errors),"expected_parse_error_count":len(expected_parse),"parse_errors":syntax_errors,"parse_error_checks":parse_checks,"unmeasured_capabilities":["runtime behavior","framework test execution","production projects","stress and memory limits"]}
    else:
        expected_unknown = {(x["path"], x["callee"], x["line"]) for x in expected["must_remain_unknown"]}
        observed_unknown = {(c["path"], c["callee"], c["line"]) for c in unknown_candidates}
        bad_syntax_file_count = None
        result = {"status": "NOT_MEASURED", "known_truth_total": 0, "predicted_total": len(predictions),
                  "TP": 0, "FP": 0, "FN": 0, "precision_denominator": 0, "recall_denominator": 0,
                  "precision": None, "recall": None, "expected_unknown_candidates": len(expected_unknown),
                  "expected_unknown_candidates_observed": len(expected_unknown & observed_unknown),
                  "false_resolved_nonresolved_count": 0, "false_resolved_nonresolved": [],
                  "syntax_error_count": bad_syntax_file_count, "syntax_validation_status": "NOT_MEASURED",
                  "unmeasured_capabilities": ["AST semantics", "module resolution", "call resolution", "syntax validation"]}
    return {"language": language, "fixture": str(fixture),
            "capability": parser_capabilities()["languages"].get(language, {
                "level": "DISCOVERY_ONLY", "supported": ["file discovery"],
                "unsupported": ["semantic parsing"], "degradation": "All relations remain UNKNOWN."}),
            "files": len(rows), "symbols": sum(len(r.get("symbols", [])) for r in rows),
            "imports": sum(len(r.get("imports", [])) for r in rows), "call_candidates": len(calls),
            "resolution_state_counts": resolver.get("counts", {}) if name == "python_cross_module" else {
                state: sum(c["status"] == state for c in calls) for state in PYTHON_STATES},
            "elapsed_ms": round(elapsed_ms, 3), **result}


def run_fixture_benchmark() -> dict[str, Any]:
    return {"schema_version": "usit-fixture-benchmark-v2", "execution_policy": "STATIC_ONLY_NO_FIXTURE_EXECUTION",
            "languages": [_run_fixture(name) for name in ("python_cross_module", "javascript_typescript", "java_kotlin")],
            "metric_definitions": {
                "TP": "predicted RESOLVED call tuple exactly matches an expected RESOLVED tuple",
                "FP": "predicted RESOLVED call tuple absent from the expected RESOLVED set",
                "FN": "expected RESOLVED call tuple absent from the predicted RESOLVED set",
                "precision": "TP/(TP+FP); denominator and value are explicit; null when denominator is zero",
                "recall": "TP/(TP+FN); denominator and value are explicit; null when denominator is zero",
                "nonresolved_state_classification": "exact expected-vs-actual status match over explicitly enumerated non-RESOLVED candidates",
                "false_resolved_nonresolved": "expected UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED but observed RESOLVED; detailed rows emitted",
                "source_evidence": "compares file, line, column, target definition, import evidence, candidates, and legacy resolved boolean",
                "generalization": "fixture scores do not establish accuracy for the language ecosystem"}}
