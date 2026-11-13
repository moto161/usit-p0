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


def find_angular_components(ts_source: str) -> list[dict[str, Any]]:
    """Find ``@Component`` classes with a literal external ``templateUrl``.

    Returns ``{"class_name", "template_url", "line"}`` per component, in
    file order.  Components without a literal ``templateUrl`` (inline
    ``template:`` or no template) are skipped: inline templates are a
    documented A1 limitation, not a failed parse.
    """
    components: list[dict[str, Any]] = []
    for match in _COMPONENT_RE.finditer(ts_source):
        open_paren = ts_source.find("(", match.end())
        if open_paren < 0:
            continue
        close = _scan_balanced_parens(ts_source, open_paren)
        if close is None:
            continue
        decorator = ts_source[open_paren:close]
        url_match = _TEMPLATE_URL_RE.search(decorator)
        if url_match is None:
            continue
        template_url = url_match.group(1) if url_match.group(1) is not None else url_match.group(2)
        if not template_url:
            continue
        class_match = _CLASS_RE.search(ts_source, close, close + 1000)
        if class_match is None:
            continue
        line, _column = _line_col(ts_source, match.start())
        components.append({
            "class_name": class_match.group(1),
            "template_url": template_url,
            "line": line,
        })
    return components


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
