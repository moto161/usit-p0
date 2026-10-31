import json, os, tempfile, unittest
from pathlib import Path
from tests.synthetic_scan import scan_project
from usit.scanner import (is_sensitive, category_for, lang_for,
                          analyze_file, _python_analysis, _scrub_sensitive_graph)

def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')

def _load(out: Path, name: str):
    return json.loads((out/name).read_text('utf-8'))

class P0_1_Sensitive(unittest.TestCase):
    def test_is_sensitive_chinese_and_english(self):
        self.assertTrue(is_sensitive('虚构密钥文档_测试.md'))
        self.assertTrue(is_sensitive('密码清单_阅后即焚_2026.md'))
        self.assertTrue(is_sensitive('secret_token.txt'))
        self.assertTrue(is_sensitive('credentials.json'))
        self.assertFalse(is_sensitive('app.py'))
        self.assertFalse(is_sensitive('README.md'))

    def test_sensitive_content_and_path_not_in_outputs(self):
        # 使用虚构文件名与虚构内容，绝不出现真实密钥
        with tempfile.TemporaryDirectory() as td:
            b=Path(td); proj=b/'project'; out=b/'out'; proj.mkdir()
            _write(proj/'app.py', 'def hello():\n    return 1\n')
            _write(proj/'虚构密钥文档_测试.md', '虚构内容：FAKE_SECRET_DO_NOT_LEAK=xxxx\n')
            _write(proj/'secret_token.txt', 'FAKE_TOKEN_VALUE_abc123\n')
            scan_project(str(proj), str(out), force=True)
            for name in ('report.json','knowledge_graph.json','test_plan.json','scan_manifest.json'):
                data=_load(out,name)
                blob=json.dumps(data, ensure_ascii=False)
                self.assertNotIn('虚构密钥文档_测试', blob, f'敏感文件名泄露于 {name}')
                self.assertNotIn('secret_token', blob, f'敏感文件名泄露于 {name}')
                self.assertNotIn('FAKE_SECRET_DO_NOT_LEAK', blob, f'敏感内容泄露于 {name}')
                self.assertNotIn('FAKE_TOKEN_VALUE_abc123', blob, f'敏感内容泄露于 {name}')
            md=(out/'report.md').read_text('utf-8')
            self.assertNotIn('虚构密钥文档_测试', md)
            self.assertNotIn('secret_token', md)
            # 正常源码中的 password 符号应保留（不过度脱敏）
            graph=_load(out,'knowledge_graph.json')
            self.assertTrue(any('app.py' in (n.get('evidence',{}) or {}).get('path','') for n in graph['nodes']))

    def test_scrub_defense_in_depth(self):
        g={'nodes':[{'id':'file:secret_token.txt','type':'file','evidence':{'path':'secret_token.txt'}},
                    {'id':'file:app.py','type':'file','evidence':{'path':'app.py'}}],
           'edges':[{'source':'file:app.py','target':'file:secret_token.txt','type':'imports'}]}
        _scrub_sensitive_graph(g)
        ids={n['id'] for n in g['nodes']}
        self.assertNotIn('file:secret_token.txt', ids)
        self.assertNotIn('file:app.py', {e['source'] for e in g['edges']} | {e['target'] for e in g['edges']})

class P0_2_Category(unittest.TestCase):
    def test_category_split(self):
        cases=[('a.py','Python','source_code'),('b.kt','Kotlin','source_code'),
               ('c.bak','Unknown','backup'),('d.zip','Unknown','archive'),
               ('e.png','Unknown','image_asset'),('f.log','Unknown','log'),
               ('g.md','Unknown','documentation'),('h.json','Unknown','config'),
               ('i.apk','Unknown','binary'),('j.txt','Unknown','documentation')]
        for name,lang,exp in cases:
            self.assertEqual(category_for(name,lang), exp, name)
    def test_language_separate_from_category(self):
        # .md 语言为 Unknown，但类别为 documentation（维度分离）
        self.assertEqual(lang_for(Path('x.md')),'Unknown')
        self.assertEqual(category_for('x.md','Unknown'),'documentation')
    def test_backup_dir_classified(self):
        self.assertEqual(category_for('backups/old_main.py','Python'),'backup')

class P0_3_Relations(unittest.TestCase):
    def test_python_calls_resolved(self):
        src='def helper():\n    return 1\ndef run():\n    return helper()\n'
        syms,imp,calls=_python_analysis(src, src.splitlines())
        self.assertTrue(any(c['callee']=='helper' and c['resolved'] and c['method']=='python-ast' for c in calls))
    def test_import_only_call_is_not_claimed_as_resolved(self):
        src='import helper\ndef run():\n    return helper()\n'
        _,_,calls=_python_analysis(src, src.splitlines())
        imported_call=next(c for c in calls if c['callee']=='helper')
        self.assertFalse(imported_call['resolved'])
        self.assertTrue(imported_call.get('via_import'))
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); project=base/'project'; out=base/'out'; project.mkdir()
            _write(project/'app.py',src)
            scan_project(str(project),str(out),force=True)
            graph=_load(out,'knowledge_graph.json')
            edge=next(e for e in graph['edges'] if e['type']=='calls')
            target=next(n for n in graph['nodes'] if n['id']==edge['target'])
            self.assertFalse(edge['resolved'])
            self.assertEqual(target['type'],'unresolved')
    def test_graph_has_calls_and_references(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td); proj=b/'p'; out=b/'o'; proj.mkdir()
            _write(proj/'m.py','import os\ndef helper():\n    return 1\ndef run():\n    return helper()\n')
            _write(proj/'s.kt','package x\nfun a(){ startActivity() }\n')
            scan_project(str(proj), str(out), force=True)
            g=_load(out,'knowledge_graph.json')
            types=g['edge_type_counts']
            self.assertIn('calls', types); self.assertIn('imports', types); self.assertIn('defines', types)
            self.assertIn('references', types)
            # references 边必须是候选且未验证
            refs=[e for e in g['edges'] if e['type']=='references']
            self.assertTrue(refs)
            self.assertTrue(all(e.get('candidate') and e.get('verified') is False for e in refs))

