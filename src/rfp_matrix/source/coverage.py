"""Pure, versioned human coverage inspection; persistence/authentication belong to review.

These records supplement immutable artifacts. They never change extracted strings,
parser coverage or source identity. A human acknowledgement is not OCR or evidence
that a vendor satisfies a requirement.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from rfp_matrix.contracts import M3Error, digest
from .artifacts import artifact_notices, validate_artifact

COVERAGE_POLICY = "m3-human-original-page-inspection-v1"
_FIELDS = frozenset({"schema_version", "policy", "artifact_sha", "page_index", "page_sha",
                     "decision", "reason", "actor", "request_id", "expected_revision",
                     "previous_revision", "inspected_original", "revision"})
_INSPECTABLE = frozenset({"native_text_sparse", "no_native_text",
                         "table_reading_order_requires_inspection", "possible_columns_require_inspection"})


def _page(artifact: Mapping[str, Any], page_index: int) -> Mapping[str, Any]:
    if type(page_index) is not int or not 0 <= page_index < len(artifact["pages"]):
        raise M3Error("invalid_coverage_page", "Coverage page index is outside this artifact")
    return artifact["pages"][page_index]


def coverage_page_sha(artifact: Mapping[str, Any], page_index: int) -> str:
    """Bind all page content/structure/warnings, not just the extracted text."""
    value = validate_artifact(artifact)
    return digest("m3.coverage-page.v1", _page(value, page_index))


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise M3Error("invalid_coverage_decision", f"{name} must be nonblank and bounded")
    return value


def _check_action(page: Mapping[str, Any], action: str, inspected: bool) -> None:
    if action == "flag_unresolved":
        return
    if action not in {"accept_blank", "accept_text"} or inspected is not True:
        raise M3Error("coverage_inspection_required", "Acceptance requires explicit inspection of the original page")
    issues = set(page["issues"])
    # Whitelisting fails closed for future parser warnings, scans and broken text.
    if page["coverage"] == "unsupported" or issues - _INSPECTABLE:
        raise M3Error("coverage_not_clearable", "Unsupported/image/error/unknown page issues cannot be acknowledged as complete")
    has_text = any(not character.isspace() for character in page["text"])
    if action == "accept_blank" and (has_text or page["coverage"] != "empty" or issues - {"no_native_text"}):
        raise M3Error("coverage_not_blank", "Only an inspected genuinely blank page can be acknowledged")
    if action == "accept_text" and (not has_text or page["coverage"] not in {"ok", "sparse"}):
        raise M3Error("coverage_no_native_text", "Text acceptance requires inspectable native extracted text")


def _validate_event(artifact: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping) or set(event) != _FIELDS:
        raise M3Error("invalid_coverage_decision", "Unknown or missing coverage event fields")
    value = dict(event)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["policy"] != COVERAGE_POLICY:
        raise M3Error("invalid_coverage_policy", "Unsupported coverage policy/schema")
    page = _page(artifact, value["page_index"])
    if (value["artifact_sha"] != artifact["artifact_sha"] or
            value["page_sha"] != digest("m3.coverage-page.v1", page)):
        raise M3Error("stale_coverage", "Decision is bound to a different artifact or page")
    for field, limit in (("actor", 160), ("reason", 4000), ("request_id", 200)):
        _text(value[field], field, limit)
    for field in ("expected_revision", "previous_revision"):
        revision = value[field]
        if revision is not None and (not isinstance(revision, str) or len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision)):
            raise M3Error("invalid_coverage_revision", "Coverage revisions must be SHA-256 identities")
    if type(value["inspected_original"]) is not bool or not isinstance(value["decision"], str):
        raise M3Error("invalid_coverage_decision", "Invalid inspection or action type")
    _check_action(page, value["decision"], value["inspected_original"])
    content = {key: item for key, item in value.items() if key != "revision"}
    if value["revision"] != digest("m3.coverage-decision.v1", content):
        raise M3Error("invalid_coverage_revision", "Coverage decision content digest mismatch")
    return value


def make_coverage_decision(artifact: Mapping[str, Any], *, page_index: int,
                           decision: str, reason: str, actor: str, request_id: str,
                           expected_revision: str | None,
                           previous: Mapping[str, Any] | None = None,
                           inspected_original: bool = False) -> dict[str, Any]:
    """Prepare an immutable event after the application resolves its reviewer actor.

    Review must transact per-page CAS and durable request-id conflict detection. This
    function can validate supplied ancestry but cannot establish that it is current.
    Identical arguments produce identical bytes; timestamps belong in the enclosing
    application ledger rather than this deterministic event.
    """
    value = validate_artifact(artifact)
    page = _page(value, page_index)
    prior = _validate_event(value, previous) if previous is not None else None
    if prior is not None and prior["page_index"] != page_index:
        raise M3Error("coverage_conflict", "Previous coverage decision belongs to another page")
    prior_revision = prior["revision"] if prior else None
    if expected_revision != prior_revision:
        raise M3Error("coverage_conflict", "Expected coverage revision does not match supplied predecessor")
    event = {"schema_version": 1, "policy": COVERAGE_POLICY,
             "artifact_sha": value["artifact_sha"], "page_index": page_index,
             "page_sha": digest("m3.coverage-page.v1", page), "decision": decision,
             "reason": reason, "actor": actor, "request_id": request_id,
             "expected_revision": expected_revision, "previous_revision": prior_revision,
             "inspected_original": inspected_original}
    event["revision"] = digest("m3.coverage-decision.v1", event)
    return _validate_event(value, event)


def same_effective_coverage(replayed: Mapping[str, Any], frozen: Any) -> bool:
    """Exact replay equality for a frozen summary.

    A summary frozen before the derived, non-blocking ``artifact_notices`` field existed
    is compared without it (that field is a pure function of the bound artifact identity).
    Every other field must match exactly.
    """
    current = dict(replayed)
    if isinstance(frozen, Mapping) and "artifact_notices" not in frozen:
        current.pop("artifact_notices", None)
    return current == frozen


def effective_coverage(artifact: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Replay the complete, ordered artifact-specific ledger or fail closed.

    Do not pass only the latest event: full per-page ancestry is required. Duplicated
    identical request IDs are replay-safe; changed payloads are rejected. Historical
    artifacts need their own ledgers, never filtered/rebound current decisions.
    """
    value = validate_artifact(artifact)
    if len(decisions) > 10000:
        raise M3Error("coverage_limit", "Coverage history exceeds this summary bound")
    latest: dict[int, dict[str, Any]] = {}
    requests: dict[str, str] = {}
    audit: list[str] = []
    for record in decisions:
        event = _validate_event(value, record)
        key, revision = event["request_id"], event["revision"]
        if key in requests:
            if requests[key] != revision:
                raise M3Error("coverage_idempotency_conflict", "Request ID was reused with different content")
            continue
        previous = latest.get(event["page_index"])
        expected = previous["revision"] if previous else None
        if event["previous_revision"] != expected or event["expected_revision"] != expected:
            raise M3Error("coverage_conflict", "Coverage history is missing, reordered or forked")
        latest[event["page_index"]] = event
        requests[key] = revision
        audit.append(revision)
    pages = []
    for page in value["pages"]:
        event = latest.get(page["page_index"])
        resolved = page["coverage"] == "ok" and not page["issues"]
        disposition = "parser_clear" if resolved else "unresolved"
        if event is not None:
            resolved = event["decision"] != "flag_unresolved"
            disposition = event["decision"]
        pages.append({"page_index": page["page_index"], "page_sha": digest("m3.coverage-page.v1", page),
                      "original_coverage": page["coverage"], "issues": list(page["issues"]),
                      "resolved": resolved, "disposition": disposition,
                      "decision_revision": event["revision"] if event else None,
                      "actor": event["actor"] if event else None,
                      "reason": event["reason"] if event else None})
    unresolved = [page["page_index"] for page in pages if not page["resolved"]]
    return {"schema_version": 1, "policy": COVERAGE_POLICY, "artifact_sha": value["artifact_sha"],
            "complete": not unresolved, "unresolved_pages": unresolved, "pages": pages,
            "artifact_issues": list(value["issues"]), "artifact_notices": artifact_notices(value), "audit_revisions": audit,
            "summary_revision": digest("m3.effective-coverage.v1", {"artifact_sha": value["artifact_sha"], "audit_revisions": audit})}
