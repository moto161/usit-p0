"""Static Android (Gradle) project adapter — USIT P0.7.13-F1.

This module recognises the *structure* of an Android project by reading a
bounded set of project files.  It is strictly read-only and never:

* imports, executes or dynamically loads any target-project code;
* runs Gradle, Gradle Wrapper, ADB, or any build/test/install command;
* writes anywhere except the caller-provided output location (handled by the
  CLI layer, not here);
* assumes a project is Android, buildable, or testable because a file exists.

It reuses the existing fail-closed primitives from :mod:`usit.safeio`
(``open_no_follow``) and :mod:`usit.safety` (``canonical_project``,
``is_reparse_point``, size/byte budgets) so path-safety behaviour matches the
rest of USIT.

Confidence model (new, versioned).  It deliberately does *not* reuse the
scanner's ``resolution_status`` (which describes call-resolution quality) because
the two axes are different; the adapter reports *evidence confidence*, not
symbol-resolution status.  New fields are covered by ``SCHEMA_VERSION`` so a
future consumer can detect incompatibility.

* ``VERIFIED_FACT``     — value was read directly from an existing artifact.
* ``STATIC_INFERENCE``  — inferred from static structure; not executed/confirmed.
* ``UNKNOWN``           — could not be determined; never guessed.
* ``CONFLICT``          — multiple sources disagree; left unresolved.
"""
from __future__ import annotations

import os
import re
import stat
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import safety
from .safeio import open_no_follow
from .scanner import SKIP_NAMES  # reuse the existing secret-name exclusions

SCHEMA_VERSION = "usit-android-profile-v1"

CONF_VERIFIED = "VERIFIED_FACT"
CONF_INFERRED = "STATIC_INFERENCE"
CONF_UNKNOWN = "UNKNOWN"
CONF_CONFLICT = "CONFLICT"

# Reuse USIT's existing safety budgets so behaviour is consistent.
MAX_FILE_BYTES = safety.MAX_FILE_BYTES
MAX_TOTAL_READ_BYTES = safety.MAX_TOTAL_READ_BYTES
MAX_FILES = safety.MAX_FILES
MAX_SCAN_SECONDS = safety.MAX_SCAN_SECONDS

ANDROID_ROOT_FILES = (
    "settings.gradle", "settings.gradle.kts",
    "build.gradle", "build.gradle.kts",
    "gradle.properties",
)
MANIFEST_NAME = "AndroidManifest.xml"

# Test-framework markers we can confirm by reading source files only.
FRAMEWORK_MARKERS: dict[str, re.Pattern[str]] = {
    "JUnit": re.compile(r"\bimport\s+org\.junit\b|@Test\b|org\.junit\."),
    "AndroidXTest": re.compile(r"\bimport\s+androidx\.test\b|androidx\.test\."),
    "Espresso": re.compile(r"\bimport\s+androidx\.test\.espresso\b|androidx\.test\.espresso\."),
    "UIAutomator": re.compile(r"\bimport\s+androidx\.test\.uiautomator\b|androidx\.test\.uiautomator\."),
    "Robolectric": re.compile(r"\bimport\s+org\.robolectric\b|org\.robolectric\."),
}


