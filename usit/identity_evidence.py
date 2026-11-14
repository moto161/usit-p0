"""Conservative identity-to-resource data-flow evidence.

Only same-line, syntactically explicit patterns are emitted, except that
a database call spanning several lines is joined on balanced parens (at
most 10 physical lines) for the sink check; the record lands on the
call's first line. Local-variable propagation is straight-line within one
scope, plus one single hop through a top-level helper whose every return
is the same identity expression (``INTERPROCEDURAL_SINGLE_HOP_RETURN``);
the helper may live in the same file, be brought in by a ``from`` import,
or be reached through a module binding (``import X`` / ``from P import M``)
as an attribute call. This is not a general taint engine and
never proves runtime identity or authorization.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .python_resolver import _module_for_path, _package_for_module, _relative_module

_TRUSTED = re.compile(
    r"\b(?:req|request)\.(?:user|auth\.user)\.(?:id|userId)\b"
    r"|\b(?:session\.user|ctx\.state\.user|currentUser|auth)\.(?:id|userId)\b"
)
_REQUEST = re.compile(
    r"\b(?:req|request)\.(?:body|params|query)\."
    r"(?:user_?id|owner_?id|account_?id|created_?by)\b"
)
_OWNER_FIELD = re.compile(r"\b(owner_?id|user_?id|created_?by|account_?id)\b", re.I)
_DB_CALL = re.compile(r"\.(?:execute|query|raw)\s*\(")
_OWNER_PREDICATE = re.compile(
    r"\b(?:owner_?id|user_?id|created_?by|account_?id)\s*=\s*(?:\?|\$\d+|:[A-Za-z_]\w*|%s|%\([A-Za-z_]\w*\)s)",
    re.I,
)
_COMPARE = re.compile(
    r"\b(owner_?id|user_?id|created_?by|account_?id)\s*(?:===|==|!==|!=)\s*"
    r"(req\.(?:user|auth\.user)\.(?:id|userId)|request\.(?:user|auth\.user)\.(?:id|userId)|"
    r"session\.user\.(?:id|userId)|ctx\.state\.user\.(?:id|userId)|currentUser\.(?:id|userId))\b",
    re.I,
)


def _id(record: Mapping[str, Any]) -> str:
    raw = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "identity-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]



def _call_parameter_code(line: str, call_start: int) -> str:
    """Return code-only text from positional arguments after the SQL string argument.

    String literals and comments are excluded. Nested arrays/objects/calls are
    retained. Unsupported or unbalanced calls return an empty string.
    """
    stack = ["("]
    args: list[str] = []
    start = call_start + 1
    quote: str | None = None
    escaped = False
    index = start
    while index < len(line):
        char = line[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'} or char == chr(96):
            quote = char
            index += 1
            continue
        if line.startswith("//", index) or char == "#":
            break
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack:
                return ""
            expected = {")": "(", "]": "[", "}": "{"}[char]
            if stack[-1] != expected:
                return ""
            stack.pop()
            if not stack:
                args.append(line[start:index])
                break
        elif char == "," and len(stack) == 1 and stack[0] == "(":
            args.append(line[start:index])
            start = index + 1
        index += 1
    if stack or len(args) < 2:
        return ""

    code_parts: list[str] = []
    for argument in args[1:]:
        quote = None
        escaped = False
        index = 0
        while index < len(argument):
            char = argument[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                code_parts.append(" ")
                index += 1
                continue
            if char in {"'", '"'} or char == chr(96):
                quote = char
                code_parts.append(" ")
                index += 1
                continue
            if argument.startswith("//", index) or char == "#":
                break
            code_parts.append(char)
            index += 1
    return "".join(code_parts)

def _call_parameter_expressions(line: str, call_start: int) -> list[str]:
    """Return cleaned positional binding arguments after the SQL string argument."""
    stack = ["("]
    args: list[str] = []
    start = call_start + 1
    quote: str | None = None
    escaped = False
    index = start
    while index < len(line):
        char = line[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'} or char == chr(96):
            quote = char
            index += 1
            continue
        if line.startswith("//", index) or char == "#":
            break
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack:
                return []
            expected = {")": "(", "]": "[", "}": "{"}[char]
            if stack[-1] != expected:
                return []
            stack.pop()
            if not stack:
                args.append(line[start:index])
                break
        elif char == "," and len(stack) == 1 and stack[0] == "(":
            args.append(line[start:index])
            start = index + 1
        index += 1
    if stack or len(args) < 2:
        return []

    cleaned: list[str] = []
    for argument in args[1:]:
        output: list[str] = []
        quote = None
        escaped = False
        index = 0
        while index < len(argument):
            char = argument[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                output.append(" ")
                index += 1
                continue
            if char in {"'", '"'} or char == chr(96):
                quote = char
                output.append(" ")
                index += 1
                continue
            if argument.startswith("//", index) or char == "#":
                break
            output.append(char)
            index += 1
        value = "".join(output).strip()
        if value:
            cleaned.append(value)
    return cleaned


def _join_balanced_call(lines: list[str], start_index: int, call_start: int) -> tuple[str, int] | None:
    """Join physical lines until the call's parens balance, or None.

    Scans from just after the call's opening paren, tracking the bracket
    stack and string quotes (``'``/``"``/backtick, ``\\`` escapes) exactly
    like _call_parameter_code; a paren inside a string literal does not
    count. At most 10 physical lines; unbalanced within the cap returns
    None (fail-closed, the caller falls back to the first line alone).
    """
    stack = ["("]
    quote: str | None = None
    escaped = False
    parts = [lines[start_index]]
    current = lines[start_index]
    pos = call_start + 1
    end_index = start_index
    while True:
        while pos < len(current):
            char = current[pos]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in {"'", '"'} or char == chr(96):
                quote = char
            elif char in "([{":
                stack.append(char)
            elif char in ")]}":
                if not stack:
                    return None
                expected = {")": "(", "]": "[", "}": "{"}[char]
                if stack[-1] != expected:
                    return None
                stack.pop()
                if not stack:
                    return ("\n".join(parts), end_index)
            pos += 1
        if end_index - start_index >= 9:
            return None
        end_index += 1
        if end_index >= len(lines):
            return None
        parts.append(lines[end_index])
        current = lines[end_index]
        pos = 0


def _python_function_owners(path: str, source: str) -> dict[int, str]:
    """Map source lines to the innermost AST-proven Python function symbol.

    Non-Python files and syntax-invalid Python deliberately produce no owner.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    scopes: list[tuple[int, int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", None)
            if isinstance(end_line, int) and end_line >= node.lineno:
                scopes.append((node.lineno, end_line, end_line - node.lineno,
                               f"symbol:{path}#{node.name}:{node.lineno}"))
    owners: dict[int, str] = {}
    for start, end, _span, symbol_id in sorted(scopes, key=lambda item: (item[2], item[0], item[3])):
        for line_no in range(start, end + 1):
            owners.setdefault(line_no, symbol_id)
    return owners


def _python_identity_return_helpers(path: str, source: str) -> dict[str, dict[str, Any]]:
    """Map top-level Python helper names to their single identity return.

    A helper qualifies only when every ``return`` in its body (not counting
    returns inside nested functions or classes) carries a value whose
    ``ast.unparse`` text fully matches one identity pattern, and all those
    values are the same kind and the same expression text. A helper with no
    return, a bare ``return``, a non-identity return, mixed kinds, or
    differing expressions does not qualify. A name defined twice with
    different return profiles is ambiguous and dropped. Non-Python files
    and syntax-invalid Python deliberately produce no helpers.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    candidates: dict[str, list[dict[str, Any]]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returns: list[ast.Return] = []
        stack = list(node.body)
        while stack:
            item = stack.pop()
            if isinstance(item, ast.Return):
                returns.append(item)
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            else:
                stack.extend(ast.iter_child_nodes(item))
        if not returns:
            continue
        profile: dict[str, Any] | None = None
        for ret in returns:
            if ret.value is None:
                profile = None
                break
            text = ast.unparse(ret.value)
            if _TRUSTED.fullmatch(text):
                kind = "trusted"
            elif _REQUEST.fullmatch(text):
                kind = "request"
            else:
                profile = None
                break
            if profile is None:
                profile = {"kind": kind, "expression": text,
                           "return_lines": [ret.lineno], "function_line": node.lineno}
            elif profile["kind"] != kind or profile["expression"] != text:
                profile = None
                break
            else:
                profile["return_lines"].append(ret.lineno)
        if profile is not None:
            candidates.setdefault(node.name, []).append(profile)
    helpers: dict[str, dict[str, Any]] = {}
    for name, profiles in candidates.items():
        first = profiles[0]
        if all(p["kind"] == first["kind"] and p["expression"] == first["expression"]
               for p in profiles):
            helpers[name] = {**first, "return_lines": sorted(
                {line for p in profiles for line in p["return_lines"]})}
    return helpers


def _python_module_index(code_by_file: dict[str, str]) -> dict[str, str]:
    """Map dotted module names to project files, dropping ambiguous ones.

    Module names follow ``python_resolver._module_for_path`` (``a/b.py`` ->
    ``a.b``, ``a/b/__init__.py`` -> ``a.b``). A module name claimed by
    more than one file is dropped entirely.
    """
    candidates: dict[str, set[str]] = {}
    for path in sorted(code_by_file):
        if not path.lower().endswith(".py"):
            continue
        candidates.setdefault(_module_for_path(path), set()).add(path)
    return {module: next(iter(paths)) for module, paths in candidates.items()
            if len(paths) == 1}


def _python_top_level_function_names(source: str) -> set[str]:
    """Top-level function names defined in one Python source (AST, fail-closed)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    return {node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _python_imported_identity_helpers(
    path: str, source: str, module_index: dict[str, str],
    helper_maps: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Resolve ``from``-imported identity-return helpers for one file.

    Only top-level ``from <module> import <name> [as <alias>]`` whose
    target file directly defines a qualifying helper under the original
    name. Star imports, submodule imports (``from . import x``), modules
    outside the scan, missing or unqualified target names, and names
    shadowed by a local top-level function are all skipped. Re-exports
    (the target file itself importing the name) are not followed.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    caller_package = _package_for_module(_module_for_path(path), path)
    local_names = _python_top_level_function_names(source)
    imported: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.level and node.level > 0:
            absolute = _relative_module(caller_package, node.level, node.module)
        else:
            absolute = node.module
        if absolute is None:
            continue
        target_path = module_index.get(absolute)
        if target_path is None:
            continue
        target_helpers = helper_maps.get(target_path, {})
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            if local_name in local_names or local_name in imported:
                continue
            helper = target_helpers.get(alias.name)
            if helper is None:
                continue
            imported[local_name] = {
                **helper,
                "path": target_path,
                "function": f"{absolute}.{alias.name}",
            }
    return imported


def _python_module_level_shadowed_names(tree: ast.Module) -> set[str]:
    """Module-level names that shadow an import: top-level defs/classes
    and simple module-level assignment targets."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _python_module_bindings(path: str, source: str,
                            module_index: dict[str, str]) -> dict[str, str]:
    """Map local names to dotted modules for one file's top-level imports.

    ``import a.b.c`` binds the top-level name ``a`` to module ``a``;
    ``import a.b.c as x`` binds ``x`` to ``a.b.c``; ``from P import M``
    binds ``M`` (or its alias) to ``P.M`` only when ``P.M`` is a scanned
    module (otherwise ``M`` is a function import, which is P5 territory).
    Relative imports resolve against the caller's package; ``from . import
    M`` binds ``M`` to the current package plus ``.M``. Star imports bind
    nothing. A local name shadowed by a module-level definition or
    assignment is dropped.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    caller_package = _package_for_module(_module_for_path(path), path)
    shadowed = _python_module_level_shadowed_names(tree)
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    local, module = alias.asname, alias.name
                else:
                    local = module = alias.name.split(".")[0]
                if local not in shadowed:
                    bindings.setdefault(local, module)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                base = _relative_module(caller_package, node.level, None)
            elif node.level and node.level > 0:
                base = _relative_module(caller_package, node.level, node.module)
            else:
                base = node.module
            if base is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}" if base else alias.name
                if candidate not in module_index:
                    continue
                local = alias.asname or alias.name
                if local not in shadowed:
                    bindings.setdefault(local, candidate)
    return bindings


def _resolve_module_attribute_helper(
    chain: str, bindings: dict[str, str], module_index: dict[str, str],
    helper_maps: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    """Resolve ``a.b.c(...)`` to an identity-return helper, or None.

    ``a`` must be a module binding; the candidate module is the bound
    module plus the middle chain segments, and the last segment is the
    helper name. The candidate module must be in the scan and the target
    file must directly define a qualifying helper under that name.
    """
    parts = chain.split(".")
    if len(parts) < 2:
        return None
    base = bindings.get(parts[0])
    if base is None:
        return None
    module = base + ("." + ".".join(parts[1:-1]) if len(parts) > 2 else "")
    target_path = module_index.get(module)
    if target_path is None:
        return None
    helper = helper_maps.get(target_path, {}).get(parts[-1])
    if helper is None:
        return None
    return {**helper, "path": target_path, "function": f"{module}.{parts[-1]}"}


def extract_identity_evidence(code_by_file: dict[str, str],
                              function_owners_by_file: dict[str, dict[int, str]] | None = None) -> list[dict[str, Any]]:
    """Extract identity evidence plus bounded, same-scope local-variable propagation.

    Propagation is intentionally limited to straight-line assignments within one
    brace/indent scope. Entering or leaving a block resets aliases; function and
    class declarations reset them too. This is not interprocedural taint analysis.

    ``function_owners_by_file`` (P3) optionally supplies, per non-Python file, a
    line-to-symbol map built from the TS parser's function line ranges
    (``js_ts_parser.ts_function_owners``). Python files always keep their
    AST-proven owners; without the map, non-Python records stay owner-less
    (``UNKNOWN``), exactly as before.
    """
    records: list[dict[str, Any]] = []
    assignment = re.compile(
        r"^\s*(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*=\s*(.*?)\s*;?\s*$"
    )
    identifier = re.compile(r"\b[A-Za-z_$][\w$]*\b")
    bare_call = re.compile(r"([A-Za-z_$][\w$]*)\s*\(.*\)$")
    attr_call = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\s*\(.*\)$")
    module_index = _python_module_index(code_by_file)
    same_file_helpers = {
        file_path: _python_identity_return_helpers(file_path, file_source)
        for file_path, file_source in code_by_file.items()
        if file_path.lower().endswith(".py")
    }
    module_bindings = {
        file_path: _python_module_bindings(file_path, file_source, module_index)
        for file_path, file_source in code_by_file.items()
        if file_path.lower().endswith(".py")
    }
    for path, source in sorted(code_by_file.items()):
        if path.lower().endswith(".py"):
            function_owners = _python_function_owners(path, source)
            imported_helpers = _python_imported_identity_helpers(
                path, source, module_index, same_file_helpers)
            return_helpers = dict(imported_helpers)
            for name, helper in same_file_helpers.get(path, {}).items():
                return_helpers[name] = {**helper, "path": path, "function": name}
            bindings = module_bindings.get(path, {})
        elif function_owners_by_file is not None:
            supplied = function_owners_by_file.get(path)
            function_owners = supplied if isinstance(supplied, dict) else {}
            return_helpers = {}
            bindings = {}
        else:
            function_owners = {}
            return_helpers = {}
            bindings = {}
        aliases: dict[str, dict[str, Any]] = {}
        brace_depth = 0
        previous_depth = 0
        previous_indent = 0
        python_scope_indent: int | None = None
        is_python = path.lower().endswith(".py")
        all_lines = source.splitlines()
        for line_no, line in enumerate(all_lines, 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if is_python:
                if re.match(r"(?:async\s+)?(?:def|class)\s+", stripped):
                    aliases.clear()
                    python_scope_indent = indent
                elif python_scope_indent is not None and stripped and indent <= python_scope_indent:
                    aliases.clear()
                    python_scope_indent = None
                elif stripped and indent < previous_indent:
                    aliases.clear()
                current_scope = python_scope_indent if python_scope_indent is not None else 0
            else:
                if brace_depth != previous_depth:
                    aliases.clear()
                if re.search(r"\b(?:function|class)\b|=>\s*\{", line):
                    aliases.clear()
                current_scope = brace_depth

            trusted = sorted(set(match.group(0) for match in _TRUSTED.finditer(line)))
            request_values = sorted(set(match.group(0) for match in _REQUEST.finditer(line)))
            owner_fields = sorted(set(match.group(1).lower() for match in _OWNER_FIELD.finditer(line)))
            comparisons = []
            for match in _COMPARE.finditer(line):
                comparisons.append({
                    "resource_field": match.group(1).lower(),
                    "identity_expression": match.group(2),
                    "operator": re.search(r"(?:===|==|!==|!=)", match.group(0)).group(0),
                })
            assignment_match = assignment.match(line)
            assigned_variable = None
            if assignment_match:
                candidate_name, candidate_rhs = assignment_match.groups()
                candidate_rhs = candidate_rhs.strip().rstrip(";")
                if _TRUSTED.fullmatch(candidate_rhs) or _REQUEST.fullmatch(candidate_rhs):
                    assigned_variable = candidate_name

            kinds: list[tuple[str, dict[str, Any]]] = []
            if trusted:
                kinds.append(("authenticated_identity_source", {"identity_expressions": trusted}))
            if request_values:
                kinds.append(("request_controlled_identity_input", {"request_expressions": request_values}))
            if comparisons:
                kinds.append(("resource_owner_comparison", {"comparisons": comparisons}))

            # P2: a DB call may span lines (SQL on one line, parameters on
            # the next). Join the call on balanced parens (at most 10
            # physical lines) and run the sink checks on the joined text;
            # the record still lands on the call's first line.
            call_match = _DB_CALL.search(line)
            effective_line = line
            call_end_line = line_no
            multi_line_call = False
            if call_match:
                joined = _join_balanced_call(all_lines, line_no - 1, call_match.end() - 1)
                if joined is not None:
                    effective_line, end_index = joined
                    call_end_line = end_index + 1
                    multi_line_call = call_end_line > line_no
            sink = bool(call_match and _OWNER_PREDICATE.search(effective_line))
            if sink:
                sink_span: dict[str, Any] = {"multi_line_call": multi_line_call}
                if multi_line_call:
                    sink_span["call_lines"] = [line_no, call_end_line]
                parameter_code = _call_parameter_code(effective_line, call_match.end() - 1)
                parameter_expressions = _call_parameter_expressions(effective_line, call_match.end() - 1)
                parameter_trusted = sorted(set(
                    match.group(0) for match in _TRUSTED.finditer(parameter_code)
                ))
                parameter_request = sorted(set(
                    match.group(0) for match in _REQUEST.finditer(parameter_code)
                ))
                # Record only binding arguments that are exactly a bare identifier
                # (optionally wrapped as a one-item list/tuple). Compound expressions
                # are deliberately excluded from interprocedural matching.
                binding_parameters = sorted({
                    candidate
                    for expression in parameter_expressions
                    for candidate in [expression.strip().strip("[](), \t\r\n")]
                    if re.fullmatch(r"[A-Za-z_$][\w$]*", candidate)
                })
                if binding_parameters:
                    kinds.append(("sql_owner_parameter_binding", {
                        "resource_fields": owner_fields,
                        "binding_parameters": binding_parameters,
                        "binding_status": "EXACT_BARE_IDENTIFIER_ARGUMENT",
                        **sink_span,
                    }))
                if parameter_trusted:
                    kinds.append(("direct_authenticated_identity_to_sql_owner_binding", {
                        "identity_expressions": parameter_trusted, "resource_fields": owner_fields,
                        "binding_status": (
                            "MULTI_LINE_CALL_PARAMETER_EXPRESSION" if multi_line_call
                            else "SAME_LINE_PARAMETER_EXPRESSION"
                        ),
                        **sink_span,
                    }))
                if parameter_request:
                    kinds.append(("direct_request_identity_to_sql_owner_binding", {
                        "request_expressions": parameter_request, "resource_fields": owner_fields,
                        "binding_status": (
                            "MULTI_LINE_CALL_PARAMETER_EXPRESSION" if multi_line_call
                            else "SAME_LINE_PARAMETER_EXPRESSION"
                        ),
                        **sink_span,
                    }))
                # Resolve aliases only in non-literal positional arguments after
                # the SQL argument; names in SQL text or trailing expressions do not count.
                used_names = set(identifier.findall(parameter_code))
                for name in sorted(used_names):
                    origin = aliases.get(name)
                    if not origin:
                        continue
                    binding_status = ("INTERPROCEDURAL_SINGLE_HOP_RETURN"
                                      if origin.get("via_return")
                                      else "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
                    if origin["kind"] == "trusted":
                        kinds.append(("direct_authenticated_identity_to_sql_owner_binding", {
                            "identity_expressions": [origin["expression"]],
                            "resource_fields": owner_fields,
                            "binding_status": binding_status,
                            "propagation": origin["propagation"] + [
                                {"path": path, "line": line_no, "sink": "SQL_OWNER_PREDICATE"}
                            ],
                            **sink_span,
                        }))
                    elif origin["kind"] == "request":
                        kinds.append(("direct_request_identity_to_sql_owner_binding", {
                            "request_expressions": [origin["expression"]],
                            "resource_fields": owner_fields,
                            "binding_status": binding_status,
                            "propagation": origin["propagation"] + [
                                {"path": path, "line": line_no, "sink": "SQL_OWNER_PREDICATE"}
                            ],
                            **sink_span,
                        }))

            for kind, details in kinds:
                if assigned_variable and kind in {
                    "authenticated_identity_source", "request_controlled_identity_input"
                }:
                    details["source_variable"] = assigned_variable
                if details.get("binding_status") == "INTRAPROCEDURAL_LINEAR_ASSIGNMENT":
                    scope = "INTRAPROCEDURAL_LINEAR_ASSIGNMENT"
                elif details.get("binding_status") == "INTERPROCEDURAL_SINGLE_HOP_RETURN":
                    scope = "INTERPROCEDURAL_SINGLE_HOP_RETURN"
                elif details.get("multi_line_call"):
                    scope = "MULTI_LINE_CALL"
                else:
                    scope = "SAME_LINE_PATTERN"
                row = {
                    "evidence_id": "", "kind": kind, "source": path, "line": line_no,
                    "details": details, "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED", "scope": scope,
                    "owner_symbol_id": function_owners.get(line_no),
                    "owner_resolution_status": "RESOLVED" if line_no in function_owners else "UNKNOWN",
                }
                row["evidence_id"] = _id(row)
                records.append(row)

            # Track only simple direct assignments. Reassignment to an unknown
            # expression invalidates the alias instead of retaining stale identity.
            match = assignment.match(line)
            if match and not sink:
                name, rhs = match.groups()
                rhs = rhs.strip().rstrip(";")
                if _TRUSTED.fullmatch(rhs):
                    aliases[name] = {"kind": "trusted", "expression": rhs, "line": line_no,
                                     "propagation": [{"path": path, "line": line_no, "variable": name}]}
                elif _REQUEST.fullmatch(rhs):
                    aliases[name] = {"kind": "request", "expression": rhs, "line": line_no,
                                     "propagation": [{"path": path, "line": line_no, "variable": name}]}
                elif re.fullmatch(r"[A-Za-z_$][\w$]*", rhs) and rhs in aliases:
                    aliases[name] = {**aliases[rhs],
                                     "propagation": aliases[rhs]["propagation"] + [
                                         {"path": path, "line": line_no, "variable": name}
                                     ]}
                elif return_helpers or bindings:
                    # P4/P5: one hop through a same-file or from-imported
                    # helper, e.g. uid = get_uid(req). Only a bare call
                    # counts: no method call, no await, no attribute access
                    # after the call.
                    call_match = bare_call.fullmatch(rhs)
                    helper = return_helpers.get(call_match.group(1)) if call_match else None
                    if helper is None:
                        # P6: one hop through a module binding, e.g.
                        # uid = server.utils.get_uid(req).
                        attr_match = attr_call.fullmatch(rhs)
                        if attr_match:
                            helper = _resolve_module_attribute_helper(
                                attr_match.group(1), bindings, module_index,
                                same_file_helpers)
                    if helper is not None:
                        aliases[name] = {
                            "kind": helper["kind"], "expression": helper["expression"],
                            "line": line_no, "via_return": True,
                            "propagation": [
                                {"path": helper["path"], "line": return_line,
                                 "function": helper["function"],
                                 "returns": helper["expression"]}
                                for return_line in helper["return_lines"]
                            ] + [{"path": path, "line": line_no, "variable": name}],
                        }
                    else:
                        aliases.pop(name, None)
                else:
                    aliases.pop(name, None)

            if not is_python:
                previous_depth = brace_depth
                brace_depth += line.count("{") - line.count("}")
                if brace_depth != previous_depth:
                    aliases.clear()
            previous_indent = indent
    records.sort(key=lambda item: (item["source"], item["line"], item["kind"], item["evidence_id"]))
    return records
