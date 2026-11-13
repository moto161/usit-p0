from __future__ import annotations
import ast, base64, hashlib, json, os, re, sys, tempfile, time, shutil
from pathlib import Path
from collections.abc import Mapping
from typing import Any
from .graph import validate_graph, validate_graph_trusted, render_validation_markdown
from .snapshot import DescriptorIdentity, capture
from .python_resolver import resolve_python_calls
from .parser_contract import CONTRACT_VERSION, parser_capabilities, adapt_graph_v1
from .file_understanding import build_file_understanding, verify_understanding_identities
from .js_ts_parser import analyze_js_ts_project, parser_availability
from .safety import (MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_READ_BYTES, MAX_SCAN_SECONDS,
                     MAX_OUTPUT_BYTES, MAX_CONCURRENCY, MAX_PARSER_TASK_SECONDS, ScanAuthorization, ScanBlocked,
                     canonical_project, canonical_output, is_reparse_point, overlaps)
from .limits import ParserTaskSupervisor, verify_safety_limits
from . import safeio

# P0.7.10-Z: the production scan path must never call the legacy by-path
# validator (``validate_graph``) -- it validates against the trusted snapshot
# captured from the audited reads instead.  The legacy symbol stays imported and
# aliased here purely as an audit anchor, so call-site inventories and the Z8
# test can prove a zero call count rather than inferring it from a diff.
_LEGACY_VALIDATE = validate_graph

SKIP_DIRS = {'.git','.hg','.svn','node_modules','vendor','.venv','venv','env','__pycache__','.pytest_cache','.mypy_cache','.idea','.vscode','build','dist','target','bin','obj','coverage','.next','.gradle'}
SKIP_NAMES = {'.env','.env.local','.env.production','.env.development'}

# P0.1: 中英文敏感名统一覆盖（密码清单/密钥/令牌/凭据/私钥等变体）
SENSITIVE_NAME = re.compile(
    r'(secret|credential|password|passwd|pwd|token|api[_-]?key|access[_-]?key|'
    r'private[_-]?key|id_rsa|id_ed25519|密钥|密码|口令|令牌|凭证|凭据|私钥|敏感|'
    r'阅后即焚|密码清单|secret[_-]?file|password[_-]?list)',
    re.I)
SECRET_NAME = SENSITIVE_NAME  # 别名，兼容目录过滤处的引用
SECRET_SUFFIXES = {'.pem','.key','.p12','.pfx','.keystore','.jks'}

EXT_LANG = {'.py':'Python','.pyi':'Python','.js':'JavaScript','.jsx':'JavaScript/JSX','.mjs':'JavaScript','.cjs':'JavaScript','.ts':'TypeScript','.tsx':'TypeScript/JSX','.java':'Java','.kt':'Kotlin','.kts':'Kotlin','.c':'C','.h':'C/C++ header','.cc':'C++','.cpp':'C++','.cxx':'C++','.hpp':'C++ header','.cs':'C#','.go':'Go','.rs':'Rust','.php':'PHP','.rb':'Ruby','.swift':'Swift','.scala':'Scala','.sh':'Shell','.bash':'Shell','.ps1':'PowerShell','.sql':'SQL','.html':'HTML','.css':'CSS','.scss':'SCSS','.vue':'Vue','.svelte':'Svelte','.dart':'Dart','.lua':'Lua','.r':'R','.R':'R','.m':'Objective-C/MATLAB','.mm':'Objective-C++','.ex':'Elixir','.exs':'Elixir','.erl':'Erlang','.hrl':'Erlang header'}
BUILD_MARKERS = {'package.json':'Node.js / npm-compatible','pnpm-lock.yaml':'pnpm','yarn.lock':'Yarn','package-lock.json':'npm','requirements.txt':'Python / pip','pyproject.toml':'Python / PEP 517','Pipfile':'Python / Pipenv','poetry.lock':'Python / Poetry','Cargo.toml':'Rust / Cargo','go.mod':'Go modules','pom.xml':'Java / Maven','build.gradle':'Gradle','build.gradle.kts':'Gradle Kotlin DSL','settings.gradle':'Gradle','settings.gradle.kts':'Gradle Kotlin DSL','CMakeLists.txt':'CMake','Makefile':'Make','meson.build':'Meson','Dockerfile':'Docker','docker-compose.yml':'Docker Compose','compose.yaml':'Docker Compose','Gemfile':'Ruby / Bundler','composer.json':'PHP / Composer','pubspec.yaml':'Dart / Flutter','Package.swift':'Swift Package Manager','*.sln':'Visual Studio solution','*.csproj':'MSBuild / .NET','*.xcodeproj':'Xcode project'}

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

def is_sensitive(rel: str) -> bool:
    """P0.1: 中英文敏感名统一识别（文件名、目录段、后缀）。"""
    p = Path(rel)
    name = p.name
    if name in SKIP_NAMES or name.lower().startswith('.env.'):
        return True
    if p.suffix.lower() in SECRET_SUFFIXES:
        return True
    if SENSITIVE_NAME.search(name):
        return True
    # 目录段含敏感名也视为敏感（如 密钥/ 私钥/ 密码 目录）
    for seg in p.parts[:-1]:
        if SENSITIVE_NAME.search(seg):
            return True
    return False

def discover_files(root: Path, *, deadline: float | None = None, clock=time.monotonic,
                   identities: dict[str, tuple[int, ...]] | None = None) -> list[Path]:
    """Bounded, deterministic discovery. Any unsafe path or budget ambiguity blocks the scan.

    P0.7.4: when ``identities`` is supplied it is filled with a platform-aware
    identity. Windows uses the stable file index ``(st_dev, st_ino)`` because
    Windows ``st_ctime_ns`` is not a reliable cross-call identity field; POSIX
    adds ctime, size and mtime to reduce inode-reuse ambiguity. A later open compares
    the *opened handle's* identity against
    this discovery-time identity, which is what detects a path that was swapped
    for a link (including an intermediate directory junction) after discovery.
    """
    root=canonical_project(root)
    out=[]; total_bytes=0; visited=0; stack=[root]
    while stack:
        if deadline is not None and clock() >= deadline:
            raise ScanBlocked('TOTAL_SCAN_TIMEOUT','The total scan deadline expired during discovery.')
        directory=stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries=[]
                for entry in iterator:
                    visited+=1
                    if visited>MAX_FILES*20:
                        raise ScanBlocked('DIRECTORY_ENTRY_BUDGET_EXCEEDED','Directory traversal exceeded the safe entry-count bound.')
                    entries.append(entry)
        except ScanBlocked:
            raise
        except OSError as exc:
            raise ScanBlocked('DIRECTORY_ACCESS_DENIED',f'A directory could not be safely enumerated ({type(exc).__name__}).') from exc
        entries.sort(key=lambda entry: entry.name.casefold())
        for entry in entries:
            if deadline is not None and clock() >= deadline:
                raise ScanBlocked('TOTAL_SCAN_TIMEOUT','The total scan deadline expired during discovery.')
            path=Path(entry.path)
            if is_reparse_point(path):
                raise ScanBlocked('REPARSE_POINT_REJECTED','A symbolic link, junction or reparse point was encountered.')
            name=entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name.casefold() in {x.casefold() for x in SKIP_DIRS} or SECRET_NAME.search(name):
                        continue
                    stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError as exc:
                raise ScanBlocked('FILE_TYPE_CHECK_FAILED',f'A directory entry could not be safely classified ({type(exc).__name__}).') from exc
            rel=path.relative_to(root).as_posix()
            if is_sensitive(rel):
                continue
            try:
                # A real os.stat is required here: on Windows ``DirEntry.stat``
                # is served from cached directory-scan data whose ``st_ino`` is
                # 0, so it cannot supply the file identity used to detect a
                # later substitution. ``os.stat(..., follow_symlinks=False)``
                # does not follow the final component and returns the index.
                info=os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ScanBlocked('FILE_STAT_FAILED',f'A candidate file could not be safely checked ({type(exc).__name__}).') from exc
            size=info.st_size
            if len(out)>=MAX_FILES:
                raise ScanBlocked('MAX_FILE_COUNT_EXCEEDED',f'More than {MAX_FILES} files are in scope; no file content was read.')
            if size>MAX_FILE_BYTES:
                raise ScanBlocked('MAX_FILE_SIZE_EXCEEDED',f'A candidate exceeds the {MAX_FILE_BYTES}-byte per-file limit; no content was read.')
            if total_bytes+size>MAX_TOTAL_READ_BYTES:
                raise ScanBlocked('MAX_TOTAL_READ_BYTES_EXCEEDED',f'The {MAX_TOTAL_READ_BYTES}-byte cumulative read budget would be exceeded; no content was read.')
            total_bytes+=size
            out.append(path)
            if identities is not None:
                # Windows reports creation-time semantics for st_ctime and
                # fstat/stat can differ across the handle boundary. Its file
                # index is the stable identity; POSIX additionally records
                # change time and content metadata to catch rapid inode reuse.
                base_identity = safeio.identity_of(info)
                if os.name == 'nt':
                    identities[rel] = base_identity
                else:
                    identities[rel] = (*base_identity, int(info.st_ctime_ns),
                                        int(info.st_size), int(info.st_mtime_ns))
    return sorted(out,key=lambda path:path.relative_to(root).as_posix().casefold())

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

