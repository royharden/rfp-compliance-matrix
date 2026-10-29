"""Explicit native source descriptors sealed as integrity metadata, never execution proof.

No collection or optional dependency import occurs on import/construction. Full
native allowlisted descriptors and normalized contexts are sealed; no source text,
arbitrary paths, secrets or private gold are accepted as descriptor fields.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Protocol, Sequence

METHODS = ("B0a", "B0b", "F1", "F2")
PROFILE = "m3-evaluation-build-provenance-v1"


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _dependencies(raw: Mapping[str, Any]) -> dict[str, Any]:
    dependencies = {}
    for component in raw.get("identity", {}).get("components", {}).values():
        for name, dependency in component.get("dependencies", {}).items():
            item = {"status": dependency["status"], "version": dependency["version"]}
            if name in dependencies and dependencies[name] != item:
                raise ValueError("Conflicting distribution metadata")
            dependencies[name] = item
    return dependencies


def _member(raw: Any, *, parser_profile: Any, collected: bool, checked: bool = False,
            semantic_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from .runner import fingerprint
    descriptor_sha = fingerprint(dict(raw)) if checked and isinstance(raw, Mapping) else None
    value = raw if checked and isinstance(raw, Mapping) else {}
    dependencies = _dependencies(value)
    restart = value.get("restart_required") is True or value.get("status") == "restart_required"
    available = value.get("status") == "available" and _sha(value.get("fingerprint"))
    return {"status": "restart_required" if restart else "available" if available else "unavailable",
            "fingerprint": value["fingerprint"] if (available or restart) and _sha(value.get("fingerprint")) else None,
            "descriptor_sha": descriptor_sha, "parser_profile_sha": fingerprint(parser_profile),
            "descriptor": dict(raw) if checked else None,
            "semantic_context": dict(semantic_context) if checked and semantic_context is not None else None,
            "native_integrity_checked": checked, "dependencies": dependencies,
            "reason_code": "restart_required" if restart else "integrity_only" if available
                           else "native_unavailable" if collected else "not_collected"}


def _assemble(semantic: Mapping[str, Any], render: Mapping[str, Any], *, collected: bool) -> dict[str, Any]:
    from .runner import fingerprint
    value = {"schema_version": 1, "profile": PROFILE, "collection": "explicit_native_operation" if collected else "not_collected",
             "semantic": dict(semantic), "render": dict(render), "execution_verified": False,
             "authenticity": "not_attested"}
    value["build_sha"] = fingerprint(value)
    return value


def validate_build_provenance(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pure shape/digest validation; neither collects files nor authenticates metadata."""
    from .runner import canonical, fingerprint, require_digest
    if value is None:
        return _assemble({method: _member(None, parser_profile=None, collected=False) for method in METHODS},
                         _member(None, parser_profile=None, collected=False), collected=False)
    result = json.loads(canonical(value))
    fields = {"schema_version", "profile", "collection", "semantic", "render", "execution_verified", "authenticity", "build_sha"}
    if (not isinstance(result, dict) or set(result) != fields or type(result["schema_version"]) is not int
            or result["schema_version"] != 1 or result["profile"] != PROFILE or result["execution_verified"] is not False
            or result["authenticity"] != "not_attested" or result["collection"] not in ("not_collected", "explicit_native_operation")
            or not isinstance(result["semantic"], dict) or set(result["semantic"]) != set(METHODS)):
        raise ValueError("Expected closed unverified build provenance")
    for method, member in [*result["semantic"].items(), (None, result["render"])]:
        if not isinstance(member, dict) or set(member) != {"status", "fingerprint", "descriptor_sha", "parser_profile_sha", "reason_code", "native_integrity_checked", "dependencies", "descriptor", "semantic_context"}:
            raise ValueError("Unknown native descriptor fields cannot enter public receipts")
        if type(member["native_integrity_checked"]) is not bool or not isinstance(member["dependencies"], dict):
            raise ValueError("Explicit native integrity/optional distribution metadata required")
        for name, dependency in member["dependencies"].items():
            if name not in {"pdfplumber", "pydantic", "typesafe-sdk", "fastapi", "jinja2", "uvicorn"} or not isinstance(dependency, dict) or set(dependency) != {"status", "version"}:
                raise ValueError("Unknown distribution metadata is not public provenance")
            if dependency["status"] == "unavailable":
                if dependency["version"] is not None:
                    raise ValueError("Missing distribution cannot have a fabricated version")
            elif dependency["status"] != "available" or not isinstance(dependency["version"], str) or not 1 <= len(dependency["version"]) <= 200:
                raise ValueError("Invalid distribution provenance")
        if member["status"] not in ("available", "unavailable", "restart_required") or member["reason_code"] not in ("restart_required", "integrity_only", "native_unavailable", "not_collected"):
            raise ValueError("Unknown descriptor status")
        require_digest(member["parser_profile_sha"])
        for field in ("fingerprint", "descriptor_sha"):
            if member[field] is not None:
                require_digest(member[field])
        if member["native_integrity_checked"]:
            from rfp_matrix.provenance import verify_fingerprint_descriptor, SEMANTIC_PROFILE, RENDER_PROFILE
            raw = member["descriptor"]
            if (not verify_fingerprint_descriptor(raw, profile=SEMANTIC_PROFILE if method else RENDER_PROFILE)
                    or fingerprint(raw) != member["descriptor_sha"]):
                raise ValueError("Native source descriptor integrity/profile changed")
            expected_status = raw["status"]
            expected_fingerprint = raw["fingerprint"] if _sha(raw["fingerprint"]) else None
            if (member["status"] != expected_status or member["fingerprint"] != expected_fingerprint
                    or member["dependencies"] != _dependencies(raw)):
                raise ValueError("Summary cannot change native status/fingerprint/dependency facts")
            if method is not None:
                _validate_context(member["semantic_context"], method=method, descriptor=raw)
                if fingerprint(member["semantic_context"]["parser_profile"]) != member["parser_profile_sha"]:
                    raise ValueError("Parser profile differs from normalized native context")
            elif member["semantic_context"] is not None:
                raise ValueError("Renderer cannot carry a semantic assessment context")
            elif member["parser_profile_sha"] != fingerprint(None):
                raise ValueError("Renderer cannot acquire a parser profile")
        elif member["descriptor"] is not None or member["semantic_context"] is not None or member["dependencies"]:
            raise ValueError("Unchecked arbitrary metadata cannot masquerade as native provenance")
        if member["status"] == "available" and (member["fingerprint"] is None or member["descriptor_sha"] is None or not member["native_integrity_checked"]):
            raise ValueError("Available integrity descriptor needs native fingerprint and descriptor digest")
        if member["status"] == "unavailable" and member["fingerprint"] is not None:
            raise ValueError("Missing descriptor cannot advertise a verified hash")
        if member["status"] == "available" and member["reason_code"] != "integrity_only":
            raise ValueError("Available descriptor cannot hide missing/restart state")
        if member["status"] == "restart_required" and member["reason_code"] != "restart_required":
            raise ValueError("Restart-required provenance must remain explicit")
        if result["collection"] == "not_collected" and (member["status"] != "unavailable" or member["descriptor_sha"] is not None
                or member["reason_code"] != "not_collected" or member["parser_profile_sha"] != fingerprint(None)
                or member["native_integrity_checked"] or member["dependencies"]):
            raise ValueError("Uncollected source cannot be relabelled as available provenance")
    require_digest(result["build_sha"])
    if fingerprint({k: v for k, v in result.items() if k != "build_sha"}) != result["build_sha"]:
        raise ValueError("Build descriptor changed after collection")
    return result


