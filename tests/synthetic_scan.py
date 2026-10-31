"""Explicit authorization helper for artificial test fixtures only."""
from datetime import datetime, timezone
from pathlib import Path
from usit.scanner import scan_project as _scan_project
from usit.safety import authorization_record

def scan_project(project, out_dir, force=False):
    root=Path(project).resolve(strict=True); out=Path(out_dir).resolve()
    auth=authorization_record(root,out,now=datetime.now(timezone.utc))
    return _scan_project(str(root),str(out),force=force,authorization=auth)
