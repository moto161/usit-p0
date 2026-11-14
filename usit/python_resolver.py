"""Conservative project-local Python call resolution using syntax and import evidence."""
from __future__ import annotations

import ast
import builtins
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

STATUSES = {"RESOLVED", "UNRESOLVED", "AMBIGUOUS", "DYNAMIC", "EXTERNAL", "UNKNOWN"}


def _module_for_path(path: str) -> str:
    parts = list(PurePosixPath(path).parts)
    if not parts:
        return ""
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_for_module(module: str, path: str) -> str:
    return module if PurePosixPath(path).name == "__init__.py" else module.rpartition(".")[0]


def _relative_module(package: str, level: int, module: str | None) -> str | None:
    if level <= 0:
        return module or ""
    parts = package.split(".") if package else []
    remove = level - 1
    if remove > len(parts):
        return None
    prefix = parts[:len(parts) - remove] if remove else parts
    suffix = module.split(".") if module else []
    return ".".join(prefix + suffix)


def _definition_id(path: str, name: str, line: int) -> str:
    return f"symbol:{path}#{name}:{line}"


def _collect_bindings(node: ast.AST) -> set[str]:
    names: set[str] = set()

    class Bindings(ast.NodeVisitor):
        def visit_Name(self, item: ast.Name) -> None:
            if isinstance(item.ctx, ast.Store):
                names.add(item.id)

        def visit_FunctionDef(self, item: ast.FunctionDef) -> None:
            names.add(item.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, item: ast.ClassDef) -> None:
            names.add(item.name)

        def visit_Lambda(self, item: ast.Lambda) -> None:
            return

        def visit_Import(self, item: ast.Import) -> None:
            for alias in item.names:
                names.add(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, item: ast.ImportFrom) -> None:
            for alias in item.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)

        def visit_ExceptHandler(self, item: ast.ExceptHandler) -> None:
            if item.name:
                names.add(item.name)
            for statement in item.body:
                self.visit(statement)

    visitor = Bindings()
    for arg in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
        names.add(arg.arg)
    if node.args.vararg:
        names.add(node.args.vararg.arg)
    if node.args.kwarg:
        names.add(node.args.kwarg.arg)
    for statement in node.body:
        visitor.visit(statement)
    return names


class _CallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.calls: list[tuple[ast.Call, set[str], tuple[str, int] | None]] = []
        self.scopes: list[set[str]] = []
        self.owners: list[tuple[str, int] | None] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.owners.append((node.name, node.lineno))
        self.scopes.append(_collect_bindings(node))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.owners.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        names = {arg.arg for arg in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)}
        if node.args.vararg:
            names.add(node.args.vararg.arg)
        if node.args.kwarg:
            names.add(node.args.kwarg.arg)
        # A lambda is a distinct callable scope but has no stable symbol in
        # the current graph model. Mask an enclosing named function rather
        # than falsely attributing lambda-body calls to that outer function.
        self.owners.append(None)
        self.scopes.append(names)
        self.visit(node.body)
        self.scopes.pop()
        self.owners.pop()

    def visit_Call(self, node: ast.Call) -> None:
        shadowed = set().union(*self.scopes) if self.scopes else set()
        caller = self.owners[-1] if self.owners else None
        self.calls.append((node, shadowed, caller))
        self.generic_visit(node)