def _write_scan_status(directory: Path, exc: BaseException) -> None:
    """Record an explicit terminal status for a scan that did not complete.

    P0.7.3: every blocked or incomplete scan leaves a machine-readable record so
    a caller never has to guess whether output was published.
    """
    status = 'BLOCKED' if isinstance(exc, ScanBlocked) else 'INCOMPLETE'
    code = exc.code if isinstance(exc, ScanBlocked) else 'UNEXPECTED_FAILURE'
    status_path = directory / ('usit-status-' + str(int(time.time() * 1000)) + '.json')
    try:
        status_path.write_text(json.dumps({'status': status, 'reason_code': code,
                                           'error_type': type(exc).__name__},
                                          ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except OSError:
        pass


def open_input_file(path: Path, limit: int, *, expected_identity: tuple[int, ...] | None = None,
                    identity_out: dict[str, Any] | None = None) -> bytes:
    """Open a discovered input file and read it without following an escape.

    P0.7.4 replaces the previous check-then-open approach with a handle-bound
    one, so safety no longer depends on "the window being short":

    1. The final path component is opened with the platform no-follow primitive
       (Windows ``FILE_FLAG_OPEN_REPARSE_POINT`` / POSIX ``O_NOFOLLOW``), so a
       link/junction/reparse point in the final component is *never* followed —
       the attribute check and the open are a single atomic operation.
    2. The descriptor requests read access only, so a concurrent write, rename or
       delete is denied while it is held.
    3. The descriptor's reparse attribute and hard-link count are checked, and its
       identity ``(st_dev, st_ino, st_ctime_ns)`` is compared against the identity recorded at
       discovery.  A mismatch means the path was substituted for a different file
       — including an *intermediate* directory junction, which the final-component
       no-follow cannot see — and the scan fails closed before reading.

    Every byte returned comes from the descriptor that passed these checks; the
    path is never resolved again after the descriptor exists.

    P0.7.10-ZRF: ``identity_out`` is an optional mapping that, when supplied,
    receives ``{"identity": DescriptorIdentity(...)}`` built from the *same*
    ``os.fstat`` that authorised the read.  This lets the capture pipeline bind
    the captured bytes to the identity of the descriptor they came from instead
    of re-deriving an identity from the path later (the P0.7.10-ZR R1/R3 binding
    gap).  The parameter is optional and the return shape is unchanged, so every
    existing caller and test patch keeps working; callers that do not supply it
    simply get no descriptor identity (and the snapshot marks such records as
    ``self_observed`` rather than ``descriptor``).
    """
    if is_reparse_point(path):
        raise ScanBlocked('REPARSE_POINT_REJECTED', 'Input path became a link, junction or reparse point before it was opened.')
    try:
        fd = safeio.open_no_follow(path)
    except OSError as exc:
        # On POSIX, O_NOFOLLOW makes a final-component symlink fail with ELOOP;
        # surface that as the same rejection code rather than a generic read error.
        errno_value = getattr(exc, 'errno', None)
        if errno_value in {getattr(os, 'ELOOP', -1), getattr(os, 'EMLINK', -1), getattr(os, 'ENOENT', -1)}:
            raise ScanBlocked('REPARSE_POINT_REJECTED', 'Input path is a link, junction or reparse point, or vanished before it was read.') from exc
        raise ScanBlocked('FILE_READ_FAILED', f'Input read failed ({type(exc).__name__}).') from exc
    try:
        info = os.fstat(fd)
        if identity_out is not None:
            # P0.7.10-ZRF: the identity of the descriptor the bytes will come
            # from, taken at the same fstat that authorises the read.  Defensive
            # against hosts (or test doubles) whose stat result lacks the
            # nanosecond field: such an identity is simply not offered, and the
            # snapshot falls back to the fail-closed self-observed path.
            try:
                mtime_ns: int | None = int(info.st_mtime_ns)
            except (AttributeError, TypeError, ValueError):
                mtime_ns = None
            identity_out['identity'] = DescriptorIdentity(
                int(info.st_dev), int(info.st_ino), int(info.st_size), mtime_ns)
        if safeio.is_reparse(info):
            raise ScanBlocked('REPARSE_POINT_REJECTED', 'Input path is a symbolic link, junction or reparse point.')
        if getattr(info, 'st_nlink', 1) > 1:
            raise ScanBlocked('HARD_LINK_REJECTED', 'Input file has multiple hard links; refusing to read a multiply-linked file.')
        if expected_identity is not None:
            expected = tuple(expected_identity)
            if safeio.identity_of(info) != expected[:2]:
                raise ScanBlocked('FILE_SUBSTITUTED_DURING_SCAN',
                                  'The opened file is not the file that was discovered; the input was substituted before reading.')
            if len(expected) >= 3:
                try:
                    opened_metadata = (int(info.st_ctime_ns), int(info.st_size),
                                       int(info.st_mtime_ns))
                except (AttributeError, TypeError, ValueError):
                    raise ScanBlocked('FILE_IDENTITY_UNAVAILABLE',
                                      'The opened file identity metadata is incomplete; refusing to read.')
                if any(actual != wanted for actual, wanted in zip(opened_metadata, expected[2:5])):
                    raise ScanBlocked('FILE_SUBSTITUTED_DURING_SCAN',
                                      'The opened file metadata changed after discovery; refusing to read.')
        return os.read(fd, limit + 1)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(65536),b''): h.update(chunk)
    return h.hexdigest()

def build_markers(root: Path, files: list[Path]) -> list[dict[str,str]]:
    names={p.name for p in files}; found=[]
    for marker, stack in BUILD_MARKERS.items():
        if marker.startswith('*.'):
            matches=sorted(n for n in names if n.endswith(marker[1:]))
            found.extend({'marker':n,'stack':stack} for n in matches)
        elif marker in names: found.append({'marker':marker,'stack':stack})
    return found

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
                # Import evidence identifies a referenced name, not its definition.
                # Until cross-file/module resolution exists, keep the call explicitly unresolved.
                calls.append({'callee':name,'line':ln,'method':'python-ast','confidence':'low','resolved':False,'via_import':True})
            else:
                calls.append({'callee':name,'line':ln,'method':'python-ast','confidence':'low','resolved':False})
    return symbols, imports, calls

def _excerpt(lines: list[str], lineno: int, width: int = 240) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno-1].strip()[:width]
    return ''

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
            continue  # 跳过定义行，避免把 `function foo(` 误判为调用自己
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
    """S1-B: describe whether this row's analysis was degraded, and how.

    This is *analysis-method* metadata. It is NOT a claim that every
    conclusion in the row is verified: a successful AST parse still leaves
    unresolved / low-confidence calls. Degraded results must never be
    presented as a complete semantic parse.
    """
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
    # S1-B: additive, optional row-level degradation metadata. `symbols[]`,
    # `calls[]` structures and schema versions are intentionally unchanged.
    row['analysis_degradation']=_degradation_metadata(row, lang)
    return row

MAX_PREVIOUS_MANIFEST_BYTES = 50 * 1024 * 1024


def load_previous(out: Path) -> dict[str,str]:
    """Load the optional incremental baseline as bounded, no-follow input.

    A missing, malformed, oversized, linked or otherwise unsafe prior manifest
    is not trusted as an incremental baseline; the scan safely falls back to a
    full classification instead of following or allocating an unbounded file.
    """
    p=out/'scan_manifest.json'
    try:
        raw=safeio.read_bounded_no_follow(p, MAX_PREVIOUS_MANIFEST_BYTES)
        data=json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('files', []), list):
            return {}
        return {x['path']:x['sha256'] for x in data.get('files', [])
                if isinstance(x, dict) and isinstance(x.get('path'), str)
                and isinstance(x.get('sha256'), str)}
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        return {}

def _edge(source: str, target: str, etype: str, ev: dict, **extra: Any) -> dict[str,Any]:
    e={'source':source,'target':target,'type':etype,'evidence':ev}
    e.update(extra)
    return e

def _scrub_sensitive_graph(graph: dict) -> dict:
    """P0.1 防御纵深：移除任一与敏感文件相关的节点/边（即便前面漏网也不进输出）。"""
    nodes=graph.get('nodes',[]); edges=graph.get('edges',[])
    sensitive_ids=set()
    for n in nodes:
        ev=n.get('evidence',{}) or {}
        pth=ev.get('path') or (n.get('id','').startswith('file:') and n['id'][5:]) or ''
        if pth and is_sensitive(pth):
            sensitive_ids.add(n['id'])
    kept_nodes=[n for n in nodes if n['id'] not in sensitive_ids]
    kept_edges=[e for e in edges if e['source'] not in sensitive_ids and e['target'] not in sensitive_ids]
    graph['nodes']=kept_nodes; graph['edges']=kept_edges
    return graph

def _scrub_sensitive_manifest(manifest: dict) -> dict:
    manifest['files']=[f for f in manifest.get('files',[]) if not is_sensitive(f.get('path',''))]
    return manifest

def _scrub_sensitive_plan(plan: dict) -> dict:
    for item in plan.get('items',[]):
        item['evidence']=[e for e in item.get('evidence',[]) if not (isinstance(e,str) and is_sensitive(e))]
    inv=plan.get('test_inventory')
    if inv:
        for key in ('test_source','test_case_document','screenshot_evidence','generic_asset'):
            inv[key]=[x for x in inv.get(key,[]) if not is_sensitive(x.get('path',''))]
    return plan

def _record_grouping_path(record: Any) -> Any:
    """Grouping key of a parser-contract record: ``source_location.file_path``.

    Mirrors ``file_understanding._record_group_key`` (the conflict/grouping key
    is ``source_location.file_path``, not the top-level ``file_path``).
    """
    if not isinstance(record, Mapping):
        return None
    loc=record.get('source_location')
    if isinstance(loc, Mapping):
        path=loc.get('file_path')
        if isinstance(path, str):
            return path
    return None

