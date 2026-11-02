"""Trusted line-count snapshots (P0.7.10-Z).

Why this module exists
----------------------
P0.7.10-X made the scan pipeline hand ``validate_graph`` a mapping of
project-relative path -> line count so the validator would stop re-opening
project sources.  P0.7.10-YR then showed that the *value* being passed was still
a bare ``dict``: any caller could pass ``{"app.py": 1000000}"`` and silently
suppress a genuine ``LINE_OUT_OF_RANGE`` (YR2a), and a genuine mapping went
stale the moment a file changed (YR3a/YR3b).  A plain mapping therefore carries
no information about **who produced it** or **which bytes it describes**.

This module makes that provenance explicit.  A :class:`LineCountSnapshot` can
only be produced by :func:`capture`, which computes the line counts itself from
bytes supplied by the caller's controlled capture path.  There is deliberately
**no constructor, no ``from_dict``, no ``from_json`` and no unpickling path**:
a ``dict`` read out of a graph file, a JSON payload, or an unpickled object can
never be mistaken for a trusted snapshot.

What a snapshot does and does not claim
---------------------------------------
A snapshot describes *the content that was captured*.  It does **not** claim
that the file on disk still has that content.  Identity metadata
(``st_dev``/``st_ino``/``st_size``/``st_mtime_ns``) is recorded so that a later
:meth:`LineCountSnapshot.verified_mapping` can *detect* a replacement -- but
metadata is a cheap substitution detector, **not** a content proof: an attacker
who preserves size and timestamp exactly is not caught by it.  Callers that need
content-level assurance must re-capture, not re-verify.

No cryptography is used here on purpose.  Signing a snapshot would need key
management and a verification protocol that this project does not have; inventing
one would be worse than documenting the absence.  Cross-process provenance is
therefore *not established* -- see :class:`LineCountSnapshot` docs.

Public surface
--------------
``capture(root, contents, *, origin=...) -> LineCountSnapshot``
``LineCountSnapshot`` properties/methods: ``root``, ``origin``, ``version``,
``created_at_utc``, ``state``, ``paths()``, ``line_count(rel)``,
``as_mapping()``, ``verified_mapping(root=None)``, ``integrity(root=None)``,
``belongs_to(root)``, ``close()``,
``verify_current_content(rel, root=None) -> ContentVerifyResult``.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from usit import safeio

__all__ = ["SNAPSHOT_VERSION", "DescriptorIdentity", "FileRecord", "LineCountSnapshot",
           "ContentVerifyResult", "capture"]

#: A captured digest must be a 64-character lowercase-or-uppercase hex SHA-256.
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

# P0.7.10-ZRF note: this stays 1 on purpose.  The ZRF change is additive
# metadata (identity_source, optional mtime) and the version is asserted by
# usit/graph.py's validate_graph_trusted(), which this ticket may not modify.
SNAPSHOT_VERSION = 1

# Construction token.  ``LineCountSnapshot.__init__`` refuses to build an
# instance unless it is handed this exact object, so the only way to obtain a
# snapshot is the module-level ``capture`` factory below.  This defends against
# accidental misuse (a caller passing a dict, a JSON blob, or a copy); it is not
# a cryptographic guarantee against code that deliberately reaches into this
# module's internals, which would require the same access needed to edit the
# module itself.
_CONSTRUCTION_TOKEN = object()


class DescriptorIdentity:
    """Identity metadata taken from the file descriptor that was actually read.

    Instances are produced by the capture pipeline (``open_input_file`` records
    the ``os.fstat`` of the audited descriptor) and handed to :func:`capture`.
    ``capture()`` cannot verify, in-process, that a given instance really came
    from that descriptor -- same-process forgery is out of scope by design, as
    the module docstring states.  What this class buys is a *binding*: the
    identity travels with the bytes it describes instead of being re-derived
    from whatever happens to be at the path later.

    Args:
        dev: ``st_dev`` of the audited descriptor.
        ino: ``st_ino`` of the audited descriptor.
        size: ``st_size`` of the audited descriptor; must equal the length of
            the captured bytes (checked by :func:`capture`).
        mtime_ns: ``st_mtime_ns`` of the audited descriptor, or ``None`` when the
            host does not expose it (such records never compare mtime).
    """

    __slots__ = ("dev", "ino", "size", "mtime_ns")

    def __init__(self, dev: int, ino: int, size: int, mtime_ns: int | None) -> None:
        object.__setattr__(self, "dev", int(dev))
        object.__setattr__(self, "ino", int(ino))
        object.__setattr__(self, "size", int(size))
        object.__setattr__(self, "mtime_ns", None if mtime_ns is None else int(mtime_ns))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"DescriptorIdentity is immutable (refused to set {name!r})")

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, DescriptorIdentity)
                and (self.dev, self.ino, self.size, self.mtime_ns)
                == (other.dev, other.ino, other.size, other.mtime_ns))

    def __hash__(self) -> int:
        return hash((self.dev, self.ino, self.size, self.mtime_ns))

    def __repr__(self) -> str:                                 # pragma: no cover
        return (f"DescriptorIdentity(dev={self.dev}, ino={self.ino}, "
                f"size={self.size}, mtime_ns={self.mtime_ns})")


class FileRecord:
    """Immutable per-file capture record.

    Args:
        rel: Normalised project-relative POSIX path (the mapping key).
        line_count: Number of lines computed from the captured bytes.
        size: Expected on-disk size -- ``len(payload)`` for both identity
            sources (for descriptor records this is cross-checked against the
            recorded ``st_size``).
        mtime_ns: ``st_mtime_ns`` bound to this record; ``None`` when no trusted
            observation exists (such records never compare mtime).
        dev: ``st_dev`` of the bound identity.
        ino: ``st_ino`` of the bound identity.
        digest: SHA-256 of the captured bytes, for auditability only.
        identity_available: False when no identity could be bound; such records
            are always treated as unverified.
        identity_source: ``"descriptor"`` when the identity came from the audited
            file descriptor via the capture pipeline, ``"self_observed"``
            otherwise (see :func:`capture`).
    """

    __slots__ = ("rel", "line_count", "size", "mtime_ns", "dev", "ino",
                 "digest", "identity_available", "identity_source")

    def __init__(self, rel: str, line_count: int, size: int, mtime_ns: int | None,
                 dev: int, ino: int, digest: str, identity_available: bool,
                 identity_source: str) -> None:
        object.__setattr__(self, "rel", rel)
        object.__setattr__(self, "line_count", line_count)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "mtime_ns", mtime_ns)
        object.__setattr__(self, "dev", dev)
        object.__setattr__(self, "ino", ino)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "identity_available", identity_available)
        object.__setattr__(self, "identity_source", identity_source)

    def __setattr__(self, name: str, value: Any) -> None:      # pragma: no cover
        raise AttributeError(f"FileRecord is immutable (refused to set {name!r})")

    def __delattr__(self, name: str) -> None:                  # pragma: no cover
        raise AttributeError(f"FileRecord is immutable (refused to delete {name!r})")

    def as_dict(self) -> dict[str, Any]:
        """Return a plain ``dict`` copy of this record (plain data, not trusted)."""
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self) -> str:                                 # pragma: no cover
        return (f"FileRecord(rel={self.rel!r}, line_count={self.line_count}, "
                f"size={self.size}, identity_available={self.identity_available})")


@dataclass(frozen=True)
class ContentVerifyResult:
    """Frozen outcome of :meth:`LineCountSnapshot.verify_current_content`.

    ``MATCH`` means the content read *during this call* hashed to the captured
    digest.  ``CHANGED`` means it was read successfully but the digest differs.
    ``UNAVAILABLE`` means the check could not be completed reliably; it is
    **never** ``MATCH``.  ``captured_digest``/``current_digest`` are SHA-256
    hashes (never file content); ``identity`` is a ``(st_dev, st_ino)`` pair, not
    a path, so it cannot leak a sensitive absolute location.
    """

    rel: str
    status: str
    captured_digest: str | None
    current_digest: str | None
    reason: str | None
    identity: tuple[int, int] | None


class LineCountSnapshot:
    """An immutable set of line counts captured from audited bytes.

    Instances are created only by :func:`capture`.  Copying, pickling and
    deep-copying are refused so that a trusted snapshot cannot be smuggled
    across a process boundary while keeping its trusted status.

    Attributes:
        root: The project root the bytes were captured from.
        origin: Free-text provenance label supplied by the capturing pipeline
            (e.g. ``"scan"``).  Informational only; it is not a signature.
        version: :data:`SNAPSHOT_VERSION` of the producing implementation.
        created_at_utc: ISO-8601 UTC timestamp of capture.
        state: ``"open"`` until :meth:`close` is called, then ``"closed"``.
    """

    __slots__ = ("_root", "_root_identity", "_files", "_origin", "_created_at_utc",
                 "_version", "_closed")

    def __init__(self, token: object, *, root: str, root_identity: tuple[int, int] | None,
                 files: Mapping[str, FileRecord], origin: str, version: int) -> None:
        if token is not _CONSTRUCTION_TOKEN:
            raise TypeError(
                "LineCountSnapshot cannot be constructed directly; obtain one from "
                "usit.snapshot.capture(). Plain dicts, graph fields and deserialised "
                "JSON are not trusted snapshots.")
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "_root_identity", root_identity)
        object.__setattr__(self, "_files", MappingProxyType(dict(files)))
        object.__setattr__(self, "_origin", origin)
        object.__setattr__(self, "_created_at_utc",
                           datetime.datetime.now(datetime.timezone.utc).isoformat())
        object.__setattr__(self, "_version", version)
        object.__setattr__(self, "_closed", False)

    # -- immutability & non-transportability -------------------------------
    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"LineCountSnapshot is immutable (refused to set {name!r}); "
            "capture a new snapshot instead")

    def __delattr__(self, name: str) -> None:                  # pragma: no cover
        raise AttributeError(f"LineCountSnapshot is immutable (refused to delete {name!r})")

    def __reduce__(self):
        raise TypeError(
            "LineCountSnapshot cannot be pickled: trusted status does not survive "
            "serialisation. Re-capture in the target process instead.")

    def __copy__(self):
        raise TypeError("LineCountSnapshot cannot be copied; trusted status is not copyable")

    def __deepcopy__(self, memo):
        raise TypeError("LineCountSnapshot cannot be copied; trusted status is not copyable")

    # -- read-only properties ---------------------------------------------
    @property
    def root(self) -> str:
        """The project root the snapshot was captured from (absolute, resolved)."""
        return self._root

    @property
    def origin(self) -> str:
        """Informational provenance label supplied by the capturing pipeline."""
        return self._origin

    @property
    def version(self) -> int:
        """Snapshot format version of the producing implementation."""
        return self._version

    @property
    def created_at_utc(self) -> str:
        """ISO-8601 UTC capture timestamp."""
        return self._created_at_utc

    @property
    def state(self) -> str:
        """``"open"`` or ``"closed"``."""
        return "closed" if self._closed else "open"

    def __len__(self) -> int:
        return len(self._files)

    def __repr__(self) -> str:                                 # pragma: no cover
        return (f"LineCountSnapshot(root={self._root!r}, origin={self._origin!r}, "
                f"files={len(self._files)}, state={self.state})")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        """Invalidate the snapshot.  A closed snapshot is rejected by consumers."""
        object.__setattr__(self, "_closed", True)

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("the snapshot has been closed; capture a new one")

    # -- data access -------------------------------------------------------
    def paths(self) -> tuple[str, ...]:
        """Return the normalised relative paths in sorted order."""
        return tuple(sorted(self._files))

    def line_count(self, rel: str) -> int:
        """Return the captured line count for ``rel``.

        Args:
            rel: Normalised project-relative POSIX path.

        Returns:
            The line count computed from the captured bytes.

        Raises:
            KeyError: ``rel`` was not part of this capture.
        """
        return self._files[rel].line_count

    def as_mapping(self) -> dict[str, int]:
        """Return a plain ``dict`` copy of path -> line count.

        The result is **plain data**.  It carries no provenance and must not be
        presented as trusted to any strict entry point; it exists so a caller can
        publish the counts as an ordinary artifact field (the graph's legacy
        ``source_line_counts``).
        """
        return {rel: rec.line_count for rel, rec in self._files.items()}

    def records(self) -> Mapping[str, FileRecord]:
        """Read-only view of the capture records (immutable records)."""
        return self._files

    # -- verification (metadata only, never reads content) -----------------
    def belongs_to(self, root: str | os.PathLike[str]) -> bool:
        """True when ``root`` is the same directory this snapshot was captured from."""
        if self._root_identity is None:
            return os.path.realpath(str(root)) == self._root
        try:
            st = os.stat(str(root))
        except OSError:
            return False
        return (int(st.st_dev), int(st.st_ino)) == self._root_identity

    def _classify(self, rel: str, root: str) -> tuple[str, os.stat_result | None]:
        rec = self._files[rel]
        if not rec.identity_available:
            return "IDENTITY_UNAVAILABLE", None
        try:
            st = os.stat(os.path.join(root, *PurePosixPath(rel).parts),
                         follow_symlinks=False)
        except OSError:
            return "MISSING", None
        if (int(st.st_dev) != rec.dev or int(st.st_ino) != rec.ino
                or int(st.st_size) != rec.size):
            return "DRIFTED", st
        if rec.mtime_ns is not None and int(st.st_mtime_ns) != rec.mtime_ns:
            return "DRIFTED", st
        return "OK", st

    def verified_mapping(self, root: str | os.PathLike[str] | None = None) -> dict[str, int]:
        """Return path -> line count for the files whose identity still matches.

        Files that are missing, were replaced/rewritten, or carry no identity
        metadata are **omitted**.  Omitting a key makes the validator emit
        ``*_LINE_UNVERIFIED`` for that file without ever falling back to reading
        it from disk, so drift degrades to UNVERIFIED rather than to a wrong
        verdict.

        Args:
            root: Root to verify against; defaults to :attr:`root`.

        Returns:
            A plain ``dict`` of the still-verifiable subset.

        Raises:
            ValueError: the snapshot was closed.
        """
        self._require_open()
        base = str(root) if root is not None else self._root
        out: dict[str, int] = {}
        for rel, rec in self._files.items():
            status, _ = self._classify(rel, base)
            if status == "OK":
                out[rel] = rec.line_count
        return out

    def integrity(self, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        """Summarise how the captured files look now, using metadata only.

        Args:
            root: Root to verify against; defaults to :attr:`root`.

        Returns:
            ``{"summary": {status: count}, "files": {rel: status},
            "root_matches": bool}`` where status is one of ``OK``, ``DRIFTED``,
            ``MISSING``, ``IDENTITY_UNAVAILABLE``.

        Raises:
            ValueError: the snapshot was closed.
        """
        self._require_open()
        base = str(root) if root is not None else self._root
        per: dict[str, str] = {}
        summary: dict[str, int] = {"OK": 0, "DRIFTED": 0, "MISSING": 0,
                                   "IDENTITY_UNAVAILABLE": 0}
        for rel in self._files:
            status, _ = self._classify(rel, base)
            per[rel] = status
            summary[status] += 1
        return {"summary": summary, "files": per, "root_matches": self.belongs_to(base)}

    # -- content verification (scheme B, opt-in, read the disk on demand) -----
    def verify_current_content(self, rel: str,
                              root: str | os.PathLike[str] | None = None
                              ) -> ContentVerifyResult:
        """Re-read the current file and check it still holds the captured content.

        This is the opt-in **scheme B** check.  Unlike :meth:`verified_mapping`
        and :meth:`integrity`, which rely on metadata alone (``dev``/``ino``/
        ``size``/``mtime_ns`` and never read content), this method opens the
        current file through a no-follow descriptor -- reusing :mod:`usit.safeio`
        so symlinks, junctions and reparse points are handled exactly as the
        production scanner expects -- and compares a freshly computed SHA-256 of
        the bytes actually read with the digest captured at :func:`capture` time
        (``FileRecord.digest``).

        It closes the ZRF-12 blind spot: a same-inode, same-size, same-mtime_ns
        in-place rewrite is invisible to the metadata checks but is caught here
        because the *content* is re-read and hashed.

        Args:
            rel: Normalised project-relative POSIX path of the file to verify.
            root: Root to read from; defaults to :attr:`root`.

        Returns:
            A frozen :class:`ContentVerifyResult`:
              * ``MATCH`` -- the captured digest exists and is well-formed, the
                file was read through a stable no-follow descriptor, and the
                current digest equals it.
              * ``CHANGED`` -- the file was read successfully but the current
                digest differs from the captured digest.
              * ``UNAVAILABLE`` -- the check could not be completed reliably
                (path not in this capture, captured digest missing/malformed,
                open/read failure, path escape, symlink/reparse bypass, path
                replacement, or identity change during the read).  ``UNAVAILABLE``
                is **never** ``MATCH``.

        Raises:
            ValueError: ``rel`` is not a usable normalised relative path, or the
                snapshot was closed.
            TypeError: ``rel`` is not a ``str``.

        Note:
            This method never mutates the snapshot, the captured ``digest``, or
            the source file.  A ``MATCH`` attests only that the content read
            *during this call* matched the captured digest; it makes no promise
            about the file afterwards and must not be presented as a permanent
            guarantee or as proof against every concurrent-modification scenario.
            It is never invoked by the scan pipeline.
        """
        self._require_open()
        norm = _normalise_rel(rel)  # raises TypeError/ValueError on bad input
        base = os.path.realpath(str(root)) if root is not None else self._root
        rec = self._files.get(norm)
        captured = rec.digest if rec is not None else None
        if rec is None:
            return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                      "path not part of this capture", None)
        # The captured digest must be a valid SHA-256 hex string.  A missing or
        # malformed digest can never yield MATCH -- there is nothing to compare
        # against, so the honest answer is UNAVAILABLE, not a content claim.
        if not isinstance(captured, str) or not _HEX64.match(captured):
            return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                      "captured digest missing or malformed", None)

        path = os.path.join(base, *PurePosixPath(norm).parts)
        try:
            fd = safeio.open_no_follow(Path(path))
        except OSError as exc:
            return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                      f"open failed: {type(exc).__name__}", None)
        try:
            try:
                st1 = os.fstat(fd)
            except OSError as exc:
                return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                          f"fstat failed: {type(exc).__name__}", None)
            # Reject a reparse point / non-regular file.  On Windows
            # open_no_follow opens the reparse point itself (it does not follow
            # it); the production pipeline's safeio rejects such objects, and so
            # do we.  On POSIX open_no_follow raises ELOOP for a final-component
            # symlink before we ever reach here.
            if safeio.is_reparse(st1):
                return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                          "path is a reparse point, not a regular file",
                                          None)
            # Read the entire content through the one descriptor we hold.
            chunks: list[bytes] = []
            while True:
                block = os.read(fd, 1 << 20)
                if not block:
                    break
                chunks.append(block)
            try:
                st2 = os.fstat(fd)
            except OSError as exc:
                return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                          f"post-read fstat failed: {type(exc).__name__}",
                                          None)
            # The file must not have changed identity while we read it.
            # POSIX ctime changes on in-place writes even when an attacker
            # restores mtime_ns. Windows exposes creation-time semantics for
            # st_ctime in supported Python versions; there the no-follow handle's
            # FILE_SHARE_READ mode prevents concurrent write/delete sharing.
            identity_before = (
                int(st1.st_dev), int(st1.st_ino), int(st1.st_size), int(st1.st_mtime_ns)
            )
            identity_after = (
                int(st2.st_dev), int(st2.st_ino), int(st2.st_size), int(st2.st_mtime_ns)
            )
            if os.name != "nt":
                identity_before += (int(st1.st_ctime_ns),)
                identity_after += (int(st2.st_ctime_ns),)
            if identity_before != identity_after:
                return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                          "file identity changed during read", None)
            # Detect a path-replacement race: the descriptor's object must still
            # be the one the path resolves to (rename-then-replace).
            try:
                st_path = os.stat(path, follow_symlinks=False)
            except OSError:
                st_path = None
            if st_path is None or (int(st_path.st_dev), int(st_path.st_ino)) != \
                    (int(st1.st_dev), int(st1.st_ino)):
                return ContentVerifyResult(norm, "UNAVAILABLE", captured, None,
                                          "path no longer refers to the read object",
                                          None)
            current = hashlib.sha256(b"".join(chunks)).hexdigest()
            identity = (int(st1.st_dev), int(st1.st_ino))
            if current == captured:
                return ContentVerifyResult(norm, "MATCH", captured, current, None,
                                          identity)
            return ContentVerifyResult(norm, "CHANGED", captured, current,
                                      "current content digest differs from captured",
                                      identity)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass


def _normalise_rel(value: Any) -> str:
    """Validate and normalise a project-relative POSIX path.

    Args:
        value: Candidate mapping key.

    Returns:
        The normalised path.

    Raises:
        TypeError: ``value`` is not a string.
        ValueError: ``value`` is absolute, contains ``..``, uses backslashes, or
            is not already in normalised form.
    """
    if not isinstance(value, str):
        raise TypeError(f"capture keys must be str, got {type(value).__name__}")
    if not value or value in {".", "./"}:
        raise ValueError(f"capture key {value!r} is not a usable relative path")
    if "\\" in value:
        raise ValueError(f"capture key {value!r} must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.drive:
        raise ValueError(f"capture key {value!r} must stay inside the project root")
    normalised = str(path)
    if normalised != value:
        raise ValueError(f"capture key {value!r} is not normalised (expected {normalised!r})")
    return normalised


def _count_lines(raw: bytes) -> int:
    """Count lines exactly as ``Path.read_text(...).splitlines()`` would.

    The scan pipeline decodes captured bytes with ``utf-8``/``replace`` and
    normalises CRLF and lone CR to LF before counting, so the counts a snapshot
    publishes are byte-for-byte the numbers the historical validator produced by
    re-reading the file.
    """
    text = raw.decode("utf-8", "replace")
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return len(text.splitlines())


def capture(root: str | os.PathLike[str], contents: Mapping[str, bytes],
            *, identities: Mapping[str, DescriptorIdentity] | None = None,
            origin: str = "capture") -> LineCountSnapshot:
    """Build a trusted snapshot from already-captured bytes.

    This is the only way to obtain a :class:`LineCountSnapshot`.  Line counts are
    computed here from ``contents`` and can never be supplied as numbers by the
    caller, so an externally chosen count cannot acquire trusted status.

    P0.7.10-ZRF closes the binding gap P0.7.10-ZR demonstrated: the identity of a
    record is no longer taken from whatever ``os.stat`` happens to see at the
    path when ``capture()`` runs.  Two identity sources exist:

    * ``"descriptor"`` -- the caller (the capture pipeline) supplies the
      :class:`DescriptorIdentity` it took from the audited file descriptor in
      the same controlled read that produced the bytes.  ``capture()``
      cross-checks ``identity.size == len(payload)`` and refuses to bind a
      mismatched pair.
    * ``"self_observed"`` -- no descriptor identity was supplied.  ``capture()``
      then stats the path **and requires ``st_size == len(payload)``**: handing
      it bytes that cannot be the content of the file at that path is refused
      outright.  This is strictly weaker than a descriptor identity (it cannot
      distinguish a same-size replacement) and is recorded as such; the
      production scan pipeline always supplies descriptor identities.

    Args:
        root: Project root the bytes came from; it must be an existing directory.
        contents: Mapping of normalised project-relative POSIX path to the exact
            bytes that were read (the scan's audited ``open_input_file`` output).
        identities: Optional mapping of path to the :class:`DescriptorIdentity`
            recorded from the same controlled read.  Keys must match ``contents``.
        origin: Informational provenance label for the audit trail.

    Returns:
        A frozen snapshot.

    Raises:
        TypeError: ``contents`` is not a mapping, a key is not ``str``, a value is
            not ``bytes``-like, ``identities`` is not a mapping of
            :class:`DescriptorIdentity`, or ``root``/``origin`` have the wrong type.
        ValueError: ``root`` is not an existing directory, a key is absolute,
            contains ``..``, uses backslashes or is not normalised, or an identity
            is supplied for a path that is not in ``contents``.

    Note:
        Evidence mismatches are NOT signalled by raising: a record whose captured
        byte length disagrees with its supplied or observed identity is marked
        ``identity_available=False`` and is therefore never accepted by
        :meth:`LineCountSnapshot.verified_mapping` (fail-closed, no fallback).
        Since P0.7.10-ZRFX, a ``self_observed`` record is bound only after the
        file at the path is re-read and its content digest matches the captured
        bytes (closing the P0.7.10-ZRFR-1 same-size-substitution bypass); this
        branch performs ONE extra sequential read per file, and the production
        pipeline never enters it because it always supplies descriptor
        identities.  This still does NOT make ``stat`` metadata a content proof
        for later drift checks: a rewrite that preserves the on-disk size and
        ``mtime_ns`` after capture remains undetectable, and identity fields
        supplied by the pipeline are trusted on the strength of the pipeline's
        own audit, not of any cryptographic binding, nor is any of this a
        defence against same-process forgery of the objects involved.
    """
    if not isinstance(contents, Mapping):
        raise TypeError(
            "capture() requires a mapping of path -> captured bytes; a pre-computed "
            "path -> line-count mapping cannot be trusted")
    if identities is not None and not isinstance(identities, Mapping):
        raise TypeError("identities must be a mapping of path -> DescriptorIdentity")
    if not isinstance(origin, str) or not origin:
        raise TypeError("origin must be a non-empty string")

    root_path = os.path.realpath(os.fspath(root))
    if not os.path.isdir(root_path):
        raise ValueError(f"capture root {root_path!r} is not a directory")
    try:
        root_st = os.stat(root_path)
        root_identity: tuple[int, int] | None = (int(root_st.st_dev), int(root_st.st_ino))
    except OSError:                                            # pragma: no cover
        root_identity = None

    contents_keys = {_normalise_rel(k) for k in contents}
    if identities is not None:
        for key in identities:
            if _normalise_rel(key) not in contents_keys:
                raise ValueError(
                    f"an identity was supplied for {key!r}, which is not part of this "
                    "capture; identities must describe exactly the captured bytes")

    records: dict[str, FileRecord] = {}
    for key, raw in contents.items():
        rel = _normalise_rel(key)
        if rel in records:
            raise ValueError(f"duplicate capture key {rel!r}")
        if isinstance(raw, str) or not isinstance(raw, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"capture value for {rel!r} must be bytes (got {type(raw).__name__}); "
                "line counts are derived from captured bytes, never supplied")
        payload = bytes(raw)
        line_count = _count_lines(payload)
        digest = hashlib.sha256(payload).hexdigest()

        supplied = identities.get(rel) if identities is not None else None
        if supplied is not None and not isinstance(supplied, DescriptorIdentity):
            raise TypeError(
                f"identity for {rel!r} must be a DescriptorIdentity issued by the "
                f"capture pipeline (got {type(supplied).__name__}); plain dicts and "
                "untyped tuples carry no provenance and are not trusted")
        if supplied is not None:
            # Descriptor-bound: the identity travelled with the bytes from the
            # audited read.  A size disagreement means the evidence does not
            # describe the bytes it accompanies: the record is marked
            # unverifiable (fail-closed) rather than bound, and it is therefore
            # never accepted by verified_mapping().
            if supplied.size != len(payload):
                records[rel] = FileRecord(rel, line_count, len(payload), None, 0, 0,
                                          digest, False, "descriptor")
                continue
            records[rel] = FileRecord(rel, line_count, len(payload), supplied.mtime_ns,
                                      supplied.dev, supplied.ino, digest, True,
                                      "descriptor")
            continue
        # P0.7.10-ZRXRF: single-descriptor binding for the compatible path.
        # The identity and the content now come from ONE os.open() descriptor:
        # fstat #1 (identity) -> read (content) -> fstat #2 (identity must be
        # unchanged) -> the descriptor content must equal the caller's payload.
        # This removes the P0.7.10-ZRXR-3 mismatch window in which a path-stat
        # from one moment was bound to content read through a DIFFERENT
        # descriptor at another moment.  Fail-closed on every discrepancy.
        #
        # Platform notes (explicit, per the work order):
        # * O_BINARY is added where it exists (Windows) so the descriptor read
        #   never suffers text-mode LF->CRLF translation.
        # * O_NOFOLLOW exists on POSIX only; Windows os.open has no
        #   final-component no-follow flag.  Reparse-point rejection on
        #   Windows is the production pipeline's job (usit/safeio); the
        #   compatible self_observed branch does NOT provide that guarantee
        #   and does not claim it.
        rel_path = os.path.join(root_path, *PurePosixPath(rel).parts)
        open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) \
            | getattr(os, "O_NOFOLLOW", 0)
        fd = None
        try:
            try:
                fd = os.open(rel_path, open_flags)
            except OSError:
                records[rel] = FileRecord(rel, line_count, len(payload), None,
                                          0, 0, digest, False, "self_observed")
                continue
            st1 = os.fstat(fd)
            if int(st1.st_size) != len(payload):
                # Cheap reject before reading: the bytes cannot be this file's
                # content if the sizes disagree.
                records[rel] = FileRecord(rel, line_count, len(payload), None,
                                          0, 0, digest, False, "self_observed")
                continue
            chunks = []
            while True:
                block = os.read(fd, 1 << 20)
                if not block:
                    break
                chunks.append(block)
            st2 = os.fstat(fd)
            if (int(st1.st_dev), int(st1.st_ino), int(st1.st_size),
                    int(st1.st_mtime_ns)) != (int(st2.st_dev), int(st2.st_ino),
                                              int(st2.st_size),
                                              int(st2.st_mtime_ns)):
                # The file changed while being read through this descriptor.
                records[rel] = FileRecord(rel, line_count, len(payload), None,
                                          0, 0, digest, False, "self_observed")
                continue
            try:
                st_path = os.stat(rel_path, follow_symlinks=False)
            except OSError:
                st_path = None
            if st_path is None or (int(st_path.st_dev), int(st_path.st_ino)) != \
                    (int(st1.st_dev), int(st1.st_ino)):
                # The path no longer refers to the object the descriptor read;
                # refuse to publish a verifiable record for it.
                records[rel] = FileRecord(rel, line_count, len(payload), None,
                                          0, 0, digest, False, "self_observed")
                continue
            if hashlib.sha256(b"".join(chunks)).hexdigest() != digest:
                # The caller's payload is not the content this descriptor read;
                # binding them would recreate the ZRFR-1/ZRXR-3 mismatch.
                records[rel] = FileRecord(rel, line_count, len(payload), None,
                                          0, 0, digest, False, "self_observed")
                continue
            records[rel] = FileRecord(rel, line_count, len(payload),
                                      int(st1.st_mtime_ns), int(st1.st_dev),
                                      int(st1.st_ino), digest, True,
                                      "self_observed")
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        continue
        records[rel] = FileRecord(rel, line_count, len(payload), int(st.st_mtime_ns),
                                  int(st.st_dev), int(st.st_ino), digest, True,
                                  "self_observed")

    return LineCountSnapshot(_CONSTRUCTION_TOKEN, root=root_path,
                             root_identity=root_identity, files=records,
                             origin=origin, version=SNAPSHOT_VERSION)