def _attribute_parts(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return list(reversed(parts))


def _methods_keyword(call: ast.Call) -> list[str] | None:
    """``methods=[...]`` keyword of an ``add_url_rule``/``add_api_route`` call.

    Returns the uppercased string literals, ``["GET"]`` when the keyword is
    absent, and None when it is present but yields no string literal (fail
    closed: do not guess the methods).
    """
    for kw in call.keywords:
        if kw.arg == "methods":
            if not isinstance(kw.value, (ast.List, ast.Tuple)):
                return None
            methods = [elt.value.upper() for elt in kw.value.elts
                       if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
            return methods or None
    return ["GET"]


_ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _route_registration(call: ast.Call) -> tuple[list[str], str, ast.AST] | None:
    """Recognize a call-style route registration.

    Returns (methods, path, handler_arg) or None. The first positional
    argument must be a string literal starting with ``/`` (a route path);
    that alone rejects ``dict.get('key', default)`` and similar non-route
    calls. A handler argument is required, so decorator calls
    (``@app.route('/x')`` — one positional argument) are never reported
    here; H1 already covers them through the decorator's following `def`.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    verb = func.attr
    if not call.args:
        return None
    first = call.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    path = first.value
    if not path.startswith("/"):
        return None
    if verb in _ROUTE_VERBS:
        if len(call.args) < 2:
            return None
        return ([verb.upper()], path, call.args[-1])
    if verb == "add_url_rule":
        handler = None
        for kw in call.keywords:
            if kw.arg == "view_func":
                handler = kw.value
        if handler is None and len(call.args) >= 3:
            handler = call.args[2]
        if handler is None:
            return None
        methods = _methods_keyword(call)
        if methods is None:
            return None
        return (methods, path, handler)
    if verb == "add_api_route":
        if len(call.args) < 2:
            return None
        methods = _methods_keyword(call)
        if methods is None:
            return None
        return (methods, path, call.args[1])
    return None


def _resolve_route_handler_arg(arg: ast.AST, *, bindings: dict[str, list[dict[str, Any]]],
                               top_symbols: dict[tuple[str, str], list[dict[str, Any]]],
                               module: str, module_paths: dict[str, list[str]],
                               has_star_import: bool, module_shadows: set[str],
                               shadowed: set[str]) -> tuple[str, dict[str, Any] | None,
                                                            list[dict[str, Any]],
                                                            dict[str, Any] | None, str | None]:
    """Resolve a route handler argument through import evidence.

    Mirrors the Name/Attribute branches of the call resolver, with one
    deliberate difference: a decorated target still resolves as RESOLVED.
    The call resolver marks decorated callees UNKNOWN because a decorator
    may replace the callable's runtime behavior; a handler binding asks
    which definition handles the route, and a decorated `def` is still that
    definition, so the link is definitional, not a runtime-call claim.

    Returns (state, target, candidates, import_binding, imported_as).
    `import_binding` is the import record the resolution went through (None
    for a same-file definition); `imported_as` is the local name it was
    imported as (None when no import was involved).
    """
    if isinstance(arg, ast.Name):
        name = arg.id
        candidates = top_symbols.get((module, name), [])
        imported = bindings.get(name, [])
        if name in shadowed or name in module_shadows:
            return ("DYNAMIC", None, [], None, None)
        if len(imported) > 1:
            found = [item["target"] for item in imported
                     if item.get("kind") == "symbol" and item.get("target")]
            found.extend(target for item in imported for target in item.get("targets", []))
            return ("AMBIGUOUS", None, found, None, None)
        if imported:
            binding = imported[0]
            if binding.get("conditional"):
                found = [binding["target"]] if binding.get("target") else []
                found.extend(binding.get("targets", []))
                return ("UNKNOWN", None, found, binding, name)
            if binding["kind"] == "symbol":
                return ("RESOLVED", binding["target"], [], binding, name)
            if binding["kind"] == "external":
                return ("EXTERNAL", None, [], binding, name)
            if binding["kind"] == "ambiguous":
                return ("AMBIGUOUS", None, binding.get("targets", []), binding, name)
            if binding["kind"] == "unresolved":
                return ("UNRESOLVED", None, [], binding, name)
            return ("UNKNOWN", None, [], binding, name)
        if len(candidates) == 1:
            return ("RESOLVED", candidates[0], [], None, None)
        if len(candidates) > 1:
            return ("AMBIGUOUS", None, candidates, None, None)
        if name in vars(builtins):
            return ("EXTERNAL", None, [], None, None)
        if has_star_import:
            return ("UNKNOWN", None, [], None, None)
        return ("UNRESOLVED", None, [], None, None)
    if isinstance(arg, ast.Attribute):
        parts = _attribute_parts(arg)
        if parts and (parts[0] in shadowed or parts[0] in module_shadows):
            return ("DYNAMIC", None, [], None, None)
        if parts and bindings.get(parts[0]):
            imported = bindings[parts[0]]
            if len(imported) > 1:
                return ("AMBIGUOUS", None, [], None, None)
            binding = imported[0]
            suffix = parts[1:]
            if binding.get("conditional"):
                return ("UNKNOWN", None, [], binding, parts[0])
            if binding["kind"] == "module" and suffix:
                member = suffix[-1]
                mod = binding["module"]
                candidates = top_symbols.get((mod, member), [])
                if binding.get("external"):
                    return ("EXTERNAL", None, [], binding, parts[0])
                if len(module_paths.get(mod, [])) > 1 or len(candidates) > 1:
                    return ("AMBIGUOUS", None, candidates, binding, parts[0])
                if len(candidates) == 1:
                    return ("RESOLVED", candidates[0], [], binding, parts[0])
                return ("UNRESOLVED", None, [], binding, parts[0])
            if binding["kind"] == "external":
                return ("EXTERNAL", None, [], binding, parts[0])
            if binding["kind"] == "ambiguous":
                return ("AMBIGUOUS", None, [], binding, parts[0])
            return ("UNKNOWN", None, [], binding, parts[0])
        return ("UNKNOWN", None, [], None, None)
    return ("UNKNOWN", None, [], None, None)


def _detect_route_handlers(rel: str, calls: list[tuple[ast.Call, set[str], tuple[str, int] | None]],
                           *, bindings: dict[str, list[dict[str, Any]]],
                           top_symbols: dict[tuple[str, str], list[dict[str, Any]]],
                           module: str, module_paths: dict[str, list[str]],
                           has_star_import: bool,
                           module_shadows: set[str]) -> list[dict[str, Any]]:
    """Build `route_handlers` records for a file's call-style registrations.

    Every detected registration yields a record (UNKNOWN when the handler
    cannot be resolved — the TypeScript adapter does the same), in the
    adapter's record shape, so the scanner can emit the same binding edges
    for Python as for TypeScript.
    """
    out: list[dict[str, Any]] = []
    for call, shadowed, _caller in calls:
        registration = _route_registration(call)
        if registration is None:
            continue
        methods, path, handler_arg = registration
        state, target, candidates, import_binding, imported_as = _resolve_route_handler_arg(
            handler_arg, bindings=bindings, top_symbols=top_symbols, module=module,
            module_paths=module_paths, has_star_import=has_star_import,
            module_shadows=module_shadows, shadowed=shadowed)
        if state not in STATUSES:
            state = "UNKNOWN"
        for method in methods:
            chain: list[dict[str, Any]] = [{
                "kind": "route_registration", "file": rel,
                "line": call.lineno, "column": call.col_offset + 1,
            }]
            if state == "RESOLVED" and import_binding is not None and imported_as is not None:
                chain.append({
                    "kind": "handler_import", "file": rel, "line": import_binding["line"],
                    "module": import_binding.get("module"), "imported_as": imported_as,
                })
            if state == "RESOLVED" and target is not None:
                chain.append({
                    "kind": "handler_definition", "file": target["file"],
                    "line": target["line"], "name": target["name"],
                })
            record: dict[str, Any] = {
                "method": method, "path": path,
                "line": call.lineno, "column": call.col_offset + 1,
                "handler_reference": ast.unparse(handler_arg),
                "resolution_status": state,
                "evidence_chain": chain,
            }
            if state == "RESOLVED" and target is not None:
                record["target"] = {"id": target["id"], "path": target["file"],
                                    "name": target["name"], "line": target["line"]}
            if state == "AMBIGUOUS" and candidates:
                record["candidate_targets"] = [item["id"] for item in candidates]
            out.append(record)
    return out


def resolve_python_calls(root: str, rows: list[dict[str, Any]], *,
                         contents: dict[str, str] | None = None) -> dict[str, Any]:
    """Annotate Python call rows with conservative local/cross-module resolution.

    Only explicit imports and unique module-level definitions produce RESOLVED.
    This function never imports or executes project modules.  When ``contents``
    is supplied (P0.7.4 subprocess isolation) the source text is taken from that
    mapping instead of being re-read from disk, so the isolated worker never
    performs an unaudited file open of its own.

    P0.7.10-V: when ``contents`` is *not* supplied the function no longer
    re-opens the path.  It consumes the text the caller already captured on the
    row (``_captured_text``, set by ``scanner.analyze_file`` for by-path
    callers).  If that is absent the file is reported UNKNOWN and skipped; a
    project file is never silently read from disk to fill the gap.
    """
    from pathlib import Path

    project = Path(root).resolve()
    parsed: dict[str, ast.Module] = {}
    module_paths: dict[str, list[str]] = defaultdict(list)
    top_symbols: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    module_for_file: dict[str, str] = {}
    unknown_files: list[str] = []
    for row in rows:
        if row.get("language") != "Python" or not row.get("readable_text"):
            continue
        rel = row["path"]
        try:
            if contents is not None:
                content = contents[rel]
            else:
                # P0.7.10-V: consume the caller-captured text; never re-open the
                # path here.  A missing snapshot raises KeyError, which the
                # handler below turns into an explicit UNKNOWN diagnostic.
                content = row.get("_captured_text")
                if content is None:
                    raise KeyError(rel)
            tree = ast.parse(content, filename=rel)
        except (OSError, SyntaxError, ValueError, KeyError):
            row["resolution_diagnostics"] = ["Python AST unavailable; calls remain UNKNOWN"]
            row["calls"] = []
            unknown_files.append(rel)
            continue
        parsed[rel] = tree
        module = _module_for_path(rel)
        module_for_file[rel] = module
        source_row = next(item for item in rows if item.get("path") == rel)
        source_row["module_symbols"] = []
        if module:
            module_paths[module].append(rel)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parameter_nodes = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    ordered = (
                        [("positional_only", item) for item in node.args.posonlyargs]
                        + [("positional_or_keyword", item) for item in node.args.args]
                        + [("keyword_only", item) for item in node.args.kwonlyargs]
                    )
                    parameter_nodes = [
                        {"name": item.arg, "index": index, "kind": kind,
                         "line": item.lineno, "column": item.col_offset + 1}
                        for index, (kind, item) in enumerate(ordered)
                    ]
                entry = {"file": rel, "name": node.name, "line": node.lineno,
                         "type": "class" if isinstance(node, ast.ClassDef) else "function",
                         "decorated": bool(getattr(node, "decorator_list", [])),
                         "id": _definition_id(rel, node.name, node.lineno),
                         "qualified_name": f"{module}.{node.name}" if module else node.name,
                         "parameters": parameter_nodes}
                top_symbols[(module, node.name)].append(entry)
                source_row["module_symbols"].append({"name": node.name, "line": node.lineno,
                                                     "type": entry["type"], "qualified_name": entry["qualified_name"]})

    result_counts = {status: 0 for status in STATUSES}
    for rel, tree in parsed.items():
        module = module_for_file[rel]
        package = _package_for_module(module, rel)
        bindings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        import_nodes: list[tuple[ast.AST, bool]] = []
        module_shadows: set[str] = set()
        has_star_import = False

        class ModuleBindings(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                return

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                return

            def visit_Lambda(self, node: ast.Lambda) -> None:
                return

            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    module_shadows.add(node.id)

        module_binding_collector = ModuleBindings()
        for statement in tree.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_binding_collector.visit(statement)

        class ImportCollector(ast.NodeVisitor):
            def __init__(self):
                self.conditional_depth = 0

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                return

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                return

            def visit_Lambda(self, node: ast.Lambda) -> None:
                return

            def visit_Import(self, node: ast.Import) -> None:
                import_nodes.append((node, self.conditional_depth > 0))

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                import_nodes.append((node, self.conditional_depth > 0))

            def _visit_conditional(self, node: ast.AST) -> None:
                self.conditional_depth += 1
                self.generic_visit(node)
                self.conditional_depth -= 1

            visit_If = _visit_conditional
            visit_For = _visit_conditional
            visit_AsyncFor = _visit_conditional
            visit_While = _visit_conditional
            visit_Try = _visit_conditional
            visit_TryStar = _visit_conditional
            visit_Match = _visit_conditional

        collector = ImportCollector()
        for statement in tree.body:
            collector.visit(statement)
        for import_node, is_conditional in sorted(import_nodes, key=lambda pair: (pair[0].lineno, pair[0].col_offset)):
            if isinstance(import_node, ast.Import):
                for alias in import_node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    bound_module = alias.name if alias.asname else alias.name.split(".")[0]
                    bindings[local].append({"kind": "module", "module": bound_module,
                                            "line": import_node.lineno, "external": not module_paths.get(bound_module),
                                            "conditional": is_conditional})
            elif isinstance(import_node, ast.ImportFrom):
                base = _relative_module(package, import_node.level, import_node.module)
                for alias in import_node.names:
                    if alias.name == "*" or base is None:
                        if alias.name == "*":
                            has_star_import = True
                        local = alias.asname or alias.name
                        bindings[local].append({"kind": "unknown", "module": base, "line": import_node.lineno,
                                                "conditional": is_conditional})
                        continue
                    local = alias.asname or alias.name
                    target_module = f"{base}.{alias.name}" if base else alias.name
                    symbol_candidates = list(top_symbols.get((base, alias.name), [])) if module_paths.get(base) else []
                    submodule_paths = module_paths.get(target_module, [])
                    base_paths = module_paths.get(base, [])
                    if len(symbol_candidates) == 1 and not submodule_paths and len(base_paths) == 1:
                        binding = {"kind": "symbol", "module": base, "name": alias.name,
                                   "target": symbol_candidates[0], "line": import_node.lineno}
                    elif symbol_candidates and submodule_paths:
                        binding = {"kind": "ambiguous", "module": base, "name": alias.name,
                                   "targets": symbol_candidates, "line": import_node.lineno}
                    elif len(symbol_candidates) > 1 or len(base_paths) > 1 or len(submodule_paths) > 1:
                        binding = {"kind": "ambiguous", "module": base, "name": alias.name,
                                   "targets": symbol_candidates, "line": import_node.lineno}
                    elif submodule_paths and len(base_paths) == 1:
                        binding = {"kind": "module", "module": target_module, "line": import_node.lineno}
                    elif base_paths:
                        binding = {"kind": "unresolved", "module": base, "name": alias.name,
                                   "line": import_node.lineno}
                    else:
                        binding = {"kind": "external", "module": base, "name": alias.name,
                                   "line": import_node.lineno}
                    binding["conditional"] = is_conditional
                    bindings[local].append(binding)

        visitor = _CallVisitor()
        visitor.visit(tree)
        calls: list[dict[str, Any]] = []
        for call, shadowed, caller in sorted(visitor.calls, key=lambda item: (item[0].lineno, item[0].col_offset)):
            func = call.func
            line = call.lineno
            col = call.col_offset
            target: dict[str, Any] | None = None
            import_evidence: dict[str, Any] | None = None
            candidates: list[dict[str, Any]] = []
            state = "UNKNOWN"
            if isinstance(func, ast.Name):
                name = func.id
                candidates = top_symbols.get((module, name), [])
                imported = bindings.get(name, [])
                if name in shadowed or name in module_shadows:
                    state = "DYNAMIC"
                elif len(imported) > 1:
                    state = "AMBIGUOUS"
                    candidates = [item["target"] for item in imported if item.get("kind") == "symbol" and item.get("target")]
                    candidates.extend(target for item in imported for target in item.get("targets", []))
                elif imported:
                    binding = imported[0]
                    import_evidence = {"path": rel, "line": binding["line"]}
                    if binding.get("conditional"):
                        state = "UNKNOWN"
                        if binding.get("target"):
                            candidates = [binding["target"]]
                        candidates.extend(binding.get("targets", []))
                    elif binding["kind"] == "symbol":
                        target = binding["target"]
                        state = "UNKNOWN" if target.get("decorated") else "RESOLVED"
                    elif binding["kind"] == "external":
                        state = "EXTERNAL"
                    elif binding["kind"] == "ambiguous":
                        state = "AMBIGUOUS"
                        candidates = binding.get("targets", [])
                    elif binding["kind"] == "unresolved":
                        state = "UNRESOLVED"
                    else:
                        state = "UNKNOWN"
                elif len(candidates) == 1:
                    target = candidates[0]
                    state = "UNKNOWN" if target.get("decorated") else "RESOLVED"
                elif len(candidates) > 1:
                    state = "AMBIGUOUS"
                elif name in vars(builtins):
                    state = "EXTERNAL"
                elif has_star_import:
                    state = "UNKNOWN"
                else:
                    state = "UNRESOLVED"
            elif isinstance(func, ast.Attribute):
                parts = _attribute_parts(func)
                if parts and (parts[0] in shadowed or parts[0] in module_shadows):
                    state = "DYNAMIC"
                elif parts and bindings.get(parts[0]):
                    imported = bindings[parts[0]]
                    if len(imported) > 1:
                        state = "AMBIGUOUS"
                    else:
                        binding = imported[0]
                        import_evidence = {"path": rel, "line": binding["line"]}
                        suffix = parts[1:]
                        if binding.get("conditional"):
                            state = "UNKNOWN"
                        elif binding["kind"] == "module" and suffix:
                            member = suffix[-1]
                            mod = binding["module"]
                            candidates = top_symbols.get((mod, member), [])
                            if binding.get("external"):
                                state = "EXTERNAL"
                            elif len(module_paths.get(mod, [])) > 1 or len(candidates) > 1:
                                state = "AMBIGUOUS"
                            elif len(candidates) == 1:
                                target = candidates[0]
                                state = "UNKNOWN" if target.get("decorated") else "RESOLVED"
                            else:
                                state = "UNRESOLVED"
                        elif binding["kind"] == "external":
                            state = "EXTERNAL"
                        elif binding["kind"] == "ambiguous":
                            state = "AMBIGUOUS"
                        else:
                            state = "UNKNOWN"
                else:
                    state = "UNKNOWN"
            elif isinstance(func, (ast.Call, ast.Subscript, ast.Lambda)):
                state = "DYNAMIC"
            else:
                state = "UNKNOWN"

            if state not in STATUSES:
                state = "UNKNOWN"
            result_counts[state] += 1
            expression = ast.unparse(func) if hasattr(ast, "unparse") else type(func).__name__
            record: dict[str, Any] = {
                "callee": expression,
                "line": line,
                "column": col,
                "method": "python-ast-project",
                "confidence": "high" if state == "RESOLVED" else "low",
                "resolved": state == "RESOLVED",
                "resolution_status": state,
            }
            if target:
                record["target"] = target
                if state == "RESOLVED" and target.get("type") == "function":
                    parameter_rows = target.get("parameters", [])
                    positional_rows = [item for item in parameter_rows
                                       if item.get("kind") in {"positional_only", "positional_or_keyword"}]
                    parameter_by_name = {item["name"]: item for item in parameter_rows}
                    argument_rows = []
                    unsupported = any(isinstance(item, ast.Starred) for item in call.args)
                    unsupported = unsupported or any(item.arg is None for item in call.keywords)
                    if len(call.args) > len(positional_rows):
                        unsupported = True
                    used_parameters: set[str] = set()
                    for index, argument in enumerate(call.args):
                        if index >= len(positional_rows):
                            continue
                        parameter = positional_rows[index]
                        used_parameters.add(parameter["name"])
                        argument_rows.append({
                            "argument_index": index,
                            "argument_kind": "POSITIONAL",
                            "argument_expression": ast.unparse(argument) if hasattr(ast, "unparse") else type(argument).__name__,
                            "argument_location": {"path": rel, "line": argument.lineno, "column": argument.col_offset + 1},
                            "parameter_name": parameter["name"],
                            "parameter_index": parameter["index"],
                            "parameter_kind": parameter["kind"],
                            "parameter_declaration": {"path": target["file"], "line": parameter["line"], "column": parameter["column"]},
                        })
                    for keyword in call.keywords:
                        if keyword.arg is None:
                            continue
                        parameter = parameter_by_name.get(keyword.arg)
                        if parameter is None or parameter["kind"] == "positional_only" or keyword.arg in used_parameters:
                            unsupported = True
                            continue
                        used_parameters.add(keyword.arg)
                        argument_rows.append({
                            "argument_index": None,
                            "argument_kind": "KEYWORD",
                            "argument_expression": ast.unparse(keyword.value) if hasattr(ast, "unparse") else type(keyword.value).__name__,
                            "argument_location": {"path": rel, "line": keyword.value.lineno, "column": keyword.value.col_offset + 1},
                            "parameter_name": parameter["name"],
                            "parameter_index": parameter["index"],
                            "parameter_kind": parameter["kind"],
                            "parameter_declaration": {"path": target["file"], "line": parameter["line"], "column": parameter["column"]},
                        })
                    record["argument_binding_status"] = "UNSUPPORTED_ARGUMENT_SHAPE" if unsupported else "EXACT_STATIC_MAPPING"
                    record["argument_bindings"] = [] if unsupported else argument_rows
            if import_evidence:
                record["import_evidence"] = import_evidence
            if candidates:
                record["candidate_targets"] = [item["id"] for item in candidates]
            if caller:
                record["caller"] = {"name": caller[0], "line": caller[1]}
                record["caller_symbol_id"] = _definition_id(rel, caller[0], caller[1])
            calls.append(record)
        rows_by_path = next(row for row in rows if row.get("path") == rel)
        rows_by_path["calls"] = calls
        rows_by_path["module"] = module or None
        rows_by_path["route_handlers"] = _detect_route_handlers(
            rel, visitor.calls, bindings=bindings, top_symbols=top_symbols,
            module=module, module_paths=module_paths, has_star_import=has_star_import,
            module_shadows=module_shadows)
    return {"counts": result_counts, "files_with_parse_errors": unknown_files,
            "modules_indexed": sum(len(paths) for paths in module_paths.values()),
            "symbols_indexed": sum(len(items) for items in top_symbols.values())}
