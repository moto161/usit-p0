"""Structured test-command candidate generation — USIT P0.7.13-F1.

The planner produces *candidates only*.  It never executes anything: it does
not import ``subprocess``, does not join ``argv`` into a shell string, and does
not call Gradle/Gradle Wrapper/ADB or any other tool.  Each candidate is a
structured, auditable data record with ``execution_status`` fixed to
``NOT_EXECUTED`` so it can never be mistaken for a performed action.

A future ExecutionPolicy / ApprovalGate (P0.7.13-F2 and later) is responsible
for deciding whether a candidate may actually run; this module only describes
what *could* be run and what approval it would require.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "usit-command-candidates-v1"

# Test-category taxonomy (aligned with P0.7.12-DESIGN B1-B9).  Categories that
# require a device, install, network access or a real backend are explicitly
# flagged so an approver can gate them.
TEST_CATEGORIES = {
    "B3_jvm_unit": {
        "tool": "gradle", "risk_level": "MEDIUM", "required_approval": "test_unit + build + dependencies",
        "side_effects": "writes build cache; may download dependencies if --offline is unavailable",
        "needs_device": False,
    },
    "B4_instrumentation": {
        "tool": "gradle", "risk_level": "HIGH", "required_approval": "test_instrumentation + device + install",
        "side_effects": "requires device/emulator; installs test APK; may touch a real backend",
        "needs_device": True,
    },
    "B5_apk_build": {
        "tool": "gradle", "risk_level": "HIGH", "required_approval": "build + dependencies",
        "side_effects": "writes build outputs; may download dependencies; does NOT prove install/launch/function",
        "needs_device": False,
    },
}


@dataclass
class Candidate:
    """A single, non-executed test-command candidate."""

    candidate_id: str
    test_category: str
    tool: str
    argv: list[str]                 # structured argument list — NEVER a shell string
    cwd: str                        # relative path under project root
    purpose: str
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    side_effects: str = ""
    risk_level: str = "MEDIUM"
    required_approval: str = ""
    execution_status: str = "NOT_EXECUTED"   # fixed; never mutated to EXECUTED here

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "test_category": self.test_category,
            "tool": self.tool,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "purpose": self.purpose,
            "evidence_refs": self.evidence_refs,
            "side_effects": self.side_effects,
            "risk_level": self.risk_level,
            "required_approval": self.required_approval,
            "execution_status": self.execution_status,
        }


class CommandPlanner:
    """Generate evidence-backed command candidates from an :class:`AndroidProjectProfile`."""

    def __init__(self, profile: dict[str, Any]):
        if not isinstance(profile, dict):
            raise TypeError("CommandPlanner requires a profile dict (AndroidProjectProfile.to_dict()).")
        self.profile = profile
        self.project_root = profile.get("project_root", "")

    @staticmethod
    def _module_gradle_path(module: dict[str, Any]) -> str:
        rel = module.get("rel_dir", ".")
        if rel in (".", ""):
            return ":" + (module.get("name") or "app")
        # 'app' -> ':app'; 'feature/login' -> ':feature:login'
        return ":" + rel.replace("/", ":")

    def plan(self) -> list[Candidate]:
        """Return candidate commands; never executes, never fabricates evidence."""
        candidates: list[Candidate] = []
        modules = self.profile.get("modules", [])
        for module in modules:
            gradle_path = self._module_gradle_path(module)
            cwd = "."  # Gradle is invoked from the project root
            evidence = [
                {"rel_path": module.get("rel_dir", "."), "kind": "module_present", "confidence": "VERIFIED_FACT"},
            ]

            # B3 — JVM unit tests: only if a `test` source set exists.
            if "test" in module.get("source_sets", {}):
                evidence_test = evidence + [
                    {"rel_path": module["source_sets"]["test"], "kind": "jvm_test_source_set", "confidence": "VERIFIED_FACT"},
                ]
                candidates.append(Candidate(
                    candidate_id=f"cand_{module.get('name', 'mod')}_B3_jvm_unit",
                    test_category="B3_jvm_unit",
                    tool="gradle",
                    argv=[f"{gradle_path}:testDebugUnitTest", "--offline"],
                    cwd=cwd,
                    purpose="Run module JVM/unit tests on the local JVM (no device).",
                    evidence_refs=evidence_test,
                    side_effects=TEST_CATEGORIES["B3_jvm_unit"]["side_effects"],
                    risk_level=TEST_CATEGORIES["B3_jvm_unit"]["risk_level"],
                    required_approval=TEST_CATEGORIES["B3_jvm_unit"]["required_approval"],
                ))

            # B4 — Instrumentation tests: only if an `androidTest` source set exists.
            if "androidTest" in module.get("source_sets", {}):
                evidence_instr = evidence + [
                    {"rel_path": module["source_sets"]["androidTest"], "kind": "instrumentation_source_set", "confidence": "VERIFIED_FACT"},
                ]
                candidates.append(Candidate(
                    candidate_id=f"cand_{module.get('name', 'mod')}_B4_instrumentation",
                    test_category="B4_instrumentation",
                    tool="gradle",
                    argv=[f"{gradle_path}:connectedDebugAndroidTest"],
                    cwd=cwd,
                    purpose="Run on-device instrumentation tests (requires emulator/device).",
                    evidence_refs=evidence_instr,
                    side_effects=TEST_CATEGORIES["B4_instrumentation"]["side_effects"],
                    risk_level=TEST_CATEGORIES["B4_instrumentation"]["risk_level"],
                    required_approval=TEST_CATEGORIES["B4_instrumentation"]["required_approval"],
                ))

            # B5 — APK build: only if the module carries a build file (Android application/library).
            if module.get("build_file"):
                candidates.append(Candidate(
                    candidate_id=f"cand_{module.get('name', 'mod')}_B5_apk_build",
                    test_category="B5_apk_build",
                    tool="gradle",
                    argv=[f"{gradle_path}:assembleDebug"],
                    cwd=cwd,
                    purpose="Assemble a debug APK/AAB artifact for integrity verification.",
                    evidence_refs=evidence + [{"rel_path": module.get("build_file"), "kind": "module_build_file", "confidence": "VERIFIED_FACT"}],
                    side_effects=TEST_CATEGORIES["B5_apk_build"]["side_effects"],
                    risk_level=TEST_CATEGORIES["B5_apk_build"]["risk_level"],
                    required_approval=TEST_CATEGORIES["B5_apk_build"]["required_approval"],
                ))

        return candidates

    def to_dict(self) -> dict[str, Any]:
        candidates = self.plan()
        return {
            "schema_version": SCHEMA_VERSION,
            "model": "CommandCandidates",
            "project_root": self.project_root,
            "execution_status_note": "All candidates are NOT_EXECUTED plans; no command was run by this module.",
            "candidate_count": len(candidates),
            "candidates": [c.to_dict() for c in candidates],
        }


def plan_commands(profile: dict[str, Any]) -> dict[str, Any]:
    """Convenience wrapper returning a JSON-serialisable candidate dict."""
    return CommandPlanner(profile).to_dict()
