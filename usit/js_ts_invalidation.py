"""Deterministic invalidation checks for static JS/TS source associations."""
from __future__ import annotations
import hashlib, shutil, tempfile
from pathlib import Path
from typing import Any
from .js_ts_parser import analyze_js_ts_project
from .scanner import discover_files

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "javascript_typescript"


def _scan(root: Path) -> dict[str, Any]:
    return analyze_js_ts_project(root, discover_files(root))


def _call(result: dict[str, Any], path: str, callee: str) -> dict[str, Any] | None:
    row = result.get("files", {}).get(path, {})
    return next((item for item in row.get("calls", []) if item.get("callee") == callee), None)


def _sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _actual(call: dict[str, Any] | None, *, path: str | None = None, file_present: bool = True) -> dict[str, Any]:
    if call is None:
        return {"file_present": file_present, "status": None, "resolved": False, "target": None,
                "diagnostic": "CALL_NOT_FOUND" if file_present else "TEST_FILE_ABSENT", "evidence": []}
    source_path = call.get("path") or path
    chain = list(call.get("evidence_chain") or [])
    if not chain:
        chain.append({"kind": "call", "file": source_path, "line": call.get("line"), "column": call.get("column")})
        imported = call.get("import_evidence")
        if imported:
            chain.append({"kind": "import", **imported})
        target = call.get("target") or {}
        if target.get("file"):
            chain.append({"kind": "definition", "file": target.get("file"), "line": target.get("line"), "name": target.get("name")})
    status = call.get("resolution_status")
    return {
        "file_present": file_present,
        "status": status,
        "resolved": bool(call.get("resolved", status == "RESOLVED")),
        "target": call.get("target"),
        "diagnostic": call.get("diagnostic"),
        "source_location": {"path": source_path, "line": call.get("line"), "column": call.get("column")},
        "import_evidence": call.get("import_evidence"),
        "evidence_chain": chain,
        "evidence": chain,
    }


def _target_is(actual: dict[str, Any], file: str, name: str, line: int) -> bool:
    target = actual.get("target") or {}
    return (target.get("file"), target.get("name"), target.get("line")) == (file, name, line)


def run_js_ts_invalidation_benchmark(fixture_root: Path = FIXTURE) -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="usit-js-invalidation-") as td:
        root = Path(td) / "project"
        shutil.copytree(Path(fixture_root), root)
        before = _scan(root)
        baseline_call = _call(before, "src/main.ts", "renamed")
        baseline_actual = _actual(baseline_call, path="src/main.ts")
        baseline_ok = (baseline_actual["status"] == "RESOLVED" and
                       _target_is(baseline_actual, "src/a.ts", "named", 1))
        scenarios.append({
            "scenario": "baseline-link",
            "input_condition": "Unmodified fixture; src/main.ts imports named from ./a as renamed and calls renamed().",
            "changed_files": [],
            "expected": "RESOLVED:src/a.ts",
            "expected_result": {"status": "RESOLVED", "target": {"file": "src/a.ts", "name": "named", "line": 1}},
            "actual": baseline_actual,
            "evidence": baseline_actual.get("evidence", []),
            "passed": baseline_ok,
        })
        actions = [
            ("target-deleted", "Delete src/a.ts, the imported target module.", "src/a.ts",
             lambda r: (r / "src/a.ts").unlink(), "src/main.ts", "renamed"),
            ("target-renamed", "Replace the exported function named with renamedTarget in src/a.ts.", "src/a.ts",
             lambda r: (r / "src/a.ts").write_text("export function renamedTarget(){ return 1; }\n", encoding="utf-8"), "src/main.ts", "renamed"),
            ("target-moved", "Move src/a.ts to src/moved-a.ts without updating imports.", "src/a.ts",
             lambda r: (r / "src/a.ts").rename(r / "src/moved-a.ts"), "src/main.ts", "renamed"),
            ("import-alias-changed", "Change import alias renamed to changedAlias but keep the old renamed() call.", "src/main.ts",
             lambda r: (r / "src/main.ts").write_text((r / "src/main.ts").read_text(encoding="utf-8").replace("named as renamed", "named as changedAlias"), encoding="utf-8"), "src/main.ts", "renamed"),
            ("same-name-added-explicit-import", "Add a second project function named, while src/main.ts explicitly imports ./a.", "src/duplicate.ts",
             lambda r: (r / "src/duplicate.ts").write_text("export function named(){ return 'other'; }\n", encoding="utf-8"), "src/main.ts", "renamed"),
            ("test-file-deleted", "Delete src/__tests__/test_main.ts and verify it disappears from the fresh source inventory.", "src/__tests__/test_main.ts",
             lambda r: (r / "src/__tests__/test_main.ts").unlink(), "src/__tests__/test_main.ts", ""),
            ("paths-mapping-broken", "Change the @app/* tsconfig mapping from src/modules/* to src/no-such-module/*.", "tsconfig.json",
             lambda r: (r / "tsconfig.json").write_text((r / "tsconfig.json").read_text(encoding="utf-8").replace("src/modules/*", "src/no-such-module/*"), encoding="utf-8"), "src/main.ts", "aliased"),
        ]
        for name, condition, changed_rel, action, call_path, callee in actions:
            case = Path(td) / name
            shutil.copytree(root, case)
            changed = case / changed_rel
            before_sha = _sha(changed)
            action(case)
            after_sha = _sha(changed)
            result = _scan(case)
            if name == "test-file-deleted":
                present = "src/__tests__/test_main.ts" in result.get("files", {})
                call = None
                actual = _actual(call, path=call_path, file_present=present)
                passed = not present
                expected = "test source absent after fresh scan"
                expected_result = {"file_present": False, "status": None}
            else:
                call = _call(result, call_path, callee)
                actual = _actual(call, path=call_path)
                if name == "same-name-added-explicit-import":
                    passed = actual["status"] == "RESOLVED" and _target_is(actual, "src/a.ts", "named", 1)
                    expected = "still uniquely resolved to explicitly imported src/a.ts"
                    expected_result = {"status": "RESOLVED", "target": {"file": "src/a.ts", "name": "named", "line": 1}}
                elif name == "paths-mapping-broken":
                    passed = actual["status"] != "RESOLVED" and actual["resolved"] is False and actual.get("target") is None
                    expected = "path-mapped call is not RESOLVED"
                    expected_result = {"status": "NOT_RESOLVED", "target": None}
                else:
                    passed = actual["status"] != "RESOLVED" and actual["resolved"] is False
                    expected = "old link is not RESOLVED"
                    expected_result = {"status": "NOT_RESOLVED", "target": None}
            scenarios.append({
                "scenario": name,
                "input_condition": condition,
                "changed_files": [{"path": changed_rel, "sha256_before": before_sha, "sha256_after": after_sha}],
                "expected": expected,
                "expected_result": expected_result,
                "actual": actual,
                "evidence": actual.get("evidence", []),
                "passed": passed,
            })
    return {
        "schema_version": "usit-js-ts-invalidation-v1",
        "execution_policy": "STATIC_ONLY_NO_FIXTURE_EXECUTION",
        "status": "PASS" if all(x["passed"] for x in scenarios) else "FAIL",
        "scenarios": scenarios,
        "scenario_total": len(scenarios),
        "passed_total": sum(x["passed"] for x in scenarios),
        "limitations": [
            "Does not establish live watch-mode cache behavior; each scenario is a fresh scan.",
            "Test presence does not establish command execution or behavioral coverage.",
        ],
    }
