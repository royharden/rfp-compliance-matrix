"""Explicit package-source integrity and conservative process-context pins.

No filesystem, environment, distribution, SDK or database operation at import.
Collection is explicit; a descriptor never proves execution or authenticity.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
import unicodedata
from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, canonical_json, digest

SOURCE_PROFILE = "m3-source-components-v1"
SEMANTIC_PROFILE = "m3-semantic-policy-v1"
RENDER_PROFILE = "m3-render-policy-v1"
CANONICAL_POLICY = "m3-python-ast-no-positions-comments-docstrings-and-text-lf-v1"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
GROUPS = {
    "parse": ("source/__init__.py", "source/artifacts.py", "source/structure.py", "source/coverage.py", "source/policy.py"),
    "segment": ("source/structure.py", "evidence/engine.py", "evidence/inventory.py"),
    "retrieve": ("evidence/engine.py", "evidence/consumer.py", "evidence/changes.py"),
    "judge": ("providers/ports.py", "providers/config.py", "providers/live.py", "providers/dispatch.py",
              "providers/envelope.py", "providers/ledger.py", "providers/tape.py"),
    "validate": ("contracts/__init__.py", "__init__.py", "providers/__init__.py", "source/__init__.py", "evidence/__init__.py", "source/policy.py",
                 "source/coverage.py", "evidence/consumer.py", "evidence/amendments.py",
                 "review/store.py", "review/application.py", "review/currentness.py", "review/reassessment.py",
                 "review/inventory.py", "review/graph.py", "provenance.py"),
    "render": ("export/__init__.py", "export/bundle.py"),
    "ui": ("web/presentation.py", "web/app.py", "web/routes.py", "web/security.py", "web/static/reviewer.css",
           "web/templates/base.html", "web/templates/_parts.html", "web/templates/_amendment_parts.html",
           "web/templates/login.html", "web/templates/runs.html", "web/templates/matrix.html",
           "web/templates/row.html", "web/templates/coverage.html", "web/templates/exports.html",
           "web/templates/inventory.html", "web/templates/amendments.html", "web/templates/error.html"),
}
LIBRARIES = {"parse": ("pdfplumber",), "segment": (), "retrieve": (), "judge": ("typesafe-sdk",),
             "validate": ("pydantic",), "render": (), "ui": ("fastapi", "jinja2", "uvicorn")}
OUTER_FIELDS = {"schema_version", "profile", "status", "fingerprint", "identity", "restart_required",
                "reasons", "execution_verified", "attestation", "descriptor_sha256"}
_LOCK = threading.RLock()
_PINS: dict[str, str | None] = {}
_RESTART: set[str] = set()


class _WithoutDocstrings(ast.NodeTransformer):
    def _body(self, node):
        self.generic_visit(node)
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:]
        return node
    visit_Module = _body
    visit_ClassDef = _body
    visit_FunctionDef = _body
    visit_AsyncFunctionDef = _body


def canonical_source(data: bytes, *, python: bool) -> bytes:
    """Canonical content only. Never compiles/imports/executes the supplied AST."""
    if len(data) > MAX_SOURCE_BYTES:
        raise M3Error("provenance_limit", "Source exceeds the explicit component byte cap")
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if not python:
        return text.encode("utf-8")
    tree = _WithoutDocstrings().visit(ast.parse(text))
    return ast.dump(tree, annotate_fields=True, include_attributes=False).encode("utf-8")


def _dependency(name: str) -> dict[str, Any]:
    try:
        version = importlib.metadata.version(name)
        if not isinstance(version, str) or not 1 <= len(version) <= 200:
            raise ValueError("metadata")
        return {"status": "available", "version": version, "reason": None}
    except importlib.metadata.PackageNotFoundError:
        return {"status": "unavailable", "version": None, "reason": "distribution_not_installed"}
    except Exception:
        return {"status": "unavailable", "version": None, "reason": "distribution_metadata_unavailable"}


def _process_start_ns() -> int | None:
    """Own-process metadata only; Windows primary API / Linux proc fallback.

    Other platforms fail currentness closed until a real start-time adapter is
    supplied. No guessed import timestamp is used as actual process creation.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            class FT(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FT)] * 4
            kernel.GetProcessTimes.restype = wintypes.BOOL
            created, exited, system, user = FT(), FT(), FT(), FT()
            if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
                return None
            ticks = (created.high << 32) | created.low
            return ticks * 100 - 11644473600 * 1_000_000_000
        if sys.platform.startswith("linux") and hasattr(time, "CLOCK_BOOTTIME"):
            with open("/proc/self/stat", encoding="ascii") as stream:
                fields = stream.read(8192).rsplit(")", 1)[1].split()
            ticks = int(fields[19])
            frequency = int(os.sysconf("SC_CLK_TCK"))
            boot_ns = time.time_ns() - int(time.clock_gettime(time.CLOCK_BOOTTIME) * 1_000_000_000)
            return boot_ns + ticks * 1_000_000_000 // frequency
    except Exception:
        return None
    return None


