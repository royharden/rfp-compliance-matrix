"""Closed public commitments to producer-owned prospective bounds, never dispatch."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Sequence


def freeze_envelope_commitments(value: Mapping[str, Any] | None, *,
        documents: Sequence[Mapping[str, Any]], methods: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Bind supplied producer receipts to the freeze, without estimating again.

    The provider adapter supplies exact digests and algebraic bounds. Missing producer
    estimates remain unknown, not affordable/zero/measured. Real custodian
    authority must authenticate producer/source/config/budget pins independently.
    """
    from .runner import canonical, require_digest
    import json
    if value is None:
        return {method: {"status": "not_yet_estimated", "documents": [],
                         "budget_policy_sha": config["budget_sha"], "measured": False}
                for method, config in methods.items()}
    if not isinstance(value, Mapping) or set(value) != set(methods):
        raise ValueError("Envelope commitments require the exact four method keys")
    result = json.loads(canonical(value))
    inputs = {doc["document_id"]: doc for doc in documents}
    fields = {"document_id", "raw_sha", "artifact_sha", "corpus_generation", "provider_config_sha",
              "envelope_sha", "counts_sha", "status", "max_requests", "max_transport_attempts", "max_input_tokens",
              "max_output_tokens", "max_concurrent_calls", "deadline_seconds_per_request",
              "worst_case_cost_usd", "whole_run_affordable", "budget_snapshot_sha", "remaining_budget_basis",
              "dispatch_enabled", "conditions"}
    for method, arm in result.items():
        if not isinstance(arm, dict) or set(arm) != {"status", "documents", "budget_policy_sha", "measured"} or arm["measured"] is not False:
            raise ValueError("Closed unmeasured envelope arm required")
        if arm["status"] not in ("not_yet_estimated", "estimated", "partial_unknown") or arm["budget_policy_sha"] != methods[method]["budget_sha"]:
            raise ValueError("Envelope budget policy/status mismatch")
        if not isinstance(arm["documents"], list) or len(arm["documents"]) > len(inputs):
            raise ValueError("Invalid bounded envelope documents")
        seen = set()
        for item in arm["documents"]:
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError("Unknown envelope fields are not public receipt metadata")
            key = item["document_id"]
            if not isinstance(key, str) or key not in inputs or key in seen or item["raw_sha"] != inputs[key]["source_sha"]:
                raise ValueError("Envelope input identity differs from raw frozen document")
            seen.add(key)
            for field in ("raw_sha", "artifact_sha", "corpus_generation", "provider_config_sha", "envelope_sha", "counts_sha"):
                require_digest(item[field])
            if item["status"] not in ("estimated", "disabled", "unknown_price", "over_budget", "partial_unknown"):
                raise ValueError("Unknown prospective envelope status")
            if item["dispatch_enabled"] is not False or (item["whole_run_affordable"] is not None and type(item["whole_run_affordable"]) is not bool):
                raise ValueError("Envelope affordability is declared/unknown; live dispatch remains disabled")
            if not isinstance(item["conditions"], list) or len(item["conditions"]) > 30 or any(not isinstance(c, str) or re.fullmatch(r"[a-z_]{1,100}", c) is None for c in item["conditions"]):
                raise ValueError("Only bounded producer condition codes are public")
            if type(item["max_concurrent_calls"]) is not int or not 0 <= item["max_concurrent_calls"] <= 2:
                raise ValueError("Concurrency is a declared ceiling, at most two calls")
            deadline = item["deadline_seconds_per_request"]
            if type(deadline) not in (int, float) or not 0 < deadline <= 60:
                raise ValueError("Per-request deadline bound must be positive and at most sixty seconds")
            for field in ("max_requests", "max_transport_attempts", "max_input_tokens", "max_output_tokens"):
                bound = item[field]
                if bound is not None and (type(bound) is not int or not 0 <= bound <= 10**12):
                    raise ValueError("Envelope bound must be nonnegative integer or unknown")
            if item["remaining_budget_basis"] == "unknown_no_ledger_snapshot":
                if item["budget_snapshot_sha"] is not None or (item["max_requests"] != 0 and item["whole_run_affordable"] is not None):
                    raise ValueError("Missing ledger balance must remain unknown, never a fresh budget")
            elif item["remaining_budget_basis"] == "caller_supplied_ledger_snapshot":
                require_digest(item["budget_snapshot_sha"])
            else:
                raise ValueError("Unknown remaining-budget provenance")
            cost = item["worst_case_cost_usd"]
            if cost is not None:
                if not isinstance(cost, str) or len(cost) > 100:
                    raise ValueError("Prospective cost uses explicit Decimal string or null")
                try:
                    amount = Decimal(cost)
                except InvalidOperation as exc:
                    raise ValueError("Invalid prospective Decimal price") from exc
                if not amount.is_finite() or amount < 0:
                    raise ValueError("Invalid prospective cost")
            if item["status"] == "unknown_price" and cost is not None:
                raise ValueError("Unknown price cannot be relabelled zero or measured cost")
        if arm["status"] == "not_yet_estimated" and seen:
            raise ValueError("Absent estimate status cannot hide supplied receipts")
        if arm["status"] == "estimated" and seen != set(inputs):
            raise ValueError("Complete prospective arm needs every public document")
    return result