def _validate_context(value: Any, *, method: str, descriptor: Mapping[str, Any]) -> None:
    from rfp_matrix.contracts import digest
    fields = {"schema_version", "profile", "method", "parser_profile", "status", "fingerprint", "descriptor",
              "reasons", "execution_verified", "context_sha"}
    if (not isinstance(value, Mapping) or set(value) != fields or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["profile"] != "m3-semantic-context-v1"
            or value["method"] != method or descriptor["identity"]["method"] != method
            or value["parser_profile"] != descriptor["identity"]["parser_profile"]
            or value["descriptor"] != descriptor or value["execution_verified"] is not False
            or value["status"] not in ("current", "missing", "restart_required")):
        raise ValueError("Expected full normalized source-policy semantic context")
    if digest("m3.semantic-context.v1", {k: v for k, v in value.items() if k != "context_sha"}) != value["context_sha"]:
        raise ValueError("Normalized semantic context changed")
    expected = "restart_required" if descriptor["status"] == "restart_required" else "current" if descriptor["status"] == "available" and _sha(descriptor["fingerprint"]) else "missing"
    if value["status"] != expected or value["fingerprint"] != (descriptor["fingerprint"] if _sha(descriptor["fingerprint"]) else None):
        raise ValueError("Normalized status cannot upgrade unavailable source")


