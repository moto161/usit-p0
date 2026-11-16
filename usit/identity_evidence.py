"""Conservative identity-to-resource data-flow evidence.

Only same-line, syntactically explicit patterns are emitted, except that
a database call spanning several lines is joined on balanced parens (at
most 10 physical lines) for the sink check; the record lands on the
call's first line. Local-variable propagation is straight-line within one
scope, plus one single hop through a top-level helper whose every return
is the same identity expression (``INTERPROCEDURAL_SINGLE_HOP_RETURN``);
the helper may live in the same file, be brought in by a ``from`` import,
be reached through a module binding (``import X`` / ``from P import M``)
as an attribute call, or be a method of the enclosing class reached
through ``self`` / ``cls``, or be a method of a class an instance
attribute was constructed from (``self.helper = AuthHelper()`` then
``self.helper.get_uid(...)``). This is not a general taint engine and
never proves runtime identity or authorization.
"""
from __future__ import annotations

import ast
import hashlib
import json
import posixpath
import re
from collections.abc import Mapping
from typing import Any

from .python_resolver import _module_for_path, _package_for_module, _relative_module
from .vue_parser import vue_script_for_adapter

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


def _identity_return_profile(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any] | None:
    """Single identity-return profile of one function, or None.

    The function qualifies only when every ``return`` in its body (not
    counting returns inside nested functions or classes) carries a value
    whose ``ast.unparse`` text fully matches one identity pattern, and all
    those values are the same kind and the same expression text. No return,
    a bare return, a non-identity return, mixed kinds, or differing
    expressions disqualify it.
    """
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
        return None
    profile: dict[str, Any] | None = None
    for ret in returns:
        if ret.value is None:
            return None
        text = ast.unparse(ret.value)
        if _TRUSTED.fullmatch(text):
            kind = "trusted"
        elif _REQUEST.fullmatch(text):
            kind = "request"
        else:
            return None
        if profile is None:
            profile = {"kind": kind, "expression": text,
                       "return_lines": [ret.lineno], "function_line": node.lineno}
        elif profile["kind"] != kind or profile["expression"] != text:
            return None
        else:
            profile["return_lines"].append(ret.lineno)
    return profile


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
        profile = _identity_return_profile(node)
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


def _python_class_method_returns(path: str, source: str) -> dict[
        tuple[str, str], dict[str, Any]]:
    """Map (class, method) pairs to their direct returns, for Python.

    Each entry carries the method's ``function_line`` and its direct
    ``(line, ast.unparse text)`` returns (returns inside nested
    functions or classes excluded, same traversal as
    ``_identity_return_profile``). A (class, method) defined twice
    merges its returns, matching ``_python_identity_return_methods``.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    methods: dict[tuple[str, str], dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            returns: list[ast.Return] = []
            stack = list(method.body)
            while stack:
                item = stack.pop()
                if isinstance(item, ast.Return):
                    returns.append(item)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                else:
                    stack.extend(ast.iter_child_nodes(item))
            entry = methods.setdefault((node.name, method.name),
                                       {"function_line": method.lineno, "returns": []})
            entry["returns"].extend((ret.lineno, ast.unparse(ret.value))
                                    for ret in returns if ret.value is not None)
    return methods


def _python_direct_identity_profiles(
        path: str, methods: dict[tuple[str, str], dict[str, Any]]
        ) -> dict[tuple[str, str], dict[str, Any]]:
    """Direct identity profiles for one file's (class, method) -> returns map.

    A method qualifies only when every direct return fully matches one
    identity pattern, all the same kind and the same expression text.
    The profile carries the qualified ``Class.method`` name, the
    defining file, the method's definition line and an empty
    ``delegation`` list (direct profiles delegate to nothing).
    """
    helpers: dict[tuple[str, str], dict[str, Any]] = {}
    for key, info in methods.items():
        profile: dict[str, Any] | None = None
        for line_no, text in info["returns"]:
            if _TRUSTED.fullmatch(text):
                kind = "trusted"
            elif _REQUEST.fullmatch(text):
                kind = "request"
            else:
                profile = None
                break
            if profile is None:
                profile = {"kind": kind, "expression": text,
                           "return_lines": [line_no],
                           "function_line": info["function_line"]}
            elif profile["kind"] != kind or profile["expression"] != text:
                profile = None
                break
            else:
                profile["return_lines"].append(line_no)
        if profile is not None:
            # Return lines are sorted and deduplicated, matching the
            # previous _python_identity_return_methods merge (the AST
            # stack walk collects them in reverse source order).
            profile["return_lines"] = sorted(set(profile["return_lines"]))
            helpers[key] = {**profile, "path": path,
                            "function": f"{key[0]}.{key[1]}", "delegation": []}
    return helpers


def _python_identity_return_methods(path: str, source: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (class, method) pairs to their single identity return.

    Only methods directly defined in a top-level class qualify, under the
    same return rule as top-level helpers (see ``_identity_return_profile``).
    A (class, method) defined twice with different return profiles is
    ambiguous and dropped. The profile carries the qualified ``Class.method``
    name and the defining file, for propagation records. Non-Python files
    and syntax-invalid Python deliberately produce no methods. This is the
    direct-only version; methods delegating through ``super()`` need the
    project-wide ``_python_all_identity_return_methods``.
    """
    return _python_direct_identity_profiles(path, _python_class_method_returns(path, source))


