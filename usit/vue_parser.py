"""Vue single-file component helpers for ring 2 UI-entry discovery.

A Vue SFC is not TypeScript: its ``<template>`` block is HTML-like and its
``<script>`` block is JS/TS.  The scanner feeds only the script block (line
padded, so adapter line numbers stay file-relative) to the TypeScript adapter,
and this module extracts the template event bindings (``@event`` /
``v-on:event``) and resolves their handler expressions against the script's
symbols.  Everything here is pure text processing over parent-verified
content; no file is opened and nothing is executed.
"""

from __future__ import annotations

import re
from typing import Any

_SCRIPT_OPEN_RE = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_SCRIPT_CLOSE_RE = re.compile(r"</script\s*>", re.IGNORECASE)
_TEMPLATE_OPEN_RE = re.compile(r"<template\b[^>]*>", re.IGNORECASE)
_TEMPLATE_CLOSE_RE = re.compile(r"</template\s*>", re.IGNORECASE)
_LANG_RE = re.compile(r"""lang\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_SETUP_RE = re.compile(r"(?:^|\s)setup(?:\s|=|/|$)", re.IGNORECASE)
# @click[.modifiers]="handler" or v-on:click[.modifiers]='handler'.
# The event name is any identifier (DOM events and custom component events
# alike, including colon names such as update:model-value); modifiers are
# accepted and ignored.  A binding must carry a quoted value, so "@" in
# plain text never matches.
_EVENT_ATTR_RE = re.compile(
    r"""(?:@|v-on:)([A-Za-z][\w-]*(?::[\w-]+)?)((?:\.[\w-]+)*)\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_MEMBER_RE = re.compile(r"^(?:[A-Za-z_$][\w$]*\.)*([A-Za-z_$][\w$]*)$")
_ARROW_RE = re.compile(r"^(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")


def _line_col(content: str, offset: int) -> tuple[int, int]:
    line = content.count("\n", 0, offset) + 1
    last_nl = content.rfind("\n", 0, offset)
    return line, offset - last_nl


def extract_vue_script(content: str) -> dict[str, Any] | None:
    """Return the first ``<script setup>`` block, else the first ``<script>``.

    Result: ``{"text", "start_line", "lang"}`` where ``start_line`` is the
    1-based line where the script text begins (the newline right after the
    opening tag counts as that line, so line-padding the text reproduces the
    file's line numbers).  ``None`` when there is no script block.  When a
    file carries both a plain and a setup script block, only the setup block
    is returned (documented V1 limitation).
    """
    opens = list(_SCRIPT_OPEN_RE.finditer(content))
    if not opens:
        return None
    chosen = next((m for m in opens if _SETUP_RE.search(m.group(1) or "")), opens[0])
    attrs = chosen.group(1) or ""
    lang_match = _LANG_RE.search(attrs)
    close = _SCRIPT_CLOSE_RE.search(content, chosen.end())
    end = close.start() if close else len(content)
    text = content[chosen.end():end]
    start_line = content.count("\n", 0, chosen.end()) + 1
    return {"text": text, "start_line": start_line,
            "lang": (lang_match.group(1).lower() if lang_match else "js")}


def vue_script_for_adapter(content: str) -> str | None:
    """Line-padded script text for the TypeScript adapter, or ``None``.

    Leading newlines make the adapter's line numbers equal the file's line
    numbers, so its symbols and calls land on the right ``.vue`` lines.
    """
    script = extract_vue_script(content)
    if script is None:
        return None
    return "\n" * (script["start_line"] - 1) + script["text"]


def extract_vue_template_bindings(content: str) -> list[dict[str, Any]]:
    """Extract ``@event`` / ``v-on:event`` bindings from the ``<template>`` block.

    Only the template block is scanned, so ``@`` in script or style text never
    matches.  Returns ``{"event_name", "line", "column", "binding_expression"}``
    per binding, in file order.
    """
    template_open = _TEMPLATE_OPEN_RE.search(content)
    if template_open is None:
        return []
    close = _TEMPLATE_CLOSE_RE.search(content, template_open.end())
    end = close.start() if close else len(content)
    bindings: list[dict[str, Any]] = []
    for match in _EVENT_ATTR_RE.finditer(content, template_open.end(), end):
        expression = match.group(3) if match.group(3) is not None else match.group(4)
        line, column = _line_col(content, match.start())
        bindings.append({
            "event_name": match.group(1),
            "line": line,
            "column": column,
            "binding_expression": expression,
        })
    return bindings


def resolve_vue_bindings(bindings: list[dict[str, Any]],
                         symbols: list[dict[str, Any]],
                         path: str) -> list[dict[str, Any]]:
    """Resolve template handler expressions against same-file script symbols.

    ``symbols`` are the merged adapter symbols for the file, each with
    ``name``, ``symbol_id`` and ``line``.  Only same-file definitions are
    considered; an imported handler name never matches (fail-closed UNKNOWN).
    Resolution statuses mirror the JSX collector: RESOLVED / AMBIGUOUS /
    UNKNOWN / INLINE_CALLBACK_NOT_SYMBOL_LINKED.
    """
    resolved: list[dict[str, Any]] = []
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
        elif _IDENTIFIER_RE.match(expression):
            name = expression
        else:
            member = _MEMBER_RE.match(expression)
            if member:
                name = member.group(1)
        if name is not None:
            matches = [s for s in symbols if s.get("name") == name]
            unique = {str(s.get("symbol_id")): s for s in matches if s.get("symbol_id")}
            if len(unique) == 1:
                target = next(iter(unique.values()))
                item["resolution_status"] = "RESOLVED"
                item["target"] = {
                    "id": target.get("symbol_id"),
                    "path": path,
                    "name": target.get("name"),
                    "line": target.get("line"),
                }
                item["evidence_chain"] = [
                    {"kind": "vue_template_event_attribute", "file": path,
                     "line": binding.get("line")},
                    {"kind": "handler_definition", "file": path,
                     "line": target.get("line"), "name": target.get("name")},
                ]
            elif len(unique) > 1:
                item["resolution_status"] = "AMBIGUOUS"
                item["candidate_targets"] = sorted(unique)
        resolved.append(item)
    return resolved
