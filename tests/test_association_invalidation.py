import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.synthetic_scan import scan_project

FIXTURE=Path(__file__).parent/'fixtures'/'python_cross_module'


class TestAssociationInvalidation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.root=self.base/'project'
        self.out=self.base/'reports'
        shutil.copytree(FIXTURE,self.root)

    def tearDown(self):
        self.temp.cleanup()

    def scan_test_asset(self):
        scan_project(str(self.root),str(self.out),force=True)
        report=json.loads((self.out/'report.json').read_text(encoding='utf-8'))
        return next((asset for asset in report['test_assets']['test_source'] if asset['path']=='tests/test_api.py'),None)

    def initial(self):
        asset=self.scan_test_asset()
        self.assertIsNotNone(asset)
        association=next(a for a in asset['associations'] if a['test_name']=='test_public_api')
        self.assertEqual(association['resolution_status'],'RESOLVED')
        self.assertEqual(association['validity_state'],'CURRENT')
        self.assertEqual({e['kind'] for e in association['evidence']},{'import','call','definition'})
        self.assertEqual(association['coverage_claim'],'NOT_PROVEN')
        return association

    def test_target_function_deletion_invalidates_previous_link(self):
        previous=self.initial()
        (self.root/'ns'/'api.py').write_text('def hidden_api():\n    return 2\n',encoding='utf-8')
        asset=self.scan_test_asset()
        current=next(a for a in asset['associations'] if a['test_name']=='test_public_api')
        self.assertEqual(current['association_id'],previous['association_id'])
        self.assertNotEqual(current['resolution_status'],'RESOLVED')
        self.assertEqual(current['validity_state'],'INVALIDATED')
        self.assertEqual(current['failure_reason'],'TARGET_SYMBOL_MISSING')

    def test_target_rename_invalidates_previous_link(self):
        self.initial()
        source=self.root/'ns'/'api.py'
        source.write_text(source.read_text(encoding='utf-8').replace('public_api','renamed_api'),encoding='utf-8')
        asset=self.scan_test_asset()
        current=next(a for a in asset['associations'] if a['test_name']=='test_public_api')
        self.assertEqual(current['resolution_status'],'UNKNOWN')
        self.assertEqual(current['validity_state'],'INVALIDATED')
        self.assertEqual(current['failure_reason'],'TARGET_SYMBOL_MISSING')

    def test_target_module_move_invalidates_previous_link(self):
        self.initial()
        shutil.move(self.root/'ns'/'api.py',self.root/'ns'/'moved_api.py')
        asset=self.scan_test_asset()
        current=next(a for a in asset['associations'] if a['test_name']=='test_public_api')
        self.assertEqual(current['resolution_status'],'UNKNOWN')
        self.assertEqual(current['validity_state'],'INVALIDATED')
        self.assertEqual(current['failure_reason'],'TARGET_MODULE_MISSING')

    def test_import_alias_change_updates_or_invalidates_link(self):
        previous=self.initial()
        test_path=self.root/'tests'/'test_api.py'
        test_path.write_text('from ns.api import public_api as selected\n\ndef test_public_api():\n    assert selected() == 1\n',encoding='utf-8')
        asset=self.scan_test_asset()
        selected=next(a for a in asset['associations'] if a['test_name']=='test_public_api')
        self.assertEqual(selected['resolution_status'],'RESOLVED')
        self.assertEqual(selected['validity_state'],'CURRENT')
        self.assertEqual(selected['imported_as'],'selected')
        self.assertNotEqual(selected['association_id'],previous['association_id'])
        self.assertFalse(any(a['association_id']==previous['association_id'] for a in asset['associations']))
        test_path.write_text('from ns.api import public_api as selected\n\ndef test_public_api():\n    assert public_api() == 1\n',encoding='utf-8')
        broken=self.scan_test_asset()
        invalidated=next(a for a in broken['associations'] if a['test_name']=='test_public_api')
        self.assertEqual(invalidated['resolution_status'],'UNKNOWN')
        self.assertEqual(invalidated['validity_state'],'INVALIDATED')
        self.assertEqual(invalidated['failure_reason'],'NO_CALL_REFERENCE_TO_IMPORTED_ALIAS')

    def test_duplicate_same_name_definitions_are_ambiguous(self):
        self.initial()
        package=self.root/'ns'/'api'
        package.mkdir()
        (package/'__init__.py').write_text('def public_api():\n    return 2\n',encoding='utf-8')
        asset=self.scan_test_asset()
        current=next(a for a in asset['associations'] if a['test_name']=='test_public_api')
        self.assertEqual(current['resolution_status'],'AMBIGUOUS')
        self.assertEqual(current['validity_state'],'INVALIDATED')
        self.assertEqual(current['failure_reason'],'TARGET_SYMBOL_AMBIGUOUS')
        self.assertEqual(len(current['target_file_candidates']),2)

    def test_test_file_deletion_removes_asset_and_old_association(self):
        previous=self.initial()
        (self.root/'tests'/'test_api.py').unlink()
        self.assertIsNone(self.scan_test_asset())
        report=json.loads((self.out/'report.json').read_text(encoding='utf-8'))
        self.assertFalse(any(a.get('association_id')==previous['association_id']
                             for asset in report['test_assets']['test_source'] for a in asset['associations']))

    def test_reproducible_association_benchmark_has_no_stale_current_links(self):
        from usit.association_benchmark import run_association_invalidation_benchmark
        result=run_association_invalidation_benchmark()
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['passed'],result['total'])
        self.assertEqual(result['false_current_links_after_evidence_change'],0)


if __name__=='__main__':
    unittest.main()
