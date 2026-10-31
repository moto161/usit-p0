"""Fail-closed, no-follow file opening for the scanner (P0.7.4).

The scanner must never follow a symbolic link, junction or reparse point into a
file outside the authorized project, and must never read a file that was
substituted after discovery.  ``open_no_follow`` returns a descriptor bound to
the opened file object using the strongest primitive each platform offers:

* **Windows** — ``CreateFileW`` with ``FILE_FLAG_OPEN_REPARSE_POINT`` (the final
  path component is opened *without* following its reparse point) and a share
  mode that requests read access only, so other processes may not write to or
  delete/rename the file while the handle is held.  The Win32 handle is wrapped
  with ``msvcrt.open_osfhandle`` so the caller can use ``os.fstat``/``os.read``.
* **POSIX** — ``os.open`` with ``O_NOFOLLOW``, which fails (``ELOOP``) instead of
  following a symbolic link in the final component.

Neither path resolves the path a second time after the descriptor exists; every
later check reads only from the returned descriptor.  No project code is
imported or executed.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Windows file attribute bit set on symbolic links, junctions and reparse points.
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

if os.name == "nt":  # pragma: no cover - exercised only on Windows
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.restype = ctypes.c_void_p
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    def open_no_follow(path: Path) -> int:
        """Open ``path`` without following the final component's reparse point.

        Returns an OS file descriptor.  Raises ``OSError`` on failure; the
        caller maps that to a fail-closed ``ScanBlocked``.  The share mode
        requests read access only, so a concurrent writer or ``DeleteFile`` is
        denied while the descriptor is open.
        """
        handle = _kernel32.CreateFileW(
            str(path), _GENERIC_READ, _FILE_SHARE_READ, None, _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS, None,
        )
        if not handle or handle == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            # The CRT did not take ownership of the handle; close it here so it
            # cannot leak if open_osfhandle fails.
            _kernel32.CloseHandle(handle)
            raise

else:
    def open_no_follow(path: Path) -> int:
        """Open ``path`` with ``O_NOFOLLOW`` so a final-component link fails."""
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        return os.open(str(path), flags)


def attributes_of(info: os.stat_result) -> int:
    """Return the raw file-attribute bitmask, or ``0`` where unsupported."""
    return int(getattr(info, "st_file_attributes", 0) or 0)


def is_reparse(info: os.stat_result) -> bool:
    """True when a stat result describes a link/junction/reparse point."""
    return bool(attributes_of(info) & FILE_ATTRIBUTE_REPARSE_POINT)


def identity_of(info: os.stat_result) -> tuple[int, int]:
    """Return the ``(st_dev, st_ino)`` file identity used to detect substitution."""
    return (int(info.st_dev), int(info.st_ino))
