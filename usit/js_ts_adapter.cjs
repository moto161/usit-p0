"use strict";

// Static TypeScript Compiler API bridge. This process analyzes source text only;
// it never loads or executes analyzed JavaScript/TypeScript modules.
//
// P0.7.5 security model
// ---------------------
// The Python scanner reads every source file through a verified, no-follow
// handle (see usit/safeio.py + open_input_file) and passes the *decoded text* to
// this adapter over controlled JSON/stdin.  The adapter therefore processes
// parent-verified content and never re-opens a project source file by path, so
// the check-then-read race (final-component link, intermediate-directory
// junction, delete/recreate, content swap after discovery) on the SOURCE files is
// gone: there is no untrusted path to follow.
//
// The compiler host is *in-memory first*:
//   * content the parent verified  -> returned from memory, never read from disk;
//   * TypeScript's own lib files   -> read from the fixed USIT_ROOT/node_modules
//                                     path (the analyzer's own dependency, not the
//                                     target project);
//   * everything else on disk      -> allowed only when it resolves strictly
//                                     WITHIN the project root (node_modules
//                                     type declarations needed for EXTERNAL
//                                     classification).  A path that escapes the
//                                     root is never read.
const fs = require("node:fs");
const path = require("node:path");

const CONTRACT = "usit-parser-result-v1";
const PARSER = "typescript-compiler-api";
const MAX_FILES = 500;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue"]);
const EXPECTED_TS_VERSION = "6.0.3";

// The analyzer's own TypeScript install. Files under this directory are the
// compiler that drives analysis, not part of the target project.
const TS_LIB_DIR = path.resolve(process.argv[3] || "", "node_modules", "typescript");

// Diagnostics only. Trust decisions MUST NOT use a string prefix: P0.7.7 proved
// a junctioned install keeps the same prefix while pointing outside the tree.
const TS_LIB_ROOT = (process.platform === "win32"
  ? TS_LIB_DIR.replace(/\\/g, "/").toLowerCase()
  : TS_LIB_DIR) + path.sep;

function within(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(".." + path.sep) && relative !== ".." && !path.isAbsolute(relative));
}

function canonical(p) {
  return path.resolve(p).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function isLink(p) {
  try {
    // On Windows a directory junction reports isSymbolicLink() true via lstat.
    return fs.lstatSync(p).isSymbolicLink();
  } catch (_) {
    return false;
  }
}

// P0.7.8: prove the compiler we are about to load really is the trusted install.
// A version match alone is not trust, and neither is a path prefix.
function verifyTsLibTrust() {
  const out = { trusted: false, tsLib: TS_LIB_DIR, realPath: null, version: null, reason: null, detail: null };
  let st;
  try { st = fs.statSync(TS_LIB_DIR); } catch (_) { out.reason = "PROJECT_LOCAL_TYPESCRIPT_MISSING"; return out; }
  if (!st.isDirectory()) { out.reason = "TYPESCRIPT_PATH_NOT_A_DIRECTORY"; return out; }
  if (isLink(TS_LIB_DIR)) { out.reason = "TS_LIB_ROOT_IS_REPARSE_POINT"; return out; }
  let real;
  try { real = fs.realpathSync(TS_LIB_DIR); } catch (_) { out.reason = "TS_LIB_REALPATH_FAILED"; return out; }
  out.realPath = real;
  if (canonical(real) !== canonical(TS_LIB_DIR)) { out.reason = "TS_LIB_ROOT_REDIRECTED"; return out; }
  for (const rel of ["package.json", path.join("lib", "typescript.js")]) {
    let fileReal;
    try { fileReal = fs.realpathSync(path.join(TS_LIB_DIR, rel)); }
    catch (_) { out.reason = "TS_LIB_FILE_MISSING"; out.detail = rel; return out; }
    if (!within(real, fileReal)) { out.reason = "TS_LIB_FILE_OUTSIDE_INSTALL"; out.detail = rel; return out; }
  }
  let version = null;
  try {
    version = JSON.parse(fs.readFileSync(path.join(TS_LIB_DIR, "package.json"), "utf8")).version;
  } catch (_) { out.reason = "TYPESCRIPT_PACKAGE_UNREADABLE"; return out; }
  out.version = version;
  if (version !== EXPECTED_TS_VERSION) { out.reason = "UNEXPECTED_TYPESCRIPT_VERSION"; return out; }
  out.trusted = true;
  return out;
}

const TS_TRUST = verifyTsLibTrust();

// Fail closed: when the install cannot be proven trustworthy we must not
// require() it at all, so no external compiler can ever drive this analysis.
let ts = null;
if (!TS_TRUST.trusted) {
  process.stdout.write(JSON.stringify({
    contract_version: CONTRACT, parser_name: PARSER, parser_version: "UNAVAILABLE",
    available: false,
    availability: { available: false, reason: TS_TRUST.reason, parser_name: PARSER },
    project_diagnostics: [{ code: TS_TRUST.reason, severity: "ERROR",
      message: "The TypeScript install could not be proven trusted; refusing to load it." }],
    files: {}, records: [],
  }));
  process.exit(0);
} else {
  ts = require(TS_LIB_DIR);
}

function safePath(root, relative) {
  if (typeof relative !== "string" || path.isAbsolute(relative) || relative.includes("\0")) return null;
  const candidate = path.resolve(root, relative);
  if (!within(root, candidate)) return null;
  try {
    const real = fs.realpathSync(candidate);
    if (!within(root, real)) return null;
    return real;
  } catch (_) {
    return null;
  }
}

// Resolve `file` to a canonical key used for the in-memory content map.
function keyOf(file) {
  const resolved = path.resolve(file);
  return process.platform === "win32" ? resolved.replace(/\\/g, "/").toLowerCase() : resolved;
}

// Guarded disk read: only ever returns bytes for a path that resolves WITHIN the
// project root (or the analyzer's own TS lib).  Anything escaping the root
// returns undefined, so a swapped symlink/junction can never pull a project
// outside file into the analysis.
function safeDiskText(root, file) {
  const resolved = path.resolve(file);
  let real;
  try { real = fs.realpathSync(resolved); } catch (_) { return undefined; }
  if (!within(root, real)) return undefined;
  try { return fs.readFileSync(real, "utf8"); } catch (_) { return undefined; }
}

function safeDiskExists(root, file, wantDirectory) {
  const resolved = path.resolve(file);
  let real;
  try { real = fs.realpathSync(resolved); } catch (_) { return false; }
  if (!within(root, real)) return false;
  try {
    const st = fs.statSync(real);
    return wantDirectory ? st.isDirectory() : st.isFile();
  } catch (_) { return false; }
}

function safeDiskReadDir(root, dir, extensions, excludes, includes, depth) {
  const safe = safePath(root, path.relative(root, path.resolve(dir)));
  if (!safe) return [];
  return ts.sys.readDirectory(safe, extensions, excludes, includes, depth)
    .filter((p) => !!safePath(root, path.relative(root, p)));
}

function loc(sourceFile, node) {
  const pos = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  return { file_path: sourceFile.fileName, line: pos.line + 1, column: pos.character + 1 };
}

// F2: resolve a URL argument to its static skeleton, or null when the URL
// is not a literal expression. Template substitutions become "{?}"
// placeholders (one per span, whatever the span expression is); "+" chains
// of literals concatenate; anything else (a variable, a call, a Request /
// URL object) yields null -- the skeleton is never guessed.
function resolveUrlSkeleton(node) {
  if (!node) return null;
  if (ts.isStringLiteralLike(node)) return node.text;
  if (ts.isTemplateExpression(node)) {
    let out = node.head.text;
    for (const span of node.templateSpans) out += "{?}" + span.literal.text;
    return out;
  }
  if (ts.isParenthesizedExpression(node)) return resolveUrlSkeleton(node.expression);
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = resolveUrlSkeleton(node.left);
    const right = resolveUrlSkeleton(node.right);
    if (left === null || right === null) return null;
    return left + right;
  }
  return null;
}

