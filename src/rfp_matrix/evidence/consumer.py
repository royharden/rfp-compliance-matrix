"""Keep generated closed labels distinct from provider-reported distributions."""
from __future__ import annotations

from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, canonical_json, digest
from rfp_matrix.providers import DisabledProvider, validate_judgment
from rfp_matrix.source.policy import policy_status, policy_identity

ENGINE_POLICY = "m3-closed-evidence-consumer-v2"


def interpret_judgment(outcome: Mapping[str, Any], questions: Mapping[str, Any], *, method: str) -> dict[str, Any]:
    """Validate a provider result without inventing probabilities for F2 labels.

    `raw` is retained for SDK/model/usage/retry/accounting provenance. Labels are
    always review-required; typed distributions are subject to policy thresholds.
    """
    if method not in {"B0b", "F1", "F2"}:
        raise M3Error("unknown_method", "This method has no model judgment stage")
    if not isinstance(outcome, Mapping):
        raise M3Error("invalid_provider_response", "Judgment must be a mapping")
    raw = dict(outcome)
    canonical_json(raw)
    result = {"status": "unavailable", "raw": raw, "reason": raw.get("reason", "provider_unavailable")}
    if raw.get("origin") not in {"mock", "replay", "live", "unavailable"}:
        raise M3Error("invalid_provider_response", "Missing or invalid provider origin")
    if method == "F2":
        if raw.get("status") != "needs_review":
            return result
        if (raw.get("distribution_semantics") != "generated-closed-labels-no-probabilities"
                or raw.get("origin") == "unavailable" or raw.get("answers")):
            raise M3Error("invalid_provider_response", "F2 labels cannot masquerade as calibrated answers")
        labels = raw.get("raw_classifications")
        if not isinstance(labels, dict) or set(labels) != set(questions):
            raise M3Error("invalid_provider_response", "Incomplete F2 closed classifications")
        for identity, question in questions.items():
            label = labels[identity]
            if question["type"] == "noul":
                if label is not None and type(label) is not bool:
                    raise M3Error("invalid_provider_response", "F2 boolean must be true, false or unknown")
            elif not isinstance(label, str) or label not in question["criteria"]:
                raise M3Error("invalid_provider_response", "F2 selection is not a registered option")
        return {"status": "labels", "labels": dict(labels), "raw": raw,
                "reason": "uncalibrated_generated_labels", "review_required": True}
    checked = validate_judgment(raw, questions)
    if checked.get("status") != "ok":
        return result
    for identity, question in questions.items():
        if question["type"] == "choice":
            answer = checked["answers"][identity]
            probability = answer["probabilities"][answer["choice"]]
            if abs(answer["confidence"] - probability) > 0.000001 or probability < max(answer["probabilities"].values()):
                raise M3Error("invalid_provider_response", "Selected choice/confidence contradicts the reported distribution")
    return {"status": "ok", "answers": checked["answers"], "raw": checked, "review_required": False}


def provider_identity(provider: Any, method: str) -> dict[str, Any]:
    """No factory, secrets, SDK or ledger I/O; config identity is application-owned."""
    value: dict[str, Any] = {"method": method, "engine_policy": ENGINE_POLICY,
                             "implementation": type(provider).__module__ + "." + type(provider).__name__}
    config = getattr(provider, "config", None)
    if config is not None:
        if config.method != method:
            raise M3Error("provider_method_mismatch", "Injected provider is configured for another method")
        value.update(config_sha256=config.config_sha256, model=config.model,
                     sdk_name=config.sdk_name, sdk_version=config.sdk_version,
                     question_policy=config.question_policy, source_policy=config.source_policy,
                     price_policy=config.price_policy)
    else:
        declared = getattr(provider, "method", method)
        if declared != method:
            raise M3Error("provider_method_mismatch", "Injected provider method is incompatible")
        value["requested_model"] = getattr(provider, "requested_model", None)
    canonical_json(value)
    return value


def judgment_port(provider: Any, method: str) -> Any:
    port = provider if provider is not None else DisabledProvider(method)
    provider_identity(port, method)
    return port