def _python_top_level_class_names(source: str) -> set[str]:
    """Top-level class names defined in one Python source (AST, fail-closed)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def _python_base_classes(
        code_by_file: dict[str, str]) -> dict[str, dict[str, tuple[str, str]]]:
    """Map files to {class: (base file, base class)} for Python extends clauses.

    Only single inheritance with a bare-identifier base counts
    (``class X(Base)``): the base resolves to a top-level class of the
    same file first, else through a ``from <module> import Base`` to a
    project file defining it (same import rules as
    ``_python_imported_identity_helpers``; re-exports not followed).
    Multiple inheritance, expression bases (``class X(mixin(Base))``)
    and attribute bases (``class X(auth.Base)``) leave the class
    without a base (fail closed). Only top-level classes are recorded.
    """
    module_index = _python_module_index(code_by_file)
    result: dict[str, dict[str, tuple[str, str]]] = {}
    for path, source in code_by_file.items():
        if not path.lower().endswith(".py"):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, ValueError):
            continue
        class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        caller_package = _package_for_module(_module_for_path(path), path)
        bases: dict[str, tuple[str, str]] = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if len(node.bases) != 1 or not isinstance(node.bases[0], ast.Name):
                continue
            base = node.bases[0].id
            if base in class_names:
                bases[node.name] = (path, base)
                continue
            target_path: str | None = None
            for import_node in tree.body:
                if not isinstance(import_node, ast.ImportFrom) or import_node.module is None:
                    continue
                if import_node.level and import_node.level > 0:
                    absolute = _relative_module(caller_package, import_node.level,
                                                import_node.module)
                else:
                    absolute = import_node.module
                if absolute is None:
                    continue
                candidate = module_index.get(absolute)
                if candidate is None:
                    continue
                if any(alias.name == base and (alias.asname or alias.name) == base
                       for alias in import_node.names):
                    target_path = candidate
                    break
            if target_path is None:
                continue
            if base in _python_top_level_class_names(code_by_file[target_path]):
                bases[node.name] = (target_path, base)
        if bases:
            result[path] = bases
    return result


def _python_method_profile(file: str, cls: str, method: str,
                           profiles: dict[str, dict[tuple[str, str], dict[str, Any]]],
                           defined: dict[str, set[tuple[str, str]]],
                           bases: dict[str, dict[str, tuple[str, str]]],
                           skip_self: bool = False) -> dict[str, Any] | None:
    """Resolve a method to its single identity return profile, walking the extends chain.

    The Python mirror of ``_ts_method_profile``: the class itself first
    (unless ``skip_self``, for ``super()`` calls, which skip the current
    class): defined but without an identity profile means shadowed --
    return None and do not walk up. Not defined: walk up the extends
    chain (at most 8 levels, cycle-safe); each ancestor either defines
    the method (profile or shadowed None) or not (continue). A profile
    carries the defining file's path.
    """
    if not skip_self and (cls, method) in defined.get(file, set()):
        return profiles.get(file, {}).get((cls, method))
    seen: set[tuple[str, str]] = set()
    current_file, current_class = file, cls
    for _ in range(8):
        base = bases.get(current_file, {}).get(current_class)
        if base is None:
            return None
        base_file, base_class = base
        if (base_file, base_class) in seen:
            return None
        seen.add((base_file, base_class))
        if (base_class, method) in defined.get(base_file, set()):
            return profiles.get(base_file, {}).get((base_class, method))
        current_file, current_class = base_file, base_class
    return None


def _python_all_identity_return_methods(
        code_by_file: dict[str, str]) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    """Project-wide Python identity return profiles, including super() delegation.

    Direct profiles first, then up to 8 fixpoint rounds resolving
    methods whose every direct return is an identity expression or a
    ``super().<method>(...)`` call to an already-profiled base method
    (P9 chain walk, ``skip_self=True``; only zero-argument ``super()``
    counts). All returns must agree on kind, expression and delegation
    steps: a direct return mixed with a super-delegated return, or
    super calls to different targets, disqualifies the method. A
    delegating profile carries ``delegation``: the target profile's
    return evidence items (its own delegation first, then its return
    lines), each ``{path, line, function, returns}``.
    """
    scans = {
        path: _python_class_method_returns(path, source)
        for path, source in code_by_file.items()
        if path.lower().endswith(".py")
    }
    bases = _python_base_classes(code_by_file)
    defined = {
        path: {(cls, method) for (cls, method) in methods}
        for path, methods in scans.items()
    }
    profiles = {
        path: _python_direct_identity_profiles(path, methods)
        for path, methods in scans.items()
    }
    for _round in range(8):
        changed = False
        for path, methods in scans.items():
            for key, info in methods.items():
                if key in profiles[path]:
                    continue
                profile: dict[str, Any] | None = None
                delegation: list[dict[str, Any]] | None = None
                for line_no, text in info["returns"]:
                    step: list[dict[str, Any]] | None = None
                    if _TRUSTED.fullmatch(text):
                        kind, expr = "trusted", text
                    elif _REQUEST.fullmatch(text):
                        kind, expr = "request", text
                    else:
                        match = _PY_SUPER_CALL_RE.match(text)
                        if not match:
                            profile = None
                            break
                        target = _python_method_profile(
                            path, key[0], match.group(1), profiles, defined, bases,
                            skip_self=True)
                        if target is None:
                            profile = None
                            break
                        kind, expr = target["kind"], target["expression"]
                        step = target.get("delegation", []) + [
                            {"path": target["path"], "line": return_line,
                             "function": target["function"],
                             "returns": target["expression"]}
                            for return_line in target["return_lines"]]
                    if profile is None:
                        profile = {"kind": kind, "expression": expr,
                                   "return_lines": [line_no],
                                   "function_line": info["function_line"]}
                        delegation = step if step is not None else []
                    elif (profile["kind"] != kind or profile["expression"] != expr
                          or delegation != (step if step is not None else [])):
                        profile = None
                        break
                    else:
                        profile["return_lines"].append(line_no)
                if profile is not None:
                    profile["return_lines"] = sorted(set(profile["return_lines"]))
                    profiles[path][key] = {
                        **profile, "path": path,
                        "function": f"{key[0]}.{key[1]}",
                        "delegation": delegation if delegation is not None else []}
                    changed = True
        if not changed:
            break
    return profiles


def _python_class_method_ranges(path: str, source: str) -> dict[int, str]:
    """Map line numbers to their enclosing top-level class name.

    Only lines inside a method directly defined in a top-level class are
    mapped. Lines inside a nested function or class definition within that
    method are excluded: their ``self`` does not refer to the outer
    instance. Non-Python files and syntax-invalid Python produce no ranges.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    ranges: dict[int, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lines = set(range(method.lineno, method.end_lineno + 1))
            for nested in ast.walk(method):
                if nested is method:
                    continue
                if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    lines -= set(range(nested.lineno, nested.end_lineno + 1))
            for line in lines:
                ranges[line] = node.name
    return ranges


_TS_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)\b"
)
_TS_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|async|readonly|declare|override|abstract)\s+)*"
    r"([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\((?:[^(){}]|\([^()]*\))*\)\s*(?::\s*[^{]+?)?\s*\{"
)
_TS_RETURN_RE = re.compile(r"^\s*return\s+([^;]+?)\s*;?\s*$")
_TS_FIELD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|readonly|declare|override|abstract|static)\s+)*"
    r"([A-Za-z_$][\w$]*)\s*(?:!)?\s*(?::\s*[^=;{]+?)?\s*=\s*(.+?)\s*;?\s*$"
)
_TS_THIS_ASSIGN_RE = re.compile(
    r"^\s*this\.([A-Za-z_$][\w$]*)\s*=(?![=>])\s*(.+?)\s*;?\s*$"
)
_TS_NEW_RE = re.compile(r"^new\s+([A-Za-z_$][\w$]*)\s*(?:\(|;|$)")
_TS_NAMED_IMPORT_RE = re.compile(
    r"import\s*\{([^}]*)\}\s*from\s*[\"']([^\"']+)[\"']"
)
_TS_IMPORT_SPEC_RE = re.compile(
    r"^([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?$"
)
_TS_NAMESPACE_IMPORT_RE = re.compile(
    r"import\s*\*\s*as\s+([A-Za-z_$][\w$]*)\s+from\s*[\"']([^\"']+)[\"']"
)
_TS_TOPLEVEL_FN_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*"
    r"([A-Za-z_$][\w$]*)\s*\("
)
_TS_EXTENDS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+"
    r"([A-Za-z_$][\w$]*)\s+extends\s+([A-Za-z_$][\w$]*)\b"
)
_TS_SUPER_CALL_RE = re.compile(r"^super\.([A-Za-z_$][\w$]*)\s*\(")
_PY_SUPER_CALL_RE = re.compile(r"^super\(\)\.([A-Za-z_]\w*)\s*\(")
_PY_SUPER_CALL_RHS = re.compile(r"super\(\)\.([A-Za-z_]\w*)\s*\(.*\)$")
_TS_NESTED_FN_RE = re.compile(r"=>\s*\{|\bfunction\s*(?:[A-Za-z_$][\w$]*)?\s*\(")
_TS_STRING_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")


