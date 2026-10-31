"""Self-contained per-file analysis core (language/category detection + AST/regex
symbol, import and call extraction).

This module is intentionally free of the heavy dependency chain carried by
``usit.scanner`` (``graph`` -> ``snapshot`` -> ``understanding_model`` -> ``inspect``/
``ctypes``).  The isolated ``python_worker.py`` subprocess only needs this module and
``usit.python_resolver``; importing ``usit.scanner`` there costs ~1s of interpreter
start-up on Windows for zero benefit.  ``usit.scanner`` re-exports every public name
below unchanged, so the public API and all scan output are byte-for-byte identical.

Nothing here opens, executes, imports or evaluates project code: analysis runs on
caller-supplied bytes with ``ast.parse`` (parse only) and regular expressions.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any

from .parser_contract import CONTRACT_VERSION, parser_capabilities

# ---------------------------------------------------------------------------
# Pure-data tables (unchanged values; moved verbatim from usit.scanner)
# ---------------------------------------------------------------------------

EXT_LANG = {'.py':'Python','.pyi':'Python','.js':'JavaScript','.jsx':'JavaScript/JSX','.mjs':'JavaScript','.cjs':'JavaScript','.ts':'TypeScript','.tsx':'TypeScript/JSX','.java':'Java','.kt':'Kotlin','.kts':'Kotlin','.c':'C','.h':'C/C++ header','.cc':'C++','.cpp':'C++','.cxx':'C++','.hpp':'C++ header','.cs':'C#','.go':'Go','.rs':'Rust','.php':'PHP','.rb':'Ruby','.swift':'Swift','.scala':'Scala','.sh':'Shell','.bash':'Shell','.ps1':'PowerShell','.sql':'SQL','.html':'HTML','.css':'CSS','.scss':'SCSS','.vue':'Vue','.svelte':'Svelte','.dart':'Dart','.lua':'Lua','.r':'R','.R':'R','.m':'Objective-C/MATLAB','.mm':'Objective-C++','.ex':'Elixir','.exs':'Elixir','.erl':'Erlang','.hrl':'Erlang header'}

# P0.2: 文件类别（与语言识别分离）
CATEGORY_BY_EXT = {
    '.py':'source_code','.pyi':'source_code','.js':'source_code','.jsx':'source_code','.mjs':'source_code','.cjs':'source_code',
    '.ts':'source_code','.tsx':'source_code','.java':'source_code','.kt':'source_code','.kts':'source_code',
    '.c':'source_code','.h':'source_code','.cc':'source_code','.cpp':'source_code','.cxx':'source_code','.hpp':'source_code',
    '.cs':'source_code','.go':'source_code','.rs':'source_code','.php':'source_code','.rb':'source_code',
    '.swift':'source_code','.scala':'source_code','.sh':'source_code','.bash':'source_code','.ps1':'source_code',
    '.sql':'source_code','.html':'source_code','.css':'source_code','.scss':'source_code','.vue':'source_code','.svelte':'source_code',
    '.dart':'source_code','.lua':'source_code','.r':'source_code','.m':'source_code','.mm':'source_code','.ex':'source_code','.exs':'source_code','.erl':'source_code',
    '.json':'config','.yaml':'config','.yml':'config','.toml':'config','.ini':'config','.cfg':'config','.conf':'config',
    '.properties':'config','.xml':'config','.gradle':'config','.pro':'config',
    '.md':'documentation','.markdown':'documentation','.txt':'documentation','.rst':'documentation',
    '.pdf':'documentation','.doc':'documentation','.docx':'documentation','.rtf':'documentation','.csv':'documentation',
    '.png':'image_asset','.jpg':'image_asset','.jpeg':'image_asset','.gif':'image_asset','.webp':'image_asset',
    '.bmp':'image_asset','.svg':'image_asset','.ico':'image_asset','.tiff':'image_asset',
    '.zip':'archive','.tar':'archive','.gz':'archive','.tgz':'archive','.bz2':'archive','.rar':'archive','.7z':'archive','.xz':'archive',
    '.log':'log',
    '.apk':'binary','.dex':'binary','.class':'binary','.jar':'binary','.so':'binary','.dll':'binary',
    '.exe':'binary','.bin':'binary','.db':'binary','.sqlite':'binary','.sqlite3':'binary','.mp4':'binary','.mov':'binary','.mp3':'binary','.wav':'binary','.dat':'binary',
}
NAME_CONFIG = {'Dockerfile','Makefile','Jenkinsfile','Procfile','Caddyfile','Vagrantfile','.gitignore','.gitattributes','.editorconfig'}
BACKUP_SEG_RE = re.compile(r'^(bak|backup|backups|old|old[_\-]?\d+|副本|旧|temp|tmp|archive)$', re.I)

PATTERNS = {
 'Python': [(r'^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(', 'function'),(r'^\s*class\s+([A-Za-z_]\w*)\b','class')],
 'JavaScript': [(r'^\s*(?:export\s+)?(?:async\s+)?function\s+([\w$]+)\s*\(', 'function'),(r'^\s*(?:export\s+)?class\s+([\w$]+)\b','class'),(r'^\s*(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>','function')],
 'TypeScript': [(r'^\s*(?:export\s+)?(?:async\s+)?function\s+([\w$]+)\s*\(', 'function'),(r'^\s*(?:export\s+)?class\s+([\w$]+)\b','class'),(r'^\s*(?:export\s+)?interface\s+([\w$]+)\b','interface'),(r'^\s*(?:export\s+)?type\s+([\w$]+)\s*=','type')],
 'Java': [(r'^\s*(?:public|protected|private|static|final|abstract|synchronized|native|default|\s)+\s*(?:[\w<>?,.\[\]]+\s+)+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:throws\s+[\w, ]+)?\s*\{?$', 'method'),(r'^\s*(?:public\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w*)','class'),(r'^\s*(?:public\s+)?interface\s+([A-Za-z_]\w*)','interface')],
 'Kotlin': [(r'^\s*(?:suspend\s+)?fun\s+(?:<[^>]+>\s*)?([A-Za-z_]\w*)\s*\(', 'function'),(r'^\s*(?:data\s+|sealed\s+|open\s+|abstract\s+)?class\s+([A-Za-z_]\w*)','class'),(r'^\s*object\s+([A-Za-z_]\w*)','object')],
 'Go': [(r'^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(', 'function'),(r'^\s*type\s+([A-Za-z_]\w*)\s+struct\b','struct'),(r'^\s*type\s+([A-Za-z_]\w*)\s+interface\b','interface')],
 'Rust': [(r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)\s*', 'function'),(r'^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)','struct'),(r'^\s*(?:pub\s+)?enum\s+([A-Za-z_]\w*)','enum'),(r'^\s*(?:pub\s+)?trait\s+([A-Za-z_]\w*)','trait')],
 'C': [(r'^\s*[\w*\s]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?$', 'function')],
 'C++': [(r'^\s*(?:[\w:*&<>~]+\s+)+([A-Za-z_~]\w*)\s*\([^;]*\)\s*(?:const\s*)?\{?$', 'function'),(r'^\s*class\s+([A-Za-z_]\w*)','class'),(r'^\s*struct\s+([A-Za-z_]\w*)','struct')],
 'C#': [(r'^\s*(?:public|private|protected|internal|static|async|virtual|override|sealed|\s)+\s*[\w<>?\[\].]+\s+([A-Za-z_]\w*)\s*\([^;]*\)', 'method'),(r'^\s*(?:public\s+)?class\s+([A-Za-z_]\w*)','class'),(r'^\s*(?:public\s+)?interface\s+([A-Za-z_]\w*)','interface')],
 'PHP': [(r'^\s*(?:public|protected|private|static|final|abstract|\s)*function\s+([A-Za-z_]\w*)\s*\(', 'function'),(r'^\s*class\s+([A-Za-z_]\w*)','class')],
 'Ruby': [(r'^\s*def\s+([\w!?=]+)', 'function'),(r'^\s*class\s+([A-Z]\w*)','class'),(r'^\s*module\s+([A-Z]\w*)','module')],
}
IMPORT_PATTERNS = {'Python':r'^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))','JavaScript':r'^\s*import\s+.*?from\s+[\'"]([^\'"]+)|require\(\s*[\'"]([^\'"]+)', 'TypeScript':r'^\s*import\s+.*?from\s+[\'"]([^\'"]+)|require\(\s*[\'"]([^\'"]+)', 'Java':r'^\s*import\s+(?:static\s+)?([\w.*]+)\s*;', 'Kotlin':r'^\s*import\s+([\w.*]+)', 'Go':r'^\s*"([\w./-]+)"\s*$', 'Rust':r'^\s*(?:use|extern\s+crate)\s+([\w:]+)', 'PHP':r'^\s*use\s+([\w\\]+)', 'Ruby':r'^\s*require(?:_relative)?\s+[\'"]([^\'"]+)'}

# P0.3: 框架候选关联（仅作候选，verified=false，不视为已连通）
FRAMEWORK_RE = {
 'Python': [(r'\b(app|router|blueprint)\.(route|get|post|put|delete|patch|add_url_rule)\s*\(', 'http_route_candidate')],
 'JavaScript': [(r'\b(app|router|server)\.(get|post|put|delete|patch|use)\s*\(', 'http_route_candidate')],
 'Kotlin': [(r'\b(startActivity|Intent\s*\(|navController\.navigate|NavHost\b)', 'android_navigation_candidate')],
 'Java': [(r'\b(startActivity|Intent\b|NavController|navigate\s*\()', 'android_navigation_candidate')],
 'TypeScript': [(r'\b(app|router)\.(get|post|put|delete|patch)\s*\(', 'http_route_candidate')],
}
CALL_KEYWORDS = {'if','for','while','switch','when','return','new','typeof','await','yield','catch','function','class','def','else','do','sizeof','cast','throw','using','with'}

# ---------------------------------------------------------------------------
# Pure per-file analysis functions (moved verbatim from usit.scanner)
# ---------------------------------------------------------------------------

def lang_for(path: Path) -> str:
    return EXT_LANG.get(path.suffix, 'Unknown')

def category_for(rel: str, lang: str) -> str:
    """P0.2: 文件类别与语言识别分离。"""
    p = Path(rel); ext = p.suffix.lower(); parts = p.parts
    if p.name.endswith('.bak') or any(BACKUP_SEG_RE.search(seg) for seg in parts):
        return 'backup'
    if ext in {'.zip','.tar','.gz','.tgz','.bz2','.rar','.7z','.xz'}:
        return 'archive'
    if ext in {'.png','.jpg','.jpeg','.gif','.webp','.bmp','.svg','.ico','.tiff'}:
        return 'image_asset'
    if ext == '.log':
        return 'log'
    if ext in {'.apk','.dex','.class','.jar','.so','.dll','.exe','.bin','.db','.sqlite','.sqlite3','.mp4','.mov','.mp3','.wav','.dat'}:
        return 'binary'
    if lang != 'Unknown':
        return 'source_code'
    if ext in CATEGORY_BY_EXT:
        return CATEGORY_BY_EXT[ext]
    if p.name in NAME_CONFIG:
        return 'config'
    if any(seg in ('generated','gen','build','dist','out') for seg in parts) or '.gen.' in p.name:
        return 'generated'
    return 'unknown'

def read_text(path: Path) -> str | None:
    try:
        raw=path.read_bytes()
        return decode_text(raw)
    except OSError: return None

def decode_text(raw: bytes) -> str | None:
    try:
        if b'\x00' in raw: return None
        for enc in ('utf-8-sig','utf-8','gb18030','latin-1'):
            try: return raw.decode(enc)
            except UnicodeDecodeError: pass
    except (UnicodeError,TypeError): return None
    return None

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(65536),b''): h.update(chunk)
    return h.hexdigest()

def _regex_symbols(lang: str, lines: list[str]) -> list[dict]:
    out=[]; patterns=PATTERNS.get(lang,[])
    for i,line in enumerate(lines,1):
        for pat,kind in patterns:
            m=re.search(pat,line)
            if m:
                out.append({'name':m.group(1),'kind':kind,'line':i,'evidence':line.strip()[:240]}); break
    return out

def _regex_imports(lang: str, lines: list[str]) -> list[dict]:
    out=[]; ip=IMPORT_PATTERNS.get(lang)
    if not ip: return out
    for i,line in enumerate(lines,1):
        m=re.search(ip,line)
        if m:
            dep=next((g for g in m.groups() if g),None)
            if dep: out.append({'name':dep,'line':i,'evidence':line.strip()[:240]})
    return out

def _excerpt(lines: list[str], lineno: int, width: int = 240) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno-1].strip()[:width]
    return ''

def _python_analysis(content: str, lines: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """P0.3: 用标准库 ast 做可靠的符号/调用解析（Python）。失败降级正则。"""
    try:
        tree=ast.parse(content)
    except (SyntaxError, ValueError) as exc:
        return _regex_symbols('Python',lines), _regex_imports('Python',lines), []
    symbols=[]; calls=[]; defined={}
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            symbols.append({'name':node.name,'kind':'function','line':node.lineno,'evidence':_excerpt(lines,node.lineno)})
            defined[node.name]=node.lineno
        elif isinstance(node,ast.ClassDef):
            symbols.append({'name':node.name,'kind':'class','line':node.lineno,'evidence':_excerpt(lines,node.lineno)})
            defined[node.name]=node.lineno
    imports=_regex_imports('Python',lines)
    imported={im['name'].split('.')[-1] for im in imports}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
            name=node.func.id
            if name in CALL_KEYWORDS: continue
            ln=node.lineno
            if name in defined:
                calls.append({'callee':name,'line':ln,'method':'python-ast','confidence':'high','resolved':True})
            elif name in imported:
                calls.append({'callee':name,'line':ln,'method':'python-ast','confidence':'low','resolved':False,'via_import':True})
            else:
                calls.append({'callee':name,'line':ln,'method':'python-ast','confidence':'low','resolved':False})
    return symbols, imports, calls

def _intra_file_calls(lang: str, lines: list[str], defined_names: set[str]) -> list[dict]:
    """P0.3: JS/Kotlin 用正则在文件内解析调用（仅解析到本文件已定义符号，不伪造跨文件）。"""
    out=[]; defs={}
    for i,line in enumerate(lines,1):
        for pat,_ in PATTERNS.get(lang,[]):
            m=re.search(pat,line)
            if m: defs[m.group(1)]=i
    defined=set(defs)|defined_names
    for i,line in enumerate(lines,1):
        if any(re.search(pat,line) for pat,_ in PATTERNS.get(lang,[])):
            continue
        for m in re.finditer(r'([A-Za-z_$][\w$]*)\s*\(', line):
            name=m.group(1)
            if name in CALL_KEYWORDS: continue
            out.append({'callee':name,'line':i,'column':m.start(1),'method':'regex-candidate','confidence':'low','resolved':False,'resolution_status':'UNKNOWN','diagnostic':'No semantic parser is available for this language.'})
    return out

def _framework_candidates(lang: str, lines: list[str]) -> list[dict]:
    """P0.3: 框架候选关联（Android 跳转 / 服务端路由），仅标记候选，不视为已连通。"""
    out=[]
    for pat,kind in FRAMEWORK_RE.get(lang,[]):
        for i,line in enumerate(lines,1):
            if re.search(pat,line):
                out.append({'kind':kind,'line':i,'method':'heuristic-framework','confidence':'low','candidate':True,'verified':False,'evidence':line.strip()[:240]})
    return out

def _degradation_metadata(row: dict[str, Any], lang: str) -> dict[str, Any]:
    """S1-B: describe whether this row's analysis was degraded, and how."""
    diagnostics = row.get("analysis_diagnostics") or []
    ref = None
    for idx, diag in enumerate(diagnostics):
        if isinstance(diag, dict) and diag.get("code") in (
            "PYTHON_SYNTAX_ERROR",
            "SEMANTIC_PARSER_UNAVAILABLE",
        ):
            ref = idx
            break
    capability = parser_capabilities()["languages"].get(lang)
    level = (capability or {}).get("level", "UNSUPPORTED")
    if lang == "Python":
        syntax_error = any(
            isinstance(d, dict) and d.get("code") == "PYTHON_SYNTAX_ERROR"
            for d in diagnostics
        )
        if syntax_error:
            return {
                "degraded": True,
                "reason": "PYTHON_SYNTAX_ERROR",
                "method": "regex-candidate",
                "resolution_status": "UNKNOWN",
                "diagnostics_ref": ref,
            }
        return {
            "degraded": False,
            "reason": None,
            "method": "python-ast",
            "resolution_status": "RESOLVED",
            "diagnostics_ref": None,
        }
    if level != "UNSUPPORTED":
        return {
            "degraded": True,
            "reason": "SEMANTIC_PARSER_UNAVAILABLE",
            "method": "regex-candidate",
            "resolution_status": "UNKNOWN",
            "diagnostics_ref": ref,
        }
    return {
        "degraded": True,
        "reason": "UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER",
        "method": "discovery-only",
        "resolution_status": "UNKNOWN",
        "diagnostics_ref": ref,
    }