def _read_source(relative: str) -> tuple[dict[str, Any], int | None]:
    entry = {"path": relative, "kind": "python_ast" if relative.endswith(".py") else "text_lf",
             "status": "unavailable", "sha256": None, "reason": "source_unavailable"}
    try:
        root = Path(__file__).resolve(strict=True).parent
        target = root.joinpath(*relative.split("/"))
        for parent in [target, *target.parents]:
            if parent == root:
                break
            if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                raise ValueError("source_link")
        if not target.resolve(strict=True).is_relative_to(root) or not target.is_file():
            raise ValueError("source_path")
        before = target.stat()
        if before.st_size > MAX_SOURCE_BYTES:
            raise ValueError("source_limit")
        with target.open("rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("source_changed_during_read")
        normalized = canonical_source(data, python=relative.endswith(".py"))
        entry.update(status="available", sha256=hashlib.sha256(normalized).hexdigest(), reason=None)
        return entry, after.st_mtime_ns
    except Exception:
        return entry, None


def _collect() -> tuple[dict[str, Any], dict[str, list[int | None]], int | None]:
    files = {name: _read_source(name) for name in sorted({name for paths in GROUPS.values() for name in paths})}
    dependencies = {name: _dependency(name) for name in sorted({name for names in LIBRARIES.values() for name in names})}
    components, modified = {}, {}
    for group, names in GROUPS.items():
        records = [files[name][0] for name in sorted(names)]
        body = {"status": "available" if all(row["status"] == "available" for row in records) else "unavailable",
                "files": records, "dependencies": {name: dependencies[name] for name in LIBRARIES[group]}}
        components[group] = {**body, "fingerprint": digest("m3.source-component.v1", body) if body["status"] == "available" else None}
        modified[group] = [files[name][1] for name in names]
    runtime = {"python_implementation": platform.python_implementation(), "python_version": platform.python_version(),
               "unicode_version": unicodedata.unidata_version, "platform": sys.platform,
               "byteorder": sys.byteorder, "canonical_policy": CANONICAL_POLICY}
    return {"method": None, "parser_profile": None, "components": components, "runtime": runtime}, modified, _process_start_ns()


def _descriptor(profile: str, identity: dict[str, Any], *, status: str, reasons: list[str]) -> dict[str, Any]:
    available = all(item["status"] == "available" for item in identity["components"].values())
    # Policy identity excludes process-guard state. A restart of identical source
    # and library metadata cannot change this deterministic fingerprint.
    binding = {"profile": profile, "identity": identity}
    result = {"schema_version": 1, "profile": profile, "status": status,
        "fingerprint": digest("m3.fingerprint-descriptor.v1", binding) if available else None,
        "identity": identity, "restart_required": status == "restart_required", "reasons": reasons,
        "execution_verified": False, "attestation": False}
    result["descriptor_sha256"] = digest("m3.fingerprint-descriptor-record.v1", result)
    return result


def _pin(identity: Mapping[str, Any], modified: Mapping[str, list[int | None]], started: int | None,
         selected: tuple[str, ...]) -> tuple[str, list[str]]:
    reasons = []
    missing = False
    with _LOCK:
        for group in selected:
            pin_name = group + (":stdlib" if group == "judge" and identity["method"] == "F2" else ":typesafe" if group == "judge" else "")
            component = identity["components"][group]
            signature = digest("m3.process-component-context.v1", {"component": component["fingerprint"], "runtime": identity["runtime"]}) if component["fingerprint"] else None
            if pin_name not in _PINS:
                _PINS[pin_name] = signature
                if started is None:
                    _RESTART.add(pin_name)
                    reasons.append("process_start_unavailable:" + group)
                elif any(value is not None and value > started for value in modified[group]):
                    _RESTART.add(pin_name)
                    reasons.append("source_newer_than_process_start:" + group)
            elif signature != _PINS[pin_name]:
                _RESTART.add(pin_name)
                reasons.append("process_component_changed:" + group)
            if component["status"] != "available":
                missing = True
                reasons.append("component_unavailable:" + group)
            if pin_name in _RESTART:
                reasons.append("restart_required:" + group)
        restart = any((group + (":stdlib" if group == "judge" and identity["method"] == "F2" else ":typesafe" if group == "judge" else "")) in _RESTART for group in selected)
    return ("restart_required" if restart else "unavailable" if missing else "available"), sorted(set(reasons))


def source_fingerprints() -> dict[str, Any]:
    """Explicit disk/metadata collection, not a loaded/executed-code attestation."""
    identity, _, _ = _collect()
    missing = ["component_unavailable:" + name for name, value in identity["components"].items() if value["status"] != "available"]
    return _descriptor(SOURCE_PROFILE, identity, status="unavailable" if missing else "available", reasons=missing)


def _parser_profile(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    allowed = {"parser", "parser_version", "normalization_policy", "parse_config", "policy_hash"}
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise M3Error("provenance_parser_profile", "Parser profile must use code-owned fields")
    for name, supplied in value.items():
        if name == "parse_config":
            fields = {"max_bytes", "max_pages", "max_text_codepoints", "sparse_floor", "timeout_seconds", "cpu_seconds", "memory_bytes"}
            if not isinstance(supplied, Mapping) or set(supplied) - fields or any(type(v) is not int or not 0 < v <= 1024**3 for v in supplied.values()):
                raise M3Error("provenance_parser_profile", "Parser caps are closed positive integers")
        elif not isinstance(supplied, str) or not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,200}", supplied):
            raise M3Error("provenance_parser_profile", "Parser profile must use code-owned fields")
    return json.loads(canonical_json(dict(value)))


def semantic_policy_fingerprint(method: str, parser_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(method, str) or method not in {"B0a", "B0b", "F1", "F2"}:
        raise M3Error("provenance_method", "Unknown semantic method")
    profile = _parser_profile(parser_profile)
    identity, modified, started = _collect()
    selected = ("parse", "segment", "retrieve", "validate") + (() if method == "B0a" else ("judge",))
    identity.update(method=method, parser_profile=profile)
    if method == "F2":
        judge = identity["components"]["judge"]
        body = {k: v for k, v in judge.items() if k != "fingerprint"}
        body["dependencies"] = {}  # F2 uses stdlib; unrelated SDK presence is not its policy.
        identity["components"]["judge"] = {**body, "fingerprint": digest("m3.source-component.v1", body) if body["status"] == "available" else None}
    status, reasons = _pin(identity, modified, started, selected)
    identity.update(method=method, parser_profile=profile,
                    components={name: identity["components"][name] for name in selected})
    return _descriptor(SEMANTIC_PROFILE, identity, status=status, reasons=reasons)


def render_policy_fingerprint() -> dict[str, Any]:
    identity, modified, started = _collect()
    selected = ("render", "ui")
    status, reasons = _pin(identity, modified, started, selected)
    identity["components"] = {name: identity["components"][name] for name in selected}
    return _descriptor(RENDER_PROFILE, identity, status=status, reasons=reasons)


def verify_fingerprint_descriptor(value: Any, *, profile: str | None = None) -> bool:
    """Pure closed integrity check, never verifies current disk/process or author."""
    try:
        if not isinstance(value, Mapping) or set(value) != OUTER_FIELDS:
            return False
        if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["profile"] not in {SOURCE_PROFILE, SEMANTIC_PROFILE, RENDER_PROFILE}
                or (profile is not None and value["profile"] != profile)
                or value["status"] not in {"available", "unavailable", "restart_required"}
                or value["restart_required"] is not (value["status"] == "restart_required")
                or value["execution_verified"] is not False or value["attestation"] is not False):
            return False
        identity = value["identity"]
        if not isinstance(identity, Mapping) or set(identity) != {"method", "parser_profile", "components", "runtime"}:
            return False
        if value["profile"] != SEMANTIC_PROFILE and (identity["method"] is not None or identity["parser_profile"] is not None):
            return False
        if _parser_profile(identity["parser_profile"]) != identity["parser_profile"]:
            return False
        runtime = identity["runtime"]
        if not isinstance(runtime, Mapping) or set(runtime) != {"python_implementation", "python_version", "unicode_version", "platform", "byteorder", "canonical_policy"}:
            return False
        if runtime["canonical_policy"] != CANONICAL_POLICY or any(not isinstance(v, str) or not 1 <= len(v) <= 200 for v in runtime.values()):
            return False
        components = identity["components"]
        expected = set(GROUPS) if value["profile"] == SOURCE_PROFILE else {"render", "ui"} if value["profile"] == RENDER_PROFILE else {"parse", "segment", "retrieve", "validate"} | ({"judge"} if identity["method"] in {"B0b", "F1", "F2"} else set())
        if not isinstance(components, Mapping) or set(components) != expected:
            return False
        if value["profile"] == SEMANTIC_PROFILE and identity["method"] not in {"B0a", "B0b", "F1", "F2"}:
            return False
        all_available = True
        for name, component in components.items():
            if not isinstance(component, Mapping) or set(component) != {"status", "files", "dependencies", "fingerprint"}:
                return False
            if component["status"] not in {"available", "unavailable"} or not isinstance(component["files"], list):
                return False
            if [item.get("path") for item in component["files"]] != sorted(GROUPS[name]):
                return False
            for item in component["files"]:
                if set(item) != {"path", "kind", "status", "sha256", "reason"} or item["status"] not in {"available", "unavailable"}:
                    return False
                if item["kind"] != ("python_ast" if item["path"].endswith(".py") else "text_lf"):
                    return False
                if item["status"] == "available":
                    if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) or item["reason"] is not None:
                        return False
                elif item["sha256"] is not None or item["reason"] != "source_unavailable":
                    return False
            dependencies = component["dependencies"]
            expected_libraries = () if value["profile"] == SEMANTIC_PROFILE and identity["method"] == "F2" and name == "judge" else LIBRARIES[name]
            if not isinstance(dependencies, Mapping) or set(dependencies) != set(expected_libraries):
                return False
            for metadata in dependencies.values():
                if set(metadata) != {"status", "version", "reason"}:
                    return False
                if metadata["status"] == "available":
                    if not isinstance(metadata["version"], str) or not 1 <= len(metadata["version"]) <= 200 or metadata["reason"] is not None:
                        return False
                elif metadata["status"] != "unavailable" or metadata["version"] is not None or metadata["reason"] not in {"distribution_not_installed", "distribution_metadata_unavailable"}:
                    return False
            present = all(row["status"] == "available" for row in component["files"])
            body = {k: v for k, v in component.items() if k != "fingerprint"}
            if component["status"] != ("available" if present else "unavailable") or component["fingerprint"] != (digest("m3.source-component.v1", body) if present else None):
                return False
            all_available &= present
        if value["status"] == "available" and not all_available:
            return False
        expected_hash = digest("m3.fingerprint-descriptor.v1", {"profile": value["profile"], "identity": identity}) if all_available else None
        if value["fingerprint"] != expected_hash:
            return False
        if not isinstance(value["reasons"], list) or len(value["reasons"]) > 100 or any(not isinstance(reason, str) or len(reason) > 4000 for reason in value["reasons"]):
            return False
        if len(canonical_json(dict(value)).encode("utf-8")) > 1024 * 1024:
            return False
        record = {k: v for k, v in value.items() if k != "descriptor_sha256"}
        if value["descriptor_sha256"] != digest("m3.fingerprint-descriptor-record.v1", record):
            return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        return False