def _strip_ts_line(line: str) -> str:
    """Remove same-line string literals and a trailing ``//`` comment.

    Only for the brace/return scan below: braces inside strings and
    comments must not move the scope stack. A backtick string without a
    closing backtick on this line (a multi-line template literal) is
    stripped from its first backtick to the end of the line.
    """
    line = _TS_STRING_RE.sub("", line)
    if "`" in line:
        line = line.split("`", 1)[0]
    return line.split("//", 1)[0]


def _ts_class_scan(path: str, source: str) -> tuple[
        dict[tuple[str, str], list[tuple[int, str]]], dict[int, str],
        dict[tuple[str, str], set[str | None]], set[str], set[tuple[str, str]]]:
    """One brace-counting pass over a TS/JS file.

    Returns ``(methods, ranges, attr_candidates, class_names, defined)``:
    ``methods`` maps ``(class, method)`` pairs directly defined in a
    top-level class to their direct ``(line, expression)`` returns;
    ``ranges`` maps line numbers to their enclosing top-level class
    name, for lines inside such a method; ``attr_candidates`` maps
    ``(class, attribute)`` pairs to the set of classes constructed for
    them (``None`` for a non-construction binding, which disqualifies
    the attribute); ``class_names`` is the set of top-level class
    names; ``defined`` is the set of ``(class, method)`` pairs directly
    defined in a top-level class (including methods without returns;
    used for the inheritance shadowing check). Returns inside nested
    functions or classes are excluded from both. A method header must
    open its body on the same line; anything else fails closed (no
    method, no returns). Only the direct returns are collected --
    whether they qualify as identity returns is decided by
    ``_ts_identity_return_methods``.
    """
    methods: dict[tuple[str, str], list[tuple[int, str]]] = {}
    ranges: dict[int, str] = {}
    attr_candidates: dict[tuple[str, str], set[str | None]] = {}
    class_names: set[str] = set()
    defined: set[tuple[str, str]] = set()
    # Frames: kind "class" | "method" | "nested", interior brace depth,
    # class name, method name. "nested" covers nested functions and
    # nested classes, whose lines and returns never belong to the outer
    # method; its "flavor" ("arrow" | "function" | "class") records
    # whether the nested body captures the outer ``this``.
    stack: list[dict[str, Any]] = []
    depth = 0
    in_template = False
    pending_nested: str | None = None
    for line_no, raw in enumerate(source.splitlines(), 1):
        # A multi-line template literal: skip detection and returns on
        # its inner lines, but keep counting braces like the rest of
        # this module does (an approximation, documented here).
        if (raw.count("`") - raw.count("\\`")) % 2 == 1:
            in_template = not in_template
        stripped = "" if in_template else _strip_ts_line(raw)
        while stack and stack[-1]["depth"] > depth:
            stack.pop()
        opens = stripped.count("{")
        closes = stripped.count("}")
        was_pending_nested = pending_nested
        pending_nested = None

        def interior(match_end: int) -> int:
            prefix = stripped[:match_end]
            return depth + prefix.count("{") - prefix.count("}")

        if stripped:
            class_frame = next((f for f in reversed(stack) if f["kind"] == "class"), None)
            method_frame = next((f for f in reversed(stack) if f["kind"] == "method"), None)
            nested = any(f["kind"] == "nested" for f in stack)
            nested_this = not any(
                f["kind"] == "nested" and f.get("flavor") in ("function", "class")
                for f in stack)
            if class_frame is None and depth == 0:
                match = _TS_CLASS_RE.match(stripped)
                if match and "{" in stripped:
                    stack.append({"kind": "class", "depth": interior(stripped.index("{") + 1),
                                  "class": match.group(1), "method": None})
                    class_frame = stack[-1]
                    class_names.add(match.group(1))
            elif method_frame is None and class_frame is not None and not nested:
                match = _TS_METHOD_RE.match(stripped)
                if match:
                    name = match.group(1)
                    is_static = "static" in re.findall(r"[A-Za-z_$][\w$]*", stripped[:match.start(1)])
                    if not is_static:
                        stack.append({"kind": "method",
                                      "depth": interior(match.end()),
                                      "class": class_frame["class"], "method": name,
                                      "constructor": name == "constructor",
                                      "returns": []})
                        method_frame = stack[-1]
                        defined.add((class_frame["class"], name))
            if method_frame is not None and class_frame is not None and "{" in stripped:
                # A nested function or class opens a nested frame only when
                # its brace sits deeper than the method body: a brace in a
                # parameter default (e.g. cb = () => {}) is at the method's
                # own depth and must not shadow the whole method.
                brace_depth = interior(stripped.index("{") + 1)
                if brace_depth > method_frame["depth"]:
                    flavor: str | None = None
                    if _TS_CLASS_RE.match(stripped):
                        flavor = "class"
                    elif _TS_NESTED_FN_RE.search(stripped):
                        flavor = ("arrow" if "=>" in stripped[:stripped.index("{")]
                                  else "function")
                    elif was_pending_nested is not None:
                        flavor = was_pending_nested
                    if flavor is not None:
                        stack.append({"kind": "nested", "depth": brace_depth,
                                      "class": None, "method": None, "flavor": flavor})
                        nested = True
                        nested_this = flavor == "arrow"
            if method_frame is not None and not nested:
                ranges[line_no] = method_frame["class"]
                ret = _TS_RETURN_RE.match(stripped)
                if ret and not _TS_NESTED_FN_RE.search(stripped[:ret.start()]):
                    method_frame["returns"].append((line_no, ret.group(1).strip()))
            if method_frame is not None and nested_this:
                # T2: this.<attr> = new <Class>(...) inside a method (an
                # arrow body's `this` still refers to the outer instance;
                # a nested `function` or class body does not). Any other
                # assignment shape records None and disqualifies the
                # attribute, exactly like the Python rule.
                assign = _TS_THIS_ASSIGN_RE.match(stripped)
                if assign:
                    constructed = _TS_NEW_RE.match(assign.group(2))
                    attr_candidates.setdefault(
                        (method_frame["class"], assign.group(1)), set()).add(
                        constructed.group(1) if constructed else None)
            if (class_frame is not None and method_frame is None and not nested
                    and depth == class_frame["depth"]):
                # T2: a class field declaration with an initializer, e.g.
                # private helper = new AuthHelper();. The depth guard keeps
                # body lines of a method whose header the scanner failed to
                # recognize from being read as field declarations. A static
                # field is not an instance attribute. A non-new initializer
                # records None and disqualifies the attribute.
                field = _TS_FIELD_RE.match(stripped)
                if field:
                    is_static = "static" in re.findall(
                        r"[A-Za-z_$][\w$]*", stripped[:field.start(1)])
                    if not is_static:
                        constructed = _TS_NEW_RE.match(field.group(2))
                        attr_candidates.setdefault(
                            (class_frame["class"], field.group(1)), set()).add(
                            constructed.group(1) if constructed else None)
            if method_frame is not None and not nested:
                # An arrow or function header whose body brace lands on the
                # next line (Allman style): remember it for exactly one line.
                if re.search(r"=>\s*$", stripped):
                    pending_nested = "arrow"
                elif re.search(r"\bfunction\s*(?:[A-Za-z_$][\w$]*)?\s*\([^;{]*\)\s*$", stripped):
                    pending_nested = "function"
        depth += opens - closes
        while stack and stack[-1]["depth"] > depth:
            frame = stack.pop()
            if frame["kind"] == "method" and frame["returns"] and not frame.get("constructor"):
                methods.setdefault((frame["class"], frame["method"]), []).extend(frame["returns"])
    while stack:
        frame = stack.pop()
        if frame["kind"] == "method" and frame["returns"] and not frame.get("constructor"):
            methods.setdefault((frame["class"], frame["method"]), []).extend(frame["returns"])
    return methods, ranges, attr_candidates, class_names, defined