def _build_file_understanding_text(manifest: dict, parser_contract: dict) -> tuple[str | None, dict]:
    """S1-C (IMPLEMENT-02B): build, self-check and serialise the file-level
    understanding payload (``usit-file-understanding-v1``).

    Never raises: any build / identity-verification / serialisation failure
    degrades to ``(None, status-with-FAILED)`` so the scan keeps its existing
    semantics and the absence of ``understanding.json`` stays explicit and
    auditable instead of being faked as success.

    Sensitive-path records are excluded BEFORE categorisation (the manifest is
    scrubbed at publish time but the parser-contract records are not, so a
    sensitive path could otherwise surface via ``unmatched_records``).  The
    exclusion is counted in the status -- never silent, never in the output.
    """
    records=parser_contract.get('records',[]) if isinstance(parser_contract, Mapping) else []
    excluded=0
    kept: list[Any]=[]
    for record in records:
        path=_record_grouping_path(record)
        if isinstance(path,str) and path and is_sensitive(path):
            excluded+=1
            continue
        kept.append(record)
    try:
        understanding=build_file_understanding(manifest.get('files',[]),kept)
        verify_understanding_identities(understanding,total_input_records=len(kept))
        text=json.dumps(understanding,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
        return text, {'status':'COMPLETE','bytes':len(text.encode('utf-8')),'sensitive_records_excluded':excluded}
    except Exception as exc:
        return None, {'status':'FAILED','error_type':type(exc).__name__,'error_message':str(exc),
                      'sensitive_records_excluded':excluded}

def _scan_target_inventory(root: Path, rows: list[dict], content_by_path: dict[str,str]) -> dict:
    """P0.4: 测试资产分类（测试代码 / 测试文档 / 截图证据 / 通用资产）。"""
    inv={'test_source':[],'test_case_document':[],'screenshot_evidence':[],'generic_asset':[],
         'execution_status':'NOT_RUN','coverage_status':'UNKNOWN'}
    code_ext={'.py','.js','.ts','.jsx','.tsx','.kt','.java','.go','.rs','.php','.rb','.cs','.c','.cpp','.cc','.h','.hpp','.m','.mm','.swift','.scala','.dart','.ex','.exs','.erl'}
    img_ext={'.png','.jpg','.jpeg','.gif','.webp','.bmp','.svg','.ico','.tiff'}
    for r in rows:
        rel=r['path']; name=Path(rel).name; ext=Path(rel).suffix.lower()
        is_test_code = (re.search(r'(^|/)(tests?|specs?)(/|$)',rel) or re.search(r'(^|/)(test_|_test\.|spec\.|_spec\.)',name,re.I)) and ext in code_ext
        is_test_doc = ext in ('.md','.markdown','.txt','.rst') and re.search(r'test|测试|TEST',name,re.I)
        is_screenshot = ext in img_ext and re.search(r'test|截图|shot|screen|screen_',name,re.I)
        if is_test_code:
            fw=_detect_test_framework(r)
            asset={'path':rel,'framework':fw['framework'],'entry':rel,'evidence':fw['evidence'],
                   'executable_asset_present':True,'execution_status':'NOT_RUN','test_cases':[],
                   'associations':[],'diagnostics':[]}
            if r['language']=='Python' and r.get('readable_text'):
                try:
                    tree=ast.parse(content_by_path[rel],filename=rel)
                    module_index={}
                    module_paths={source_row.get('module') for source_row in rows if source_row.get('module')}
                    for source_row in rows:
                        for symbol in source_row.get('module_symbols',[]):
                            module_index.setdefault(source_row.get('module') or '',{}).setdefault(symbol['name'],[]).append((source_row['path'],symbol))
                    imported={}
                    for stmt in tree.body:
                        if isinstance(stmt,ast.ImportFrom):
                            base=_python_relative_module(r['path'],stmt.level,stmt.module)
                            for alias in stmt.names:
                                if alias.name!='*': imported[alias.asname or alias.name]=(base,alias.name,stmt.lineno)
                    for node in ast.walk(tree):
                        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) or not node.name.startswith('test'):
                            continue
                        test={'name':node.name,'source_location':{'file_path':rel,'line':node.lineno},'assertion_count':0}
                        called_import_aliases=set()
                        for child in ast.walk(node):
                            if isinstance(child,ast.Assert) or (isinstance(child,ast.Call) and isinstance(child.func,ast.Attribute) and child.func.attr.startswith('assert')):
                                test['assertion_count']+=1
                            if isinstance(child,ast.Call) and isinstance(child.func,ast.Name) and child.func.id in imported:
                                called_import_aliases.add(child.func.id)
                                mod,symbol,import_line=imported[child.func.id]
                                candidates=module_index.get(mod or '',{}).get(symbol,[])
                                association_id=hashlib.sha256(
                                    f"{rel}:{node.name}:{import_line}:{child.func.id}:{mod or ''}.{symbol}".encode('utf-8')).hexdigest()[:20]
                                if len(candidates)==1:
                                    target_path,target_symbol=candidates[0]
                                    asset['associations'].append({'test_name':node.name,'target':f"{mod}.{symbol}" if mod else symbol,
                                        'target_file':target_path,'target_line':target_symbol['line'],'association_method':'explicit_import_and_call',
                                        'association_id':association_id,'imported_as':child.func.id,
                                        'target_symbol_id':f"symbol:{target_path}#{symbol}:{target_symbol['line']}",
                                        'confidence':0.9,'resolution_status':'RESOLVED','validity_state':'CURRENT',
                                        'coverage_claim':'NOT_PROVEN','failure_reason':None,'evidence':[
                                            {'file_path':rel,'line':import_line,'kind':'import'},
                                            {'file_path':rel,'line':child.lineno,'kind':'call'},
                                            {'file_path':target_path,'line':target_symbol['line'],'kind':'definition'}]})
                                else:
                                    if candidates:
                                        reason='TARGET_SYMBOL_AMBIGUOUS'
                                    elif mod not in module_paths:
                                        reason='TARGET_MODULE_MISSING'
                                    else:
                                        reason='TARGET_SYMBOL_MISSING'
                                    asset['associations'].append({'test_name':node.name,'target':symbol,'association_method':'explicit_import_but_target_not_unique',
                                        'association_id':association_id,'imported_as':child.func.id,
                                        'target_file_candidates':[item[0] for item in candidates],
                                        'confidence':0.0,'resolution_status':'AMBIGUOUS' if candidates else 'UNKNOWN',
                                        'validity_state':'INVALIDATED','failure_reason':reason,'coverage_claim':'NOT_PROVEN',
                                        'evidence':[{'file_path':rel,'line':import_line,'kind':'import'},
                                                    {'file_path':rel,'line':child.lineno,'kind':'call'}]})
                        for local,(mod,symbol,import_line) in imported.items():
                            if local in called_import_aliases:
                                continue
                            candidates=module_index.get(mod or '',{}).get(symbol,[])
                            association_id=hashlib.sha256(
                                f"{rel}:{node.name}:{import_line}:{local}:{mod or ''}.{symbol}".encode('utf-8')).hexdigest()[:20]
                            asset['associations'].append({'test_name':node.name,'target':f"{mod}.{symbol}" if mod else symbol,
                                'association_method':'explicit_import_without_test_call','association_id':association_id,
                                'imported_as':local,'target_file_candidates':[item[0] for item in candidates],
                                'confidence':0.0,'resolution_status':'UNKNOWN','validity_state':'INVALIDATED',
                                'failure_reason':'NO_CALL_REFERENCE_TO_IMPORTED_ALIAS','coverage_claim':'NOT_PROVEN',
                                'evidence':[{'file_path':rel,'line':import_line,'kind':'import'},
                                            {'file_path':rel,'line':node.lineno,'kind':'test_without_matching_call'}]})
                        asset['test_cases'].append(test)
                    for test in asset['test_cases']:
                        if test['assertion_count']==0:
                            asset['diagnostics'].append({'code':'NO_STATIC_ASSERTION_FOUND','test_name':test['name'],'severity':'UNKNOWN'})
                except (OSError,UnicodeError,SyntaxError,ValueError) as exc:
                    asset['diagnostics'].append({'code':'TEST_SOURCE_PARSE_FAILED','severity':'UNKNOWN','reason':type(exc).__name__})
            elif r['language'] in {'JavaScript','JavaScript/JSX','TypeScript','TypeScript/JSX'}:
                calls=r.get('calls',[])
                test_names=sorted({call.get('test_name') for call in calls if call.get('test_name')})
                import_rows=[item for item in r.get('imports',[]) if item.get('bindings')]
                for test_name in test_names:
                    test_calls=[call for call in calls if call.get('test_name')==test_name]
                    test={'name':test_name,'source_location':{'file_path':rel,'line':min(c['line'] for c in test_calls)},
                          'assertion_count':sum(1 for c in test_calls if str(c.get('callee','')).startswith(('expect','assert')))}
                    asset['test_cases'].append(test)
                    seen_imports=set()
                    for call in test_calls:
                        imp=call.get('import_evidence') or {}
                        target=call.get('target') or {}
                        if not imp:
                            continue
                        alias=imp.get('imported_as') or call.get('callee','')
                        key=(imp.get('line'),alias)
                        seen_imports.add(key)
                        association_id=hashlib.sha256(
                            f"{rel}:{test_name}:{imp.get('line')}:{alias}:{target.get('id','') or call.get('resolution_status')}".encode('utf-8')).hexdigest()[:20]
                        status=call.get('resolution_status','UNKNOWN')
                        current=status in {'RESOLVED','EXTERNAL'}
                        reason=None if current else ('TARGET_MODULE_MISSING' if call.get('diagnostic')=='PROJECT_MODULE_NOT_FOUND' else 'TARGET_SYMBOL_MISSING')
                        association={'test_name':test_name,'target':target.get('qualified_name') or call.get('callee'),
                            'association_method':'typescript-compiler-import-and-call','association_id':association_id,
                            'imported_as':alias,'confidence':0.9 if status=='RESOLVED' else 0.0,
                            'resolution_status':status,'validity_state':'CURRENT' if current else 'INVALIDATED',
                            'coverage_claim':'NOT_PROVEN','failure_reason':reason,
                            'evidence':[{'file_path':rel,'line':imp.get('line'),'kind':'import','module':imp.get('module')},
                                        {'file_path':rel,'line':call.get('line'),'column':call.get('column'),'kind':'call'}]}
                        if target.get('file'):
                            association.update({'target_file':target['file'],'target_line':target.get('line'),'target_symbol_id':target.get('id')})
                            association['evidence'].append({'file_path':target['file'],'line':target.get('line'),'kind':'definition'})
                        if call.get('candidate_targets'):
                            association['target_file_candidates']=call['candidate_targets']
                        asset['associations'].append(association)
                    for imp in import_rows:
                        for binding in imp.get('bindings',[]):
                            alias=binding.get('local')
                            if not alias or (imp.get('line'),alias) in seen_imports:
                                continue
                            association_id=hashlib.sha256(f"{rel}:{test_name}:{imp.get('line')}:{alias}:{imp.get('module','')}".encode('utf-8')).hexdigest()[:20]
                            asset['associations'].append({'test_name':test_name,'target':imp.get('module','')+'.'+binding.get('imported',''),
                                'association_method':'typescript-explicit-import-without-test-call','association_id':association_id,
                                'imported_as':alias,'confidence':0.0,'resolution_status':'UNKNOWN','validity_state':'INVALIDATED',
                                'coverage_claim':'NOT_PROVEN','failure_reason':'NO_CALL_REFERENCE_TO_IMPORTED_ALIAS',
                                'evidence':[{'file_path':rel,'line':imp.get('line'),'kind':'import','module':imp.get('module')},
                                            {'file_path':rel,'line':test['source_location']['line'],'kind':'test_without_matching_call'}]})
                    if test['assertion_count']==0:
                        asset['diagnostics'].append({'code':'NO_STATIC_ASSERTION_FOUND','test_name':test_name,'severity':'UNKNOWN'})
            else:
                asset['diagnostics'].append({'code':'SEMANTIC_TEST_DISCOVERY_UNSUPPORTED','severity':'UNKNOWN','reason':'No contract parser for this language.'})
            inv['test_source'].append(asset)
        elif is_test_doc:
            inv['test_case_document'].append({'path':rel,'executable':False})
        elif is_screenshot:
            inv['screenshot_evidence'].append({'path':rel,'executable':False,'note':'仅作为人工/视觉辅助证据，不可当作可执行测试'})
        elif ext in img_ext or ext in {'.zip','.tar','.gz','.tgz','.rar','.7z','.pdf','.doc','.docx'}:
            inv['generic_asset'].append({'path':rel})
    return inv