def analyze_file(root: Path, path: Path, *, raw_bytes: bytes | None = None) -> dict[str,Any]:
    rel=path.relative_to(root).as_posix(); lang=lang_for(path); category=category_for(rel,lang)
    if raw_bytes is None:
        content=read_text(path); file_sha=sha(path); size_bytes=path.stat().st_size
    else:
        content=decode_text(raw_bytes); file_sha=hashlib.sha256(raw_bytes).hexdigest(); size_bytes=len(raw_bytes)
    row={'path':rel,'language':lang,'category':category,'size_bytes':size_bytes,'sha256':file_sha,'readable_text':content is not None,'symbols':[],'imports':[],'calls':[]}
    if content is None: return row
    # P0.7.10-V: when the row was produced by opening the path (raw_bytes is None,
    # i.e. benchmark/self-test callers) keep the exact decoded text on the row so
    # a downstream resolver can consume the captured snapshot instead of
    # re-opening the file.  Rows produced by the scan pipeline always carry
    # raw_bytes, so this key never reaches scanned artifacts; it is additionally
    # stripped before serialisation (see the manifest build) as defence in depth.
    if raw_bytes is None:
        row['_captured_text'] = content
    lines=content.splitlines()
    if lang=='Python':
        row['symbols'], row['imports'], row['calls'] = _python_analysis(content, lines)
        try:
            ast.parse(content, filename=rel)
        except (SyntaxError, ValueError) as exc:
            row['analysis_diagnostics']=[{'code':'PYTHON_SYNTAX_ERROR','severity':'ERROR','line':getattr(exc,'lineno',None),'message':type(exc).__name__}]
    else:
        row['symbols']=_regex_symbols(lang,lines)
        row['imports']=_regex_imports(lang,lines)
        defined={s['name'] for s in row['symbols']}
        row['calls']=_intra_file_calls(lang,lines,defined)
        if lang in parser_capabilities()['languages']:
            row['analysis_diagnostics']=[{'code':'SEMANTIC_PARSER_UNAVAILABLE','severity':'UNKNOWN',
                'message':'Text candidates are not semantic parse results; syntax validity is not measured.'}]
    row['framework_candidates']=_framework_candidates(lang,lines)
    row.setdefault('analysis_diagnostics',[])
    row['parser']={**parser_capabilities()['languages'].get(lang,{
        'parser_name':'discovery-only','parser_version':'1','level':'UNSUPPORTED',
        'supported':['file discovery'],'unsupported':['syntax and semantic parsing'],
        'degradation':'No semantic claims are made.'}), 'contract_version':CONTRACT_VERSION}
    row['analysis_degradation']=_degradation_metadata(row, lang)
    return row