class AdapterBlocked(ValueError):
    """Fail-closed precondition for the Android adapter (path/safety)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class AndroidProjectProfile:
    """Structured, JSON-serialisable Android project recognition result."""

    schema_version: str = SCHEMA_VERSION
    model: str = "AndroidProjectProfile"
    project_root: str = ""
    detection_status: str = CONF_UNKNOWN
    modules: list[dict[str, Any]] = field(default_factory=list)
    build_files: list[dict[str, Any]] = field(default_factory=list)
    manifest_evidence: dict[str, Any] = field(default_factory=dict)
    sdk_evidence: dict[str, Any] = field(default_factory=dict)
    test_sources: list[dict[str, Any]] = field(default_factory=list)
    test_frameworks: list[dict[str, Any]] = field(default_factory=list)
    wrapper_candidates: list[dict[str, Any]] = field(default_factory=list)
    variant_candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model": self.model,
            "project_root": self.project_root,
            "detection_status": self.detection_status,
            "modules": self.modules,
            "build_files": self.build_files,
            "manifest_evidence": self.manifest_evidence,
            "sdk_evidence": self.sdk_evidence,
            "test_sources": self.test_sources,
            "test_frameworks": self.test_frameworks,
            "wrapper_candidates": self.wrapper_candidates,
            "variant_candidates": self.variant_candidates,
            "warnings": self.warnings,
            "evidence_refs": self.evidence_refs,
        }


def _local(tag: str) -> str:
    """Return an XML tag/attribute local name with any namespace stripped."""
    return tag.split("}", 1)[-1]


def _regex_lines(text: str, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    """Return (1-based line number, matched line) for every pattern hit."""
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            out.append((i, line.strip()))
    return out


class AndroidProjectAdapter:
    """Read-only recogniser for Android/Gradle project structure.

    Args:
        project_root: An explicitly authorised, absolute project root.  It is
            canonicalised (and reparse-rejected) by :func:`safety.canonical_project`.
    """

    def __init__(self, project_root: str | Path):
        self.root = safety.canonical_project(project_root)
        self._total_read_bytes = 0
        self._file_count = 0
        self._entry_count = 0
        self._started_at = time.monotonic()

    def _check_budget(self) -> None:
        if time.monotonic() - self._started_at > MAX_SCAN_SECONDS:
            raise AdapterBlocked("SCAN_TIME_BUDGET_EXCEEDED", f"Android inspection exceeded {MAX_SCAN_SECONDS} seconds.")
        if self._entry_count >= MAX_FILES:
            raise AdapterBlocked("FILE_BUDGET_EXCEEDED", f"Android inspection exceeded {MAX_FILES} discovered entries.")

    def _reject_reparse_path(self, path: Path) -> None:
        absolute = Path(os.path.abspath(os.fspath(path)))
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            if safety.is_reparse_point(current):
                raise AdapterBlocked("REPARSE_POINT_REJECTED", f"{current} is a link/junction/reparse point.")

    def _validate_project_path(self, path: Path) -> Path:
        """Validate a candidate before any is_file/is_dir/stat operation."""
        self._reject_reparse_path(path)
        try:
            resolved = path.resolve(strict=False)
        except OSError as exc:
            raise AdapterBlocked("PATH_CHECK_FAILED", f"Could not resolve {path}: {type(exc).__name__}") from exc
        if resolved != self.root and self.root not in resolved.parents:
            raise AdapterBlocked("PATH_TRAVERSAL_REJECTED", f"{path} is outside the authorised project root.")
        return resolved

    def _count_entry(self, path: Path) -> None:
        self._check_budget()
        self._entry_count += 1
        self._validate_project_path(path)

    # ------------------------------------------------------------------
    # Safe, no-follow reading
    # ------------------------------------------------------------------
    def _read_text(self, path: Path) -> dict[str, Any]:
        """Read a file with fail-closed, no-follow semantics.

        Returns a dict with at least ``text`` (str|None) and ``read_ok``.  On a
        reparse point, missing file, over-size file, or decode problem the call
        degrades safely and records the reason instead of raising or guessing.
        """
        self._check_budget()
        # Check the directory entry itself before exists()/is_file(), including dangling links.
        self._validate_project_path(path)
        if not path.exists() or not path.is_file():
            return {"text": None, "read_ok": False, "error": "MISSING"}
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise AdapterBlocked("PATH_CHECK_FAILED", f"Could not resolve {path}: {type(exc).__name__}") from exc
        if resolved != self.root and self.root not in resolved.parents:
            raise AdapterBlocked("PATH_TRAVERSAL_REJECTED", f"{path} is outside the authorised project root.")
        if self._file_count >= MAX_FILES:
            raise AdapterBlocked("FILE_BUDGET_EXCEEDED", f"Android inspection exceeds {MAX_FILES} file reads.")
        try:
            fd = open_no_follow(resolved)
        except OSError as exc:
            raise AdapterBlocked("SAFE_OPEN_FAILED", f"Could not safely open {path}: {type(exc).__name__}") from exc
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise AdapterBlocked("FILE_SIZE_LIMIT_EXCEEDED", f"{path} exceeds the {MAX_FILE_BYTES}-byte regular-file limit.")
            self._validate_project_path(path)
            path_before = os.stat(resolved, follow_symlinks=False)
            identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            if identity != (path_before.st_dev, path_before.st_ino, path_before.st_size,
                            path_before.st_mtime_ns, path_before.st_ctime_ns):
                raise AdapterBlocked("FILE_CHANGED_DURING_OPEN", f"{path} changed while being opened.")
            if self._total_read_bytes + before.st_size > MAX_TOTAL_READ_BYTES:
                raise AdapterBlocked("BYTE_BUDGET_EXCEEDED", f"Android inspection exceeds {MAX_TOTAL_READ_BYTES} total bytes.")
            chunks = []
            remaining = MAX_FILE_BYTES + 1
            while remaining > 0:
                block = os.read(fd, min(65536, remaining))
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            raw = b"".join(chunks)
            after = os.fstat(fd)
            self._validate_project_path(path)
            path_after = os.stat(resolved, follow_symlinks=False)
            before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            path_identity = (path_after.st_dev, path_after.st_ino, path_after.st_size,
                             path_after.st_mtime_ns, path_after.st_ctime_ns)
            if before_identity != after_identity or after_identity != path_identity:
                raise AdapterBlocked("FILE_CHANGED_DURING_READ", f"{path} changed while being read.")
            if len(raw) != before.st_size or len(raw) > MAX_FILE_BYTES:
                raise AdapterBlocked("FILE_SIZE_LIMIT_EXCEEDED", f"{path} exceeded its size budget during read.")
        finally:
            os.close(fd)
        self._file_count += 1
        self._total_read_bytes += len(raw)
        try:
            text = raw.decode("utf-8")
            encoding_issue = False
        except UnicodeDecodeError:
            # Degrade safely: keep a lossy copy for best-effort matching but flag it.
            text = raw.decode("utf-8", errors="replace")
            encoding_issue = True
        return {"text": text, "read_ok": True, "encoding_issue": encoding_issue, "size": len(raw), "error": None}

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    # ------------------------------------------------------------------
    # Root-level signals
    # ------------------------------------------------------------------
    def _detect_root_signals(self, profile: AndroidProjectProfile) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for name in ANDROID_ROOT_FILES:
            self._check_budget()
            p = self.root / name
            self._validate_project_path(p)
            if p.is_file():
                found[name] = p
                profile.evidence_refs.append({
                    "rel_path": self._rel(p), "kind": "root_file_present",
                    "confidence": CONF_VERIFIED, "detail": "file exists",
                })
        wrapper_props = self.root / "gradle" / "wrapper" / "gradle-wrapper.properties"
        gradlew = self.root / "gradlew"
        self._validate_project_path(wrapper_props)
        self._validate_project_path(gradlew)
        if wrapper_props.is_file():
            found["gradle-wrapper.properties"] = wrapper_props
        if gradlew.is_file():
            found["gradlew"] = gradlew
        # Manifest search: root, app/, and any immediate module dir.
        manifest = self._find_manifest()
        if manifest is not None:
            found[MANIFEST_NAME] = manifest
        return found

    def _find_manifest(self) -> Path | None:
        candidates = [self.root / MANIFEST_NAME, self.root / "app" / MANIFEST_NAME]
        for candidate in candidates:
            self._validate_project_path(candidate)
        for sub in self.root.iterdir():
            self._count_entry(sub)
            if sub.is_dir():
                cand = sub / "src" / "main" / MANIFEST_NAME
                self._validate_project_path(cand)
                if cand.is_file():
                    candidates.append(cand)
        for c in candidates:
            if c.is_file():
                return c
        return None

    # ------------------------------------------------------------------
    # Module discovery (settings.gradle includes + fallback directory scan)
    # ------------------------------------------------------------------
    def _parse_settings_modules(self, settings_path: Path | None) -> list[str]:
        if settings_path is None:
            return []
        data = self._read_text(settings_path)
        if not data["read_ok"]:
            return []
        text = data["text"]
        modules: list[str] = []
        # Gradle module declarations use quoted paths beginning with ':'.  Both
        # DSLs are covered: Groovy ``include ':a', ':b:c'`` (no parentheses) and
        # Kotlin DSL ``include(":a")``.  We capture every quoted ':...' token; a
        # leading colon is the reliable signature of a project-path include.
        for m in re.finditer(r"['\"](:[^'\"]+)['\"]", text):
            modules.append(m.group(1))
        # includeBuild(':other') (composite build) also matches the pattern but
        # resolves to a directory we cannot test here; it is harmlessly skipped
        # later because no such directory exists under the root.
        return modules

    def _module_dir(self, gradle_path: str) -> Path:
        # Gradle project paths are colon-delimited identifiers. Never interpret
        # a slash, backslash, dot-segment or drive-like token as a filesystem path.
        if not isinstance(gradle_path, str) or not gradle_path.startswith(":"):
            raise AdapterBlocked("INVALID_MODULE_PATH", "Gradle module path must start with ':'.")
        parts = gradle_path[1:].split(":")
        if (not parts or any(not part or part in (".", "..") or
                             "/" in part or chr(92) in part or "\x00" in part
                             for part in parts)):
            raise AdapterBlocked("INVALID_MODULE_PATH", f"Unsafe Gradle module path refused: {gradle_path!r}")
        candidate = self.root.joinpath(*parts)
        self._validate_project_path(candidate)
        return candidate

    def _discover_modules(self, settings_modules: list[str]) -> list[Path]:
        dirs: list[Path] = []
        for gm in settings_modules:
            d = self._module_dir(gm)
            if d.is_dir():
                dirs.append(d)
        if not dirs:
            # Fallback: any directory containing a Gradle build file is a module.
            for sub in self.root.iterdir():
                self._count_entry(sub)
                build_candidates = (sub / "build.gradle", sub / "build.gradle.kts")
                for candidate in build_candidates:
                    self._validate_project_path(candidate)
                if sub.is_dir() and any(candidate.is_file() for candidate in build_candidates):
                    dirs.append(sub)
        # De-duplicate while preserving order.
        seen: set[Path] = set()
        unique: list[Path] = []
        for d in dirs:
            rd = d.resolve()
            if rd not in seen:
                seen.add(rd)
                unique.append(d)
        return unique

    # ------------------------------------------------------------------
    # Per-module analysis
    # ------------------------------------------------------------------
    def _module_build_file(self, module_dir: Path) -> Path | None:
        self._validate_project_path(module_dir)
        for name in ("build.gradle", "build.gradle.kts"):
            p = module_dir / name
            self._validate_project_path(p)
            if p.is_file():
                return p
        return None

    def _detect_source_sets(self, module_dir: Path) -> dict[str, Path]:
        sets: dict[str, Path] = {}
        self._validate_project_path(module_dir)
        for name in ("main", "test", "androidTest"):
            d = module_dir / "src" / name
            self._validate_project_path(d)
            if d.is_dir():
                sets[name] = d
        return sets

    def _detect_test_sources(self, source_sets: dict[str, Path]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for set_name, base in source_sets.items():
            if set_name not in ("test", "androidTest"):
                continue
            for src in base.rglob("*"):
                self._count_entry(src)
                if src.is_file() and src.suffix in (".kt", ".java"):
                    data = self._read_text(src)
                    markers: list[str] = []
                    text = data.get("text")
                    if text:
                        for fw, pat in FRAMEWORK_MARKERS.items():
                            if pat.search(text):
                                markers.append(fw)
                    out.append({
                        "source_set": set_name,
                        "rel_path": self._rel(src),
                        "framework_markers": markers,
                        "read_ok": data["read_ok"],
                        "encoding_issue": data.get("encoding_issue", False),
                        "confidence": CONF_VERIFIED if data["read_ok"] else CONF_UNKNOWN,
                        # Directory existence alone never proves executability.
                        "executable_inferred": False,
                    })
        return out

    def _detect_frameworks(self, test_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {fw: 0 for fw in FRAMEWORK_MARKERS}
        for ts in test_sources:
            for fw in ts.get("framework_markers", []):
                counts[fw] = counts.get(fw, 0) + 1
        out: list[dict[str, Any]] = []
        for fw, n in counts.items():
            if n > 0:
                out.append({
                    "name": fw, "evidence_file_count": n,
                    "confidence": CONF_VERIFIED,
                    "note": "marker found by static source scan; not executed",
                })
        return out

    def _detect_wrapper_variants(self, build_files: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        wrapper: list[dict[str, Any]] = []
        variants: list[dict[str, Any]] = []
        props = self.root / "gradle" / "wrapper" / "gradle-wrapper.properties"
        if props.is_file():
            data = self._read_text(props)
            dist = None
            if data["read_ok"]:
                m = re.search(r"distributionUrl\s*=\s*(.+)", data["text"])
                if m:
                    dist = m.group(1).strip()
            wrapper.append({
                "type": "gradle-wrapper", "distribution_url": dist,
                "rel_path": self._rel(props),
                "confidence": CONF_VERIFIED if dist else CONF_UNKNOWN,
                "note": "static property only; wrapper version NOT executed/verified",
            })
        for bf in build_files:
            data = self._read_text(bf)
            if not data["read_ok"]:
                continue
            text = data["text"]
            flav = re.findall(r"\bproductFlavors\s*\{", text)
            builds = re.findall(r"\bbuildTypes\s*\{", text)
            dims = re.findall(r"\bflavorDimensions\s+([\'\"][^\'\"]+[\'\"])", text)
            if flav or builds or dims:
                variants.append({
                    "rel_path": self._rel(bf),
                    "product_flavors_detected": bool(flav),
                    "build_types_detected": bool(builds),
                    "flavor_dimensions": dims,
                    "confidence": CONF_INFERRED,
                    "note": "static DSL markers; actual variants NOT resolved without executing Gradle",
                })
        return wrapper, variants

    # ------------------------------------------------------------------
    # Manifest + SDK evidence
    # ------------------------------------------------------------------
    def _parse_manifest(self, manifest_path: Path, profile: AndroidProjectProfile) -> None:
        data = self._read_text(manifest_path)
        if not data["read_ok"]:
            profile.warnings.append({
                "code": "MANIFEST_UNREADABLE", "rel_path": self._rel(manifest_path),
                "detail": data.get("error", "unknown"),
            })
            profile.manifest_evidence = {
                "present": False, "confidence": CONF_UNKNOWN,
                "detail": f"manifest could not be read ({data.get('error')})",
            }
            return
        text = data["text"]
        if data.get("encoding_issue"):
            profile.warnings.append({
                "code": "MANIFEST_ENCODING_ISSUE", "rel_path": self._rel(manifest_path),
                "detail": "UTF-8 decode used lossy replacement; evidence may be incomplete",
            })
        # Line-number evidence from the raw text.
        pkg_lines = _regex_lines(text, re.compile(r"\b(?:package|namespace)\s*=\s*[\"']([^\"']+)[\"']|"
                                                 r"<manifest[^>]*\b(?:package|namespace)\s*=\s*[\"']([^\"']+)[\"']"))
        perm_lines = _regex_lines(text, re.compile(r"<uses-permission[^>]*android:name\s*=\s*[\"']([^\"']+)[\"']"))
        sdk_lines = _regex_lines(text, re.compile(r"android:(minSdkVersion|targetSdkVersion|maxSdkVersion)\s*=\s*[\"']?(\d+)[\"']?"))
        act_lines = _regex_lines(text, re.compile(r"<activity\b[^>]*android:name\s*=\s*[\"']([^\"']+)[\"']"))

        manifest_ev: dict[str, Any] = {"present": True, "rel_path": self._rel(manifest_path), "confidence": CONF_VERIFIED}
        # Structured parse (namespace-tolerant).
        package_val = None
        try:
            tree = ET.fromstring(text)
            for k, v in tree.attrib.items():
                if _local(k) in ("package", "namespace"):
                    package_val = v
            permissions: list[dict[str, Any]] = []
            activities: list[dict[str, Any]] = []
            for el in tree.iter():
                lt = _local(el.tag)
                if lt == "uses-permission":
                    for ak, av in el.attrib.items():
                        if _local(ak) == "name":
                            permissions.append(av)
                elif lt == "activity":
                    for ak, av in el.attrib.items():
                        if _local(ak) == "name":
                            activities.append(av)
            manifest_ev["package_or_namespace"] = package_val
            manifest_ev["permissions"] = sorted(set(permissions))
            manifest_ev["activities"] = sorted(set(activities))
        except ET.ParseError as exc:
            profile.warnings.append({
                "code": "MANIFEST_PARSE_ERROR", "rel_path": self._rel(manifest_path),
                "detail": f"XML parse failed: {exc}",
            })
            manifest_ev["confidence"] = CONF_UNKNOWN
            manifest_ev["detail"] = "XML could not be parsed; only raw-text evidence available"

        manifest_ev["evidence_refs"] = []
        for ln, _ in pkg_lines:
            manifest_ev["evidence_refs"].append({"field": "package/namespace", "line": ln, "confidence": CONF_VERIFIED})
        for ln, line in perm_lines:
            manifest_ev["evidence_refs"].append({"field": "uses-permission", "line": ln, "confidence": CONF_VERIFIED})
        for ln, line in act_lines:
            manifest_ev["evidence_refs"].append({"field": "activity", "line": ln, "confidence": CONF_VERIFIED})
        profile.manifest_evidence = manifest_ev

        # SDK evidence from manifest uses-sdk (structured + raw line numbers).
        sdk: dict[str, Any] = {"confidence": CONF_UNKNOWN, "sources": []}
        try:
            tree = ET.fromstring(text)
            for el in tree.iter():
                if _local(el.tag) == "uses-sdk":
                    for ak, av in el.attrib.items():
                        if _local(ak) in ("minSdkVersion", "targetSdkVersion", "maxSdkVersion"):
                            sdk.setdefault("from_manifest", {})[_local(ak)] = av
        except ET.ParseError:
            pass
        sdk["from_manifest_raw_lines"] = []
        value_re = re.compile(r"android:(minSdkVersion|targetSdkVersion|maxSdkVersion)\s*=\s*[\"']?(\d+)[\"']?")
        for ln, line in sdk_lines:
            m = value_re.search(line)
            if m:
                sdk["from_manifest_raw_lines"].append({"field": m.group(1), "line": ln, "value": m.group(2)})
        if sdk.get("from_manifest") or sdk.get("from_manifest_raw_lines"):
            sdk["confidence"] = CONF_VERIFIED
        else:
            sdk["confidence"] = CONF_UNKNOWN
            sdk["detail"] = "no SDK levels in manifest; compileSdk/minSdk may live in build files (not inferred here)"
        profile.sdk_evidence = sdk

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def analyze(self) -> AndroidProjectProfile:
        profile = AndroidProjectProfile()
        profile.project_root = str(self.root)
        signals = self._detect_root_signals(profile)
        has_gradle = any(k in signals for k in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"))
        has_manifest = MANIFEST_NAME in signals

        if not has_gradle:
            profile.detection_status = "INSUFFICIENT_EVIDENCE_NO_GRADLE"
            profile.warnings.append({"code": "NO_GRADLE_FILES", "detail": "no settings/build Gradle files found"})
            return profile

        # Module discovery.
        settings_path = signals.get("settings.gradle") or signals.get("settings.gradle.kts")
        declared = self._parse_settings_modules(settings_path)
        module_dirs = self._discover_modules(declared)

        all_test_sources: list[dict[str, Any]] = []
        all_frameworks: set[str] = set()
        build_files: list[Path] = []
        modules_out: list[dict[str, Any]] = []

        for md in module_dirs:
            bf = self._module_build_file(md)
            if bf is not None:
                build_files.append(bf)
                profile.build_files.append({
                    "rel_path": self._rel(bf), "module": md.name,
                    "confidence": CONF_VERIFIED,
                })
            source_sets = self._detect_source_sets(md)
            test_sources = self._detect_test_sources(source_sets)
            frameworks = self._detect_frameworks(test_sources)
            for fw in frameworks:
                all_frameworks.add(fw["name"])
            all_test_sources.extend(test_sources)
            modules_out.append({
                "name": md.name,
                "rel_dir": self._rel(md) if md != self.root else ".",
                "build_file": self._rel(bf) if bf else None,
                "source_sets": {k: self._rel(v) for k, v in source_sets.items()},
                "has_test_sources": bool(test_sources),
                "test_source_count": len(test_sources),
                "frameworks": [f["name"] for f in frameworks],
                "confidence": CONF_VERIFIED,
            })

        profile.modules = modules_out
        profile.test_sources = all_test_sources
        profile.test_frameworks = sorted(
            ({"name": fw, "evidence_file_count": sum(1 for t in all_test_sources if fw in t.get("framework_markers", [])),
              "confidence": CONF_VERIFIED, "note": "static marker scan; not executed"}
             for fw in all_frameworks),
            key=lambda x: x["name"],
        )

        wrapper, variants = self._detect_wrapper_variants(build_files)
        profile.wrapper_candidates = wrapper
        profile.variant_candidates = variants

        if has_manifest:
            self._parse_manifest(signals[MANIFEST_NAME], profile)
            profile.detection_status = "DETECTED_ANDROID_GRADLE"
        else:
            profile.detection_status = "DETECTED_GRADLE_NO_ANDROID_MANIFEST"
            profile.manifest_evidence = {
                "present": False, "confidence": CONF_UNKNOWN,
                "detail": "no AndroidManifest.xml discovered",
            }
            profile.warnings.append({
                "code": "NO_ANDROID_MANIFEST",
                "detail": "Gradle files present but no AndroidManifest.xml found; treated as non-Android Gradle",
            })

        # Top-level evidence summary.
        profile.evidence_refs.append({
            "kind": "summary", "detection_status": profile.detection_status,
            "module_count": len(modules_out),
            "test_source_count": len(all_test_sources),
            "confidence": CONF_VERIFIED,
        })
        return profile


def inspect_android(project_root: str | Path) -> dict[str, Any]:
    """Convenience wrapper returning a JSON-serialisable profile dict."""
    return AndroidProjectAdapter(project_root).analyze().to_dict()
