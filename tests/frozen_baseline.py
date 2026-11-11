"""Versioned integrity helpers for historical and maintained test fixtures."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE_BASELINE_PATH = (
    PROJECT_ROOT / "outputs" / "p0710zxrxf" / "MAINTENANCE_BASELINE.json"
)


def git_blob_sha1(relative_path: str, base_dir: Path = PROJECT_ROOT) -> str | None:
    """Return the canonical tracked Git blob ID, independent of checkout EOLs."""
    result = subprocess.run(
        ["git", "show", f"HEAD:{relative_path}"],
        cwd=base_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return None
    data = result.stdout
    payload = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    return hashlib.sha1(payload).hexdigest()


def load_maintenance_baseline() -> dict:
    return json.loads(MAINTENANCE_BASELINE_PATH.read_text(encoding="utf-8"))


def maintenance_entry_matches(relative_path: str) -> bool:
    entry = load_maintenance_baseline().get("files", {}).get(relative_path, {})
    return (
        git_blob_sha1(relative_path) == entry.get("git_blob_sha1")
        and isinstance(entry.get("reason"), str)
        and bool(entry["reason"].strip())
    )