class P0_4_TestPlan(unittest.TestCase):
    def test_inventory_classification(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td); proj=b/'p'; out=b/'o'; proj.mkdir()
            _write(proj/'app.py','def f(): pass\n')
            _write(proj/'test_login.py','import unittest\nclass T(unittest.TestCase): pass\n')
            _write(proj/'TEST_PLAN_2026.md','测试计划文档\n')
            _write(proj/'test_home.png','fakeimage')
            scan_project(str(proj), str(out), force=True)
            plan=_load(out,'test_plan.json')
            inv=plan['test_inventory']
            self.assertTrue(any(x['path'].endswith('test_login.py') for x in inv['test_source']))
            self.assertTrue(any(x['path'].endswith('TEST_PLAN_2026.md') for x in inv['test_case_document']))
            shots=[x for x in inv['screenshot_evidence'] if x['path'].endswith('test_home.png')]
            self.assertTrue(shots and shots[0]['executable'] is False)
            for it in plan['items']:
                self.assertEqual(it['status'],'NOT_RUN')

    def test_call_relation_stats_and_consistency(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td); proj=b/'p'; out=b/'o'; proj.mkdir()
            _write(proj/'m.py','def helper():\n    return 1\ndef run():\n    return helper()\n')
            _write(proj/'s.kt','package x\nfun a(){ b() }\nfun b(){}\n')
            _write(proj/'x.png','fakeimage')
            s=scan_project(str(proj), str(out), force=True)
            # P0.4 调用统计存在且自洽
            self.assertIn('call_method_counts', s)
            self.assertIn('call_resolved_x_confidence', s)
            self.assertEqual(sum(s['call_method_counts'].values()), s['calls'])
            self.assertEqual(sum(s['call_resolved_x_confidence'].values()), s['calls'])
            # P0.3 统计一致性：语言总和 == 类别总和 == 扫描文件数
            self.assertEqual(sum(s['languages'].values()), s['files_scanned'])
            self.assertEqual(sum(s['categories'].values()), s['files_scanned'])
            # P0.3 报告须注明语言 Unknown 含非源码、并区分两个 unknown
            md=(out/'report.md').read_text('utf-8')
            self.assertIn('Unknown', md)
            self.assertIn('非源码', md)
            self.assertIn('source_code', md)

class P0_5_Incremental(unittest.TestCase):
    def test_five_scenarios(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td); proj=b/'p'; out=b/'o'; proj.mkdir()
            _write(proj/'a.py','x=1\n'); _write(proj/'b.py','y=2\n')
            s1=scan_project(str(proj), str(out), force=True)
            self.assertEqual(set(s1['incremental']['added']), {'a.py','b.py'})
            self.assertFalse(s1['incremental']['unchanged'])
            # 2) 二次无改动
            s2=scan_project(str(proj), str(out), force=True)
            self.assertEqual(set(s2['incremental']['unchanged']), {'a.py','b.py'})
            self.assertFalse(s2['incremental']['modified'])
            self.assertEqual(s2['categories'], s1['categories'])  # 分类一致性
            # 3) 单文件修改
            _write(proj/'a.py','x=111\n')
            s3=scan_project(str(proj), str(out), force=True)
            self.assertEqual(s3['incremental']['modified'], ['a.py'])
            # 4) 新增
            _write(proj/'c.py','z=3\n')
            s4=scan_project(str(proj), str(out), force=True)
            self.assertEqual(s4['incremental']['added'], ['c.py'])
            # 5) 删除
            (proj/'b.py').unlink()
            s5=scan_project(str(proj), str(out), force=True)
            self.assertEqual(s5['incremental']['deleted'], ['b.py'])

    def test_no_overwrite_creates_subdir(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td); proj=b/'p'; out=b/'o'; proj.mkdir()
            _write(proj/'a.py','x=1\n')
            scan_project(str(proj), str(out))  # 首次
            first=(out/'report.md').read_text('utf-8')
            scan_project(str(proj), str(out))  # 二次无 --force
            subdirs=[d for d in out.iterdir() if d.is_dir() and d.name.startswith('scan-')]
            self.assertTrue(subdirs, '应新建带时间戳子目录而非覆盖')
            self.assertEqual((out/'report.md').read_text('utf-8'), first, '原始结果不得被覆盖')

    def test_target_not_modified(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td); proj=b/'p'; out=b/'o'; proj.mkdir()
            f=proj/'a.py'; _write(f,'x=1\n')
            before=f.read_bytes()
            scan_project(str(proj), str(out), force=True)
            self.assertEqual(f.read_bytes(), before, '目标项目文件被修改')

if __name__=='__main__':
    unittest.main()
