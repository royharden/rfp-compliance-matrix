"""Explicit read-time policy guards; stored history and content hashes stay intact."""
from copy import deepcopy

from .store import ReviewError


def policy_review(snapshot: dict, *, provider=None) -> dict:
    from rfp_matrix.source import artifact_policy_status, policy_context
    from rfp_matrix.evidence import assessment_policy_status
    artifact = snapshot["artifact"]
    profile = {key: artifact[key] for key in ("parser", "parser_version", "normalization_policy")}
    try:
        source = artifact_policy_status(artifact, current_context=policy_context("B0a", profile))
    except Exception:
        source = {"status": "missing", "allows_current_claim": False, "reasons": ["Historical source policy could not be verified"],
                  "action": "reimport_source_then_reassess", "execution_verified": False}
    contexts, rows = {}, {}
    for row in snapshot["rows"]:
        method = row["assessment"].get("method")
        if method not in {"B0a", "B0b", "F1", "F2"}:
            status = {"status": "missing", "allows_current_claim": False, "reasons": ["Unknown assessment method"], "action": "reimport_or_reassess"}
        elif not source["allows_current_claim"]:
            status = {**source, "action": "restart_then_reimport" if source["status"] == "restart_required" else "reimport_source_then_reassess"}
        else:
            if method not in contexts:
                contexts[method] = policy_context(method)
            from .policy_binding import ReviewPolicyBindings
            try:
                selected_provider = provider.resolve(snapshot, method) if isinstance(provider, ReviewPolicyBindings) else provider
                status = assessment_policy_status(row["assessment"], current_context=contexts[method], provider=selected_provider)
            except ReviewError as exc:
                status = {"status": "mismatch", "allows_current_claim": False, "reasons": [str(exc)],
                          "action": "refresh_review_policy_or_reassess", "execution_verified": False}
        if row.get("retired"):
            status = {**status, "status": "historical", "allows_current_claim": False, "action": "view_history", "reasons": ["Retired historical obligation"]}
        rows[row["row_id"]] = {**status, "execution_verified": False}
    active = [rows[row["row_id"]] for row in snapshot["rows"] if not row.get("retired")]
    failures = [source] if not source["allows_current_claim"] else [item for item in active if not item["allows_current_claim"]]
    priority = {"restart_required": 0, "mismatch": 1, "legacy": 2, "missing": 3}
    overall = min(failures, key=lambda item: priority.get(item["status"], 4)) if failures else source
    return {**overall, "schema_version": 1, "source": source, "rows": rows,
            "allows_current_claim": not failures, "execution_verified": False, "collection": "explicit_operation_not_constructor"}


def presented(snapshot: dict, *, provider=None) -> dict:
    result = deepcopy(snapshot)
    review = policy_review(snapshot, provider=provider)
    result["policy_status"] = review
    for row in result["rows"]:
        row["policy_status"] = review["rows"][row["row_id"]]
        row["current_approval"] = row["approval"] if row["policy_status"]["allows_current_claim"] else "needs_reassessment"
    return result


def in_queue(row: dict, queue: str) -> bool:
    current = row.get("policy_status", {}).get("allows_current_claim") is True
    approval = row.get("approval", "pending")
    if queue == "all":
        return True
    if queue in {"approved_positive", "approved_gap"}:
        return approval == queue and current and not row.get("retired")
    if queue == "stale":
        return approval == "stale" or (not current and not row.get("retired"))
    if queue == "unresolved":
        return approval not in {"approved_positive", "approved_gap"} or not current
    if queue == "retired":
        return bool(row.get("retired"))
    return approval == queue


def require_current(snapshot: dict, row: dict | None = None, *, provider=None, source_only=False) -> dict:
    review = policy_review(snapshot, provider=provider)
    selected = review["source"] if source_only else review["rows"][row["row_id"]] if row else review
    if not selected["allows_current_claim"]:
        raise ReviewError("policy_not_current", "Current semantics are not established; explicitly restart, reimport or reassess as indicated.",
                          policy_status=selected, matrix_generation=snapshot["matrix_generation"], assessment_generation=snapshot["assessment_generation"])
    return review


def require_execution_context(snapshot: dict, method: str) -> None:
    from rfp_matrix.source import policy_context
    require_current(snapshot, source_only=True)
    context = policy_context(method)
    if context["status"] != "current":
        raise ReviewError("policy_not_current", "Execution context is missing or drifted; restart safely before reassessment.", policy_status=context)
