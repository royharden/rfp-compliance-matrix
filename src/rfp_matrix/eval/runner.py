"""Prospective, injected-only comparison boundary; never discovers labels or files.

This module cannot authenticate a custodian's independence. The host must isolate
the method processes from the custodian process and its private storage. No live
transport is admitted by this construction runner.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

METHODS = ("B0a", "B0b", "F1", "F2")
STATUSES = ("supported", "partial", "cannot_answer_absent", "cannot_answer_conflicting", "needs_review", "unavailable")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(value: Any) -> str:
    return sha(canonical(value).encode("utf-8"))


def require_digest(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Expected lowercase SHA256 digest")


@dataclass(frozen=True)
class PublicSpan:
    """Registered original-string interval supplied by the source/evidence resolver."""

    span_id: str
    kind: str  # source or evidence
    artifact_sha: str
    page_index: int
    start: int
    end: int
    quote: str
    record_id: str | None = None

    def wire(self) -> dict[str, Any]:
        require_digest(self.artifact_sha)
        if self.kind == "evidence" and (not isinstance(self.record_id, str) or not self.record_id):
            raise ValueError("Evidence spans require a registered record ID")
        if self.kind == "source" and self.record_id is not None:
            raise ValueError("Source span cannot carry an evidence record ID")
        if (not isinstance(self.span_id, str) or not self.span_id or len(self.span_id) > 160
                or not isinstance(self.quote, str) or len(self.quote) > 200000
                or self.kind not in ("source", "evidence")
                or type(self.page_index) is not int or self.page_index < 0
                or type(self.start) is not int or type(self.end) is not int
                or self.start < 0 or self.end <= self.start
                or self.end - self.start != len(self.quote)):
            raise ValueError("Invalid registered original-string span")
        return {"id": self.span_id, "kind": self.kind, "artifact_sha": self.artifact_sha,
                "page_index": self.page_index, "start": self.start, "end": self.end,
                "quote": self.quote, "excerpt_sha": sha(self.quote.encode("utf-8")), "record_id": self.record_id}


@dataclass(frozen=True)
class PublicInput:
    """Public data only: opaque IDs must not encode answers or private filenames."""

    document_id: str
    family_id: str
    pdf: bytes
    vendor_jsonl: bytes
    rights_record_sha: str
    rights_status: str
    spans: tuple[PublicSpan, ...]

    def manifest(self) -> dict[str, Any]:
        require_digest(self.rights_record_sha)
        if (not isinstance(self.document_id, str) or not self.document_id or len(self.document_id) > 160
                or not isinstance(self.family_id, str) or not self.family_id or len(self.family_id) > 160
                or self.rights_status != "verified"
                or not isinstance(self.pdf, bytes) or not self.pdf or len(self.pdf) > 20 * 1024 * 1024
                or not isinstance(self.vendor_jsonl, bytes) or not self.vendor_jsonl
                or len(self.vendor_jsonl) > 2 * 1024 * 1024 or type(self.spans) is not tuple
                or len(self.spans) > 22000 or any(type(span) is not PublicSpan for span in self.spans)):
            raise ValueError("Only bounded, rights-verified public inputs are admitted")
        spans = [span.wire() for span in self.spans]
        if len({s["id"] for s in spans}) != len(spans):
            raise ValueError("Duplicate registered span ID")
        return {"document_id": self.document_id, "family_id": self.family_id,
                "source_sha": sha(self.pdf), "vendor_sha": sha(self.vendor_jsonl),
                "rights_record_sha": self.rights_record_sha, "registry_sha": fingerprint(spans)}


@dataclass(frozen=True)
class FrozenComparison:
    """Canonical immutable bytes prevent configuration changes after freezing."""

    canonical_manifest: str
    digest: str


def freeze_comparison(public_inputs: Sequence[PublicInput], *, split: str,
                      pins: Mapping[str, str], method_configs: Mapping[str, Mapping[str, Any]],
                      dataset_counts: Mapping[str, int], run_envelopes: Mapping[str, Any] | None = None,
                      build_provenance: Mapping[str, Any] | None = None) -> FrozenComparison:
    """Freeze all four arms before dispatch, including unmeasured arms.

    Required pin digests cover source tree, parser, normalizer, question policy,
    thresholds, split, labels (opaque commitment only), scorer, budget and price.
    Counts are prospective commitments, not authenticated human label counts.
    """
    required = {"source", "parser", "normalizer", "questions", "thresholds", "split", "labels", "scorer", "budget", "price"}
    if split not in ("dev", "heldout") or set(pins) != required or set(method_configs) != set(METHODS):
        raise ValueError("Incomplete prospective split/pins/method configuration")
    for value in pins.values():
        require_digest(value)
    if not public_inputs or len(public_inputs) > 100 or any(type(item) is not PublicInput for item in public_inputs):
        raise ValueError("Comparison requires 1..100 public documents")
    manifests = [item.manifest() for item in public_inputs]
    if len({m["document_id"] for m in manifests}) != len(manifests):
        raise ValueError("Duplicate document ID")
    if len({m["source_sha"] for m in manifests}) != len(manifests):
        raise ValueError("Duplicate raw document cannot inflate counts")
    counts = dict(dataset_counts)
    if any(type(v) is not int or v < 0 for v in counts.values()):
        raise ValueError("Counts must be explicit nonnegative integers")
    if counts.get("documents") != len(manifests) or counts.get("families") != len({m["family_id"] for m in manifests}):
        raise ValueError("Public document/family count mismatch")
    configs = {}
    for method in METHODS:
        config = dict(method_configs[method])
        if set(config) != {"config_sha", "model_snapshot", "sdk_snapshot", "price_sha", "budget_sha", "origin"}:
            raise ValueError("Method configuration has unknown/missing fields")
        for key in ("config_sha", "price_sha", "budget_sha"):
            require_digest(config[key])
        if config["price_sha"] != pins["price"] or config["budget_sha"] != pins["budget"]:
            raise ValueError("Arms require identical frozen price/budget policy")
        if config["origin"] not in ("baseline", "mock", "replay", "unavailable"):
            raise ValueError("Live arms require a separately admitted execution boundary")
        if method != "B0a" and config["origin"] == "baseline":
            raise ValueError("Typed/model arms cannot be relabelled deterministic baseline")
        if any(not isinstance(config[k], str) or not config[k] or len(config[k]) > 160 for k in ("model_snapshot", "sdk_snapshot")):
            raise ValueError("Explicit exact snapshot or unadmitted marker required")
        configs[method] = config
    from .envelopes import freeze_envelope_commitments
    from .build_provenance import validate_build_provenance
    envelopes = freeze_envelope_commitments(run_envelopes, documents=manifests, methods=configs)
    build = validate_build_provenance(build_provenance)
    manifest = {"schema_version": 1, "split": split, "pins": dict(pins),
                "inputs": manifests, "input_sha": fingerprint(manifests), "methods": configs,
                "dataset_counts": counts, "run_envelopes": envelopes, "build_provenance": build,
                "policy": "prospective-fake-only-v3"}
    encoded = canonical(manifest)
    return FrozenComparison(encoded, sha(encoded.encode("utf-8")))


class FakeMethodPort(Protocol):
    """Host injects an effect-free adapter; it receives no seal or gold metadata."""

    def predict(self, *, method: str, document: PublicInput, config: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class Authorization:
    freeze_sha: str
    input_sha: str
    split_sha: str
    labels_sha: str
    seal_sha: str
    authorization_id_sha: str
    disclose_failure_ids: bool = False


class CustodianPort(Protocol):
    """External trusted boundary, implemented and operated by a separate owner.

    authorize_and_consume must atomically reject spent/raw-overlapping/tuned
    families, mismatched seals and unauthorized callers, and consume this set
    BEFORE prediction. A failed/aborted run remains spent. The runner cannot
    enforce durable consumption inside an injected object.
    """

    def authorize_and_consume(self, *, frozen_manifest: str, seal: bytes) -> Authorization: ...

    def score(self, *, authorization: Authorization, method: str,
              predictions: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any]: ...


def _prediction(value: Mapping[str, Any], document: PublicInput) -> dict[str, Any]:
    # JSON copy strips aliases; exact-key validation closes label/prose escape hatches.
    result = json.loads(canonical(value))
    if set(result) not in ({"document_id", "atoms", "assessments", "usage"}, {"document_id", "atoms", "assessments", "usage", "trace"}) or result["document_id"] != document.document_id:
        raise ValueError("Invalid prediction envelope")
    spans = {span.span_id: span.kind for span in document.spans}
    evidence_records = {span.record_id for span in document.spans if span.kind == "evidence"}
    record_for_span = {span.span_id: span.record_id for span in document.spans if span.kind == "evidence"}
    if not isinstance(result["atoms"], list) or len(result["atoms"]) > 2000:
        raise ValueError("Invalid atom list")
    atom_ids = set()
    for atom in result["atoms"]:
        if not isinstance(atom, dict) or set(atom) != {"id", "source_span_ids", "mandatory", "conditional", "administrative", "uncertain"}:
            raise ValueError("Invalid closed atom")
        if not isinstance(atom["id"], str) or not atom["id"] or atom["id"] in atom_ids:
            raise ValueError("Duplicate or invalid atom ID")
        atom_ids.add(atom["id"])
        refs = atom["source_span_ids"]
        if not isinstance(refs, list) or not refs or len(refs) > 100 or any(not isinstance(r, str) or spans.get(r) != "source" for r in refs):
            raise ValueError("Atom references unregistered source spans")
        if any(type(atom[k]) is not bool for k in ("mandatory", "conditional", "administrative", "uncertain")):
            raise ValueError("Explicit boolean atom flags required")
    assessments = result["assessments"]
    if not isinstance(assessments, list) or len(assessments) != len(atom_ids):
        raise ValueError("Every predicted atom needs one assessment")
    seen = set()
    for row in assessments:
        if not isinstance(row, dict) or set(row) != {"atom_id", "status", "retrieved_ids", "support_ids", "counterevidence_ids"}:
            raise ValueError("Invalid closed assessment")
        if not isinstance(row["atom_id"], str) or row["atom_id"] not in atom_ids or row["atom_id"] in seen or row["status"] not in STATUSES:
            raise ValueError("Unknown/duplicate assessment")
        seen.add(row["atom_id"])
        for key in ("retrieved_ids", "support_ids", "counterevidence_ids"):
            refs = row[key]
            allowed = evidence_records if key == "retrieved_ids" else record_for_span
            if not isinstance(refs, list) or len(refs) > 10 or any(not isinstance(r, str) or r not in allowed for r in refs) or len(set(refs)) != len(refs):
                raise ValueError("Unregistered/duplicate evidence selection")
        retrieved = set(row["retrieved_ids"])
        if not {record_for_span[r] for r in row["support_ids"] + row["counterevidence_ids"]} <= retrieved:
            raise ValueError("Selected evidence must occur in bounded retrieval")
        if row["status"] == "supported" and (not row["support_ids"] or row["counterevidence_ids"]):
            raise ValueError("Positive status requires support without counterevidence")
        if row["status"] == "cannot_answer_conflicting" and not row["counterevidence_ids"]:
            raise ValueError("Conflicting status requires counterevidence")
        if row["status"] == "cannot_answer_absent" and (row["support_ids"] or row["counterevidence_ids"]):
            raise ValueError("Absent status cannot carry support/conflict")
    usage = result["usage"]
    if not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens", "cost_usd", "latency_ms"}:
        raise ValueError("Invalid usage wire")
    for key, val in usage.items():
        if val is not None and (type(val) not in (int, float) or not math.isfinite(val) or val < 0):
            raise ValueError("Usage is nonnegative measured numeric or null")
        if key.endswith("tokens") and val is not None and type(val) is not int:
            raise ValueError("Token usage requires integer or null")
    if "trace" in result:
        _validate_trace(result["trace"], document)
    return result


def _validate_trace(trace: Mapping[str, Any], document: PublicInput) -> None:
    fields = {"method", "source_sha", "artifact_sha", "corpus_generation", "parser_sha", "normalizer_sha",
              "policy_sha", "config_sha", "provider_config_sha", "document_config_sha", "model_snapshot",
              "sdk_snapshot", "origin", "inventory_sha", "inventory_cache_hit", "requests", "failures"}
    fields |= {"inventory_semantic_fingerprint", "assessment_semantic_fingerprint"}
    if not isinstance(trace, dict) or set(trace) != fields or trace["method"] not in METHODS:
        raise ValueError("Invalid closed provenance trace")
    for key in fields - {"method", "model_snapshot", "sdk_snapshot", "origin", "inventory_cache_hit", "requests", "failures", "inventory_semantic_fingerprint", "assessment_semantic_fingerprint"}:
        require_digest(trace[key])
    for key in ("inventory_semantic_fingerprint", "assessment_semantic_fingerprint"):
        if trace[key] is not None:
            require_digest(trace[key])
    if trace["source_sha"] != sha(document.pdf) or trace["origin"] not in ("baseline", "mock", "replay", "unavailable"):
        raise ValueError("Trace origin/source mismatch")
    if type(trace["inventory_cache_hit"]) is not bool or any(not isinstance(trace[k], str) or not trace[k] or len(trace[k]) > 160 for k in ("model_snapshot", "sdk_snapshot")):
        raise ValueError("Invalid snapshot/cache provenance")
    if not isinstance(trace["requests"], list) or len(trace["requests"]) > 22000:
        raise ValueError("Unbounded request trace")
    for request in trace["requests"]:
        if set(request) != {"request_sha", "outcome_sha", "origin", "status", "input_tokens", "output_tokens", "cost_usd", "attempts", "cache_hit"}:
            raise ValueError("Invalid closed request trace")
        require_digest(request["request_sha"])
        require_digest(request["outcome_sha"])
        if request["origin"] not in ("mock", "replay", "unavailable") or request["status"] not in ("ok", "needs_review", "unavailable"):
            raise ValueError("Invalid request origin/status")
        for key in ("input_tokens", "output_tokens", "cost_usd"):
            value = request[key]
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid reported usage")
        if request["attempts"] is not None and (type(request["attempts"]) is not int or not 0 <= request["attempts"] <= 2):
            raise ValueError("Invalid bounded attempt count")
        if request["cache_hit"] is not None and type(request["cache_hit"]) is not bool:
            raise ValueError("Invalid cache trace")
    if not isinstance(trace["failures"], list) or len(trace["failures"]) > 2000:
        raise ValueError("Invalid failure trace")
    for failure in trace["failures"]:
        if set(failure) != {"stage", "id_sha", "code"} or failure["stage"] not in ("inventory", "assessment") or failure["code"] not in ("provider_unavailable", "incomplete_coverage"):
            raise ValueError("Unapproved failure diagnostic")
        require_digest(failure["id_sha"])


def _sanitized_scores(value: Mapping[str, Any], *, population_sha: str, disclose_failure_ids: bool) -> dict[str, Any]:
    """Only aggregate closed numerators/denominators cross back to the author.

    No failure IDs/quotes, labels, free text, exception messages or arbitrary
    metadata are copied. Publishing diagnostics requires a separate spent-set
    disclosure decision by the custodian.
    """
    allowed = {"inventory_precision", "inventory_recall", "mandatory_recall", "citation_precision",
               "absent_precision", "absent_coverage", "conflict_precision", "conflict_coverage", "answered_coverage"}
    if value.get("population_sha") != population_sha:
        raise ValueError("Scorer population differs from frozen labels/input/split")
    if set(value) - allowed - {"population_sha", "failure_ids", "mandatory_miss_ids"}:
        raise ValueError("Custodian returned unapproved metric fields")
    result = {}
    for name in sorted(allowed):
        metric = value.get(name)
        if metric is None:
            result[name] = {"status": "unmeasured", "numerator": None, "denominator": None, "value": None}
            continue
        if not isinstance(metric, Mapping) or set(metric) != {"numerator", "denominator"}:
            raise ValueError("Expected closed aggregate count pair")
        n, d = metric["numerator"], metric["denominator"]
        if type(n) is not int or type(d) is not int or not 0 <= n <= d:
            raise ValueError("Invalid metric count pair")
        result[name] = {"status": "reported" if d else "not_applicable", "numerator": n,
                        "denominator": d, "value": n / d if d else None}
    diagnostics = {}
    for key in ("failure_ids", "mandatory_miss_ids"):
        ids = value.get(key, [])
        if not isinstance(ids, list) or len(ids) > 10000 or any(not isinstance(i, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", i) is None for i in ids):
            raise ValueError("Failure IDs must be bounded opaque tokens")
        diagnostics[key] = sorted(set(ids)) if disclose_failure_ids else None
    return {"population_sha": population_sha, "metrics": result, "diagnostics": diagnostics}


def run_comparison(frozen: FrozenComparison, public_inputs: Sequence[PublicInput], *,
                   ports: Mapping[str, FakeMethodPort], custodian: CustodianPort,
                   seal: bytes, build_observer: Any = None) -> dict[str, Any]:
    """Consume an external seal before any dispatch; emit sanitized protocol receipts.

    Deliberately no paths, file reads, provider imports, cache, or seal creation.
    An absent arm is unmeasured; a failed arm cannot become a successful absence.
    """
    if sha(frozen.canonical_manifest.encode("utf-8")) != frozen.digest:
        raise ValueError("Frozen comparison digest mismatch")
    manifest = json.loads(frozen.canonical_manifest)
    if manifest.get("policy") != "prospective-fake-only-v3":
        raise ValueError("Legacy comparison remains historical; explicit new freeze is required")
    if set(ports) - set(METHODS) or not isinstance(seal, bytes) or not seal or len(seal) > 65536:
        raise ValueError("Unknown arm or absent/bounded seal")
    # Reconstruct via the freezer to enforce its complete schema even on manually
    # constructed FrozenComparison objects.
    public_inputs = tuple(public_inputs)
    rebuilt = freeze_comparison(public_inputs, split=manifest["split"], pins=manifest["pins"],
                                method_configs=manifest["methods"], dataset_counts=manifest["dataset_counts"],
                                run_envelopes=manifest["run_envelopes"], build_provenance=manifest["build_provenance"])
    if rebuilt != frozen:
        raise ValueError("Raw input, candidate, ordering or configuration changed after freeze")
    from .build_provenance import compare_build_provenance
    active_methods = tuple(m for m in METHODS if m in ports and manifest["methods"][m]["origin"] != "unavailable")
    def check_build() -> dict[str, Any]:
        try:
            observed = build_observer.observe() if build_observer is not None else None
            result = compare_build_provenance(manifest["build_provenance"], observed, active_methods=active_methods)
        except Exception:
            raise ValueError("Build provenance is unavailable or malformed; no sealed execution claim") from None
        if not result["allows_sealed_run"]:
            raise ValueError("Frozen semantic/render provenance changed; restart or explicitly refreeze")
        return result
    build_status = check_build()
    auth = custodian.authorize_and_consume(frozen_manifest=frozen.canonical_manifest, seal=seal)
    if type(auth.disclose_failure_ids) is not bool:
        raise ValueError("Custodian must explicitly decide failure-ID disclosure")
    for value in (auth.freeze_sha, auth.input_sha, auth.split_sha, auth.labels_sha, auth.seal_sha, auth.authorization_id_sha):
        require_digest(value)
    if (auth.freeze_sha != frozen.digest or auth.input_sha != manifest["input_sha"]
            or auth.split_sha != manifest["pins"]["split"] or auth.labels_sha != manifest["pins"]["labels"]
            or auth.seal_sha != sha(seal)):
        raise ValueError("Custodian authorization is bound to different sealed inputs")
    receipts = {}
    staged: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for method in METHODS:
        config = manifest["methods"][method]
        receipt: dict[str, Any] = {"status": "not_yet_measured", "origin": config["origin"],
                                  "config_sha": config["config_sha"], "prediction_sha": None,
                                  "actual_origins": [], "prospective_envelope": manifest["run_envelopes"][method],
                                  "metrics": None, "usage": None, "quality_acceptance": False}
        if method in ports and config["origin"] != "unavailable":
            try:
                def predict_checked(item: PublicInput) -> Mapping[str, Any]:
                    check_build()
                    return _prediction(ports[method].predict(method=method, document=item,
                        config=json.loads(canonical(config))), item)
                predictions = tuple(predict_checked(item) for item in public_inputs)
                for prediction in predictions:
                    trace = prediction.get("trace")
                    semantic = manifest["build_provenance"]["semantic"]
                    if semantic[method]["status"] == "available":
                        inventory_method = "F1" if method in ("B0b", "F1") else method
                        if (trace is None or semantic[inventory_method]["status"] != "available"
                                or trace["inventory_semantic_fingerprint"] != semantic[inventory_method]["fingerprint"]
                                or trace["assessment_semantic_fingerprint"] != semantic[method]["fingerprint"]):
                            raise ValueError("Prediction provenance differs from frozen normalized policy context")
                    if trace is not None and (trace["method"] != method or any(trace[k] != config[k] for k in ("config_sha", "model_snapshot", "sdk_snapshot"))):
                        raise ValueError("Prediction trace differs from frozen method configuration")
                receipt["prediction_sha"] = fingerprint(predictions)
                receipt["actual_origins"] = sorted({p["trace"]["origin"] for p in predictions if "trace" in p})
                staged[method] = predictions
            except Exception:
                # Never expose model/source text or private scorer exceptions.
                receipt.update(status="failed_unmeasured", metrics=None, usage=None)
        receipts[method] = receipt
    build_status = check_build()
    # B0b ablates matching only and must replay the exact F1 inventory.
    ablation = "unmeasured"
    if "B0b" in staged and "F1" in staged:
        same = fingerprint([p["atoms"] for p in staged["B0b"]]) == fingerprint([p["atoms"] for p in staged["F1"]])
        ablation = "shared_inventory" if same else "inventory_mismatch"
        if not same:
            for method in ("B0b", "F1"):
                staged.pop(method)
                receipts[method]["status"] = "failed_inventory_mismatch"
    for method, predictions in staged.items():
        build_status = check_build()
        try:
            receipts[method]["metrics"] = _sanitized_scores(custodian.score(authorization=auth, method=method, predictions=predictions),
                population_sha=fingerprint({"input_sha": auth.input_sha, "labels_sha": auth.labels_sha, "split_sha": auth.split_sha}),
                disclose_failure_ids=auth.disclose_failure_ids)
            receipts[method]["usage"] = {k: sum(p["usage"][k] for p in predictions) if all(p["usage"][k] is not None for p in predictions) else None
                                        for k in ("input_tokens", "output_tokens", "cost_usd", "latency_ms")}
            receipts[method]["status"] = "protocol_only_mock_or_replay" if manifest["methods"][method]["origin"] in ("mock", "replay") else "reported_baseline_only"
        except Exception:
            receipts[method].update(status="failed_unmeasured", metrics=None, usage=None)
    build_status = check_build()
    receipt = {"schema_version": 1, "freeze_sha": frozen.digest, "input_sha": manifest["input_sha"],
            "split": manifest["split"], "authorization_id_sha": auth.authorization_id_sha,
            "methods": receipts, "sealed_set_consumed": True, "matching_ablation": ablation,
            "build_origin": manifest["build_provenance"], "build_status": build_status,
            "public_quality_claim": False, "runtime_verified": False}
    return {**receipt, "receipt_sha": fingerprint(receipt)}
