"""Angular component helpers for ring 2 UI-entry discovery.

An Angular component is a TypeScript class carrying a ``@Component``
decorator; its template is usually an external ``.html`` file named by
``templateUrl``.  The TypeScript adapter already analyses the component
class (its methods are symbols), but the template's ``(event)="handler()"``
bindings live in the ``.html`` file, so this module extracts them as pure
text processing over parent-verified content and resolves their handler
expressions against the component's method symbols.  No file is opened and
nothing is executed.

Symbol-id convention (important): a binding target id is built as
``symbol:<component path>#<method name>:<line>`` from the matched symbol's
``name`` and ``line``.  The adapter's ``symbol_id`` field must NOT be reused
here: it carries the qualified name (``PhotoCardComponent.savePhoto``) while
the graph's symbol node ids, ``caller_symbol_id`` and ``all_symbol_ids``
are all bare names (``savePhoto``).  A qualified target id would miss
``all_symbol_ids`` at edge emission and the binding would read as
unresolved even though the handler exists.
"""

from __future__ import annotations

import re
from typing import Any

_COMPONENT_RE = re.compile(r"@Component\b")
_CLASS_RE = re.compile(r"(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")
_TEMPLATE_URL_RE = re.compile(r"""templateUrl\s*:\s*(?:"([^"]*)"|'([^']*)')""")
_TEMPLATE_RE = re.compile(r"""template\s*:\s*(["'`])""")
# <!-- ... --> comments are blanked (newlines kept) before matching, so a
# commented-out binding never matches and offsets stay valid.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# (event)="expr" or the canonical on-event="expr" form.  The event name is
# kept verbatim (Angular key events such as keyup.enter carry a dot).
_EVENT_ATTR_RE = re.compile(
    r"""(?:\(\s*([A-Za-z][\w.-]*)\s*\)|on-([A-Za-z][\w.-]*))\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_MEMBER_RE = re.compile(r"^(?:[A-Za-z_$][\w$]*\.)*([A-Za-z_$][\w$]*)$")
_CALL_RE = re.compile(r"^([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\([\s\S]*\)$")
_ARROW_RE = re.compile(r"^(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")


def _line_col(content: str, offset: int) -> tuple[int, int]:
    line = content.count("\n", 0, offset) + 1
    last_nl = content.rfind("\n", 0, offset)
    return line, offset - last_nl


def _scan_balanced_parens(content: str, open_index: int) -> int | None:
    """Index just past the ``)`` matching the ``(`` at ``open_index``.

    String-aware (single/double/backtick quotes, backslash escapes) so a
    paren inside a decorator string never counts.  ``None`` when the
    parens never balance (fail-closed).
    """
    depth = 0
    quote: str | None = None
    escaped = False
    index = open_index
    while index < len(content):
        char = content[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _iter_component_decorators(ts_source: str):
    """Yield ``(decorator_text, open_paren, close, line)`` per ``@Component``.

    ``decorator_text`` spans the balanced parens (inclusive);
    ``open_paren``/``close`` are indexes into ``ts_source`` so a match
    inside the decorator can be located in the file; ``line`` is the
    1-based line of the ``@Component`` token.  Unbalanced decorators are
    skipped (fail-closed).
    """
    for match in _COMPONENT_RE.finditer(ts_source):
        open_paren = ts_source.find("(", match.end())
        if open_paren < 0:
            continue
        close = _scan_balanced_parens(ts_source, open_paren)
        if close is None:
            continue
        line, _column = _line_col(ts_source, match.start())
        yield ts_source[open_paren:close], open_paren, close, line


def _scan_string(ts_source: str, open_quote_index: int) -> int | None:
    """End index (exclusive) of the string starting at ``open_quote_index``.

    Backslash escapes are honored, so an escaped quote never ends the
    string.  ``None`` when the string never closes (fail-closed).
    """
    quote = ts_source[open_quote_index]
    index = open_quote_index + 1
    escaped = False
    while index < len(ts_source):
        char = ts_source[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return index + 1
        index += 1
    return None


def find_angular_components(ts_source: str) -> list[dict[str, Any]]:
    """Find ``@Component`` classes with a literal external ``templateUrl``.

    Returns ``{"class_name", "template_url", "line"}`` per component, in
    file order.  Components without a literal ``templateUrl`` (inline
    ``template:`` or no template) are skipped: inline templates are
    handled by :func:`find_inline_templates`, not a failed parse.
    """
    components: list[dict[str, Any]] = []
    for decorator, _open_paren, close, line in _iter_component_decorators(ts_source):
        url_match = _TEMPLATE_URL_RE.search(decorator)
        if url_match is None:
            continue
        template_url = url_match.group(1) if url_match.group(1) is not None else url_match.group(2)
        if not template_url:
            continue
        class_match = _CLASS_RE.search(ts_source, close, close + 1000)
        if class_match is None:
            continue
        components.append({
            "class_name": class_match.group(1),
            "template_url": template_url,
            "line": line,
        })
    return components


def find_inline_templates(ts_source: str) -> list[dict[str, Any]]:
    """Find ``@Component`` classes with a literal inline ``template:``.

    Returns ``{"class_name", "text", "start_line", "start_column", "line"}``
    per component, in file order.  ``text`` is the raw string content
    (quotes removed); ``start_line``/``start_column`` locate its first
    character in the file; ``line`` is the ``@Component`` line.  A
    component carrying a literal ``templateUrl`` is skipped (Angular
    forbids both; the external template wins), as are non-literal
    template values (a constant or variable reference) and unclosed
    strings (fail-closed).
    """
    templates: list[dict[str, Any]] = []
    for decorator, open_paren, close, line in _iter_component_decorators(ts_source):
        if _TEMPLATE_URL_RE.search(decorator):
            continue
        template_match = _TEMPLATE_RE.search(decorator)
        if template_match is None:
            continue
        quote_index = open_paren + template_match.start(1)
        end = _scan_string(ts_source, quote_index)
        if end is None:
            continue
        class_match = _CLASS_RE.search(ts_source, close, close + 1000)
        if class_match is None:
            continue
        start_line, start_column = _line_col(ts_source, quote_index + 1)
        templates.append({
            "class_name": class_match.group(1),
            "text": ts_source[quote_index + 1:end - 1],
            "start_line": start_line,
            "start_column": start_column,
            "line": line,
        })
    return templates


def inline_template_for_binding(text: str, start_line: int, start_column: int) -> str:
    """Line/column-padded template text for binding extraction.

    The padding aligns the template string's first content character with
    its file position, so :func:`extract_angular_template_bindings`
    returns file-relative line/column numbers directly (the same trick
    Vue's line-padded script block uses).
    """
    return "\n" * (start_line - 1) + " " * (start_column - 1) + text


def _blank_comments(html_source: str) -> str:
    """Replace ``<!-- ... -->`` spans with spaces (newlines preserved)."""
    def _blank(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))
    return _COMMENT_RE.sub(_blank, html_source)


def extract_angular_template_bindings(html_source: str) -> list[dict[str, Any]]:
    """Extract ``(event)="expr"`` / ``on-event="expr"`` bindings.

    Returns ``{"event_name", "line", "column", "binding_expression"}`` per
    binding, in file order.  Property bindings (``[prop]``), structural
    directives (``*ngIf``) and bare ``click=`` attributes never match.
    """
    text = _blank_comments(html_source)
    bindings: list[dict[str, Any]] = []
    for match in _EVENT_ATTR_RE.finditer(text):
        event = match.group(1) if match.group(1) is not None else match.group(2)
        expression = match.group(3) if match.group(3) is not None else match.group(4)
        line, column = _line_col(text, match.start())
        bindings.append({
            "event_name": event,
            "line": line,
            "column": column,
            "binding_expression": expression,
        })
    return bindings


def _handler_name(expression: str) -> str | None:
    """Handler method name for a template expression, or ``None``.

    Bare identifiers and member chains resolve to their last segment.  A
    single method call (the framework's canonical ``(click)="save($event)"``
    form) resolves to its callee; multi-statement expressions (``;``) do
    not.  Anything else is not a handler reference.
    """
    if _IDENTIFIER_RE.match(expression):
        return expression
    member = _MEMBER_RE.match(expression)
    if member:
        return member.group(1)
    if ";" not in expression:
        call = _CALL_RE.match(expression)
        if call:
            return call.group(1).rsplit(".", 1)[-1]
    return None


def resolve_angular_bindings(bindings: list[dict[str, Any]],
                             symbols: list[dict[str, Any]],
                             template_path: str,
                             component_path: str) -> list[dict[str, Any]]:
    """Resolve template handler expressions against component method symbols.

    ``symbols`` are the merged adapter symbols for the component ``.ts``
    file, each with ``name``, ``kind`` and ``line``.  Only ``method`` symbols
    are considered: a template can only invoke component members, so a
    same-named top-level function in the file is not a handler.  Resolution
    statuses mirror the JSX/Vue collectors: RESOLVED / AMBIGUOUS / UNKNOWN /
    INLINE_CALLBACK_NOT_SYMBOL_LINKED.
    """
    resolved: list[dict[str, Any]] = []
    methods = [s for s in symbols if s.get("kind") == "method"]
    for binding in bindings:
        expression = (binding.get("binding_expression") or "").strip()
        item: dict[str, Any] = dict(binding)
        item["resolution_status"] = "UNKNOWN"
        item["candidate_targets"] = []
        item["confidence"] = "STATIC_INFERENCE"
        item["verification_status"] = "NOT_VERIFIED"
        name: str | None = None
        if _ARROW_RE.match(expression) or expression.startswith("function"):
            item["resolution_status"] = "INLINE_CALLBACK_NOT_SYMBOL_LINKED"
        else:
            name = _handler_name(expression)
        if name is not None:
            matches = [s for s in methods if s.get("name") == name]
            unique = {
                f"symbol:{component_path}#{s.get('name')}:{s.get('line')}": s
                for s in matches
                if isinstance(s.get("name"), str) and isinstance(s.get("line"), int)
            }
            if len(unique) == 1:
                target = next(iter(unique.values()))
                target_id = next(iter(unique.keys()))
                item["resolution_status"] = "RESOLVED"
                item["target"] = {
                    "id": target_id,
                    "path": component_path,
                    "name": target.get("name"),
                    "line": target.get("line"),
                }
                item["evidence_chain"] = [
                    {"kind": "angular_template_event_attribute", "file": template_path,
                     "line": binding.get("line")},
                    {"kind": "handler_definition", "file": component_path,
                     "line": target.get("line"), "name": target.get("name")},
                ]
            elif len(unique) > 1:
                item["resolution_status"] = "AMBIGUOUS"
                item["candidate_targets"] = sorted(unique)
        resolved.append(item)
    return resolved
