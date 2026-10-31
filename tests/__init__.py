"""Test package for USIT.

The synthetic projects created by the test-suite live under
``tempfile.gettempdir()``.  On Windows that directory can be reported through an
8.3 short name (for example ``C:\\Users\\ADMINI~1\\AppData\\Local\\Temp``) while the
scanner canonicalizes every project root with ``Path.resolve()``, which expands
short names to their long form.  Comparing a canonical path with an unresolved
short-named root then raises ``ValueError`` and the tests fail for a reason that
has nothing to do with the code under test.

Pinning ``tempfile.tempdir`` to the resolved (long) form once, at import time,
keeps every synthetic-project test independent of the host's short-name
configuration.  It changes only where temporary directories are created; it does
not relax any scanner limit or safety gate.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

_current = tempfile.gettempdir()
_resolved = str(Path(_current).resolve())
if _resolved != _current:
    tempfile.tempdir = _resolved
