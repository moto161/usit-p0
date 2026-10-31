import unittest, tempfile
from pathlib import Path
from tests.synthetic_scan import scan_project
class Smoke(unittest.TestCase):
 def test_scan_and_guard(self):
  with tempfile.TemporaryDirectory() as td:
   b=Path(td); p=b/'p'; p.mkdir(); (p/'main.py').write_text('def hello():\n return 1\n')
   s=scan_project(str(p),str(b/'out'))
   self.assertEqual(s['files_scanned'],1)
   self.assertTrue((b/'out'/'knowledge_graph.json').exists())
   with self.assertRaises(ValueError): scan_project(str(p),str(p/'out'))
if __name__=='__main__': unittest.main()