def collect_build_provenance(*, parser_profiles: Mapping[str, Any] | None = None,
                             semantic_collector: Callable[..., Any] | None = None,
                             render_collector: Callable[..., Any] | None = None,
                             descriptor_verifier: Callable[..., bool] | None = None) -> dict[str, Any]:
    """Explicit future native collection; never called automatically by constructor.

    Native collection owns package allowlists, LF/CRLF policy, Python/Unicode/
    library metadata and process restart semantics. This adapter duplicates none
    of that algorithm and never imports an SDK to discover a version.
    """
    from .runner import canonical
    profiles = json.loads(canonical(dict(parser_profiles or {})))
    if set(profiles) - set(METHODS) or len(canonical(profiles).encode("utf-8")) > 65536:
        raise ValueError("Unknown/unbounded parser-profile context")
    if semantic_collector is None or render_collector is None or descriptor_verifier is None:
        try:
            from rfp_matrix.provenance import render_policy_fingerprint, verify_fingerprint_descriptor
            from rfp_matrix.source.policy import policy_context
            semantic_collector = semantic_collector or policy_context
            render_collector = render_collector or render_policy_fingerprint
            descriptor_verifier = descriptor_verifier or verify_fingerprint_descriptor
        except Exception:
            # Missing optional/native source is a visible unavailable descriptor.
            pass
    semantic = {}
    for method in METHODS:
        try:
            context = semantic_collector(method, parser_profile=profiles.get(method)) if semantic_collector else None
            raw = context.get("descriptor") if isinstance(context, Mapping) else None
            checked = bool(descriptor_verifier and descriptor_verifier(raw, profile="m3-semantic-policy-v1") is True
                and raw["identity"]["method"] == method and raw["identity"]["parser_profile"] == profiles.get(method))
            if checked:
                _validate_context(context, method=method, descriptor=raw)
            semantic[method] = _member(raw, parser_profile=profiles.get(method), collected=True, checked=checked, semantic_context=context)
        except Exception:
            semantic[method] = _member(None, parser_profile=profiles.get(method), collected=True)
    try:
        raw = render_collector() if render_collector else None
        checked = bool(descriptor_verifier and descriptor_verifier(raw, profile="m3-render-policy-v1") is True)
        render = _member(raw, parser_profile=None, collected=True, checked=checked)
    except Exception:
        render = _member(None, parser_profile=None, collected=True)
    return validate_build_provenance(_assemble(semantic, render, collected=True))


class BuildObserver(Protocol):
    def observe(self) -> Mapping[str, Any]: ...


class NativeBuildObserver:
    """No I/O constructor. observe is an explicit native source operation."""

    def __init__(self, *, parser_profiles: Mapping[str, Any] | None = None) -> None:
        from .runner import canonical
        self._profiles_json = canonical(dict(parser_profiles or {}))

    def observe(self) -> Mapping[str, Any]:
        return collect_build_provenance(parser_profiles=json.loads(self._profiles_json))


def compare_build_provenance(recorded: Mapping[str, Any], observed: Mapping[str, Any] | None, *,
                             active_methods: Sequence[str]) -> dict[str, Any]:
    """Separate semantic and renderer drift; native availability is not execution proof."""
    old, now = validate_build_provenance(recorded), validate_build_provenance(observed)
    if set(active_methods) - set(METHODS):
        raise ValueError("Unknown active method for provenance guard")
    def status(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
        if left["status"] == "restart_required" or right["status"] == "restart_required":
            return "restart_required"
        if left["status"] != "available":
            return "legacy_unverified"
        if right["status"] != "available":
            return "missing"
        return "current_integrity_only" if (left["fingerprint"], left["parser_profile_sha"]) == (right["fingerprint"], right["parser_profile_sha"]) else "mismatch"
    semantic = {method: status(old["semantic"][method], now["semantic"][method]) for method in active_methods}
    render = status(old["render"], now["render"])
    blocking = {"restart_required", "missing", "mismatch"}
    return {"semantic": semantic, "render": render,
            "allows_sealed_run": not any(s in blocking for s in semantic.values()) and (not active_methods or render not in blocking),
            "all_active_integrity_current": bool(active_methods) and all(s == "current_integrity_only" for s in semantic.values())
                                            and render == "current_integrity_only",
            "recorded_build_sha": old["build_sha"], "observed_build_sha": now["build_sha"],
            "execution_verified": False, "authenticity": "not_attested"}
