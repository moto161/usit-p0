"""P0.7.13-F1: Android read-only adapter + structured command-candidate tests.

All fixtures are synthetic and live in the OS temp directory (outside the USIT
project).  No Gradle, ADB, device, network, or target-project code is touched.
The tests assert the adapter recognises structure from evidence and that the
planner only *describes* commands (execution_status == NOT_EXECUTED) without
executing anything.

Environment capability is recorded explicitly: if the OS refuses to create a
symlink/junction, that path type is reported as NOT TESTED, never as PASS.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import usit.android_adapter as adapter
import usit.command_planner as planner
from usit.android_adapter import AndroidProjectAdapter, AdapterBlocked, CONF_VERIFIED, CONF_UNKNOWN
from usit.command_planner import CommandPlanner, Candidate
from usit.safety import ScanBlocked, authorization_record, canonical_project

CAPABILITY_LOG: dict[str, str] = {}


def _now():
    return datetime.now(timezone.utc)


def _make_symlink(link: Path, target: Path, is_dir: bool = False) -> bool:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
        return True
    except (OSError, NotImplementedError, ValueError):
        return False


def _make_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        # Decode the (often localized) mklink output safely so the reader thread
        # never raises a UnicodeDecodeError.
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                   capture_output=True, encoding="utf-8", errors="replace",
                                   check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and link.exists()


class _SyntheticAndroidProject:
    """Build an in-memory Android/Gradle fixture under a temp directory."""

    def __init__(self, case: unittest.TestCase, name: str = "androidapp"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p0713-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / name
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, rel: str, text: str, *, binary: bool = False) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def profile(self) -> dict:
        return AndroidProjectAdapter(self.root).analyze().to_dict()

    def candidates(self) -> dict:
        return CommandPlanner(self.profile()).to_dict()


class SingleModuleIdentificationTests(unittest.TestCase):
    def test_single_module_kotlin_gradle_identified(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("build.gradle", "buildscript {}\n")
        p.write("gradle.properties", "org.gradle.jvmargs=-Xmx2g\n")
        p.write("app/build.gradle", "apply plugin: 'com.android.application'\n")
        p.write("app/src/main/AndroidManifest.xml",
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
                '    package="com.example.app">\n'
                '    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="34"/>\n'
                '    <uses-permission android:name="android.permission.INTERNET"/>\n'
                '    <application>\n'
                '        <activity android:name=".MainActivity"/>\n'
                '    </application>\n'
                '</manifest>\n')
        p.write("app/src/main/java/com/example/app/MainActivity.kt",
                "package com.example.app\nclass MainActivity\n")
        prof = p.profile()
        self.assertEqual(prof["detection_status"], "DETECTED_ANDROID_GRADLE")
        module_names = [m["name"] for m in prof["modules"]]
        self.assertEqual(module_names, ["app"])
        self.assertEqual(prof["manifest_evidence"]["package_or_namespace"], "com.example.app")
        self.assertIn("android.permission.INTERNET", prof["manifest_evidence"]["permissions"])
        self.assertIn(".MainActivity", prof["manifest_evidence"]["activities"])
        self.assertEqual(prof["sdk_evidence"]["from_manifest"]["minSdkVersion"], "21")


class MultiModuleIdentificationTests(unittest.TestCase):
    def test_multi_module_identified(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app', ':lib', ':feature:login'\n")
        p.write("build.gradle", "buildscript {}\n")
        for mod in ("app", "lib", "feature/login"):
            p.write(f"{mod}/build.gradle", "apply plugin: 'com.android.library'\n")
            p.write(f"{mod}/src/main/AndroidManifest.xml",
                    '<manifest xmlns:android="http://schemas.android.com/apk/res/android"/>\n')
        prof = p.profile()
        self.assertEqual(prof["detection_status"], "DETECTED_ANDROID_GRADLE")
        self.assertEqual(len(prof["modules"]), 3)
        names = sorted(m["name"] for m in prof["modules"])
        self.assertIn("app", names)
        self.assertIn("lib", names)
        # ':feature:login' resolves to directory feature/login; its leaf name is 'login'.
        self.assertIn("login", names)
        self.assertTrue(any(m["rel_dir"] == "feature/login" for m in prof["modules"]))


class DslTests(unittest.TestCase):
    def test_groovy_dsl_identified(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android { compileSdk 34 }\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.groovy"/>\n')
        self.assertEqual(p.profile()["detection_status"], "DETECTED_ANDROID_GRADLE")

    def test_kotlin_dsl_identified(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle.kts", 'include(":app")\n')
        p.write("app/build.gradle.kts", "android { compileSdk = 34 }\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.kts"/>\n')
        prof = p.profile()
        self.assertEqual(prof["detection_status"], "DETECTED_ANDROID_GRADLE")
        # Root settings.gradle.kts is recorded in evidence_refs; module build
        # files are the Kotlin-DSL build.gradle.kts.
        refs = json.dumps(prof["evidence_refs"], ensure_ascii=False)
        self.assertIn("settings.gradle.kts", refs)
        self.assertTrue(any(b["rel_path"].endswith("build.gradle.kts") for b in prof["build_files"]))


class ManifestEvidenceTests(unittest.TestCase):
    def test_permissions_sdk_and_activities_extracted_with_line_evidence(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        manifest = (
            '<?xml version="1.0"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    package="com.evidence">\n'
            '    <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="33"/>\n'
            '    <uses-permission android:name="android.permission.CAMERA"/>\n'
            '    <uses-permission android:name="android.permission.INTERNET"/>\n'
            '    <application>\n'
            '        <activity android:name=".SplashActivity"/>\n'
            '        <activity android:name=".Main"/>\n'
            '    </application>\n'
            '</manifest>\n'
        )
        p.write("app/src/main/AndroidManifest.xml", manifest)
        prof = p.profile()
        me = prof["manifest_evidence"]
        self.assertTrue(me["present"])
        self.assertEqual(me["package_or_namespace"], "com.evidence")
        self.assertIn("android.permission.CAMERA", me["permissions"])
        self.assertIn(".SplashActivity", me["activities"])
        self.assertIn(".Main", me["activities"])
        # Evidence references must carry line numbers (VERIFIED_FACT).
        lines = [e.get("line") for e in me["evidence_refs"] if e.get("field") == "uses-permission"]
        self.assertTrue(lines and all(isinstance(l, int) and l > 0 for l in lines))
        self.assertEqual(prof["sdk_evidence"]["from_manifest"]["minSdkVersion"], "23")


class TestEntryCandidateTests(unittest.TestCase):
    def test_test_entry_candidates_and_frameworks_detected(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.tests"/>\n')
        p.write("app/src/test/java/com/test/UnitTest.java",
                "import org.junit.Test;\npublic class UnitTest { @Test public void ok(){} }\n")
        p.write("app/src/androidTest/java/com/test/InstTest.java",
                "import androidx.test.espresso.Espresso;\n"
                "import androidx.test.uiautomator.UiDevice;\n"
                "public class InstTest {}\n")
        prof = p.profile()
        self.assertEqual(len(prof["test_sources"]), 2)
        fw_names = {f["name"] for f in prof["test_frameworks"]}
        self.assertIn("JUnit", fw_names)
        self.assertIn("Espresso", fw_names)
        self.assertIn("UIAutomator", fw_names)
        cand = p.candidates()
        cats = {c["test_category"] for c in cand["candidates"]}
        self.assertIn("B3_jvm_unit", cats)
        self.assertIn("B4_instrumentation", cats)
        self.assertIn("B5_apk_build", cats)
        # Every candidate must be explicitly NOT_EXECUTED.
        for c in cand["candidates"]:
            self.assertEqual(c["execution_status"], "NOT_EXECUTED")
            self.assertIsInstance(c["argv"], list)
            self.assertNotIsInstance(c["argv"], str)


class SafeDegradationTests(unittest.TestCase):
    def test_missing_manifest_is_not_android_and_does_not_crash(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        prof = p.profile()
        self.assertEqual(prof["detection_status"], "DETECTED_GRADLE_NO_ANDROID_MANIFEST")
        self.assertFalse(prof["manifest_evidence"]["present"], prof["manifest_evidence"])
        self.assertTrue(any(w["code"] == "NO_ANDROID_MANIFEST" for w in prof["warnings"]))

    def test_empty_manifest_parses_to_unknown_not_crash(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/src/main/AndroidManifest.xml", "")
        prof = p.profile()
        self.assertEqual(prof["manifest_evidence"]["confidence"], CONF_UNKNOWN)

    def test_invalid_xml_manifest_warns_and_keeps_unknown(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/src/main/AndroidManifest.xml", "<manifest><activity></manifest>")
        prof = p.profile()
        self.assertTrue(any(w["code"] == "MANIFEST_PARSE_ERROR" for w in prof["warnings"]))
        self.assertEqual(prof["manifest_evidence"]["confidence"], CONF_UNKNOWN)

    def test_invalid_utf8_source_does_not_crash_and_is_flagged(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        # Write bytes that are not valid UTF-8.
        p.write("app/src/test/java/com/test/Bad.java", b"\xff\xfe\x00import org.junit.Test;\n", binary=True)
        prof = p.profile()
        bad = next((t for t in prof["test_sources"] if t["rel_path"].endswith("Bad.java")), None)
        self.assertIsNotNone(bad)
        self.assertTrue(bad["encoding_issue"])
        self.assertTrue(bad["read_ok"])

    def test_oversized_file_is_skipped_safely(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        big = "x = 1\n" * 200000  # ~2 MiB, exceeds MAX_FILE_BYTES (1 MiB)
        p.write("app/build.gradle", big)
        prof = p.profile()
        # Analysis still completes; the oversized file is not read for content.
        self.assertEqual(prof["detection_status"], "DETECTED_GRADLE_NO_ANDROID_MANIFEST")
        self.assertTrue(any(b["rel_path"].endswith("build.gradle") for b in prof["build_files"]))


class PathSafetyTests(unittest.TestCase):
    def test_read_outside_root_is_rejected(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        adapter_obj = AndroidProjectAdapter(p.root)
        outside = p.base / "escape.txt"
        outside.write_text("SECRET", encoding="utf-8")
        with self.assertRaises(AdapterBlocked) as blocked:
            adapter_obj._read_text(outside)
        self.assertEqual(blocked.exception.code, "PATH_TRAVERSAL_REJECTED")

    def test_symlinked_file_inside_project_is_rejected(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        outside = p.base / "outside.txt"
        outside.write_text("OUTSIDE_MARKER", encoding="utf-8")
        link = p.root / "linked.txt"
        if not _make_symlink(link, outside):
            CAPABILITY_LOG["android_symlink"] = "NOT TESTED - OS denied symlink creation"
            self.skipTest("OS denied symlink creation")
        CAPABILITY_LOG["android_symlink"] = "created"
        adapter_obj = AndroidProjectAdapter(p.root)
        with self.assertRaises(AdapterBlocked) as blocked:
            adapter_obj._read_text(link)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")

    def test_symlinked_project_root_is_rejected(self):
        p = _SyntheticAndroidProject(self)
        real = p.base / "real"
        real.mkdir()
        (real / "settings.gradle").write_text("include ':app'\n", encoding="utf-8")
        link = p.base / "linked-root"
        if not _make_symlink(link, real):
            CAPABILITY_LOG["android_symlink_root"] = "NOT TESTED - OS denied symlink creation"
            self.skipTest("OS denied symlink creation")
        CAPABILITY_LOG["android_symlink_root"] = "created"
        with self.assertRaises(ScanBlocked):
            AndroidProjectAdapter(link)

    def test_junction_inside_project_rejected(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        outside = p.base / "junction-target"
        outside.mkdir()
        link = p.root / "junction-link"
        if not _make_junction(link, outside):
            CAPABILITY_LOG["android_junction"] = "NOT TESTED - OS refused mklink /J"
            self.skipTest("OS refused junction creation")
        CAPABILITY_LOG["android_junction"] = "created"
        adapter_obj = AndroidProjectAdapter(p.root)
        with self.assertRaises(AdapterBlocked) as blocked:
            adapter_obj._read_text(link)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")


class CandidateSafetyTests(unittest.TestCase):
    def test_candidates_use_argv_array_and_never_execute(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.c"/>\n')
        p.write("app/src/test/java/com/c/UnitTest.java",
                "import org.junit.Test;\npublic class UnitTest { @Test public void ok(){} }\n")
        # Monkeypatch every plausible execution sink; plan() must not call them.
        with patch("os.system", side_effect=AssertionError("os.system must not be called")), \
             patch("os.popen", side_effect=AssertionError("os.popen must not be called")), \
             patch("subprocess.run", side_effect=AssertionError("subprocess.run must not be called")):
            cand = p.candidates()
        self.assertGreaterEqual(cand["candidate_count"], 1)
        for c in cand["candidates"]:
            self.assertEqual(c["execution_status"], "NOT_EXECUTED")
            self.assertIsInstance(c["argv"], list)
        # The planner exposes no execution method.
        self.assertFalse(hasattr(CommandPlanner, "execute"))
        self.assertFalse(hasattr(CommandPlanner, "run"))

    def test_no_fabricated_candidates_without_evidence(self):
        # Project with no test sources -> no B3/B4; module with build file still
        # yields B5.  Project with no Gradle files -> zero candidates.
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.none"/>\n')
        prof = p.profile()
        cand = CommandPlanner(prof).to_dict()
        cats = {c["test_category"] for c in cand["candidates"]}
        self.assertNotIn("B3_jvm_unit", cats)
        self.assertNotIn("B4_instrumentation", cats)
        self.assertIn("B5_apk_build", cats)

        # No Gradle files at all -> no modules -> no candidates.
        p2 = _SyntheticAndroidProject(self, name="nogradle")
        prof2 = p2.profile()
        self.assertEqual(prof2["detection_status"], "INSUFFICIENT_EVIDENCE_NO_GRADLE")
        self.assertEqual(CommandPlanner(prof2).to_dict()["candidate_count"], 0)


class SerializationTests(unittest.TestCase):
    def test_profile_is_stable_and_json_roundtrips(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.ser"/>\n')
        a1 = AndroidProjectAdapter(p.root).analyze().to_dict()
        a2 = AndroidProjectAdapter(p.root).analyze().to_dict()
        self.assertEqual(json.dumps(a1, sort_keys=True), json.dumps(a2, sort_keys=True))
        blob = json.dumps(a1, ensure_ascii=False, indent=2)
        reloaded = json.loads(blob)
        self.assertEqual(reloaded["schema_version"], adapter.SCHEMA_VERSION)
        self.assertIn("modules", reloaded)


class SensitiveFileTests(unittest.TestCase):
    def test_sensitive_files_are_never_referenced(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.sec"/>\n')
        # Drop secret-looking files into the project.
        p.write(".env", "API_TOKEN=secret\n")
        p.write(".env.local", "DB_PASSWORD=secret\n")
        p.write("id_rsa", "PRIVATE KEY CONTENT\n")
        p.write("gradle.properties", "org.gradle.jvmargs=-Xmx2g\n")
        prof = p.profile()
        all_refs = json.dumps(prof, ensure_ascii=False)
        self.assertNotIn(".env", all_refs)
        self.assertNotIn("id_rsa", all_refs)


class CliIntegrationTests(unittest.TestCase):
    def test_inspect_android_writes_two_files_and_does_not_execute(self):
        p = _SyntheticAndroidProject(self)
        p.write("settings.gradle", "include ':app'\n")
        p.write("app/build.gradle", "android {}\n")
        p.write("app/src/main/AndroidManifest.xml", '<manifest package="com.cli"/>\n')
        out = p.base / "inspect-out"
        auth = authorization_record(p.root, out, now=_now())
        auth_file = p.base / "auth.json"
        auth_file.write_text(json.dumps(auth), encoding="utf-8")
        with (
            patch("os.system", side_effect=AssertionError("no exec")),
            patch("subprocess.run", side_effect=AssertionError("no exec")),
        ):
            rc = __import__("usit.cli", fromlist=["main"]).main([
                "inspect-android", str(p.root), "--out", str(out),
                "--authorization-file", str(auth_file),
            ])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "android_profile.json").is_file())
        self.assertTrue((out / "android_test_plan.json").is_file())
        plan = json.loads((out / "android_test_plan.json").read_text(encoding="utf-8"))
        for c in plan["candidates"]:
            self.assertEqual(c["execution_status"], "NOT_EXECUTED")


class RegressionTests(unittest.TestCase):
    def test_usit_selftest_still_passes(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from usit.cli import selftest
        with redirect_stdout(StringIO()):
            self.assertEqual(selftest(), 0)


class CapabilityLogTests(unittest.TestCase):
    def test_capability_log_records_real_creation(self):
        for key in ("android_symlink", "android_symlink_root", "android_junction"):
            CAPABILITY_LOG.setdefault(key, "NOT TESTED - not exercised in this run")
        self.assertTrue(all(isinstance(v, str) and v for v in CAPABILITY_LOG.values()))


if __name__ == "__main__":
    unittest.main()