// Cut a skeleton path at the first literal "?" or "#" that is not part of
// a "{?}" placeholder.
function stripUrlSuffix(skeletonPath) {
  let out = "";
  for (let i = 0; i < skeletonPath.length; i++) {
    if (skeletonPath.startsWith("{?}", i)) { out += "{?}"; i += 2; continue; }
    const ch = skeletonPath[i];
    if (ch === "?" || ch === "#") break;
    out += ch;
  }
  return out;
}

// Take the path part of a URL skeleton, or null when there is none: a
// skeleton starting with "/" is a path; a leading "{?}/" is a ${BASE_URL}
// prefix; a literal scheme://host prefix is stripped to its path.
function extractSkeletonPath(skeleton) {
  if (typeof skeleton !== "string" || skeleton.length === 0) return null;
  let candidate = null;
  if (skeleton.startsWith("/")) {
    candidate = skeleton;
  } else if (skeleton.startsWith("{?}/")) {
    candidate = skeleton.slice(3);
  } else {
    const m = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^/]+(\/.*)?$/.exec(skeleton);
    if (m) candidate = m[1] || "/";
  }
  if (candidate === null || !candidate.startsWith("/")) return null;
  const stripped = stripUrlSuffix(candidate);
  return stripped.startsWith("/") && stripped.length > 0 ? stripped : null;
}

function diagnostic(code, severity, message, line, column) {
  const result = { code: String(code), severity: severity || "UNKNOWN", message: String(message || code) };
  if (line != null) result.line = line;
  if (column != null) result.column = column;
  return result;
}

function languageFor(file) {
  const ext = path.extname(file).toLowerCase();
  return ext === ".tsx" || ext === ".jsx" ? (ext === ".tsx" ? "TypeScript/JSX" : "JavaScript/JSX") :
    [".ts"].includes(ext) ? "TypeScript" : "JavaScript";
}