def _ts_direct_identity_profiles(
        path: str, methods: dict[tuple[str, str], list[tuple[int, str]]]
        ) -> dict[tuple[str, str], dict[str, Any]]:
    """Direct identity profiles for one file's (class, method) -> returns map.

    A method qualifies only when every direct return fully matches one
    identity pattern, all the same kind and the same expression text.
    The profile carries the qualified ``Class.method`` name, the
    defining file and an empty ``delegation`` list (direct profiles
    delegate to nothing).
    """
    helpers: dict[tuple[str, str], dict[str, Any]] = {}
    for key, returns in methods.items():
        profile: dict[str, Any] | None = None
        for line_no, text in returns:
            if _TRUSTED.fullmatch(text):
                kind = "trusted"
            elif _REQUEST.fullmatch(text):
                kind = "request"
            else:
                profile = None
                break
            if profile is None:
                profile = {"kind": kind, "expression": text,
                           "return_lines": [line_no]}
            elif profile["kind"] != kind or profile["expression"] != text:
                profile = None
                break
            else:
                profile["return_lines"].append(line_no)
        if profile is not None:
            helpers[key] = {**profile, "path": path,
                            "function": f"{key[0]}.{key[1]}", "delegation": []}
    return helpers


def _ts_top_level_function_returns(path: str, source: str) -> dict[str, list[tuple[int, str]]]:
    """Map top-level function names to their direct returns, for TS/JS.

    Only depth-0 ``function`` declarations count (``export`` / ``default`` /
    ``async`` / ``*`` prefixes allowed; an anonymous default export or a
    declaration without a body does not). The header must open its body on
    the same line (Allman style fails closed, like ``_ts_class_scan``).
    Returns inside nested functions or classes are excluded; returns
    inside plain blocks (``if``/``for``) count, as for class methods.
    Arrow-function consts, function expressions and object methods are
    out of scope. A function defined twice merges its returns.
    """
    if not path.lower().endswith((".ts", ".tsx", ".js", ".mjs", ".cjs", ".vue")):
        return {}
    functions: dict[str, list[tuple[int, str]]] = {}
    # Frames: kind "function" | "nested", interior brace depth, name,
    # returns. "nested" covers nested functions and nested classes, whose
    # lines and returns never belong to the outer function.
    stack: list[dict[str, Any]] = []
    depth = 0
    in_template = False
    pending_nested: str | None = None
    for line_no, raw in enumerate(source.splitlines(), 1):
        if (raw.count("`") - raw.count("\\`")) % 2 == 1:
            in_template = not in_template
        stripped = "" if in_template else _strip_ts_line(raw)
        while stack and stack[-1]["depth"] > depth:
            frame = stack.pop()
            if frame["kind"] == "function" and frame["returns"]:
                functions.setdefault(frame["name"], []).extend(frame["returns"])
        opens = stripped.count("{")
        closes = stripped.count("}")
        was_pending_nested = pending_nested
        pending_nested = None

        def interior(match_end: int) -> int:
            prefix = stripped[:match_end]
            return depth + prefix.count("{") - prefix.count("}")

        if stripped:
            fn_frame = next((f for f in reversed(stack) if f["kind"] == "function"), None)
            nested = any(f["kind"] == "nested" for f in stack)
            if fn_frame is None and depth == 0:
                match = _TS_TOPLEVEL_FN_RE.match(stripped)
                if match and "{" in stripped:
                    stack.append({"kind": "function",
                                  "depth": interior(stripped.index("{") + 1),
                                  "name": match.group(1), "returns": []})
                    fn_frame = stack[-1]
            elif fn_frame is not None and "{" in stripped:
                brace_depth = interior(stripped.index("{") + 1)
                if brace_depth > fn_frame["depth"]:
                    flavor: str | None = None
                    if _TS_CLASS_RE.match(stripped):
                        flavor = "class"
                    elif _TS_NESTED_FN_RE.search(stripped):
                        flavor = ("arrow" if "=>" in stripped[:stripped.index("{")]
                                  else "function")
                    elif was_pending_nested is not None:
                        flavor = was_pending_nested
                    if flavor is not None:
                        stack.append({"kind": "nested", "depth": brace_depth,
                                      "name": None, "returns": []})
                        nested = True
            if fn_frame is not None and not nested:
                ret = _TS_RETURN_RE.match(stripped)
                if ret and not _TS_NESTED_FN_RE.search(stripped[:ret.start()]):
                    fn_frame["returns"].append((line_no, ret.group(1).strip()))
                if re.search(r"=>\s*$", stripped):
                    pending_nested = "arrow"
                elif re.search(r"\bfunction\s*(?:[A-Za-z_$][\w$]*)?\s*\([^;{]*\)\s*$", stripped):
                    pending_nested = "function"
        depth += opens - closes
        while stack and stack[-1]["depth"] > depth:
            frame = stack.pop()
            if frame["kind"] == "function" and frame["returns"]:
                functions.setdefault(frame["name"], []).extend(frame["returns"])
    while stack:
        frame = stack.pop()
        if frame["kind"] == "function" and frame["returns"]:
            functions.setdefault(frame["name"], []).extend(frame["returns"])
    return functions


def _ts_top_level_function_profiles(path: str, source: str) -> dict[str, dict[str, Any]]:
    """Map top-level function names to their single identity return, for TS/JS.

    The TS mirror of ``_python_identity_return_helpers``: a top-level
    function qualifies only when every direct return fully matches one
    identity pattern, all the same kind and the same expression text. A
    function defined twice with different return profiles is ambiguous
    and dropped. The profile carries the bare function name and the
    defining file, for propagation records.
    """
    if not path.lower().endswith((".ts", ".tsx", ".js", ".mjs", ".cjs", ".vue")):
        return {}
    returns_map = _ts_top_level_function_returns(path, source)
    helpers: dict[str, dict[str, Any]] = {}
    for name, returns in returns_map.items():
        profile: dict[str, Any] | None = None
        for line_no, text in returns:
            if _TRUSTED.fullmatch(text):
                kind = "trusted"
            elif _REQUEST.fullmatch(text):
                kind = "request"
            else:
                profile = None
                break
            if profile is None:
                profile = {"kind": kind, "expression": text,
                           "return_lines": [line_no]}
            elif profile["kind"] != kind or profile["expression"] != text:
                profile = None
                break
            else:
                profile["return_lines"].append(line_no)
        if profile is not None:
            helpers[name] = {**profile, "path": path,
                             "function": name, "delegation": []}
    return helpers


