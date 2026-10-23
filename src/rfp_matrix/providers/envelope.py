"""Pure prospective whole-run algebra; no registry traversal, I/O or dispatch.

Counts come from the trusted deterministic application registry. A digest binds
them; it does not authenticate their author or prove model/tokenizer behavior.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, digest
from .config import ProviderConfig
from .ports import QUESTION_POLICY

ENVELOPE_POLICY = "m3-whole-run-envelope-v1"
COUNT_REQUIRED = {"schema_version", "artifact_sha", "corpus_generation", "registry_sha256",
    "assessment_generation", "matrix_generation", "inventory_candidates", "requirement_rows",
    "retrieval_top_k", "max_windows_per_candidate"}
COUNT_OPTIONAL = {"retrieved_evidence_candidates", "inventory_oversize_requests",
                  "evidence_oversize_requests", "replay_covered_requests"}
APPROVAL_FIELDS = {"schema_version", "envelope_sha256", "config_sha256", "source_sha256",
    "corpus_sha256", "method", "origin", "registry_counts_sha256", "approval_reference",
    "assumptions_acknowledged"}


def _count(value: Any, maximum: int, name: str) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise M3Error("envelope_count", f"{name} must be an integer in 0..{maximum}")
    return value


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise M3Error("envelope_identity", "Exact registry/source/generation hashes are required")
    return value


def _counts(value: Mapping[str, Any], config: ProviderConfig) -> dict[str, Any]:
    if not isinstance(value, Mapping) or COUNT_REQUIRED - set(value) or set(value) - COUNT_REQUIRED - COUNT_OPTIONAL:
        raise M3Error("envelope_counts", "Unknown or missing deterministic registry count fields")
    result = dict(value)
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise M3Error("envelope_counts", "Unsupported registry count version")
    for name in ["artifact_sha", "corpus_generation", "registry_sha256", "assessment_generation", "matrix_generation"]:
        _sha(result[name])
    if result["artifact_sha"] != config.source_sha256 or result["corpus_generation"] != config.corpus_sha256:
        raise M3Error("envelope_identity", "Config must bind this exact artifact and corpus generation")
    for name in ["inventory_candidates", "requirement_rows"]:
        _count(result[name], 2000, name)
    _count(result["retrieval_top_k"], 10, "retrieval_top_k")
    # Actual frozen-window maxima may exceed the per-request Choice cap. Keep
    # this visible as truncation, never multiply windows into extra requests.
    _count(result["max_windows_per_candidate"], 20000, "max_windows_per_candidate")
    evidence_cap = result["requirement_rows"] * result["retrieval_top_k"]
    retrieved = result.setdefault("retrieved_evidence_candidates", None)
    if retrieved is not None:
        _count(retrieved, evidence_cap, "retrieved_evidence_candidates")
    _count(result.setdefault("inventory_oversize_requests", 0), result["inventory_candidates"], "inventory_oversize_requests")
    _count(result.setdefault("evidence_oversize_requests", 0), evidence_cap if retrieved is None else retrieved, "evidence_oversize_requests")
    replay = result.setdefault("replay_covered_requests", None)
    if replay is not None:
        _count(replay, result["inventory_candidates"] + evidence_cap, "replay_covered_requests")
    return result


def approval_status(envelope: Mapping[str, Any], approval: Mapping[str, Any] | None) -> str:
    """Compare a caller-supplied prospective record; no identity authentication."""
    if approval is None:
        return "missing"
    if not isinstance(approval, Mapping) or set(approval) != APPROVAL_FIELDS:
        return "mismatch"
    identity = envelope["identity"]
    expected = {"schema_version": 1, "envelope_sha256": envelope["envelope_sha256"],
        "config_sha256": identity["config_sha256"], "source_sha256": identity["source_sha256"],
        "corpus_sha256": identity["corpus_sha256"], "method": identity["method"],
        "origin": identity["origin"], "registry_counts_sha256": identity["registry_counts_sha256"]}
    if type(approval.get("schema_version")) is not int or any(approval.get(k) != v for k, v in expected.items()):
        return "mismatch"
    reference = approval.get("approval_reference")
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 2000:
        return "mismatch"
    if approval.get("assumptions_acknowledged") is not True:
        return "assumptions_not_acknowledged"
    return "matches_declared_record"


def _number(value: Any) -> Decimal:
    try:
        number = Decimal(value) if isinstance(value, str) else Decimal("NaN")
    except InvalidOperation as exc:
        raise M3Error("envelope_price", "Amounts must be bounded decimal text") from exc
    if not number.is_finite() or number < 0 or number > Decimal("1000000000") or not -18 <= number.as_tuple().exponent <= 18:
        raise M3Error("envelope_price", "Amounts require finite nonnegative values, <=1e9 and <=18 decimal places")
    return number


def estimate_run_envelope(config: ProviderConfig | Mapping[str, Any], registry_counts: Mapping[str, Any], *,
                          method: str | None = None, origin: str = "live",
                          approval: Mapping[str, Any] | None = None,
                          budget_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 80  # Exact bounded rates * <=352M tokens; no float prices.
        return _estimate(config, registry_counts, method=method, origin=origin,
                         approval=approval, budget_context=budget_context)


def _estimate(config: ProviderConfig | Mapping[str, Any], registry_counts: Mapping[str, Any], *,
                          method: str | None = None, origin: str = "live",
                          approval: Mapping[str, Any] | None = None,
                          budget_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    policy = config if isinstance(config, ProviderConfig) else ProviderConfig.from_mapping(config)
    policy.validate()
    selected = policy.method if method is None else method
    if selected not in {"B0a", "B0b", "F1", "F2"} or origin not in {"live", "mock", "replay", "baseline"}:
        raise M3Error("envelope_method", "Unknown method/origin")
    if selected != "B0a" and selected != policy.method:
        raise M3Error("envelope_method", "Typed method must match the exact configured provider")
    effective_origin = "baseline" if selected == "B0a" else origin
    if effective_origin == "baseline" and selected != "B0a":
        raise M3Error("envelope_origin", "Typed judgments cannot be labelled a no-provider baseline")
    counts = _counts(registry_counts, policy)
    inventory = counts["inventory_candidates"] if selected != "B0a" else 0
    evidence_rows_upper = max(counts["inventory_candidates"], counts["requirement_rows"])
    evidence_cap = evidence_rows_upper * counts["retrieval_top_k"]
    # A new typed inventory may retain EVERY source candidate, even if the
    # present method rejected most of them. Present retrieval totals cannot
    # reduce a full prospective run's upper bound.
    evidence = evidence_cap if selected in {"F1", "F2"} else 0
    requests = inventory + evidence
    attempts = requests * 2  # Exactly one controller retry; no hidden SDK retries.
    question_judgments = inventory * 5 + evidence * 2
    input_tokens = attempts * policy.max_input_tokens
    output_tokens = attempts * policy.max_output_tokens
    # Every bundle includes all its questions inside the existing request cap.
    # Choice windows are options, not separate calls/questions.
    price_known = (policy.input_per_million_usd is not None and policy.output_per_million_usd is not None
                   and bool(policy.price_policy) and bool(policy.price_provenance))
    declared_cost = ((_number(policy.input_per_million_usd) * input_tokens
                     + _number(policy.output_per_million_usd) * output_tokens) / Decimal(1_000_000)) if price_known else None
    if requests == 0:
        declared_cost = Decimal(0)
    live = effective_origin == "live"
    incremental_cost = declared_cost if live else Decimal(0)
    document_budget, batch_budget = _number(policy.document_budget_usd), _number(policy.budget_usd)
    balance = None
    if budget_context is not None:
        expected = {"schema_version", "document_id", "origin", "batch_committed_usd", "document_committed_usd", "ledger_snapshot_sha256"}
        if (not isinstance(budget_context, Mapping) or set(budget_context) != expected
                or type(budget_context["schema_version"]) is not int or budget_context["schema_version"] != 1
                or budget_context["document_id"] != policy.document_id
                or budget_context["origin"] not in ({effective_origin} if effective_origin in {"live", "mock"} else {"live", "mock"})):
            raise M3Error("envelope_budget", "Budget snapshot must bind this exact document/accounting origin")
        _sha(budget_context["ledger_snapshot_sha256"])
        balance = dict(budget_context)
        batch_committed = _number(balance["batch_committed_usd"])
        document_committed = _number(balance["document_committed_usd"])
        available_budget = max(Decimal(0), min(batch_budget - batch_committed, document_budget - document_committed))
    else:
        available_budget = None
    conditions = []
    if selected != "B0a" and policy.question_policy != QUESTION_POLICY:
        conditions.append("question_bundle_policy_unknown")
    if live and requests and not policy.semantic_fingerprint:
        conditions.append("semantic_source_fingerprint_missing")
    if requests and not price_known:
        conditions.append("price_unknown")
    if counts["inventory_oversize_requests"] and inventory:
        conditions.append("inventory_requests_exceed_input_cap")
    if counts["evidence_oversize_requests"] and evidence:
        conditions.append("evidence_requests_exceed_input_cap")
    if counts["max_windows_per_candidate"] > 254 and evidence:
        conditions.append("evidence_choice_windows_truncated")
    if live and requests and (not policy.enabled or batch_budget <= 0):
        conditions.append("live_disabled_zero_budget")
    if batch_budget > 10 or not 0 < document_budget <= 1:
        conditions.append("budget_scope_limit")
    gross_affordable = declared_cost <= min(document_budget, batch_budget) if declared_cost is not None else None
    whole_run_affordable = (declared_cost <= available_budget if declared_cost is not None and available_budget is not None else True if requests == 0 else None)
    if live and requests and available_budget is None:
        conditions.append("remaining_ledger_budget_unknown")
    if live and requests and (whole_run_affordable is False or gross_affordable is False):
        conditions.append("full_run_budget_insufficient")
    replay_covered = counts["replay_covered_requests"]
    if effective_origin == "replay" and replay_covered is not None and replay_covered > requests:
        raise M3Error("envelope_count", "Replay coverage exceeds this method's logical requests")
    if effective_origin == "replay" and replay_covered != requests:
        conditions.append("replay_coverage_unknown" if replay_covered is None else "replay_coverage_incomplete")
    identity = {"policy": ENVELOPE_POLICY, "method": selected, "origin": effective_origin,
        "config_sha256": policy.config_sha256, "source_sha256": counts["artifact_sha"],
        "corpus_sha256": counts["corpus_generation"], "assessment_generation": counts["assessment_generation"],
        "matrix_generation": counts["matrix_generation"], "registry_sha256": counts["registry_sha256"],
        "registry_counts_sha256": digest("m3.run-registry-counts.v1", counts),
        "model": policy.model if selected != "B0a" else None,
        "sdk_name": policy.sdk_name if selected != "B0a" else None,
        "sdk_version": policy.sdk_version if selected != "B0a" else None,
        "question_policy": policy.question_policy, "source_policy": policy.source_policy,
        "semantic_fingerprint": policy.semantic_fingerprint or None,
        "price_policy": policy.price_policy, "price_provenance": policy.price_provenance}
    value = {"schema_version": 1, "kind": "prospective_run_envelope", "identity": identity,
        "registry_counts": counts,
        "budget_context": balance,
        "bounds": {"inventory_requests": inventory, "evidence_requests": evidence,
            "requirement_rows_for_evidence_upper": evidence_rows_upper if evidence else 0,
            "evidence_count_basis": "all_potential_inventory_rows_times_top_k_upper_bound",
            "bundled_inventory_questions": 5 if inventory else 0, "bundled_evidence_questions": 2 if evidence else 0,
            "logical_requests": requests, "question_judgments_before_retry": question_judgments,
            "retry_maximum_per_request": 1, "transport_attempts_maximum": attempts,
            "question_judgments_with_retries_maximum": question_judgments * 2,
            "choice_window_options_maximum": min(counts["max_windows_per_candidate"], 254) + 1 if evidence else 0,
            "max_input_tokens_per_attempt": policy.max_input_tokens,
            "max_output_tokens_per_attempt": policy.max_output_tokens,
            "input_tokens_maximum": input_tokens, "output_tokens_maximum": output_tokens,
            "concurrent_transport_calls_maximum": min(2, requests),
            "deadline_seconds_per_logical_request": policy.deadline_seconds,
            "serial_dispatch_deadline_sum_seconds": str(Decimal(str(policy.deadline_seconds)) * requests),
            "retry_jitter_seconds_maximum": "0.25"},
        "cost": {"unit_prices_declared_with_provenance": bool(price_known),
            "declared_live_cost_upper_usd": str(declared_cost) if declared_cost is not None else None,
            "incremental_cost_upper_usd": str(incremental_cost) if incremental_cost is not None else None,
            "original_observed_cost_usd": None, "measured_cost_usd": None,
            "semantics": "algebraic_declared_price_bound_not_measurement"},
        "budget": {"document_budget_usd": str(document_budget), "batch_budget_usd": str(batch_budget),
            "available_full_run_budget_usd": str(available_budget) if available_budget is not None else None,
            "declared_gross_budget_can_cover_run": gross_affordable, "whole_run_live_affordable": whole_run_affordable,
            "remaining_budget_basis": "caller_supplied_ledger_snapshot" if balance is not None else "unknown_no_ledger_snapshot",
            "partial_budget_is_incomplete_evaluation": True,
            "incremental_mode": "live" if live else "replay_zero_incremental_original_unknown" if effective_origin == "replay" else "simulation_only" if effective_origin == "mock" else "no_provider"},
        "assumptions": {"token_bound_policy": policy.token_bound_policy,
            "tokenizer_framing_verified": False, "typesafe_output_cap_is_api_enforced": False,
            "all_requests_must_pass_individual_dispatch_preflight": True,
            "registry_authenticity": "trusted_caller_supplied_not_independently_verified",
            "two_call_limit_requires_one_shared_ledger": True,
            "deadline_sum_excludes_cpu_db_scheduler_overhead": True,
            "budget_snapshot_is_not_a_whole_run_reservation": True,
            "concurrency_is_ceiling_not_promised_parallel_schedule": True},
        "conditions": conditions, "status": "needs_attention" if conditions else "bound_declared",
        "dispatch_performed": False, "runtime_verified": False, "live_authorization_verified": False}
    value["envelope_sha256"] = digest("m3.prospective-run-envelope.v1", value)
    value["prospective_approval"] = {"status": approval_status(value, approval), "authenticity": "not_verified",
                                     "creates_authorization": False}
    return value


def validate_future_live_envelope(config: ProviderConfig, envelope: Mapping[str, Any] | None,
                                  approval: Mapping[str, Any] | None) -> str | None:
    """Factory guard for a later explicitly authorized caller; never dispatches.

    Recompute algebra rather than accepting a resealed lower cost/token total.
    Authentication of external approval and actual registry custody stays with
    the trusted caller; this helper compares only exact records and pins.
    """
    if not isinstance(envelope, Mapping):
        return "full_run_preflight_required"
    try:
        content = {k: v for k, v in envelope.items() if k not in {"envelope_sha256", "prospective_approval"}}
        if digest("m3.prospective-run-envelope.v1", content) != envelope.get("envelope_sha256"):
            return "full_run_preflight_mismatch"
        actual = estimate_run_envelope(config, envelope["registry_counts"], origin="live",
                                       budget_context=envelope.get("budget_context"))
        if envelope.get("envelope_sha256") != actual["envelope_sha256"]:
            return "full_run_preflight_mismatch"
        if actual["conditions"]:
            return "full_run_preflight_not_admissible"
        if approval_status(actual, approval) != "matches_declared_record":
            return "full_run_prospective_approval_required"
        return None
    except (KeyError, TypeError, ValueError):
        return "full_run_preflight_invalid"
