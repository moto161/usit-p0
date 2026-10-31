import json
import shutil
import tempfile
import unittest
from pathlib import Path

from usit.parser_contract import CONTRACT_VERSION, RESOLUTION_STATUSES, adapt_graph_v1, validate_record
from usit.scanner import analyze_file, discover_files
from tests.synthetic_scan import scan_project
from usit.python_resolver import resolve_python_calls

FIXTURES = Path(__file__).parent / "fixtures"


class ParserContractTests(unittest.TestCase):
    def test_contract_record_shape_statuses_and_graph_v1_adapter(self):
        graph=json.loads((FIXTURES/'graphs'/'valid_v1.json').read_text(encoding='utf-8'))
        adapted=adapt_graph_v1(graph)
        self.assertEqual(adapted['contract_version'],CONTRACT_VERSION)
        self.assertEqual(adapted['source_schema'],'usit-graph-v1')
        self.assertTrue(RESOLUTION_STATUSES.issuperset({'RESOLVED','UNKNOWN','AMBIGUOUS','DYNAMIC','EXTERNAL','UNRESOLVED'}))
        self.assertTrue(all(not validate_record(record) for record in adapted['records']))
        self.assertEqual(graph['schema_version'],'usit-graph-v1')

    def test_javascript_typescript_semantic_fixture_and_jvm_honest_fallback(self):
        from usit.benchmark import run_fixture_benchmark
        result=run_fixture_benchmark()
        js=next(x for x in result['languages'] if x['language']=='JavaScript/TypeScript')
        self.assertEqual(js['status'],'PASS',js.get('relation_results'))
        self.assertEqual((js['TP'],js['FP'],js['FN']),(20,0,0))
        self.assertEqual(js['false_resolved_nonresolved_count'],0)
        self.assertEqual(js['relation_classification_accuracy'],1.0)
        self.assertEqual(js['nonresolved_state_classification_accuracy'],1.0)
        self.assertTrue(all(x['matched'] for x in js['relation_results']))
        from usit.js_ts_parser import analyze_js_ts_project
        fixture=FIXTURES/'javascript_typescript'
        parsed=analyze_js_ts_project(fixture,discover_files(fixture))
        extension_results={Path(path).suffix.lower():row['syntax_status'] for path,row in parsed['files'].items()}
        self.assertTrue({'.js','.jsx','.mjs','.cjs','.ts','.tsx'}.issubset(extension_results))
        self.assertTrue(all(extension_results[suffix] in {'VALID','INVALID'} for suffix in ('.js','.jsx','.mjs','.cjs','.ts','.tsx')))
        jvm=next(x for x in result['languages'] if x['language']=='Java/Kotlin')
        self.assertEqual(jvm['status'],'NOT_MEASURED')

    def test_javascript_typescript_associations_recompute_after_source_changes(self):
        from usit.js_ts_invalidation import run_js_ts_invalidation_benchmark
        result=run_js_ts_invalidation_benchmark()
        historical_ids={
            'baseline-link','target-deleted','target-renamed','target-moved',
            'import-alias-changed','same-name-added-explicit-import',
            'test-file-deleted','paths-mapping-broken',
        }
        self.assertEqual(result['status'],'PASS',result['scenarios'])
        self.assertEqual(result['scenario_total'],8)
        self.assertEqual(result['passed_total'],8)
        self.assertEqual({x['scenario'] for x in result['scenarios']},historical_ids)
        for scenario in result['scenarios']:
            with self.subTest(scenario=scenario['scenario']):
                self.assertTrue(scenario['input_condition'])
                self.assertTrue(scenario['expected_result'])
                self.assertIn('changed_files',scenario)
                self.assertIn('actual',scenario)
                self.assertIn('evidence',scenario)
                self.assertTrue(scenario['passed'])
        baseline=next(x for x in result['scenarios'] if x['scenario']=='baseline-link')
        self.assertEqual(baseline['actual']['status'],'RESOLVED')
        self.assertEqual((baseline['actual']['target']['file'],baseline['actual']['target']['name'],baseline['actual']['target']['line']),('src/a.ts','named',1))
        paths=next(x for x in result['scenarios'] if x['scenario']=='paths-mapping-broken')
        self.assertNotEqual(paths['actual']['status'],'RESOLVED')
        self.assertIsNone(paths['actual']['target'])
        same=next(x for x in result['scenarios'] if x['scenario']=='same-name-added-explicit-import')
        self.assertEqual((same['actual']['target']['file'],same['actual']['target']['name'],same['actual']['target']['line']),('src/a.ts','named',1))

    def test_python_ground_truth_includes_namespace_star_decorator_and_rebinding(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'project'; shutil.copytree(FIXTURES/'python_cross_module',root)
            rows=[analyze_file(root,p) for p in discover_files(root) if p.suffix=='.py']
            diagnostics=resolve_python_calls(str(root),rows)
            calls={(r['path'],c['line'],c['callee']):c for r in rows for c in r['calls']}
            broken=next(row for row in rows if row['path']=='broken.py')
            self.assertEqual(broken['analysis_diagnostics'][0]['code'],'PYTHON_SYNTAX_ERROR')
            self.assertIn('broken.py',diagnostics['files_with_parse_errors'])
            self.assertEqual(calls[('ns/sub/caller.py',6,'renamed')]['resolution_status'],'RESOLVED')
            self.assertEqual(calls[('ns/sub/caller.py',7,'decorated_api')]['resolution_status'],'UNKNOWN')
            self.assertEqual(calls[('ns/sub/caller.py',8,'hidden_api')]['resolution_status'],'UNKNOWN')
            self.assertEqual(calls[('ns/sub/rebind.py',9,'chosen')]['resolution_status'],'DYNAMIC')
            self.assertIn('ns/api.py',diagnostics['modules_indexed'] and [r['path'] for r in rows])
            expected=json.loads((root/'expected.json').read_text(encoding='utf-8'))
            true_resolved={(x['path'],x['line'],x['callee'],x['target']['file'],x['target']['line']) for x in expected['expected_relations'] if x['status']=='RESOLVED'}
            actual={(path,line,callee,call['target']['file'],call['target']['line']) for (path,line,callee),call in calls.items() if call['resolution_status']=='RESOLVED'}
            self.assertEqual(actual,true_resolved)

    def test_test_asset_links_only_explicit_import_and_call(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'project'; shutil.copytree(FIXTURES/'python_cross_module',root)
            out=Path(td)/'out'; summary=scan_project(str(root),str(out),force=True)
            report=json.loads((out/'report.json').read_text(encoding='utf-8'))
            test_assets=report['test_assets']['test_source']
            asset=next(item for item in test_assets if item['path']=='tests/test_api.py')
            self.assertEqual(asset['execution_status'],'NOT_RUN')
            self.assertEqual(report['test_assets']['coverage_evidence_status'],'UNKNOWN')
            self.assertEqual(len(asset['associations']),1)
            association=asset['associations'][0]
            self.assertEqual(association['resolution_status'],'RESOLVED')
            self.assertEqual(association['target_file'],'ns/api.py')
            contract=json.loads((out/'parser_contract.json').read_text(encoding='utf-8'))
            self.assertEqual(contract['contract_version'],CONTRACT_VERSION)
            self.assertFalse(contract['record_validation_errors'])
            self.assertEqual(summary['graph_validation_status'],'VALID_WITH_ISSUES')

    def test_failure_and_size_boundaries_are_diagnostic_and_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'project'; root.mkdir()
            (root/'bad.py').write_text('def broken(:\n    pass\n',encoding='utf-8')
            (root/'external.py').write_text('import missing_package\ndef invoke():\n    return missing_package.call()\n',encoding='utf-8')
            (root/'invalid.py').write_bytes(b'\x00\xff\xfe')
            (root/'large.py').write_bytes(b'x'*(1_048_576+1))
            from usit.safety import ScanBlocked
            rows=[analyze_file(root,p) for p in (root/'bad.py',root/'external.py',root/'invalid.py')]
            with self.assertRaises(ScanBlocked) as blocked: discover_files(root)
            self.assertEqual(blocked.exception.code,'MAX_FILE_SIZE_EXCEEDED')
            bad=next(row for row in rows if row['path']=='bad.py')
            self.assertEqual(bad['analysis_diagnostics'][0]['code'],'PYTHON_SYNTAX_ERROR')
            self.assertEqual(bad['calls'],[])
            invalid=next(row for row in rows if row['path']=='invalid.py')
            self.assertFalse(invalid['readable_text'])
            resolve_python_calls(str(root),rows)
            call=next(call for row in rows for call in row['calls'] if call['callee']=='missing_package.call')
            self.assertEqual(call['resolution_status'],'EXTERNAL')

    def test_fixture_benchmark_metrics_keep_unsupported_languages_unmeasured(self):
        from usit.benchmark import run_fixture_benchmark
        result=run_fixture_benchmark()
        py,js,jvm=result['languages']
        self.assertEqual((py['TP'],py['FP'],py['FN']),(8,0,0))
        self.assertEqual((py['precision'],py['recall']),(1.0,1.0))
        self.assertEqual(py['status'],'PASS')
        self.assertEqual(py['relation_classification_hits'],py['expected_relation_total'])
        self.assertEqual(py['nonresolved_state_hits'],py['nonresolved_state_total'])
        self.assertEqual(py['false_resolved_nonresolved_count'],0)
        self.assertEqual(js['status'],'PASS')
        self.assertEqual(jvm['status'],'NOT_MEASURED')
        self.assertEqual([x['false_resolved_nonresolved_count'] for x in result['languages']],[0,0,0])


class GraphV1EvidenceFallbackTests(unittest.TestCase):
    """adapt_graph_v1 must never fabricate evidence (Task A).

    A synthetic ``{"graph_node_id": id}`` placeholder is forbidden: it would let
    a node with no real evidence masquerade as evidence and be promoted
    downstream. Missing/empty/malformed evidence stays an empty mapping plus a
    diagnostic; node identity remains traceable via ``graph_ref``.
    """

    def _adapt(self, nodes, edges=None):
        return adapt_graph_v1({"schema_version": "usit-graph-v1", "nodes": nodes, "edges": edges or []})

    def test_real_evidence_passed_through_unchanged(self):
        ev = {"path": "app.py", "line": 3, "excerpt": "def f():"}
        rec = self._adapt([{"id": "n1", "type": "function", "label": "f", "evidence": ev, "resolution_status": "RESOLVED"}])["records"][0]
        self.assertEqual(rec["evidence"], ev)
        self.assertEqual(rec["diagnostics"], [])

    def test_missing_evidence_is_not_fabricated(self):
        rec = self._adapt([{"id": "n1", "type": "function", "label": "f", "resolution_status": "RESOLVED"}])["records"][0]
        self.assertEqual(rec["evidence"], {})
        self.assertNotIn("graph_node_id", rec["evidence"])
        self.assertEqual(rec["graph_ref"], "n1")  # traceability preserved
        self.assertEqual(rec["diagnostics"], [{"code": "missing_evidence", "graph_node_id": "n1"}])

    def test_empty_mapping_evidence_flagged_not_fabricated(self):
        rec = self._adapt([{"id": "n1", "type": "function", "label": "f", "evidence": {}, "resolution_status": "RESOLVED"}])["records"][0]
        self.assertEqual(rec["evidence"], {})
        self.assertEqual(rec["diagnostics"][0]["code"], "empty_evidence")

    def test_non_mapping_evidence_does_not_crash_and_is_flagged(self):
        for bad in ("a-string", ["a", "list"], 42, True, 3.5):
            with self.subTest(bad=bad):
                rec = self._adapt([{"id": "n1", "type": "function", "label": "f", "evidence": bad, "resolution_status": "RESOLVED"}])["records"][0]
                self.assertEqual(rec["evidence"], {})
                self.assertTrue(rec["diagnostics"][0]["code"].startswith("malformed_evidence:"))

    def test_missing_node_id_still_adapts(self):
        rec = self._adapt([{"type": "function", "label": "f", "evidence": {"path": "x.py"}}])["records"][0]
        self.assertEqual(rec["evidence"], {"path": "x.py"})
        self.assertEqual(rec["graph_ref"], {"source": None, "target": None})

    def test_edge_without_path_falls_back_to_source_without_crash(self):
        nodes = [{"id": "s", "type": "file", "label": "a.py", "evidence": {"path": "a.py"}}]
        edges = [{"source": "s", "target": "s", "type": "calls", "resolution_status": "RESOLVED"}]
        edge = self._adapt(nodes, edges)["records"][-1]
        self.assertEqual(edge["file_path"], "a.py")

    def test_edge_with_malformed_source_evidence_does_not_crash(self):
        nodes = [{"id": "s", "type": "file", "label": "a.py", "evidence": "BROKEN"}]
        edges = [{"source": "s", "target": "s", "type": "calls", "resolution_status": "RESOLVED"}]
        edge = self._adapt(nodes, edges)["records"][-1]
        self.assertEqual(edge["file_path"], "")  # no fabricated path

    def test_invalid_status_is_downgraded_to_unknown(self):
        rec = self._adapt([{"id": "n1", "type": "function", "label": "f", "evidence": {"path": "x.py"}, "resolution_status": "BOGUS"}])["records"][0]
        self.assertEqual(rec["resolution_status"], "UNKNOWN")

    def test_missing_evidence_record_projects_to_non_success(self):
        from usit.understanding_projection import project_records
        rec = self._adapt([{"id": "n1", "type": "function", "label": "f", "resolution_status": "RESOLVED"}])["records"][0]
        result = project_records([rec])
        self.assertTrue(all(not item.is_success for item in result.items))
        self.assertEqual(result.stats["N_evidence_none"], 1)
        self.assertEqual(result.stats["N_construct_ok"], 0)


if __name__ == '__main__':
    unittest.main()