def _ts_identity_return_methods(path: str, source: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (class, method) pairs to their single identity return, for TS/JS.

    The TS mirror of ``_python_identity_return_methods``: only methods
    directly defined in a top-level class qualify, under the same return
    rule as ``_identity_return_profile`` (every direct return fully
    matches one identity pattern, all the same kind and the same
    expression text). A (class, method) defined twice with different
    return profiles is ambiguous and dropped. The profile carries the
    qualified ``Class.method`` name and the defining file, for
    propagation records. This is the direct-only version; methods
    delegating through ``super`` need the project-wide
    ``_ts_all_identity_return_methods``.
    """
    if not path.lower().endswith((".ts", ".tsx", ".js", ".mjs", ".cjs", ".vue")):
        return {}
    methods, _ranges, _candidates, _names, _defined = _ts_class_scan(path, source)
    return _ts_direct_identity_profiles(path, methods)


def _ts_all_identity_return_methods(
        code_by_file: dict[str, str]) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    """Project-wide TS identity return profiles, including super delegation.

    Direct profiles first, then up to 8 fixpoint rounds resolving
    methods whose every direct return is an identity expression or a
    ``super.<method>(...)`` call to an already-profiled base method
    (T4 chain walk, ``skip_self=True``). All returns must agree on
    kind, expression and delegation steps: a direct return mixed with
    a super-delegated return, or super calls to different targets,
    disqualifies the method. A delegating profile carries
    ``delegation``: the target profile's return evidence items (its
    own delegation first, then its return lines), each
    ``{path, line, function, returns}``.
    """
    scans = {
        path: _ts_class_scan(path, source)
        for path, source in code_by_file.items()
        if not path.lower().endswith(".py")
    }
    bases = _ts_base_classes(code_by_file)
    defined = {path: scan[4] for path, scan in scans.items()}
    profiles = {
        path: _ts_direct_identity_profiles(path, scan[0])
        for path, scan in scans.items()
    }
    for _round in range(8):
        changed = False
        for path, scan in scans.items():
            for key, returns in scan[0].items():
                if key in profiles[path]:
                    continue
                profile: dict[str, Any] | None = None
                delegation: list[dict[str, Any]] | None = None
                for line_no, text in returns:
                    step: list[dict[str, Any]] | None = None
                    if _TRUSTED.fullmatch(text):
                        kind, expr = "trusted", text
                    elif _REQUEST.fullmatch(text):
                        kind, expr = "request", text
                    else:
                        match = _TS_SUPER_CALL_RE.match(text)
                        if not match:
                            profile = None
                            break
                        target = _ts_method_profile(
                            path, key[0], match.group(1), profiles, defined, bases,
                            skip_self=True)
                        if target is None:
                            profile = None
                            break
                        kind, expr = target["kind"], target["expression"]
                        step = target.get("delegation", []) + [
                            {"path": target["path"], "line": return_line,
                             "function": target["function"],
                             "returns": target["expression"]}
                            for return_line in target["return_lines"]]
                    if profile is None:
                        profile = {"kind": kind, "expression": expr,
                                   "return_lines": [line_no]}
                        delegation = step if step is not None else []
                    elif (profile["kind"] != kind or profile["expression"] != expr
                          or delegation != (step if step is not None else [])):
                        profile = None
                        break
                    else:
                        profile["return_lines"].append(line_no)
                if profile is not None:
                    profile["return_lines"] = sorted(set(profile["return_lines"]))
                    profiles[path][key] = {
                        **profile, "path": path,
                        "function": f"{key[0]}.{key[1]}",
                        "delegation": delegation if delegation is not None else []}
                    changed = True
        if not changed:
            break
    return profiles


def _ts_class_method_ranges(path: str, source: str) -> dict[int, str]:
    """Map line numbers to their enclosing top-level class name, for TS/JS.

    The TS mirror of ``_python_class_method_ranges``: only lines inside
    a method directly defined in a top-level class are mapped. Lines
    inside a nested function or class definition within that method are
    excluded: their ``this`` does not refer to the outer instance.
    """
    if not path.lower().endswith((".ts", ".tsx", ".js", ".mjs", ".cjs", ".vue")):
        return {}
    _methods, ranges, _candidates, _names, _defined = _ts_class_scan(path, source)
    return ranges


def _strip_ts_comment(line: str) -> str:
    """Remove // and same-line /* */ comments, keeping string contents.

    ``_strip_ts_line`` also blanks string contents, which is right for
    return scanning but wrong for import parsing (the module specifier
    is a string). This keeps strings intact and only cuts comments.
    """
    out: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(line):
        ch = line[index]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and index + 1 < len(line):
                out.append(line[index + 1])
                index += 2
                continue
            if ch == quote:
                quote = None
            index += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
        elif ch == "/" and index + 1 < len(line) and line[index + 1] == "/":
            break
        elif ch == "/" and index + 1 < len(line) and line[index + 1] == "*":
            end = line.find("*/", index + 2)
            if end == -1:
                break
            index = end + 2
            continue
        else:
            out.append(ch)
        index += 1
    return "".join(out)


def _ts_import_bindings(path: str, source: str) -> dict[str, tuple[str, str]]:
    """Map local names to (module specifier, imported name) for named TS/JS imports.

    Only value named imports count (``import { A, B as C } from "./x"``);
    default imports, namespace imports, side-effect imports, dynamic
    ``import(...)``, ``require(...)`` and ``import type`` are ignored.
    A local name imported from two different modules is ambiguous and
    dropped. Multi-line imports are joined until the ``from "..."``
    part is complete.
    """
    if not path.lower().endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")):
        return {}
    bindings: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        stripped = _strip_ts_comment(lines[index])
        head = stripped.lstrip()
        if (head.startswith("import") and not head.startswith("import(")
                and not re.match(r"import\s+type\b", head)):
            buf = stripped
            while (not re.search(r"from\s*[\"'][^\"']+[\"']", buf)
                   and index + 1 < len(lines)):
                index += 1
                buf += " " + _strip_ts_comment(lines[index])
            for match in _TS_NAMED_IMPORT_RE.finditer(buf):
                specifier = match.group(2)
                for spec in match.group(1).split(","):
                    spec = spec.strip()
                    if not spec:
                        continue
                    spec_match = _TS_IMPORT_SPEC_RE.match(spec)
                    if not spec_match:
                        continue
                    imported = spec_match.group(1)
                    local = spec_match.group(2) or imported
                    if local in ambiguous:
                        continue
                    binding = (specifier, imported)
                    if local in bindings and bindings[local] != binding:
                        del bindings[local]
                        ambiguous.add(local)
                    else:
                        bindings[local] = binding
        index += 1
    return bindings


def _ts_namespace_imports(path: str, source: str) -> dict[str, str]:
    """Map local names to module specifiers for namespace TS/JS imports.

    Only ``import * as ns from \"./x\"`` counts; default imports,
    named imports, side-effect imports, dynamic ``import(...)``,
    ``require(...)`` and ``import type`` are ignored. A local name
    imported from two different modules is ambiguous and dropped.
    Multi-line imports are joined until the ``from \"...\"`` part is
    complete, like ``_ts_import_bindings``.
    """
    if not path.lower().endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")):
        return {}
    bindings: dict[str, str] = {}
    ambiguous: set[str] = set()
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        stripped = _strip_ts_comment(lines[index])
        head = stripped.lstrip()
        if (head.startswith("import") and not head.startswith("import(")
                and not re.match(r"import\s+type\b", head)):
            buf = stripped
            while (not re.search(r"from\s*[\"'][^\"']+[\"']", buf)
                   and index + 1 < len(lines)):
                index += 1
                buf += " " + _strip_ts_comment(lines[index])
            for match in _TS_NAMESPACE_IMPORT_RE.finditer(buf):
                local, specifier = match.group(1), match.group(2)
                if local in ambiguous:
                    continue
                if local in bindings and bindings[local] != specifier:
                    del bindings[local]
                    ambiguous.add(local)
                else:
                    bindings[local] = specifier
        index += 1
    return bindings


def _ts_namespace_import_bindings(
        code_by_file: dict[str, str]) -> dict[str, dict[str, str]]:
    """Map files to {namespace local: target file} for namespace imports.

    Only relative specifiers resolving to a project file count; package
    imports, ``tsconfig`` path aliases and anything outside the project
    fail closed. The target file's top-level function profiles decide
    whether a ``ns.<fn>(...)`` call binds -- this map alone binds
    nothing.
    """
    result: dict[str, dict[str, str]] = {}
    for path, source in code_by_file.items():
        if path.lower().endswith(".py"):
            continue
        bindings: dict[str, str] = {}
        for local, specifier in _ts_namespace_imports(path, source).items():
            target = _ts_resolve_import(specifier, path, code_by_file)
            if target is not None and target != path:
                bindings[local] = target
        if bindings:
            result[path] = bindings
    return result


def _ts_named_function_bindings(
        code_by_file: dict[str, str],
        top_level_profiles: dict[str, dict[str, dict[str, Any]]]
        ) -> dict[str, dict[str, dict[str, Any]]]:
    """Map files to {local name: profile} for named-imported top-level functions.

    The TS mirror of ``_python_imported_identity_helpers`` for functions:
    only ``import { name [as alias] } from \"./x\"`` whose target file
    directly defines a qualifying top-level function under the original
    name. A local name shadowed by a top-level function defined in the
    importing file itself is skipped (the local definition wins).
    Re-exports (the target file itself importing the name) are not
    followed.
    """
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for path, source in code_by_file.items():
        if path.lower().endswith(".py"):
            continue
        local_names = set(_ts_top_level_function_returns(path, source))
        bindings: dict[str, dict[str, Any]] = {}
        for local, (specifier, imported) in _ts_import_bindings(path, source).items():
            if local in local_names or local in bindings:
                continue
            target = _ts_resolve_import(specifier, path, code_by_file)
            if target is None or target == path:
                continue
            profile = top_level_profiles.get(target, {}).get(imported)
            if profile is not None:
                bindings[local] = profile
        if bindings:
            result[path] = bindings
    return result


def _ts_resolve_import(specifier: str, from_path: str,
                       code_by_file: dict[str, str]) -> str | None:
    """Resolve a relative TS/JS import specifier to a project file path.

    Only relative specifiers (``./`` / ``../``) resolve, against the
    importing file's directory, trying the usual extension and
    ``index`` variants. The target must be a file in ``code_by_file``;
    package imports, ``tsconfig`` path aliases and anything outside
    the project fail closed.
    """
    if not specifier.startswith(("./", "../")):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), specifier))
    # V2: an explicit `.vue` suffix resolves directly when the file is in
    # the project (the extension candidates below would look for
    # `<name>.vue.ts` instead).
    if base in code_by_file:
        return base
    for candidate in [base + ext for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")]:
        if candidate in code_by_file:
            return candidate
    for candidate in [base + "/index" + ext for ext in (".ts", ".tsx", ".js", ".jsx", ".vue")]:
        if candidate in code_by_file:
            return candidate
    return None


def _ts_cross_file_instance_bindings(
        code_by_file: dict[str, str]) -> dict[str, dict[tuple[str, str], tuple[str, str]]]:
    """Map files to cross-file (class, attribute) -> (target file, class) bindings.

    The TS mirror of ``_python_imported_identity_helpers`` for instance
    attributes: for each file, take the ``_ts_class_scan`` candidates
    whose constructed class is not a top-level class of the same file
    (those are the same-file T2 bindings) and, when the constructed
    name is a named import resolving to a project file that defines
    the imported name as a top-level class, record the cross-file
    binding. The T2 disqualification rule applies first: an attribute
    with a non-construction binding or two different constructed
    classes is dropped here too.
    """
    result: dict[str, dict[tuple[str, str], tuple[str, str]]] = {}
    for path, source in code_by_file.items():
        if path.lower().endswith(".py"):
            continue
        _methods, _ranges, candidates, class_names, _defined = _ts_class_scan(path, source)
        imports = _ts_import_bindings(path, source)
        bindings: dict[tuple[str, str], tuple[str, str]] = {}
        for (cls, attr), constructed in candidates.items():
            if len(constructed) != 1:
                continue
            only = next(iter(constructed))
            if only is None or only in class_names:
                continue
            binding = imports.get(only)
            if binding is None:
                continue
            specifier, imported = binding
            target = _ts_resolve_import(specifier, path, code_by_file)
            if target is None or target == path:
                continue
            _m, _r, _c, target_classes, _d = _ts_class_scan(target, code_by_file[target])
            if imported in target_classes:
                bindings[(cls, attr)] = (target, imported)
        if bindings:
            result[path] = bindings
    return result


def _ts_base_classes(
        code_by_file: dict[str, str]) -> dict[str, dict[str, tuple[str, str]]]:
    """Map files to {class: (base file, base class)} for TS/JS extends clauses.

    Only ``class X extends Y`` with a bare-identifier base counts: the
    base resolves to a top-level class of the same file first, else
    through a named import (T3) to a project file defining it. An
    expression base (``extends mixin(...)``), a namespaced base
    (``extends ns.Base``) or an unresolvable base leaves the class
    without a base (fail closed). Only top-level classes are recorded.
    """
    result: dict[str, dict[str, tuple[str, str]]] = {}
    for path, source in code_by_file.items():
        if path.lower().endswith(".py"):
            continue
        _m, _r, _c, class_names, _d = _ts_class_scan(path, source)
        imports = _ts_import_bindings(path, source)
        bases: dict[str, tuple[str, str]] = {}
        for line in source.splitlines():
            match = _TS_EXTENDS_RE.match(_strip_ts_comment(line))
            if not match:
                continue
            cls, base = match.group(1), match.group(2)
            if cls not in class_names:
                continue
            if base in class_names:
                bases[cls] = (path, base)
                continue
            binding = imports.get(base)
            if binding is None:
                continue
            specifier, imported = binding
            target = _ts_resolve_import(specifier, path, code_by_file)
            if target is None or target == path:
                continue
            _m2, _r2, _c2, target_classes, _d2 = _ts_class_scan(
                target, code_by_file[target])
            if imported in target_classes:
                bases[cls] = (target, imported)
        if bases:
            result[path] = bases
    return result


def _ts_method_profile(file: str, cls: str, method: str,
                       profiles: dict[str, dict[tuple[str, str], dict[str, Any]]],
                       defined: dict[str, set[tuple[str, str]]],
                       bases: dict[str, dict[str, tuple[str, str]]],
                       skip_self: bool = False) -> dict[str, Any] | None:
    """Resolve a method to its single identity return profile, walking the extends chain.

    The class itself first (unless ``skip_self``, for ``super`` calls,
    which skip the current class): defined but without an identity
    profile means shadowed -- return None and do not walk up. Not
    defined: walk up the extends chain (at most 8 levels, cycle-safe);
    each ancestor either defines the method (profile or shadowed None)
    or not (continue). A profile carries the defining file's path.
    """
    if not skip_self and (cls, method) in defined.get(file, set()):
        return profiles.get(file, {}).get((cls, method))
    seen: set[tuple[str, str]] = set()
    current_file, current_class = file, cls
    for _ in range(8):
        base = bases.get(current_file, {}).get(current_class)
        if base is None:
            return None
        base_file, base_class = base
        if (base_file, base_class) in seen:
            return None
        seen.add((base_file, base_class))
        if (base_class, method) in defined.get(base_file, set()):
            return profiles.get(base_file, {}).get((base_class, method))
        current_file, current_class = base_file, base_class
    return None


def _ts_instance_attribute_classes(path: str, source: str) -> dict[tuple[str, str], str]:
    """Map (class, attribute) pairs to the class constructed for them, for TS/JS.

    The TS mirror of ``_python_instance_attribute_classes``: for each
    top-level class, collect ``this.<attr> = new <Class>(...)``
    assignments across its directly defined methods (including the
    constructor and nested arrow functions, whose bodies capture
    ``this``; nested ``function`` declarations and nested classes do
    not) plus class field declarations with a ``new`` initializer
    (``private helper = new AuthHelper();``). An attribute qualifies
    only when every such binding constructs the same top-level class
    defined in the same file; any other assignment shape, a different
    class, or a class not defined in this file disqualifies it.
    """
    if not path.lower().endswith((".ts", ".tsx", ".js", ".mjs", ".cjs", ".vue")):
        return {}
    _methods, _ranges, candidates, class_names, _defined = _ts_class_scan(path, source)
    bindings: dict[tuple[str, str], str] = {}
    for key, constructed in candidates.items():
        if len(constructed) == 1:
            only = next(iter(constructed))
            if only is not None and only in class_names:
                bindings[key] = only
    return bindings


def _python_instance_attribute_classes(path: str, source: str) -> dict[tuple[str, str], str]:
    """Map (class, attribute) pairs to the class constructed for them.

    For each top-level class, collect ``self.<attr> = <ClassName>(...)``
    assignments across its directly defined methods (including assignments
    inside nested definitions, whose closures still capture ``self``).
    An attribute qualifies only when every such assignment constructs the
    same top-level class defined in the same file; any other assignment
    shape, a different class, or a class not defined in this file
    disqualifies it. Non-Python files and syntax-invalid Python produce
    no bindings.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    candidates: dict[tuple[str, str], set[str | None]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for item in ast.walk(method):
                targets: list[ast.expr] = []
                value: ast.expr | None = None
                if isinstance(item, ast.Assign):
                    targets = item.targets
                    value = item.value
                elif isinstance(item, ast.AnnAssign) and item.value is not None:
                    targets = [item.target]
                    value = item.value
                else:
                    continue
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    constructed: str | None = value.func.id
                else:
                    constructed = None
                for target in targets:
                    if not (isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"):
                        continue
                    key = (node.name, target.attr)
                    candidates.setdefault(key, set()).add(
                        constructed if constructed in class_names else None)
    bindings: dict[tuple[str, str], str] = {}
    for key, constructed in candidates.items():
        if len(constructed) == 1:
            only = next(iter(constructed))
            if only is not None:
                bindings[key] = only
    return bindings


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
    # V2: a `.vue` file is scanned through its line-padded `<script>` text
    # (template/style braces never reach the brace-depth tracking), so every
    # TS precompute and the per-file line loop below use the effective
    # source. Padding keeps line numbers equal to the file's, and records
    # still carry the `.vue` path. A `.vue` file without a `<script>` block
    # scans as empty. Python precomputes keep the original text.
    effective_code_by_file = {
        file_path: (vue_script_for_adapter(file_source) or "")
        if file_path.lower().endswith(".vue") else file_source
        for file_path, file_source in code_by_file.items()
    }
    module_index = _python_module_index(code_by_file)
    same_file_helpers = {
        file_path: _python_identity_return_helpers(file_path, file_source)
        for file_path, file_source in code_by_file.items()
        if file_path.lower().endswith(".py")
    }
    same_file_methods = _python_all_identity_return_methods(code_by_file)
    py_defined_methods = {
        file_path: set(_python_class_method_returns(file_path, file_source))
        for file_path, file_source in code_by_file.items()
        if file_path.lower().endswith(".py")
    }
    py_base_classes = _python_base_classes(code_by_file)
    same_file_instance_bindings = {
        file_path: _python_instance_attribute_classes(file_path, file_source)
        for file_path, file_source in code_by_file.items()
        if file_path.lower().endswith(".py")
    }
    module_bindings = {
        file_path: _python_module_bindings(file_path, file_source, module_index)
        for file_path, file_source in code_by_file.items()
        if file_path.lower().endswith(".py")
    }
    ts_same_file_methods = _ts_all_identity_return_methods(effective_code_by_file)
    ts_same_file_functions = {
        file_path: _ts_top_level_function_profiles(file_path, file_source)
        for file_path, file_source in effective_code_by_file.items()
        if not file_path.lower().endswith(".py")
    }
    ts_named_function_bindings = _ts_named_function_bindings(effective_code_by_file, ts_same_file_functions)
    ts_namespace_import_bindings = _ts_namespace_import_bindings(effective_code_by_file)
    ts_same_file_ranges = {
        file_path: _ts_class_method_ranges(file_path, file_source)
        for file_path, file_source in effective_code_by_file.items()
        if not file_path.lower().endswith(".py")
    }
    ts_same_file_instance = {
        file_path: _ts_instance_attribute_classes(file_path, file_source)
        for file_path, file_source in effective_code_by_file.items()
        if not file_path.lower().endswith(".py")
    }
    ts_cross_instance_bindings = _ts_cross_file_instance_bindings(effective_code_by_file)
    ts_defined_methods = {
        file_path: _ts_class_scan(file_path, file_source)[4]
        for file_path, file_source in effective_code_by_file.items()
        if not file_path.lower().endswith(".py")
    }
    ts_base_classes = _ts_base_classes(effective_code_by_file)
    for path, source in sorted(effective_code_by_file.items()):
        if path.lower().endswith(".py"):
            function_owners = _python_function_owners(path, source)
            imported_helpers = _python_imported_identity_helpers(
                path, source, module_index, same_file_helpers)
            return_helpers = dict(imported_helpers)
            for name, helper in same_file_helpers.get(path, {}).items():
                return_helpers[name] = {**helper, "path": path, "function": name}
            bindings = module_bindings.get(path, {})
            method_helpers = same_file_methods.get(path, {})
            class_ranges = _python_class_method_ranges(path, source)
            instance_bindings = same_file_instance_bindings.get(path, {})
            ts_method_helpers = {}
            ts_class_ranges = {}
            ts_instance_bindings = {}
            ts_cross_bindings = {}
            ts_has_base = False
            ts_file_functions = {}
            ts_file_named_functions = {}
            ts_file_namespaces = {}
            py_has_base = path in py_base_classes
        elif function_owners_by_file is not None:
            supplied = function_owners_by_file.get(path)
            function_owners = supplied if isinstance(supplied, dict) else {}
            return_helpers = {}
            bindings = {}
            method_helpers = {}
            class_ranges = {}
            instance_bindings = {}
            ts_method_helpers = ts_same_file_methods.get(path, {})
            ts_class_ranges = ts_same_file_ranges.get(path, {})
            ts_instance_bindings = ts_same_file_instance.get(path, {})
            ts_cross_bindings = ts_cross_instance_bindings.get(path, {})
            ts_has_base = path in ts_base_classes
            ts_file_functions = ts_same_file_functions.get(path, {})
            ts_file_named_functions = ts_named_function_bindings.get(path, {})
            ts_file_namespaces = ts_namespace_import_bindings.get(path, {})
            py_has_base = False
        else:
            function_owners = {}
            return_helpers = {}
            bindings = {}
            method_helpers = {}
            class_ranges = {}
            instance_bindings = {}
            ts_method_helpers = ts_same_file_methods.get(path, {})
            ts_class_ranges = ts_same_file_ranges.get(path, {})
            ts_instance_bindings = ts_same_file_instance.get(path, {})
            ts_cross_bindings = ts_cross_instance_bindings.get(path, {})
            ts_has_base = path in ts_base_classes
            ts_file_functions = ts_same_file_functions.get(path, {})
            ts_file_named_functions = ts_named_function_bindings.get(path, {})
            ts_file_namespaces = ts_namespace_import_bindings.get(path, {})
            py_has_base = False
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
                elif (return_helpers or bindings or method_helpers
                        or instance_bindings or ts_method_helpers
                        or ts_instance_bindings or ts_cross_bindings
                        or ts_has_base or py_has_base
                        or ts_file_functions or ts_file_named_functions
                        or ts_file_namespaces):
                    # P4/P5: one hop through a same-file or from-imported
                    # helper, e.g. uid = get_uid(req). Only a bare call
                    # counts: no method call, no await, no attribute access
                    # after the call.
                    call_match = bare_call.fullmatch(rhs)
                    helper = return_helpers.get(call_match.group(1)) if call_match else None
                    if helper is None and call_match:
                        # T6: a top-level TS/JS function, same file first,
                        # then a named import (the P5 mirror for TS
                        # functions).
                        helper = ts_file_functions.get(call_match.group(1))
                        if helper is None:
                            helper = ts_file_named_functions.get(call_match.group(1))
                    if helper is None:
                        # P6: one hop through a module binding, e.g.
                        # uid = server.utils.get_uid(req).
                        attr_match = attr_call.fullmatch(rhs)
                        if attr_match:
                            helper = _resolve_module_attribute_helper(
                                attr_match.group(1), bindings, module_index,
                                same_file_helpers)
                        if helper is None and attr_match:
                            # P7: one hop through a method of the enclosing
                            # class, e.g. uid = self.get_uid(req) or
                            # uid = cls.get_uid(req). P9: when the enclosing
                            # class does not define the method, walk up the
                            # extends chain (a definition without an identity
                            # profile shadows the base and stops the walk).
                            parts = attr_match.group(1).split(".")
                            if len(parts) == 2 and parts[0] in ("self", "cls"):
                                enclosing = class_ranges.get(line_no)
                                if enclosing is not None:
                                    helper = _python_method_profile(
                                        path, enclosing, parts[1],
                                        same_file_methods, py_defined_methods,
                                        py_base_classes)
                            elif len(parts) == 2 and parts[0] == "this":
                                # T1: one hop through a method of the
                                # enclosing TS/JS class, e.g.
                                # uid = this.get_uid(req). T4: when the
                                # enclosing class does not define the
                                # method, walk up the extends chain (a
                                # definition without an identity profile
                                # shadows the base and stops the walk).
                                enclosing = ts_class_ranges.get(line_no)
                                if enclosing is not None:
                                    helper = _ts_method_profile(
                                        path, enclosing, parts[1],
                                        ts_same_file_methods, ts_defined_methods,
                                        ts_base_classes)
                            elif len(parts) == 2 and parts[0] == "super":
                                # T4: one hop through the direct base
                                # class, e.g. uid = super.get_uid(req).
                                enclosing = ts_class_ranges.get(line_no)
                                if enclosing is not None:
                                    helper = _ts_method_profile(
                                        path, enclosing, parts[1],
                                        ts_same_file_methods, ts_defined_methods,
                                        ts_base_classes, skip_self=True)
                            elif len(parts) == 2:
                                # T6: one hop through a namespace import,
                                # e.g. import * as auth from "./auth";
                                # uid = auth.get_uid(req). The helper is a
                                # top-level function of the target file.
                                target_file = ts_file_namespaces.get(parts[0])
                                if target_file is not None:
                                    helper = ts_same_file_functions.get(
                                        target_file, {}).get(parts[1])
                            elif len(parts) == 3 and parts[0] == "this":
                                # T2: one hop through an instance attribute
                                # bound in the enclosing TS/JS class, e.g.
                                # uid = this.helper.get_uid(req).
                                enclosing = ts_class_ranges.get(line_no)
                                if enclosing is not None:
                                    target = ts_instance_bindings.get((enclosing, parts[1]))
                                    if target is not None:
                                        # T4: the helper's class may itself
                                        # inherit the method.
                                        helper = _ts_method_profile(
                                            path, target, parts[2],
                                            ts_same_file_methods, ts_defined_methods,
                                            ts_base_classes)
                                    else:
                                        # T3: the attribute's class lives in
                                        # another file, brought in by a named
                                        # import; the profile carries the
                                        # target file's path.
                                        cross = ts_cross_bindings.get((enclosing, parts[1]))
                                        if cross is not None:
                                            target_file, target_class = cross
                                            helper = _ts_method_profile(
                                                target_file, target_class, parts[2],
                                                ts_same_file_methods, ts_defined_methods,
                                                ts_base_classes)
                            elif len(parts) == 3 and parts[0] == "self":
                                # P8: one hop through an instance attribute
                                # bound in the enclosing class, e.g.
                                # uid = self.helper.get_uid(req).
                                enclosing = class_ranges.get(line_no)
                                if enclosing is not None:
                                    target = instance_bindings.get((enclosing, parts[1]))
                                    if target is not None:
                                        # P9: the helper's class may itself
                                        # inherit the method.
                                        helper = _python_method_profile(
                                            path, target, parts[2],
                                            same_file_methods, py_defined_methods,
                                            py_base_classes)
                    if helper is None:
                        # P9: one hop through the direct base class, e.g.
                        # uid = super().get_uid(req). (attr_call cannot
                        # match this: its dotted prefix contains "()".)
                        super_match = _PY_SUPER_CALL_RHS.fullmatch(rhs)
                        if super_match:
                            enclosing = class_ranges.get(line_no)
                            if enclosing is not None:
                                helper = _python_method_profile(
                                    path, enclosing, super_match.group(1),
                                    same_file_methods, py_defined_methods,
                                    py_base_classes, skip_self=True)
                    if helper is not None:
                        aliases[name] = {
                            "kind": helper["kind"], "expression": helper["expression"],
                            "line": line_no, "via_return": True,
                            "propagation": (
                                helper.get("delegation", []) +
                                [{"path": helper["path"], "line": return_line,
                                  "function": helper["function"],
                                  "returns": helper["expression"]}
                                 for return_line in helper["return_lines"]]
                                + [{"path": path, "line": line_no, "variable": name}]
                            ),
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