function scriptKind(file) {
  const ext = path.extname(file).toLowerCase();
  if (ext === ".tsx") return ts.ScriptKind.TSX;
  if (ext === ".jsx") return ts.ScriptKind.JSX;
  if ([".js", ".mjs", ".cjs"].includes(ext)) return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function flattenMessage(message) {
  return ts.flattenDiagnosticMessageText(message, " ");
}

function main(input) {
  const projectRoot = fs.realpathSync(path.resolve(process.argv[2]));
  const entries = Array.isArray(input.files) ? input.files : [];
  // Memory mode: entries are {rel, content}.  Legacy disk mode: entries are rel strings.
  const memoryMode = entries.length > 0 && typeof entries[0] !== "string";

  const fileDiagnostics = {};
  const projectDiagnostics = [];
  const sourcePaths = [];
  const contentByFull = new Map();
  let totalBytes = 0;

  function addFileDiag(rel, d) {
    if (!fileDiagnostics[rel]) fileDiagnostics[rel] = [];
    fileDiagnostics[rel].push(d);
  }

  const accepted = [];
  if (memoryMode) {
    for (const entry of entries) {
      const rel = String(entry.rel);
      const ext = path.extname(rel).toLowerCase();
      if (!EXTENSIONS.has(ext)) continue;
      const full = path.resolve(projectRoot, rel);
      if (!within(projectRoot, full)) {
        addFileDiag(rel, diagnostic("PATH_OUTSIDE_PROJECT", "ERROR", "Source path escapes the project root."));
        continue;
      }
      const text = entry.content != null ? String(entry.content) : "";
      const size = Buffer.byteLength(text, "utf8");
      if (size > 1024 * 1024) {
        addFileDiag(rel, diagnostic("FILE_TOO_LARGE", "UNKNOWN", "Source file exceeds the 1 MiB per-file limit."));
        continue;
      }
      if (accepted.length >= MAX_FILES || totalBytes + size > MAX_TOTAL_BYTES) {
        addFileDiag(rel, diagnostic("PROJECT_ANALYSIS_BUDGET_EXCEEDED", "UNKNOWN", "The JS/TS file-count or total-size limit was reached."));
        continue;
      }
      totalBytes += size;
      accepted.push({ rel: rel.replace(/\\/g, "/"), full });
      sourcePaths.push(full);
      contentByFull.set(keyOf(full), text);
    }
  } else {
    for (const rel of entries) {
      const ext = path.extname(String(rel)).toLowerCase();
      if (!EXTENSIONS.has(ext)) continue;
      // V1: legacy disk mode has no parent-supplied script text, so a Vue
      // SFC cannot be reduced to its <script> block here; parsing the full
      // SFC as TypeScript would manufacture garbage symbols. Skip it.
      if (ext === ".vue") {
        addFileDiag(String(rel), diagnostic("VUE_SCRIPT_EXTRACTION_REQUIRES_MEMORY_MODE", "UNKNOWN", "Legacy disk mode cannot extract a Vue <script> block; the file is not analyzed."));
        continue;
      }
      const full = safePath(projectRoot, rel);
      if (!full) {
        addFileDiag(String(rel), diagnostic("PATH_OUTSIDE_PROJECT", "ERROR", "Source path is missing, symbolic-link escaped, or outside the project root."));
        continue;
      }
      const stat = fs.statSync(full);
      if (!stat.isFile()) continue;
      if (stat.size > 1024 * 1024) {
        addFileDiag(String(rel), diagnostic("FILE_TOO_LARGE", "UNKNOWN", "Source file exceeds the 1 MiB per-file limit."));
        continue;
      }
      if (accepted.length >= MAX_FILES || totalBytes + stat.size > MAX_TOTAL_BYTES) {
        addFileDiag(String(rel), diagnostic("PROJECT_ANALYSIS_BUDGET_EXCEEDED", "UNKNOWN", "The JS/TS file-count or total-size limit was reached."));
        continue;
      }
      totalBytes += stat.size;
      accepted.push({ rel: String(rel).replace(/\\/g, "/"), full });
      sourcePaths.push(full);
    }
  }

  // V1: TypeScript drops a `.vue` root before ever asking the host for it
  // (unsupported extension), so a Vue script can never join the program
  // under its own path and checker resolution degrades to DYNAMIC.  Give
  // each memory-mode `.vue` entry a virtual `.ts` twin root carrying the
  // same line-padded script: the checker resolves the twin, while every
  // result still reports the `.vue` rel (rows, symbol ids and evidence all
  // use entry.rel, never the twin path).  Twins are program roots only and
  // never produce their own analysis row.  A twin is skipped when a real
  // file already occupies that path, or when the entry came from disk mode
  // (its content is the full SFC, not the padded script).
  const vueTwins = new Map();
  const acceptedFulls = new Set(accepted.map((entry) => path.resolve(entry.full)));
  for (const entry of accepted) {
    if (path.extname(entry.rel).toLowerCase() !== ".vue") continue;
    const twinFull = entry.full + ".ts";
    if (acceptedFulls.has(path.resolve(twinFull))) continue;
    const text = contentByFull.get(keyOf(entry.full));
    if (text == null) continue;
    contentByFull.set(keyOf(twinFull), text);
    vueTwins.set(entry.rel, { full: twinFull });
    acceptedFulls.add(path.resolve(twinFull));
  }

  // Config: prefer parent-verified content (memory mode); otherwise read the
  // root tsconfig/jsconfig through the same within-root guard.
  let configFile = null;
  let configText = null;
  if (memoryMode && input.config && input.config.content != null) {
    configFile = input.config.name || "tsconfig.json";
    configText = String(input.config.content);
  } else {
    const tsconfig = safePath(projectRoot, "tsconfig.json");
    const jsconfig = safePath(projectRoot, "jsconfig.json");
    if (tsconfig && jsconfig) projectDiagnostics.push(diagnostic("BOTH_TSCONFIG_AND_JSCONFIG", "WARNING", "Both root configs exist; tsconfig.json takes precedence."));
    if (tsconfig && fs.statSync(tsconfig).isFile()) configFile = tsconfig;
    else if (jsconfig && fs.statSync(jsconfig).isFile()) configFile = jsconfig;
    if (configFile) {
      let real;
      try { real = fs.realpathSync(configFile); } catch (_) { real = null; }
      if (real && within(projectRoot, real)) {
        try { configText = fs.readFileSync(real, "utf8"); } catch (_) { configText = null; }
      }
    }
  }

  const options = {
    allowJs: true,
    checkJs: false,
    noEmit: true,
    skipLibCheck: true,
    module: ts.ModuleKind.Preserve,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    target: ts.ScriptTarget.Latest,
    jsx: ts.JsxEmit.Preserve,
    types: [],
  };
  let configFileNames = null;
  if (configFile && configText != null) {
    try {
      const parsedText = ts.parseConfigFileTextToJson(configFile, configText);
      if (parsedText.error) {
        projectDiagnostics.push(diagnostic("CONFIG_PARSE_ERROR", "ERROR", flattenMessage(parsedText.error.messageText)));
      } else {
        const parseHost = {
          useCaseSensitiveFileNames: ts.sys.useCaseSensitiveFileNames,
          readDirectory: (dir, extensions, excludes, includes, depth) => safeDiskReadDir(projectRoot, dir, extensions, excludes, includes, depth),
          fileExists: (file) => safeDiskExists(projectRoot, file, false),
          readFile: (file) => safeDiskText(projectRoot, file),
          onUnRecoverableConfigFileDiagnostic: (d) => projectDiagnostics.push(diagnostic("CONFIG_READ_ERROR", "ERROR", flattenMessage(d.messageText))),
        };
        const parsed = ts.parseJsonConfigFileContent(parsedText.config, parseHost, projectRoot, {}, configFile);
        for (const d of parsed.errors) {
          const pos = d.file && d.start != null ? d.file.getLineAndCharacterOfPosition(d.start) : null;
          projectDiagnostics.push(diagnostic("TSCONFIG_" + d.code, "UNKNOWN", flattenMessage(d.messageText), pos ? pos.line + 1 : null, pos ? pos.character + 1 : null));
        }
        Object.assign(options, parsed.options);
        configFileNames = new Set(parsed.fileNames.map((p) => path.resolve(p)));
      }
    } catch (error) {
      projectDiagnostics.push(diagnostic("CONFIG_READ_ERROR", "ERROR", error && error.name === "TypeError" ? "Configuration encoding is invalid." : "Could not safely parse project configuration."));
    }
  } else {
    projectDiagnostics.push(diagnostic("NO_PROJECT_CONFIG_SAFE_DEFAULTS", "UNKNOWN", "No root tsconfig.json/jsconfig.json; relative resolution uses explicit conservative Bundler defaults."));
  }

  if (options.allowJs === false) {
    options.allowJs = true;
    projectDiagnostics.push(diagnostic("ALLOWJS_OVERRIDDEN_FOR_ANALYSIS", "WARNING", "allowJs=false was overridden only to parse discovered JS files; no emit or execution occurs."));
  }
  options.noEmit = true;
  const host = ts.createCompilerHost(options, true);
  // P0.7.8: the previous implementation kept the default host readers around and
  // called them for any path under TS_LIB_DIR, which bypassed the
  // realpathSync + within() guard.  Every disk read now goes through the guarded
  // path below, so those unguarded fallbacks are gone entirely.
  // P0.7.9 dual boundary. Two INDEPENDENT trusted roots, never a string prefix:
  //   A. projectRoot    - the authorised project (source, config, project node_modules)
  //   B. TS_LIB_REAL    - the canonical path of the verified USIT TypeScript
  //                       install, i.e. the compiler AND its bundled lib/*.d.ts.
  // A path may be read only if its canonical (realpath) form lies within A or B.
  // Anything else is refused; there is no fallback to the default ts.sys reader.
  const TS_LIB_REAL = TS_TRUST.trusted ? TS_TRUST.realPath : null;

  // Paths reported in results must not leak the analyzer's absolute install
  // directory: anything inside the trusted TS install is rendered as "<ts-lib>/…".
  function displayPath(full) {
    let real = full;
    try { real = fs.realpathSync(full); } catch (_) { real = full; }
    if (TS_LIB_REAL && within(TS_LIB_REAL, real)) {
      return "<ts-lib>/" + path.relative(TS_LIB_REAL, real).replace(/\\/g, "/");
    }
    return path.relative(projectRoot, full).replace(/\\/g, "/");
  }

  function withinTrustedRoot(real) {
    if (within(projectRoot, real)) return true;             // boundary A
    if (TS_LIB_REAL && within(TS_LIB_REAL, real)) return true; // boundary B
    return false;
  }

  host.getCurrentDirectory = () => projectRoot;
  // In-memory first: source the parent verified comes from memory (boundary A
  // content the parent already proved); the compiler's own standard library is
  // read only from the verified install (boundary B); everything else must be
  // strictly inside one of the two roots.
  host.readFile = (file) => {
    const k = keyOf(file);
    if (contentByFull.has(k)) return contentByFull.get(k);
    const resolved = path.resolve(file);
    let real;
    try { real = fs.realpathSync(resolved); } catch (_) { return undefined; }
    if (!withinTrustedRoot(real)) return undefined;
    try { return fs.readFileSync(real, "utf8"); } catch (_) { return undefined; }
  };
  host.fileExists = (file) => {
    const k = keyOf(file);
    if (contentByFull.has(k)) return true;
    const resolved = path.resolve(file);
    let real;
    try { real = fs.realpathSync(resolved); } catch (_) { return false; }
    if (!withinTrustedRoot(real)) return false;
    try { return fs.statSync(real).isFile(); } catch (_) { return false; }
  };
  host.directoryExists = (dir) => {
    const k = keyOf(dir);
    const resolved = path.resolve(dir);
    let real;
    try { real = fs.realpathSync(resolved); } catch (_) { return false; }
    if (!withinTrustedRoot(real)) return false;
    try { return fs.statSync(real).isDirectory(); } catch (_) { return false; }
  };
  host.realpath = (file) => {
    const k = keyOf(file);
    if (contentByFull.has(k)) return path.resolve(file);
    const resolved = path.resolve(file);
    let real;
    try { real = fs.realpathSync(resolved); } catch (_) { return resolved; }
    return withinTrustedRoot(real) ? real : resolved;
  };
  // The scanner supplies the bounded, discovered source set explicitly. Keep
  // those files as roots even when tsconfig omits JS from fileNames (a common
  // checkJs=false behavior); config membership remains diagnostic evidence,
  // not a reason to silently skip a discovered source file.
  const includedRoots = sourcePaths;
  for (const entry of accepted) {
    if (!includedRoots.includes(entry.full)) addFileDiag(entry.rel, diagnostic("CONFIG_EXCLUDED_SOURCE", "UNKNOWN", "The source was discovered but excluded by the project config; syntax-only fallback is used."));
  }
  const roots = [...(includedRoots.length ? includedRoots : sourcePaths),
    ...Array.from(vueTwins.values(), (twin) => twin.full)];
  const program = ts.createProgram({ rootNames: roots, options, host });
  const checker = program.getTypeChecker();
  const discoveredByFull = new Map(accepted.map((entry) => [path.resolve(entry.full), entry.rel]));
  for (const [rel, twin] of vueTwins) discoveredByFull.set(path.resolve(twin.full), rel);
  const rows = {};
  const records = [];
  // V1: a Vue entry is analyzed through its twin program file; every other
  // entry keeps the existing program-or-standalone fallback.
  function sourceFileFor(entry) {
    const twin = vueTwins.get(entry.rel);
    if (twin) {
      const fromProgram = program.getSourceFile(twin.full);
      if (fromProgram) return fromProgram;
    }
    return program.getSourceFile(entry.full) || ts.createSourceFile(entry.full, safeRead(entry.full), ts.ScriptTarget.Latest, true, scriptKind(entry.full));
  }
  const sourceFiles = accepted.map((entry) => sourceFileFor(entry));

  function safeRead(full) {
    const k = keyOf(full);
    if (contentByFull.has(k)) return contentByFull.get(k);
    // Guarded fallback (legacy disk mode, or node_modules/config/lib).  Never
    // follows a path that escapes the project root.
    let real;
    try { real = fs.realpathSync(path.resolve(full)); } catch (_) { return ""; }
    if (!within(projectRoot, real)) return "";
    try { return new TextDecoder("utf-8", { fatal: true }).decode(fs.readFileSync(real)); } catch (_) { return ""; }
  }

  function record(entry, nodeType, name, relation, status, confidence, evidence, where, diagnostics) {
    const row = {
      language: languageFor(entry.full), file_path: entry.rel, parser_name: PARSER,
      parser_version: ts.version, node_type: nodeType, qualified_name: name || "UNKNOWN",
      source_location: { file_path: entry.rel, line: where.line, column: where.column }, relation_type: relation, resolution_status: status,
      confidence, evidence: evidence || { source_file: entry.rel }, diagnostics: diagnostics || [],
    };
    records.push(row);
    return row;
  }

  const importsBySource = new Map();
  const moduleDiagnostics = new Map();
  for (const d of ts.getPreEmitDiagnostics(program)) {
    if (!d.file) continue;
    const key = path.resolve(d.file.fileName);
    if (!moduleDiagnostics.has(key)) moduleDiagnostics.set(key, []);
    if (d.code === 2307 || d.code === 2305 || d.code === 7016) {
      const pos = d.start == null ? null : d.file.getLineAndCharacterOfPosition(d.start);
      moduleDiagnostics.get(key).push(diagnostic("TS_" + d.code, "UNKNOWN", flattenMessage(d.messageText), pos ? pos.line + 1 : null, pos ? pos.character + 1 : null));
    }
  }

  function bindingRows(statement) {
    const result = [];
    const clause = statement.importClause;
    if (!clause) return result;
    if (clause.name) result.push({ imported: "default", local: clause.name.text, kind: "default" });
    if (clause.namedBindings && ts.isNamespaceImport(clause.namedBindings)) result.push({ imported: "*", local: clause.namedBindings.name.text, kind: "namespace" });
    if (clause.namedBindings && ts.isNamedImports(clause.namedBindings)) {
      for (const spec of clause.namedBindings.elements) result.push({ imported: (spec.propertyName || spec.name).text, local: spec.name.text, kind: "named" });
    }
    return result;
  }

  function targetNode(node) {
    if (ts.isFunctionDeclaration(node) && node.body) return node;
    if ((ts.isMethodDeclaration(node) || ts.isMethodSignature(node)) && node.body) return node;
    if (ts.isClassDeclaration(node) && node.name) return node;
    if (ts.isVariableDeclaration(node) && node.initializer && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) return node;
    if (ts.isPropertyDeclaration(node) && node.initializer && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) return node;
    if (ts.isConstructorDeclaration(node) && node.body) return node;
    return null;
  }

  function symbolAfterAliases(symbol) {
    const seen = new Set();
    let current = symbol;
    while (current && current.flags & ts.SymbolFlags.Alias && !seen.has(current)) {
      seen.add(current);
      try { current = checker.getAliasedSymbol(current); } catch (_) { return null; }
    }
    return current || null;
  }

  function declarationTargets(symbol) {
    const distinct = new Map();
    const declarations = symbol && symbol.declarations ? symbol.declarations : [];
    for (const declaration of declarations) {
      const target = targetNode(declaration);
      if (!target) continue;
      const sf = target.getSourceFile();
      const full = path.resolve(sf.fileName);
      const entry = discoveredByFull.get(full);
      const pos = sf.getLineAndCharacterOfPosition(target.getStart(sf));
      const nameNode = target.name || (ts.isVariableDeclaration(target) ? target.name : null);
      const name = nameNode && (ts.isIdentifier(nameNode) || ts.isPrivateIdentifier(nameNode)) ? nameNode.text : symbol.getName();
      const id = "symbol:" + (entry || path.relative(projectRoot, full).replace(/\\/g, "/")) + "#" + name + ":" + (pos.line + 1);
      distinct.set(id, { target, full, entry, name, line: pos.line + 1, id });
    }
    return Array.from(distinct.values());
  }

  function findImportEvidence(sourceFile, localName, referenceNode) {
    const imports = importsBySource.get(path.resolve(sourceFile.fileName)) || [];
    const referenceSymbol = referenceNode ? checker.getSymbolAtLocation(referenceNode) : null;
    return imports.find((item) => {
      if (!item.bindings.some((binding) => binding.local === localName)) return false;
      const bindingNode = item.binding_nodes && item.binding_nodes[localName];
      if (!bindingNode || !referenceSymbol) return false;
      return checker.getSymbolAtLocation(bindingNode) === referenceSymbol;
    }) || null;
  }

  function reexportEvidence(targets) {
    const result = [];
    const targetIds = new Set(targets.map((x) => x.id));
    for (const sourceFile of sourceFiles) {
      for (const statement of sourceFile.statements) {
        if (!ts.isExportDeclaration(statement) || !statement.exportClause || !ts.isNamedExports(statement.exportClause)) continue;
        for (const spec of statement.exportClause.elements) {
          let sym = symbolAfterAliases(checker.getSymbolAtLocation(spec.name));
          if (!sym) continue;
          if (declarationTargets(sym).some((t) => targetIds.has(t.id))) {
            const position = loc(sourceFile, spec);
            const entry = accepted.find((x) => path.resolve(x.full) === path.resolve(sourceFile.fileName));
            if (entry) result.push({ kind: "reexport", file: entry.rel, line: position.line, column: position.column, exported: spec.name.text });
          }
        }
      }
    }
    return result;
  }

  function callStatus(call, sourceFile, entry, symbol, localName, importEvidence) {
    const pos = loc(sourceFile, call);
    const base = { callee: call.expression.getText(sourceFile), line: pos.line, column: pos.column, method: PARSER, confidence: "high", resolved: false, resolution_status: "UNKNOWN", diagnostics: [] };
    if (importEvidence) base.import_evidence = { path: entry.rel, line: importEvidence.line, module: importEvidence.module, imported_as: localName };
    const expr = call.expression;
    if (ts.isElementAccessExpression(expr) && (!expr.argumentExpression || !ts.isStringLiteralLike(expr.argumentExpression) && !ts.isNumericLiteral(expr.argumentExpression))) {
      base.resolution_status = "DYNAMIC"; base.confidence = "low"; base.diagnostic = "COMPUTED_PROPERTY_CALL";
      base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }]; return base;
    }
    if (ts.isIdentifier(expr) && ["eval", "Function"].includes(expr.text)) {
      base.resolution_status = "DYNAMIC"; base.confidence = "low"; base.diagnostic = "DYNAMIC_CODE_EXECUTION_CALL";
      base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }]; return base;
    }
    const resolvedSymbol = symbolAfterAliases(symbol);
    const rawDeclarations = resolvedSymbol && resolvedSymbol.declarations ? resolvedSymbol.declarations : [];
    const externalDeclarations = rawDeclarations.filter((declaration) => !discoveredByFull.has(path.resolve(declaration.getSourceFile().fileName)));
    if (externalDeclarations.length) {
      const declaration = externalDeclarations[0];
      const full = path.resolve(declaration.getSourceFile().fileName);
      const externalFile = displayPath(full);
      const position = loc(declaration.getSourceFile(), declaration);
      base.resolution_status = "EXTERNAL"; base.confidence = "medium"; base.diagnostic = "DECLARATION_OUTSIDE_ANALYZED_SOURCE_SET";
      base.target = { file: externalFile, name: resolvedSymbol.getName(), line: position.line, id: "external:" + externalFile + "#" + resolvedSymbol.getName() + ":" + position.line };
      base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }, { kind: "external_definition", file: externalFile, line: position.line }];
      if (importEvidence) base.import_evidence = { path: entry.rel, line: importEvidence.line, module: importEvidence.module, imported_as: localName };
      return base;
    }
    // A conditional rebinding can expose a union of callable symbols. The
    // checker may return one branch as the apparent symbol, so inspect the
    // initializer before accepting that single declaration as proof.
    let conditionalTargets = [];
    if (resolvedSymbol) {
      for (const declaration of rawDeclarations) {
        if (!ts.isVariableDeclaration(declaration) || !declaration.initializer || !ts.isConditionalExpression(declaration.initializer)) continue;
        for (const branch of [declaration.initializer.whenTrue, declaration.initializer.whenFalse]) {
          const branchSymbol = symbolAfterAliases(checker.getSymbolAtLocation(branch));
          conditionalTargets.push(...declarationTargets(branchSymbol));
        }
      }
    }
    const conditionalUnique = new Map(conditionalTargets.map((target) => [target.id, target]));
    if (conditionalUnique.size > 1) {
      base.resolution_status = "AMBIGUOUS"; base.confidence = "low";
      base.candidate_targets = Array.from(conditionalUnique.keys()).sort();
      base.diagnostic = "CONDITIONAL_CALL_TARGET_HAS_MULTIPLE_DEFINITIONS";
      base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }]; return base;
    }
    let targets = declarationTargets(resolvedSymbol);
    if (!targets.length && resolvedSymbol && rawDeclarations.some((declaration) => /\.d\.(?:ts|mts|cts)$/i.test(declaration.getSourceFile().fileName))) {
      const declaration = rawDeclarations.find((item) => /\.d\.(?:ts|mts|cts)$/i.test(item.getSourceFile().fileName));
      const source = declaration.getSourceFile();
      const declarationEntry = discoveredByFull.get(path.resolve(source.fileName));
      const position = loc(source, declaration);
      base.resolution_status = "UNKNOWN"; base.confidence = "low"; base.diagnostic = "TYPE_DECLARATION_HAS_NO_RUNTIME_TARGET";
      base.target = { file: declarationEntry || displayPath(source.fileName), name: resolvedSymbol.getName(), line: position.line };
      base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }, { kind: "type_declaration", file: base.target.file, line: position.line }];
      return base;
    }
    if (!targets.length && resolvedSymbol) {
      const type = checker.getTypeAtLocation(expr);
      const signatures = [];
      const seenTypes = new Set();
      function collectCallSignatures(candidateType) {
        if (!candidateType || seenTypes.has(candidateType)) return;
        seenTypes.add(candidateType);
        signatures.push(...checker.getSignaturesOfType(candidateType, ts.SignatureKind.Call));
        if (candidateType.flags & (ts.TypeFlags.Union | ts.TypeFlags.Intersection)) {
          for (const member of candidateType.types || []) collectCallSignatures(member);
        }
      }
      collectCallSignatures(type);
      const alternatives = new Map();
      for (const signature of signatures) for (const declaration of (signature.declaration ? [signature.declaration] : [])) {
        const candidate = targetNode(declaration);
        if (!candidate) continue;
        const sf = candidate.getSourceFile(); const rel = discoveredByFull.get(path.resolve(sf.fileName));
        if (!rel) continue;
        const l = sf.getLineAndCharacterOfPosition(candidate.getStart(sf)).line + 1;
        const n = candidate.name && candidate.name.text ? candidate.name.text : resolvedSymbol.getName();
        const id = rel + "#" + n + ":" + l; alternatives.set(id, { target: candidate, full: path.resolve(sf.fileName), entry: rel, name: n, line: l, id });
      }
      if (alternatives.size) targets = Array.from(alternatives.values());
    }
    if (targets.length > 1) {
      base.resolution_status = "AMBIGUOUS"; base.confidence = "low";
      base.candidate_targets = targets.map((t) => t.id).sort(); base.diagnostic = "MULTIPLE_CALLABLE_DECLARATIONS";
      base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }]; return base;
    }
    if (targets.length === 1) {
      const target = targets[0];
      if (!target.entry || target.full.toLowerCase().includes(path.sep + "node_modules" + path.sep)) {
        base.resolution_status = "EXTERNAL"; base.confidence = "medium"; base.diagnostic = "TARGET_OUTSIDE_ANALYZED_SOURCE_SET";
        base.target = { file: displayPath(target.full), name: target.name, line: target.line, id: target.id };
        base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }, { kind: "external_definition", file: base.target.file, line: target.line }]; return base;
      }
      if (path.basename(target.full).endsWith(".d.ts") || path.basename(target.full).endsWith(".d.mts") || path.basename(target.full).endsWith(".d.cts")) {
        base.resolution_status = "UNKNOWN"; base.confidence = "low"; base.diagnostic = "TYPE_ONLY_DECLARATION_HAS_NO_RUNTIME_BODY";
        base.target = { file: target.entry, name: target.name, line: target.line, id: target.id };
        base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }, { kind: "type_declaration", file: target.entry, line: target.line }]; return base;
      }
      base.resolution_status = "RESOLVED"; base.resolved = true;
      base.target = { file: target.entry, name: target.name, line: target.line, id: "symbol:" + target.entry + "#" + target.name + ":" + target.line, qualified_name: target.entry.replace(/\//g, ".") + "." + target.name };
      const callable = (ts.isVariableDeclaration(target.target) || ts.isPropertyDeclaration(target.target))
        ? target.target.initializer : target.target;
      const parameters = callable && callable.parameters ? callable.parameters : [];
      const argumentsList = call.arguments || [];
      const unsupportedArguments = argumentsList.some((argument) => ts.isSpreadElement(argument))
        || argumentsList.length > parameters.length
        || parameters.some((parameter) => !!parameter.dotDotDotToken || !ts.isIdentifier(parameter.name));
      base.argument_binding_status = unsupportedArguments ? "UNSUPPORTED_ARGUMENT_SHAPE" : "EXACT_STATIC_MAPPING";
      base.argument_bindings = unsupportedArguments ? [] : argumentsList.map((argument, index) => {
        const parameter = parameters[index];
        const argumentPosition = loc(sourceFile, argument);
        const parameterPosition = loc(parameter.getSourceFile(), parameter.name);
        return {
          argument_index: index,
          argument_kind: "POSITIONAL",
          argument_expression: argument.getText(sourceFile).slice(0, 240),
          argument_location: { path: entry.rel, line: argumentPosition.line, column: argumentPosition.column },
          parameter_name: parameter.name.text,
          parameter_index: index,
          parameter_kind: "POSITIONAL",
          parameter_declaration: { path: target.entry, line: parameterPosition.line, column: parameterPosition.column }
        };
      });
      const chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }];
      if (importEvidence) chain.push({ kind: "import", file: entry.rel, line: importEvidence.line, module: importEvidence.module, imported_as: localName });
      chain.push(...reexportEvidence([target]));
      chain.push({ kind: "definition", file: target.entry, line: target.line, name: target.name });
      base.evidence_chain = chain;
      if (importEvidence) base.import_evidence = { path: entry.rel, line: importEvidence.line, module: importEvidence.module, imported_as: localName };
      return base;
    }
    if (importEvidence) {
      const moduleName = importEvidence.module;
      const moduleResolution = ts.resolveModuleName(moduleName, sourceFile.fileName, options, host).resolvedModule;
      if (!moduleResolution) {
        base.resolution_status = moduleName.startsWith(".") || moduleName.startsWith("/") ? "UNRESOLVED" : "EXTERNAL";
        base.confidence = "low"; base.diagnostic = base.resolution_status === "UNRESOLVED" ? "PROJECT_MODULE_NOT_FOUND" : "EXTERNAL_PACKAGE_NOT_RESOLVED";
        base.import_evidence = { path: entry.rel, line: importEvidence.line, module: moduleName, imported_as: localName };
        base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }, { kind: "import", file: entry.rel, line: importEvidence.line, module: moduleName, imported_as: localName }];
        return base;
      }
    }
    const type = checker.getTypeAtLocation(expr);
    if (type.flags & ts.TypeFlags.Any) {
      base.resolution_status = "DYNAMIC"; base.confidence = "low"; base.diagnostic = "CALL_TARGET_HAS_ANY_TYPE";
    } else if (ts.isElementAccessExpression(expr)) {
      base.resolution_status = "DYNAMIC"; base.confidence = "low"; base.diagnostic = "COMPUTED_PROPERTY_CALL";
    }
    base.evidence_chain = [{ kind: "call", file: entry.rel, line: pos.line, column: pos.column }];
    return base;
  }

  for (const entry of accepted) {
    const sourceFile = sourceFileFor(entry);
    const row = { path: entry.rel, language: languageFor(entry.full), parser_name: PARSER, parser_version: ts.version, syntax_status: "VALID", symbols: [], imports: [], calls: [], api_client_calls: [], diagnostics: [] };
    const syntaxErrors = sourceFile.parseDiagnostics || [];
    for (const d of syntaxErrors) {
      const position = d.start == null ? null : sourceFile.getLineAndCharacterOfPosition(d.start);
      row.diagnostics.push(diagnostic("TYPESCRIPT_SYNTAX_ERROR_" + d.code, "ERROR", flattenMessage(d.messageText), position ? position.line + 1 : null, position ? position.character + 1 : null));
    }
    if (syntaxErrors.length) row.syntax_status = "INVALID";
    row.diagnostics.push(...(fileDiagnostics[entry.rel] || []));
    row.diagnostics.push(...(moduleDiagnostics.get(path.resolve(sourceFile.fileName)) || []));

    const imports = [];
    const calls = [];
    const importEvidenceRows = [];
    for (const statement of sourceFile.statements) {
      if (ts.isImportDeclaration(statement) && ts.isStringLiteralLike(statement.moduleSpecifier)) {
        const position = loc(sourceFile, statement);
        const bindingNodes = {};
        const clause = statement.importClause;
        if (clause && clause.name) bindingNodes[clause.name.text] = clause.name;
        if (clause && clause.namedBindings && ts.isNamespaceImport(clause.namedBindings)) bindingNodes[clause.namedBindings.name.text] = clause.namedBindings.name;
        if (clause && clause.namedBindings && ts.isNamedImports(clause.namedBindings)) for (const spec of clause.namedBindings.elements) bindingNodes[spec.name.text] = spec.name;
        importEvidenceRows.push({ module: statement.moduleSpecifier.text, line: position.line, bindings: bindingRows(statement), binding_nodes: bindingNodes });
      } else if (ts.isExportDeclaration(statement) && statement.moduleSpecifier && ts.isStringLiteralLike(statement.moduleSpecifier)) {
        const position = loc(sourceFile, statement);
        const bindings = statement.exportClause && ts.isNamedExports(statement.exportClause) ? statement.exportClause.elements.map((spec) => ({ imported: (spec.propertyName || spec.name).text, local: spec.name.text, kind: "reexport" })) : [];
        importEvidenceRows.push({ module: statement.moduleSpecifier.text, line: position.line, bindings });
      }
    }
    function collectRequireImports(node) {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && ts.isCallExpression(node.initializer)
          && ts.isIdentifier(node.initializer.expression) && node.initializer.expression.text === "require"
          && node.initializer.arguments.length && ts.isStringLiteralLike(node.initializer.arguments[0])) {
        const position = loc(sourceFile, node.initializer);
        importEvidenceRows.push({ module: node.initializer.arguments[0].text, line: position.line,
          bindings: [{ imported: "*", local: node.name.text, kind: "commonjs" }], binding_nodes: { [node.name.text]: node.name } });
      }
      ts.forEachChild(node, collectRequireImports);
    }
    collectRequireImports(sourceFile);
    importsBySource.set(path.resolve(sourceFile.fileName), importEvidenceRows);
    function isFunctionBoundary(node) {
      return ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)
        || ts.isArrowFunction(node) || ts.isFunctionExpression(node)
        || ts.isConstructorDeclaration(node) || ts.isGetAccessorDeclaration(node)
        || ts.isSetAccessorDeclaration(node);
    }

    function declaredFunctionOwner(node) {
      let name = null;
      let locationNode = node;
      if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.name) {
        name = node.name.getText(sourceFile);
      } else if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
        const parent = node.parent;
        if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
          name = parent.name.text;
        } else if (ts.isPropertyDeclaration(parent)) {
          name = parent.name.getText(sourceFile);
        } else if (node.name && ts.isIdentifier(node.name)) {
          name = node.name.text;
        }
      } else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer
          && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) {
        name = node.name.text;
      } else if (ts.isPropertyDeclaration(node) && node.initializer
          && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) {
        name = node.name.getText(sourceFile);
      }
      if (!name) return null;
      const position = loc(sourceFile, locationNode);
      return { name, line: position.line,
        symbol_id: "symbol:" + entry.rel + "#" + name + ":" + position.line };
    }

    function visit(node, testContext, callerOwner) {
      // Anonymous callbacks/lambdas are real scope boundaries. They have no
      // stable symbol yet, so calls inside them must not inherit the outer
      // named function's identity. Named declarations and assigned arrows
      // retain their own exact owner symbol.
      const currentOwner = isFunctionBoundary(node)
        ? declaredFunctionOwner(node)
        : (declaredFunctionOwner(node) || callerOwner || null);
      let childTestContext = testContext;
      if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.name && /^test\w*/i.test(node.name.getText(sourceFile))) {
        childTestContext = node.name.getText(sourceFile);
      }
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && ["test", "it"].includes(node.expression.text) && node.arguments.length && ts.isStringLiteralLike(node.arguments[0])) {
        childTestContext = node.arguments[0].text;
      }
      if (ts.isImportDeclaration(node) && ts.isStringLiteralLike(node.moduleSpecifier)) {
        const position = loc(sourceFile, node);
        const item = { name: node.moduleSpecifier.text, module: node.moduleSpecifier.text, line: position.line, column: position.column, evidence: node.getText(sourceFile).slice(0, 240), bindings: bindingRows(node), method: PARSER };
        imports.push(item);
        record(entry, "import", item.module, "imports", "UNKNOWN", 0.0, { import: item }, position, []);
      } else if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteralLike(node.moduleSpecifier)) {
        const position = loc(sourceFile, node);
        const item = { name: node.moduleSpecifier.text, module: node.moduleSpecifier.text, line: position.line, column: position.column, evidence: node.getText(sourceFile).slice(0, 240), bindings: [], reexport: true, method: PARSER };
        if (node.exportClause && ts.isNamedExports(node.exportClause)) item.bindings = node.exportClause.elements.map((spec) => ({ imported: (spec.propertyName || spec.name).text, local: spec.name.text, kind: "reexport" }));
        imports.push(item);
        record(entry, "reexport", item.module, "imports", "UNKNOWN", 0.0, { reexport: item }, position, []);
      } else if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
        if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "require" && node.arguments.length && ts.isStringLiteralLike(node.arguments[0])) {
          const position = loc(sourceFile, node);
          const parent = node.parent;
          const bindings = ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name) ? [{ imported: "*", local: parent.name.text, kind: "commonjs" }] : [];
          const item = { name: node.arguments[0].text, module: node.arguments[0].text, line: position.line, column: position.column, evidence: node.getText(sourceFile).slice(0, 240), bindings, commonjs: true, method: PARSER };
          imports.push(item);
        } else {
          const expression = node.expression;
          const localName = ts.isIdentifier(expression) ? expression.text : (ts.isPropertyAccessExpression(expression) && ts.isIdentifier(expression.expression) ? expression.expression.text : null);
          const symbolNode = ts.isPropertyAccessExpression(expression) ? expression.name : expression;
          const symbol = checker.getSymbolAtLocation(symbolNode);
          const importReference = ts.isPropertyAccessExpression(expression) ? expression.expression : expression;
          const importEvidence = localName ? findImportEvidence(sourceFile, localName, importReference) : null;
          const call = callStatus(node, sourceFile, entry, symbol, localName, importEvidence);
          if (currentOwner) {
            call.caller = { name: currentOwner.name, line: currentOwner.line };
            call.caller_symbol_id = currentOwner.symbol_id;
          }
          if (childTestContext) call.test_name = childTestContext;
          if (row.syntax_status === "INVALID" && call.resolution_status === "RESOLVED") {
            call.resolution_status = "UNKNOWN"; call.resolved = false; call.diagnostic = "SOURCE_HAS_SYNTAX_ERRORS"; delete call.target;
          }
          if (ts.isNewExpression(node) && call.resolution_status === "RESOLVED") call.relation_type = "instantiates";
          calls.push(call);
          if (ts.isCallExpression(node)) {
            const calleeText = node.expression.getText(sourceFile);
            const isFetch = ts.isIdentifier(node.expression) && node.expression.text === "fetch";
            const isAxios = /^(?:axios|http|api|client)\.(?:get|post|put|patch|delete|head|options)$/i.test(calleeText);
            if (isFetch || isAxios) {
              const args = node.arguments || [];
              const urlArg = args[0];
              const literalUrl = urlArg && (ts.isStringLiteralLike(urlArg) || ts.isNoSubstitutionTemplateLiteral(urlArg)) ? urlArg.text : null;
              let httpMethod = isFetch ? "GET" : calleeText.split(".").pop().toUpperCase();
              if (isFetch && args[1] && ts.isObjectLiteralExpression(args[1])) {
                for (const property of args[1].properties) {
                  if (ts.isPropertyAssignment(property) && property.name && property.name.getText(sourceFile).replace(/["']/g, "") === "method"
                      && property.initializer && (ts.isStringLiteralLike(property.initializer) || ts.isNoSubstitutionTemplateLiteral(property.initializer))) {
                    httpMethod = property.initializer.text.toUpperCase();
                  }
                }
              }
              const apiPosition = loc(sourceFile, node);
              // F2: a single literal keeps the old rule exactly (a literal
              // containing "?" / "#" / "$" stays dynamic); anything else
              // resolves through the URL skeleton, whose "{?}" placeholders
              // mark template interpolations.
              let skeletonPath = null;
              let apiResolution = "DYNAMIC_OR_UNSUPPORTED_URL";
              if (literalUrl !== null) {
                if (literalUrl.startsWith("/") && !/[?#{}$]/.test(literalUrl)) {
                  skeletonPath = literalUrl;
                  apiResolution = httpMethod ? "LITERAL_METHOD_PATH" : "DYNAMIC_OR_UNSUPPORTED_URL";
                }
              } else {
                const skeleton = urlArg ? resolveUrlSkeleton(urlArg) : null;
                skeletonPath = skeleton !== null ? extractSkeletonPath(skeleton) : null;
                if (skeletonPath !== null && httpMethod) {
                  apiResolution = skeletonPath.includes("{?}")
                    ? "TEMPLATE_METHOD_PATH" : "LITERAL_METHOD_PATH";
                }
              }
              row.api_client_calls.push({
                callee: calleeText, method: httpMethod,
                path: skeletonPath,
                url_expression: urlArg ? urlArg.getText(sourceFile).slice(0, 240) : null,
                line: apiPosition.line, column: apiPosition.column,
                caller_symbol_id: currentOwner ? currentOwner.symbol_id : null,
                caller_name: currentOwner ? currentOwner.name : null,
                resolution_status: apiResolution,
                confidence: "STATIC_INFERENCE", verification_status: "NOT_VERIFIED",
              });
            }
          }
        }
      }
      ts.forEachChild(node, (child) => visit(child, childTestContext, currentOwner));
    }
    for (const statement of sourceFile.statements) visit(statement, null, null);
    row.imports = imports;
    row.calls = calls;
    row.route_handlers = [];
    function collectRouteHandlers(node) {
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
          && ["get", "post", "put", "patch", "delete", "options", "head", "all"].includes(node.expression.name.text.toLowerCase())
          && node.arguments.length >= 2 && ts.isStringLiteralLike(node.arguments[0])) {
        const handlerNode = node.arguments[node.arguments.length - 1];
        const handlerReference = handlerNode.getText(sourceFile);
        const position = loc(sourceFile, node);
        const methodName = node.expression.name.text.toUpperCase();
        const handlerSymbol = checker.getSymbolAtLocation(handlerNode);
        const resolved = symbolAfterAliases(handlerSymbol);
        const targets = declarationTargets(resolved);
        const unique = new Map(targets.map((target) => [target.id, target]));
        const importRoot = ts.isIdentifier(handlerNode) ? handlerNode
          : (ts.isPropertyAccessExpression(handlerNode) && ts.isIdentifier(handlerNode.expression)
            ? handlerNode.expression : null);
        const importEvidence = importRoot
          ? findImportEvidence(sourceFile, importRoot.text, importRoot) : null;
        const recordRow = {
          method: methodName, path: node.arguments[0].text,
          line: position.line, column: position.column,
          handler_reference: handlerReference,
          resolution_status: "UNKNOWN", candidate_targets: [],
          evidence_chain: [{ kind: "route_registration", file: entry.rel, line: position.line, column: position.column }]
        };
        if (unique.size === 1) {
          const target = Array.from(unique.values())[0];
          if (target.entry && !/\\.d\\.(?:ts|mts|cts)$/i.test(target.full)) {
            recordRow.resolution_status = "RESOLVED";
            recordRow.target = {
              id: "symbol:" + target.entry + "#" + target.name + ":" + target.line,
              path: target.entry, name: target.name, line: target.line
            };
            if (importEvidence) {
              recordRow.evidence_chain.push({
                kind: "handler_import", file: entry.rel, line: importEvidence.line,
                module: importEvidence.module, imported_as: importRoot.text,
              });
            }
            recordRow.evidence_chain.push(...reexportEvidence([target]));
            recordRow.evidence_chain.push({
              kind: "handler_definition", file: target.entry, line: target.line, name: target.name
            });
          } else {
            recordRow.resolution_status = "EXTERNAL";
          }
        } else if (unique.size > 1) {
          recordRow.resolution_status = "AMBIGUOUS";
          recordRow.candidate_targets = Array.from(unique.keys()).sort();
        }
        row.route_handlers.push(recordRow);
      }
      ts.forEachChild(node, collectRouteHandlers);
    }
    collectRouteHandlers(sourceFile);

    // S2.13-A: explicit JSX event-prop -> named handler evidence.
    row.ui_event_bindings = [];
    const uiEventProps = new Set(["onClick", "onSubmit", "onChange", "onPress", "onTap", "onLongPress"]);
    function collectUiEventBindings(node) {
      if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name) && uiEventProps.has(node.name.text)) {
        const position = loc(sourceFile, node);
        const initializer = node.initializer;
        const expression = initializer && ts.isJsxExpression(initializer) ? initializer.expression : null;
        const item = { event_name: node.name.text, line: position.line, column: position.column,
          binding_expression: expression ? expression.getText(sourceFile).slice(0, 240) : null,
          resolution_status: "UNKNOWN", candidate_targets: [], confidence: "STATIC_INFERENCE",
          verification_status: "NOT_VERIFIED" };
        if (expression && (ts.isIdentifier(expression) || ts.isPropertyAccessExpression(expression))) {
          const symbolNode = ts.isPropertyAccessExpression(expression) ? expression.name : expression;
          const resolved = symbolAfterAliases(checker.getSymbolAtLocation(symbolNode));
          const targets = declarationTargets(resolved);
          const unique = new Map(targets.map((target) => [target.id, target]));
          if (unique.size === 1) {
            const target = Array.from(unique.values())[0];
            if (target.entry && !/\\.d\\.(?:ts|mts|cts)$/i.test(target.full)) {
              item.resolution_status = "RESOLVED";
              item.target = { id: "symbol:" + target.entry + "#" + target.name + ":" + target.line,
                path: target.entry, name: target.name, line: target.line };
              item.evidence_chain = [{ kind: "jsx_event_attribute", file: entry.rel, line: position.line },
                { kind: "handler_definition", file: target.entry, line: target.line, name: target.name }];
            } else item.resolution_status = "EXTERNAL";
          } else if (unique.size > 1) {
            item.resolution_status = "AMBIGUOUS";
            item.candidate_targets = Array.from(unique.keys()).sort();
          }
        } else if (expression && (ts.isArrowFunction(expression) || ts.isFunctionExpression(expression))) {
          item.resolution_status = "INLINE_CALLBACK_NOT_SYMBOL_LINKED";
        } else if (initializer && ts.isStringLiteral(initializer)) {
          item.resolution_status = "NON_EXPRESSION_ATTRIBUTE";
        }
        row.ui_event_bindings.push(item);
      }
      ts.forEachChild(node, collectUiEventBindings);
    }
    collectUiEventBindings(sourceFile);

    const importBindings = new Map();
    for (const item of imports) for (const binding of item.bindings || []) importBindings.set(binding.local, { module: item.module, line: item.line, imported: binding.imported, kind: binding.kind });

    function collectSymbols(node, parentNames) {
      let name = null; let kind = null;
      if (ts.isFunctionDeclaration(node) && node.name) { name = node.name.text; kind = "function"; }
      else if (ts.isClassDeclaration(node) && node.name) { name = node.name.text; kind = "class"; }
      else if (ts.isMethodDeclaration(node) && node.name) { name = node.name.getText(sourceFile); kind = "method"; }
      else if (ts.isInterfaceDeclaration(node)) { name = node.name.text; kind = "interface"; }
      else if (ts.isTypeAliasDeclaration(node)) { name = node.name.text; kind = "type"; }
      else if (ts.isEnumDeclaration(node)) { name = node.name.text; kind = "enum"; }
      else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) { name = node.name.text; kind = "function"; }
      if (name && kind) {
        const position = loc(sourceFile, node);
        const qn = parentNames.concat(name).join(".");
        const item = { name, qualified_name: qn, kind, line: position.line, column: position.column, evidence: node.getText(sourceFile).slice(0, 240), parser_name: PARSER, parser_version: ts.version, symbol_id: "symbol:" + entry.rel + "#" + qn + ":" + position.line };
        row.symbols.push(item);
        record(entry, kind, qn, "defines", "UNKNOWN", 0.0, { definition: { path: entry.rel, line: position.line, column: position.column } }, position, []);
      }
      const nextNames = name && ["class", "function"].includes(kind) ? parentNames.concat(name) : parentNames;
      ts.forEachChild(node, (child) => collectSymbols(child, nextNames));
    }
    collectSymbols(sourceFile, []);
    for (const call of calls) {
      const position = { file_path: entry.rel, line: call.line, column: call.column };
      record(entry, "call", call.callee, "calls", call.resolution_status, call.resolution_status === "RESOLVED" ? 0.9 : 0.0, { evidence_chain: call.evidence_chain || [{ kind: "call", file: entry.rel, line: call.line, column: call.column }] }, position, call.diagnostics || []);
    }
    rows[entry.rel] = row;
  }

  for (const [rel, diagnostics] of Object.entries(fileDiagnostics)) {
    if (!rows[rel]) rows[rel] = { path: rel, language: languageFor(rel), parser_name: PARSER, parser_version: ts.version, syntax_status: "UNKNOWN", symbols: [], imports: [], calls: [], diagnostics };
  }
  return { contract_version: CONTRACT, parser_name: PARSER, parser_version: ts.version, available: true, project_diagnostics: projectDiagnostics, files: rows, records };
}

let inputText = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { inputText += chunk; });
process.stdin.on("end", () => {
  try {
    const result = main(JSON.parse(inputText || "{}"));
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    process.stderr.write(String(error && error.stack ? error.stack : error));
    process.exitCode = 2;
  }
});