def _python_relative_module(path: str, level: int, module: str | None) -> str | None:
    if level == 0:
        return module or ''
    parts=Path(path).with_suffix('').as_posix().split('/')[:-1]
    if Path(path).name=='__init__.py': parts=Path(path).with_suffix('').as_posix().split('/')
    if level:
        remove=level-1
        if remove>len(parts): return None
        parts=parts[:len(parts)-remove]
    if module: parts.extend(module.split('.'))
    return '.'.join(parts)

def _detect_test_framework(r: dict) -> dict:
    # 简易框架识别：基于符号/导入证据
    blob=' '.join(s.get('evidence','') for s in r.get('symbols',[])) + ' ' + ' '.join(i.get('evidence','') for i in r.get('imports',[]))
    if re.search(r'unittest|pytest',blob,re.I): return {'framework':'Python unittest/pytest','evidence':blob[:120]}
    if re.search(r'jest|mocha|vitest',blob,re.I): return {'framework':'JS jest/mocha','evidence':blob[:120]}
    if re.search(r'junit|androidx\.test|org\.junit',blob,re.I): return {'framework':'JVM JUnit','evidence':blob[:120]}
    if re.search(r'go test|testing\.T',blob,re.I): return {'framework':'Go testing','evidence':blob[:120]}
    return {'framework':'unknown','evidence':blob[:120]}

def scan_project(project: str, out_dir: str, force: bool = False, *, authorization=None) -> dict[str,Any]:
    """Run a bounded scan only after explicit, scope-bound authorization."""
    if authorization is None:
        raise ScanBlocked('AUTHORIZATION_MISSING','An explicit project/action/output authorization is required.')
    # P0.7.2: fail closed unless every protocol limit is present and unaltered.
    verify_safety_limits()
    root=canonical_project(project); requested_out=canonical_output(out_dir)
    auth=authorization if isinstance(authorization,ScanAuthorization) else ScanAuthorization.from_mapping(authorization)
    auth.validate(root,requested_out)
    if force and 'overwrite_existing_output' not in auth.allowed_actions:
        raise ScanBlocked('OVERWRITE_NOT_AUTHORIZED','Replacing prior output requires explicit authorization.')
    if overlaps(root,requested_out):
        raise ScanBlocked('OUTPUT_OVERLAPS_PROJECT','The output path must not overlap the project tree.')
    if requested_out.exists() and not requested_out.is_dir():
        raise ScanBlocked('OUTPUT_NOT_DIRECTORY','The output path exists and is not a directory.')
    final_out=requested_out
    if (requested_out/'scan_manifest.json').exists() and not force:
        from datetime import datetime
        final_out=requested_out/f"scan-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    started=time.monotonic(); deadline=started+MAX_SCAN_SECONDS
    # P0.7.3: discovery and the read loop are guarded too. Previously a failure
    # here propagated without any terminal status record, so a caller could not
    # tell a blocked scan from a crash.
    try:
        identities={}
        paths=discover_files(root,deadline=deadline,identities=identities); raw_by_path={}; total=0
        # P0.7.10-ZRF: descriptor identities, taken from the same fstat that
        # authorised each read, travel with the bytes so capture() never has to
        # re-derive an identity from the path (the P0.7.10-ZR R1/R3 binding gap).
        descriptor_ids: dict[str, DescriptorIdentity | None] = {}
        for p in paths:
            if time.monotonic()>=deadline: raise ScanBlocked('TOTAL_SCAN_TIMEOUT','Total scan deadline expired while reading inputs.')
            # P0.7.4: read through the guarded, handle-bound opener. The identity
            # recorded at discovery is compared against the opened descriptor, so
            # a swapped or linked path (including an intermediate junction) is
            # detected at open time instead of being silently followed.
            rel=p.relative_to(root).as_posix()
            expected_identity = identities.get(rel)
            if expected_identity is None:
                raise ScanBlocked('FILE_IDENTITY_UNAVAILABLE',
                                  'Discovery did not provide a verifiable identity for an input file.')
            # Recheck the path metadata immediately before opening, then let
            # open_input_file compare the opened descriptor as well. This
            # closes the POSIX unlink/recreate window even on filesystems that
            # rapidly recycle inode numbers.
            try:
                pre_open_info = os.stat(p, follow_symlinks=False)
            except OSError as exc:
                raise ScanBlocked('FILE_STAT_FAILED',
                                  'Input changed or became inaccessible before opening.') from exc
            if safeio.identity_of(pre_open_info) != tuple(expected_identity[:2]):
                raise ScanBlocked('FILE_SUBSTITUTED_DURING_SCAN',
                                  'The input path no longer identifies the file recorded at discovery.')
            if os.name != 'nt' and len(expected_identity) >= 5:
                observed_metadata = (int(pre_open_info.st_ctime_ns),
                                     int(pre_open_info.st_size),
                                     int(pre_open_info.st_mtime_ns))
                if observed_metadata != tuple(expected_identity[2:5]):
                    raise ScanBlocked('FILE_SUBSTITUTED_DURING_SCAN',
                                      'The input metadata changed after discovery; refusing to open it.')
            box: dict[str, Any] = {}
            raw=open_input_file(p, MAX_FILE_BYTES, expected_identity=expected_identity,
                                identity_out=box)
            descriptor_ids[rel]=box.get('identity')
            if len(raw)>MAX_FILE_BYTES: raise ScanBlocked('MAX_FILE_SIZE_EXCEEDED','Input exceeded the per-file read limit.')
            total+=len(raw)
            if total>MAX_TOTAL_READ_BYTES: raise ScanBlocked('MAX_TOTAL_READ_BYTES_EXCEEDED','Cumulative input read budget exceeded.')
            # P0.7.10-ZRF: the post-read recheck compares the full identity of the
            # descriptor that produced the bytes, not just the size -- a same-size
            # substitution or an in-place rewrite inside the read window is now
            # detected instead of flowing into the snapshot with mismatched
            # evidence.  (Metadata only; it cannot detect a rewrite that
            # preserves size AND mtime_ns -- that limitation is documented.)
            desc=descriptor_ids[rel]
            try: current=p.stat()
            except OSError as exc: raise ScanBlocked('FILE_STAT_FAILED','Input changed or became inaccessible.') from exc
            if desc is not None:
                if (int(current.st_dev),int(current.st_ino))!=(desc.dev,desc.ino) \
                   or int(current.st_size)!=desc.size or int(current.st_mtime_ns)!=desc.mtime_ns:
                    raise ScanBlocked('INPUT_CHANGED_DURING_SCAN','Input changed while being read.')
            elif current.st_size!=len(raw):
                raise ScanBlocked('INPUT_CHANGED_DURING_SCAN','Input changed while being read.')
            raw_by_path[p]=raw
    except Exception as exc:
        _write_scan_status(requested_out.parent, exc)
        raise
    stage=Path(tempfile.mkdtemp(prefix='.usit-stage-',dir=str(final_out.parent)))
    try:
        summary=_scan_project_impl(root,stage,force,paths,raw_by_path,deadline,load_previous(final_out),descriptor_ids)
        actual=sum(f.stat().st_size for f in stage.rglob('*') if f.is_file())
        if actual>MAX_OUTPUT_BYTES:
            raise ScanBlocked('MAX_OUTPUT_BYTES_EXCEEDED','Generated output exceeds the 256 MiB budget; staged results were not published.')
        if time.monotonic()>=deadline:
            raise ScanBlocked('TOTAL_SCAN_TIMEOUT','Total scan deadline expired before publishing output.')
        backup=None
        if final_out.exists():
            if not force:
                raise ScanBlocked('OUTPUT_CONFLICT','Output already exists; choose a new output directory.')
            backup=final_out.with_name(final_out.name+'.preserved-'+str(int(time.time()*1000)))
            final_out.rename(backup)
        try: stage.rename(final_out)
        except Exception:
            if backup and backup.exists() and not final_out.exists(): backup.rename(final_out)
            raise
        summary['output_dir']=str(final_out)
        return summary
    except Exception as exc:
        if stage.exists():
            try: shutil.rmtree(stage)
            except OSError: pass
        _write_scan_status(requested_out.parent, exc)
        raise


