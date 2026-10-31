"""argv-only subprocess worker for isolated Python analysis and cross-file resolution (P0.7.4).

Invoked as ``python python_worker.py {analyze|resolve}`` with one JSON document
on stdin and one JSON document on stdout, under ``ParserTaskSupervisor``.
The worker never imports, evaluates or executes any project code: symbol
extraction uses ``ast.parse`` (parse only) plus regular expressions over the
*content supplied by the parent*, never by opening a project file itself.  This
keeps every project-file read inside the parent's audited, no-follow
``open_input_file`` and makes the work killable (a stuck task is a child process
the supervisor can kill and verify, not an uninterruptible thread).

Actions
-------
``analyze``
    stdin ``{"root": str, "files": [{"path": rel, "content_b64": str}, ...]}``
    stdout ``{"rows": [ ...analyze_file rows... ]}``
``resolve``
    stdin ``{"root": str, "rows": [...], "contents": {rel: text}}``
    stdout ``{"summary": {...}, "rows": [...mutated in place...]}``
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path, PurePosixPath

# The worker is launched by absolute path, so ``usit`` may not be importable
# from the default sys.path; add the package parent explicitly.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _analyze(payload: dict) -> dict:
    """Analyze supplied file contents; the project tree is never opened here."""
    from usit.scanner import analyze_file

    root = Path(payload["root"])
    rows = []
    for item in payload.get("files", []):
        rel = item["path"]
        raw = base64.b64decode(item["content_b64"])
        path = root.joinpath(*PurePosixPath(rel).parts)
        rows.append(analyze_file(root, path, raw_bytes=raw))
    return {"rows": rows}


def _resolve(payload: dict) -> dict:
    """Resolve cross-file Python calls from supplied content, never from disk."""
    from usit.python_resolver import resolve_python_calls

    rows = payload["rows"]
    summary = resolve_python_calls(payload["root"], rows, contents=payload.get("contents"))
    return {"summary": summary, "rows": rows}


def main(argv: list[str]) -> int:
    """Run one worker action and emit its JSON result. Never raises to the caller."""
    action = argv[1] if len(argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        sys.stderr.write("python_worker: invalid JSON input\n")
        return 2
    if not isinstance(payload, dict):
        sys.stderr.write("python_worker: input must be a JSON object\n")
        return 2
    try:
        if action == "analyze":
            result = _analyze(payload)
        elif action == "resolve":
            result = _resolve(payload)
        else:
            sys.stderr.write("python_worker: unknown action %r\n" % (action,))
            return 2
    except Exception as exc:  # noqa: BLE001 - surfaced to the parent as a crash
        sys.stderr.write("python_worker: %s: %s\n" % (type(exc).__name__, exc))
        return 3
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
