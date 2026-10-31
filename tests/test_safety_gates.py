import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import usit.scanner as scanner
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import discover_files, scan_project

class SafetyGateTests(unittest.TestCase):
    def test_authorization_missing_scope_and_output_overlap_block(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); root=base/'project'; root.mkdir(); (root/'a.py').write_text('x=1\n')
            with self.assertRaises(ScanBlocked) as missing: scan_project(str(root),str(base/'out'))
            self.assertEqual(missing.exception.code,'AUTHORIZATION_MISSING')
            auth=authorization_record(root,base/'out')
            auth['project_root']=str(base/'other')
            with self.assertRaises(ScanBlocked) as mismatch: scan_project(str(root),str(base/'out'),authorization=auth)
            self.assertIn(mismatch.exception.code,{'PROJECT_SCOPE_MISMATCH','PROJECT_PATH_INVALID'})
            auth=authorization_record(root,root/'out')
            with self.assertRaises(ScanBlocked): scan_project(str(root),str(root/'out'),authorization=auth)

    def test_file_count_and_bytes_allow_boundary_reject_over(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'a.py').write_bytes(b'1234'); (root/'b.py').write_bytes(b'5678')
            with patch.object(scanner,'MAX_FILES',2), patch.object(scanner,'MAX_FILE_BYTES',4), patch.object(scanner,'MAX_TOTAL_READ_BYTES',8):
                self.assertEqual(len(discover_files(root)),2)
                (root/'c.py').write_bytes(b'')
                with self.assertRaises(ScanBlocked) as count: discover_files(root)
                self.assertEqual(count.exception.code,'MAX_FILE_COUNT_EXCEEDED')
                (root/'c.py').unlink(); (root/'b.py').write_bytes(b'56789')
                with self.assertRaises(ScanBlocked) as size: discover_files(root)
                self.assertEqual(size.exception.code,'MAX_FILE_SIZE_EXCEEDED')

    def test_total_input_budget_boundary_and_excess(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'a.py').write_bytes(b'123'); (root/'b.py').write_bytes(b'45')
            with patch.object(scanner,'MAX_TOTAL_READ_BYTES',5): self.assertEqual(len(discover_files(root)),2)
            with patch.object(scanner,'MAX_TOTAL_READ_BYTES',4):
                with self.assertRaises(ScanBlocked) as over: discover_files(root)
                self.assertEqual(over.exception.code,'MAX_TOTAL_READ_BYTES_EXCEEDED')

    def test_deadline_checkpoint_fails_closed_without_waiting(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'a.py').write_text('x=1')
            with self.assertRaises(ScanBlocked) as timeout: discover_files(root,deadline=1,clock=lambda:1)
            self.assertEqual(timeout.exception.code,'TOTAL_SCAN_TIMEOUT')

    def test_output_budget_blocks_before_publish_and_records_status(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); root=base/'project'; root.mkdir(); (root/'a.py').write_text('x=1\n')
            out=base/'out'; auth=authorization_record(root,out)
            with patch.object(scanner,'MAX_OUTPUT_BYTES',1):
                with self.assertRaises(ScanBlocked) as blocked: scan_project(str(root),str(out),authorization=auth)
            self.assertEqual(blocked.exception.code,'MAX_OUTPUT_BYTES_EXCEEDED')
            self.assertFalse((out/'knowledge_graph.json').exists())
            statuses=list(base.glob('usit-status-*.json')); self.assertEqual(len(statuses),1)
            self.assertIn('BLOCKED',statuses[0].read_text(encoding='utf-8'))

    def test_directory_permission_failure_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'a.py').write_text('x=1')
            with patch('usit.scanner.os.scandir',side_effect=PermissionError()):
                with self.assertRaises(ScanBlocked) as blocked: discover_files(root)
            self.assertEqual(blocked.exception.code,'DIRECTORY_ACCESS_DENIED')

    def test_parser_crash_and_timeout_diagnostics_block_publication(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); root=base/'project'; root.mkdir(); (root/'main.ts').write_text('export const f = () => 1')
            out=base/'out'; auth=authorization_record(root,out)
            with patch.object(scanner,'analyze_js_ts_project',side_effect=RuntimeError('synthetic parser crash')):
                with self.assertRaises(ScanBlocked) as crashed: scan_project(str(root),str(out),authorization=auth)
            self.assertEqual(crashed.exception.code,'PARSER_CRASHED')
            self.assertFalse((out/'knowledge_graph.json').exists())
            with patch.object(scanner,'analyze_js_ts_project',return_value={'project_diagnostics':[{'code':'ADAPTER_TIMEOUT'}]}):
                with self.assertRaises(ScanBlocked) as timed: scan_project(str(root),str(out),authorization=auth)
            self.assertEqual(timed.exception.code,'ADAPTER_TIMEOUT')

    def test_staged_write_failure_is_incomplete_and_not_published(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); root=base/'project'; root.mkdir(); (root/'main.py').write_text('x=1')
            out=base/'out'; auth=authorization_record(root,out)
            with patch.object(Path,'write_bytes',side_effect=OSError('synthetic disk error')):
                with self.assertRaises(OSError): scan_project(str(root),str(out),authorization=auth)
            self.assertFalse((out/'knowledge_graph.json').exists())
            marker=list(base.glob('usit-status-*.json'))[-1]
            import json
            result=json.loads(marker.read_text(encoding='utf-8'))
            self.assertEqual(result['status'],'INCOMPLETE')
            self.assertEqual(result['reason_code'],'UNEXPECTED_FAILURE')
            self.assertNotIn('synthetic disk error',marker.read_text(encoding='utf-8'))

    def test_symlink_is_rejected_when_platform_allows_creation(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); root=base/'project'; root.mkdir(); external=base/'external'; external.mkdir()
            (external/'outside.py').write_text('x=1')
            link=root/'linked'
            try: link.symlink_to(external,target_is_directory=True)
            except (OSError,NotImplementedError): self.skipTest('OS denied symlink creation; junction/reparse behavior not independently exercised')
            with self.assertRaises(ScanBlocked) as blocked: discover_files(root)
            self.assertEqual(blocked.exception.code,'REPARSE_POINT_REJECTED')

class ProtocolDocumentTests(unittest.TestCase):
    """P0.7.2: the protocol document must be strict UTF-8 and pin the enforced limits.

    The previous version of this test asserted literal '?' runs, which only
    matched because the protocol file had lost its Chinese characters to an
    encoding defect. It now asserts the real phrases.
    """
    def test_protocol_utf8_and_mandatory_limits(self):
        path=Path(__file__).parents[1]/'PILOT-READONLY-PROTOCOL.md'
        data=path.read_bytes(); text=data.decode('utf-8',errors='strict')
        self.assertTrue(text.startswith('# USIT 只读试点准入协议'))
        for required in ('授权起止时间','输出目录','个人数据','INCOMPLETE','并发解析任务',
                         '5,000','1,048,576','268,435,456','600','45','256 MiB','BLOCKED',
                         'junction','reparse point','.env*','node_modules','.git'):
            with self.subTest(required=required):
                self.assertIn(required,text)

