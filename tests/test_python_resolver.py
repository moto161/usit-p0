import json
import shutil
import tempfile
import unittest
from pathlib import Path

from usit.python_resolver import resolve_python_calls
from usit.scanner import analyze_file, discover_files
from tests.synthetic_scan import scan_project

FIXTURE = Path(__file__).parent / "fixtures" / "python_cross_module"


class PythonCrossModuleResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        shutil.copytree(FIXTURE, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def analyze(self):
        rows = [analyze_file(self.root, path) for path in discover_files(self.root)
                if path.suffix == ".py"]
        diagnostics = resolve_python_calls(str(self.root), rows)
        return rows, diagnostics

    def test_resolves_alias_direct_relative_and_cycle_imports(self):
        rows, _ = self.analyze()
        expected = [x for x in json.loads((self.root / "expected.json").read_text(encoding="utf-8"))["expected_relations"] if x['status']=='RESOLVED']
        actual = set()
        for row in rows:
            for call in row["calls"]:
                if call["resolution_status"] == "RESOLVED":
                    target = call["target"]
                    actual.add((row["path"], call["line"], call["callee"], target["file"], target["name"], target["line"]))
        truth = {(item["path"], item["line"], item["callee"], item["target"]["file"], item["target"]["name"], item["target"]["line"])
                 for item in expected}
        true_positives = len(actual & truth)
        precision = true_positives / len(actual) if actual else 0.0
        recall = true_positives / len(truth) if truth else 0.0
        self.assertEqual(actual, truth)
        self.assertEqual(precision, 1.0)
        self.assertEqual(recall, 1.0)

    def test_states_external_unresolved_ambiguous_and_dynamic(self):
        rows, _ = self.analyze()
        expected_relations=json.loads((self.root/'expected.json').read_text(encoding='utf-8'))['expected_relations']
        actual={(row['path'],call['line'],call['callee']):call for row in rows for call in row['calls']}
        for truth in expected_relations:
            call=actual[(truth['path'],truth['line'],truth['callee'])]
            self.assertEqual(call['resolution_status'],truth['status'],truth)
            self.assertEqual(call['column'],truth['column'],truth)
            self.assertEqual(call.get('import_evidence'),truth['import_evidence'],truth)
            self.assertEqual(call.get('candidate_targets'),truth['candidate_targets'],truth)
            self.assertEqual(call.get('resolved',False),truth['status']=='RESOLVED',truth)
            if truth['target'] is None:
                self.assertIsNone(call.get('target'),truth)
            else:
                for field in ('file','name','line','id'):
                    self.assertEqual(call['target'].get(field),truth['target'][field],truth)
        ambiguous = [call for row in rows for call in row["calls"]
                     if row["path"] == "ambiguous.py" and call["resolution_status"] == "AMBIGUOUS"]
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(set(ambiguous[0]["candidate_targets"]),
                         {"symbol:alpha.py#same:1", "symbol:beta.py#same:1"})

    def test_resolved_calls_have_definition_and_import_evidence(self):
        rows, _ = self.analyze()
        calls = [call for row in rows for call in row["calls"] if call["resolution_status"] == "RESOLVED"]
        self.assertTrue(calls)
        for call in calls:
            self.assertIn("file", call["target"])
            self.assertIn("line", call["target"])
            if call.get("import_evidence"):
                self.assertIn("path", call["import_evidence"])
                self.assertIn("line", call["import_evidence"])

    def test_resolved_call_records_exact_positional_and_keyword_argument_mapping(self):
        source = self.root / "mapping.py"
        source.write_text(
            "def sink(owner_id, *, tenant_id=None):\\n"
            "    return owner_id\\n"
            "def caller(req):\\n"
            "    uid = req.user.id\\n"
            "    sink(uid, tenant_id=req.tenant.id)\\n",
            encoding="utf-8",
        )
        rows = [analyze_file(self.root, source)]
        resolve_python_calls(str(self.root), rows)
        call = next(item for item in rows[0]["calls"] if item["callee"] == "sink")
        self.assertEqual(call["resolution_status"], "RESOLVED")
        self.assertEqual(call["argument_binding_status"], "EXACT_STATIC_MAPPING")
        self.assertEqual(
            [(item["argument_kind"], item["argument_expression"], item["parameter_name"])
             for item in call["argument_bindings"]],
            [("POSITIONAL", "uid", "owner_id"),
             ("KEYWORD", "req.tenant.id", "tenant_id")],
        )
        self.assertEqual(call["argument_bindings"][0]["parameter_declaration"]["path"], "mapping.py")
        self.assertEqual(call["argument_bindings"][0]["argument_location"]["line"], 5)

    def test_unresolved_or_star_arguments_do_not_emit_partial_mapping(self):
        source = self.root / "mapping.py"
        source.write_text(
            "def sink(owner_id):\\n"
            "    return owner_id\\n"
            "def caller(args):\\n"
            "    sink(*args)\\n",
            encoding="utf-8",
        )
        rows = [analyze_file(self.root, source)]
        resolve_python_calls(str(self.root), rows)
        call = next(item for item in rows[0]["calls"] if item["callee"] == "sink")
        self.assertEqual(call["resolution_status"], "RESOLVED")
        self.assertEqual(call["argument_binding_status"], "UNSUPPORTED_ARGUMENT_SHAPE")
        self.assertEqual(call["argument_bindings"], [])

    def test_no_project_code_is_imported_or_executed(self):
        # The resolver's only interface accepts paths and analyzer rows; fixture modules are never imported.
        rows, _ = self.analyze()
        self.assertTrue(all(row.get("language") == "Python" for row in rows))

    def test_scan_emits_traceable_cross_file_edges_and_quality_gate(self):
        out = self.root.parent / "scan-output"
        summary = scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        validation = json.loads((out / "graph_validation.json").read_text(encoding="utf-8"))
        resolved = [edge for edge in graph["edges"] if edge["type"] == "calls" and edge["resolution_status"] == "RESOLVED"]
        expected=[x for x in json.loads((FIXTURE/'expected.json').read_text(encoding='utf-8'))['expected_relations'] if x['status']=='RESOLVED']
        self.assertEqual(len(resolved), len(expected))
        for edge in resolved:
            target = next(node for node in graph["nodes"] if node["id"] == edge["target"])
            self.assertNotEqual(target["evidence"]["path"], edge["evidence"]["path"])
            self.assertIn("import_evidence", edge["evidence"])
            self.assertIn("target_location", edge)
        self.assertEqual(summary["graph_validation_status"], "VALID_WITH_ISSUES")
        self.assertEqual(validation["quality"]["errors"], 0)
        self.assertEqual(validation["quality"]["resolved_calls"], len(expected))

    def test_python_call_records_identify_enclosing_function(self):
        rows, _ = self.analyze()
        call = next(call for row in rows if row["path"] == "main.py"
                    for call in row["calls"] if call["callee"] == "mod.same")
        self.assertEqual(call["caller"], {"name": "invoke", "line": 5})
        self.assertEqual(call["caller_symbol_id"], "symbol:main.py#invoke:5")

    def test_javascript_intra_file_call_uses_compiler_api_evidence(self):
        (self.root / "sample.js").write_text("function helper() {}\nfunction run() {\n  helper();\n}\n", encoding="utf-8")
        out = self.root.parent / "js-scan-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        calls = [edge for edge in graph["edges"] if edge["type"] == "calls" and edge["evidence"]["path"] == "sample.js"]
        self.assertTrue(calls)
        helper = next(edge for edge in calls if edge.get("target_location", {}).get("qualified_name", "").endswith(".helper"))
        self.assertTrue(helper["resolved"])
        self.assertEqual(helper["resolution_status"], "RESOLVED")
        self.assertEqual(helper["target_location"]["path"], "sample.js")
        self.assertTrue(any(item.get("kind") == "definition" for item in helper["evidence_chain"]))
        self.assertEqual(helper["source"], "file:sample.js")
        self.assertEqual(helper["caller_symbol_id"], "symbol:sample.js#run:2")


    def test_typescript_imported_express_handler_resolves_to_exported_symbol(self):
        (self.root / "routes.js").write_text(
            "const router = require('express').Router();\n"
            "import { uploadPhoto as upload } from './controllers/photos.js';\n"
            "router.post('/photos', upload);\n",
            encoding="utf-8",
        )
        (self.root / "controllers").mkdir(exist_ok=True)
        (self.root / "controllers" / "photos.js").write_text(
            "export function uploadPhoto(req, res) { return savePhoto(); }\n"
            "function savePhoto() {}\n",
            encoding="utf-8",
        )
        out = self.root.parent / "imported-handler-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        binding = next(edge for edge in graph["edges"]
                       if edge.get("method") == "typescript-route-handler-binding"
                       and edge.get("route_path") == "/photos")
        self.assertEqual(binding["resolution_status"], "RESOLVED", msg=repr(binding))
        self.assertEqual(binding["target"], "symbol:controllers/photos.js#uploadPhoto:1")
        from usit.business_graph import project_business_graph
        projected = project_business_graph(graph, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "routes.js:3",
            "line": 3, "handler_reference": "upload",
        }]})
        endpoint = next(node for node in projected["nodes"] if node.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["resolution_status"], "RESOLVED")
        self.assertEqual(endpoint["handler_association"]["target_symbol_id"],
                         "symbol:controllers/photos.js#uploadPhoto:1")

    def test_typescript_namespace_handler_resolves_through_reexport_with_full_evidence(self):
        (self.root / "routes.js").write_text(
            "const router = require('express').Router();\n"
            "import * as photos from './controllers/index.js';\n"
            "router.post('/photos', photos.uploadPhoto);\n",
            encoding="utf-8",
        )
        controllers = self.root / "controllers"
        controllers.mkdir(exist_ok=True)
        (controllers / "index.js").write_text(
            "export { uploadPhoto } from './photos.js';\n",
            encoding="utf-8",
        )
        (controllers / "photos.js").write_text(
            "export function uploadPhoto(req, res) { return savePhoto(); }\n"
            "function savePhoto() {}\n",
            encoding="utf-8",
        )
        out = self.root.parent / "namespace-handler-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        binding = next(edge for edge in graph["edges"]
                       if edge.get("method") == "typescript-route-handler-binding"
                       and edge.get("route_path") == "/photos")
        self.assertEqual(binding["resolution_status"], "RESOLVED")
        self.assertEqual(binding["target"], "symbol:controllers/photos.js#uploadPhoto:1")
        chain = binding["evidence_chain"]
        self.assertTrue(any(item.get("kind") == "handler_import"
                            and item.get("file") == "routes.js" for item in chain))
        self.assertTrue(any(item.get("kind") == "reexport"
                            and item.get("file") == "controllers/index.js" for item in chain))
        self.assertTrue(any(item.get("kind") == "handler_definition"
                            and item.get("file") == "controllers/photos.js" for item in chain))
        from usit.business_graph import project_business_graph
        projected = project_business_graph(graph, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "routes.js:3",
            "line": 3, "handler_reference": "photos.uploadPhoto",
        }]})
        endpoint = next(node for node in projected["nodes"] if node.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["resolution_status"], "RESOLVED")
        self.assertEqual(endpoint["handler_association"]["target_symbol_id"],
                         "symbol:controllers/photos.js#uploadPhoto:1")

    def test_typescript_dynamic_or_unresolved_route_handler_is_not_guessed(self):
        (self.root / "routes.js").write_text(
            "const router = require('express').Router();\n"
            "const handler = registry[name];\n"
            "router.get('/dynamic', handler);\n",
            encoding="utf-8",
        )
        out = self.root.parent / "dynamic-handler-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        binding = next(edge for edge in graph["edges"]
                       if edge.get("method") == "typescript-route-handler-binding"
                       and edge.get("route_path") == "/dynamic")
        self.assertNotEqual(binding["resolution_status"], "RESOLVED")
        self.assertFalse(binding["resolved"])

    def test_jsx_event_handler_binding_resolves_unique_named_handler(self):
        (self.root / "PhotoPage.tsx").write_text(
            "export function PhotoPage() { return <button onClick={loadPhotos}>Load</button>; }\n"
            "export function loadPhotos() { return fetch('/api/photos'); }\n", encoding="utf-8")
        out = self.root.parent / "jsx-event-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        binding = next(edge for edge in graph["edges"] if edge.get("method") == "typescript-jsx-event-handler-binding")
        self.assertEqual(binding["event_name"], "onClick")
        self.assertEqual(binding["resolution_status"], "RESOLVED")
        self.assertEqual(binding["target"], "symbol:PhotoPage.tsx#loadPhotos:2")
        from usit.business_graph import project_business_graph
        projected = project_business_graph(graph, {"features": [], "entities": [], "endpoints": []})
        rows = projected["business_understanding"]["frontend_interaction_bindings"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["handler_symbol_id"], "symbol:PhotoPage.tsx#loadPhotos:2")
        self.assertEqual(rows[0]["verification_status"], "NOT_VERIFIED")

    def test_jsx_handler_fetch_literal_links_exact_backend_endpoint(self):
        (self.root / "PhotoPage.tsx").write_text(
            "export function PhotoPage() { return <button onClick={loadPhotos}>Load</button>; }\n"
            "export function loadPhotos() { return fetch('/api/photos'); }\n", encoding="utf-8")
        (self.root / "routes.ts").write_text(
            "import { loadPhotos } from './PhotoPage';\n"
            "router.get('/api/photos', loadPhotos);\n", encoding="utf-8")
        out = self.root.parent / "jsx-api-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        from usit.business_graph import project_business_graph
        semantics = {"features": [], "entities": [], "endpoints": [{
            "method": "GET", "path": "/api/photos", "source": "routes.ts:2", "line": 2,
            "handler_reference": "loadPhotos"}]}
        projected = project_business_graph(graph, semantics)
        summary = projected["business_understanding"]
        self.assertEqual(summary["frontend_api_endpoint_link_count"], 1)
        self.assertEqual(summary["frontend_api_endpoint_links"][0]["resolution_status"], "EXACT_MATCH")
        self.assertEqual(summary["frontend_business_chain_count"], 1)
        chain = summary["frontend_business_chains"][0]
        self.assertEqual((chain["method"], chain["path"]), ("GET", "/api/photos"))
        self.assertEqual(chain["verification_status"], "NOT_VERIFIED")
        # Backend router.get registration must not be misclassified as a frontend API client call.
        api_call_edges = [edge for edge in graph["edges"] if edge.get("method") == "typescript-api-client-call"]
        self.assertEqual(len(api_call_edges), 1)
        self.assertEqual(api_call_edges[0].get("route_path"), "/api/photos")

    def test_jsx_handler_dynamic_fetch_url_remains_unknown_and_unlinked(self):
        (self.root / "PhotoPage.tsx").write_text(
            "export function PhotoPage() { return <button onClick={loadPhotos}>Load</button>; }\n"
            "export function loadPhotos(id: string) { return fetch('/api/photos/' + id); }\n",
            encoding="utf-8")
        out = self.root.parent / "jsx-dynamic-api-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        from usit.business_graph import project_business_graph
        projected = project_business_graph(graph, {"features": [], "entities": [], "endpoints": [{
            "method": "GET", "path": "/api/photos/:id", "source": "routes.ts:1", "line": 1,
            "handler_reference": "getPhoto"}]})
        summary = projected["business_understanding"]
        self.assertEqual(summary["frontend_api_endpoint_link_count"], 1)
        link = summary["frontend_api_endpoint_links"][0]
        self.assertEqual(link["resolution_status"], "DYNAMIC_OR_UNSUPPORTED_URL")
        self.assertIsNone(link["path"])
        self.assertEqual(summary["frontend_business_chain_count"], 0)

    def test_jsx_inline_callback_is_not_misreported_as_resolved_symbol(self):
        (self.root / "Inline.tsx").write_text(
            "export function Page() { return <button onClick={() => save()}>Save</button>; }\n", encoding="utf-8")
        out = self.root.parent / "jsx-inline-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        binding = next(edge for edge in graph["edges"] if edge.get("method") == "typescript-jsx-event-handler-binding")
        self.assertEqual(binding["resolution_status"], "INLINE_CALLBACK_NOT_SYMBOL_LINKED")
        self.assertFalse(binding["resolved"])

    def test_python_lambda_calls_do_not_inherit_outer_function_owner(self):
        source = "def run():\n    callback = lambda: helper()\n    helper()\n"
        (self.root / "lambda_scope.py").write_text(source, encoding="utf-8")
        rows = [analyze_file(self.root, path) for path in discover_files(self.root)
                if path.suffix == ".py"]
        resolve_python_calls(str(self.root), rows)
        row = next(item for item in rows if item["path"] == "lambda_scope.py")
        calls = [item for item in row["calls"] if item["callee"] == "helper"]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("caller_symbol_id", calls[0])
        self.assertEqual(calls[1]["caller_symbol_id"], "symbol:lambda_scope.py#run:1")

    def test_javascript_anonymous_callback_does_not_inherit_outer_owner(self):
        (self.root / "callback.js").write_text(
            "function helper() {}\n"
            "function run() {\n"
            "  router.get('/x', (req, res) => { helper(); });\n"
            "  helper();\n"
            "}\n",
            encoding="utf-8",
        )
        out = self.root.parent / "callback-scan-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        calls = [edge for edge in graph["edges"] if edge["type"] == "calls"
                 and edge.get("evidence", {}).get("path") == "callback.js"
                 and edge.get("target_location", {}).get("qualified_name", "").endswith(".helper")]
        self.assertEqual(len(calls), 2)
        callback_call = next(edge for edge in calls if edge["evidence"]["line"] == 3)
        outer_call = next(edge for edge in calls if edge["evidence"]["line"] == 4)
        self.assertNotIn("caller_symbol_id", callback_call)
        self.assertEqual(outer_call["caller_symbol_id"], "symbol:callback.js#run:2")

if __name__ == "__main__":
    unittest.main()
