"""Windows runner-user spawning (UID isolation E1, Windows only).

This module is the Windows counterpart of the POSIX ``subprocess.Popen(user=...)``
path in :mod:`usit.verification_capture`: it runs a command as a dedicated
low-privilege local Windows user through ``CreateProcessWithLogonW``
(``ctypes`` only, no new dependency).

Importing this module on a non-Windows platform raises ``ValueError``
immediately; callers must import it lazily inside the Windows branch only.

Mechanism note (Run #1563): the first implementation used
``LogonUser`` + ``CreateProcessAsUser``, but that needs
SeAssignPrimaryTokenPrivilege, which an administrator token does not
hold by default (it is granted only to LOCAL SERVICE / NETWORK
SERVICE), so the spawn failed with Win32 error 1300 on the CI runner.
``CreateProcessWithLogonW`` performs the logon itself and does not need
that privilege -- it is the same call ``runas`` uses, and it works from
an administrator parent with the target user's password.

Security rules (fail-closed, mirroring the POSIX path):

* The password is supplied by the caller (see ``spawn_as_user``); it is
  never written to disk, never added to the child environment, never put in
  provenance, logs or exception messages. A wrong or missing password fails
  the logon, never falls back to the parent identity.
* The user must already exist; the parent must run as an administrator.
  Any refusal raises ``ValueError`` before the process starts.
* The child gets no profile load (``LoadUserProfile`` is not called); the
  caller redirects HOME/USERPROFILE/TMP/TEMP into staging, as the POSIX
  path does with ``_minimal_environment``.
* This is an execution boundary, not a sandbox: the child can still read
  world-readable files, reach the network and consume host resources, so
  ``provenance_authenticity_status`` stays ``UNAUTHENTICATED``.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

if os.name != "nt":  # pragma: no cover - import guard, exercised on POSIX by test
    raise ValueError("usit.windows_runner requires a Windows platform")

# use_last_error=True is mandatory: _win32_error reads the Win32 last-error
# value through ctypes.get_last_error(). With use_errno only, every failure
# reported "Win32 error 0" (Run #1562) and the real cause was invisible.
# argtypes/restype are equally mandatory: without them ctypes passes
# Python str as ANSI bytes where LPWSTR is expected and truncates 64-bit
# HANDLE values to 32-bit int.
_advapi32 = ctypes.WinDLL("advapi32", use_errno=True, use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_errno=True, use_last_error=True)
_netapi32 = ctypes.WinDLL("netapi32", use_errno=True, use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_errno=True, use_last_error=True)

CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259
NERR_SUCCESS = 0

HANDLE = wintypes.HANDLE
DWORD = wintypes.DWORD
BOOL = wintypes.BOOL
LPWSTR = wintypes.LPWSTR


_advapi32.CreateProcessWithLogonW.argtypes = [LPWSTR, LPWSTR, LPWSTR, DWORD, LPWSTR, LPWSTR, DWORD, ctypes.c_void_p, LPWSTR, ctypes.c_void_p, ctypes.c_void_p]
_advapi32.CreateProcessWithLogonW.restype = BOOL
_kernel32.SetHandleInformation.argtypes = [HANDLE, DWORD, DWORD]
_kernel32.SetHandleInformation.restype = BOOL
_kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
_kernel32.WaitForSingleObject.restype = DWORD
_kernel32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
_kernel32.GetExitCodeProcess.restype = BOOL
_kernel32.TerminateProcess.argtypes = [HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = BOOL
_kernel32.CloseHandle.argtypes = [HANDLE]
_kernel32.CloseHandle.restype = BOOL
_netapi32.NetUserGetInfo.argtypes = [LPWSTR, LPWSTR, DWORD, ctypes.POINTER(ctypes.c_void_p)]
_netapi32.NetUserGetInfo.restype = DWORD
_netapi32.NetApiBufferFree.argtypes = [ctypes.c_void_p]
_netapi32.NetApiBufferFree.restype = DWORD
_shell32.IsUserAnAdmin.argtypes = []
_shell32.IsUserAnAdmin.restype = BOOL


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", LPWSTR),
        ("lpDesktop", LPWSTR),
        ("lpTitle", LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCount", DWORD),
        ("dwYCount", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    ]


def _win32_error(what: str) -> ValueError:
    code = ctypes.get_last_error()
    return ValueError(f"{what} failed: Win32 error {code}")


def is_parent_admin() -> bool:
    """True when the current process runs with administrator rights."""
    return bool(_shell32.IsUserAnAdmin())


def user_exists(username: str) -> bool:
    """True when a local user with this name exists (level 0 query)."""
    # NetUserGetInfo(servername=NULL, username, level=0, bufptr): level 0
    # returns USER_INFO_0; the buffer must be freed with NetApiBufferFree.
    buf = ctypes.c_void_p()
    status = _netapi32.NetUserGetInfo(None, username, 0, ctypes.byref(buf))
    if buf:
        _netapi32.NetApiBufferFree(buf)
    return status == NERR_SUCCESS


def _environment_block(env: dict[str, str] | None) -> ctypes.Array | None:
    if env is None:
        return None
    text = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0\0"
    return ctypes.create_unicode_buffer(text)


def _inheritable_handle(file_obj) -> int:
    """Return the raw Windows HANDLE for an open binary file object, marked inheritable."""
    import msvcrt

    handle = msvcrt.get_osfhandle(file_obj.fileno())
    if not _kernel32.SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT):
        raise _win32_error("marking the child stdio handle inheritable")
    return handle


class WindowsProcess:
    """A minimal Popen-like handle around a CreateProcessWithLogonW child.

    Only the operations :func:`usit.verification_capture.capture_run` needs
    are provided: :meth:`poll`, :meth:`terminate`, :meth:`kill`,
    :meth:`wait` (raises :class:`subprocess.TimeoutExpired` on timeout, like
    :class:`subprocess.Popen`) and the :attr:`returncode` attribute.
    """

    def __init__(self, process_handle: int, pid: int) -> None:
        self._handle = process_handle
        self.pid = pid
        self.returncode: int | None = None
        self._closed = False

    def _close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._handle:
                _kernel32.CloseHandle(self._handle)
                self._handle = 0

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        result = _kernel32.WaitForSingleObject(self._handle, 0)
        if result == WAIT_TIMEOUT:
            return None
        if result != WAIT_OBJECT_0:
            raise _win32_error("waiting on the runner child process")
        code = DWORD()
        if not _kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise _win32_error("reading the runner child exit code")
        if code.value == STILL_ACTIVE:
            return None
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        ms = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        result = _kernel32.WaitForSingleObject(self._handle, ms)
        if result == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(cmd="runner child", timeout=timeout)
        if result != WAIT_OBJECT_0:
            raise _win32_error("waiting on the runner child process")
        code = DWORD()
        if not _kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise _win32_error("reading the runner child exit code")
        self.returncode = code.value
        self._close()
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            if not _kernel32.TerminateProcess(self._handle, 1):
                raise _win32_error("terminating the runner child process")

    def kill(self) -> None:
        self.terminate()


def grant_modify_access(paths, username: str) -> None:
    """Grant a local user Modify on the given paths via icacls (fail-closed).

    Used for exactly the staging paths the isolated test process legitimately
    writes (the two pre-created output files and ``.usit-home``/``.usit-tmp``);
    everything else in staging stays owned by the parent. Raises ValueError
    when icacls is unavailable or exits non-zero; the username is not a
    secret and never appears in a password context.

    icacls takes exactly one file per invocation: a second file argument
    is rejected as an invalid parameter, so each path gets its own call.
    """
    import subprocess

    if not username or not isinstance(username, str):
        raise ValueError("grant_modify_access requires a non-empty username string")
    targets = [str(path) for path in paths]
    if not targets or any(not target for target in targets):
        raise ValueError("grant_modify_access requires at least one path")
    for target in targets:
        try:
            result = subprocess.run(
                ["icacls", target, "/grant", f"{username}:(M)"],
                capture_output=True, text=True, timeout=60,
            )
        except OSError as exc:
            raise ValueError(f"icacls grant failed for the runner user: {type(exc).__name__}") from exc
        if result.returncode != 0:
            detail = (result.stdout.strip() + " " + result.stderr.strip()).strip()
            raise ValueError(f"icacls grant failed for the runner user: {detail[:500]}")


def grant_read_access(paths, username: str, *, recursive: bool = False) -> None:
    """Grant a local user Read & Execute on the given paths via icacls (fail-closed).

    Used for the staging tree of a Windows isolated run: the runner user
    must be able to traverse into staging and read the staged sources
    (the two output files and HOME/TMPDIR get Modify separately, via
    :func:`grant_modify_access`). With ``recursive=True`` the grant covers
    the whole existing tree (icacls ``/T``); without it, only the named
    paths. Raises ValueError when icacls is unavailable or exits non-zero;
    the username is not a secret and never appears in a password context.
    """
    import subprocess

    if not username or not isinstance(username, str):
        raise ValueError("grant_read_access requires a non-empty username string")
    targets = [str(path) for path in paths]
    if not targets or any(not target for target in targets):
        raise ValueError("grant_read_access requires at least one path")
    for target in targets:
        command = ["icacls", target, "/grant", f"{username}:(RX)"]
        if recursive:
            command.append("/T")
        try:
            result = subprocess.run(
                command,
                capture_output=True, text=True, timeout=120,
            )
        except OSError as exc:
            raise ValueError(f"icacls grant failed for the runner user: {type(exc).__name__}") from exc
        if result.returncode != 0:
            detail = (result.stdout.strip() + " " + result.stderr.strip()).strip()
            raise ValueError(f"icacls grant failed for the runner user: {detail[:500]}")


def deny_write_access(paths, username: str, *, recursive: bool = False) -> None:
    """Deny a local user Write on the given paths via icacls (fail-closed).

    E2 read-only hardening for the staging tree: an explicit deny ACE
    beats any allow ACE, so a deny must never be placed on a path the
    runner user legitimately writes, and never with inheritance over
    such a path (see the design doc, section 14). Callers deny each
    staging entry except the four writable paths, one by one. With
    ``recursive=True`` the deny covers the whole existing subtree
    (icacls ``/T``). Raises ValueError when icacls is unavailable or
    exits non-zero; the username is not a secret and never appears in a
    password context.

    The denied rights are the granular write bits (wd,ad,wea,wa) --
    write data/add file, append data/add subdirectory, write extended
    attributes, write attributes -- and deliberately NOT the simple
    (W) right: (W) maps to generic write, which also carries the
    SYNCHRONIZE and READ_CONTROL bits, so denying (W) denies opening
    the file at all, even for reading. Run #1567 proved it: with a (W)
    deny, the child pytest died at plugin load with PermissionError in
    importlib get_data on _usit_runner_plugin.py. The granular bits
    block writes without touching opens for read.
    """
    import subprocess

    if not username or not isinstance(username, str):
        raise ValueError("deny_write_access requires a non-empty username string")
    targets = [str(path) for path in paths]
    if not targets or any(not target for target in targets):
        raise ValueError("deny_write_access requires at least one path")
    for target in targets:
        command = ["icacls", target, "/deny", f"{username}:(wd,ad,wea,wa)"]
        if recursive:
            command.append("/T")
        try:
            result = subprocess.run(
                command,
                capture_output=True, text=True, timeout=120,
            )
        except OSError as exc:
            raise ValueError(f"icacls deny failed for the runner user: {type(exc).__name__}") from exc
        if result.returncode != 0:
            detail = (result.stdout.strip() + " " + result.stderr.strip()).strip()
            raise ValueError(f"icacls deny failed for the runner user: {detail[:500]}")


def spawn_as_user(command, *, cwd, env, stdout_file, stderr_file, username, password) -> WindowsProcess:
    """Spawn ``command`` (argv list) as a dedicated local Windows user.

    Uses ``CreateProcessWithLogonW`` (the call ``runas`` uses): it performs
    the logon itself from ``username``/``password`` (domain ``"."``, the
    local machine; logon flags 0, so no user profile is loaded) and does
    not need SeAssignPrimaryTokenPrivilege in the parent token -- the
    privilege an administrator token does not hold by default, which is
    what blocked the earlier LogonUser + CreateProcessAsUser path
    (Run #1563, Win32 error 1300). ``stdout_file`` / ``stderr_file`` are
    open binary file objects the child writes to; stdin is the NUL device.
    The password is never logged or stored. Returns a :class:`WindowsProcess`.
    """
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must be a non-empty sequence of non-empty strings")
    if not isinstance(username, str) or not username:
        raise ValueError("runner username must be a non-empty string")
    if not isinstance(password, str) or not password:
        raise ValueError("runner password must be a non-empty string")
    from subprocess import list2cmdline

    command_line = ctypes.create_unicode_buffer(list2cmdline(list(command)))
    env_block = _environment_block(env)
    # The stdin handle must be a real Windows HANDLE, not the CRT file
    # descriptor os.open returns: STARTUPINFO takes HANDLEs, and a small
    # integer fd is either invalid or, worse, a different kernel handle.
    import msvcrt

    stdin_fd = os.open(os.devnull, os.O_RDONLY)
    try:
        stdin_handle = msvcrt.get_osfhandle(stdin_fd)
        if not _kernel32.SetHandleInformation(stdin_handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT):
            raise _win32_error("marking the child stdin handle inheritable")
        stdout_handle = _inheritable_handle(stdout_file)
        stderr_handle = _inheritable_handle(stderr_file)
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = STARTF_USESTDHANDLES
        startup.hStdInput = stdin_handle
        startup.hStdOutput = stdout_handle
        startup.hStdError = stderr_handle
        info = PROCESS_INFORMATION()
        flags = CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
        ok = _advapi32.CreateProcessWithLogonW(
            username, ".", password, 0, None, command_line, flags,
            env_block, str(cwd), ctypes.byref(startup), ctypes.byref(info),
        )
        if not ok:
            # Name the working directory in the failure: CreateProcessWithLogonW
            # reports an unusable current directory only as a bare Win32 code
            # (Run #1564: error 267 for a staging dir under the parent user's
            # profile Temp, which the runner user cannot traverse), and the
            # directory is the first thing to check. The cwd holds no secret.
            raise _win32_error(f"spawning the runner child process (cwd={cwd})")
        try:
            _kernel32.CloseHandle(info.hThread)
        except OSError:
            pass
        return WindowsProcess(info.hProcess, info.dwProcessId)
    finally:
        try:
            os.close(stdin_fd)
        except OSError:
            pass