def pipeline_envelope_commitments(ports: Mapping[str, Any], registry_counts: Mapping[str, Mapping[str, Mapping[str, Any]]],
                                  method_configs: Mapping[str, Mapping[str, Any]], *,
                                  budget_contexts: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None) -> dict[str, Any]:
    """Use actual provider-planner algebra over trusted caller-supplied registry/generation counts.

    Does not derive fake assessment/matrix generations or run retrieval to fill
    missing counts. Partial context stays partial_unknown. No factory/client/DB
    is created; this pure planner is distinct from any live authorization.
    """
    from rfp_matrix.providers.envelope import estimate_run_envelope
    from .runner import sha
    if set(ports) - set(method_configs) or set(registry_counts) - set(method_configs):
        raise ValueError("Unknown method in prospective pipeline context")
    budget_contexts = budget_contexts if budget_contexts is not None else {}
    if not isinstance(budget_contexts, Mapping) or set(budget_contexts) - set(method_configs):
        raise ValueError("Unknown method in declared budget snapshots")
    result = {}
    for method, config in method_configs.items():
        arm = {"status": "not_yet_estimated", "documents": [], "budget_policy_sha": config["budget_sha"], "measured": False}
        result[method] = arm
        if method not in ports or config["origin"] == "unavailable":
            continue
        port = ports[method]
        supplied = registry_counts.get(method, {})
        balances = budget_contexts.get(method, {})
        if not isinstance(balances, Mapping) or set(balances) - set(port.prepared):
            raise ValueError("Unknown document in declared budget snapshot")
        if set(supplied) - set(port.prepared):
            raise ValueError("Envelope count context contains unknown document")
        for document_id, counts in supplied.items():
            prepared = port.prepared[document_id]
            policy = port.prospective_policy(document_id)
            if config != port.frozen_configuration(price_sha=config["price_sha"], budget_sha=config["budget_sha"], origin=config["origin"]):
                raise ValueError("Envelope port differs from frozen method config")
            source_count = sum(span.kind == "source" for span in prepared.public.spans)
            if counts.get("inventory_candidates") != source_count or counts.get("requirement_rows") != source_count:
                raise ValueError("Prospective blind inventory must bound all frozen candidates, not corrected/gold rows")
            # Exactly this configured provider producer computes bounds and digest;
            # no parallel price/fanout algorithm is implemented in evaluation.
            report = estimate_run_envelope(policy, counts, method=method, origin=config["origin"],
                                           budget_context=balances.get(document_id))
            identity, bounds, cost, budget = report["identity"], report["bounds"], report["cost"], report["budget"]
            if report["dispatch_performed"] is not False or report["runtime_verified"] is not False:
                raise ValueError("Prospective source cannot accept an executed/verified envelope")
            conditions = report["conditions"]
            incomplete = any(c in conditions for c in ("question_bundle_policy_unknown", "inventory_requests_exceed_input_cap",
                "evidence_requests_exceed_input_cap", "evidence_choice_windows_truncated", "replay_coverage_unknown", "replay_coverage_incomplete"))
            status = ("partial_unknown" if incomplete else "over_budget" if "full_run_budget_insufficient" in conditions else "unknown_price"
                      if cost["declared_live_cost_upper_usd"] is None else "partial_unknown"
                      if bounds["logical_requests"] and budget["whole_run_live_affordable"] is None
                      else "disabled" if bounds["logical_requests"] else "estimated")
            arm["documents"].append({"document_id": document_id, "raw_sha": sha(prepared.public.pdf),
                "artifact_sha": identity["source_sha256"], "corpus_generation": identity["corpus_sha256"],
                "provider_config_sha": identity["config_sha256"], "envelope_sha": report["envelope_sha256"],
                "counts_sha": identity["registry_counts_sha256"], "status": status,
                "max_requests": bounds["logical_requests"], "max_transport_attempts": bounds["transport_attempts_maximum"],
                "max_input_tokens": bounds["input_tokens_maximum"], "max_output_tokens": bounds["output_tokens_maximum"],
                "max_concurrent_calls": bounds["concurrent_transport_calls_maximum"],
                "deadline_seconds_per_request": bounds["deadline_seconds_per_logical_request"],
                "worst_case_cost_usd": cost["declared_live_cost_upper_usd"],
                "whole_run_affordable": budget["whole_run_live_affordable"],
                "budget_snapshot_sha": report["budget_context"]["ledger_snapshot_sha256"] if report["budget_context"] is not None else None,
                "remaining_budget_basis": budget["remaining_budget_basis"], "dispatch_enabled": False,
                "conditions": list(conditions)})
        arm["documents"].sort(key=lambda item: item["document_id"])
        complete = len(arm["documents"]) == len(port.prepared) and all(item["status"] not in ("unknown_price", "partial_unknown") for item in arm["documents"])
        arm["status"] = "estimated" if complete else "partial_unknown" if arm["documents"] else "not_yet_estimated"
    return result
