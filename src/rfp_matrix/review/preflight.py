"""Snapshot-derived prospective bounds. No SDK/key/ledger/dispatch operation."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from contextlib import closing
from typing import Any

from .store import ReviewError, canonical, digest, safe_id


def run_envelope(store, run_id: str, policy_context: Mapping[str, Any]) -> dict:
    from rfp_matrix.providers import ProviderConfig, build_questions, estimate_run_envelope
    from rfp_matrix.providers.live import OpenAIClassificationProvider, TypeSafeProvider
    from rfp_matrix.evidence import registered_source_candidates, registered_evidence_windows, retrieve_evidence
    from rfp_matrix.evidence.engine import ENGINE_POLICY
    safe_id(run_id)
    allowed = {"config", "method", "origin", "approval", "budget_context"}
    if not isinstance(policy_context, Mapping) or set(policy_context) - allowed:
        raise ReviewError("invalid_preflight_context", "Preflight accepts only nonsecret policy/approval/budget mappings, never paths, keys or count overrides.")
    if len(canonical(dict(policy_context)).encode("utf-8")) > 128 * 1024:
        raise ReviewError("input_limit", "Preflight policy context exceeds its bound.")
    with closing(store._connect()) as connection:
        connection.execute("BEGIN")
        snapshot = store._load(connection, run_id)
    artifact, corpus = snapshot["artifact"], snapshot["corpus"]
    method = policy_context.get("method", "B0a")
    if not isinstance(method, str) or method not in {"B0a", "B0b", "F1", "F2"}:
        raise ReviewError("invalid_preflight_context", "Unknown prospective method.")
    config_value = policy_context.get("config")
    if config_value is None:
        if method != "B0a":
            raise ReviewError("preflight_config_required", "Typed bounds need an explicit exact nonsecret provider policy; no model/price pin is inferred.")
        # These are the provider layer's declared disabled defaults, not observed model/SDK pins.
        # B0a's envelope exposes no provider model/SDK and makes zero calls.
        config = ProviderConfig.from_mapping({"enabled": False, "budget_usd": "0", "document_id": run_id,
            "source_sha256": artifact["artifact_sha"], "corpus_sha256": corpus["generation"]})
    else:
        config = ProviderConfig.from_mapping(config_value)
    if (config.document_id != run_id or config.source_sha256 != artifact["artifact_sha"]
            or config.corpus_sha256 != corpus["generation"]):
        raise ReviewError("preflight_identity_mismatch", "Config must bind this owned run, immutable artifact and corpus generation.",
                          matrix_generation=snapshot["matrix_generation"], assessment_generation=snapshot["assessment_generation"])
    origin = policy_context.get("origin", "baseline" if method == "B0a" else "live")
    if not isinstance(origin, str) or origin not in {"baseline", "live", "mock", "replay"}:
        raise ReviewError("invalid_preflight_context", "Unknown prospective accounting origin.")
    registry = registered_source_candidates(artifact)
    evidence = registered_evidence_windows(corpus)
    active = [row for row in snapshot["rows"] if not row.get("retired")]
    windows_per_record = Counter(window["record_id"] for window in evidence["windows"])
    inventory_oversize = evidence_oversize = retrieved_count = 0
    # request_body methods are pure dictionaries; do not construct SDK/HTTP
    # clients or call factory/judge/attempt even for an enabled policy preview.
    body_builder = OpenAIClassificationProvider(config) if method == "F2" else TypeSafeProvider(config)
    inventory_questions = build_questions("inventory")
    for candidate in registry["candidates"]:
        anchor = candidate["anchor"]
        page = artifact["pages"][anchor["page_index"]]
        state = {"clause": candidate["literal_clause"], "context": page["text"][max(0, anchor["start"] - 200):min(len(page["text"]), anchor["end"] + 200)],
                 "anchor": anchor, "artifact_sha": artifact["artifact_sha"], "source_sha": artifact["source"]["raw_sha"],
                 "normalization_policy": artifact["normalization_policy"]}
        if len(canonical(body_builder.request_body(state, inventory_questions)).encode("utf-8")) + 1024 > config.max_input_tokens:
            inventory_oversize += 1
    records = {record["record_id"]: record for record in corpus["records"]}
    for row in active:
        requirement = row["requirement"]
        retrieved = retrieve_evidence(requirement, corpus, k=10)
        retrieved_count += len(retrieved["candidates"])
        for candidate in retrieved["candidates"]:
            record = records[candidate["record_id"]]
            windows = candidate["windows"][:254]
            questions = build_questions("evidence", [window["sentence_id"] for window in windows])
            state = {"requirement": requirement["interpretation"], "condition": requirement["condition"],
                "requirement_revision": requirement["revision"], "requirement_anchors": requirement["anchors"],
                "corpus_generation": corpus["generation"], "vendor_context": record["text"],
                "record_id": record["record_id"], "record_sha": record["raw_sha"],
                "registered_windows": windows, "engine_policy": ENGINE_POLICY}
            if len(canonical(body_builder.request_body(state, questions)).encode("utf-8")) + 1024 > config.max_input_tokens:
                evidence_oversize += 1
    counts = {"schema_version": 1, "artifact_sha": artifact["artifact_sha"], "corpus_generation": corpus["generation"],
        "assessment_generation": snapshot["assessment_generation"], "matrix_generation": snapshot["matrix_generation"],
        "registry_sha256": digest("m3.application-run-count-registry.v1", {
            "source_registry": registry["registry_sha"], "evidence_registry": evidence["registry_sha"], "engine_policy": ENGINE_POLICY,
            "active_rows": [{"row_id": row["row_id"], "row_revision": row["row_revision"]} for row in sorted(active, key=lambda row: row["row_id"])]}),
        "inventory_candidates": len(registry["candidates"]), "requirement_rows": len(active),
        "retrieval_top_k": min(10, len(records)), "max_windows_per_candidate": max(windows_per_record.values(), default=0),
        "retrieved_evidence_candidates": retrieved_count, "inventory_oversize_requests": inventory_oversize,
        "evidence_oversize_requests": evidence_oversize, "replay_covered_requests": None}
    envelope = estimate_run_envelope(config, counts, method=method, origin=origin,
                                     approval=policy_context.get("approval"), budget_context=policy_context.get("budget_context"))
    return {"schema_version": 1, "run_id": run_id, "matrix_generation": snapshot["matrix_generation"],
        "assessment_generation": snapshot["assessment_generation"], "envelope": envelope,
        "registry_derivation": {"source_registry_sha": registry["registry_sha"], "evidence_registry_sha": evidence["registry_sha"],
            "engine_policy": ENGINE_POLICY, "configured_top_k": 10, "effective_frozen_corpus_top_k_cap": counts["retrieval_top_k"],
            "oversize_evidence_basis": "current_active_registered_rows; future_typed_inventory_may_differ",
            "replay_coverage": "unknown_no_tape_execution", "provider_model_sdk_pins_inferred": False},
        "dispatch_performed": False, "sdk_imported": False, "key_checked": False, "ledger_read": False,
        "runtime_verified": False, "live_authorization_verified": False}