PYTHON_WORKER = Path(__file__).with_name('python_worker.py')
PYTHON_ANALYZE_TASK = 'analyze:python-batch'
PYTHON_RESOLVE_TASK = 'resolve-python-calls'


def _python_worker_argv(action: str) -> list[str]:
    """argv for the isolated Python parser worker; never a shell, eval or exec."""
    return [sys.executable, str(PYTHON_WORKER), action]


def _run_python_worker(supervisor: ParserTaskSupervisor, name: str, action: str, payload: dict) -> dict:
    """Run one isolated, supervised Python worker task and decode its JSON result.

    The task is launched as an argv-only subprocess with a controlled JSON stdin
    and stdout (no shell, no ``eval``/``exec``, no target-project script).  The
    supervisor enforces the per-task timeout, kills the child on timeout and
    verifies termination; a non-zero exit or malformed output fails closed.
    """
    completed = supervisor.run_subprocess(
        name, _python_worker_argv(action), input_text=json.dumps(payload, ensure_ascii=False))
    if completed['returncode'] != 0:
        raise ScanBlocked('PARSER_CRASHED',
                          f'Parser task {name} exited with code {completed["returncode"]}; no graph was published.')
    try:
        result = json.loads(completed['stdout'])
    except json.JSONDecodeError as exc:
        raise ScanBlocked('PARSER_OUTPUT_INVALID',
                          f'Parser task {name} returned malformed JSON; no graph was published.') from exc
    if not isinstance(result, dict):
        raise ScanBlocked('PARSER_OUTPUT_INVALID',
                          f'Parser task {name} returned an unexpected payload; no graph was published.')
    return result


def _resolver_text(raw: bytes) -> str:
    """Reproduce ``Path.read_text(encoding='utf-8', errors='replace')`` exactly."""
    text = raw.decode('utf-8', 'replace')
    return text.replace('\r\n', '\n').replace('\r', '\n') if '\r' in text else text


