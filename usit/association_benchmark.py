"""Synthetic test-to-symbol invalidation checks; fixture code is never executed."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from .scanner import scan_project
from .safety import authorization_record

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "python_cross_module"


def _asset(root: Path, out: Path) -> dict[str, Any] | None:
    scan_project(str(root), str(out), force=True, authorization=authorization_record(root.resolve(), out.resolve()))
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return next((x for x in report["test_assets"]["test_source"] if x["path"] == "tests/test_api.py"), None)


def _association(asset: dict[str, Any] | None) -> dict[str, Any] | None:
    if not asset:
        return None
    return next((x for x in asset.get("associations", []) if x.get("test_name") == "test_public_api"), None)


def _scenario(name: str, mutate: Callable[[Path], None], expectation: Callable[[dict[str, Any] | None, dict[str, Any] | None], bool]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="usit-association-") as td:
        base = Path(td); root = base / "project"; out = base / "reports"
        shutil.copytree(FIXTURE, root)
        before = _association(_asset(root, out))
        mutate(root)
        after = _association(_asset(root, out))
        passed = expectation(before, after)
        return {"scenario": name, "status": "PASS" if passed else "FAIL",
                "previous": before, "current": after,
                "old_resolved_link_preserved": bool(after and before
                    and after.get("association_id") == before.get("association_id")
                    and after.get("resolution_status") == "RESOLVED"
                    and after.get("validity_state") == "CURRENT")}


def run_association_invalidation_benchmark() -> dict[str, Any]:
    scenarios = []
    scenarios.append(_scenario("target_function_deleted",
        lambda root: (root / "ns" / "api.py").write_text("def hidden_api():\n    return 2\n", encoding="utf-8"),
        lambda before, after: bool(before and after and after["association_id"] == before["association_id"]
            and after["resolution_status"] == "UNKNOWN" and after["validity_state"] == "INVALIDATED"
            and after["failure_reason"] == "TARGET_SYMBOL_MISSING")))
    scenarios.append(_scenario("target_function_renamed",
        lambda root: (root / "ns" / "api.py").write_text(
            (root / "ns" / "api.py").read_text(encoding="utf-8").replace("public_api", "renamed_api"), encoding="utf-8"),
        lambda before, after: bool(before and after and after["resolution_status"] == "UNKNOWN"
            and after["validity_state"] == "INVALIDATED" and after["failure_reason"] == "TARGET_SYMBOL_MISSING")))
    scenarios.append(_scenario("target_module_moved",
        lambda root: shutil.move(root / "ns" / "api.py", root / "ns" / "moved_api.py"),
        lambda before, after: bool(before and after and after["resolution_status"] == "UNKNOWN"
            and after["validity_state"] == "INVALIDATED" and after["failure_reason"] == "TARGET_MODULE_MISSING")))

    def change_alias(root: Path) -> None:
        (root / "tests" / "test_api.py").write_text(
            "from ns.api import public_api as selected\n\ndef test_public_api():\n    assert selected() == 1\n", encoding="utf-8")
    scenarios.append(_scenario("import_alias_changed_and_call_updated", change_alias,
        lambda before, after: bool(before and after and after["resolution_status"] == "RESOLVED"
            and after["validity_state"] == "CURRENT" and after["imported_as"] == "selected"
            and after["association_id"] != before["association_id"])))

    def break_alias_reference(root: Path) -> None:
        (root / "tests" / "test_api.py").write_text(
            "from ns.api import public_api as selected\n\ndef test_public_api():\n    assert public_api() == 1\n", encoding="utf-8")
    scenarios.append(_scenario("import_alias_changed_but_call_not_updated", break_alias_reference,
        lambda before, after: bool(before and after and after["resolution_status"] == "UNKNOWN"
            and after["validity_state"] == "INVALIDATED"
            and after["failure_reason"] == "NO_CALL_REFERENCE_TO_IMPORTED_ALIAS")))

    def add_duplicate(root: Path) -> None:
        package = root / "ns" / "api"; package.mkdir()
        (package / "__init__.py").write_text("def public_api():\n    return 2\n", encoding="utf-8")
    scenarios.append(_scenario("same_name_definition_added",
        add_duplicate,
        lambda before, after: bool(before and after and after["resolution_status"] == "AMBIGUOUS"
            and after["validity_state"] == "INVALIDATED" and after["failure_reason"] == "TARGET_SYMBOL_AMBIGUOUS"
            and len(after["target_file_candidates"]) == 2)))

    def delete_test_file(root: Path) -> None:
        (root / "tests" / "test_api.py").unlink()
    scenarios.append(_scenario("test_file_deleted", delete_test_file, lambda before, after: bool(before and after is None)))
    return {"schema_version": "usit-test-association-invalidation-v1",
            "execution_policy": "STATIC_ONLY_NO_FIXTURE_EXECUTION",
            "scenarios": scenarios,
            "passed": sum(x["status"] == "PASS" for x in scenarios), "total": len(scenarios),
            "false_current_links_after_evidence_change": sum(x["old_resolved_link_preserved"] for x in scenarios),
            "status": "PASS" if all(x["status"] == "PASS" for x in scenarios) else "FAIL",
            "semantics": {"resolution_status": "Existing resolution vocabulary is unchanged.",
                "validity_state": "Additive association field: CURRENT or INVALIDATED.",
                "coverage": "Static associations never assert execution or behavior coverage."}}
