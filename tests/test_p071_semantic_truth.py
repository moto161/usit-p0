"""Focused, file/line/target assertions for P0.7.1 synthetic truth cases."""
from __future__ import annotations
import json
from pathlib import Path
import unittest
from usit.benchmark import run_fixture_benchmark

ROOT = Path(__file__).parent
TRUTH = {
    "Python": "fixtures/python_cross_module/expected.json",
    "JavaScript/TypeScript": "fixtures/javascript_typescript/expected.json",
}
# Explicit targets are repeated here intentionally: a wrong target must fail this test,
# even if the scanner's own aggregate benchmark summary were accidentally changed.
CASES = [
    {"language":"Python","key":{"path":"main.py","line":6,"callee":"mod.same"},"status":"RESOLVED","target":{"file":"alpha.py","name":"same","line":1}},
    {"language":"Python","key":{"path":"main.py","line":7,"callee":"beta_same"},"status":"RESOLVED","target":{"file":"beta.py","name":"same","line":1}},
    {"language":"Python","key":{"path":"ambiguous.py","line":7,"callee":"same"},"status":"AMBIGUOUS","target":None,"candidates":["symbol:alpha.py#same:1","symbol:beta.py#same:1"]},
    {"language":"Python","key":{"path":"conditional.py","line":5,"callee":"same"},"status":"UNKNOWN","target":None},
    {"language":"Python","key":{"path":"main.py","line":9,"callee":"missing"},"status":"UNRESOLVED","target":None},
    {"language":"Python","key":{"path":"ns/sub/caller.py","line":8,"callee":"hidden_api"},"status":"UNKNOWN","target":None},
    {"language":"JavaScript/TypeScript","id":"extra-path-fallback","status":"RESOLVED","target":{"file":"src/routes/api.ts","name":"fallbackTarget","line":1}},
    {"language":"JavaScript/TypeScript","id":"extra-specific-path-wins","status":"RESOLVED","target":{"file":"src/priority/api.ts","name":"priorityTarget","line":1}},
    {"language":"JavaScript/TypeScript","id":"extra-multilevel-barrel","status":"RESOLVED","target":{"file":"src/modules/reexported.ts","name":"reexported","line":1}},
    {"language":"JavaScript/TypeScript","id":"extra-local-parameter-shadows-import","status":"UNKNOWN","target":None},
    {"language":"JavaScript/TypeScript","id":"extra-unshadowed-import","status":"RESOLVED","target":{"file":"src/a.ts","name":"named","line":1}},
    {"language":"JavaScript/TypeScript","id":"call-same-left","status":"RESOLVED","target":{"file":"src/modules/left.ts","name":"same","line":1}},
    {"language":"JavaScript/TypeScript","id":"call-same-right","status":"RESOLVED","target":{"file":"src/modules/right.ts","name":"same","line":1}},
    {"language":"JavaScript/TypeScript","id":"call-missing","status":"UNRESOLVED","target":None},
    {"language":"JavaScript/TypeScript","id":"call-computed","status":"DYNAMIC","target":None},
]


def _target(actual):
    value = actual.get("target") or {}
    return {k:value.get(k) for k in ("file", "name", "line")} if value else None


def collect_semantic_truth_results():
    benchmark = run_fixture_benchmark()
    languages = {x["language"]:x for x in benchmark["languages"]}
    results=[]
    for case in CASES:
        language=languages[case["language"]]
        if "id" in case:
            row=next((r for r in language["relation_results"] if r["expected"].get("id")==case["id"]),None)
        else:
            key=case["key"]
            row=next((r for r in language["relation_results"] if all(r["expected"].get(k)==v for k,v in key.items())),None)
        expected={"status":case["status"],"target":case["target"]}
        actual=row["actual"] if row else {}
        actual_summary={"status":actual.get("resolution_status",actual.get("status")),"resolved":actual.get("resolved"),"target":_target(actual),"candidate_targets":actual.get("candidate_targets"),"diagnostic":actual.get("diagnostic"),"evidence_chain":actual.get("evidence_chain"),"import_evidence":actual.get("import_evidence")}
        target_ok=actual_summary["target"]==case["target"]
        status_ok=actual_summary["status"]==case["status"]
        bool_ok=actual_summary["resolved"]==(case["status"]=="RESOLVED")
        candidates_ok=("candidates" not in case or sorted(actual_summary["candidate_targets"] or [])==sorted(case["candidates"]))
        source=case.get("key") or {k:row["expected"].get(k) for k in ("path","line","column","callee")}
        no_false_upgrade=(case["status"]=="RESOLVED" or actual_summary["status"]!="RESOLVED")
        results.append({"id":case.get("id") or f"{case['language']}:{source.get('path')}:{source.get('line')}:{source.get('callee')}","language":case["language"],"truth_source":str(ROOT/TRUTH[case["language"]]),"input":source,"expected":expected,"actual":actual_summary,"checks":{"present":row is not None,"status":status_ok,"target":target_ok,"resolved_boolean":bool_ok,"candidate_set":candidates_ok,"no_false_resolved_upgrade":no_false_upgrade},"false_resolved_upgrade":case["status"]!="RESOLVED" and actual_summary["status"]=="RESOLVED","passed":bool(row and status_ok and target_ok and bool_ok and candidates_ok and no_false_upgrade)})
    return {"schema_version":"usit-p0.7.1-focused-semantic-truth-v1","execution_policy":"SYNTHETIC_FIXTURES_ONLY_NO_TARGET_EXECUTION","truth_sources":{k:str(ROOT/v) for k,v in TRUTH.items()},"expected_case_count":len(CASES),"passed":sum(x["passed"] for x in results),"total":len(results),"false_resolved_upgrade_count":sum(x["false_resolved_upgrade"] for x in results),"cases":results}


class P071SemanticTruthTests(unittest.TestCase):
    def test_explicit_python_and_javascript_targets_and_negative_states(self):
        result=collect_semantic_truth_results()
        self.assertEqual(result["total"],15)
        self.assertEqual(result["passed"],15,result["cases"])
        self.assertEqual(result["false_resolved_upgrade_count"],0,result["cases"])
        for item in result["cases"]:
            self.assertTrue(all(item["checks"].values()),item)

    def test_truth_sources_cover_wrong_target_and_unresolved_cases(self):
        result=collect_semantic_truth_results()
        byid={x["id"]:x for x in result["cases"]}
        self.assertEqual(byid["call-same-left"]["actual"]["target"],{"file":"src/modules/left.ts","name":"same","line":1})
        self.assertEqual(byid["call-same-right"]["actual"]["target"],{"file":"src/modules/right.ts","name":"same","line":1})
        self.assertIsNone(byid["call-missing"]["actual"]["target"])
        self.assertEqual(byid["extra-local-parameter-shadows-import"]["actual"]["status"],"UNKNOWN")


if __name__ == "__main__":
    print(json.dumps(collect_semantic_truth_results(),ensure_ascii=False,indent=2))