def _scan_project_impl(root: Path, out: Path, force: bool, paths: list[Path], raw_by_path: dict[Path,bytes], deadline: float, previous_seed: dict[str,str], descriptor_ids: dict[str, DescriptorIdentity | None] | None = None) -> dict[str,Any]:
    # P0.1 safety behavior: preserve prior outputs by choosing a fresh timestamped path unless force is explicitly authorized.
    if (out/'scan_manifest.json').exists() and not force:
        from datetime import datetime
        out=out/f"scan-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True,exist_ok=True)
    previous=previous_seed; rows=[]; errors=[]; content_by_path={}; raw_by_rel={}
    # P0.7.10-ZRF: per-file descriptor identities handed over by the read loop;
    # capture() binds each record to these instead of stat-ing the path.
    ids_for_capture: dict[str, DescriptorIdentity | None] = {}
    # P0.7.2/P0.7.4: one supervisor per scan enforces the per-task timeout and the
    # concurrency cap for every parser task, and records an auditable trail. All
    # parser work now runs as a supervised subprocess, so a stuck task is a child
    # that can be killed and its termination verified, instead of an
    # uninterruptible thread that could only be abandoned.
    supervisor=ParserTaskSupervisor()
    analyze_payload=[]
    for p in paths:
        if time.monotonic()>=deadline: raise ScanBlocked('TOTAL_SCAN_TIMEOUT','Total scan deadline expired during analysis.')
        rel=p.relative_to(root).as_posix()
        raw=raw_by_path[p]; raw_by_rel[rel]=raw
        content_by_path[rel]=decode_text(raw) or ''
        ids_for_capture[rel]=descriptor_ids.get(rel) if descriptor_ids else None
        analyze_payload.append({'path':rel,'content_b64':base64.b64encode(raw).decode('ascii')})
    # P0.7.4: per-file Python (and other-language) analysis runs in an isolated,
    # supervised subprocess. The worker receives the already-read content over a
    # controlled JSON stdin and never opens a project file itself, so every
    # project-file read stays inside the audited, no-follow ``open_input_file``.
    try:
        analyzed=_run_python_worker(supervisor, PYTHON_ANALYZE_TASK, 'analyze',
                                    {'root':str(root),'files':analyze_payload})
        rows=list(analyzed.get('rows',[]))
    except ScanBlocked:
        raise
    except Exception as exc:
        raise ScanBlocked('PARSER_CRASHED',
                          f'The per-file analyzer failed ({type(exc).__name__}); no graph was published.') from exc
    try:
        # P0.7.5: pass the parent-verified, already-decoded source text (and any
        # root tsconfig/jsconfig) to the adapter.  The adapter then analyzes
        # in-memory content and never re-opens a project file by path, eliminating
        # the check-then-read race on JS/TS source that P0.7.4 still carried.
        js_ts_source={rel:content_by_path[rel] for rel in content_by_path
                      if Path(rel).suffix.lower() in {'.js','.jsx','.mjs','.cjs','.ts','.tsx'}}
        js_ts_config={}
        for rel,raw in raw_by_rel.items():
            name=Path(rel).name
            if name in ('tsconfig.json','jsconfig.json'):
                text=decode_text(raw)
                if text is not None:
                    js_ts_config[name]=text
        js_ts_result=analyze_js_ts_project(root,paths,supervisor=supervisor,
                                          js_ts_source=js_ts_source,js_ts_config=js_ts_config)
    except Exception as exc: raise ScanBlocked('PARSER_CRASHED',f'Semantic parser failed ({type(exc).__name__}).') from exc
    # P0.7.5: a crashed, cancelled-but-unverified, or otherwise failed JS/TS
    # adapter (including malformed output) must fail the scan closed rather than
    # degrade into a graph that still looks COMPLETE.  A timeout already proves
    # the child was killed and verified; a plain crash / invalid JSON proves no
    # trustworthy result was produced, so publishing one would be unsafe.
    fatal_parser_codes={'ADAPTER_TIMEOUT','ADAPTER_START_FAILED','ADAPTER_FAILED','ADAPTER_INVALID_JSON'}
    if any(d.get('code') in fatal_parser_codes for d in js_ts_result.get('project_diagnostics',[])):
        code=next(d['code'] for d in js_ts_result.get('project_diagnostics',[]) if d.get('code') in fatal_parser_codes)
        raise ScanBlocked(code,'The JavaScript/TypeScript parser did not complete; no graph was published.')
    js_ts_paths={r['path'] for r in rows if r.get('language') in {'JavaScript','JavaScript/JSX','TypeScript','TypeScript/JSX'}}
    js_ts_capabilities=parser_capabilities()['languages']
    if not js_ts_result.get('available'):
        for language in ('JavaScript','JavaScript/JSX','TypeScript','TypeScript/JSX'):
            capability=dict(js_ts_capabilities[language])
            capability.update({'parser_name':'heuristic-candidate','parser_version':'1','level':'DISCOVERY_ONLY',
                'degradation':'Node or project-local TypeScript is unavailable; regex candidates remain UNKNOWN.'})
            js_ts_capabilities[language]=capability
    for row in rows:
        if row.get('path') not in js_ts_paths:
            continue
        parsed=js_ts_result.get('files',{}).get(row['path'])
        if parsed is not None:
            row['symbols']=parsed.get('symbols',[])
            row['imports']=parsed.get('imports',[])
            row['calls']=parsed.get('calls',[])
            row['route_handlers']=parsed.get('route_handlers',[])
            row['ui_event_bindings']=parsed.get('ui_event_bindings',[])
            row['api_client_calls']=parsed.get('api_client_calls',[])
            row['analysis_diagnostics']=parsed.get('diagnostics',[])
            row['syntax_status']=parsed.get('syntax_status','UNKNOWN')
            row['parser']={**js_ts_capabilities[row['language']],'parser_name':parsed.get('parser_name','typescript-compiler-api'),
                'parser_version':parsed.get('parser_version','unknown'),'contract_version':CONTRACT_VERSION}
        else:
            row.setdefault('analysis_diagnostics',[])
            for diagnostic in js_ts_result.get('project_diagnostics',[]):
                row['analysis_diagnostics'].append({**diagnostic,'file_path':row['path']})
            if not js_ts_result.get('available'):
                row['analysis_diagnostics'].append({'code':js_ts_result.get('availability',{}).get('reason','SEMANTIC_PARSER_UNAVAILABLE'),
                    'severity':'UNKNOWN','message':'No parser result was produced; textual candidates are not semantic evidence.'})
            for call in row.get('calls',[]):
                call['resolved']=False; call['resolution_status']='UNKNOWN'
            row['parser']={**js_ts_capabilities[row['language']],'contract_version':CONTRACT_VERSION}
        # S1-B-FIX-01: the JS/TS branch above replaces `analysis_diagnostics`
        # wholesale, which can orphan the `diagnostics_ref` computed by
        # `analyze_file`. Re-materialise the diagnostic that this row's
        # degradation metadata actually claims (never null the reference: that
        # would hide the reason), then recompute the metadata so the reference
        # always resolves to a real, readable entry. Python rows and
        # unsupported-language rows never reach this branch.
        claimed_reason=(row.get('analysis_degradation') or {}).get('reason')
        if claimed_reason=='SEMANTIC_PARSER_UNAVAILABLE':
            diags=row.setdefault('analysis_diagnostics',[])
            if not any(isinstance(d,dict) and d.get('code')=='SEMANTIC_PARSER_UNAVAILABLE' for d in diags):
                diags.append({'code':'SEMANTIC_PARSER_UNAVAILABLE','severity':'UNKNOWN',
                    'message':'Text candidates are not semantic parse results; syntax validity is not measured.'})
        row['analysis_degradation']=_degradation_metadata(row, row['language'])
    # P0.7.4: cross-file resolution runs as a supervised *subprocess* task too,
    # obeying the same per-task timeout and concurrency cap as per-file analysis.
    # The worker receives the Python source text the parent already read (never
    # re-reading the disk), so a timeout kills and *verifies* a child process
    # instead of abandoning a thread, and a stuck or crashed resolver cannot
    # publish a partial graph.
    python_readable={r['path'] for r in rows if r.get('language')=='Python' and r.get('readable_text')}
    resolver_contents={rel:_resolver_text(raw) for rel, raw in raw_by_rel.items() if rel in python_readable}
    try:
        resolved=_run_python_worker(supervisor, PYTHON_RESOLVE_TASK, 'resolve',
                                    {'root':str(root),'rows':rows,'contents':resolver_contents})
        rows=list(resolved.get('rows', rows))
        resolution_summary=resolved.get('summary', {})
    except ScanBlocked:
        raise
    except Exception as exc:
        # A resolver crash must fail closed as BLOCKED, not degrade into a
        # partial graph published as INCOMPLETE.
        raise ScanBlocked('PARSER_CRASHED',
                          f'Cross-file resolution failed ({type(exc).__name__}); no graph was published.') from exc
    for row in rows:
        if row.get('language')!='Python' and row.get('language') not in {'JavaScript','JavaScript/JSX','TypeScript','TypeScript/JSX'}:
            for call in row.get('calls',[]):
                call['heuristic_resolved_candidate']=bool(call.get('resolved'))
                call['resolved']=False
                call['resolution_status']='UNKNOWN'
        row.setdefault('module_symbols',[])
    current={x['path']:x['sha256'] for x in rows}
    added=sorted(set(current)-set(previous)); deleted=sorted(set(previous)-set(current)); modified=sorted(k for k in set(current)&set(previous) if current[k]!=previous[k]); unchanged=sorted(set(current)&set(previous)-set(modified))
    languages={}; categories={}
    for r in rows:
        languages[r['language']]=languages.get(r['language'],0)+1
        categories[r['category']]=categories.get(r['category'],0)+1
    markers=build_markers(root,paths)
    nodes=[]; edges=[]
    all_symbol_ids = {
        'symbol:' + row['path'] + '#' + symbol['name'] + ':' + str(symbol['line'])
        for row in rows for symbol in row.get('symbols', [])
    }
    for f in rows:
        fid='file:'+f['path']; nodes.append({'id':fid,'type':'file','label':f['path'],'category':f['category'],'language':f['language'],'module':f.get('module'),'evidence':{'path':f['path']}})
        defined_ids={}
        defined_symbol_ids=set()
        for s in f['symbols']:
            sid='symbol:'+f['path']+'#'+s['name']+':'+str(s['line'])
            module_symbol=next((x for x in f.get('module_symbols',[]) if x['name']==s['name'] and x['line']==s['line']),None)
            node={'id':sid,'type':s['kind'],'label':s['name'],'evidence':{'path':f['path'],'line':s['line'],'excerpt':s['evidence']}}
            if module_symbol: node.update({'module':f.get('module'),'qualified_name':module_symbol['qualified_name']})
            nodes.append(node)
            edges.append(_edge(fid,sid,'defines',{'path':f['path'],'line':s['line']},method='extractor',confidence='medium'))
            defined_ids[s['name']]=sid
            defined_symbol_ids.add(sid)
        for dep in f['imports']:
            did='dependency:'+dep['name']
            if not any(n['id']==did for n in nodes): nodes.append({'id':did,'type':'dependency','label':dep['name'],'evidence':{'path':f['path'],'line':dep['line']}})
            edges.append(_edge(fid,did,'imports',{'path':f['path'],'line':dep['line'],'excerpt':dep.get('evidence','')},method=dep.get('method','regex'),confidence='medium'))
        for route_handler in f.get('route_handlers', []):
            state = route_handler.get('resolution_status', 'UNKNOWN')
            target = route_handler.get('target') or {}
            # The binding edge method follows the row language: the TypeScript
            # Compiler API produces these rows for JS/TS, the Python AST
            # resolver produces the same shape for Python.
            binding_method = ('python-route-handler-binding' if f.get('language') == 'Python'
                              else 'typescript-route-handler-binding')
            extras = {
                'resolution_status': state,
                'handler_reference': route_handler.get('handler_reference', ''),
                'route_method': route_handler.get('method', ''),
                'route_path': route_handler.get('path', ''),
                'evidence_chain': route_handler.get('evidence_chain', []),
                'candidate': True,
                'verified': False,
                'verification_status': 'NOT_VERIFIED',
            }
            if state == 'RESOLVED' and target.get('id') in all_symbol_ids:
                extras['resolved'] = True
                edges.append(_edge(fid, target['id'], 'references',
                    {'path': f['path'], 'line': route_handler.get('line'),
                     'column': route_handler.get('column'),
                     'handler_reference': route_handler.get('handler_reference')},
                    method=binding_method, confidence='high', **extras))
            else:
                extras['resolved'] = False
                if route_handler.get('candidate_targets'):
                    extras['candidate_targets'] = route_handler['candidate_targets']
                edges.append(_edge(fid, 'unresolved-route-handler:' + f['path'] + ':' +
                    str(route_handler.get('line')) + ':' + str(route_handler.get('handler_reference')),
                    'references', {'path': f['path'], 'line': route_handler.get('line'),
                    'handler_reference': route_handler.get('handler_reference')},
                    method=binding_method, confidence='low', **extras))
        for api_call in f.get('api_client_calls', []):
            call_id = 'api-client-call:' + f['path'] + ':' + str(api_call.get('line')) + ':' + str(api_call.get('column') or 0)
            if not any(n['id'] == call_id for n in nodes):
                nodes.append({'id': call_id, 'type': 'api_client_call',
                    'label': (api_call.get('method') or 'UNKNOWN') + ' ' + (api_call.get('path') or api_call.get('url_expression') or 'dynamic URL'),
                    'evidence': {'path': f['path'], 'line': api_call.get('line'),
                                 'resolution_status': api_call.get('resolution_status')}})
            source_id = api_call.get('caller_symbol_id')
            if source_id not in all_symbol_ids:
                source_id = fid
            edges.append(_edge(source_id, call_id, 'references',
                {'path': f['path'], 'line': api_call.get('line'), 'column': api_call.get('column'),
                 'callee': api_call.get('callee'), 'url_expression': api_call.get('url_expression')},
                method='typescript-api-client-call', confidence='medium',
                route_method=api_call.get('method'), route_path=api_call.get('path'),
                resolution_status=api_call.get('resolution_status'),
                caller_symbol_id=api_call.get('caller_symbol_id'), candidate=True, verified=False,
                verification_status='NOT_VERIFIED', confidence_level='STATIC_INFERENCE'))
        for ui_binding in f.get('ui_event_bindings', []):
            state = ui_binding.get('resolution_status', 'UNKNOWN')
            target = ui_binding.get('target') or {}
            extras = {'resolution_status': state, 'event_name': ui_binding.get('event_name', ''),
                'binding_expression': ui_binding.get('binding_expression'), 'candidate': True,
                'verified': False, 'verification_status': 'NOT_VERIFIED',
                'evidence_chain': ui_binding.get('evidence_chain', [])}
            target_id = target.get('id') if state == 'RESOLVED' else None
            if target_id not in all_symbol_ids:
                target_id = 'unresolved-ui-event:' + f['path'] + ':' + str(ui_binding.get('line')) + ':' + str(ui_binding.get('event_name'))
                if not any(n['id'] == target_id for n in nodes):
                    nodes.append({'id': target_id, 'type': 'unresolved',
                        'label': ui_binding.get('binding_expression') or ui_binding.get('event_name') or 'UI event',
                        'evidence': {'path': f['path'], 'line': ui_binding.get('line'), 'resolution_status': state}})
                extras['resolved'] = False
                if ui_binding.get('candidate_targets'):
                    extras['candidate_targets'] = ui_binding['candidate_targets']
            else:
                extras['resolved'] = True
            edges.append(_edge(fid, target_id, 'references',
                {'path': f['path'], 'line': ui_binding.get('line'), 'column': ui_binding.get('column'),
                 'event_name': ui_binding.get('event_name'), 'binding_expression': ui_binding.get('binding_expression')},
                method='typescript-jsx-event-handler-binding', confidence='high' if extras['resolved'] else 'low', **extras))
        for c in f['calls']:
            state=c.get('resolution_status','UNKNOWN')
            target=c.get('target') or {}
            if state=='RESOLVED' and target.get('id'):
                tid=target['id']
            elif state=='EXTERNAL':
                external_label=c.get('callee','external')
                import_info=c.get('import_evidence') or {}
                dep_name=next((d['name'] for d in f['imports'] if d['line']==import_info.get('line')),external_label)
                tid='external:'+str(dep_name)+'.'+str(external_label)
                if not any(n['id']==tid for n in nodes): nodes.append({'id':tid,'type':'external','label':external_label,'evidence':{'note':'external or standard-library call; target source not in project'}})
            else:
                tid='unresolved:'+f['path']+'#'+str(c.get('line'))+':'+str(c.get('column',0))+':'+str(c.get('callee','unknown'))
                if not any(n['id']==tid for n in nodes): nodes.append({'id':tid,'type':'unresolved','label':c.get('callee','unknown'),'evidence':{'path':f['path'],'line':c['line'],'resolution_status':state}})
            ev={'path':f['path'],'line':c['line']}
            if 'column' in c: ev['column']=c['column']
            if c.get('import_evidence'): ev['import_evidence']=c['import_evidence']
            extras={'resolved':state=='RESOLVED','resolution_status':state}
            if c.get('candidate_targets'): extras['candidate_targets']=c['candidate_targets']
            if c.get('evidence_chain'): extras['evidence_chain']=c['evidence_chain']
            if c.get('diagnostic'): extras['diagnostic']=c['diagnostic']
            # Preserve parameter mapping only for a uniquely resolved call.
            # Unsupported shapes remain explicit but never publish partial mappings.
            if state == 'RESOLVED' and c.get('argument_binding_status'):
                extras['argument_binding_status'] = c['argument_binding_status']
                extras['argument_bindings'] = (
                    c.get('argument_bindings', [])
                    if c.get('argument_binding_status') == 'EXACT_STATIC_MAPPING' else []
                )
            if target:
                extras['target_location']={'path':target['file'],'line':target['line'],'qualified_name':target.get('qualified_name')}
            caller_id=c.get('caller_symbol_id')
            if caller_id in defined_symbol_ids:
                extras['caller_symbol_id']=caller_id
                caller=c.get('caller') or {}
                extras['caller_location']={'path':f['path'],'line':caller.get('line'),'name':caller.get('name')}
            # graph-v1 requires every relationship source to remain a file node.
            # Preserve precise function ownership as call-edge metadata until a
            # versioned graph schema can represent function-originating edges.
            edges.append(_edge(fid,tid,'calls',ev,method=c.get('method','regex'),confidence=c.get('confidence','low'),**extras))
        for fc in f.get('framework_candidates',[]):
            cid='candidate:'+fc['kind']
            if not any(n['id']==cid for n in nodes): nodes.append({'id':cid,'type':'candidate','label':fc['kind'],'evidence':{'note':'框架候选关联，未验证，不视为已连通'}})
            edges.append(_edge(fid,cid,'references',{'path':f['path'],'line':fc.get('line')},method=fc.get('method'),confidence=fc.get('confidence'),candidate=True,verified=False))
    # P0.3 图谱类型统计
    edge_types={}; node_types={}
    for e in edges: edge_types[e['type']]=edge_types.get(e['type'],0)+1
    for n in nodes: node_types[n['type']]=node_types.get(n['type'],0)+1
    # P0.4 调用关系边界统计：按提取方法分 + resolved×confidence 交叉
    call_edges=[e for e in edges if e['type']=='calls']
    call_method_counts={}; call_resolved_conf={}
    resolution_counts={'RESOLVED':0,'UNRESOLVED':0,'AMBIGUOUS':0,'DYNAMIC':0,'EXTERNAL':0,'UNKNOWN':0}
    for e in call_edges:
        m=e.get('method','unknown'); call_method_counts[m]=call_method_counts.get(m,0)+1
        key=f"resolved={bool(e.get('resolved'))}/confidence={e.get('confidence','?')}"
        call_resolved_conf[key]=call_resolved_conf.get(key,0)+1
        state=e.get('resolution_status','UNKNOWN')
        if state not in resolution_counts: state='UNKNOWN'
        resolution_counts[state]+=1
    call_stats={'by_method':call_method_counts,'resolved_x_confidence':call_resolved_conf,
                'resolution_counts':resolution_counts,
                'note':'Python uses AST plus explicit project import/symbol indexes. Other-language regex matches remain UNKNOWN candidates and are not counted as resolved.'}
    # P0.7.10-Z: build the trusted snapshot from the SAME audited bytes captured
    # above (``raw_by_rel`` comes from ``open_input_file``: a no-follow open plus a
    # descriptor identity check).  The snapshot computes the line counts itself, so
    # no externally supplied number can acquire trusted status, and it records each
    # file's identity so a later replacement is detectable without re-reading.
    snapshot=capture(root,raw_by_rel,identities=ids_for_capture,origin='scan:usit-p0')
    # The published field is now a projection of that snapshot: identical numbers,
    # but on its own it is plain data with no provenance.
    source_line_counts=snapshot.as_mapping()
    graph={'schema_version':'usit-graph-v1','nodes':nodes,'edges':edges,'edge_type_counts':edge_types,'node_type_counts':node_types,'call_relation_stats':call_stats,
           'source_line_counts':source_line_counts,
           'resolution_summary':resolution_summary,
           'limitations':['符号/导入/调用关系使用启发式与 ast（Python）提取，不等同于编译器语义或完整调用图。',
                          'Python 仅对显式导入且唯一指向项目内顶层定义的调用标记 RESOLVED；同名冲突、条件重绑定、属性类型和动态行为保守降级。',
                          '其他语言调用仅保留启发式候选并标记 UNKNOWN，不宣称与 Python 解析精度相同。',
                          'Android 页面跳转/客户端 API/Node 路由以 candidate 边标记（verified=false），需语义或人工验证。']}
    inventory=_scan_target_inventory(root,rows,content_by_path)
    parser_contract=adapt_graph_v1(graph)
    parser_contract['records'].extend(js_ts_result.get('records',[]))
    for diagnostic in js_ts_result.get('project_diagnostics',[]):
        parser_contract['records'].append({'language':'JavaScript/TypeScript','file_path':'','parser_name':js_ts_result.get('parser_name','typescript-compiler-api'),
            'parser_version':js_ts_result.get('parser_version','unknown'),'node_type':'diagnostic','qualified_name':diagnostic.get('code','JS_TS_PROJECT_DIAGNOSTIC'),
            'source_location':{'file_path':'','line':diagnostic.get('line'),'column':diagnostic.get('column')},'relation_type':None,
            'resolution_status':'UNKNOWN','confidence':0.0,'evidence':{'diagnostic':diagnostic.get('code','JS_TS_PROJECT_DIAGNOSTIC')},'diagnostics':[diagnostic]})
    for r in rows:
        for diagnostic in r.get('analysis_diagnostics',[]):
            parser_contract['records'].append({'language':r.get('language','UNKNOWN'),'file_path':r['path'],
                'parser_name':(r.get('parser') or {}).get('parser_name','unknown'),'parser_version':(r.get('parser') or {}).get('parser_version','unknown'),
                'node_type':'diagnostic','qualified_name':diagnostic.get('code','analysis_diagnostic'),
                'source_location':{'file_path':r['path'],'line':diagnostic.get('line')},'relation_type':None,
                'resolution_status':'UNKNOWN','confidence':0.0,'evidence':{'diagnostic':diagnostic.get('code','analysis_diagnostic')},
                'diagnostics':[diagnostic]})
    from .parser_contract import validate_record
    parser_contract['record_validation_errors']=[{'index':i,'problems':problems} for i,record in enumerate(parser_contract['records'])
                                                  if (problems:=validate_record(record))]
    node_ids={n['id'] for n in nodes}
    edge_count=len(edges)
    endpoint_valid=sum(e['source'] in node_ids and e['target'] in node_ids for e in edges)
    evidence_complete=sum(bool(e.get('evidence',{}).get('path')) and isinstance(e.get('evidence',{}).get('line'),int)
                          and e['evidence']['line']>0 for e in edges)
    parser_quality={'schema':'usit-parser-quality-v1','counts':{'node_total':len(nodes),'edge_total':edge_count,
        'endpoint_valid_rate':endpoint_valid/edge_count if edge_count else None,
        'evidence_complete_rate':evidence_complete/edge_count if edge_count else None,
        'resolved_call_count':resolution_counts['RESOLVED'],'unresolved_call_count':resolution_counts['UNRESOLVED'],
        'ambiguous_call_count':resolution_counts['AMBIGUOUS'],'dynamic_call_count':resolution_counts['DYNAMIC'],
        'external_call_count':resolution_counts['EXTERNAL'],'unknown_call_count':resolution_counts['UNKNOWN']},'languages':{}}
    for language,file_count in languages.items():
        capability=js_ts_capabilities.get(language,{'parser_name':'discovery-only','level':'UNSUPPORTED',
            'supported':['file discovery'],'unsupported':['semantic parsing'],'degradation':'All relations remain UNKNOWN.'})
        lang_calls=[e for e in call_edges if next((n.get('language') for n in nodes if n['id']==e['source']),None)==language]
        parser_quality['languages'][language]={'file_count':file_count,'capability':capability,
            'call_resolution_counts':{state:sum(e.get('resolution_status','UNKNOWN')==state for e in lang_calls) for state in resolution_counts},
            'ground_truth_metrics':{'status':'NOT_MEASURED','known_truth_total':0,
                'predicted_total':sum(e.get('resolution_status')=='RESOLVED' for e in lang_calls),
                'TP':0,'FP':0,'FN':0,'precision':None,'recall':None}}
    inventory['test_execution_status']='NOT_RUN'; inventory['coverage_evidence_status']='UNKNOWN'
    plan={'schema_version':'usit-test-plan-v1','project_root':str(root),'execution_policy':'PLAN_ONLY_NOT_EXECUTED',
          'test_inventory':inventory,
          'items':[
            {'id':'T001','priority':'P0','type':'build_smoke','goal':'确认构建入口和所需工具链可用','suggested_action':'依据识别到的构建标记人工复核后再运行构建','evidence':[m['marker'] for m in markers],'status':'NOT_RUN'},
            {'id':'T002','priority':'P0','type':'test_discovery','goal':'定位已有测试文件/框架/文档/截图证据','suggested_action':'人工确认测试命令与副作用后再执行；截图仅作辅助证据','evidence':[x['path'] for x in inventory['test_source']][:100],'status':'NOT_RUN','note':'本扫描区分 test_source/test_case_document/screenshot_evidence/generic_asset；截图 executable=false'},
            {'id':'T003','priority':'P1','type':'dependency_review','goal':'复核依赖清单与锁文件一致性','suggested_action':'检查清单/锁文件及版本约束；P0不联网安装依赖','evidence':[m['marker'] for m in markers if 'lock' in m['marker'].lower() or m['marker'] in ('package.json','requirements.txt','pyproject.toml','Cargo.toml','go.mod','pom.xml')],'status':'NOT_RUN'}]}
    # P0.7.10-V defence in depth: never serialise the captured source text that
    # analyze_file keeps for by-path callers.  Scan-pipeline rows never carry it,
    # so this is a no-op there; it exists so the key can never leak into an
    # artifact if a future caller reuses by-path rows.
    manifest={'schema_version':'usit-manifest-v1','project_root':str(root),'files':[{k:v for k,v in r.items() if k!='_captured_text'} for r in rows],'summary':{'files_scanned':len(rows),'languages':languages,'categories':categories,'build_markers':markers,'errors_count':len(errors)},'errors':errors,'incremental':{'added':added,'modified':modified,'unchanged':unchanged,'deleted':deleted,'note':'P0.1 仍对当前文件做全量轻量分析；增量仅用于变更分类。'}}
    summary={'project':str(root),'files_scanned':len(rows),'languages':languages,'categories':categories,'build_markers':markers,
             'symbols':sum(len(r['symbols']) for r in rows),'imports':sum(len(r['imports']) for r in rows),
             'calls':sum(len(r['calls']) for r in rows),'framework_candidates':sum(len(r.get('framework_candidates',[])) for r in rows),
             'call_method_counts':call_method_counts,'call_resolved_x_confidence':call_resolved_conf,
             'call_resolution_counts':resolution_counts,'python_resolution':resolution_summary,
             'graph_nodes':len(nodes),'graph_edges':len(edges),'edge_type_counts':edge_types,'node_type_counts':node_types,
             'incremental':manifest['incremental'],'errors':errors,
             'parser_contract_version':CONTRACT_VERSION,'parser_capabilities':js_ts_capabilities,
             'js_ts_analysis':{'available':js_ts_result.get('available',False),'parser_name':js_ts_result.get('parser_name'),
                'parser_version':js_ts_result.get('parser_version'),'project_diagnostics':js_ts_result.get('project_diagnostics',[]),
                'records':len(js_ts_result.get('records',[]))},
             'parser_quality':parser_quality,'parser_contract_record_count':len(parser_contract['records']),
             'parser_contract_errors':parser_contract['record_validation_errors'],'test_assets':inventory,
             'status':'COMPLETE','scan_outcome':'COMPLETE',
             'parser_task_audit':supervisor.audit(),
             'runtime_limits':{'max_files':MAX_FILES,'max_file_bytes':MAX_FILE_BYTES,'max_total_read_bytes':MAX_TOTAL_READ_BYTES,
                'max_scan_seconds':MAX_SCAN_SECONDS,'max_parser_task_seconds':MAX_PARSER_TASK_SECONDS,
                'max_concurrency':MAX_CONCURRENCY,'max_output_bytes':MAX_OUTPUT_BYTES},
             'module_count':len({r.get('module') for r in rows if r.get('module')}),
             'test_asset_file_count':len(inventory['test_source']),
             'test_case_count':sum(len(asset.get('test_cases',[])) for asset in inventory['test_source']),
             'unknown_items':[{'file':r['path'],'diagnostics':r.get('analysis_diagnostics',[])} for r in rows if r.get('analysis_diagnostics')],
             'unknown_relationships':[{'source':e['source'],'target':e['target'],'relation_type':e['type'],
                 'resolution_status':e.get('resolution_status','UNKNOWN'),'evidence':e.get('evidence',{}),
                 'candidate_targets':e.get('candidate_targets',[])} for e in edges if e.get('resolution_status') in {'UNKNOWN','AMBIGUOUS','DYNAMIC','UNRESOLVED'}],
             'safety':{'read_only_target':True,'project_code_executed':False,'network_used':False,'sensitive_name_files_skipped':True,'sensitive_output_scrubbed':True}}

    # P0.1 防御纵深：写入前再 scrub 一次所有输出
    graph=_scrub_sensitive_graph(graph); manifest=_scrub_sensitive_manifest(manifest); plan=_scrub_sensitive_plan(plan)
    # S1-C (IMPLEMENT-02B): file-level understanding payload.  Built AFTER the
    # output scrub and BEFORE report.json is written, so the summary carries a
    # terminal understanding status.  ``COMPLETE`` means built + serialised into
    # the staging area only; publication truth stays with scan_status.json.
    understanding_text, understanding_status = _build_file_understanding_text(manifest, parser_contract)
    summary['understanding_status']=understanding_status
    # P0.7.10-Z: validate against the trusted snapshot, not against the graph's own
    # field.  ``validate_graph_trusted`` accepts only a LineCountSnapshot, applies
    # the snapshot's counts, and omits any file whose capture-time identity no
    # longer matches -- so a stale or tampered mapping degrades to UNVERIFIED and
    # never causes a project file to be re-opened.  ``_LEGACY_VALIDATE`` is
    # deliberately not called from this path.
    validation=validate_graph_trusted(graph,snapshot,project_root=root)
    summary['graph_validation_status']=validation['status']
    summary['graph_quality']=validation['quality']

    report=_render_markdown(root,summary,markers,edge_types,categories,len(added),len(modified),len(unchanged),len(deleted))
    output_budget=[0]
    def bounded_write(path: Path, text: str) -> None:
        encoded=text.encode('utf-8')
        output_budget[0]+=len(encoded)
        if output_budget[0]>MAX_OUTPUT_BYTES:
            raise ScanBlocked('MAX_OUTPUT_BYTES_EXCEEDED','Generated output exceeds the 256 MiB write budget.')
        path.write_bytes(encoded)
    # Object key order has no graph semantics. Stable key sorting removes
    # insertion-order differences from nested maps while preserving every
    # array's existing order (nodes, edges, diagnostics, and evidence chains).
    def write_json(name,data): bounded_write(out/name,json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    write_json('report.json',summary); write_json('scan_manifest.json',manifest); write_json('knowledge_graph.json',graph); write_json('test_plan.json',plan)
    write_json('parser_contract.json',parser_contract)
    # S1-C (IMPLEMENT-02B): same staging area, same bounded_write budget, same
    # single-directory publish as every other output.  Absent on build failure
    # (honest absence; scan_status.json then does not enumerate it).
    if understanding_text is not None:
        bounded_write(out/'understanding.json',understanding_text)
    write_json('graph_validation.json',validation)
    bounded_write(out/'graph_validation.md',render_validation_markdown(validation))
    bounded_write(out/'report.md','\n'.join(report)+'\n')
    cache=out/'cache'; cache.mkdir(exist_ok=True); bounded_write(cache/'previous_manifest.json',json.dumps({'files':current},ensure_ascii=False,indent=2)+'\n')
    # P0.7.2: a published output directory always carries an explicit terminal
    # status, written last so it can enumerate every other published file.
    # A failed or partial scan never reaches this line, so no directory can look
    # complete while being incomplete.
    bounded_write(out/'scan_status.json',json.dumps({'schema_version':'usit-scan-status-v1','status':'COMPLETE',
        'project_root':str(root),'incomplete':False,'blocked':False,
        'published_files':sorted(f.relative_to(out).as_posix() for f in out.rglob('*') if f.is_file() and f.name!='scan_status.json')},ensure_ascii=False,indent=2)+'\n')
    summary['output_dir']=str(out)
    return summary

def _render_markdown(root,summary,markers,edge_types,categories,na,nm,nu,nd) -> list[str]:
    src=summary['languages'].get('Unknown',0)
    L=[]
    L += ['# USIT P0.1 扫描报告','',f"- 项目：`{root}`",f"- 扫描文件：{summary['files_scanned']}",
          f"- 语言统计：{json.dumps(summary['languages'],ensure_ascii=False)}",
          f"- 文件类别统计：{json.dumps(summary['categories'],ensure_ascii=False)}",
          f"- 构建标记：{', '.join(m['marker']+'（'+m['stack']+'）' for m in markers) or '未识别'}",
          f"- 符号数：{summary['symbols']}；导入：{summary['imports']}；调用边：{summary['calls']}；候选关联：{summary['framework_candidates']}",
          f"- 知识图谱节点/边：{summary['graph_nodes']}/{summary['graph_edges']}（边类型：{json.dumps(edge_types,ensure_ascii=False)}）",
          f"- 调用边按提取方法：{json.dumps(summary.get('call_method_counts',{}),ensure_ascii=False)}",
          f"- 调用边 resolved×confidence 交叉：{json.dumps(summary.get('call_resolved_x_confidence',{}),ensure_ascii=False)}",
          f"- 调用解析状态（UNKNOWN 不计为成功）：{json.dumps(summary.get('call_resolution_counts',{}),ensure_ascii=False)}",
          f"- 解析器契约：{summary.get('parser_contract_version')}；记录={summary.get('parser_contract_record_count')}；契约错误={len(summary.get('parser_contract_errors',[]))}",
          f"- 语言能力等级：{json.dumps({k:v.get('capability',{}).get('level') for k,v in summary.get('parser_quality',{}).get('languages',{}).items()},ensure_ascii=False)}",
          f"- 测试资产：{len(summary.get('test_assets',{}).get('test_source',[]))} 个文件；测试执行=NOT_RUN；覆盖证据=UNKNOWN",
          f"- 图谱校验：{summary.get('graph_validation_status','NOT_RUN')}；端点有效率={summary.get('graph_quality',{}).get('endpoint_valid_rate')}；证据完整率={summary.get('graph_quality',{}).get('evidence_complete_rate')}；错误/警告/未知={summary.get('graph_quality',{}).get('errors')}/{summary.get('graph_quality',{}).get('warnings')}/{summary.get('graph_quality',{}).get('unknown')}",
          f"- 增量分类：新增 {na} / 修改 {nm} / 未变 {nu} / 删除 {nd}",
          '- 安全：目标项目只读；未执行项目代码/构建/测试；未联网；敏感文件（中英文）默认跳过其内容、文件名、路径、符号、摘要与片段，不写入任何输出。','',
          '## 统计口径说明（重要）',
          f'- 语言统计中的 `Unknown`（{src} 个）**包含图片、压缩包、日志、文档、备份等非源码文件**；判断真实源码规模请看「文件类别统计」中的 `source_code`（{summary["categories"].get("source_code",0)} 个）。',
          '- `Unknown`（语言维度：扩展名无法映射到已知编程语言）与 `unknown`（文件类别维度：连类别也无法判定）是**两个不同维度**，数值不相等属正常。',
          f'- 校验：语言总和 = {sum(summary["languages"].values())}，类别总和 = {sum(summary["categories"].values())}，均应等于扫描文件数 {summary["files_scanned"]}。','',
          '## 重要限制','符号/导入/调用关系使用启发式与 ast（Python）；不代表精确 AST、完整调用图或测试通过。跨文件连通仅基于 import，未凭同名函数伪造。测试计划仅生成，不执行，状态均为 NOT_RUN。未知语言/框架不会被猜测。','',
          '## 输出文件','`report.md`、`report.json`、`scan_manifest.json`、`knowledge_graph.json`、`test_plan.json`、`parser_contract.json`、`graph_validation.json`、`graph_validation.md`、`cache/previous_manifest.json`']
    return L