def check_provider_sources(provider: Any, *, artifact: Mapping[str, Any] | None = None,
                            corpus: Mapping[str, Any] | None = None,
                            requirement: Mapping[str, Any] | None = None) -> None:
    config = getattr(provider, "config", None)
    if config is None:
        return
    if artifact is not None and config.source_sha256 != artifact["artifact_sha"]:
        raise M3Error("provider_source_mismatch", "Provider admission is for a different extraction artifact")
    if requirement is not None and any(anchor["artifact_sha"] != config.source_sha256 for anchor in requirement["anchors"]):
        raise M3Error("provider_source_mismatch", "Requirement artifact differs from provider admission")
    if corpus is not None and config.corpus_sha256 != corpus["generation"]:
        raise M3Error("provider_corpus_mismatch", "Provider admission is for a different corpus generation")


def call_judgment(provider: Any, state: Mapping[str, Any], questions: Mapping[str, Any], *, method: str) -> dict[str, Any]:
    """One bounded engine request. Provider dispatcher alone owns attempts/cost."""
    raw: Any = None
    try:
        if len(canonical_json({"state": dict(state), "questions": dict(questions)}).encode("utf-8")) + 1024 > 8000:
            raw = {"status": "unavailable", "origin": "unavailable", "reason": "request_input_bound_exceeded"}
        else:
            raw = provider.judge(state, questions)
        return interpret_judgment(raw, questions, method=method)
    except Exception as exc:
        # Preserve typed failure class, never include arbitrary provider exception
        # text which could contain request data or credentials. No engine retries.
        failure = {"status": "unavailable", "origin": "unavailable", "reason": "provider_failure",
                   "code": exc.code if isinstance(exc, M3Error) else type(exc).__name__}
        if isinstance(raw, Mapping):
            try:
                canonical_json(dict(raw))
                failure.update(dict(raw))
                failure["consumer_error"] = exc.code if isinstance(exc, M3Error) else type(exc).__name__
            except (TypeError, ValueError):
                pass
        return {"status": "unavailable", "reason": "provider_failure", "raw": failure}


def assessment_policy(method: str, provider: Any, semantic_context: Mapping[str, Any]) -> str:
    return digest("m3.assessment-policy.v4", {"policy": ENGINE_POLICY, "method": method,
        "typed_threshold": 0.9, "provider": provider_identity(provider, method), "semantic_identity": policy_identity(semantic_context)})


def assessment_policy_status(assessment: Mapping[str, Any], *, current_context: Mapping[str, Any], provider: Any = None) -> dict[str, Any]:
    """Pure code+method+provider/model/SDK policy guard, never an evidence rerun."""
    status = policy_status(assessment.get("policy_provenance"), current_context)
    if status["status"] != "current":
        return status
    method = assessment.get("method")
    if method not in {"B0a", "B0b", "F1", "F2"}:
        return {**status, "status": "mismatch", "allows_current_claim": False, "reasons": ["Unknown recorded method."], "action": "reimport_or_reassess"}
    if current_context.get("method") != method:
        return {**status, "status": "mismatch", "allows_current_claim": False,
                "reasons": ["Current semantic descriptor belongs to a different assessment method."], "action": "reimport_or_reassess"}
    if provider is None and method != "B0a":
        return {**status, "status": "missing", "allows_current_claim": False,
                "reasons": ["Current model/SDK/provider configuration is unavailable; do not infer it from old answers."], "action": "reimport_or_reassess"}
    try:
        expected = assessment_policy(method, judgment_port(provider, method), current_context)
    except Exception:
        expected = None
    if expected is None or assessment.get("policy_hash") != expected:
        return {**status, "status": "mismatch", "allows_current_claim": False,
                "reasons": ["Provider/model/SDK/question or validation policy changed; obtain a new actual assessment."], "action": "reimport_or_reassess"}
    return status


def assessment_generation(artifact_sha: str, corpus_generation: str, method: str, provider: Any,
                          semantic_context: Mapping[str, Any] | None = None) -> str:
    return digest("m3.assessment-generation.v4", {"artifact_sha": artifact_sha,
        "corpus_generation": corpus_generation, "provider": provider_identity(provider, method),
        "inventory_policy": "m3-structural-modal-v1", "assessment_policy": ENGINE_POLICY,
        "semantic_identity": policy_identity(semantic_context) if semantic_context is not None else None})
