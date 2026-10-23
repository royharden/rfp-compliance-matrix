"""Actual source/evidence adapters for the injected-only public comparison runner.

Imports are lazy. Construction has no I/O. prepare_public_input and predict are
future execution APIs, never run during Dirty Mode. No labels enter this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from .runner import PublicInput, PublicSpan, canonical, fingerprint, sha


def extract_pdf_bytes(raw: bytes, *, document_id: str, terms: str) -> Mapping[str, Any]:
    """Future real bounded parser bridge; no file/process operation until called."""
    from rfp_matrix.source import parse_pdf
    with TemporaryDirectory(prefix="rfp-eval-public-") as folder:
        path = Path(folder) / "public-input.pdf"
        path.write_bytes(raw)
        return parse_pdf(path, source_id=document_id, terms=terms)


def source_span_id(anchor: Mapping[str, Any]) -> str:
    return "S-" + fingerprint({key: anchor[key] for key in ("artifact_sha", "page_index", "start", "end")})[:32]


@dataclass(frozen=True)
class PreparedInput:
    public: PublicInput
    artifact_json: str
    corpus_json: str
    preparation_sha: str


def prepare_public_input(*, document_id: str, family_id: str, pdf: bytes,
                         vendor_jsonl: bytes, rights_record_sha: str, terms: str,
                         extractor: Callable[..., Mapping[str, Any]] = extract_pdf_bytes) -> PreparedInput:
    """Extract once for identical arms; freeze public candidates before prediction.

    No rights authentication is performed here: the custodian validates the
    referenced rights record. Returned raw source/evidence is public, never gold.
    """
    from rfp_matrix.source import validate_artifact
    from rfp_matrix.evidence import engine
    if not isinstance(pdf, bytes) or not pdf or len(pdf) > 20 * 1024 * 1024:
        raise ValueError("Bounded public PDF bytes required")
    if not isinstance(vendor_jsonl, bytes) or len(vendor_jsonl) > 2 * 1024 * 1024:
        raise ValueError("Bounded fictional vendor JSONL bytes required")
    artifact = validate_artifact(extractor(pdf, document_id=document_id, terms=terms))
    if artifact["source"]["raw_sha"] != sha(pdf) or artifact["source"]["source_id"] != document_id:
        raise ValueError("Extractor does not address the exact raw public document")
    records = [json.loads(line) for line in vendor_jsonl.decode("utf-8").splitlines() if line.strip()]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Fictional vendor JSONL needs object records")
    corpus = engine.make_corpus(records)
    spans = []
    source_registry = engine.registered_source_candidates(artifact)
    if source_registry["artifact_sha"] != artifact["artifact_sha"] or source_registry["classification_applied"] is not False:
        raise ValueError("Candidate registry is not the exact preclassification artifact")
    anchors = [candidate["anchor"] for candidate in source_registry["candidates"]]
    for anchor in anchors:
        from rfp_matrix.source import resolve_anchor
        resolve_anchor(artifact, anchor)
        spans.append(PublicSpan(source_span_id(anchor), "source", artifact["artifact_sha"],
                                anchor["page_index"], anchor["start"], anchor["end"], anchor["quote"]))
    evidence_registry = engine.registered_evidence_windows(corpus)
    if evidence_registry["corpus_generation"] != corpus["generation"]:
        raise ValueError("Registered evidence belongs to another corpus")
    pointers = [window["pointer"] for window in evidence_registry["windows"]]
    for pointer in pointers:
        engine.resolve_evidence(corpus, pointer)
        spans.append(PublicSpan(pointer["sentence_id"], "evidence", pointer["artifact_sha"],
                                0, pointer["start"], pointer["end"], pointer["quote"], pointer["record_id"]))
    public = PublicInput(document_id, family_id, pdf, vendor_jsonl, rights_record_sha,
                         "verified", tuple(spans))
    public.manifest()
    artifact_json, corpus_json = canonical(artifact), canonical(corpus)
    return PreparedInput(public, artifact_json, corpus_json,
                         fingerprint({"public": public.manifest(), "artifact": artifact_json, "corpus": corpus_json}))


class RecordingPort:
    """Closed producer request allowlists; outcomes retained by digest, never logs."""

    def __init__(self, delegate: Any) -> None:
        self.delegate, self.receipts = delegate, []
        self.requested_model = getattr(getattr(delegate, "config", None), "model", getattr(delegate, "requested_model", None))

    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> dict[str, Any]:
        inventory = {"clause", "context", "anchor"}
        matching = {"requirement", "condition", "vendor_context", "registered_windows"}
        inventory_v2 = inventory | {"artifact_sha", "source_sha", "normalization_policy"}
        matching_v2 = matching | {"requirement_revision", "requirement_anchors", "corpus_generation", "record_id", "record_sha", "engine_policy"}
        if set(state) not in (inventory, matching, inventory_v2, matching_v2):
            raise ValueError("Producer request contains unapproved metadata")
        # A JSON copy prevents a port mutating producer-owned state or questions.
        request = {"state": json.loads(canonical(state)), "questions": json.loads(canonical(questions))}
        outcome = dict(self.delegate.judge(request["state"], request["questions"]))
        origin = outcome.get("origin", "unavailable")
        if origin not in ("mock", "replay", "unavailable"):
            raise ValueError("Injected-only comparison cannot dispatch live judgments")
        raw_usage = outcome.get("usage")
        usage = {key: raw_usage.get(key) if isinstance(raw_usage, dict) else None
                 for key in ("input_tokens", "output_tokens")}
        attempts = outcome.get("attempts")
        attempts = len(attempts) if isinstance(attempts, list) else attempts if type(attempts) is int else None
        cost = outcome.get("actual_cost_usd")
        if isinstance(cost, str):
            try:
                cost = float(cost)
            except ValueError:
                cost = None
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            cost = None
        self.receipts.append({"request_sha": fingerprint(request), "outcome_sha": fingerprint(outcome),
                              "origin": origin, "status": outcome.get("status") if outcome.get("status") in ("ok", "needs_review") else "unavailable",
                              "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                              "cost_usd": cost, "attempts": attempts,
                              "cache_hit": outcome.get("cache_hit") if type(outcome.get("cache_hit")) is bool else None})
        return outcome


class PipelineMethodPort:
    """One public arm, with explicit injected factory/configuration and no gold.

    B0b and F1 can share inventory_cache; B0b always extracts using F1 policy,
    then uses B0b rule evidence matching. Cache keys contain exact raw/preparation/
    provider configuration digests, never private labels. Cache is caller-owned
    ephemeral memory; no file cache, index, checkpoint or label store is opened.
    """

    def __init__(self, method: str, prepared: tuple[PreparedInput, ...], *,
                 provider_config: Mapping[str, Any] | None = None,
                 factory: Callable[..., Any] | None = None, client: Any = None,
                 http_client: Any = None, ledger: Any = None,
                 inventory_cache: dict[str, str] | None = None) -> None:
        if method not in ("B0a", "B0b", "F1", "F2"):
            raise ValueError("Unknown comparison arm")
        self.method, self.prepared = method, {p.public.document_id: p for p in prepared}
        if len(self.prepared) != len(prepared):
            raise ValueError("Duplicate prepared document")
        self.config_json = canonical(dict(provider_config or {}))
        self.factory, self.client, self.http_client, self.ledger = factory, client, http_client, ledger
        self.inventory_cache = inventory_cache if inventory_cache is not None else {}

    def frozen_configuration(self, *, price_sha: str, budget_sha: str, origin: str) -> dict[str, Any]:
        from rfp_matrix.providers import ProviderConfig
        supplied = json.loads(self.config_json)
        supplied["method"] = "F1" if self.method == "B0a" else self.method
        policy = ProviderConfig.from_mapping(supplied)
        return {"config_sha": fingerprint({"method": self.method, "provider_config_sha": policy.config_sha256,
                    "prepared": [p.preparation_sha for p in self.prepared.values()]}),
                "model_snapshot": policy.model if self.method != "B0a" else "none-baseline",
                "sdk_snapshot": f"{policy.sdk_name}=={policy.sdk_version}" if self.method != "B0a" else "none-baseline",
                "price_sha": price_sha, "budget_sha": budget_sha, "origin": origin}

    def predict(self, *, method: str, document: PublicInput, config: Mapping[str, Any]) -> Mapping[str, Any]:
        from rfp_matrix.evidence import engine
        from rfp_matrix.source import resolve_anchor, validate_artifact
        from rfp_matrix.providers import create_provider
        if method != self.method or document.document_id not in self.prepared:
            raise ValueError("Method/document binding mismatch")
        prepared = self.prepared[document.document_id]
        if prepared.public != document or fingerprint({"public": document.manifest(), "artifact": prepared.artifact_json,
                                                      "corpus": prepared.corpus_json}) != prepared.preparation_sha:
            raise ValueError("Prepared source bytes/configuration changed")
        artifact, corpus = validate_artifact(json.loads(prepared.artifact_json)), json.loads(prepared.corpus_json)
        policy = self.prospective_policy(document.document_id)
        if dict(config) != self.frozen_configuration(price_sha=config["price_sha"], budget_sha=config["budget_sha"], origin=config["origin"]):
            raise ValueError("Frozen arm configuration does not match actual pipeline")
        if method != "B0a" and policy.model != config["model_snapshot"]:
            raise ValueError("Frozen model snapshot differs from provider policy")
        if method != "B0a" and f"{policy.sdk_name}=={policy.sdk_version}" != config["sdk_snapshot"]:
            raise ValueError("Frozen SDK snapshot differs from provider policy")
        expected_config = fingerprint({"method": method, "preparation_sha": prepared.preparation_sha,
                                       "provider_config_sha": policy.config_sha256})
        # The host freezes an overall arm configuration across all documents;
        # this per-document identity is included in trace, not substituted for it.
        delegate = (self.factory or create_provider)(policy, client=self.client, http_client=self.http_client, ledger=self.ledger)
        recording = RecordingPort(delegate)
        inventory_method = "F1" if method in ("B0b", "F1") else method
        from rfp_matrix.source.policy import policy_context
        inventory_context = policy_context(inventory_method)
        assessment_context = inventory_context if inventory_method == method else policy_context(method)
        cache_key = fingerprint({"preparation": prepared.preparation_sha, "inventory_method": inventory_method,
                                 "inventory_context_sha": inventory_context["context_sha"],
                                 "extraction_policy": {k: v for k, v in policy.identity().items() if k not in {"method", "semantic_fingerprint"}}})
        cached_inventory = cache_key in self.inventory_cache and method in ("B0b", "F1")
        if cached_inventory:
            cached = json.loads(self.inventory_cache[cache_key])
            inventory = cached["inventory"]
            recording.receipts.extend({**r, "origin": "replay", "cache_hit": True} for r in cached["requests"])
        else:
            inventory = engine.inventory_report(artifact, method=inventory_method, provider=recording,
                                                _semantic_context=inventory_context)
            if method in ("B0b", "F1"):
                self.inventory_cache[cache_key] = canonical({"inventory": inventory, "requests": recording.receipts})
        atoms, assessments, failures = [], [], []
        for requirement in inventory["requirements"]:
            for anchor in requirement["anchors"]:
                resolve_anchor(artifact, anchor)
            atom_id = requirement["requirement_id"]
            atoms.append({"id": atom_id, "source_span_ids": [source_span_id(a) for a in requirement["anchors"]],
                          "mandatory": requirement["mandatory"], "conditional": requirement["conditional"],
                          "administrative": requirement["administrative"], "uncertain": bool(requirement["uncertainty"])})
            assessment = engine.assess_requirement(requirement, corpus, method=method, provider=recording,
                                                  _semantic_context=assessment_context)
            if assessment.get("policy_provenance", {}).get("context_sha") != assessment_context["context_sha"]:
                raise ValueError("Assessment returned a different normalized semantic context")
            for pointer in assessment["support"] + assessment["counterevidence"]:
                engine.resolve_evidence(corpus, pointer)
            # Preserve real BM25 record order. Citation selections remain separate
            # sentence/window IDs and never get promoted into retrieval ranks.
            retrieved = engine.retrieve_evidence(requirement, corpus, _semantic_context=assessment_context)
            retrieval_ids = [c["record_id"] for c in retrieved["candidates"]]
            status = assessment["status"]
            if "inventory_provider_unavailable" in requirement["uncertainty"] or assessment["origin"] == "unavailable":
                status = "unavailable"
                failures.append({"stage": "assessment", "id_sha": fingerprint(atom_id), "code": "provider_unavailable"})
            elif not artifact["complete"]:
                failures.append({"stage": "inventory", "id_sha": fingerprint(atom_id), "code": "incomplete_coverage"})
            assessments.append({"atom_id": atom_id, "status": status, "retrieved_ids": retrieval_ids,
                                "support_ids": [p["sentence_id"] for p in assessment["support"]],
                                "counterevidence_ids": [p["sentence_id"] for p in assessment["counterevidence"]]})
        requests = recording.receipts
        def total(key: str) -> Any:
            values = [request[key] for request in requests]
            if not requests:
                return 0 if method == "B0a" else None
            if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
                return None
            return sum(values)
        observed_origins = {request["origin"] for request in requests}
        origin = ("baseline" if method == "B0a" else "unavailable" if failures or not requests
                  or "unavailable" in observed_origins else "mock" if "mock" in observed_origins else "replay")
        trace = {"method": method, "source_sha": sha(document.pdf), "artifact_sha": artifact["artifact_sha"],
                 "corpus_generation": corpus["generation"], "parser_sha": fingerprint([artifact["parser"], artifact["parser_version"], artifact["policy_hash"]]),
                 "normalizer_sha": fingerprint(artifact["normalization_policy"]), "policy_sha": fingerprint([inventory["policy_hash"], method]),
                 "config_sha": config["config_sha"], "provider_config_sha": policy.config_sha256,
                 "document_config_sha": expected_config, "model_snapshot": config["model_snapshot"],
                 "sdk_snapshot": config["sdk_snapshot"], "origin": origin,
                 "inventory_semantic_fingerprint": inventory_context["fingerprint"],
                 "assessment_semantic_fingerprint": assessment_context["fingerprint"],
                 "inventory_sha": fingerprint(inventory), "inventory_cache_hit": cached_inventory,
                 "requests": requests, "failures": failures}
        return {"document_id": document.document_id, "atoms": atoms, "assessments": assessments,
                "usage": {"input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
                          "cost_usd": total("cost_usd"), "latency_ms": None}, "trace": trace}

    def prospective_policy(self, document_id: str) -> Any:
        """Exact disabled source policy for D4's pure envelope planner, no factory."""
        from rfp_matrix.providers import ProviderConfig
        prepared = self.prepared[document_id]
        artifact, corpus = json.loads(prepared.artifact_json), json.loads(prepared.corpus_json)
        supplied = json.loads(self.config_json)
        supplied["method"] = "F1" if self.method == "B0a" else self.method
        supplied.update(document_id=document_id, source_sha256=artifact["artifact_sha"], corpus_sha256=corpus["generation"])
        policy = ProviderConfig.from_mapping(supplied)
        if policy.enabled or Decimal(policy.budget_usd) != 0:
            raise ValueError("Prospective eval planner requires disabled zero-budget source policy")
        return policy
