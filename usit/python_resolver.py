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
        self.calls: list[tuple[ast.Call, set[str]]] = []
        self.scopes: list[set[str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scopes.append(_collect_bindings(node))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        names = {arg.arg for arg in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)}
        if node.args.vararg:
            names.add(node.args.vararg.arg)
        if node.args.kwarg:
            names.add(node.args.kwarg.arg)
        self.scopes.append(names)
        self.visit(node.body)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        shadowed = set().union(*self.scopes) if self.scopes else set()
        self.calls.append((node, shadowed))
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
                entry = {"file": rel, "name": node.name, "line": node.lineno,
                         "type": "class" if isinstance(node, ast.ClassDef) else "function",
                         "decorated": bool(getattr(node, "decorator_list", [])),
                         "id": _definition_id(rel, node.name, node.lineno),
                         "qualified_name": f"{module}.{node.name}" if module else node.name}
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
        for call, shadowed in sorted(visitor.calls, key=lambda pair: (pair[0].lineno, pair[0].col_offset)):
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
            if import_evidence:
                record["import_evidence"] = import_evidence
            if candidates:
                record["candidate_targets"] = [item["id"] for item in candidates]
            calls.append(record)
        rows_by_path = next(row for row in rows if row.get("path") == rel)
        rows_by_path["calls"] = calls
        rows_by_path["module"] = module or None
    return {"counts": result_counts, "files_with_parse_errors": unknown_files,
            "modules_indexed": sum(len(paths) for paths in module_paths.values()),
            "symbols_indexed": sum(len(items) for items in top_symbols.values())}
