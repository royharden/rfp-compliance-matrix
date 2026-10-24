"""Explicit provenance collection and pure historical/current policy comparison.

No I/O at import or constructor time. Fingerprints are integrity descriptors,
never proof of executed assessment, trusted attestation or reviewer authority.
"""
from __future__ import annotations

from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, digest


def _fingerprint(value: Any) -> str | None:
    return value if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value) else None


def _context(method: str, profile: Any, descriptor: Any) -> dict[str, Any]:
    raw = dict(descriptor) if isinstance(descriptor, Mapping) else {}
    valid = _descriptor_valid(raw, method, profile)
    fingerprint = _fingerprint(raw.get("fingerprint")) if valid else None
    restart = valid and (raw.get("restart_required") is True or raw.get("status") == "restart_required")
    available = valid and raw.get("status") == "available" and fingerprint is not None
    status = "restart_required" if restart else ("current" if available else "missing")
    reasons = raw.get("reasons") if isinstance(raw.get("reasons"), list) else []
    reasons = [item[:4000] for item in reasons[:100] if isinstance(item, str)]
    if not reasons and status != "current":
        reasons = ["Provenance is unavailable, unrecognized or requires a safe process restart."]
    value = {"schema_version": 1, "profile": "m3-semantic-context-v1", "method": method,
        "parser_profile": profile, "status": status, "fingerprint": fingerprint,
        "descriptor": raw if valid else None, "reasons": reasons, "execution_verified": False}
    value["context_sha"] = digest("m3.semantic-context.v1", value)
    return value


def _descriptor_valid(descriptor: Any, method: str, profile: Any) -> bool:
    try:
        from rfp_matrix.provenance import SEMANTIC_PROFILE, verify_fingerprint_descriptor
        return (verify_fingerprint_descriptor(descriptor, profile=SEMANTIC_PROFILE)
                and descriptor["identity"]["method"] == method and descriptor["identity"]["parser_profile"] == profile)
    except Exception:
        return False


def _context_valid(value: Mapping[str, Any]) -> bool:
    if not _descriptor_valid(value.get("descriptor"), value.get("method"), value.get("parser_profile")):
        return False
    content = {key: item for key, item in value.items() if key != "context_sha"}
    return (value.get("context_sha") == digest("m3.semantic-context.v1", content)
            and value.get("fingerprint") == value["descriptor"]["fingerprint"])


def policy_context(method: str, parser_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Explicit semantic operation: collect the provenance layer's descriptor, with no SDK dispatch."""
    if method not in {"B0a", "B0b", "F1", "F2"}:
        raise M3Error("unknown_policy_method", "Unknown semantic method")
    profile = dict(parser_profile) if parser_profile is not None else None
    try:
        from rfp_matrix.provenance import semantic_policy_fingerprint
        descriptor = semantic_policy_fingerprint(method, parser_profile=profile)
    except Exception as exc:
        descriptor = {"status": "unavailable", "reasons": ["Source provenance collection failed: " + type(exc).__name__]}
    return _context(method, profile, descriptor)


def policy_identity(context: Mapping[str, Any]) -> dict[str, Any]:
    """Stable semantic identity; process guard/reasons belong to record integrity.

    A missing fingerprint stays null. This is never a currentness verdict.
    """
    return {"profile": context.get("profile"), "method": context.get("method"),
            "parser_profile": context.get("parser_profile"), "fingerprint": _fingerprint(context.get("fingerprint"))}


def policy_status(recorded: Any, current: Any) -> dict[str, Any]:
    """Pure necessary currentness guard. Never executes/relabels old evidence."""
    old = recorded if isinstance(recorded, Mapping) else {}
    if isinstance(old.get("semantic"), Mapping):
        old = old["semantic"]
    now = current if isinstance(current, Mapping) else {}
    status = "current"
    reasons = []
    if now.get("status") == "restart_required":
        status, reasons = "restart_required", ["Source changed relative to the process semantic context; restart before reassessment."]
    elif (now.get("profile") != "m3-semantic-context-v1" or now.get("schema_version") != 1
          or now.get("status") != "current" or _fingerprint(now.get("fingerprint")) is None or not _context_valid(now)):
        status, reasons = "missing", ["Current semantic fingerprint is unavailable; current approval cannot be asserted."]
    elif not old:
        status, reasons = "legacy", ["Historical content has no recorded semantic provenance; reimport/reassess explicitly."]
    elif old.get("profile") != "m3-semantic-context-v1" or old.get("schema_version") != 1:
        status, reasons = "legacy", ["Historical semantic provenance uses an unknown profile; do not reinterpret it as current."]
    elif old.get("status") == "restart_required":
        status, reasons = "restart_required", ["Recorded work was created in a drifted process; no fresh execution is established."]
    elif old.get("status") != "current" or _fingerprint(old.get("fingerprint")) is None or not _context_valid(old):
        status, reasons = "missing", ["Recorded semantic fingerprint is unavailable; hashing old evidence again cannot repair it."]
    elif (old.get("fingerprint") != now.get("fingerprint") or old.get("method") != now.get("method")
          or old.get("parser_profile") != now.get("parser_profile")):
        status, reasons = "mismatch", ["Semantic source, parser/profile or method changed; obtain a new actual assessment."]
    return {"schema_version": 1, "status": status, "reasons": reasons,
        "recorded_fingerprint": _fingerprint(old.get("fingerprint")), "current_fingerprint": _fingerprint(now.get("fingerprint")),
        "allows_current_claim": status == "current", "execution_verified": False,
        "action": "none" if status == "current" else ("restart_then_reassess" if status == "restart_required" else "reimport_or_reassess" )}
